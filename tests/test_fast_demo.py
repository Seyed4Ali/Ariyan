import copy, json, tempfile, time, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from arian.engine import Engine
from arian.feed import MT5Feed
from arian.news import news_status
from arian.paper import PaperBroker
from arian.profiles import apply_profile
from arian.storage import Store
from arian.strategy import Bar, analyze
from test_core import CONFIG, NOW, CANDIDATE, TICK, SPEC

class FastDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'db.sqlite'
        self.store=Store(self.path)
    def tearDown(self): self.temp.cleanup()
    def test_live_feed_does_not_launch_historical_tick_download_by_default(self):
        feed=MT5Feed(dict(CONFIG,observed_magic_numbers=[777888]))
        def call(**kwargs):
            self.assertEqual(kwargs['action'],'snapshot')
            return dict(connected=True, identity='demo:1', tracking_history_complete=True,
                markets={s:dict(timeframes={tf:dict(bars=[{}]*120) for tf in ('M1','M5')}) for s in CONFIG['symbols']})
        feed.call=Mock(side_effect=call)
        feed.snapshot()
        self.assertIsNone(feed.audit_thread)
        feed.call.assert_called_once()
    def test_plan_uses_broker_metrics_with_uncertain_record_in_ledger(self):
        broker=PaperBroker(self.store,CONFIG,'demo-test')
        unknown=dict(CANDIDATE,id='uncertain',stream=broker.stream,status='UNCERTAIN',opened=NOW,closed=None)
        self.store.save_trade(unknown)
        planned=broker.open(dict(CANDIDATE,cycle_id='next'),TICK,SPEC,NOW,
            persist=False,equity=10000,metrics=dict(daily_drawdown_percent=0))
        self.assertIsNotNone(planned)
        self.assertEqual(len(self.store.trades()),1)
    def test_fast_profile_only_activates_on_connected_demo_and_preserves_caps(self):
        engine=Engine(copy.deepcopy(CONFIG),self.path)
        with self.assertRaises(ValueError):engine.action('set_profile',dict(profile='fast-demo'))
        engine.feed_state=dict(connected=True,demo=True)
        engine.action('set_profile',dict(profile='fast-demo'))
        self.assertEqual(engine.config['cooldown_seconds'],30)
        self.assertEqual(engine.config['risk_percent'],.1)
        self.assertEqual(engine.config['daily_loss_percent'],CONFIG['daily_loss_percent'])
        self.assertIsNone(engine.demo_identity)
        restarted=Engine(copy.deepcopy(CONFIG),self.path)
        self.assertEqual(restarted.config['execution_profile'],'fast-demo')
        self.assertIsNone(restarted.demo_identity)
    def test_automatic_news_requires_current_fresh_feed_and_respects_high_events(self):
        self.assertFalse(news_status(self.store,NOW,automatic=True)['ok'])
        feed=dict(fetched_at=NOW,week_start=NOW-86400,week_end=NOW+86400,
            events=[dict(time=NOW+1201,title='Release',currency='USD',impact='High')])
        self.store.set('calendar_feed',feed)
        self.assertTrue(news_status(self.store,NOW,automatic=True)['ok'])
        self.assertFalse(news_status(self.store,NOW+2,automatic=True)['ok'])
        self.assertFalse(news_status(self.store,NOW+6*3600+1,automatic=True)['ok'])
        self.assertIsNone(self.store.get('news'))
    def test_demo_arm_rejects_stale_state_and_excludes_external_ea(self):
        engine=Engine(copy.deepcopy(CONFIG),self.path)
        engine.feed_state=dict(connected=True,demo=True,currency='USD',identity='d:1',
            trade_allowed=True,positions=[],orders_count=0,balance=10000,equity=10000)
        engine.updated=time.time()-30
        with self.assertRaisesRegex(ValueError,'تازه'):engine.action('demo_arm',dict(confirmation='DEMO'))
        engine.updated=time.time();engine.ea_control.start();engine.paused=True
        engine.action('demo_arm',dict(confirmation='DEMO'))
        self.assertFalse(engine.ea_control.running)
        self.assertFalse(engine.paused)
        self.assertEqual(engine.demo_identity,'d:1')
    def test_demo_orders_repeat_only_on_new_cycle_after_exit_and_cooldown(self):
        config=apply_profile(CONFIG,'fast-demo')
        e=Engine(config,self.path)
        feed=dict(connected=True,identity='d:1',demo=True,currency='USD',
                  balance=10000,equity=10000,positions=[],orders_count=0)
        e.demo_identity=feed['identity']
        e.feed.call=Mock(return_value=dict(ok=True,order=10,price=TICK['ask'],volume=.1))
        e.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,feed,{'ok':True},True,NOW)
        self.assertEqual(e.feed.call.call_count,1)
        stream=e.demo_stream(feed['identity'])
        trade=e.store.trades(stream)[0]
        # Broker reconciliation has confirmed a winning close.
        trade.update(status='BROKER_CLOSED',closed=NOW+1,pnl=1,r=.1)
        e.store.save_trade(trade)
        e.maybe_open_demo(dict(CANDIDATE,cycle_id='too-soon',signal_time=NOW+10),dict(TICK,time=NOW+10),SPEC,feed,{'ok':True},True,NOW+10)
        self.assertEqual(e.feed.call.call_count,1)
        e.maybe_open_demo(dict(CANDIDATE,signal_time=NOW+60),dict(TICK,time=NOW+60),SPEC,feed,{'ok':True},True,NOW+60)
        self.assertEqual(e.feed.call.call_count,1)  # same cycle never retried
        e.maybe_open_demo(dict(CANDIDATE,cycle_id='next-bar',signal_time=NOW+60),dict(TICK,time=NOW+60),SPEC,feed,{'ok':True},True,NOW+60)
        self.assertEqual(e.feed.call.call_count,2)
        e.maybe_open_demo(dict(CANDIDATE,cycle_id='third',signal_time=NOW+61),dict(TICK,time=NOW+61),SPEC,feed,{'ok':True},True,NOW+61)
        self.assertEqual(e.feed.call.call_count,2)  # open journal intent blocks concurrent symbol
    def test_ambiguous_order_disarms_and_is_never_automatically_resent(self):
        e=Engine(CONFIG,self.path)
        feed=dict(identity='d:1',demo=True,balance=10000,equity=10000,positions=[],orders_count=0)
        e.demo_identity='d:1'
        e.feed.call=Mock(return_value=dict(uncertain=True,error='timeout'))
        e.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,feed,{'ok':True},True,NOW)
        self.assertIsNone(e.demo_identity)
        self.assertEqual(e.store.trades(e.demo_stream('d:1'))[0]['status'],'UNCERTAIN')
        e.maybe_open_demo(dict(CANDIDATE,cycle_id='new'),TICK,SPEC,feed,{'ok':True},True,NOW)
        self.assertEqual(e.feed.call.call_count,1)
    def test_switching_executor_preserves_historical_peak_until_explicit_new_session(self):
        e=Engine(CONFIG,self.path)
        e.tracking_stream='external'
        e.capital_binding=dict(initial=1000)
        e.store.set('external:peak',1200)
        f=dict(identity='d:1',connected=True,demo=True,currency='USD',balance=1000,equity=1000,positions=[],orders_count=0)
        self.assertGreater(e.demo_metrics(f,NOW)['drawdown_percent'],5)
        e.feed_state=f;e.updated=time.time()
        with self.assertRaises(ValueError):e.action('demo_new_session',dict(confirmation='DEMO'))
        e.action('demo_new_session',dict(confirmation='NEW DEMO'))
        self.assertEqual(e.demo_metrics(f,time.time())['drawdown_percent'],0)
        self.assertEqual(e.store.get('external:peak'),1200)
        self.assertIsNone(e.demo_identity)
    def test_new_session_cannot_hide_uncertain_or_open_orders(self):
        e=Engine(CONFIG,self.path)
        e.feed_state=dict(identity='d:1',connected=True,demo=True,currency='USD',balance=1000,equity=1000,positions=[{}],orders_count=0)
        e.updated=time.time()
        with self.assertRaises(ValueError):e.action('demo_new_session',dict(confirmation='NEW DEMO'))
        e.feed_state['positions']=[];e.store.set('demo_pending','unknown')
        with self.assertRaises(ValueError):e.action('demo_new_session',dict(confirmation='NEW DEMO'))
    def test_definitive_rejection_consumes_only_signal_and_next_bar_can_trade(self):
        e=Engine(CONFIG,self.path)
        feed=dict(identity='d:1',demo=True,balance=10000,equity=10000,positions=[],orders_count=0)
        e.demo_identity='d:1'
        e.feed.call=Mock(return_value=dict(ok=False,uncertain=False,retcode=10016,comment='Invalid stops'))
        e.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,feed,{'ok':True},True,NOW)
        self.assertEqual(e.demo_identity,'d:1')
        self.assertEqual(e.demo_metrics(feed,NOW)['trades_today'],0)
        self.assertEqual(e.demo_metrics(feed,NOW)['closed_count'],0)
        e.maybe_open_demo(copy.deepcopy(CANDIDATE),TICK,SPEC,feed,{'ok':True},True,NOW)
        self.assertEqual(e.feed.call.call_count,1)
        e.feed.call.return_value=dict(ok=True,uncertain=False,order=11,price=TICK['ask'],volume=.1)
        e.maybe_open_demo(dict(CANDIDATE,cycle_id='next',signal_time=NOW+60),dict(TICK,time=NOW+60),SPEC,feed,{'ok':True},True,NOW+60)
        self.assertEqual(e.feed.call.call_count,2)
    def test_risk_stop_is_visible_even_without_a_new_strategy_candidate(self):
        e=Engine(CONFIG,self.path)
        e.feed_state=dict(connected=True,identity='d:1')
        with patch.object(e,'demo_metrics',return_value=dict(daily_drawdown_percent=0,drawdown_percent=0,
                consecutive_losses=3,trades_today=4,open_count=0,last_exit=0)):
            self.assertTrue(any('متوالی' in reason for reason in e.demo_blockers(NOW)))
    def test_scalp_uses_distinct_closed_bar_cycles_and_retains_trend_filter(self):
        m1=[Bar(NOW-120*60+i*60,1.1+i*.00002,1.1001+i*.00002,1.0999+i*.00002,1.1+i*.00002) for i in range(120)]
        m1[-2]=Bar(NOW-120,1.1023,1.10245,1.1021,1.10235)
        m1[-1]=Bar(NOW-60,1.10235,1.1027,1.1022,1.1026)
        m5=[Bar(NOW-60*300+i*300,1.09+i*.0002,1.0903+i*.0002,1.0899+i*.0002,1.0902+i*.0002) for i in range(60)]
        report=analyze('EURUSD',m1,m5,apply_profile(CONFIG,'fast-demo'))
        self.assertIsNotNone(report['candidate'],report)
        self.assertEqual(report['candidate']['signal_time'],NOW)
        self.assertEqual(report['candidate']['direction'],'BUY')
        shifted=[Bar(b.time+60,b.open,b.high,b.low,b.close) for b in m1]
        second=analyze('EURUSD',shifted,m5,apply_profile(CONFIG,'fast-demo'))
        self.assertNotEqual(report['candidate']['cycle_id'],second['candidate']['cycle_id'])
        flat=[Bar(b.time,1.1,1.1001,1.0999,1.1) for b in m5]
        self.assertIsNone(analyze('EURUSD',m1,flat,apply_profile(CONFIG,'fast-demo'))['candidate'])

if __name__=='__main__':unittest.main()
