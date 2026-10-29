import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from websockets.asyncio.server import serve
from standalone.gamma_source import source_loop
from standalone.failover import fingerprints
from ke2_feed_server import Instrument
class GammaTransportTests(unittest.IsolatedAsyncioTestCase):
    def simulator(self,url):
        rows=[]
        return SimpleNamespace(master=SimpleNamespace(by_id={123:Instrument(123,"NSE",123,"NIFTY","2030-12-31",None,None,"FUTIDX")}),
            source=url,connected=False,connection_epoch=0,quotes={},last_sample={},error=None,
            ingest=rows.append,rows=rows)
    async def wait(self,test):
        async with asyncio.timeout(3):
            while not test():await asyncio.sleep(.01)
    async def test_routed_source_verifies_identity_and_rejects_stale_quotes(self):
        sim=self.simulator("")
        async def handler(ws):
            await ws.send(json.dumps({"type":"source_identity","mode":"routed",
                "master_fingerprints":fingerprints(sim.master)}))
            for stamp in (time.time()*1000-20000,time.time()*1000):
                await ws.send(json.dumps({"type":"market_quote","received_ms":stamp,"token":123}))
            await ws.wait_closed()
        async with serve(handler,"127.0.0.1",0) as server:
            sim.source="ws://127.0.0.1:%s/"%server.sockets[0].getsockname()[1]
            task=asyncio.create_task(source_loop(sim))
            try:
                await self.wait(lambda:bool(sim.rows))
                self.assertEqual(len(sim.rows),1);self.assertTrue(sim.connected)
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
    async def test_mismatched_metadata_never_ingests(self):
        sim=self.simulator("")
        async def handler(ws):
            await ws.send(json.dumps({"type":"source_identity","mode":"routed","master_fingerprints":{}}))
            await ws.wait_closed()
        async with serve(handler,"127.0.0.1",0) as server:
            sim.source="ws://127.0.0.1:%s/"%server.sockets[0].getsockname()[1]
            task=asyncio.create_task(source_loop(sim))
            try:
                await self.wait(lambda:sim.error is not None)
                self.assertIn("metadata mismatch",sim.error)
                self.assertFalse(sim.connected);self.assertFalse(sim.rows)
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
if __name__=="__main__":unittest.main()
