import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from arian.storage import Store
from arian.paper import PaperBroker
from arian.strategy import Bar, analyze, valid_bars, nds_reference
from arian.risk import size_for_risk, entry_checks, trade_window
from arian.learning import Learner
from arian.news import news_status, save_calendar
from arian.engine import Engine
from arian.clock import broker_offset, broker_to_utc

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config.json').read_text())
NOW = 1793347200  # Fri 2026-10-30 08:00 UTC
SPEC = dict(symbol='EURUSD', factor=100000, point=.00001, tick_size=.00001,
            digits=5, volume_min=.01, volume_max=100, volume_step=.01, stops_level=0)
TICK = dict(time=NOW, bid=1.10000, ask=1.10002)
CANDIDATE = dict(symbol='EURUSD', direction='BUY', sl=1.0995, atr=.0005,
                 signal_time=NOW, cycle_id='cycle1', features=dict(symbol='EURUSD', session='london', spread_bucket='tight'))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'data')
        self.path = Path(self.tmp.name) / 'test.sqlite'
        self.store = Store(self.path)
        self.config = copy.deepcopy(CONFIG)
        self.broker = PaperBroker(self.store, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_minimum_lot_is_not_forced(self):
        self.assertEqual(size_for_risk(100, .25, 100, 7, .01, 100, .01), 0)

    def test_volume_never_exceeds_budget(self):
        for equity in (100, 543, 10000, 200000):
            for loss in (1, 43, 99, 544, 3000):
                lots = size_for_risk(equity, .25, loss, 7, .01, 100, .01)
                self.assertLessEqual(lots * (loss + 7), equity * .0025 + 1e-7)

    def test_non_finite_values_are_rejected(self):
        self.assertEqual(size_for_risk(math.inf, .25, 100, 7, .01, 100, .01), 0)

    def test_round_trip_cost_reserved(self):
        t = self.broker.open(CANDIDATE, TICK, SPEC, NOW)
        self.assertIsNotNone(t)
        self.assertLessEqual(t['risk_money'], 25)
        self.assertGreater(t['commission'], 0)

    def test_one_position_across_symbols(self):
        self.broker.open(CANDIDATE, TICK, SPEC, NOW)
        second = dict(CANDIDATE, symbol='XAUUSD', cycle_id='second')
        self.assertIsNone(self.broker.open(second, TICK, SPEC, NOW))

    def test_restart_restores_position_and_equity(self):
        t = self.broker.open(CANDIDATE, TICK, SPEC, NOW)
        store2 = Store(self.path)
        broker2 = PaperBroker(store2, self.config)
        self.assertEqual(broker2.metrics(NOW)['open_count'], 1)
        broker2.mark('EURUSD', dict(time=NOW+10, bid=t['sl']-.0003, ask=t['sl']-.00028), NOW+10)
        closed = store2.trades('mt5-paper')[0]
        self.assertEqual(closed['status'], 'STOP')
        self.assertLess(closed['exit'], t['sl'])
        self.assertLess(broker2.metrics(NOW+10)['balance'], 10000)

    def test_cycle_not_reentered_after_close_or_restart(self):
        t = self.broker.open(CANDIDATE, TICK, SPEC, NOW)
        self.broker.mark('EURUSD', dict(time=NOW+1, bid=t['tp'], ask=t['tp']+.00002), NOW+1)
        again = PaperBroker(Store(self.path), self.config)
        self.assertIsNone(again.open(CANDIDATE, TICK, SPEC, NOW+600))

    def test_stale_tick_cannot_close_or_value_position(self):
        t = self.broker.open(CANDIDATE, TICK, SPEC, NOW)
        self.broker.mark('EURUSD', dict(TICK, bid=t['sl']-.1), NOW+100)
        self.assertEqual(self.broker.metrics(NOW+100)['open_count'], 1)

    def test_time_exit_and_costs(self):
        t = self.broker.open(CANDIDATE, TICK, SPEC, NOW)
        self.broker.mark('EURUSD', dict(TICK, time=NOW+480), NOW+480)
        closed = self.store.trades('mt5-paper')[0]
        self.assertEqual(closed['status'], 'TIME')
        self.assertLess(closed['pnl'], 0)
        self.assertAlmostEqual(closed['r'], closed['pnl']/closed['risk_money'])

    def test_sell_closes_on_ask(self):
        c = dict(CANDIDATE, direction='SELL', sl=1.1005)
        t = self.broker.open(c, TICK, SPEC, NOW)
        self.broker.mark('EURUSD', dict(time=NOW+1, bid=t['sl']-.00003, ask=t['sl']+.00002), NOW+1)
        self.assertEqual(self.store.trades('mt5-paper')[0]['status'], 'STOP')

    def test_pause_gate_news_gate_future_tick_gate(self):
        metrics = self.broker.metrics(NOW)
        checks = entry_checks(NOW, TICK, CANDIDATE, self.config, metrics, {'ok':False}, True, True)
        self.assertFalse(next(c['ok'] for c in checks if c['id']=='news'))
        self.assertFalse(next(c['ok'] for c in checks if c['id']=='pause'))
        checks = entry_checks(NOW, dict(TICK,time=NOW+10), CANDIDATE, self.config, metrics, {'ok':True}, False, True)
        self.assertFalse(next(c['ok'] for c in checks if c['id']=='feed'))

    def test_news_expiry_and_blackout(self):
        self.assertFalse(news_status(self.store,NOW)['ok'])
        save_calendar(self.store,dict(reviewed=True, valid_until=NOW+10000,
            events=[dict(time=NOW+1201,currency='USD',title='Test')]),NOW)
        self.assertTrue(news_status(self.store,NOW)['ok'])
        self.assertFalse(news_status(self.store,NOW+2)['ok'])
        self.assertFalse(news_status(self.store,NOW+10001)['ok'])

    def test_weekends_blocked(self):
        self.assertTrue(trade_window(NOW,self.config))
        self.assertFalse(trade_window(NOW+86400,self.config))

    def test_ledger_streams_isolated(self):
        self.broker.open(CANDIDATE,TICK,SPEC,NOW)
        self.assertEqual(PaperBroker(self.store,self.config,'backtest').metrics(NOW)['open_count'],0)

    def test_planning_does_not_persist_fake_fill(self):
        planned = self.broker.open(CANDIDATE,TICK,SPEC,NOW,persist=False)
        self.assertIsNotNone(planned)
        self.assertEqual(self.store.trades(),[])

    def test_engine_disarms_on_disconnect(self):
        engine = Engine(self.config,self.path)
        engine.demo_identity = 'demo:1'
        engine.step({'connected':False,'error':'test'}, NOW)
        self.assertIsNone(engine.demo_identity)

    def test_live_account_cannot_be_armed(self):
        engine = Engine(self.config,self.path)
        engine.feed_state = dict(connected=True,demo=False,currency='USD')
        with self.assertRaises(ValueError):
            engine.action('demo_arm',{'confirmation':'DEMO'})

    def test_stale_feed_never_opens_even_with_mock_signal(self):
        engine = Engine(self.config,self.path)
        bar=Bar(NOW-60,1.1,1.11,1.09,1.1)
        feed=dict(connected=True,identity='d:1',currency='USD',demo=True,deals=[],positions=[],orders_count=0,
            markets={'EURUSD':dict(tick=dict(TICK,time=NOW-1000),spec=SPEC,m1=[vars(bar)],m5=[vars(bar)])})
        report=dict(symbol='EURUSD',checks=[],candidate=copy.deepcopy(CANDIDATE),reason='fixture')
        with patch('arian.engine.analyze',return_value=report):engine.step(feed,NOW)
        self.assertEqual(self.store.trades(),[])
        self.assertEqual(engine.markets['EURUSD']['decision'],'BLOCK')


class LearningTests(unittest.TestCase):
    setUp = StoreCase.setUp
    tearDown = StoreCase.tearDown
    def rows(self,count,start=1):
        rows=[]
        for i in range(start,start+count):
            bad=i%2==0
            rows.append(dict(id=str(i),status='CLOSED',opened=i*100,closed=i*100+10,
                r=-1 if bad else 1.5,features={'symbol':'EURUSD','session':'bad' if bad else 'good','spread_bucket':'tight'}))
        return rows

    def test_not_enough_data_never_changes_policy(self):
        learner=Learner(self.store,self.config)
        learner.update(self.rows(99),[],now=10001)
        self.assertIsNone(learner.state['active'])
        self.assertIsNone(learner.state['candidate'])

    def test_future_holdout_is_required_and_persisted(self):
        learner=Learner(self.store,self.config)
        learner.update(self.rows(100),[],now=10011)
        self.assertIsNotNone(learner.state['candidate'])
        self.assertIsNone(learner.state['active'])
        learner=Learner(Store(self.path),self.config)
        learner.update(self.rows(149),[],now=15000)
        self.assertIsNone(learner.state['active'])
        learner.update(self.rows(150),[],now=15011)
        self.assertIsNotNone(learner.state['active'])
        self.assertFalse(learner.allow({'session':'bad'}))
        self.assertTrue(learner.allow({'session':'good'}))
        self.assertEqual(learner.state['active']['scope'],'paper-only')

    def test_forward_failure_rejects_frozen_rule(self):
        learner=Learner(self.store,self.config)
        learner.update(self.rows(100),[],now=10011)
        forward=self.rows(50,101)
        for row in forward:row['r']=1
        learner.update(self.rows(100)+forward,[],now=15011)
        self.assertIsNone(learner.state['active'])
        self.assertIsNone(learner.state['candidate'])


class StrategyTests(unittest.TestCase):
    def test_fxpro_clock_winter_summer_and_stale_guard(self):
        self.assertEqual(broker_offset(NOW,{'mode':'fxpro-eu'}),7200)
        self.assertEqual(broker_offset(NOW-100*86400,{'mode':'fxpro-eu'}),10800)
        self.assertEqual(broker_to_utc(NOW+7200,{'mode':'fxpro-eu'}),NOW)
        self.assertEqual(broker_to_utc(NOW-86400+7200,{'mode':'fxpro-eu'}),NOW-86400)

    def test_original_nds_rr(self):
        bars=[Bar(i*60,100+i,102+i,99+i,101+i) for i in range(20)]
        bars[0]=Bar(0,110,111,98,99)
        result=nds_reference(bars)
        self.assertAlmostEqual(result['reward_risk'],.5245098039)
        self.assertGreater(result['entry'],result['sl'])

    def test_bad_candle_rejected(self):
        self.assertFalse(valid_bars([Bar(1,10,9,8,10)]))
        self.assertFalse(valid_bars([Bar(1,10,11,9,10),Bar(1,10,11,9,10)]))

    def test_signal_uses_confirmed_sweep(self):
        m5=[Bar(i*300,1+i*.001,1.002+i*.001,.999+i*.001,1.001+i*.001) for i in range(70)]
        m1=[Bar(i*60,1.06,1.061,1.059,1.0605) for i in range(120)]
        m1[-2]=Bar(118*60,1.0595,1.061,1.058,1.0605)
        m1[-1]=Bar(119*60,1.0605,1.063,1.0604,1.0625)
        result=analyze('EURUSD',m1,m5,CONFIG)
        self.assertIsNotNone(result['candidate'])
        self.assertEqual(result['direction'],'BUY')
        self.assertEqual(result['candidate']['signal_time'],120*60)
        # Removing confirmation removes the signal. No later candles supplied.
        unconfirmed=m1[:-1]+[Bar(119*60,1.0605,1.061,1.0604,1.0606)]
        self.assertIsNone(analyze('EURUSD',unconfirmed,m5,CONFIG)['candidate'])


if __name__=='__main__':unittest.main()
