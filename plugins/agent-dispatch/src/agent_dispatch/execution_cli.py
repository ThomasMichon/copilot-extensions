"""Resolve/run/evaluate/charter command bodies extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .client import DispatchError
from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def _run_resolution_step(step: Any, *, cwd: str | None = None) -> dict:
    """Execute one non-advisory :class:`ResolutionStep` in the caller's worktree.

    Runs the step's fixed ``argv`` (git only) and returns a bounded result. An
    advisory step is never run here -- the caller reports it as an instruction.
    """
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed git argv from a ResolutionStep
            list(step.argv), cwd=cwd, check=False, capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"kind": step.kind, "ran": True, "ok": False, "error": str(exc)}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return {
        "kind": step.kind,
        "ran": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": out[:2000],
        "error": err[:2000] or None,
    }

def _cmd_resolve(args: argparse.Namespace) -> int:
    """Drive THIS worktree to a clean, resolved final state (the enforced
    *drive-the-worktree-to-resolution* invariant). Plans by default; ``--execute``
    performs the (destructive) unwind on the caller's own workspace."""
    from .resolution import ResolutionError, plan_resolution

    try:
        plan = plan_resolution(
            args.outcome, base=args.base, source_ref=args.source, reason=args.reason
        )
    except ResolutionError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    if not args.execute:
        payload = plan.to_dict()
        payload["executed"] = False
        payload["note"] = (
            "plan only -- re-run with --execute to perform the unwind "
            "(destructive steps discard working-tree state)"
        )
        return _core()._emit(payload)

    results: list[dict] = []
    instructions: list[str] = []
    failed = False
    for step in plan.steps:
        if step.advisory:
            instructions.append(step.description)
            results.append({"kind": step.kind, "ran": False, "advisory": True})
            continue
        res = _core()._run_resolution_step(step)
        results.append(res)
        if not res["ok"]:
            failed = True
            # A failed destructive unwind must not be papered over -- stop so the
            # worker/operator can look, rather than pressing on into a dirtier
            # state.
            if step.destructive:
                break

    payload = plan.to_dict()
    payload.update({"executed": True, "results": results, "instructions": instructions})
    _core()._emit(payload)
    return 1 if failed else 0

def _spawn_detached_waiter(spec: Any) -> dict:
    """Re-exec ``agent-dispatch run`` (without ``--detach``) as a fully detached
    waiter that outlives this process, so the kicking worker can be torn down
    while a cheap OS-level process owns the wait and fires the resume."""
    from . import hibernation
    from .procutil import detached_kwargs, windowless_python, windowless_python_env

    python = sys.executable
    argv = hibernation.detached_run_argv(spec, python=windowless_python(python))
    env = dict(os.environ)
    env.update(windowless_python_env(python))
    proc = subprocess.Popen(  # noqa: S603 -- fixed argv (interpreter + our own module)
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached_kwargs(),
    )
    return {"pid": proc.pid, "argv": argv}

def _suspend_for_detached_wait(args: argparse.Namespace, spec: Any) -> dict | None:
    """Atomically suspend ``spec.task_id`` once its detached waiter is live, and
    journal a ``task``-kind agent-worktrees claim on the current worktree so
    it cannot be finalized out from under the still-open task (Boundary I /
    ThomasMichon/copilot-extensions#2584).

    Closes the gap where ``run --detach`` handed a wait off to a cheap
    detached process, but the caller's own ``agent-dispatch suspend`` call --
    a second, separate step the task prompt merely *asks* workers to
    remember -- was skipped or never reached before the session tore down.
    That left tasks stuck ``started`` (implying a live agent is actively
    working) for as long as the external wait ran, sometimes indefinitely
    once the detached waiter itself died with nothing to notice. Separately,
    without the claim, a worktree-lifecycle sweep that only checks for a
    *live session* (correctly absent here -- that's the whole point of
    hibernation) could still reclaim the worktree the suspended task expects
    to resume in.

    Returns ``None`` when no ``--task`` was given (a plain untracked wait --
    nothing to suspend or claim). Never raises: neither the suspend nor the
    claim is allowed to fail the overall detach (the wait is already safely
    handed off by the time this runs), so any error is folded into the
    returned dict for the caller to see rather than propagated.
    """
    if not spec.task_id:
        return None
    from . import hibernation_claims

    reason = f"hibernating: {' '.join(spec.command)}"
    claim = hibernation_claims.add_hibernation_claim(spec.task_id, note=reason)
    # Resolve the owner FROM THE TASK ITSELF, not from CWD/machine-worktree
    # identity: a headless-embodied worker's actual claim identity is a
    # `headless-<hash>` worker id, never `machine/worktree`, so composing from
    # CWD here always 409'd for headless workers ("owned by 'headless-xxx',
    # not 'machine/worktree'") -- leaving the task `started` (never actually
    # suspended) for the task's entire wait and continuing to occupy its
    # pool's concurrency slot the whole time (ThomasMichon/copilot-extensions
    # #2576's remaining scope; gim-home/odsp-web-harness#458 comment thread).
    # The task's own `owner` field is always correct for whichever kind of
    # worker actually holds it, so prefer that and fall back to CWD-derived
    # identity only if the lookup itself fails.
    worker_id = None
    try:
        with _core()._client(args) as c:
            worker_id = c.get(spec.task_id).get("owner")
    except Exception:  # noqa: BLE001 -- owner lookup is best-effort, never fatal here
        worker_id = None
    if not worker_id:
        worker_id = _core()._resolve_owner(args, verb="run --detach")
    if worker_id is None:
        return {"error": "could not resolve the owning worker for suspend", "claim": claim}
    try:
        with _core()._client(args) as c:
            task = c.suspend(spec.task_id, worker_id, reason=reason)
    except DispatchError as exc:
        return {"error": str(exc), "worker_id": worker_id, "claim": claim}
    return {"status": task.get("status"), "worker_id": worker_id, "claim": claim}

def _cmd_run(args: argparse.Namespace) -> int:
    """Hand a blocking wait to the layer (*hibernate-the-wait*): run ``-- <cmd>``
    to completion, then resume the worktree-affinitied worker via agent-bridge.
    With ``--detach`` the wait runs in a detached process so the worker can be
    torn down (costing nothing) while it waits. When ``--task`` is also given,
    a successful detach atomically suspends that task (see
    :func:`_suspend_for_detached_wait`) so ``started`` never outlives the
    session that was actually doing the work -- closing the gap where a
    worker hibernates but forgets (or never gets to) call ``suspend``
    separately before its session tears down."""
    from . import bridge
    from .hibernation import RunSpec, run_and_resume

    command = getattr(args, "_dashdash_tail", None)
    if command is None:
        command = list(args.command or [])
        if command and command[0] == "--":
            command = command[1:]
    if not command:
        print(
            "agent-dispatch: run needs a command after '--', e.g. "
            "`agent-dispatch run --resume <worktree> -- <blocking-cmd>`",
            file=sys.stderr,
        )
        return 2

    spec = RunSpec(
        command=tuple(command),
        resume_worktree=args.resume,
        task_id=args.task,
        message=args.message,
    )

    if args.detach:
        handle = _core()._spawn_detached_waiter(spec)
        suspended = _core()._suspend_for_detached_wait(args, spec)
        return _core()._emit(
            {
                "detached": True,
                "resume_worktree": spec.resume_worktree,
                "command": list(spec.command),
                "suspended": suspended,
                **handle,
            }
        )

    def runner(cmd: tuple[str, ...]) -> int:
        try:
            proc = subprocess.run(list(cmd), check=False)  # noqa: S603 -- operator-supplied wait
            return proc.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"agent-dispatch: run: could not execute the wait: {exc}", file=sys.stderr)
            return 127

    report = run_and_resume(spec, runner=runner, resumer=bridge.send_nudge)
    if spec.task_id:
        # This is the foreground path: for a detached wait, it's the re-exec'd
        # child running here (see detached_run_argv), reached exactly once the
        # wait resolves -- the right moment to retire the claim
        # _suspend_for_detached_wait journaled, regardless of the wait's
        # outcome or whether the resume nudge itself succeeded.
        from . import hibernation_claims

        report["claim_released"] = hibernation_claims.release_hibernation_claim(spec.task_id)
    return _core()._emit(report)

def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Feed one task **lifecycle event** through a declarative evaluator and apply
    its decisions (the *evaluator* half of emitters-and-evaluators). The event
    JSON is read from ``--event-file`` or stdin; the coordinator shape is
    ``{"type": "task.completed", "task": {...}}``."""
    from .producers.evaluator import EvaluatorError, SpecEvaluator, evaluate_and_apply

    try:
        spec = json.loads(Path(args.spec).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"agent-dispatch: cannot read evaluator spec: {exc}", file=sys.stderr)
        return 2
    raw = (
        Path(args.event_file).expanduser().read_text(encoding="utf-8")
        if args.event_file
        else sys.stdin.read()
    )
    try:
        event = json.loads(raw)
    except ValueError as exc:
        print(f"agent-dispatch: event is not valid JSON: {exc}", file=sys.stderr)
        return 2
    try:
        evaluator = SpecEvaluator(spec)
    except EvaluatorError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        report = evaluate_and_apply(
            evaluator, event, creator=lambda *a, **k: {}, repo=args.repo, apply=False
        )
        return _core()._emit(report)

    with _core()._client(args) as c:
        try:
            report = evaluate_and_apply(
                evaluator, event, creator=c.create, repo=args.repo, apply=True
            )
        except EvaluatorError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 2
    return _core()._emit(report)

def _cmd_charter_show(args: argparse.Namespace) -> int:
    from .worker_charter import charter_text

    try:
        text = charter_text(args.name)
    except KeyError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 0
