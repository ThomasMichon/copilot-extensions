"""Streamed stdlib receiver: bound preparation and its subprocess tree."""

from __future__ import annotations

import base64
import os
import signal
import subprocess
import sys

# The worker owns the kill-on-close Job before it can execute preparation or
# spawn installers. It inherits containment; no breakaway or durable process.
_WORKER = r"""
import ctypes,os,sys
if os.name == 'nt':
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [
            ('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
            ('flags',w.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),
            ('active',w.DWORD),('affinity',ctypes.c_size_t),
            ('priority',w.DWORD),('scheduling',w.DWORD),
        ]
    class Counters(ctypes.Structure):
        _fields_ = [(name,ctypes.c_uint64) for name in (
            'reads','writes','other','read_bytes','write_bytes','other_bytes')]
    class Limits(ctypes.Structure):
        _fields_ = [('basic',Basic),('io',Counters),
            ('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
            ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p,w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE,w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    job = kernel.CreateJobObjectW(None,None)
    if not job: raise ctypes.WinError(ctypes.get_last_error())
    limits = Limits()
    limits.basic.flags = 0x2000
    if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
exec(compile(sys.stdin.buffer.read().decode('utf-8'),'<ssh-plugin-preparation>','exec'))
"""


def supervise(encoded: str, timeout: float) -> None:
    """Exit only after the receiver and its captured streams have settled."""
    worker = subprocess.Popen(
        [sys.executable, "-I", "-S", "-X", "utf8", "-u", "-c", _WORKER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # headless-guard: allow streamed stdlib receiver has no host agent_procutil
        start_new_session=os.name != "nt",
    )
    timed_out = False
    try:
        stdout, stderr = worker.communicate(base64.b64decode(encoded), timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            worker.kill()  # Closing its Job kills installer descendants too.
        else:
            os.killpg(worker.pid, signal.SIGKILL)
        stdout, stderr = worker.communicate(timeout=15)
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    if timed_out:
        sys.stderr.write("\nSSH plugin receiver preparation timed out\n")
        raise SystemExit(124)
    raise SystemExit(worker.returncode)
