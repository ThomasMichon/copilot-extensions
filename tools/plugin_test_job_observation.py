"""Opt-in numeric observations of an existing disposable CI Windows Job."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import time
from typing import TYPE_CHECKING, Literal, Protocol, TextIO


ENVIRONMENT = "COPILOT_EXTENSIONS_CI_JOB_OBSERVATION"
MAX_ROWS = 4096
WAIT_MIN_AGE_SECONDS = 8.0
Row = dict[str, int | bool | None]


class _JobAPI(Protocol):
    def members(self, job: int, capacity: int) -> list[int]: ...
    def open(self, pid: int) -> int: ...
    def inside(self, handle: int, job: int) -> bool: ...
    def times(self, handle: int) -> tuple[int, int, int, int]: ...
    def exit_code(self, handle: int) -> int | None: ...
    def close(self, handle: int) -> bool: ...


class _WaitAPI(Protocol):
    @property
    def pending(self) -> bool: ...
    def poll(
        self, target: tuple[int, int, int, int] | None, deadline: float
    ) -> Row | None: ...
    def finish(self) -> Row | None: ...


def _wait_api() -> _WaitAPI:
    if TYPE_CHECKING:
        from plugin_test_job_waits import WindowsWaitProbe
    elif __package__:
        from .plugin_test_job_waits import WindowsWaitProbe
    else:
        from plugin_test_job_waits import WindowsWaitProbe
    return WindowsWaitProbe()


def _api() -> _JobAPI:
    if TYPE_CHECKING:
        from plugin_test_job_native import WindowsJobAPI
    elif __package__:
        from .plugin_test_job_native import WindowsJobAPI
    else:
        from plugin_test_job_native import WindowsJobAPI
    return WindowsJobAPI()


@dataclass
class _Pin:
    handle: int
    birth: int
    first_seen: float
    wait_cursor: int = 0
    verified_live: bool = False


class JobObservation:
    """Borrow the owner's Job; retain only verified member process handles."""

    def __init__(self, job: int, max_processes: int) -> None:
        self.kernel = _api()
        self.job = job
        self.capacity = max_processes + 1
        self.started = time.monotonic()
        self.started_unix_ns = time.time_ns()
        self.pins: dict[int, _Pin] = {}
        self.work: deque[tuple[int, bool] | Literal["discover", "wait"]] = deque()
        self.summary_capacity = MAX_ROWS // 2
        self.timeline_capacity = MAX_ROWS - self.summary_capacity
        self.summaries: dict[tuple[int, int], Row] = {}
        self.summary_truncated = False
        self.summary_dropped_samples = 0
        self.rows: deque[tuple[Literal["PROCESS", "WAIT"], Row]] = deque(
            maxlen=self.timeline_capacity,
        )
        self.errors: set[str] = set()
        self.truncated = False
        self.discarded_rows = 0
        self.next_wait = 0
        self.wait_started_ms = 0
        self.wait_pending_polls = 0
        self.waits: _WaitAPI | None = None
        try:
            self.waits = _wait_api()
        except (OSError, AttributeError):
            self.errors.add("wait-api-unavailable")

    def _append(self, kind: Literal["PROCESS", "WAIT"], row: Row) -> None:
        if len(self.rows) >= self.timeline_capacity:
            self.truncated = True
            self.discarded_rows += 1
        self.rows.append((kind, row))

    def _retain_summary(self, pid: int, birth: int, row: Row) -> None:
        previous = self.summaries.get((pid, birth))
        if previous is None and len(self.summaries) >= self.summary_capacity:
            self.summary_truncated = self.truncated = True
            self.summary_dropped_samples += 1
            return
        summary = dict(row)
        if previous is None:
            summary.update(
                {
                    "first_at_ms": row["at_ms"],
                    "first_kernel_100ns": row["kernel_100ns"],
                    "first_user_100ns": row["user_100ns"],
                    "sample_count": 1,
                }
            )
        else:
            for name in ("first_at_ms", "first_kernel_100ns", "first_user_100ns"):
                summary[name] = previous[name]
            summary["sample_count"] = int(previous["sample_count"] or 0) + 1
        self.summaries[(pid, birth)] = summary

    def _append_wait(self, row: Row | None) -> None:
        if row is not None:
            row["sample_at_ms"] = self.wait_started_ms
            row["at_ms"] = round((time.monotonic() - self.started) * 1000)
            self._append("WAIT", row)

    def _sample_wait(self, deadline: float) -> None:
        now = time.monotonic()
        if self.waits is None or now >= deadline:
            return
        target = None
        if not self.waits.pending:
            candidates = [
                (pid, pin)
                for pid, pin in self.pins.items()
                if pin.verified_live and now - pin.first_seen >= WAIT_MIN_AGE_SECONDS
            ]
            if not candidates:
                return
            pid, pin = candidates[self.next_wait % len(candidates)]
            self.next_wait += 1
            target = pid, pin.handle, pin.birth, pin.wait_cursor
            pin.wait_cursor += 1
            self.wait_started_ms = round((now - self.started) * 1000)
        try:
            self._append_wait(self.waits.poll(target, deadline))
        except OSError:
            self.errors.add("wait-sample-unavailable")
        if self.waits.pending:
            self.wait_pending_polls += 1

    def _release(self, pid: int) -> None:
        if self.kernel.close(self.pins[pid].handle):
            del self.pins[pid]
        else:
            self.errors.add("handle-close-unconfirmed")

    def _sample(self, pid: int) -> None:
        pin = self.pins[pid]
        pin.verified_live = False
        if pin.birth <= 0:
            raise OSError("birth-unavailable")
        code = self.kernel.exit_code(pin.handle)
        birth, exit_time, kernel_time, user_time = self.kernel.times(pin.handle)
        if birth != pin.birth:
            raise OSError("birth-changed")
        pin.verified_live = code is None
        row: Row = {
            "pid": pid,
            "birth_100ns": birth,
            "at_ms": round((time.monotonic() - self.started) * 1000),
            "kernel_100ns": kernel_time,
            "user_100ns": user_time,
            "exited": code is not None,
            "exit_100ns": exit_time if code is not None else None,
            "exit_code": code,
        }
        self._retain_summary(pid, birth, row)
        self._append("PROCESS", row)
        if code is not None:
            self._release(pid)

    def _sample_member(self, pid: int, discovered: bool) -> None:
        if not discovered:
            if pid in self.pins:
                self._sample(pid)
            return
        if pid in self.pins:
            return
        if len(self.pins) >= self.capacity:
            self.errors.add("pin-budget-exhausted")
            return
        handle = self.kernel.open(pid)
        if not handle:
            self.errors.add("member-open-unavailable")
            return
        self.pins[pid] = _Pin(handle, 0, time.monotonic())
        try:
            if not self.kernel.inside(handle, self.job):
                self.errors.add("member-no-longer-in-job")
                self._release(pid)
                return
            self.pins[pid].birth = self.kernel.times(handle)[0]
            self._sample(pid)
        except OSError:
            self._release(pid)
            raise

    def sample(self, deadline: float) -> None:
        """Resume a counter/discovery/wait pass within the original poll budget."""
        if not self.work:
            self.work.extend((pid, False) for pid in self.pins)
            self.work.extend(("discover", "wait"))
        try:
            while self.work:
                if time.monotonic() >= deadline:
                    self.errors.add("poll-budget-exhausted")
                    return
                work = self.work.popleft()
                if isinstance(work, tuple):
                    self._sample_member(*work)
                elif work == "discover":
                    members = self.kernel.members(self.job, self.capacity)
                    self.work.extendleft(
                        (pid, True) for pid in reversed(members) if pid not in self.pins
                    )
                else:
                    self._sample_wait(deadline)
                    return
        except OSError:
            self.errors.add("sample-unavailable")

    def finish(self, output: TextIO) -> None:
        """Read retained handles after owner teardown; never reopen a closed Job."""
        try:
            for pid in tuple(self.pins):
                try:
                    self._sample(pid)
                except OSError:
                    self.errors.add("final-sample-unavailable")
        finally:
            if self.waits is not None:
                try:
                    self._append_wait(self.waits.finish())
                except OSError:
                    self.errors.add("wait-close-unconfirmed")
            for pid in tuple(self.pins):
                self._release(pid)
            for kind, row in self.rows:
                print(
                    f"[CI-JOB-{kind}] " + json.dumps(row, sort_keys=True), file=output
                )
            for row in self.summaries.values():
                print(
                    "[CI-JOB-PROCESS-SUMMARY] " + json.dumps(row, sort_keys=True),
                    file=output,
                )
            print(
                "[CI-JOB-OBSERVATION] "
                + json.dumps(
                    {
                        "schema": 3,
                        "diagnostic_only": True,
                        "rows": len(self.rows) + len(self.summaries),
                        "process_rows": sum(kind == "PROCESS" for kind, _ in self.rows),
                        "wait_rows": sum(kind == "WAIT" for kind, _ in self.rows),
                        "summary_rows": len(self.summaries),
                        "summary_capacity": self.summary_capacity,
                        "summary_truncated": self.summary_truncated,
                        "summary_dropped_samples": self.summary_dropped_samples,
                        "timeline_capacity": self.timeline_capacity,
                        "origin_monotonic_seconds": self.started,
                        "origin_unix_ns": self.started_unix_ns,
                        "discarded_rows": self.discarded_rows,
                        "truncated": self.truncated,
                        "errors": sorted(self.errors),
                        "unclosed_handles": len(self.pins),
                        "unclosed_wait_requests": int(
                            self.waits is not None and self.waits.pending
                        ),
                        "wait_pending_polls": self.wait_pending_polls,
                        "wait_min_age_ms": round(WAIT_MIN_AGE_SECONDS * 1000),
                        "coverage": "sampled-job-members-only; short-lived or breakaway children may be absent",
                    },
                    sort_keys=True,
                ),
                file=output,
            )
        if self.pins or (self.waits is not None and self.waits.pending):
            raise OSError("CI Job observation handle closure remains unconfirmed")
