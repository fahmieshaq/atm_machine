import psutil
from subprocess import Popen
import keys
import telebot

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

# Start listening for messages from telegram. If you get https() connection error
# due to internet disconnectivity, infinity_polling() won't stop. Despite the
# ugly https exception dump, infinity_polling() will stay there wait for the internet
# connction to come back up indefinitely as per my testing. Once the internet is back
# infinity_polling() will continue listening as usual. I tried to wrap infinity_polling()
# in try/catch just to beautify the dump a little bit, the exception wasn't caught. Thus,
# it was pointless to add try/catch, I removed it. 
bot.infinity_polling()


