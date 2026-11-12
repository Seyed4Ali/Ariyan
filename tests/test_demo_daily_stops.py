import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from arian.engine import Engine
from arian.risk import entry_checks
from test_core import CONFIG, NOW, CANDIDATE, SPEC, TICK


class DemoDailyStopsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'db.sqlite'
        self.engine=Engine(copy.deepcopy(CONFIG),self.path)
        self.feed=dict(connected=True,demo=True,currency='USD',identity='demo:123',
                       trade_allowed=True,balance=10000,equity=10000,positions=[],orders_count=0)
        self.engine.feed_state=self.feed
        self.engine.updated=time.time()
        self.metrics=dict(equity=10000,balance=10000,daily_drawdown_percent=3,
            drawdown_percent=0,consecutive_losses=5,trades_today=4,last_exit=0,open_count=0)
        self.engine.feed.call=Mock(return_value=dict(ok=True,order=11,price=TICK['ask'],volume=.1))
    def tearDown(self): self.temp.cleanup()

    def toggle(self,enabled):
        return self.engine.action('demo_daily_stops',dict(enabled=enabled))

    def test_both_stops_toggle_without_resetting_stats_or_arming(self):
        self.engine.store.set('history-marker',123)
        self.assertTrue(self.engine.demo_daily_stops_enabled())
        self.toggle(False)
        self.assertFalse(self.engine.demo_daily_stops_enabled())
        self.assertIsNone(self.engine.demo_identity)
        self.assertEqual(self.engine.store.get('history-marker'),123)
        self.assertEqual(self.metrics['consecutive_losses'],5)
        with patch.object(self.engine,'demo_metrics',return_value=self.metrics):
            self.assertEqual(self.engine.demo_blockers(NOW),[])
            self.toggle(True)
            blockers=self.engine.demo_blockers(NOW)
        self.assertTrue(any('روزانه' in b for b in blockers))
        self.assertTrue(any('متوالی' in b for b in blockers))

    def test_entry_and_lot_sizing_can_continue_after_daily_loss_and_streak(self):
        self.engine.demo_identity=self.feed['identity']
        with patch.object(self.engine,'demo_metrics',return_value=self.metrics):
            result=self.engine.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,self.feed,{'ok':True},True,NOW)
            self.assertFalse(result['ok'])
            self.engine.feed.call.assert_not_called()
            self.toggle(False)
            result=self.engine.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,self.feed,{'ok':True},True,NOW)
        self.assertTrue(result['ok'])
        self.engine.feed.call.assert_called_once()
        trade=self.engine.store.trades(self.engine.demo_stream(self.feed['identity']))[0]
        self.assertGreater(trade['volume'],0)
        self.assertLessEqual(trade['risk_money'],self.feed['equity']*CONFIG['risk_percent']/100)
        self.assertGreater(trade['sl'],0)
        self.assertEqual(self.engine.demo_identity,self.feed['identity'])

    def test_disabling_stops_preserves_other_entry_gates(self):
        self.toggle(False)
        for changes, news, paused in (
                ({'drawdown_percent':6},{'ok':True},False),
                ({'trades_today':CONFIG['max_trades_day']},{'ok':True},False),
                ({'open_count':1},{'ok':True},False),
                ({'last_exit':NOW-1},{'ok':True},False),
                ({},{'ok':False},False), ({},{'ok':True},True)):
            with self.subTest(changes=changes,news=news,paused=paused):
                self.engine.paused=paused
                with patch.object(self.engine,'demo_metrics',return_value=dict(self.metrics,**changes)):
                    result=self.engine.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,self.feed,news,True,NOW)
                self.assertFalse(result['ok'])
        self.engine.feed.call.assert_not_called()

    def test_no_override_for_real_disconnected_wrong_currency_or_stale_state(self):
        for changes in (dict(demo=False),dict(connected=False),dict(currency='EUR'),dict(identity=None)):
            self.engine.feed_state=dict(self.feed,**changes)
            with self.assertRaises(ValueError):self.toggle(False)
        self.engine.feed_state=self.feed
        self.engine.updated=time.time()-30
        with self.assertRaises(ValueError):self.toggle(False)
        self.engine.updated=time.time()
        with patch.object(self.engine.clock,'ready',return_value=False):
            with self.assertRaises(ValueError):self.toggle(False)
        with self.assertRaises(ValueError):self.toggle('false')
        self.assertTrue(self.engine.demo_daily_stops_enabled())

    def test_account_switch_disconnect_and_restart_restore_stops(self):
        self.toggle(False)
        self.engine.feed_state=dict(self.feed,identity='demo:456')
        self.assertTrue(self.engine.demo_daily_stops_enabled())
        self.engine.step(dict(connected=False),NOW)
        self.engine.feed_state=self.feed
        self.assertTrue(self.engine.demo_daily_stops_enabled())
        self.engine.updated=time.time();self.toggle(False)
        restarted=Engine(copy.deepcopy(CONFIG),self.path)
        restarted.feed_state=self.feed
        self.assertTrue(restarted.demo_daily_stops_enabled())

    def test_paper_and_baseline_keep_their_own_limits(self):
        self.toggle(False)
        checks=entry_checks(NOW,TICK,CANDIDATE,self.engine.paper.config,self.metrics,{'ok':True},False,True)
        by_id={c['id']:c['ok'] for c in checks}
        self.assertFalse(by_id['daily'])
        self.assertFalse(by_id['streak'])
        self.assertTrue(self.engine.baseline.config.get('daily_loss_stop_enabled',True))


if __name__=='__main__':unittest.main()
