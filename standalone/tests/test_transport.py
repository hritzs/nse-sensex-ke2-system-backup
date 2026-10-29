"""Isolated deterministic peer sockets; never joins exchange multicast."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from websockets.asyncio.server import serve
from websockets.asyncio.client import connect
from standalone.feed_runtime import Runtime

class PeerTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name);(root/"shared").mkdir()
        (root/"shared/GreekTokens.csv").write_text("Token,Symbol,Expiry,Strike,OpType\n123,NIFTY,2030-12-31,0,FUT\n")
        (root/"shared/BSEIndexTokens.csv").write_text("xx.Token,Symbol,Expiry,Strike,OpType,Type\n456,SENSEX,2030-12-31,0,,FUTURE\n")
        self.root=root;self.tasks=[]
    async def asyncTearDown(self):
        for t in self.tasks:t.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tmp.cleanup()
    def runtime(self,node,peers=()):
        return Runtime({"root":str(self.root),"node_id":node,"peers":list(peers),"recovery_seconds":0})
    async def wait(self,predicate):
        async with asyncio.timeout(3):
            while not predicate():await asyncio.sleep(.01)
    def quote(self,ltp=100):
        return {"type":"market_quote","exchange":"BSE","token":456,"received_ms":time.time()*1000,
                "ltp":ltp,"bid":ltp-.05,"ask":ltp+.05,"volume":123}
    async def test_verified_peer_routes_but_never_republishes_fallback(self):
        a=self.runtime("node-a")
        a.selector.sources["local"].connected=a.selector.sources["local"].verified=True
        async with serve(a.raw_handler,"127.0.0.1",0) as server:
            url="ws://127.0.0.1:%s/ke2-source"%server.sockets[0].getsockname()[1]
            b=self.runtime("node-b",[{"name":"desk-a","raw_url":url}])
            t=asyncio.create_task(b.peer({"name":"desk-a","raw_url":url}));self.tasks.append(t)
            await self.wait(lambda:b.selector.sources["desk-a"].verified)
            await a.event("local",self.quote())
            await self.wait(lambda:b.routed.quotes.get(456,{}).get("ltp")==100)
            self.assertEqual(b.selector.selected["BSE"],"desk-a")
            self.assertNotIn(456,b.native.quotes)
            b.selector.sources["local"].connected=b.selector.sources["local"].verified=True
            await b.event("local",self.quote(101))
            self.assertEqual(b.selector.selected["BSE"],"local")
            self.assertEqual(b.native.quotes[456]["ltp"],101)
            self.assertEqual(b.routed.quotes[456]["ltp"],101)
    async def test_metadata_mismatch_rejects_even_connected_peer(self):
        async def peer(ws):
            await ws.send(json.dumps({"type":"source_identity","mode":"native","node_id":"other",
                "master_fingerprints":{"NSE":"wrong","BSE":"wrong"}}))
            await asyncio.sleep(.2)
        async with serve(peer,"127.0.0.1",0) as server:
            entry={"name":"bad","raw_url":"ws://127.0.0.1:%s/"%server.sockets[0].getsockname()[1]}
            b=self.runtime("node-b",[entry]);self.tasks.append(asyncio.create_task(b.peer(entry)))
            await self.wait(lambda:b.selector.sources["bad"].error is not None)
            self.assertFalse(b.selector.sources["bad"].verified)
            self.assertFalse(b.routed.quotes)
    async def test_routed_endpoint_cannot_be_used_as_peer(self):
        a=self.runtime("node-a")
        async with serve(lambda ws:a.raw_handler(ws,True),"127.0.0.1",0) as server:
            entry={"name":"loop","raw_url":"ws://127.0.0.1:%s/"%server.sockets[0].getsockname()[1]}
            b=self.runtime("node-b",[entry]);self.tasks.append(asyncio.create_task(b.peer(entry)))
            await self.wait(lambda:b.selector.sources["loop"].error is not None)
            self.assertFalse(b.selector.sources["loop"].verified)
    async def test_ke2_snapshot_exposes_routed_quote_and_source_status(self):
        b=self.runtime("node-b")
        b.selector.sources["local"].connected=b.selector.sources["local"].verified=True
        await b.event("local",self.quote())
        async with serve(b.routed.handler,"127.0.0.1",0) as server:
            async with connect("ws://127.0.0.1:%s/"%server.sockets[0].getsockname()[1]) as ws:
                self.assertEqual(json.loads(await ws.recv())["type"],"capabilities")
                await ws.send(json.dumps({"type":"subscribe","expiry":"2030-12-31",
                    "underlyings":[{"symbol":"SENSEX","segment":"FO_IDX","future":True}]}))
                self.assertEqual(json.loads(await ws.recv())["type"],"subscribed")
                snap=json.loads(await ws.recv())
                self.assertEqual(snap["data"][0]["ltp"],100)
                heartbeat=b.routed.heartbeat(next(iter(b.routed.sessions.values())))
                self.assertEqual(heartbeat["source_selection"]["selected"]["BSE"],"local")
if __name__=="__main__":unittest.main()
