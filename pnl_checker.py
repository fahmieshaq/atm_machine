from flask import Flask, request
from pybit.usdt_perpetual import HTTP
import config, keys
import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from models import db
import models
import schedule
import time
import telegram_send

# The purpose of this program is to monitor today's profit and losses and send out a telegram
# notificaiton if I lose more than the threshold and send out telegram notification if I win
# an $X amount. The program sends telegram notification only; it doesn't take any further action.
# You have to take action manually. The first you can take once you receive a loss alert or profit alert
# is you stop the alert in the tradingview platform. This logic goes like this:
# 1. Every minute, get symbol, loss percentage threshold, and profit amount threshold from the database table MyPnlConfig.
#    This table MyPnlConfig is maintained manually in pgAdmin4.
# 2. Get available balance from Exchange and update it in your database table AvailableBalance. This happens ONCE a day 
#    only I already added a date check in there.
# 3. Now lets fetch today's total PNL from Exchange beginning of the day 00:00:00 to end of the day. Anyway, our API will 
#    fetch whatever PNL of that day up to the point of calling that API because that all PNL we have anyway.
# 4. Take the total PNL and check if its negative or not. If its negative, it means our total is in loss. Now check how 
#    much % did we lose. Have we lost more than our loss threshold percentage (MyPnlConfig.loss_perc_threshold)? If yes,
#    a telegram notification will be sent. You have to take a manual action by going to TV and disable alert to see what you lost alot.
#    If total PNL is positive, it means we made profit. How much dollars did we make? If its more or equal than the profit threshold
#    amount (MyPnlConfig.profit_amount_threshold), send a telegram notificaiton that we have made enough for the day! To stop trading
#    for the day, go to TV and stop alerts. That's it

# API rate limit for /private/linear/trade/closed-pnl/list is 120 requests per minute.
# In this program, we send 1 API closed_profit_and_loss() request per minute so we are good.
# https://bybit-exchange.github.io/docs/futuresV2/linear/#t-ipratelimits

# Setup flask
app = Flask(__name__)


# Setup ORM and data models
app.config['SQLALCHEMY_DATABASE_URI'] = keys.DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
app.app_context().push() # its required for insert and update to work properly


# Global variable
pnl_config_obj = None 
is_received_notification_today_for_loss=False   # You only want to receive the alert once per day.
is_received_notification_today_for_profit=False # You only want to receive the alert once per day.
session_auth=None

# You have to sync time via WSL command: $ sudo ntpdate -sb time.nist.gov
if(config.TESTNET_FLAG):
    session_auth = HTTP(config.TESTNET_ENDPOINT, api_key=keys.TESTNET_API_KEY, api_secret=keys.TESTNET_API_SECRET, recv_window=config.RECV_WINDOW)
else:
    session_auth = HTTP(config.LIVE_ENDPOINT, api_key=keys.LIVE_API_KEY, api_secret=keys.LIVE_API_SECRET, recv_window=config.RECV_WINDOW)


def read_config_values_from_db():
    db.session.commit() # Refresh sqlalchemy cache so your read statement receives fresh data from db
    global pnl_config_obj
    pnl_config_obj = models.MyPnlConfig.query.order_by(models.MyPnlConfig.id.desc()).first()


def update_available_balance_once_a_day():
    global session_auth
    global is_received_notification_today_for_loss
    global is_received_notification_today_for_profit
    
    db.session.commit() # Refresh sqlalchemy cache so your read statement receives fresh data from db
    rec_object = models.AvailableBalance.query.order_by(models.AvailableBalance.id.desc()).first() # grab the latest record
    if rec_object.created_at.strftime('%Y-%m-%d') < datetime.date.today().strftime('%Y-%m-%d'):
        is_received_notification_today_for_loss=False
        is_received_notification_today_for_profit=False
        try:
            today_balance = session_auth.get_wallet_balance(coin="USDT").get('result').get('USDT').get('available_balance')
        except Exception as e:
            pass # don't do anything. We expect the next schedule to kick off soon anyway which will trigger the API again.
        else: 
            rec_object.update(available_balance=int(today_balance), 
                            created_at=datetime.datetime.strptime(datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), '%Y-%m-%d %H:%M:%S'))


def get_today_balance():
    # Refreshes session so whatever read/select query 
    # you run, it gives you a fresh data not from cache
    db.session.commit()
    rec_object = models.AvailableBalance.query.order_by(models.AvailableBalance.id.desc()).first()
    return rec_object.available_balance


def accumulate_pnl_every_minute_daily():
    global session_auth

    total_pnl=0.0
    data=None

    # We only look for today's PNL
    start_time = int(datetime.datetime.combine(datetime.date.today(), datetime.time(00, 00, 00, 999999)).timestamp())
    end_time = int(datetime.datetime.combine(datetime.date.today(), datetime.time(23, 59, 59, 999999)).timestamp())

    max_page_num=50
    for i in range(1, max_page_num):
        try:
            data = session_auth.closed_profit_and_loss(
                symbol=pnl_config_obj.symbol,
                page=i,
                limit=50,
                start_time=start_time,
                end_time=end_time
            ).get('result').get('data')
        except Exception as e:
            print(e)
            data=None # don't do anything. We expect the next schedule to kick off soon anyway which will trigger the API again.
            break

        if data is None:
            break

        rec_count = 0
        if data is not None:
            rec_count = len(data)
        
        if rec_count > 0:
            for i in range(0, rec_count):
                total_pnl = total_pnl + data[i].get('closed_pnl')

    return total_pnl


# If you make money more than threshold, send telegram notification so I can have the choice to stop TV alerts
# If you lose money more than the threshold, send telegram notification so I can stop stop TV alerts and see what 
# caused me to lose alot
def check_profit_and_loss():
    global pnl_config_obj
    global is_received_notification_today_for_loss
    global is_received_notification_today_for_profit

    # We only receive notification once per day. If we already receive notification for both, its pointless to proceed
    # and if you do proceed while both flags are true, you'll simply call PNL api for nothing cause no telegram
    # notification will be sent out.
    if is_received_notification_today_for_loss == True and is_received_notification_today_for_profit == True:
        return

    today_balance = get_today_balance()
    current_pnl = accumulate_pnl_every_minute_daily()
    if today_balance > 0:
        perc = (current_pnl * 100) / today_balance
    else:
        perc = 0
    if perc < 0:
        if abs(perc) >= pnl_config_obj.loss_perc_threshold:
            # if you remove "if is_received_notification_today_for_loss" line, you'll be bombarded with 
            # telegram notificaiton message nonstop until your loss recovers above the threshold.
            if is_received_notification_today_for_loss == False:
                bal_after_loss = today_balance - (today_balance * abs(perc / 100))
                telegram_send.send(messages=["You have lost " + str(perc) + "% of your available balance. Earlier today your balance " \
                                    + " was $" + str(today_balance) + ". Now your balance is $" + str(bal_after_loss) + ". To stop trading temporarily, " \
                                        "go to tradingview and stop the alert. Enable the alert once you complete your investigation"])
                is_received_notification_today_for_loss=True
    else:
        today_profit = today_balance * (perc / 100)
        if today_profit >= pnl_config_obj.profit_amount_threshold:
            if is_received_notification_today_for_profit == False:
                bal_after_profit = today_balance + today_profit
                telegram_send.send(messages=["You made profit of $" + str(today_profit) + " today! Your available balance was $" \
                                    + str(today_balance) + " and now it is $" + str(bal_after_profit) \
                                    + ". You are done for the day. " \
                                    + "You can stop trading today if you choose to. Go to tradingview and stop the alert. " \
                                    + "Enable the alert tomorrow beginning of day"])
                is_received_notification_today_for_profit = True


# This is where I glue the whole process together
def start_main():
    print('Run start_main(). This function runs once every minute')
    read_config_values_from_db()
    update_available_balance_once_a_day()
    check_profit_and_loss()


# Run the schedule. Python is handling the scheduling.
print('Begin start_main()')
start_main()
schedule.every(1).minutes.do(start_main) # Every 5 seconds: schedule.every(5).seconds.do(start_main)

while True:
    schedule.run_pending()
    time.sleep(1)



