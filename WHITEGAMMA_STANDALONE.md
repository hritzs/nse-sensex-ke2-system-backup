# WhiteGamma standalone app - Windows build and LAN feed failover

Build status: release candidate. A Windows x64 EXE has been produced. Linux
source integration and deterministic peer failover pass. Full Windows runtime
acceptance is pending because this machine's Application Control policy blocked
a pandas native module during the model diagnostic. Do not treat this candidate
as a production-validated Windows deployment. Do not disable or bypass that policy.

## Files to share

- dist/whitegamma-standalone/WhiteGammaStandalone.exe: one-file Windows app,
  including Python runtime, native decoder DLL, KE2, WhiteGamma, Gamma simulator,
  indicative native server, API tester, UI, and dated reference seeds.
- dist/whitegamma-standalone/ke2_feed.dll: separate native C ABI for other hosts.
- sdk/include/ke2_feed.h and sdk/README.md: DLL and Linux .so integration contract.
- The Windows ZIP and source ZIP produced by standalone/package_release.py include
  this guide, build details and checksums. No current positions, trades, marks,
  history database, or private portfolio configuration are bundled.

The EXE is a complete host application. The DLL itself is the raw market decoder;
it does not replace the Python application, simulator, or HTTP services.

Target: Windows 10 version 1903 or newer / Windows 11, x64. Windows execution
still needs validation on an approved target. No separately installed Python,
WSL, Docker or Redis is required by the frozen app. The embedded window uses
WebView2; if unavailable, the app opens the system browser instead.

Live data requires access to the authorized exchange multicast network or a
configured PC running this package. An EXE cannot supply market data on a PC
without a reachable source. Reference files and SPAN access are independent.

## First run

1. Copy the Windows ZIP to the recipient PC, verify its SHA-256 and extract it.
2. After the build has been approved under that PC's application policy, launch
   WhiteGammaStandalone.exe as the normal user. It needs no administrator rights.
3. Open System / peers. Set interface to the PC's actual feed-facing IPv4 address
   if 0.0.0.0 does not select the correct interface.
4. Add the other feed-producing PCs to peers, in priority order, for example:

~~~json
[
  {"name":"desk-b","raw_url":"ws://192.168.1.22:19001/ke2-source"},
  {"name":"desk-c","raw_url":"ws://192.168.1.23:19001/ke2-source"}
]
~~~

5. Save settings. This restarts only this app's own workers. Configure the same
   authoritative input sources on each PC so metadata fingerprints match.
6. Allow inbound TCP 19001 for native peer sharing and 19002 for the routed KE2
   API on the intended LAN, under your existing firewall policy. No firewall
   changes are made automatically. These read-only feed endpoints have no
   authentication or TLS by default; keep their exposure on the intended LAN.
7. Use Application, Simulation, Indicative and API tester tabs. The initial
   portfolio is empty. Configure trade/position paths explicitly in
   %LOCALAPPDATA%/WhiteGammaStandalone/whitegamma/config.json and restart owned
   services to import your own authorized files.

Closing the embedded app or pressing Stop and exit stops its owned services.
In browser fallback, use Stop and exit; closing a browser tab alone does not
stop the local server. It never kills another application's occupied ports.

## Endpoints

All ports are configurable in settings.json. Defaults intentionally differ
from the existing WSL production stack.

| Interface | Default | Meaning |
| --- | --- | --- |
| Desktop/control | http://127.0.0.1:18201/ | All service tabs, sources, settings, BOD |
| WhiteGamma | http://127.0.0.1:18200/ | Existing application UI and calculations |
| Gamma | http://127.0.0.1:18000/ | Five lanes for every listed NIFTY option expiry |
| Indicative | http://127.0.0.1:18100/ | Existing independent NSE F&O + CM dashboard |
| Swagger | http://127.0.0.1:19003/docs | Existing KE2 HTTP test client |
| LAN KE2 | ws://PC_IP:19002/ | Local-first routed normalized feed |
| Native KE2 | ws://PC_IP:19002/native | This PC's decoder only |
| LAN native peer stream | ws://PC_IP:19001/ke2-source | Native-only raw events plus identity |
| Private routed KE2 | ws://127.0.0.1:19004/ | Stable input used by WhiteGamma and tester |
| Private routed raw | ws://127.0.0.1:19005/ke2-source | Stable input used by Gamma |

The packaged port 19001 is a raw-source service. It does not implement the
original C++ legacy analytics protocol. Existing production binaries, protocol,
ports, strategy code and database are unchanged.

## Feed selection and API

- Selection is independent for NSE F&O, BSE F&O and BSE indices.
- Local fresh data wins. If it becomes stale for more than 10 seconds, a verified
  fresh peer is selected in configured order. A still-healthy peer stays selected.
- Return to local requires 15 seconds of continuously fresh local data.
- A live TCP socket, heartbeat or OI event alone does not count as a fresh price.
- Original receive timestamps are preserved. Events more than 10 seconds old or
  over two seconds in the future are rejected. Synchronize the PCs' clocks.
- Peers must send native source_identity, a different node_id, and matching
  canonical NSE/BSE instrument metadata fingerprints. Mismatch is shown as an
  error rather than applying a reused token to a different contract.
- The shared native stream never republishes peer data. Routed streams are
  refused as peers. This prevents A-to-B-to-A fallback loops.
- Source changes clear the affected cache. Raw consumers reconnect for a fresh
  selected-source snapshot. An all-stale subscription remains degraded.
- Local decoder startup failures retry every five seconds. Persistent native
  socket/decoder error bursts trigger a rejoin. Peer connections retry every two
  seconds. Native socket counters, queue drops and selected sources are visible.
- A quiet individual option can remain stale while its exchange is active.
  Quote timestamps and the normal subscription heartbeat still apply.

The existing KE2 message contract remains: instrument_master, subscribe,
unsubscribe, snapshot, resume, indices; responses capabilities, quote, indices,
heartbeat and error. See KE2_STARTUP_AND_API.md and KE2_DEVELOPER_HANDOFF.md
(if present) for field definitions and unsupported capabilities.

Routed quote rows additionally include source_name ("local" or the configured
peer name). Snapshots and heartbeats include source_selection. Example:

~~~json
{
  "type":"heartbeat",
  "source_selection":{
    "selected":{"NSE":"local","BSE":"desk-b","BSE_INDEX":"local"},
    "stale_seconds":10,
    "recovery_seconds":15,
    "sources":{
      "desk-b":{"connected":true,"verified":true,"error":null,"age_seconds":{"BSE":0.4}}
    }
  }
}
~~~

This is a partial schema example, not a live-price response. Unsupported fields
remain null/unsupported; no missing price or reference is synthesized.

The indicative server remains an independent local NSE F&O/CM consumer. The
KE2 peer router does not supply its separate NSE CM/indicative stream. Its
health endpoint alone does not establish fresh indicative values.

## BOD and token updates

System / peers exposes input_sources, a map from these fixed target names to
accessible local files or UNC share files:

- shared/GreekTokens.csv
- shared/BSEIndexTokens.csv
- shared/cm_bhavcopy/current.csv
- shared/bod_bhavcopy/nse-fo/current.csv
- shared/bod_bhavcopy/bse-fo/current.csv
- data/gamma_inputs/GammaShortDailydata.csv
- data/gamma_inputs/EventDates.xlsx
- live_lut/final_r46V2/LUT_0916.csv, LUT_0917.csv, LUT_0918.csv,
  LUT_0919.csv, LUT_0920_Onwards.csv

JSON path example:

~~~json
"input_sources": {
  "shared/GreekTokens.csv": "\\\\YOUR-SERVER\\MarketData\\GreekTokens.csv",
  "shared/BSEIndexTokens.csv": "\\\\YOUR-SERVER\\MarketData\\BSEIndexTokens.csv",
  "data/gamma_inputs/GammaShortDailydata.csv": "\\\\YOUR-SERVER\\MarketData\\GammaShortDailydata.csv"
}
~~~

Replace the example with actual authoritative paths; configure the calendar,
LUTs and bhavcopies too. No credentials, private network paths or automatic
external downloader are assumed in the distributed defaults.

While the app is open, it checks configured sources every minute after the
08:45 IST BOD time, and on startup/manual refresh. A changed source is copied
to a new immutable snapshot, checked for stability during copying and parsed
before activation. Changed contents restart only the app-owned consumers;
unchanged contents do not cause a restart. No terminal command is needed for
subsequent configured source updates. Failed updates retain the last validated
snapshot and expose the failure in System / peers.

Reference/LUT changes are deferred after simulation activity or after 09:15
on an already active day. Pre-session updates can replace an empty day's binding;
observations, NOW requests and active/closed lane results prevent that change.
Only empty bindings and waiting results can be released. Existing production
data is never opened by this package.

Automatic BOD requires the app to be running and its configured paths accessible.
It does not install an OS startup task. If input_sources is empty, refresh_enabled
is false and only the dated bundled seed is available. Seed date and BOD status
are shown explicitly. A missing daily expiry reference remains unavailable;
fixed-time lanes retain the existing configured behavior.

## Files, logs, resources and rollback

Persistent folder: %LOCALAPPDATA%/WhiteGammaStandalone, overridable using
--data-dir or WHITEGAMMA_DATA_DIR.

- settings.json: node ID, peer order, ports, interface, BOD and source paths.
- whitegamma/config.json: per-user portfolio inputs and application settings.
- whitegamma/: marks, UI state and SPAN cache; new installation starts clean.
- data/gamma_weekly_simulation.db: this installation's separate SQLite history.
- bod/active.json and bod/snapshots/: validated, dated reference versions.
- feed-status.json: selected sources and native decoder diagnostics.
- logs/launcher.log and logs/YYYYMMDD-HHMMSS/{feed,whitegamma,gamma,tester,indicative}.log.
- *-bootstrap.log: worker bootloader/startup output before Python logging starts.

Logs use IST and rotate at 10 MiB with three backups per file. Old launch
directories and reference snapshots are retained for manual archival. Allow
about 1-2 GB free disk initially, plus SQLite history, SPAN downloads and retained
snapshots. Allow approximately 1-2 GB available RAM for the complete stack;
these are planning estimates, not a Windows benchmark. Native peer streams
carry all decoded events, so each hot standby adds network/processing load.
Actual capacity must be measured during a full trading session.

One-file launch extracts bundled runtime resources to a temporary directory.
User data stays outside the EXE and survives updates. To roll back, close this
candidate and use the prior app; preserve the user-data folder. This package
does not replace or stop the existing WSL services.

## Build and validation

Native libraries are built using scripts/build_feed_sdk.py. The indicative
Windows/Linux adaptations are generated reproducibly by
standalone/build_indicative.py and build_indicative_linux.py from the existing
indicative source; its calculation code is retained.

On Linux/WSL, from the repository:
~~~bash
python3 standalone/build_indicative.py
python3 standalone/build_indicative_linux.py
python3 standalone/prepare_build.py
~~~

On Windows PowerShell:
~~~powershell
.\standalone\build_windows.ps1
~~~

The build uses Python 3.14.10 and requirements-windows.lock.txt. Runtime Python
packages are frozen into the EXE. The existing fixed-lane adapter removes
Numba decorators and never imports Numba, so that unused compiler is excluded.
See the third-party notices in the distribution. PyInstaller builds are
platform-specific; the Windows EXE is not a Linux executable.

Offline approved-target diagnostic:
~~~powershell
.\WhiteGammaStandalone.exe --headless --self-test C:\Temp\whitegamma-diagnostic.json
~~~

Isolated acceptance run (fresh folder and shifted ports):
~~~powershell
.\WhiteGammaStandalone.exe --headless --data-dir C:\Temp\WhiteGammaAcceptance --port-offset 1000 --seconds 120
~~~

Acceptance must verify all service endpoints, native multicast on the target
interface, SENSEX derivatives and indices, NSE indices/stocks, an actual peer
disconnect and stable local recovery. The bundled tests cover deterministic
packets and socket peers; they do not prove exchange-wire completeness or live
prices on an untested PC. The earlier NSE packet-layout discrepancy remains
outside this packaging change.

Implementation references:
[PyInstaller operating mode](https://pyinstaller.org/en/stable/operating-mode.html),
[pywebview installation](https://pywebview.flowrl.com/guide/installation.html),
[Python 3.14.10](https://www.python.org/downloads/release/python-31410/).
