"""Task browsing / inbox / payload CLI commands extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .client import DispatchError
from .handoff_claim_release import is_handoff_task
from .loop_commands import _resolve_cli_module
from .queue_common import worker_id_for


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
    "Paused",
    "Proposed",
    "Started",
    "Queued",
    "Suspended",
    "Submitted",
    "Completed",
    "Abandoned",
)
_BOARD_TERMINAL = frozenset({"Submitted", "Completed", "Abandoned"})


def _board_group(task: dict) -> str:
    """The display group for a task on the picker board (see ``_BOARD_GROUPS``).

    A **concluded** status (submitted / completed / abandoned / dead_letter)
    wins first -- a task can carry a stale ``awaiting_steer`` flag after being
    abandoned while blocked, and a finished task is never "Blocked". A durable
    operator-set pause hold (``hold_reason``) is next -- its own group,
    distinct from system-``Suspended`` and from ``Blocked`` (see
    `board_cli.py`'s byte-identical `_group`). Otherwise ``awaiting_steer`` (a
    live task needing the operator's steer) wins over the raw lifecycle
    state, then proposed/queued/suspended, else any other owned in-flight
    state reads as *Started*."""
    st = task.get("status")
    if st == "submitted":
        return "Submitted"
    if st == "completed":
        return "Completed"
    if st in ("abandoned", "dead_letter"):
        return "Abandoned"
    if task.get("hold_reason"):
        return "Paused"
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
    shadowed-but-live earlier attempt. In the repo/label sweep (no
    ``--task``), also separately queries any ``queued`` task stuck behind a
    genuinely failed spawn reservation (:func:`doctor
    .find_stuck_queued_reservations`, #5209) -- a bounded, independent query
    that never competes with the main sweep's own ``--limit``. See
    :mod:`agent_dispatch.doctor`."""
    from . import doctor

    with _core()._client(args) as c:
        if args.task:
            try:
                tasks = [c.get(args.task)]
            except DispatchError as exc:
                print(f"agent-dispatch: {exc}", file=sys.stderr)
                return 1
            payload = doctor.diagnose_many(
                c,
                tasks,
                check_live_sessions=args.check_live_sessions,
                stale_lease_seconds=args.stale_lease_seconds,
                repair_orphaned=args.repair,
            )
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
            stuck_queued = doctor.find_stuck_queued_reservations(
                c, repo=repo, label=args.label, limit=args.limit
            )
            if stuck_queued:
                payload["examined"] += len(stuck_queued)
                payload["diagnoses"].extend(d.as_dict() for d in stuck_queued)
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

        status = (
            "proposed,queued,claimed,started,suspended,"
            "submitted,completed,abandoned,dead_letter"
        )
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

def _task_is_handoff(task: dict) -> bool:
    """Shared by the initial-snapshot check and the refreshed-re-fetch check
    below so both apply the identical classification rather than drifting apart."""
    return is_handoff_task(task)

def _fence_consumer_session(
    c, task_id: str, owner: str, *, expected_generation: int | None
) -> int | None:
    """Bind this invocation's durable per-session identity exclusively to
    ``task_id`` via a generation-fenced CAS.

    Matching ``owner`` to a resolved ``machine/worktree`` worker_id never
    proves THIS invocation is the one that claimed/holds the task: worker_id
    is shared by every session running in the same machine/worktree, so a
    second, concurrently-running successor session resolves to the
    identical worker_id and can start/complete/replay the same task's
    payload too -- whether it raced in on the original claim (claiming
    clears ``owner_session_id``, leaving the binding open) or found the task
    already claimed/started at its own first ``get()``. Binds exclusively in
    both cases via the same CAS: the first invocation to bind wins
    (idempotent on a genuine retry by the same session, since it re-binds
    the same identity), and a second, distinct session's bind is refused.

    Returns ``None`` to proceed normally, or an exit code: ``3`` if a
    concurrent session already won the bind (a CAS conflict, HTTP 409 --
    refuse exactly like a lost claim race, never replay the payload), or
    ``1`` for any other bind failure (a missing task, a coordinator error --
    a real error, not evidence of a lost race). No identity available
    (``COPILOT_AGENT_SESSION_ID`` unset, e.g. a bare CLI invocation outside a
    tracked session) leaves this unfenced, same as before this check
    existed.
    """
    session_identity = os.environ.get("COPILOT_AGENT_SESSION_ID")
    if not session_identity:
        return None
    try:
        c.bind_owner_session(
            task_id,
            owner,
            session_identity,
            expected_generation=expected_generation,
        )
    except DispatchError as exc:
        if exc.status_code == 409:
            print(
                f"[agent-dispatch] Handoff task {task_id} is already bound "
                f"to a different session than this one -- a concurrent "
                f"successor session in the same worktree won the race. NOT "
                f"replaying the brief. Do NOT act on this task; end your "
                f"turn.\n"
                f"If this is unexpected, inspect with: agent-dispatch show "
                f"{task_id}"
            )
            print(
                f"agent-dispatch: handoff {task_id} bound to a different "
                f"session ({exc}); not replayed",
                file=sys.stderr,
            )
            return 3
        print(
            f"agent-dispatch: could not bind handoff task {task_id} to this "
            f"session: {exc}",
            file=sys.stderr,
        )
        return 1
    return None

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
        f"[agent-dispatch] Handoff task {task_id} is already spent"
        f"{result_str}.\n"
        f"This handoff was already picked up and its work finished -- NOT "
        f"replaying the brief. Do NOT redo this work; end your turn.\n"
        f"If this is unexpected, inspect with: agent-dispatch show {task_id}"
    )
    print(
        f"agent-dispatch: handoff {task_id} already consumed (submitted/completed); not replayed",
        file=sys.stderr,
    )
    return 3

def _consume_retired(task_id: str, task: dict) -> int:
    """Refuse to deliver a retired (abandoned) handoff baton.

    A handoff is abandoned for several reasons: a newer handoff for the same
    worktree superseded it, it was aborted, or liveness cleanup retired it. Its
    payload stays readable, but a successor seeded with it must not act on it.
    The notice names no cause; the event log records the actual one.
    """
    worktree = task.get("target_worktree")
    where = f" (worktree {worktree})" if worktree else ""
    print(
        f"[agent-dispatch] Handoff task {task_id}{where} was abandoned, so it "
        f"is retired. NOT delivering the brief. Do NOT act on this task; end "
        f"your turn.\n"
        f"See why with: agent-dispatch events {task_id}"
    )
    print(
        f"agent-dispatch: handoff {task_id} was abandoned (retired); not "
        f"delivered -- do not act on it. See why: agent-dispatch events {task_id}",
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

    - **Baton (default):** drive the task all the way to ``submitted`` in one
      shot -- loading the brief IS consuming the baton, so a handoff is marked
      submitted the *moment* it is picked up (the classic quick-baton resume:
      /resume-handoff, a hand-pasted seed). The continuation *work* is tracked
      by its effort/issue, not this task.
    - **Deferred (``--defer-complete``):** approve -> claim -> **start** the task
      (take ownership, mark it in-progress) and print the brief, but do **not**
      complete it. This is the *takeover* pickup: a dispatched/embodied successor
      loads the brief, works the task, and calls ``agent-dispatch complete
      <id>`` **explicitly** only when it reaches the handoff's goal -- so
      ``submitted`` means *the work is done*, not *the baton was handed over*.

    Ordinary transitions are best-effort and idempotent: an already-advanced
    task just prints its payload. A **claim race** is stricter: if a
    concurrent claimant already won it, the payload is refused (not
    replayed) to the losing caller -- see the replay-debounce note below,
    the same exactly-once contract applies here too. Suspended pickup is
    stricter in the same way: deferred mode atomically adopts the task into
    the successor's current session, while baton mode completes only the
    exact suspended incarnation that was read. If either fence loses a race,
    the payload is not replayed.

    **Replay debounce (a *spent handoff* is spent, and a *lost race* is not
    yours).** A handoff is a baton: once it has been picked up and its work
    driven to ``submitted``, re-consuming it must NOT re-deliver the brief as
    if it were fresh. A live-cutover (or any re-seeded successor) that
    re-runs ``consume <id>`` on an already-spent handoff would otherwise redo
    finished work. So a completed *handoff* is refused here with a clear
    stop notice (exit ``3``) instead of its payload -- the single chokepoint
    every task-backed resume seed flows through. The same exit ``3`` refusal
    applies when this invocation loses a claim race to a concurrent
    claimant: replaying the brief to the loser as well as the winner would
    let two successors both believe they are the one continuing a baton
    meant for exactly one. A still-in-flight handoff (``started`` -- e.g. a
    legitimate takeover recovery) is unaffected; only ``submitted`` is
    treated as spent. An ``abandoned`` handoff (superseded by a newer
    handoff, or aborted) is likewise refused with exit ``3`` rather than
    delivered, so a successor seeded with an out-of-date brief stands down.
    A genuine failure to claim at all (no owner, no
    concurrent claimant either) is a real error (exit ``1``), never a silent
    success.
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
        # Debounce a spent baton: a submitted/completed handoff is never
        # replayed, and neither is a retired (abandoned) one.
        is_handoff = _task_is_handoff(task)
        if is_handoff and status in ("submitted", "completed"):
            return _core()._consume_already_spent(task_id, task)
        if is_handoff and status == "abandoned":
            return _core()._consume_retired(task_id, task)
        if status not in ("submitted", "completed", "abandoned"):
            owner: str | None = None
            if status == "proposed":
                try:
                    c.approve(task_id)
                    status = "queued"
                except DispatchError:
                    pass
            if status in ("queued", "proposed"):
                claim_exc: DispatchError | None = None
                try:
                    claimed = c.claim(
                        worker_id=args.worker_id,
                        repo=repo,
                        machine=machine,
                        worktree=worktree,
                        task_id=task_id,
                    )
                    owner = (claimed or {}).get("owner")
                except DispatchError as exc:
                    owner = None
                    claim_exc = exc
                if not owner:
                    # A claim that fails -- whether by raising, or by
                    # returning an ownerless 200 (the coordinator is
                    # draining, or the task was no longer claimable;
                    # DispatchClient.claim() can legitimately return None
                    # with no exception in either case) -- is only ever
                    # benign when a *legitimate* concurrent claimant already
                    # won the race (the docstring's "ordinary transitions
                    # are idempotent" contract). Confirm that via a re-fetch
                    # before deciding: if nobody else owns it either, this
                    # is a genuine failure (real error, exit 1); if a
                    # concurrent claimant does, refuse to replay the payload
                    # to this losing caller (exit 3, below).
                    try:
                        refreshed = c.get(task_id)
                    except DispatchError:
                        refreshed = None
                    refreshed_status = (refreshed or {}).get("status")
                    if refreshed_status in ("submitted", "completed", "abandoned"):
                        # The winning consumer already drove the race all the
                        # way to completion (or abandonment) by the time of
                        # this re-fetch -- baton-mode completion/abandonment
                        # clears the owner, so checking ownership alone would
                        # misread this exact case as a genuine claim failure
                        # instead of the terminal state it actually is.
                        if _task_is_handoff(refreshed):
                            if refreshed_status == "abandoned":
                                return _core()._consume_retired(task_id, refreshed)
                            return _core()._consume_already_spent(task_id, refreshed)
                        # A non-handoff terminal task: the documented
                        # contract is idempotent payload delivery (the same
                        # path a terminal task takes when observed terminal
                        # from the very first get() above), never the
                        # handoff's exit-3 replay refusal.
                        result = c.payload(task_id)
                        content = result.get("payload")
                        if content is None:
                            print(
                                f"agent-dispatch: task {task_id} has no "
                                "resolvable payload",
                                file=sys.stderr,
                            )
                            return 4
                        sys.stdout.write(content)
                        if not content.endswith("\n"):
                            sys.stdout.write("\n")
                        return 0
                    if not (refreshed and refreshed.get("owner")):
                        detail = f": {claim_exc}" if claim_exc is not None else ""
                        print(
                            f"agent-dispatch: could not claim handoff task "
                            f"{task_id}{detail}",
                            file=sys.stderr,
                        )
                        return 1
                    # A legitimate concurrent claimant now owns this task --
                    # this invocation lost the race. For an exactly-once
                    # handoff baton, replaying the payload to the LOSING
                    # caller is itself a duplicate-delivery bug: the losing
                    # caller's own consumer (consumeDispatchHandoffTask)
                    # treats a zero exit as "taskConsumed" and records
                    # itself as the consuming session regardless of who
                    # actually holds the task, so two racing successors
                    # could both receive and act on the same baton. Refuse
                    # exactly like an already-spent handoff rather than
                    # falling through to print a payload this invocation
                    # never actually earned.
                    print(
                        f"[agent-dispatch] Handoff task {task_id} is owned by "
                        f"{refreshed.get('owner')!r}, not this caller -- a "
                        f"concurrent claimant won the race. NOT replaying the "
                        f"brief. Do NOT act on this task; end your turn.\n"
                        f"If this is unexpected, inspect with: agent-dispatch "
                        f"show {task_id}"
                    )
                    print(
                        f"agent-dispatch: handoff {task_id} claimed by a "
                        "concurrent owner; not replayed",
                        file=sys.stderr,
                    )
                    return 3
                # The claim succeeded under this invocation's worker_id --
                # but claiming clears owner_session_id, and worker_id is
                # shared by every session in this machine/worktree, so a
                # second session resolving to the identical worker_id can
                # race in right here (its own claim() call succeeding too,
                # since the coordinator can't distinguish them by worker_id
                # either) before either reaches start()/complete(). Fence
                # the freshly claimed snapshot the same way an
                # already-claimed task is fenced below.
                fence = _fence_consumer_session(
                    c, task_id, owner, expected_generation=(claimed or {}).get("generation")
                )
                if fence is not None:
                    return fence
            elif status in ("claimed", "started", "suspended"):
                owner = task.get("owner")
                # This invocation never actually claimed the task itself in
                # this branch (it was already claimed/started/suspended by
                # the time of the very first get() above) -- so 'owner'
                # here is just whatever this stale snapshot says, not proof
                # this invocation is the one that holds it. Refuse exactly
                # like a lost claim race unless the owner actually matches
                # this invocation's own resolved identity; otherwise this
                # would start/complete the task *as* a different owner and
                # replay the payload to a caller that never won anything.
                this_worker_id = args.worker_id or (
                    worker_id_for(machine, worktree) if machine and worktree else None
                )
                if owner != this_worker_id:
                    print(
                        f"[agent-dispatch] Handoff task {task_id} is owned by "
                        f"{owner!r}, not this caller ({this_worker_id!r}) -- "
                        f"it was already claimed by someone else before this "
                        f"invocation even started. NOT replaying the brief. "
                        f"Do NOT act on this task; end your turn.\n"
                        f"If this is unexpected, inspect with: agent-dispatch "
                        f"show {task_id}"
                    )
                    print(
                        f"agent-dispatch: handoff {task_id} owned by a "
                        "different consumer; not replayed",
                        file=sys.stderr,
                    )
                    return 3
                if status in ("claimed", "started"):
                    # Matching worker_id is still not proof THIS invocation
                    # performed the claim: worker_id is derived from
                    # machine+worktree and is shared by every session
                    # running in the same worktree, so a second,
                    # concurrently-running successor session resolves to
                    # the identical worker_id and passes the check above
                    # despite never claiming anything itself. Fence it the
                    # same way the freshly claimed snapshot above is fenced.
                    fence = _fence_consumer_session(
                        c, task_id, owner, expected_generation=task.get("generation")
                    )
                    if fence is not None:
                        return fence
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
                    # Unlike the claim above, we already hold this task's
                    # ownership by this point -- a start/complete failure here
                    # is never a benign lost race (nothing else can legitimately
                    # race an owned task) and must surface as a real error, not
                    # silently print a payload the caller believes it now owns.
                    try:
                        c.start(task_id, owner)
                    except DispatchError as exc:
                        print(
                            f"agent-dispatch: could not start handoff task "
                            f"{task_id}: {exc}",
                            file=sys.stderr,
                        )
                        return 1
                    # Deferred pickup stops at 'started': the successor completes
                    # explicitly when the work is done. Baton mode completes now.
                    if not defer:
                        result_ref = args.result_ref or f"consumed:{worktree or 'successor'}"
                        try:
                            c.complete(task_id, owner, result_ref=result_ref)
                        except DispatchError as exc:
                            print(
                                f"agent-dispatch: could not complete handoff "
                                f"task {task_id}: {exc}",
                                file=sys.stderr,
                            )
                            return 1
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
