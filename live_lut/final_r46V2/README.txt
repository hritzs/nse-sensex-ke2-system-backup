FINAL PRODUCTION BOOK - CW + NW, comfort-zone floor
(locked 2026-09-01; REBASED TO NO-ROLL 2026-09-17, round 48)

NO-ROLL BASE (round 48): live always held the entry strike all day; the
old sim micro-rolled from 13:00. Re-simulated on the production config,
no-roll wins +916 PnL / +9.2 MDD on the flat CW book (5 of 7 FYs better,
both time-halves positive) - the sim now matches live. restrike_check in
apply_strategy.py is permanently disabled (ROLL_ENABLED=False).
Canonical cache: round48_noroll (ps1 = no-roll production config).

Replay (1,373 days, no-roll, same LUTs/floor/NW):
  comfort book Total ~10,655-10,800 / MDD ~134
  (10,655/133.8 by the conservative offline NW accounting that captures
   50 of 61 NW days; production accounting adds ~+100-160)
  flat CW book (no floor/NW): 10,816 / 130.3, 807 trades
Yearly (comfort, offline accounting): FY20-21 1,190 | FY21-22 2,435 |
  FY22-23 1,927 | FY23-24 1,026 | FY24-25 1,865 | FY25-26 1,565 |
  FY26-27 647 (partial)
Legacy-roll reference (superseded): 9,988/121.1.

=========================== DAILY PROCEDURE ===========================
DTE 3-7 days: current-week only, normal scan 09:16-13:30. No floor, no NW.

DTE 1-2 (expiry / one-day-out) days:
 1. IV FLOOR, ALL DAY: no current-week entry at ANY minute where the CW
    adjusted Build IV < 0.12. (Owner's comfort-zone rule; the sweep
    confirmed 0.12/0.12 as the best floor pair: 9,988/121.1.)
 2. 09:16-09:20: CW scan vs stage LUTs at floor-passing minutes.
 3. First scanned minute after 09:20 with no trade: ONE check of
    NW_LUT.csv (day's 09:16 next-week features: NW IV ratio = NW ATM
    quoted IV x sqrt(cal/trading NW-DTE) / blended IDV; NW build IV;
    norm OG gap). YES -> sell NEXT-WEEK ATM straddle: SL 15 bps,
    delta-hedge to neutral at |net|>0.10, exit 15:15, no TP. Day over.
 4. NW declined -> CW scan resumes to 09:59 (expiry) / 13:30 (DTE2),
    still subject to the floor. One trade per day, always.
=======================================================================

CW block: bothsplit, ADJ -0.007/+0.005, T=(13,5,2,2,6) (V-curve validated
vs all logical alternatives + unconstrained search), tax floor -3, veto
1:1.70 2:1.275 3:1.225 4:1.175 5:1.15 6:1.125, half slippage (entry
1.0/0.5/0.25 Rs; hedge 0.4/0.3/0.2 bps of futures notional).
intra_frac = 1.00 (intra-minute hedges FULLY to neutral; minute-start
0.75). SL 16/14, TP ramp, TV/TV4/14:57 IV-drop exits, PtsAllowed hedging
(div 57, str_d 4, floor 8 bps), |NetDelta| 0.25/0.20 triggers last two
DTEs, NO restrike (round 48 - hold entry strike all day). Evidence-gated
+ monotone closure across DTE 1-7 and all six axes; nesting
L16<=L17<=L18>=L19>=L20 holds.

Shortlist on the no-roll base (2026-09-17):
- ADOPT AT NEXT RE-SOLVE: S2 str edge 1.125 (+178 full, +210 cold CV,
  both directions positive; sole survivor of the 24-variant edge audit).
- OPTIONAL: elite sizing pack (depth-score 1.10 + trough-add, +726).
- CANDIDATE TABLES: FUNNELED AND REJECTED (2026-09-17). All three
  no-roll-solved candidates (stiff-mid (13,10,1,5,9)@L0070_H0050,
  (13,7,2,3,11)@L0100_H0060, (13,6,0,4,8)@L0070_H0060) lost every
  split-half and walk-forward fold cold - ~50% capture at ~2x MDD,
  the standard overfit profile. Their comfort-book edges (+84..+369)
  were in-sample flattery. These E tables remain production.
- STAGED: round 49 wide-SL below str 0.95 (ps2 x1.5 / ps3 x2.0 on the
  no-roll base; PARAM_SETS comment in phase1_extract.py has the spec).
- FALLBACK MODE (automation down): 0918 table + its 267-cell 1-step
  core at 09:19 only = 9,649/132.6 flat (~89% of book, no staging).
- Walk-forward calibration stands: forward solves capture ~45% of
  full-sample credit - haircut all in-sample numbers accordingly.

NW block (NW_LUT.csv, 17+7 cells): min-cut on 1,093 measured NW days,
monotone (cheaper NW ratio / higher NW premium / middle gap safer;
DTE2 row subset of DTE1 row). Direction flip vs CW IV axis is
deliberate: the NW position lives through the event a rich NW IV
prices. NW leg ~10 trades/yr - PAPER-VERIFY in first live year.
Robustness: book survives 2x NW slippage (approx -50).

SIZING APPENDIX (validated, optional - flat 1.0 is default):
Depth score per CW entry = # of {IV-ratio -1 bucket, str-ratio +1
bucket} neighbor cells still ON in the stage LUT (0,1,2).
OOS (split-half): s0 +23/91% win, s1 +10/69%, s2 +7/60% - stable.
One step OUTSIDE the boundary: -6/47% -> never trade outside, any size.
If margin-constrained, taper 1.0/0.5/0.25 by score: +33% return per
unit capital, MDD -21%, at 0.54x deployment. If book-MDD-constrained,
stay flat. Standalone 2-variable monotone sizing models (pooled and
per-DTE) were built and tested OOS: they recover only 17%/43% of the
LUT's capital efficiency - the LUT IS the monotone model, completed.

Retired with measurements: NW shifts/blends on fired days (all gates,
all weights), skip-day entries at any size (one step outside boundary
= -6/trade), conviction maps from day-start features (rank inverts
OOS), scale-up tranches (positive but leverage-dominated), skew rr25/
bf25 (null), overnight-IV-change filters (benign U, no rule),
single all-day LUT (~60 candidates, 3 construction methods, ceiling
6.6k/MDD>190 vs 9.9k/121 - time-conditioning is irreducible),
IV/str/OG-bracket tax-curve reshaping incl. linear-early at low IV
(0/45 curves positive at the 0.12 bracket), 0.12-partition split LUTs
(cross-validated: +1,100 in-sample becomes -3,100/-3,500 cold),
round-47 restrike-at-straddle/2-from-12:00 roll rule (-1,281 PnL,
+34.5 MDD on flat book net of 0.7/0.4bps impact; legacy 1bp micro-roll
from 13:00 stays), str-conditioned tranche ladders (all cutoff sets:
gated entries avg +16..19/trade, better than book avg - laddering
them loses 95-390), earlier/later entry timing (1 min earlier -3.4..
-11.8/trade, 1 min later -1.9..-3.7/trade: fired minute is a measured
local optimum both directions), recency-weighted solves (walk-forward:
starves evidence, forward PnL -26..-35%), first-hit reweighting
(oscillates; shadowed-row mass encodes the cascade nesting evidence -
solver design vindicated).
Walk-forward calibration: forward-solved books capture ~45% of the
full-sample credit on FY24-27 - apply that haircut to any in-sample
number when setting live expectations.
Adopt-at-next-resolve candidate: str edge 1.125 added (S2): +178
in-sample, +210 cold-CV (both directions positive), MDD/trades
unchanged. Sole survivor of the 24-variant axis-edge audit.
