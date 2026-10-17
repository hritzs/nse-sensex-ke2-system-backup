# KE2 adapter: data available from this decoder

This implementation uses only the existing NSE and BSE multicast decoder. It
does not fetch external data and does not import or alter the simulator,
strategies, hedging, PnL, risk, or replay code. The original KE2 JSON remains
unchanged. This is a documented partial implementation, not a claim of full
KE2 v1 compliance.

## Paths and ports

- Existing legacy clients: `ws://127.0.0.1:9001/`, same messages.
- Internal raw source: `ws://127.0.0.1:9001/ke2-source`. This path does not
  replay the analytics cache and publishes only raw decoder events.
- Separate KE2 adapter: `ws://127.0.0.1:9002/`, localhost by default.
- NSE ingress remains `233.1.2.5:34330`.
- BSE/SENSEX ingress is `233.1.2.4:2004`. The previously missing listener is
  now wired into startup/shutdown. `BSE_LOCAL_IP` selects the local interface;
  unset means the kernel's default multicast interface (0.0.0.0). This
  replaces a hard-coded address that is not present on this machine.
- The indicative-close service on port 8100 is unchanged.

No existing live process was restarted. Changes are in source and the separate
`build-ke2/` directory. The current running binary does not yet expose the
new source route.

## Supported data

| Data | Availability |
| --- | --- |
| Actual LTP, best bid and ask | Raw decoded NSE/BSE prices, before analytics |
| NSE OI | Existing decoder OI messages; 64-bit values preserved |
| BSE OI | Unavailable; null |
| Contract ID, symbol, expiry, strike, option type, instrument type | Local CSV masters |
| Lot size and tick size | Unavailable; null, never guessed |
| Exchange timestamp and last-trade timestamp | Unavailable; null |
| Local decoder receipt time | `received_ts`, ISO-8601 milliseconds in IST |
| Volume and quantities | Unavailable in current integration; null |
| Exchange-time minute bars | Unsupported: verified exchange timestamps are absent |
| EOD, ban lists, results, holidays, session reference data | Unsupported; no new sources added |
| Feed IV/Greeks | Omitted; KE2 computes its own and raw quotes do not depend on analytics |

The master reports CSV modification times. It is loaded once at adapter
startup. Stale/missing contracts are not synthesized: the NSE CSV is from
8 September and lacks the intermediate October NIFTY weeklies. Refreshing
the authoritative CSV is still required for current weekly coverage.

NSE instrument IDs use the CSV's market `Token`. BSE uses `xx.Token`,
matching the existing ContractLoader assumption. BSE wire IDs must still be
confirmed against a live capture: the packet tests validate the repository's
existing 2020/2021 layout, not an independent BSE protocol certification.
BSE futures use `Type=FUTURE` with a blank `OpType`; this encoding is
explicitly supported. The current master includes the 29-October-2026 SENSEX
future as ID 864571. Conflicting IDs fail adapter startup instead of mixing
instruments. Extended
BSE tokens that cannot be represented by the current 32-bit master are rejected.

Missing values remain null; zero from a decoded book is preserved as zero.
`received_ts` is local decode time and is never relabelled as an exchange or
trade timestamp. Data gaps cannot be repaired by snapshots.

## Protocol

The connection begins with a `capabilities` message, including a random
`session_id` and master provenance. The limitations above are machine-readable.

Use the subscribe JSON from the original KE2 spec. SENSEX uses the same schema,
with `symbol: "SENSEX"` and `segment: "FO_IDX"`. For example:

```json
{
  "type": "subscribe",
  "request_id": "sensex-monthly",
  "expiry": "2026-10-29",
  "underlyings": [{
    "symbol": "SENSEX",
    "segment": "FO_IDX",
    "future": true,
    "options": {
      "mode": "around_future",
      "strikes_each_side": 6,
      "types": ["CE", "PE"]
    },
    "extra_strikes": []
  }]
}
```

The example expiry is illustrative: request an expiry actually present in your
master. NIFTY weekly and KE2 monthly subscriptions remain independent.

- Each subscribe atomically replaces that connection's requested cycle.
- Only listed strikes are selected. The window is centred on the latest
  observed actual future of the exact expiry. If no such future quote exists,
  the response reports `future_quote_unavailable`; futures and explicitly
  requested extra strikes are still selected. No anchor is invented.
- Windows recenter as that future moves; listed extra strikes stay pinned.
- Response: `subscribed` acknowledgement, then a `quote` with
  `snapshot: true`, including every resolved instrument even if its values
  are all null.
- Streaming quote messages are conflated to once per second per client.
- `{"type":"unsubscribe","instrument_ids":[12345]}` prevents those IDs from
  reappearing during recentering. A new subscribe resets exclusions.
- `{"type":"instrument_master","expiry":"2026-10-27"}` returns chunks of
  up to 500 instruments with a `more` flag; omitting expiry returns all.
- `{"type":"snapshot"}` requests the current full subscription snapshot.
- After reconnect, send `{"type":"resume","session_id":"..."}` for a full
  snapshot of the previous subscription. Sessions persist in memory for one
  hour after disconnect; after adapter restart resend subscribe. Concurrent
  use of the same session is rejected.
- Source reconnect clears quote/OI caches; missing new observations remain
  unavailable instead of resurrecting stale data.
- Heartbeats every five seconds show source connection, exchange activity and
  pending resolution. Status is DEGRADED if any selected instrument lacks a
  quote observed within ten seconds. A quiet market can therefore be DEGRADED
  even when the network connection is intact.
- Unsupported requests (including `bar_1m`, `eod`, `reference_daily`) return
  `code: "unsupported"`. Invalid requests return `invalid_request`.

KE2 binds to localhost by default. Any remote exposure needs the deployment's
own access control; the adapter is not an order interface.

## Build and verification

Use the existing environment with websockets 17.0.1:

```bash
cmake -S . -B build-ke2 -DCMAKE_BUILD_TYPE=Release
cmake --build build-ke2 --target nse_decoder test_bse_feed feed_transport_fixture -j2
ctest --test-dir build-ke2 --output-on-failure
./venv/bin/python -m unittest -v test_ke2_feed
KE2_TRANSPORT_FIXTURE="$PWD/build-ke2/feed_transport_fixture" ./venv/bin/python -m unittest -v test_ke2_transport.TransportTests.test_bse_packet_to_ke2_and_legacy
```

The transport fixture opens temporary localhost ports and feeds deterministic
BSE packets. It does not join multicast, start a trading service, or touch
production state. Its C++ server's working directory is a temporary directory.

After separate approval to activate the rebuilt decoder, run the adapter with:

```bash
./venv/bin/python ke2_feed_server.py --host 127.0.0.1 --port 9002
```

Do not run the new decoder alongside the old one on port 9001. Live activation,
multicast reception and current token matching remain deployment checks.

## Changes and rollback

Original edited C++/CMake files were copied to
`backups/ke2_feed_before_changes/` before editing; prior user modifications
are preserved in those copies. Rollback means restore those files, leave
`ke2_feed_server.py` unused, and retain the existing live executable.
There is no database migration or simulator change.

The packet queue now processes received packets rather than discarding all
other instruments whenever one packet is selected. Its existing bounded
overflow policy remains; overflow still loses data and is reported in feed
diagnostics. This is not lossless capture or replay. Raw-feed serialization
is skipped when no consumer is connected to the internal source route.

The local GitHub export prepared earlier predates these edits; it must be
refreshed before any eventual push.

## Validation result

- Isolated Release decoder and real-WebSocket fixture builds passed.
- Five Python protocol tests passed.
- NSE/BSE packet test passed with assertions enabled, covering raw LTP,
  exchange isolation, extended token rejection and all truncated prefixes.
- BSE packet -> real C++ WebSocket -> KE2 adapter -> client test passed;
  legacy future messages remained isolated from the raw source.
- Actual masters load successfully, including blank-OpType BSE futures.
- Changes checked against saved originals; full-tree whitespace warnings
  belong to pre-existing user changes.
- Production executable was not replaced and live services were not restarted.

Full startup and response examples: [KE2_STARTUP_AND_API.md](KE2_STARTUP_AND_API.md).

Developer integration contract: [KE2_DEVELOPER_API.md](KE2_DEVELOPER_API.md).
Complete JSON examples: [KE2_API_EXAMPLES.json](KE2_API_EXAMPLES.json).
