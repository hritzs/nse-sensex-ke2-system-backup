# Deployment verified on 19 October 2026

All five services were started under the launcher at 14:25 IST:
decoder 9001, KE2 9002, Gamma simulator 8000, indicative 8100, FastAPI tester 9003.
The new decoder binary is build-ke2/nse_decoder. The legacy simulator/database
remain available for rollback.

~~~bash
cd /home/administrator/projects/nse-fo-low-latency-decoder
bash start_system.sh --restart --gamma-weekly --with-api --lan
~~~

For an authorized input refresh before restart add --refresh-inputs.
The six loaded near-term listed NIFTY expiries have five independent lanes each.
Fresh quotes were observed for all six. LUTs and event calendar loaded.
Later-expiry daily references remain unavailable; fixed lanes can run full
trade calculations with REFERENCE_UNAVAILABLE verdict as requested.
Morning entries show NO_ENTRY_DATA when capture started after their entry time.
NOW remains a deliberate user action; no production NOW entry was triggered
during verification.

KE2 listens on 0.0.0.0:9002. Other LAN machines should connect to
ws://172.16.9.119:9002/ and send a subscription per KE2_STARTUP_AND_API.md.
This address must be rechecked if the server's IP changes. Windows localhost
TCP access passed. Windows connecting to its own LAN address timed out under
mirrored WSL; a separate physical LAN client has not yet been tested.
On that client run Test-NetConnection 172.16.9.119 -Port 9002 (PowerShell),
then use a WebSocket client to test the actual subscription.

Post-deployment NIFTY, SENSEX and RELIANCE F&O probes each received streaming
rows with LTP. This verifies liveness, not exchange-wide field completeness.
A sampled SENSEX bid was zero; consumers must not treat zero as an executable
bid. Unsupported fields remain null or explicitly unsupported.

The decoder sample showed zero queue drops, LZO errors and framing errors.
REJECTED_UNITS was nonzero (28,729 at 1,065,256 datagrams in one sample).
The counter includes bounds-rejected BSE messages and other rejected units;
the current counters do not classify every rejection reason. Full BSE packet
coverage remains unverified. Do not interpret successful probes as certification.

See latest_checks.json for 35 passing automated/native/transport checks and
latest_deployment.json for measured health. Model and reference hashes are
bound per trade date. Use a new GS_SIM_DB for same-day model/input changes.
Rollback: restart with the legacy simulator selection, or follow the previous
approved original-binary procedure; retain both simulator databases.
