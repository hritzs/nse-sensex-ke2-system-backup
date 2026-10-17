# KE2 developer API handoff

Contract: current implemented feed-only subset, 16 October 2026.
Read this with `KE2_API_EXAMPLES.json`, which contains complete JSON requests
and response shapes based on the existing masters. The original
`KE2_WebSim_Feed_Spec.json` describes the larger desired contract; features
marked unsupported below are not implemented by this feed.

## Connection and scope

Connect your application's backend using WebSocket JSON:

```text
ws://127.0.0.1:9002/
```

There are no REST routes on port 9002. Do not connect the KE2 client to
`9001/` (legacy analytics) or `9001/ke2-source` (internal raw decoder events).

127.0.0.1 refers to the feed machine. A developer on a different machine needs
an agreed reachable deployment address or an authenticated tunnel to the feed
host; changing the URL to their own localhost will not reach this service.
Network access configuration is separate from this protocol.

Supported instrument families:

| Family | subscribe.segment | metadata.instrument_type |
| --- | --- | --- |
| NSE stock futures | FO_STK | FUTSTK |
| NSE stock options | FO_STK | OPTSTK |
| NSE index futures/options, including NIFTY | FO_IDX | FUTIDX / OPTIDX |
| BSE index futures/options, including SENSEX | FO_IDX | FUTIDX / OPTIDX |

Cash-equity/spot stock quotes are not exposed by this API. Index levels and
synthetic futures are not separate KE2 instruments in this implementation.

Stock support is verified with a deterministic stock packet -> real C++
WebSocket -> KE2 adapter -> client test, including raw future/option quotes
and an OI event. This is not a live stock-feed certification. Services were
not started for deployment; current tokens and network delivery still need
operator verification. The source master contains 212 stock underlyings and
67,840 stock F&O instruments, including expired contracts. Counts are not
evidence of current tradable coverage.

## Data dictionary: what your app receives

Instrument metadata comes from the local master, not from quote messages:

| Field | JSON type | Meaning |
| --- | --- | --- |
| instrument_id | integer | Join key; NSE market Token, BSE xx.Token under existing mapping |
| exchange | string | NSE or BSE |
| symbol | string | Underlying, e.g. RELIANCE, NIFTY, SENSEX |
| instrument_type | string | FUTSTK, OPTSTK, FUTIDX, OPTIDX |
| expiry | string | YYYY-MM-DD |
| strike | number or null | Option strike in rupees; null for futures |
| option_type | string or null | CE / PE; null for futures |
| lot_size | null | Unavailable |
| tick_size | null | Unavailable |
| unavailable_fields | array of strings | Currently lot_size and tick_size |

Every quote row has the same keys:

| Field | JSON type | Meaning / availability |
| --- | --- | --- |
| instrument_id | integer | Join to metadata |
| ltp | number or null | Raw decoded last traded price in INR; not a bid/ask midpoint |
| bid | number or null | Raw best bid in INR |
| ask | number or null | Raw best ask in INR |
| oi | integer or null | Latest decoded NSE OI; BSE OI unavailable |
| received_ts | string or null | Local decode time, ISO-8601 milliseconds, +05:30 |
| volume | null | Unavailable |
| exchange_ts | null | Unavailable |
| last_trade_ts | null | Unavailable |
| ltq | null | Unavailable in current integration |
| bid_qty | null | Unavailable in current integration |
| ask_qty | null | Unavailable in current integration |

OI is passed through from the existing decoder without conversion. Do not
assume contracts versus underlying quantity or apply lot-size conversions
without separately validating the feed's units. OI has no independent
exchange timestamp here and may be older than the latest price.

Prices, OI and timestamps are not fabricated when missing. A numeric zero is
preserved as zero; it is not equivalent to null. A decoded zero bid/ask is not
a positive executable price. Do not calculate a spread or simulated fill from
a missing or unusable side. Unavailable fields are not automatically filled
from legacy analytics.

## Recommended connection flow

1. Open WebSocket and read the capabilities message. Save its session_id.
2. Request instrument_master for the desired expiry. Collect all chunks until
   more:false and index metadata by instrument_id.
3. Send subscribe for that expiry. Read the subscribed acknowledgement and the
   full quote snapshot. Heartbeats may interleave with replies; route by type.
4. Merge quote rows into your cache by instrument_id. Data contains updates for
   selected instruments, not the complete portfolio on every message.
5. Monitor heartbeat status, pending dependencies and received_ts. A connected
   socket alone does not establish price freshness.
6. Reconnect by sending resume with the OLD session ID, or resend subscribe if
   the session is unavailable. Handle a fresh full snapshot.

## 1. capabilities: sent immediately on connection

Keys:

```text
type: "capabilities"
session_id: string
data: capability object
master_sources: [{exchange, path, modified_ts}, ...]
```

Current capability values:

```json
{
  "quotes": true,
  "subscriptions": true,
  "snapshots": true,
  "instrument_master": true,
  "heartbeat": true,
  "bar_1m": false,
  "eod": false,
  "reference_daily": false,
  "exchange_ts": false,
  "last_trade_ts": false,
  "volume": false,
  "lot_size": false,
  "tick_size": false,
  "bse_oi": false,
  "reason": "Only fields decoded by this repository are supplied; no external sources."
}
```

Master file modification times indicate provenance, not exchange validation.
The adapter loads files at startup and does not refresh them automatically.

## 2. instrument_master

Request:

```json
{"type":"instrument_master","request_id":"stock-master","expiry":"2026-10-27"}
```

Output envelope:

```text
type: "instrument_master"
request_id: echoed value or null
data: array of instrument metadata
more: boolean
```

Up to 500 records per message. The response includes every symbol of the
expiry; filter by symbol on your side. Omit expiry for all loaded contracts.
No filtering by today's date is implied.

Actual RELIANCE metadata from the inspected master:

```json
[
  {
    "instrument_id":48987,"exchange":"NSE","symbol":"RELIANCE",
    "instrument_type":"FUTSTK","expiry":"2026-10-27",
    "strike":null,"option_type":null,"lot_size":null,"tick_size":null,
    "unavailable_fields":["lot_size","tick_size"]
  },
  {
    "instrument_id":106719,"exchange":"NSE","symbol":"RELIANCE",
    "instrument_type":"OPTSTK","expiry":"2026-10-27",
    "strike":1300.0,"option_type":"CE","lot_size":null,"tick_size":null,
    "unavailable_fields":["lot_size","tick_size"]
  },
  {
    "instrument_id":106727,"exchange":"NSE","symbol":"RELIANCE",
    "instrument_type":"OPTSTK","expiry":"2026-10-27",
    "strike":1300.0,"option_type":"PE","lot_size":null,"tick_size":null,
    "unavailable_fields":["lot_size","tick_size"]
  }
]
```

These are master records, not proof of live quotes. Discover current IDs through
instrument_master; never hard-code the example tokens in application logic.

## 3. subscribe: stocks

Request:

```json
{
  "type":"subscribe",
  "request_id":"stock-bod",
  "expiry":"2026-10-27",
  "underlyings":[{
    "symbol":"RELIANCE",
    "segment":"FO_STK",
    "future":true,
    "options":{"mode":"around_future","strikes_each_side":6,"types":["CE","PE"]},
    "extra_strikes":[1300]
  }]
}
```

Add other stock underlyings to the same request when they share the expiry.
NIFTY may also share this connection if its requested expiry is the same.
SENSEX uses FO_IDX and its own actual expiry, e.g. 2026-10-29; use a separate
connection for a different expiry cycle.

Output:

```text
type: "subscribed"
request_id: echoed value or null
session_id: string
instrument_ids: array of resolved instrument IDs
pending: array of {symbol, expiry, reason}
```

Before the RELIANCE future has supplied an anchor, the above example resolves
the future and pinned 1300 CE/PE:

```json
{
  "type":"subscribed",
  "request_id":"stock-bod",
  "session_id":"<session-id>",
  "instrument_ids":[48987,106719,106727],
  "pending":[{
    "symbol":"RELIANCE",
    "expiry":"2026-10-27",
    "reason":"future_quote_unavailable"
  }]
}
```

Once an observed exact-expiry future is available, the strike window resolves
and can contain more IDs. The example list is not a fixed response.

Rules:

- Each subscribe replaces that connection's whole cycle; include the entire
  desired set when rebuilding, rolling, or replacing legs.
- Select only listed strikes, up to N each side plus the centre. Equal-distance
  centres choose the lower listed strike. CE and PE types are explicit.
- Optional positive step filters the around-future strike grid; listed pinned
  extra_strikes are retained even outside that grid/window.
- Missing future quotes produce future_quote_unavailable. A missing future
  contract produces future_contract_unavailable. Prices/anchors are not guessed.
- Unsupported symbols/expiries, bad segment and unlisted extra strikes are errors.
- At most 100 underlyings per subscribe and strikes_each_side in 0..1000.
- Subscription windows recenter automatically. New IDs can first appear in
  quote messages, so fetch metadata in advance. No separate removal event is
  sent when an instrument leaves a window; request snapshot for the current
  authoritative membership.

## 4. quote: immediate snapshot and streaming output

Snapshot output before any quotes for the stock future:

```json
{
  "type":"quote",
  "snapshot":true,
  "exchange_ts":null,
  "ts":"<current-send-time-in-IST>",
  "data":[{
    "instrument_id":48987,
    "ltp":null,
    "bid":null,
    "ask":null,
    "oi":null,
    "volume":null,
    "exchange_ts":null,
    "last_trade_ts":null,
    "ltq":null,
    "bid_qty":null,
    "ask_qty":null,
    "received_ts":null
  }],
  "unavailable_fields":["exchange_ts","last_trade_ts","volume"]
}
```

For the full stock request above, data also contains rows for pinned options
106719 and 106727, and any resolved window contracts. See the complete examples
JSON for an actual null-valued full snapshot.

Streaming uses the same row shape with snapshot:false. The envelope includes
type, snapshot, exchange_ts:null, ts and data. The current streaming envelope
does not include unavailable_fields. Price fields and oi become numeric only
when their corresponding decoder events arrive.

The ts field is the server send time, not exchange time. Unsupported fields
stay null. Complete rows are emitted for changed instruments; the client should
replace that instrument's quote row rather than treating null as zero.

Unchanged quotes are not resent on every second. Streaming changes are
conflated to once per second per connection, so this is not tick-by-tick data.
A snapshot sends every currently selected instrument, including rows without
quotes. Snapshots are a view of cached observations, not lossless recovery of
all missed ticks. Do not build exchange-time bars from send times.

## 5. snapshot, unsubscribe and resume

```json
{"type":"snapshot"}
```

Returns a complete quote message with snapshot:true.

```json
{"type":"unsubscribe","request_id":"remove-leg","instrument_ids":[106719]}
```

Returns:

```json
{"type":"unsubscribed","request_id":"remove-leg","instrument_ids":[106719]}
```

Exclusions survive window recentering until the next subscribe. They are local
to this session and do not affect other clients.

After reconnect, first read the newly generated capabilities message, then:

```json
{"type":"resume","session_id":"<OLD-session-id>"}
```

Successful resume returns a full quote snapshot without a subscribed
acknowledgement. Continue using the old session ID. The extra newly created
session is discarded. Sessions persist in memory for one hour after
disconnect, not across adapter restart; active sessions cannot be shared.
If resume fails, send a fresh complete subscribe request.

If the adapter reconnects to its C++ source, caches are cleared. Quotes missing
after that reconnect are unavailable until new observations arrive.

## 6. heartbeat

Output every five seconds:

```json
{
  "type":"heartbeat",
  "ts":"<current-send-time-in-IST>",
  "feed_status":"DEGRADED",
  "source_connected":true,
  "exchanges":{"NSE":"STALE"},
  "pending":[]
}
```

feed_status is OK only for a nonempty resolved subscription when every selected
instrument has a quote observed within ten seconds and the source is connected.
Otherwise it is DEGRADED. A quiet/untraded option may cause DEGRADED despite other
prices moving. exchanges indicates recent events per selected exchange;
it is not a per-field freshness guarantee. pending lists unresolved dependencies.

## 7. errors

Invalid request:

```json
{
  "type":"error",
  "code":"invalid_request",
  "request_id":"bad-stock",
  "message":"Expected segment FO_STK for RELIANCE"
}
```

Unavailable interface, for example a request with type:eod:

```text
type: "error"
code: "unsupported"
request_id: echoed value or null
requested_type: "eod"
message: "Unavailable from the currently decoded feed"
capabilities: the current capability object
```

No reference/EOD/bar response is silently substituted. Request IDs are echoed
on acknowledgements, errors and master chunks, but quote/heartbeat messages are
not correlated through request_id. Route them by type and instrument_id.

## Integration limits the application must handle

The feed alone cannot supply lot-based sizing, tick rounding, EOD rebase,
14-day traded-value ranking, corporate-results filtering, ban logic, holiday
calendars or exchange-time minute bars. Those features require separately
provided inputs or must remain disabled. Do not treat this subset as full
compliance with the original KE2 spec.

Unknown lot sizes are not 1; unknown timestamps are not now; null OI is not 0.
Current weekly coverage is still limited by the stale local NSE master.
BSE mapping still needs live wire validation. OI units also need verification.

Client/server frames are limited to 256 KiB for requests. Master responses are
chunked. Slow clients can be disconnected; reconnect and request a snapshot.
There is no sequence number or lossless gap-replay guarantee in this protocol.

## Stock verification commands

Use startup instructions in KE2_STARTUP_AND_API.md. Once the operator has
activated the decoder and adapter, run:

```bash
./venv/bin/python scripts/ke2_probe.py --symbol RELIANCE --expiry 2026-10-27 --master
./venv/bin/python scripts/ke2_probe.py --symbol RELIANCE --expiry 2026-10-27 --future-only --seconds 20 --require-live
./venv/bin/python scripts/ke2_probe.py --symbol RELIANCE --expiry 2026-10-27 --seconds 30 --require-live
```

A live check requires active multicast delivery. No current market prices are
asserted by the metadata examples or by synthetic packet tests.
