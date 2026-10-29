"""Raw transport validation for the separately hosted Gamma simulator."""
import asyncio
import json
import time
import websockets
from standalone.failover import fingerprints

async def source_loop(simulator):
    expected=fingerprints(simulator.master)
    while True:
        try:
            async with websockets.connect(simulator.source,max_size=2**20,
                    open_timeout=5,close_timeout=2) as socket:
                identity=json.loads(await asyncio.wait_for(socket.recv(),5))
                if (not isinstance(identity,dict) or identity.get("type")!="source_identity"
                    or identity.get("mode") not in ("native","routed")
                    or identity.get("master_fingerprints")!=expected):
                    raise ValueError("Gamma source metadata mismatch; refresh authoritative masters on both PCs")
                simulator.connected=True
                simulator.connection_epoch+=1
                simulator.quotes.clear()
                simulator.last_sample.clear()
                simulator.error=None
                async for message in socket:
                    value=json.loads(message)
                    if not isinstance(value,dict):continue
                    if value.get("type")=="market_quote":
                        stamp=value.get("received_ms")
                        if type(stamp) not in (int,float) or not -2000<=time.time()*1000-stamp<=10000:
                            continue
                    simulator.ingest(value)
        except (OSError,ValueError,TimeoutError,websockets.exceptions.WebSocketException) as exc:
            simulator.error=str(exc)
        finally:
            simulator.connected=False
            simulator.quotes.clear()
        await asyncio.sleep(2)
