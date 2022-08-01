from syslog import LOG_SYSLOG
from time import sleep
from pybit import usdt_perpetual
import config
import bybit
import math
import models_utils
import logging

ws_linear = None

# This socket https://bybit-exchange.github.io/docs/linear/#t-websocketkline helps us monitor
# the chart(candles) real-time to trail stoploss accordingly. Kline is a critical socket
# for trailing to breakeven and catching/trailing/locking profits
def ws_kline_fun(symbol, stop_order_id, order_direction):
    config.exchange.connect_to_exchange()
    config.exchange.check_and_reconnect_sockets()

    global ws_linear

    # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
    if config.stop_order_id == '':
        config.run_ws_flag = False # end kline thread

    msg_is_debug_mode = ''
    if config.MY_DEBUG_MODE:
        msg_is_debug_mode = '[DEBUG MODE: True] '

    ws_linear = usdt_perpetual.WebSocket(
        test=config.TESTNET_FLAG,         # True connects to testnet and False connects to mainnet
        trace_logging=config.WS_TRACE_LOGGING,
        ping_interval=config.WS_PING_INTERVAL_SECS,  # the default is 30.
        ping_timeout=config.WS_PING_TIMEOUT_SECS,  # the default is 10
        domain=config.WS_SERVER_DOMAIN  # the default is "bybit"
    )

    def handle_message(msg):
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False # end kline thread

        close = msg.get('data')[0].get('close')
        confirm = msg.get('data')[0].get('confirm') # If confim is true, it means the current candle has closed and a new candle started. False means current candle hasn't closed yet.
    
        if order_direction == config.LONG:
            if config.is_breakeven_hit == False:
                if close > config.breakeven_target_price:
                    if confirm == True or confirm == False:
                        trail_sl_to_breakeven(symbol, stop_order_id, close, confirm)

            if config.is_breakeven_hit == True:
                if close > config.profit_target_price:
                    if confirm == True or confirm == False:
                        trail_sl_to_profit_zone(symbol, stop_order_id, order_direction, close, confirm)

        if order_direction == config.SHORT:
            if config.is_breakeven_hit == False:
                if close < config.breakeven_target_price:
                    if confirm == True or confirm == False:
                        trail_sl_to_breakeven(symbol, stop_order_id, close, confirm)   

            if config.is_breakeven_hit == True:
                if close < config.profit_target_price:
                    if confirm == True or confirm == False:
                        trail_sl_to_profit_zone(symbol, stop_order_id, order_direction, close, confirm)


    def trail_sl_to_breakeven(symbol, stop_order_id, close, confirm):
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False # end kline thread

        config.entry_price = config.exchange.truncate(config.entry_price, config.max_precision)
        config.exchange.connect_to_exchange()
        try:
            config.exchange.change_stoploss(symbol=symbol, stop_order_id=stop_order_id, new_stoploss_price=config.entry_price)
        except Exception as e:
            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + '[Issue kline] change_stoploss() inside trail_sl_to_breakeven threw an exception: ' + str(e))
            
            if str(e).lower() in 'Order not exists or too late to replace'.lower(): # This exception indicates we no longer have any order. The order got closed 
                                                                                    # so fast in the platform and went back (fast long candle move one side and 
                                                                                    # a fraction of a second moved to the original state/other side. Its like a lightning 
                                                                                    # fast fakeout/wick). As a result, the stoporder socket didn't get a chance to catch 
                                                                                    # the triggered status
                with config.app.app_context():
                    models_utils.update_tradingview_alert(id=config.tvalert_id,
                        notes=msg_is_debug_mode + '[Order Closed] Kline change_stoploss() trail_sl_to_breakeven threw this exception: ' + str(e) + '. Our open position had a whipsawed candle that hit stoploss so lightning fast and then the candle moved back ' \
                                                    'to its original state in a fraction of second. It looks like our stoporder websocket was not fast ' \
                                                    'enough to catch the stoloss trigger (Triggered status), so our stoploss did not get a chance to pause the socket. ' \
                                                    'This lead our kline to continue montioring the trade while we do not have an open position! Also, our stoploss ws ' \
                                                    'continues to listen for triggers when in fact there is no open position to listen for. So evertime our kline tries ' \
                                                    'to update our stoploss, you end up getting this exception "Order not exists or too late to replace". Remember, ' \
                                                    'if ws stoporder socket is active then kline would be active too because initially kill kline when stoploss catches a ' \
                                                    'triggered a stoploss, kline will continue montoring using the global config values and we will be getting this ' \
                                                    'exception "Order not exists or too late to replace" everytime a change_stoploss() opportunity appears. As a result, kline ' \
                                                    'and stoploss got killed and now we starting fresh to receive new alerts.')
                config.run_ws_flag = False # End sockets
        else: # It gets executed only if no exception occurs
            if config.MY_DEBUG_MODE:
                print('*** Trailed SL to Breakeven ***')
                print('Current price: ' + str(close))
                print('Is confirmed: ' + str(confirm))
                print('Initial entry price: ' + str(config.entry_price))
                print('Initial stop loss: ' + str(config.stoploss))
                print('Breakeven target: ' + str(config.breakeven_target_price))
                print('Stop order id: ' + str(config.stop_order_id))
                
            config.stoploss = config.entry_price
            config.is_breakeven_hit = True

            if config.MY_DEBUG_MODE:
                print('New stop loss: ' + str(config.stoploss))
                print('------')

            # Save global variables int a file in case main program shuts down. When you re-run
            # main program, you program will pick up from where your sockets global 
            # variables left
            config.exchange.save_global_variables_into_file(config.GLOBAL_VARS_FILE)
            with config.app.app_context():
                models_utils.update_tradingview_alert(id=config.tvalert_id, 
                                notes=msg_is_debug_mode + 'Trailed SL to Breakeven',
                                exg_passed_bev_target=True,
                                exg_trailed_sl_price=config.entry_price)


    def trail_sl_to_profit_zone(symbol, stop_order_id, order_direction, close, confirm):
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False # end kline thread
        
        new_sl = 0.0
        if order_direction == config.LONG:
            new_sl = config.profit_target_price - config.gap_to_sl
        elif order_direction == config.SHORT:
            new_sl = config.profit_target_price + config.gap_to_sl

        config.stoploss = config.exchange.truncate(new_sl, config.max_precision)
        config.exchange.connect_to_exchange()
        try:
            config.exchange.change_stoploss(symbol=symbol, stop_order_id=stop_order_id, new_stoploss_price=config.stoploss)
        except Exception as e:
            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + '[Issue kline] change_stoploss() inside trail_sl_to_profit_zone threw an exception: ' + str(e))
            
            if str(e).lower() in 'Order not exists or too late to replace'.lower(): # This exception indicates we no longer have any order. The order got closed 
                                                                                    # so fast in the platform and went back (fast long candle move one side and 
                                                                                    # a fraction of a second moved to the original state/other side. Its like a lightning 
                                                                                    # fast fakeout/wick). As a result, the stoporder socket didn't get a chance to catch 
                                                                                    # the triggered status
                with config.app.app_context():
                    models_utils.update_tradingview_alert(id=config.tvalert_id,
                        notes=msg_is_debug_mode + '[Order Closed] Kline change_stoploss() in trail_sl_to_profit_zone threw this exception: ' + str(e) + '. Our open position had a whipsawed candle that hit stoploss so lightning fast and then the candle moved back ' \
                                                    'to its original state in a fraction of second. It looks like our stoporder websocket was not fast ' \
                                                    'enough to catch the stoloss trigger (Triggered status), so our stoploss did not get a chance to pause the socket. ' \
                                                    'This lead our kline to continue montioring the trade while we do not have an open position! Also, our stoploss ws ' \
                                                    'continues to listen for triggers when in fact there is no open position to listen for. So evertime our kline tries ' \
                                                    'to update our stoploss, you end up getting this exception "Order not exists or too late to replace". Remember, ' \
                                                    'if ws stoporder socket is active then kline would be active too because initially kill kline when stoploss catches a ' \
                                                    'triggered a stoploss, kline will continue montoring using the global config values and we will be getting this ' \
                                                    'exception "Order not exists or too late to replace" everytime a change_stoploss() opportunity appears. As a result, kline ' \
                                                    'and stoploss got killed and now we starting fresh to receive new alerts.')
                config.run_ws_flag = False # End sockets
        else: # It gets executed only if no exception occurs
            config.entry_price = config.profit_target_price
            config.profit_target_price = config.exchange.get_target_price(type=order_direction, entry_price=config.entry_price, stoploss=config.stoploss, target_ratio=config.PROFIT_RW_RATIO)
            config.exg_trailed_profit_sl_counter += 1

            config.exchange.save_global_variables_into_file(config.GLOBAL_VARS_FILE)

            if config.MY_DEBUG_MODE:
                print('*** Trailed SL to Profit Zone ***')
                print('Current price: ' + str(close))
                print('Is confirmed: ' + str(confirm))
                print('Entry price (Profit Target): ' + str(config.entry_price))
                print('New stop loss: ' + str(config.stoploss))
                print('New profit target: ' + str(config.profit_target_price))
                print('Trail counter: ' + str(config.exg_trailed_profit_sl_counter))
                print('Stop order id: ' + str(config.stop_order_id))
                print('------')

            with config.app.app_context():
                models_utils.update_tradingview_alert(id=config.tvalert_id,
                                notes=msg_is_debug_mode + 'Trailed SL to Profit Zone',
                                exg_passed_profit_target=True,
                                exg_trailed_sl_price=config.stoploss,
                                exg_trailed_entry_price=config.entry_price,
                                exg_trailed_profit_target=config.profit_target_price,
                                exg_trailed_profit_sl_counter=config.exg_trailed_profit_sl_counter)

    try:
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False # end kline thread

        # To subscribe to multiple symbols,
        # pass a list: ["BTCUSDT", "ETHUSDT"]
        # pass an interval. Check https://bybit-exchange.github.io/docs/linear/#t-websocketkline
        ws_linear.kline_stream(
            handle_message, symbol, "1"
        )
        
        # Override function on_error. The original on_error() takes two params. Our function should have same signature of the original on_error.
        # on_error gets triggered if you internet gets interrupted. Also, any unhandled exception caught in handle_message, it'll trigger my_on_error()
        # Source: https://websocket-client.readthedocs.io/en/latest/examples.html
        def my_on_error(self, error):
            # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
            if config.stop_order_id == '':
                config.run_ws_flag = False # end kline thread

            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + '[Issue kline] my_on_error is triggered due this error: ' + str(error) + '. Attempt to reconnect ws_kline_fun()')
            
            if config.run_ws_flag == True:
                ws_kline_fun(symbol, stop_order_id, order_direction)

        # Sometimes we may face a situation where on_close() gets called due to server connection issue or whatever and on_error()
        # doesn't; due to lack of input, I can't recall what scenarios would trigger on_close(). However, if such thing happens,
        # we'll attemp to restart/reconnect the socket. 
        # Source: https://websocket-client.readthedocs.io/en/latest/examples.html
        def my_on_close(wsapp, close_status_code, close_msg):
            # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
            if config.stop_order_id == '':
                config.run_ws_flag = False # end kline thread

            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + '[Issue kline] my_on_close is triggered due this close message: ' + str(close_msg) + '. Attempt to reconnect ws_kline_fun()')
            
            if config.run_ws_flag == True:
                ws_kline_fun(symbol, stop_order_id, order_direction)

        # We are overriding on_error and on_close because ByBit provides a limited attempt
        # to reconnect sockets based on your ping_interval and ping_timeout. If you hit the timeout,
        # that's it you'll have to restart the sockets manually in one way or another AND it is known
        # that websockets tend to disconnect from the server side more than normal and I can't afford
        # to login into the server to reboot sockets. As a result, I chose to override on_error() 
        # with my code in my_on_error. my_on_error will keep attempting to connect to the sockets
        # indefinitely. I literally can't afford to shut the sockets once we go live. The only
        # time a socket shuts down itself is when the order gets closed.
        ws_linear.ws_public.ws.on_error = my_on_error # on_error() is part of threading library
        # ws_linear.ws_public.ws.on_close = my_on_close # on_close() is part of threading library. Overridng it won't harm.
                                                      # Important! ws_linear.ws_public.ws.on_close GOT NOTHING TO DO WITH ws_linear.ws_public.ws.close()
                                                      # Keep ws_linear.ws_public.ws.close() intact.
        
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False # end kline thread

        if config.MY_DEBUG_MODE:
            print('*** Kline connected')
    
    except Exception as e:
        # if you don't stop order id, then it means the ordr has closed already and there is nothing to trail
        if config.stop_order_id == '':
            config.run_ws_flag = False

        if config.MY_DEBUG_MODE:
            print(msg_is_debug_mode + '[Issue kline] kline_stream threw an exception: ' + str(e) + '. Attempt to reconnect ws_kline_fun()')

        if config.run_ws_flag == True:
            ws_kline_fun(symbol, stop_order_id, order_direction)

    # It'll keep running until config.run_ws_flag gets turned to False
    # ws_stoporder websocket will turn config.run_ws_flag to False once
    # a stoploss is triggered and in return, kline websockets exits as well
    while config.run_ws_flag:
        sleep(1)
    
    # Once you reach here, it means config.run_ws_flag is False
    ws_linear.ws_public.ws.close()
    config.exchange.delete_file(config.GLOBAL_VARS_FILE)

    if config.MY_DEBUG_MODE:
        print('*** Kline exited')

    config.exchange.reset_global_variables() # Reset all global variables
    exit() # exit thread
