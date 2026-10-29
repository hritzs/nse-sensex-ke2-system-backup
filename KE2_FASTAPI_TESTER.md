> Deployment update, 29 October 2026: see [daily BOD, corrected BSE feed and all listed expiries](BOD_AND_SENSEX.md). Earlier deployment/capability statements below are historical.

# FastAPI / Swagger tester for KE2

This is an HTTP test client in front of the existing KE2 WebSocket. It does
not change the production feed, simulator, instrument data, or trading logic.

- Swagger UI: http://127.0.0.1:9003/docs
- OpenAPI: http://127.0.0.1:9003/openapi.json
- Default upstream: ws://127.0.0.1:9002/
- Underlying feed contract: KE2_DEVELOPER_API.md

Port 9003 belongs to this tester. The actual KE2 feed on 9002 is still
WebSocket-only. Tester responses wrap or pass through real upstream messages;
they do not fill missing prices, lot/tick sizes, timestamps, OI or reference data.

## Setup and launch

Dependencies are installed in a separate environment so running feed services
keep their existing Python dependencies. For a new checkout:

```bash
cd /home/administrator/projects/nse-fo-low-latency-decoder
./venv/bin/python -m venv .venv-ke2-api
./.venv-ke2-api/bin/python -m pip install -r requirements-ke2-api.txt
```

The environment has already been prepared and tested on this machine.

One-command stack launch, reusing existing services:

```bash
bash start_system.sh --reuse-existing --with-api
```

If only the tester needs to run and the feed is already available:

```bash
./.venv-ke2-api/bin/python -m uvicorn ke2_test_api:app --host 127.0.0.1 --port 9003 --workers 1
```

Use one worker: test sessions are stored in that process's memory. An API
restart loses HTTP session IDs. It does not restart the feed or simulator.
The launcher stops only processes it started; existing services are retained.

To select a different upstream, set KE2_TEST_UPSTREAM before launch:

```bash
KE2_TEST_UPSTREAM=ws://127.0.0.1:9002/ ./.venv-ke2-api/bin/python -m uvicorn ke2_test_api:app --host 127.0.0.1 --port 9003 --workers 1
```

127.0.0.1 is the machine running the tester. Keep the tester local; this
development service has no user-authentication layer.

## Swagger workflow

1. Open /docs and execute GET /health.
2. Execute GET /capabilities to check the actual KE2 service.
3. Execute GET /instruments with expiry=2026-10-27 and symbol=RELIANCE.
4. Execute POST /sessions with the stock request below.
5. Copy the returned session_id into the session endpoints.
6. Execute snapshot, inspect quotes/events, test unsubscribe, then reconnect.
7. DELETE the test session when finished.

Stock request for POST /sessions:

```json
{
  "expiry": "2026-10-27",
  "underlyings": [{
    "symbol": "RELIANCE",
    "segment": "FO_STK",
    "future": true,
    "options": {
      "mode": "around_future",
      "strikes_each_side": 6,
      "types": ["CE", "PE"]
    },
    "extra_strikes": [1300]
  }]
}
```

For a simpler first test, omit options and extra_strikes to subscribe only to
the stock future. Swagger also includes NIFTY and SENSEX request examples.
Use actual listed expiries. SENSEX's example expiry is 2026-10-29 and uses
FO_IDX. Different expiry cycles should use different test sessions.

A successful create returns HTTP 201:

```text
{
  "session_id": "<HTTP-test-session-ID>",
  "upstream_session_id": "<KE2-WebSocket-session-ID>",
  "acknowledgement": <actual KE2 subscribed message>,
  "snapshot": <actual KE2 full quote snapshot>
}
```

Use the TOP-LEVEL session_id in HTTP paths. The upstream_session_id is a
different identifier used internally when resuming the KE2 connection.
The tester automatically reads streaming messages in the background while
you make HTTP requests.

Initial prices may be null and the subscription may have pending dependencies.
That is a valid upstream response, not a generated test price. A quiet feed
can remain DEGRADED; use the upstream heartbeat and receipt timestamps.

## Endpoints

| Method / path | Purpose |
| --- | --- |
| GET /health | Tester process health and session count; not market-data health |
| GET /capabilities | Open a temporary KE2 connection and return its capabilities |
| GET /instruments?expiry=2026-10-27&symbol=RELIANCE | Collect master chunks; optionally filter by symbol |
| GET /sessions | List test sessions and their connection states |
| POST /sessions | Create an independent connection, subscribe, return acknowledgement + snapshot |
| GET /sessions/{id} | Connection status, upstream ID, latest heartbeat and acknowledgement |
| POST /sessions/{id}/subscribe | Replace that session's complete subscription |
| POST /sessions/{id}/snapshot | Return actual full KE2 snapshot |
| POST /sessions/{id}/unsubscribe | Remove specified IDs in this session |
| POST /sessions/{id}/reconnect | Reconnect, resume old upstream session, return full snapshot |
| POST /sessions/{id}/request | Send raw KE2 JSON, including unsupported types to test errors |
| GET /sessions/{id}/quotes | Latest cached quote rows and heartbeat |
| GET /sessions/{id}/events?after=0&limit=50 | Poll recent upstream messages with a local cursor |
| GET /sessions/{id}/stream?after=0 | Observe upstream messages over server-sent events (SSE) |
| DELETE /sessions/{id} | Close only this tester's upstream connection |

Instrument lookup returns count, data and master_sources. The tester collects
all upstream master chunks before returning and filters by symbol locally.

Unsubscribe body:

```json
{"instrument_ids":[106719]}
```

The example ID belongs to the inspected RELIANCE master; discover current
IDs rather than hard-coding it in an application.

Raw request example:

```json
{"type":"eod"}
```

The feed does not provide EOD data. This should return HTTP 400 with the
original KE2 unsupported error under detail. It does not add EOD support.

## Quotes, event polling, and SSE

GET /sessions/{id}/quotes returns:

```text
{
  "session_id": "<HTTP-test-session-ID>",
  "connected": true,
  "heartbeat": <latest upstream heartbeat, or null>,
  "data": [<actual cached KE2 quote rows>]
}
```

Quote rows retain exactly the original KE2 field meanings. The tester does
not rename received_ts as exchange time or substitute midpoint for LTP.

A cache is not authoritative subscription membership after automatic strike
recentering: call POST snapshot to refresh membership and reset the cache.

Event records have cursor, observed_at and message. observed_at is this
tester's local observation time in UTC, separate from the feed's timestamps.
The cursor is local to the tester, not an exchange sequence number. Only the
last 50 upstream messages per test session are retained. Poll with the returned
next_cursor. history_truncated means requested history has fallen out of the
ring buffer; request a snapshot instead of assuming a lossless replay.

SSE is easier to inspect with curl than Swagger's interactive response panel:

```bash
curl -N http://127.0.0.1:9003/sessions/REPLACE_WITH_HTTP_SESSION_ID/stream
```

SSE uses the same bounded history, not an additional market-data subscription.

## Reconnect and errors

POST reconnect closes only this session's socket and attempts to resume its
previous KE2 session. It does not restart any server. KE2 sessions expire
one hour after disconnect and do not survive an upstream adapter restart.

If resume fails, use POST /sessions/{id}/subscribe with the full request to
establish the desired subscription on the newly opened connection.

| HTTP status | Meaning |
| --- | --- |
| 200 / 201 | Successful tester operation, not proof of live prices |
| 400 | Original KE2 error in detail, e.g. unsupported data or wrong segment |
| 404 | Unknown/deleted HTTP test session |
| 422 | Invalid HTTP request model/query or missing raw request type |
| 429 | Maximum 16 HTTP test sessions reached; delete unused sessions |
| 502 | Upstream connected but is not the expected KE2 protocol |
| 503 | KE2 unavailable/disconnected |
| 504 | KE2 response timed out; socket closed to discard late responses |

Use DELETE to release sessions. There is no disk persistence. GET
/capabilities opens and closes a temporary upstream connection; its session ID
is not an HTTP tester session.

GET /health returns status:ok even when the KE2 service is down; it reports
only that FastAPI is alive. GET /capabilities probes the upstream connection;
market freshness still comes from subscribed quote data and heartbeat.

## Validation

```bash
./.venv-ke2-api/bin/python -m unittest -v test_ke2_api
./venv/bin/python -m unittest discover -s tests -p test_launcher.py -v
```

Six API integration tests passed using a real isolated KE2 adapter with fixture
data. They cover Swagger/OpenAPI, capabilities, stock master/snapshots, OI,
unsubscribe/resume, independent sessions, upstream errors and timeouts.
Launcher tests start the actual FastAPI app on a temporary port, verify /docs,
and verify cleanup. No production service was restarted by these tests.
