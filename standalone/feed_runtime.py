"""Native-only LAN publisher and private routed feed. No relay loops."""
from __future__ import annotations
import asyncio
import ctypes as C
import json
import logging
import math
from pathlib import Path
import queue
import time
from contextlib import AsyncExitStack
import websockets
from websockets.asyncio.server import serve
from ke2_feed_server import Feed, Master
from sdk.examples.feed_client import load, CALLBACK, Stream
from standalone.failover import Selector, fingerprints

LOG=logging.getLogger(__name__)

class Stats(C.Structure):
    _fields_=[("struct_size",C.c_uint32),("running",C.c_uint32)]+[(n,C.c_uint64) for n in
        ("datagrams","events","socket_errors","callback_errors","last_received_ms")]

class Native:
    def __init__(self, library, root, interface):
        self.lib=load(library)
        self.handle=C.c_void_p()
        self.lib.ke2_feed_get_stats.argtypes=[C.c_void_p,C.POINTER(Stats)]
        self.lib.ke2_feed_get_stats.restype=C.c_int32
        self.events=queue.Queue(maxsize=100000)
        self.dropped=0
        @CALLBACK
        def callback(user,data,size):
            try:self.events.put_nowait(C.string_at(data,size))
            except queue.Full:self.dropped+=1
        self.callback=callback
        self.check(self.lib.ke2_feed_create(
            str(Path(root)/"shared/GreekTokens.csv").encode("utf-8"),
            str(Path(root)/"shared/BSEIndexTokens.csv").encode("utf-8"),
            callback,None,C.byref(self.handle)))
        local=interface.encode("ascii")
        self.streams=(Stream*3)(
            Stream(C.sizeof(Stream),1,b"233.1.2.5",local,34330),
            Stream(C.sizeof(Stream),2,b"233.1.2.4",local,2004),
            Stream(C.sizeof(Stream),3,b"233.1.2.3",local,2003))
    def check(self,result):
        if result:raise RuntimeError(self.lib.ke2_feed_last_error().decode("utf-8"))
    def stats(self):
        value=Stats();value.struct_size=C.sizeof(Stats)
        self.check(self.lib.ke2_feed_get_stats(self.handle,C.byref(value)))
        return {name:getattr(value,name) for name,_ in Stats._fields_}
    def start(self):self.check(self.lib.ke2_feed_start(self.handle,self.streams,3))
    def close(self):
        if self.handle.value:
            self.lib.ke2_feed_stop(self.handle)
            self.lib.ke2_feed_destroy(self.handle)
            self.handle=C.c_void_p()

class TimestampFeed(Feed):
    def ingest(self,event):
        super().ingest(event)
        if event.get("type")=="market_quote":
            inst=self.master.by_key.get((event.get("exchange"),event.get("token")))
            if inst and inst.instrument_id in self.quotes:
                age=max(0,(time.time()*1000-event["received_ms"])/1000)
                self.quotes[inst.instrument_id]["_quote_rx"]=time.monotonic()-age
                self.last_by_exchange[inst.exchange]=time.monotonic()-age

class RoutedFeed(TimestampFeed):
    def quote(self,instrument_id):
        value=super().quote(instrument_id)
        value["source_name"]=self.selector.selected.get(self.master.by_id[instrument_id].exchange)
        return value
    def snapshot(self,sub):
        value=super().snapshot(sub)
        value["source_selection"]=self.selector.status()
        return value
    def heartbeat(self, sub):
        value=super().heartbeat(sub)
        value["source_selection"]=self.selector.status()
        return value

class Runtime:
    def __init__(self, config):
        self.config=config
        self.master=Master(config["root"])
        self.hashes=fingerprints(self.master)
        self.native=TimestampFeed(self.master)
        self.routed=RoutedFeed(self.master)
        peers=config.get("peers",[])
        self.selector=Selector(["local"]+[p["name"] for p in peers],
            config.get("stale_seconds",10),config.get("recovery_seconds",15))
        self.routed.selector=self.selector
        self.raw_clients={False:set(),True:set()}
        self.native_driver=None

    def stream(self,event):
        exchange=event.get("exchange")
        if event.get("type")=="market_index":
            if exchange!="BSE" or not isinstance(event.get("symbol"),str):return None
            if type(event.get("index_code")) is not int:return None
            stream="BSE_INDEX"
        else:
            if (exchange,event.get("token")) not in self.master.by_key:return None
            if event.get("type") not in ("market_quote","market_oi"):return None
            stream=exchange
        if event.get("type") in ("market_quote","market_index"):
            value=event.get("ltp")
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                return None
            for k in ("bid","ask","ltq","bid_qty","ask_qty","volume"):
                v=event.get(k)
                if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0):
                    return None
        elif type(event.get("oi")) is not int or event["oi"]<0:return None
        return stream

    def publish_raw(self,event,routed=False):
        raw=json.dumps(event,allow_nan=False,separators=(",",":"))
        for ws,q in list(self.raw_clients[routed]):
            try:q.put_nowait(raw)
            except asyncio.QueueFull:
                self.raw_clients[routed].discard((ws,q))
                asyncio.create_task(ws.close(code=1013,reason="Raw consumer cannot keep up"))

    async def select(self):
        changes=self.selector.choose()
        if not changes:return
        for stream,(old,new) in changes.items():
            LOG.warning("Source change %s: %s -> %s",stream,old,new)
            if stream=="BSE_INDEX":
                self.routed.indices.clear()
            else:
                for iid in list(self.routed.quotes):
                    if self.master.by_id[iid].exchange==stream:self.routed.quotes.pop(iid,None)
                self.routed.last_by_exchange.pop(stream,None)
            for e in self.selector.snapshot(new,stream):self.routed.ingest(e)
        # Clear raw consumers' quote caches through their existing reconnect path.
        clients=[ws for ws,q in list(self.raw_clients[True])]
        if clients:
            try:
                await asyncio.wait_for(asyncio.gather(*(ws.close(code=1012,
                    reason="Feed source changed; reconnect for fresh snapshot") for ws in clients)),2)
            except TimeoutError:
                for ws in clients:
                    if ws.close_code is None:ws.transport.abort()
        self.routed.source_connected=any(self.selector.selected.values())
        for sub in self.routed.sessions.values():
            before=set(sub.instruments)
            self.routed.resolve(sub)
            sub.dirty.update(before|sub.instruments)

    async def event(self,name,event):
        stream=self.stream(event)
        if stream is None:return
        if not self.selector.observe(name,stream,event):return
        if name=="local":
            self.native.ingest(event)
            self.publish_raw(event)
        await self.select()
        if self.selector.selected[stream]==name:
            self.routed.ingest(event)
            self.publish_raw(event,True)

    async def raw_handler(self,ws,routed=False):
        q=asyncio.Queue(maxsize=10000)
        item=(ws,q)
        self.raw_clients[routed].add(item)
        try:
            await ws.send(json.dumps({"type":"source_identity","mode":"routed" if routed else "native",
                "node_id":self.config["node_id"],"master_fingerprints":self.hashes}))
            for stream in self.selector.selected:
                name=self.selector.selected[stream] if routed else "local"
                for e in self.selector.snapshot(name,stream):
                    await ws.send(json.dumps(e,allow_nan=False))
            while True:
                try:payload=await asyncio.wait_for(q.get(),timeout=3)
                except TimeoutError:
                    if ws.close_code is not None:return
                    await ws.ping()
                    continue
                await ws.send(payload)
        except websockets.ConnectionClosed:pass
        finally:self.raw_clients[routed].discard(item)

    async def native_pump(self):
        src=self.selector.sources["local"]
        while True:
            driver=None
            try:
                driver=Native(self.config["library"],self.config["root"],self.config.get("interface","0.0.0.0"))
                self.native_driver=driver
                driver.start()
                src.connected=src.verified=True
                src.error=None
                self.native.source_connected=True
                next_stats=0
                old_errors=0
                while True:
                    batch=[]
                    for _ in range(2000):
                        try:batch.append(driver.events.get_nowait())
                        except queue.Empty:break
                    for data in batch:
                        try:await self.event("local",json.loads(data))
                        except (ValueError,TypeError):LOG.exception("Rejected native event")
                    if time.monotonic()>=next_stats:
                        next_stats=time.monotonic()+1
                        stats=driver.stats()
                        if stats["socket_errors"]-old_errors>1000:
                            raise RuntimeError("Persistent native socket/decoder errors; rejoining multicast")
                        old_errors=stats["socket_errors"]
                    await asyncio.sleep(0 if batch else .01)
            except asyncio.CancelledError:raise
            except Exception as exc:
                LOG.exception("Local decoder unavailable; retrying in five seconds; peers remain eligible")
                self.selector.disconnect("local",str(exc))
                self.native.source_connected=False
                self.native.quotes.clear();self.native.indices.clear();self.native.last_by_exchange.clear()
                await self.select()
            finally:
                if driver:driver.close()
                self.native_driver=None
            await asyncio.sleep(5)

    async def peer(self,peer):
        name=peer["name"]
        while True:
            try:
                async with websockets.connect(peer["raw_url"],max_size=4*1024*1024,
                        open_timeout=5,close_timeout=2,ping_interval=10,ping_timeout=10) as ws:
                    hello=json.loads(await asyncio.wait_for(ws.recv(),5))
                    if (hello.get("type")!="source_identity" or hello.get("mode")!="native"
                            or hello.get("node_id")==self.config["node_id"]
                            or hello.get("master_fingerprints")!=self.hashes):
                        raise ValueError("Peer must be a different native publisher with identical instrument metadata")
                    src=self.selector.sources[name]
                    src.connected=src.verified=True
                    src.error=None
                    async for payload in ws:
                        e=json.loads(payload)
                        if isinstance(e,dict):await self.event(name,e)
            except asyncio.CancelledError:raise
            except Exception as exc:
                LOG.warning("Peer %s disconnected/rejected: %s",name,exc)
                self.selector.disconnect(name,str(exc))
                await self.select()
                await asyncio.sleep(2)

    async def monitor(self):
        path=Path(self.config["status_file"])
        while True:
            await self.select()
            status=self.selector.status()
            status["source_endpoints"]={"local":"native decoder",**{p["name"]:p["raw_url"] for p in self.config.get("peers",[])}}
            status["updated_at_ms"]=time.time()*1000
            status["native_queue_dropped"]=getattr(self.native_driver,"dropped",0)
            if self.native_driver:status["native_stats"]=self.native_driver.stats()
            status["master_fingerprints"]=self.hashes
            temporary=path.with_suffix(".tmp")
            temporary.write_text(json.dumps(status,indent=2),encoding="utf-8")
            temporary.replace(path)
            await asyncio.sleep(1)

    async def public_handler(self,ws):
        if ws.request.path.split("?",1)[0]=="/ke2-source":
            await self.raw_handler(ws,True)
        elif ws.request.path.split("?",1)[0]=="/native":
            await self.native.handler(ws)
        else:
            await self.routed.handler(ws)

    async def run(self):
        c=self.config
        async with AsyncExitStack() as stack:
            for handler,host,port in (
                (self.public_handler,c.get("publish_host","0.0.0.0"),c["publish_port"]),
                (self.raw_handler,c.get("publish_host","0.0.0.0"),c["raw_port"]),
                (self.routed.handler,"127.0.0.1",c["routed_port"]),
                (lambda ws:self.raw_handler(ws,True),"127.0.0.1",c["routed_raw_port"])):
                await stack.enter_async_context(serve(handler,host,port,max_size=256*1024,max_queue=16))
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.native_pump())
                tasks.create_task(self.monitor())
                tasks.create_task(self.native.cleanup())
                tasks.create_task(self.routed.cleanup())
                for peer in c.get("peers",[]):tasks.create_task(self.peer(peer))
