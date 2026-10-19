#!/usr/bin/env python3
"""Receipt-time, all-expiry GammaShort simulator; never sends orders."""
import argparse
import asyncio
from datetime import datetime, date, time as daytime, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import websockets
from ke2_feed_server import Master
from gamma_weekly_engine import ROOT, LANES, load_engine, ReferenceData, evaluate

IST = ZoneInfo("Asia/Kolkata")


def configured_environment(root, environ):
    result = dict(environ)
    local = Path(root) / "data/gamma_inputs"
    for key, name in (("GS_DAILY_CSV", "GammaShortDailydata.csv"), ("GS_EVENT_PATH", "EventDates.xlsx")):
        if not result.get(key) and (local/name).is_file():
            result[key] = str(local/name)
    if not result.get("GS_LUT_DIR") and (Path(root)/"live_lut/final_r46V2").is_dir():
        result["GS_LUT_DIR"] = str(Path(root)/"live_lut/final_r46V2")
    return result


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return clean(value.item())
    return value


def eligible_expiries(master, today, horizon=42, explicit=None):
    listed = sorted({i.expiry for i in master.by_id.values()
                     if i.exchange == "NSE" and i.symbol == "NIFTY" and i.option_type})
    if explicit:
        requested = set(explicit)
        missing = requested - set(listed)
        if missing:
            raise ValueError("Expiries absent from master: " + ",".join(sorted(missing)))
        return [e for e in listed if e in requested and date.fromisoformat(e) >= today]
    return [e for e in listed if 0 <= (date.fromisoformat(e) - today).days <= horizon]


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        with self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("""CREATE TABLE IF NOT EXISTS observations
                (day TEXT, expiry TEXT, second TEXT, strike REAL, side TEXT, payload TEXT,
                 PRIMARY KEY(day,expiry,second,strike,side))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS lane_results
                (day TEXT,expiry TEXT,lane TEXT,fingerprint TEXT,payload TEXT,
                 PRIMARY KEY(day,expiry,lane,fingerprint))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS now_requests
                (day TEXT,expiry TEXT,hhmm INTEGER, PRIMARY KEY(day,expiry))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS settings
                (key TEXT PRIMARY KEY,value TEXT)""")

    def bind_model(self, fingerprint, day=None):
        with self.lock, self.db:
            key = "model:"+day if day else "model"
            row = self.db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
            if row and row[0] != fingerprint:
                raise ValueError("Model/reference configuration changed. Use a new GS_SIM_DB; existing results are retained.")
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key,fingerprint))

    def append(self, day, expiry, rows):
        with self.lock, self.db:
            # A resample within the same second replaces the complete row set.
            # Otherwise a moved ATM window could leave rows with an old U/book.
            self.db.executemany("DELETE FROM observations WHERE day=? AND expiry=? AND second=?",
                [(day, expiry, second) for second in {r["Time_dt"] for r in rows}])
            self.db.executemany("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?)",
                [(day, expiry, r["Time_dt"], r["Strike"], r["Type"], json.dumps(r)) for r in rows])

    def frame(self, engine, day, expiry):
        with self.lock:
            rows = self.db.execute("SELECT payload FROM observations WHERE day=? AND expiry=? ORDER BY second,strike,side",
                                   (day, expiry)).fetchall()
        return engine.pd.DataFrame([json.loads(r[0]) for r in rows])

    def now(self, day, expiry, value=None):
        with self.lock, self.db:
            if value is not None:
                self.db.execute("INSERT OR IGNORE INTO now_requests VALUES(?,?,?)", (day, expiry, value))
            row = self.db.execute("SELECT hhmm FROM now_requests WHERE day=? AND expiry=?", (day, expiry)).fetchone()
        return row[0] if row else None

    def save(self, day, expiry, fingerprint, lanes):
        with self.lock, self.db:
            for lane, result in lanes.items():
                self.db.execute("INSERT OR REPLACE INTO lane_results VALUES(?,?,?,?,?)",
                    (day, expiry, lane, fingerprint, json.dumps(clean(result), allow_nan=False)))

    def restored(self, day, expiry, fingerprint):
        with self.lock:
            rows = self.db.execute("SELECT lane,payload FROM lane_results WHERE day=? AND expiry=? AND fingerprint=?",
                                   (day, expiry, fingerprint)).fetchall()
        return {lane: json.loads(payload) for lane, payload in rows}


class Simulator:
    def __init__(self, root=ROOT, environ=None):
        self.env = configured_environment(root, os.environ if environ is None else environ)
        self.engine = load_engine(Path(root) / "GammaShort_Backtest_Final.py")
        self.references = ReferenceData(self.engine, self.env)
        self.master = Master(root)
        self.horizon = int(self.env.get("GS_WEEKLY_HORIZON_DAYS", "42"))
        if not 1 <= self.horizon <= 366:
            raise ValueError("GS_WEEKLY_HORIZON_DAYS must be in 1..366")
        self.explicit = [e.strip() for e in self.env.get("GS_WEEKLY_EXPIRIES", "").split(",") if e.strip()]
        self.store = Store(self.env.get("GS_SIM_DB", str(Path(root) / "data/gamma_weekly_simulation.db")))
        self.store.bind_model(self.references.fingerprint,datetime.now(IST).date().isoformat())
        self.quotes = {}
        self.results = {}
        self.last_sample = {}
        self.last_evaluation = None
        self.last_quote_received = {}
        self.connected = False
        self.connection_epoch = 0
        self.error = None
        self.lock = threading.RLock()
        self.source = self.env.get("GS_SOURCE_WS", "ws://127.0.0.1:9001/ke2-source")
        self.day = datetime.now(IST).date().isoformat()
        self.expiries = []
        self.refresh_day(datetime.now(IST))

    def refresh_day(self, stamp):
        day = stamp.date().isoformat()
        if self.expiries and self.day == day:
            return
        self.store.bind_model(self.references.fingerprint,day)
        self.day = day
        self.expiries = eligible_expiries(self.master, stamp.date(), self.horizon, self.explicit)
        with self.lock:
            self.results = {}
            for expiry in self.expiries:
                restored = self.store.restored(day, expiry, self.references.fingerprint)
                blocked = self.references.blockers(day, expiry)
                initial = {lane: {"status": "WAITING_INPUTS" if blocked else
                    ("READY" if lane == "NOW" else "WAITING_QUOTES"), "signal": None,
                    "scheduled_hhmm": None if lane == "NOW" else lane,
                    "blockers": blocked} for lane in LANES}
                initial.update(restored)
                self.results[expiry] = {"status": "RESTORED" if restored else
                    ("WAITING_INPUTS" if blocked else "WAITING_QUOTES"), "lanes": initial}
        self.quotes.clear()
        self.last_sample.clear()
        self.last_quote_received.clear()

    def ingest(self, event, received=None):
        if event.get("type") != "market_quote" or event.get("exchange") != "NSE":
            return
        instrument = self.master.by_key.get(("NSE", event.get("token")))
        if instrument is None or instrument.symbol != "NIFTY" or not instrument.option_type:
            return
        values = [event.get(k) for k in ("ltp", "bid", "ask")]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in values):
            return
        if values[1] > values[2]:
            return
        now = received or datetime.now(IST)
        self.refresh_day(now)
        if instrument.expiry not in self.expiries:
            return
        self.last_quote_received[instrument.expiry] = now.timestamp()
        self.quotes[instrument.expiry, instrument.strike, instrument.option_type] = {
            "ltp": values[0], "bid": values[1], "ask": values[2], "stamp": now.timestamp()}

    def sample(self, stamp):
        self.refresh_day(stamp)
        if not daytime(9, 15) <= stamp.replace(microsecond=0).time().replace(tzinfo=None) <= daytime(15, 37):
            return
        second = stamp.replace(microsecond=0).replace(tzinfo=None).isoformat(sep=" ")
        for expiry in self.expiries:
            pairs = {}
            for (ex, strike, side), quote in list(self.quotes.items()):
                if ex == expiry and 0 <= stamp.timestamp() - quote["stamp"] <= 10:
                    pairs.setdefault(strike, {})[side] = quote
            pairs = {k: v for k, v in pairs.items() if "CE" in v and "PE" in v}
            if not pairs:
                continue
            anchor = min(pairs, key=lambda k: abs(pairs[k]["CE"]["ltp"] - pairs[k]["PE"]["ltp"]))
            pair = pairs[anchor]
            newest = max(q["stamp"] for p in pairs.values() for q in p.values())
            if newest <= self.last_sample.get(expiry, 0):
                continue  # no observed update: do not manufacture an underlying tick
            self.last_sample[expiry] = newest
            u = anchor + pair["CE"]["ltp"] - pair["PE"]["ltp"]
            ub = anchor + pair["CE"]["bid"] - pair["PE"]["ask"]
            ua = anchor + pair["CE"]["ask"] - pair["PE"]["bid"]
            if not 0 < ub <= ua or u <= 0:
                continue
            atm = round(u / 50) * 50
            # Keep a small live window and every already-held strike.
            with self.lock:
                held = {v.get("strike") for v in self.results.get(expiry, {}).get("lanes", {}).values()}
            selected = {k for k in pairs if abs(k - atm) <= 150 or k in held}
            if atm not in pairs:
                continue
            expiry_close = datetime.combine(date.fromisoformat(expiry), daytime(15, 30), IST)
            dte = (expiry_close - stamp).total_seconds() / 86400
            if dte <= 0:
                continue
            rows = []
            for strike in sorted(selected):
                for side, quote in pairs[strike].items():
                    rows.append(dict(Time_dt=second, Strike=strike, Type=side,
                        LTP=quote["ltp"], Bid=quote["bid"], Ask=quote["ask"], DTE=dte,
                        Expiry=expiry, UnderlyingLTP=u, UnderlyingBid=ub, UnderlyingAsk=ua))
            self.store.append(self.day, expiry, rows)

    def calculate(self):
        day = self.day
        for expiry in list(self.expiries):
            blockers = self.references.blockers(day, expiry)
            if blockers:
                result = {"status": "WAITING_INPUTS", "blockers": blockers,
                          "lanes": {lane: {"status": "WAITING_INPUTS", "blockers": blockers} for lane in LANES}}
            else:
                frame = self.store.frame(self.engine, day, expiry)
                if frame.empty:
                    result = {"status": "WAITING_QUOTES",
                              "lanes": {lane: {"status": "WAITING_QUOTES"} for lane in LANES}}
                else:
                    result = evaluate(self.engine, self.references, frame, day, expiry,
                                      self.store.now(day, expiry))
            with self.lock:
                if day != self.day:
                    return
                # Closed results remain immutable within the model/reference version.
                previous = self.results.get(expiry, {}).get("lanes", {})
                for lane, old in previous.items():
                    if old.get("status") == "CLOSED":
                        result["lanes"][lane] = old
                self.results[expiry] = result
            self.store.save(day, expiry, self.references.fingerprint, result["lanes"])
        self.last_evaluation = datetime.now(IST).isoformat()

    def state(self):
        stamp = datetime.now(IST).timestamp()
        with self.lock:
            freshness = {}
            for expiry in self.expiries:
                received = self.last_quote_received.get(expiry)
                age = max(0., stamp-received) if received is not None else None
                freshness[expiry] = {
                    "status": "WAITING_QUOTES" if age is None else
                              ("FRESH" if self.connected and age <= 10 else "STALE"),
                    "quote_age_seconds": None if age is None else round(age,3),
                    "last_quote_received": None if received is None else
                                           datetime.fromtimestamp(received,IST).isoformat()}
            return clean(dict(
                service="gamma_weekly_simulator", trade_date=self.day,
                source_connected=self.connected, source=self.source,
                expiries=self.expiries, results=self.results, errors=self.references.errors,
                last_error=self.error, last_evaluation=self.last_evaluation,
                feed_freshness=freshness,
                daily_reference_availability={e: (self.day,e) in self.references.inputs for e in self.expiries},
                lut_available=all(table is not None for table in self.references.tables),
                event_calendar_available=self.references.events is not None,
                model_sha256=self.engine.source_sha256,
                reference_fingerprint=self.references.fingerprint,
                expiry_classification="explicit_allowlist" if self.explicit else "listed_NIFTY_options_within_horizon",
                horizon_days=self.horizon,
                basis={"clock": "local_receipt_time", "iv": "quote_observation_OTM",
                       "underlying": "same_expiry_call_put_parity",
                       "dte": "calendar_days_to_expiry_15:30_IST",
                       "option_fill": "30_observed_rows_forward_average",
                       "hedge_fill": "3_observed_rows_forward_average",
                       "backtest_tick_parity_verified": False,
                       "pnl": "normalized_to_25000", "filters_gate_entry": False}))

    async def source_loop(self):
        while True:
            try:
                async with websockets.connect(self.source, max_size=2**20) as socket:
                    self.connected = True
                    self.connection_epoch += 1
                    self.quotes.clear()
                    self.last_sample.clear()
                    self.error = None
                    async for message in socket:
                        value = json.loads(message)
                        if isinstance(value, dict):
                            self.ingest(value)
            except (OSError, ValueError, websockets.exceptions.WebSocketException) as exc:
                self.error = str(exc)
            finally:
                self.connected = False
                self.quotes.clear()
            await asyncio.sleep(2)

    async def clock_loop(self):
        while True:
            self.sample(datetime.now(IST))
            await asyncio.sleep(.2)

    async def evaluation_loop(self):
        last_minute = None
        while True:
            stamp = datetime.now(IST)
            minute = stamp.strftime("%Y%m%d%H%M")
            if minute != last_minute and stamp.second >= 35:
                try:
                    await asyncio.to_thread(self.calculate)
                    last_minute = minute
                except Exception as exc:
                    self.error = type(exc).__name__ + ": " + str(exc)
            await asyncio.sleep(.5)


PAGE = """<!doctype html><meta charset="utf-8"><title>GammaShort weekly lanes</title>
<style>body{font:16px system-ui;background:#10202c;color:#ddd;margin:30px}button,select{padding:10px;margin:8px}
pre{white-space:pre-wrap}.lane{background:#203746;padding:15px;margin:8px;display:inline-block;vertical-align:top;width:270px}
small{color:#aac}</style><h1>GammaShort: independent expiry lanes</h1>
<p>Local receipt-time simulation. No orders. PnL normalized to 25,000. Live tick parity is unverified.</p>
<select id="expiry"></select><button id="now">Schedule NOW at next minute</button><pre id="health"></pre><div id="lanes"></div>
<script>
const ex=document.querySelector('#expiry');
async function refresh(){const s=await(await fetch('/api/state')).json();const selected=ex.value;
ex.replaceChildren(...s.expiries.map(e=>new Option(e,e)));if(s.expiries.includes(selected))ex.value=selected;
document.querySelector('#health').textContent=JSON.stringify({connected:s.source_connected,errors:s.errors,last_error:s.last_error,last_evaluation:s.last_evaluation,freshness:s.feed_freshness},null,2);
const root=document.querySelector('#lanes');root.replaceChildren();
for(const [id,lane] of Object.entries(s.results[ex.value]?.lanes||{})){
const card=document.createElement('div');card.className='lane';const heading=document.createElement('h2');heading.textContent=id;
const content=document.createElement('pre');content.textContent=JSON.stringify(lane,null,2);card.append(heading,content);root.append(card);}
}
document.querySelector('#now').onclick=async()=>{const r=await fetch('/api/now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expiry:ex.value})});alert(JSON.stringify(await r.json()));refresh();};
ex.onchange=refresh;refresh();setInterval(refresh,3000);
</script>"""


def make_handler(simulator):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            encoded = json.dumps(clean(value), allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                data = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path in ("/api/state", "/api/health", "/api/runs"):
                self.reply(200, simulator.state())
            elif path == "/api/expiries":
                self.reply(200, {"expiries": simulator.expiries,
                                "classification": simulator.state()["expiry_classification"]})
            else:
                self.reply(404, {"error": "unknown_endpoint"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/now":
                self.reply(404, {"error": "unknown_endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("JSON body required; maximum 4096 bytes")
                data = json.loads(self.rfile.read(length))
                expiry = data.get("expiry")
                if expiry not in simulator.expiries:
                    raise ValueError("Select a listed eligible expiry")
                stamp = datetime.now(IST)
                target = (stamp + timedelta(minutes=1)).replace(second=0, microsecond=0)
                if target.date() != stamp.date() or not daytime(9, 16) <= target.time().replace(tzinfo=None) <= daytime(15, 35):
                    raise ValueError("NOW can be scheduled only during the configured session")
                blockers = simulator.references.blockers(stamp.date().isoformat(), expiry)
                if blockers:
                    self.reply(409, {"error": "missing_inputs", "blockers": blockers})
                    return
                hhmm = target.hour * 100 + target.minute
                saved = simulator.store.now(stamp.date().isoformat(), expiry, hhmm)
                self.reply(200, {"expiry": expiry, "lane": "NOW",
                                "entry_hhmm": saved, "one_request_per_expiry_per_day": True})
            except (ValueError, TypeError, AttributeError) as exc:
                self.reply(400, {"error": str(exc)})

        def log_message(self, *_):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    simulator = Simulator()
    if args.check:
        simulator.calculate()
        print(json.dumps(simulator.state(), indent=2))
        return
    host = os.environ.get("MINUTE_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("MINUTE_HTTP_PORT", "8000"))
    http = ThreadingHTTPServer((host, port), make_handler(simulator))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    async def run():
        async with asyncio.TaskGroup() as group:
            group.create_task(simulator.source_loop())
            group.create_task(simulator.clock_loop())
            group.create_task(simulator.evaluation_loop())
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        http.shutdown()
        http.server_close()
        simulator.store.db.close()


if __name__ == "__main__":
    main()
