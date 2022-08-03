from decimal import Decimal
import math
from pybit.usdt_perpetual import HTTP, WebSocket
import json
import sys
from datetime import datetime, timedelta, timezone
from syslog import LOG_SYSLOG # For web socket
from time import sleep
import config, keys
import time
import os
import pickle # save gobal vars in a file
from threading import Thread
import threading
import requests
import telegram_send
import models_utils
from sqlalchemy import func
import models
from models import db
import ws_stoporder, ws_kline # keep these two imports last because imports run in sequence in case they rely on upper imports

# All of my exchange's (ByBit) APIs are called here. All APIs below are wrapped
# in functions and those functions get used by main program and web sockets
# This is the only place I call APIs, I don't call API outside of bybit.py
class ByBit:
    def __init__(self):
        # Connect to ByBit
        self.is_connected = False
        self.is_critical_http_occured = False
        self.connect_to_exchange()


    # connect_to_exchange() will only attempt to connect to the exchange's server IF 
    # there isn't a connection to the exchange due to internet failure, etc. You can
    # call connect_to_exchange() wherever you want to re-assure server's connection
    def connect_to_exchange(self):
        self.check_bybit_connection()

        retry_counter = 0
        if config.MY_DEBUG_MODE:
            if self.is_connected == True:
                print('[Debug Mode] Already connected to ByBit server. There is no need to re-connect')

        while self.is_connected == False and retry_counter < config.MAX_RETRY_COUNTER_FOR_HTTP_CONNECT:
            retry_counter += 1

            if config.MY_DEBUG_MODE:
                print('[Debug Mode] Connecting to bybit server. In progress')

            # what is recv_window? https://bybit-exchange.github.io/docs/linear/#t-authenticationparameters
            if(config.TESTNET_FLAG):
                self.session = HTTP(config.TESTNET_ENDPOINT, api_key=keys.TESTNET_API_KEY, api_secret=keys.TESTNET_API_SECRET, recv_window=config.RECV_WINDOW)
            else:
                self.session = HTTP(config.LIVE_ENDPOINT, api_key=keys.LIVE_API_KEY, api_secret=keys.LIVE_API_SECRET, recv_window=config.RECV_WINDOW)

            # Check the connection here. We could call self.check_bybit_connection() but I didn't due to
            # deadline constraints and I wasn't ready to retest the refactor
            try:
                if(self.session.server_time()):
                    self.is_connected = True
                    if config.MY_DEBUG_MODE:
                        print('[Debug Mode] Connected to ByBit server. Done')
            except Exception as e:
                self.is_connected = False
                if config.MY_DEBUG_MODE:
                    print('[Debug Mode] Issue with ByBit server. Wait for 3 seconds and retry. Attempt# ' + str(retry_counter) + ' out of ' + str(config.MAX_RETRY_COUNTER_FOR_HTTP_CONNECT))
                sleep(3)

        if self.is_connected == False:
            self.is_critical_http_occured = True
            server_endpoint=''
            if (config.TESTNET_FLAG): 
                server_endpoint = config.TESTNET_ENDPOINT
            else:
                server_endpoint = config.LIVE_ENDPOINT

            msg = 'CRITICAL: Warning - Failed to connect to ByBit HTTP() exchange server ' + str(server_endpoint) + ' after maximum attempt ' + str(config.MAX_RETRY_COUNTER_FOR_HTTP_CONNECT) + 'x. The whole application is down due to lose of connectivity with bybit server. Manual intervention ' \
                  'might be required by running python app.py in the server ONLY and ONLY IF you do not received a telegram message shortly that says CRITICAL HTTP() Connection is Resolved, after 5-10 mins or so.'
            try:
                telegram_send.send(messages=[msg])
                models_utils.insert_mylogs(notes=msg)
                if config.MY_DEBUG_MODE:
                    print('[Debug Mode] ByBit Server is down. The application is down and manual intervention might be required ONLY and ONLY IF you do not receive a telegram message shortly that says CRITICAL HTTP() Connection is Resolved after 5-10 mins or so. We attempted to connect ' + str(retry_counter) + ' times with a wait time of 3 seconds between one attempt to another.')
            except Exception as e:
                msg = 'Internet is down! Telegram could not send you the critical message due internet connection issues. The exception is ' + str(e)
                models_utils.insert_mylogs(notes=msg)
                if config.MY_DEBUG_MODE:
                    print('[Debug mode] ' + msg)
        
        if self.is_connected == True and self.is_critical_http_occured == True:
            self.is_critical_http_occured = False # Done critical is resolved
            msg = 'RESOLVED - The critical warning HTTP() connection to the exchange server got resolved. Your atm machine is connected to the exchange successfully and your app is running fine! No manual intervention is required'
            try:
                telegram_send.send(messages=[msg])
                models_utils.insert_mylogs(notes=msg)
                if config.MY_DEBUG_MODE:
                    print(msg)
            except Exception as e:
                msg = 'Internet is down! Telegram could not send you the success message (critical reoslution) due internet connection issues. The exception is ' + str(e)
                models_utils.insert_mylogs(notes=msg)
                if config.MY_DEBUG_MODE:
                    print('[Debug mode] ' + msg)


    # Check https://bybit-config.exchange.github.io/docs/linear/#t-position
    # This automated trading is meant to have 1 and only 1 open position at
    # all times.
    def is_open_position_available(self):
        is_open_position_found = False

        # These variables help us retry api in case failure happens
        is_exception_found = True
        retry_count = 0
        
        while is_exception_found and retry_count < config.MAX_RETRY_COUNTER_FOR_CHECKING_OPEN_POS:
            try:
                all_positions = self.session.my_position()
            except Exception as e:
                retry_count += 1
                sleep(3)
                self.connect_to_exchange()
                is_exception_found = True
            else: # if try block doesn't thorw exception, execute the code inside else
                is_exception_found = False
                # my_position returns all positions, the moment you find at least one
                # symbol with a size larger than 0, return. Size > 0 means we have
                # at least one open position
                result = all_positions.get('result')
                for i in range(0, len(result)):
                    if result[i].get('data').get('size') > 0:
                        is_open_position_found = True
                        break

        return is_open_position_found

    
    # Our program is built in a way we only have one open position at all times
    # so whatever position info we get here would be the only open position
    # we have in the platform anyway that is why I'g getting the first
    # open position regardless of the symbol
    def get_first_open_position_info(self):
        pos_symbol=''
        pos_size=0.0
        pos_side=''

        # These variables help us retry api in case failure happens
        is_exception_found = True
        retry_count = 0
        
        while is_exception_found and retry_count < config.MAX_RETRY_COUNTER_FOR_CHECKING_OPEN_POS:
            try:
                # my_position returns all position but we'll catch the first
                # non-zero position and consider it to be the position we want
                # because we'd have only one open position at all times.
                all_positions = self.session.my_position()
            except Exception as e:
                retry_count += 1
                sleep(3)
                self.connect_to_exchange()
                is_exception_found = True
            else: # if try block doesn't thorw exception, execute the code inside else
                is_exception_found = False
                # my_position returns all positions, the moment you find at least one
                # symbol with a size larger than 0, return. Size > 0 means we have
                # at least one open position
                result = all_positions.get('result')
                for i in range(0, len(result)):
                    if result[i].get('data').get('size') > 0:
                        pos_symbol=result[i].get('data').get('symbol')
                        pos_size=Decimal(result[i].get('data').get('size'))
                        pos_side=result[i].get('data').get('side')
                        break

        return (pos_symbol, pos_size, pos_side)


    # The sockets are responsible for trailing stoplosses
    def check_and_reconnect_sockets(self):
        # In case main program shuts down. When you re-run
        # main program, you program will pick up from where your sockets global 
        # variables left. If some global variables are empty then probably connection
        # got interrupted and somehow lost global variables from heap. As a result, lets
        # fetch our global variables from the file. The file stores the most recent, latest,
        # variables values before the connection interruption happened
        if config.stoploss <= 0 or config.stop_order_id == '' or config.entry_price <= 0:
            config.exchange.read_global_variables_from_file(config.GLOBAL_VARS_FILE)
            if config.MY_DEBUG_MODE:
                print('************ Continue ' + config.side + ' Trading [Read Global Vairables from File] ***********')
                print('symbol: ' + config.symbol)
                print('entry price: ' + str(config.entry_price))
                print('stoploss: ' + str(config.exchange.truncate(config.stoploss, config.max_precision)))
                print('profit target: ' + str(config.profit_target_price))
                print('breakeven target: ' + str(config.breakeven_target_price))
                print('gap_to_sl: ' + str(config.gap_to_sl))
                print('tick_size: ' + str(config.tick_size))
                print('max_precision: ' + str(config.max_precision))
                
        thread_names = [t.name for t in threading.enumerate()]
        if "ws_stoporder_thread" not in thread_names:
            config.run_ws_flag = True
            ws_stoporder_thread = Thread(target=ws_stoporder.ws_stoporder_fun, name=config.WS_STOPORDER_NAME)
            ws_stoporder_thread.start()
        else:
            pass # We don't create another stop ws because its already running

        if "ws_kline_thread" not in thread_names:
            config.run_ws_flag = True
            ws_kline_thread = Thread(target=ws_kline.ws_kline_fun, args=(config.symbol, config.stop_order_id, config.side), name=config.WS_KLINE_NAME)
            ws_kline_thread.start()
        else:
            pass # We don't create another stop ws because its already running


    # You can validate it here https://www.cashbackforex.com/tools/position-size-calculator/BTCUSD
    def calculate_crypto_required_qty(self, risk_amount, entry_price, stoploss):
        qty=0.0
        try:
            qty = config.exchange.truncate((Decimal(risk_amount) / abs(Decimal(entry_price) - Decimal(stoploss))), config.max_precision)
        except Exception as e:
            pass
        return qty


    def get_usdt_amount_using_crypto_qty(self, entry_price, stoploss, qty):
        dollar_amount=0.0
        try:
            dollar_amount = config.exchange.truncate(abs(Decimal(entry_price) - Decimal(stoploss)) * qty, config.max_precision)
        except Exception as e:
            pass
        return dollar_amount


    # You can view Maintenance Margin basic value under 'Contract Details' located on the bybit trading platform.
    def get_maintenance_margin_rate(self, symbol, qty, entry_price):
        self.connect_to_exchange()
        position_value = Decimal(qty) * Decimal(entry_price) # position_value is the value that you see under Positions tab -> Value column in the trading platform
        result = self.session.get_risk_limit(symbol=symbol).get('result')
        mmr_value = 0.0
        for i in range(len(result)):
            if(position_value <= result[i]['limit']):
                mmr_value = result[i]['maintain_margin']
                break
        return mmr_value


    # https://help.bybit.com/hc/en-us/articles/900000181046-Liquidation-Price-USDT-Contract-
    # Formulas: 
    #   Liquidation Price (Long) = Entry Price * (1 - Initial Margin Rate + Maintenance Margin Rate)
    #   Liquidation Price (Short) = Entry Price * (1 + Initial Margin Rate - Maintenance Margin Rate)
    def calculate_liq_price_for_isolated_margin(self, symbol, leverage, qty, entry_price, type):
        maintenance_margin_rate = self.get_maintenance_margin_rate(symbol=symbol, qty=qty, entry_price=entry_price)
        initial_margin_rate = 1 / leverage
        liquidation_price = 0.0
        if(type == config.LONG):
            liquidation_price = config.exchange.truncate(Decimal(entry_price) * (1 - Decimal(initial_margin_rate) + Decimal(maintenance_margin_rate)), config.max_precision)
        if(type == config.SHORT):
            liquidation_price = config.exchange.truncate(Decimal(entry_price) * (1 + Decimal(initial_margin_rate) - Decimal(maintenance_margin_rate)), config.max_precision)
        
        if(liquidation_price <= 0):
            liquidation_price = config.tick_size
            # liquidation_price, _ = self.get_tick_size(symbol=symbol)

        return liquidation_price # liquidation price uses mark price


    # https://help.bybit.com/hc/en-us/articles/900000181046-Liquidation-Price-USDT-Contract-
    # Formulas:
    #   Liquidation Price (Long) = Mark Price - (Available Balance + Initial Margin - Maintenance Margin)/Exposed Position Share
    #   Liquidation Price (Short) = Mark Price + (Available Balance + Initial Margin - Maintenance Margin)/Exposed Position Share
    def calculate_liq_price_for_cross_margin(self, symbol, qty, entry_price, initial_margin, type):
        maintenance_margin_rate = self.get_maintenance_margin_rate(symbol=symbol, qty=qty, entry_price=entry_price)
        mark_price, best_bid_price, best_ask_price = self.get_latest_symbol_info(symbol=symbol) # we only need Mark Price for liq price calculation
        available_balance = self.get_available_usdt_balance()
        maintenance_margin_amount = qty * entry_price * maintenance_margin_rate
        liquidation_price = 0.0
        if(type == config.LONG):
            liquidation_price = Decimal(mark_price) - (Decimal(available_balance) + initial_margin - maintenance_margin_amount) / qty
        if(type == config.SHORT):
            liquidation_price = Decimal(mark_price) + (Decimal(available_balance) + initial_margin - maintenance_margin_amount) / qty
        
        if(liquidation_price <= 0):
            liquidation_price = config.tick_size
            #liquidation_price, _ = self.get_tick_size(symbol=symbol)

        return liquidation_price # liquidation price uses mark price


    # You can send a GET request via postman https://api-testnet.bybit.com/v2/public/orderBook/L2/?symbol=BTCUSDT to see the output
    # For more information about order book response, check Order Book section under KnowledgeNotes and check Minimize Slippages section too.
    # I manually validated my result with ByBit platform order book successfully. Its accurate.
    # To avoid slippages, place order with a small qty.
    # This code has been test thoroughly and successfully.
    def get_entry_price_from_order_book(self, symbol, qty, type):
        order_book = self.session.orderbook(symbol=symbol).get('result')

        max_slippage_count = config.MAX_SLIPPAGE_PRICE_TIER # For example, max_slippage_count = 4 represents the 4th price tier in the order book.
        if type == config.LONG:
            aggregated_size = Decimal(0.0)
            slippage_counter = 0
            # Red zone (ask price)
            for i in range(25, 50): # Range: This secton 25 (25 is excluded), 50 handles long orders (Ask Prices). 25 to 50 represents the red order book area. Hence, range(25, 50) stars from line 26 (not 25) to 50
                slippage_counter += 1
                config.slippage_counter = slippage_counter
                if order_book[i].get('side') != 'Sell': # it's an issue, we expect to get side=Sell
                    telegram_send.send(messages=["CRITICAL - ByBit changed the response sequence of session.orderbook(). Ask price (Red Zone) used to appear from row 26 to 50 and Bid price (Greend Zone) used to appear from row 1 to 25. However, it looks like they switched it over. Go to your code get_entry_price_from_order_book() in ByBit.py and flip the ranges in the foor loop 'for i in range(fromX, toY)'"])
                    return -1
                if slippage_counter > max_slippage_count:
                    return -1 # -1 means we don't want to proceed with the order because it looks like our order would be 
                              # fulfilled in the later price tiers beyond max_slippage_count
                ask_price = order_book[i].get('price')
                aggregated_size = aggregated_size + Decimal(order_book[i].get('size'))
                if qty <= aggregated_size:
                    return ask_price # entry price for long order
        
        # Green zone (bid price)
        if type == config.SHORT:
            aggregated_size = Decimal(0.0)
            slippage_counter = 0
            for i in range(25): # Range: This secton 1 to 25 (25 is included) handles short orders (Bid Prices). 1 to 25 represents the green order book area
                slippage_counter += 1
                config.slippage_counter = slippage_counter
                if order_book[i].get('side') != 'Buy': # it's an issue, we expect to get side=Buy
                    telegram_send.send(messages=["CRITICAL - ByBit changed the response sequence of session.orderbook(). Ask price (Red Zone) used to appear from row 26 to 50 and Bid price (Greend Zone) used to appear from row 1 to 25. However, it looks like they switched it over. Go to your code get_entry_price_from_order_book() in ByBit.py and flip the ranges in the foor loop 'for i in range(fromX, toY)'"])
                    return -1
                if slippage_counter > max_slippage_count:
                    return -1 # -1 means we don't want to proceed with the order because it looks like our order 
                              # would be fulfilled in the later price tiers beyond max_slippage_count
                bid_price = order_book[i].get('price')
                aggregated_size = aggregated_size + Decimal(order_book[i].get('size'))
                if qty <= aggregated_size:
                    return bid_price # entry price for short order
                
 

    # determine_sl determines stoploss price by validating whether mid candle price (mid_price) is below the entry price for long trade and mid candle price is above
    # the entry price for short trade. We ideally look for mid_price to be the stoploss. determine_sl also allows us to add a buffer to our stoploss.
    # ticks_buffer: lets you move stoploss in number of ticks. You can provide whole number like 1, 2, 5, 6, etc OR decimals like 0.5
    # For example, if ticks_buffer is 1, it means we are shifting the stoploss by 1 minimum tick that is if BTCUSDT min tick size 0.5 and our ticks_buffer is 1
    # and our initial stoploss is $5, our stoploss after applying ticks_buffer will shift down to $4.5 for a long trade and it'll shift up to $5.5 in a short trade.
    # If you provide a ticks_buffer of 0.5 and assume BTCUSDT tick size 0.5, then the $5 stoploss would shift to $4.75 for long trade and $5.25 for short trade.
    # However, if our ticker_buffer is 0, our stoploss won't shift; it'll remain at $5. 
    # Check for an alternative SL logic in KnowledgeBase/AlternativeCode.txt file under "Alternative SL Logic" section
    # The logic below is equivelant to the one I have in tradingview backtest strategy.
    def determine_sl(self, symbol, possible_entry_price, mid_price, open, close, high, low, is_candle_green, \
                        mid_candle_mark_price, open_mark_price, close_mark_price, high_mark_price, low_mark_price, \
                        is_candle_green_for_mark_price, type, ticks_buffer):

        possible_entry_price = Decimal(possible_entry_price)
        
        mid_price = Decimal(mid_price)
        open = Decimal(open)
        close = Decimal(close)
        high = Decimal(high)
        low = Decimal(low)

        mid_candle_mark_price = Decimal(mid_candle_mark_price)
        open_mark_price = Decimal(open_mark_price)
        close_mark_price = Decimal(close_mark_price)
        high_mark_price = Decimal(high_mark_price)
        low_mark_price = Decimal(low_mark_price)
        
        sl_price = Decimal(0.0)
        sl_mark_price = Decimal(0.0) # we need to determine mark price of our stoploss so we can check later on if our mark price > liq price or not given that liq price is mark price.
        
        if type == config.LONG:
            sl_price = mid_price
            sl_mark_price = mid_candle_mark_price
            if sl_price > possible_entry_price: 
                if low < possible_entry_price:
                    sl_price = low
                    sl_mark_price = low_mark_price
                else:
                    sl_price = -1 # no viable SL.
            if sl_price != -1 and ticks_buffer > 0:
                mintick = config.tick_size
                #mintick, _ = self.get_tick_size(symbol=symbol) # mintick is the smallest unit you can move your SL in a chart
                sl_price = sl_price + (Decimal(mintick) * ticks_buffer * -1) # -1 means shift stoploss down because we added a negative sign * -1
                sl_mark_price = sl_mark_price + (Decimal(mintick) * ticks_buffer * -1)

        if type == config.SHORT:
            sl_price = mid_price
            sl_mark_price = mid_candle_mark_price
            if sl_price < possible_entry_price: 
                if high > possible_entry_price:
                    sl_price = high
                    sl_mark_price = high_mark_price
                else:
                    sl_price = -1 # no viable SL.
            if sl_price != -1 and ticks_buffer > 0:
                mintick = config.tick_size
                #mintick, _ = self.get_tick_size(symbol=symbol) # mintick is the smallest unit you can move your SL in a chart
                sl_price = sl_price + (Decimal(mintick) * ticks_buffer) # shifts stoploss up cause we didn't add a negative sign * -1
                sl_mark_price = sl_mark_price + (Decimal(mintick) * ticks_buffer)

        return config.exchange.truncate(sl_price, config.max_precision), config.exchange.truncate(sl_mark_price, config.max_precision)


    # https://help.bybit.com/hc/en-us/articles/900000630066-P-L-calculations-USDT-Contract-
    # Formula: Initial margin = (Qty x Entry price) / leverage = (0.2 x 7000) /10 = 140 USDT
    def calculate_initial_margin(self, required_qty, entry_price, leverage):
        return config.exchange.truncate((Decimal(required_qty) * Decimal(entry_price)) / Decimal(leverage), config.max_precision) 


    def get_latest_symbol_info(self, symbol):
        self.connect_to_exchange()
        latest_info_symbol = self.session.latest_information_for_symbol(symbol=symbol).get('result')
        mark_price = latest_info_symbol[0].get('mark_price')
        highest_bid_price = latest_info_symbol[0].get('bid_price') # entry price for short order. Fetches the highest bid price (green zone) from order book (1st price tier). I manually validated with ByBit platform order book successfully. Its accurate.
        lowest_ask_price = latest_info_symbol[0].get('ask_price') # entry price for long order. Fetches the lowest ask price (red zone) from order book (1st price tier). I manually validated with ByBit platform order book successfully. Its accurate.
        last_price = latest_info_symbol[0].get('last_price')
        return mark_price, highest_bid_price, lowest_ask_price, last_price


    def get_available_usdt_balance(self):
        self.connect_to_exchange()
        available_balance = self.session.get_wallet_balance(coin="USDT").get('result').get('USDT').get('available_balance')
        return Decimal(available_balance)


    # Source: https://bybit-exchange.github.io/docs/inverse/#t-querysymbol
    def get_tick_size(self, symbol):
        self.connect_to_exchange()
        result = self.session.query_symbol().get('result') # gives you back all symbols
        tick_size = Decimal(0.0)
        price_scale = Decimal(0.0)
        for i in range(len(result)):
            if(symbol == result[i].get('name')):
                tick_size = result[i].get('price_filter').get('tick_size')
                price_scale = result[i].get('price_scale') # Price scale is the number of decimal places to which a price can be submitted, although the final price may be rounded to conform to the tick_size
                break
        return tick_size, price_scale


    # https://bybit-exchange.github.io/docs/linear/#t-querykline or https://bybit-exchange.github.io/docs/linear/#t-markpricekline
    # previous_candle_index = 1 refers to current candle
    # Warning: get_1_min_candle_info() works well in mainnet (live server); it does not work well in testnet. To test get_1_min_candle_info()
    # you have to test it in live environment not testnet; for testing it in live server, just go to TESTNET_FLAG and set it to False
    def get_1_min_candle_info(self, symbol, previous_candle_index):
        current_date = datetime.utcnow()
        last_min = current_date - timedelta(minutes=previous_candle_index) # previous_candle_index = 1 refers to current candle
        last_min_ts = datetime.timestamp(last_min.replace(microsecond=0, tzinfo=timezone.utc))

        # Interval parameter refers to enum: 1 3 5 15 30 60 120 240 360 720 "D" "M" "W". For example, 720 refers to minutes worth of 12hrs
        # Limit parameter refers to data size. Limit has a max size is 200. Default as showing 200 pieces of data
        # Get ByBit platform (last traded) price
        candle = self.session.query_kline(symbol=symbol, interval="1", limit=1, from_time=last_min_ts).get('result')

        open_price = candle[0].get('open')
        close_price = candle[0].get('close')
        high_price = candle[0].get('high')
        low_price = candle[0].get('low')
        mid_candle_price = config.exchange.truncate((Decimal(candle[0].get('open')) + Decimal(candle[0].get('close'))) / 2, config.max_precision) # that's the stoploss I aim for
        is_candle_green = True if close_price > open_price else False

        # Get symbol's mark price. Mark price reflects the real-time spot price on the major exchanges. We need mark price 
        # We'll use mark price to determine whether our SL mark price > liq price given that liq price is mark price.
        candle_for_mark_price = self.session.query_mark_price_kline(symbol=symbol, interval="1", limit=1, from_time=last_min_ts).get('result')
        open_mark_price = candle_for_mark_price[0].get('open')
        close_mark_price = candle_for_mark_price[0].get('close')
        high_mark_price = candle_for_mark_price[0].get('high')
        low_mark_price = candle_for_mark_price[0].get('low')
        mid_candle_mark_price = config.exchange.truncate((Decimal(candle_for_mark_price[0].get('open')) + Decimal(candle_for_mark_price[0].get('close'))) / 2, config.max_precision) # that's the stoploss I aim for
        is_candle_green_for_mark_price = True if close_mark_price > open_mark_price else False

        return mid_candle_price, open_price, close_price, high_price, low_price, is_candle_green, \
                mid_candle_mark_price, open_mark_price, close_mark_price, high_mark_price, low_mark_price, is_candle_green_for_mark_price


    # Sometimes when you place an order, you get 'insufficient margin error' when
    # the position margin (initial margin) is larger than your available balance.
    # To avoid such error, lets see find out the required position margin and then
    # determine what's the minimum leverage do we ever need to place our trade successfully.
    # Ideally, I prefer to use 1x leverage because it gives you the biggest room for liq. price
    # The higher leverage, the smaller room you get for liq price. Remember, leverage doesn't
    # increase your profitability, In other words, leverage doesn't increase your position size i.e. qty.
    # Leverage simply lets you take a trade with your position size i.e. qty BUT with smaller position margin
    def get_minimum_required_leverage(self, qty, entry_price, available_bal):
        position_margin = Decimal(qty) * Decimal(entry_price)
        min_required_leverage = Decimal(position_margin) / Decimal(available_bal)
        if min_required_leverage < 1: min_required_leverage = 1
        # Jack up the leverage a little up so we guarantee our position margin would be a little less than the available balance
        # and we won't end up mistakenly end up with insufficient margin error due to decimal rounding of position margin.
        # For example, available balance go to multiple decimal places 900.4054 and position margin goes to 2 decimal places so
        # our position margin would technically show up 900.41 so when you place a trade, ByBit will throw a false positive error
        # insufficient margin because our position is one decimal 900.41 higher than available balance 900.4054. To avoid such a thing,
        # we ceil the leverage so we guarantee our position margin will be less than available balance and decimals discrepencies won't
        # get into our way
        return math.ceil(min_required_leverage)

        
    # This is equivelant to the logic of tradingview. This functional allows us to know
    # our target price at a specific ratio such as 1.5x, 5x, etc.
    # target_ratio takes decimal like 3.5 or 1.55 as well as integer as 5
    def get_target_price(self, type, entry_price, stoploss, target_ratio):
        entry_price = Decimal(entry_price)
        stoploss = Decimal(stoploss)
        points = abs(stoploss - entry_price) * Decimal(target_ratio)
        target_price = Decimal(0.0)
        if type == config.SHORT:
            target_price = entry_price - points
            #target_price = math.floor(target_price) # -23.11 becomes -24.0
        else:
            target_price = entry_price + points
            #target_price = math.ceil(target_price) # 300.16 becomes 301.0
        return config.exchange.truncate(target_price, config.max_precision)


    def delete_file(self, filename):
        if os.path.exists(filename):
            os.remove(filename)


    # Global variables are used to share data across different programs/threads such as main program and sockets
    # We reset global variables whenever we receive a new TV alert and about to start a new risk management and a new order process 
    def reset_global_variables(self):
        # Reset trailing SL variables. The variables are used in web sockets
        config.entry_price = 0.0
        config.stoploss = 0.0
        config.gap_to_sl = 0.0
        config.breakeven_target_price = 0.0
        config.profit_target_price = 0.0
        config.is_breakeven_hit = False
        config.stop_order_id = ''
        config.side = ''
        config.run_ws_flag = True
        config.symbol = ''
        config.init_sl=0.0          # this is specifically used in ws_stoporder to prevent incidents when we have open positions without stoploss
        config.init_qty=0.0         # this is specifically used in ws_stoporder to prevent incidents when we have open positions without stoploss
        config.tick_size=0.0
        config.max_precision=0 

        # Reset database variables. The variables are used during db insertion
        config.tvalert_id=0
        config.slippage_counter=0 
        config.exg_executed_at=None
        config.exg_order_id=''
        config.exg_trailed_profit_sl_counter=0.0


    # We save global variables in a file in case the main program
    # gets killed mistakenly or so. If main program is killed, all
    # global variables disappear. We need global variables to know how
    # our open position is progressing via web sockets, how much we trailed, etc, so
    # if main program gets killed while we have an open position in the exchange,
    # our program will grab whatever has been saved in the file and continue
    # monitoring via web sockets. Global variables is the only source we have
    # to monitor our websockets. 
    def save_global_variables_into_file(self, filename):
        try:
            file = open(filename, 'wb')
            global_vars = [
                    config.tvalert_id,
                    config.entry_price,
                    config.stoploss,
                    config.gap_to_sl,
                    config.breakeven_target_price,
                    config.profit_target_price,
                    config.is_breakeven_hit,
                    config.stop_order_id,
                    config.side,
                    config.exg_trailed_profit_sl_counter,
                    config.symbol,
                    config.init_sl,
                    config.init_qty, 
                    config.tick_size,
                    config.max_precision
            ]
            pickle.dump(global_vars, file)
            file.close()
        except Exception as e:
            # if we couldn't save global vars into file, its ok!
            # proceed trading cause our global variables are stored 
            # in heap. The only time we need to act on the exception is
            # when we read the file
            pass


    # Read global variables whenever you think we lost main program
    # connection/process and re-connected it. Losing main program
    # causes you to lose global variables from memory that is why
    # I'm grabbing our global variables from the file in case of
    # re-starting main program. Ideally, main program shouldn't fail
    # in production.
    def read_global_variables_from_file(self, filename):
        if not os.path.exists(filename):
            # Close the open position if any exist
            (pos_symbol, pos_size, pos_side) = self.get_first_open_position_info()
            if pos_size > 0:
                if pos_side == 'Buy': pos_side = config.LONG
                if pos_side == 'Sell': pos_side = config.SHORT
                try:
                    # reduce_only=True is used for closing orders. You don't necessarily need stoploss value to force close open position that's why i passed stop loss as 0.0
                    self.place_market_order(symbol=pos_symbol, side=pos_side, qty=pos_size, stop_loss=0.0, reduce_only=True)
                except Exception as err:
                    if config.MY_DEBUG_MODE:
                        print('[Debug mode] Pickle file was not found, so we could not continue monitoring the open position. Nevertheless, we did not find the open position in the platform anymore; it got closed already by the market movements, so we have no residual open position to force close are good now to receive new alerts. No issues.')
                    pass
                else: # execute else if no exception is found
                    # Store status into db. Since get_first_open_position_info() doesn't return order_id, I won't
                    # have a clear of identifying which db record to update. Thus, I chose to grab the latest
                    # table's id and update its status.
                    msg = 'Global vars pickle file went missing i.e. got deleted! We could not continue monitoring the open position. Therefore, I forced closed the open position for risk management and the program now waits for a new signal.'
                    max_id = db.session.query(func.max(models.TvAlert.id)).scalar()
                    models_utils.update_tradingview_alert(id=max_id, notes=msg)
                    if config.MY_DEBUG_MODE:
                        print('[Debug Mode] Global vars pickle file went missing i.e. got deleted! We could not continue monitoring the open position. Therefore, I forced closed the open position for risk management and the program now waits for a new signal.')
            return
        
        try:
            file = open(filename, 'rb')
            [
                config.tvalert_id,
                config.entry_price,
                config.stoploss,
                config.gap_to_sl,
                config.breakeven_target_price,
                config.profit_target_price,
                config.is_breakeven_hit,
                config.stop_order_id,
                config.side,
                config.exg_trailed_profit_sl_counter,
                config.symbol,
                config.init_sl,
                config.init_qty,
                config.tick_size,
                config.max_precision
            ] = pickle.load(file)
            file.close()
        except (EOFError, Exception) as e: # if you hit here, most probably the file is corrupted 
                                           # and the program can't read stored global variables, 
                                           # as a result, we can't monitor our open position and lost
                                           # track of our critical global variables as new sl, target profit,
                                           # etc. Therefore, if a file is corrupted, I choose to force close my
                                           # position without hesitation. Maybe the trade at the time of force close
                                           # is in the profit zone or maybe is not heading to the loss! who knows.
                                           # Shut the position down and wait for another singal. The goal here is to control the risk.
                                           # This error "EOFError: Ran out of input" means the file is corrupted.
            # Close the open position
            (pos_symbol, pos_size, pos_side) = self.get_first_open_position_info()
            if pos_size > 0:
                if pos_side == 'Buy': pos_side = config.LONG
                if pos_side == 'Sell': pos_side = config.SHORT
                try:
                    # reduce_only=True is used for closing orders. You don't necessarily need stoploss value to force close open position that's why i passed stop loss as 0.0
                    self.place_market_order(symbol=pos_symbol, side=pos_side, qty=pos_size, stop_loss=0.0, reduce_only=True)
                except Exception as err:
                    if config.MY_DEBUG_MODE:
                        print('[Debug mode] Pickle file was not found, so we could not continue monitoring the open position. Nevertheless, we did not find the open position in the platform anymore; it got closed already by the market movements, so we have no residual open position to force close are good now to receive new alerts. No issues.')
                    pass
                else: # execute else if no exception is found
                    # Store status into db. Since get_first_open_position_info() doesn't return order_id, I won't
                    # have a clear of identifying which db record to update. Thus, I chose to grab the latest
                    # table's id and update its status.
                    msg = 'Global vars pickle file is corrupted! We could not continue monitoring the open position. Therefore, I forced closed the open position for risk management and the program now waits for a new signal.'
                    max_id = db.session.query(func.max(models.TvAlert.id)).scalar()
                    models_utils.update_tradingview_alert(id=max_id, notes=msg)
                    if config.MY_DEBUG_MODE:
                        print('[Debug mode] Global vars pickle file is corrupted! We could not continue monitoring the open position. Therefore, I forced closed the open position for risk management and the program now waits for a new signal.')
            

    def place_market_order(self, symbol, side, qty, stop_loss, reduce_only):
        my_side = ''

        # time_in_force is ImmediateOrCancel (IOC) for market orders and GoodTillCancel (GTC) for limit order. As per my testing, you don't 
        # have other options for place_active_order. This article is easy to understand how 
        # IOC works https://help.bybit.com/hc/en-us/articles/360039749233-What-Are-Time-In-Force-TIF-GTC-IOC-FOK-
        # ret_code=0 and ext_code="" means create order success
        if side == config.LONG: my_side = 'Buy'
        if side == config.SHORT: my_side = 'Sell'

        # If reduce_only is True, it means we choose to force close the open position right in the middle of the trade.
        # To close an open position, do the following:
        #    1. Use the opposite side. If you placed a Buy order to open a position, place a Sell position for closing the order and vice versa
        #    2. The qty should be as same as the qty you used when you placed your order. For example, if you opened an order with 0.035, close it with 
        #       0.035 as well. If you close your order with a less qty to something else like 0.015, then 0.015 out of 0.035 will be CLOSED only and the open position qty
        #       will reduce from 0.035 to 0.020; the open position will still be open though. So to close the full position, the closing api call should have a qty that
        #       match the qty you used to open the position
        #    3. Set reduce_only to True
        if reduce_only == True:
            # We have to intentionally flip sides to force close an order
            if side == config.LONG: my_side = 'Sell'
            if side == config.SHORT: my_side = 'Buy'

        sl = config.exchange.truncate(stop_loss, config.max_precision) # Gives you decimal datatype
        sl = str(sl) # You had to convert Decimal to string. Otherwise, the api throws this exception Object of type Decimal is not JSON serializable 
                     # Wurzel Wurum said, the api is taking these params as 
                     # string, which allows to use the precision you/the exchange want (decimal datatypes are 
                     # usually represented as string internally, so decimal can be converted to string easily). What 
                     # you are using is a library for the bybit api (pybit I think?), which forces you to input these 
                     # params as float, which does not make sence in my opinion because the library has still to convert 
                     # it to string. (@dextertd you are the creator of pybit, right? Maybe consider accepting string for these params.) However 
                     # it is still better doing all the stuff with decimal datatype and convert it to float in the end than only 
                     # calculating everything with float, especially if you have coins, where the price is very low (like 0.00XXXX). Doing 
                     # many calculations or/and calculations with very small numbers with the float datatype will lead to very 
                     # bad precision (or call it mistake).
                     # As a result, I conveted decimal to string to maintain the fractional decimals and I told Wuzrel I hope pybit
                     # does not convert string back to float behind the scenes, Wurzel agreed. He said it'd be crazy if pybit converts string
                     # to float. I check the source code in github and I couldn't see float conversion, so we are good. Also, you can test
                     # SHIBA USDT to test how bybit behaves placing order for symbols priced in fractins like 0.0000xxx aka SHIB USDT or so.
                     # Dexter (pybit) developer said it does not cast/convert it to float; pybit takes your price string as it is and send it to bybit
        qty = str(qty) # You had to convert Decimal to string. Otherwise, the api throws this exception Object of type Decimal is not JSON serializable 

        order_response_number = 0
        order_response = self.session.place_active_order(
                symbol=symbol,
                side=my_side,
                qty=qty,
                order_type='Market',
                time_in_force='ImmediateOrCancel',
                stop_loss=sl, # ByBit automatically places the stoploss as a conditional market order
                reduce_only=reduce_only,
                close_on_trigger=False)
        
        if order_response.get('ret_code') == 0 and order_response.get('ext_code') == "":
            order_response_number = 1
            config.exg_executed_at = order_response.get('result').get('created_time')
            config.exg_order_id = order_response.get('result').get('order_id')
        elif order_response.get('ret_code') == 0 and order_response.get('ext_code') != "":
            order_response_number = 2
        elif order_response.get('ret_code') != 0:
            order_response_number = 3
        else:
            order_response_number = 4
        
        return order_response_number


    def get_stop_order_id(self, symbol):
        try:
            stop_ord_id = self.session.query_conditional_order(symbol=symbol).get('result')[0].get('stop_order_id')
        except Exception as e:
            return ''
        return stop_ord_id

    # This code gives you the first open position it encounters because
    # My atm_machine is built it take one trade at a time and so handle
    # one position at a time, so whatever open position info this code
    # returns, it'd be the position we are looking for because the program
    # assumes you either have 1 position only or none.
    def get_open_position_info(self, symbol):
        entry_price = 0.0 
        stoploss = 0.0
        liq_price = 0.0
        is_isolated = 0.0
        pos_margin = 0.0
        leverage = 0.0
        qty = 0.0

        open_position_response = self.session.my_position(symbol=symbol)
        for record in open_position_response.get('result'): 
            if record['size'] > 0 and record['symbol'] == symbol:
                entry_price = record.get('entry_price')
                stoploss = record.get('stop_loss')
                liq_price=record.get('liq_price')
                is_isolated=record.get('is_isolated')
                pos_margin=record.get('position_margin')
                leverage=record.get('leverage')
                qty=record.get('size')

        return entry_price, stoploss, liq_price, is_isolated, pos_margin, leverage, qty
    

    # Set leverage and pick isolated margin
    def set_isolated_margin(self, symbol, leverage):
        # https://bybit-config.exchange.github.io/docs/linear/#t-marginswitch
        try:
            config.exchange.session.set_leverage(symbol=symbol, buy_leverage=leverage, sell_leverage=leverage)
        except Exception as err:
            pass
        
        try:
            config.exchange.session.cross_isolated_margin_switch(symbol=symbol, is_isolated=True, buy_leverage=leverage, sell_leverage=leverage)
        except Exception as err:
            pass


    def change_stoploss(self, symbol, stop_order_id, new_stoploss_price):
        new_sl_price = config.exchange.truncate(new_stoploss_price, config.max_precision) # Gives you decimal datatype
        new_sl_price = str(new_sl_price) # You had to convert Decimal to string. 
                                        # Wurzel Wurum said, the api is taking these params as 
                                        # string, which allows to use the precision you/the exchange want (decimal datatypes are 
                                        # usually represented as string internally, so decimal can be converted to string easily). What 
                                        # you are using is a library for the bybit api (pybit I think?), which forces you to input these 
                                        # params as float, which does not make sence in my opinion because the library has still to convert 
                                        # it to string. (@dextertd you are the creator of pybit, right? Maybe consider accepting string for these params.) However 
                                        # it is still better doing all the stuff with decimal datatype and convert it to float in the end than only 
                                        # calculating everything with float, especially if you have coins, where the price is very low (like 0.00XXXX). Doing 
                                        # many calculations or/and calculations with very small numbers with the float datatype will lead to very 
                                        # bad precision (or call it mistake).
                                        # As a result, I conveted decimal to string to maintain the fractional decimals and I told Wuzrel I hope pybit
                                        # does not convert string back to float behind the scenes, Wurzel agreed. He said it'd be crazy if pybit converts string
                                        # to float. I check the source code in github and I couldn't see float conversion, so we are good. Also, you can test
                                        # SHIBA USDT to test how bybit behaves placing order for symbols priced in fractins like 0.0000xxx aka SHIB USDT or so.
                                        # Dexter (pybit) developer said it does not cast/convert it to float; pybit takes your price string as it is and send it to bybit

        config.exchange.session.replace_conditional_order(
                                    symbol=symbol,
                                    stop_order_id=stop_order_id,
                                    p_r_trigger_price=new_sl_price)


    # Call this function for debugging purposes only to see whether
    # our WSL2 time has drifted from ByBit server time a.k.a unix time.
    def compare_local_time_with_exchange_server_time(self):
        exg_server_time = self.session.server_time().get('time_now')
        exg_server_time = int(Decimal(exg_server_time))
        print('Exchange server time: ' + datetime.utcfromtimestamp(exg_server_time).strftime('%Y-%m-%d %H:%M:%S')) # convert unix time to a readable format

        local_server_time = int(time.time())
        print('WSL2 time: ' + datetime.utcfromtimestamp(local_server_time).strftime('%Y-%m-%d %H:%M:%S')) # convert unix time to a readable format
 
    
    # API response timezone is UTC always. API datetime looks like 2022-06-23T04:06:55.402188346Z. I use substring to pick 2022-06-23T04:06:55 only and exclude fractional seconds 
    def convert_api_datetime_from_str_to_obj(self, datetime_str):
        return datetime.strptime(datetime_str[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')


    # Check whether we are connected to bybit server
    def check_bybit_connection(self):
        try:
            self.session.server_time()
            self.is_connected = True
        except Exception as e:
            self.is_connected = False


    # Source: https://kodify.net/python/math/truncate-decimals/
    def truncate(self, number, decimals=0):
        """
        Returns a value truncated to a specific number of decimal places.
        """
        if not isinstance(decimals, int):
            raise TypeError("decimal places must be an integer.")
        elif decimals < 0:
            raise ValueError("decimal places has to be 0 or more.")
        elif decimals == 0:
            return math.trunc(number)

        factor = Decimal(10.0) ** decimals
        return math.trunc(Decimal(number) * Decimal(factor)) / Decimal(factor)

