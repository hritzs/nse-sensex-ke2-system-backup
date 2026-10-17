# GammaShort all-expiry simulator

The new simulator is an opt-in service, implemented in
[gamma_weekly_server.py](gamma_weekly_server.py) and
[gamma_weekly_engine.py](gamma_weekly_engine.py). It runs at port 8000 instead
of the old simulator when `--gamma-weekly` is selected. The original
`simulation_server_2.py`, its frontend and database are retained.

## Single-terminal startup

From the project directory in Ubuntu WSL:

```bash
bash start_system.sh --check --gamma-weekly --with-api
bash start_system.sh --restart --gamma-weekly --with-api
```

The second command stops verified project listeners, including indicative
8100 and FastAPI 9003, then starts decoder, adapter, new simulator, indicative
and FastAPI. Unknown processes cause the entire stop preflight to fail before
any signal is sent. SIGINT is followed by bounded TERM/KILL only for the
verified PID and process start time. It does not call `pkill` or the
bhavcopy-copying indicative maintenance script. Dependencies and input
readiness are checked before a Gamma restart. No live restart was performed
while implementing these changes.

Use `bash start_system.sh --restart --with-api` for the original simulator.
`--restart` and `--reuse-existing` are mutually exclusive. Omitted services
are not selected for stopping. Keep this terminal open; Ctrl+C stops its new
children. Logs are in `logs/system-*/`.

## Required actual input files

Run this read-only check:

```bash
./venv/bin/python scripts/check_gamma_inputs.py
```

Set Linux-accessible paths before the launcher:

```bash
export GS_LUT_DIR=/actual/path/to/new-model-luts
export GS_EXPIRY_INPUTS=/actual/path/to/expiry_inputs.csv
export GS_EVENT_PATH=/actual/path/to/EventDates.xlsx
```

These are placeholders to replace with real files. XLSX reading additionally
requires openpyxl. Event CSV is also supported with a `Date` header; a verified
calendar with no event dates can have a header and no rows.

The five LUT files must be named `LUT_0916.csv`, `LUT_0917.csv`,
`LUT_0918.csv`, `LUT_0919.csv`, `LUT_0920_Onwards.csv` and have exactly
52,920 rows in the backtest's product order, with a `Trade` column.
The local 34,650-row Custom tables are incompatible. They are retained and
never reshaped, relabeled, or treated as new-model tables.

Normalized expiry reference CSV header:

```csv
Date,Expiry,prev_straddle,adj_iv_chg,yesterday_iv,prev_future,prev_pure,prev_with_prev
```

Supply one authoritative row per trade date AND expiry. Dates are ISO;
IVs are decimals. `adj_iv_chg` is the already normalized change
`change * 0.1 / prior2_base`. Straddle and future are rupees.
No example financial values are supplied as real inputs.

Alternatively, `GS_DAILY_CSV` plus an explicit `GS_DAILY_EXPIRY=YYYY-MM-DD`
uses the supplied backtest's daily loader for that expiry alone. A current-week
row is never applied to later expiries. Missing daily/event inputs produce
WAITING_INPUTS and no fabricated trades. Missing LUTs produce LUT_UNAVAILABLE
when that diagnostic is reached; they do not gate fixed-time entries.

The currently available files do not satisfy the new model's LUT/reference
requirements for the expiries in the loaded master. Supplying these paths is an operational prerequisite. The
launcher refuses a Gamma `--restart` if required expiry references/calendar
are missing, keeping the current services running.

## Expiries and lanes

All listed, unexpired NIFTY option expiries within 42 calendar days are
discovered from the local master. That master has no weekly/monthly flag:
this is a near-term listed ladder, not an authoritative weekly classification.
Use `GS_WEEKLY_EXPIRIES=YYYY-MM-DD,YYYY-MM-DD` for an explicit verified list,
or adjust `GS_WEEKLY_HORIZON_DAYS`. Missing exchange contracts remain missing.

Every expiry has independent 0916, 0917, 0918, 0919 and NOW lanes. Fixed
entries use the last observed features at/before the minute and the first
available entry row at/after it. Startup after a fixed minute cannot reconstruct
missing observations. NOW schedules its own lane at the next minute boundary
to retain the same feature/fill convention. Each expiry has one NOW request
per day, preserved across restart; there is no automatic second NOW trade.

As requested, fixed lanes ignore LUT/veto/floor/event-time entry gates and
record the signal. Event sizing still applies. The separate production CW/NW
signal-selection branch is disabled; no additional production trade is started.

## Calculation and limits

The adapter parses the supplied backtest into an isolated module, retaining its
constants and functions and removing numba decorators. It does not run main,
mutate thread environment variables, run a historical batch, or rewrite the
backtest file. Only production selection is disabled, the missing-LUT
diagnostic is explicit, and historical backward fills are removed for live use.

The inherited mathematics includes OTM Black-Scholes IV, adjusted build IV,
DTE buckets, fixed strike, TP ramp, 16/14-bps SL, time-value/IV-drop exits,
delta/points hedge thresholds, 75% minute and full intraminute adjustment,
30-row option fills, 3-row hedge fills, normalization to 25,000, entry costs,
hedge costs and event sizing. Exit order is SL, TP, TV, TV4, IV_DROP, EOD.

Raw source LTP/bid/ask is consumed at `9001/ke2-source`. Each expiry derives
its own underlying and hedge bid/ask from call-put parity. Invalid/crossed
books are rejected. Option quotes must be no more than ten seconds old;
a second is recorded only when an observed quote updated. Missing book sides
are not replaced with prices. DTE is derived as calendar days to the listed
expiry at 15:30 IST because the feed does not supply the historical DTE column.

The source lacks exchange timestamps and trade-print markers. Consequently
this is a local receipt-time, quote-observation simulation, not certified
tick-backtest parity. Historical initial backward fill is forbidden. The
underlying basis and DTE convention are explicit API metadata.

Forward averages need later observations. Results remain OPEN_PROVISIONAL
until a stable exit has the required later observations; closed results are
retained. EOD is finalized only after an observed row reaches the model's grid
end (15:37 from 3 June 2026; 15:27 earlier). If actual feed data stops earlier,
the position remains provisional. No EOD tick is invented.

## HTTP output for application developers

This simulator API is separate from KE2 market data 9002 and the FastAPI
tester 9003. Its new UI is at `http://127.0.0.1:8000/`.

| Method/path | Output |
| --- | --- |
| GET /api/state | Full service state, basis, errors, expiries and per-expiry lane results |
| GET /api/health | Same state; source connectivity is not proof of fresh market data |
| GET /api/expiries | Listed eligible expiries and classification method |
| GET /api/runs | Same full state |
| POST /api/now | Body `{"expiry":"YYYY-MM-DD"}`; schedules next minute or returns missing-input/session error |

A state response has `service`, `trade_date`, `source_connected`, `source`,
`expiries`, `results`, `errors`, `last_error`, `last_evaluation`,
`model_sha256`, `reference_fingerprint`, `expiry_classification`,
`horizon_days` and `basis`. Under `results[expiry].lanes[lane]` a calculated
lane includes:

- `status`: READY, WAITING_INPUTS, WAITING_QUOTES, NO_ENTRY_DATA,
  INVALID_MARKET_DATA, OPEN_PROVISIONAL or CLOSED.
- `signal`: EVENT_PRE_0918, VETO, FLOOR, LUT_ON, LUT_OFF,
  LUT_UNAVAILABLE or NO_DATA. It does not block entry.
- `strike`, `entry_time`, `entry_underlying`, `entry_credit`,
  `dte_idx`, `build_iv`, `sl_bps`, `tp_bps`, `exit_time`,
  `exit_reason`, `stable_fill`.
- `gross` and `hedge_notional`: unscaled model output normalized to 25,000;
  `gross_pnl`, `entry_cost`, `hedge_cost`, `net_pnl`: event-adjusted
  final accounting components. Entry cost remains unscaled on event days,
  matching the supplied fixed-entry comparison.
- `event`, `size`, `filters_gate_entry:false`,
  `pnl_basis:"normalized_to_25000"`.

An OPEN_PROVISIONAL lane's EOD reason/time is the current calculation tail,
not a final executed exit. Consumers must use status and stable_fill.
The original simulator API/frontend is available by launching the original
script; it is not wire-compatible with this opt-in UI.

## Persistence, validation and rollback

Default new database: `data/gamma_weekly_simulation.db`; override with
`GS_SIM_DB`. SQLite WAL stores observations, lane results and NOW requests.
Keys include date and expiry. The model and input-file hashes bind a database
to one configuration; changed inputs/model require a new database path.
Old databases remain intact.

```bash
./venv/bin/python -m unittest discover -s tests -p 'test_gamma_weekly.py' -v
./venv/bin/python -m unittest discover -s tests -p 'test_system_operations.py' -v
```

Fixtures test both expiries end-to-end through capture, SQLite, the inherited
calculations and HTTP. They also test filter bypass, event sizing, missing LUTs,
per-expiry references, restart persistence and no backward fill. These checks
do not certify real multicast packets or tick-history equivalence.

Rollback uses the original simulator selection and retained database; stop the
new foreground stack and run `bash start_system.sh --with-api` after checking
ports. The pre-edit launcher is retained in `backups/gamma_before_changes/`.

## Reference snapshots found during Windows-share verification

The exact daily CSV and event calendar from the configured Windows share were
reachable and copied into data/gamma_inputs with file hashes in PROVENANCE.json.
These remain private offline runtime data. The simulator/checker automatically
uses the local daily and event snapshots unless explicit paths override them.

The daily file includes an authoritative Expiry column, which is now used for
binding each row. Its latest date is 2026-10-16 with current expiry 2026-10-19.
That expiry is missing from the stale local master. The separate dailyNW CSV
ends 2026-09-28 and is retained for inspection, not treated as current.
The loaded master currently exposes 2026-10-27 and 2026-11-23 in the horizon;
matching full per-expiry reference rows remain unavailable. The daily CSV's
additional NW columns are preserved without silently substituting a different
model's inputs. The actual event calendar now loads, removing that blocker.
New-model LUTs remain absent at their configured path.

The original daily CSV can therefore be used without GS_DAILY_EXPIRY when it
has its own authoritative Expiry column; explicit binding remains supported
for a CSV lacking that column. Farther expiries still need their own inputs.
