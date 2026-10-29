# Daily BOD, SENSEX feed and longer expiries

Verified deployment: 29 October 2026. This supersedes older manual-start,
42-day expiry and BSE-volume/index-unavailable statements.

## Automatic daily operation

Windows task NSE-KE2-Daily-BOD runs at 08:45 IST, repeats every five minutes
and runs at login. Its unattended WSL invocation was verified with
LastTaskResult 0. Windows must be powered on, the account logged in (locking
is fine), WSL available and the network share reachable.

No daily manual restart is required. BOD copies and checksums both masters,
Gamma daily references, event calendar, LUTs and previous available NSE FO,
BSE FO and NSE cash bhavcopies. Validation finishes before stopping project
services. Inputs are frozen under data/bod/snapshots; daily receipts identify
their exact versions. Decoder and adapter load the same master pair.

nse-ke2-stack.service supervises all five services. A completed-day receipt
makes repeated checks idempotent. A new weekday or missed startup triggers
BOD. Weekends do not trigger new refresh. Failed BOD retries; after 09:10,
a missing current-day receipt stops the managed stack to prevent old-day
operation. Holidays are not inferred; absent current-day masters fail visibly.
READY is startup/input readiness, separate from fresh exchange quotes.

~~~bash
cd /home/administrator/projects/nse-fo-low-latency-decoder
bash run_all.sh
./venv/bin/python scripts/bod_manager.py --status
journalctl --user -u nse-ke2-stack.service -n 80 --no-pager
~~~

Intentional stop pauses automatic recovery; start resumes it. Explicit
same-day refresh is available without changing trading logic:

~~~bash
./venv/bin/python scripts/bod_manager.py --stop
./venv/bin/python scripts/bod_manager.py --start
./venv/bin/python scripts/bod_manager.py --refresh
~~~

Logs: logs/bod/YYYY-MM-DD.log and logs/system-*/. Config/status/receipts:
data/bod/. Deployment on another machine: run scripts/install_bod.py using
the project venv. Windows task registration may require elevated PowerShell:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu\home\administrator\projects\nse-fo-low-latency-decoder\scripts\install_bod_task.ps1"
~~~

Then rerun the WSL installer. Unit template:
ops/systemd/nse-ke2-stack.service. Keep existing databases and snapshots.

## BSE feed correction

| Stream | Group | Port | Used data |
| --- | --- | --- | --- |
| BSE FO | 233.1.2.4 | 2004 | Outright futures/options |
| BSE EQ | 233.1.2.3 | 2003 | Actual indices, including SENSEX |

Both joins were verified. Use 0.0.0.0 for kernel-selected interface unless an
actual local feed-facing address is required. The supplied 172.16.1.9 is an
older interface; this machine currently uses LAN address 172.16.9.119.

The old decoder omitted nine fields before depth and used four-byte escapes
for eight-byte quantities, displacing depth/record boundaries. The correction
follows the [official BSE NFCAST v6.3 package](https://www.bseindia.com/downloads1/BSE_NFCAST_Manual.zip).
Whole packets are parsed before publishing. Actual BSE volume, LTQ and best
bid/ask quantities are supplied; unavailable books are null. A 1,000-packet
capture replay published 1,898 mapped rows without decoding rejections.

Complex/spread message 2021 uses extended identifiers and can have negative
spread prices. Those instruments are absent from the outright master and
are explicitly excluded. EQ cash-security books are also excluded without
a cash-security master, avoiding cash/FO token collisions.

BSE OI, exchange/trade timestamps, lot/tick sizes, bars and EOD remain
unsupported. NSE volume remains null. received_ts is local decode time.
Legacy WebSocket compatibility is preserved.

## Longer expiries and simulator state

All actual listed unexpired NIFTY option expiries are selected, currently
18 dates through 24 June 2031. This includes monthly/quarterly/long-dated
contracts; it does not classify them all as weekly. Five lanes per eligible
expiry and the original calculation functions remain unchanged.

Missing books give WAITING_QUOTES. Missing entry observations give
NO_ENTRY_DATA; old ticks are never manufactured. Missing daily references
record REFERENCE_UNAVAILABLE while fixed-time calculations run when quote
inputs exist. NOW is never triggered by BOD or startup.

The existing data/gamma_weekly_simulation.db is retained. Current-day
observations/trades lock their reference fingerprint. On such days BOD freezes
that reference version and reports references_deferred_to_next_bod:true.
New references apply to the next unbound day. Only empty pre-entry bindings
may be replaced after transactional checks; historic rows remain intact.

## Developer API additions

- WebSocket: ws://172.16.9.119:9002/
- Swagger: http://172.16.9.119:9003/docs
- Simulator: http://172.16.9.119:8000/
- Indicative: http://172.16.9.119:8100/
- GET http://172.16.9.119:9003/indices?symbol=SENSEX
- GET http://172.16.9.119:9003/bod/status

WebSocket real-index snapshot request:

~~~json
{"type":"indices","symbols":["SENSEX"]}
~~~

Response is type:indices, snapshot:true, request_id, ts and data. Each
observed row contains exchange:BSE, symbol:SENSEX, index_code:1, numeric ltp,
received_ts and exchange_ts:null. No observed index gives an empty data list.
Subscribed derivative symbols also receive changed index rows with
snapshot:false. Index values are never synthesized from futures.

Long-dated options without an exact-expiry future support explicit strikes:

~~~json
{"type":"subscribe","expiry":"2031-06-24","underlyings":[
 {"symbol":"NIFTY","segment":"FO_IDX","future":false,
  "options":{"mode":"explicit_strikes","strikes":[25000],"types":["CE","PE"]}}]}
~~~

25000 is illustrative: obtain and use actual listed strikes from
instrument_master. around_future still requires an observed exact-expiry
future; no proxy future is substituted. Existing quote, subscription,
snapshot, unsubscribe, heartbeat and resume messages remain supported.
Capabilities now advertise volume:true, volume_exchanges:["BSE"],
underlying_indices:true and complex_instruments:false.

## Verification and rollback

Live evidence: docs/verification/bod_sensex_20261029.json.
Unit, capture replay, native-to-KE2 transport and HTTP API checks cover the
changes; a connection does not prove every option is liquid.

Rollback: bod_manager.py --stop, then disable NSE-KE2-Daily-BOD in Windows
Task Scheduler. Restore reviewed sources/binaries from
backups/bod_sensex_before_changes/ and masters from backups/master_refresh/
as required; use the earlier approved startup procedure. Preserve the current
database. Do not restore an old database merely to roll back code.
Public sanitized source and private runtime backups remain separate.
