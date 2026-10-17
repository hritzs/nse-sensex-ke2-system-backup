#!/usr/bin/env python3
"""Read-only KE2 client: inspect master data or subscribe and print responses."""
import argparse
import asyncio
import json
import sys
import time

from websockets.asyncio.client import connect


async def run(args):
    async with connect(args.url, max_size=8 * 1024 * 1024) as ws:
        print(await ws.recv(), flush=True)  # capabilities and session ID
        if args.master:
            request = {"type": "instrument_master", "request_id": "probe-master",
                       "expiry": args.expiry}
        else:
            underlying = {"symbol": args.symbol, "segment": args.segment or ("FO_IDX" if args.symbol in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTYNXT50","SENSEX","BANKEX","SENSEX50"} else "FO_STK"), "future": True}
            if not args.future_only:
                underlying["options"] = {"mode": "around_future", "strikes_each_side": 2,
                                         "types": ["CE", "PE"]}
                underlying["extra_strikes"] = []
            request = {"type": "subscribe", "request_id": "probe-subscribe",
                       "expiry": args.expiry, "underlyings": [underlying]}
        await ws.send(json.dumps(request))
        deadline = time.monotonic() + args.seconds
        live_updates = 0
        while time.monotonic() < deadline:
            try:
                text = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            message = json.loads(text)
            if args.master and message.get("type") == "instrument_master":
                # Filter for display; the API itself returns all symbols of the expiry.
                message["data"] = [row for row in message["data"] if row["symbol"] == args.symbol]
                print(json.dumps(message), flush=True)
                if not message["more"]:
                    return
            else:
                print(text, flush=True)
            if message.get("type") == "error":
                raise RuntimeError(message.get("message", "API error"))
            if message.get("type") == "quote" and not message.get("snapshot"):
                live_updates += sum(row.get("ltp") is not None for row in message.get("data", []))
        if not args.master:
            print(f"Streaming rows with LTP: {live_updates}; this is not an exchange-completeness check.",
                  file=sys.stderr)
            if args.require_live and not live_updates:
                raise RuntimeError("No streaming rows with LTP received; inspect heartbeat and feed logs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:9002/")
    parser.add_argument("--symbol", required=True, type=str.upper, help="Underlying, e.g. NIFTY, SENSEX, RELIANCE")
    parser.add_argument("--segment", choices=("FO_IDX", "FO_STK"), help="Defaults to index or stock by symbol")
    parser.add_argument("--expiry", required=True, help="YYYY-MM-DD; must exist in the local master")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--master", action="store_true", help="Inspect metadata instead of subscribing")
    parser.add_argument("--future-only", action="store_true", help="Only request the exact-expiry future")
    parser.add_argument("--require-live", action="store_true", help="Fail if no streaming row has LTP")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
