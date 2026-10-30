import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from arian.engine import Engine
from arian.feed import MT5Feed
from arian.timeframes import TIMEFRAMES
from arian.risk import trade_window
from test_core import CONFIG, NOW, CANDIDATE, TICK, SPEC


class AccountBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data')
        self.path=Path(self.temp.name)/'account.sqlite'
        self.engine=Engine(copy.deepcopy(CONFIG),self.path)
        self.account=dict(identity='demo:123',demo=True,currency='USD',balance=444.62,
                          equity=444.62,server='demo',account_login=123)

    def tearDown(self):
        self.temp.cleanup()

    def test_seed_matches_demo_balance_and_risk(self):
        self.assertTrue(self.engine.bind_paper_account(self.account,NOW))
        self.assertEqual(self.engine.paper.metrics(NOW)['initial'],444.62)
        self.assertEqual(self.engine.baseline.metrics(NOW)['initial'],444.62)
        trade=self.engine.paper.open(CANDIDATE,TICK,SPEC,NOW)
        self.assertIsNotNone(trade)
        self.assertLessEqual(trade['risk_money'],444.62*.0025)

    def test_poll_and_restart_do_not_erase_simulated_loss(self):
        self.engine.bind_paper_account(self.account,NOW)
        trade=self.engine.paper.open(CANDIDATE,TICK,SPEC,NOW)
        self.engine.paper.mark('EURUSD',dict(time=NOW+1,bid=trade['sl'],ask=trade['sl']+.00002),NOW+1)
        balance=self.engine.paper.metrics(NOW+1)['balance']
        self.assertLess(balance,444.62)
        changed=dict(self.account,balance=888)
        self.engine.bind_paper_account(changed,NOW+2)
        self.assertEqual(self.engine.paper.metrics(NOW+2)['balance'],balance)
        restarted=Engine(copy.deepcopy(CONFIG),self.path)
        restarted.bind_paper_account(changed,NOW+3)
        self.assertEqual(restarted.paper.metrics(NOW+3)['balance'],balance)

    def test_account_switch_preserves_and_separates_ledgers_and_learning(self):
        self.engine.bind_paper_account(self.account,NOW)
        stream_a=self.engine.paper.stream
        self.engine.paper.open(CANDIDATE,TICK,SPEC,NOW)
        self.engine.learner.state['version']=2
        self.engine.learner.save()
        self.engine.bind_paper_account(dict(self.account,identity='demo:456',balance=900),NOW+1)
        self.assertNotEqual(stream_a,self.engine.paper.stream)
        self.assertEqual(self.engine.paper.metrics(NOW+1)['equity'],900)
        self.assertEqual(self.engine.paper.metrics(NOW+1)['open_count'],0)
        self.assertEqual(self.engine.learner.state['version'],0)
        self.engine.bind_paper_account(self.account,NOW+2)
        self.assertEqual(self.engine.paper.stream,stream_a)
        self.assertEqual(self.engine.paper.metrics(NOW+2)['open_count'],1)
        self.assertEqual(self.engine.learner.state['version'],2)

    def test_real_zero_and_invalid_balances_do_not_seed(self):
        for changes in ({'demo':False},{'balance':0},{'balance':float('nan')},{'currency':'EUR'}):
            self.assertFalse(self.engine.bind_paper_account(dict(self.account,**changes),NOW))
        self.assertIsNone(self.engine.capital_binding)


class TimeframeAndFeedTests(unittest.TestCase):
    def test_supported_timeframes_reach_one_hour(self):
        self.assertEqual(list(TIMEFRAMES),['M1','M2','M3','M4','M5','M6','M10','M12','M15','M20','M30','H1'])
        self.assertEqual(TIMEFRAMES['H1'],3600)

    def test_bitcoin_session_is_separate_from_fx_weekend(self):
        self.assertTrue(trade_window(NOW+86400,CONFIG,'BTCUSD'))
        self.assertFalse(trade_window(NOW+86400,CONFIG,'EURUSD'))
        self.assertTrue(trade_window(NOW+12*3600,CONFIG,'BTCUSD'))
        self.assertFalse(trade_window(NOW+12*3600,CONFIG,'XAUUSD'))

    def test_cache_reuses_closed_bars_but_never_other_account_history(self):
        feed=MT5Feed(CONFIG)
        identity=['A'];calls=[]
        def snapshot(**kwargs):
            calls.append(kwargs['include_history'])
            result=dict(connected=True,identity=identity[0],markets={})
            for symbol in CONFIG['symbols']:
                frames={tf:dict(current=None) for tf in ('M1','M5')}
                if kwargs['include_history']:
                    for frame in frames.values():frame['bars']=[dict(account=identity[0]) for _ in range(120)]
                result['markets'][symbol]=dict(timeframes=frames)
            return result
        feed.call=snapshot
        with patch('arian.feed.time.time',return_value=NOW):
            first=feed.snapshot();second=feed.snapshot()
            self.assertEqual(calls,[True,False])
            self.assertEqual(second['markets']['EURUSD']['m1'][0]['account'],'A')
            identity[0]='B'
            third=feed.snapshot()
            self.assertEqual(calls[-2:],[False,True])
            self.assertEqual(third['markets']['EURUSD']['m1'][0]['account'],'B')

    def test_chart_current_bar_is_separate_from_closed_signal_bars(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as temp:
            engine=Engine(CONFIG,Path(temp)/'db.sqlite')
            row=dict(time=NOW-3600,open=10,high=12,low=9,close=11,volume=1)
            current=dict(row,time=NOW,close=10.5)
            frame=engine.chart_frames({'timeframes':{'H1':{'bars':[row],'current':current}}},NOW)['H1']
            self.assertEqual(frame['bars'],[row])
            self.assertEqual(frame['current'],current)
            self.assertTrue(frame['fresh'])


if __name__=='__main__':unittest.main()
