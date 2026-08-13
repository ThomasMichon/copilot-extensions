"""Compose and execute a launch plan -- mux by capability (Phase 6b, DQ9).

The Manager's Picker resumes/launches a worktree by fetching a launch plan from
the engine (:func:`engine_client.resolve_launch_plan`, a pure process-boundary
read) and then *acting* on it here. This module is that executor -- and the one
place the **DQ9 mux-by-capability** rule lives:

- **The Worktree Manager owns mux.** ``agent-worktrees`` / ``agent-bridge`` only
  *detect-and-fall-back*; inside the Manager, multiplexing is a capability we add
  when a Manager-owned backend is present. The engine's ``resolve --json`` always
  sets ``no_mux`` (it must never spawn a mux in machine-readable mode), so the
  Manager's decision to mux is **its own**, gated on capability + intent -- not on
  the plan's ``no_mux`` flag.
- **Graceful degrade.** No Manager-owned mux backend has been relocated yet (DQ9
  deliberately does *not* force that split), so the default capability is
  *unavailable* and every launch runs **non-muxed / directly** -- which is exactly
  the current behavior. A real backend slots into :func:`set_mux_capability`
  without touching the :func:`compose_launch` / :func:`execute` seam.

Everything is kept a **pure** compose (:func:`compose_launch`) separate from the
side-effecting :func:`execute`, so the composition -- including whether a launch
would be muxed -- is unit-testable with a fake capability and never spawns a real
Copilot.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .engine_client import LaunchPlan

#: Force/override the mux probe. ``off`` forces non-muxed regardless of backend.
MUX_ENV = "WORKTREE_MANAGER_MUX"

#: How a mux backend wraps a launch: ``(argv, session, work_dir) -> wrapped argv``.
MuxWrap = Callable[[Sequence[str], str, "str | None"], "list[str]"]


@dataclass(frozen=True)
class MuxCapability:
    """A Manager-owned multiplexer capability (present or not) + how it wraps.

    ``available`` is the detect-and-fall-back signal DQ9 turns on: when False the
    launcher runs the plan directly (single session, no mux). ``wrap`` turns a raw
    Copilot ``argv`` into a mux-managed launch of session ``wt-<id>`` -- supplied by
    a real backend when one lands.
    """

    name: str = "none"
    available: bool = False
    wrap: MuxWrap | None = None


_NO_MUX = MuxCapability()
_capability: MuxCapability = _NO_MUX


def set_mux_capability(cap: MuxCapability | None) -> None:
    """Install the active Manager-owned mux capability (or reset to none)."""
    global _capability
    _capability = cap or _NO_MUX


def mux_capability() -> MuxCapability:
    """The active mux capability. ``WORKTREE_MANAGER_MUX=off`` forces non-muxed."""
    if os.environ.get(MUX_ENV, "").strip().lower() == "off":
        return _NO_MUX
    return _capability


@dataclass(frozen=True)
class LaunchExec:
    """A fully-composed launch: what to run, where, and whether it is muxed.

    ``kind`` is the plan's action: ``exec`` (run ``argv``), ``none`` (nothing to
    run -- just return ``exit_code``), or any other engine action passed through
    for the caller to handle. Produced by :func:`compose_launch`; run by
    :func:`execute`.
    """

    kind: str
    argv: list[str]
    cwd: str | None
    env: dict
    muxed: bool
    exit_code: int
    session: str | None


def compose_launch(
    plan: LaunchPlan,
    capability: MuxCapability | None = None,
    *,
    want_mux: bool = True,
) -> LaunchExec:
    """Turn a launch plan into a concrete, runnable :class:`LaunchExec` (pure).

    Muxing happens iff a Manager-owned mux backend is ``available`` **and** the
    caller wants it (``want_mux``) -- never gated on ``plan.no_mux`` (that reflects
    the *engine's* mux, always suppressed by ``--json``; the Manager owns mux, DQ9).
    A non-``exec`` plan (e.g. ``none``) composes to a no-op carrying its exit code.
    """
    cap = capability if capability is not None else mux_capability()
    session = f"wt-{plan.worktree_id}" if plan.worktree_id else "wt-base"

    if plan.action != "exec":
        return LaunchExec(
            kind=plan.action, argv=[], cwd=None, env={},
            muxed=False, exit_code=plan.exit_code, session=None)

    argv = list(plan.cmd)
    muxed = False
    if cap.available and cap.wrap is not None and want_mux and argv:
        argv = list(cap.wrap(argv, session, plan.work_dir))
        muxed = True

    env = {**os.environ, **plan.env}
    return LaunchExec(
        kind="exec", argv=argv, cwd=plan.work_dir or None, env=env,
        muxed=muxed, exit_code=plan.exit_code, session=session)


def execute(le: LaunchExec) -> int:
    """Run a composed launch and return its exit code.

    ``none`` returns the carried ``exit_code`` without running anything; ``exec``
    runs ``argv`` in ``cwd`` with the merged env and returns Copilot's exit code
    (``post_exit`` semantics). Any other action returns non-zero -- the Manager
    does not know how to execute engine actions like ``handoff``/``wsl`` yet.
    """
    if le.kind == "none":
        return le.exit_code
    if le.kind != "exec" or not le.argv:
        return 1
    proc = subprocess.run(le.argv, cwd=le.cwd, env=le.env)
    return proc.returncode


def launch(
    plan: LaunchPlan,
    capability: MuxCapability | None = None,
    *,
    want_mux: bool = True,
) -> int:
    """Compose then execute a launch plan (the Picker's launch/resume action)."""
    return execute(compose_launch(plan, capability, want_mux=want_mux))
