"""Box-spread Borrow / Lend grids and top pairs (BorrowLend.xlsx).

Sheets ported:
  Borrow / BorroB   NIFTY / BANKNIFTY borrow grids: row strike K1 (lower) x column strike K2 (higher)
  Lend   / LendB    NIFTY / BANKNIFTY lend grids
  Borrow Top Pairs  lowest borrow cost pairs per expiry + overall, inside Future +/- Range %
  Lend Top Pairs    highest lend yield pairs per expiry + overall
  Sheet2            best (min borrow / max lend) per expiry

Prices are the MarketWatch best bid ("Buy Price", MW!L) and best ask ("Sell Price", MW!O).

Borrow cell (Borrow!H2):
  K1 + CE1_bid*STT - PE1_ask*PEf - (K2 + CE2_ask*CEf - PE2_bid*STT)
  ---------------------------------------------------------------- * 365 / days * 100
                              K1 - K2
Lend cell (Lend!H2):
  -(K1 + CE1_ask*CEf - PE1_bid*STT) + (K2 + CE2_bid*STT - PE2_ask*PEf)
  -------------------------------------------------------------------- * 365 / days * 100
                              K2 - K1
CEf / PEf = 1 when "squaring off" the short leg is Y, else the buy cost multiplier (1.00126).
A cell is blank when any of its four prices is 0 / missing, or K1 >= K2.
"""
from __future__ import annotations

import math
from datetime import date

DEFAULT_PARAMS = {
    "top_n": 20,             # Top N (max 20)
    "input_borrow": 11.0,    # Input Borrow (% p.a.)
    "input_lend": 11.0,      # Input Lend (% p.a.)
    "range": 0.2,            # Range: both strikes within Future * (1 +/- range)
    "multiplier": 1.00126,   # Buy cost multiplier
    "stt": 0.9975,           # STT on sell legs (fixed)
    "step": 500,             # strike step of the grid
    "each_side": 15,         # strikes each side of MROUND(near future, step): 31 x 31 grid
}
FIXED_PARAMS = ("multiplier", "stt")   # shown, not editable
# expiries / squaring-off flags as they were in the workbook
DEFAULT_SHEETS = {
    "borrow": {
        "NIFTY": {"expiries": ["2027-03-30", "2027-06-29", "2026-12-29"], "sq_ce": "N", "sq_pe": "N"},
        "BANKNIFTY": {"expiries": ["2026-11-23", "2026-12-29"], "sq_ce": "N", "sq_pe": "N"},
    },
    "lend": {
        "NIFTY": {"expiries": ["2027-03-30", "2026-12-29"], "sq_ce": "N", "sq_pe": "N"},
        "BANKNIFTY": {"expiries": ["2026-10-27", "2026-12-29"], "sq_ce": "Y", "sq_pe": "Y"},
    },
}
SLOTS = 3


def _ok(x):
    return x is not None and x != 0


def mround(x, m):
    """Excel MROUND (halves round away from zero)."""
    return math.floor(x / m + 0.5) * m if m else x


def grid_strikes(center, step, each_side):
    return [center + i * step for i in range(-each_side, each_side + 1)]


def borrow_cell(k1, k2, ce1, pe1, ce2, pe2, days, cef, pef, stt):
    """Borrow!H2. ce1 = CE bid @K1, pe1 = PE ask @K1, ce2 = CE ask @K2, pe2 = PE bid @K2."""
    if not (_ok(ce1) and _ok(pe1) and _ok(ce2) and _ok(pe2)) or k1 >= k2 or days <= 0:
        return None
    return ((k1 + ce1 * stt - pe1 * pef - (k2 + ce2 * cef - pe2 * stt)) / (k1 - k2)) * 365 / days * 100


def lend_cell(k1, k2, ce1, pe1, ce2, pe2, days, cef, pef, stt):
    """Lend!H2. ce1 = CE ask @K1, pe1 = PE bid @K1, ce2 = CE bid @K2, pe2 = PE ask @K2."""
    if not (_ok(ce1) and _ok(pe1) and _ok(ce2) and _ok(pe2)) or k1 >= k2 or days <= 0:
        return None
    return ((-(k1 + ce1 * cef - pe1 * stt) + (k2 + ce2 * stt - pe2 * pef)) / (-k1 + k2)) * 365 / days * 100


def build_grid(kind, strikes, quote, days, params, sq_ce, sq_pe):
    """quote(strike, 'CE'|'PE', 'bid'|'ask') -> price or None. Returns the grid and the legs used."""
    mult, stt = params["multiplier"], params["stt"]
    cef = 1.0 if str(sq_ce).upper() == "Y" else mult      # D30
    pef = 1.0 if str(sq_pe).upper() == "Y" else mult      # D31
    if kind == "borrow":       # row legs: CE bid, PE ask; column legs: CE ask, PE bid
        row_legs = {k: (quote(k, "CE", "bid"), quote(k, "PE", "ask")) for k in strikes}
        col_legs = {k: (quote(k, "CE", "ask"), quote(k, "PE", "bid")) for k in strikes}
        cell = borrow_cell
    else:                      # lend: row CE ask, PE bid; column CE bid, PE ask
        row_legs = {k: (quote(k, "CE", "ask"), quote(k, "PE", "bid")) for k in strikes}
        col_legs = {k: (quote(k, "CE", "bid"), quote(k, "PE", "ask")) for k in strikes}
        cell = lend_cell
    grid = []
    for k1 in strikes:
        ce1, pe1 = row_legs[k1]
        grid.append([cell(k1, k2, ce1, pe1, col_legs[k2][0], col_legs[k2][1], days, cef, pef, stt) for k2 in strikes])
    return grid, row_legs, col_legs, cef, pef


def top_pairs(kind, exp, days, strikes, grid, row_legs, col_legs, cef, pef, params, lo, hi):
    """Borrow / Lend Top Pairs, one expiry: the N best cells with both strikes inside [lo, hi].
    Ties keep grid order (row by row), like AGGREGATE on the position helper."""
    stt, n = params["stt"], int(params["top_n"])
    rate_in = params["input_borrow"] if kind == "borrow" else params["input_lend"]
    cells = []
    for i, k1 in enumerate(strikes):
        if not (lo <= k1 <= hi):
            continue
        for j, k2 in enumerate(strikes):
            v = grid[i][j]
            if v is None or not (lo <= k2 <= hi):
                continue
            cells.append((v, i * len(strikes) + j, i, j))
    cells.sort(key=(lambda c: (c[0], c[1])) if kind == "borrow" else (lambda c: (-c[0], c[1])))
    out = []
    for v, pos, i, j in cells[:n]:
        k1, k2 = strikes[i], strikes[j]
        ce1, pe1 = row_legs[k1]
        ce2, pe2 = col_legs[k2]
        width = k2 - k1
        diff = width - (ce1 - pe1 - ce2 + pe2)                                   # H
        if kind == "borrow":
            cost = ce1 * (1 - stt) + pe1 * (pef - 1) + ce2 * (cef - 1) + pe2 * (1 - stt)     # I
            at_input = width * rate_in / 100 * days / 365 - cost                              # J
        else:
            cost = ce1 * (cef - 1) + pe1 * (1 - stt) + ce2 * (1 - stt) + pe2 * (pef - 1)
            at_input = width * rate_in / 100 * days / 365 + cost
        out.append({"expiry": exp, "days": days, "k1": k1, "k2": k2, "width": width, "rate": v,
                    "diff": diff, "cost": cost, "at_input": at_input,
                    "ce1": ce1, "pe1": pe1, "ce2": ce2, "pe2": pe2})
    return out


def compute(kind, sym, expiries, today: date, center_price, bound_price, quote_for, params, sq_ce, sq_pe):
    """One sheet (Borrow / Lend for one index). quote_for(exp) -> quote(strike, kind, side)."""
    step, each = params["step"], int(params["each_side"])
    lo, hi = (bound_price * (1 - params["range"]), bound_price * (1 + params["range"])) if bound_price else (None, None)
    blocks = []
    for exp in expiries:
        if not exp:
            continue
        days = (date.fromisoformat(exp) - today).days
        if not center_price:
            blocks.append({"expiry": exp, "days": days, "strikes": [], "grid": [], "top": [], "best": None})
            continue
        strikes = grid_strikes(mround(center_price, step), step, each)
        grid, row_legs, col_legs, cef, pef = build_grid(kind, strikes, quote_for(exp), days, params, sq_ce, sq_pe)
        vals = [v for row in grid for v in row if v is not None]
        best = (min(vals) if kind == "borrow" else max(vals)) if vals else None       # Borrow!H33 / Lend!M34
        top = top_pairs(kind, exp, days, strikes, grid, row_legs, col_legs, cef, pef, params, lo, hi) if lo else []
        blocks.append({"expiry": exp, "days": days, "strikes": strikes, "grid": grid, "best": best, "top": top,
                       "row_legs": [row_legs[k] for k in strikes], "col_legs": [col_legs[k] for k in strikes]})
    # overall: SMALL / LARGE over the per-expiry tables (expiry order breaks ties)
    allp = [(p["rate"], bi, ri, p) for bi, b in enumerate(blocks) for ri, p in enumerate(b["top"])]
    allp.sort(key=(lambda x: (x[0], x[1], x[2])) if kind == "borrow" else (lambda x: (-x[0], x[1], x[2])))
    overall = [x[3] for x in allp[: int(params["top_n"])]]
    return {"kind": kind, "sym": sym, "center": mround(center_price, step) if center_price else None,
            "future": bound_price, "lo": lo, "hi": hi, "blocks": blocks, "overall": overall,
            "sq_ce": sq_ce, "sq_pe": sq_pe}
