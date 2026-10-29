# Standalone WhiteGamma architecture

This is the desktop distribution layer around existing calculation code.
Existing Linux live services are separate deployments and are not replaced.

## High-level design

~~~mermaid
flowchart LR
  N[NSE F&O multicast] --> DLL[Native decoder DLL / .so]
  B[BSE F&O and index multicast] --> DLL
  DLL --> NP[Native-only raw publisher 19001]
  DLL --> R[Per-stream source router]
  PEER[Other PCs native raw publishers] --> V[Identity and metadata validation]
  V --> R
  R --> API[LAN KE2 19002]
  R --> WG[WhiteGamma UI 18200]
  R --> GS[Gamma simulator 18000]
  R --> TEST[FastAPI tester 19003]
  CM[NSE F&O and CM multicast] --> IC[Native indicative 18100]
  SUP[Owned-process supervisor] --> DLL
  SUP --> WG
  SUP --> GS
  SUP --> TEST
  SUP --> IC
  BOD[Immutable BOD snapshots] --> SUP
  UI[Desktop control window 18201] --> SUP
~~~

The peer edge always terminates at a native-only publisher. Routed KE2 data
can be consumed by LAN applications but is never used as a peer input.
That separation is the loop-prevention invariant.

WhiteGamma's existing engine, Black calculations, synthetic forwards,
Borrow/Lend and SPAN code are bundled without numerical edits. Gamma uses the
existing isolated fixed-lane adapter and supplied backtest source. Indicative
uses its existing C++ calculations, with generated platform/socket adaptation.

## Low-level design

| Module | Responsibility |
| --- | --- |
| launcher.py | Resource location, per-user settings, single-instance lock, GUI/browser and control API |
| supervisor.py | Port checks, exact owned child PIDs, shared stop file, bounded retry, BOD coordination |
| owned_job.py | Windows kill-on-close job encompassing worker bootloader descendants |
| worker.py | Separate module environments for feed, WhiteGamma, Gamma and FastAPI |
| feed_runtime.py | ctypes callback queue, native publisher, KE2 endpoints, peer readers, source status |
| failover.py | Deterministic per-stream local/peer selection and timestamp acceptance |
| inputs_refresh.py | Validated immutable snapshots, same-day reference protection, empty binding release |
| logging_utils.py | IST logging, bounded files, windowed worker stdout/stderr |
| build_indicative*.py | Reproducible native platform/transport adaptation from original C++ source |
| prepare_build.py | Allowlisted seed and source resources, excluding portfolio state |
| WhiteGammaStandalone.spec | Windows Python runtime and native one-file packaging |

Native callbacks copy bytes into a bounded queue and return promptly.
Only the async pump parses and processes JSON. Quote events are checked against
the loaded exchange/token identity and accepted timestamp bounds before
affecting health or routed caches. A hot peer has its own bounded cache of
latest instrument event types. No prices are manufactured.

The selector owns one selected source for NSE, BSE and BSE_INDEX. Each source
tracks connected/verified flags, last actual quote time, continuous healthy
start and latest event cache. Clock aging uses monotonic time after subtracting
original receive age. Duplicate snapshots do not extend freshness.

On a source transition, the router clears affected quotes/OI or index state,
replays only fresh selected-source rows, re-resolves subscriptions, marks
updates dirty and reconnects raw consumers for a clean snapshot. Quote rows
and heartbeats report source provenance. Unsupported feed fields retain their
existing null/unsupported semantics.

Peers initially send source_identity containing mode=native, node_id and
canonical instrument metadata hashes for both exchanges. A mismatched node
or master is rejected before events enter the cache. Default LAN transport is
trusted-network WebSocket; this handshake verifies configuration consistency,
not cryptographic publisher authentication.

Each owned runtime launch has an immutable JSON configuration and shared stop
file. Workers release their own servers/tasks/database on shutdown. On Windows,
a kill-on-close job also owns frozen-worker descendants. An unrelated listener
causes a visible port error rather than being terminated.

BOD writes a new snapshot, checks source stability, master identity and reference
file parseability, and commits active.json atomically. It computes content
hashes so mere source timestamp changes do not restart consumers. A failed
candidate never becomes active. Intraday calculation references stay fixed once
activity exists or the active day reaches 09:15. Empty pre-session model
bindings can be released; observed rows, NOW requests and active/closed results
block release. All database operations target this installation's fresh,
per-user database.

## Persistence and boundaries

The EXE contains a dated master/reference seed, code and runtime. Mutable
settings, positions supplied later by the user, SPAN, logs, snapshots and SQLite
live under the per-user data directory. It never opens the production database.

NSE F&O and BSE peer failover is part of the router. Indicative's separate NSE CM
consumer remains local and independent. The package does not implement legacy
C++ analytics messages or alter that existing server. It does not place orders
or automatically square off positions on a source transition.

## Validation and pending acceptance

Deterministic tests exercise freshness, priority, hysteresis, cache clearing,
actual peer sockets, metadata mismatch, relay rejection, KE2 snapshots and BOD
reference protection. The Linux standalone stack has served all five application
health/state endpoints with a fresh simulator database. Native Linux metadata
contains 86,011 instruments; native BSE index updates were observed.

Windows x64 binaries compile. Windows pure selection and process-job tests
pass; an offline decoder packet has decoded through the DLL. The pandas model
diagnostic is blocked by Windows Application Control. GUI, frozen multiprocess
startup, target multicast and real multi-PC failover require approved Windows
acceptance before production deployment.
