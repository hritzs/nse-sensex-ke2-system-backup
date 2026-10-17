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
