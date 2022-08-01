# Constants used across the application
TARGET_EXCHANGE='BYBIT' # Decide what exchange does this program interacts with. This value should match the exchange name that we received from tradingview alert
TESTNET_FLAG = True     # If True, the program connects to testnet. Otherwie, it connects to the live server (mainnet)
TESTNET_ENDPOINT = 'https://api-testnet.bybit.com'
LIVE_ENDPOINT = 'https://api.bybit.com'
RECV_WINDOW = 5000          # recv_window is in milliseconds and its used when connecting to the exchange's server via HTTP()
FLASK_DEBUG_FLAG = True     # Set it to False once you deploy to production
MY_DEBUG_MODE=True          # This flag switches my code to be debugging friendly. This allows you to print values for easy visual tracking and debugging purposes 
SKIP_VALIDATION=True        # This flag switches my code to be debugging friendly. This allows you to test place market order by skipping all of the pre-order validations such as risk management calculations, etc. 
WS_TRACE_LOGGING = False    # Set it to True if you want your websockets to throw logs. Its useful for debugging complex websocket issues.
LONG = 'LONG'               # This value should match the one we receive from tradingview alert json request
SHORT = 'SHORT'             # This value should match the one we receive from tradingview alert json request
EXECUTE_MARKET_ORDER = 'EXECUTE_MARKET_ORDER' # This value should match the one we receive from tradingview alert json request
LIQ_GAP_PERC = 8            # this number represents a fraction of a 100 e.g. 8 means 8%, 1 means 1%, 0.4 means 0.4% (less than 1%), etc. Its the minimum threshold for the gap between stoploss and liquidation price
BREAKEVEN_RW_RATIO = 0.5    # Its risk-reward ratio i.e. 1x, 10x, etc. Make sure its 1.5 before deploying to prod
PROFIT_RW_RATIO = 1.5       # Its risk-reward ratio i.e. 1x, 10x, etc. Mke sure its 5 before deploying to prod
WS_PING_INTERVAL_SECS = 30  # Its used when create web socket connection
WS_PING_TIMEOUT_SECS = 20   # Its used when create web socket connection
MAX_LEVERAGE = 5            # That's the maximum threshold for leverage I'm willing to go for.
RISK_PERCENTAGE = 0.01      # How much money I'm willing to risk per trade. 0.01 is 1%, 0.05 is 5%, 1 is 100%
GLOBAL_VARS_FILE = 'my_global_vars.pickle' # Its the file name that holds my global variable values
MAX_SLIPPAGE_PRICE_TIER = 7                     # The number represents the nth price tier in the order book.
MAX_RETRY_COUNTER_FOR_HIGH_SLIPPAGE = 3         # I retry calling some failed APIs. This count defines the maximum retry I'm willing to go for.
MAX_RETRY_COUNTER_FOR_HTTP_CONNECT = 10         # I retry calling some failed APIs. This count defines the maximum retry I'm willing to go for.
MAX_RETRY_COUNTER_FOR_CHECKING_OPEN_POS = 10    # I retry calling some failed APIs. This count defines the maximum retry I'm willing to go for.
MAX_RETRY_COUNTER_FOR_PLACE_ACTIVE_ORDER = 3    # I retry calling some failed APIs. This count defines the maximum retry I'm willing to go for.
WS_KLINE_NAME='ws_kline_thread'                 # Its used for initializing websocket connection
WS_STOPORDER_NAME='ws_stoporder_thread'         # Its used for initializing websocket connection
WS_SERVER_DOMAIN='bybit'                        # Its used for initializing websocket connection
SL_TICKS_BUFFER=0                               # Its used in determine_sl() to a buffer to your stoploss. For bullish trades, the buffer pushes
                                                # the stoploss down and for bearish trades, the buffer pushes the stoploss up.
                                                # For example, sl_ticks_buffer lets you move stoploss in number of ticks. You can provide whole number like 1, 2, 5, 6, etc OR decimals like 0.5
                                                # For example, if ticks_buffer is 1, it means we are shifting the stoploss by 1 minimum tick that is if BTCUSDT min tick size 0.5 and our ticks_buffer is 1
                                                # and our initial stoploss is $5, our stoploss after applying ticks_buffer will shift down to $4.5 for a long trade and it'll shift up to $5.5 in a short trade.
                                                # If you provide a ticks_buffer of 0.5 and assume BTCUSDT tick size 0.5, then the $5 stoploss would shift to $4.75 for long trade and $5.25 for short trade.
                                                # However, if our ticker_buffer is 0, our stoploss won't shift; it'll remain at $5. 
                                                # Check for an alternative SL logic in KnowledgeBase/AlternativeCode.txt file under "Alternative SL Logic" section
                                                # The logic below is equivelant to the one I have in tradingview backtest strategy.

###################### Global Variables ###########################
#[IMPORTANT #1]: Whenever you add a new global variable that requires resetting, make sure you reset it in bybit.py inside reset_global_variables
#[IMPORTANT #2]: Whenever you add a new global variable below, make sure you add it in save_global_variables_into_file() and read_global_variables_from_file() 
#               inside bybit.py if you want to store that global variable in a file and most importantly make sure you assign a value to the new global variables
#               in app.py before you start websocket threads.

# This object gives you access to exchange's API. Doesn't require resetting 
exchange = None

# These variables are used for trailing SL in web sockets. Requires resetting reset_global_variables
symbol=''
entry_price = 0.0
stoploss = 0.0
gap_to_sl = 0.0 # The distance between entry to stoploss in points/ticks
breakeven_target_price = 0.0
profit_target_price = 0.0
is_breakeven_hit = False
stop_order_id = ''
side = ''
run_ws_flag = True # Keep it True by default. Otherwise, sockets may have issues booting up
app = None
tick_size=0.0      # We need it cause we keep using tick_size across application
max_precision=0      # Its called price_scale. It basically lets us know the max decimal we can allow in our stoploss before submitting our active order

init_sl=0.0          # initialized the first time we place an order only. I need the first initial value only for a specific use case in ws_stoporder
init_qty=0.0         # initialized the first time we place an order only. I need the first initial value only for a specific use case in ws_stoporder

# These variables are used to store logs in the database. Requires resetting reset_global_variables
tvalert_id=0            # Each new alert gets stored in a database with a unique id. This variable holds alert's database unique ID throughout the trade from opening the order to close
slippage_counter=0      # Lets us know estimate how far our estimated selected entry price have slipped from the best price in the order book
exg_executed_at=None    # Stores the date/time our order got executed in the exchange
exg_order_id=''         # Stores the order id of our open position
exg_trailed_profit_sl_counter=0.0 # Keeps a count of how many times have you we trailed our stoploss (excluding breakeven).
                                  # If exg_trailed_profit_sl_counter is 1, it means the market has crossed our first profit target price. 
                                  # In other words, If PROFIT_RW_RATIO is 5x, it means the market passed 5x and our SL got trailed to 4x. 
                                  # If exg_trailed_profit_sl_counter is 2, it means the market passed 10x and our SL got trailed to 8x, etc.
