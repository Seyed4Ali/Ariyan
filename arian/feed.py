import json
import subprocess
import sys
import time
import copy
import os
import threading
from pathlib import Path


class MT5Feed:
    def __init__(self, config):
        self.config = config
        self.history_identity = None
        self.history_minute = None
        self.history = {}
        self.execution_audits = {}
        self.audit_identity = None
        self.audit_thread = None
        self.audit_result = None
        self.next_audit = 0

    def call(self, **kwargs):
        payload = dict(verified_utc_offset_seconds=self.config.get('verified_utc_offset_seconds',0),terminal_path=self.config['terminal_path'], symbols=self.config['symbols'],
                       observed_magic_numbers=self.config.get('observed_magic_numbers', []),
                       broker_clock=self.config.get('broker_clock', {'mode':'utc'}), **kwargs)
        try:
            # MT5 imports NumPy. This IPC reader needs no BLAS thread pool;
            # limiting it avoids OpenBLAS allocation failures on busy hosts.
            worker_env = dict(os.environ, OPENBLAS_NUM_THREADS='1')
            result = subprocess.run([sys.executable, '-m', 'arian.mt5_worker'], input=json.dumps(payload),
                                    text=True, capture_output=True, timeout=45 if kwargs.get('action') == 'audit' else 12,
                                    env=worker_env,
                                    cwd=Path(__file__).resolve().parent.parent,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                return dict(connected=False, uncertain=kwargs.get('action', 'snapshot') not in ('snapshot', 'audit'),
                            error='خطای ارتباط محلی MT5؛ مسیر و نصب کتابخانه را بررسی کنید')
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            return dict(connected=False, uncertain=kwargs.get('action', 'snapshot') not in ('snapshot', 'audit'),
                        error='ارتباط با MT5 پاسخ نداد؛ متاتریدر را باز کنید و وارد حساب شوید')
        except (ValueError, OSError):
            return dict(connected=False, uncertain=kwargs.get('action', 'snapshot') not in ('snapshot', 'audit'),
                        error='ارتباط محلی MT5 برقرار نشد')

    def snapshot(self, **kwargs):
        minute = int((time.time()+self.config.get('verified_utc_offset_seconds',0))//60)
        incomplete = any(len(self.history.get((symbol,tf),[])) < 120
                         for symbol in self.config['symbols'] for tf in ('M1','M5'))
        include_history = not self.history or minute != self.history_minute or incomplete
        feed = self.call(action='snapshot', include_history=include_history,
                         audited_positions=[pid for pid,audit in self.execution_audits.items()
                             if audit.get('status')!='unavailable' or time.time()-audit.get('checked_at',0)<300], **kwargs)
        if not feed.get('connected'):
            return feed
        if feed['identity'] != self.history_identity and not include_history:
            # Never attach another account's cached candles to a new account.
            feed = self.call(action='snapshot', include_history=True, **kwargs)
            include_history = True
            if not feed.get('connected'):
                return feed
        if self.audit_identity != feed['identity']:
            self.execution_audits = {}
            self.audit_identity = feed['identity']
        # A separate read-only worker owns potentially slow historical tick downloads.
        # Only this foreground thread mutates the audit cache; late account results are discarded.
        if self.audit_result is not None:
            result = self.audit_result
            self.audit_result = None
            if result.get('identity') == feed['identity']:
                self.execution_audits.update(result.get('execution_audits', {}))
        if (self.config.get('historical_tick_audit_enabled', False) and feed.get('tracking_history_complete') and self.config.get('observed_magic_numbers') and time.time() >= self.next_audit
                and (self.audit_thread is None or not self.audit_thread.is_alive())):
            self.next_audit = time.time() + 300
            audit_args = dict(kwargs, audited_positions=list(self.execution_audits))
            def fetch_audit():
                self.audit_result = self.call(action='audit', **audit_args)
            self.audit_thread = threading.Thread(target=fetch_audit, daemon=True, name='mt5-history-audit')
            self.audit_thread.start()
        self.execution_audits.update(feed.get('execution_audits',{}))
        feed['execution_audits'] = copy.deepcopy(self.execution_audits)
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
