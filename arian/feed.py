import json
import subprocess
import sys
import time
import copy
from pathlib import Path


class MT5Feed:
    def __init__(self, config):
        self.config = config
        self.history_identity = None
        self.history_minute = None
        self.history = {}

    def call(self, **kwargs):
        payload = dict(terminal_path=self.config['terminal_path'], symbols=self.config['symbols'],
                       broker_clock=self.config.get('broker_clock', {'mode':'utc'}), **kwargs)
        try:
            result = subprocess.run([sys.executable, '-m', 'arian.mt5_worker'], input=json.dumps(payload),
                                    text=True, capture_output=True, timeout=12,
                                    cwd=Path(__file__).resolve().parent.parent,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                return dict(connected=False, uncertain=kwargs.get('action', 'snapshot') != 'snapshot',
                            error='خطای ارتباط محلی MT5؛ مسیر و نصب کتابخانه را بررسی کنید')
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            return dict(connected=False, uncertain=kwargs.get('action', 'snapshot') != 'snapshot',
                        error='ارتباط با MT5 پاسخ نداد؛ متاتریدر را باز کنید و وارد حساب شوید')
        except (ValueError, OSError):
            return dict(connected=False, uncertain=kwargs.get('action', 'snapshot') != 'snapshot',
                        error='ارتباط محلی MT5 برقرار نشد')

    def snapshot(self, **kwargs):
        minute = int(time.time()//60)
        incomplete = any(len(self.history.get((symbol,tf),[])) < 120
                         for symbol in self.config['symbols'] for tf in ('M1','M5'))
        include_history = not self.history or minute != self.history_minute or incomplete
        feed = self.call(action='snapshot', include_history=include_history, **kwargs)
        if not feed.get('connected'):
            return feed
        if feed['identity'] != self.history_identity and not include_history:
            # Never attach another account's cached candles to a new account.
            feed = self.call(action='snapshot', include_history=True, **kwargs)
            include_history = True
            if not feed.get('connected'):
                return feed
        if include_history:
            self.history_identity, self.history_minute = feed['identity'], minute
            self.history = {}
        for symbol, market in feed.get('markets',{}).items():
            for name, frame in market.get('timeframes',{}).items():
                key = (symbol,name)
                if 'bars' in frame:
                    self.history[key] = frame['bars']
                else:
                    frame['bars'] = copy.deepcopy(self.history.get(key,[]))
            market['m1'] = market.get('timeframes',{}).get('M1',{}).get('bars',[])
            market['m5'] = market.get('timeframes',{}).get('M5',{}).get('bars',[])
        return feed
