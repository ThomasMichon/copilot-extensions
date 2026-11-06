"""Opt-in observation of one SDK-created, pre-resume root; never the desktop."""

from __future__ import annotations

from copy import deepcopy
import inspect
import math
from pathlib import Path
import threading
import time
from typing import Any

from sdk_desktop_observer import ObservationBlocked
from sdk_visibility_sources import SCOPED_PROFILE, ScopedEnrollment, verify_scoped_root_packet


class ScopedCleanupError(ObservationBlocked):
    """Unconfirmed observer cleanup retains its exact owner and evidence."""

    def __init__(self, observer: ScopedObserver, cause: BaseException) -> None:
        super().__init__("scoped observer cleanup is unconfirmed; exact custody retained")
        self.scoped_observer = observer
        self.scoped_observer_evidence = observer.packet()
        self.cleanup_error = cause


class ScopedObserver:
    """Wrap ``spawn(before_resume=observer.admit)`` in ``with observer``.

    Finish while the original Job is still queryable and the root has exited.
    Unadmitted descendants and all console/terminal-host associations are
    unsupported. This object never discovers processes or changes their state.
    """

    INTERVAL = .02
    MAX_GAP = .5
    EVENT_LIMIT = 4096

    def __init__(self, api: Any, enrollment: ScopedEnrollment) -> None:
        if type(enrollment) is not ScopedEnrollment:
            raise ObservationBlocked("scoped observation requires source-owner enrollment")
        self.api, self.enrollment = api, enrollment
        self.source_digest = enrollment.verify()
        self._owner()
        self.pin: Any = None
        self.thread_pin: int | None = None
        self.process: Any = None
        self.job: Any = None
        self.initial_thread = 0
        self.events: list[dict] = []
        self.known_windows: set[int] = set()
        self.errors: list[str] = []
        self.overflow = False
        self.hooks_closed = False
        self.hook_count = 0
        self.max_gap = 0.0
        self.pumps = 0
        self.started: float | None = None
        self.ended: float | None = None
        self.barrier: float | None = None
        self.root_exit_observed: float | None = None
        self.delivery_tail = 0.0
        self.settled = False
        self.sealed = False
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.pins_closed = False
        self.hooks: Any = None
        self.execution_deadline = enrollment.deadline
        self.cleanup_deadline = enrollment.deadline
        self._context_active = False
        self._close_attempted = False
        self._cleanup_error: ScopedCleanupError | None = None
        self._retained_pin_error: BaseException | None = None
        self._admission_error: BaseException | None = None

    def __enter__(self) -> ScopedObserver:
        if self._context_active or self._close_attempted:
            raise ObservationBlocked("scoped observer context cannot be reused")
        self._context_active = True
        return self

    def __exit__(
        self, _kind: type[BaseException] | None, error: BaseException | None, _trace: Any,
    ) -> bool:
        try:
            self._close_for_exit(error)
        finally:
            self._context_active = False
        return False

    def _close_for_exit(self, error: BaseException | None) -> None:
        try:
            self.close()
        except ScopedCleanupError as cleanup:
            if error is None:
                raise
            if cleanup is not error:
                error.scoped_observer_cleanup_error = cleanup
        finally:
            if error is not None:
                error.scoped_observer = self
                error.scoped_observer_evidence = self.packet()

    def fail(self, error: BaseException) -> None:
        with self.lock:
            if len(self.errors) < 64:
                self.errors.append(f"{type(error).__name__}: {error}")
            else:
                self.overflow = True

    def _owner(self) -> Any:
        import agent_procutil
        from agent_procutil import _windows_caller_owned as owner

        package = self.enrollment.root / "libs/agent-procutil/src/agent_procutil"
        if (
            Path(agent_procutil.__file__) != package / "__init__.py"
            or Path(owner.__file__) != package / "_windows_caller_owned.py"
        ):
            raise ObservationBlocked("scoped launch owner belongs to another SDK root")
        seam = inspect.signature(owner.spawn).parameters.get("before_resume")
        if (
            seam is None or seam.kind is not inspect.Parameter.KEYWORD_ONLY
            or not callable(getattr(owner, "require_pre_resume_admission", None))
        ):
            raise ObservationBlocked("selected native owner lacks scoped pre-resume admission")
        return owner

    def admit(
        self, process: Any, job: Any, deadline: float, cleanup_deadline: float,
    ) -> None:
        from agent_procutil._windows_caller_owned import _Process, _job_pids, _job_total, _api

        self.enrollment.verify()
        owner = self._owner()
        try:
            owner.require_pre_resume_admission(process, job, deadline, cleanup_deadline)
        except RuntimeError as error:
            raise ObservationBlocked("scope lacks its native owner's pre-resume transaction") from error
        if (
            self.process is not None or self.sealed or type(process) is not _Process
            or getattr(process, "_observation_pending", False) is not True
            or not process._thread_handle or not job.handle
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (deadline, cleanup_deadline))
            or not time.monotonic() < deadline <= cleanup_deadline <= self.enrollment.deadline
        ):
            raise ObservationBlocked("scope requires one original pre-resume launch and deadline")
        self.process, self.job = process, job
        self.execution_deadline, self.cleanup_deadline = deadline, cleanup_deadline
        try:
            self.api.require_console()
            self.pin = self.api.pin_handle(process._handle, process.pid)
            self.thread_pin = self.api.duplicate(process._thread_handle)
            self.initial_thread = int(self.api.kernel.GetThreadId(self.thread_pin))
            if (
                not self.initial_thread
                or self.api.kernel.GetProcessIdOfThread(self.thread_pin) != self.pin.pid
                or _job_total(_api(), job.handle) != 1
                or _job_pids(job.handle) != {self.pin.pid}
            ):
                raise ObservationBlocked("native root/thread/Job ownership is incomplete")
            self.started = time.monotonic()
            self.thread = threading.Thread(target=self._run, name="sdk-scoped-observer")
            try:
                self.thread.start()
            except BaseException:
                if self.thread.ident is None:
                    self.thread = None
                raise
            if not self.ready.wait(max(0, deadline - time.monotonic())):
                raise ObservationBlocked("scoped subscription barrier timed out")
            if self.errors or not self.thread.is_alive() or self.pin.gone():
                raise ObservationBlocked("scoped subscription barrier failed")
            self.barrier = time.monotonic()
            if self.barrier >= deadline:
                raise ObservationBlocked("scoped subscription barrier expired")
        except BaseException as error:
            # Return to native root retirement before any observer join or lock.
            self._admission_error = error
            self.stop.set()
            if self.pin is None:
                self.pin = getattr(error, "process_pin", None)
                if self.pin is not None:
                    self._retained_pin_error = error
            error.scoped_observer = self
            raise

    def event(
        self, event: int, hwnd: int, object_id: int, child_id: int,
        source_thread: int, generated_ms: int, delay_ms: int,
    ) -> None:
        try:
            with self.lock:
                if self.sealed:
                    raise ObservationBlocked("callback after scoped seal")
                if self.errors:
                    return
                if event != 3 and (object_id != 0 or child_id != 0):
                    return
                kind = {3: "foreground", 0x8000: "create", 0x8001: "destroy",
                        0x8002: "show", 0x8003: "hide"}.get(event)
                if kind is None or delay_ms > self.MAX_GAP * 1000 or not hwnd:
                    raise ObservationBlocked("unknown, delayed or incomplete scoped callback")
                if kind == "destroy":
                    if hwnd not in self.known_windows:
                        raise ObservationBlocked("destroyed endpoint lacks its creation witness")
                    visible = False
                    self.known_windows.remove(hwnd)
                else:
                    if self.api.window_identity(hwnd) != (self.pin.pid, source_thread):
                        raise ObservationBlocked("scoped callback endpoint identity changed")
                    self.known_windows.add(hwnd)
                    visible = bool(self.api.user.IsWindowVisible(hwnd))
                if len(self.events) >= self.EVENT_LIMIT or len(self.known_windows) > self.EVENT_LIMIT:
                    self.overflow = True
                    raise ObservationBlocked("scoped callback storage overflow")
                self.events.append({
                    "kind": kind, "hwnd": hwnd, "pid": self.pin.pid, "birth": self.pin.birth,
                    "thread": source_thread, "generated_ms": generated_ms,
                    "delay_ms": delay_ms, "received": time.monotonic(),
                    "violation": event == 3 or visible or event == 0x8002,
                })
        except BaseException as error:
            self.fail(error)

    def _run(self) -> None:
        hooks = None
        previous = time.monotonic()
        try:
            hooks = self.api.scoped_event_hooks(self.event, self.fail, self.pin)
            self.hooks = hooks
            hooks.start()
            self.hook_count = hooks.count
            self._require_initial_thread()
            for hwnd in self.api.thread_windows(self.initial_thread, self.pin.pid):
                self.event(0x8000, hwnd, 0, 0, self.initial_thread, 0, 0)
            self._require_initial_thread()
            if self.errors or self.hook_count != 2:
                raise ObservationBlocked("scoped initial coverage failed")
            while True:
                hooks.drain()
                if self.errors:
                    raise ObservationBlocked("scoped callback delivery failed")
                now = time.monotonic()
                self.max_gap = max(self.max_gap, now - previous)
                if now - previous > self.MAX_GAP:
                    raise ObservationBlocked("scoped message-pump coverage gap")
                previous = now
                self.pumps += 1
                self.ready.set()
                if self.stop.wait(self.INTERVAL):
                    break
                if now >= self.execution_deadline:
                    raise ObservationBlocked("scoped observation deadline expired")
        except BaseException as error:
            self.fail(error)
        finally:
            if hooks is not None:
                try:
                    hooks.close()
                except BaseException as error:
                    self.fail(error)
                finally:
                    self.hooks_closed = not hooks.handles
            self.ended = time.monotonic()
            if self.ended - previous > self.MAX_GAP:
                self.fail(ObservationBlocked("scoped cleanup coverage gap"))
            if self.ended >= self.execution_deadline:
                self.fail(ObservationBlocked("scoped observation crossed its execution deadline"))
            self.ready.set()

    def _require_initial_thread(self) -> None:
        if (
            not self.thread_pin
            or self.api.kernel.GetThreadId(self.thread_pin) != self.initial_thread
            or self.api.kernel.GetProcessIdOfThread(self.thread_pin) != self.pin.pid
            or self.api.kernel.WaitForSingleObject(self.thread_pin, 0) != 0x102
        ):
            raise ObservationBlocked("initial owned thread exited or changed during enumeration")

    def finish(self) -> dict:
        from agent_procutil._windows_caller_owned import _job_pids, _job_total, _api

        try:
            self.enrollment.verify()
            if self.pin is None or not self.pin.gone() or not self.job.handle:
                raise ObservationBlocked("scoped root exit or original Job is unconfirmed")
            if _job_total(_api(), self.job.handle) != 1 or _job_pids(self.job.handle):
                raise ObservationBlocked("unadmitted descendant or unsettled Job: unsupported scope")
            self.root_exit_observed = time.monotonic()
            tail_end = self.root_exit_observed + self.MAX_GAP
            if tail_end >= self.execution_deadline:
                raise ObservationBlocked("original execution budget cannot cover callback delivery tail")
            while time.monotonic() < tail_end:
                if self.errors or self.thread is None or not self.thread.is_alive():
                    raise ObservationBlocked("scoped delivery tail lost its original pump")
                time.sleep(min(self.INTERVAL, max(0, tail_end - time.monotonic())))
            self.delivery_tail = time.monotonic() - self.root_exit_observed
            self.enrollment.verify()
            if _job_total(_api(), self.job.handle) != 1 or _job_pids(self.job.handle):
                raise ObservationBlocked("original Job changed during scoped delivery tail")
            self.settled = True
        except BaseException as error:
            self.fail(error)
        self.close()
        packet = self.packet()
        if not packet["qualified"]:
            raise ObservationBlocked("scoped observation is unqualified; retain packet() evidence")
        verify_scoped_root_packet(packet, self.enrollment, packet["identity"])
        return packet

    def close(self) -> None:
        if self._close_attempted:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            return
        self._close_attempted = True
        try:
            if self._admission_error is not None:
                self.fail(self._admission_error)
            self.stop.set()
            if self.thread is not None:
                self.thread.join(max(0, self.cleanup_deadline - time.monotonic()))
                if self.thread.is_alive():
                    raise ObservationBlocked("scoped pump cleanup is unconfirmed")
            if self.hooks is None:
                self.hooks_closed = True
            if not self.hooks_closed:
                raise ObservationBlocked("scoped unhooking is unconfirmed")
            if self._retained_pin_error is not None:
                raise ObservationBlocked("admission retained an unconfirmed process-pin close")
            if self.thread_pin is not None:
                self.api.check(self.api.kernel.CloseHandle(self.thread_pin), "close owned thread pin")
                self.thread_pin = None
            if self.pin is not None:
                self.pin.close()
            self.pins_closed = True
            self.sealed = True
        except BaseException as error:
            self.fail(error)
            self._cleanup_error = ScopedCleanupError(self, error)
            raise self._cleanup_error from error

    def packet(self) -> dict:
        with self.lock:
            qualified = bool(
                self.sealed and self.settled and self.hooks_closed and self.pins_closed
                and self.barrier is not None and self.hook_count == 2 and self.pumps > 0
                and self.max_gap <= self.MAX_GAP and not self.errors and not self.overflow
            )
            return deepcopy({
                "schema": SCOPED_PROFILE, "case": self.enrollment.case,
                "epoch": self.enrollment.epoch, "source_digest": self.source_digest,
                "scope": "sdk-pre-resume-root-only", "descendants": "unmanaged-unsupported",
                "console_hosts": "unverified-unsupported",
                "consumer_qualified": False,
                "identity": None if self.pin is None else {
                    "pid": self.pin.pid, "birth": self.pin.birth,
                    "initial_thread": self.initial_thread,
                },
                "started": self.started, "barrier": self.barrier, "ended": self.ended,
                "root_exit_observed": self.root_exit_observed, "delivery_tail": self.delivery_tail,
                "sealed": self.sealed, "root_and_job_settled": self.settled,
                "hooks_closed": self.hooks_closed, "pins_closed": self.pins_closed,
                "hook_count": self.hook_count, "pumps": self.pumps,
                "maximum_gap": self.max_gap, "overflow": self.overflow,
                "errors": self.errors, "raw_events": self.events,
                "qualified": qualified,
                "passed": qualified and not any(event["violation"] for event in self.events),
            })

    def require_host(self, *args: Any, **kwargs: Any) -> None:
        raise ObservationBlocked(
            "console/Default Terminal association is unsupported; no host subscription acquired"
        )
