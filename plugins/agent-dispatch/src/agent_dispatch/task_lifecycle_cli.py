"""Task lifecycle and admin command bodies extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import sys

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def _cmd_claim(args: argparse.Namespace) -> int:
    # The positional is the TASK id (consistent with start/complete/yield/abandon,
    # which all take the task id first); ``--task`` is kept as a back-compat alias.
    # The owner/worker id -- rarely needed, since identity resolves from CWD -- is
    # the explicit ``--worker``/``--as`` flag. This removes the old ambiguity where
    # a bare ``claim <id>`` bound <id> to the worker slot and silently leased an
    # arbitrary task under it.
    task_id = args.task_id or args.task
    if args.task_id and args.task and args.task_id != args.task:
        print(
            f"agent-dispatch claim: conflicting task ids (positional '{args.task_id}' "
            f"vs --task '{args.task}'). Pass the task id once.",
            file=sys.stderr,
        )
        return 2
    machine, worktree = _core()._identity(args)
    all_repos = bool(getattr(args, "all_repos", False))
    repo = None if all_repos else _core()._scope_repo(args)
    if not all_repos and not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _core()._client(args) as c:
        task = c.claim(
            worker_id=args.worker_id,
            capabilities=args.capability or [],
            repo=repo,
            all_repos=all_repos,
            machine=machine,
            worktree=worktree,
            task_id=task_id,
            lease_seconds=args.lease_seconds,
            evaluation=getattr(args, "evaluation", False),
        )
    if task is None:
        print("no claimable task", file=sys.stderr)
        return 3
    return _core()._emit(_core()._enrich(task))

def _cmd_worktree_status(args: argparse.Namespace) -> int:
    machine, worktree = _core()._identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve worktree identity — pass --machine and --worktree "
            "(agent-worktrees not found, or not inside a worktree)",
            file=sys.stderr,
        )
        return 2
    repo = _core()._scope_repo(args)
    if not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _core()._client(args) as c:
        inbox = c.mine(machine, worktree, repo=repo)
    return _core()._emit(_core()._enrich({"machine": machine, "worktree": worktree, "repo": repo, **inbox}))

def _cmd_claim_status(args: argparse.Namespace) -> int:
    """Claim-provider callback (claim-provider-pattern effort): ``claim-status
    <task_id>`` for the ``dispatch-task:`` namespace agent-worktrees'
    claim-provider registry resolves. Returns the small envelope
    ``agent_worktrees.claim_providers`` documents -- ``exists`` (required),
    plus ``state``/``detail`` when the task is known -- never raises even
    when the coordinator itself is unreachable (degrades to
    ``exists: False`` with a ``detail`` explaining why, which the registry's
    own caller treats as a callback failure only on a non-JSON/non-zero
    exit, not on this JSON envelope).
    """
    from .client import DispatchError

    try:
        with _core()._client(args) as c:
            task = c.get(args.task_id)
    except DispatchError as exc:
        if exc.status_code == 404:
            return _core()._emit({"exists": False, "detail": "no such task"})
        return _core()._emit({"exists": False, "detail": f"HTTP {exc.status_code}: {exc.detail}"})
    return _core()._emit({"exists": True, "state": task.get("status"), "detail": task.get("owner") or ""})


def _cmd_show(args: argparse.Namespace) -> int:
    with _core()._client(args) as c:
        task = c.get(args.task_id)
        # Surface the accumulated append-only progress log alongside the task, so
        # a (re-)embodied worker reads its goal, done-criteria, AND recorded
        # progress in one call and resumes from it rather than restarting.
        task = dict(task)
        task["progress_log"] = c.progress_log(args.task_id)
        if getattr(args, "history", False):
            task["attachments"] = c.attachments(args.task_id)
    from . import tracking

    return _core()._emit(tracking.enrich_task(_core()._enrich(task)))

def _cmd_claimant(args: argparse.Namespace) -> int:
    """task -> claiming worktree: resolve which worktree owns a task.

    The inbound-ledger reverse of ``worktree-status`` (worktree -> its tasks).
    Returns a focused record: the actual claimant (``owner`` = machine/worktree,
    once the task is claimed/started), or -- for a not-yet-claimed task -- the
    pinned ``target`` worktree, with ``claimed`` distinguishing the two.
    """
    with _core()._client(args) as c:
        task = c.get(args.task_id)
    status = task.get("status")
    owner = task.get("owner")
    claimed = bool(owner) and status in ("claimed", "started", "suspended", "completed")
    if claimed:
        machine, worktree = _core()._split_owner(owner)
        source = "owner"
    else:
        # Not yet claimed -- surface the pin (intended claimant), if any.
        machine = task.get("target_machine")
        worktree = task.get("target_worktree")
        source = "target" if worktree else None
    result = {
        "task_id": args.task_id,
        "status": status,
        "claimed": claimed,
        "worker_id": owner if claimed else None,
        "machine": machine,
        "worktree": worktree,
        "resolved_from": source,
        "owner_session_id": task.get("owner_session_id"),
        "repo": task.get("repo"),
    }
    return _core()._emit(_core()._enrich(result))

def _cmd_yield(args: argparse.Namespace) -> int:
    worker_id = _core()._resolve_owner(args, verb="yield")
    if worker_id is None:
        return 2
    exclude = args.exclude
    if not exclude and getattr(args, "exclude_self", None):
        machine, worktree = _core()._identity(args)
        if args.exclude_self == "worktree" and worktree:
            exclude = f"worktree:{worktree}"
        elif args.exclude_self == "machine" and machine:
            exclude = f"machine:{machine}"
    with _core()._client(args) as c:
        return _core()._emit(c.yield_task(args.task_id, worker_id, note=args.note, exclude=exclude))

def _cmd_start(args: argparse.Namespace) -> int:
    worker_id = _core()._resolve_owner(args, verb="start")
    if worker_id is None:
        return 2
    with _core()._client(args) as c:
        return _core()._emit(c.start(args.task_id, worker_id))

def _cmd_suspend(args: argparse.Namespace) -> int:
    worker_id = _core()._resolve_owner(args, verb="suspend")
    if worker_id is None:
        return 2
    with _core()._client(args) as c:
        return _core()._emit(c.suspend(args.task_id, worker_id, reason=args.reason))

def _cmd_resume(args: argparse.Namespace) -> int:
    worker_id = _core()._resolve_owner(args, verb="resume")
    if worker_id is None:
        return 2
    with _core()._client(args) as c:
        return _core()._emit(
            c.resume(
                args.task_id,
                worker_id,
                wake=args.wake,
                message=args.message,
            )
        )

def _cmd_release(args: argparse.Namespace) -> int:
    worker_id = _core()._resolve_owner(args, verb="release")
    if worker_id is None:
        return 2
    with _core()._client(args) as c:
        return _core()._emit(c.release(args.task_id, worker_id, reason=args.reason))

def _cmd_pause(args: argparse.Namespace) -> int:
    with _core()._client(args) as c:
        return _core()._emit(
            c.set_hold(
                args.task_id,
                reason=args.reason,
                actor=_core()._hold_actor(args),
                expected_status=args.expected_status,
            )
        )

def _cmd_unpause(args: argparse.Namespace) -> int:
    with _core()._client(args) as c:
        return _core()._emit(
            c.clear_hold(
                args.task_id,
                actor=_core()._hold_actor(args),
                expected_status=args.expected_status,
            )
        )

def _cmd_embody_interactive(args: argparse.Namespace) -> int:
    """Run Phase 1's interactive-embodiment transaction and print its result.

    The caller (an operator's own shell, or the Picker acting on their
    behalf) is the machine the new/resumed worktree lives on -- resolved from
    CWD via agent-worktrees, or an explicit ``--machine`` override for a
    CWD-neutral caller (e.g. a service acting on an operator's request).
    """
    from .identity import resolve_machine
    from .interactive_embody import InteractiveEmbodimentError, launch_interactive_embodiment

    machine = getattr(args, "machine", None) or resolve_machine()
    if not machine:
        print(
            "agent-dispatch: could not resolve this machine's identity "
            "(agent-worktrees absent?). Pass --machine explicitly.",
            file=sys.stderr,
        )
        return 2
    with _core()._client(args) as c:
        try:
            result = launch_interactive_embodiment(
                c,
                args.task_id,
                machine=machine,
                project=args.project,
            )
        except InteractiveEmbodimentError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 1
    return _core()._emit(result)

def _cmd_force_stop(args: argparse.Namespace) -> int:
    from .force_stop import ForceStopError, force_stop
    from .identity import resolve_machine

    actor = getattr(args, "actor", None) or _core()._owner_from_identity(args)
    with _core()._client(args) as c:
        try:
            result = force_stop(
                c,
                args.task_id,
                local_machine=getattr(args, "machine", None) or resolve_machine(),
                actor=actor,
            )
        except ForceStopError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 1
    return _core()._emit(result)

def _cmd_reset(args: argparse.Namespace) -> int:
    if args.to != "proposed":
        print(
            f"agent-dispatch: reset only supports --to proposed today, not {args.to!r}",
            file=sys.stderr,
        )
        return 2
    with _core()._client(args) as c:
        return _core()._emit(
            c.reset(
                args.task_id,
                reason=args.reason,
                expected_status=args.expected_status,
            )
        )

def _cmd_progress(args: argparse.Namespace) -> int:
    worker_id = _core()._resolve_owner(args, verb="progress")
    if worker_id is None:
        return 2
    with _core()._client(args) as c:
        return _core()._emit(
            c.progress(
                args.task_id,
                worker_id,
                phase=args.phase or "",
                summary=args.summary,
                blocker=args.blocker,
                pr=args.pr,
            )
        )

def _cmd_focus(args: argparse.Namespace) -> int:
    # worktree-status-core convergence: a worktree's "focus" IS its status-core
    # summary on the worktree record (the single owning layer). There is no
    # parallel focus store -- writes forward through the `agent-worktrees status`
    # verb (single-writer contract) and reads DERIVE from `agent-worktrees list
    # --json`. `progress` stays task-scoped; only this worktree-scoped focus
    # converges.
    from .identity import aw_list_records, aw_set_summary

    def _focus_row(w: dict) -> dict:
        return {
            "machine": w.get("machine"),
            "worktree": w.get("id"),
            "focus": (w.get("summary") or "").strip(),
            "updated_at": w.get("status_note_at"),
        }

    if args.list:
        rows = [
            _focus_row(w)
            for w in aw_list_records(machine=args.machine)
            if (w.get("summary") or "").strip()
        ]
        return _core()._emit(rows)

    machine, worktree = _core()._identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve this worktree's identity — run "
            "inside a worktree, or pass --machine and --worktree.",
            file=sys.stderr,
        )
        return 2

    if not args.focus_text:
        # Show this worktree's current focus (its status-core summary).
        mine = [w for w in aw_list_records(machine=machine) if w.get("id") == worktree]
        return _core()._emit(
            _focus_row(mine[0]) if mine and (mine[0].get("summary") or "").strip() else {}
        )

    # Write-through to the status core (never a parallel store). The write
    # always targets the CWD worktree via the `agent-worktrees status` verb.
    if not aw_set_summary(args.focus_text):
        print(
            "agent-dispatch: focus write-through failed (agent-worktrees status "
            "unavailable, or not inside a worktree).",
            file=sys.stderr,
        )
        return 2
    return _core()._emit(
        {
            "machine": machine,
            "worktree": worktree,
            "focus": args.focus_text.strip(),
        }
    )

def _cmd_complete(args: argparse.Namespace) -> int:
    # Owner is optional: a worker that claimed under its CWD identity can
    # complete with just the task id -- we resolve the same machine/worktree
    # owner. This is what lets a taken-over successor finish a handoff task with
    # one clean command (`agent-dispatch complete <id>`) once the goal is met.
    worker_id = _core()._resolve_owner(args, verb="complete")
    if worker_id is None:
        return 2
    try:
        result = _core()._read_result(args)
    except (OSError, ValueError) as exc:
        print(f"agent-dispatch: invalid result: {exc}", file=sys.stderr)
        return 2
    with _core()._client(args) as c:
        return _core()._emit(
            c.complete(
                args.task_id,
                worker_id,
                result_ref=args.result_ref,
                result=result,
            )
        )

def _cmd_abandon(args: argparse.Namespace) -> int:
    from . import reattach as _reattach

    with _core()._client(args) as c:
        try:
            result = _reattach.cli_abandon(c, args)
        except _reattach.AbandonRefused as exc:
            print(f"agent-dispatch abandon: {exc}", file=sys.stderr)
            return 2
    return _core()._emit(result)

def _cmd_reattach(args: argparse.Namespace) -> int:
    """Reattach a terminal task's still-live session (Phase 9 / copilot-extensions#2884)."""
    from . import reattach as _reattach

    with _core()._client(args) as c:
        try:
            result = _reattach.reattach(
                c, args.task_id, args.session_id, host=args.host, resume=not args.no_resume
            )
        except _reattach.ReattachError as exc:
            print(f"agent-dispatch reattach: {exc}", file=sys.stderr)
            return 1
    return _core()._emit(result.as_dict())

