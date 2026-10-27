"""Startup-only transfer of a sacrificial helper's lifetime to its real parent."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
import os
import re
import threading
from typing import Any

from agent_procutil import _build_job_extended_limit_info, _is_windows
from agent_procutil._daemon_instance_identity import _windows_birth
from agent_procutil._windows_caller_owned import _api as _child_api


@dataclass(frozen=True)
class ParentJob:
    """The handle belongs to the parent process, never to this helper."""

    parent_pid: int
    parent_birth: str
    worker_pid: int
    worker_birth: str
    parent_handle: int


_transferred = False


@lru_cache(maxsize=1)
def _api() -> Any:
    kernel = _child_api()
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    return kernel


def _check(result: object) -> None:
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())


def _parent_live(kernel: Any, handle: int) -> None:
    state = kernel.WaitForSingleObject(handle, 0)
    if state == 0:
        raise ProcessLookupError("original parent exited before lifetime transfer")
    if state != 0x102:
        raise ctypes.WinError(ctypes.get_last_error())


def transfer_lifetime_to_parent(*, parent_pid: int, parent_birth: str) -> ParentJob:
    """Bind this startup helper to the original parent before application work.

    Only a single-threaded, sacrificial direct child may call this, once. The
    caller must already admit the interpreter and helper source. This does not
    select Python, authenticate a command, or prove application settlement.

    The parent receives the only continuing, non-inheritable Job handle. It
    must adopt that handle before accepting work and close it after observing
    this worker's completion. Parent death then kills this worker and all its
    descendants in the kernel, even if Python is blocked or dies abruptly.
    No helper-side Job handle or polling watchdog can prolong that lifetime.

    A failure after self-assignment is terminal for this sacrificial process;
    closing its last Job handle may terminate it before Python can unwind.
    Callers must never continue application work following a failed transfer.
    """
    global _transferred
    if not _is_windows():
        raise OSError("parent-owned Job transfer requires native Windows")
    if threading.current_thread() is not threading.main_thread() or threading.active_count() != 1:
        raise RuntimeError("parent-owned Job transfer requires single-threaded startup")
    if _transferred:
        raise RuntimeError("this worker already transferred its lifetime")
    if (
        type(parent_pid) is not int or not 0 < parent_pid <= 0xFFFFFFFF
        or parent_pid == os.getpid() or parent_pid != os.getppid()
    ):
        raise ValueError("lifetime owner must be this worker's original direct parent")
    if (
        not isinstance(parent_birth, str)
        or re.fullmatch(r"windows:[1-9][0-9]{0,19}", parent_birth) is None
        or int(parent_birth.partition(":")[2]) >= 1 << 64
    ):
        raise ValueError("original parent requires an exact Windows birth identity")
    kernel = _api()
    # Pin query, synchronization and duplication rights together on the same
    # process object; never reopen a numeric PID after validating its birth.
    parent = kernel.OpenProcess(0x1000 | 0x100000 | 0x40, False, parent_pid)
    _check(parent)
    job = None
    remote = wintypes.HANDLE()
    duplicated = False
    assigned = False
    close_attempted = False
    try:
        if _windows_birth(parent) != parent_birth:
            raise ProcessLookupError("original parent birth identity changed")
        _parent_live(kernel, parent)
        current = kernel.GetCurrentProcess()
        worker_birth = _windows_birth(current)
        job = kernel.CreateJobObjectW(None, None)
        _check(job)
        limits = _build_job_extended_limit_info()()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        _check(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        # Duplicate first, but permit no application work before self-assignment
        # and local-handle closure have both succeeded.
        _check(kernel.DuplicateHandle(current, job, parent, ctypes.byref(remote), 0, False, 2))
        duplicated = True
        if not remote.value:
            raise RuntimeError("Job transfer returned an empty parent handle")
        _parent_live(kernel, parent)
        _check(kernel.AssignProcessToJobObject(job, current))
        assigned = True
        _transferred = True
        close_attempted = True
        _check(kernel.CloseHandle(job))
        job = None
        return ParentJob(parent_pid, parent_birth, os.getpid(), worker_birth, remote.value)
    finally:
        try:
            if duplicated and remote.value and not assigned:
                # The parent has not received the handle yet and cannot use it.
                # CLOSE_SOURCE closes it even on failure; never retry its value.
                recovered = wintypes.HANDLE()
                _check(kernel.DuplicateHandle(
                    parent, remote, kernel.GetCurrentProcess(),
                    ctypes.byref(recovered), 0, False, 1 | 2,
                ))
                _check(kernel.CloseHandle(recovered))
        finally:
            try:
                if job and not close_attempted:
                    _check(kernel.CloseHandle(job))
            finally:
                _check(kernel.CloseHandle(parent))
