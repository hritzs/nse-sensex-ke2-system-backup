"""Live feed WebSocket client: replaces RTD("greekserver...") and the GreekExcel MarketWatch.

One WebSocket connection per (symbol, expiry), because the feed binds one expiry cycle to a
connection. Each connection subscribes to the future (when listed) plus an explicit
strike list chosen by the engine (open positions + a window around the forward).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

log = logging.getLogger("feed")


class Instrument:
    __slots__ = ("iid", "exchange", "symbol", "expiry", "strike", "kind")

    def __init__(self, row):
        self.iid = row["instrument_id"]
        self.exchange = row["exchange"]
        self.symbol = row["symbol"]
        self.expiry = row["expiry"]
        self.strike = row["strike"]
        self.kind = row["option_type"] or "FUT"


class Master:
    def __init__(self):
        self.by_id: dict[int, Instrument] = {}
        self.by_key: dict[tuple, int] = {}
        self.expiries: dict[str, list[str]] = {}
        self.strikes: dict[tuple, list[float]] = {}
        self.has_future: set[tuple] = set()
        self.loaded_at: str | None = None
        self.sources = []

    def load(self, rows, symbols):
        for row in rows:
            if row["symbol"] not in symbols:
                continue
            ins = Instrument(row)
            self.by_id[ins.iid] = ins
            self.by_key[(ins.symbol, ins.expiry, ins.strike if ins.kind != "FUT" else None, ins.kind)] = ins.iid
        exp: dict[str, set] = {}
        strikes: dict[tuple, set] = {}
        for ins in self.by_id.values():
            exp.setdefault(ins.symbol, set()).add(ins.expiry)
            if ins.kind == "FUT":
                self.has_future.add((ins.symbol, ins.expiry))
            else:
                strikes.setdefault((ins.symbol, ins.expiry), set()).add(ins.strike)
        self.expiries = {s: sorted(v) for s, v in exp.items()}
        self.strikes = {k: sorted(v) for k, v in strikes.items()}
        self.loaded_at = datetime.now().isoformat(timespec="seconds")

    def iid(self, symbol, expiry, strike, kind):
        return self.by_key.get((symbol, expiry, None if kind == "FUT" else float(strike), kind))


class Connection:
    """One feed session for one (symbol, expiry)."""

    def __init__(self, feed, symbol, expiry):
        self.feed = feed
        self.symbol = symbol
        self.expiry = expiry
        self.strikes: tuple = ()
        self.want_future = False
        self.session_id = None
        self.connected = False
        self.status = "starting"
        self.pending = []
        self.heartbeat = None
        self.last_error = None
        self.instrument_ids: list[int] = []
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    def request(self):
        segment = "FO_IDX"
        und = {"symbol": self.symbol, "segment": segment, "future": self.want_future}
        if self.strikes:
            und["options"] = {"mode": "around_future", "strikes_each_side": 0, "types": ["CE", "PE"]}
            und["extra_strikes"] = list(self.strikes)
        return {"type": "subscribe", "request_id": f"wg-{self.symbol}-{self.expiry}-{uuid.uuid4().hex[:6]}",
                "expiry": self.expiry, "underlyings": [und]}

    def set_target(self, strikes, want_future):
        strikes = tuple(sorted(strikes))
        if strikes != self.strikes or want_future != self.want_future:
            self.strikes = strikes
            self.want_future = want_future
            self.wake.set()

    async def run(self):
        backoff = 1
        while True:
            try:
                async with connect(self.feed.url, open_timeout=10, close_timeout=2,
                                   max_size=16 * 1024 * 1024, max_queue=256) as ws:
                    hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if hello.get("type") != "capabilities":
                        raise RuntimeError("Not the market feed (expected capabilities)")
                    self.connected = True
                    self.status = "connected"
                    backoff = 1
                    sent = None
                    self._hello_id = hello["session_id"]
                    if self.session_id:
                        await ws.send(json.dumps({"type": "resume", "session_id": self.session_id}))
                        sent = self._current_request_key()
                    else:
                        self.session_id = hello["session_id"]
                    reader = asyncio.create_task(self._read(ws))
                    try:
                        while True:
                            key = self._current_request_key()
                            if key != sent and (self.strikes or self.want_future):
                                await ws.send(json.dumps(self.request()))
                                sent = key
                            self.wake.clear()
                            waiter = asyncio.create_task(self.wake.wait())
                            done, _ = await asyncio.wait({reader, waiter}, return_when=asyncio.FIRST_COMPLETED)
                            if reader in done:
                                waiter.cancel()
                                reader.result()
                                break
                    finally:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException, RuntimeError, TimeoutError, ValueError) as exc:
                self.last_error = str(exc)
            self.connected = False
            self.status = "reconnecting"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15)

    def _current_request_key(self):
        return (self.strikes, self.want_future)

    async def _read(self, ws):
        async for text in ws:
            msg = json.loads(text)
            typ = msg.get("type")
            if typ == "quote":
                self.feed.on_quote(msg)
            elif typ == "heartbeat":
                self.heartbeat = msg
                self.pending = msg.get("pending", [])
                self.status = msg.get("feed_status", "?")
            elif typ == "subscribed":
                self.instrument_ids = msg.get("instrument_ids", [])
                self.pending = msg.get("pending", [])
                self.session_id = msg.get("session_id", self.session_id)
            elif typ == "error":
                self.last_error = msg.get("message")
                if "session" in (msg.get("message") or "").lower():
                    # resume failed: start a fresh subscription on this socket
                    self.session_id = getattr(self, "_hello_id", None)
                    await ws.send(json.dumps(self.request()))
                log.warning("%s %s feed error: %s", self.symbol, self.expiry, msg.get("message"))

    def state(self):
        hb = self.heartbeat or {}
        return {"symbol": self.symbol, "expiry": self.expiry, "connected": self.connected,
                "status": self.status, "pending": self.pending, "subscribed": len(self.instrument_ids),
                "requested_strikes": len(self.strikes), "source_connected": hb.get("source_connected"),
                "exchanges": hb.get("exchanges"), "last_error": self.last_error}

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class Feed:
    def __init__(self, url, symbols):
        self.url = url
        self.symbols = set(symbols)
        self.master = Master()
        self.quotes: dict[int, dict] = {}
        self.conns: dict[tuple, Connection] = {}
        self.master_error = None
        self.last_quote_mono = None

    async def _fetch_master(self) -> Master:
        async with connect(self.url, open_timeout=10, max_size=32 * 1024 * 1024) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            rid = "wg-master"
            await ws.send(json.dumps({"type": "instrument_master", "request_id": rid}))
            rows = []
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if msg.get("type") == "instrument_master" and msg.get("request_id") == rid:
                    rows.extend(msg.get("data", []))
                    if not msg.get("more"):
                        break
                elif msg.get("type") == "error":
                    raise RuntimeError(msg.get("message"))
        m = Master()
        m.sources = hello.get("master_sources", [])
        m.load(rows, self.symbols)
        return m

    async def refresh_master(self):
        """Fetch the feed's list again (after the feed adapter's inputs were refreshed). One try."""
        try:
            m = await self._fetch_master()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Instrument master refresh failed: %s", exc)
            return False
        if m.by_id:
            added = len(set(m.by_key) - set(self.master.by_key))
            self.master = m
            if added:
                log.info("Instrument master refreshed: %d index instruments (%d new)", len(m.by_id), added)
        return True

    async def load_master(self):
        """Fetch the full instrument master (all expiries) and keep index rows; retry until the feed is up."""
        while True:
            try:
                self.master = await self._fetch_master()
                self.master_error = None
                log.info("Instrument master: %d index instruments", len(self.master.by_id))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep retrying until the feed is up
                self.master_error = f"{type(exc).__name__}: {exc}"
                log.warning("Master load failed (%s); retrying", self.master_error)
                await asyncio.sleep(3)

    def on_quote(self, msg):
        now = time.monotonic()
        for row in msg.get("data", []):
            iid = row.get("instrument_id")
            if iid is None:
                continue
            q = self.quotes.setdefault(iid, {})
            q.update({k: row.get(k) for k in ("ltp", "bid", "ask", "oi", "received_ts")})
            if row.get("received_ts"):
                q["rx"] = now
                self.last_quote_mono = now

    def quote(self, iid):
        return self.quotes.get(iid) if iid is not None else None

    def set_targets(self, targets: dict):
        """targets: {(symbol, expiry): set(strikes)}"""
        for key, strikes in targets.items():
            conn = self.conns.get(key)
            if conn is None:
                conn = self.conns[key] = Connection(self, *key)
            conn.set_target(strikes, key in self.master.has_future)
        for key in list(self.conns):
            if key not in targets:
                asyncio.create_task(self.conns.pop(key).close())

    def state(self):
        age = None if self.last_quote_mono is None else round(time.monotonic() - self.last_quote_mono, 1)
        return {"url": self.url, "master_instruments": len(self.master.by_id),
                "master_loaded_at": self.master.loaded_at, "master_error": self.master_error,
                "last_quote_age_s": age,
                "connections": [c.state() for c in sorted(self.conns.values(), key=lambda c: (c.symbol, c.expiry))]}
