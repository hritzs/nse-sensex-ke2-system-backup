"""Offline packaged-DLL and metadata verification; never subscribes live."""
import ctypes as C
import json
from pathlib import Path
import struct
import tempfile
from sdk.examples.feed_client import load,CALLBACK
from standalone.failover import Selector
def run(resources,output):
    import os
    library=Path(resources)/"native"/("ke2_feed.dll" if os.name=="nt" else "libke2_feed.so")
    rows=[]
    @CALLBACK
    def callback(user,data,size):rows.append(json.loads(C.string_at(data,size)))
    lib=load(library);handle=C.c_void_p()
    with tempfile.TemporaryDirectory(prefix="whitegamma-diagnostic-") as tmp:
        path=Path(tmp)/"bse.csv"
        path.write_text("xx.Token,Exch,Type,Symbol,Expiry,Strike,OpType,Token\n456,BSE,FUTURE,SENSEX,29-OCT-26,0,,IO\n")
        result=lib.ke2_feed_create(None,str(path).encode("utf-8"),callback,None,C.byref(handle))
        if result:raise RuntimeError(lib.ke2_feed_last_error().decode())
        try:
            p=bytearray(104);struct.pack_into(">I",p,0,2020);struct.pack_into(">H",p,26,1)
            struct.pack_into(">I",p,28,456);struct.pack_into(">Q",p,36,123456)
            struct.pack_into(">H",p,78,1);struct.pack_into(">Q",p,92,10);struct.pack_into(">I",p,100,10000)
            packet=bytes(p)+b"\0\0"*21+struct.pack(">h",-5)+b"\0\0"*4+struct.pack(">h",5)+b"\0\0"*4
            lib.ke2_feed_process_datagram.argtypes=[C.c_void_p,C.c_uint32,C.c_void_p,C.c_uint32]
            lib.ke2_feed_process_datagram.restype=C.c_int32
            data=C.create_string_buffer(packet)
            if lib.ke2_feed_process_datagram(handle,2,data,len(packet)):raise RuntimeError("Fixture decode failed")
            if not rows or rows[-1]["ask"]!=100.05 or rows[-1]["volume"]!=123456:
                raise RuntimeError("Incorrect decoded fixture")
        finally:
            lib.ke2_feed_stop(handle);lib.ke2_feed_destroy(handle)
    from ke2_feed_server import Master
    master=Master(Path(resources)/"seed")
    partial={"ok":False,"abi":lib.ke2_feed_abi_version(),"dll_packet_decode":True,
        "metadata_instruments":len(master.by_id),"live_network_verified":False,
        "note":"Native packet decoding and metadata passed; model diagnostic has not completed."}
    Path(output).write_text(json.dumps(partial,indent=2),encoding="utf-8")
    from gamma_weekly_engine import load_engine,ReferenceData
    engine=load_engine(Path(resources)/"GammaShort_Backtest_Final.py")
    ref=ReferenceData(engine,{
        "GS_DAILY_CSV":str(Path(resources)/"seed/data/gamma_inputs/GammaShortDailydata.csv"),
        "GS_EVENT_PATH":str(Path(resources)/"seed/data/gamma_inputs/EventDates.xlsx"),
        "GS_LUT_DIR":str(Path(resources)/"seed/live_lut/final_r46V2")})
    selector=Selector(["local","peer"])
    for src in selector.sources.values():src.connected=src.verified=True
    e={"type":"market_quote","received_ms":100000,"token":456}
    selector.observe("peer","BSE",e,100,100000);selector.choose(100)
    if selector.selected["BSE"]!="peer":raise RuntimeError("Failover diagnostic failed")
    receipt={"ok":True,"abi":lib.ke2_feed_abi_version(),"metadata_instruments":len(master.by_id),
        "fixture":"deterministic BSE packet; not live market data",
        "dll_packet_decode":True,"peer_selection":True,"gamma_model_loaded":True,
        "reference_diagnostics":ref.errors,"live_network_verified":False}
    Path(output).write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    return receipt
