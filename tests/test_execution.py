import csv
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from arian.mt5_worker import run as worker_run
from backtest import run as replay, aggregate_m5
from arian.strategy import Bar
from test_core import CONFIG, NOW, SPEC


class BrokerGateTests(unittest.TestCase):
    def test_worker_rejects_real_account_even_if_engine_requests_order(self):
        module=SimpleNamespace(initialize=Mock(return_value=True),shutdown=Mock(),
            terminal_info=lambda:SimpleNamespace(connected=True,trade_allowed=True,tradeapi_disabled=False),
            account_info=lambda:SimpleNamespace(server='test',login=1,currency='USD',trade_mode=0,
                equity=10000,balance=10000,trade_allowed=True),ACCOUNT_TRADE_MODE_DEMO=1,
            order_send=Mock())
        with patch.dict('sys.modules',{'MetaTrader5':module}):
            with self.assertRaisesRegex(ValueError,'دمو'):
                worker_run(dict(terminal_path='unused',action='demo_open',identity='test:1'))
        module.order_send.assert_not_called()
        module.shutdown.assert_called_once()


class ReplayTests(unittest.TestCase):
    def test_m5_requires_five_closed_minutes(self):
        bars=[Bar(NOW+i*60,1,2,.5,1.5) for i in range(9)]
        result=aggregate_m5(bars)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].time,NOW)

    def test_ambiguous_ohlc_bar_resolves_stop_first(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as temp:
            root=Path(temp);path=root/'fixture.csv'
            with path.open('w',newline='') as file:
                w=csv.writer(file);w.writerow(['time','open','high','low','close','volume','spread_points'])
                for i in range(25):w.writerow([NOW+i*60,1.1,1.102,1.098,1.1,10,2])
            manifest={'markets':{'EURUSD':{'csv':str(path),'spec':SPEC}}}
            def signal(symbol,m1,m5,config):
                return {'candidate':None if not m1 else dict(symbol=symbol,direction='BUY',sl=1.0995,
                    atr=.0005,signal_time=m1[-1].time+60,cycle_id=str(m1[-1].time),features={'symbol':symbol})}
            with patch('backtest.analyze',side_effect=signal):
                report=replay(manifest,root/'out',unfiltered=True)
            self.assertGreater(report['metrics']['closed_count'],0)
            self.assertEqual(report['metrics']['win_rate'],0)
            from arian.storage import Store
            trades=Store(root/'out'/'research.sqlite').trades()
            self.assertTrue(all(t['status']=='STOP' for t in trades))


if __name__=='__main__':unittest.main()
