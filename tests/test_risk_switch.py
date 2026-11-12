import copy
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from arian.engine import Engine
from test_core import CONFIG

class RiskSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite'
        self.e=Engine(copy.deepcopy(CONFIG),self.path)
        self.e.feed_state=dict(connected=True,demo=True,currency='USD',identity='demo:123',trade_allowed=True,deals=[],tracking_history_complete=True)
        self.e.tracking_stream='external';self.e.capital_binding=dict(initial=1000)
        self.metrics=dict(daily_drawdown_percent=5,drawdown_percent=14,trades_today=1,consecutive_losses=1)
    def tearDown(self):self.tmp.cleanup()
    def test_switch_only_removes_loss_and_drawdown_caps_and_pauses_run(self):
        self.e.ea_control.start()
        with patch('arian.engine.ea_metrics',return_value=self.metrics):
            self.assertFalse(self.e.ea_gate(time.time())['allowed'])
            self.e.action('risk_budget',dict(enabled=False))
            self.assertFalse(self.e.ea_control.running)
            gate=self.e.ea_gate(time.time())
            self.assertTrue(gate['allowed']);self.assertEqual(gate['risk_budget'],CONFIG['risk_percent'])
            self.metrics['trades_today']=CONFIG['max_trades_day']
            self.assertFalse(self.e.ea_gate(time.time())['allowed'])
            self.e.action('risk_budget',dict(enabled=True))
            self.assertTrue(self.e.risk_budget_enabled())
    def test_no_override_on_live_disconnected_or_missing_identity(self):
        for change in (dict(demo=False),dict(connected=False),dict(currency='EUR'),dict(identity=None)):
            original=self.e.feed_state.copy();self.e.feed_state.update(change)
            with self.assertRaises(ValueError):self.e.action('risk_budget',dict(enabled=False))
            self.e.feed_state=original
        with self.assertRaises(ValueError):self.e.action('risk_budget',dict(enabled='false'))
    def test_override_is_not_carried_to_other_accounts_or_restart(self):
        self.e.action('risk_budget',dict(enabled=False))
        self.assertFalse(self.e.risk_budget_enabled())
        self.e.feed_state['identity']='demo:other'
        self.assertTrue(self.e.risk_budget_enabled())
        restarted=Engine(copy.deepcopy(CONFIG),self.path)
        restarted.feed_state=dict(connected=True,demo=True,currency='USD',identity='demo:123')
        self.assertTrue(restarted.risk_budget_enabled())
