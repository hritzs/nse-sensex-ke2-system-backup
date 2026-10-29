# Separate Gamma and KE2 Windows applications

Version 0.2.0-rc1. Windows x64 release candidates; full frozen Windows execution
still needs acceptance on a machine whose Application Control policy permits
the dependencies. The build machine previously blocked a pandas native library.
No policy is disabled or bypassed.

## Which file to run

| EXE | What it owns | Control | Per-user folder |
| --- | --- | --- | --- |
| KE2Feed.exe | Native decoder DLL, KE2 API, indicative, Swagger tester | http://127.0.0.1:18301/ | %LOCALAPPDATA%/WhiteGammaKE2Feed |
| WhiteGamma.exe | WhiteGamma interface only | http://127.0.0.1:18201/ | %LOCALAPPDATA%/WhiteGammaDesktop |
| GammaSimulator.exe | All-expiry Gamma simulator only | http://127.0.0.1:18401/ | %LOCALAPPDATA%/GammaSimulator |

Start KE2Feed.exe, WhiteGamma.exe and GammaSimulator.exe for all three parts.
Each owns a different service group and can be stopped independently.
WhiteGamma.exe does not launch the simulator. GammaSimulator.exe owns port 18000.

Each file includes its runtime. No external Python installation or WSL is needed
on the Windows target. Existing standalone/all-in-one and WSL services are not
replaced. Do not run the old complete EXE and these split apps on the same ports.

The separate processes do not start or kill one another. Closing Gamma leaves
the feed, indicative and Swagger running. Closing KE2Feed does not terminate
Gamma; the consumer reports loss of source and reconnects when feed returns.
The feed executable does not launch simulation or portfolio code.

## Same PC

Default endpoints:

- KE2 normalized WebSocket: ws://127.0.0.1:19002/
- Routed raw WebSocket for Gamma: ws://127.0.0.1:19002/ke2-source
- Native-only peer publisher: ws://PC_IP:19001/ke2-source
- Indicative: http://127.0.0.1:18100/
- Swagger: http://127.0.0.1:19003/docs
- WhiteGamma: http://127.0.0.1:18200/
- Simulator: http://127.0.0.1:18000/

The feed control page configures local multicast interface, peer priority and
BOD sources. It prefers its own fresh feed, fails over to matching native peers
and returns to local after stable recovery. The existing 10-second stale and
15-second local-recovery defaults remain.

## Gamma on another PC

Run KE2Feed.exe on the exchange-connected PC. In WhiteGamma's own System / peers
settings, set feed_url. In GammaSimulator's separate System / peers settings, set
source_url to that same PC's routed raw endpoint:

~~~json
{
  "feed_url": "ws://192.168.1.22:19002/",
  "source_url": "ws://192.168.1.22:19002/ke2-source"
}
~~~

Each executable has a separate settings file. Saving WhiteGamma settings does
not update GammaSimulator settings. Save the complete object shown by each page;
the snippet above only identifies the respective fields. Use actual addresses.

Allow intended LAN access to feed TCP 19002 and peer TCP 19001 under your existing
firewall policy. No firewall rule is added automatically. Feed URLs use plain
WebSocket by default and should stay on the intended LAN.

Command-line alternatives:

~~~powershell
.\WhiteGamma.exe --feed-url ws://192.168.1.22:19002/
.\GammaSimulator.exe --source-url ws://192.168.1.22:19002/ke2-source
~~~

CLI URL overrides apply to that launch; save settings in the UI for persistence.

Gamma requires the raw source's instrument fingerprints to match its local
authoritative master snapshot. It rejects mismatched metadata and stale/replayed
quotes rather than valuing a reused token as the wrong contract. Configure the
same BOD source files on both PCs, or use matching packaged seeds until refresh
is configured. This routed raw interface is implemented by the new KE2Feed build;
older KE2 JSON-only servers do not implement this source_identity protocol.

The public /ke2-source route can contain local or peer-selected events. It is
for application consumers. Native peer failover must use port 19001, whose
native-only identity prevents circular forwarding.

## BOD, references, state and logs

Each executable has its own settings.json, immutable BOD snapshots and log
directory under the per-user folder listed above. Gamma has a separate private
SQLite database. Existing production history, positions, marks and private
trade configuration are not included in these public builds.

Configure input_sources for authoritative master/calendar/LUT/bhavcopy paths.
While each app is open, configured input changes are checked and activated
automatically; inactive paths retain the last validated snapshot and report an
error. The dated seed is not an automatic exchange master downloader.
Reference changes preserve the current simulator's observed/active calculation
identity. An empty pre-session binding can refresh safely.

The feed profile validates instrument masters without loading unused Gamma
numerical dependencies. Gamma profiles also validate their model reference
inputs. Missing data stays unavailable; no price or reference is invented.

Logs: %LOCALAPPDATA%/<folder>/logs/launcher.log and
logs/YYYYMMDD-HHMMSS/{feed,whitegamma,gamma,tester,indicative}.log for the services
that executable owns. Files use IST, 10 MiB rotation and three backups. The
control page displays exact data/log paths, service PIDs and errors.

The independent indicative server's NSE CM data remains local. KE2 peer
failover does not provide that separate CM/indicative stream.

## Build and checks

Use standalone/build_separate_windows.ps1, with the pinned Windows build
environment and prepared authoritative resources described in
WHITEGAMMA_STANDALONE.md. SeparateApps.spec creates three distinct binaries from the shared runtime with
separate compiled entry points. Each entry fixes its default profile, so renaming
or downloading a duplicate file does not accidentally start another service group.
The explicit --profile option remains available for controlled testing.

Tests cover profile ownership, distinct control ports/data folders, metadata
validation, stale-data rejection, and existing KE2/BOD behavior. The isolated
split acceptance script also checks that stopping Gamma leaves feed services
alive. Those tests do not establish live multicast delivery on a new Windows PC.

Check SHA256SUMS.txt after downloading. Release notes record the exact validation
limits. Full Windows GUI, multicast and multi-PC acceptance remains pending.
The original NSE parser-layout investigation is not resolved by packaging.
