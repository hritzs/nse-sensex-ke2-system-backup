# Start and use the NSE + SENSEX feed system

Updated 19 October 2026. Run commands in Ubuntu WSL from:

```bash
cd /home/administrator/projects/nse-fo-low-latency-decoder
```

## Start everything with one script

~~~bash
cd /home/administrator/projects/nse-fo-low-latency-decoder
bash run_all.sh
~~~

This is the complete default stack: decoder, KE2, Gamma weekly simulator,
indicative dashboard and FastAPI tester. It restarts only verified project
services. It enables LAN binding for KE2, the simulator and FastAPI; indicative
already binds on all interfaces. It prints all service URLs and supported
HTTP routes and saves them to logs/system-<timestamp>-<pid>/endpoints.txt.
Keep the terminal open; Ctrl+C stops the services started by that launcher.

~~~bash
bash run_all.sh --check           # read-only; no restart
bash run_all.sh --refresh-inputs  # refresh approved input sources, then restart
~~~

Current LAN entry points (recheck the server IP if it changes):

| Service | LAN URL |
| --- | --- |
| KE2 WebSocket feed | ws://172.16.9.119:9002/ |
| Gamma simulator | http://172.16.9.119:8000/ |
| Indicative dashboard | http://172.16.9.119:8100/ |
| FastAPI Swagger | http://172.16.9.119:9003/docs |

Use KE2_PUBLIC_HOST to override the LAN address printed by automatic detection.
Swagger documents all tester request bodies and responses. The simulator GET
routes are /api/health, /api/state, /api/expiries and /api/runs; POST /api/now
schedules the selected expiry's simulation lane. The indicative GET routes
are /, /health and /api/data. Feed messages are documented in
[KE2_DEVELOPER_API.md](KE2_DEVELOPER_API.md).
On the other LAN computer test ports 9002, 8000, 8100 and 9003 as needed.
Binding on all interfaces does not establish that remote routing works.

## One-command stack, including LAN access

```bash
bash start_system.sh --restart --gamma-weekly --with-api --lan
```

This starts the corrected C++ decoder (9001), KE2 feed (9002), new Gamma
simulator (8000), indicative-close (8100) and FastAPI tester (9003).
Restart stops only positively identified project listeners, including
indicative. Unknown listeners abort before signals. Keep this terminal open;
Ctrl+C stops its new child processes. Logs are printed at startup.

The other LAN system connects to `ws://<server-LAN-IP>:9002/`.
The observed Windows/WSL server address is `172.16.9.119` on 19 October 2026,
so its current endpoint is `ws://172.16.9.119:9002/`.
It must send JSON subscriptions from [the developer API](KE2_DEVELOPER_API.md).
Use the server's current address after any network/address change.

`--lan` binds the feed on 0.0.0.0; without it the feed is localhost-only.
`KE2_HOST` selects a particular bind address. The simulator and FastAPI tester
remain localhost with --lan; --lan-all (used by run_all.sh) exposes those too. Windows firewall/WSL routing must permit TCP 9002 from the
client; test from that other Windows system with:

```powershell
Test-NetConnection 172.16.9.119 -Port 9002
```

For a minimal two-terminal feed without simulator/UI, run the decoder first,
then `./venv/bin/python -u ke2_feed_server.py --host 0.0.0.0 --port 9002`.

## Refresh current input files before startup

```bash
bash start_system.sh --refresh-inputs --restart --gamma-weekly --with-api --lan
```

The explicit refresh copies and hashes the current NSE master, daily CSV,
event calendar and exact LUTs from the configured Windows share, then
validates/installs the master with a rollback copy. PowerShell's policy
override applies only to that invocation. No persistent policy is changed.
Use KE2_INPUT_SHARE to override the share root.

Read-only configuration/port checks:

```bash
./venv/bin/python scripts/check_gamma_inputs.py --strict
bash start_system.sh --check --restart --gamma-weekly --with-api --lan
```

The new simulator uses six actual near-term NIFTY expiry dates and five lanes
per date: 0916, 0917, 0918, 0919 and NOW. All hedge/exit/PnL calculations use
the supplied backtest functions. Fixed entries record filters without using
them to block entry. Missing daily verdict references record
REFERENCE_UNAVAILABLE; missing event sizing information blocks calculation.

Read [Gamma timings and final HTTP API](GAMMA_WEEKLY_SIMULATOR.md) before use.
Its database is data/gamma_weekly_simulation.db. The original simulator and
minute_simulation.db remain available as the rollback selection:

```bash
bash start_system.sh --restart --with-api --lan
```

`--reuse-existing` deliberately keeps existing processes and cannot upgrade
them. It is mutually exclusive with `--restart`. No auto-boot daemon is added.

The following sections document component commands and KE2 message examples.
The legacy simulator command in section 6 is for the retained original UI.

## 1. What runs where

| Component | Address | Role |
| --- | --- | --- |
| C++ decoder | ws://127.0.0.1:9001/ | Existing analytics and legacy clients |
| Raw decoder source | ws://127.0.0.1:9001/ke2-source | Internal input to KE2 adapter |
| KE2 adapter | ws://127.0.0.1:9002/ | New market-data JSON API |
| FastAPI tester | http://127.0.0.1:9003/docs | Optional Swagger/HTTP test client |
| Selected NIFTY simulator | http://127.0.0.1:8000/ | Gamma with --gamma-weekly; retained legacy otherwise |
| Indicative-close service | http://127.0.0.1:8100/ | Separate existing dashboard |

```text
NSE 233.1.2.5:34330 ----+
                      +--> C++ decoder :9001 --> existing NIFTY simulator :8000
BSE 233.1.2.4:2004 ----+          |
                                +--> /ke2-source --> KE2 adapter :9002
                                                     |
                                                     +--> KE2 developer's client
```

The KE2 adapter is a feed API, not a new simulator UI. It does not place orders.
Port 8100 is independent of KE2 startup. Redis, Docker, KDB, and an external
reference-data service are not required for this direct live-feed path.

## 2. Prerequisites and current limits

Check tools, input files, and occupied ports:

```bash
command -v cmake g++
./venv/bin/python -c 'import sys, websockets; print(sys.version); print(websockets.__version__)'
ls -lh shared/GreekTokens.csv shared/BSEIndexTokens.csv
ip -4 addr
ss -ltnp
```

The existing environment has Python 3.14.4 and websockets 17.0.1. If creating
a separate Python environment is necessary, install `requirements-ke2.txt`
in that environment. The C++ build uses the repository's existing native
dependencies; CMake reports a missing dependency if the machine lacks one.

The multicast network must deliver BOTH configured streams to this machine.
A successful WebSocket connection alone does not prove exchange data is flowing.

- NSE master: `shared/GreekTokens.csv`, imported from the authoritative
  share-root export. Current near-term NIFTY coverage includes 19/27 October
  and 3/9/17/23 November 2026. Refresh/provenance validation does not invent
  missing exchange tokens. One PAGEIND scientific-notation ID is excluded.
- BSE master: `shared/BSEIndexTokens.csv`. It includes the 29 October 2026
  SENSEX future, ID 864571. Blank `OpType` on `Type=FUTURE` is supported.
- Both decoder and adapter load their masters at startup, not continuously.
  Any authorized master update must reach both processes before their next
  authorized start. Use the explicit --refresh-inputs operation described above.
- Current BSE wire-token matching still requires live verification.
- Actual LTP, bid, ask, and decoded NSE OI are supplied. Unsupported fields
  are null or omitted; no external data or substitute prices are invented.
- Lot/tick size, volume, quantities, exchange/trade timestamps, BSE OI,
  exchange-time bars, EOD, holidays, ban lists, and results are unavailable.
  See `KE2_FEED_IMPLEMENTATION.md` for the full capability limits.

## 3. Build and verify before startup

These commands use a separate build directory and do not replace
`build/nse_decoder`:

```bash
cmake -S . -B build-ke2 -DCMAKE_BUILD_TYPE=Release
cmake --build build-ke2 --target nse_decoder test_bse_feed feed_transport_fixture -j2
ctest --test-dir build-ke2 --output-on-failure
./venv/bin/python -m unittest -v test_ke2_feed
KE2_TRANSPORT_FIXTURE="$PWD/build-ke2/feed_transport_fixture" ./venv/bin/python -m unittest -v test_ke2_transport.TransportTests.test_bse_packet_to_ke2_and_legacy
```

The tests exercise protocol behaviour and deterministic packets; they do not
prove live multicast delivery or freshness of today's master.

## 4. Start the core feed (terminal A)

Check that port 9001 is free. If it is occupied, identify the existing process
and obtain approval before replacing/restarting it. Do not run two decoders.

From the project root:

```bash
./build-ke2/nse_decoder
```

This is the NEW binary. The older `./build/nse_decoder` does not contain the
new source route or the new BSE listener wiring.

The decoder defaults the BSE multicast interface to 0.0.0.0 (kernel-selected).
For a machine with multiple interfaces, use its actual feed-facing address:

```bash
BSE_LOCAL_IP=ACTUAL_LOCAL_INTERFACE_IP ./build-ke2/nse_decoder
```

Replace the placeholder; do not reuse the old hard-coded 172.16.1.9 unless it
actually belongs to this machine. NSE group/port are unchanged.

Expected startup log markers:

- `Multicast JOIN SUCCESS: group=233.1.2.5 port=34330`
- `Multicast JOIN SUCCESS: group=233.1.2.4 port=2004`
- `[WS] LIVE ON PORT 9001`

A join-success message means membership was accepted, not that packets have
arrived. Check packet-rate diagnostics and actual quotes during feed activity.
The decoder also appends to `dalal_backend.log`.

## 5. Start the KE2 adapter (terminal B)

From the same project root, with port 9002 free:

```bash
./venv/bin/python -u ke2_feed_server.py --host 127.0.0.1 --port 9002
```

It connects to `ws://127.0.0.1:9001/ke2-source`. It can run before the decoder
and will retry the source connection, but no prices appear until raw events
arrive. It binds to localhost by default.

Optional explicit configuration:

```bash
./venv/bin/python -u ke2_feed_server.py --root "$PWD" --source ws://127.0.0.1:9001/ke2-source --host 127.0.0.1 --port 9002
```

There is no HTTP REST endpoint on 9002. Send JSON over a WebSocket connection.

## 6. Start the retained LEGACY NIFTY simulator if needed (terminal C)

This is independent of the KE2 adapter. If port 8000 already has your simulator
or a container, keep that instance; do not start a duplicate or change databases.

For the existing direct Python launch:

```bash
NIFTY_WS_URI=ws://127.0.0.1:9001 MINUTE_HTTP_HOST=127.0.0.1 MINUTE_HTTP_PORT=8000 ./venv/bin/python -u simulation_server_2.py
```

This uses the script's existing default database `data/minute_simulation.db`
and restores its existing simulator state. If your normal deployment uses
`MINUTE_SIM_DB` or Docker, retain that configuration and database path.

Open `http://127.0.0.1:8000/`. Read-only checks:

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/expiries
curl -s http://127.0.0.1:8000/api/runs
```

The health response includes `status`, `websocket`, `db`, `trade_date`,
`selected_expiry`, `expiry_mode`, `option_quotes`, and existing view-state
fields. HTTP `status: OK` is not proof of fresh exchange prices. Missing weekly
contracts can still make NEXT unavailable.

This existing UI remains a NIFTY simulator. SENSEX is supplied through the
KE2 feed API; this change does not add a SENSEX trading strategy or UI.

## 7. Indicative-close service (optional, terminal D)

Leave an existing service on 8100 running. For a cold start with that port free,
from the project root:

```bash
./indicative_close_server/indicative_close_server
```

Its existing local data files must be present. It consumes its own NSE F&O
and CM streams and is not the BSE/SENSEX feed source for KE2.

The existing `start_indicative_server.sh` is NOT a passive launcher: it copies
bhavcopy from a Windows share, rebuilds the binary, and can terminate/restart
the service on 8100. Do not use it as part of a routine KE2 startup unless
those changes and that restart are intended and authorized.

## 8. What the KE2 WebSocket API returns

### First response: capabilities

A connection immediately receives:

```json
{
  "type": "capabilities",
  "session_id": "<server-generated-session-id>",
  "data": {
    "quotes": true,
    "subscriptions": true,
    "snapshots": true,
    "instrument_master": true,
    "heartbeat": true,
    "bar_1m": false,
    "eod": false,
    "reference_daily": false,
    "exchange_ts": false,
    "last_trade_ts": false,
    "volume": false,
    "lot_size": false,
    "tick_size": false,
    "bse_oi": false,
    "reason": "Only fields decoded by this repository are supplied; no external sources."
  },
  "master_sources": [
    {"exchange": "NSE", "path": "shared/GreekTokens.csv", "modified_ts": "<actual file modification time>"},
    {"exchange": "BSE", "path": "shared/BSEIndexTokens.csv", "modified_ts": "<actual file modification time>"}
  ]
}
```

Angle-bracket values in examples are explanatory placeholders, not market data.

### Instrument master

Request:

```json
{"type":"instrument_master","request_id":"master-sensex","expiry":"2026-10-29"}
```

The server sends ALL symbols for that expiry in chunks of at most 500. A
SENSEX future record within those chunks has this shape:

```json
{
  "instrument_id": 864571,
  "exchange": "BSE",
  "symbol": "SENSEX",
  "instrument_type": "FUTIDX",
  "expiry": "2026-10-29",
  "strike": null,
  "option_type": null,
  "lot_size": null,
  "tick_size": null,
  "unavailable_fields": ["lot_size", "tick_size"]
}
```

The enclosing message is `{"type":"instrument_master","request_id":"master-sensex","data":[...],"more":true}`.
Continue reading until `more:false`. Filter by symbol on the client. Omit
`expiry` to request every instrument in the loaded masters.

### Subscribe: first test the SENSEX future only

```json
{
  "type": "subscribe",
  "request_id": "sensex-future",
  "expiry": "2026-10-29",
  "underlyings": [{"symbol":"SENSEX","segment":"FO_IDX","future":true}]
}
```

With the inspected master, acknowledgement:

```json
{
  "type": "subscribed",
  "request_id": "sensex-future",
  "session_id": "<server-generated-session-id>",
  "instrument_ids": [864571],
  "pending": []
}
```

Then an immediate quote snapshot. If no quote has arrived yet:

```json
{
  "type": "quote",
  "snapshot": true,
  "exchange_ts": null,
  "ts": "<snapshot time in IST>",
  "data": [{
    "instrument_id": 864571,
    "ltp": null,
    "bid": null,
    "ask": null,
    "oi": null,
    "volume": null,
    "exchange_ts": null,
    "last_trade_ts": null,
    "ltq": null,
    "bid_qty": null,
    "ask_qty": null,
    "received_ts": null
  }],
  "unavailable_fields": ["exchange_ts", "last_trade_ts", "volume"]
}
```

When actual prices arrive, streaming messages use `type:quote`,
`snapshot:false`, a send-time `ts`, top-level `exchange_ts:null`, and the
same per-instrument fields. Decoded LTP/bid/ask replace nulls with actual
rupee values. No example prices are provided as if they were live data.
`received_ts` is local decode time, not exchange or trade time.
Streaming changes are conflated to once per second per connection.

### Subscribe to a strike window

```json
{
  "type":"subscribe",
  "request_id":"sensex-options",
  "expiry":"2026-10-29",
  "underlyings":[{
    "symbol":"SENSEX",
    "segment":"FO_IDX",
    "future":true,
    "options":{"mode":"around_future","strikes_each_side":6,"types":["CE","PE"]},
    "extra_strikes":[]
  }]
}
```

This selects up to six listed strikes each side of the nearest strike to the
observed exact-expiry future, plus the centre, for both types. The window
recentres as that future moves. `extra_strikes` must be listed strikes and
stay pinned. Until that future is observed, `pending` includes
`future_quote_unavailable`; option selection is not guessed.

NIFTY uses `symbol:NIFTY`, `segment:FO_IDX`, and its actual listed expiry,
for example `2026-10-27` in the current master. Stock requests use `FO_STK`.

Each subscribe replaces that connection's current subscription. Because
NIFTY and SENSEX can have different monthly expiry dates, use separate
connections for those two cycles. Existing legacy clients are independent.

### Other requests and responses

| Send | Return |
| --- | --- |
| `{"type":"snapshot"}` | Current full subscription as a quote with snapshot:true |
| `{"type":"unsubscribe","instrument_ids":[864571]}` | unsubscribed acknowledgement; removes those IDs, including during recentering |
| `{"type":"resume","session_id":"<saved-id>"}` | Full snapshot of that inactive saved session |
| `{"type":"eod"}` or `{"type":"bar_1m"}` | error with code:unsupported |
| Invalid/unlisted expiry, bad segment, unlisted extra strike | error with code:invalid_request |

Unsubscribe acknowledgement also includes `request_id` (null if omitted)
and `instrument_ids`. Unsupported responses include `requested_type`,
`message`, and `capabilities`. Invalid-request responses include `message`.

Save the original session ID before disconnecting. Reconnect first receives a
new capabilities message; send resume with the OLD ID to recover the old
subscription. Resume does not send a subscribed acknowledgement; it sends the
snapshot. Sessions survive one hour after disconnection in adapter memory.
After an adapter restart, resend subscribe.

### Heartbeats

Every five seconds:

```json
{
  "type":"heartbeat",
  "ts":"<send time in IST>",
  "feed_status":"DEGRADED",
  "source_connected":true,
  "exchanges":{"BSE":"STALE"},
  "pending":[]
}
```

`source_connected:true` only proves the upstream WebSocket is connected.
`OK` requires a resolved nonempty subscription and fresh quotes for ALL
selected instruments within ten seconds. Quiet/untraded options can make the
overall status DEGRADED even when other quotes are moving. Check the actual
quote timestamps and pending reasons.

## 9. Ready-to-run API checks

These commands create only the probe's own subscription; they do not alter
other clients or start simulated trades.

Inspect SENSEX master data:

```bash
./venv/bin/python scripts/ke2_probe.py --symbol SENSEX --expiry 2026-10-29 --master
```

Test the exact SENSEX future:

```bash
./venv/bin/python scripts/ke2_probe.py --symbol SENSEX --expiry 2026-10-29 --future-only --seconds 20
```

Watch a small SENSEX option window:

```bash
./venv/bin/python scripts/ke2_probe.py --symbol SENSEX --expiry 2026-10-29 --seconds 30
```

Watch the listed NIFTY monthly cycle on a separate connection:

```bash
./venv/bin/python scripts/ke2_probe.py --symbol NIFTY --expiry 2026-10-27 --seconds 30
```

Add `--require-live` to fail the probe when no streaming row containing LTP
arrives. This is a basic liveness check, not a guarantee of exchange completeness.

## 10. Troubleshooting, shutdown, and rollback

| Symptom | Check |
| --- | --- |
| Cannot connect to 9002 | Adapter terminal, port occupancy, Python environment |
| Source connection refused | Decoder on 9001 and source URL |
| Source disconnected: no close frame received or sent | Internal decoder-to-adapter failure; use the updated binary/adapter, run the full stack, inspect source close/backpressure diagnostics; see docs/verification/SOURCE_DISCONNECT_FIX.md |
| Connected but no market_quote data | Confirm NEW binary; old wildcard WS accepts the path but sends legacy messages |
| BSE STALE; NSE active | BSE multicast membership, interface, port 2004 delivery, current BSE token matches |
| future_quote_unavailable | Exact-expiry future exists but has not supplied a quote |
| future_contract_unavailable | No matching future exists in the loaded master |
| No listed contracts | Correct symbol/expiry and refresh authoritative local master |
| NEXT remains null in existing UI | Weekly contracts absent from master/stream; monthly messages do not fix this |
| Lot/tick size or timestamps null | Current feed capability limit, not a REST lookup to retry |
| Address already in use | Identify existing service with ss; do not kill unrelated processes |

For foreground processes started from these instructions, use Ctrl+C in their
own terminals: optional simulator, adapter, then decoder. Do not stop the
independent indicative-close service unless that shutdown is intended.
Startup commands are manual; services do not automatically start after reboot.

Rollback: stop only the new foreground services you started, retain or
relaunch the original `./build/nse_decoder` under the previous approved
operational procedure, and leave the KE2 adapter stopped. The original
binary has not been overwritten. Source backups are in
`backups/ke2_feed_before_changes/`; no simulator/database migration is involved.

Developer integration contract: [KE2_DEVELOPER_API.md](KE2_DEVELOPER_API.md).
Complete JSON examples: [KE2_API_EXAMPLES.json](KE2_API_EXAMPLES.json).

## 11. Stock F&O checks

Stock futures/options use FO_STK. Cash stock quotes are not exposed here.
The probe now accepts stock symbols and infers FO_STK, or accepts an explicit
--segment FO_STK. Once the decoder and adapter are active:

```bash
./venv/bin/python scripts/ke2_probe.py --symbol RELIANCE --expiry 2026-10-27 --master
./venv/bin/python scripts/ke2_probe.py --symbol RELIANCE --expiry 2026-10-27 --future-only --seconds 20 --require-live
./venv/bin/python scripts/ke2_probe.py --symbol RELIANCE --expiry 2026-10-27 --seconds 30 --require-live
```

Six Python protocol tests and two real-WebSocket transport tests (SENSEX and
stock F&O) passed in isolation. The stock test covers raw future/option prices
and an OI event. These are synthetic packet tests, not live-market validation.

## FastAPI testing service

The original KE2 feed on 9002 remains WebSocket-only. A separate FastAPI tester
is available on 9003, with interactive Swagger at http://127.0.0.1:9003/docs.

```bash
bash start_system.sh --reuse-existing --with-api
```

Its dependencies are in .venv-ke2-api, already installed on this machine.
Detailed endpoint testing and fresh-checkout setup:
[KE2_FASTAPI_TESTER.md](KE2_FASTAPI_TESTER.md).
Use POST /sessions in Swagger, copy its top-level session_id, then test
snapshot, quotes, unsubscribe, reconnect and DELETE.

## 11. Updated all-service restart and Gamma weekly simulator

The launcher includes the independent indicative service at 8100. Explicitly
restart selected, verified project services with:

```bash
bash start_system.sh --restart --with-api
```

For the new five-lanes-per-expiry Gamma simulator, inspect the loaded model/input readiness, then use:

```bash
./venv/bin/python scripts/check_gamma_inputs.py
bash start_system.sh --restart --gamma-weekly --with-api
```

Gamma restart refuses to stop existing services when the event calendar or
eligible contracts are unavailable. Missing daily signal references are diagnostic. Unknown listeners are never killed. The original simulator
selection remains available, and the new simulator uses its own database.

Read [GAMMA_WEEKLY_SIMULATOR.md](GAMMA_WEEKLY_SIMULATOR.md) for required files,
all-expiry timing, fixed filter bypass, final HTTP output, provisional fills,
receipt-time limitations and rollback.

Full design: [HLD](docs/architecture/HLD.md), [LLD](docs/architecture/LLD.md).
Offline/GitHub procedure: [Backup and restore](docs/backup/BACKUP_RESTORE.md).
