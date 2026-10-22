"""Streamed stdlib receiver: bound preparation and its subprocess tree."""

from __future__ import annotations

import base64
import json
import os
import select
import signal
import subprocess
import sys
import threading

# The worker owns the kill-on-close Job before it can execute preparation or
# spawn installers. It inherits containment; no breakaway or durable process.
_WORKER = r"""
import ctypes,os,sys,threading
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
    kernel.TerminateJobObject.argtypes = [w.HANDLE,w.UINT]
    kernel.TerminateJobObject.restype = w.BOOL
    def expire(handle=job,terminate=kernel.TerminateJobObject,exit_now=os._exit):
        if not terminate(handle,124):
            exit_now(124)
    deadline = threading.Timer(float(sys.argv[1]),expire)
    deadline.daemon = True
    deadline.start()
exec(compile(sys.stdin.buffer.read().decode('utf-8'),'<ssh-plugin-preparation>','exec'))
"""


_POSIX_KEEPER = r"""
import base64,json,os,signal,subprocess,sys,time
try:
    request=json.loads(sys.stdin.buffer.read())
    worker=subprocess.Popen(
        [sys.executable,'-I','-S','-X','utf8','-u','-c',request['bootstrap'],str(request['timeout'])],
        stdin=subprocess.PIPE)
    try:
        worker.communicate(base64.b64decode(request['program']),timeout=request['timeout'])
        code=worker.returncode
    except subprocess.TimeoutExpired:
        code=124
    os.write(int(sys.argv[1]),str(code).encode('ascii'))
    # Keep group ownership until the supervisor reaps the whole group.
    time.sleep(15)
finally:
    # A lost supervisor (including a broken report pipe) cannot orphan writers.
    os.killpg(os.getpgrp(),signal.SIGKILL)
"""


def _posix_run(encoded: str, timeout: float) -> tuple[bytes, bytes, int]:
    completion, report = os.pipe()
    try:
        keeper = subprocess.Popen(  # headless-guard: allow streamed POSIX-only stdlib receiver
            [sys.executable, "-I", "-S", "-X", "utf8", "-u", "-c", _POSIX_KEEPER, str(report)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(report,), start_new_session=True,
        )
    except BaseException:
        os.close(completion)
        raise
    finally:
        os.close(report)
    results = []
    failures = []

    def communicate():
        try:
            results.append(keeper.communicate(json.dumps({
                "program": encoded, "bootstrap": _WORKER, "timeout": timeout,
            }).encode("utf-8")))
        except BaseException as exc:
            failures.append(exc)

    drain = threading.Thread(target=communicate, daemon=True)
    drain.start()
    try:
        ready, _, _ = select.select([completion], [], [], timeout)
        if ready:
            status = os.read(completion, 32)
            if not status:
                raise RuntimeError("SSH plugin receiver keeper exited without an outcome")
            code = int(status.decode("ascii"))
        else:
            code = 124
    finally:
        os.close(completion)
        # The keeper remains alive after the actual worker's success or error,
        # so its PID/group cannot be recycled before this cleanup boundary.
        if keeper.poll() is None:
            os.killpg(keeper.pid, signal.SIGKILL)
        drain.join(timeout=15)
    if drain.is_alive():
        raise RuntimeError("SSH plugin receiver pipes did not quiesce after cleanup")
    if failures:
        raise failures[0]
    stdout, stderr = results[0]
    return stdout, stderr, code


def supervise(encoded: str, timeout: float) -> None:
    """Exit only after the receiver and its captured streams have settled."""
    if os.name != "nt":
        stdout, stderr, code = _posix_run(encoded, timeout)
    else:
        worker = subprocess.Popen(
            [sys.executable, "-I", "-S", "-X", "utf8", "-u", "-c", _WORKER, str(timeout)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # headless-guard: allow streamed stdlib receiver has no host agent_procutil
        )
        try:
            stdout, stderr = worker.communicate(base64.b64decode(encoded), timeout=timeout)
            code = worker.returncode
        except subprocess.TimeoutExpired:
            worker.kill()  # Closing its Job kills installer descendants too.
            stdout, stderr = worker.communicate(timeout=15)
            code = 124
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    if code == 124:
        sys.stderr.write("\nSSH plugin receiver preparation timed out\n")
    raise SystemExit(code)
