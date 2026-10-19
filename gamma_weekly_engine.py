#!/usr/bin/env python3
"""Isolated adapter for the supplied GammaShort fixed-time comparison engine.

Uses the original numerical functions, without importing numba, changing thread
environment variables, running production CW/NW selection, or running main().
"""
import ast
import hashlib
import math
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parent
LANES = ("0916", "0917", "0918", "0919", "NOW")


def lut_signal(tables, stage, coordinate):
    table = tables[stage]
    if table is None:
        return "LUT_UNAVAILABLE"
    return "LUT_ON" if table[coordinate] else "LUT_OFF"



def usable_daily_input(value):
    if not isinstance(value, dict):
        return False
    fields = ("prev_straddle", "adj_iv_chg", "yesterday_iv", "prev_future", "prev_pure", "prev_with_prev")
    try:
        return all(math.isfinite(float(value[k])) and
                   (k == "adj_iv_chg" or float(value[k]) > 0) for k in fields)
    except (KeyError, TypeError, ValueError):
        return False

def load_engine(path=ROOT / "GammaShort_Backtest_Final.py"):
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import) and any(a.name == "numba" for a in node.names):
                continue
            body.append(node)
        elif isinstance(node, ast.FunctionDef):
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if all(isinstance(t, (ast.Name, ast.Tuple)) for t in targets):
                body.append(node)
    worker = next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == "process_day")
    # Disable production selection. Only the explicitly requested independent
    # fixed-time comparison trades run; their event sizing and costs remain.
    worker.body = [n for n in worker.body]
    class FixedOnly(ast.NodeTransformer):
        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "bfill":
                return self.visit(node.func.value)
            return self.generic_visit(node)

        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "decide":
                return ast.copy_location(ast.Pass(), node)
            return self.generic_visit(node)

        def visit_IfExp(self, node):
            # The fixed comparison records this diagnostic without filtering.
            if (isinstance(node.body, ast.Constant) and node.body.value == "LUT_ON"
                    and isinstance(node.orelse, ast.Constant) and node.orelse.value == "LUT_OFF"):
                return ast.copy_location(ast.parse("lut_signal(LUTS, row['stage'], row['coord'])", mode="eval").body, node)
            return self.generic_visit(node)
    for definition in body:
        if isinstance(definition, ast.FunctionDef):
            FixedOnly().visit(definition)
    module = types.ModuleType("gamma_fixed_authoritative")
    module.__dict__.update(__file__=str(path), lut_signal=lut_signal)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 str(path), "exec"), module.__dict__)
    module.source_sha256 = hashlib.sha256(source.encode()).hexdigest()
    return module


class ReferenceData:
    """All reference inputs are expiry-specific; never recycle CW inputs for NW."""
    def __init__(self, engine, environ):
        self.engine = engine
        self.errors = []
        self.tables = [None] * 5
        self.inputs = {}
        self.events = None
        self.files = {}
        folder = Path(environ.get("GS_LUT_DIR", engine.LUT_DIR))
        try:
            self.tables = engine.load_cw_luts(str(folder))
            self.files["luts"] = {n: hashlib.sha256((folder / n).read_bytes()).hexdigest()
                                  for n in engine.STAGE_FILES}
        except (OSError, ValueError, KeyError) as exc:
            self.errors.append("LUT_UNAVAILABLE: " + str(exc))
        # Canonical normalized inputs avoid assigning the one CW daily row to
        # every expiry. Original daily CSV can be used only with an explicit
        # authoritative expiry binding.
        normalized = environ.get("GS_EXPIRY_INPUTS")
        if normalized:
            try:
                frame = engine.pd.read_csv(normalized)
                for record in frame.to_dict("records"):
                    day = str(engine.pd.Timestamp(record["Date"]).date())
                    expiry = str(engine.pd.Timestamp(record["Expiry"]).date())
                    fields = ("prev_straddle", "adj_iv_chg", "yesterday_iv",
                              "prev_future", "prev_pure", "prev_with_prev")
                    values = {key: float(record[key]) for key in fields}
                    if not all(math.isfinite(v) for v in values.values()):
                        raise ValueError("Non-finite expiry reference")
                    if any(values[k] <= 0 for k in fields if k != "adj_iv_chg"):
                        raise ValueError("Nonpositive expiry reference")
                    if (day, expiry) in self.inputs:
                        raise ValueError("Duplicate expiry/date input")
                    self.inputs[day, expiry] = values
                self.files["expiry_inputs"] = hashlib.sha256(Path(normalized).read_bytes()).hexdigest()
            except (OSError, ValueError, KeyError) as exc:
                self.inputs.clear()
                self.errors.append("REFERENCE_UNAVAILABLE: " + str(exc))
        elif environ.get("GS_DAILY_CSV"):
            try:
                engine.DAILY_CSV = environ["GS_DAILY_CSV"]
                engine.DAY_INPUTS = "daily_csv"
                days = engine.load_day_inputs()
                if environ.get("GS_DAILY_EXPIRY"):
                    expiry = str(engine.pd.Timestamp(environ["GS_DAILY_EXPIRY"]).date())
                    self.inputs = {(d, expiry): v for d, v in days.items()}
                else:
                    frame = engine.pd.read_csv(engine.DAILY_CSV)
                    if "Expiry" not in frame:
                        raise ValueError("Daily CSV needs an Expiry column or explicit GS_DAILY_EXPIRY")
                    for r in frame.to_dict("records"):
                        day = str(engine.pd.Timestamp(r["Date"]).date())
                        expiry = str(engine.pd.Timestamp(r["Expiry"]).date())
                        if day in days:
                            self.inputs[day, expiry] = days[day]
                self.files["daily_csv"] = hashlib.sha256(Path(engine.DAILY_CSV).read_bytes()).hexdigest()
            except (OSError, ValueError, KeyError) as exc:
                self.errors.append("REFERENCE_UNAVAILABLE: " + str(exc))
        else:
            self.errors.append("REFERENCE_UNAVAILABLE: set GS_EXPIRY_INPUTS, or GS_DAILY_CSV with authoritative Expiry")
        self.inputs = {key: value for key, value in self.inputs.items() if usable_daily_input(value)}
        event_path = environ.get("GS_EVENT_PATH")
        try:
            if not event_path:
                raise ValueError("set GS_EVENT_PATH to an authoritative event calendar")
            path = Path(event_path)
            if path.suffix.lower() == ".csv":
                frame = engine.pd.read_csv(path)
                if "Date" not in frame:
                    raise ValueError("Event CSV requires a Date header (empty rows are permitted)")
                self.events = set(engine.pd.to_datetime(frame["Date"], dayfirst=True).dt.date)
            else:
                frame = engine.pd.read_excel(path, header=None)
                self.events = set(engine.pd.to_datetime(frame[0], dayfirst=True).dt.date)
            self.files["events"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError, KeyError, ImportError) as exc:
            self.errors.append("EVENT_CALENDAR_UNAVAILABLE: " + str(exc))
        adapter_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (ROOT / "gamma_weekly_engine.py", ROOT / "gamma_weekly_server.py")}
        bindings = sorted((day, expiry, tuple(sorted(value.items())))
                          for (day, expiry), value in self.inputs.items())
        grid_config = (engine.GRID_END_EARLY, engine.GRID_END_LATE, str(engine.GRID_END_SWITCH))
        fingerprint = repr((engine.source_sha256, sorted(self.files.items()),
                            sorted(adapter_hashes.items()), bindings, grid_config))
        self.fingerprint = hashlib.sha256(fingerprint.encode()).hexdigest()

    def blockers(self, day, expiry):
        result = []
        # Fixed-time trade maths do not depend on daily LUT/veto features.
        # Missing daily references are diagnostic, as explicitly requested.
        if self.events is None:
            result.append("EVENT_CALENDAR_UNAVAILABLE")
        return result

def evaluate(engine, references, frame, day, expiry, now_hhmm=None):
    """Evaluate one expiry; no production NW leg and no cross-expiry state."""
    blockers = references.blockers(day, expiry)
    if blockers:
        return {"status": "WAITING_INPUTS", "blockers": blockers, "lanes": {
            lane: {"status": "WAITING_INPUTS", "blockers": blockers} for lane in LANES}}
    pd = engine.pd
    entries = (916, 917, 918, 919) + ((now_hhmm,) if now_hhmm else ())
    engine.FIXED_ENTRIES = entries
    engine.NW_ENABLED = False
    engine.IV_MODE = "tick"  # live adapter: observed quote updates, not certified prints
    engine.CHECK_FALLBACK = "prev"
    engine._CTX.clear()
    engine._CTX.update(luts=references.tables, nwl=None,
                       inputs={day: references.inputs.get((day, expiry))},
                       events=references.events)
    original_loader, original_sim = engine.load_cw_ticks, engine.simulate_cw
    original_worker = engine.process_day
    missing_daily = not usable_daily_input(references.inputs.get((day, expiry)))
    captured = {}
    def simulate(grid, entry_idx, cache, dte_idx, build_iv):
        result = original_sim(grid, entry_idx, cache, dte_idx, build_iv)
        tag = int(grid.iloc[entry_idx].Time.strftime("%H%M"))
        if result is not None:
            exit_stamp = pd.Timestamp(day + " " + result["exit_time"])
            tail = grid.loc[grid.Time >= exit_stamp]
            # A forward average is final only after all its observed rows exist.
            captured[tag] = dict(result, dte_idx=int(dte_idx), build_iv=float(build_iv),
                                 entry_time=str(grid.iloc[entry_idx].Time),
                                 stable_fill=len(tail) >= 61)
        return result
    engine.load_cw_ticks = lambda *_: (frame.copy(), pd.Timestamp(expiry), None)
    engine.simulate_cw = simulate
    if missing_daily:
        engine.process_day = lambda _: market_fixed_worker(engine, frame, day, expiry)
    try:
        fake_path = "/" + pd.Timestamp(day).strftime("%d%m%Y") + "/live"
        result = engine.process_day(fake_path)
    finally:
        engine.load_cw_ticks, engine.simulate_cw = original_loader, original_sim
        engine.process_day = original_worker
    lanes = {}
    last_time = pd.to_datetime(frame.Time_dt).max() if not frame.empty else pd.Timestamp(day)
    end = engine.GRID_END_LATE if pd.Timestamp(day) >= engine.GRID_END_SWITCH else engine.GRID_END_EARLY
    end_stamp = pd.Timestamp(day).replace(hour=end[0], minute=end[1], second=end[2])
    for lane in LANES:
        th = now_hhmm if lane == "NOW" else int(lane)
        if not th:
            lanes[lane] = {"status": "READY", "signal": None}
            continue
        tag = f"{th:04d}"
        signal = result.get("Signal_" + tag, "NO_DATA")
        sim = captured.get(th)
        if sim is None:
            at = pd.Timestamp(day).replace(hour=th // 100, minute=th % 100)
            status = "WAITING_QUOTES" if last_time <= at else "NO_ENTRY_DATA"
            lanes[lane] = {"status": status, "signal": signal, "note": result.get("Note", "")}
            continue
        finite = all(math.isfinite(sim[key]) for key in ("gross", "hedge_notional",
                     "entry_credit", "entry_underlying", "tp_bps"))
        if not finite:
            lanes[lane] = {"status": "INVALID_MARKET_DATA", "signal": signal}
            continue
        event = day in {d.isoformat() for d in references.events}
        size = engine.EVENT_SIZE if event else 1.0
        di = sim["dte_idx"]
        entry_cost = engine.ENTRY_SLIP.get(di, engine.ENTRY_SLIP_DEF)
        hedge_cost = engine.HEDGE_SLIP_BPS.get(di, engine.HEDGE_SLIP_DEF) / 1e4 * sim["hedge_notional"] * size
        closed = sim["exit_reason"] != "EOD" and sim["stable_fill"]
        end_reached = last_time >= end_stamp
        status = "CLOSED" if closed or end_reached else "OPEN_PROVISIONAL"
        lanes[lane] = dict(sim, status=status, signal=signal, event=event,
            size=size, gross_pnl=sim["gross"] * size, entry_cost=entry_cost,
            hedge_cost=hedge_cost, net_pnl=sim["gross"] * size - entry_cost - hedge_cost,
            pnl_basis="normalized_to_25000", filters_gate_entry=False)
    return {"status": "EVALUATED", "lanes": lanes, "note": result.get("Note", ""),
            "reference_fingerprint": references.fingerprint,
            "model_sha256": engine.source_sha256,
            "daily_reference_available": not missing_daily}

def market_fixed_worker(engine, frame, day, expiry):
    """Full fixed-trade maths when optional daily signal references are absent."""
    pd, np = engine.pd, engine.np
    data = frame.copy()
    data["Time"] = pd.to_datetime(data["Time_dt"])
    data["Second"] = data["Time"].dt.time
    data["_K"] = pd.to_numeric(data["Strike"])
    base = pd.Timestamp(day)
    end = engine.GRID_END_LATE if base >= engine.GRID_END_SWITCH else engine.GRID_END_EARLY
    close = base.replace(hour=end[0], minute=end[1], second=end[2])
    data = data[(data.Time >= base.replace(hour=9, minute=15)) & (data.Time <= close)]
    grouped = data.groupby("Time", sort=True)
    grid = grouped[["UnderlyingLTP", "UnderlyingBid", "UnderlyingAsk", "DTE"]].first().reset_index()
    grid.rename(columns={"UnderlyingLTP":"Underlying"}, inplace=True)
    grid.dropna(subset=["Underlying"], inplace=True)
    grid.reset_index(drop=True, inplace=True)
    result = {"Note":"Daily signal reference unavailable; full fixed trade calculations used"}
    if grid.empty:
        return result
    grid["Second"] = grid.Time.dt.time
    grid["ATMRef"] = (grid.Underlying / 50).round() * 50
    for side in ("Bid","Ask"):
        values = grid["Underlying"+side]
        grid["FutExec"+side] = values.rolling(engine.N_FUT_SECONDS,min_periods=1).mean().shift(
            -(engine.N_FUT_SECONDS-1)).fillna(values)
    cache = {strike:engine.strike_arrays(strike,grid,data) for strike in grid.ATMRef.unique()}
    for name in ("OTMIVRef","CERef","PERef"):
        grid[name] = np.nan
    for strike, arrays in cache.items():
        mask = (grid.ATMRef == strike).values
        grid.loc[mask,"OTMIVRef"] = arrays[2][mask]
        grid.loc[mask,"CERef"] = arrays[0][mask]
        grid.loc[mask,"PERef"] = arrays[1][mask]
    raw_initial = float(grid.DTE.iloc[0])
    bus_days = int(np.busday_count(np.datetime64(day),np.datetime64(day)+np.timedelta64(int(np.ceil(raw_initial)),"D")))
    for th in engine.FIXED_ENTRIES:
        tag = f"{th:04d}"
        boundary = base.replace(hour=th//100,minute=th%100)
        before = grid[grid.Time <= boundary]
        after = grid[grid.Time >= boundary]
        if before.empty or after.empty:
            result["Signal_"+tag] = "NO_DATA"
            continue
        feature = before.iloc[-1]
        raw = float(feature.DTE)
        iv = float(feature.OTMIVRef)
        if not (math.isfinite(raw) and raw > 0 and math.isfinite(iv)):
            result["Signal_"+tag] = "NO_DATA"
            continue
        adjusted = max(0.,raw-engine.WKND_ADJ) if int(np.ceil(raw))-bus_days > 0 else raw
        factor = math.sqrt(raw/adjusted) if adjusted > 0 and raw != adjusted else 1.
        bucket = int(np.clip(np.ceil(adjusted)-1,0,6))
        engine.simulate_cw(grid,int(after.index[0]),cache,bucket,iv*factor)
        result["Signal_"+tag] = "REFERENCE_UNAVAILABLE"
    return result
