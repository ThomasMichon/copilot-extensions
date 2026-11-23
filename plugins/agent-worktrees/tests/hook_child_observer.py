"""Bounded, opt-in metadata for one original test-created Windows child."""

from __future__ import annotations

import ctypes as ct
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType, SimpleNamespace
from typing import Any, Protocol, cast

from agent_procutil._daemon_instance_identity import created_process_identity

Sample = dict[str, object]
_NATIVE_RUN = subprocess.run
_NATIVE_POPEN = subprocess.Popen


def is_native_subprocess(value: object) -> bool:
    return (
        value is subprocess and subprocess.run is _NATIVE_RUN
        and subprocess.Popen is _NATIVE_POPEN
    )


def last_error() -> int:
    return int(getattr(ct, "get_last_error")())


def windows_error() -> OSError:
    return cast(OSError, getattr(ct, "WinError")(last_error()))


class NativeSample(Protocol):
    def sample(self) -> Sample: ...
    def close(self) -> None: ...


class WindowsChildSample:
    MAX_THREADS = 16
    WAIT_SECONDS = .025

    def __init__(
        self, process: subprocess.Popen[Any], *, include_identity: bool = False,
    ) -> None:
        from ctypes import wintypes as wt

        identity = created_process_identity(process)
        if os.name != "nt" or identity is None:
            raise RuntimeError("created child identity unavailable")
        self.process, self.pid = process, identity[0]
        self.birth, self.include_identity = identity[1], include_identity
        self.handle = int(getattr(process, "_handle"))
        library = getattr(ct, "WinDLL")
        self.kernel = library("kernel32", use_last_error=True)
        self.advapi = library("advapi32", use_last_error=True)
        self.snapshot = wt.HANDLE()
        self.marker = wt.HANDLE()
        self.sessions: list[int] = []
        self.threads: list[int] = []
        self.callbacks: list[Any] = []
        self.buffers: list[Any] = []
        self.waits: list[Sample] = []

        class Thread(ct.Structure):
            _fields_ = [
                ("exit_status", wt.DWORD), ("teb", ct.c_void_p),
                ("pid", wt.DWORD), ("tid", wt.DWORD), ("affinity", ct.c_size_t),
                ("priority", ct.c_int), ("base_priority", ct.c_int),
                ("syscall_argument", ct.c_void_p), ("syscall", wt.WORD),
                ("created", wt.FILETIME), ("exited", wt.FILETIME),
                ("kernel", wt.FILETIME), ("user", wt.FILETIME),
                ("start_address", ct.c_void_p), ("captured", wt.FILETIME),
                ("flags", ct.c_int), ("suspend_count", wt.WORD),
                ("context_size", wt.WORD), ("context", ct.c_void_p),
            ]

        class Lock(ct.Structure):
            _fields_ = [
                ("name", wt.WCHAR * 128), ("timeout", ct.c_longlong),
                ("alertable", wt.BOOL),
            ]

        class WaitThread(ct.Structure):
            _fields_ = [
                ("pid", wt.DWORD), ("tid", wt.DWORD),
                ("wait_ms", wt.DWORD), ("switches", wt.DWORD),
            ]

        class Detail(ct.Union):
            _fields_ = [("lock", Lock), ("thread", WaitThread)]

        class Node(ct.Structure):
            _fields_ = [("kind", ct.c_int), ("status", ct.c_int), ("detail", Detail)]

        self.Thread, self.Node = Thread, Node
        self.Callback = getattr(ct, "WINFUNCTYPE")(
            None, wt.HANDLE, ct.c_size_t, wt.DWORD,
            ct.POINTER(wt.DWORD), ct.POINTER(Node), ct.POINTER(wt.BOOL),
        )
        signatures: dict[str, tuple[list[Any], Any]] = {
            "GetProcessTimes": ([wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
            "PssCaptureSnapshot": ([wt.HANDLE, ct.c_int, wt.DWORD, ct.POINTER(wt.HANDLE)], wt.DWORD),
            "PssWalkMarkerCreate": ([ct.c_void_p, ct.POINTER(wt.HANDLE)], wt.DWORD),
            "PssWalkSnapshot": ([wt.HANDLE, ct.c_int, wt.HANDLE, ct.c_void_p, wt.DWORD], wt.DWORD),
            "PssWalkMarkerFree": ([wt.HANDLE], wt.DWORD),
            "PssFreeSnapshot": ([wt.HANDLE, wt.HANDLE], wt.DWORD),
            "GetCurrentProcess": ([], wt.HANDLE),
            "OpenThread": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "GetProcessIdOfThread": ([wt.HANDLE], wt.DWORD),
            "GetThreadTimes": ([wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.advapi.OpenThreadWaitChainSession.argtypes = [wt.DWORD, self.Callback]
        self.advapi.OpenThreadWaitChainSession.restype = wt.HANDLE
        self.advapi.GetThreadWaitChain.argtypes = [
            wt.HANDLE, ct.c_size_t, wt.DWORD, wt.DWORD,
            ct.POINTER(wt.DWORD), ct.POINTER(Node), ct.POINTER(wt.BOOL),
        ]
        self.advapi.GetThreadWaitChain.restype = wt.BOOL
        self.advapi.CloseThreadWaitChainSession.argtypes = [wt.HANDLE]
        self.advapi.CloseThreadWaitChainSession.restype = None

    @staticmethod
    def require_success(code: int) -> None:
        if code:
            raise OSError(code, "native observation unavailable")

    def sample(self) -> Sample:
        from ctypes import wintypes as wt

        times = [wt.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *(ct.byref(value) for value in times)):
            raise windows_error()
        ticks = [
            (value.dwHighDateTime << 32) | value.dwLowDateTime for value in times
        ]
        state = self.kernel.WaitForSingleObject(self.handle, 0)
        result: Sample = {
            "process_state": {0: "exited", 258: "live"}.get(state, "unknown"),
            "kernel_ticks": ticks[2], "user_ticks": ticks[3],
        }
        if self.include_identity:
            if (
                state != 0 or ticks[0] <= 0 or ticks[1] < ticks[0]
                or self.birth != f"windows:{ticks[0]}"
            ):
                raise RuntimeError("exited child identity unavailable")
            return {
                **result, "pid": self.pid,
                "birth_filetime": ticks[0], "exit_filetime": ticks[1],
            }
        if state != 258:
            return result
        self.require_success(self.kernel.PssCaptureSnapshot(
            self.handle, 0x80, 0, ct.byref(self.snapshot),
        ))
        self.require_success(self.kernel.PssWalkMarkerCreate(None, ct.byref(self.marker)))
        entries: list[tuple[int, int, int]] = []
        for _ in range(self.MAX_THREADS + 1):
            entry = self.Thread()
            status = self.kernel.PssWalkSnapshot(
                self.snapshot, 3, self.marker, ct.byref(entry), ct.sizeof(entry),
            )
            if status == 259:
                break
            self.require_success(status)
            if entry.pid != self.pid:
                raise RuntimeError("snapshot child identity mismatch")
            birth = (entry.created.dwHighDateTime << 32) | entry.created.dwLowDateTime
            entries.append((entry.tid, birth, entry.suspend_count))
        result["threads_truncated"] = len(entries) > self.MAX_THREADS
        result["threads_observed"] = min(len(entries), self.MAX_THREADS)
        result["suspended_threads"] = sum(count > 0 for _, _, count in entries[:self.MAX_THREADS])
        deadline = time.monotonic() + self.WAIT_SECONDS
        pending: list[threading.Event] = []
        for tid, birth, _ in entries[:self.MAX_THREADS]:
            thread = self.kernel.OpenThread(0x100000 | 0x800, False, tid)
            if not thread:
                self.waits.append({"thread_pin_error": last_error()})
                continue
            self.threads.append(thread)
            thread_times = [wt.FILETIME() for _ in range(4)]
            readable = self.kernel.GetThreadTimes(
                thread, *(ct.byref(value) for value in thread_times),
            )
            actual_birth = (
                thread_times[0].dwHighDateTime << 32
            ) | thread_times[0].dwLowDateTime
            if (
                not readable or actual_birth != birth
                or self.kernel.GetProcessIdOfThread(thread) != self.pid
                or self.kernel.WaitForSingleObject(thread, 0) != 258
            ):
                self.waits.append({"thread_identity_unavailable": True})
                continue
            done = threading.Event()
            pending.append(done)

            def receive(
                _session: int, _context: int, status: int, count: Any,
                nodes: Any, _cycle: Any, *, completed: threading.Event = done,
            ) -> None:
                try:
                    row: Sample = {"status_code": status}
                    if status in (0, 234, 565) and count and nodes:
                        limit = min(int(count[0]), 16)
                        # Do not expose names, addresses, IDs or another process's nodes.
                        row["nodes"] = [
                            {"kind": int(nodes[index].kind), "status": int(nodes[index].status)}
                            for index in range(limit)
                            if nodes[index].kind != 8
                            or nodes[index].detail.thread.pid == self.pid
                        ]
                    self.waits.append(row)
                except Exception:
                    self.waits.append({"callback_error": True})
                finally:
                    completed.set()

            callback = self.Callback(receive)
            self.callbacks.append(callback)
            session = self.advapi.OpenThreadWaitChainSession(1, callback)
            if not session:
                self.waits.append({"status_code": last_error()})
                done.set()
                continue
            self.sessions.append(session)
            count, nodes, cycle = wt.DWORD(16), (self.Node * 16)(), wt.BOOL()
            self.buffers.append((count, nodes, cycle))
            if not self.advapi.GetThreadWaitChain(
                session, 0, 0, tid, ct.byref(count), nodes, ct.byref(cycle),
            ):
                error = last_error()
                if error != 997:
                    self.waits.append({"status_code": error})
                    done.set()
        for done in pending:
            done.wait(max(0, deadline - time.monotonic()))
        result["waits_pending_at_cutoff"] = sum(not done.is_set() for done in pending)
        self.close()
        result["wait_callbacks_settled"] = all(done.is_set() for done in pending)
        result["waits"] = list(self.waits)
        return result

    def close(self) -> None:
        for session in self.sessions:
            self.advapi.CloseThreadWaitChainSession(session)
        self.sessions.clear()
        self.callbacks.clear()
        self.buffers.clear()
        while self.threads:
            if not self.kernel.CloseHandle(self.threads[-1]):
                raise windows_error()
            self.threads.pop()
        if self.marker:
            self.require_success(self.kernel.PssWalkMarkerFree(self.marker))
            self.marker.value = None
        if self.snapshot:
            self.require_success(self.kernel.PssFreeSnapshot(
                self.kernel.GetCurrentProcess(), self.snapshot,
            ))
            self.snapshot.value = None


class ChildObserver:
    INITIAL_DELAY = 0.0
    INTERVAL = .5
    MAX_SAMPLES = 24
    JOIN_SECONDS = .25

    def __init__(
        self, process: subprocess.Popen[Any], *,
        factory: Callable[[subprocess.Popen[Any]], NativeSample] = WindowsChildSample,
    ) -> None:
        self.process, self.factory = process, factory
        self.stop = threading.Event()
        self.samples: list[Sample] = []
        self.errors: list[Sample] = []
        self.unsettled: list[NativeSample] = []
        self.worker = threading.Thread(target=self.collect, daemon=True, name="hook-child-metadata")
        self.started = time.monotonic()
        self.closed = False

    def sample_once(self) -> bool:
        native: NativeSample | None = None
        sampled = False
        try:
            native = self.factory(self.process)
            value = native.sample()
            self.samples.append({
                "elapsed_ms": round((time.monotonic() - self.started) * 1000), **value,
            })
            sampled = True
        except Exception as error:
            self.errors.append({"stage": "sample", "winerror": getattr(error, "winerror", None)})
        finally:
            if native is not None:
                try:
                    native.close()
                except Exception as error:
                    self.unsettled.append(native)
                    self.errors.append({
                        "stage": "close", "winerror": getattr(error, "winerror", None),
                    })
        return sampled and not self.unsettled

    def collect(self) -> None:
        if self.stop.wait(self.INITIAL_DELAY):
            self.closed = True
            return
        for _ in range(self.MAX_SAMPLES):
            if self.stop.is_set() or not self.sample_once():
                break
            if self.stop.wait(self.INTERVAL):
                break
        self.closed = True

    def start(self) -> None:
        self.worker.start()

    def finish(self, outcome: str) -> Sample:
        self.stop.set()
        if self.worker.ident is not None:
            self.worker.join(self.JOIN_SECONDS)
        return {
            "outcome": outcome,
            "collector_settled": not self.worker.is_alive() and self.closed and not self.unsettled,
            "samples": list(self.samples), "errors": list(self.errors),
        }


class PostExitChildObserver(ChildObserver):
    """Start the single query only after stdlib has cleaned up a timeout."""

    def __init__(
        self, process: subprocess.Popen[Any], *,
        factory: Callable[[subprocess.Popen[Any]], NativeSample] | None = None,
    ) -> None:
        super().__init__(
            process, factory=factory if factory is not None else
            lambda selected: WindowsChildSample(selected, include_identity=True),
        )

    def start(self) -> None:
        pass

    def collect(self) -> None:
        try:
            if self.process.poll() is None:
                self.errors.append({"stage": "post-exit", "process_state": "live"})
            else:
                self.sample_once()
        except Exception as error:
            self.errors.append({"stage": "post-exit", "winerror": getattr(error, "winerror", None)})
        finally:
            self.closed = True

    def finish(self, outcome: str) -> Sample:
        if outcome == "timeout":
            try:
                super().start()
            except Exception as error:
                self.errors.append({"stage": "start", "winerror": getattr(error, "winerror", None)})
                self.closed = True
        else:
            self.closed = True
        return {
            **super().finish(outcome), "observer_started_monotonic": self.started,
        }


@dataclass
class CommunicationObservation:
    observer: ChildObserver | None = None
    outcome: str = "error"


def observe_communication(
    process: subprocess.Popen[Any], call: Callable[[], Any],
    pending: list[CommunicationObservation], *,
    factory: Callable[[subprocess.Popen[Any]], ChildObserver] = ChildObserver,
) -> Any:
    record = CommunicationObservation()
    pending.append(record)
    try:
        record.observer = factory(process)
        record.observer.start()
    except Exception:
        pass
    try:
        result = call()
        record.outcome = "returned"
        return result
    except subprocess.TimeoutExpired:
        record.outcome = "timeout"
        raise
    finally:
        try:
            if record.observer is not None:
                record.observer.stop.set()
        except Exception:
            pass


def finish_observations(
    pending: list[CommunicationObservation], emit: Callable[[Sample], None],
) -> None:
    for record in pending:
        try:
            emit(record.observer.finish(record.outcome) if record.observer is not None else {
                "outcome": record.outcome, "collector_settled": False,
                "errors": [{"stage": "start"}],
            })
        except Exception:
            pass


class ObservedSubprocess(SimpleNamespace):
    @property
    def Popen(self) -> Any:
        return subprocess.Popen

    @Popen.setter
    def Popen(self, value: Any) -> None:
        setattr(subprocess, "Popen", value)


def observed_subprocess(
    emit: Callable[[Sample], None], *,
    factory: Callable[[subprocess.Popen[Any]], ChildObserver] = ChildObserver,
) -> SimpleNamespace:
    original_run = cast(FunctionType, subprocess.run)

    def run(*args: Any, **kwargs: Any) -> Any:
        pending: list[CommunicationObservation] = []

        class ObservedPopen(subprocess.Popen[Any]):
            def communicate(
                self, input: Any = None, timeout: float | None = None,
            ) -> Any:
                communicate = super().communicate
                if getattr(self, "_metadata_started", False):
                    return communicate(input=input, timeout=timeout)
                self._metadata_started = True
                return observe_communication(
                    self, lambda: communicate(input=input, timeout=timeout),
                    pending, factory=factory,
                )

        # Stdlib owns kill/drain/context-exit before any collector join or report.
        native_run = FunctionType(
            original_run.__code__, dict(original_run.__globals__, Popen=ObservedPopen),
            original_run.__name__, original_run.__defaults__, original_run.__closure__,
        )
        native_run.__kwdefaults__ = original_run.__kwdefaults__
        try:
            return native_run(*args, **kwargs)
        finally:
            finish_observations(pending, emit)

    namespace = vars(subprocess) | {"run": run}
    namespace.pop("Popen")
    return ObservedSubprocess(**namespace)
