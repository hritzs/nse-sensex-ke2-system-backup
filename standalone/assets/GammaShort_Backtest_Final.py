r"""
GAMMA SHORT - FINAL BACKTEST (single file)
==========================================
One script, one pass over the tick data, the full production book with every
rule settled up to round 52 (2026-10-09). Structure follows 7DResume.py:
config -> loaders -> kernels -> per-day worker (parallel, resumable) -> sweep
summary + daily log.

WHAT IS SIMULATED, PER DAY
--------------------------
CURRENT-WEEK (CW) LEG
  1-second grid      underlying = first print of each second; seconds with no
                     underlying are dropped (never filled).
  Option prices      every strike's CE/PE LTP, bid, ask = last print of the
                     second, forward-filled. NO price is ever rebuilt from IV
                     (round 52: the old BS(S, K, OTM-IV) reference straddle is
                     gone). Missing bid/ask fall back to the strike's own LTP.
  IV                 solved from LTP (BS, r = 0, T = DTE/365) on the OTM leg,
                     ONLY in seconds where that leg actually printed, then
                     held until the next print (IV_MODE = "tick").
  Entry check        each minute 09:16-13:30 (expiry: to 09:59) reads the last
                     second AT OR BEFORE hh:mm:00 (:59 fallback). The 09:16
                     opening gap uses the same snapshot. The fill starts at the
                     first second at/after hh:mm:00.
  Features           IV ratio (adj Build IV / blended IDV 0.2/0.5/0.3),
                     straddle ratio (yesterday straddle / ATM LTP straddle),
                     adj Build IV, opening gap (x 0.19 / yesterday Adj IV),
                     ADJ band (-0.007 / +0.005), DTE (weekend-adjusted).
  Filters            straddle veto 1:1.70 2:1.275 3:1.225 4:1.175 5:1.15
                     6:1.125; 0.12 Build-IV floor on DTE1/DTE2 (all day);
                     event days: no entry before 09:18, size 1/3.
  Tables             5 stage LUTs (09:16, :17, :18, :19, 09:20+), first hit
                     wins, one trade per day.
  Execution          sell ATM straddle at the 30-s average bid, hold the strike
                     all day (no roll). Hedge in the synthetic future every
                     second: minute start PtsOut > PtsAllowed -> 75 %; within
                     the minute PtsOut > 1.2 x PtsAllowed -> fully neutral;
                     DTE1/DTE2 use |NetDelta| > 0.25 / 0.20 instead. PtsAllowed
                     = max(min(IV/57 x U, straddle/4), 8 bps of U). Futures at
                     the 3-s average bid/ask.
  Exits (per minute) SL 16 bps (expiry) / 14 bps on entry credit - ask + futures;
                     TP ramp by Build IV and DTE; TV, TV4; 14:57 IV-drop
                     (DTE <= 1); else end of grid. Exit at the 30-s avg ask.
  Costs              entry 1.0 / 0.5 / 0.25 (DTE1 / DTE2 / rest), hedge 0.4 /
                     0.3 / 0.2 bps of hedge notional (normalised Rs).

NEXT-WEEK (NW) LEG - DTE1/DTE2 days only, one check, same LTP basis
  Check              NW_CHECK_MODE "live": at 09:21 if no CW trade by 09:20
                     (what apply_strategy.py does live); "replay": only at the
                     first scan minute after 09:20 that passes the straddle
                     veto (the 2026-09-28 production replay).
  Features           09:16 snapshot (last second <= 09:16:00): ATM = nearest
                     strike with both legs traded; NW IV from the OTM leg's
                     last real print; NW ratio / NW Build IV (weekend-adjusted)
                     / gap normalised by the NEXT-week yesterday Adj IV ->
                     NW_LUT.csv.
  Execution          sell at the 30-s average bid from 09:21:00, marks at the
                     hh:mm:00 snapshot, stop 15 bps on entry - mid + futures,
                     re-hedge to neutral when |net delta| > 0.10 (once a
                     minute), exit 15:15 at the 30-s average ask, hedge cost
                     0.2 bps of notional. Not scaled on event days.

TICK DATA
  TICK_LAYOUT "combined" (default): one file per day holding every expiry,
  \\172.16.1.85\Shared\TickDataIV\DDMMYYYY\NIFTY.parquet. The CW leg uses the
  nearest option expiry on or after the trade date, the NW leg the next one.
  Each expiry carries its own underlying (its synthetic forward), so the two
  are never mixed; futures (Type FUT) rows are dropped.
  TICK_LAYOUT "split": the research layout, D:\NIFTY_TickData (current week)
  + D:\NIFTY_NextTickData\DDMMYYYY\NIFTY_NW.feather (next week).

PnL is "normalised Rs" = index points x 25000 / entry underlying (one unit
sized 25000/U), exactly as in the research pipeline.

REFERENCE NUMBERS (1,379 days, 2021-01-01 .. 2026-08-24, DAY_INPUTS =
"backtest_files", GRID_END fixed at 15:27):
  NW_CHECK_MODE "live"   : 10,640.5 / MDD 144.4 / CW 713 + NW 71
  NW_CHECK_MODE "replay" : 10,434.7 / MDD 144.4 / CW 713 + NW 58
  NW_ENABLED False       : 10,216.7 / MDD 120.6 / CW 720

FIXED ENTRY TIMES
  For every day the script also simulates the CW trade entered at each of
  FIXED_ENTRIES (default 09:16, 09:17, 09:18) - same data, execution, exits,
  costs and event sizing as production, but WITHOUT the LUT / veto / floor /
  event-time filters - and records next to it what production's rules said
  at that minute (Signal: LUT_ON, LUT_OFF, VETO, FLOOR, EVENT_PRE_0918). Saved
  as entry_time_pnl.csv beside the production result of the same day.

USAGE
  python GammaShort_Backtest_Final.py                 # run / resume
  python GammaShort_Backtest_Final.py --only 2025-06-10,2025-06-11
  python GammaShort_Backtest_Final.py --summary       # rebuild logs from done days
Safe to stop and re-run: every finished day is saved on its own. Results live
in a folder named after a hash of the full configuration, so changing any rule
or table starts a clean set instead of mixing results.
"""
import os
os.environ['NUMBA_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'

import argparse
import concurrent.futures
import gc
import glob
import hashlib
import json
import math
import pickle
import time
import traceback
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

import numpy.polynomial.polynomial
if not hasattr(np.polynomial, 'polynomial'):
    np.polynomial.polynomial = numpy.polynomial.polynomial
import numba as nb

warnings.filterwarnings("ignore")


def _env(name, default):
    return os.environ.get(name, default)


# ============================================================
# 1. DATA LOCATIONS
# ============================================================
TICK_LAYOUT   = _env("GS_TICK_LAYOUT", "combined")   # "combined" | "split" (see header)
TICK_DIR      = _env("GS_TICK_DIR", r"\\172.16.1.85\Shared\TickDataIV")   # DDMMYYYY\NIFTY.parquet|feather
NW_TICK_DIR   = _env("GS_NW_TICK_DIR", r"D:\NIFTY_NextTickData")  # "split" only: DDMMYYYY\NIFTY_NW.feather
LUT_DIR       = _env("GS_LUT_DIR", r"D:\GammaShort\LUTS_r46V2_cwnw_adj007_005_T13_5_2_2_6")
EVENT_PATH    = _env("GS_EVENT_PATH", r"\\172.16.1.85\Shared\EventDates.xlsx")
EVENT_PATH_FALLBACK = r"D:\GammaShort\EventDates.xlsx"   # used if the share copy can't be read
OUTPUT_BASE   = _env("GS_OUTPUT_DIR", r"D:\GammaShort\backtest_final_v2")

# Day-level inputs (yesterday straddle / IV / IDVs / future / IV change / NW IV)
#   "daily_csv"      : GammaShortDailydata.csv - the live engine's source, one
#                      row per trading day (as 7DResume.py / live_day_context)
#   "backtest_files" : NiftyPrevStrIV.xlsx + nifty_idv_history.csv +
#                      NiftyPrevStrIV_NW.csv - the research pipeline's source;
#                      use this to reproduce the reference numbers exactly
DAY_INPUTS    = _env("GS_DAY_INPUTS", "daily_csv")
DAILY_CSV     = _env("GS_DAILY_CSV",
                     r"\\172.16.1.85\Shared\Hardik\Project_Codes\GammaShortDailyProcess\GammaShortDailydata.csv")
IDV_PATH      = _env("GS_IDV_PATH", r"D:\GammaShort\nifty_idv_history.csv")
PREV_STR_PATH = _env("GS_PREV_STR_PATH", r"D:\GammaShort\NiftyPrevStrIV.xlsx")
NW_PREV_PATH  = _env("GS_NW_PREV_PATH", r"D:\GammaShort\NiftyPrevStrIV_NW.csv")

START_DATE    = pd.Timestamp(_env("GS_START", "2021-01-01"))
END_DATE      = pd.Timestamp(_env("GS_END", "2100-01-01"))
SKIP_DATES    = {pd.Timestamp("2024-04-18")}
WORKERS       = int(_env("GS_WORKERS", str(min(max((os.cpu_count() or 2) - 1, 1), 30))))

# 1-second grid end. 7DResume.py runs the grid to 15:37 from 2026-06-03 (the
# share's files carry ticks to 15:40 from then). The reference numbers in the
# header came from D:\NIFTY_TickData, whose files stop at 15:30 throughout, so
# days from 2026-06-03 on can differ when run on the share's data.
GRID_END_EARLY  = (15, 27, 0)
GRID_END_LATE   = (15, 37, 0)
GRID_END_SWITCH = pd.Timestamp(_env("GS_GRID_END_SWITCH", "2026-06-03"))

# ============================================================
# 2. STRATEGY - the finalised rule set
# ============================================================
# --- data basis (round 52) ---
IV_MODE        = _env("GS_IV_MODE", "tick")   # "tick" (final) | "ltp" (IV every second, gaps interpolated)
CHECK_FALLBACK = _env("GS_CHECK_FALLBACK", "prev")  # "prev" = last tick <= hh:mm:00 (final) | "next" = first >= :00
N_OPT_SECONDS  = 30                     # option fills: 30-second average
N_FUT_SECONDS  = 3                      # futures fills: 3-second average
WKND_ADJ       = 1.0

# --- signal (bothsplit, ADJ -0.007 / +0.005) ---
WEIGHTS        = (0.2, 0.5, 0.3)        # (IDV_Pure, IDV_With_Prev, Yesterday Adj IV)
ADJ_LOWER, ADJ_HIGHER = -0.007, 0.005
IV_THRESHOLDS  = np.array([0.6501, 0.8001, 0.9001, 0.9501, 1.0001, 1.0501, 1.1001, 1.2001, 1.3001])
STR_THRESHOLDS = np.array([0.9501, 1.0001, 1.0501, 1.1001, 1.1501, 1.2001, 1.3001, 1.4001,
                           1.5001, 1.7501, 2.0001])
BLD_THRESHOLDS = np.array([0.0801, 0.0901, 0.1001, 0.1201, 0.1601, 0.2001])
OG_THRESHOLDS  = np.array([-0.7501, 0.7501])
DIMS           = (7, 10, 12, 7, 3, 3)   # DTE, IV ratio, straddle ratio, Build IV, gap, ADJ
STAGE_FILES    = ["LUT_0916.csv", "LUT_0917.csv", "LUT_0918.csv", "LUT_0919.csv",
                  "LUT_0920_Onwards.csv"]
EXPECTED_ON    = {"LUT_0916.csv": 1298, "LUT_0917.csv": 3309, "LUT_0918.csv": 4534,
                  "LUT_0919.csv": 3828, "LUT_0920_Onwards.csv": 2441}
STAGE_NAMES    = ["09:16", "09:17", "09:18", "09:19", "09:20+"]

# --- entry rules ---
ENTRY_FIRST_HHMM    = 916
ENTRY_LAST_HHMM     = 1330
EXPIRY_ENTRY_CUTOFF = 959
EVENT_FIRST_HHMM    = 918
EVENT_SIZE          = 1.0 / 3.0
VETO_STR            = {0: 1.70, 1: 1.275, 2: 1.225, 3: 1.175, 4: 1.15, 5: 1.125}   # dte_idx -> max str ratio
IV_FLOOR            = {0: 0.12, 1: 0.12}                                          # dte_idx -> min Build IV

# --- exits ---
SL_BPS_DEFAULT = 14.0
SL_BPS_BY_DTE  = {0: 16.0}
TP_HIGH        = np.array([30.0, 25.0, 25.0, 20.0, 15.0, 15.0, 15.0])
TP_LOW         = np.array([16.0, 13.0, 13.0, 10.0, 8.0, 8.0, 8.0])
TP_SCALE       = 1.0
TV_THRESH      = 0.00015
TV4_THRESH     = 0.0006
IV_DROP_THRESH = 0.1                    # at/after 14:57 (minute 897), DTE <= 1

# --- hedge ---
HEDGE_DIV      = 57
STR_D          = 4
PTS_FLOOR      = 0.0008
HEDGE_FRAC     = 0.75                   # minute-start hedge fraction
INTRA_MULT     = 1.2                    # intra-minute trigger multiple
INTRA_FRAC     = 1.00                   # intra-minute: fully neutral
DELTA_MAP      = {0: 0.25, 1: 0.20}     # dte_idx -> |NetDelta| trigger

# --- costs (half-slippage standard) ---
ENTRY_SLIP     = {0: 1.0, 1: 0.5}       # normalised Rs per trade, else 0.25
ENTRY_SLIP_DEF = 0.25
HEDGE_SLIP_BPS = {0: 0.4, 1: 0.3}       # bps of hedge notional, else 0.2
HEDGE_SLIP_DEF = 0.2

# --- next-week leg ---
NW_ENABLED     = _env("GS_NW_ENABLED", "1") == "1"
NW_CHECK_MODE  = _env("GS_NW_CHECK_MODE", "live")   # "live" | "replay" (see header)
NW_AFTER_HHMM  = 920                    # the check comes after this minute
NW_ENTRY_T     = 921
NW_EXIT_T      = 1515
NW_SL_BPS      = 15.0
NW_HEDGE_THR   = 0.10
NW_HEDGE_COST_BPS = 0.2
NW_RAT_EDGES   = [1.00, 1.20, 1.35]
NW_LVL_EDGES   = [0.12, 0.14, 0.17]
NW_OG_EDGES    = [-0.75, 0.7501]
NW_EXPECTED_ON = 23

EXIT_REASONS   = {0: "EOD", 1: "SL", 2: "TP", 3: "TV", 4: "TV4", 5: "IV_DROP"}

# --- fixed entry times recorded for every day (see header); "" disables ---
FIXED_ENTRIES  = [int(x) for x in _env("GS_FIXED_ENTRIES", "916,917,918").split(",") if x.strip()]


# ============================================================
# 3. NUMBA KERNELS (identical maths to phase1_extract / hedge_v2)
# ============================================================
@nb.njit(fastmath=True)
def _norm_cdf(x): return 0.5 * (1.0 + math.erf(x / 1.4142135623730951))


@nb.njit(fastmath=True)
def _norm_pdf(x): return 0.3989422804014327 * math.exp(-0.5 * x * x)


@nb.njit(fastmath=True)
def _bs_price_kernel(is_call, S, K, T, r, sig):
    if T <= 0 or sig <= 0: return 0.0
    sq = math.sqrt(T); d1 = (math.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * sq); d2 = d1 - sig * sq
    if is_call: return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:       return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


@nb.njit(fastmath=True)
def _iv_solver_kernel(is_call, price, S, K, T):
    if price <= 0 or T <= 0: return np.nan
    low, high = 1e-6, 5.0
    pl = _bs_price_kernel(is_call, S, K, T, 0, low); ph = _bs_price_kernel(is_call, S, K, T, 0, high)
    if pl > price or ph < price: return np.nan
    for _ in range(30):
        mid = 0.5 * (low + high); pm = _bs_price_kernel(is_call, S, K, T, 0, mid)
        if abs(pm - price) < 1e-5: return mid
        if pm < price: low = mid
        else:          high = mid
    return 0.5 * (low + high)


@nb.njit(fastmath=True)
def compute_iv_array(S_arr, K, T_arr, CE_arr, PE_arr):
    """IV per second from the OTM leg's price (CE below the strike, PE above)."""
    n = len(S_arr)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        t = max(T_arr[i], 1e-5)
        if S_arr[i] < K:
            p = CE_arr[i]
            if math.isnan(p) or p <= 0.1: out[i] = np.nan
            else: out[i] = _iv_solver_kernel(True, p, S_arr[i], K, t)
        else:
            p = PE_arr[i]
            if math.isnan(p) or p <= 0.1: out[i] = np.nan
            else: out[i] = _iv_solver_kernel(False, p, S_arr[i], K, t)
    return out


@nb.njit
def distance_weighted_iv_numba(iv_values, time_seconds):
    """IV_MODE 'ltp' only: fill gaps by time-weighting the neighbours."""
    n = len(iv_values); out = iv_values.copy(); valid = ~np.isnan(out)
    vi = np.where(valid)[0]
    if len(vi) == 0: return out
    for i in range(n):
        if valid[i]: continue
        ip = np.searchsorted(vi, i)
        if ip == 0 or ip == len(vi): continue
        p = vi[ip - 1]; nx = vi[ip]
        d1 = abs(time_seconds[i] - time_seconds[p]); d2 = abs(time_seconds[nx] - time_seconds[i])
        if d1 + d2 > 0: out[i] = (out[p] * d2 + out[nx] * d1) / (d1 + d2)
    return out


@nb.njit(cache=True)
def _valid(S, K, T, sig):
    return not (math.isnan(S) or math.isnan(K) or math.isnan(T) or math.isnan(sig)
                or T <= 0.0 or sig <= 0.0 or S <= 0.0 or K <= 0.0)


@nb.njit(cache=True)
def opt_delta_gamma_arr(S, K, T, sig):
    """Short-straddle delta (-dc-dp) and gamma (-2g). NaN-safe."""
    n = S.shape[0]
    od = np.empty(n, dtype=np.float64)
    og = np.empty(n, dtype=np.float64)
    for i in range(n):
        if not _valid(S[i], K[i], T[i], sig[i]):
            od[i] = np.nan; og[i] = np.nan
            continue
        sq = math.sqrt(T[i])
        d1 = (math.log(S[i] / K[i]) + 0.5 * sig[i] * sig[i] * T[i]) / (sig[i] * sq)
        cd = _norm_cdf(d1)
        od[i] = -cd - (cd - 1.0)
        og[i] = -2.0 * (_norm_pdf(d1) / (S[i] * sig[i] * sq))
    return od, og


@nb.njit(cache=True)
def hedge_walk(opt_delta, opt_gamma, pts_allowed, chunk_id, is_min_start,
               hedge_frac, intra_mult, intra_frac, delta_thresh):
    """Second-by-second futures hedge (hedge_v2.hedge_walk, no cutoff)."""
    n = opt_delta.shape[0]
    fut_tot = np.zeros(n, dtype=np.float64)
    fut_trade = np.zeros(n, dtype=np.float64)
    pos = 0.0
    prev_net = 0.0
    od_prev = 0.0
    cur_chunk = -1
    for i in range(n):
        od = opt_delta[i]
        og = opt_gamma[i]
        if chunk_id[i] != cur_chunk:
            cur_chunk = chunk_id[i]
            if i == 0:
                pos = -od
            else:
                prev_net = od_prev + pos
                pos = -od + prev_net
            fut_trade[i] += pos - (fut_tot[i - 1] if i > 0 else 0.0)
        net = od + pos
        if delta_thresh > 0.0:
            if not math.isnan(net):
                if is_min_start[i]:
                    thr = delta_thresh; hf = hedge_frac
                else:
                    thr = intra_mult * delta_thresh; hf = intra_frac
                if abs(net) > thr:
                    resid = (1.0 - hf) * delta_thresh
                    adj = (-net + resid) if net < 0.0 else (-net - resid)
                    pos += adj
                    fut_trade[i] += adj
        elif og != 0.0 and not (math.isnan(og) or math.isnan(net)):
            pts = abs(net / og)
            pa = pts_allowed[i]
            if is_min_start[i]:
                thr = pa; hf = hedge_frac
            else:
                thr = intra_mult * pa; hf = intra_frac
            if pts > thr:
                if net < 0.0:
                    adj = -net + (1.0 - hf) * pa * og
                else:
                    adj = -net - (1.0 - hf) * pa * og
                pos += adj
                fut_trade[i] += adj
        fut_tot[i] = pos
        od_prev = od
    return fut_tot, fut_trade


@nb.njit(cache=True)
def hedge_notional_cum(fut_trade, u_bid, u_ask):
    n = fut_trade.shape[0]
    out = np.empty(n, dtype=np.float64)
    tot = 0.0
    for i in range(n):
        q = fut_trade[i]
        if q != 0.0:
            tot += abs(q) * 0.5 * (u_bid[i] + u_ask[i])
        out[i] = tot
    return out


@nb.njit(cache=True)
def futures_pnl(fut_tot, fut_trade, exec_bid, exec_ask, u_bid, u_ask):
    n = fut_tot.shape[0]
    out = np.empty(n, dtype=np.float64)
    cash = 0.0
    for i in range(n):
        q = fut_trade[i]
        if q > 0.0:
            cash -= q * exec_ask[i]
        elif q < 0.0:
            cash -= q * exec_bid[i]
        p = fut_tot[i]
        if p > 0.0:
            liq = p * u_bid[i]
        elif p < 0.0:
            liq = p * u_ask[i]
        else:
            liq = 0.0
        out[i] = cash + liq
    return out


@nb.njit(fastmath=True)
def apply_exits(tot_pnl, otm_trade, otm_ref, underlying, time_min, otm_iv, dte,
                ce_exec_bid, pe_exec_bid, ce_exec_ask, pe_exec_ask, fut_pnl,
                sl_bps, tp_bps, tv_thresh, tv4_thresh, iv_drop_thresh, entry_underlying):
    """Minute-grid exit test (single strike, no roll). Returns (points, idx, reason)."""
    n = len(tot_pnl)
    max_loss_pts = -(entry_underlying * (sl_bps / 10000.0))
    max_prof_pts = entry_underlying * (tp_bps / 10000.0)
    exit_idx = -1
    reason = 0
    for i in range(n):
        sl_cond = (tot_pnl[i] <= max_loss_pts)
        tp_cond = (tot_pnl[i] >= max_prof_pts)
        tv_cond = ((2.0 * otm_trade[i]) < (tv_thresh * underlying[i]))
        tv4_cond = ((2.0 * otm_ref[i]) < (tv4_thresh * underlying[i]))
        iv_cond = (time_min[i] >= 897) and (otm_iv[i] < iv_drop_thresh) and (dte[i] <= 1.0)
        if sl_cond or tp_cond or tv_cond or tv4_cond or iv_cond:
            exit_idx = i
            if sl_cond: reason = 1
            elif tp_cond: reason = 2
            elif tv_cond: reason = 3
            elif tv4_cond: reason = 4
            else: reason = 5
            break
    if exit_idx != -1:
        ev = ce_exec_bid[0] + pe_exec_bid[0]
        raw = ev - (ce_exec_ask[exit_idx] + pe_exec_ask[exit_idx]) + fut_pnl[exit_idx]
        return raw, exit_idx, reason
    return tot_pnl[n - 1], n - 1, 0


# ============================================================
# 4. LOADERS
# ============================================================
def load_cw_luts(lut_dir=LUT_DIR):
    """5 bool arrays shaped DIMS. Row order of the CSVs = itertools.product order."""
    out = []
    for name in STAGE_FILES:
        df = pd.read_csv(os.path.join(lut_dir, name))
        n = int(np.prod(DIMS))
        if len(df) != n:
            raise ValueError(f"{name}: {len(df)} rows, expected {n} (DIMS {DIMS})")
        arr = (df["Trade"].astype(str).str.strip().str.upper() == "YES").values
        if int(arr.sum()) != EXPECTED_ON[name]:
            print(f"  [WARN] {name}: {int(arr.sum())} ON cells (production table has "
                  f"{EXPECTED_ON[name]}) - a different table is loaded")
        out.append(arr.reshape(DIMS))
    return out


def load_nw_lut(lut_dir=LUT_DIR):
    df = pd.read_csv(os.path.join(lut_dir, "NW_LUT.csv"))
    arr = (df["Trade"].astype(str).str.strip().str.upper() == "YES").values.reshape(2, 4, 4, 3)
    if int(arr.sum()) != NW_EXPECTED_ON:
        print(f"  [WARN] NW_LUT.csv: {int(arr.sum())} ON cells (production has {NW_EXPECTED_ON})")
    return arr


def load_event_dates():
    for path in (EVENT_PATH, EVENT_PATH_FALLBACK):
        try:
            ev = pd.read_excel(path, header=None)
            dates = set(pd.to_datetime(ev[0], dayfirst=True).dt.date)
            print(f"[INFO] {len(dates)} event dates from {path}, last {max(dates)}")
            return dates
        except Exception as e:
            print(f"[WARN] EventDates not read from {path} ({type(e).__name__})")
    print("[WARN] no event calendar - event days trade as normal days")
    return set()


def _norm_adj_chg(chg, base, gap):
    """Normalized_Adj_Chg = IV change x 0.1 / its prev2 base."""
    if base is None or chg is None or not (np.isfinite(base) and np.isfinite(chg)) or base == 0:
        return float("nan")
    return float(chg) * 0.1 / float(base)


def load_day_inputs():
    """{ 'YYYY-MM-DD': dict(prev_straddle, adj_iv_chg, yesterday_iv, prev_future,
                            prev_pure, prev_with_prev, nw_adj_iv, nw_expiry) }"""
    out = {}
    if DAY_INPUTS == "daily_csv":
        d = pd.read_csv(DAILY_CSV)
        d["Date"] = pd.to_datetime(d["Date"]).dt.strftime("%Y-%m-%d")
        d["_pd"] = pd.to_datetime(d["PrevDate"], errors="coerce")
        d = d.sort_values("Date").reset_index(drop=True)
        for i, r in d.iterrows():
            try:
                gap = (pd.Timestamp(r["PrevDate"]) - pd.Timestamp(r["Prev2Date"])).days > 1
                base = r["Prev2_Adj_IV"] if gap else r["Prev2_IV"]
                chg = r["Final_IV_Chg"] if "Final_IV_Chg" in d.columns else \
                    (r["Adj_IV_Chg"] if gap else r["IV_Chg"])
                pure, wprev = r["IDV_Pure"], r["IDV_With_Prev"]
                if not (np.isfinite(pure) and np.isfinite(wprev)):
                    # same 30-day fallback as live_day_context / phase1
                    day = pd.Timestamp(r["Date"])
                    e = d[(d["Date"] < r["Date"]) & d["IDV_Pure"].notna() & d["IDV_With_Prev"].notna()
                          & (d["_pd"] >= day - pd.Timedelta(days=30)) & (d["_pd"] < day)]
                    if len(e):
                        r2 = e.sort_values("_pd").iloc[-1]
                        pure, wprev = r2["IDV_Pure"], r2["IDV_With_Prev"]
                yiv = r["Adj_IV"]
                out[r["Date"]] = dict(
                    prev_straddle=float(r["Straddle"]),
                    adj_iv_chg=_norm_adj_chg(chg, base, gap),
                    yesterday_iv=float(yiv) if np.isfinite(yiv) and yiv != 0 else float("nan"),
                    prev_future=float(r["Future"]),
                    prev_pure=float(pure), prev_with_prev=float(wprev),
                    nw_adj_iv=float(r["NW_Adj_IV"]) if "NW_Adj_IV" in d.columns and np.isfinite(r["NW_Adj_IV"]) else float("nan"),
                    nw_expiry=str(r["NW_Expiry"]) if "NW_Expiry" in d.columns and pd.notna(r["NW_Expiry"]) else None)
            except Exception:
                continue
        print(f"[INFO] day inputs from {os.path.basename(DAILY_CSV)}: {len(out)} days, "
              f"{min(out)} -> {max(out)}")
        return out

    # ---- backtest_files: exactly phase1_extract.load_reference_data ----
    idv = pd.read_csv(IDV_PATH, parse_dates=['Date'], dayfirst=True)
    idv['Date'] = idv['Date'].dt.normalize()
    idv = idv.sort_values('Date').reset_index(drop=True)
    idv_map = {k: (float(g.iloc[-1]['IDV_Pure']), float(g.iloc[-1]['IDV_With_Prev']))
               for k, g in idv.groupby('Date')}
    ps = pd.read_excel(PREV_STR_PATH)
    for c in ['Date', 'PrevDate', 'Prev2Date']:
        ps[c] = pd.to_datetime(ps[c], dayfirst=True)
    gap = (ps['PrevDate'] - ps['Prev2Date']).dt.days > 1
    ps['Target_Chg'] = np.where(gap, ps['Adj IV Chg'], ps['IV Chg'])
    prev2_col = 'Prev2 IV' if 'Prev2 IV' in ps.columns else 'Prev2 Adj IV'
    ps['Target_Base'] = np.where(gap, ps['Prev2 Adj IV'], ps[prev2_col])
    ps['Target_Base'] = ps['Target_Base'].replace(0, np.nan)
    ps['Normalized_Adj_Chg'] = (ps['Target_Chg'] * 0.1) / ps['Target_Base']
    yest_col = 'Adj IV' if 'Adj IV' in ps.columns else 'IV'
    ps['Yesterday_IV'] = ps[yest_col].replace(0, np.nan)
    nw = {}
    if os.path.exists(NW_PREV_PATH):
        p = pd.read_csv(NW_PREV_PATH)
        p["k"] = pd.to_datetime(p["Date"]).dt.strftime("%Y-%m-%d")
        nw = {r.k: (r.NW_Adj_IV, r.NW_Expiry) for r in p.itertuples()}
    for _, row in ps.iterrows():
        try:
            key = row['Date'].strftime('%Y-%m-%d')
            fut = row.get('Future')
            if pd.isna(fut) or fut is None:
                fut = row.get('Underlying')
            pure = wprev = float("nan")
            chk = row['Date'] - timedelta(days=1)
            for _ in range(30):                       # phase1 get_prev_idvs_local
                if chk in idv_map:
                    pure, wprev = idv_map[chk]; break
                chk -= timedelta(days=1)
            nv, ne = nw.get(key, (float("nan"), None))
            out[key] = dict(prev_straddle=float(row['Straddle']),
                            adj_iv_chg=float(row['Normalized_Adj_Chg']),
                            yesterday_iv=float(row['Yesterday_IV']), prev_future=float(fut),
                            prev_pure=pure, prev_with_prev=wprev,
                            nw_adj_iv=float(nv) if nv is not None and np.isfinite(nv) else float("nan"),
                            nw_expiry=str(ne) if ne is not None and pd.notna(ne) else None)
        except Exception:
            pass
    print(f"[INFO] day inputs from backtest files: {len(out)} days, {min(out)} -> {max(out)}")
    return out


def _option_expiries(path, base_date):
    """Option expiries in a day file on/after the trade date, nearest first."""
    if path.endswith(".parquet"):
        e = pd.read_parquet(path, columns=["Expiry", "Type"])
    else:
        e = pd.read_feather(path, columns=["Expiry", "Type"])
    e = e[e["Type"].astype(str).isin(["CE", "PE"])]
    ex = pd.to_datetime(pd.Series(e["Expiry"].unique()))
    return sorted(x for x in ex if x.normalize() >= base_date.normalize())


def _read_expiry(path, cols, expiry=None):
    """Option rows (CE/PE) of one expiry, only the needed columns."""
    if path.endswith(".parquet"):
        import pyarrow.parquet as pq
        names = pq.ParquetFile(path).schema_arrow.names
        use = [c for c in cols if c in names]
        if expiry is not None and "Expiry" in names:
            df = pd.read_parquet(path, columns=use, filters=[("Expiry", "==", pd.Timestamp(expiry))])
        else:
            df = pd.read_parquet(path, columns=use)
    else:
        df = _read_ticks(path, cols)
        if expiry is not None and "Expiry" in df.columns:
            df = df[pd.to_datetime(df["Expiry"]) == pd.Timestamp(expiry)]
    df["Type"] = df["Type"].astype(str)
    return df[df["Type"].isin(["CE", "PE"])].reset_index(drop=True)


def load_cw_ticks(path, base_date):
    """(CW option rows, CW expiry, NW expiry or None)."""
    exps = _option_expiries(path, base_date)
    if not exps:
        return pd.DataFrame(), None, None
    cw = exps[0]
    nw = exps[1] if (TICK_LAYOUT == "combined" and len(exps) > 1) else None
    return _read_expiry(path, CW_COLS, cw), cw, nw


def load_nw_ticks(ddmmyyyy, cw_path, nw_expiry):
    """Next-week option rows: same file (combined) or the NW file (split)."""
    if TICK_LAYOUT == "combined":
        if nw_expiry is None:
            return None
        return _read_expiry(cw_path, NW_COLS + ["Expiry"], nw_expiry)
    f = _tick_file(os.path.join(NW_TICK_DIR, ddmmyyyy), "NIFTY_NW")
    if f is None:
        return None
    exps = _option_expiries(f, pd.Timestamp(year=int(ddmmyyyy[4:]), month=int(ddmmyyyy[2:4]),
                                                 day=int(ddmmyyyy[:2])))
    return _read_expiry(f, NW_COLS + ["Expiry"], exps[0] if exps else None)


def _tick_file(folder, stem):
    for ext in (".feather", ".parquet"):
        p = os.path.join(folder, stem + ext)
        if os.path.exists(p):
            return p
    return None


def _read_ticks(path, cols):
    try:
        if path.endswith(".parquet"):
            return pd.read_parquet(path, columns=[c for c in cols])
        import pyarrow as pa
        with pa.memory_map(path, 'r') as src:
            names = pa.ipc.open_file(src).schema.names
        return pd.read_feather(path, columns=[c for c in cols if c in names])
    except Exception:
        df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_feather(path)
        return df[[c for c in cols if c in df.columns]]


def _fy(ts):
    y = ts.year
    return f"FY{str(y)[-2:]}-{str(y+1)[-2:]}" if ts.month >= 4 else f"FY{str(y-1)[-2:]}-{str(y)[-2:]}"


# ============================================================
# 5. CW: 1-SECOND GRID, STRIKE ARRAYS, TRADE SIMULATION
# ============================================================
CW_COLS = ['Time_dt', 'Time', 'Strike', 'StrikePrice', 'Type', 'LTP', 'Bid', 'Ask', 'DTE',
           'Expiry', 'UnderlyingLTP', 'UnderlyingBid', 'UnderlyingAsk']


def strike_arrays(strike, grid, df):
    """LTP / bid / ask / IV / 30-s fills for one strike on the 1-second grid."""
    temp = df[df['_K'] == float(strike)]

    def _map_col(c):
        d = temp[temp['Type'] == 'CE'].set_index('Second')[c].to_dict()
        p = temp[temp['Type'] == 'PE'].set_index('Second')[c].to_dict()
        return grid['Second'].map(d).ffill().bfill(), grid['Second'].map(p).ffill().bfill()

    ce_ltp, pe_ltp = _map_col('LTP'); ce_bid, pe_bid = _map_col('Bid'); ce_ask, pe_ask = _map_col('Ask')
    raw_iv = compute_iv_array(grid['Underlying'].values, float(strike),
                              (grid['DTE'].values / 365.0), ce_ltp.values, pe_ltp.values)
    if IV_MODE == "tick":
        ce_sec = set(temp.loc[temp['Type'] == 'CE', 'Second'])
        pe_sec = set(temp.loc[temp['Type'] == 'PE', 'Second'])
        sec = grid['Second']
        live = np.where(grid['Underlying'].values < float(strike),
                        sec.isin(ce_sec).values, sec.isin(pe_sec).values)
        iv = np.where(live, raw_iv, np.nan)
    else:
        tsec = grid['Time'].dt.hour * 3600 + grid['Time'].dt.minute * 60 + grid['Time'].dt.second
        iv = distance_weighted_iv_numba(raw_iv, tsec.values)
    bad = (iv > 2.0) | (iv < 0.01); iv[bad] = np.nan
    iv = pd.Series(iv).ffill().bfill().values
    ce_bid = ce_bid.fillna(ce_ltp); pe_bid = pe_bid.fillna(pe_ltp)
    ce_ask = ce_ask.fillna(ce_ltp); pe_ask = pe_ask.fillna(pe_ltp)

    def _exec(s):
        return s.rolling(N_OPT_SECONDS, min_periods=1).mean().shift(-(N_OPT_SECONDS - 1)).fillna(s)
    return (ce_ltp.values, pe_ltp.values, iv, ce_bid.values, pe_bid.values, ce_ask.values,
            pe_ask.values, _exec(ce_bid).values, _exec(pe_bid).values,
            _exec(ce_ask).values, _exec(pe_ask).values)


def simulate_cw(grid, entry_idx, strike_cache, dte_idx, abiv):
    """One CW trade from grid row entry_idx. Returns dict or None."""
    sim_base = grid.iloc[entry_idx:].copy().reset_index(drop=True)
    if sim_base.empty:
        return None
    real_start = sim_base['Time'].iloc[0]
    sim_base['safe_idx'] = np.arange(len(sim_base))
    mins = pd.date_range(real_start, sim_base['Time'].iloc[-1], freq='1min')
    tgt = pd.Index([real_start]).union(mins)
    merged = pd.merge_asof(pd.DataFrame({'Time': tgt}), sim_base[['Time', 'safe_idx']],
                           on='Time', direction='forward')
    ti = merged['safe_idx'].dropna().astype(int).values
    ti = np.unique(ti[ti < len(sim_base)])
    sim = sim_base.iloc[ti].copy().reset_index(drop=True)

    K = float(sim['ATMRef'].iloc[0])            # strike held all day (no roll)
    arr = strike_cache.get(K)
    if arr is None:
        return None
    a = sim['safe_idx'].values + entry_idx
    ce_px, pe_px, ce_ask, pe_ask = arr[0][a], arr[1][a], arr[5][a], arr[6][a]
    cxb, pxb, cxa, pxa = arr[7][a], arr[8][a], arr[9][a], arr[10][a]

    # option leg, minute grid: sold at the 30-s avg bid, marked at the ask
    ev = cxb[0] + pxb[0]
    opt_pnl = ev - (ce_ask + pe_ask)
    opt_pnl[-1] = ev - (cxa[-1] + pxa[-1])

    # futures leg, second grid (hedge_integration.build_second_arrays, one strike)
    first = int(sim['safe_idx'].iloc[0]); last = int(sim['safe_idx'].iloc[-1])
    sec_of_min = sim['safe_idx'].values.astype(np.int64) - first
    n = last - first + 1
    seg_end = np.empty(len(sec_of_min), dtype=np.int64)
    seg_end[:-1] = sec_of_min[1:]; seg_end[-1] = n
    owner_l, sec_l = [], []
    for j in range(len(sec_of_min)):
        s0, s1 = sec_of_min[j], seg_end[j]
        if s1 <= s0:
            owner_l.append(j); sec_l.append(s0)
        else:
            owner_l.extend([j] * (s1 - s0)); sec_l.extend(range(s0, s1))
    owner = np.array(owner_l, dtype=np.int64); sec_pos = np.array(sec_l, dtype=np.int64)
    row_pos = np.full(len(sec_of_min), -1, dtype=np.int64)
    for k in range(len(owner)):
        if row_pos[owner[k]] == -1:
            row_pos[owner[k]] = k
    abs_idx = np.arange(entry_idx + first, entry_idx + last + 1)[sec_pos]
    S = np.ascontiguousarray(grid['Underlying'].values[abs_idx], float)
    T = np.maximum(grid['DTE'].values[abs_idx] / 365.0, 1e-5).astype(float)
    Karr = np.full(len(abs_idx), K)
    iv = np.ascontiguousarray(arr[2][abs_idx], float)
    od, og = opt_delta_gamma_arr(S, Karr, T, iv)
    ivref = np.ascontiguousarray(grid['OTMIVRef'].values[abs_idx], float)
    ceref = np.ascontiguousarray(grid['CERef'].values[abs_idx], float)
    peref = np.ascontiguousarray(grid['PERef'].values[abs_idx], float)
    pa = np.maximum(np.minimum((ivref / HEDGE_DIV) * S, (ceref + peref) / STR_D), PTS_FLOOR * S)
    chunk = np.ones(len(abs_idx), dtype=np.int64)
    is_min = np.zeros(len(abs_idx), dtype=np.bool_); is_min[row_pos] = True
    ft, tr = hedge_walk(od, og, pa, chunk, is_min, HEDGE_FRAC, INTRA_MULT, INTRA_FRAC,
                        float(DELTA_MAP.get(dte_idx, 0.0)))
    ub = np.ascontiguousarray(grid['UnderlyingBid'].values[abs_idx], float)
    ua = np.ascontiguousarray(grid['UnderlyingAsk'].values[abs_idx], float)
    hn = hedge_notional_cum(tr, ub, ua)[row_pos]
    fut = futures_pnl(ft, tr, np.ascontiguousarray(grid['FutExecBid'].values[abs_idx], float),
                      np.ascontiguousarray(grid['FutExecAsk'].values[abs_idx], float), ub, ua)[row_pos]
    tot = opt_pnl + fut

    U = sim['Underlying'].values.astype(float)
    otm_trade = np.where(U > K, pe_px, ce_px)
    otm_ref = np.where(U > sim['ATMRef'].values, sim['PERef'].values, sim['CERef'].values)
    tmin = (sim['Time'].dt.hour * 60 + sim['Time'].dt.minute).values.astype(float)
    u0 = float(grid['Underlying'].iloc[entry_idx])

    fraction = (round(abiv, 2) - 0.08) / 0.12
    base_tp = TP_LOW[dte_idx] + fraction * (TP_HIGH[dte_idx] - TP_LOW[dte_idx])
    tp_bps = float(max(1.0, round(base_tp * TP_SCALE)))
    sl_bps = float(SL_BPS_BY_DTE.get(dte_idx, SL_BPS_DEFAULT))
    pts, xi, reason = apply_exits(
        tot.astype(float), otm_trade.astype(float), otm_ref.astype(float), U, tmin,
        sim['OTMIVRef'].values.astype(float), sim['DTE'].values.astype(float),
        cxb.astype(float), pxb.astype(float), cxa.astype(float), pxa.astype(float),
        fut.astype(float), sl_bps, tp_bps, TV_THRESH, TV4_THRESH, IV_DROP_THRESH, u0)
    sc = 25000.0 / u0
    return dict(gross=float(pts * sc), hedge_notional=float(hn[xi] * sc), strike=K,
                entry_underlying=u0, entry_credit=float(ev * sc), sl_bps=sl_bps, tp_bps=tp_bps,
                exit_time=sim['Time'].iloc[xi].strftime('%H:%M:%S'),
                exit_reason=EXIT_REASONS[int(reason)])


# ============================================================
# 6. NW LEG (extract_nw_ltp.py: snapshot features, 30-s fills)
# ============================================================
NW_COLS = ["Time_dt", "Strike", "Type", "LTP", "Bid", "Ask", "DTE", "UnderlyingLTP"]


def _bs_py(is_call, S, K, T, sig):
    sq = math.sqrt(T); d1 = (math.log(S / K) + 0.5 * sig * sig * T) / (sig * sq); d2 = d1 - sig * sq
    nc = lambda x: 0.5 * (1.0 + math.erf(x / 1.4142135623730951))
    return S * nc(d1) - K * nc(d2) if is_call else K * nc(-d2) - S * nc(-d1)


def _iv_py(is_call, price, S, K, T):
    if not (price > 0.1 and T > 0 and S > 0):
        return np.nan
    lo, hi = 1e-6, 5.0
    if _bs_py(is_call, S, K, T, lo) > price or _bs_py(is_call, S, K, T, hi) < price:
        return np.nan
    for _ in range(30):
        mid = 0.5 * (lo + hi); pm = _bs_py(is_call, S, K, T, mid)
        if abs(pm - price) < 1e-5:
            return mid
        lo, hi = (mid, hi) if pm < price else (lo, mid)
    return 0.5 * (lo + hi)


def nw_day(d):
    """09:16 NW features + the NW trade's PnL from the next-week option rows.
    Returns (dict, None) or (None, reason)."""
    if d is None:
        return None, "nw_no_data"
    if d.empty:
        return None, "nw_empty"
    if d["Time_dt"].dt.tz is not None:
        d["Time_dt"] = d["Time_dt"].dt.tz_localize(None)
    d["Type"] = d["Type"].astype(str)
    d = d[d.Type.isin(["CE", "PE"])]
    d["sec"] = (d.Time_dt.dt.hour * 3600 + d.Time_dt.dt.minute * 60 + d.Time_dt.dt.second).astype(int)
    g = d.dropna(subset=["UnderlyingLTP"]).groupby("sec").agg(u=("UnderlyingLTP", "first"),
                                                              dte=("DTE", "first"))
    g = g[(g.index >= 9 * 3600 + 15 * 60) & (g.index <= 15 * 3600 + 27 * 60)]
    if g.empty:
        return None, "nw_no_underlying"
    secs = g.index.values

    def snap(hhmm):
        return int(np.searchsorted(secs, (hhmm // 100) * 3600 + (hhmm % 100) * 60, side="right")) - 1

    def first_at_or_after(hhmm):
        return int(np.searchsorted(secs, (hhmm // 100) * 3600 + (hhmm % 100) * 60, side="left"))

    p916 = snap(916)
    if p916 < 0:
        return None, "nw_no_0916_snapshot"
    s916 = secs[p916]; u916 = float(g.u.iloc[p916])
    pre = d[(d.sec <= s916) & (d.LTP > 0)]
    have = pre.groupby(["Strike", "Type"]).size().unstack()
    if have is None or "CE" not in have or "PE" not in have:
        return None, "nw_no_atm_quotes"
    ok = have[["CE", "PE"]].dropna().index.values
    ok = ok[ok > 0]
    if not len(ok):
        return None, "nw_no_atm_quotes"
    K = float(ok[np.argmin(np.abs(ok - u916))])
    A = d[d.Strike == K]
    leg = {}
    for typ in ("CE", "PE"):
        x = A[A.Type == typ].groupby("sec").last().reindex(secs)
        x["traded"] = x["LTP"].notna()
        for c in ("LTP", "Bid", "Ask"):
            x[c] = x[c].where(x[c] > 0).ffill()
        x["Bid"] = x["Bid"].fillna(x["LTP"]); x["Ask"] = x["Ask"].fillna(x["LTP"])
        leg[typ] = x
    S = g.u.values.astype(float); T = np.maximum(g.dte.values / 365.0, 1e-5)
    ce, pe = leg["CE"], leg["PE"]
    iv = np.full(len(secs), np.nan)
    ce_tr, pe_tr = ce["traded"].values, pe["traded"].values
    ce_l, pe_l = ce["LTP"].values, pe["LTP"].values
    for i in range(len(secs)):
        if S[i] < K:
            if ce_tr[i]:
                iv[i] = _iv_py(True, ce_l[i], S[i], K, T[i])
        elif pe_tr[i]:
            iv[i] = _iv_py(False, pe_l[i], S[i], K, T[i])
    iv[(iv > 2.0) | (iv < 0.01)] = np.nan
    iv = pd.Series(iv).ffill().values

    def xavg(s):
        return s.rolling(N_OPT_SECONDS, min_periods=1).mean().shift(-(N_OPT_SECONDS - 1)).fillna(s).values
    cb, pb = ce["Bid"].reset_index(drop=True), pe["Bid"].reset_index(drop=True)
    ca, pa_ = ce["Ask"].reset_index(drop=True), pe["Ask"].reset_index(drop=True)
    cxb, pxb, cxa, pxa = xavg(cb), xavg(pb), xavg(ca), xavg(pa_)
    if not np.isfinite(iv[p916]):
        return None, "nw_no_0916_iv"
    for v in (cb.iat[p916], ca.iat[p916], pb.iat[p916], pa_.iat[p916]):
        if not np.isfinite(v):
            return None, "nw_no_0916_quotes"

    # the trade: rows 09:21 .. 15:15 at hh:mm:00 snapshots
    rows = []
    for t in [h * 100 + m for h in range(9, 16) for m in range(60)]:
        if t < NW_ENTRY_T or t > NW_EXIT_T:
            continue
        p = snap(t); q = first_at_or_after(t)
        if p < 0 or q >= len(secs):
            continue
        v = (cb.iat[p], ca.iat[p], pb.iat[p], pa_.iat[p], iv[p])
        if not all(np.isfinite(x) for x in v):
            continue
        rows.append((t, v[0], v[1], v[2], v[3], v[4], S[p], float(g.dte.iloc[p]),
                     cxb[q], pxb[q], cxa[q], pxa[q]))
    out = dict(nw_dte=round(float(g.dte.iloc[p916]), 3), iv_916=float(iv[p916]), strike=K, u_916=u916,
               pnl=float("nan"), exit_time=None, exit_reason=None)
    if len(rows) < 10:
        return out, None
    e = rows[0]
    u0 = e[6]; credit = e[8] + e[9]; sl_pts = -u0 * NW_SL_BPS / 1e4
    pos = fut = hn = 0.0; prev_u = u0; ex = rows[-1]; why = "EOD"
    for r in rows:
        fut += pos * (r[6] - prev_u); prev_u = r[6]
        mid = (r[1] + r[2] + r[3] + r[4]) / 2
        if credit - mid + fut <= sl_pts:
            ex = r; why = "SL"; break
        Tm = max(r[7] / 365.0, 1e-5); sg = max(r[5], 1e-4)
        d1 = (math.log(r[6] / K) + 0.5 * sg * sg * Tm) / (sg * math.sqrt(Tm))
        net = (1.0 - 2.0 * (0.5 * (1.0 + math.erf(d1 / 1.4142135623730951)))) + pos
        if abs(net) > NW_HEDGE_THR:
            pos += -net; hn += abs(net) * r[6]
    sc = 25000.0 / u0
    out.update(pnl=(credit - (ex[10] + ex[11]) + fut) * sc - NW_HEDGE_COST_BPS / 1e4 * hn * sc,
               exit_time=f"{ex[0] // 100:02d}:{ex[0] % 100:02d}", exit_reason=why,
               entry_credit=credit * sc)
    return out, None


def nw_decision(day_key, dte_idx, fixed_idv, og_pct, inputs, nwm, NWL):
    """Features exactly as build_nw_dataset.py -> NW_LUT.csv verdict."""
    dt = pd.Timestamp(day_key)
    nw_dte = nwm["nw_dte"]
    cal = int(np.ceil(nw_dte))
    d0 = np.datetime64(dt.date(), "D")
    wknd = cal - int(np.busday_count(d0, d0 + np.timedelta64(cal, "D")))
    trading = float(np.round(nw_dte - wknd))
    adjf = math.sqrt(nw_dte / trading) if trading > 0 else float("nan")
    biv = nwm["iv_916"] * adjf
    ratio = biv / fixed_idv
    og_iv = inputs.get("nw_adj_iv", float("nan"))
    if inputs.get("nw_expiry"):
        tick_exp = dt + pd.Timedelta(days=float(np.round(nw_dte) - 1))
        if pd.Timestamp(inputs["nw_expiry"]).normalize() != tick_exp.normalize():
            og_iv = float("nan")              # bhavcopy NW contract != tick NW contract
    norm_og = og_pct * (0.19 / og_iv) if np.isfinite(og_iv) and og_iv > 0 else float("nan")
    i = int(np.searchsorted(NW_RAT_EDGES, ratio))
    j = int(np.searchsorted(NW_LVL_EDGES, biv))
    feats = dict(nw_ratio=ratio, nw_build_iv=biv, nw_norm_og=norm_og)
    if not (np.isfinite(norm_og) and np.isfinite(ratio)):
        return False, feats, "nw_no_gap_feature"
    k = int(np.searchsorted(NW_OG_EDGES, norm_og))
    feats["nw_cell"] = (dte_idx, i, j, k)
    return bool(NWL[dte_idx, i, j, k]), feats, None


# ============================================================
# 7. PER-DAY WORKER
# ============================================================
_CTX = {}


def _init_worker(ctx):
    _CTX.update(ctx)


def process_day(file_path):
    """Everything for one trading day. Returns a result dict (always)."""
    LUTS, NWL, inputs_all, events = _CTX["luts"], _CTX["nwl"], _CTX["inputs"], _CTX["events"]
    ds = os.path.basename(os.path.dirname(file_path))
    base_date = pd.Timestamp(year=int(ds[4:]), month=int(ds[2:4]), day=int(ds[:2]))
    key = base_date.strftime('%Y-%m-%d')
    res = dict(Date=key, FY=_fy(base_date), Event=base_date.date() in events, Leg="", Note="")
    try:
        inp = inputs_all.get(key)
        if not inp:
            res["Note"] = "no_day_inputs"; return res
        for f in ("prev_straddle", "adj_iv_chg", "yesterday_iv", "prev_future"):
            if not np.isfinite(inp[f]):
                res["Note"] = f"blank_{f}"; return res
        if not (inp["prev_pure"] > 0 and inp["prev_with_prev"] > 0):
            res["Note"] = "bad_idv"; return res
        fixed_idv = WEIGHTS[0] * inp["prev_pure"] + WEIGHTS[1] * inp["prev_with_prev"] + WEIGHTS[2] * inp["yesterday_iv"]
        if fixed_idv <= 0:
            res["Note"] = "bad_idv"; return res
        is_event = res["Event"]

        df, cw_exp, nw_exp = load_cw_ticks(file_path, base_date)
        if df.empty:
            res["Note"] = "empty_ticks"; return res
        res.update(CW_Expiry=str(cw_exp.date()), NW_Expiry=str(nw_exp.date()) if nw_exp is not None else "")
        tcol = next((c for c in ['Time_dt', 'Time'] if c in df.columns), None)
        if not pd.api.types.is_datetime64_any_dtype(df[tcol]):
            df[tcol] = pd.to_datetime(df[tcol])
        if df[tcol].dt.tz is not None:
            df[tcol] = df[tcol].dt.tz_localize(None)
        df['Second'] = df[tcol].dt.time
        df['Time'] = df[tcol]
        scol = 'Strike' if 'Strike' in df.columns else 'StrikePrice'
        df['_K'] = pd.to_numeric(df[scol], errors='coerce')

        # ---- 1-second grid ----
        end = GRID_END_LATE if base_date >= GRID_END_SWITCH else GRID_END_EARLY
        grid = pd.DataFrame({'Time': pd.date_range(base_date.replace(hour=9, minute=15),
                                                   base_date.replace(hour=end[0], minute=end[1], second=end[2]),
                                                   freq='1s')})
        grid['Second'] = grid['Time'].dt.time
        grp = df.groupby('Second')
        grid['Underlying'] = grid['Second'].map(grp['UnderlyingLTP'].first())
        grid['UnderlyingBid'] = grid['Second'].map(grp['UnderlyingBid'].first())
        grid['UnderlyingAsk'] = grid['Second'].map(grp['UnderlyingAsk'].first())
        grid['DTE'] = grid['Second'].map(grp['DTE'].first())
        grid.dropna(subset=['Underlying'], inplace=True)
        grid.reset_index(drop=True, inplace=True)
        if grid.empty:
            res["Note"] = "no_underlying"; return res
        grid['Time_hhmm'] = grid['Time'].dt.hour * 100 + grid['Time'].dt.minute

        u0916 = grid[grid['Time_hhmm'] >= 916]['Underlying']
        if u0916.empty:
            res["Note"] = "no_0916"; return res
        underlying_0916 = float(u0916.values[0])
        if CHECK_FALLBACK == "prev":
            pre = grid[grid['Time'] <= base_date.replace(hour=9, minute=16)]
            if not pre.empty:
                underlying_0916 = float(pre['Underlying'].values[-1])
        og_pct = (underlying_0916 - inp["prev_future"]) / inp["prev_future"] * 100.0
        if not np.isfinite(og_pct):
            res["Note"] = "nan_og"; return res
        norm_og = og_pct * (0.19 / inp["yesterday_iv"])
        og_idx = int(np.searchsorted(OG_THRESHOLDS, norm_og, side='right'))
        adj_idx = int(np.searchsorted(np.array([ADJ_LOWER, ADJ_HIGHER]), inp["adj_iv_chg"], side='right'))

        grid['UnderlyingBid'] = grid['UnderlyingBid'].fillna(grid['Underlying'])
        grid['UnderlyingAsk'] = grid['UnderlyingAsk'].fillna(grid['Underlying'])
        fx = lambda s: s.rolling(N_FUT_SECONDS, min_periods=1).mean().shift(-(N_FUT_SECONDS - 1)).fillna(s)
        grid['FutExecBid'] = fx(grid['UnderlyingBid']); grid['FutExecAsk'] = fx(grid['UnderlyingAsk'])
        grid['ATMRef'] = (grid['Underlying'] / 50).round() * 50

        cache = {st: strike_arrays(st, grid, df) for st in grid['ATMRef'].unique()}
        grid['OTMIVRef'] = np.nan; grid['CERef'] = np.nan; grid['PERef'] = np.nan
        for st, arr in cache.items():
            m = (grid['ATMRef'] == st).values
            grid.loc[m, 'OTMIVRef'] = arr[2][m]
            grid.loc[m, 'CERef'] = arr[0][m]          # reference straddle = real LTP
            grid.loc[m, 'PERef'] = arr[1][m]
        del df
        grid['Minute'] = grid['Time'].dt.floor('1min')

        # ---- minute check values (:59 fallback) ----
        if CHECK_FALLBACK == "prev":
            mins = pd.DataFrame({'Minute': np.sort(grid['Minute'].unique())})
            src = grid[['Time', 'CERef', 'PERef', 'OTMIVRef', 'DTE']].rename(columns={'Time': 'FeatTime'})
            mb = pd.merge_asof(mins, src, left_on='Minute', right_on='FeatTime', direction='backward')
        else:
            mb = grid.groupby('Minute').first().reset_index()
            mb['FeatTime'] = mb['Time']
        mb['str_rat'] = inp["prev_straddle"] / (mb['CERef'] + mb['PERef']).replace(0, np.nan)
        mb['build_iv'] = mb['OTMIVRef']
        mb = mb.dropna(subset=['str_rat', 'build_iv', 'DTE'])
        mb = mb[mb['DTE'] > 0].reset_index(drop=True)
        if mb.empty:
            res["Note"] = "no_minute_bars"; return res
        mb['time_hhmm'] = mb['Minute'].dt.hour * 100 + mb['Minute'].dt.minute
        d0 = np.array([base_date.date()], dtype='datetime64[D]')
        bus_days = np.busday_count(d0, d0 + np.timedelta64(int(np.ceil(mb['DTE'].iloc[0])), 'D'))[0]
        min_to_idx = {mn: g.index[0] for mn, g in grid.groupby('Minute')}

        # ---- scan rows ----
        def mk_row(r):
            th = int(r.time_hhmm)
            raw_dte = float(r.DTE)
            wk = int(np.ceil(raw_dte)) - bus_days
            tdte = max(0.0, raw_dte - WKND_ADJ) if wk > 0 else raw_dte
            adjf = math.sqrt(raw_dte / tdte) if raw_dte > 0 and tdte > 0 and raw_dte != tdte else 1.0
            di = int(np.clip(np.ceil(tdte) - 1, 0, 6))
            ei = min_to_idx.get(pd.Timestamp(r.Minute))
            abiv = float(r.build_iv) * adjf
            sr = float(r.str_rat)
            stage = {916: 0, 917: 1, 918: 2, 919: 3}.get(th, 4)
            coord = (di, int(np.searchsorted(IV_THRESHOLDS, abiv / fixed_idv, side='right')),
                     int(np.searchsorted(STR_THRESHOLDS, sr, side='right')),
                     int(np.searchsorted(BLD_THRESHOLDS, abiv, side='right')), og_idx, adj_idx)
            thr = VETO_STR.get(di)
            return dict(th=th, stage=stage, di=di, abiv=abiv, sr=sr, coord=coord, ei=ei,
                        iv_ratio=abiv / fixed_idv, feat_lag=float((r.FeatTime - r.Minute).total_seconds()),
                        veto=thr is not None and sr > thr,
                        floor=di in IV_FLOOR and abiv < IV_FLOOR[di])

        rows = []
        sw = mb[(mb['time_hhmm'] >= ENTRY_FIRST_HHMM) & (mb['time_hhmm'] <= ENTRY_LAST_HHMM)]
        for r in sw.itertuples():
            th = int(r.time_hhmm)
            if is_event and th < EVENT_FIRST_HHMM:
                continue
            row = mk_row(r)
            if row["di"] == 0 and th > EXPIRY_ENTRY_CUTOFF:
                break
            if row["ei"] is None:
                continue
            rows.append(row)
        fixed_rows = {}
        for r in mb[mb['time_hhmm'].isin(FIXED_ENTRIES)].itertuples():
            row = mk_row(r)
            if row["ei"] is not None:
                fixed_rows[row["th"]] = row
        if not rows and not fixed_rows:
            res["Note"] = "no_scan_rows"; return res
        dte0 = rows[0]["di"] if rows else next(iter(fixed_rows.values()))["di"]
        comfort = dte0 <= 1
        res.update(DTE_idx=dte0, Norm_OG=norm_og, Adj_IV_Chg=inp["adj_iv_chg"], Fixed_IDV=fixed_idv)
        sims = {}

        def sim_at(row):
            if row["ei"] not in sims:
                sims[row["ei"]] = simulate_cw(grid, row["ei"], cache, row["di"], row["abiv"])
            return sims[row["ei"]]

        def net_of(row, sim):
            gross = sim["gross"] / 3.0 if is_event else sim["gross"]
            hn = sim["hedge_notional"] / 3.0 if is_event else sim["hedge_notional"]
            ecost = ENTRY_SLIP.get(row["di"], ENTRY_SLIP_DEF)
            hcost = HEDGE_SLIP_BPS.get(row["di"], HEDGE_SLIP_DEF) / 1e4 * hn
            return gross, hn, ecost, hcost

        def try_cw(row):
            if row["veto"] or (comfort and row["floor"]):
                return None
            if not LUTS[row["stage"]][row["coord"]]:
                return None
            return sim_at(row)

        def take_cw(row, sim):
            gross, hn, ecost, hcost = net_of(row, sim)
            res.update(Leg="CW", Entry_Time=f"{row['th'] // 100:02d}:{row['th'] % 100:02d}",
                       Stage=STAGE_NAMES[row["stage"]], Cell=str(row["coord"]),
                       Build_IV=row["abiv"], Str_Ratio=row["sr"], IV_Ratio=row["iv_ratio"],
                       Check_Lag_s=row["feat_lag"], Strike=sim["strike"],
                       Entry_U=sim["entry_underlying"], SL_bps=sim["sl_bps"], TP_bps=sim["tp_bps"],
                       Exit_Time=sim["exit_time"], Exit_Reason=sim["exit_reason"],
                       Gross_PnL=gross, Hedge_Notional=hn, Entry_Cost=ecost, Hedge_Cost=hcost,
                       Net_PnL=gross - ecost - hcost, Trades=EVENT_SIZE if is_event else 1.0)

        def record_fixed():
            for th in FIXED_ENTRIES:
                tag = f"{th:04d}"
                row = fixed_rows.get(th)
                if row is None:
                    res[f"Signal_{tag}"] = "NO_DATA"; continue
                if is_event and th < EVENT_FIRST_HHMM:
                    sig = "EVENT_PRE_0918"
                elif row["veto"]:
                    sig = "VETO"
                elif comfort and row["floor"]:
                    sig = "FLOOR"
                else:
                    sig = "LUT_ON" if LUTS[row["stage"]][row["coord"]] else "LUT_OFF"
                res[f"Signal_{tag}"] = sig
                sim = sim_at(row)
                if sim is None:
                    continue
                gross, hn, ecost, hcost = net_of(row, sim)
                res[f"PnL_{tag}"] = gross - ecost - hcost
                res[f"Exit_{tag}"] = f"{sim['exit_reason']} {sim['exit_time'][:5]}"

        nw_state = {"done": False}

        def check_nw():
            nw_state["done"] = True
            if not (NW_ENABLED and comfort):
                return False
            nwm, why = nw_day(load_nw_ticks(ds, file_path, nw_exp))
            res["NW_Checked"] = True
            if nwm is None:
                res["NW_Note"] = why; return False
            ok, feats, why = nw_decision(key, dte0, fixed_idv, og_pct, inp, nwm, NWL)
            res.update(NW_Ratio=feats.get("nw_ratio"), NW_Build_IV=feats.get("nw_build_iv"),
                       NW_Norm_OG=feats.get("nw_norm_og"), NW_Cell=str(feats.get("nw_cell")),
                       NW_Verdict="YES" if ok else "NO", NW_Note=why or "")
            if not ok or not np.isfinite(nwm["pnl"]):
                return False
            res.update(Leg="NW", Entry_Time="09:21", Stage="NW", Strike=nwm["strike"],
                       Exit_Time=nwm["exit_time"], Exit_Reason=nwm["exit_reason"],
                       Gross_PnL=nwm["pnl"], Hedge_Notional=float("nan"), Entry_Cost=0.0,
                       Hedge_Cost=0.0, Net_PnL=nwm["pnl"], Trades=1.0)
            return True

        def decide():
            if NW_CHECK_MODE == "live":
                for row in rows:
                    if row["th"] > NW_AFTER_HHMM:
                        break
                    sim = try_cw(row)
                    if sim is not None:
                        take_cw(row, sim); return
                if comfort and check_nw():
                    return
                for row in rows:
                    if row["th"] <= NW_AFTER_HHMM:
                        continue
                    sim = try_cw(row)
                    if sim is not None:
                        take_cw(row, sim); return
            else:   # "replay": NW check at the first post-veto scan minute after 09:20
                for row in rows:
                    if row["veto"]:
                        continue
                    if comfort and row["th"] > NW_AFTER_HHMM and not nw_state["done"]:
                        if check_nw():
                            return
                    sim = try_cw(row)
                    if sim is not None:
                        take_cw(row, sim); return
            res["Note"] = res["Note"] or "no_signal"

        decide()
        record_fixed()
        return res
    except Exception:
        res["Note"] = "exception: " + traceback.format_exc().strip().split("\n")[-1][:150]
        return res
    finally:
        gc.collect()


# ============================================================
# 8. MAIN: RUN / RESUME / SUMMARY
# ============================================================
def config_fingerprint():
    keys = ["TICK_LAYOUT", "DAY_INPUTS", "IV_MODE", "CHECK_FALLBACK", "N_OPT_SECONDS", "N_FUT_SECONDS", "WKND_ADJ",
            "WEIGHTS", "ADJ_LOWER", "ADJ_HIGHER", "IV_THRESHOLDS", "STR_THRESHOLDS", "BLD_THRESHOLDS",
            "OG_THRESHOLDS", "ENTRY_FIRST_HHMM", "ENTRY_LAST_HHMM", "EXPIRY_ENTRY_CUTOFF",
            "EVENT_FIRST_HHMM", "EVENT_SIZE", "VETO_STR", "IV_FLOOR", "SL_BPS_DEFAULT", "SL_BPS_BY_DTE",
            "TP_HIGH", "TP_LOW", "TP_SCALE", "TV_THRESH", "TV4_THRESH", "IV_DROP_THRESH", "HEDGE_DIV",
            "STR_D", "PTS_FLOOR", "HEDGE_FRAC", "INTRA_MULT", "INTRA_FRAC", "DELTA_MAP", "ENTRY_SLIP",
            "ENTRY_SLIP_DEF", "HEDGE_SLIP_BPS", "HEDGE_SLIP_DEF", "NW_ENABLED", "NW_CHECK_MODE",
            "NW_AFTER_HHMM", "NW_ENTRY_T", "NW_EXIT_T", "NW_SL_BPS", "NW_HEDGE_THR",
            "NW_HEDGE_COST_BPS", "NW_RAT_EDGES", "NW_LVL_EDGES", "NW_OG_EDGES",
            "GRID_END_EARLY", "GRID_END_LATE", "GRID_END_SWITCH", "FIXED_ENTRIES"]
    g = globals()
    blob = repr([(k, g[k].tolist() if isinstance(g[k], np.ndarray) else g[k]) for k in keys])
    for name in STAGE_FILES + ["NW_LUT.csv"]:
        with open(os.path.join(LUT_DIR, name), "rb") as f:
            blob += hashlib.md5(f.read()).hexdigest()
    return hashlib.md5(blob.encode()).hexdigest()[:10]


def list_files(only=None):
    files = glob.glob(os.path.join(TICK_DIR, "*", "NIFTY.parquet")) + \
            glob.glob(os.path.join(TICK_DIR, "*", "NIFTY.feather"))      # feather wins if both
    by_day = {}
    for f in files:
        ds = os.path.basename(os.path.dirname(f))
        try:
            dt = pd.Timestamp(year=int(ds[4:]), month=int(ds[2:4]), day=int(ds[:2]))
        except Exception:
            continue
        if dt < START_DATE or dt > END_DATE or dt in SKIP_DATES:
            continue
        if only and dt.strftime('%Y-%m-%d') not in only:
            continue
        by_day[dt] = f
    return [by_day[k] for k in sorted(by_day)]


def summarise(day_dir, out_dir):
    recs = []
    for n in sorted(os.listdir(day_dir)):
        if n.endswith(".pkl"):
            with open(os.path.join(day_dir, n), "rb") as f:
                recs.append(pickle.load(f))
    if not recs:
        print("[SUMMARY] no days yet"); return
    D = pd.DataFrame(recs).sort_values("Date").reset_index(drop=True)
    for c in ["Net_PnL", "Gross_PnL", "Entry_Cost", "Hedge_Cost", "Trades"]:
        if c not in D:
            D[c] = np.nan
    for c in ["Leg", "Stage", "Exit_Reason", "Note"]:
        if c not in D:
            D[c] = ""
        D[c] = D[c].fillna("")
    D["Net_PnL"] = D["Net_PnL"].fillna(0.0)
    D["Cum_PnL"] = D["Net_PnL"].cumsum()
    D["High_Water"] = D["Cum_PnL"].cummax().clip(lower=0.0)      # equity starts at 0
    D["Drawdown"] = D["Cum_PnL"] - D["High_Water"]
    T = D[D["Leg"] != ""]
    mdd = float(-D["Drawdown"].min())
    t_idx = int(D["Drawdown"].idxmin())
    p_idx = int(D.loc[:t_idx, "Cum_PnL"].idxmax()) if t_idx > 0 else 0

    fy = D.groupby("FY").agg(PnL=("Net_PnL", "sum"),
                             CW_Trades=("Leg", lambda s: int((s == "CW").sum())),
                             NW_Trades=("Leg", lambda s: int((s == "NW").sum())))
    fy["MDD"] = [float(-(g["Net_PnL"].cumsum() - g["Net_PnL"].cumsum().cummax().clip(lower=0)).min())
                 for _, g in D.groupby("FY")]
    stage = T.groupby("Stage").agg(Trades=("Trades", "sum"), Count=("Net_PnL", "size"),
                                   PnL=("Net_PnL", "sum"))
    exits = T.groupby(["Leg", "Exit_Reason"]).agg(Count=("Net_PnL", "size"), PnL=("Net_PnL", "sum"))
    notes = D[D["Leg"] == ""]["Note"].value_counts()

    print("\n" + "=" * 62)
    print("GAMMA SHORT - FINAL BACKTEST")
    print("=" * 62)
    print(f"   Days            : {len(D)}  ({D['Date'].iloc[0]} -> {D['Date'].iloc[-1]})")
    print(f"   Trades          : {len(T)}  (CW {int((T['Leg'] == 'CW').sum())} + NW "
          f"{int((T['Leg'] == 'NW').sum())}; weighted {T['Trades'].sum():.1f})")
    print(f"   Net PnL         : {D['Net_PnL'].sum():,.1f}   (CW {T.loc[T['Leg'] == 'CW', 'Net_PnL'].sum():,.1f}"
          f" / NW {T.loc[T['Leg'] == 'NW', 'Net_PnL'].sum():,.1f})")
    print(f"   Costs           : entry {T['Entry_Cost'].sum():,.1f} + hedge {T['Hedge_Cost'].sum():,.1f}")
    print(f"   Max drawdown    : {mdd:.1f}  ({D['Date'].iloc[p_idx]} -> {D['Date'].iloc[t_idx]})")
    if len(T):
        print(f"   Per trade / win : {T['Net_PnL'].mean():.2f} / {100 * (T['Net_PnL'] > 0).mean():.1f}%")
    print("\n   By FY:")
    print(fy.round(1).to_string())
    print("\n   By stage:")
    print(stage.round(1).to_string())
    print("\n   Exits:")
    print(exits.round(1).to_string())
    if len(notes):
        print("\n   No-trade days by reason:")
        print(notes.to_string())

    # ---- fixed entry times vs production ----
    E = None
    fcols = [f"PnL_{th:04d}" for th in FIXED_ENTRIES if f"PnL_{th:04d}" in D]
    if fcols:
        keep = ["Date", "FY", "DTE_idx", "Event"]
        for th in FIXED_ENTRIES:
            for c in (f"PnL_{th:04d}", f"Signal_{th:04d}", f"Exit_{th:04d}"):
                if c in D:
                    keep.append(c)
        E = D[keep].copy()
        E.insert(3, "DTE", E.pop("DTE_idx") + 1)
        E["Prod_Leg"] = D["Leg"]; E["Prod_Entry"] = D.get("Entry_Time"); E["Prod_PnL"] = D["Net_PnL"]

        def _stats(s):
            s = s.dropna(); t = s[s != 0] if s.name == "Prod_PnL" else s
            cum = np.concatenate([[0.0], s.cumsum().values])
            return dict(Total=s.sum(), Trades=len(t), Per_trade=t.mean() if len(t) else np.nan,
                        Win_pct=100 * (t > 0).mean() if len(t) else np.nan,
                        MDD=float((np.maximum.accumulate(cum) - cum).max()))
        cols = fcols + ["Prod_PnL"]
        ST = pd.DataFrame({c: _stats(E[c]) for c in cols}).T
        FYE = E.groupby("FY")[cols].sum()
        print("\n   Fixed entry times vs production (every day; fixed entries ignore the LUT):")
        print(ST.round(1).to_string())
        print("\n   By FY:")
        print(FYE.round(1).to_string())

    os.makedirs(out_dir, exist_ok=True)
    if E is not None:
        E.to_csv(os.path.join(out_dir, "entry_time_pnl.csv"), index=False)
    D.to_csv(os.path.join(out_dir, "daily_log.csv"), index=False)
    T.to_csv(os.path.join(out_dir, "trades.csv"), index=False)
    try:
        with pd.ExcelWriter(os.path.join(out_dir, "backtest_summary.xlsx")) as w:
            summ = pd.DataFrame({"Metric": ["Days", "Trades", "CW trades", "NW trades", "Net PnL",
                                            "MDD", "MDD peak", "MDD trough", "Per trade", "Win %"],
                                 "Value": [len(D), len(T), int((T['Leg'] == 'CW').sum()),
                                           int((T['Leg'] == 'NW').sum()), round(D['Net_PnL'].sum(), 2),
                                           round(mdd, 2), D['Date'].iloc[p_idx], D['Date'].iloc[t_idx],
                                           round(T['Net_PnL'].mean(), 2),
                                           round(100 * (T['Net_PnL'] > 0).mean(), 1)]})
            summ.to_excel(w, sheet_name="Summary", index=False)
            fy.round(2).to_excel(w, sheet_name="By_FY")
            stage.round(2).to_excel(w, sheet_name="By_Stage")
            exits.round(2).to_excel(w, sheet_name="Exits")
            T.to_excel(w, sheet_name="Trades", index=False)
            if E is not None:
                ST.round(2).to_excel(w, sheet_name="Entry_Times_Summary")
                FYE.round(2).to_excel(w, sheet_name="Entry_Times_By_FY")
                E.to_excel(w, sheet_name="Entry_Times_Daily", index=False)
            D.to_excel(w, sheet_name="Daily", index=False)
    except Exception as e:
        print(f"   [WARN] Excel summary not written ({e}); CSVs are.")
    print(f"\n[SAVED] daily_log.csv, trades.csv, entry_time_pnl.csv, backtest_summary.xlsx in {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated YYYY-MM-DD")
    ap.add_argument("--summary", action="store_true", help="only rebuild the logs from finished days")
    a = ap.parse_args()

    print("=" * 62)
    print(">>> GAMMA SHORT - FINAL BACKTEST <<<")
    print("=" * 62)
    print(f"Basis: IV {IV_MODE}, check fallback {CHECK_FALLBACK}, day inputs {DAY_INPUTS}, "
          f"NW {'ON (' + NW_CHECK_MODE + ' check)' if NW_ENABLED else 'OFF'}")
    fp = config_fingerprint()
    run_dir = os.path.join(OUTPUT_BASE, f"run_{fp}")
    day_dir = os.path.join(run_dir, "days")
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(run_dir, "config.json"), "w") as f:
        json.dump({k: (v.tolist() if isinstance(v, np.ndarray) else
                       (str(v) if isinstance(v, (pd.Timestamp, set)) else v))
                   for k, v in globals().items() if k.isupper() and not k.startswith("_")},
                  f, indent=1, default=str)
    print(f"Results: {run_dir}")
    if a.summary:
        return summarise(day_dir, run_dir)

    luts = load_cw_luts()
    nwl = load_nw_lut()
    events = load_event_dates()
    inputs = load_day_inputs()
    only = {s.strip() for s in a.only.split(",") if s.strip()} or None
    files = list_files(only)
    done = {n[:-4] for n in os.listdir(day_dir) if n.endswith(".pkl")}

    def _k(f):
        ds = os.path.basename(os.path.dirname(f)); return f"{ds[4:]}-{ds[2:4]}-{ds[:2]}"
    todo = [f for f in files if _k(f) not in done]
    print(f"[RUN] {len(files)} tick days in range, {len(files) - len(todo)} already done, "
          f"{len(todo)} to run on {WORKERS} workers")

    ctx = dict(luts=luts, nwl=nwl, inputs=inputs, events=events)
    t0 = time.time()
    if todo:
        if WORKERS <= 1:
            _init_worker(ctx)
            it = ((f, process_day(f)) for f in todo)
            for n, (f, r) in enumerate(it, 1):
                with open(os.path.join(day_dir, f"{r['Date']}.pkl"), "wb") as fh:
                    pickle.dump(r, fh)
                if n % 25 == 0 or n == len(todo):
                    print(f"   {n}/{len(todo)}  ({(time.time() - t0) / 60:.1f} min)", flush=True)
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=WORKERS, initializer=_init_worker,
                                                        initargs=(ctx,)) as ex:
                futs = {ex.submit(process_day, f): f for f in todo}
                for n, fu in enumerate(concurrent.futures.as_completed(futs), 1):
                    try:
                        r = fu.result()
                        with open(os.path.join(day_dir, f"{r['Date']}.pkl"), "wb") as fh:
                            pickle.dump(r, fh)
                    except Exception as e:
                        print(f"   [ERROR] {futs[fu]}: {e}")
                    if n % 25 == 0 or n == len(todo):
                        print(f"   {n}/{len(todo)}  ({(time.time() - t0) / 60:.1f} min)", flush=True)
    summarise(day_dir, run_dir)


if __name__ == "__main__":
    main()
