import unittest
from arian.execution_audit import audit_fill


class ExecutionAuditTests(unittest.TestCase):
    def test_reported_sell_above_entire_tick_range_is_flagged(self):
        d=dict(ticket=1,type=1,price=3899.19,time_msc=1000)
        ticks=[dict(time_msc=990,bid=3897.04,ask=3897.19)]
        audit=audit_fill(d,ticks,dict(point=.01,tick_size=.01))
        self.assertEqual(audit['status'],'quote_mismatch')
        self.assertAlmostEqual(audit['difference'],2.15)

    def test_buy_compares_ask_and_sell_compares_bid(self):
        tick=[dict(time_msc=1000,bid=100,ask=100.15)]
        for side,px in ((0,100.15),(1,100)):
            self.assertEqual(audit_fill(dict(ticket=1,type=side,price=px,time_msc=1000),tick,
                             dict(point=.01,tick_size=.01))['status'],'consistent')

    def test_absent_or_old_ticks_never_count_as_verified(self):
        d=dict(ticket=1,type=1,price=100,time_msc=10000)
        for ticks in ([],[dict(time_msc=100,bid=100,ask=100.1)]):
            self.assertEqual(audit_fill(d,ticks,{})['status'],'unavailable')

    def test_prefers_quote_before_fill_even_if_later_is_closer(self):
        d=dict(ticket=1,type=1,price=100,time_msc=10000)
        a=audit_fill(d,[dict(time_msc=9990,bid=100,ask=100.1),
                       dict(time_msc=10001,bid=105,ask=105.1)],dict(point=.01))
        self.assertEqual(a['bid'],100)


if __name__=='__main__':unittest.main()
