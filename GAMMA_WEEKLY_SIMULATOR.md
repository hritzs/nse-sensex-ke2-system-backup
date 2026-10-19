# GammaShort all-expiry simulator

This is the new simulator selected by `--gamma-weekly`. It uses the numerical
functions from `GammaShort_Backtest_Final.py`, with its own SQLite database
and five independent lanes per listed near-term NIFTY expiry.

## Start the complete stack and LAN feed

Run in Ubuntu WSL from the project root:

```bash
bash start_system.sh --restart --gamma-weekly --with-api --lan
```

This starts decoder 9001, KE2 feed 9002, Gamma simulator 8000, indicative-close
8100 and FastAPI tester 9003. `--restart` stops positively identified project
listeners first. Any unknown listener aborts the stop plan before signals.

`--lan` binds the KE2 feed to 0.0.0.0. Other machines use
`ws://<Windows-server-LAN-IP>:9002/`; the observed server address on
19 October 2026 is `172.16.9.119`. The simulator and FastAPI tester remain local.
`KE2_HOST` can choose a particular bind address instead of `--lan`.

Refresh authoritative inputs before starting when required:

```bash
bash start_system.sh --refresh-inputs --restart --gamma-weekly --with-api --lan
```

Refresh copies the share's current master, daily CSV, event calendar and exact
LUTs, verifies checksums, validates master coverage and retains the prior
master. It does not manufacture tokens or terminate processes itself.
PowerShell uses a policy override for that invocation only; persistent Windows
execution policy is not changed. `KE2_INPUT_SHARE` overrides the share root.

A read-only startup/configuration check is:

```bash
./venv/bin/python scripts/check_gamma_inputs.py --strict
bash start_system.sh --check --restart --gamma-weekly --with-api --lan
```

Keep the launcher terminal open. Ctrl+C stops its children. Logs are in
`logs/system-<IST-time>-<pid>/`. No automatic boot service is installed.

## Actual model inputs

The configured source root is `\\172.16.1.85\Shared`.

| Input | Local path | Source beneath that share |
| --- | --- | --- |
| NSE master | shared/GreekTokens.csv | GreekTokens.csv |
| Daily references | data/gamma_inputs/GammaShortDailydata.csv | Hardik/Project_Codes/GammaShortDailyProcess/GammaShortDailydata.csv |
| Event dates | data/gamma_inputs/EventDates.xlsx | EventDates.xlsx |
| Model tables | live_lut/final_r46V2/ | GammaShortData/GammaShort/LUTS_r46V2_cwnw_adj007_005_T13_5_2_2_6/ |

The five CW LUTs each have 52,920 rows in dimensions (7,10,12,7,3,3).
Expected YES counts, verified in the imported files, are 1298, 3309, 4534,
3828 and 2441 for 0916, 0917, 0918, 0919 and 0920_Onwards respectively.
The old 34,650-row Custom LUTs remain separate and are not used by this model.

The imported NW_LUT has 24 YES cells whereas the author's expected count is
23. It is retained with provenance; the separate production NW strategy is
disabled. The requested five fixed-time comparisons use the five CW tables.

A daily CSV's own Expiry column binds its rows. A current-week straddle,
future or IV is never substituted for another expiry. Alternatively set
`GS_EXPIRY_INPUTS` to a normalized CSV with this header:

```csv
Date,Expiry,prev_straddle,adj_iv_chg,yesterday_iv,prev_future,prev_pure,prev_with_prev
```

Dates are ISO, IV is decimal and prices are rupees. adj_iv_chg is already
normalized as change*0.1/prior2_base. `GS_DAILY_CSV`, `GS_DAILY_EXPIRY`,
`GS_EVENT_PATH` and `GS_LUT_DIR` support explicit overrides.

As explicitly selected, missing daily references do not block fixed trades:
the full market-driven calculations run and the verdict records
REFERENCE_UNAVAILABLE. Missing compatible LUTs record LUT_UNAVAILABLE when
that diagnostic is reached. A missing event calendar blocks calculation as
WAITING_INPUTS because it changes actual event sizing. No non-event calendar
or substitute daily input is invented. Event CSV with a Date header is also
supported; XLSX uses openpyxl.

## Expiries and independent timing

Discovery uses actual unexpired NIFTY option expiries within 42 calendar days.
The current imported ladder contains 2026-10-19, 2026-10-27, 2026-11-03,
2026-11-09, 2026-11-17 and 2026-11-23: six expiries and 30 lanes.
Holiday-adjusted dates come from the master, not Tuesday arithmetic.

The master has no weekly/monthly flag. This is the listed near-term ladder,
including the monthly date where it overlaps the weekly cycle. Far quarterlies
are excluded. Set `GS_WEEKLY_EXPIRIES` to an explicit listed allowlist or
adjust `GS_WEEKLY_HORIZON_DAYS` when a different universe is intended.

Every expiry has 0916, 0917, 0918, 0919 and NOW. Each entry holds its own strike
and state. Fixed lanes use features at/before the target minute and the first
available execution row at/after it. NOW schedules the next minute boundary.
There is one NOW request per date/expiry; repeated calls retain the first.

Startup after a fixed minute cannot reconstruct absent observations. A missing
entry is reported as NO_ENTRY_DATA; no past trade is fabricated. Observations
already captured in the new database can restore that day's lanes on restart.

Fixed comparisons bypass LUT, veto, floor and event-time entry gates, while
recording their verdicts. Event position sizing and costs still apply.
The separate signal-selected CW/NW production branch does not create trades.

## Full calculations and live-feed limits

The adapter isolates the supplied source through its AST. It retains its
constants and numerical functions, strips numba decorators, avoids main and
thread-environment assignments, disables production selection, and removes
historical backward fills. It does not edit the original backtest.

Both the reference-backed path and market-only reference-unavailable path use
the author's simulate_cw functions. These include OTM Black-Scholes IV,
weekend-adjusted build IV and DTE buckets, fixed position strike, TP ramp,
16/14-bps SL, TV/TV4/IV-drop exits, delta/points hedge thresholds, 75% minute
and full intraminute adjustment, 30-row option fills, 3-row hedge fills,
normalization to 25,000, entry and hedge costs, and event sizing.
Exit priority is SL, TP, TV, TV4, IV_DROP, EOD.

Actual raw LTP/bid/ask comes from 9001/ke2-source. Each expiry independently
derives a synthetic underlying and hedge book from call-put parity. Crossed,
missing and nonpositive book sides are rejected. Both legs must have been
received within ten seconds. A grid second is recorded only after an actual
quote update; resampling replaces the complete row set for that second.

The source lacks exchange timestamps and trade-print markers. The model uses
local receipt time and quote-observation OTM IV, with DTE derived as calendar
days to expiry 15:30 IST. Historical tick/DTE equivalence is not certified.
Historical backward filling from future observations is prohibited.

Forward fills need later observed rows. Calculated lanes stay OPEN_PROVISIONAL
until a stable exit or actual observed grid end. A non-EOD exit is finalized
only after at least 61 subsequent observed rows; closed results are retained.
EOD requires an observed row reaching the model grid end: 15:37 from 3 June
2026, 15:27 earlier. A feed that stops earlier leaves a provisional tail.
An OPEN_PROVISIONAL EOD reason/time is not a final executed exit.

## HTTP contract for the application developer

Open `http://127.0.0.1:8000/` for the expiry selector and five-lane UI.
This API is separate from KE2 market data 9002 and FastAPI tester 9003.

| Request | Response |
| --- | --- |
| GET /api/state | Full state, six expiry datasets, lanes and capability/basis metadata |
| GET /api/health | Same state; inspect freshness and errors, not connectivity alone |
| GET /api/expiries | Eligible dates and classification method |
| GET /api/runs | Same full state |
| POST /api/now | JSON expiry; returns saved next-minute entry HHMM or validation error |

State includes service, trade_date, source_connected, source, expiries, results,
errors, last_error, last_evaluation, model_sha256, reference_fingerprint,
daily_reference_availability, lut_available, event_calendar_available,
expiry_classification, horizon_days and basis.

feed_freshness[expiry] reports the latest accepted quote's FRESH/STALE/
WAITING_QUOTES status, quote_age_seconds and last_quote_received. This is
expiry-level liveness, not a guarantee that every held option has just traded.

Each calculated results[expiry].lanes[lane] includes:

- status: READY, WAITING_INPUTS, WAITING_QUOTES, NO_ENTRY_DATA,
  INVALID_MARKET_DATA, OPEN_PROVISIONAL or CLOSED;
- signal: EVENT_PRE_0918, VETO, FLOOR, LUT_ON, LUT_OFF, LUT_UNAVAILABLE,
  REFERENCE_UNAVAILABLE or NO_DATA;
- strike, entry_time, entry_underlying, entry_credit, dte_idx, build_iv,
  sl_bps, tp_bps, exit_time, exit_reason and stable_fill;
- gross and hedge_notional: unscaled model output normalized to 25,000;
- gross_pnl, entry_cost, hedge_cost and net_pnl: event-adjusted accounting;
- event, size, filters_gate_entry:false and pnl_basis:normalized_to_25000.

Event gross/notional scale by 1/3; entry cost remains unscaled, matching the
supplied fixed comparison. All five lanes are shown immediately at startup.
Waiting/invalid lanes do not contain fabricated PnL.

## Persistence, tests and rollback

Default new DB is data/gamma_weekly_simulation.db; override GS_SIM_DB.
WAL tables store observations, lane results, NOW requests and settings.
Keys include trade date and expiry. Model/input hashes bind a configuration
per trade date. New-day reference versions can reuse the database; a
same-day configuration change requires a new DB path to preserve old results.

```bash
./venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Tests include two-expiry capture->SQLite->model->HTTP, false/missing LUTs,
event sizing/costs, moving-market hedge/PnL parity without daily references,
NOW persistence, date-specific versions and complete-second replacement.
A full-day five-lane fixture ran under one second on this host; that is a
fixture measurement, not an exchange latency SLA.

Rollback: stop the Gamma foreground stack and launch the retained original
simulator with `bash start_system.sh --restart --with-api --lan`.
The original simulator and data/minute_simulation.db are retained.
Do not point legacy replay scripts at the new Gamma DB.
