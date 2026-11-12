import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from arian.engine import Engine
from arian.storage import Store
from arian.tracking import reconcile_ea, ea_metrics
from arian.calendar_feed import CalendarFeed, parse_events
from arian.news import news_status
from test_core import CONFIG, NOW


def deal(ticket, entry, volume=1, profit=0, magic=777888, pid=10):
    return dict(ticket=ticket,entry=entry,volume=volume,profit=profit,magic=magic,
        position_id=pid,order=100,symbol='GOLD',type=0 if entry==0 else 1,
        price=100 if entry==0 else 110,time_msc=(NOW+ticket)*1000,commission=-1,swap=0,fee=0)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'db.sqlite')
        self.feed=dict(identity='demo:1',connected=True,demo=True,currency='USD',balance=1000,equity=1000,
            execution_audits={'10':dict(status='consistent')},
            deals=[deal(1,0),deal(2,1,profit=10,magic=0)],positions=[],orders_count=0,
            tracking_orders=[dict(ticket=100,sl=99,tp=110)],
            markets={'XAUUSD':dict(spec=dict(symbol='GOLD',factor=100))})

    def tearDown(self):
        self.temp.cleanup()

    def test_manual_exit_attributed_once_and_unrelated_manual_trade_excluded(self):
        self.feed['deals'] += [deal(3,0,magic=0,pid=99),deal(4,1,magic=0,pid=99,profit=999)]
        stream=reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        rows=self.store.trades(stream)
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['learning_eligible'])
        self.assertEqual(rows[0]['pnl'],8)
        self.assertAlmostEqual(rows[0]['r'],.08)
        m=ea_metrics(self.store,stream,NOW+10,1000)
        self.assertEqual((m['trades_today'],m['closed_count'],m['daily_pnl']),(1,1,8))

    def test_partial_close_remains_open_and_floating_included(self):
        self.feed['deals'][1]['volume']=.4
        self.feed['positions']=[dict(identifier=10,magic=777888,symbol='GOLD',type=0,volume=.6,
            price_open=100,profit=7,swap=-.5,sl=100,tp=110,time=NOW)]
        stream=reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        t=self.store.trades(stream)[0]
        self.assertEqual(t['status'],'OPEN')
        self.assertFalse(t['learning_eligible'])
        self.assertEqual(t['risk_money'],100)  # original SL, not current trailing SL
        m=ea_metrics(self.store,stream,NOW+10,1000)
        self.assertEqual(m['closed_count'],0)
        self.assertEqual(m['daily_pnl'],14.5)

    def test_missing_initial_stop_is_not_invented_for_learning(self):
        self.feed['tracking_orders']=[]
        stream=reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        t=self.store.trades(stream)[0]
        self.assertIsNone(t['r'])
        self.assertFalse(t['learning_eligible'])

    def test_quote_mismatch_retains_broker_profit_but_excludes_learning(self):
        self.feed['execution_audits']['10']={'status':'quote_mismatch'}
        stream=reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        t=self.store.trades(stream)[0]
        self.assertEqual(t['pnl'],8)
        self.assertFalse(t['learning_eligible'])
        self.assertEqual(ea_metrics(self.store,stream,NOW+10,1000)['review_count'],1)

    def test_temporary_order_history_failure_preserves_initial_risk(self):
        stream=reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        self.feed['tracking_orders']=[]
        reconcile_ea(self.store,CONFIG,self.feed,NOW+11)
        self.assertEqual(self.store.trades(stream)[0]['risk_money'],100)

    def test_engine_counts_ea_learning_without_opening_a_trade_or_mixing_paper(self):
        engine=Engine(copy.deepcopy(CONFIG),Path(self.temp.name)/'engine.sqlite')
        self.feed['markets']={}
        engine.step(self.feed,NOW+10)
        s=engine.snapshot()
        self.assertEqual(s['tracking']['metrics']['closed_count'],1)
        # No historical spec -> no invented risk; tracked, but excluded from training.
        self.assertEqual(s['tracking']['learner']['excluded_count'],1)
        self.assertEqual(s['account']['closed_count'],0)
        self.assertEqual(s['mode'],'SHADOW')

    def test_restart_and_account_switch_do_not_duplicate_or_mix_trades(self):
        a=reconcile_ea(self.store,CONFIG,self.feed,NOW+10)
        b=reconcile_ea(self.store,CONFIG,dict(self.feed,identity='demo:2'),NOW+10)
        self.assertNotEqual(a,b)
        self.assertEqual(len(Store(self.store.path).trades(a)),1)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'db.sqlite')
        self.rows=[dict(title='Test',country='USD',date='2026-10-30T08:00:00-04:00',impact='High')]

    def tearDown(self):
        self.temp.cleanup()

    def test_timezone_cache_rate_limit_and_no_automatic_news_approval(self):
        feed=CalendarFeed(self.store)
        with patch('arian.calendar_feed.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(self.rows).encode())) as fetch:
            feed.refresh(NOW);feed.refresh(NOW+30)
        fetch.assert_called_once()
        state=feed.snapshot(NOW+60)
        self.assertTrue(state['fresh'])
        self.assertEqual(state['events'][0]['time'],NOW+4*3600)
        self.assertFalse(news_status(self.store,NOW)['ok'])
        self.assertFalse(feed.snapshot(NOW+7*3600)['fresh'])
        restarted=CalendarFeed(self.store)
        with patch('arian.calendar_feed.urllib.request.urlopen') as fetch:
            restarted.refresh(NOW+100)
        fetch.assert_not_called()

    def test_failure_preserves_cache_and_reports_error(self):
        feed=CalendarFeed(self.store)
        with patch('arian.calendar_feed.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(self.rows).encode())):
            feed.refresh(NOW)
        with patch('arian.calendar_feed.urllib.request.urlopen',side_effect=OSError('offline')):
            feed.refresh(NOW+3601)
        self.assertEqual(len(feed.snapshot(NOW+3602)['events']),1)
        self.assertIn('offline',feed.snapshot(NOW+3602)['error'])

    def test_rejects_missing_timezone_and_old_week(self):
        with self.assertRaises(ValueError):parse_events([dict(self.rows[0],date='2026-10-30T08:00:00')])
        feed=CalendarFeed(self.store)
        with patch('arian.calendar_feed.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(self.rows).encode())):
            feed.refresh(NOW+14*86400)
        self.assertFalse(feed.snapshot(NOW+14*86400)['fresh'])


if __name__=='__main__':unittest.main()
