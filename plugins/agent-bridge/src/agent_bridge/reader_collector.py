"""Provider-local collector costs, independent of reader CPU and ownership."""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from agent_procutil.metrics import SampleAccounting

log = logging.getLogger("agent-bridge")
_MAX_COUNT = (1 << 63) - 1
_MAX_SEQUENCE = (1 << 63) - 1


@dataclass(frozen=True)
class CollectorAccounting:
    epoch: str
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    wall_seconds: float | None = None
    wall_known: int = 0
    wall_unknown: int = 0
    thread_cpu_seconds: float | None = None
    thread_cpu_known: int = 0
    thread_cpu_unknown: int = 0
    issues: tuple[str, ...] = ()
    coverage: str = "observed_lower_bound"

    def fields(self) -> dict[str, object]:
        return asdict(self)


def _add_cost(
    total: float | None, value: float | None, label: str,
) -> tuple[float | None, bool, str | None]:
    if value is None:
        return total, False, None
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        log.warning("Collector %s contribution is invalid", label)
        return total, False, f"{label}_invalid"
    updated = (total if total is not None else 0.0) + value
    if not math.isfinite(updated):
        log.warning("Collector %s total overflowed", label)
        return total, False, f"{label}_overflow"
    return updated, True, None


class CollectorLedger:
    """Retain one immutable total, not a history of samplers or events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = CollectorAccounting(uuid4().hex)

    @property
    def snapshot(self) -> CollectorAccounting:
        with self._lock:
            return self._snapshot

    def source(self) -> CollectorSource:
        return CollectorSource(self)


class CollectorSource:
    """A cursor pinned to one sampler by its existing completion callbacks."""

    def __init__(self, ledger: CollectorLedger) -> None:
        self._ledger = ledger
        self._last: tuple[str, int] | None = None
        self._previous: tuple[str, int] | None = None

    def complete(self, sample: SampleAccounting) -> CollectorAccounting | None:
        if sample.state == "pending":
            return None
        ledger = self._ledger
        with ledger._lock:
            current = ledger._snapshot
            issues = set(current.issues)
            if (
                sample.state not in ("succeeded", "failed")
                or re.fullmatch(r"[0-9a-f]{32}", sample.instance) is None
                or isinstance(sample.sequence, bool)
                or not 1 <= sample.sequence <= _MAX_SEQUENCE
            ):
                log.warning("Collector completion has invalid provenance")
                ledger._snapshot = replace(
                    current, issues=tuple(sorted(issues | {"sample_provenance_invalid"})),
                )
                return None
            last = self._last
            if (
                self._previous is not None and sample.instance == self._previous[0]
                and sample.sequence <= self._previous[1]
            ):
                return None
            if last is not None and sample.instance == last[0]:
                if sample.sequence <= last[1]:
                    return None
                continuous = sample.sequence == last[1] + 1
            else:
                continuous = sample.sequence == 1 and (
                    last is None or last[1] == _MAX_SEQUENCE
                )
                if last is not None:
                    if not continuous:
                        log.warning("Collector sampler identity changed without continuity")
                        ledger._snapshot = replace(
                            current, issues=tuple(sorted(issues | {"sample_continuity_lost"})),
                        )
                        return None
                    self._previous = last
            if not continuous:
                log.warning("Collector sample continuity is incomplete")
                issues.add("sample_continuity_lost")
            self._last = (sample.instance, sample.sequence)
            if current.completed == _MAX_COUNT:
                log.warning("Collector attempt count overflowed")
                ledger._snapshot = replace(
                    current, issues=tuple(sorted(issues | {"counter_overflow"})),
                )
                return ledger._snapshot
            wall, wall_known, wall_issue = _add_cost(
                current.wall_seconds, sample.wall_seconds, "wall",
            )
            cpu, cpu_known, cpu_issue = _add_cost(
                current.thread_cpu_seconds, sample.thread_cpu_seconds, "thread_cpu",
            )
            issues.update(issue for issue in (wall_issue, cpu_issue) if issue is not None)
            ledger._snapshot = CollectorAccounting(
                epoch=current.epoch,
                completed=current.completed + 1,
                succeeded=current.succeeded + (sample.state == "succeeded"),
                failed=current.failed + (sample.state == "failed"),
                wall_seconds=wall,
                wall_known=current.wall_known + wall_known,
                wall_unknown=current.wall_unknown + (not wall_known),
                thread_cpu_seconds=cpu,
                thread_cpu_known=current.thread_cpu_known + cpu_known,
                thread_cpu_unknown=current.thread_cpu_unknown + (not cpu_known),
                issues=tuple(sorted(issues)),
            )
            return ledger._snapshot
