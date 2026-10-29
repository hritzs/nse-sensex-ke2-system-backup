"""The calculations of the Nifty / Banknifty / Sensex / FB sheets, driven by the live market feed.

Sheet cell -> code
  FB!C / FB!N  live / close Synthetic Future -> synthetic.SyntheticModel (all FB row types)
  FB!D..T      roll, ATM bid IV, straddle     -> Engine._board_row()
  Nifty!L16    per-strike IV change vs close  -> Engine._book() (ce_iv_ch / pe_iv_ch / iv_ch)
  Nifty!H3   'Day Left'                   -> day_left()
  Nifty!I16  strike IV (OTM side, cap 7)  -> black.otm_iv()
  BN..EV     delta/vega/gamma/theta x qty -> Engine._book()
  D7:G7      per-expiry Delta/Gamma/Vega/Theta
  FT..LR     +/-1,2,5,10 % stress P&L
  PrevQty BB:BE  Vega/Delta/Theta G/L and FTD vs previous close
  TradeSheet BF  Hedge G/L on today's trades
"""
from __future__ import annotations

import math
import dataclasses
import time
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

import black
import box
import span
from synthetic import SyntheticModel, mround


def hhmm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def day_left(now: datetime, start="09:15", end="15:40") -> float:
    """Nifty!H3 = MAX(MIN(1,(ED-now)/(ED-ST)),0)."""
    s, e = hhmm(start), hhmm(end)
    minutes = now.hour * 60 + now.minute + now.second / 60
    return max(min(1.0, (e - minutes) / (e - s)), 0.0)


def _pos(x):
    return x is not None and isinstance(x, (int, float)) and x > 0 and math.isfinite(x)


class Engine:
    def __init__(self, cfg, feed):
        self.cfg = cfg
        self.feed = feed
        self.bhav: dict = {}
        self.tokens: dict = {}         # daily token files (GreekTokens / IndexTokens.csv): (sym, exp, strike, kind) -> token
        self._tok_cache = (None, {}, {}, set())
        self.span_rf: dict = {}        # "NSCCL" / "ICCL" -> span.RiskFile (daily SPAN risk files)
        self.ael: dict = {}            # exact ELM % per option contract (F_AEL_OPT_CONTRACTS)
        self.span_tiers: dict = {}
        self.span_floor = True
        self.prev: dict = {}
        self.trades: dict = {}
        self.sum_marks: dict = {}      # Greeks & P&L typed-over cells: "SYM|field" or "SYM|EXP|field" -> value as shown
        self.syn_marks: dict = {}      # "SYM|EXP" -> {"base": "YYYY-MM-DD", "add": points}: Synthetic Future = base expiry's + add
        self.box_settings: dict = {}   # Borrow / Lend tabs: {"params": {...}, "borrow": {SYM: {...}}, "lend": {...}}
        self._box_targets: dict = {}   # (sym, exp) -> grid strikes to subscribe
        self.box_grids: dict = {}      # (kind, sym) -> {exp: {strikes, grid, days}}
        self.trades_raw: dict = {}      # as read from the trade files; self.trades = snapped to listed expiries
        self.centers: dict = {}
        self.syn: dict = {}            # (sym, exp) -> SynRow from the last compute
        self.touched: dict = {}        # (sym, exp) -> strikes the FB model looked up
        self.deps: set = set()         # (sym, exp) the FB model needs live quotes for
        self._close_strikes: dict = {}
        self._bhav_id = None
        self._iv_cache: dict = {}
        self.marks: dict = {}          # "SYM|EXP" -> {"STRIKE|field": value}; fields pe_qty ce_qty iv ce_iv pe_iv, add (strike added to the table)
        self.compute_ms = 0.0
        self.closed_mode = False
        self.today_iso = ""

    # ------------------------------------------------------------ prices
    def live(self, sym, exp, strike, kind):
        """Today's live quote. Empty outside market hours (Nifty!K2 'Cl Flag': the sheet then
        prices everything off the bhavcopy) and for quotes the adapter cached on an earlier day."""
        if strike is not None:
            self.touched.setdefault((sym, exp), set()).add(float(strike))
        if self.closed_mode:
            return {}
        q = self.feed.quote(self.feed.master.iid(sym, exp, strike, kind))
        if not q or not str(q.get("received_ts") or "").startswith(self.today_iso):
            return {}
        return q

    def price(self, sym, exp, strike, kind):
        """Live LTP (or mid when configured); bhavcopy close when there is no live quote
        (before 09:15, after 17:55, or no trade yet today)."""
        q = self.live(sym, exp, strike, kind)
        if self.cfg.get("price_source") == "mid" and _pos(q.get("bid")) and _pos(q.get("ask")):
            return (q["bid"] + q["ask"]) / 2, "live"
        if _pos(q.get("ltp")):
            return q["ltp"], "live"
        c = self.close(sym, exp, strike, kind)
        return (c, "close") if c is not None else (None, None)

    def close(self, sym, exp, strike, kind):
        c = self.bhav.get((sym, exp, None if kind == "FUT" else float(strike), kind))
        return c if _pos(c) else None

    def _tok_index(self):
        if self._tok_cache[0] is not self.tokens:      # rebuilt only when a token file is re-read
            exps, strikes, futs = {}, {}, set()
            for (s, e, k, kind) in self.tokens:
                exps.setdefault(s, set()).add(e)
                if kind == "FUT":
                    futs.add((s, e))
                else:
                    strikes.setdefault((s, e), set()).add(k)
            self._tok_cache = (self.tokens, {s: sorted(v) for s, v in exps.items()},
                               {key: sorted(v) for key, v in strikes.items()}, futs)
        return self._tok_cache[1:]

    def all_expiries(self, sym):
        """Expiries in the feed's instrument master plus the daily token files (so an expiry listed today
        shows even before the feed adapter is refreshed - it just has no live prices until then)."""
        t_exps, _, _ = self._tok_index()
        return sorted(set(self.feed.master.expiries.get(sym, [])) | set(t_exps.get(sym, [])))

    def has_future(self, sym, exp):
        return (sym, exp) in self.feed.master.has_future or (sym, exp) in self._tok_index()[2]

    def in_feed(self, sym, exp):
        return exp in (self.feed.master.expiries.get(sym) or [])

    def listed(self, sym, exp):
        s = self.feed.master.strikes.get((sym, exp))
        return s if s else self._tok_index()[1].get((sym, exp), [])

    def close_strikes(self, sym, exp):
        if self._bhav_id != id(self.bhav):
            cs = {}
            for (s, e, k, kind) in self.bhav:
                if k is not None:
                    cs.setdefault((s, e), set()).add(k)
            self._close_strikes = {key: sorted(v) for key, v in cs.items()}
            self._bhav_id = id(self.bhav)
            self._iv_cache.clear()
        return self._close_strikes.get((sym, exp), [])

    def _apply_syn_marks(self, sym, syn):
        """Marked Synthetic Future: this expiry = the chosen (liquid) expiry's Synthetic Future + points.
        Applied to the live and the close value alike, so FTD / IV change stay consistent."""
        for exp in sorted(syn):
            m = self.syn_marks.get(f"{sym}|{exp}")
            if not m or m.get("base") == exp or m.get("base") not in syn:
                continue
            r, base, add = syn[exp], syn[m["base"]], float(m.get("add") or 0)
            f = lambda v: None if v is None else v + add
            det = dict(r.detail)
            det.update(mark_base=m["base"], mark_add=add, calc_value=r.value, calc_close=r.close)
            syn[exp] = dataclasses.replace(r, value=f(base.value), live=f(base.live), close=f(base.close),
                                           src="mark", detail=det)

    def _snap_trades(self, raw):
        """A trade whose expiry date isn't a listed expiry (e.g. a BSE monthly worked out as the last
        Thursday when the exchange moved it for a holiday) goes to the listed expiry of that month."""
        if not raw:
            return {}
        out = {}
        for (sym, exp, k, kind), tr in raw.items():
            exps = self.all_expiries(sym)
            if exps and exp not in exps:
                same = [e for e in exps if e[:7] == exp[:7]]
                if same:
                    exp = max([e for e in same if e <= exp] or [min(same)])
            key = (sym, exp, k, kind)
            if key in out:
                t = out[key]
                t = type(tr)(t.buy_qty + tr.buy_qty, t.buy_value + tr.buy_value,
                             t.sell_qty + tr.sell_qty, t.sell_value + tr.sell_value, t.count + tr.count)
                out[key] = t
            else:
                out[key] = tr
        return out

    # ------------------------------------------------------------ expiries / targets
    def expiries(self, sym, today_iso):
        return [e for e in self.all_expiries(sym) if e >= today_iso]

    def position_keys(self, today_iso):
        keys = {k for k, v in self.prev.items() if v.net_qty and k[1] >= today_iso}
        keys |= {k for k in self.trades if k[1] >= today_iso}
        return keys

    def tracked(self, today_iso):
        out = {}
        pos = self.position_keys(today_iso)
        for sym, icfg in self.cfg["indices"].items():
            exps = self.expiries(sym, today_iso)
            chosen = set(exps[: icfg.get("board_expiries", 4)])
            chosen |= {k[1] for k in pos if k[0] == sym and k[1] in exps}
            chosen |= {e for (s, e) in self.deps if s == sym and e in exps}
            for e in sorted(chosen):
                out[(sym, e)] = {k[2] for k in pos if k[0] == sym and k[1] == e and k[2] is not None}
        return out

    def fb_expiries(self, sym, today_iso):
        """The expiries the FB sheet has rows for (rows 5-20: 2 monthlies, the weeklies, the first far
        expiries after the 2nd monthly). Shown in Synthetic Future & IV even when you hold nothing there."""
        c = SyntheticModel(sym, (self.cfg.get("synthetic") or {}).get(sym), self).c
        if c.get("kind") != "fb":
            return []
        exps = self.expiries(sym, today_iso)
        months = [e for e in exps if self.has_future(sym, e)][: c.get("monthly_count", 2)]
        m2 = months[1] if len(months) > 1 else (months[0] if months else None)
        weeklies = [e for e in exps if e != m2][: c.get("weekly_count", 0)]
        far = [e for e in exps if m2 and e > m2][: len(c.get("far_gaps") or [])]
        return sorted(set(months) | set(weeklies) | set(far))

    def anchor(self, sym, exp):
        r = self.syn.get((sym, exp))
        if r and r.value:
            return r.value
        p, _ = self.price(sym, exp, None, "FUT")
        if p:
            return p
        for e in self.all_expiries(sym):
            if self.has_future(sym, e):
                p, _ = self.price(sym, e, None, "FUT")
                if p:
                    return p
        return None

    def window(self, sym, exp, anchor):
        """Listed strikes around the anchor; keeps its centre until it drifts a third of the window."""
        listed = self.listed(sym, exp)
        if not listed or anchor is None:
            return []
        n = self.cfg["indices"][sym].get("chain_strikes_each_side", 15)
        idx = min(range(len(listed)), key=lambda i: abs(listed[i] - anchor))
        prev = self.centers.get((sym, exp))
        if prev is not None and prev < len(listed) and abs(prev - idx) <= max(1, n // 3):
            idx = prev
        self.centers[(sym, exp)] = idx
        return listed[max(0, idx - n): idx + n + 1]

    def subscription_targets(self, today_iso):
        """Only contracts in the feed's own list: anything else is refused by the feed (an expiry
        listed today shows from the token files but gets live prices once the feed adapter is refreshed)."""
        fs = self.feed.master.strikes
        targets = {}
        tracked = self.tracked(today_iso)
        for (sym, exp), pos_strikes in tracked.items():
            if not self.in_feed(sym, exp):
                continue
            listed = set(fs.get((sym, exp), []))
            strikes = set(pos_strikes) | set(self.window(sym, exp, self.anchor(sym, exp)))
            strikes |= self.touched.get((sym, exp), set())
            targets[(sym, exp)] = {k for k in strikes if k in listed}
        for sym in self.cfg["indices"]:                            # FB rows without a position
            for exp in self.fb_expiries(sym, today_iso):
                if (sym, exp) in tracked or not self.in_feed(sym, exp):
                    continue
                listed = set(fs.get((sym, exp), []))
                targets[(sym, exp)] = {k for k in self.touched.get((sym, exp), set()) if k in listed}
        for (sym, exp), ks in self._box_targets.items():          # Borrow / Lend grids
            if not self.in_feed(sym, exp):
                continue
            listed = set(fs.get((sym, exp), []))
            targets.setdefault((sym, exp), set()).update(k for k in ks if k in listed)
        return targets

    # ------------------------------------------------------------ Borrow / Lend (BorrowLend.xlsx)
    def box_config(self):
        st = self.box_settings or {}
        params = {**box.DEFAULT_PARAMS, **(st.get("params") or {})}
        for k in box.FIXED_PARAMS:                 # buy cost multiplier and STT are fixed (not editable)
            params[k] = box.DEFAULT_PARAMS[k]
        sheets = {}
        for kind in ("borrow", "lend"):
            sheets[kind] = {}
            for sym, d in box.DEFAULT_SHEETS[kind].items():
                if sym in self.cfg["indices"]:
                    sheets[kind][sym] = {**d, **((st.get(kind) or {}).get(sym) or {})}
        return params, sheets

    def master_check(self, today_iso):
        """Compare the feed's instrument master (built from GreekTokens.csv / BSEIndexTokens.csv on the
        feed machine) with our own data: contracts you hold or traded today, and the previous bhavcopy.
        A contract missing from the master gets no live price (the feed refuses it)."""
        m = self.feed.master
        if not m.by_key:
            return {"ready": False}
        listed = m.by_key
        def name(k):
            sym, exp, strike, kind = k
            return {"sym": sym, "exp": exp, "strike": strike, "kind": kind}
        held, traded = [], []
        for k, v in self.prev.items():
            if v.net_qty and k[1] >= today_iso and k not in listed:
                held.append({**name(k), "qty": v.net_qty})
        for k, t in self.trades.items():
            if k[1] >= today_iso and k not in listed:
                traded.append({**name(k), "qty": t.net, "trades": t.count})
        # bhavcopy: whole expiries missing, and per expiry how many strikes are missing
        exp_missing, strike_missing = [], []
        by_exp = {}
        for k in self.bhav:
            if k[1] >= today_iso and k[0] in self.cfg["indices"]:
                by_exp.setdefault((k[0], k[1]), []).append(k)
        for (sym, exp), keys in sorted(by_exp.items()):
            if exp not in (m.expiries.get(sym) or []):
                exp_missing.append({"sym": sym, "exp": exp, "contracts": len(keys)})
                continue
            miss = sorted({k[2] for k in keys if k[2] is not None and k not in listed})
            if miss:
                strike_missing.append({"sym": sym, "exp": exp, "count": len(miss), "strikes": miss[:12]})
        tok_exps, tok_strikes = self.token_gaps(today_iso)
        return {"ready": True, "held": sorted(held, key=lambda x: (x["sym"], x["exp"], x["strike"] or 0)),
                "token_expiries": tok_exps, "token_strikes": tok_strikes, "token_contracts": len(self.tokens),
                "traded": sorted(traded, key=lambda x: (x["sym"], x["exp"], x["strike"] or 0)),
                "bhav_expiries": exp_missing, "bhav_strikes": strike_missing,
                "master_contracts": len(listed), "loaded_at": m.loaded_at}

    def _margins(self, summary, today):
        """SPAN + Exposure on the live book (previous close positions + today's trades; marks not applied).
        'margin' now, 'margin_nd' next day (without contracts expiring today)."""
        today_iso = today.isoformat()
        book = {}
        for k in self.position_keys(today_iso):
            u = self.net_qty(k[0], k[1], k[2], k[3])
            if u:
                book[k] = u
        nxt = {k: u for k, u in book.items() if k[1] > today_iso}
        keys = ("span", "exposure", "margin", "margin_nd")
        grand = dict.fromkeys(keys, 0.0)
        have = False
        for sym in self.cfg["indices"]:
            ec = span.exchange_of(sym)
            rf = self.span_rf.get(ec)
            exps = [e for e in self.all_expiries(sym) if e >= today_iso]
            near = next((e for e in exps if self.has_future(sym, e)), None)
            und = self.price(sym, near, None, "FUT")[0] if near else None
            if not any(k[0] == sym for k in book):
                continue
            r = span.compute(sym, book, rf, und, today, self.ael, self.span_tiers, self.span_floor)
            rn = span.compute(sym, nxt, rf, und, today, self.ael, self.span_tiers, self.span_floor)
            r["margin_nd"] = rn["margin"]
            r["file"] = rf.path.replace("\\", "/").rsplit("/", 1)[-1] if rf else None
            summary[sym]["margin_detail"] = r
            if rf is None:
                continue
            have = True
            for k in keys:
                summary[sym][k] = r[k]
                grand[k] += r[k]
        if have:
            summary["TOTAL"].update(grand)

    def token_gaps(self, today_iso):
        """Daily token files (1.85 IndexTokens.csv / BSEIndexTokens.csv) vs the feed's list: expiries
        and strikes listed by the exchange that the feed adapter has not loaded yet."""
        m = self.feed.master
        exps, strikes = [], []
        if not self.tokens or not m.by_key:
            return exps, strikes
        t_exps, t_strikes, _ = self._tok_index()
        for sym in self.cfg["indices"]:
            feed_exps = set(m.expiries.get(sym) or [])
            for e in t_exps.get(sym, []):
                if e < today_iso:
                    continue
                if e not in feed_exps:
                    n = sum(1 for k in self.tokens if k[0] == sym and k[1] == e)
                    exps.append({"sym": sym, "exp": e, "contracts": n})
                    continue
                fk = set(m.strikes.get((sym, e)) or [])
                miss = [k for k in t_strikes.get((sym, e), []) if k not in fk]
                if miss:
                    strikes.append({"sym": sym, "exp": e, "count": len(miss), "strikes": miss[:12]})
        return exps, strikes

    def net_qty(self, sym, exp, k, kind):
        """Live position: previous close + today's trades (marks not applied)."""
        key = (sym, exp, None if kind == "FUT" else float(k), kind)
        p, tr = self.prev.get(key), self.trades.get(key)
        return (p.net_qty if p else 0.0) + (tr.net if tr else 0.0)

    # legs of the box: (option, strike, +1 buy / -1 sell)
    BOX_LEGS = {"borrow": (("CE", "k1", -1), ("PE", "k1", 1), ("CE", "k2", 1), ("PE", "k2", -1)),
                "lend": (("CE", "k1", 1), ("PE", "k1", -1), ("CE", "k2", -1), ("PE", "k2", 1))}

    def compute_box(self, today):
        params, sheets = self.box_config()
        today_iso = today.isoformat()
        out, targets = {"params": params, "expiries": {}}, {}
        for kind, per_sym in sheets.items():
            out[kind] = {}
            for sym, sh in per_sym.items():
                listed = self.expiries(sym, today_iso)
                out["expiries"][sym] = listed
                exps = [e for e in (sh.get("expiries") or [])[: box.SLOTS] if e in listed]
                near = next((e for e in listed if self.has_future(sym, e)), None)
                fut = self.price(sym, near, None, "FUT")[0] if near else None    # Borrow!C3 / Top Pairs!H3

                def quote_for(exp, sym=sym):
                    def q(k, kind_, side):
                        v = self.live(sym, exp, k, kind_).get(side)
                        return v if _pos(v) else None
                    return q
                res = box.compute(kind, sym, exps, today, fut, fut, quote_for, params, sh.get("sq_ce", "N"), sh.get("sq_pe", "N"))
                res["slots"] = (sh.get("expiries") or [])[: box.SLOTS]
                full = {}
                for b in res["blocks"]:
                    for p in b["top"]:        # legs that would square off (reduce) a live position
                        p["sq"] = []
                        for opt, kk, d in self.BOX_LEGS[kind]:
                            q = self.net_qty(sym, b["expiry"], p[kk], opt)
                            if q and (q > 0) != (d > 0):
                                p["sq"].append({"side": "Buy" if d > 0 else "Sell", "opt": opt, "k": p[kk], "qty": q})
                    targets.setdefault((sym, b["expiry"]), set()).update(b["strikes"])
                    full[b["expiry"]] = {"strikes": b.pop("strikes"), "grid": b.pop("grid"), "days": b["days"],
                                         "row_legs": b.pop("row_legs", []), "col_legs": b.pop("col_legs", [])}
                    b["cells"] = sum(1 for row in full[b["expiry"]]["grid"] for v in row if v is not None)
                self.box_grids[(kind, sym)] = full                 # sent on request (/api/boxgrid), not every tick
                out[kind][sym] = res
        self._box_targets = targets
        return out

    # ------------------------------------------------------------ synthetic futures (FB sheet)
    def synthetic(self, sym, today, preopen, tracked):
        exps = self.expiries(sym, today.isoformat())
        futs = {e for e in exps if self.has_future(sym, e)}
        pos_strikes, windows = {}, {}
        for (s, e), ks in tracked.items():
            if s == sym:
                pos_strikes[e] = ks
                c = self.centers.get((s, e))
                listed = self.listed(s, e)
                if c is not None and listed:
                    n = self.cfg["indices"][sym].get("display_chain_each_side", 6) + 2
                    windows[e] = listed[max(0, c - n): c + n + 1]
        model = SyntheticModel(sym, (self.cfg.get("synthetic") or {}).get(sym), self)
        rows = model.compute(exps, futs, today, preopen, pos_strikes, windows)
        # live-quote dependencies of the tracked rows: both monthlies, first far row
        months = sorted(futs)[:2]
        deps = {(sym, e) for e in months}
        far = [e for e in exps if months and e > months[-1]]
        if far and any((sym, e) in tracked for e in far):
            deps.add((sym, far[0]))
        return rows, deps

    # ------------------------------------------------------------ main compute
    def compute(self, now: datetime | None = None, prev_date: date | None = None):
        t0 = time.perf_counter()
        now = now or datetime.now(IST).replace(tzinfo=None)
        today = now.date()
        today_iso = today.isoformat()
        sess = self.cfg.get("session", {})
        h3 = day_left(now, sess.get("start", "09:15"), sess.get("end", "15:40"))
        minute = now.hour * 60 + now.minute
        # Nifty!K2 'Cl Flag' = 1 before 09:15 or after 15:40 + 2:15 (17:55): bhavcopy prices only
        self.closed_mode = (minute < hhmm(sess.get("start", "09:15"))
                            or minute > hhmm(sess.get("close_prices_after", "17:55")))
        self.today_iso = today_iso
        preopen = self.closed_mode
        shocks = self.cfg.get("stress_shocks", [0.01, 0.02, 0.05, 0.10])
        extra_days = self.cfg.get("expiry_extra_days", {})
        self.trades = self._snap_trades(self.trades_raw)
        tracked = self.tracked(today_iso)
        self.touched = {}

        books, board, summary = {}, {}, {}
        grand = _zero_totals(shocks)
        deps = set()
        for sym in self.cfg["indices"]:
            syn, d = self.synthetic(sym, today, preopen, tracked)
            self._apply_syn_marks(sym, syn)
            deps |= d
            for e, r in syn.items():
                self.syn[(sym, e)] = r
            exps = sorted(e for (s, e) in tracked if s == sym)
            sym_tot = _zero_totals(shocks)
            books[sym], board[sym] = [], []
            all_exps = self.expiries(sym, today_iso)
            near = next((e for e in all_exps if self.has_future(sym, e)), None)
            near_fut = self.price(sym, near, None, "FUT")[0] if near else None
            first = syn.get(near).value if near in syn else None
            for exp in exps:
                r = syn.get(exp)
                days = (date.fromisoformat(exp) - today).days
                extra = float(extra_days.get(exp, 0))
                t = max((days + h3 + extra) / 365.0, 1e-5)
                fwd, src, cl_fwd = (r.value, r.src, r.close) if r else (None, "", None)
                book = self._book(sym, exp, days, extra, t, fwd, src, cl_fwd, shocks, tracked[(sym, exp)])
                book["syn_row"] = r.row if r else ""
                books[sym].append(book)
            for exp in sorted(set(exps) | set(self.fb_expiries(sym, today_iso))):
                r = syn.get(exp)
                if r:
                    days = (date.fromisoformat(exp) - today).days
                    board[sym].append(self._board_row(sym, r, days, h3, preopen, first, near_fut, prev_date, today))
            # Nifty!W5:X11: skew scaled by sqrt(T / T of the nearest expiry you hold), summed over expiries
            held = [b for b in books[sym] if b["fut_qty"] or any(x["pos"] for x in b["rows"])]
            t_ref = held[0]["t_days"] if held else None
            for book in books[sym]:
                tb = book["totals"]
                tb["skew_t"] = tb["skew"] / math.sqrt(book["t_days"] / t_ref) if (t_ref and book["t_days"] > 0) else 0.0
                book["skew_ref"] = held[0]["expiry"] if held else None
                _add_totals(sym_tot, tb, book["pnl"], book["stress"])
            summary[sym] = sym_tot
            _add_totals(grand, sym_tot, sym_tot, sym_tot["stress"])
        summary["TOTAL"] = grand
        self._margins(summary, today)
        self.deps = deps
        box_out = self.compute_box(today)
        self.compute_ms = (time.perf_counter() - t0) * 1000
        return {"ts": now.isoformat(timespec="seconds"), "today": today_iso, "day_left": h3,
                "preopen": preopen, "summary": summary, "board": board, "books": books, "shocks": shocks,
                "box": box_out,
                "compute_ms": round(self.compute_ms, 1)}

    def _close_iv(self, sym, exp, k, kind, cl_fwd, t):
        key = (sym, exp, k, kind, round(cl_fwd, 4), round(t, 8))
        if key not in self._iv_cache:
            px = self.close(sym, exp, k, kind)
            self._iv_cache[key] = black.implied_vol(cl_fwd, k, px, t, kind) if px else None
        return self._iv_cache[key]

    def _book(self, sym, exp, days, extra, t, fwd, src, cl_fwd, shocks, pos_strikes):
        key_prev = lambda k, kind: self.prev.get((sym, exp, k, kind))
        key_tr = lambda k, kind: self.trades.get((sym, exp, k, kind))

        def qty(k, kind):
            p, tr = key_prev(k, kind), key_tr(k, kind)
            return (p.net_qty if p else 0.0) + (tr.net if tr else 0.0)

        chain = set()
        if self.cfg.get("show_chain", True) and fwd:
            n = self.cfg["indices"][sym].get("display_chain_each_side", 6)
            listed = self.listed(sym, exp)
            if listed:
                i = min(range(len(listed)), key=lambda j: abs(listed[j] - fwd))
                chain = set(listed[max(0, i - n): i + n + 1])
        marks = self.marks.get(f"{sym}|{exp}", {})
        marked_strikes = {float(k.split("|")[0]) for k in marks}

        def mk(k, field):
            return marks.get(f"{k:g}|{field}")

        strikes = sorted(set(pos_strikes) | chain | marked_strikes)
        tot = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "gross": 0.0, "net_qty": 0.0, "skew": 0.0, "skew_t": 0.0}
        stress = {s: 0.0 for s in _shock_keys(shocks)}
        rows = []
        t_now = t
        t_prev = max((days + 1 + extra) / 365.0, 1e-5)   # Nifty!L16 / PrevQty!AS: T at previous close
        pnl = {"ftd": 0.0, "theta_gl": 0.0, "vega_gl": 0.0, "delta_gl": 0.0, "hedge_gl": 0.0}
        atm = min(strikes, key=lambda k: abs(k - fwd)) if (fwd and strikes) else None

        for k in strikes:
            ce_px, ce_src = self.price(sym, exp, k, "CE")
            pe_px, pe_src = self.price(sym, exp, k, "PE")
            cq, pq = qty(k, "CE"), qty(k, "PE")
            marked = []
            if mk(k, "ce_qty") is not None:
                cq = float(mk(k, "ce_qty")); marked.append("ce_qty")
            if mk(k, "pe_qty") is not None:
                pq = float(mk(k, "pe_qty")); marked.append("pe_qty")
            iv = black.otm_iv(fwd, k, ce_px, pe_px, t) if fwd else None
            if mk(k, "iv") is not None:
                iv = float(mk(k, "iv")); marked.append("iv")
            ce_iv = black.implied_vol(fwd, k, ce_px, t, "CE") if (fwd and ce_px) else None
            pe_iv = black.implied_vol(fwd, k, pe_px, t, "PE") if (fwd and pe_px) else None
            leg_ce, leg_pe = iv, iv                       # the sheet prices both legs at the strike IV
            if mk(k, "ce_iv") is not None:
                ce_iv = leg_ce = float(mk(k, "ce_iv")); marked.append("ce_iv")
            if mk(k, "pe_iv") is not None:
                pe_iv = leg_pe = float(mk(k, "pe_iv")); marked.append("pe_iv")
            row = {"strike": k, "pe_qty": pq, "pe_px": pe_px, "pe_src": pe_src,
                   "ce_qty": cq, "ce_px": ce_px, "ce_src": ce_src, "iv": iv, "ce_iv": ce_iv, "pe_iv": pe_iv,
                   "atm": k == atm, "pos": bool(cq or pq or k in pos_strikes), "marked": marked,
                   "added": mk(k, "add") is not None}
            # Nifty!L16: IV change vs previous close (CE / PE / OTM side)
            if fwd and cl_fwd:
                cce = self._close_iv(sym, exp, k, "CE", cl_fwd, t_prev)
                cpe = self._close_iv(sym, exp, k, "PE", cl_fwd, t_prev)
                cotm = cce if k > cl_fwd else cpe
                row.update(cl_ce_iv=cce, cl_pe_iv=cpe, cl_iv=cotm,
                           ce_iv_ch=_diff(ce_iv, cce), pe_iv_ch=_diff(pe_iv, cpe), iv_ch=_diff(iv, cotm))
            gc = black.greeks(fwd, k, leg_ce, t) if (fwd and leg_ce) else None
            gp = gc if (leg_pe == leg_ce) else (black.greeks(fwd, k, leg_pe, t) if (fwd and leg_pe) else None)
            if gc or gp:
                d = (gp["put_delta"] * pq if gp else 0) + (gc["call_delta"] * cq if gc else 0)
                gm = (gp["gamma"] * pq if gp else 0) + (gc["gamma"] * cq if gc else 0)
                vg = ((gp["vega"] * pq if gp else 0) + (gc["vega"] * cq if gc else 0)) / 100
                th = ((gp["theta"] * pq if gp else 0) + (gc["theta"] * cq if gc else 0)) / 365
                row.update(delta=d, gamma=gm, vega_k=vg / 1000, theta_k=th / 1000)
                # Nifty!J64.. / J100 -> J10 "skew": sum((strike - Synthetic Future) * V(k) / 100) / 100
                tot["skew"] += (k - fwd) * (vg / 1000) / 100 / 100
                tot["delta"] += d
                tot["gamma"] += gm
                tot["vega"] += vg
                tot["theta"] += th
                for s in shocks:
                    for sign in (1, -1):
                        f2 = fwd * (1 + sign * s)
                        sk = _shock_label(sign * s)
                        if pq and pe_px is not None and gp:
                            stress[sk] += (black.price(f2, k, leg_pe, t, "PE") - pe_px) * pq
                        if cq and ce_px is not None and gc:
                            stress[sk] += (black.price(f2, k, leg_ce, t, "CE") - ce_px) * cq
            tot["gross"] += (pq * (pe_px or 0)) + (cq * (ce_px or 0))
            tot["net_qty"] += pq + cq
            rows.append(row)

        # futures leg (row 52: 'F')
        fut_q = qty(None, "FUT")
        fut_px, fut_src = self.price(sym, exp, None, "FUT")
        tot["delta"] += fut_q
        if fwd and fut_q:
            for s in shocks:
                for sign in (1, -1):
                    stress[_shock_label(sign * s)] += fut_q * fwd * sign * s

        # P&L attribution vs previous close (PrevQty BB:BE) + hedge on today's trades
        for (s_, e_, k, kind), p in self.prev.items():
            if s_ != sym or e_ != exp or not p.net_qty:
                continue
            cur, _ = self.price(sym, exp, k, kind)
            prev_px = self.close(sym, exp, k, kind) or p.close
            if cur is None or prev_px is None:
                continue
            L = p.net_qty
            if kind == "FUT":
                pnl["delta_gl"] += (cur - prev_px) * L
                pnl["ftd"] += (cur - prev_px) * L
                continue
            pf = cl_fwd or fwd
            if not (pf and fwd):
                pnl["ftd"] += (cur - prev_px) * L
                continue
            raw = black.implied_vol(pf, k, prev_px, t_prev, kind)
            if raw is None or raw > 7:
                # no previous-close IV (e.g. deep ITM below intrinsic): the sheet falls back to
                # 0.01, which only splits the move into equal and opposite theta/delta - book it as delta
                pnl["delta_gl"] += (cur - prev_px) * L
                pnl["ftd"] += (cur - prev_px) * L
                continue
            iv_prev = raw
            iv_cur = _iv_or(fwd, k, cur, t_now, kind)
            ay = black.price(pf, k, iv_prev, t_now, kind)
            az = black.price(pf, k, iv_cur, t_now, kind)
            pnl["theta_gl"] += (ay - prev_px) * L
            pnl["vega_gl"] += (az - ay) * L
            pnl["delta_gl"] += (cur - az) * L
            pnl["ftd"] += (cur - prev_px) * L
        for (s_, e_, k, kind), tr in self.trades.items():
            if s_ != sym or e_ != exp:
                continue
            cur, _ = self.price(sym, exp, k, kind)
            if cur is None:
                continue
            pnl["hedge_gl"] += (cur - tr.buy_avg) * tr.buy_qty - (cur - tr.sell_avg) * tr.sell_qty
        pnl["ftd"] += pnl["hedge_gl"]

        return {"expiry": exp, "days": days, "t_days": round(t * 365, 4), "monthly": self.has_future(sym, exp),
                "in_feed": self.in_feed(sym, exp),
                "fwd": fwd, "fwd_src": src, "cl_fwd": cl_fwd, "fut_qty": fut_q, "fut_px": fut_px, "fut_src": fut_src,
                "totals": tot, "pnl": pnl, "stress": stress, "rows": rows,
                "marks": sum(1 for c in marks if not c.endswith("|add")),
                "added": sum(1 for c in marks if c.endswith("|add"))}

    def _board_row(self, sym, r, days, a1, preopen, first, near_fut, prev_date, today):
        """FB columns D..T: roll, ATM CE/PE (best bid, field '4'), IV, straddle, close values."""
        exp = r.expiry
        out = {"expiry": exp, "row": r.row, "monthly": self.has_future(sym, exp), "in_feed": self.in_feed(sym, exp),
               "fwd": r.value, "fwd_src": r.src, "live": r.live, "cl_fwd": r.close,
               "roll": (r.value - first) if (r.value and first) else None, "d": dict(r.detail)}
        gap = r.gap or 100
        if r.value:
            atm = mround(r.value, gap)
            ce = self.live(sym, exp, atm, "CE").get("bid")
            pe = self.live(sym, exp, atm, "PE").get("bid")
            ce, pe = (ce if _pos(ce) else None), (pe if _pos(pe) else None)
            t = max((days + a1) / 365.0, 1e-5)
            civ = black.implied_vol(r.value, atm, ce, t, "CE") if ce else None
            piv = black.implied_vol(r.value, atm, pe, t, "PE") if pe else None
            out.update(atm=atm, ce=ce, pe=pe, ce_iv=civ, pe_iv=piv, t_iv=t * 365,
                       iv=_pick_iv(civ, piv, r.value, near_fut),
                       straddle=(ce + pe) if (ce and pe) else None)
        if r.close:
            k = mround(r.close, gap)
            c, p = self.close(sym, exp, k, "CE"), self.close(sym, exp, k, "PE")
            if r.row == "L" or not prev_date:
                tc = max(((date.fromisoformat(exp) - today).days + 1) / 365.0, 1e-5)
            else:
                tc = max((date.fromisoformat(exp) - prev_date).days / 365.0, 1e-5)
            civ = black.implied_vol(r.close, k, c, tc, "CE") if c else None
            piv = black.implied_vol(r.close, k, p, tc, "PE") if p else None
            out.update(cl_atm=k, cl_ce=c, cl_pe=p, cl_ce_iv=civ, cl_pe_iv=piv, t_cl=tc * 365,
                       cl_iv=_pick_iv(civ, piv, r.close, near_fut),
                       cl_straddle=(c + p) if (c and p) else None)
        if preopen or out.get("iv") is None:          # FB!I5/J5: before 09:15 show the close values
            out["iv"], out["straddle"] = out.get("cl_iv"), out.get("cl_straddle")
        if out.get("iv") is not None and out.get("cl_iv") is not None:
            out["iv_ch"] = out["iv"] - out["cl_iv"]
        if out.get("straddle") is not None and out.get("cl_straddle") is not None:
            out["straddle_ch"] = out["straddle"] - out["cl_straddle"]
        return out


def _diff(a, b):
    return a - b if (a is not None and b is not None) else None


def _pick_iv(civ, piv, fwd, near_fut):
    """FB!I5 / S5: average of CE and PE IV unless they differ by >7 %; then the CE IV when the
    synthetic is above the near future (C3), else the PE IV."""
    if civ is None or piv is None:
        return civ if piv is None else piv
    if max(civ, piv) > 1.07 * min(civ, piv):
        return civ if fwd > (near_fut or 0) else piv
    avg = (civ + piv) / 2
    return None if avg > 100 else avg


def _iv_or(f, k, px, t, kind):
    """PrevQty AS/AW: IV, or 0.01 when it fails or exceeds 7."""
    iv = black.implied_vol(f, k, px, t, kind)
    return 0.01 if (iv is None or iv > 7) else iv


def _shock_label(x):
    return f"{x * 100:+g}%"


def _shock_keys(shocks):
    return [_shock_label(s) for s in shocks] + [_shock_label(-s) for s in shocks]


def _zero_totals(shocks):
    return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "gross": 0.0, "net_qty": 0.0, "skew": 0.0, "skew_t": 0.0,
            "ftd": 0.0, "theta_gl": 0.0, "vega_gl": 0.0, "delta_gl": 0.0, "hedge_gl": 0.0,
            "stress": {k: 0.0 for k in _shock_keys(shocks)}}


def _add_totals(dst, totals, pnl, stress):
    for k in ("delta", "gamma", "vega", "theta", "gross", "net_qty", "skew", "skew_t"):
        dst[k] += totals.get(k, 0.0)
    for k in ("ftd", "theta_gl", "vega_gl", "delta_gl", "hedge_gl"):
        dst[k] += pnl.get(k, 0.0)
    for k, v in stress.items():
        dst["stress"][k] = dst["stress"].get(k, 0.0) + v
