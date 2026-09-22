"""Task creation / spawn CLI commands extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from .client import DispatchClient, DispatchError
from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


_REPO_UNRESOLVED = (
    "agent-dispatch: could not resolve the calling repo (lane). Run inside a repo/"
    "worktree, or pass --repo <name|remote>. Tasks are scoped per repo, so a lane "
    "is required."
)


def _cmd_create(args: argparse.Namespace) -> int:
    repo = _core()._scope_repo(args)
    if not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    # Cross-machine dispatch (Phase 8 8a): an embody spawn targeted at *another*
    # machine runs the whole create+embody THERE over the SSH mesh, so
    # the task lives on the target's coordinator and the autopilot session runs
    # + completes on the target. agent-dispatch is per-host, so there is no local
    # task in this path.
    from . import remote_dispatch

    if remote_dispatch.is_cross_machine(args):
        return _core()._dispatch_cross_machine(args, repo)
    payload_inline = args.payload_inline
    if args.remote_create_envelope:
        if args.payload_file or args.payload_inline is not None:
            print(
                "agent-dispatch create: --remote-create-envelope cannot be "
                "combined with --payload-file/--payload-inline",
                file=sys.stderr,
            )
            return 2
        try:
            envelope = json.loads(_core()._read_payload_file(args.remote_create_envelope))
        except (OSError, ValueError) as exc:
            print(
                f"agent-dispatch create: invalid remote create envelope: {exc}",
                file=sys.stderr,
            )
            return 2
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"payload", "producer_capability"}
            or (envelope["payload"] is not None and not isinstance(envelope["payload"], str))
            or not isinstance(envelope["producer_capability"], str)
            or not envelope["producer_capability"]
        ):
            print(
                "agent-dispatch create: remote create envelope must contain "
                "exactly payload (string or null) and producer_capability "
                "(non-empty string)",
                file=sys.stderr,
            )
            return 2
        payload_inline = envelope["payload"]
        args.producer_capability = envelope["producer_capability"]
    elif args.payload_file:
        payload_inline = _core()._read_payload_file(args.payload_file)
    producer_capability = args.producer_capability
    producer_tuple_without_capability = all(
        value is not None
        for value in (
            args.source,
            args.producer_id,
            args.producer_generation,
            args.producer_request_id,
        )
    )
    if producer_capability is None and producer_tuple_without_capability:
        producer_capability = _core().producer_capability_value()
    producer_fence_requested = any(
        value is not None
        for value in (
            args.producer_id,
            args.producer_generation,
            producer_capability,
            args.producer_request_id,
        )
    )
    claim_as = None
    if getattr(args, "claim", False):
        claim_as = _core()._owner_from_identity(args)
        if claim_as is None:
            print(
                "agent-dispatch create --claim: could not resolve this worktree's "
                "identity to claim as; run inside a worktree or pass "
                "--machine/--worktree.",
                file=sys.stderr,
            )
            return 2
    with _core()._client(args) as c:
        task = c.create(
            args.title,
            repo=repo,
            prompt=args.prompt,
            proposed=args.proposed,
            requires=args.require or [],
            excludes=args.exclude or [],
            affinity=_core()._parse_affinity(args.affinity),
            labels=args.label or [],
            payload_ref=args.payload_ref,
            payload_inline=payload_inline,
            target_machine=args.target_machine,
            target_worktree=args.target_worktree,
            target_repo=args.target_repo,
            exclusive_key=args.exclusive_key,
            supersede_exclusive_key=args.supersede_exclusive_key,
            source=args.source,
            origin_ref=args.origin_ref,
            evaluator_ref=args.evaluator_ref,
            dedup_key=args.dedup_key,
            producer_scope=(
                {"repo": repo, "source": args.source} if producer_fence_requested else None
            ),
            producer_id=args.producer_id,
            producer_generation=args.producer_generation,
            producer_capability=producer_capability,
            producer_request_id=args.producer_request_id,
            goal=args.goal,
            done_criteria=args.done_criteria,
            not_before=args.not_before,
            claim_as=claim_as,
        )
    if claim_as is not None:
        # Signal whether THIS call won the create-and-claim (mine now) or lost the
        # dedup race (the subject was already taken by someone else).
        won = task.get("owner") == claim_as and task.get("status") == "claimed"
        return _core()._emit(_core()._enrich({**task, "claimed_by_me": won}))
    if args.spawn and not args.proposed:
        _core()._spawn_worker_for(args, task)
    return _core()._emit(_core()._enrich(task))

def _cmd_producer_fence(args: argparse.Namespace) -> int:
    """Inspect or atomically hand create authority to a producer generation."""
    repo = _core()._scope_repo(args)
    if not repo:
        print(
            "agent-dispatch producer-fence: could not resolve the repo lane; "
            "run inside a repo or pass --repo",
            file=sys.stderr,
        )
        return 2
    with _core()._client(args) as c:
        if args.producer_fence_command == "status":
            return _core()._emit(c.producer_scope_status(repo, args.source))
        if args.producer_fence_command == "handoff":
            return _core()._emit(
                c.handoff_producer_scope(
                    repo,
                    args.source,
                    producer_id=args.producer_id,
                    expected_generation=args.expected_generation,
                    required_label=args.required_label,
                )
            )
    return 2

def _cmd_propose(args: argparse.Namespace) -> int:
    """Draft an unclaimable ``proposed`` task (the propose -> queue lifecycle).

    Identical to ``create`` but the task is always ``proposed`` (unclaimable) and is
    never claimed or spawned -- a proposal is a *plan*, committed to binding later
    with ``queue <id>``. Rejects the execution-only flags ``--claim`` / ``--spawn``
    rather than silently ignoring them.
    """
    if getattr(args, "claim", False) or getattr(args, "spawn", False):
        print(
            "agent-dispatch propose: a proposed draft is not claimed or spawned; use "
            "'create' for that, or 'queue <id>' after proposing to make it claimable",
            file=sys.stderr,
        )
        return 2
    args.proposed = True
    return _core()._cmd_create(args)

def _read_payload_file(path: str) -> str:
    """Read a payload file, or stdin when ``path`` is ``-``."""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()

def _dispatch_cross_machine(args: argparse.Namespace, repo: str) -> int:
    """SSH-push the create+embody to the target machine (Phase 8 8a)."""
    from . import remote_dispatch

    payload: str | None = None
    if args.payload_file:
        payload = _core()._read_payload_file(args.payload_file)
    elif args.payload_inline:
        payload = args.payload_inline
    try:
        result = remote_dispatch.dispatch_to_remote(
            args.target_machine, args, repo=repo, payload=payload
        )
    except remote_dispatch.RemoteDispatchUnavailable as exc:
        print(
            f"agent-dispatch: cross-machine dispatch to {args.target_machine!r} "
            f"unavailable ({exc}); nothing was queued",
            file=sys.stderr,
        )
        return 2
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        diagnosis = remote_dispatch.diagnose_remote_failure(
            args.target_machine, result.returncode, result.stderr
        )
        print(
            f"agent-dispatch: remote dispatch to {args.target_machine!r} failed -- "
            f"{diagnosis}; nothing was queued on {args.target_machine!r}",
            file=sys.stderr,
        )
        return result.returncode
    return 0

def _spawn_worker_for(args: argparse.Namespace, task: dict) -> None:
    """Reserve the spawn atomically, then spawn a worker **exactly once**.

    The spawn is gated on an **atomic spawn reservation** taken from the
    coordinator before launching anything. This closes the gap between the
    queue's transactional dedup/claim and the non-transactional spawn: a dedup
    collision (``create --spawn`` on an existing ``dedup_key``), a racing second
    ``create --spawn``, or a re-poll can never double-spawn -- exactly one caller
    wins the reservation and spawns; the rest skip. If no active reservation can
    be taken (one already exists), this returns without spawning.
    """
    task_id = task["id"]
    reserved_by = f"cli:{uuid.uuid4().hex[:8]}"
    # Resolve (and validate) the worker's coordinator routing BEFORE reserving,
    # so an invalid raw --url target fails loud without leaking a reservation.
    route = _core()._spawn_route(args)
    try:
        with _core()._client(args) as c:
            resp = c.reserve_spawn(task_id, reserved_by=reserved_by)
    except DispatchError as exc:
        # Fail safe: if we cannot reserve, we do NOT spawn (better to leave the
        # task queued than risk a second autonomous worker).
        print(
            f"agent-dispatch: --spawn skipped (could not reserve spawn: {exc}); "
            f"task {task_id} left queued for any worker to claim",
            file=sys.stderr,
        )
        return
    if not resp.get("reserved"):
        res = resp.get("reservation", {})
        print(
            f"agent-dispatch: --spawn skipped -- task {task_id} already has an "
            f"active spawn ({res.get('key')} is {res.get('state')}); not spawning "
            "a second worker",
            file=sys.stderr,
        )
        return

    reservation = resp["reservation"]
    key = reservation["key"]
    spawn_task = task
    prepared = None
    ownership = "unknown"
    from . import embody, remote_dispatch

    try:
        interface = "cli" if getattr(args, "spawn_backend", "bridge") == "embody" else "acp"
        prepared = embody.prepare_reusable_worktree(
            task,
            reservation,
            interface=interface,
            driver="agent-dispatch",
            supervisor=reserved_by,
        )
        worktree = str(prepared["worktree"])
        ownership = str(prepared.get("ownership") or "unknown")
        if (
            reservation.get("worktree") != worktree
            or reservation.get("worktree_ownership") != ownership
        ):
            with _core()._client(args) as c:
                c.record_spawn_worktree(
                    key,
                    worktree,
                    ownership=ownership,
                    creating_host=(
                        remote_dispatch.local_machine() if ownership == "created" else None
                    ),
                    driver="agent-dispatch",
                )
        spawn_task = {
            **task,
            "spawn_worktree": worktree,
            "spawn_worktree_path": prepared["path"],
            "spawn_worktree_ownership": ownership,
            "spawn_session_handle": (
                None if prepared.get("replaced") else reservation.get("session_handle")
            ),
        }
    except (DispatchError, embody.EmbodyUnavailable) as exc:
        if prepared is None or ownership != "created":
            try:
                with _core()._client(args) as c:
                    c.fail_spawn(key, detail=f"worktree create failed: {exc}")
            except DispatchError:
                pass
        print(
            f"agent-dispatch: --spawn skipped (could not create worktree: {exc}); "
            + (
                f"reservation {key} retained for repair"
                if prepared is not None and ownership == "created"
                else f"task {task_id} left queued for any worker to claim"
            ),
            file=sys.stderr,
        )
        return
    spawned = _core()._do_spawn(args, spawn_task, route=route)
    try:
        with _core()._client(args) as c:
            if spawned is None:
                if spawn_task.get("spawn_worktree_ownership") == "created":
                    _core()._release_failed_created_spawn(
                        c,
                        key,
                        worktree=str(spawn_task["spawn_worktree"]),
                        session_id=None,
                        detail="no spawn mechanism available",
                        body_absent=True,
                    )
                else:
                    c.fail_spawn(key, detail="no spawn mechanism available")
            else:
                result, via, handle = spawned
                if result.returncode != 0:
                    detail = f"{via} exited {result.returncode}"
                    if spawn_task.get("spawn_worktree_ownership") == "created":
                        _core()._release_failed_created_spawn(
                            c,
                            key,
                            worktree=str(spawn_task["spawn_worktree"]),
                            session_id=handle.get("session"),
                            detail=detail,
                            body_absent=False,
                        )
                    else:
                        c.fail_spawn(key, detail=detail)
                else:
                    c.record_spawn(
                        key,
                        session_handle=handle.get("session"),
                        worktree=handle.get("worktree"),
                    )
    except DispatchError:
        # Best-effort bookkeeping -- the spawn itself already ran and was
        # reported; a coordinator hiccup here must not crash `create`.
        pass


def _release_failed_created_spawn(
    client: DispatchClient,
    key: str,
    *,
    worktree: str,
    session_id: str | None,
    detail: str,
    body_absent: bool,
) -> None:
    """Fence and synchronously conclude a one-shot spawn's created checkout."""
    from . import embody
    from .supervisor import Supervisor

    client.request_spawn_release(
        key,
        detail=detail,
        disposition="failed",
    )
    try:
        outcome = embody.conclude_dispatch_attempt(
            worktree,
            session_id,
            key,
        )
    except embody.DisposableConclusionError as exc:
        outcome = {
            "action": "failed",
            "reason": str(exc)[:300],
        }
        conclusion_detail = json.dumps(
            outcome,
            sort_keys=True,
            separators=(",", ":"),
        )
        if body_absent:
            client.retire_spawn(
                key,
                exact_absence=True,
                detail=f"{detail}; attempt conclusion failed",
                conclusion_state="pending",
                conclusion_detail=conclusion_detail,
            )
        else:
            client.record_spawn_conclusion(
                key,
                conclusion_state="pending",
                conclusion_detail=conclusion_detail,
            )
        return
    state = Supervisor._conclusion_state(outcome)
    conclusion_detail = json.dumps(
        outcome,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not body_absent:
        client.record_spawn_conclusion(
            key,
            conclusion_state=state,
            conclusion_detail=conclusion_detail,
        )
        return
    action = str(outcome.get("action") or "unknown")
    reason = str(outcome.get("reason") or "")
    suffix = f"attempt conclusion {action}"
    if reason:
        suffix += f" ({reason})"
    client.retire_spawn(
        key,
        exact_absence=True,
        detail=f"{detail}; {suffix}",
        conclusion_state=state,
        conclusion_detail=conclusion_detail,
    )


def _embody_handle(result) -> dict[str, str | None]:
    """Best-effort extract the session/worktree handle from ``embody --json``."""
    from . import embody

    return embody.parse_handle(result)

def _spawn_route(args: argparse.Namespace) -> str:
    """Coordinator routing intent to hand a **locally-spawned** worker, as an
    ``agent-dispatch`` flag fragment.

    A spawned local body reaches its coordinator by discovery or a stable
    moniker, never a raw endpoint: the default local path yields ``""`` (the
    worker rediscovers the live local coordinator, so a zero-downtime port cutover
    is transparent), and ``--shared`` yields ``" --shared"`` (the env-configured
    shared moniker). A raw ``--url`` is refused -- baking a raw, possibly-dynamic
    endpoint into a worker is the exact foot-gun this routing avoids; route by the
    default local coordinator, ``--shared``, or fleet mode
    (``--pool``/``--origin``, which routes by machine alias).
    """
    if getattr(args, "url", None):
        raise SystemExit(
            "agent-dispatch: a spawned worker cannot be pinned to a raw --url "
            "coordinator; route it by the default local coordinator, --shared, or "
            "fleet mode (--pool/--origin)"
        )
    return " --shared" if getattr(args, "shared", False) else ""

def _do_spawn(args: argparse.Namespace, task: dict, *, route: str = ""):
    """Launch a worker for a task (best effort); return ``(result, via, handle)``.

    Returns ``None`` if no spawn mechanism is available (task left queued). Two
    backends select *how* the worker is embodied:

    - ``embody`` -- a **CLI-backed autopilot** session in a fresh parallel
      worktree via ``agent-worktrees embody`` (the "dispatch an agent to do X"
      path: a durable, NF-viewable session that works the task to explicit
      completion). Falls back to the bridge backend if agent-worktrees is
      absent.
    - ``bridge`` (default) -- a **headless** agent-bridge ACP worker.
    """
    backend = getattr(args, "spawn_backend", "bridge")
    if backend == "embody":
        from . import embody

        if embody.embody_available():
            worker_id = f"embody-{uuid.uuid4().hex[:8]}"
            try:
                result = embody.spawn_embodied_worker(
                    task["id"],
                    worker_id=worker_id,
                    route=route,
                    worktree_id=(task.get("target_worktree") or task.get("spawn_worktree")),
                    verify_timeout=getattr(args, "verify_timeout", 0) or 0,
                )
            except embody.EmbodyUnavailable as exc:
                print(
                    f"agent-dispatch: --spawn (embody) skipped ({exc}); task "
                    f"{task['id']} left queued for any worker to claim",
                    file=sys.stderr,
                )
                return None
            _core()._report_spawn_result(result, task["id"], "agent-worktrees embody")
            return result, "agent-worktrees embody", _core()._embody_handle(result)
        # Graceful degrade: no agent-worktrees -> try the headless bridge path.
        print(
            "agent-dispatch: embody backend unavailable (agent-worktrees not on "
            "PATH); falling back to the bridge backend",
            file=sys.stderr,
        )

    from . import bridge, embody

    worker_id = f"spawn-{uuid.uuid4().hex[:8]}"
    prompt = bridge.worker_prompt(
        task["id"],
        worker_id=worker_id,
        route=route,
    )
    prior_session = None
    session_handle = task.get("spawn_session_handle")
    if isinstance(session_handle, str) and session_handle.startswith("local-body:"):
        prior_session = session_handle.removeprefix("local-body:") or None
    try:
        result = bridge.spawn_or_resume_worker(
            task["id"],
            agent=args.spawn_agent,
            worker_id=worker_id,
            prompt=prompt,
            prior_session_id=prior_session,
            liveness_fn=embody.local_body_verdict,
            project=embody.project_for_task(task),
            target_dir=task.get("spawn_worktree_path"),
            worktree_id=task.get("spawn_worktree"),
            wait=not args.run_async,
            json_output=bool(task.get("spawn_worktree")),
        )
    except bridge.BridgeUnavailable as exc:
        print(
            f"agent-dispatch: --spawn skipped ({exc}); task {task['id']} left queued "
            "for any worker to claim",
            file=sys.stderr,
        )
        return None
    _core()._report_spawn_result(result, task["id"], "agent-bridge")
    session = embody.parse_fleet_body_session(result)
    handle = f"local-body:{session}" if session and task.get("spawn_worktree") else worker_id
    return (
        result,
        "agent-bridge",
        {
            "session": handle,
            "worktree": task.get("spawn_worktree"),
        },
    )

def _report_spawn_result(result, task_id: str, via: str) -> None:
    """Print a warning if a best-effort spawn subprocess reported failure."""
    if result.returncode != 0:
        print(
            f"agent-dispatch: spawn via {via} failed (exit {result.returncode}); "
            f"task {task_id} remains queued. stderr: {result.stderr.strip()[:400]}",
            file=sys.stderr,
        )

def _create_args_parent() -> argparse.ArgumentParser:
    """The shared argument surface for ``create`` and ``propose`` (an argparse parent).

    Both verbs enqueue a task from the identical inputs; ``propose`` just forces the
    ``proposed`` (unclaimable) state. Defining the args once keeps the two verbs from
    drifting.
    """
    cp = argparse.ArgumentParser(add_help=False)
    cp.add_argument("title", help="short, specific, self-contained summary of the work")
    cp.add_argument(
        "--prompt",
        default="",
        help="the task instruction -- describe the work fully enough to dedup "
        "against and to execute without extra context",
    )
    cp.add_argument(
        "--repo",
        help="lane (repo) this task belongs to: a local repo name or a remote "
        "URL. Default: the calling repo resolved from the CWD. Tasks stay "
        "in their producing repo's lane -- for a cross-repo *code* target "
        "use --target-repo and let the lane agent do it via working-cross-repo.",
    )
    cp.add_argument("--proposed", action="store_true", help="create as an unclaimable draft")
    cp.add_argument(
        "--claim",
        action="store_true",
        help="atomically create-AND-claim as this worktree (no queued gap). With "
        "--dedup-key <subject>, this is the lazy open-ended-pickup primitive: "
        "either mint the subject as mine, or (on a dedup collision) get back "
        "the row someone else already took -- see 'claimed_by_me' in the "
        "output to tell which.",
    )
    cp.add_argument(
        "--require", action="append", help="hard capability/identity token (repeatable)"
    )
    cp.add_argument(
        "--exclude",
        action="append",
        help="hard EXCLUSION token -- a worker whose capabilities/identity match "
        "any exclude is ineligible (anti-affinity; repeatable). E.g. "
        "'machine:host-a', 'worktree:foo', 'agent:reviewer'.",
    )
    cp.add_argument("--affinity", action="append", help="soft preference key=value (repeatable)")
    cp.add_argument("--label", action="append", help="free-form label (repeatable)")
    cp.add_argument("--payload-ref")
    cp.add_argument("--payload-inline")
    cp.add_argument(
        "--payload-file",
        help="read the payload from a file (large payloads spill to a blob "
        "automatically); '-' reads from stdin",
    )
    cp.add_argument(
        "--remote-create-envelope",
        help=argparse.SUPPRESS,
    )
    cp.add_argument(
        "--target-machine",
        help="route the task to this machine. With `--spawn --spawn-backend "
        "embody` for another machine, dispatch runs there over the SSH "
        "mesh (Phase 8: create+embody land on the target's coordinator).",
    )
    cp.add_argument("--target-worktree")
    cp.add_argument("--target-repo")
    cp.add_argument(
        "--exclusive-key",
        help="logical resource whose spawned worker must be singleton across tasks",
    )
    cp.add_argument(
        "--supersede-exclusive-key",
        action="store_true",
        help="when creating a task with --exclusive-key, abandon older queued/"
        "proposed tasks carrying the same key",
    )
    cp.add_argument("--source")
    cp.add_argument("--origin-ref")
    cp.add_argument("--evaluator-ref")
    cp.add_argument("--dedup-key")
    cp.add_argument(
        "--producer-id",
        help="selected producer metadata for --producer-generation",
    )
    cp.add_argument(
        "--producer-generation",
        type=int,
        help="current create-authority generation for the producer scope",
    )
    cp.add_argument(
        "--producer-capability",
        help="opaque current-generation capability (default: AGENT_DISPATCH_PRODUCER_CAPABILITY)",
    )
    cp.add_argument(
        "--producer-request-id",
        help="mandatory idempotency identity for a managed producer create",
    )
    cp.add_argument(
        "--goal",
        help="durable objective the worker loops toward across turns/embodiments "
        "(the resumable-goal feature); a worker resumes it from recorded "
        "progress rather than restarting. Omit for a plain one-shot task.",
    )
    cp.add_argument(
        "--done-criteria",
        help="explicit criteria for when --goal is met; the worker completes only "
        "once it judges these satisfied (deferred completion).",
    )
    cp.add_argument("--not-before", type=float, default=0.0)
    cp.add_argument(
        "--spawn",
        action="store_true",
        help="after creating, spawn a worker to execute it (best effort)",
    )
    cp.add_argument(
        "--spawn-backend",
        choices=["bridge", "embody"],
        default="bridge",
        help="how to embody the spawned worker: 'embody' = a CLI-backed "
        "autopilot session in a fresh parallel worktree (agent-worktrees "
        "embody -- the 'dispatch an agent to do X' path); 'bridge' "
        "(default) = a headless agent-bridge ACP worker",
    )
    cp.add_argument(
        "--spawn-agent",
        default="task-worker",
        help="agent-bridge agent name to spawn (bridge backend only; "  # marketplace-isolation: allow agent-bridge-management
        "default: task-worker)",
    )
    cp.add_argument(
        "--verify-timeout",
        type=int,
        default=0,
        help="embody backend: wait up to N seconds for the spawned mux session "
        "to come up before returning (default 0: don't wait)",
    )
    cp.add_argument(
        "--async",
        dest="run_async",
        action="store_true",
        help="with --spawn, don't wait for the worker (fire-and-forget)",
    )
    return cp

def register_create_commands(sub) -> None:
    create_parent = _create_args_parent()
    p = sub.add_parser(
        "create",
        parents=[create_parent],
        help="enqueue a task (write a self-contained title + --prompt so a producer sweeping existing tasks can judge duplication)",
    )
    p.set_defaults(func=_core()._cmd_create)

    p = sub.add_parser(
        "propose",
        parents=[create_parent],
        help="draft an unclaimable 'proposed' task (the propose -> queue lifecycle): like create but always proposed, never claimed or spawned; run 'queue <id>' to make it claimable",
    )
    p.set_defaults(func=_core()._cmd_propose)

    p = sub.add_parser(
        "approve", aliases=["queue"], help="move a proposed task to queued (commit it to binding)"
    )
    p.add_argument("task_id")
    p.set_defaults(func=_core()._simple("approve", "task_id"))

    p = sub.add_parser(
        "producer-fence",
        help="inspect or atomically hand off generation-managed task creation",
    )
    fence_sub = p.add_subparsers(dest="producer_fence_command", required=True)
    fp = fence_sub.add_parser("status", help="inspect one exact repo+source producer scope")
    fp.add_argument(
        "--repo",
        help="canonical repo lane (default: the calling repo)",
    )
    fp.add_argument("--source", required=True)
    fp.set_defaults(func=_core()._cmd_producer_fence)
    fp = fence_sub.add_parser(
        "handoff",
        help="retire generation N and activate N+1 for one selected producer",
    )
    fp.add_argument(
        "--repo",
        help="canonical repo lane (default: the calling repo)",
    )
    fp.add_argument("--source", required=True)
    fp.add_argument("--producer-id", required=True)
    fp.add_argument("--expected-generation", required=True, type=int)
    fp.add_argument(
        "--required-label",
        help="immutable label requirement set on initial scope activation",
    )
    fp.set_defaults(func=_core()._cmd_producer_fence)
