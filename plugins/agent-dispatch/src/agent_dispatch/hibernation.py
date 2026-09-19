"""Hibernate-the-wait -- hand a blocking wait to the layer so a worker costs
no running process while it waits.

A goal-loop worker often reaches a point where it can only wait on a slow
external condition: a review to be posted, a build to go green, a PR to become
mergeable. Sitting on a live agent session (and its token budget) through that
wait is the waste this substrate removes. Instead the worker **hands the wait
off**:

1. It kicks ``agent-dispatch run --detach --resume <its-own-worktree> -- <cmd>``,
   where ``<cmd>`` blocks until the awaited condition resolves.
2. The layer runs ``<cmd>`` in a **detached, cheap OS-level waiter** (no agent,
   no tokens) that outlives the worker.
3. The worker session is torn down -- it now costs nothing.
4. When ``<cmd>`` returns, the waiter **resumes the same worktree-affinitied
   worker** with a nudge via agent-bridge, and the worker wakes with its context
   intact and continues toward its goal.

This module is the pure core: a :class:`RunSpec` describing the wait + how to
resume, and :func:`run_and_resume`, an orchestration with **injected** runner and
resumer so it is fully testable without shelling out or reaching a live bridge.
The CLI (``agent-dispatch run``) wires the real subprocess runner and the
agent-bridge nudge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RunSpec:
    """A blocking wait handed to the layer, plus how to resume the worker.

    ``command`` is the blocking wait (a fixed argv). ``resume_worktree`` is the
    worker to wake when it resolves -- a worktree handle agent-bridge resolves to
    whichever session is live then (routing cross-machine through the mesh); when
    absent, the wait runs and nothing is resumed (a plain awaited step).
    ``task_id`` and ``message`` shape the resume nudge.
    """

    command: tuple[str, ...]
    resume_worktree: str | None = None
    task_id: str | None = None
    message: str | None = None
    sender: str = "agent-dispatch-hibernate"


def resume_message(spec: RunSpec, returncode: int) -> str:
    """The nudge text delivered to the resumed worker.

    An explicit ``spec.message`` wins; otherwise a default that states the awaited
    step's outcome and tells the worker to resume where it suspended.
    """
    if spec.message:
        return spec.message
    outcome = "finished" if returncode == 0 else f"exited with code {returncode}"
    what = f" for task {spec.task_id}" if spec.task_id else ""
    return (
        f"The awaited step{what} {outcome}. Resume where you suspended and continue "
        f"toward your goal."
    )


TIMED_OUT_CODE = 124
"""Shared "no real transition, the wait window merely elapsed" convention.

Blocking-wait commands intended for this substrate (``agent-worktrees
pr-watch wait`` and any other tool built to the same contract) exit ``124`` to
mean "I polled for the full window and nothing changed" -- distinct from ``0``
(a real transition fired), ``2`` (usage error), and ``3`` (provider/auth
error, e.g. a bad token). Only ``124`` is safe to treat as a no-op re-arm
signal; every other code must still wake the worker (a genuine transition, or
a real problem it needs to know about immediately).
"""


def run_and_resume(
    spec: RunSpec,
    *,
    runner: Callable[[tuple[str, ...]], int],
    resumer: Callable[[str, str], bool],
) -> dict:
    """Run the blocking wait, then resume the worktree-affinitied worker.

    ``runner`` executes ``spec.command`` and returns its exit code; ``resumer``
    delivers the resume nudge ``(worktree, message) -> bool``. Both are injected
    so the orchestration is testable. Returns a bounded report of what happened.
    ``resumed`` is ``None`` when no ``resume_worktree`` was given, else the
    resumer's success flag.

    A ``runner`` result of :data:`TIMED_OUT_CODE` (124) is **not** a resume
    trigger: it means the wait command itself polled the full window and found
    no real transition, so waking a torn-down, token-costing embodied worker
    to independently re-discover "nothing changed" would be pure waste -- and,
    observed in production, a source of redundant near-duplicate PR comments
    when a worker re-posted a status update on every such no-op wake
    (ThomasMichon/copilot-extensions#2576, gim-home/odsp-web-harness#458). On a
    124, re-invoke ``runner`` again in place (the wait command re-arms itself
    against the same baseline/cursor) rather than resuming; only a genuine
    transition (0) or an actual error (anything else) escalates to a resume.
    """
    returncode = runner(spec.command)
    reattempts = 0
    while returncode == TIMED_OUT_CODE:
        reattempts += 1
        returncode = runner(spec.command)
    message = resume_message(spec, returncode)
    resumed: bool | None = None
    if spec.resume_worktree:
        try:
            resumed = bool(resumer(spec.resume_worktree, message))
        except Exception:  # a failed resume is never fatal -- liveness recovery backstops
            resumed = False
    return {
        "command": list(spec.command),
        "returncode": returncode,
        "resume_worktree": spec.resume_worktree,
        "message": message,
        "resumed": resumed,
        "reattempts_on_timeout": reattempts,
    }


def detached_run_argv(
    spec: RunSpec, *, python: str, module: str = "agent_dispatch"
) -> list[str]:
    """Reconstruct the ``run`` argv for the **detached waiter** re-exec.

    Turns a :class:`RunSpec` back into ``<python> -m <module> run [flags] --
    <command>`` (deliberately **without** ``--detach``, since this *is* the
    detached copy). The ``--`` fences the wait command so its own flags are never
    parsed as ``run`` options.
    """
    argv = [python, "-m", module, "run"]
    if spec.resume_worktree:
        argv += ["--resume", spec.resume_worktree]
    if spec.task_id:
        argv += ["--task", spec.task_id]
    if spec.message:
        argv += ["--message", spec.message]
    argv.append("--")
    argv += list(spec.command)
    return argv
