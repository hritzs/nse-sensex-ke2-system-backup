#!/usr/bin/env python3
"""WhiteGamma live: the WhiteGammaTemp.xlsm risk book, fed directly by the live market feed (no Excel).

    python server.py --config config.json
    open http://127.0.0.1:8200/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

import box
import inputs
import span
from engine import IST, Engine
from feed_client import Feed

# When frozen by PyInstaller, code + bundled data live in _MEIPASS (a temp folder that is
# deleted on exit); writable files (marks, box, ui_state, span downloads, trade_check) and an
# optional override static/ folder live next to the .exe.
_FROZEN = getattr(sys, "frozen", False)
_MEIPASS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
HERE = _MEIPASS if _FROZEN else Path(__file__).resolve().parent
BASE = Path(sys.executable).resolve().parent if _FROZEN else Path(__file__).resolve().parent
# Prefer an external static/ folder next to the .exe (UI updates without a rebuild)
STATIC = (BASE / "static") if (BASE / "static" / "index.html").is_file() else (HERE / "static")
log = logging.getLogger("whitegamma")


def _clean(o):
    """JSON-safe: NaN/inf -> None, rounded floats keep payloads small."""
    if isinstance(o, float):
        if not math.isfinite(o):
            return None
        return round(o, 6)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _code_mtimes():
    here = os.path.dirname(os.path.abspath(__file__))
    out = {}
    for name in os.listdir(here):
        if name.endswith(".py"):
            try:
                out[name] = os.path.getmtime(os.path.join(here, name))
            except OSError:
                pass
    return out


class App:
    def __init__(self, cfg):
        self.code_mtimes = _code_mtimes()   # to tell the page when the .py files were updated after start
        self.cfg = cfg
        self.symbols = set(cfg["indices"])
        self.feed = Feed(cfg["feed_url"], self.symbols)
        self.engine = Engine(cfg, self.feed)
        self.files: dict[str, inputs.WatchedFile] = {}
        self.snapshot_text = "{}"
        self.clients: set[WebSocket] = set()
        self.tasks = []
        self.last_poll = 0.0
        self.last_master_refresh = 0.0
        self.last_span = -1e9
        self.engine.span_tiers = (self.cfg.get("span") or {}).get("tiers") or {}
        self.engine.span_floor = (self.cfg.get("span") or {}).get("floor_span", True)
        self._span_task = None
        self._span_loaded = {}
        self._master_task = None
        self.marks_path = Path(cfg.get("marks_file") or (BASE / "marks.json"))
        self.load_marks()
        self.box_path = Path(cfg.get("box_file") or (BASE / "box.json"))   # Borrow / Lend tab settings
        self.ui_state_path = Path(cfg.get("ui_state_file") or (BASE / "ui_state.json"))   # Save button layout
        self.ui_state: dict = {}
        self.load_ui_state()
        self.load_box()

    # ---------------------------------------------------------------- dates
    def dates(self):
        today = date.fromisoformat(self.cfg["today"]) if self.cfg.get("today") else datetime.now(IST).date()
        prev = None
        if self.cfg.get("prev_date"):
            prev = date.fromisoformat(self.cfg["prev_date"])
        elif self.cfg.get("prev_date_file"):
            prev = inputs.read_prev_date_file(self.cfg["prev_date_file"])
        if prev is None:
            prev = inputs.prev_trading_day(today, set(self.cfg.get("holidays", [])))
        return today, prev

    # ---------------------------------------------------------------- input files
    def _watch(self, name, template, today, prev, allow_fallback=True):
        wf = self.files.get(name)
        if wf is None or wf.template != template:
            wf = self.files[name] = inputs.WatchedFile(name, template)
        path, expected = inputs.resolve_path(template, today, prev, allow_fallback)
        mtime = os.path.getmtime(path) if path else None
        changed = (path != wf.path) or (mtime != wf.mtime)
        wf.path, wf.mtime, wf.expected = path, mtime, expected
        wf.error = None if path else "file not found"
        if not path:
            wf.rows = 0
        return wf, changed

    def _write_trade_check(self):
        """trade_check.txt next to server.py: what each trade file looked like to the reader (for troubleshooting)."""
        try:
            out = []
            for wf in self.files.values():
                if not wf.name.startswith("trades "):
                    continue
                c = (wf.extra or {}).get("check", {})
                out += [f"[{wf.name[7:]}] {wf.path or 'not found: ' + str(wf.expected)}",
                        f"  lines {c.get('lines', 0)}, index lines {c.get('index_lines', 0)}, used {wf.rows},"
                        f" buy {wf.extra.get('buy', 0):g}, sell {wf.extra.get('sell', 0):g}",
                        f"  skipped {c.get('skipped') or 'none'}",
                        f"  first line:    {c.get('first_line', '')}",
                        f"  first skipped: {c.get('first_skipped', '')}", ""]
            with open(BASE / "trade_check.txt", "w", encoding="utf-8") as fh:
                fh.write("\n".join(out))
        except Exception as exc:  # never let troubleshooting output stop the app
            log.warning("trade_check.txt not written: %s", exc)

    def poll_files(self, force=False):
        today, prev = self.dates()
        fcfg = self.cfg.get("files", {})
        now_s = lambda: datetime.now().isoformat(timespec="seconds")
        # Bhavcopy sheet: NSE + BSE close prices of the previous trading day
        bhav_changed, bhav_files = force, []
        for i, tpl in enumerate(fcfg.get("bhavcopy", [])):
            wf, ch = self._watch(f"bhavcopy {i + 1}", tpl, today, prev)
            bhav_changed |= ch
            bhav_files.append(wf)
        if bhav_changed:
            merged = {}
            for wf in bhav_files:
                part = inputs.load_bhavcopy([wf.path], self.symbols) if wf.path else {}
                wf.rows, wf.loaded_at = len(part), now_s()
                merged.update(part)
            self.engine.bhav = merged
        # GreekTokens sheet: the exchange's daily token lists (FB!B1 / C1 on 1.85), refreshed every morning
        tok = fcfg.get("tokens") or []
        tok = [tok] if isinstance(tok, str) else tok
        tok_changed, tok_files = force, []
        for i, tpl in enumerate(tok):
            wf, ch = self._watch(f"tokens {i + 1}", tpl, today, prev)
            tok_changed |= ch
            tok_files.append(wf)
        if tok_changed and tok:
            merged = {}
            for wf in tok_files:
                try:
                    part, rows, diag = inputs.load_tokens(wf.path, self.symbols) if wf.path else ({}, 0, {})
                    wf.rows, wf.loaded_at = rows, now_s()
                    if wf.path:
                        log.info("Tokens %s: %d lines, %d index contracts, skipped %s, first line: %s",
                                 wf.path, diag.get("lines", 0), rows, diag.get("skipped") or "none", diag.get("first_line", ""))
                    if wf.path and not rows:
                        wf.error = f"no index contracts read ({diag.get('skipped') or 'check the layout'})"
                    merged.update(part)
                except Exception as exc:
                    wf.error = f"{type(exc).__name__}: {exc}"
            self.engine.tokens = merged
            self.last_master_refresh = 0.0          # check the feed's list against the new files soon
        # PrevQty sheet: previous-day net positions
        tpl = fcfg.get("prev_positions")
        if tpl:
            wf, ch = self._watch("prev positions", tpl, today, prev)
            if ch or force:
                try:
                    self.engine.prev = inputs.load_prev_positions(wf.path, self.symbols)
                    wf.rows = sum(1 for v in self.engine.prev.values() if v.net_qty)
                    wf.loaded_at = now_s()
                except Exception as exc:  # keep the last good copy
                    wf.error = f"{type(exc).__name__}: {exc}"
        # HPTrades / HDTrades / TNTrades: today's trade files, never an older day's file
        trade_changed = force
        for t in fcfg.get("trades", []):
            wf, ch = self._watch(f"trades {t['name']}", t["path"], today, prev, allow_fallback=False)
            if not wf.path:
                wf.error = "today's file not found yet"
            trade_changed |= ch
        if trade_changed:
            parts = []
            for t in fcfg.get("trades", []):
                wf = self.files[f"trades {t['name']}"]
                try:
                    if t.get("format", "nse").lower() == "bse":
                        part, rows, diag = inputs.load_bse_trades(wf.path, self.symbols, t.get("columns") or fcfg.get("bse_trade_columns"), today)
                    else:
                        part, rows, diag = inputs.load_trades(wf.path, self.symbols, t.get("columns") or fcfg.get("trade_columns"), today)
                    wf.rows, wf.loaded_at = rows, now_s()
                    wf.extra = inputs.trade_summary(part, diag) if wf.path else {}
                    if wf.path:
                        log.info("Trades %s %s: %d lines, %d index lines, %d used, skipped %s",
                                 t["name"], wf.path, diag["lines"], diag["index_lines"], rows, diag["skipped"] or "none")
                        self._write_trade_check()
                    parts.append(part)
                except Exception as exc:
                    wf.error = f"{type(exc).__name__}: {exc}"
            self.engine.trades_raw = inputs.merge_trades(parts)

    # ---------------------------------------------------------------- loops
    async def run(self):
        self.poll_files(force=True)
        await self.feed.load_master()
        poll_every = self.cfg.get("file_poll_seconds", 3)
        loop = asyncio.get_running_loop()
        self.last_master_refresh = loop.time()
        while True:
            try:
                now = loop.time()
                if now - self.last_poll >= poll_every:
                    self.poll_files()
                    self.last_poll = now
                self._maybe_refresh_master(now)
                self._maybe_span(now)
                await self.tick()
            except Exception:
                log.exception("compute loop error")
            await asyncio.sleep(self.cfg.get("refresh_seconds", 1))

    def _maybe_refresh_master(self, now):
        """While the token files list contracts the feed does not have (e.g. an expiry listed today and the
        feed adapter not refreshed yet), fetch the feed's list again every minute; otherwise every 5 min."""
        if self._master_task and not self._master_task.done():
            return
        every = self.cfg.get("master_refresh_seconds", 300)
        if self.feed.master.by_id and self.engine.tokens:
            e, s = self.engine.token_gaps(self.dates()[0].isoformat())
            if e or s:
                every = self.cfg.get("master_refresh_gap_seconds", 60)
        if now - self.last_master_refresh >= every:
            self.last_master_refresh = now
            self._master_task = asyncio.create_task(self.feed.refresh_master())

    # ---------------------------------------------------------------- SPAN margin files
    SPAN_DEFAULTS = {
        "folder": "span", "download": True, "refresh_seconds": 300,
        # NSE SPAN risk parameter files: end of day (s) and the intraday ones, newest first
        "nse_urls": ["https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{d:%Y%m%d}.{sfx}.zip"],
        "nse_suffixes": ["s", "i5", "i4", "i3", "i2", "i1"],
        "bse_urls": [],
        # NSE contract-wise ELM file (exact exposure % per option); without it the 2 / 3 / 5 % tiers are used
        "ael_urls": ["https://nsearchives.nseindia.com/content/nsccl/F_AEL_OPT_CONTRACTS_{d:%d%m%Y}.csv",
                     "https://nsearchives.nseindia.com/archives/nsccl/ael/F_AEL_OPT_CONTRACTS_{d:%d%m%Y}.csv",
                     "https://nsearchives.nseindia.com/content/fo/F_AEL_OPT_CONTRACTS_{d:%d%m%Y}.csv"],
        "extra_folders": [],
    }

    def _maybe_span(self, now):
        sc = {**self.SPAN_DEFAULTS, **(self.cfg.get("span") or {})}
        if sc.get("enabled") is False or (self._span_task and not self._span_task.done()):
            return
        if now - self.last_span >= sc["refresh_seconds"]:
            self.last_span = now
            for label in ("SPAN NSE", "SPAN BSE", "exposure % (AEL)"):   # created here, not in the worker thread
                if label not in self.files:
                    self.files[label] = inputs.WatchedFile(label, sc["folder"])
                    self.files[label].error = "loading…"
            self._span_task = asyncio.create_task(asyncio.to_thread(self._span_cycle, sc))

    def _span_cycle(self, sc):
        """Download today's newest SPAN / AEL files (or the previous day's), then load any newer local file."""
        folder = sc["folder"] if os.path.isabs(sc["folder"]) else str(BASE / sc["folder"])
        os.makedirs(folder, exist_ok=True)
        today, prev = self.dates()
        report = [f"{datetime.now().isoformat(timespec='seconds')}  folder {folder}"]
        if sc.get("download", True):
            for label, urls, sfx in (("NSE SPAN", sc["nse_urls"], sc["nse_suffixes"]), ("BSE SPAN", sc["bse_urls"], sc.get("bse_suffixes")),
                                     ("AEL", sc["ael_urls"], None)):
                if not urls:
                    report.append(f"{label}: no download address set")
                    continue
                for d in (today, prev):
                    try:
                        got, url, tried = span.download(folder, urls, d, sfx)
                    except Exception as exc:
                        got, url, tried = None, None, [f"{type(exc).__name__}: {exc}"]
                    if got:
                        report.append(f"{label}: downloaded {url} -> {got}")
                    elif url:
                        report.append(f"{label}: have {url.rsplit('/', 1)[-1]} (newest available)")
                    report += [f"   {t}" for t in tried]
                    if got or url:
                        break
        found = {}
        for f in [folder] + list(sc.get("extra_folders") or []):
            for kind, path in span.newest_files(f).items():
                if kind not in found or os.path.getmtime(path) > os.path.getmtime(found[kind]):
                    found[kind] = path
        symbols = list(self.cfg["indices"])
        for kind, label in (("NSCCL", "SPAN NSE"), ("ICCL", "SPAN BSE"), ("AEL", "exposure % (AEL)")):
            wf = self.files[label]
            wf.template = os.path.join(folder, "nsccl.*.spn" if kind == "NSCCL" else "BSERISK*.spn" if kind == "ICCL" else "F_AEL_OPT_CONTRACTS_*.csv")
            path = found.get(kind)
            if not path:
                wf.path, wf.rows = None, 0
                wf.error = "not downloaded yet" if kind != "AEL" else "not found - 2 / 3 / 5 % tiers used"
                continue
            mtime = os.path.getmtime(path)
            if self._span_loaded.get(kind) == (path, mtime):
                continue
            try:
                if kind == "AEL":
                    self.engine.ael = span.load_ael(path, set(symbols))
                    wf.rows = len(self.engine.ael)
                else:
                    syms = [s for s in symbols if span.exchange_of(s) == kind]
                    rf = span.parse_risk_file(path, syms)
                    self.engine.span_rf[kind] = rf
                    wf.rows = len(rf.contracts)
                    report.append(f"{label}: read {path}: {len(rf.contracts)} contracts, cc {sorted(rf.cc)}, {rf.parse_s:.1f}s")
                    log.info("%s: %s, %d contracts (%s) in %.1fs", label, path, len(rf.contracts), sorted(rf.cc), rf.parse_s)
                wf.path, wf.mtime, wf.error = path, mtime, None
                wf.loaded_at = datetime.now().isoformat(timespec="seconds")
                self._span_loaded[kind] = (path, mtime)
            except Exception as exc:
                wf.error = f"{type(exc).__name__}: {exc}"
                report.append(f"{label}: {path}: {wf.error}")
                log.warning("%s %s: %s", label, path, wf.error)
        try:
            with open(os.path.join(folder, "span_status.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(report) + "\n")
        except OSError:
            pass

    async def tick(self):
        if not self.feed.master.by_id:
            return
        today, prev = self.dates()
        self.feed.set_targets(self.engine.subscription_targets(today.isoformat()))
        snap = self.engine.compute(prev_date=prev)
        snap["prev_date"] = prev.isoformat()
        snap["feed"] = self.feed.state()
        snap["files"] = [w.status() for w in self.files.values()]
        snap["restart_needed"] = _code_mtimes() != self.code_mtimes
        try:   # the page reloads itself when static/index.html is updated
            snap["page_version"] = os.path.getmtime(STATIC / "index.html")
        except OSError:
            pass
        snap["sum_marks"] = self.engine.sum_marks
        snap["master_check"] = self.engine.master_check(snap.get("today") or date.today().isoformat())
        snap["config"] = {"price_source": self.cfg.get("price_source", "ltp"),
                          "session": self.cfg.get("session", {})}
        self.snapshot_text = json.dumps(_clean(snap), separators=(",", ":"))
        await self.broadcast()

    # ---------------------------------------------------------------- user markings
    def load_marks(self):
        try:
            self.engine.marks = json.loads(self.marks_path.read_text())
        except (OSError, ValueError):
            self.engine.marks = {}
        self.engine.syn_marks = self.engine.marks.pop("_syn", {}) or {}
        self.engine.sum_marks = self.engine.marks.pop("_sum", {}) or {}

    def load_box(self):
        try:
            self.engine.box_settings = json.loads(self.box_path.read_text())
        except (OSError, ValueError):
            self.engine.box_settings = {}

    def save_box(self):
        try:
            self.box_path.write_text(json.dumps(self.engine.box_settings, indent=1))
        except OSError as exc:
            log.warning("could not save Borrow/Lend settings: %s", exc)

    def save_marks(self):
        try:
            data = dict(self.engine.marks)
            if self.engine.syn_marks:
                data["_syn"] = self.engine.syn_marks
            if self.engine.sum_marks:
                data["_sum"] = self.engine.sum_marks
            self.marks_path.write_text(json.dumps(data, indent=1))
        except OSError as exc:
            log.warning("could not save marks: %s", exc)

    def load_ui_state(self):
        try:
            self.ui_state = json.loads(self.ui_state_path.read_text())
            if not isinstance(self.ui_state, dict):
                self.ui_state = {}
        except (OSError, ValueError):
            self.ui_state = {}

    def save_ui_state(self, data: dict):
        try:
            self.ui_state = dict(data) if isinstance(data, dict) else {}
            self.ui_state_path.write_text(json.dumps(self.ui_state, indent=1))
        except OSError as exc:
            log.warning("could not save UI state: %s", exc)
            raise

    async def run_before_master(self):
        """Serve file status while the instrument master is still loading."""
        while not self.feed.master.by_id:
            today, prev = self.dates()
            self.snapshot_text = json.dumps(_clean({
                "ts": datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(),
                "prev_date": prev.isoformat(), "feed": self.feed.state(),
                "files": [w.status() for w in self.files.values()], "books": {}, "board": {}, "summary": {},
                "waiting": "Loading the instrument master..."}))
            await self.broadcast()
            await asyncio.sleep(1)

    async def broadcast(self):
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(self.snapshot_text), 2)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def build(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text())
    base = Path(cfg_path).resolve().parent
    rel = lambda p: p if (not p or os.path.isabs(p)) else str(base / p)
    files = cfg.setdefault("files", {})
    files["bhavcopy"] = [rel(p) for p in files.get("bhavcopy", [])]
    files["prev_positions"] = rel(files.get("prev_positions"))
    tok = files.get("tokens") or []
    files["tokens"] = [rel(p) for p in ([tok] if isinstance(tok, str) else tok)]
    for t in files.get("trades", []):
        t["path"] = rel(t["path"])
    cfg["prev_date_file"] = rel(cfg.get("prev_date_file"))
    state = App(cfg)

    @asynccontextmanager
    async def lifespan(app):
        state.tasks = [asyncio.create_task(state.run()), asyncio.create_task(state.run_before_master())]
        yield
        for t in state.tasks:
            t.cancel()
        await asyncio.gather(*state.tasks, return_exceptions=True)

    app = FastAPI(title="WhiteGamma live", lifespan=lifespan)

    @app.get("/")
    async def index():
        # never let the browser keep an old copy of the page after an update
        return FileResponse(STATIC / "index.html",
                            headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"})

    @app.get("/api/state")
    async def api_state():
        return JSONResponse(content=json.loads(state.snapshot_text))

    @app.post("/api/reload")
    async def api_reload():
        state.poll_files(force=True)
        return {"reloaded": [w.status() for w in state.files.values()]}

    @app.get("/api/ui_state")
    async def api_ui_state_get():
        """Layout prefs: open tab, column widths, checkboxes, zoom, minimise, etc."""
        return state.ui_state

    @app.post("/api/ui_state")
    async def api_ui_state_post(body: dict):
        """Save layout prefs next to the app (ui_state.json). Survives EXE restarts."""
        try:
            state.save_ui_state(body)
            return {"ok": True}
        except OSError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    def may_mark(request: Request):
        """config 'marks_from': 'all' (any PC that opens the page) or 'local' (only this PC)."""
        if cfg.get("marks_from", "all") != "local":
            return True
        return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")

    @app.post("/api/mark")
    async def api_mark(body: dict, request: Request):
        """Mark (override) a position qty or IV: {sym, exp, strike, field, value}; value null clears it.
        field: pe_qty | ce_qty (units) | iv | ce_iv | pe_iv (decimal, 0.185 = 18.5 %) | add (show this strike)."""
        if not may_mark(request):
            return JSONResponse(status_code=403, content={"error": "Marking is only allowed on the host PC (marks_from = local)."})
        field = body.get("field")
        if field not in ("pe_qty", "ce_qty", "iv", "ce_iv", "pe_iv", "add"):
            return JSONResponse(status_code=400, content={"error": "bad field"})
        if field == "add" and body.get("value") not in (None, ""):
            listed = state.engine.listed(body["sym"], body["exp"])
            if listed and float(body["strike"]) not in listed:
                near = min(listed, key=lambda k: abs(k - float(body["strike"])))
                return JSONResponse(status_code=400, content={
                    "error": f"{float(body['strike']):g} is not a listed {body['sym']} {body['exp']} strike (nearest {near:g})"})
        key = f"{body['sym']}|{body['exp']}"
        cell = f"{float(body['strike']):g}|{field}"
        value = body.get("value")
        table = state.engine.marks.setdefault(key, {})
        if value is None or value == "":
            table.pop(cell, None)
        else:
            value = float(value)
            if field.endswith("iv") and not 0 < value < 7:
                return JSONResponse(status_code=400, content={"error": "IV must be between 0 and 700 %"})
            table[cell] = value
        if not table:
            state.engine.marks.pop(key, None)
        state.save_marks()
        await state.tick()
        return {"ok": True, "marks": state.engine.marks.get(key, {})}

    @app.get("/api/boxgrid")
    async def api_boxgrid(kind: str, sym: str, exp: str):
        """The full Borrow / Lend grid of one expiry (row strike K1 x column strike K2, % p.a.)."""
        g = state.engine.box_grids.get((kind, sym), {}).get(exp)
        if not g:
            return JSONResponse(status_code=404, content={"error": "no grid for that expiry"})
        return {"kind": kind, "sym": sym, "exp": exp, "days": g["days"], "strikes": g["strikes"],
                "grid": [[None if v is None else round(v, 4) for v in row] for row in g["grid"]],
                "row_legs": g.get("row_legs", []), "col_legs": g.get("col_legs", [])}

    @app.post("/api/box")
    async def api_box(body: dict, request: Request):
        """Borrow / Lend settings. {params: {...}} and/or {kind, sym, expiries: [...], sq_ce, sq_pe}."""
        if not may_mark(request):
            return JSONResponse(status_code=403, content={"error": "Changing settings is only allowed on the host PC (marks_from = local)."})
        st = state.engine.box_settings
        if isinstance(body.get("params"), dict):
            p = st.setdefault("params", {})
            for k, v in body["params"].items():
                if k not in box.DEFAULT_PARAMS or k in box.FIXED_PARAMS:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": f"{k} must be a number"})
                if k == "top_n":
                    v = int(max(1, min(20, v)))
                p[k] = v
        if body.get("kind") in ("borrow", "lend") and body.get("sym"):
            d = st.setdefault(body["kind"], {}).setdefault(body["sym"], {})
            if isinstance(body.get("expiries"), list):
                d["expiries"] = [e or "" for e in body["expiries"]][: box.SLOTS]
            for k in ("sq_ce", "sq_pe"):
                if body.get(k) in ("Y", "N"):
                    d[k] = body[k]
        if body.get("reset"):
            st.clear()
        state.save_box()
        await state.tick()
        return {"ok": True, "settings": st}

    SUM_EDITABLE = ("gross",)        # Greeks & P&L "Other" columns that can be typed over

    @app.post("/api/summark")
    async def api_summark(body: dict, request: Request):
        """Type over a Greeks & P&L cell: {key: "SYM" or "SYM|EXP", field, value}; empty value clears it.
        {reset: true} clears them all."""
        if not may_mark(request):
            return JSONResponse(status_code=403, content={"error": "Marking is only allowed on the host PC (marks_from = local)."})
        if body.get("reset"):
            state.engine.sum_marks.clear()
        else:
            field = body.get("field")
            if field not in SUM_EDITABLE or not body.get("key"):
                return JSONResponse(status_code=400, content={"error": "that column can't be marked"})
            cell = f"{body['key']}|{field}"
            v = body.get("value")
            if v in (None, ""):
                state.engine.sum_marks.pop(cell, None)
            else:
                try:
                    state.engine.sum_marks[cell] = float(v)
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": "not a number"})
        state.save_marks()
        await state.tick()
        return {"ok": True, "sum_marks": state.engine.sum_marks}

    @app.post("/api/synmark")
    async def api_synmark(body: dict, request: Request):
        """Mark a Synthetic Future from another expiry: {sym, exp, base, add}; empty base clears it."""
        if not may_mark(request):
            return JSONResponse(status_code=403, content={"error": "Marking is only allowed on the host PC (marks_from = local)."})
        key, base = f"{body['sym']}|{body['exp']}", (body.get("base") or "").strip()
        if not base:
            state.engine.syn_marks.pop(key, None)
        else:
            if base == body["exp"]:
                return JSONResponse(status_code=400, content={"error": "pick a different expiry"})
            try:
                add = float(body.get("add") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"error": "points must be a number, e.g. 300 or -300"})
            state.engine.syn_marks[key] = {"base": base, "add": add}
        state.save_marks()
        await state.tick()
        return {"ok": True, "mark": state.engine.syn_marks.get(key)}

    @app.post("/api/synmark/reset")
    async def api_synmark_reset(body: dict, request: Request):
        """Clear Synthetic Future marks: {sym} for one index, or sym 'ALL' for every index."""
        if not may_mark(request):
            return JSONResponse(status_code=403, content={"error": "Marking is only allowed on the host PC (marks_from = local)."})
        sym = body.get("sym") or "ALL"
        for key in [k for k in state.engine.syn_marks if sym == "ALL" or k.split("|")[0] == sym]:
            state.engine.syn_marks.pop(key, None)
        state.save_marks()
        await state.tick()
        return {"ok": True}

    @app.post("/api/marks/reset")
    async def api_marks_reset(body: dict, request: Request):
        """Refresh one table: drop its markings so it goes back to the formula values."""
        if not may_mark(request):
            return JSONResponse(status_code=403, content={"error": "Marking is only allowed on the host PC (marks_from = local)."})
        state.engine.marks.pop(f"{body['sym']}|{body['exp']}", None)
        state.save_marks()
        await state.tick()
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        state.clients.add(ws)
        try:
            await ws.send_text(state.snapshot_text)
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.clients.discard(ws)

    return app, cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(BASE / "config.json"))
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app, cfg = build(args.config)
    host = args.host or cfg.get("http_host", "127.0.0.1")
    port = args.port or cfg.get("http_port", 8200)
    print(f"WhiteGamma Live on this PC:   http://127.0.0.1:{port}/", flush=True)
    if host in ("0.0.0.0", "::"):
        import socket
        try:
            ips = sorted({ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")})
        except OSError:
            ips = []
        try:   # the address used to reach the LAN (works even when the hostname lookup doesn't)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
                so.connect(("172.16.9.119", 9002))
                ips = sorted(set(ips) | {so.getsockname()[0]})
        except OSError:
            pass
        for ip in ips:
            print(f"From other PCs on the network: http://{ip}:{port}/", flush=True)
        print(f"(Windows firewall must allow TCP {port}: run allow_lan.bat once as administrator)", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
