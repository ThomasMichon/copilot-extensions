"""The **monitor** vocabulary: a suspend's companion resolution handler.

Just as an *evaluator* is the emitter's companion handler for a task's
*lifecycle* (see the parent agent-dispatch vision's §Concepts/*The
evaluator*), a **monitor** is a **suspend's** companion handler for a
*wait*: it names the specific, checkable condition that resolves the wait,
drawn from a small, closed vocabulary of resolvable conditions rather than
an open-ended "suspend and something will surely follow up."

This module declares that vocabulary and its pure resolution logic only. It
deliberately has **no** dependency on :mod:`agent_dispatch.queue` or any of
its mixins, and performs no I/O: every function here is a pure computation
over plain data (a monitor plus a "now" timestamp), the same
declare-before-wire posture :mod:`agent_dispatch.task_state_machine` takes
for the lifecycle table. Wiring a monitor into an actual suspend/wake call
path is a later phase's job (see
``efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md``).

Exactly one monitor kind exists today: **cooldown** — a plain bounded timer
that resolves once elapsed, with no other condition to check. This is
deliberately the **default**: a worker that suspends without naming a more
specific externality still gets a real, checkable, eventually-resolving
monitor rather than an unwatched idle. It is what turns a bare "yield the
lane" into a genuine round-robin time-slice rather than a task that can get
stuck forever — see the vision's *suspension-requires-a-monitor* behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MonitorKind(Enum):
    """The closed vocabulary of resolvable wait conditions.

    Deliberately small and closed, the same way :mod:`recipes` names a
    small, closed set of loop archetypes rather than an open-ended plugin
    surface — a monitor is only ever "suspend on *this specific, checkable
    thing*," never a free-text description nothing actually watches.
    """

    #: The default: a plain bounded timer with no other condition. Resolves
    #: once ``not_before`` has elapsed, full stop.
    COOLDOWN = "cooldown"


#: The default cooldown duration applied when a worker suspends without
#: naming a more specific monitor -- long enough that a genuinely busy
#: worker isn't nudged needlessly often, short enough that a task can never
#: sit unexamined for long. Deliberately a module-level constant (not a
#: config knob yet) until real usage data suggests otherwise.
DEFAULT_COOLDOWN_SECONDS: float = 600.0


@dataclass(frozen=True)
class CooldownMonitor:
    """A monitor that resolves once a plain deadline has elapsed."""

    kind: MonitorKind
    not_before: float

    def __post_init__(self) -> None:
        if self.kind is not MonitorKind.COOLDOWN:
            raise ValueError(f"CooldownMonitor requires kind=COOLDOWN, got {self.kind!r}")


#: The union of every monitor record type this module declares. A second
#: monitor kind (per the parent vision's Non-Goals: this vision does not
#: pin the closed set of monitor kinds a deployment ships) extends this
#: union and :func:`is_resolved`'s dispatch below -- it never needs a
#: parallel resolution mechanism.
Monitor = CooldownMonitor


def default_monitor(*, now: float, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS) -> Monitor:
    """The monitor a bare, unmonitored suspend defaults to.

    ``cooldown_seconds`` must be non-negative; a zero cooldown resolves
    immediately (the caller's next liveness/reconcile pass sees it due right
    away), which is a legitimate, if unusual, choice -- never rejected here.
    """
    if cooldown_seconds < 0:
        raise ValueError(f"cooldown_seconds must be >= 0, got {cooldown_seconds!r}")
    return CooldownMonitor(kind=MonitorKind.COOLDOWN, not_before=now + cooldown_seconds)


def is_resolved(monitor: Monitor, *, now: float) -> bool:
    """Whether ``monitor``'s wait condition has resolved as of ``now``.

    The single dispatch point every monitor kind's resolution logic goes
    through -- a future second monitor kind adds a branch here, not a
    parallel "check if resolved" entry point elsewhere.
    """
    if isinstance(monitor, CooldownMonitor):
        return now >= monitor.not_before
    raise TypeError(f"unknown monitor type: {type(monitor)!r}")  # pragma: no cover
