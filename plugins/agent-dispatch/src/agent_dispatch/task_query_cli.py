"""Task browsing / inbox / payload CLI commands extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import json
import sys
import time

from .client import DispatchError
from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def _core_helper(name: str, local):
    """Prefer a monkeypatched ``agent_dispatch.__main__`` helper when present."""

    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


#: The picker Tasks-pivot **board** groups, in the operator's priority order:
#: what needs your attention first (a task blocked awaiting your steer), then
#: what's actually running (more interesting to inspect at a glance than a
#: task not yet running), then the rest of the pickable/in-flight lifecycle,
#: then recently-finished tasks. The tuple index is the sort key; the string
#: is the pivot section header. ``--board`` tags each task with its group and
#: orders by this sequence so the picker's first-seen grouping renders the
#: sections in exactly this order. Operator feedback 2026-09-20: Started
#: moved ahead of Queued -- keep this in sync with `board_cli.py`'s
#: byte-identical `GROUPS` tuple (used by the Picker's own direct board read,
#: distinct from this module's delegated `inbox` CLI path).
_BOARD_GROUPS = (
    "Blocked",
    "Proposed",
    "Started",
    "Queued",
    "Suspended",
    "Completed",
    "Confirmed",
    "Abandoned",
)
_BOARD_TERMINAL = frozenset({"Completed", "Confirmed", "Abandoned"})


def _board_group(task: dict) -> str:
    """The display group for a task on the picker board (see ``_BOARD_GROUPS``).

    A **terminal** status (completed / confirmed / abandoned / dead_letter)
    wins first -- a task can carry a stale ``awaiting_steer`` flag after being
    abandoned while blocked, and a finished task is never "Blocked". Otherwise
    ``awaiting_steer`` (a live task needing the operator's steer) wins over
    the raw lifecycle state, then proposed/queued/suspended, else any other
    owned in-flight state reads as *Started*."""
    st = task.get("status")
    if st == "completed":
        return "Completed"
    if st == "confirmed":
        return "Confirmed"
    if st in ("abandoned", "dead_letter"):
        return "Abandoned"
    if task.get("awaiting_steer"):
        return "Blocked"
    if st == "proposed":
        return "Proposed"
    if st == "queued":
        return "Queued"
    if st == "suspended":
        return "Suspended"
    return "Started"


_BOARD_ACTIVITY_TTL_SECONDS = 90.0


def _browse_peer(args: argparse.Namespace, subcommand: str, *, repo: str | None = None) -> int:
    """Peer-queue browse (Phase 8 Slice 8c): run the read command on the remote
    ``--machine`` over the SSH mesh and stream its JSON straight through.

    The remote CLI reads *its own* loopback coordinator (and, via 8b, enriches
    against its own local bridge), so the output is exactly what a local run on
    the peer would produce.
    """
    from . import remote_dispatch

    argv = remote_dispatch.build_remote_browse_argv(subcommand, args, repo=repo)
    try:
        result = remote_dispatch.browse_remote(args.machine, argv)
    except remote_dispatch.RemoteDispatchUnavailable as exc:
        print(
            f"agent-dispatch: peer-queue browse of {args.machine!r} unavailable ({exc})",
            file=sys.stderr,
        )
        return 2
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.returncode != 0:
        diagnosis = remote_dispatch.diagnose_remote_failure(
            args.machine, result.returncode, result.stderr
        )
        print(f"agent-dispatch: {diagnosis}", file=sys.stderr)
    return result.returncode

def _cmd_list(args: argparse.Namespace) -> int:
    repo = _core()._scope_repo(args)
    if not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    from . import remote_dispatch

    if remote_dispatch.is_peer_machine(getattr(args, "machine", None)):
        return _core()._browse_peer(args, "list", repo=repo)
    with _core()._client(args) as c:
        tasks = c.list(
            repo=repo,
            status=args.status,
            target_machine=args.target_machine,
            target_repo=args.target_repo,
            label=args.label,
            evaluator_ref=args.evaluator_ref,
            limit=args.limit,
        )
    from . import tracking

    return _core()._emit(tracking.enrich_tasks(_core()._enrich(tasks)))

def _cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnose held/suspended tasks (Boundary I / #2577; #2884 session-
    liveness / #2884). ``--task`` narrows to one exact task;
    ``--check-live-sessions`` walks its full reservation history for a
    shadowed-but-live earlier attempt. See :mod:`agent_dispatch.doctor`."""
    from . import doctor

    with _core()._client(args) as c:
        if args.task:
            try:
                tasks = [c.get(args.task)]
            except DispatchError as exc:
                print(f"agent-dispatch: {exc}", file=sys.stderr)
                return 1
        else:
            repo = _core()._scope_repo(args)
            if not repo:
                print(_core()._REPO_UNRESOLVED, file=sys.stderr)
                return 2
            tasks = c.list(
                repo=repo,
                status=",".join(doctor.EXAMINED_STATUSES),
                label=args.label,
                limit=args.limit,
            )
        payload = doctor.diagnose_many(
            c,
            tasks,
            check_live_sessions=args.check_live_sessions,
            stale_lease_seconds=args.stale_lease_seconds,
            repair_orphaned=args.repair,
        )
    return _core()._emit(payload)

def _board_activity(task: dict, *, now: float | None = None) -> str | None:
    """Independent live-execution badge for the picker task board.

    Lifecycle ``group`` answers where the task is (Blocked/Queued/Started/etc.).
    This badge answers whether its assigned embodiment is executing a turn now.
    It deliberately does not infer activity from ``status == started``.
    """
    activity = task.get("activity")
    if activity not in {"ACTIVE", "STALLED"}:
        return None
    try:
        observed = float(task.get("activity_updated_at"))
        current = time.time() if now is None else float(now)
    except (TypeError, ValueError):
        return None
    ttl = getattr(_core(), "_BOARD_ACTIVITY_TTL_SECONDS", _BOARD_ACTIVITY_TTL_SECONDS)
    return activity if current - observed <= ttl else None

def _board_sort_key(task: dict) -> tuple:
    group_fn = _core_helper("_board_group", _board_group)
    groups = getattr(_core(), "_BOARD_GROUPS", _BOARD_GROUPS)
    grp = group_fn(task)
    prio = groups.index(grp) if grp in groups else len(groups)
    # Within a group, surface the most recent activity first.
    ts = task.get("updated_at") or task.get("created_at") or 0
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = 0.0
    return (prio, -ts)

def _board_keep(task: dict, cutoff: float) -> bool:
    """Keep an active task always; keep a terminal (completed/abandoned) task only
    when its terminal timestamp is at/after ``cutoff`` (the recency window), so the
    board shows *recently* finished work without unbounded growth."""
    group_fn = _core_helper("_board_group", _board_group)
    terminals = getattr(_core(), "_BOARD_TERMINAL", _BOARD_TERMINAL)
    if group_fn(task) not in terminals:
        return True
    ts = task.get("completed_at") or task.get("updated_at") or 0
    try:
        return float(ts) >= cutoff
    except (TypeError, ValueError):
        return False

def _cmd_inbox(args: argparse.Namespace) -> int:
    """Machine-scoped, cross-lane view of pickable tasks.

    Unlike ``list`` (which scopes to the calling repo's lane), ``inbox`` asks
    the coordinator for tasks across *every* lane and keeps those this machine
    can pick up: a matching ``target_machine`` plus machine-agnostic tasks
    (``target_machine`` unset). Defaults to ``proposed`` -- the "available to
    start" state. Each entry carries ``target_worktree``, ``affinity``,
    ``labels`` and the display-only ``repo_name`` so a consumer (e.g. the
    worktree picker's task pivot) can group by worktree and badge handoffs.

    With ``--machine Y`` naming a *remote* peer, the inbox is read from **Y's
    own coordinator** over the SSH mesh (Phase 8 Slice 8c) -- what Y can actually
    pick up -- rather than filtering the local queue.
    """
    from . import remote_dispatch

    if remote_dispatch.is_peer_machine(args.machine):
        return _core()._browse_peer(args, "inbox")
    machine = args.machine
    if not machine:
        from .identity import resolve_machine

        machine = resolve_machine()
    if not machine:
        print(
            "agent-dispatch: could not resolve this machine — pass --machine "
            "(agent-worktrees not found, or not inside a worktree)",
            file=sys.stderr,
        )
        return 2
    # --board: the status-grouped picker board. Widens the fetch across the whole
    # visible lifecycle (proposed -> in-flight -> recently terminal), tags each
    # task with a display `group`, drops terminal tasks older than the recency
    # window, and orders by group priority so the picker renders the sections
    # Blocked -> Proposed -> Started -> Queued -> Suspended -> Completed ->
    # Abandoned. Overrides --awaiting-steer / --status.
    if getattr(args, "board", False):
        from . import board_cli as _board_cli

        status = "proposed,queued,claimed,started,suspended,completed,abandoned,dead_letter"
        with _core()._client(args) as c:
            tasks = c.list(repo=None, status=status, label=args.label, limit=args.limit)
            def _relay_fetch_many(
                refs: list[tuple[str, str]]
            ) -> dict[tuple[str, str], dict | None]:
                try:
                    return c.worktree_status_relays(refs)
                except DispatchError:
                    return {}

            inbox = _board_cli._build(
                tasks,
                machine=machine,
                recent_mins=getattr(args, "recent_mins", 120),
                relay_fetch_many=_relay_fetch_many,
            )
        return _core()._emit(inbox)
    # --awaiting-steer widens the fetch to the owned states (a task blocked on
    # operator steering is `claimed`/`started`, not a filterable "held" -- HELD
    # is a derived category), then keeps only the *pickable* (`proposed`) rows
    # plus any *awaiting-steer* row. This is the picker steer surface's read:
    # "what I can start + what needs my answer", without the rest of the owned
    # in-progress queue.
    steer_only = getattr(args, "awaiting_steer", False)
    status = "proposed,claimed,started,suspended" if steer_only else args.status
    with _core()._client(args) as c:
        tasks = c.list(repo=None, status=status, label=args.label, limit=args.limit)
    from .queue import machine_matches

    inbox = [t for t in tasks if machine_matches(t.get("target_machine"), machine)]
    if steer_only:
        inbox = [t for t in inbox if t.get("status") == "proposed" or t.get("awaiting_steer")]
    return _core()._emit(_core()._enrich(inbox))

def _cmd_find(args: argparse.Namespace) -> int:
    repo = _core()._scope_repo(args)
    if not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _core()._client(args) as c:
        return _core()._emit(_core()._enrich(c.find(args.query, repo=repo, limit=args.limit)))

def _cmd_sweep(args: argparse.Namespace) -> int:
    repo = _core()._scope_repo(args)
    if not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _core()._client(args) as c:
        return _core()._emit(_core()._enrich(c.sweep(repo=repo, limit=args.limit)))

def _cmd_watch(args: argparse.Namespace) -> int:
    with _core()._client(args) as c:
        try:
            for event in c.stream_events():
                json.dump(event, sys.stdout)
                sys.stdout.write("\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            return 0
    return 0

def _cmd_payload(args: argparse.Namespace) -> int:
    with _core()._client(args) as c:
        result = c.payload(args.task_id)
    if args.raw:
        content = result.get("payload")
        if content is None:
            print(
                f"agent-dispatch: task {args.task_id} has no resolvable payload",
                file=sys.stderr,
            )
            return 4
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    return _core()._emit(result)

def _consume_already_spent(task_id: str, task: dict) -> int:
    """Refuse to replay a spent handoff baton.

    Prints a clear STOP notice (read by the successor agent in place of the
    brief) and returns exit ``3`` so programmatic callers can detect the
    already-consumed no-op. The work is done; a re-seeded successor must not
    redo it.
    """
    result_ref = task.get("result_ref")
    result_str = f" (result: {result_ref})" if result_ref else ""
    print(
        f"[agent-dispatch] Handoff task {task_id} is already COMPLETED"
        f"{result_str}.\n"
        f"This handoff was already picked up and its work finished -- NOT "
        f"replaying the brief. Do NOT redo this work; end your turn.\n"
        f"If this is unexpected, inspect with: agent-dispatch show {task_id}"
    )
    print(
        f"agent-dispatch: handoff {task_id} already consumed (completed); not replayed",
        file=sys.stderr,
    )
    return 3

def _cmd_result(args: argparse.Namespace) -> int:
    with _core()._client(args) as c:
        result = c.result(args.task_id)
    if args.raw:
        content = result.get("result")
        if content is None:
            print(
                f"agent-dispatch: task {args.task_id} has no structured result",
                file=sys.stderr,
            )
            return 1
        json.dump(content, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    return _core()._emit(result)

def _cmd_consume(args: argparse.Namespace) -> int:
    """Resume-and-consume a handoff and print its payload content.

    Two completion modes:

    - **Baton (default):** drive the task all the way to ``completed`` in one
      shot -- loading the brief IS consuming the baton, so a handoff is marked
      completed the *moment* it is picked up (the classic quick-baton resume:
      /resume-handoff, a hand-pasted seed). The continuation *work* is tracked
      by its effort/issue, not this task.
    - **Deferred (``--defer-complete``):** approve -> claim -> **start** the task
      (take ownership, mark it in-progress) and print the brief, but do **not**
      complete it. This is the *takeover* pickup: a dispatched/embodied successor
      loads the brief, works the task, and calls ``agent-dispatch complete
      <id>`` **explicitly** only when it reaches the handoff's goal -- so
      ``completed`` means *the work is done*, not *the baton was handed over*.

    Ordinary transitions are best-effort and idempotent: an already-advanced
    task just prints its payload. Suspended pickup is stricter: deferred mode
    atomically adopts the task into the successor's current session, while
    baton mode completes only the exact suspended incarnation that was read.
    If either fence loses a race, the payload is not replayed.

    **Replay debounce (a *completed handoff* is spent).** A handoff is a baton:
    once it has been picked up and its work driven to ``completed``, re-consuming
    it must NOT re-deliver the brief as if it were fresh. A live-cutover (or any
    re-seeded successor) that re-runs ``consume <id>`` on an already-completed
    handoff would otherwise redo finished work. So a completed *handoff* is
    refused here with a clear stop notice (exit ``3``) instead of its payload --
    the single chokepoint every task-backed resume seed flows through. A
    still-in-flight handoff (``started`` -- e.g. a legitimate takeover recovery)
    is unaffected; only ``completed`` is treated as spent.
    """
    task_id = args.task_id
    defer = getattr(args, "defer_complete", False)
    machine, worktree = _core()._identity(args)
    try:
        repo = _core()._scope_repo(args)
    except Exception:  # lane resolution is best-effort here -- still print payload
        repo = None
    with _core()._client(args) as c:
        try:
            task = c.get(task_id)
        except DispatchError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 1
        status = task.get("status")
        # Debounce a spent baton: a *completed handoff* is never replayed.
        is_handoff = ("handoff" in (task.get("labels") or [])) or (
            task.get("source") == "context-handoff"
        )
        if is_handoff and status in ("completed", "confirmed"):
            return _core()._consume_already_spent(task_id, task)
        if status not in ("completed", "confirmed", "abandoned"):
            owner: str | None = None
            if status == "proposed":
                try:
                    c.approve(task_id)
                    status = "queued"
                except DispatchError:
                    pass
            if status in ("queued", "proposed"):
                try:
                    claimed = c.claim(
                        worker_id=args.worker_id,
                        repo=repo,
                        machine=machine,
                        worktree=worktree,
                        task_id=task_id,
                    )
                    owner = (claimed or {}).get("owner")
                except DispatchError:
                    owner = None
            elif status in ("claimed", "started", "suspended"):
                owner = task.get("owner")
            if owner:
                if status == "suspended":
                    if defer:
                        try:
                            c.resume(
                                task_id,
                                owner,
                                wake=False,
                                adopt_session=True,
                                expected_owner_session_id=task.get("owner_session_id"),
                                expected_generation=task.get("generation"),
                            )
                        except DispatchError as exc:
                            print(f"agent-dispatch: {exc}", file=sys.stderr)
                            return 1
                    else:
                        result_ref = args.result_ref or f"consumed:{worktree or 'successor'}"
                        try:
                            c.complete(
                                task_id,
                                owner,
                                result_ref=result_ref,
                                expected_status="suspended",
                                expected_owner_session_id=task.get("owner_session_id"),
                                expected_generation=task.get("generation"),
                            )
                        except DispatchError as exc:
                            print(f"agent-dispatch: {exc}", file=sys.stderr)
                            return 1
                else:
                    try:
                        c.start(task_id, owner)
                    except DispatchError:
                        pass
                    # Deferred pickup stops at 'started': the successor completes
                    # explicitly when the work is done. Baton mode completes now.
                    if not defer:
                        result_ref = args.result_ref or f"consumed:{worktree or 'successor'}"
                        try:
                            c.complete(task_id, owner, result_ref=result_ref)
                        except DispatchError:
                            pass
        result = c.payload(task_id)
    content = result.get("payload")
    if content is None:
        print(
            f"agent-dispatch: task {task_id} has no resolvable payload",
            file=sys.stderr,
        )
        return 4
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    return 0

def _cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve_stdio

    serve_stdio()
    return 0
