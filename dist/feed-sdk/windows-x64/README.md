# KE2 native feed SDK, ABI 1 (Windows x64 / Linux x64)

The managed feed stack was stopped on 29 October 2026 and BOD recovery is
PAUSED. The DLL/.so does not start merely by being loaded. Your application
owns its feed lifecycle.

## Files and build

- Windows: ke2_feed.dll, ke2_feed.h, ke2_feed.def and MinGW import library.
- Linux: libke2_feed.so (SONAME libke2_feed.so.1), header and smoke executable.
- feed_client.py: working Python ctypes integration, using C calling convention.
- MANIFEST.json: SHA256 of the packaged files.
- COPYING.LZO: the LZO dependency license; source URL/hash are in the manifest.

The Windows DLL statically links LZO, GCC/C++ and threading runtimes.
Its remaining imports are Windows system DLLs KERNEL32, msvcrt and WS2_32.
The Linux build uses the host's liblzo2, libstdc++, libgcc and glibc. Build on
your target Linux distribution when its runtime versions differ; this release
was tested in this project's Ubuntu x64 WSL environment. x86/ARM are not built.

From the repository in WSL:

~~~bash
./venv/bin/python scripts/build_feed_sdk.py --platform all
ctest --test-dir build-sdk-linux --output-on-failure
./venv/bin/python -m unittest discover -s sdk/tests -p test_sdk.py -v
~~~

The standalone source ZIP includes the shared decoder sources and pinned LZO
source archive. There, use python3 in place of ./venv/bin/python.

Prerequisites: CMake, make, g++, liblzo2-dev; for Windows cross-compilation,
g++-mingw-w64-x86-64-posix. The builder checks the pinned LZO source SHA256
and downloads/extracts that dependency when necessary.

For MSVC callers, use LoadLibrary/GetProcAddress with the C header, or generate
an MSVC import library from the supplied .def in a VS Developer Command Prompt:

~~~bat
lib /def:ke2_feed.def /machine:x64 /out:ke2_feed.lib
~~~

Windows DLL smoke test: run ke2_feed_smoke.exe beside ke2_feed.dll in a writable
directory. Linux: run ./ke2_feed_smoke beside the shared libraries.

## Host integration

1. Check ke2_feed_abi_version() == 1.
2. Call ke2_feed_create(nse_csv,bse_csv,callback,user,&handle).
3. Optionally enumerate instruments through ke2_feed_instruments.
4. Call ke2_feed_start with explicit stream configurations, or NULL,0 for all
   three production defaults. The defaults require both masters.
5. Copy callback JSON bytes immediately into your own bounded queue.
6. Call ke2_feed_stop and ke2_feed_destroy before unloading the library.

A NULL/empty master path disables that exchange. Each supplied master must
contain valid supported rows. NSE market token comes from Token; BSE token
comes from xx.Token. Ambiguous IDs and conflicting duplicates are rejected.
Master replacement is atomic and only permitted while stopped.

One context per process is supported because the shared decoder owns global
token maps. API calls are serialized. Callbacks can run on multicast worker
threads or synchronously during process_datagram/instruments. Host callbacks
must be thread safe, return promptly, retain their managed delegate/function
reference, and never invoke context API functions. Reentry returns
KE2_INVALID_STATE. Never throw exceptions out of a callback. Callback JSON
storage expires on return. Stop joins workers; no receive callbacks remain
after it returns. Externally synchronize destroy with other host API calls.

Every context operation returns 0 for success, otherwise a negative status; inspect
ke2_feed_last_error() on that same calling thread. It points to thread-local
storage and should be copied before the next context call. No C++ exception
crosses the C API. Initialize each public struct's struct_size to sizeof it.

Exchange constants: 1=NSE FO, 2=BSE FO, 3=BSE EQ.
Defaults:

| Exchange | Multicast | Port |
| --- | --- | --- |
| NSE FO | 233.1.2.5 | 34330 |
| BSE FO | 233.1.2.4 | 2004 |
| BSE EQ indices | 233.1.2.3 | 2003 |

Use a real local interface address or 0.0.0.0. The old 172.16.1.9 address
must not be assumed to exist on another machine. Multicast network access and
host firewall settings must permit the configured streams.

## Event contract

Callbacks receive UTF-8 JSON using the existing decoder's internal source
protocol. These are decoded events, delivered as observed, without the
WebSocket adapter's one-second conflation:

~~~json
{"type":"market_quote","exchange":"BSE","token":864571,
 "received_ms":1790000000000,"exchange_ts":null,"last_trade_ts":null,
 "ltp":73833.05,"bid":73826.3,"ask":73855.6,"ltq":40,
 "bid_qty":40,"ask_qty":40,"volume":16800}
~~~

The row above is an explanatory shape with historical/example values,
not a current quote. Token identity maps to instrument_id in the master.

- market_quote: exchange/token, actual LTP/bid/ask, local received_ms;
  BSE supplies LTQ, best quantities and volume. NSE quantities/volume are null.
- market_oi: exchange/token, oi and local received_ms for decoded NSE OI.
- market_index: exchange:BSE, actual index_code, symbol, ltp, received_ms.
- Instrument enumeration callback: one metadata object per instrument with
  instrument_id, exchange, symbol, instrument_type, ISO expiry, strike,
  option_type and unavailable lot/tick sizes as null.

Prices are in rupees; quantities preserve the decoded exchange units without
lot conversion. Zero/missing bid or ask is null. received_ms is local wall-clock decode time,
not exchange time. Unsupported complex/spread 2021 and unmapped EQ cash books
are excluded. No bars, EOD, external reference lookups, BSE OI or fabricated
prices are added.

process_datagram accepts one UDP payload, with its existing exchange framing;
it is useful for deterministic fixtures, capture replay and application-owned
network readers. It is disabled while library multicast input is active.
Success means the buffer was processed, not that it contained a supported
valid quote. Unsupported/malformed packets can produce zero events.
Stats count received/injected datagrams and emitted events; last_received_ms
is datagram receipt, not a guarantee of fresh market prices.

The DLL contains the native decoder and multicast readers. KE2 subscription,
reconnect/session and snapshot behavior remains in ke2_feed_server.py; the
simulator, indicative HTTP dashboard, FastAPI tester and BOD manager remain
their separate services. Trading and analytics calculations are not invoked
by this raw-feed SDK.

## Python example

Metadata-only check (does not start multicast):

~~~bash
./venv/bin/python sdk/examples/feed_client.py \
 --library dist/feed-sdk/linux-x64/libke2_feed.so \
 --nse-master shared/GreekTokens.csv --bse-master shared/BSEIndexTokens.csv
~~~

Add --live --seconds 20 to explicitly run the library's multicast readers
for twenty seconds. On Windows use a 64-bit Python and the .dll path.
Use actual accessible CSV paths on that host. The callback is shared between
both platforms; C.CDLL / C.CFUNCTYPE use the header's C calling convention.

## Existing service recovery

The service stack stays stopped unless explicitly resumed:

~~~bash
./venv/bin/python scripts/bod_manager.py --status
./venv/bin/python scripts/bod_manager.py --start
~~~

Resume removes the operator pause and restores scheduled supervision. Keep
the old services stopped while evaluating a replacement feed deployment.

## Windows verification status

Native Windows tests passed for DLL loading, compressed/uncompressed NSE,
BSE depth/index and multicast start/stop. A later rebuilt smoke executable
was blocked by this machine's Windows Application Control. The security
policy was left unchanged. The final smoke executable requires approval
under that policy before it can be run here; Linux tests pass.
