"""Python ctypes example; no third-party Python dependencies.

Explicit --live starts multicast; omitted means metadata only.
Supply authoritative local master paths. Keep the callback alive until destroy.
"""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import time

CALLBACK=C.CFUNCTYPE(None,C.c_void_p,C.c_void_p,C.c_uint32)
class Stream(C.Structure):
    _fields_=[("struct_size",C.c_uint32),("exchange",C.c_uint32),
              ("multicast_group",C.c_char_p),("local_interface",C.c_char_p),
              ("port",C.c_uint32)]

def load(path):
    dll=C.CDLL(str(Path(path).resolve()))
    dll.ke2_feed_abi_version.restype=C.c_uint32
    dll.ke2_feed_last_error.restype=C.c_char_p
    dll.ke2_feed_create.argtypes=[C.c_char_p,C.c_char_p,CALLBACK,C.c_void_p,C.POINTER(C.c_void_p)]
    dll.ke2_feed_create.restype=C.c_int32
    dll.ke2_feed_instruments.argtypes=[C.c_void_p,C.c_uint32,CALLBACK,C.c_void_p]
    dll.ke2_feed_instruments.restype=C.c_int32
    dll.ke2_feed_start.argtypes=[C.c_void_p,C.POINTER(Stream),C.c_uint32]
    dll.ke2_feed_start.restype=C.c_int32
    dll.ke2_feed_stop.argtypes=[C.c_void_p];dll.ke2_feed_stop.restype=C.c_int32
    dll.ke2_feed_destroy.argtypes=[C.c_void_p];dll.ke2_feed_destroy.restype=C.c_int32
    if dll.ke2_feed_abi_version()!=1:raise RuntimeError("Unsupported SDK ABI")
    return dll

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library",required=True)
    parser.add_argument("--nse-master",required=True)
    parser.add_argument("--bse-master",required=True)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--seconds",type=float,default=20)
    parser.add_argument("--interface",default="0.0.0.0")
    args=parser.parse_args()
    lib=load(args.library);handle=C.c_void_p()
    def check(result):
        if result:raise RuntimeError(lib.ke2_feed_last_error().decode("utf-8"))
    @CALLBACK
    def event(user,data,size):
        # Copy now; do substantial work in your own bounded queue/worker.
        print(C.string_at(data,size).decode("utf-8"),flush=True)
    counts={"total":0}
    @CALLBACK
    def master(user,data,size):counts["total"]+=1
    check(lib.ke2_feed_create(args.nse_master.encode("utf-8"),args.bse_master.encode("utf-8"),
                              event,None,C.byref(handle)))
    try:
        check(lib.ke2_feed_instruments(handle,0,master,None))
        print(json.dumps({"loaded_instruments":counts["total"]}),flush=True)
        if args.live:
            local=args.interface.encode("ascii")
            streams=(Stream*3)(
                Stream(C.sizeof(Stream),1,b"233.1.2.5",local,34330),
                Stream(C.sizeof(Stream),2,b"233.1.2.4",local,2004),
                Stream(C.sizeof(Stream),3,b"233.1.2.3",local,2003))
            check(lib.ke2_feed_start(handle,streams,3))
            time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        check(lib.ke2_feed_stop(handle))
        check(lib.ke2_feed_destroy(handle))
if __name__=="__main__":main()
