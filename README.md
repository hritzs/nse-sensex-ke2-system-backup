## Separate Windows applications

[Download the separate WhiteGamma and KE2 prerelease](https://github.com/hritzs/nse-sensex-ke2-system-backup/releases/tag/windows-split-v0.2.0-rc1).

- KE2Feed.exe owns decoder, KE2, indicative and Swagger.
- WhiteGamma.exe owns only the WhiteGamma app.
- GammaSimulator.exe independently owns the all-expiry Gamma simulator.

See [startup and LAN configuration](SEPARATE_WINDOWS_APPS.md) and
[standalone architecture](standalone/ARCHITECTURE.md).
These are release candidates: full frozen Windows GUI/multicast acceptance is
pending; the build PC blocked a pandas native dependency through Application Control.
Existing portfolio state and historical databases are excluded.

# Run the complete system

From Ubuntu WSL run bash run_all.sh. This starts/restarts the decoder, KE2,
Gamma simulator, indicative dashboard and FastAPI tester, enables LAN access
and prints their endpoints. Each run verifies and refreshes both NSE and BSE
masters from the configured share before restart. Use bash run_all.sh --check
for read-only local preflight, or --no-refresh-masters for deliberate offline use.
See [startup and endpoints](KE2_STARTUP_AND_API.md) for details.

# NSE + SENSEX decoder, KE2 feed and Gamma simulator

Current startup and API documentation:
[Startup/LAN](KE2_STARTUP_AND_API.md), [Gamma full strategy and HTTP API](GAMMA_WEEKLY_SIMULATOR.md),
[KE2 client contract](KE2_DEVELOPER_API.md), [FastAPI tester](KE2_FASTAPI_TESTER.md),
[HLD/LLD](docs/architecture/README.md), [Backup/restore](docs/backup/BACKUP_RESTORE.md).

From Ubuntu WSL:

```bash
bash start_system.sh --restart --gamma-weekly --with-api --lan
```

Feed clients use ws://SERVER_LAN_IP:9002/. The Gamma UI is localhost:8000,
indicative localhost:8100, and Swagger localhost:9003/docs. Optional
--refresh-inputs verifies/copies the authoritative master, daily references,
event calendar and exact model LUTs before starting.

Six near-term NIFTY expiries each have independent 0916/0917/0918/0919/NOW
lanes using the supplied full numerical model. Missing daily signal
references are diagnostic; prices and historical trades are never invented.
Original simulator/database and legacy decoder messages remain available.

The public source repository excludes credentials, licenses, runtime databases
and original Git history. See model/feed capability limits in the linked docs.

---
# NIFTY Minute Straddle — Clean Rewrite

This is a clean replacement for the legacy `simulation_server.py` architecture.

## Included

- `simulation_server.py` — minute-straddle backend only
- `frontend/index.html` — standalone UI
- `frontend/app.js` — UI/API logic only
- `frontend/styles.css` — styling only
- SQLite persistence at `data/minute_simulation.db`

## Removed

- LUT engine
- five-stage signal engine
- GammaShortDailydata.csv dependency
- EventDates.xlsx dependency
- historical signal/reference calculations
- embedded frontend HTML
- frontend-side API monkey-patching

## Runtime

Backend remains on port `8000` and consumes the existing C++ WebSocket feed on `ws://127.0.0.1:9001`.

Use the venv that already contains `websockets`:

```bash
./venv/bin/python3 -m py_compile simulation_server.py
./venv/bin/python3 -u simulation_server.py
```

Then open `http://127.0.0.1:8000/`.

## State model

- `market.*` is the live current ATM reference.
- `position.*` is the frozen entry strike and its live marks.
- `min_straddle` / `max_straddle` are extrema of the frozen position strike only.
- Completed trades keep financial state/history frozen while the live market reference continues updating.
- DTE uses the 09:15–15:40 IST trading session; one session is 385 minutes.
- Backend Greeks are always per 1 quantity. The UI multiplies them by the selected display size.
- Hedge cash flow is persisted and is never reset on completion.
