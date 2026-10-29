"""Run with KE2_SDK_LIBRARY pointing at the built Linux shared library."""
import ctypes as C
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("feed_client",ROOT/"sdk/examples/feed_client.py")
client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)

def packet():
    p=bytearray(104);struct.pack_into(">I",p,0,2020);struct.pack_into(">H",p,26,1)
    struct.pack_into(">I",p,28,456);struct.pack_into(">Q",p,36,123456)
    struct.pack_into(">H",p,78,1);struct.pack_into(">Q",p,92,10);struct.pack_into(">I",p,100,10000)
    return bytes(p)+b"\0\0"*21+struct.pack(">h", -5)+b"\0\0"*4+struct.pack(">h",5)+b"\0\0"*4

class SDKTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();path=Path(self.temp.name)/"bse.csv"
        path.write_text("xx.Token,Exch,Type,Symbol,Expiry,Strike,OpType,Token\n456,BSE,FUTURE,SENSEX,29-OCT-26,0,,IO\n")
        self.lib=client.load(os.environ.get("KE2_SDK_LIBRARY",str(ROOT/"build-sdk-linux/libke2_feed.so")))
        self.lib.ke2_feed_process_datagram.argtypes=[C.c_void_p,C.c_uint32,C.c_void_p,C.c_uint32]
        self.lib.ke2_feed_process_datagram.restype=C.c_int32
        self.rows=[];self.got=threading.Event()
        @client.CALLBACK
        def callback(user,data,size):
            self.rows.append(json.loads(C.string_at(data,size)));self.got.set()
        self.callback=callback;self.handle=C.c_void_p()
        self.assertEqual(self.lib.ke2_feed_create(None,str(path).encode(),callback,None,C.byref(self.handle)),0)

    def tearDown(self):
        self.lib.ke2_feed_stop(self.handle);self.lib.ke2_feed_destroy(self.handle);self.temp.cleanup()

    def test_concurrent_injection_is_serialized(self):
        data=C.create_string_buffer(packet());errors=[]
        def inject():
            for _ in range(100):
                result=self.lib.ke2_feed_process_datagram(self.handle,2,data,len(packet()))
                if result:errors.append(result)
        threads=[threading.Thread(target=inject) for _ in range(4)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(errors,[]);self.assertEqual(len(self.rows),400)
        self.assertTrue(all(row["volume"]==123456 and row["token"]==456 for row in self.rows))

    def test_local_multicast_delivery_and_stop(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserve:
            reserve.bind(("127.0.0.1",0));port=reserve.getsockname()[1]
        stream=client.Stream(C.sizeof(client.Stream),2,b"239.255.243.32",b"127.0.0.1",port)
        self.assertEqual(self.lib.ke2_feed_start(self.handle,C.byref(stream),1),0,
                         self.lib.ke2_feed_last_error())
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton("127.0.0.1"))
            sender.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_LOOP,1)
            sender.sendto(packet(),("239.255.243.32",port))
            self.assertTrue(self.got.wait(3),"No decoded callback from loopback multicast")
            self.assertEqual(self.rows[-1]["ask"],100.05)
            self.assertEqual(self.lib.ke2_feed_stop(self.handle),0)
            count=len(self.rows)
            sender.sendto(packet(),("239.255.243.32",port));time.sleep(.25)
            self.assertEqual(len(self.rows),count)

if __name__=="__main__":unittest.main()
