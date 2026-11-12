import copy
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from arian.ea_control import EAControl
from arian.storage import Store
from arian.engine import Engine
from test_core import CONFIG

class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'db.sqlite')
        self.bridge=EAControl(self.store,[777888])
        self.feed=dict(demo=True,currency='USD',common_files_path=str(self.root/'Files'),server='demo',account_login=123)
        self.bridge.bind(self.feed)
    def tearDown(self):self.tmp.cleanup()
    def test_lease_capped_risk_expiry_and_restart_disarms(self):
        self.bridge.start()
        self.bridge.renew(dict(allowed=True,risk_budget=.1),5,1000)
        command=self.bridge.paths()[0].read_text().strip().split('|')
        self.assertEqual(command[1:],[ '1020','0.100000','1'])
        restarted=EAControl(self.store,[777888])
        self.assertFalse(restarted.running)
        self.assertTrue(restarted.paths()[0].read_text().startswith('PAUSE:'))
    def test_stop_overrides_and_unrelated_chart_ack_is_not_confirmation(self):
        self.bridge.start();self.bridge.renew(dict(allowed=False),.25,1000)
        path=self.bridge.paths()[0]
        path.with_suffix('.txt.ack').write_text(path.read_text())
        self.assertFalse(self.bridge.snapshot()['acknowledged'])
        self.bridge.request('FLATTEN')
        self.bridge.renew(dict(allowed=True),.25,1004)
        self.assertTrue(path.read_text().startswith('FLATTEN:'))
    def test_chart_telemetry_requires_freshness_and_exact_run_id(self):
        self.bridge.start();self.bridge.renew(dict(allowed=True),.25)
        path=self.bridge.paths()[0];command=path.read_text().split('|')[0]
        status=path.with_name(path.name+'.123.status')
        status.write_text(f'GOLD|123|7.30|{command}|READY|1|0|{int(time.time())}')
        self.assertTrue(self.bridge.snapshot()['acknowledged'])
        status.write_text(f'GOLD|123|7.30|RUN:old|READY|1|0|{int(time.time())}')
        self.assertFalse(self.bridge.snapshot()['acknowledged'])
        status.write_text(f'GOLD|123|7.30|{command}|READY|1|0|1')
        self.assertEqual(self.bridge.snapshot()['charts'],[])
    def test_risk_cap_cannot_be_cleared_by_resume(self):
        e=Engine(copy.deepcopy(CONFIG),self.root/'engine.sqlite')
        e.feed_state=dict(connected=True,demo=True,currency='USD',trade_allowed=True,deals=[],tracking_history_complete=True)
        e.updated=time.time();e.tracking_stream='external';e.capital_binding=dict(initial=1000)
        metrics=dict(daily_drawdown_percent=5,drawdown_percent=14,trades_today=1,consecutive_losses=1)
        with patch('arian.engine.ea_metrics',return_value=metrics):
            gate=e.ea_gate(time.time())
            self.assertFalse(gate['allowed']);self.assertEqual(gate['risk_budget'],0)
            e.action('ea_resume',{})
            self.assertTrue(e.ea_control.running)
            self.assertFalse(e.ea_control.gate['allowed'])
    def test_entry_block_reason_from_731_is_available_to_dashboard(self):
        self.bridge.start();self.bridge.renew(dict(allowed=True),.25)
        path=self.bridge.paths()[0];command=path.read_text().split('|')[0]
        path.with_name(path.name+'.123.status').write_text(
            f'EURUSD|123|7.31|{command}|READY|1|0|{int(time.time())}|PERIOD_M5|VOLUME_BELOW_MIN_OR_RISK')
        chart=self.bridge.snapshot()['charts'][0]
        self.assertEqual(chart['entry_reason'],'VOLUME_BELOW_MIN_OR_RISK')
        self.assertEqual(chart['timeframe'],'PERIOD_M5')
        self.assertTrue(self.bridge.snapshot()['acknowledged'])
    def test_closed_terminal_cannot_start(self):
        e=Engine(copy.deepcopy(CONFIG),self.root/'engine.sqlite')
        with self.assertRaises(ValueError):e.action('ea_resume',{})
        self.assertFalse(e.ea_control.running)
    def test_one_chart_ack_cannot_claim_all_symbols_are_running(self):
        bridge=EAControl(self.store,[777888],['GOLD','EURUSD','BITCOIN'])
        bridge.start();bridge.renew(dict(allowed=True),.25)
        path=bridge.paths()[0];command=path.read_text().split('|')[0]
        path.with_name(path.name+'.123.status').write_text(f'GOLD|123|7.30|{command}|READY|1|0|{int(time.time())}')
        self.assertFalse(bridge.snapshot()['acknowledged'])
