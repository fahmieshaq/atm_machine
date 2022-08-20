from decimal import Decimal
from symtable import Symbol
from sys import api_version
import json

from importlib_metadata import entry_points
from flask import Flask, request
from pybit.usdt_perpetual import HTTP, WebSocket
from bybit import ByBit
from datetime import datetime, timedelta
import config, keys
import threading
import signal
from threading import Thread
from time import sleep
import time
import telegram_send
from flask_migrate import Migrate
from models import db
import models_utils
import pytz
import ws_stoporder, ws_kline # keep these two imports last because imports run in sequence in case they rely on upper imports

# IMPORTANT NOTES: app.py file is a blueprint that's applicable to any exchange I connect with.
#                  The file calls exchange.py wrappers which in turn calls the exchange's API.
#                  The logic in app.py follows the risk management I need to do before placing any
#                  crypto order and any change you make in here will be applicable to all exchanges.
#                  This main program, classes, and function are catered to handle one position only and won't allow you to open
#                  a second position as long as you have an open position in your platform. Do not use this
#                  program for managing multiple trades at once; it won't work. Instead, you'd need to develop another program
#                  for multiple trades. I chose to handle one open position at a time for risk management...

# Setup flask
app = Flask(__name__)

# Setup ORM and data models
app.config['SQLALCHEMY_DATABASE_URI'] = keys.DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
app.app_context().push() # its required for insert and update to work properly
migrate = Migrate(app, db)

# Initialize variables
leverage = 1
risk_amount = 0.0
available_balance = 0.0

# Initialize debug variables. Debug variables will take place only if config.MY_DEBUG_MODE is True
# Fyi, order type LONG or SHORT (tradingview_alert['type']) has to be changed from the tradingview alert json request
debug_leverage_val=Decimal(1)
debug_my_qty_val=Decimal(10000)        #Set it to 0 if you want to pick the actual risk management value during MY_DEBUG_MODE. SKIP_VALIDATION has to be False for risk management to apply
debug_sl_price_val=Decimal(0.0119955)  #Set it to 0 if you want to pick the actual risk management value during MY_DEBUG_MODE. SKIP_VALIDATION has to be False for risk management to apply

# The moment you initiate the app, connect to the exchange
config.exchange = ByBit()


# This webhook receives tradingview alert json request. We process the json request and place an order in the exchange
# This webhook takes one alert at a time only and it never processes an alert if we already have an open position. Also,
# this webhook deals with one exchange only; it deals with the exchange that's defined here config.TARGET_EXCHANGE
@app.route("/webhook", methods=['POST'])
def webhook():
    config.exchange.connect_to_exchange()

    # Receive tradingview json request
    tradingview_alert = json.loads(request.data)
    
    # Check are we sending the alert to the appropriate exchange
    if tradingview_alert['exchange'] != config.TARGET_EXCHANGE:
        msg = 'CRITICAL: Alert is sent to the wrong exchange. Target exchange should be ' + config.TARGET_EXCHANGE
        telegram_send.send(messages=[msg])
        return {'code': 'error', 'message': msg}

    # Verify password
    if tradingview_alert['passphrase'] != keys.TV_ALERT_PASSPHRASE:
        msg = 'CRITICAL: WARNING, your tradingview passphrase is malformed!'
        telegram_send.send(messages=[msg])
        return {'code': 'error', 'message': msg}

    if tradingview_alert['command'] != config.EXECUTE_MARKET_ORDER:
        msg = 'CRITICAL - Hold on! Something wrong with tradingview "command" alert value. ' + config.EXECUTE_MARKET_ORDER + ' is expected but you received ' + tradingview_alert['command'] + '.'
        telegram_send.send(messages=[msg])
        return {'code': 'error', 'message': msg}

    # If MY_DEBUG_MODE is True, you've to hardcode leverage, my_qty, and sl_price
    # Setting MY_DEBUG_MODE to True allows you to place market order by skipping all 
    # of the pre-order validations such as risk management calculations, etc. 
    msg_is_debug_mode = ''
    if config.MY_DEBUG_MODE:
        try:
            config.exchange.compare_local_time_with_exchange_server_time()
            msg_is_debug_mode = '[DEBUG MODE: True] '
            leverage=debug_leverage_val # This will only take affect if SKIP_VALIDATION is set to True
            my_qty=debug_my_qty_val     # This will take affect whether SKIP_VALIDATION is set to True or False. Its subjective, search for the other my_qty=debug_my_qty_val for more info
            sl_price=debug_sl_price_val # This will only take affect if SKIP_VALIDATION is set to True
        except Exception as e:
            print('Disconnected from bybit server due to this exception: ' + str(e))
    
    # We receive datetime from tradingview in this format 30/5/2022 22:8:0 and postgres stores it in this format 2022-05-30 21:08:00
    # and convert it to UTC datetime object
    received_at_est = datetime.strptime(tradingview_alert['time'], '%d/%m/%Y %H:%M:%S') # convert alert datetime from string to datetime object
    received_at_utc = received_at_est.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S") # tradingview_alert['time'] comes in EST timezone. Lets convert it to UTC
    received_at_utc = datetime.strptime(received_at_utc, '%Y-%m-%d %H:%M:%S') # Convert UTC string datetime back to datetime object

    # If you don't have an open position in the platform AND we have open websockets, kill your active websockets if any exist
    # so your new alert trade starts clean without any residual data from previous alert's websockets.
    # Search KnowledgeNotes.txt for multiprocessing, an easy alternative solution to threading
    is_open_position_found = config.exchange.is_open_position_available()
    if not is_open_position_found:
        thread_names = [t.name for t in threading.enumerate()]
        if "ws_stoporder_thread" in thread_names:
            config.run_ws_flag = False
        else:
            pass # Ignore. We don't have an active websocket

        if "ws_kline_thread" in thread_names:
            config.run_ws_flag = False
        else:
            pass # Ignore. We don't have an active websocket
        
    # Store alert in the database to track the progress of the alert from json request acquisition to order execution
    tvalert_id = 0
    tvalert_id = models_utils.insert_tradingview_alert(exchange=tradingview_alert['exchange'], symbol=tradingview_alert['symbol'],
                                            command=tradingview_alert['command'], type=tradingview_alert['type'],
                                            received_at=received_at_utc,
                                            is_executed=False, # It means the order hasn't been executed yet
                                            notes=msg_is_debug_mode + 'First call')

    # Although you get connected to the server the moment you ran the main app.py, do a courtesy connection check.
    config.exchange.connect_to_exchange()
    if config.exchange.is_connected:
        msg = msg_is_debug_mode + '[Passed] config.exchange.is_connected is True'
        models_utils.update_tradingview_alert(id=tvalert_id, notes=msg, 
                                                my_be_rw_config=config.BREAKEVEN_RW_RATIO,
                                                my_profit_rw_config=config.PROFIT_RW_RATIO,
                                                my_max_slippage_counter_config=config.MAX_SLIPPAGE_PRICE_TIER,
                                                my_max_leverage_config=config.MAX_LEVERAGE
                                                )

        # --- 0. Do we have any open trade? If yes, stop here. 
        if is_open_position_found:
            config.app = app # You'll need it in websockets to access sqlalchemy insert/update
            config.exchange.check_and_reconnect_sockets() # Do a courtesy check on the sockets. The sockets are responsible for trailing stoplosses
            msg = msg_is_debug_mode + '[Ignore Alert] is_open_position_available() - We already have one open position. Do not open a second one.'
            models_utils.update_tradingview_alert(id=tvalert_id, is_executed=False, notes=msg)
            return {'code': 'error', 'message': msg}

        # At this point, we are sure we don't have an open position and ready to apply risk management and execute the acquired alert. 

        # --- 0.1 Reset global variables because we are starting a new risk management and a new order process ---
        config.exchange.reset_global_variables()
        config.exchange.delete_file(filename=config.GLOBAL_VARS_FILE) # Delete the file that holds some of our global variables

        config.tvalert_id = tvalert_id # save to global variables after resetting global variables above. It is pointless to 
                                       # globalize tvalert_id before resetting
        
        config.app = app # Globalize app because inside websockets you'll need to place your insert/update functions within with app.app_context()
                         # so your insert/update don't end up throwing this exception: No application found. Either work inside a view function or push an application context.

        # Get generic data that we'll need through our risk management calculations. Tick size and price scale (aka max decimal)
        # are important for determing up to how many decimals your symbol takes. For example, if you trade BTCUSDT and you
        # place an active order with a stoploss of 30,000.534423432, your api will fail because BTCUSDT does not permit
        # that many decimals. To know the max decimal per symbol, you need to get price scale from ByBit and use
        # price scale as max decimal limit for the symbol you are trading with. For example, SHIB1000USDT has a tick
        # size of 0.000005 USDT and its price scale is 6 (you get price scale from an API call), so when you open
        # position with SHIB1000USDT, make sure whatever price you pass such as stoploss, limit entry price, etc has
        # covers up enough decimal up of price scale.
        tick_size, price_scale = config.exchange.get_tick_size(symbol=tradingview_alert['symbol'])
        config.tick_size=Decimal(tick_size)
        config.max_precision=int(price_scale)

        # --- 1. determine risk amount ---
        available_balance = config.exchange.get_available_usdt_balance() # We need available balance to apply for risk management
        if available_balance <= 0:
            msg = msg_is_debug_mode + '[Issue] get_available_usdt_balance() - No available balance.'
            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg, exg_avail_balance=available_balance)
            return {'code': 'error', 'message': msg}
        

        risk_amount = available_balance * Decimal(config.RISK_PERCENTAGE) # Determine your risk amount in dollars. Its the amount we want to risk
        models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, 
                                                exg_avail_balance=available_balance,
                                                my_risk_perc=config.RISK_PERCENTAGE,
                                                my_risk_amount=risk_amount,
                                                notes=msg_is_debug_mode + '[Passed] get_available_usdt_balance()')

        if not config.SKIP_VALIDATION:
            # --- 2. determine the possible entry price ---
            mark_price, bid_price, ask_price, last_price = config.exchange.get_latest_symbol_info(symbol=tradingview_alert['symbol'])

            # ask_price is best buy entry price and bid_price is best short entry price. We will use it to calculate position size (qty)
            if tradingview_alert['type'] == config.LONG: best_entry_price = ask_price
            if tradingview_alert['type'] == config.SHORT: best_entry_price = bid_price

            # --- 3. determine proper stoploss ---
            # mid_price is the stoploss. Its a mid price of previous candle between open and close.
            # previous_candle_index = 1 represents current candle and previous_candle_index = 2 represents candle before the current candle.
            # You can go back as many candles as you want by inserting the candle index in the previous_candle_index parameter.
            # sl_price first return value gives you the middle of the candle between open and close.
            # is_candle_green refers to the candle of our previous_candle_index value.
            # Warning: get_1_min_candle_info() works well in mainnet (live server); it does not work well in testnet. To test get_1_min_candle_info()
            # you have to test it in live environment not testnet; for testing it in live server, just go to TESTNET_FLAG and set it to False
            # We return one candle ONLY candle that is positioned in SL_CANDLE_IDX
            mid_price, open, close, high, low, is_candle_green, \
            mid_mprice, open_m, close_m, high_m, low_m, is_candle_green_m = config.exchange.get_1_min_candle_info(symbol=tradingview_alert['symbol'], previous_candle_index=config.SL_CANDLE_IDX)

            # we only use sl_mark_price later on to check if our sl_mark_price hits before liquidation price or not; sl_mark_price has no other use. For rest of
            # risk management calculations, we use sl_price that is bybit platform's last traded stoploss price. To know more about ticks_buffer, go to determine_sl
            # definition in bybit.py. I entered last_price value as possible_entry_price because when I determine our SL visually, I look at last price on chart.
            # last_price aka possible_entry_price allows us to know whether our elected SL is below or above last price and last_price does not have any other role here
            sl_price, sl_mark_price = config.exchange.determine_sl(symbol=tradingview_alert['symbol'], possible_entry_price=last_price, mid_price=mid_price, \
                                                open=open, close=close, high=high, low=low, is_candle_green=is_candle_green, \
                                                mid_candle_mark_price=mid_mprice, open_mark_price=open_m, close_mark_price=close_m, high_mark_price=high_m, \
                                                low_mark_price=low_m, is_candle_green_for_mark_price=is_candle_green_m, \
                                                type=tradingview_alert['type'], ticks_buffer=config.SL_TICKS_BUFFER)
            
            if config.MY_DEBUG_MODE:
                if debug_sl_price_val == 0:
                    pass
                else:
                    sl_price=debug_sl_price_val
                print('[Debug Mode] last price: ' + str(last_price))
                print('[Debug Mode] stoploss: ' + str(sl_price))
                
            if sl_price == -1: # sl_price = -1 means there wasn't a proper stoploss, so ignore the trade
                msg = msg_is_debug_mode + '[Issue] determine_sl() - Issue with determining stoploss.'
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg)
                return {'code': 'error', 'message': msg}

            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False,  
                                                    my_init_sl_price=sl_price, my_init_sl_mark_price=sl_mark_price,
                                                    my_init_entry_price=best_entry_price,
                                                    notes=msg_is_debug_mode + '[Passed] determine_sl()')

            # --- 4. calculate our required qty that lets us lose our risk amount at most once our trade (best price is last trade price) hits our SL price (SL is last traded price not mark price) ---
            my_qty = config.exchange.calculate_crypto_required_qty(risk_amount=risk_amount, entry_price=best_entry_price, stoploss=sl_price) # best_entry_price is the best price in order book without slippage
            if my_qty <= 0:
                msg = msg_is_debug_mode + '[Issue] calculate_crypto_required_qty() - Issue with qty. Probably there was divison by zero exception because the previous bar is too thin like a dash and so the current is too thin like a dash as well due to very low volatility.'
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, my_qty=my_qty, notes=msg)
                return {'code': 'error', 'message': msg}

            if config.MY_DEBUG_MODE:
                print('[Debug Mode] risk amount: ' + str(risk_amount))
                print('[Debug Mode] pre-orderbook my_qty: ' + str(my_qty))
                print('[Debug Mode] pre-orderbook best_entry_price: ' + str(best_entry_price))
                print('[Debug Mode] pre-orderbook sl_price: ' + str(sl_price))

            my_init_ep_sl_gap = abs(Decimal(best_entry_price) - Decimal(sl_price)) # This does not necessarily reflect the actual estimated gap of the order you are about to 
                                                                             # make! my_init_best_ord_book_ep_sl_gap below gives you more accurate gap that would almost
                                                                             # match the exchange's gap. We store my_init_ep_sl_gap for our reference only.
                                                                             # The gap between sl and last price here does not consider slippages, the higher the slippage
                                                                             # the bigger the gap and the only way to know if we'll encounter a slippage is to go take
                                                                             # my_qty and check it in the order book; hence, we are doing it in the next step get_entry_price_from_order_book()
                                                                             # my_init_ep_sl_gap would match the exchange's gap if our order does not encounter slippages
            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, my_qty=my_qty, my_init_ep_sl_gap=my_init_ep_sl_gap,
                                                    notes=msg_is_debug_mode + '[Passed] calculate_crypto_required_qty()')

            # During risk management validation process you face challenges with quantity
            # as sometimes we get high quantity that requires multiple price tiers from the
            # order book which basically denotes to a high slippage. Plus, high qty
            # requires high position margin that we may not have available. Qty is key. If qty
            # is high, then most probably one of the next validation will fail; at last, I can
            # think of position_margin would fail. To avoid getting stoppoed by validation checks,
            # reduce the my_qty to something very small like 0.035. I usually debug and test on BTCUSDT
            # and as known BTCUSDT is expensive. Other coins may not encounter my_qty issue as often.
            # For example,  here is rough calculation! if my_qty is 0.8, it means I need 0.8 btc to open a 1x leverage position, which 
            # means i will need a position margin of approx 30,000 * 0.8 = 24,000 approx; in this case, you can
            # use leverage like 5x to chip in a $4,800 (24,000/5x) instead of chipping 24,000. Still! trading
            # BTCUSDT in the live account can be expensive if you don't have proper capital. I found out that adding
            # high risk amount with a tight stoploss here calculate_crypto_required_qty() yields to quantity.
            if config.MY_DEBUG_MODE:
                if debug_my_qty_val == 0:
                    pass # pass means lets use the calculated qty and avoid using the harcoded one debug_my_qty_val
                else:
                    my_qty=debug_my_qty_val # force a low quantity so rest of validations below don't stop you as often.
                                        # Hence, I had to do this because I was testing with BTCUSDT. If you test
                                        # with averagely priced symbols like SOLUSDT, you may not face qunatity validation issues


            # --- 5. now determine the most possibly accurate entry price we anticipate bybit would pick for our market order. ---
            # Why didn't we the actual entry price right from the beginning? Why we had to pick possible entry price and then actual entry price?
            # Because to identify the actual entry price (closest accurate price) from the order book, we need to know our required qty first and based
            # on the required qty, we can look at the order book and estimate the actual entry price at that moment. You cannot get required qty
            # until you have an entry price, so I had get the estimated entry price (best ask/bid price) from latest symbol api response just to calculate
            # the required qty and based on our qty, we leech into the order book to pick best estimated (actual) entry price that I expect ByBit to pick
            # for my market order execution. To know more about picking from order book, please refer to my personal video in the "--- Order Book ---" section 
            # in my KnowledgeNotes.txt
            def get_entry_price_from_order_book():
                return config.exchange.get_entry_price_from_order_book(symbol=tradingview_alert['symbol'], qty=my_qty, type=tradingview_alert['type'])

            retry_counter = 0
            actual_entry_price = get_entry_price_from_order_book()
            while (actual_entry_price == -1 or actual_entry_price is None) and retry_counter < config.MAX_RETRY_COUNTER_FOR_HIGH_SLIPPAGE: 
                retry_counter += 1
                sleep(config.SLEEP_SECONDS_FAST)
                actual_entry_price = get_entry_price_from_order_book()
                
            if actual_entry_price == -1 or actual_entry_price is None: # most probably we are facing slippage issues. Sometimes for uncelar reasons, get_entry_price_from_order_book() returns None
                msg = msg_is_debug_mode + '[Issue] get_entry_price_from_order_book() - Issues with fetching actual entry price due to high slippage. My slippage counter is ' + str(config.slippage_counter) + '+ and our max slippage price tier config is ' + str(config.MAX_SLIPPAGE_PRICE_TIER)
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg,
                                                        my_slippage_counter=config.slippage_counter)
                return {'code': 'error', 'message': msg}

            # --- 5.1. Re-calculate our qty using the actual entry entry because actual entry price simulates the entry price if you were to execute the order now ---
            # actual_entry_price is the closest price the real price you'd get after executing the order. Acutal_entry_price considers the slippages if applicable.
            # To estimate the entry price of a market order, you need to have qty; no other way. Thus, we first had to get qty using the best price as entry price.
            # best_entry_price does not consider the slippage, it simply represents the best ask price for a long order and best bid price for a short order.
            # If actual_entry_price did not encounter slippages then most probably actual_entry_price would be almost close or identical to best_entry_price, and so
            # qty as well remain same. However, if a slippage happens then actual_entry_price would little far from best_entry_price. Thus, my_qty value would change
            # after qty. Nevertheless, the risk amount will remain the same. Qty tends to be higher for a tight stoploss and qty tends to be lower for a large stoploss,
            # which means the larger the gap between entry price and stoploss, the lower the qty and the tighter the gap, the higher the qty AND risk amount remains same
            my_qty = config.exchange.calculate_crypto_required_qty(risk_amount=risk_amount, entry_price=actual_entry_price, stoploss=sl_price)
            if my_qty <= 0:
                msg = msg_is_debug_mode + '[Issue] Recalculate qty with calculate_crypto_required_qty() using the entry price from get_entry_price_from_order_book() - Issue with qty. Probably there was divison by zero exception because the previous bar is too thin like a dash and so the current is too thin like a dash as well due to very low volatility.'
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, my_qty=my_qty, notes=msg)
                return {'code': 'error', 'message': msg}

            # Get the gap between actual entry price and sl
            my_init_best_ord_book_ep_sl_gap = abs(Decimal(actual_entry_price) - Decimal(sl_price))

            if config.MY_DEBUG_MODE:
                if debug_my_qty_val == 0:
                    pass
                else:
                    my_qty=debug_my_qty_val

            # Get risk amount after applying the actual entry price. This step is added for conformity only. Nothing much
            my_expected_risk_amount = config.exchange.get_usdt_amount_using_crypto_qty(entry_price=actual_entry_price, stoploss=sl_price, qty=my_qty)
            
            if config.MY_DEBUG_MODE:
                print('[Debug Mode] my_expected_risk_amount: ' + str(my_expected_risk_amount))
                print('[Debug Mode] post-orderbook my_qty: ' + str(my_qty))
                print('[Debug Mode] post-orderbook actual entry price: ' + str(actual_entry_price))
                print('[Debug Mode] post-orderbook sl_price: ' + str(sl_price))
                print('[Debug Mode] post-orderbook slippage_counter: ' + str(config.slippage_counter))
                
            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, 
                                                    my_init_best_order_book_price=actual_entry_price, 
                                                    my_slippage_counter=config.slippage_counter,
                                                    my_init_best_ord_book_ep_sl_gap=my_init_best_ord_book_ep_sl_gap,
                                                    my_expected_risk_amount=my_expected_risk_amount,
                                                    my_qty=my_qty,
                                                    notes=msg_is_debug_mode + '[Passed] get_entry_price_from_order_book()')

            # --- 6. Identify the minimum leverag I can use to place the trade. We do this step to avoid 'insufficient margin error' when placing a trade ---
            # Ideally, I prefer to go with 1x leverage so I can have the biggest liq price room possible but sometimes 1x
            # leads to insufficient margin error because sometimes 1x leverage requires big position/initial margin larger than 
            # my available balance, so get_minimum_required_leverage helps us know what's the minimum leverage I can use to place
            # my trade successfully without getting insufficient margin error. Go to the function definition for more info.
            leverage = config.exchange.get_minimum_required_leverage(qty=my_qty, entry_price=actual_entry_price, available_bal=available_balance)
            if leverage > config.MAX_LEVERAGE:
                msg = msg_is_debug_mode + '[Issue] get_minimum_required_leverage() - Too much leverage ' + str(leverage) + 'x over our max leverage config ' + str(config.MAX_LEVERAGE) + 'x'
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg, my_leverage=leverage)
                return {'code': 'error', 'message': msg}
            
            if config.MY_DEBUG_MODE:
                print('[Debug Mode] leverage: ' + str(leverage))

            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, my_leverage=leverage,
                                                    notes=msg_is_debug_mode + '[Passed] get_minimum_required_leverage()')

            # --- 6.1 Position margin is how much money does ByBit need to hold for you to take the trade. When you hit a stoploss, you'll lose your
            # risk amount only from your position margin. If you hit liquidation price for isolated margin, you lose your whole 
            # position margin. FYI, cross margin trade makes you lose all of your available balance if you hit liq price. My automated
            # trading uses isolated margin.
            position_margin = config.exchange.calculate_initial_margin(required_qty=my_qty, entry_price=actual_entry_price, leverage=leverage)
            if position_margin >= available_balance:
                msg = msg_is_debug_mode + '[Issue] calculate_initial_margin() - Position margin ' + str(position_margin) + ' is larger than available balance ' + str(available_balance) + '.'
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg, my_position_margin=position_margin)
                return {'code': 'error', 'message': msg}

            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, my_position_margin=position_margin,
                                                    notes=msg_is_debug_mode + '[Passed] calculate_initial_margin()')

            # --- 7. get liq price for isolated margin. Liq_price uses mark price ---
            liq_price = config.exchange.calculate_liq_price_for_isolated_margin(symbol=tradingview_alert['symbol'], leverage=leverage, qty=my_qty, entry_price=actual_entry_price, type=tradingview_alert['type'])
            # our mark stoploss price must hit before liquidation does. Liq price represents mark price. That is why 
            # I had to check sl_mark_price <= liq_price. I chose to add sl_price <= liq_price as well for courtesy check only just in case
            msg = msg_is_debug_mode + '[Issue] calculate_liq_price_for_isolated_margin() - Stoploss is hit after liquidation price.'
            if tradingview_alert['type'] == config.LONG:
                if sl_mark_price <= liq_price \
                    or sl_price <= liq_price: 
                    models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg, my_liq_mark_price=liq_price)
                    return {'code': 'error', 'message': msg}
            if tradingview_alert['type'] == config.SHORT:
                if sl_mark_price >= liq_price \
                    or sl_price >= liq_price: 
                    models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg, my_liq_mark_price=liq_price)
                    return {'code': 'error', 'message': msg}

            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, 
                                                    my_liq_mark_price=liq_price,
                                                    notes=msg_is_debug_mode + '[Passed] calculate_liq_price_for_isolated_margin()')

            # --- 8. Identify how far the stoploss from the liquidation price ---
            sl_vs_liq_gap = (abs(liq_price - sl_price)/sl_price) * 100 # the number is in % like 50%, 1.4%, 0.5% (less than 1%), etc
            mark_sl_vs_liq_gap = (abs(liq_price - sl_mark_price)/sl_mark_price) * 100 # the number is in % like 50%, 1.4%, 0.5% (less than 1%), etc
            # Number 8 is the gap percentage between stoploss and liquidation price. 8%+ in 1 min is a big gap
            if sl_vs_liq_gap < config.LIQ_GAP_PERC or mark_sl_vs_liq_gap < config.LIQ_GAP_PERC:
                msg = msg_is_debug_mode + '[Issue] The gap between last price stoploss and liquidation price is ' + str(sl_vs_liq_gap) + ' and/or stoploss mark price ' + str(mark_sl_vs_liq_gap) + ' less than ' + str(config.LIQ_GAP_PERC) + '%. In short, either mark SL or last price SL is very close to liquidation price.'
                models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, notes=msg)
                return {'code': 'error', 'message': msg}

            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=False, my_sl_vs_liq_gap_perc=sl_vs_liq_gap, 
                                                    my_mark_sl_vs_liq_gap_perc=mark_sl_vs_liq_gap,
                                                    notes=msg_is_debug_mode + '[Passed] The gap between stoploss and liquidation price is ' + str(mark_sl_vs_liq_gap) + '% LARGER than minimum threshold ' + str(config.LIQ_GAP_PERC) + '%.')
        ############## END - if not config.SKIP_VALIDATION #################

        # --- 9. Make sure your system is in Isolated Margin mode and setup the leverage ---
        config.exchange.set_isolated_margin(symbol=tradingview_alert['symbol'], leverage=leverage)

        # --- 10. Place a market order (It'll be an isolated margin trade with the leverag we set above) ---
        if tradingview_alert['command']  == config.EXECUTE_MARKET_ORDER:
            is_success = False

            # These variables help us retry api in case failure happens
            is_exception_found = True
            retry_count = 0
            while is_exception_found and retry_count < config.MAX_RETRY_COUNTER_FOR_PLACE_ACTIVE_ORDER:
                try:
                    # For opening positions, make sure reduce_only is False
                    order_response_number = config.exchange.place_market_order(symbol=tradingview_alert['symbol'], side=tradingview_alert['type'], qty=my_qty, stop_loss=sl_price, reduce_only=False)
                except Exception as err:
                    is_success = False
                    is_exception_found = True
                    retry_count += 1
                    msg = '[Issue] Order failed to execute due to this exception: ' + str(err)
                    #sleep(config.SLEEP_SECONDS_FAST)
                    config.exchange.connect_to_exchange()
                else:
                    is_exception_found = False
    
                    msg = ''
                    if order_response_number == 1:
                        is_success = True
                        msg = '[Passed] Order executed. You are at place_market_order(). ' + 'order_response_number: ' + str(order_response_number)
                    elif order_response_number == 2:
                        is_success = True
                        msg = '[Passed] Order executed with parameter issues. You are at place_market_order(). ' + 'order_response_number: ' + str(order_response_number)
                    elif order_response_number == 3:
                        is_success = False
                        msg = '[Issue] Order failed to execute! You are at place_market_order(). ' + 'order_response_number: ' + str(order_response_number)
                    elif order_response_number == 4:
                        is_success = False
                        msg = '[Issue] Order failed to execute for unknown reasons! You are at place_market_order(). ' + 'order_response_number: ' + str(order_response_number)
                    else:
                        is_success = False
                        msg = '[Issue] Order failed. No clue why. You are at place_market_order(). order_response_number: ' + str(order_response_number)
        else:
            is_success = False
            # I moved the warning message up at the beginning of the code:
            # msg = 'CRITICAL - Hold on! Something wrong with tradingview "command" alert value. ' + config.EXECUTE_MARKET_ORDER + ' is expected. You are at place_market_order().'
            # telegram_send.send(messages=[msg])


        if is_success == True:
            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=is_success, 
                        notes=msg_is_debug_mode + msg,
                        exg_executed_at=config.exchange.convert_api_datetime_from_str_to_obj(config.exg_executed_at), 
                        exg_order_id=config.exg_order_id,
                        exg_passed_bev_target=False,
                        exg_passed_profit_target=False,
                        my_executed_at=datetime.strptime(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), '%Y-%m-%d %H:%M:%S'))    
        else:
            models_utils.update_tradingview_alert(id=config.tvalert_id, is_executed=is_success, 
                                                    notes=msg_is_debug_mode + msg)
            if config.MY_DEBUG_MODE:
                print(msg_is_debug_mode + msg)
            return {'code': 'error', 'message': msg}

        # --- 11. Get your open position entry price and stoploss (These are the final prices that got executed in the platform) ---
        entry_price, stoploss, liq_price, is_isolated, pos_margin, leverage, qty = config.exchange.get_open_position_info(symbol=tradingview_alert['symbol'])
        points_between_ep_and_sl = abs(entry_price - stoploss)
        exg_risk_amount = config.exchange.get_usdt_amount_using_crypto_qty(entry_price=entry_price, stoploss=stoploss, qty=qty)

        # store into db
        models_utils.update_tradingview_alert(id=config.tvalert_id, 
                                                exg_init_entry_price=entry_price,
                                                exg_init_sl_last_price=stoploss,
                                                exg_liq_mark_price=liq_price,
                                                exg_is_isolated=is_isolated,
                                                exg_pos_margin=pos_margin,
                                                exg_leverage=leverage,
                                                exg_trailed_sl_price=stoploss,
                                                exg_qty=qty,
                                                exg_init_ep_sl_gap=points_between_ep_and_sl,
                                                exg_risk_amount=exg_risk_amount,
                                                notes=msg_is_debug_mode + '[Passed] Order executed. You are at get_open_position_info()')

        # --- 12. Identify our target prices ----
        breakeven_target_price = config.exchange.get_target_price(type=tradingview_alert['type'], entry_price=entry_price, stoploss=stoploss, target_ratio=config.BREAKEVEN_RW_RATIO)
        profit_target_price = config.exchange.get_target_price(type=tradingview_alert['type'], entry_price=entry_price, stoploss=stoploss, target_ratio=config.PROFIT_RW_RATIO)

        models_utils.update_tradingview_alert(id=config.tvalert_id, my_init_be_price=breakeven_target_price, 
                                                my_init_profit_target=profit_target_price,
                                                notes=msg_is_debug_mode + '[Passed] Order executed. You are at breakeven_target_price() and profit_target_price()')

        # --- 13. Get the id of the conditional stop-order because we'll need the id for trailing ---
        stop_order_id = config.exchange.get_stop_order_id(symbol=tradingview_alert['symbol']) 
        models_utils.update_tradingview_alert(id=config.tvalert_id, 
                                                exg_stop_order_id=stop_order_id,
                                                notes=msg_is_debug_mode + '[Passed] Order executed. You are at get_stop_order_id()')

        # --- 14. Start monitoring the chart keep trailing SL as necessarily needed ---
        # Move the values to global variables for the monitor to use
        config.symbol = tradingview_alert['symbol']
        config.entry_price = entry_price
        config.stoploss = stoploss
        config.gap_to_sl = points_between_ep_and_sl
        config.breakeven_target_price = breakeven_target_price
        config.profit_target_price = profit_target_price
        config.is_breakeven_hit = False
        config.stop_order_id = stop_order_id
        config.side = tradingview_alert['type']
        config.init_sl=stoploss
        config.init_qty=qty

        if config.MY_DEBUG_MODE:
            print('************ Start ' + config.side + ' Trading ***********')
            print('symbol: ' + tradingview_alert['symbol'])
            print('qty: ' + str(config.init_qty))
            print('entry price: ' + str(entry_price))
            print('stoploss: ' + str(stoploss))
            print('profit target: ' + str(profit_target_price))
            print('breakeven target: ' + str(config.breakeven_target_price))
            print('gap_to_sl: ' + str(config.gap_to_sl))
            print('tick_size: ' + str(config.tick_size))
            print('max_precision: ' + str(config.max_precision))

        # Save global variables in case main program shuts down. When you re-run
        # main program, you program will pick up from where your sockets global 
        # variables left. 
        config.exchange.save_global_variables_into_file(filename=config.GLOBAL_VARS_FILE)

        # Search KnowledgeNotes.txt for multiprocessing, an easy alternative solution to threading
        thread_names = [t.name for t in threading.enumerate()]
        if "ws_stoporder_thread" not in thread_names:
            config.run_ws_flag = True
            ws_stoporder_thread = Thread(target=ws_stoporder.ws_stoporder_fun, name=config.WS_STOPORDER_NAME)
            ws_stoporder_thread.start()
        else:
            pass # We don't create another stop ws because its already running

        if "ws_kline_thread" not in thread_names:
            config.run_ws_flag = True
            ws_kline_thread = Thread(target=ws_kline.ws_kline_fun, args=(tradingview_alert['symbol'], stop_order_id, tradingview_alert['type']), name=config.WS_KLINE_NAME)
            ws_kline_thread.start()
        else:
            pass # We don't create another stop ws because its already running

        return {'code': 'success', 'message': 'Order executed! Done.'}
    else:
        config.exchange.connect_to_exchange()


# Keep this code at the bottom
if __name__ == "__main__":
    # The moment you run the main app, check if we ever have an open position. If we do
    # have an open position, boot up the sockets if they are not running. If you already have
    # an open position, then there will be a file called my_global_vars.pickle that holds
    # our global variable values, which check_and_reconnect_sockets() reads from and continue
    # monitoring based on latest values we stored in my_global_vars.pickle.
    config.exchange.connect_to_exchange()
    if config.exchange.is_open_position_available():
        config.app = app # You'll need it in websockets to access sqlalchemy insert/update
        config.exchange.check_and_reconnect_sockets()
    else: # Ideally, when you launch a the main app (app.py), you won't have lingering websockets because
          # when main app dies, its websocket threads die too; BUT, I included the code below for conformity
          # only in case an unexpected incident happens.
        # If you don't have an open position in the platform AND we have open websockets, kill your active websockets if any exist
        # so your new alert trade starts clean without any residual data from previous alert's websockets.
        # Search KnowledgeNotes.txt for multiprocessing, an easy alternative solution to threading
        thread_names = [t.name for t in threading.enumerate()]
        if "ws_stoporder_thread" in thread_names:
            config.run_ws_flag = False
        else:
            pass # Ignore. We don't have an active websocket

        if "ws_kline_thread" in thread_names:
            config.run_ws_flag = False
        else:
            pass # Ignore. We don't have an active websocket
    app.run(debug=config.FLASK_DEBUG_FLAG, use_reloader=False, host='0.0.0.0') # Sometimes its better to set realoder to False. Re-loader 
                                                               # is useful if you don't want to re-run local server after every change
                                                               # However, re-loadeder tends to double execute main() commands after 
                                                               # saving every code modification.
                                                               # host='0.0.0.0' is required for production. It allows us to receive
                                                               # external requests
