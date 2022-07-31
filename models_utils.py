from models import db
import models
import config
from datetime import datetime
import pytz


def insert_tradingview_alert(exchange, symbol, command, type, received_at, is_executed, notes):
    obj = models.TvAlert(exchange, symbol, command, type, received_at, is_executed, notes)
    try:
        db.session.add(obj)
        db.session.commit()
    except Exception as err:
        print(err)
        
    return obj.id


# Call it as update_tradingview_alert(id=33, whatever1='val1', whatever2='val2'):
def update_tradingview_alert(id, **kwargs):
    obj = models.TvAlert.query.filter_by(id=id).first() # or models.TvAlert.query.get(id=id)
    obj.update(**kwargs)


def insert_mylogs(notes):
    obj = models.MyLogs(notes)
    try:
        db.session.add(obj)
        db.session.commit()
    except Exception as err:
        print('We could not insert into the db due to this exception: ' + str(err))
        
    return obj.id