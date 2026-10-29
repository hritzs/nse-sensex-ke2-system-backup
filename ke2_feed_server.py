#!/usr/bin/env python3
"""Feed-only KE2 WebSocket adapter. Never imports simulator or trading code."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

import websockets
from websockets.asyncio.server import serve

IST = timezone(timedelta(hours=5, minutes=30))
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
                 "SENSEX", "BANKEX", "SENSEX50"}
QUOTE_FIELDS = ("ltp", "bid", "ask", "oi", "volume", "exchange_ts", "last_trade_ts",
                "ltq", "bid_qty", "ask_qty", "received_ts")
CAPABILITIES = {
    "quotes": True, "subscriptions": True, "snapshots": True,
    "instrument_master": True, "heartbeat": True,
    "bar_1m": False, "eod": False, "reference_daily": False,
    "exchange_ts": False, "last_trade_ts": False, "volume": True,
    "volume_exchanges": ["BSE"], "underlying_indices": True,
    "complex_instruments": False,
    "lot_size": False, "tick_size": False, "bse_oi": False,
    "reason": "Only fields decoded by this repository are supplied; no external sources.",
}


def iso_ms(milliseconds=None):
    if milliseconds is None:
        milliseconds = time.time() * 1000
    return datetime.fromtimestamp(milliseconds / 1000, IST).isoformat(timespec="milliseconds")


def expiry_iso(value):
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.upper(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Invalid expiry: " + value)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Instrument:
    instrument_id: int
    exchange: str
    token: int
    symbol: str
    expiry: str
    strike: float | None
    option_type: str | None
    instrument_type: str

    def metadata(self):
        return {
            "instrument_id": self.instrument_id, "exchange": self.exchange,
            "symbol": self.symbol, "instrument_type": self.instrument_type,
            "expiry": self.expiry, "strike": self.strike, "option_type": self.option_type,
            "lot_size": None, "tick_size": None,
            "unavailable_fields": ["lot_size", "tick_size"],
        }


class Master:
    def __init__(self, root):
        self.by_id = {}
        self.by_key = {}
        self.source_files = []
        for exchange, relative in (("NSE", "shared/GreekTokens.csv"),
                                   ("BSE", "shared/BSEIndexTokens.csv")):
            path = Path(root) / relative
            self.source_files.append({"exchange": exchange, "path": relative,
                                      "modified_ts": iso_ms(path.stat().st_mtime * 1000)})
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    typ = row.get("OpType", "").strip().upper()
                    if not typ and row.get("Type", "").strip().upper() in ("FUTURE", "FUTIDX", "FUTSTK"):
                        typ = "FUT"
                    if typ not in ("CE", "PE", "XX", "FUT"):
                        continue
                    try:
                        # Matches ContractLoader's present BSE identity assumption.
                        token = int(row["Token"] if exchange == "NSE" else row["xx.Token"])
                        symbol = row["Symbol"].strip().upper()
                        expiry = expiry_iso(row["Expiry"].strip())
                        strike = float(row["Strike"]) if typ in ("CE", "PE") else None
                        if token <= 0 or (strike is not None and (not math.isfinite(strike) or strike < 0)):
                            raise ValueError("Invalid token or strike")
                    except (ValueError, KeyError):
                        continue
                    is_option = typ in ("CE", "PE")
                    inst_type = ("OPT" if is_option else "FUT") + ("IDX" if symbol in INDEX_SYMBOLS else "STK")
                    instrument = Instrument(token, exchange, token, symbol, expiry, strike,
                                            typ if is_option else None, inst_type)
                    previous = self.by_id.get(token)
                    if previous is not None and previous != instrument:
                        raise ValueError(f"Ambiguous instrument_id {token}: {previous.exchange}/{exchange}")
                    self.by_id[token] = instrument
                    self.by_key[(exchange, token)] = instrument
        self.groups = {}
        for instrument in self.by_id.values():
            self.groups.setdefault((instrument.symbol, instrument.expiry), []).append(instrument)

    def metadata(self, expiry=None):
        return [i.metadata() for i in self.by_id.values() if expiry is None or i.expiry == expiry]


@dataclass
class Subscription:
    rules: list = field(default_factory=list)
    instruments: set = field(default_factory=set)
    excluded: set = field(default_factory=set)
    dirty: set = field(default_factory=set)
    pending: list = field(default_factory=list)
    last_seen: float = field(default_factory=time.monotonic)


class Feed:
    def __init__(self, master, stale_seconds=10):
        self.master = master
        self.quotes = {}
        self.indices = {}
        self.sessions = {}
        self.source_connected = False
        self.last_by_exchange = {}
        self.stale_seconds = stale_seconds

    def quote(self, instrument_id):
        return {"instrument_id": instrument_id,
                **{key: self.quotes.get(instrument_id, {}).get(key) for key in QUOTE_FIELDS}}

    def snapshot(self, subscription):
        return {"type": "quote", "snapshot": True, "exchange_ts": None,
                "ts": iso_ms(), "data": [self.quote(i) for i in sorted(subscription.instruments)],
                "unavailable_fields": ["exchange_ts", "last_trade_ts"] +
                    (["volume"] if all(self.master.by_id[i].exchange != "BSE"
                                      for i in subscription.instruments) else [])}

    def resolve(self, subscription):
        selected = set()
        pending = []
        for rule in subscription.rules:
            expiry = rule["expiry"]
            symbol = rule["symbol"]
            group = self.master.groups[(symbol, expiry)]
            futures = [i for i in group if i.option_type is None]
            if rule["future"]:
                selected.update(i.instrument_id for i in futures)
            options = rule["options"]
            if not futures and (rule["future"] or (options and options["mode"] == "around_future")):
                pending.append({"symbol": symbol, "expiry": expiry, "reason": "future_contract_unavailable"})
            types = options["types"] if options else []
            strikes = {i.strike for i in group if i.option_type in types}
            chosen = set(rule["extra_strikes"])
            if options and options["mode"] == "explicit_strikes":
                chosen.update(options["strikes"])
            if options and options["mode"] == "around_future":
                # Center only on an observed exact-expiry future; do not invent one.
                anchor = next((self.quotes.get(i.instrument_id, {}).get("ltp") for i in futures
                               if finite_number(self.quotes.get(i.instrument_id, {}).get("ltp"))
                               and self.quotes[i.instrument_id]["ltp"] > 0), None)
                if anchor is None and futures:
                    pending.append({"symbol": symbol, "expiry": expiry, "reason": "future_quote_unavailable"})
                elif anchor is not None:
                    grid = sorted(strikes)
                    if options.get("step"):
                        step = options["step"]
                        grid = [k for k in grid if abs(k / step - round(k / step)) < 1e-7]
                    if grid:
                        center = min(range(len(grid)), key=lambda n: (abs(grid[n] - anchor), grid[n]))
                        count = options["strikes_each_side"]
                        chosen.update(grid[max(0, center-count):center+count+1])
            selected.update(i.instrument_id for i in group if i.option_type in types and i.strike in chosen)
        subscription.instruments = selected - subscription.excluded
        subscription.pending = pending

    def subscribe(self, subscription, request):
        expiry = expiry_iso(request.get("expiry", ""))
        underlyings = request.get("underlyings")
        if not isinstance(underlyings, list) or not underlyings or len(underlyings) > 100:
            raise ValueError("underlyings must contain 1..100 entries")
        rules = []
        for value in underlyings:
            if not isinstance(value, dict):
                raise ValueError("underlying must be an object")
            symbol = str(value.get("symbol", "")).upper()
            group = self.master.groups.get((symbol, expiry))
            if not group:
                raise ValueError(f"No listed contracts for {symbol}/{expiry}; refresh the local master")
            segment = value.get("segment")
            expected = "FO_IDX" if symbol in INDEX_SYMBOLS else "FO_STK"
            if segment != expected:
                raise ValueError(f"Expected segment {expected} for {symbol}")
            options = value.get("options")
            if options is not None:
                if not isinstance(options, dict) or options.get("mode") not in ("around_future","explicit_strikes"):
                    raise ValueError("options.mode must be around_future or explicit_strikes")
                count = options.get("strikes_each_side")
                types = options.get("types")
                if options["mode"] == "around_future" and (type(count) is not int or not 0 <= count <= 1000):
                    raise ValueError("strikes_each_side must be 0..1000")
                if options["mode"] == "explicit_strikes":
                    requested = options.get("strikes")
                    if not isinstance(requested,list) or not requested or len(requested)>2000 or any(
                            not finite_number(k) or k<0 for k in requested):
                        raise ValueError("explicit_strikes requires 1..2000 listed strikes")
                if not isinstance(types, list) or not types or any(t not in ("CE", "PE") for t in types):
                    raise ValueError("types must contain CE and/or PE")
                if "step" in options and (not finite_number(options["step"]) or options["step"] <= 0):
                    raise ValueError("step must be positive")
            extra = value.get("extra_strikes", [])
            if not isinstance(extra, list) or any(not finite_number(k) or k < 0 for k in extra):
                raise ValueError("extra_strikes must be nonnegative numbers")
            if extra and options is None:
                raise ValueError("extra_strikes requires options.types")
            if type(value.get("future", False)) is not bool:
                raise ValueError("future must be boolean")
            listed = {i.strike for i in group if i.option_type in (options["types"] if options else [])}
            if options and options["mode"] == "explicit_strikes" and any(
                    k not in listed for k in options["strikes"]):
                raise ValueError("Explicit strike is not listed for this expiry")
            if any(k not in listed for k in extra):
                raise ValueError(f"extra_strikes contains unlisted contracts for {symbol}/{expiry}")
            rules.append({"expiry": expiry, "symbol": symbol, "future": value.get("future", False),
                          "options": options, "extra_strikes": extra})
        # One request replaces this connection's cycle; other consumers are independent.
        subscription.rules = rules
        subscription.excluded.clear()
        self.resolve(subscription)

    def ingest(self, event):
        if event.get("type") == "market_index":
            symbol = event.get("symbol")
            value, stamp = event.get("ltp"), event.get("received_ms")
            if (event.get("exchange") == "BSE" and isinstance(symbol, str)
                    and symbol and finite_number(value) and value > 0
                    and finite_number(stamp) and type(event.get("index_code")) is int):
                self.indices[symbol] = {"exchange": "BSE", "symbol": symbol,
                    "index_code": event["index_code"], "ltp": value,
                    "received_ts": iso_ms(stamp), "exchange_ts": None}
            return
        if event.get("type") not in ("market_quote", "market_oi"):
            return
        instrument = self.master.by_key.get((event.get("exchange"), event.get("token")))
        if instrument is None:
            return
        received = event.get("received_ms")
        if not finite_number(received):
            return
        iid = instrument.instrument_id
        quote = self.quotes.setdefault(iid, {})
        if event["type"] == "market_quote":
            for key in ("ltp", "bid", "ask", "ltq", "bid_qty", "ask_qty", "volume"):
                value = event.get(key)
                if value is not None and (not finite_number(value) or value < 0):
                    return
            quote.update({k: event.get(k) for k in ("ltp", "bid", "ask", "ltq", "bid_qty", "ask_qty", "volume")})
            quote["received_ts"] = iso_ms(received)
            quote["_quote_rx"] = time.monotonic()
        elif type(event.get("oi")) is int and event["oi"] >= 0:
            quote["oi"] = event["oi"]
        else:
            return
        self.last_by_exchange[instrument.exchange] = time.monotonic()
        for sub in self.sessions.values():
            # Only this rule's exact-expiry future can move its strike window.
            # Copying a large subscription on every unrelated option tick can
            # block the socket reader long enough to hit upstream backpressure.
            if (event["type"] == "market_quote" and instrument.option_type is None
                    and any(rule["symbol"] == instrument.symbol
                            and rule["expiry"] == instrument.expiry
                            for rule in sub.rules)):
                before = sub.instruments
                self.resolve(sub)
                sub.dirty.update(sub.instruments - before)
            if iid in sub.instruments:
                sub.dirty.add(iid)

    def heartbeat(self, sub):
        exchanges = {self.master.by_id[i].exchange for i in sub.instruments}
        now = time.monotonic()
        status = {}
        for exchange in exchanges:
            age = now - self.last_by_exchange.get(exchange, float("-inf"))
            status[exchange] = "OK" if self.source_connected and age <= self.stale_seconds else "STALE"
        healthy = bool(sub.instruments) and not sub.pending and self.source_connected and all(
            now - self.quotes.get(i, {}).get("_quote_rx", float("-inf")) <= self.stale_seconds
            for i in sub.instruments)
        return {"type": "heartbeat", "ts": iso_ms(), "feed_status": "OK" if healthy else "DEGRADED",
                "source_connected": self.source_connected, "exchanges": status,
                "pending": sub.pending}

    async def source(self, uri):
        while True:
            connected_at = time.monotonic()
            received_messages = 0
            try:
                async with websockets.connect(uri, max_size=4 * 1024 * 1024) as ws:
                    self.quotes.clear()
                    self.indices.clear()
                    self.last_by_exchange.clear()
                    self.source_connected = True
                    for sub in self.sessions.values():
                        self.resolve(sub)
                        sub.dirty.update(sub.instruments)
                    async for message in ws:
                        received_messages += 1
                        try:
                            event = json.loads(message)
                            if isinstance(event, dict):
                                self.ingest(event)
                        except (ValueError, TypeError, OverflowError):
                            logging.warning("Rejected invalid source event")
            except (OSError, websockets.ConnectionClosed) as exc:
                logging.warning(
                    "Source disconnected after %.1fs / %d messages: %s; received_close=%s sent_close=%s",
                    time.monotonic() - connected_at, received_messages, exc,
                    getattr(exc, "rcvd", None), getattr(exc, "sent", None))
            finally:
                self.source_connected = False
            await asyncio.sleep(2)

    async def handler(self, ws):
        session_id = uuid.uuid4().hex
        sub = Subscription(last_seen=float("inf"))
        self.sessions[session_id] = sub
        async def send(payload):
            await asyncio.wait_for(ws.send(json.dumps(payload, allow_nan=False)), timeout=5)
        async def updates():
            last_heartbeat = 0.0
            last_indices = {}
            while True:
                await asyncio.sleep(1)
                watched = {rule["symbol"] for rule in sub.rules}
                index_rows = [row for symbol, row in self.indices.items()
                              if symbol in watched and row != last_indices.get(symbol)]
                if index_rows:
                    await send({"type": "indices", "snapshot": False, "ts": iso_ms(),
                                "data": index_rows})
                    last_indices.update({row["symbol"]: row.copy() for row in index_rows})
                dirty = sorted(sub.dirty & sub.instruments)
                sub.dirty.clear()
                if dirty:
                    await send({"type": "quote", "snapshot": False, "exchange_ts": None,
                                "ts": iso_ms(), "data": [self.quote(i) for i in dirty]})
                if time.monotonic() - last_heartbeat >= 5:
                    await send(self.heartbeat(sub))
                    last_heartbeat = time.monotonic()
        sender = asyncio.create_task(updates())
        async def sender_done():
            try:
                await sender
            except Exception:
                await ws.close(code=1013, reason="Client cannot keep up")
        monitor = asyncio.create_task(sender_done())
        try:
            await send({"type": "capabilities", "session_id": session_id, "data": CAPABILITIES,
                        "master_sources": self.master.source_files})
            async for message in ws:
                request = {}
                try:
                    request = json.loads(message)
                    if not isinstance(request, dict):
                        raise ValueError("Request must be an object")
                    typ = request.get("type")
                    if typ == "subscribe":
                        self.subscribe(sub, request)
                        await send({"type": "subscribed", "request_id": request.get("request_id"),
                                    "session_id": session_id, "instrument_ids": sorted(sub.instruments),
                                    "pending": sub.pending})
                        await send(self.snapshot(sub))
                    elif typ == "resume":
                        old_id = request.get("session_id")
                        if not isinstance(old_id, str) or old_id not in self.sessions or old_id == session_id:
                            raise ValueError("Unknown session_id; resend subscribe")
                        old = self.sessions[old_id]
                        if old.last_seen == float("inf"):
                            raise ValueError("Session already active")
                        if time.monotonic() - old.last_seen > 3600:
                            raise ValueError("Session expired; resend subscribe")
                        del self.sessions[session_id]
                        session_id, sub = old_id, old
                        sub.last_seen = float("inf")
                        await send(self.snapshot(sub))
                    elif typ == "unsubscribe":
                        ids = request.get("instrument_ids")
                        if not isinstance(ids, list) or any(type(i) is not int for i in ids):
                            raise ValueError("instrument_ids must be integers")
                        sub.excluded.update(ids)
                        sub.instruments.difference_update(ids)
                        await send({"type": "unsubscribed", "request_id": request.get("request_id"),
                                    "instrument_ids": ids})
                    elif typ == "instrument_master":
                        exp = expiry_iso(request["expiry"]) if "expiry" in request else None
                        data = self.master.metadata(exp)
                        for offset in range(0, max(1, len(data)), 500):
                            await send({"type": "instrument_master", "request_id": request.get("request_id"),
                                        "data": data[offset:offset+500], "more": offset+500 < len(data)})
                    elif typ == "indices":
                        symbols = request.get("symbols")
                        if symbols is not None and (not isinstance(symbols, list)
                                or any(not isinstance(s, str) for s in symbols)):
                            raise ValueError("symbols must be a list of index symbols")
                        wanted = None if symbols is None else {s.upper() for s in symbols}
                        await send({"type": "indices", "snapshot": True,
                                    "request_id": request.get("request_id"), "ts": iso_ms(),
                                    "data": [row for symbol,row in sorted(self.indices.items())
                                             if wanted is None or symbol in wanted]})
                    elif typ == "snapshot":
                        await send(self.snapshot(sub))
                    else:
                        await send({"type": "error", "code": "unsupported",
                                    "request_id": request.get("request_id"), "requested_type": typ,
                                    "message": "Unavailable from the currently decoded feed",
                                    "capabilities": CAPABILITIES})
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    await send({"type": "error", "code": "invalid_request",
                                "request_id": request.get("request_id") if isinstance(request, dict) else None,
                                "message": str(exc)})
        except (websockets.ConnectionClosed, TimeoutError):
            pass
        finally:
            sender.cancel()
            monitor.cancel()
            await asyncio.gather(sender, monitor, return_exceptions=True)
            sub.last_seen = time.monotonic()

    async def cleanup(self):
        while True:
            await asyncio.sleep(60)
            cutoff = time.monotonic() - 3600
            for key, sub in list(self.sessions.items()):
                if sub.last_seen < cutoff:
                    del self.sessions[key]


async def run(args):
    feed = Feed(Master(args.root))
    async with serve(feed.handler, args.host, args.port, max_size=256 * 1024, max_queue=16):
        await asyncio.gather(feed.source(args.source), feed.cleanup())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--source", default="ws://127.0.0.1:9001/ke2-source")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9002)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(parser.parse_args()))
