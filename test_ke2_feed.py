import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from ke2_feed_server import Feed, Master, Subscription

class FeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "shared").mkdir()
        header = ["xx.Token", "Exchange", "Type", "Symbol", "Expiry", "Strike", "OpType", "Token"]
        for exchange, symbol, base, filename in [
            ("NSE", "NIFTY", 100, "GreekTokens.csv"), ("BSE", "SENSEX", 200, "BSEIndexTokens.csv")]:
            with (self.root / "shared" / filename).open("w", newline="") as f:
                writer = csv.writer(f); writer.writerow(header)
                writer.writerow([base, exchange, "FUTURE" if exchange=="BSE" else "FUTIDX", symbol, "27-OCT-26", 0, "" if exchange=="BSE" else "XX", "IF" if exchange=="BSE" else base])
                for n, (strike, typ) in enumerate(( (k,t) for k in (100,150,200,250,300) for t in ("CE","PE")),1):
                    writer.writerow([base+n,exchange,"OPTIDX",symbol,"27-OCT-26",strike,typ,base+n])
        with (self.root / "shared" / "GreekTokens.csv").open("a", newline="") as f:
            writer=csv.writer(f)
            writer.writerow([300,"NSE","FUTSTK","RELIANCE","27-OCT-26",0,"XX",300])
            for n,(strike,typ) in enumerate(((k,t) for k in (100,150,200,250,300) for t in ("CE","PE")),1):
                writer.writerow([300+n,"NSE","OPTSTK","RELIANCE","27-OCT-26",strike,typ,300+n])
        self.master = Master(self.root)
        self.feed = Feed(self.master)

    def tearDown(self):
        self.temp.cleanup()

    async def test_only_matching_future_recenters_subscription(self):
        from unittest.mock import patch
        from dataclasses import replace
        sub = Subscription()
        self.feed.subscribe(sub, self.request())
        self.feed.sessions["test"] = sub
        original = sub.instruments.copy()
        # Neither another symbol nor another expiry can move this window.
        other = replace(self.master.by_id[100], instrument_id=999, token=999,
                        expiry="2026-11-03")
        self.master.by_key[("NSE",999)] = other
        with patch.object(self.feed, "resolve", wraps=self.feed.resolve) as resolve:
            self.feed.ingest(self.tick(200, exchange="BSE"))
            self.feed.ingest(self.tick(999))
            self.feed.ingest(self.tick(101))
            resolve.assert_not_called()
            self.assertEqual(sub.instruments, original)
            self.feed.ingest(self.tick(100))
            resolve.assert_called_once_with(sub)
            self.assertGreater(len(sub.instruments),len(original))

    def request(self, symbol="NIFTY"):
        return {"type":"subscribe", "expiry":"2026-10-27", "underlyings":[
            {"symbol":symbol,"segment":"FO_IDX" if symbol in ("NIFTY","SENSEX") else "FO_STK","future":True,
             "options":{"mode":"around_future","strikes_each_side":1,"types":["CE","PE"]},
             "extra_strikes":[300]}]}

    def tick(self, token, ltp=150, exchange="NSE"):
        return {"type":"market_quote","exchange":exchange,"token":token,
                "ltp":ltp,"bid":ltp-1,"ask":ltp+3,"received_ms":1792030000000}

    def test_raw_ltp_and_missing_fields(self):
        self.feed.ingest(self.tick(101,18.45))
        q=self.feed.quote(101)
        self.assertEqual(q["ltp"],18.45)
        self.assertNotEqual(q["ltp"],(q["bid"]+q["ask"])/2)
        self.assertIsNone(q["volume"])
        self.assertIsNone(q["exchange_ts"])
        self.assertIsNotNone(q["received_ts"])
        self.feed.ingest({"type":"market_oi","exchange":"NSE","token":101,"oi":2**40,"received_ms":1792030000000})
        self.assertEqual(self.feed.quote(101)["oi"],2**40)

    def test_strike_recenter_and_extra_strikes(self):
        sub=Subscription();self.feed.sessions["one"]=sub
        self.feed.ingest(self.tick(100))
        self.feed.subscribe(sub,self.request())
        self.assertEqual(len(sub.instruments),9)  # future, 3 pairs, pinned 300 pair
        removed=101; sub.excluded.add(removed);sub.instruments.discard(removed)
        self.feed.ingest(self.tick(100,200))
        self.assertNotIn(removed,sub.instruments)
        self.assertIn(109,sub.instruments)  # pinned strike survives center change
        self.assertIn(107,sub.dirty)

    def test_pending_future_and_exchange_isolation(self):
        n=Subscription(); b=Subscription()
        self.feed.subscribe(n,self.request())
        self.feed.subscribe(b,self.request("SENSEX"))
        self.feed.sessions.update(n=n,b=b)
        self.assertTrue(n.pending)
        self.assertEqual(b.instruments,{200,209,210})
        self.feed.ingest(self.tick(200,150,"BSE"))
        self.assertFalse(b.pending)
        self.assertTrue(n.pending)
        self.assertTrue(all(self.master.by_id[i].exchange=="BSE" for i in b.instruments))
        self.assertEqual(self.feed.heartbeat(b)["feed_status"],"DEGRADED")

    def test_stock_contracts_quotes_and_segment_validation(self):
        sub=Subscription();self.feed.sessions["stock"]=sub
        req=self.request("RELIANCE")
        self.feed.subscribe(sub,req)
        self.assertEqual(self.master.by_id[300].instrument_type,"FUTSTK")
        self.assertEqual(self.master.by_id[301].instrument_type,"OPTSTK")
        self.feed.ingest(self.tick(300))
        self.feed.ingest(self.tick(301,18.45))
        self.feed.ingest({"type":"market_oi","exchange":"NSE","token":301,"oi":4820000,"received_ms":1792030000000})
        self.assertIn(301,sub.instruments)
        self.assertEqual(self.feed.quote(301)["ltp"],18.45)
        self.assertEqual(self.feed.quote(301)["oi"],4820000)
        self.assertIsNone(self.feed.quote(301)["volume"])
        req["underlyings"][0]["segment"]="FO_IDX"
        with self.assertRaisesRegex(ValueError,"FO_STK"):self.feed.subscribe(sub,req)

    def test_invalid_subscription_is_atomic(self):
        sub=Subscription();self.feed.subscribe(sub,self.request())
        before=sub.instruments.copy()
        bad=self.request();bad["underlyings"][0]["extra_strikes"]=[999]
        with self.assertRaises(ValueError):self.feed.subscribe(sub,bad)
        self.assertEqual(sub.instruments,before)
        with self.assertRaises(ValueError):self.feed.subscribe(sub,{**self.request(),"expiry":"2026-10-13"})

    async def test_websocket_snapshot_unsubscribe_resume_and_unsupported(self):
        async with serve(self.feed.handler,"127.0.0.1",0) as server:
            port=server.sockets[0].getsockname()[1]
            uri=f"ws://127.0.0.1:{port}"
            async with connect(uri) as ws:
                hello=json.loads(await ws.recv());session=hello["session_id"]
                self.assertFalse(hello["data"]["eod"])
                await ws.send(json.dumps(self.request("SENSEX")))
                ack=json.loads(await ws.recv())
                self.assertIn(200,ack["instrument_ids"])
                snap=json.loads(await ws.recv())
                self.assertTrue(snap["snapshot"])
                self.assertTrue(all(d["ltp"] is None for d in snap["data"]))
                await ws.send(json.dumps({"type":"unsubscribe","instrument_ids":[200]}))
                self.assertEqual(json.loads(await ws.recv())["type"],"unsubscribed")
                await ws.send(json.dumps({"type":"eod"}))
                self.assertEqual(json.loads(await ws.recv())["code"],"unsupported")
                await ws.send("[]")
                self.assertEqual(json.loads(await ws.recv())["code"],"invalid_request")
            await asyncio.sleep(.05)
            async with connect(uri) as ws:
                await ws.recv()
                await ws.send(json.dumps({"type":"resume","session_id":session}))
                snap=json.loads(await ws.recv())
                self.assertTrue(snap["snapshot"])
                self.assertNotIn(200,[d["instrument_id"] for d in snap["data"]])

if __name__=="__main__": unittest.main()
