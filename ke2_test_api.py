#!/usr/bin/env python3
"""HTTP/Swagger test client for the existing KE2 WebSocket feed."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import os
from typing import Annotated, Literal
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class OptionWindow(InputModel):
    mode: Literal["around_future"] = "around_future"
    strikes_each_side: Annotated[int, Field(ge=0, le=1000, strict=True)] = 6
    types: Annotated[list[Literal["CE", "PE"]], Field(min_length=1, max_length=2)] = ["CE", "PE"]
    step: Annotated[float, Field(gt=0)] | None = None

class Underlying(InputModel):
    symbol: Annotated[str, Field(min_length=1, max_length=40)]
    segment: Literal["FO_STK", "FO_IDX"]
    future: bool = True
    options: OptionWindow | None = None
    extra_strikes: list[Annotated[float, Field(ge=0)]] = []

class Subscribe(InputModel):
    type: Literal["subscribe"] = "subscribe"
    request_id: Annotated[str, Field(max_length=128)] | None = None
    expiry: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    underlyings: Annotated[list[Underlying], Field(min_length=1, max_length=100)]
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, json_schema_extra={
        "examples": [
            {"expiry":"2026-10-27","underlyings":[{"symbol":"RELIANCE","segment":"FO_STK","future":True,
             "options":{"mode":"around_future","strikes_each_side":6,"types":["CE","PE"]},"extra_strikes":[1300]}]},
            {"expiry":"2026-10-29","underlyings":[{"symbol":"SENSEX","segment":"FO_IDX","future":True}]},
            {"expiry":"2026-10-27","underlyings":[{"symbol":"NIFTY","segment":"FO_IDX","future":True}]},
        ]})

class Unsubscribe(InputModel):
    instrument_ids: list[Annotated[int, Field(gt=0, strict=True)]]

class FeedFailure(Exception):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail

class Session:
    def __init__(self, uri, timeout=10):
        self.id = uuid.uuid4().hex
        self.uri = uri
        self.timeout = timeout
        self.ws = None
        self.reader = None
        self.connected = False
        self.capabilities = None
        self.upstream_session_id = None
        self.last_error = None
        self.heartbeat = None
        self.acknowledgement = None
        self.quotes = {}
        self.history = deque(maxlen=50)
        self.cursor = 0
        self.command_lock = asyncio.Lock()
        self.responses = None

    def record(self, message):
        self.cursor += 1
        self.history.append({"cursor":self.cursor,
                             "observed_at":datetime.now(timezone.utc).isoformat(),
                             "message":message})
        typ = message.get("type")
        if typ == "heartbeat":
            self.heartbeat = message
        elif typ == "subscribed":
            self.acknowledgement = message
            self.upstream_session_id = message["session_id"]
        elif typ == "quote":
            if message.get("snapshot"):
                self.quotes.clear()
            for row in message.get("data", []):
                self.quotes[row["instrument_id"]] = row
        elif typ == "unsubscribed":
            for iid in message.get("instrument_ids", []):
                self.quotes.pop(iid, None)

    async def open(self):
        try:
            self.ws = await connect(self.uri, open_timeout=self.timeout, close_timeout=2,
                                    max_size=8*1024*1024, max_queue=16)
            hello = json.loads(await asyncio.wait_for(self.ws.recv(), self.timeout))
            if not isinstance(hello, dict) or hello.get("type") != "capabilities":
                raise FeedFailure(502, "Expected KE2 capabilities; use port 9002, not legacy port 9001.")
            self.capabilities = hello
            self.upstream_session_id = hello["session_id"]
            self.last_error = None
            self.connected = True
            self.record(hello)
            self.reader = asyncio.create_task(self.read())
        except FeedFailure:
            await self.close()
            raise
        except (OSError, WebSocketException, TimeoutError, ValueError, KeyError) as exc:
            await self.close()
            raise FeedFailure(503, f"Cannot open KE2 upstream {self.uri}: {exc}") from exc

    async def read(self):
        try:
            async for text in self.ws:
                message = json.loads(text)
                if not isinstance(message, dict):
                    raise ValueError("Upstream message must be an object")
                self.record(message)
                if self.responses is not None:
                    self.responses.put_nowait(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self.connected = False
            if self.responses is not None:
                try:
                    self.responses.put_nowait(None)
                except asyncio.QueueFull:
                    pass

    async def close(self):
        self.connected = False
        if self.ws is not None:
            await self.ws.close()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None

    async def command(self, request):
        async with self.command_lock:
            return await self.exchange(request)

    async def exchange(self, request):
        if not self.connected:
            raise FeedFailure(503, {"message":"KE2 session disconnected; call reconnect.",
                                    "last_error":self.last_error})
        request = dict(request)
        request.setdefault("request_id", uuid.uuid4().hex)
        if request["request_id"] is None:
            request["request_id"] = uuid.uuid4().hex
        typ = request.get("type")
        result = []
        self.responses = asyncio.Queue(maxsize=256)
        try:
            async with asyncio.timeout(self.timeout):
                await self.ws.send(json.dumps(request, allow_nan=False))
                while True:
                    message = await self.responses.get()
                    if message is None:
                        raise FeedFailure(503, "KE2 upstream disconnected while awaiting response.")
                    if "request_id" in message and message["request_id"] != request["request_id"]:
                        continue
                    kind = message.get("type")
                    if kind == "error":
                        raise FeedFailure(400, message)
                    if typ == "subscribe":
                        if kind == "subscribed":
                            result.append(message)
                        elif kind == "quote" and message.get("snapshot") and result:
                            return result + [message]
                    elif typ in ("snapshot", "resume"):
                        if kind == "quote" and message.get("snapshot"):
                            if typ == "resume":
                                self.upstream_session_id = request["session_id"]
                            return [message]
                    elif typ == "unsubscribe" and kind == "unsubscribed":
                        return [message]
                    elif typ == "indices" and kind == "indices" and message.get("snapshot"):
                        return [message]
                    elif typ == "instrument_master" and kind == "instrument_master":
                        result.append(message)
                        if not message.get("more"):
                            return result
                    # Other request types should return the upstream unsupported error.
        except TimeoutError as exc:
            # Discard this connection so a late uncorrelated snapshot cannot
            # be mistaken for the response to a subsequent command.
            await self.close()
            raise FeedFailure(504, "Timed out awaiting a KE2 response; inspect upstream status.") from exc
        except (OSError, WebSocketException) as exc:
            raise FeedFailure(503, str(exc)) from exc
        finally:
            self.responses = None

    async def reconnect(self):
        async with self.command_lock:
            old = self.upstream_session_id
            await self.close()
            self.heartbeat = None
            await self.open()
            # The upstream may still be completing the old disconnect handler.
            for attempt in range(3):
                try:
                    messages = await self.exchange({"type":"resume","session_id":old})
                    self.upstream_session_id = old
                    return messages[0]
                except FeedFailure as exc:
                    active = isinstance(exc.detail, dict) and exc.detail.get("message") == "Session already active"
                    if not active or attempt == 2:
                        raise
                    await asyncio.sleep(.05)

    def state(self):
        return {"session_id":self.id, "upstream_session_id":self.upstream_session_id,
                "connected":self.connected, "last_error":self.last_error,
                "heartbeat":self.heartbeat, "subscribed":self.acknowledgement,
                "quote_count":len(self.quotes), "cursor":self.cursor}

    def events(self, after, limit):
        events = [event for event in self.history if event["cursor"] > after][:limit]
        earliest = self.history[0]["cursor"] if self.history else 0
        return {"session_id":self.id, "events":events,
                "next_cursor":events[-1]["cursor"] if events else after,
                "history_truncated":bool(after and after < earliest-1)}


def create_app(upstream=None, timeout=10):
    uri = upstream or os.environ.get("KE2_TEST_UPSTREAM","ws://127.0.0.1:9002/")
    sessions = {}
    registry_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.gather(*(s.close() for s in sessions.values()), return_exceptions=True)
        sessions.clear()

    app = FastAPI(title="KE2 Feed API Tester", version="1.0",
                  description="HTTP test client for the existing KE2 WebSocket. Market data is passed through unchanged. No order/trading endpoints.",
                  lifespan=lifespan)
    app.state.sessions = sessions

    @app.exception_handler(FeedFailure)
    async def failure(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail":exc.detail})

    def get_session(session_id):
        if session_id not in sessions:
            raise HTTPException(404, "Unknown test session")
        return sessions[session_id]

    @app.get("/health", tags=["Discovery"])
    async def health():
        """HTTP service health, not exchange-data freshness."""
        return {"status":"ok", "upstream":uri, "test_sessions":len(sessions),
                "note":"Use capabilities and session heartbeat to inspect the upstream feed."}

    @app.get("/capabilities", tags=["Discovery"])
    async def capabilities():
        """Connect to KE2 and return its actual capabilities message."""
        session = Session(uri,timeout)
        try:
            await session.open()
            return session.capabilities
        finally:
            await session.close()

    @app.get("/indices", tags=["Discovery"])
    async def indices(symbol: str | None = None):
        """Actual observed index snapshots, including SENSEX from BSE EQ."""
        session = Session(uri,timeout)
        try:
            await session.open()
            request = {"type": "indices"}
            if symbol is not None: request["symbols"] = [symbol.upper()]
            return (await session.command(request))[0]
        finally:
            await session.close()

    @app.get("/bod/status", tags=["Discovery"])
    async def bod_status():
        """Last daily preparation outcome; separate from price freshness."""
        path = Path(__file__).resolve().parent/"data/bod/status.json"
        if not path.exists():
            return {"state": "NOT_INSTALLED"}
        return json.loads(path.read_text())

    @app.get("/instruments", tags=["Discovery"])
    async def instruments(expiry: str | None = None, symbol: str | None = None):
        """Collect all master chunks, then optionally filter by symbol."""
        session = Session(uri,timeout)
        try:
            await session.open()
            request = {"type":"instrument_master"}
            if expiry is not None:
                request["expiry"] = expiry
            messages = await session.command(request)
            rows = [row for message in messages for row in message["data"]
                    if symbol is None or row["symbol"] == symbol.upper()]
            return {"count":len(rows), "data":rows,
                    "master_sources":session.capabilities.get("master_sources",[])}
        finally:
            await session.close()

    @app.get("/sessions", tags=["Sessions"])
    async def list_sessions():
        return [session.state() for session in sessions.values()]

    @app.post("/sessions", status_code=201, tags=["Sessions"])
    async def new_session(request: Subscribe):
        """Create an independent KE2 connection, subscribe, and return its snapshot."""
        session = Session(uri,timeout)
        async with registry_lock:
            if len(sessions) >= 16:
                raise HTTPException(429, "Close an unused test session; maximum is 16.")
            sessions[session.id] = session
        try:
            await session.open()
            messages = await session.command(request.model_dump(exclude_none=True))
            return {"session_id":session.id, "upstream_session_id":session.upstream_session_id,
                    "acknowledgement":messages[0], "snapshot":messages[-1]}
        except BaseException:
            sessions.pop(session.id,None)
            await session.close()
            raise

    @app.get("/sessions/{session_id}", tags=["Sessions"])
    async def session_state(session_id: str):
        return get_session(session_id).state()

    @app.delete("/sessions/{session_id}", tags=["Sessions"])
    async def delete_session(session_id: str):
        """Close only this tester's upstream connection."""
        session = get_session(session_id)
        async with session.command_lock:
            sessions.pop(session_id,None)
            await session.close()
        return {"closed":True, "session_id":session_id}

    @app.post("/sessions/{session_id}/subscribe", tags=["Feed requests"])
    async def resubscribe(session_id: str, request: Subscribe):
        """Replace the whole subscription for this test session."""
        messages = await get_session(session_id).command(request.model_dump(exclude_none=True))
        return {"acknowledgement":messages[0], "snapshot":messages[-1]}

    @app.post("/sessions/{session_id}/snapshot", tags=["Feed requests"])
    async def snapshot(session_id: str):
        return (await get_session(session_id).command({"type":"snapshot"}))[0]

    @app.post("/sessions/{session_id}/unsubscribe", tags=["Feed requests"])
    async def unsubscribe(session_id: str, request: Unsubscribe):
        return (await get_session(session_id).command(
            {"type":"unsubscribe", **request.model_dump()}))[0]

    @app.post("/sessions/{session_id}/reconnect", tags=["Feed requests"])
    async def reconnect(session_id: str):
        """Reconnect and resume the old KE2 session; return its full snapshot."""
        return await get_session(session_id).reconnect()

    @app.post("/sessions/{session_id}/request", tags=["Feed requests"])
    async def raw_request(session_id: str, request: dict):
        """Send raw KE2 JSON, including unsupported eod/bar_1m requests to inspect errors."""
        if not isinstance(request.get("type"),str):
            raise HTTPException(422, "A string type field is required.")
        return {"messages":await get_session(session_id).command(request)}

    @app.get("/sessions/{session_id}/quotes", tags=["Observe"])
    async def quotes(session_id: str):
        """Latest cached rows; call snapshot for authoritative current membership."""
        session = get_session(session_id)
        return {"session_id":session_id, "connected":session.connected,
                "heartbeat":session.heartbeat,
                "data":[session.quotes[key] for key in sorted(session.quotes)]}

    @app.get("/sessions/{session_id}/events", tags=["Observe"])
    async def events(session_id: str, after: Annotated[int,Query(ge=0)]=0,
                     limit: Annotated[int,Query(ge=1,le=50)]=50):
        """Poll the last 50 received messages. Cursor is local, not an exchange sequence."""
        return get_session(session_id).events(after,limit)

    @app.get("/sessions/{session_id}/stream", tags=["Observe"])
    async def stream(request: Request, session_id: str, after: Annotated[int,Query(ge=0)]=0):
        """SSE observation stream; try curl -N. Does not alter the subscription."""
        session = get_session(session_id)
        async def generate():
            cursor = after
            while sessions.get(session_id) is session and not await request.is_disconnected():
                batch = session.events(cursor,50)
                if batch["history_truncated"]:
                    yield "event: history_truncated\ndata: {}\n\n"
                for event in batch["events"]:
                    yield f"id: {event['cursor']}\ndata: {json.dumps(event)}\n\n"
                    cursor = event["cursor"]
                await asyncio.sleep(.2)
        return StreamingResponse(generate(),media_type="text/event-stream",
                                 headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    return app

app = create_app()
