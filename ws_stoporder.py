from fileinput import close
from time import sleep
from pybit import usdt_perpetual
import config, keys
import models_utils
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask import Flask, request
from flask_migrate import Migrate
from models import db


# This websockets keeps monitoring whether my open position has hit stoploss or not
# The moment we hit SL, stoporder websocket shuts down itself and kline websocket
# shuts next. 
def ws_stoporder_fun():
    config.exchange.connect_to_exchange()
    config.exchange.check_and_reconnect_sockets()
    
    # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
    if config.stop_order_id == '':
        config.run_ws_flag = False

    msg_is_debug_mode = ''
    if config.MY_DEBUG_MODE:
        msg_is_debug_mode = '[DEBUG MODE: True] '

    ws_linear = usdt_perpetual.WebSocket(
        test=config.TESTNET_FLAG,
        trace_logging=config.WS_TRACE_LOGGING,
        api_key=keys.TESTNET_API_KEY,
        api_secret=keys.TESTNET_API_SECRET,
        ping_interval=config.WS_PING_INTERVAL_SECS,  # the default is 30
        ping_timeout=config.WS_PING_TIMEOUT_SECS,  # the default is 10
        domain=config.WS_SERVER_DOMAIN  # the default is "bybit"
    )

    def handle_message(msg):
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False

        if config.MY_DEBUG_MODE:
            if msg.get('data')[0].get('order_status') == 'Deactivated':
                print(msg_is_debug_mode + 'Stoporder: The order is ' + msg.get('data')[0].get('order_status') + ', which means someone closed the order manually from the platform')
            elif msg.get('data')[0].get('order_status') == 'Untriggered': # Untriggered means the stoploss hasn't been triggered yet; in other words, the conditional stop order is still active
                print('[Debug Mode] Stoporder: Stoploss was moved. Stoploss is ' + msg.get('data')[0].get('order_status') + ', which means stoploss has not been hit yet.')

        # This condition gets triggered ONLY if a stoploss is met.
        # Triggered means the trade hit the stoploss automatically.
        # Deactivated means someone closed the trade manually the platform
        if msg.get('data')[0].get('order_status') == 'Triggered':
            # Clear stop order id from global variable
            config.stop_order_id = ''

            with config.app.app_context():
                models_utils.update_tradingview_alert(id=config.tvalert_id,
                                                        notes=msg_is_debug_mode + '[Stoporder success] Stoploss is "' + msg.get('data')[0].get('order_status') + '"! Your order is closed. "Triggered" means your trade hit the stoploss automatically.',
                                                        exg_close_at=config.exchange.convert_api_datetime_from_str_to_obj(msg.get('data')[0].get('update_time')),
                                                        exg_close_trigger_price=msg.get('data')[0].get('trigger_price'))

            config.exchange.delete_file(config.GLOBAL_VARS_FILE)
            if config.MY_DEBUG_MODE:
                print('*** [' + msg.get('data')[0].get('order_status') + '] Order Closed ***')

            config.run_ws_flag = False # Setting this value to False will exit kline as well.
        elif msg.get('data')[0].get('order_status') == 'Untriggered':
            pass # It means a stoploss hasn't been triggered yet. You get Untriggered status when you move a stoploss up or down. 
                 # Untriggered status is healthy cause it indicates our stoporder is listening for SL movements. Its fine let it pass.
        elif msg.get('data')[0].get('order_status') == 'Deactivated': # Deactivated precisely means the conditional stoporder got killed/cancelled/removed
                                                                      # and the stoploss conditional order never got a chance to trigger. Deactivation could mean
                                                                      # someone manually cancelled the stop order in the platform, it could meas someone
                                                                      # forced closed an open position in the platform so when a position is closed in middle of a trade
                                                                      # the conditional order associated to the open position would be cancelled (deactivated) too.
                                                                      # WARNING! You can cancel a stop order (conditional stoploss order) while keeping the position
                                                                      # open and according to my risk management, this is VERY RISKY, so to avoid such thing from
                                                                      # happening, whenever I encounter Deactivated, I will close force close the open position
                                                                      # if we still have an open position
            # Clear stop order id from global variable
            config.stop_order_id = ''

            # Force close an open position. If we don't have an open position, an exception will be thrown internally that says
            # you don't have a positon to close, so we'll pass. My goal is just to close the open position if any and I'm not
            # interested to track the status of place_market_order, just force close the open position that's it BECAUSE
            # with Deactivated, you no longer have a conditional stop order for sure, so I wouldn't careless about the open
            # position and shut down as fast as possible because I don't want an open position without stoploss.
            # reduce_only=True is used for closing orders
            try:
                config.exchange.place_market_order(symbol=config.symbol, side=config.side, qty=config.init_qty, stop_loss=config.init_sl, reduce_only=True)
            except Exception as e: # Hitting this section most probabl indicate you don't have an open position which is good.
                if config.MY_DEBUG_MODE:
                    print('[Debug mode] place_market_order() inside ws_stoporder() triggered an exception: ' + str(e))
                else:
                    pass

            with config.app.app_context():
                models_utils.update_tradingview_alert(id=config.tvalert_id,
                                                        notes=msg_is_debug_mode + '[Order Exited Mid Trade] Stoploss is "' + msg.get('data')[0].get('order_status') + '"! Your order is closed. "Deactivated" means someone cancelled the condtional stoporder (the stoploss) in the platform and in return our code forced closed the position to avoid having an open position without a stoploss.',
                                                        exg_close_at=config.exchange.convert_api_datetime_from_str_to_obj(msg.get('data')[0].get('update_time')),
                                                        exg_close_trigger_price=msg.get('data')[0].get('trigger_price'))

            config.exchange.delete_file(config.GLOBAL_VARS_FILE)
            if config.MY_DEBUG_MODE:
                print('*** [' + msg.get('data')[0].get('order_status') + '] [Order Exited Mid Trade] Stop order is cancelled and the open position got closed ***') 
        else: # Other order_status that I haven't counted for such as Rejected, etc. I don't know under what circmustances this would happen If it happens, 
              # simply ignore it because such weird order status could appear after you stoploss is closed already so config.stop_order_id would be blank by then
              # For example, If a conditional order with Close on Trigger enabled is triggered and there is no existing open position, the system will reject the order. 
              # source: https://help.bybit.com/hc/en-us/articles/900000065323-The-Reason-Why-Conditional-Orders-Are-Triggered-But-Not-Successfully-Executed
            # In such case, if you hit else then most probably YOU NO LONGER HAVE. However, sometimes when a sharp quick whipsaw movement happens TOO FAST,
            # the program sometimes misses this if condition, order_status == 'Triggered' or order_status == 'Deactivated' and end up hitting the
            # current else: block. That's why I do a courtesy check whether we still have stop order id or not. If we don't, just close the sockets.
            msg = ''
            sl_ord_id = config.exchange.get_stop_order_id(symbol=config.symbol)
            # Since we don't have the stop order id anymore, close sockets gracefully here because websocket didn't get a chance to catch Triggered code block
            if sl_ord_id == '':
                with config.app.app_context():
                    # Clear stop order id from global variable
                    config.stop_order_id = ''
                    models_utils.update_tradingview_alert(id=config.tvalert_id,
                                                            notes=msg_is_debug_mode + '[Stoporder success] Stoploss is triggered successfully but your order status of your stop_order_stream() API response '
                                                            'shows as "' + msg.get('data')[0].get('order_status') + '"! Most probably you got this status such as Rejected cause '
                                                            'your order is triggered and there is no existing open position. Rejected could happen when there is a sharp quick '
                                                            'whipsaw movement too fast, your platform closes the trade, and the socket does not get a chance to listen the socket "Triggered" '
                                                            'command on-time i.e. order closes in platform and triggers command occurs a second later when there is no trigger data to read and as a result the socket '
                                                            'does not receive Triggered command and in return the API gives you back an order status '
                                                            'called Rejected, or the like. https://help.bybit.com/hc/en-us/articles/900000065323-The-Reason-Why-Conditional-Orders-Are-Triggered-But-Not-Successfully-Executed',
                                                            exg_close_at=config.exchange.convert_api_datetime_from_str_to_obj(msg.get('data')[0].get('update_time')),
                                                            exg_close_trigger_price=msg.get('data')[0].get('trigger_price'))
                    config.exchange.delete_file(config.GLOBAL_VARS_FILE)
                    if config.MY_DEBUG_MODE:
                        print('*** [' + msg.get('data')[0].get('order_status') + '] Order Closed ***')
            else:
                pass # it means we have stop order id, so its ok. Keep listening for stop loss hit.

    try:
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False

        # https://bybit-exchange.github.io/docs/linear/#t-websocketstoporder
        ws_linear.stop_order_stream(
            handle_message
        )

        # Please refer to ws_kline for more info about this function
        def my_on_error(self, error):
            # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
            if config.stop_order_id == '':
                config.run_ws_flag = False 

            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + '[Issue stoporder] my_on_error is triggered due this error: ' + str(error) + '. Attempt to reconnect ws_stoporder_fun()')
            
            if config.run_ws_flag == True:
                ws_stoporder_fun()

        # Please refer to ws_kline for more info about this function
        def my_on_close(wsapp, close_status_code, close_msg):
            # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
            if config.stop_order_id == '':
                config.run_ws_flag = False

            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + '[Issue stoporder] my_on_close is triggered due this close message: ' + str(close_msg) + '. Attempt to reconnect ws_stoporder_fun()')
            
            if config.run_ws_flag == True:
                ws_stoporder_fun()

        ws_linear.ws_private.ws.on_error = my_on_error # Please refer to ws_kline for more info
        # ws_linear.ws_private.ws.on_close = my_on_close # Please refer to ws_kline for more info

        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False

        if config.MY_DEBUG_MODE:
            print('*** Stoporder connected')
    except Exception as e:
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False

        if config.MY_DEBUG_MODE:
            print(msg_is_debug_mode + '[Issue stoporder] stop_order_stream threw an exception: ' + str(e) + '. Attempt to reconnect ws_stoporder_fun()')
        
        if config.run_ws_flag == True:
            ws_stoporder_fun()

    # It'll keep running until config.run_ws_flag gets turned to False.
    # It'll turn to False an open order hits SL
    while config.run_ws_flag:
        sleep(1)

    # Once you reach here, it means config.run_ws_flag is False
    ws_linear.ws_private.ws.close()

    if config.MY_DEBUG_MODE:
        print('*** Stoporder exited')

    exit() # exit the thread
