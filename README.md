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
