"""Synthetic Future per expiry, replicating the FB sheet (and Sensex!F51) of WhiteGammaTemp.xlsm.

Excel lookup order (Nifty!F52 = VLOOKUP(expiry, FB!B5:T20, 2)): the first matching row wins,
so an expiry is priced by its monthly row (M) if it has one, else its weekly row (W),
else its far row (L).

NIFTY
  C3      near future LTP (first monthly)
  M row i U = MROUND(anchor,500) + CE - PE (LTP)   anchor: C3 for i=0, previous M row's C after
          C = MROUND(U, gap_i) + CE - PE (LTP)                                gaps 50 / 100 / 500
          close N: V0 = fut close; V_i = MROUND(V_{i-1}, gap_i) + CEc - PEc; N = MROUND(V, gap) + CEc - PEc
  W rows  (first 4 expiries excluding the 2nd monthly)
          C = K* + midCE - midPE, K* = strike with the smallest (CE ask-bid)+(PE ask-bid) among
              MROUND(C5,500)-500, +500, ... +2500 (6 strikes, all four quotes > 0)
          close N: V = MROUND(V0, gap) + CEc - PEc; N = MROUND(V, gap) + CEc - PEc   gaps 50/100/100/500
  L rows  (expiries after the 2nd monthly) - two-strike interpolation with carry
          U0 = MROUND(C(2nd M), gap) + CE - PE; U_i = K_minspread + CE - PE (strikes 0.7-1.3 x C(L0))
          X = listed strike <= U, Y = listed strike >= U (row 0: FLOOR/CEILING to 500)
          Z  = X + (CE mid(X) + |X-U| W d/365) - PE mid(X)
          AA = Y + CE mid(Y) - (PE mid(Y) + |Y-U| W d/365)       (mid used only if spread < 100)
          C  = (|X-Z| AA + |Y-AA| Z) / (|X-Z| + |Y-AA|)
          close N: same on bhavcopy closes around V_i = MROUND(N_{i-1}, gap_i) + CEc - PEc
          (AC/AD are the bhavcopy strikes strictly below/above V)
BANKNIFTY
  M rows  like NIFTY with gaps 100 / 500 / 1000, U anchored on the near future (C23) for every row
  L rows  smallest-spread strike among X25 = MROUND(MROUND(C25,500),1500), +1500 ... (5 strikes)
SENSEX (Sensex!F51, FB is not used by the Sensex sheet)
  expiry with a future -> future LTP; otherwise the strike with the smallest |CE LTP - PE LTP|:
  K + CE - PE.

Before 09:15 (FB!A1 = 1) every row uses its close value N. When the live inputs are missing
(Excel would show 0 or #N/A) the row also falls back to N and says so in `src`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

DEFAULTS = {
    "NIFTY": {
        "kind": "fb",
        "rough": 500,
        "monthly_count": 2,
        "monthly_gaps": [50, 100, 500],
        "monthly_anchor": "prev",
        "weekly_count": 4,
        "weekly_gaps": [50, 100, 100, 500],
        "spread_base_round": 500, "spread_step": 500, "spread_below": 1, "spread_count": 6,
        "far_method": "interp",
        "far_gaps": [500, 1500, 3000, 1500, 3000, 1500, 1500, 1500],
        "far_interest": [0.12, 0.11, 0.10, 0.12, 0.12, 0.06, 0.12, 0.12],
        "far_first_round": 500,
        "far_spread_limit": 100,
        # cells typed over formulas in FB (kept so the numbers match the workbook)
        "overrides": {"2027-12-28": {"close_ceil_strike": 26000}, "2028-12-26": {"close": 26500}},
    },
    "BANKNIFTY": {
        "kind": "fb",
        "rough": 500,
        "monthly_count": 2,
        "monthly_gaps": [100, 500, 1000],
        "monthly_anchor": "future",
        "weekly_count": 0,
        "weekly_gaps": [],
        "far_method": "min_spread",
        "far_gaps": [1000, 1500, 1000, 1000, 1000, 1000, 1000, 1000],
        "spread_base_round": 500, "spread_grid": 1500, "spread_step": 1500, "spread_below": 0, "spread_count": 5,
    },
    "SENSEX": {"kind": "sensex_sheet", "gap": 100},
}


def mround(x, m):
    return math.floor(x / m + 0.5) * m


def floor_to(x, m):
    return math.floor(x / m + 1e-9) * m


def ceil_to(x, m):
    return math.ceil(x / m - 1e-9) * m


def pos(x):
    return x is not None and x > 0


@dataclass
class SynRow:
    expiry: str
    row: str            # 'M', 'W', 'L' or 'S' (Sensex sheet rule)
    gap: float
    live: float | None = None
    close: float | None = None
    value: float | None = None
    src: str = ""
    strike: float | None = None
    detail: dict = field(default_factory=dict)


class SyntheticModel:
    """Computes FB rows for one index. `px` gives access to quotes and bhavcopy closes.

    Every row records the inputs it used in `detail` (shown as optional columns in the
    Synthetic Future & IV tab):
      live : near_fut (C3), anchor, u_k (strike for U), U, u_ce, u_pe, K, ce, pe,
             ce_bid, ce_ask, pe_bid, pe_ask, spread, cand_from, X, Y, Z, AA
      close: near_fut_cl, V, v_k, K_cl, ce_cl, pe_cl, AC, AD, AE, AF, override
      params: gap, rough, interest, days
    """

    def __init__(self, sym, cfg, px):
        self.sym = sym
        self.c = {**DEFAULTS.get(sym, DEFAULTS["NIFTY"]), **(cfg or {})}
        self.px = px

    # ------------------------------------------------------------ price helpers
    def ltp(self, exp, k, kind):
        q = self.px.live(self.sym, exp, k, kind)
        return q.get("ltp") if pos(q.get("ltp")) else None

    def ba(self, exp, k, kind):
        q = self.px.live(self.sym, exp, k, kind)
        b, a = q.get("bid"), q.get("ask")
        return (b, a) if (pos(b) and pos(a)) else (None, None)

    def cl(self, exp, k, kind):
        return self.px.close(self.sym, exp, k, kind)

    def parity_ltp(self, exp, k, d=None, pre=""):
        c, p = self.ltp(exp, k, "CE"), self.ltp(exp, k, "PE")
        if d is not None:
            d.update({pre + "ce": c, pre + "pe": p})
        return k + c - p if (c is not None and p is not None) else None

    def parity_close(self, exp, k, d=None, pre=""):
        c, p = self.cl(exp, k, "CE"), self.cl(exp, k, "PE")
        if d is not None:
            d.update({pre + "ce_cl": c, pre + "pe_cl": p})
        return k + c - p if (c is not None and p is not None) else None

    # ------------------------------------------------------------ main
    def compute(self, expiries, futures, today: date, preopen: bool, pos_strikes=None, listed=None):
        """expiries: all listed expiries >= today (sorted); futures: those with a future."""
        if self.c["kind"] == "sensex_sheet":
            return self._sensex(expiries, futures, today, preopen, pos_strikes or {}, listed or {})
        rows: dict[str, SynRow] = {}
        months = [e for e in expiries if e in futures][: self.c["monthly_count"]]
        m2 = months[1] if len(months) > 1 else (months[0] if months else None)
        weeklies = [e for e in expiries if e != m2][: self.c["weekly_count"]]
        far = [e for e in expiries if m2 and e > m2]
        near_fut_ltp = self.ltp(months[0], None, "FUT") if months else None
        near_fut_cl = self.cl(months[0], None, "FUT") if months else None
        rough = self.c["rough"]

        def new(e, kind, gap):
            r = SynRow(e, kind, gap)
            r.detail = {"gap": gap, "near_fut": near_fut_ltp, "near_fut_cl": near_fut_cl,
                        "days": (date.fromisoformat(e) - today).days}
            return r

        # M rows: U = MROUND(anchor, 500) + CE - PE ; C = MROUND(U, gap) + CE - PE
        prev_c = prev_v = None
        for i, e in enumerate(months):
            gap = self.c["monthly_gaps"][min(i, len(self.c["monthly_gaps"]) - 1)]
            r = new(e, "M", gap)
            d = r.detail
            d["rough"] = rough
            # close chain (N): V0 = fut close; V_i = MROUND(V_{i-1}, gap) + CEc - PEc
            if i == 0:
                v = near_fut_cl
            else:
                d["v_k"] = mround(prev_v, gap) if prev_v else None
                v = self.parity_close(e, d["v_k"], d, "v_") if prev_v else None
            d["V"] = v
            if v:
                d["K_cl"] = mround(v, gap)
                r.close = self.parity_close(e, d["K_cl"], d)
            prev_v = v
            # live (C)
            anchor = near_fut_ltp if (i == 0 or self.c["monthly_anchor"] == "future") else prev_c
            d["anchor"] = anchor
            if anchor:
                d["u_k"] = mround(anchor, rough)
                u = self.parity_ltp(e, d["u_k"], d, "u_")
                d["U"] = u
                if u:
                    r.strike = d["K"] = mround(u, gap)
                    r.live = self.parity_ltp(e, r.strike, d)
            self._finish(r, preopen, "M parity")
            prev_c = r.value
            rows[e] = r

        # W rows (NIFTY): smallest bid/ask spread on the 500 grid around C of the 1st monthly
        c5 = rows[months[0]].value if months else None
        for i, e in enumerate(weeklies):
            if e in rows:
                continue  # priced by its monthly row (VLOOKUP finds that first)
            gap = self.c["weekly_gaps"][min(i, len(self.c["weekly_gaps"]) - 1)]
            r = new(e, "W", gap)
            d = r.detail
            if near_fut_cl:
                d["v_k"] = mround(near_fut_cl, gap)
                v = d["V"] = self.parity_close(e, d["v_k"], d, "v_")
                if v:
                    d["K_cl"] = mround(v, gap)
                    r.close = self.parity_close(e, d["K_cl"], d)
            if c5:
                d["anchor"] = c5
                base = mround(mround(c5, self.c["spread_base_round"]), self.c["spread_step"]) \
                    - self.c["spread_below"] * self.c["spread_step"]
                r.live, r.strike = self._min_spread(e, base, d)
                if r.live is None and near_fut_ltp:   # FB row 8 method when no two-sided quotes
                    d["rough"] = rough
                    d["u_k"] = mround(near_fut_ltp, rough)
                    u = d["U"] = self.parity_ltp(e, d["u_k"], d, "u_")
                    if u:
                        r.strike = d["K"] = mround(u, gap)
                        r.live = self.parity_ltp(e, r.strike, d)
            self._finish(r, preopen, "W min-spread" if d.get("spread") is not None else "W parity")
            rows[e] = r

        # L rows
        prev_n = rows[m2].close if m2 in rows else None
        prev_live = rows[m2].value if m2 in rows else None
        l0_live = None
        for i, e in enumerate(far):
            if e in rows:
                continue
            gap = self.c["far_gaps"][min(i, len(self.c["far_gaps"]) - 1)]
            r = new(e, "L", gap)
            d = r.detail
            days = d["days"]
            if self.c["far_method"] == "interp":
                w = self.c["far_interest"][min(i, len(self.c["far_interest"]) - 1)]
                w = d["interest"] = float((self.c.get("far_interest_by_expiry") or {}).get(e, w))
                first_round = self.c["far_first_round"] if i == 0 else None
                if prev_n:
                    r.close = self._interp_close(e, prev_n, gap, w, days, first_round, d)
                if i == 0:
                    d["anchor"] = prev_live
                    d["u_k"] = mround(prev_live, gap) if prev_live else None
                    u = self.parity_ltp(e, d["u_k"], d, "u_") if prev_live else None
                else:
                    u = None
                    ref = l0_live or prev_live
                    d["anchor"] = ref
                    if ref:
                        k, sp = self._min_positive_spread(e, ref * 0.7, ref * 1.3)
                        d["u_k"], d["spread"] = k, sp
                        u = self.parity_ltp(e, k, d, "u_") if k is not None else None
                d["U"] = u
                if u:
                    r.live = self._interp_live(e, u, w, days, first_round, d)
                if i == 0:
                    l0_live = r.live
                prev_n = r.close or prev_n
            else:  # BANKNIFTY: smallest spread on a coarse grid around C of the 1st monthly
                c25 = rows[months[0]].value if months else None
                cl25 = rows[months[0]].close if months else None
                d["anchor"] = c25
                if c25:
                    base = mround(mround(c25, self.c["spread_base_round"]), self.c["spread_grid"]) \
                        - self.c["spread_below"] * self.c["spread_step"]
                    r.live, r.strike = self._min_spread(e, base, d)
                if cl25:
                    d["K_cl"] = mround(mround(cl25, self.c["spread_base_round"]), self.c["spread_grid"])
                    r.close = self.parity_close(e, d["K_cl"], d)
            self._finish(r, preopen, "L interp" if self.c["far_method"] == "interp" else "L min-spread")
            rows[e] = r
        for e in expiries:   # anything the sheet has no row for: nearest-strike parity
            if e not in rows:
                rows[e] = self._fallback(new(e, "?", 100), c5 or near_fut_ltp, near_fut_cl, preopen)
        return rows

    def _finish(self, r, preopen, label):
        if preopen or r.live is None:
            r.value, r.src = r.close, ("close" if r.close is not None else "")
        else:
            r.value, r.src = r.live, label

    def _min_spread(self, e, base, d):
        d["cand_from"] = base
        d["cand_to"] = base + (self.c["spread_count"] - 1) * self.c["spread_step"]
        best = None
        for j in range(self.c["spread_count"]):
            k = base + j * self.c["spread_step"]
            cb, ca = self.ba(e, k, "CE")
            pb, pa = self.ba(e, k, "PE")
            if cb is None or pb is None:
                continue
            spread = (ca - cb) + (pa - pb)
            if best is None or spread < best[0]:
                best = (spread, k, cb, ca, pb, pa)
        if best is None:
            return None, None
        spread, k, cb, ca, pb, pa = best
        d.update(spread=spread, K=k, ce_bid=cb, ce_ask=ca, pe_bid=pb, pe_ask=pa,
                 ce=(cb + ca) / 2, pe=(pb + pa) / 2)
        return k + (cb + ca) / 2 - (pb + pa) / 2, k

    def _min_positive_spread(self, e, lo, hi):
        best = None
        for k in self.px.listed(self.sym, e):
            if not lo <= k <= hi:
                continue
            cb, ca = self.ba(e, k, "CE")
            pb, pa = self.ba(e, k, "PE")
            if cb is None or pb is None:
                continue
            s = (ca - cb) + (pa - pb)
            if s > 0 and (best is None or s < best[0]):
                best = (s, k)
        return (best[1], best[0]) if best else (None, None)

    def _interp_live(self, e, u, w, days, first_round, d):
        listed = self.px.listed(self.sym, e)
        below = [k for k in listed if k <= u]
        above = [k for k in listed if k >= u]
        if not below or not above:
            return None
        x, y = below[-1], above[0]
        if first_round:
            x, y = floor_to(x, first_round), ceil_to(y, first_round)
        carry = w * days / 365

        def mid(k, kind):
            b, a = self.ba(e, k, kind)
            return (b, a, (b + a) / 2) if b is not None else (0.0, 0.0, 0.0)

        cxb, cxa, cxm = mid(x, "CE")
        pxb, pxa, pxm = mid(x, "PE")
        cyb, cya, cym = mid(y, "CE")
        pyb, pya, pym = mid(y, "PE")
        lim = self.c.get("far_spread_limit", 100)
        ah = max(abs(cxb - cxa), abs(pxb - pxa))
        am = max(abs(cyb - cya), abs(pyb - pya))
        as_ = (cxm + abs(x - u) * carry) if ah < lim and cxm else 0.0
        at = pxm if ah < lim else 0.0
        au = cym if am < lim else 0.0
        av = (pym + abs(y - u) * carry) if am < lim and pym else 0.0
        d.update(X=x, Y=y, ce_x=as_, pe_x=at, ce_y=au, pe_y=av)
        if not (as_ and at) and not (au and av):
            return None
        z = (y + au - av) if (as_ == 0 or at == 0) else (x + as_ - at)
        aa = (x + as_ - at) if (au == 0 or av == 0) else (y + au - av)
        den = abs(x - z) + abs(y - aa)
        d.update(Z=z, AA=aa)
        return (abs(x - z) * aa + abs(y - aa) * z) / den if den else (z + aa) / 2

    def _interp_close(self, e, prev_n, gap, w, days, first_round, d):
        ov = (self.c.get("overrides") or {}).get(e, {})
        if ov:
            d["override"] = ", ".join(f"{k}={v}" for k, v in ov.items())
        if "close" in ov:                       # e.g. FB!N19 typed in as 26500
            return float(ov["close"])
        d["v_k"] = mround(prev_n, gap)
        v = d["V"] = self.parity_close(e, d["v_k"], d, "v_")
        if v is None:
            return None
        strikes = sorted({k for k in self.px.close_strikes(self.sym, e)})
        lo = [k for k in strikes if k < v]
        hi = [k for k in strikes if k > v]
        if not lo or not hi:
            return None
        ac, ad = lo[-1], hi[0]
        if first_round:
            ac, ad = floor_to(ac, first_round), ceil_to(ad, first_round)
        ac = float(ov.get("close_floor_strike", ac))   # e.g. FB!AD17 typed in as 26000
        ad = float(ov.get("close_ceil_strike", ad))
        carry = w * days / 365
        cac, pac = self.cl(e, ac, "CE"), self.cl(e, ac, "PE")
        cad, pad = self.cl(e, ad, "CE"), self.cl(e, ad, "PE")
        if None in (cac, pac, cad, pad):
            return None
        ax = cac + abs(ac - v) * carry
        ba_ = pad + abs(ad - v) * carry
        ae = ac + ax - pac
        af = ad + cad - ba_
        d.update(AC=ac, AD=ad, AE=ae, AF=af)
        den = abs(ac - ae) + abs(ad - af)
        return (abs(ac - ae) * af + abs(ad - af) * ae) / den if den else (ae + af) / 2

    def _fallback(self, r, anchor, anchor_cl, preopen):
        e, d = r.expiry, r.detail
        listed = self.px.listed(self.sym, e)
        d["anchor"] = anchor
        if anchor and listed:
            for k in sorted(listed, key=lambda s: abs(s - anchor))[:3]:
                r.live = self.parity_ltp(e, k, d)
                if r.live:
                    r.strike = d["K"] = k
                    break
        if anchor_cl:
            for k in sorted(self.px.close_strikes(self.sym, e), key=lambda s: abs(s - anchor_cl))[:3]:
                r.close = self.parity_close(e, k, d)
                if r.close:
                    d["K_cl"] = k
                    break
        self._finish(r, preopen, "parity")
        return r

    # ------------------------------------------------------------ SENSEX (Sensex!F51)
    def _sensex(self, expiries, futures, today, preopen, pos_strikes, listed):
        rows = {}
        for e in expiries:
            r = SynRow(e, "S", self.c.get("gap", 100))
            d = r.detail = {"gap": r.gap, "days": (date.fromisoformat(e) - today).days}
            if e in futures:
                r.live = d["fut"] = self.ltp(e, None, "FUT")
                r.close = d["fut_cl"] = self.cl(e, None, "FUT")
                label = "future"
            else:
                strikes = sorted(set(pos_strikes.get(e, ())) | set(listed.get(e, ())))
                best = None
                for k in strikes:
                    c, p = self.ltp(e, k, "CE"), self.ltp(e, k, "PE")
                    if c is not None and p is not None and (best is None or abs(c - p) < best[0]):
                        best = (abs(c - p), k, c, p)
                if best:
                    _, k, c, p = best
                    r.live, r.strike = k + c - p, k
                    d.update(K=k, ce=c, pe=p, spread=abs(c - p))
                bestc = None
                for k in strikes:            # same table strikes, on previous closes
                    c, p = self.cl(e, k, "CE"), self.cl(e, k, "PE")
                    if c is not None and p is not None and (bestc is None or abs(c - p) < bestc[0]):
                        bestc = (abs(c - p), k, c, p)
                if bestc:
                    _, k, c, p = bestc
                    r.close = k + c - p
                    d.update(K_cl=k, ce_cl=c, pe_cl=p)
                label = "min |CE-PE|"
            self._finish(r, preopen, label)
            rows[e] = r
        return rows
