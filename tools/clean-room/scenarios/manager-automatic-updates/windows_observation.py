"""Bounded desktop evidence with capture-time coverage and deferred attribution."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import os
import threading
import time
from typing import TYPE_CHECKING, Protocol

from native import UnsupportedNative

if TYPE_CHECKING:
    from windows_api import WinAPI


class NativeBlocked(UnsupportedNative):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class HookPump(Protocol):
    count: int

    def start(self) -> None: ...
    def drain(self) -> None: ...
    def close(self) -> None: ...


class WindowObserver:
    INTERVAL = 0.02
    MAX_GAP = 0.5
    EVENT_LIMIT = 4096

    def __init__(self, api: WinAPI) -> None:
        self.api = api
        api.refresh_processes()
        baseline = api.windows()
        self.baseline = {window.key for window in baseline.values()}
        self.raw_baseline = api.raw_windows()
        self.foreground = api.foreground()
        if not self.foreground:
            raise NativeBlocked("foreground-unavailable", "no foreground window at observation start")
        self.foreground_identity = api.window_identity(self.foreground)
        self.roots = {api.process(os.getpid()).key}
        self.events: list[dict] = []
        self.trace: deque[dict] = deque(maxlen=128)
        self.samples = 0
        self.max_gap = 0.0
        self.started = time.monotonic()
        self.ended: float | None = None
        self.previous: dict[str, float] = {}
        self.stage_maxima: dict[str, float] = {}
        self.first_capture: dict[str, float] = {}
        self.error: str | None = None
        self.cleanup_error: str | None = None
        self.callback_error: str | None = None
        self.overflow = False
        self.hook_count = 0
        self.hooks_closed = False
        self.sealed = False
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.RLock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self._packet: dict | None = None
        self._visible_seen: set[tuple[int, int, int]] = set()
        self._pending_neighbors: list[dict] = []

    def add_pid(self, pid: int) -> None:
        info = self.api.process(pid)
        if info.session != self.api.session(os.getpid()):
            raise NativeBlocked("fixture-session-mismatch", "fixture child left the interactive session")
        if self.api.elevated(self.api.handles[pid]):
            raise NativeBlocked("fixture-elevated", "fixture child unexpectedly has an elevated token")
        with self.lock:
            if self.ended is not None:
                raise NativeBlocked("observer-sealed", "cannot change a completed observation")
            self.roots.add(info.key)

    def _record(self, kind: str, *, violation: bool = True, **data: object) -> None:
        if len(self.events) >= self.EVENT_LIMIT:
            self.overflow = True
            return
        record = {
            "kind": kind, "violation": violation, "received": time.monotonic(),
            "neighbors_before": list(self.trace)[-2:], **data,
        }
        self.events.append(record)
        self._pending_neighbors.append(record)

    def callback_failed(self, exc: Exception) -> None:
        self.callback_error = f"{type(exc).__name__}: {exc}"

    def event(self, event: int, hwnd: int, object_id: int, child_id: int,
              source_thread: int, generated_ms: int, delivery_delay_ms: int = 0) -> None:
        """The WinEvent callback records raw evidence only, never process inspection."""
        try:
            with self.lock:
                names = {3: "foreground-event", 0x8000: "create-event",
                         0x8001: "destroy-event", 0x8002: "show-event", 0x8003: "hide-event"}
                relevant = event == 3 or (object_id == 0 and child_id == 0)
                self._record(
                    names.get(event, "unknown-event"),
                    violation=relevant and event in {3, 0x8002},
                    hwnd=hwnd, object_id=object_id, child_id=child_id,
                    source_thread=source_thread, generated_ms=generated_ms,
                    delivery_delay_ms=delivery_delay_ms,
                )
                if delivery_delay_ms > self.MAX_GAP * 1000:
                    self._record("delayed-event-delivery", hwnd=hwnd,
                                 delivery_delay_ms=delivery_delay_ms)
        except Exception as exc:
            self.callback_failed(exc)

    def _coverage(self, channel: str, start: float, end: float) -> None:
        previous = self.previous.get(channel, self.started)
        gap = max(start - previous, end - start) if channel != "desktop" else end - start
        self.max_gap = max(self.max_gap, gap)
        self.first_capture.setdefault(channel, start)
        self.previous[channel] = end
        self.stage_maxima[channel] = max(self.stage_maxima.get(channel, 0.0), end - start)
        if gap > self.MAX_GAP:
            self._record("coverage-gap", channel=channel, start=start, end=end,
                         previous_end=previous, seconds=gap)

    def sample(self) -> None:
        with self.lock:
            if self.ended is not None:
                raise NativeBlocked("observer-sealed", "cannot sample a completed observation")
            sample: dict = {"index": self.samples, "started": time.monotonic()}
            start = time.monotonic()
            foreground, identity = None, None
            try:
                foreground = self.api.foreground()
                identity = self.api.window_identity(foreground) if foreground else None
            except Exception as exc:
                self._record("foreground-identity-failed", hwnd=foreground,
                             error=f"{type(exc).__name__}: {exc}")
            end = time.monotonic()
            self._coverage("foreground", start, end)
            sample["foreground"] = {
                "start": start, "end": end, "hwnd": foreground, "identity": identity,
            }
            if foreground == 0:
                self._record("foreground-null", hwnd=0)
            elif foreground is not None and (
                foreground != self.foreground or identity != self.foreground_identity
            ):
                self._record("foreground", hwnd=foreground, identity=identity)
            if foreground is not None:
                self.foreground, self.foreground_identity = foreground, identity
            start = time.monotonic()
            try:
                windows = self.api.raw_windows()
            except Exception as exc:
                windows = None
                self._record("visible-capture-failed", error=f"{type(exc).__name__}: {exc}")
            end = time.monotonic()
            self._coverage("visible", start, end)
            sample["visible"] = {"start": start, "end": end,
                                 "count": len(windows) if windows is not None else None}
            if windows is not None:
                if foreground and foreground not in windows:
                    self._record("foreground-missing-enumeration", hwnd=foreground, identity=identity)
                for hwnd, window_identity in windows.items():
                    key = (hwnd, *window_identity)
                    if self.raw_baseline.get(hwnd) != window_identity and key not in self._visible_seen:
                        if len(self._visible_seen) < self.EVENT_LIMIT:
                            self._visible_seen.add(key)
                        else:
                            self.overflow = True
                        self._record("visible", hwnd=hwnd, identity=window_identity)
            sample["ended"] = time.monotonic()
            for record in self._pending_neighbors:
                record["neighbor_after"] = sample
            self._pending_neighbors.clear()
            self.trace.append(sample)
            self.samples += 1

    def _pump(self, hooks: HookPump) -> None:
        start = time.monotonic()
        hooks.drain()
        with self.lock:
            self._coverage("hooks", start, time.monotonic())

    def _run(self) -> None:
        hooks = None
        try:
            self.started = time.monotonic()
            hooks = self.api.event_hooks(self.event, self.callback_failed)
            hooks.start()
            self.hook_count = hooks.count
            while True:
                self._pump(hooks)
                self.sample()
                if self.samples % 50 == 1:
                    start = time.monotonic()
                    try:
                        self.api.require_desktop()
                    except Exception as exc:
                        self._record("desktop-unobservable", error=f"{type(exc).__name__}: {exc}")
                    with self.lock:
                        self._coverage("desktop", start, time.monotonic())
                self.ready.set()
                if self.stop.wait(self.INTERVAL):
                    break
            self.sample()
            self._pump(hooks)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if hooks is not None:
                try:
                    hooks.close()
                    self.hooks_closed = True
                except Exception as exc:
                    self.cleanup_error = f"{type(exc).__name__}: {exc}"
            with self.lock:
                self.ended = time.monotonic()
                for channel in ("foreground", "visible", "hooks"):
                    tail = self.ended - self.previous.get(channel, self.started)
                    self.max_gap = max(self.max_gap, tail)
                    if tail > self.MAX_GAP:
                        self._record("coverage-tail-gap", channel=channel, seconds=tail)
            self.ready.set()

    def __enter__(self) -> WindowObserver:
        self.thread.start()
        ready = self.ready.wait(5)
        with self.lock:
            invalid = any(event["violation"] for event in self.events)
        if (not ready or invalid or self.error or self.cleanup_error or self.callback_error
                or self.overflow or self.ended is not None or self.hook_count != 2):
            self.__exit__()
            raise NativeBlocked("observer-not-ready", self.error or self.cleanup_error or
                                "capture and WinEvent hooks did not become ready")
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.error = "window observer did not quiesce"
        else:
            with self.lock:
                self.sealed = True

    def _violation(self, event: dict) -> dict:
        result = {**event, "pid": None, "image": None, "attribution": "ambiguous-desktop"}
        identity = event.get("identity")
        if identity:
            result["pid"] = identity[0]
            try:
                process = self.api.process(identity[0])
                lineage = self.api.lineage(identity[0])
                result["image"] = process.image
                result["attribution"] = (
                    "fixture" if set(lineage) & self.roots else
                    "ambiguous-console-host" if process.image in self.api.console_images else
                    f"ambiguous-desktop-{event['kind']}"
                )
            except Exception as exc:
                result["identity_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def packet(self) -> dict:
        with self.lock:
            if self._packet is not None:
                return deepcopy(self._packet)
            violations = [self._violation(event) for event in self.events if event["violation"]]
            packet = {
                "baseline_visible_windows": len(self.baseline), "samples": self.samples,
                "observed_seconds": (self.ended if self.ended is not None else
                                     self.previous.get("foreground", self.started)) - self.started,
                "observation_started": self.started, "observation_ended": self.ended,
                "poll_interval_seconds": self.INTERVAL,
                "maximum_observation_gap_seconds": self.max_gap,
                "capture_stage_maximum_seconds": dict(self.stage_maxima),
                "first_capture": dict(self.first_capture), "last_capture": dict(self.previous),
                "recent_captures": list(self.trace), "raw_events": deepcopy(self.events),
                "event_limit": self.EVENT_LIMIT, "overflow": self.overflow,
                "hooks_registered": self.hook_count, "hooks_closed": self.hooks_closed,
                "observation_policy": "no new visible windows or foreground transitions",
                "violations": violations, "observer_error": self.error,
                "cleanup_error": self.cleanup_error, "callback_error": self.callback_error,
                "sealed": self.sealed,
                "passed": bool(
                    self.sealed and not self.thread.is_alive() and self.hook_count == 2
                    and self.hooks_closed and not violations and self.error is None
                    and self.cleanup_error is None and self.callback_error is None
                    and not self.overflow and self.max_gap <= self.MAX_GAP and self.samples > 0
                ),
            }
            if self.sealed and not self.thread.is_alive():
                self._packet = deepcopy(packet)
            return packet
