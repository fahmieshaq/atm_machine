import psutil
from subprocess import Popen
import keys
import telebot
import config
from decimal import Decimal
from datetime import datetime
import time
from pybit.usdt_perpetual import HTTP

API_KEY=keys.TELEGRAM_API_KEY

# Make a connection to bot
bot = telebot.TeleBot(API_KEY)

def is_main_program_alive():
    is_live=False
    main_program_filename='app.py'
    for process in psutil.process_iter():
        if process.cmdline() == ['python', main_program_filename]:
            is_live=True

    return is_live

# Send /check command from telegram channel to test the heartbeat of main app.py
@bot.message_handler(commands=['check'])
def check_main_program(message):
    app_status = is_main_program_alive()
    msg=''
    if app_status:
        msg='The main atm_machine program app.py is alive'
    else:
        msg='The main atm_machine program app.py is not working! Please run /boot command to revive it.'

    bot.send_message(message.chat.id, msg) # or you could call: bot.reply_to(message, msg)

# Send /boot command from telegram channel to revive the main program app.py
@bot.message_handler(commands=['boot'])
def start_main_program(message):
    app_status = is_main_program_alive()
    msg=''
    if not app_status:
        Popen(['python', 'app.py'])
        msg='We attempted to start it. Please call /check to see if the app got started.'
    else:
        msg='Your program is active! There is no need to revive it. You can call /check to confirm.'

    bot.send_message(message.chat.id, msg) # or you could call: bot.reply_to(message, msg)


@bot.message_handler(commands=['time'])
def check_and_sync_time(message):
    is_connected = False
    msg=''
    try:
        if(config.TESTNET_FLAG):
            session = HTTP(config.TESTNET_ENDPOINT, api_key=keys.TESTNET_API_KEY, api_secret=keys.TESTNET_API_SECRET, recv_window=config.RECV_WINDOW)
        else:
            session = HTTP(config.LIVE_ENDPOINT, api_key=keys.LIVE_API_KEY, api_secret=keys.LIVE_API_SECRET, recv_window=config.RECV_WINDOW)

        # Check the connection here. We could call self.check_exchange_connection() but I didn't due to
        # deadline constraints and I wasn't ready to retest the refactor
        try:
            if(session.server_time()):
                is_connected = True
        except Exception as e:
            is_connected = False
            msg='Main app or ByBit server is down! We could not fetch exchane server time. Send /check command'
    except Exception as e:
        msg='Main app or ByBit server is down! We could not fetch exchane server time. Send /check command'
    else:
        exg_server_time = session.server_time().get('time_now')
        exg_server_time = int(Decimal(exg_server_time))
        msg_detail = 'Exchange server time UTC: ' + datetime.utcfromtimestamp(exg_server_time).strftime('%Y-%m-%d %H:%M:%S') # convert unix time to a readable format

        local_server_time = int(time.time())
        msg_detail += '  Local time UTC: ' + datetime.utcfromtimestamp(local_server_time).strftime('%Y-%m-%d %H:%M:%S') # convert unix time to a readable format

        if exg_server_time == local_server_time: 
            msg='Great... Local time is synced with exchange server time! ' + msg_detail
        else:
            msg='Nah! Local time is not syned with exchange server time! It should be auto-synced shortly in one minute or less. If unsync happens frequently, login to your server to find out WHY your cron ' \
                'job is not syncing time. Rest API and Websockets tend not to work if time is out of sync. ' + msg_detail

    bot.send_message(message.chat.id, msg) # or you could call: bot.reply_to(message, msg)


# Start listening for messages from telegram. If you get https() connection error
# due to internet disconnectivity, infinity_polling() won't stop. Despite the
# ugly https exception dump, infinity_polling() will stay there wait for the internet
# connction to come back up indefinitely as per my testing. Once the internet is back
# infinity_polling() will continue listening as usual. I tried to wrap infinity_polling()
# in try/catch just to beautify the dump a little bit, the exception wasn't caught. Thus,
# it was pointless to add try/catch, I removed it. 
bot.infinity_polling()


