import unittest
from standalone.failover import Selector

def event(stamp=100000, typ="market_quote", token=1):
    return {"type":typ,"token":token,"received_ms":stamp}

class SelectionTests(unittest.TestCase):
    def ready(self):
        s=Selector(["local","peer-a","peer-b"],10,15)
        for x in s.sources.values(): x.connected=x.verified=True
        return s
    def put(self,s,name,stream="NSE",now=100,stamp=100000,typ="market_quote"):
        return s.observe(name,stream,event(stamp,typ),now,now*1000)
    def test_local_first_and_per_exchange_failover(self):
        s=self.ready()
        self.put(s,"local");self.put(s,"peer-a");self.put(s,"peer-a","BSE")
        s.choose(100)
        self.assertEqual(s.selected,{"NSE":"local","BSE":"peer-a","BSE_INDEX":None})
        self.put(s,"peer-b",now=111,stamp=111000)
        s.choose(111);self.assertEqual(s.selected["NSE"],"peer-b")
    def test_tcp_and_oi_do_not_prove_freshness(self):
        s=self.ready();self.put(s,"local",typ="market_oi")
        s.choose(100);self.assertIsNone(s.selected["NSE"])
    def test_recovery_requires_continuous_quotes(self):
        s=self.ready();self.put(s,"peer-a");s.choose(100)
        self.put(s,"local",now=101,stamp=101000);s.choose(101)
        self.assertEqual(s.selected["NSE"],"peer-a")
        for now in (108,115,116):
            self.put(s,"peer-a",now=now,stamp=now*1000)
            self.put(s,"local",now=now,stamp=now*1000);s.choose(now)
        self.assertEqual(s.selected["NSE"],"local")
    def test_stale_future_and_replayed_timestamps(self):
        s=self.ready()
        self.assertFalse(self.put(s,"local",stamp=89000))
        self.assertFalse(self.put(s,"local",stamp=103000))
        self.put(s,"local",stamp=100000)
        self.assertFalse(self.put(s,"local",stamp=99000))
        self.put(s,"local",now=109,stamp=100000)
        s.choose(111);self.assertIsNone(s.selected["NSE"])
    def test_disconnect_discards_cache(self):
        s=self.ready();self.put(s,"local");s.choose(100)
        s.disconnect("local","connection lost");s.choose(100)
        self.assertIsNone(s.selected["NSE"]);self.assertEqual(s.snapshot("local","NSE",100000),[])
    def test_unverified_source_rejected(self):
        s=self.ready();s.sources["peer-a"].verified=False
        self.assertFalse(self.put(s,"peer-a"));s.choose(100)
        self.assertIsNone(s.selected["NSE"])
if __name__=="__main__":unittest.main()
