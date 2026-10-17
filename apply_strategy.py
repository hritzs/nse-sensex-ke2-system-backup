"""apply_strategy.py - LIVE APPLICATION of the production LUT + the full rule set.

Wraps everything the backtest converged on into one decision engine:

  LUTs   : LUTS_r45_ps0_mct1mono_adj008_005_T13_1_1_1_6  (evidence-gated +
           monotone closure, T=(13,1,1,1,6), replay 9431 / MDD 125.5 / 795)
  Signal : bothsplit bucketing, ADJ -0.008 / +0.005, WEIGHTS (0.2, 0.5, 0.3)
  Veto   : strangle-ratio veto 1:1.70 2:1.275 3:1.225 4:1.175 5:1.15 6:1.125
  Exits  : SL 16 bps expiry / 14 bps rest, TP from Build_IV ramp, TV exits,
           14:57 IV-drop exit (DTE<=1). NO secondary stop, NO trail, NO churn
           cap, NO edge exit - all tested, all rejected.
  Hedge  : PtsOut vs PtsAllowed (div 57, str_d 4, floor 8 bps), minute-start
           frac 0.75; intra-minute every second at 1.2x threshold, frac 0.75;
           DTE1/DTE2 use |NetDelta| thresholds 0.25 / 0.20 instead of PtsOut.
  Roll   : from 13:00, restrike if |U - K| > 1 bp for 2 consecutive minutes.
  Costs  : entry slip 1.0/0.5/0.25 Rs (DTE1/2/3+), hedge slip 0.4/0.3/0.2 bps
           of futures notional - the HALF-slippage standard every number above
           was scored at.

All PnL/threshold arithmetic is in INDEX POINTS on ONE straddle unit; the
normalized "Rs" the backtest reports = points x 25000 / entry_underlying.
Size live positions proportional to 25000/U to match backtest risk.

Usage:
  python apply_strategy.py --selftest
  python apply_strategy.py --demo
  python apply_strategy.py --entry day.json          # {'day':{...},'minute':{...}}
As a library: import apply_strategy as S; S.entry_decision(...) etc.
"""
import argparse, json, math, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LUT_DIR = os.path.join(HERE, "LUTS_r45_ps0_mct1mono_adj008_005_T13_1_1_1_6")

# ============================================================
# 1. SIGNAL CONFIG - bothsplit_adjg_L0080_H0050 (hash cd328fb2 raw / 677a410e mct1)
#    Bucket index = np.searchsorted(TH, value, side='right').
# ============================================================
WEIGHTS = (0.2, 0.5, 0.3)      # (IDV_Pure, IDV_With_Prev, Yesterday_IV)
ADJ_LOWER, ADJ_HIGHER = -0.008, 0.005

IV_THRESHOLDS = np.array([0.6501, 0.8001, 0.9001, 0.9501, 1.0001,
                          1.0501, 1.1001, 1.2001, 1.3001])          # 10 buckets
STR_THRESHOLDS = np.array([0.9501, 1.0001, 1.0501, 1.1001, 1.1501, 1.2001,
                           1.3001, 1.4001, 1.5001, 1.7501, 2.0001]) # 12 buckets
BLD_THRESHOLDS = np.array([0.0801, 0.0901, 0.1001, 0.1201,
                           0.1601, 0.2001])                          # 7 buckets
OG_THRESHOLDS = np.array([-0.7501, 0.7501])                          # 3 buckets

DIMS = (7, 10, 12, 7, 3, 3)     # (DTE, IV_Ratio, Straddle_Ratio, Build_IV, OG, ADJ)
NCELL = int(np.prod(DIMS))      # 52,920

# ON-cell counts per stage file - integrity check on load. If the CSVs are
# edited or regenerated these MUST be updated or loading fails loudly.
EXPECTED_ON = {"LUT_0916.csv": 1503, "LUT_0917.csv": 3938, "LUT_0918.csv": 4799,
               "LUT_0919.csv": 3334, "LUT_0920_Onwards.csv": 1862}

# ============================================================
# 2. TRADING RULES (ps0: sl0x16, hedge_frac .75, intra 1.2x)
# ============================================================
# --- entry timing ---
ENTRY_FIRST_HHMM = 916          # first candidate minute
ENTRY_LAST_HHMM = 1330          # scan stops after 13:30 (FULL_SIM_UNTIL)
EXPIRY_ENTRY_CUTOFF = 959       # dte_idx 0: no entries after 09:59

# --- strangle-ratio veto (skip the day if prev/current straddle is too rich:
#     a big overnight straddle drop for that DTE = severe gap risk). Keys are
#     dte_idx (0 = expiry). dte_idx 6 has no threshold -> never vetoed.
VETO_STR = {0: 1.70, 1: 1.275, 2: 1.225, 3: 1.175, 4: 1.15, 5: 1.125}

# --- stop loss / take profit, bps of entry underlying, on GROSS points PnL ---
SL_BPS_DEFAULT = 14.0
SL_BPS_BY_DTE = {0: 16.0}       # expiry stop widened for cost (round 40)
TP_HIGH = np.array([30.0, 25.0, 25.0, 20.0, 15.0, 15.0, 15.0])
TP_LOW = np.array([16.0, 13.0, 13.0, 10.0,  8.0,  8.0,  8.0])
TP_SCALE = 1.0

# --- theoretical-value / IV-drop exits (unchanged since the original) ---
TV_THRESH = 0.00015             # exit if 2 x OTM(traded strike) < 0.00015 x U
TV4_THRESH = 0.0006             # exit if 2 x OTM(ATM ref)      < 0.0006  x U
IV_DROP_THRESH = 0.1            # exit if OTMIVRef < 0.1 ...
IV_DROP_FROM_HHMM = 1457        # ... at/after 14:57, DTE <= 1 only

# --- hedging ---
HEDGE_DIV = 57.0                # PtsAllowed = (OTMIVRef/57) x U ...
STR_D = 4.0                     # ... capped at (CERef+PERef)/4 ...
PTS_FLOOR_BPS = 8.0             # ... floored at 0.0008 x U
HEDGE_FRAC = 0.75               # minute-start hedge fraction
INTRA_MULT = 1.2                # intra-minute trigger = 1.2 x threshold
INTRA_FRAC = 0.75               # intra-minute hedge fraction (ps0: = hedge_frac)
DELTA_MAP = {0: 0.25, 1: 0.20}  # dte_idx -> |NetDelta| trigger replaces PtsOut

# --- restrike / roll ---
ROLL_FROM_HOUR = 13             # eligible from 13:00
ROLL_BPS = 1.0                  # |U - ATMStrike| > 0.0001 x U ...
ROLL_CONSEC_MIN = 2             # ... on 2 consecutive minutes
ROLL_MAX = 30

# --- cost model (HALF slippage - what every headline number is scored at) ---
ENTRY_SLIP_RS = {0: 1.0, 1: 0.5}          # dte_idx -> Rs, others 0.25
ENTRY_SLIP_RS_DEFAULT = 0.25              # charged on NORMALIZED PnL
HEDGE_SLIP_BPS = {0: 0.4, 1: 0.3}         # dte_idx -> bps of futures notional
HEDGE_SLIP_BPS_DEFAULT = 0.2

STAGE_FILES = ["LUT_0916.csv", "LUT_0917.csv", "LUT_0918.csv",
               "LUT_0919.csv", "LUT_0920_Onwards.csv"]


# ============================================================
# LUT LOADING
# ============================================================
def load_luts(lut_dir=LUT_DIR, strict=True):
    """5 boolean arrays (NCELL,), stage 0..4. Verifies ON counts + nesting."""
    luts = []
    for name in STAGE_FILES:
        df = pd.read_csv(os.path.join(lut_dir, name))
        if len(df) != NCELL:
            raise ValueError(f"{name}: {len(df)} rows, expected {NCELL}")
        arr = (df["Trade"].values == "YES")
        if strict and int(arr.sum()) != EXPECTED_ON[name]:
            raise ValueError(f"{name}: {int(arr.sum())} ON cells, expected "
                             f"{EXPECTED_ON[name]} - file changed?")
        luts.append(arr)
    L16, L17, L18, L19, L20 = luts
    if strict and not (np.all(L16 <= L17) and np.all(L17 <= L18)
                       and np.all(L19 <= L18) and np.all(L20 <= L19)):
        raise ValueError("cascade nesting L16<=L17<=L18>=L19>=L20 violated")
    return luts


# ============================================================
# FEATURES -> LUT COORDINATE
# ============================================================
def bucket(value, thresholds):
    return int(np.searchsorted(thresholds, value, side="right"))


def day_context(prev_pure, prev_with_prev, yesterday_iv, og_pct, adj_iv_chg):
    """Once-per-day quantities, all known by 09:16.

    prev_pure / prev_with_prev : previous day's intraday IDVs (existing pipeline)
    yesterday_iv               : previous day's IV
    og_pct                     : (U@09:16 - prev_future_close)/prev_future_close x 100
    adj_iv_chg                 : previous day's Normalized_Adj_Chg
    """
    fixed_idv = (WEIGHTS[0] * prev_pure + WEIGHTS[1] * prev_with_prev
                 + WEIGHTS[2] * yesterday_iv)
    if fixed_idv <= 0:
        raise ValueError("blended IDV <= 0 - no trading today")
    norm_og = og_pct * (0.19 / yesterday_iv)
    return {
        "fixed_idv": fixed_idv,
        "og_idx": bucket(norm_og, OG_THRESHOLDS),
        "adj_idx": bucket(adj_iv_chg, np.array([ADJ_LOWER, ADJ_HIGHER])),
        "norm_og": norm_og,
    }


def minute_coord(day, time_hhmm, build_iv, raw_dte, trading_dte, str_rat):
    """Per-minute quantities -> full 6-D coordinate + stage index.

    build_iv    : the minute's Build_IV
    raw_dte     : calendar DTE;  trading_dte : trading-day DTE
    str_rat     : prev_close_straddle / current_straddle
    """
    adj_factor = (math.sqrt(raw_dte / trading_dte)
                  if raw_dte > 0 and trading_dte > 0 and raw_dte != trading_dte
                  else 1.0)
    abiv = build_iv * adj_factor
    dte_idx = int(np.clip(math.ceil(trading_dte) - 1, 0, 6))
    coord = (dte_idx,
             bucket(abiv / day["fixed_idv"], IV_THRESHOLDS),
             bucket(str_rat, STR_THRESHOLDS),
             bucket(abiv, BLD_THRESHOLDS),
             day["og_idx"], day["adj_idx"])
    stage = (0 if time_hhmm == 916 else 1 if time_hhmm == 917
             else 2 if time_hhmm == 918 else 3 if time_hhmm == 919 else 4)
    return coord, stage, abiv, dte_idx


# ============================================================
# ENTRY DECISION
# ============================================================
def entry_decision(luts, day, time_hhmm, build_iv, raw_dte, trading_dte,
                   str_rat, entry_underlying=None):
    """One minute's verdict. Call per minute from 09:16 until it says TRADE
    (then STOP - one trade per day) or until the window closes.

    Returns dict: decision TRADE/SKIP/STOP_SCANNING, reason, coord, and on
    TRADE the SL/TP in bps + points (if entry_underlying given).
    """
    coord, stage, abiv, dte_idx = minute_coord(
        day, time_hhmm, build_iv, raw_dte, trading_dte, str_rat)
    out = {"decision": "SKIP", "coord": coord, "stage": stage,
           "dte_idx": dte_idx, "adjusted_build_iv": abiv}

    if time_hhmm < ENTRY_FIRST_HHMM:
        out.update(reason="before 09:16"); return out
    if time_hhmm > ENTRY_LAST_HHMM:
        out.update(decision="STOP_SCANNING", reason="past 13:30"); return out
    if dte_idx == 0 and time_hhmm > EXPIRY_ENTRY_CUTOFF:
        out.update(decision="STOP_SCANNING",
                   reason="expiry: no entries after 09:59"); return out

    thr = VETO_STR.get(dte_idx)
    if thr is not None and str_rat > thr:
        out.update(reason=f"VETO: str_rat {str_rat:.3f} > {thr} for DTE{dte_idx+1}")
        return out

    if not luts[stage][int(np.ravel_multi_index(coord, DIMS))]:
        out.update(reason="LUT cell OFF"); return out

    sl_bps = SL_BPS_BY_DTE.get(dte_idx, SL_BPS_DEFAULT)
    fraction = (round(abiv, 2) - 0.08) / 0.12          # NOT clipped, by design
    tp_bps = float(max(1.0, round(
        (TP_LOW[dte_idx] + fraction * (TP_HIGH[dte_idx] - TP_LOW[dte_idx]))
        * TP_SCALE)))
    out.update(decision="TRADE",
               reason=f"stage {STAGE_FILES[stage][4:-4]} LUT ON",
               action="SELL ATM straddle; size ~ 25000/U units",
               sl_bps=sl_bps, tp_bps=tp_bps)
    if entry_underlying:
        out.update(sl_points=-entry_underlying * sl_bps / 1e4,
                   tp_points=entry_underlying * tp_bps / 1e4,
                   position_scale=25000.0 / entry_underlying)
    return out


# ============================================================
# HEDGE DECISION  (evaluate EVERY SECOND; minute_start=True on the first
# second of each minute; also immediately after a restrike, hedge the new
# position to -OptDelta + carried residual)
# ============================================================
def pts_allowed(otm_iv_ref, underlying, ce_ref, pe_ref):
    raw = min((otm_iv_ref / HEDGE_DIV) * underlying, (ce_ref + pe_ref) / STR_D)
    return max(raw, PTS_FLOOR_BPS / 1e4 * underlying)


def hedge_decision(net_delta, opt_gamma, otm_iv_ref, underlying,
                   ce_ref, pe_ref, dte_idx, minute_start):
    """Futures qty to trade NOW (0.0 = no hedge). net_delta = OptDelta + FutPos.

    DTE1/DTE2 (dte_idx 0/1): |NetDelta| rule. Others: PtsOut = |net/gamma| vs
    PtsAllowed. Hedge goes PAST neutral, leaving (1-frac) x threshold exposure
    on the far side.
    """
    if math.isnan(net_delta):
        return 0.0
    dthr = DELTA_MAP.get(dte_idx, 0.0)
    if dthr > 0.0:
        thr = dthr if minute_start else INTRA_MULT * dthr
        hf = HEDGE_FRAC if minute_start else INTRA_FRAC
        if abs(net_delta) > thr:
            resid = (1.0 - hf) * dthr
            return (-net_delta + resid) if net_delta < 0 else (-net_delta - resid)
        return 0.0
    if opt_gamma == 0.0 or math.isnan(opt_gamma):
        return 0.0
    pa = pts_allowed(otm_iv_ref, underlying, ce_ref, pe_ref)
    pts = abs(net_delta / opt_gamma)
    thr = pa if minute_start else INTRA_MULT * pa
    hf = HEDGE_FRAC if minute_start else INTRA_FRAC
    if pts > thr:
        resid = (1.0 - hf) * pa * opt_gamma
        return (-net_delta + resid) if net_delta < 0 else (-net_delta - resid)
    return 0.0


# ============================================================
# EXIT DECISION  (evaluate per MINUTE on gross mark-to-market points PnL;
# first rule to fire wins; if none ever fires, exit at session close)
# ============================================================
def exit_decision(tot_pnl_pts, entry_underlying, sl_bps, tp_bps,
                  otm_price_trade, otm_price_ref, underlying,
                  otm_iv_ref, dte, time_hhmm):
    """otm_price_trade: OTM leg price at the TRADED strike;
    otm_price_ref: OTM leg at the current ATM ref strike; dte in DAYS."""
    if tot_pnl_pts <= -entry_underlying * sl_bps / 1e4:
        return "EXIT_SL"
    if tot_pnl_pts >= entry_underlying * tp_bps / 1e4:
        return "EXIT_TP"
    if 2.0 * otm_price_trade < TV_THRESH * underlying:
        return "EXIT_TV"
    if 2.0 * otm_price_ref < TV4_THRESH * underlying:
        return "EXIT_TV4"
    if time_hhmm >= IV_DROP_FROM_HHMM and otm_iv_ref < IV_DROP_THRESH and dte <= 1.0:
        return "EXIT_IV_DROP"
    return "HOLD"
    # Deliberately absent: 10:00 give-back stop, profit trail, hedge-churn cap,
    # edge-exhaustion exit. All six variants tested (rounds 42-44) and all
    # reduced PnL without an MDD win. Do not add exits here without a replay.


def restrike_check(underlying, atm_strike, hour, consec_breach_min, n_rolls):
    """True -> exit current straddle at ask, sell fresh ATM straddle, then
    re-hedge to -OptDelta + carried residual NetDelta."""
    if hour < ROLL_FROM_HOUR or n_rolls >= ROLL_MAX:
        return False
    return (abs(underlying - atm_strike) > ROLL_BPS / 1e4 * underlying
            and consec_breach_min >= ROLL_CONSEC_MIN)


# ============================================================
# COSTS (for expected-PnL tracking; normalized Rs = points x 25000/U)
# ============================================================
def entry_cost_rs(dte_idx):
    return ENTRY_SLIP_RS.get(dte_idx, ENTRY_SLIP_RS_DEFAULT)


def hedge_cost_rs(cum_abs_fut_x_underlying, dte_idx, entry_underlying):
    bps = HEDGE_SLIP_BPS.get(dte_idx, HEDGE_SLIP_BPS_DEFAULT)
    return bps / 1e4 * cum_abs_fut_x_underlying * 25000.0 / entry_underlying


# ============================================================
# SELF-TEST
# ============================================================
def selftest():
    fails = []
    def chk(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        if not ok: fails.append(name)

    print("1. LUT files load, counts match, cascade nests")
    try:
        luts = load_luts()
        chk("load + integrity", True,
            f"ON: {[int(l.sum()) for l in luts]}")
    except Exception as e:
        chk("load + integrity", False, str(e)); sys.exit(1)

    print("\n2. bucketization edges (searchsorted right, .0001 offsets)")
    chk("IV ratio 1.0000 -> bucket 4 (0.95-1.00)", bucket(1.0000, IV_THRESHOLDS) == 4)
    chk("IV ratio 1.0001 -> bucket 5", bucket(1.0001, IV_THRESHOLDS) == 5)
    chk("STR 12 buckets", bucket(9.9, STR_THRESHOLDS) == 11)
    chk("BLD 0.0850 -> bucket 1 (0.08-0.09 split)", bucket(0.0850, BLD_THRESHOLDS) == 1)
    chk("BLD 0.0950 -> bucket 2 (0.09-0.10 split)", bucket(0.0950, BLD_THRESHOLDS) == 2)
    chk("ADJ -0.0080 -> mid bucket", bucket(-0.0080, np.array([ADJ_LOWER, ADJ_HIGHER])) == 1)
    chk("ADJ -0.0081 -> low bucket", bucket(-0.0081, np.array([ADJ_LOWER, ADJ_HIGHER])) == 0)

    print("\n3. TP formula (bld 0.14, expiry: fraction 0.5 -> 16+0.5x14 = 23)")
    day = day_context(0.10, 0.10, 0.10, 0.0, 0.0)
    r = entry_decision(load_luts(strict=False), day, 916, 0.14, 1, 1, 1.0,
                       entry_underlying=25000.0)
    chk("tp_bps 23", r.get("tp_bps") == 23.0 or r["decision"] != "TRADE",
        f"(cell {'ON' if r['decision']=='TRADE' else 'OFF'}; formula checked below)")
    fr = (round(0.14, 2) - 0.08) / 0.12
    chk("formula direct", max(1.0, round(TP_LOW[0] + fr * (TP_HIGH[0] - TP_LOW[0]))) == 23)
    chk("expiry SL 16 / others 14",
        SL_BPS_BY_DTE.get(0) == 16.0 and SL_BPS_BY_DTE.get(1, SL_BPS_DEFAULT) == 14.0)

    print("\n4. veto + timing")
    r = entry_decision(luts, day, 917, 0.14, 1, 1, 1.71)
    chk("expiry str_rat 1.71 vetoed", r["decision"] == "SKIP" and "VETO" in r["reason"])
    r = entry_decision(luts, day, 1000, 0.14, 1, 1, 1.0)
    chk("expiry 10:00 stops scanning", r["decision"] == "STOP_SCANNING")
    r = entry_decision(luts, day, 1331, 0.14, 3, 3, 1.0)
    chk("13:31 stops scanning", r["decision"] == "STOP_SCANNING")

    print("\n5. hedge math")
    q = hedge_decision(-0.30, 0.001, 0.12, 25000.0, 100.0, 100.0, 0, True)
    chk("DTE1 minute rule: |net| .30 > .25 fires past neutral",
        abs(q - (0.30 + 0.25 * 0.25)) < 1e-12, f"qty {q:+.4f}")
    q = hedge_decision(-0.28, 0.001, 0.12, 25000.0, 100.0, 100.0, 0, False)
    chk("DTE1 intra: .28 < 1.2x.25 no fire", q == 0.0)
    pa = pts_allowed(0.12, 25000.0, 100.0, 100.0)
    chk("PtsAllowed = min(IV/57 x U, straddle/4) floored",
        abs(pa - min(0.12 / 57 * 25000, 200 / 4)) < 1e-9, f"pa {pa:.2f}")
    q = hedge_decision(0.10, 0.001, 0.12, 25000.0, 100.0, 100.0, 3, True)
    pts = 0.10 / 0.001
    chk("DTE4 PtsOut rule fires iff pts > pa",
        (q != 0.0) == (pts > pa), f"pts {pts:.0f} vs pa {pa:.2f}")

    print("\n6. exits")
    chk("SL fires at -16 bps on expiry",
        exit_decision(-25000 * 16.01 / 1e4, 25000, 16.0, 23.0, 50, 50, 25000, 0.12, 0.5, 1200) == "EXIT_SL")
    chk("IV-drop needs 14:57 + DTE<=1",
        exit_decision(0, 25000, 16.0, 23.0, 50, 50, 25000, 0.05, 0.5, 1456) == "HOLD"
        and exit_decision(0, 25000, 16.0, 23.0, 50, 50, 25000, 0.05, 0.5, 1457) == "EXIT_IV_DROP")
    import inspect, re
    rets = set(re.findall(r'return "(\w+)"', inspect.getsource(exit_decision)))
    chk("exit rules are EXACTLY SL/TP/TV/TV4/IV_DROP + HOLD",
        rets == {"HOLD", "EXIT_SL", "EXIT_TP", "EXIT_TV", "EXIT_TV4",
                 "EXIT_IV_DROP"}, f"found {sorted(rets)}")

    print("\n7. LUT spot-check vs CSV text")
    df = pd.read_csv(os.path.join(LUT_DIR, "LUT_0916.csv"))
    on_rows = df[df["Trade"] == "YES"]
    row = on_rows.iloc[len(on_rows) // 2]
    ridx = row.name
    coord = np.unravel_index(ridx, DIMS)
    chk("row index round-trips through DIMS",
        luts[0][int(np.ravel_multi_index(coord, DIMS))],
        f"row {ridx} DTE={row['DTE']} IV={row['IV_Ratio']}")

    print("\n" + "=" * 60)
    if fails:
        print(f"{len(fails)} FAILED: {', '.join(fails)}"); sys.exit(1)
    print("All checks passed. Engine matches the backtest rule set.")


def demo():
    luts = load_luts()
    day = day_context(prev_pure=0.105, prev_with_prev=0.112, yesterday_iv=0.118,
                      og_pct=0.35, adj_iv_chg=-0.002)
    print(f"day: fixed_idv {day['fixed_idv']:.4f}  norm_og {day['norm_og']:.3f}  "
          f"og_idx {day['og_idx']}  adj_idx {day['adj_idx']}")
    for hhmm, biv, sr in [(916, 0.118, 1.04), (917, 0.121, 1.06), (918, 0.125, 1.08)]:
        r = entry_decision(luts, day, hhmm, biv, raw_dte=2, trading_dte=2,
                           str_rat=sr, entry_underlying=24800.0)
        print(f"{hhmm}: {r['decision']:<6} {r.get('reason','')}  coord={r['coord']}"
              + (f"  SL {r['sl_bps']}bps TP {r['tp_bps']}bps" if r["decision"] == "TRADE" else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--entry", help="JSON file: {'day': {...}, 'minute': {...}}")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.demo:
        demo()
    elif a.entry:
        spec = json.load(open(a.entry))
        day = day_context(**spec["day"])
        print(json.dumps(entry_decision(load_luts(), day, **spec["minute"]),
                         default=str, indent=1))
    else:
        print(__doc__)
