################### Lets check main program and pnl checker automatically (Start) #################
import psutil
import telegram_send
import schedule
import time
from decimal import Decimal
import config
from pybit.usdt_perpetual import HTTP
import keys
from datetime import datetime


# Global variable
is_telegram_app_error_sent=False # This allows to stop sending lots of telegram messages if system is down.
is_telegram_pnl_error_sent=False # This allows to stop sending lots of telegram messages if system is down.
is_telegram_success_sent=False # This allows to stop sending lots of telegram messages if system is up.
is_telegram_timesync_error_sent=False
is_telegram_timesync_success_sent=False
session=None # Connection to bybit. We connect once only when the program runs he first time


def is_main_program_alive():
    is_live=False
    main_program_filename='app.py'
    for process in psutil.process_iter():
        if process.cmdline() == ['python', main_program_filename]:
            is_live=True

    return is_live


def is_pnl_checker_alive():
    is_live=False
    main_program_filename='pnl_checker.py'
    for process in psutil.process_iter():
        if process.cmdline() == ['python', main_program_filename]:
            is_live=True

    return is_live


# check is time synced with bybit
def check_and_sync_time():
    global session
    is_connected = False
    try:
        # Connection to bybit happens ONCE only once you launch the program. I tested it thoroughly
        if session is None:
            # print('CONNECT TO BYBIT')
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
            # my app.py and bybit.py takes care of sending telegram notifications that's why I didn't bother to take care sending
            # telegram notifications for these exceptions for the sake of simplifying this code.
            print('Main app or ByBit server is down! We could not fetch exchane server time. Send /check command')
    except Exception as e:
        # my app.py and bybit.py takes care of sending telegram notifications that's why I didn't bother to take care sending
        # telegram notifications for these exceptions for the sake of simplifying this code.
        print('Main app or ByBit server is down! We could not fetch exchane server time. Send /check command')
    
    return is_connected


# This function does the same thing as telegram_checker.check_main_program() its just
# the function runs periodically automatically to catch system failures.
def check_main_program_automatically():
    global is_telegram_app_error_sent
    global is_telegram_pnl_error_sent
    global is_telegram_success_sent

    # print('Run check_main_program_automatically(). This function runs once every minute')
    app_status = is_main_program_alive()
    pnl_checker_status = is_pnl_checker_alive()
    msg_app=''
    msg_pnl=''
    if not app_status:
        msg_app='Warning: The main atm_machine program app.py is not working! Please run /boot command to revive the main program. Run /check command to check again.\n\n'
    
    if not pnl_checker_status:
        msg_pnl= 'Warning: The pnl_checker.py program is not working.\n\nGo to the server and run python pnl_checker.py inside ' \
                + 'a screen command. pnl_checker.py can not be revived via /boot. Run /check command to check again.'

    # The condition 'is_telegram_sent == False' helps us send telegram notification once
    if not app_status and is_telegram_app_error_sent == False:
        telegram_send.send(messages=[msg_app])
        is_telegram_app_error_sent=True
        is_telegram_success_sent=False
    if not pnl_checker_status and is_telegram_pnl_error_sent == False:
        telegram_send.send(messages=[msg_pnl])
        is_telegram_pnl_error_sent=True
        is_telegram_success_sent=False
    if is_telegram_success_sent == False and app_status and pnl_checker_status:
        telegram_send.send(messages=['Great... Your main program app.py and pnl_checker.py are live. Do /check to confirm'])
        is_telegram_success_sent=True
        is_telegram_app_error_sent=False
        is_telegram_pnl_error_sent=False


def check_and_sync_time_automatically():
    global session
    global is_telegram_timesync_error_sent
    global is_telegram_timesync_success_sent

    is_connected = check_and_sync_time() # Connects to bybit
    if is_connected: # if not connected, my app.py and bybit.py takes care of sending telegram notifications
        exg_server_time = session.server_time().get('time_now') 
        exg_server_time = int(Decimal(exg_server_time))
        msg_detail = 'Exchange server time UTC: ' + datetime.utcfromtimestamp(exg_server_time).strftime('%Y-%m-%d %H:%M:%S') # convert unix time to a readable format

        local_server_time = int(time.time())
        msg_detail += '  Local time UTC: ' + datetime.utcfromtimestamp(local_server_time).strftime('%Y-%m-%d %H:%M:%S') # convert unix time to a readable format

        if (exg_server_time == local_server_time) and is_telegram_timesync_success_sent==False: 
            msg='Great... Local time is synced with exchange server time! ' + msg_detail
            telegram_send.send(messages=[msg])
            is_telegram_timesync_error_sent=False
            is_telegram_timesync_success_sent=True
        elif (exg_server_time != local_server_time) and is_telegram_timesync_error_sent==False:
            msg='Warning: Local time is not syned with exchange server time! It should be auto-synced shortly in one minute or less. If unsync happens frequently, login to your server to find out WHY your cron ' \
            'job is not syncing time. Rest API and Websockets tend not to work if time is out of sync. You can call /time to manually double check syncing.' + msg_detail
            telegram_send.send(messages=[msg])
            is_telegram_timesync_error_sent=True
            is_telegram_timesync_success_sent=False
       

# Run the schedule. Python is handling the scheduling.
print('Begin check_main_program_automatically()')
check_main_program_automatically()
schedule.every(60).seconds.do(check_main_program_automatically) # Every 5 seconds: schedule.every(5).seconds.do(start_main)

# Run the schedule. Python is handling the scheduling.
print('Begin check_and_sync_time_automatically()')
check_and_sync_time_automatically()
schedule.every(5).minutes.do(check_and_sync_time_automatically) # Every 5 seconds: schedule.every(5).seconds.do(start_main)

while True:
    schedule.run_pending()
    time.sleep(1)
################### Lets check main program and pnl checker automatically (End) #################
