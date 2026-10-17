# NSE C++ Decoder + KE2 Web Simulator — Codex Handoff

## Workspace
`/home/administrator/projects/nse-fo-low-latency-decoder`

## Existing services
- C++ decoder WebSocket: `ws://127.0.0.1:9001` (`./build/nse_decoder`)
- Python simulator: `simulation_server_2.py` on port 8000
- Separate indicative-close server on port 8100

## Problems and requirements
1. A previous 15-second WebSocket capture saw expiries `27-OCT-26` and `23-NOV-26`, but not the expected NIFTY weekly expiries. Trace the entire pipeline from instrument master and `shared/GreekTokens.csv` (including its generator), through contract loading, expiry logic, token subscriptions, decoding, analytics and WebSocket publishing. Do not assume the cause is solely `ExpiryEngine`.
2. Add the KE2 protocol specified in `KE2_WebSim_Feed_Spec(1).json` for a separate developer building a simulator: subscribe/unsubscribe, instrument master, snapshots, quote streaming, bid/ask, OI, volume, timestamps, minute bars, EOD/reference interfaces, heartbeat and recovery. Preserve the spec's semantics and do not invent unavailable market data.
3. Existing Python simulator needs current and next weekly expiries. The KE2 spec requests one monthly expiry per cycle. Support both without conflating the two consumers.

## Existing findings to verify against current code
- Observed WebSocket analytics messages contain `type`, `symbol`, `expiry`, `strike`, `ltp`, `bid`, `ask`, and sometimes `timestamp`.
- Previous expiry selection API returned current `27-OCT-26`, next `null`, available `27-OCT-26`, `23-NOV-26`.
- Previous inspection suggested decoder loads `shared/GreekTokens.csv`. Verify exact code paths and file freshness.
- The Python simulator previously received a fix for continuing live quotes after historical replay. Preserve that behavior.

## Safety requirements
- Read-only audit first. Do not edit code, restart services, kill processes, change data files or subscribe to live orders until the user approves a plan.
- Keep the existing port-9001 WebSocket clients and their messages backward-compatible.
- Do not modify hedge, PnL, risk, order execution, or replay logic.
- Identify authoritative sources for contract IDs, lot/tick sizes, timestamps, bid/ask, OI, EOD, ban lists, results and holidays. Clearly flag missing data.
- After approval: back up files, implement tests, build, verify weekly and monthly contract coverage, test KE2 subscription/reconnect behavior, and provide rollback instructions. Do not restart the live decoder without separate approval.

## First Codex task
Read this file and `KE2_WebSim_Feed_Spec(1).json`, inspect all relevant repository source and input files, and present an evidence-based root-cause analysis and implementation plan only. Await approval before changes.


## Current implementation status (16 October 2026)

The user narrowed implementation to fields supplied by the existing feed and
authorized implementing the KE2 adapter and SENSEX wiring. The initial read-only
task above records the starting context, not a request to repeat that audit.

- Startup instructions and API request/response examples: [KE2_STARTUP_AND_API.md](KE2_STARTUP_AND_API.md).
- Supported fields, verification and rollback: [KE2_FEED_IMPLEMENTATION.md](KE2_FEED_IMPLEMENTATION.md).
- Adapter: `ke2_feed_server.py`, localhost WebSocket port 9002.
- Internal raw source: `ws://127.0.0.1:9001/ke2-source` in the separate
  `build-ke2/nse_decoder` build.
- BSE/SENSEX multicast listener wired to 233.1.2.4:2004 with configurable
  BSE_LOCAL_IP; separate NSE and BSE token maps.
- Read-only API probe: `scripts/ke2_probe.py`.
- Tests and isolated build passed; live BSE token matching is still unverified.
- Stale NSE master still needs authoritative refresh for missing weeklies.
- Trading strategy, simulator, PnL, hedge, risk and replay code were not
  changed by this implementation.
- No live service was started/restarted and the original executable is intact.
- At the latest read-only port check only 8100 was listening; recheck before
  following startup instructions.
- GitHub upload is still pending; the earlier temporary export predates the
  implementation and must be refreshed before pushing.

Developer integration contract: [KE2_DEVELOPER_API.md](KE2_DEVELOPER_API.md).
Complete JSON examples: [KE2_API_EXAMPLES.json](KE2_API_EXAMPLES.json).

## Single-command launcher

Run `bash start_system.sh --reuse-existing` from the project root to start
missing stack services from one terminal and retain existing listeners.
Separate logs and Ctrl+C cleanup are handled for owned children only.
See the one-command section in KE2_STARTUP_AND_API.md.
Syntax, no-start preflight, and isolated lifecycle tests passed. No live
service was started or restarted during launcher development.

## FastAPI test service

- `ke2_test_api.py` is an HTTP test client for the existing KE2 WebSocket.
- Swagger: http://127.0.0.1:9003/docs.
- Isolated environment: .venv-ke2-api; requirements-ke2-api.txt.
- Launcher: `bash start_system.sh --reuse-existing --with-api`.
- Instructions: [KE2_FASTAPI_TESTER.md](KE2_FASTAPI_TESTER.md).
- Six API integration tests and two launcher lifecycle tests passed.
- Production feed/simulator services were not restarted.
