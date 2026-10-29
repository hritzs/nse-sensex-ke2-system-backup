import asyncio
import socket
import unittest

import httpx
from websockets.asyncio.server import serve

from ke2_test_api import create_app
import test_ke2_feed as fixtures

class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=fixtures.FeedTests("test_raw_ltp_and_missing_fields")
        self.fixture.setUp()
        self.server=await serve(self.fixture.feed.handler,"127.0.0.1",0)
        port=self.server.sockets[0].getsockname()[1]
        self.app=create_app(f"ws://127.0.0.1:{port}/",timeout=2)
        self.lifecycle=self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None,None,None)
        self.server.close();await self.server.wait_closed()
        self.fixture.tearDown()

    async def create_stock(self):
        response=await self.client.post("/sessions",json=self.fixture.request("RELIANCE"))
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    async def test_docs_health_capabilities_and_master(self):
        self.assertEqual((await self.client.get("/health")).json()["status"],"ok")
        self.assertEqual((await self.client.get("/docs")).status_code,200)
        schema=(await self.client.get("/openapi.json")).json()
        self.assertIn("/sessions/{session_id}/reconnect",schema["paths"])
        response=await self.client.get("/capabilities")
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()["data"]["eod"])
        response=await self.client.get("/instruments",params={"expiry":"2026-10-27","symbol":"RELIANCE"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["count"],11)
        self.assertEqual({row["instrument_type"] for row in response.json()["data"]},{"FUTSTK","OPTSTK"})

    async def test_stock_snapshot_quotes_events_and_unsupported(self):
        created=await self.create_stock();sid=created["session_id"]
        self.assertTrue(created["snapshot"]["snapshot"])
        self.assertTrue(all(r["ltp"] is None for r in created["snapshot"]["data"]))
        self.fixture.feed.ingest(self.fixture.tick(300))
        self.fixture.feed.ingest(self.fixture.tick(301,18.45))
        self.fixture.feed.ingest({"type":"market_oi","exchange":"NSE","token":301,
                                 "oi":4820000,"received_ms":1792030000000})
        response=await self.client.post(f"/sessions/{sid}/snapshot")
        self.assertEqual(response.status_code,200,response.text)
        rows={row["instrument_id"]:row for row in response.json()["data"]}
        self.assertEqual(rows[301]["ltp"],18.45)
        self.assertEqual(rows[301]["oi"],4820000)
        self.assertIsNone(rows[301]["volume"])
        cached=(await self.client.get(f"/sessions/{sid}/quotes")).json()
        self.assertTrue(any(row["instrument_id"]==301 for row in cached["data"]))
        events=(await self.client.get(f"/sessions/{sid}/events")).json()
        self.assertTrue(events["events"])
        self.assertEqual((await self.client.get(f"/sessions/{sid}/events",
                            params={"after":events["next_cursor"]})).json()["events"],[])
        bad=await self.client.post(f"/sessions/{sid}/request",json={"type":"eod"})
        self.assertEqual(bad.status_code,400)
        self.assertEqual(bad.json()["detail"]["code"],"unsupported")

    async def test_unsubscribe_reconnect_and_session_isolation(self):
        self.fixture.feed.ingest(self.fixture.tick(300))
        first=await self.create_stock();second=await self.create_stock()
        sid=first["session_id"]
        response=await self.client.post(f"/sessions/{sid}/unsubscribe",json={"instrument_ids":[301]})
        self.assertEqual(response.json()["type"],"unsubscribed")
        response=await self.client.post(f"/sessions/{sid}/reconnect")
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["snapshot"])
        self.assertNotIn(301,[r["instrument_id"] for r in response.json()["data"]])
        state=(await self.client.get(f"/sessions/{sid}")).json()
        self.assertEqual(state["upstream_session_id"],first["upstream_session_id"])
        other=await self.client.post(f"/sessions/{second['session_id']}/snapshot")
        self.assertIn(301,[r["instrument_id"] for r in other.json()["data"]])
        self.assertEqual((await self.client.delete(f"/sessions/{sid}")).status_code,200)
        self.assertEqual((await self.client.get(f"/sessions/{sid}")).status_code,404)
        self.assertTrue((await self.client.get(f"/sessions/{second['session_id']}")).json()["connected"])

    async def test_invalid_requests_preserve_upstream_error(self):
        bad=self.fixture.request("RELIANCE");bad["underlyings"][0]["segment"]="FO_IDX"
        response=await self.client.post("/sessions",json=bad)
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn("FO_STK",response.json()["detail"]["message"])
        self.assertEqual((await self.client.get("/sessions")).json(),[])
        self.assertEqual((await self.client.post("/sessions",json={"expiry":"bad","underlyings":[]})).status_code,422)
        self.assertEqual((await self.client.post("/sessions/no-such-id/snapshot")).status_code,404)

    async def test_real_index_snapshot_and_bod_status_routes(self):
        self.fixture.feed.ingest({"type":"market_index","exchange":"BSE",
            "index_code":1,"symbol":"SENSEX","ltp":80000.25,"received_ms":1792030000000})
        result=await self.client.get("/indices",params={"symbol":"SENSEX"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["data"][0]["ltp"],80000.25)
        self.assertEqual(result.json()["data"][0]["symbol"],"SENSEX")
        self.assertEqual((await self.client.get("/bod/status")).status_code,200)

    async def test_unreachable_upstream_is_not_healthy_feed(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            port=sock.getsockname()[1]
            app=create_app(f"ws://127.0.0.1:{port}/",timeout=.1)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    self.assertEqual((await client.get("/health")).status_code,200)
                    self.assertEqual((await client.get("/capabilities")).status_code,503)

    async def test_response_timeout_closes_session(self):
        async def silent(ws):
            await ws.send('{"type":"capabilities","session_id":"fixture","data":{}}')
            await ws.recv()
            await ws.wait_closed()
        async with serve(silent,"127.0.0.1",0) as server:
            port=server.sockets[0].getsockname()[1]
            app=create_app(f"ws://127.0.0.1:{port}/",timeout=.05)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    response=await client.post("/sessions",json=self.fixture.request("RELIANCE"))
                    self.assertEqual(response.status_code,504,response.text)
                    self.assertEqual((await client.get("/sessions")).json(),[])

if __name__=="__main__":unittest.main()
