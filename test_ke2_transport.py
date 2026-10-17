import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from test_ke2_feed import FeedTests

FIXTURE_REASON = "Set KE2_TRANSPORT_FIXTURE to the fixture executable"

class TransportTests(FeedTests):
    @unittest.skipUnless(os.environ.get("KE2_TRANSPORT_FIXTURE"), FIXTURE_REASON)
    async def test_bse_packet_to_ke2_and_legacy(self):
        await self._check_packet_to_ke2("SENSEX",200,100)

    @unittest.skipUnless(os.environ.get("KE2_TRANSPORT_FIXTURE"), FIXTURE_REASON)
    async def test_stock_packet_to_ke2_and_legacy(self):
        await self._check_packet_to_ke2("RELIANCE",300,150)

    async def _check_packet_to_ke2(self,symbol,base,future_ltp):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        executable=str(Path(os.environ["KE2_TRANSPORT_FIXTURE"]).resolve())
        process=subprocess.Popen([executable,str(port)],cwd=self.root,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        source=None
        try:
            for attempt in range(100):
                try:
                    legacy=await connect(f"ws://127.0.0.1:{port}/")
                    break
                except OSError:
                    await asyncio.sleep(.05)
            else:
                self.fail("Fixture WebSocket did not start")
            await legacy.close()
            async with connect(f"ws://127.0.0.1:{port}/") as legacy:
                source=asyncio.create_task(self.feed.source(f"ws://127.0.0.1:{port}/ke2-source"))
                async with serve(self.feed.handler,"127.0.0.1",0) as server:
                    ke2port=server.sockets[0].getsockname()[1]
                    async with connect(f"ws://127.0.0.1:{ke2port}") as client:
                        await client.recv()
                        req=self.request(symbol)
                        if symbol=="SENSEX":
                            req["underlyings"][0].pop("options")
                            req["underlyings"][0].pop("extra_strikes")
                        await client.send(json.dumps(req))
                        await client.recv()
                        snap=json.loads(await client.recv())
                        self.assertIn(base,[r["instrument_id"] for r in snap["data"]])
                        rows={}
                        async with asyncio.timeout(6):
                            while True:
                                message=json.loads(await client.recv())
                                if message["type"]=="quote":
                                    rows.update({r["instrument_id"]:r for r in message["data"]})
                                future_ready=rows.get(base,{}).get("ltp") is not None
                                option_ready=symbol=="SENSEX" or (
                                    rows.get(base+1,{}).get("ltp") is not None and
                                    rows.get(base+1,{}).get("oi")==4820000)
                                if future_ready and option_ready:
                                    break
                        quote=rows[base]
                        self.assertEqual(quote["ltp"],future_ltp)
                        self.assertEqual(quote["bid"],future_ltp-.05)
                        self.assertEqual(quote["ask"],future_ltp+.05)
                        self.assertIsNone(quote["exchange_ts"])
                        if symbol=="RELIANCE":
                            option=rows[base+1]
                            self.assertEqual(option["ltp"],18.45)
                            self.assertEqual(option["bid"],18.4)
                            self.assertEqual(option["ask"],18.55)
                            self.assertEqual(option["oi"],4820000)
                            self.assertIsNone(option["volume"])
                        async with asyncio.timeout(3):
                            while True:
                                old=json.loads(await legacy.recv())
                                self.assertNotEqual(old["type"],"market_quote")
                                if old["type"]=="future_quote" and old["symbol"].startswith(symbol+"|"):
                                    break
                        self.assertEqual(old["ltp"],future_ltp)
        finally:
            if source:
                source.cancel();await asyncio.gather(source,return_exceptions=True)
            process.terminate()
            await asyncio.to_thread(process.wait,5)

if __name__=="__main__":unittest.main()
