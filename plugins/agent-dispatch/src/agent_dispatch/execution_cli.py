"""Resolve/run/evaluate/charter command bodies extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


_DETACHED_CLI_ARGS: argparse.Namespace | None = None


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
    argv = hibernation.detached_run_argv(
        spec,
        python=windowless_python(python),
    )
    env = dict(os.environ)
    cli_args = _DETACHED_CLI_ARGS
    if cli_args is not None:
        if getattr(cli_args, "shared", False):
            shared_url = os.environ.get("AGENT_DISPATCH_SHARED_URL")
            if shared_url:
                env["AGENT_DISPATCH_URL"] = shared_url
            if os.environ.get("AGENT_DISPATCH_SHARED_TOKEN"):
                env["AGENT_DISPATCH_TOKEN"] = os.environ["AGENT_DISPATCH_SHARED_TOKEN"]
            if os.environ.get("AGENT_DISPATCH_SHARED_CONTROL_TOKEN"):
                env["AGENT_DISPATCH_CONTROL_TOKEN"] = os.environ[
                    "AGENT_DISPATCH_SHARED_CONTROL_TOKEN"
                ]
        elif getattr(cli_args, "url", None):
            env["AGENT_DISPATCH_URL"] = str(cli_args.url)
        if getattr(cli_args, "token", None):
            env["AGENT_DISPATCH_TOKEN"] = str(cli_args.token)
        if getattr(cli_args, "control_token", None):
            env["AGENT_DISPATCH_CONTROL_TOKEN"] = str(cli_args.control_token)
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


def _register_run_waiter(args: argparse.Namespace, spec: Any, handle: dict, suspended: dict | None) -> dict | None:
    """Compatibility wrapper retained for re-export/tests.

    Detached waiter registration is now prepared atomically via the coordinator
    before the child process is spawned, so this helper no longer performs any
    live registration work.
    """
    _ = (args, spec, handle, suspended)
    return None


def _rollback_detached_wait(
    args: argparse.Namespace,
    spec: Any,
    suspended: dict | None,
) -> dict | None:
    if not spec.task_id or not suspended or "status" not in suspended:
        return None
    worker_id = suspended.get("worker_id")
    if not worker_id:
        return None
    from . import hibernation_claims

    cancelled = None
    try:
        with _core()._client(args) as c:
            waiter_generation = suspended.get("waiter_generation")
            if waiter_generation is not None:
                cancelled = c.abort_run_waiter(
                    spec.task_id,
                    generation=int(waiter_generation),
                    message="Detached waiter spawn failed before the child armed.",
                    wake=False,
                )
            resumed = c.resume(
                spec.task_id,
                worker_id,
                wake=False,
                reuse_session=True,
                expected_generation=suspended.get("generation"),
                expected_owner_session_id=suspended.get("owner_session_id"),
            )
    except Exception as exc:  # noqa: BLE001 -- degraded rollback
        return {"error": str(exc)}
    claim_key = suspended.get("claim_key")
    claim = (
        hibernation_claims.release_hibernation_claim(str(claim_key))
        if claim_key
        else None
    )
    return {
        "status": resumed.get("status"),
        "claim_released": claim,
        "waiter_cancelled": cancelled,
    }


def _suspend_for_detached_wait(args: argparse.Namespace, spec: Any) -> dict | None:
    """Compatibility wrapper retained for re-export/tests.

    The atomic detached-waiter prepare path now owns suspension + waiter
    preparation together, so this legacy helper is no longer used by `_cmd_run`.
    """
    _ = (args, spec)
    return None

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
        waiter_generation=args.waiter_generation,
    )

    if args.detach:
        suspended = None
        waiter = None
        rollback = None
        prepared = None
        if spec.task_id:
            from . import hibernation_claims

            reason = f"hibernating: {' '.join(spec.command)}"
            worker_id = None
            try:
                with _core()._client(args) as c:
                    worker_id = c.get(spec.task_id).get("owner")
            except Exception:  # noqa: BLE001
                worker_id = None
            if not worker_id:
                worker_id = _core()._resolve_owner(args, verb="run --detach")
            if worker_id is None:
                return _core()._emit(
                    {
                        "detached": False,
                        "error": "could not resolve the owning worker for suspend",
                        "claim": None,
                    }
                )
            try:
                with _core()._client(args) as c:
                    prepared = c.prepare_run_waiter(
                        spec.task_id,
                        worker_id=worker_id,
                        reason=reason,
                        resume_worktree=spec.resume_worktree or "",
                        command=list(spec.command),
                    )
            except Exception as exc:  # noqa: BLE001
                rollback = (
                    _rollback_detached_wait(args, spec, suspended)
                    if suspended and "status" in suspended
                    else {"error": str(exc), "claim_released": None}
                )
            else:
                suspended = {
                    "status": "suspended",
                    "worker_id": worker_id,
                    "generation": prepared.get("task_generation"),
                    "waiter_generation": prepared.get("generation"),
                    "owner_session_id": prepared.get("owner_session_id"),
                }
                claim_key = hibernation_claims.waiter_claim_key(
                    spec.task_id,
                    int(prepared["generation"]),
                )
                claim = hibernation_claims.add_hibernation_claim(claim_key, note=reason)
                suspended["claim"] = claim
                suspended["claim_key"] = claim_key
                spec = dataclasses.replace(
                    spec,
                    waiter_child=True,
                    waiter_generation=int(prepared["generation"]),
                )
                waiter = prepared
        if spec.task_id and prepared is None:
            return _core()._emit(
                {
                    "detached": False,
                    "resume_worktree": spec.resume_worktree,
                    "command": list(spec.command),
                    "suspended": suspended,
                    "waiter": waiter,
                    "rollback": rollback,
                    "error": "could not prepare the detached waiter transactionally",
                }
            )
        try:
            globals()["_DETACHED_CLI_ARGS"] = args
            handle = _core()._spawn_detached_waiter(spec)
        except Exception as exc:  # noqa: BLE001
            if suspended and suspended.get("claim") is not None:
                rollback = _rollback_detached_wait(args, spec, suspended)
            else:
                rollback = {"error": str(exc)}
            return _core()._emit(
                {
                    "detached": False,
                    "resume_worktree": spec.resume_worktree,
                    "command": list(spec.command),
                    "suspended": suspended,
                    "waiter": waiter,
                    "rollback": rollback,
                    "error": f"could not spawn detached waiter: {exc}",
                }
            )
        finally:
            globals()["_DETACHED_CLI_ARGS"] = None
        return _core()._emit(
            {
                "detached": True,
                "resume_worktree": spec.resume_worktree,
                "command": list(spec.command),
                "suspended": suspended,
                "waiter": waiter,
                "rollback": rollback,
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
    if getattr(args, "waiter_child", False) and spec.task_id:
        from . import companion, remote_dispatch
        waiter_generation = spec.waiter_generation
        if waiter_generation is None:
            return _core()._emit(
                {
                    "command": list(spec.command),
                    "returncode": 125,
                    "resume_worktree": spec.resume_worktree,
                    "message": "Detached waiter has no registered generation fence.",
                    "resumed": None,
                    "waiter": {"accepted": False, "waiter": None},
                    "claim_released": None,
                }
            )
        host = remote_dispatch.local_machine()
        start_token = companion.process_start_token(os.getpid())
        if not host or not start_token:
            aborted = None
            try:
                with _core()._client(args) as c:
                    aborted = c.abort_run_waiter(
                        spec.task_id,
                        generation=waiter_generation,
                        message=(
                            "The detached wait could not establish its process identity"
                            " after spawning. Re-check the external state and decide"
                            " whether to run the wait again."
                        ),
                    )
            except Exception:  # noqa: BLE001 -- degraded report only
                aborted = None
            report = {
                "command": list(spec.command),
                "returncode": 125,
                "resume_worktree": spec.resume_worktree,
                "message": "Detached waiter could not resolve its process identity.",
            }
            report["waiter"] = aborted or {"accepted": False, "waiter": None}
            report["resumed"] = None
            report["claim_released"] = None
            return _core()._emit(report)
        with _core()._client(args) as c:
            armed = c.arm_run_waiter(
                spec.task_id,
                generation=waiter_generation,
                pid=os.getpid(),
                host=host,
                start_token=start_token,
            )
        if not armed.get("accepted"):
            aborted = None
            try:
                with _core()._client(args) as c:
                    aborted = c.abort_run_waiter(
                        spec.task_id,
                        generation=waiter_generation,
                        message=(
                            "The detached wait could not arm its coordinator-side"
                            " registration. Re-check the external state and decide"
                            " whether to run the wait again."
                        ),
                    )
            except Exception:  # noqa: BLE001 -- degraded report only
                aborted = None
            return _core()._emit(
                {
                    "command": list(spec.command),
                    "returncode": 125,
                    "resume_worktree": spec.resume_worktree,
                    "message": "Detached waiter could not arm its coordinator-side registration.",
                    "resumed": None,
                    "waiter": aborted or armed,
                    "claim_released": None,
                }
            )
        report = run_and_resume(
            dataclasses.replace(spec, resume_worktree=None),
            runner=runner,
            resumer=lambda *_a: True,
        )
        report["resume_worktree"] = spec.resume_worktree
        with _core()._client(args) as c:
            waiter = c.finish_run_waiter(
                spec.task_id,
                generation=waiter_generation,
                pid=os.getpid(),
                host=host,
                start_token=start_token,
                message=str(report["message"]),
            )
        report["waiter"] = waiter
        report["resumed"] = None
        report["claim_released"] = None
        return _core()._emit(report)

    report = run_and_resume(spec, runner=runner, resumer=bridge.send_nudge)
    if spec.task_id:
        report["claim_released"] = None
    return _core()._emit(report)

def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Feed one task **lifecycle event** through a declarative evaluator and apply
    its decisions (the *evaluator* half of emitters-and-evaluators). The event
    JSON is read from ``--event-file`` or stdin; the coordinator shape is
    ``{"type": "task.submitted", "task": {...}}``."""
    from .producers.evaluator import (
        EvaluatorError,
        evaluate_and_apply,
        load_evaluator,
        load_spec,
    )

    try:
        spec = load_spec(args.spec)
    except (OSError, ValueError, EvaluatorError) as exc:
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
        evaluator = load_evaluator(spec, evaluator_ref=args.evaluator_ref)
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


def _cmd_verify_submitted(args: argparse.Namespace) -> int:
    """Explicit, scoped re-check for already-submitted verification tasks."""
    with _core()._client(args) as c:
        reports = [
            c.verify_submitted(task_id, evaluator_ref=args.evaluator_ref)
            for task_id in args.task_id
        ]
    return _core()._emit(reports if len(reports) != 1 else reports[0])

def _cmd_charter_show(args: argparse.Namespace) -> int:
    from .worker_charter import charter_text

    try:
        text = charter_text(args.name)
    except KeyError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 0
