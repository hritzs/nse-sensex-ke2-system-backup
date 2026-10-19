# Architecture decision record

Updated: 19 October 2026. Decisions describe the current implementation,
not authorization to deploy/restart services.

## ADR-001: preserve legacy consumer messages

Keep existing analytics/future_quote delivery on 9001 and add the isolated
/ke2-source route. Legacy simulator behavior and trading logic are preserved.
Cost: two wire protocols and legacy cache behavior remain to maintain.

## ADR-002: raw quote store before analytics

Publish decoded LTP/bid/ask before analytics gates; merge OI separately in
the adapter. This prevents midpoint pricing and analytics initialization from
being treated as raw market data. Cost: source decoding correctness remains
a prerequisite; raw publication does not certify field offsets.

## ADR-003: Python KE2 adapter owns client state

Asyncio/websockets handles subscriptions, one-second conflation, snapshots,
heartbeats and reconnect sessions. Core decoding stays C++.
Cost: an extra process/hop and memory-only sessions, with no lossless replay.

## ADR-004: explicit weekly/monthly separation

Existing simulator derives weekly views; KE2 clients request an explicit
expiry cycle. Different monthly dates use different connections.
Cost: clients manage connection lifecycle and master freshness; no guessed
calendar date can replace an exchange token.

## ADR-005: no invented or externally fetched data

Export only observed supported fields. Unsupported values are null or explicit
unsupported errors. This honors the user's narrowed feed-only scope.
Cost: the original full KE2 application needs separately supplied lot/tick,
calendar, EOD/reference and exchange-time dependencies.

## ADR-006: exchange-specific token lookup

Separate NSE/BSE maps and preserve BSE extended-token rejection instead of
32-bit aliasing. Adapter detects global instrument-ID conflicts.
Cost: current BSE xx.Token identity still needs live verification.

## ADR-007: retain independent indicative service

Keep its NSE F&O/CM listeners, local reference map, HTTP dashboard and port
8100 separate from KE2. Include its code, data and binary in backup.
Cost: duplicate feed parsing exists, and an enhanced-layout disagreement
was resolved against NNF 9.48 with transaction-specific parser fixtures.

## ADR-008: FIFO dequeue instead of cross-instrument deletion

Workers dequeue individual received packets rather than clearing all other
instruments when selecting the newest packet. The bounded overflow policy
still applies. Cost: backlog latency and parallel completion ordering need
load testing; this is not lossless tick capture.

## ADR-009: FastAPI as a tester, not the source of record

An optional localhost HTTP service translates test operations to actual KE2
WebSocket connections. Each test session is isolated; timeouts disconnect
to prevent late response confusion. Cost: memory-only state and one worker;
maximum 16 sessions, last 50 observed messages each.

## ADR-010: one foreground launcher owns only its children

Preflight all ports; never terminate reused services. Explicit reuse-existing
mode trades identity verification for operator convenience. Keep API optional
with --with-api. Logs and SIGINT cleanup are owned per invocation.
Cost: not a systemd/Windows autostart or a production supervisor.

## ADR-011: separate shareable source from private runtime backup

Use immutable versioned offline artifacts with SHA-256 manifests, consistent
SQLite snapshots and original Git history retained locally. New private
GitHub repo receives a clean source snapshot and LFS-backed large CSVs.
Licenses, credentials, DBs and historical Git objects remain offline.
Cost: GitHub is not a complete runtime/OS image; native dependencies and local
runtime restoration must follow the restore guide.

## ADR-012: source-grounded documentation with explicit uncertainty

Record exact implemented layouts and state ownership. Do not hide the
214/258-byte parser divergence or promote fixtures to exchange certification.
Future changes should update HLD/LLD, tests and the versioned backup together.

## ADR-012: isolated all-expiry Gamma fixed comparisons

Following explicit user authorization, add an opt-in simulator using the
supplied final backtest functions, independent five lanes per near-term
expiry, separate DB and expiry-specific references. Fixed entries bypass
filters and record verdicts; event sizing/costs still apply. Historical
backward fill is removed. Missing inputs remain missing. Cost: receipt-time
quote observations and derived DTE cannot certify historical tick parity.

## ADR-013: explicit, identified project restart

Add --restart to the single-terminal launcher, including indicative and
optional API. Validate all selected listener identities before signaling.
Unknown listeners fail closed. Strict Gamma input readiness precedes stop.
Cost: deployment cannot proceed until real reference files are supplied.


## ADR-014: full fixed calculations without daily signal references

The user explicitly chose to run full hedge/exit/PnL calculations and record
REFERENCE_UNAVAILABLE for later expiries without daily references. Market
inputs and the event calendar still drive actual calculations. Missing event
sizing information blocks; missing LUT/veto references are not fabricated.

## ADR-015: explicit LAN feed and public source publication

--lan exposes KE2 9002 on all interfaces; local-only remains the default.
The user explicitly selected public GitHub publication. Credentials, licenses,
runtime databases, binaries and original Git history stay in offline archives.
New source snapshots append to public main without force-pushing history.

## ADR-016: canonical NSE wire formats

NNF 9.48 defines 214-byte legacy and 258-byte enhanced MBP records, and zero
compression length means uncompressed. Both are implemented and regression
tested. Queue drops and decoder rejections have separate named counters.
