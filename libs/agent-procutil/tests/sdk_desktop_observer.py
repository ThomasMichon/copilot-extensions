"""Whole-desktop evidence; native callbacks never perform process attribution."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import threading
import time


class ObservationBlocked(RuntimeError):
    pass


def capture_foreground(api) -> tuple[int, tuple[int, int, int]]:
    hwnd = api.foreground()
    if not hwnd:
        raise ObservationBlocked("foreground unavailable during direct capture")
    owner = api.window_identity(hwnd)
    pin = api.pin(owner[0])
    try:
        if (
            pin.gone() or not api.user.IsWindow(hwnd) or not api.user.IsWindowVisible(hwnd)
            or api.foreground() != hwnd or api.window_identity(hwnd) != owner
            or not api.user.IsWindow(hwnd) or not api.user.IsWindowVisible(hwnd) or pin.gone()
        ):
            raise ObservationBlocked("foreground changed, exited or is not directly visible")
        return hwnd, (*owner, pin.birth)
    finally:
        pin.close()


def validate_calibration(packet: dict, witnesses: list[dict], hwnd: int,
                         thread: int, original: int) -> None:
    if (
        not packet["sealed"] or not packet["hooks_closed"] or packet["hook_count"] != 2
        or packet["samples"] <= 0 or packet["maximum_gap"] > 0.5
        or any(packet[key] for key in ("error", "callback_error", "cleanup_error", "overflow"))
        or packet["passed"] is not False
    ):
        raise ObservationBlocked("calibration capture did not satisfy the original observer contract")
    invalid = {
        "foreground-identity-failed", "foreground-null", "foreground-missing-enumeration",
        "visible-capture-failed", "coverage-gap", "coverage-tail-gap",
        "delayed-event-delivery", "foreground-capture-changed",
        "foreground-enumeration-identity-mismatch", "unknown",
    }
    for event in packet["raw_events"]:
        if event["kind"] in invalid:
            raise ObservationBlocked(f"calibration has unobservable evidence: {event['kind']}")
        if event["kind"] in {"foreground", "sampled-foreground"} and event.get("hwnd") not in {
            hwnd, original,
        }:
            raise ObservationBlocked("calibration includes an unrelated foreground event")
        if event["kind"] == "sampled-visible" and event.get("hwnd") != hwnd:
            raise ObservationBlocked("calibration includes unrelated sampled visibility")
        if (event["kind"] == "show" and event.get("object_id") == 0
                and event.get("child_id") == 0 and event.get("hwnd") != hwnd):
            raise ObservationBlocked("calibration includes unrelated top-level visibility")
    if len(witnesses) != 4:
        raise ObservationBlocked("calibration needs four ordered raw witnesses")
    show, foreground, restored, retired = witnesses
    if (
        show["kind"] != "show" or show.get("hwnd") != hwnd or show.get("source_thread") != thread
        or foreground["kind"] != "foreground" or foreground.get("hwnd") != hwnd
        or restored["kind"] != "foreground" or restored.get("hwnd") != original
        or retired["kind"] not in {"hide", "destroy"} or retired.get("hwnd") != hwnd
        or retired.get("source_thread") != thread
        or not packet["started"] <= show["received"] <= foreground["received"]
        <= restored["received"] <= retired["received"] <= packet["ended"]
    ):
        raise ObservationBlocked("raw show/foreground/restoration/retirement order is incomplete")
    keys = ("kind", "hwnd", "received", "source_thread", "generated_ms")
    for witness in witnesses:
        if not any(all(event.get(key) == witness.get(key) for key in keys)
                   for event in packet["raw_events"]):
            raise ObservationBlocked("calibration witness is absent from the sealed raw stream")


class DesktopObserver:
    INTERVAL = 0.02
    MAX_GAP = 0.5
    EVENT_LIMIT = 4096

    def __init__(self, api, epoch: str) -> None:
        self.api, self.epoch = api, epoch
        api.require_desktop()
        self.baseline = api.raw_windows()
        self.foreground, self.foreground_identity = capture_foreground(api)
        self.events: list[dict] = []
        self.trace: deque[dict] = deque(maxlen=128)
        self.pending_neighbors: list[dict] = []
        self.visible_seen: set[tuple] = set()
        self.samples = 0
        self.started = time.monotonic()
        self.ended: float | None = None
        self.previous: dict[str, float] = {}
        self.first_capture: dict[str, float] = {}
        self.stage_maxima: dict[str, float] = {}
        self.max_gap = 0.0
        self.error: str | None = None
        self.callback_error: str | None = None
        self.cleanup_error: str | None = None
        self.overflow = False
        self.hook_count = 0
        self.hooks_closed = False
        self.sealed = False
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.RLock()
        self.thread = threading.Thread(target=self._run, name="sdk-desktop-observer", daemon=True)

    def record(self, kind: str, *, violation: bool = True, **data) -> None:
        if self.sealed:
            raise ObservationBlocked("cannot append to a sealed desktop observation")
        if len(self.events) >= self.EVENT_LIMIT:
            self.overflow = True
            return
        row = {
            "kind": kind, "violation": violation, "received": time.monotonic(),
            "neighbors_before": list(self.trace)[-2:], **data,
        }
        self.events.append(row)
        self.pending_neighbors.append(row)

    def callback_failed(self, error: Exception) -> None:
        self.callback_error = f"{type(error).__name__}: {error}"

    def event(self, event: int, hwnd: int, object_id: int, child_id: int,
              source_thread: int, generated_ms: int, delay_ms: int) -> None:
        if self.sealed:
            raise ObservationBlocked("callback arrived after desktop observation was sealed")
        try:
            with self.lock:
                kinds = {3: "foreground", 0x8000: "create", 0x8001: "destroy",
                         0x8002: "show", 0x8003: "hide"}
                relevant = event == 3 or (object_id == 0 and child_id == 0)
                self.record(
                    kinds.get(event, "unknown"), violation=relevant and event in {3, 0x8002},
                    hwnd=hwnd, object_id=object_id, child_id=child_id,
                    source_thread=source_thread, generated_ms=generated_ms, delay_ms=delay_ms,
                )
                if delay_ms > self.MAX_GAP * 1000:
                    self.record("delayed-event-delivery", hwnd=hwnd, delay_ms=delay_ms)
        except Exception as error:
            self.callback_failed(error)

    def coverage(self, channel: str, start: float, end: float) -> None:
        previous = self.previous.get(channel, self.started)
        gap = end - start if channel == "desktop" else max(start - previous, end - start)
        self.max_gap = max(self.max_gap, gap)
        self.first_capture.setdefault(channel, start)
        self.previous[channel] = end
        self.stage_maxima[channel] = max(self.stage_maxima.get(channel, 0), end - start)
        if gap > self.MAX_GAP:
            self.record("coverage-gap", channel=channel, start=start, end=end,
                        previous_end=previous, seconds=gap)

    def sample(self) -> None:
        with self.lock:
            if self.ended is not None:
                raise ObservationBlocked("cannot sample a completed desktop observation")
            row = {"index": self.samples, "started": time.monotonic()}
            start = time.monotonic()
            foreground, identity = None, None
            try:
                foreground, identity = capture_foreground(self.api)
            except Exception as error:
                self.record("foreground-identity-failed", hwnd=foreground,
                            error=f"{type(error).__name__}: {error}")
            end = time.monotonic()
            self.coverage("foreground", start, end)
            row["foreground"] = {"start": start, "end": end, "hwnd": foreground, "identity": identity}
            if foreground == 0:
                self.record("foreground-null", hwnd=0)
            elif foreground is not None and (
                foreground != self.foreground or identity != self.foreground_identity
            ):
                self.record("sampled-foreground", hwnd=foreground, identity=identity)
            if foreground is not None:
                self.foreground, self.foreground_identity = foreground, identity
            start = time.monotonic()
            try:
                windows = self.api.raw_windows()
            except Exception as error:
                windows = None
                self.record("visible-capture-failed", error=f"{type(error).__name__}: {error}")
            confirmation, confirmed_identity = None, None
            try:
                confirmation, confirmed_identity = capture_foreground(self.api)
                if (confirmation, confirmed_identity) != (foreground, identity):
                    self.record("foreground-capture-changed", hwnd=confirmation,
                                identity=confirmed_identity, previous_hwnd=foreground,
                                previous_identity=identity)
            except Exception as error:
                self.record("foreground-identity-failed", stage="after-enumeration",
                            error=f"{type(error).__name__}: {error}")
            end = time.monotonic()
            self.coverage("visible", start, end)
            row["visible"] = {"start": start, "end": end,
                              "count": len(windows) if windows is not None else None,
                              "foreground_enumerated": (
                                  foreground in windows if windows is not None and foreground else None
                              ),
                              "foreground_corroboration": {
                                  "hwnd": confirmation, "identity": confirmed_identity,
                              }}
            if windows is not None:
                # EnumWindows covers desktop apps, not every directly observable foreground.
                if foreground in windows and identity is not None and windows[foreground] != identity[:2]:
                    self.record("foreground-enumeration-identity-mismatch",
                                hwnd=foreground, identity=identity, enumerated=windows[foreground])
                for hwnd, owner in windows.items():
                    key = (hwnd, *owner)
                    if self.baseline.get(hwnd) != owner and key not in self.visible_seen:
                        if len(self.visible_seen) >= self.EVENT_LIMIT:
                            self.overflow = True
                        else:
                            self.visible_seen.add(key)
                        self.record("sampled-visible", hwnd=hwnd, identity=owner)
            row["ended"] = time.monotonic()
            for event in self.pending_neighbors:
                event["neighbor_after"] = row
            self.pending_neighbors.clear()
            self.trace.append(row)
            self.samples += 1

    def healthy(self) -> bool:
        with self.lock:
            return not any((self.error, self.callback_error, self.cleanup_error, self.overflow)) and (
                self.max_gap <= self.MAX_GAP
            )

    def raw(self, kind: str, hwnd: int, thread: int | None = None) -> list[dict]:
        with self.lock:
            return [
                deepcopy(event) for event in self.events
                if event["kind"] == kind and event.get("hwnd") == hwnd
                and (thread is None or event.get("source_thread") == thread)
                and event.get("object_id") == 0 and event.get("child_id") == 0
            ]

    def _run(self) -> None:
        hooks = None
        try:
            self.started = time.monotonic()
            hooks = self.api.event_hooks(self.event, self.callback_failed)
            hooks.start()
            self.hook_count = hooks.count
            while True:
                start = time.monotonic()
                hooks.drain()
                with self.lock:
                    self.coverage("hooks", start, time.monotonic())
                self.sample()
                if self.samples % 50 == 1:
                    start = time.monotonic()
                    self.api.require_desktop()
                    with self.lock:
                        self.coverage("desktop", start, time.monotonic())
                self.ready.set()
                if self.stop.wait(self.INTERVAL):
                    break
            self.sample()
            start = time.monotonic()
            hooks.drain()
            with self.lock:
                self.coverage("hooks", start, time.monotonic())
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
        finally:
            if hooks is not None:
                try:
                    hooks.close()
                    self.hooks_closed = True
                except Exception as error:
                    self.cleanup_error = f"{type(error).__name__}: {error}"
            with self.lock:
                self.ended = time.monotonic()
                for channel in ("foreground", "visible", "hooks"):
                    gap = self.ended - self.previous.get(channel, self.started)
                    self.max_gap = max(self.max_gap, gap)
                    if gap > self.MAX_GAP:
                        self.record("coverage-tail-gap", channel=channel, seconds=gap)
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        if not self.ready.wait(5) or not self.healthy() or self.ended is not None or self.hook_count != 2:
            self.__exit__()
            raise ObservationBlocked("desktop capture and both WinEvent hooks are not ready")
        with self.lock:
            invalid = any(event["violation"] for event in self.events)
        if invalid:
            self.__exit__()
            raise ObservationBlocked("desktop changed or was unobservable before epoch admission")
        return self

    def __exit__(self, *_args) -> None:
        self.stop.set()
        self.thread.join(5)
        if self.thread.is_alive():
            self.error = "desktop observer did not quiesce"
        else:
            self.sealed = True

    def packet(self) -> dict:
        with self.lock:
            violations = [deepcopy(event) for event in self.events if event["violation"]]
            return {
                "epoch": self.epoch, "samples": self.samples,
                "visible_enumeration_scope": "EnumWindows: top-level desktop-app windows",
                "foreground_capture": "independent visible HWND/PID/TID/process-birth endpoints",
                "started": self.started, "ended": self.ended,
                "poll_interval": self.INTERVAL, "maximum_gap": self.max_gap,
                "first_capture": dict(self.first_capture), "last_capture": dict(self.previous),
                "stage_maxima": dict(self.stage_maxima), "recent_captures": list(self.trace),
                "raw_events": deepcopy(self.events), "violations": violations,
                "event_limit": self.EVENT_LIMIT, "overflow": self.overflow,
                "hook_count": self.hook_count, "hooks_closed": self.hooks_closed,
                "error": self.error, "callback_error": self.callback_error,
                "cleanup_error": self.cleanup_error, "sealed": self.sealed,
                "passed": bool(self.sealed and not self.thread.is_alive() and self.healthy()
                               and self.hook_count == 2 and self.hooks_closed
                               and self.samples > 0 and not violations),
            }
