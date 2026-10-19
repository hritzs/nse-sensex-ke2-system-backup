# KE2 upstream disconnect investigation, 19 October 2026

Port 9002 remained available while its upstream WebSocket on
ws://127.0.0.1:9001/ke2-source repeatedly closed without a close frame.
This is an internal source failure, independent of a LAN client's connection.

A direct drain-only reader received 245,684 messages (47,357,562 bytes) during
an approximately 18-second receive window; total context lifetime was 21 seconds.
The decoder was therefore emitting a substantial source stream.

The adapter hot path copied every session's instrument set on every event and
re-resolved every session's strike window for every future, including unrelated
symbols and expiries. Those operations can stall source reads as subscription
size and source rate increase. The fix removes unconditional set copies and
recentres only subscriptions whose exact symbol/expiry future changed.

The original close frame was absent, so its precise server-side close reason
cannot be established retrospectively. Source close code/buffer diagnostics and
backpressure diagnostics are now logged by C++; Python logs connection duration,
message count, and sent/received close details for future failures. The bounded
1 MiB source backpressure limit and keepalive checks remain enabled.

Validation:
- Seven KE2 feed tests, including unrelated symbol/expiry isolation, passed.
- Decoder and native transport fixture built successfully.
- Native-to-KE2 stock and SENSEX transport checks passed.
- Three concurrent actual subscriptions ran for 50 seconds, each receiving
  49 streaming messages; no upstream disconnects, disconnected heartbeats, or
  source backpressure events were observed.
- Maximum observed receipt ages were 0.950s NIFTY, 1.001s SENSEX and 0.969s RELIANCE.

See source_connection_checks.json for measured evidence. This bounded live
check does not certify every possible subscription load or exchange message.

Start the complete dependency chain from the project root:
~~~bash
bash run_all.sh
~~~
The standalone adapter command starts only 9002; it requires the new decoder
already running on 9001. Do not start a second adapter on the same port.
Rollback source is in backups/source_disconnect_before_changes/.
