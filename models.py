from sqlalchemy.dialects.postgresql import JSON
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy()

# Big Warning! Do not rename the class. If you rename the class e.g. TvAlert to TvAlertX, SQLAlchemy will drop TvAlert completely and re-create a new model called TvAlertX
# Any field post-fixed with _config, it means this field represents a constant value from our config.py file
# exg_ columns represent the actual values we got from the exchange's platform after placing the order
# All datetime columns are stored in UTC timezone. Also, db.Float is interpreted in postgres as 'double precision' data type 
# and as per this source https://stackoverflow.com/questions/13113096/how-to-round-an-average-to-2-decimal-places-in-postgresql 
# 'double precision' does not round your decimals which is good because throughout my program I only truncate decimasl to a certain precision and
# I want my postgres tvalert to reflect that exact decimals propogated by my program and I do not want my decimals to be rounded up in postgres.
class TvAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exchange = db.Column(db.String(20))
    symbol = db.Column(db.String(50))
    command = db.Column(db.String(30))
    type = db.Column(db.String(7))
    received_at = db.Column(db.DateTime)
    my_init_entry_price = db.Column(db.Float, nullable=True) # This price represents last price and it is used to calculate position qty.
    my_init_best_order_book_price = db.Column(db.Float, nullable=True) # This is the most accurate market price before placing a market order. 
                                                                       # To get the best price from the order book, we need to know our qty first. 
                                                                       # I use this price to do rest of calculations like leverage, position margin, etc.
                                                                       # This price as known as actual entry price and its the one to be picked by ByBit
                                                                       # platform if I place an active market order.
    my_slippage_counter = db.Column(db.Float, nullable=True) # If 1, it means no slippage happened, we pick the best price in the order book.
                                                             # If counter is larger than 1, it means slippage had happened. The bigger the tier, the bigger slippage
                                                             # This counter lets us know the price tier of our my_init_best_order_book_price. Ideally, we want
                                                             # our LONG market order to pick the first ask price from the order book and for short order, pick first 
                                                             # bid price from the order book.
    my_max_slippage_counter_config = db.Column(db.Float, nullable=True)
    my_init_sl_price = db.Column(db.Float, nullable=True)                   # This sl is of type last price
    my_init_sl_mark_price = db.Column(db.Float, nullable=True)              # This sl is of type mark price
    my_init_be_price = db.Column(db.Float, nullable=True)                   # BE stands for breakeven
    my_init_profit_target = db.Column(db.Float, nullable=True)              # Our target profit determined by my_profit_rw_config. We trail sl the moment the chart crosses our target profit
    my_liq_mark_price = db.Column(db.Float, nullable=True)                  # Liqudiation price is always of type mark price
    my_sl_vs_liq_gap_perc = db.Column(db.Float, nullable=True)              # stoploss here represents last price stoploss
    my_mark_sl_vs_liq_gap_perc = db.Column(db.Float, nullable=True)
    my_max_leverage_config = db.Column(db.Float, nullable=True)
    my_leverage = db.Column(db.Float, nullable=True)                        # The estimated leverage we need to set to place our order successfully
    my_init_ep_sl_gap = db.Column(db.Float, nullable=True)                  # Store points gap between last price and SL. We use last price until here calculate_crypto_required_qty()
    my_init_best_ord_book_ep_sl_gap = db.Column(db.Float, nullable=True)    # Store points gap between best price from order book and SL. We use best price from order book after this function calculate_crypto_required_qty()
    my_qty = db.Column(db.Float, nullable=True)                             # The size that allows us to enter the trade with our my_risk_amount
    my_position_margin = db.Column(db.Float, nullable=True)
    my_risk_perc = db.Column(db.Float, nullable=True)
    my_risk_amount = db.Column(db.Float, nullable=True)                     # Its the amount we want to risk
    my_be_rw_config = db.Column(db.Float, nullable=True)
    my_profit_rw_config = db.Column(db.Float, nullable=True)
    my_executed_at = db.Column(db.DateTime, nullable=True)
    is_executed = db.Column(db.Boolean(), nullable=True)
    notes = db.Column(db.Text(), nullable=True)
    exg_executed_at = db.Column(db.DateTime, nullable=True)
    exg_order_id = db.Column(db.String(), nullable=True)
    exg_stop_order_id = db.Column(db.String(), nullable=True)
    exg_is_isolated = db.Column(db.String(), nullable=True)
    exg_qty = db.Column(db.Float, nullable=True) 
    exg_avail_balance = db.Column(db.Float, nullable=True) 
    exg_pos_margin = db.Column(db.Float, nullable=True) 
    exg_init_entry_price = db.Column(db.Float, nullable=True)
    exg_init_sl_last_price = db.Column(db.Float, nullable=True)
    exg_init_ep_sl_gap = db.Column(db.Float, nullable=True)         
    exg_liq_mark_price = db.Column(db.Float, nullable=True)
    exg_leverage = db.Column(db.Float, nullable=True)               # The actual leverage that got placed by the exchange
    exg_trailed_entry_price = db.Column(db.Float, nullable=True)
    exg_trailed_profit_target = db.Column(db.Float, nullable=True)
    exg_trailed_sl_price = db.Column(db.Float, nullable=True)
    exg_trailed_profit_sl_counter = db.Column(db.Float, nullable=True)
    exg_passed_bev_target = db.Column(db.Boolean(), nullable=True) # bev is breakeven
    exg_passed_profit_target = db.Column(db.Boolean(), nullable=True)
    exg_close_trigger_price = db.Column(db.Float, nullable=True)
    exg_close_at = db.Column(db.DateTime, nullable=True)
    my_expected_risk_amount = db.Column(db.Float, nullable=True) # This is closest estimated risk amount to the actual order risk amount. Its calculated based on the estimated actual entry price from order book 
    exg_risk_amount = db.Column(db.Float, nullable=True) # This is the actual risk amount in the exchange after we placed the order

    def __init__(self, exchange, symbol, command, type, received_at, is_executed, notes): 
        self.exchange = exchange
        self.symbol = symbol
        self.command = command
        self.type = type
        self.received_at = received_at 
        self.is_executed = is_executed
        self.notes = notes

    # Allows us to update any field inside the model
    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        db.session.commit()

    def __repr__(self):
        return f'<TvAlert ID: {self.id}>'


# Keep track of generic logs such as critical HTTP bybit server connection failure or so
class MyLogs(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime)
    notes = db.Column(db.Text(), nullable=True)

    def __init__(self, notes): 
        self.created_at = datetime.strptime(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), '%Y-%m-%d %H:%M:%S')
        self.notes = notes
    
    def __repr__(self):
        return f'<MyLogs ID: {self.id}>'