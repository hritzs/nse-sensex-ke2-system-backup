"""Windows job ownership includes frozen worker bootloader descendants."""
import ctypes as C
from ctypes import wintypes as W
import os

class OwnedJob:
    def __init__(self):
        self.handle=None
        if os.name!="nt":return
        class Basic(C.Structure):
            _fields_=[("process_time",C.c_int64),("job_time",C.c_int64),("flags",W.DWORD),
                ("min_ws",C.c_size_t),("max_ws",C.c_size_t),("active",W.DWORD),
                ("affinity",C.c_size_t),("priority",W.DWORD),("scheduling",W.DWORD)]
        class IO(C.Structure):
            _fields_=[(n,C.c_uint64) for n in ("read_ops","write_ops","other_ops","read_bytes","write_bytes","other_bytes")]
        class Extended(C.Structure):
            _fields_=[("basic",Basic),("io",IO),("process_memory",C.c_size_t),
                ("job_memory",C.c_size_t),("peak_process",C.c_size_t),("peak_job",C.c_size_t)]
        self.api=C.WinDLL("kernel32",use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR]
        self.api.CreateJobObjectW.restype=W.HANDLE
        self.api.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
        self.api.SetInformationJobObject.restype=W.BOOL
        self.api.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
        self.api.AssignProcessToJobObject.restype=W.BOOL
        self.api.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]
        self.api.OpenProcess.restype=W.HANDLE
        self.api.CloseHandle.argtypes=[W.HANDLE];self.api.CloseHandle.restype=W.BOOL
        handle=self.api.CreateJobObjectW(None,None)
        if not handle:raise C.WinError(C.get_last_error())
        info=Extended();info.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(handle,9,C.byref(info),C.sizeof(info)):
            error=C.get_last_error();self.api.CloseHandle(handle);raise C.WinError(error)
        self.handle=handle
    def assign(self,process):
        if not self.handle:return
        handle=self.api.OpenProcess(0x0101,False,process.pid)
        if not handle:raise C.WinError(C.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle,handle):
                raise C.WinError(C.get_last_error())
        finally:self.api.CloseHandle(handle)
    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None
