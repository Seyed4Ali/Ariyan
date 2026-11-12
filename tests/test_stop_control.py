import copy
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import Mock, patch
from arian.ea_control import EAControl, control_name
from arian.engine import Engine
from arian.mt5_worker import run
from arian.storage import Store
from test_core import CONFIG, NOW


class StopControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'db.sqlite')

    def tearDown(self):self.temp.cleanup()

    def test_closed_terminal_is_never_initialized_even_for_exit(self):
        module=Mock()
        with patch('arian.mt5_worker.terminal_is_running',return_value=False),patch.dict('sys.modules',{'MetaTrader5':module}):
            for action in ('snapshot','demo_open','demo_close'):
                result=run(dict(terminal_path='unused',action=action))
                self.assertTrue(result['terminal_closed'])
        module.initialize.assert_not_called()
        module.order_send.assert_not_called()

    def test_stop_files_are_account_server_scoped_and_require_exact_ack(self):
        bridge=EAControl(self.store,[777888])
        bridge.bind(dict(demo=True,currency='USD',common_files_path=str(self.root/'Files'),server='FxPro-MT5 Demo',account_login=123))
        bridge.request('FLATTEN')
        path=self.root/'Files'/control_name('FxPro-MT5 Demo',123,777888)
        command=path.read_text(encoding='ascii').strip()
        self.assertTrue(command.startswith('FLATTEN:'))
        self.assertFalse(bridge.snapshot()['acknowledged'])
        path.with_suffix('.txt.ack').write_text('old command',encoding='ascii')
        self.assertFalse(bridge.snapshot()['acknowledged'])
        path.with_suffix('.txt.ack').write_text(command,encoding='ascii')
        self.assertTrue(bridge.snapshot()['acknowledged'])
        self.assertNotEqual(control_name('demo',123,777888),control_name('live',123,777888))
        with self.assertRaises(ValueError):bridge.request('RESUME')

    def test_live_account_never_binds_stop_bridge(self):
        bridge=EAControl(self.store,[777888])
        bridge.bind(dict(demo=False,currency='USD',common_files_path=str(self.root/'Files'),server='live',account_login=123))
        bridge.request('FLATTEN')
        self.assertFalse((self.root/'Files').exists())

    def test_disabled_monitoring_skips_feed_and_survives_restart(self):
        engine=Engine(copy.deepcopy(CONFIG),self.root/'engine.sqlite')
        engine.monitoring=False;engine.store.set('monitoring',False)
        engine.feed=Mock()
        with patch.object(engine.stop,'wait',side_effect=lambda _:engine.stop.set()):engine.loop()
        engine.feed.snapshot.assert_not_called()
        again=Engine(copy.deepcopy(CONFIG),self.root/'engine.sqlite')
        self.assertFalse(again.monitoring)
        again.action('monitor_resume',{})
        self.assertTrue(again.monitoring)
        self.assertIsNone(again.demo_identity)

    def test_flatten_waits_for_ea_ack_before_disabling_monitoring(self):
        engine=Engine(copy.deepcopy(CONFIG),self.root/'engine.sqlite')
        feed=dict(connected=True,identity='demo:123',demo=True,currency='USD',balance=1000,equity=1000,
            server='demo',account_login=123,common_files_path=str(self.root/'Files'),positions=[],observed_orders=[],
            orders_count=0,deals=[],markets={})
        engine.step(feed,NOW)
        engine.action('flatten',{})
        engine.step(feed,NOW+1)
        self.assertTrue(engine.flatten)
        self.assertTrue(engine.monitoring)
        path=engine.ea_control.paths()[0]
        path.with_suffix('.txt.ack').write_text(path.read_text(encoding='ascii'),encoding='ascii')
        for chart,symbol in enumerate(engine.config['symbols'].values()):
            path.with_name(path.name+f'.{chart}.status').write_text(
                f'{symbol}|{chart}|7.30|{path.read_text().strip()}|FLATTEN|1|0|{int(time.time())}',encoding='ascii')
        engine.step(feed,NOW+2)
        self.assertFalse(engine.flatten)
        self.assertFalse(engine.monitoring)
        self.assertTrue(engine.paused)
        self.assertIsNone(engine.demo_identity)
        restarted=Engine(copy.deepcopy(CONFIG),self.root/'engine.sqlite')
        self.assertFalse(restarted.monitoring)
        self.assertEqual(restarted.tracking_stream,engine.tracking_stream)
        self.assertEqual(restarted.capital_binding,engine.capital_binding)


if __name__=='__main__':unittest.main()
