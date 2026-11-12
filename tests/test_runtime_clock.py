import io,time,unittest
from email.message import Message
from unittest.mock import patch
from arian.runtime_clock import RuntimeClock
class Response(io.BytesIO):
    def __init__(self,date,age=None):
        super().__init__(b'{}');self.headers=Message();self.headers['Date']=date
        if age is not None:self.headers['Age']=age
class ClockTests(unittest.TestCase):
    def test_requires_agreeing_fresh_https_dates_and_advances_monotonically(self):
        c=RuntimeClock('https-verified');self.assertFalse(c.ready())
        with patch('arian.runtime_clock.time.time',return_value=1000),patch('arian.runtime_clock.time.monotonic',return_value=100),patch('arian.runtime_clock.urllib.request.urlopen',side_effect=[Response('Thu, 01 Jan 1970 00:17:00 GMT'),Response('Thu, 01 Jan 1970 00:17:00 GMT')]):
            c.refresh();self.assertTrue(c.ready());self.assertEqual(c.now(),1020.5)
        with patch('arian.runtime_clock.time.monotonic',return_value=110):self.assertEqual(c.now(),1030.5)
        with patch('arian.runtime_clock.time.monotonic',return_value=3701):self.assertFalse(c.ready())
    def test_disagreement_cached_dates_or_large_correction_never_authorize(self):
        pairs=[(Response('Thu, 01 Jan 1970 00:17:00 GMT'),Response('Thu, 01 Jan 1970 00:18:00 GMT')),
               (Response('Thu, 01 Jan 1970 00:17:00 GMT','10'),Response('Thu, 01 Jan 1970 00:17:00 GMT')),
               (Response('Thu, 01 Jan 1970 01:17:00 GMT'),Response('Thu, 01 Jan 1970 01:17:00 GMT'))]
        for pair in pairs:
            c=RuntimeClock('https-verified')
            with patch('arian.runtime_clock.time.time',return_value=1000),patch('arian.runtime_clock.urllib.request.urlopen',side_effect=pair):c.refresh()
            self.assertFalse(c.ready());self.assertIsNone(c.anchor)
    def test_system_mode_preserves_existing_offline_behavior(self):
        self.assertTrue(RuntimeClock().ready())
if __name__=='__main__':unittest.main()
