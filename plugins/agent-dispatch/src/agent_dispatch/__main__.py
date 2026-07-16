"""CLI entry point for agent-dispatch.

Two modes:
  * ``agent-dispatch serve`` runs the per-host coordinator (uvicorn).
  * every other subcommand is a thin client that talks to a coordinator
    (``--url`` / ``AGENT_DISPATCH_URL``; ``--token`` / ``AGENT_DISPATCH_TOKEN``).

Output is JSON on stdout so the CLI composes with other tooling.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from . import __version__
from .client import DispatchClient, DispatchError
from .config import Config, client_token, client_url, shared_token, shared_url


def _emit(value: Any) -> int:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _resolve_client_target(args: argparse.Namespace) -> tuple[str, str | None]:
    """Resolve which coordinator (URL + token) a client command targets.

    Precedence:

    1. An explicit ``--url`` (with ``--token``/``AGENT_DISPATCH_TOKEN``) -- the
       operator's direct override, always wins.
    2. ``--shared`` -- route to the **shared/elected coordinator**
       (``AGENT_DISPATCH_SHARED_URL``; facility: the gateway) for cross-machine
       dispatch, authenticated with its own ``AGENT_DISPATCH_SHARED_TOKEN``. If no
       shared coordinator is configured, error loudly rather than silently using
       the local queue (which would strand a cross-machine task on one host).
    3. Otherwise the **local** loopback coordinator -- same-machine work, the
       single-machine default that needs no shared service.
    """
    url = getattr(args, "url", None)
    token = getattr(args, "token", None)
    if url:
        return url, (token or client_token())
    if getattr(args, "shared", False):
        surl = shared_url()
        if not surl:
            print(
                "no shared coordinator configured -- set AGENT_DISPATCH_SHARED_URL "
                "(facility: the gateway endpoint) or pass --url",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return surl, (token or shared_token())
    return client_url(), (token or client_token())


def _client(args: argparse.Namespace) -> DispatchClient:
    url, token = _resolve_client_target(args)
    return DispatchClient(url, token=token)


def _parse_affinity(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        key, _, val = item.partition("=")
        out[key.strip()] = val.strip()
    return out


def _cmd_serve(args: argparse.Namespace) -> int:
    from .config import load_config
    from .server import serve

    base = load_config()
    cfg = Config(
        host=_resolve_serve_host(args, base),
        port=args.port or base.port,
        db_path=args.db or base.db_path,
        token=args.token or base.token,
    )
    serve(cfg)
    return 0


def _resolve_serve_host(args: argparse.Namespace, base: Config) -> str:
    """The host the coordinator binds when ``agent-dispatch serve`` runs.

    Precedence: an explicit ``--host``; then an explicit ``AGENT_DISPATCH_HOST``
    env override (this is how ``serve-service.ps1`` passes the resolved bind
    host); then, on Windows, the topology-derived bind host
    (:func:`netinfo.resolve_bind_host` -- ``127.0.0.1`` on mirrored, the
    ``vEthernet (WSL)`` IP on NAT, never ``0.0.0.0``/LAN); otherwise the local
    default. The Windows host now **owns** the coordinator -- it no longer defers
    to a WSL peer (reverses issue #2777).
    """
    import os

    if args.host:
        return args.host
    if "AGENT_DISPATCH_HOST" in os.environ:
        return base.host
    if sys.platform == "win32":
        from .netinfo import resolve_bind_host

        return resolve_bind_host()
    return base.host


def _cmd_create(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    # Cross-machine dispatch (Phase 8 8a): an embody spawn targeted at *another*
    # machine runs the whole create+embody THERE over the facility SSH mesh, so
    # the task lives on the target's coordinator and the autopilot session runs
    # + completes on the target. agent-dispatch is per-host, so there is no local
    # task in this path.
    from . import remote_dispatch

    if remote_dispatch.is_cross_machine(args):
        return _dispatch_cross_machine(args, repo)
    payload_inline = args.payload_inline
    if args.payload_file:
        payload_inline = _read_payload_file(args.payload_file)
    claim_as = None
    if getattr(args, "claim", False):
        claim_as = _owner_from_identity(args)
        if claim_as is None:
            print(
                "agent-dispatch create --claim: could not resolve this worktree's "
                "identity to claim as; run inside a worktree or pass "
                "--machine/--worktree.",
                file=sys.stderr,
            )
            return 2
    with _client(args) as c:
        task = c.create(
            args.title,
            repo=repo,
            prompt=args.prompt,
            proposed=args.proposed,
            requires=args.require or [],
            excludes=args.exclude or [],
            affinity=_parse_affinity(args.affinity),
            labels=args.label or [],
            payload_ref=args.payload_ref,
            payload_inline=payload_inline,
            target_machine=args.target_machine,
            target_worktree=args.target_worktree,
            target_repo=args.target_repo,
            source=args.source,
            origin_ref=args.origin_ref,
            dedup_key=args.dedup_key,
            not_before=args.not_before,
            claim_as=claim_as,
        )
    if claim_as is not None:
        # Signal whether THIS call won the create-and-claim (mine now) or lost the
        # dedup race (the subject was already taken by someone else).
        won = task.get("owner") == claim_as and task.get("status") == "claimed"
        return _emit(_enrich({**task, "claimed_by_me": won}))
    if args.spawn and not args.proposed:
        _spawn_worker_for(args, task)
    return _emit(_enrich(task))


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
        payload = _read_payload_file(args.payload_file)
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
    try:
        with _client(args) as c:
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

    key = resp["reservation"]["key"]
    spawned = _do_spawn(args, task)
    try:
        with _client(args) as c:
            if spawned is None:
                c.fail_spawn(key, detail="no spawn mechanism available")
            else:
                result, via, handle = spawned
                if result.returncode != 0:
                    c.fail_spawn(key, detail=f"{via} exited {result.returncode}")
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


def _embody_handle(result) -> dict[str, str | None]:
    """Best-effort extract the session/worktree handle from ``embody --json``."""
    from . import embody

    return embody.parse_handle(result)


def _do_spawn(args: argparse.Namespace, task: dict):
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
    coordinator_url = _resolve_client_target(args)[0]

    if backend == "embody":
        from . import embody

        if embody.embody_available():
            worker_id = f"embody-{uuid.uuid4().hex[:8]}"
            try:
                result = embody.spawn_embodied_worker(
                    task["id"],
                    coordinator_url=coordinator_url,
                    worker_id=worker_id,
                    verify_timeout=getattr(args, "verify_timeout", 0) or 0,
                )
            except embody.EmbodyUnavailable as exc:
                print(
                    f"agent-dispatch: --spawn (embody) skipped ({exc}); task "
                    f"{task['id']} left queued for any worker to claim",
                    file=sys.stderr,
                )
                return None
            _report_spawn_result(result, task["id"], "agent-worktrees embody")
            return result, "agent-worktrees embody", _embody_handle(result)
        # Graceful degrade: no agent-worktrees -> try the headless bridge path.
        print(
            "agent-dispatch: embody backend unavailable (agent-worktrees not on "
            "PATH); falling back to the bridge backend",
            file=sys.stderr,
        )

    from . import bridge

    worker_id = f"spawn-{uuid.uuid4().hex[:8]}"
    try:
        result = bridge.spawn_worker(
            task["id"],
            agent=args.spawn_agent,
            coordinator_url=coordinator_url,
            worker_id=worker_id,
            wait=not args.run_async,
        )
    except bridge.BridgeUnavailable as exc:
        print(
            f"agent-dispatch: --spawn skipped ({exc}); task {task['id']} left queued "
            "for any worker to claim",
            file=sys.stderr,
        )
        return None
    _report_spawn_result(result, task["id"], "agent-bridge")
    return result, "agent-bridge", {"session": worker_id, "worktree": None}


def _report_spawn_result(result, task_id: str, via: str) -> None:
    """Print a warning if a best-effort spawn subprocess reported failure."""
    if result.returncode != 0:
        print(
            f"agent-dispatch: spawn via {via} failed (exit {result.returncode}); "
            f"task {task_id} remains queued. stderr: {result.stderr.strip()[:400]}",
            file=sys.stderr,
        )


def _identity(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """(machine, worktree): explicit flags override the agent-worktrees resolution."""
    machine = getattr(args, "machine", None)
    worktree = getattr(args, "worktree", None)
    if machine is None or worktree is None:
        from .identity import resolve_identity

        r_machine, r_worktree = resolve_identity()
        machine = machine or r_machine
        worktree = worktree or r_worktree
    return (machine, worktree)


_REPO_UNRESOLVED = (
    "agent-dispatch: could not resolve the calling repo (lane). Run inside a repo/"
    "worktree, or pass --repo <name|remote>. Tasks are scoped per repo, so a lane "
    "is required."
)


def _scope_repo(args: argparse.Namespace) -> str | None:
    """Resolve the lane for this command: an explicit ``--repo`` (a local repo
    name or a remote URL) wins; otherwise the calling repo, resolved from the
    CWD. Returns a canonical remote, or ``None`` if nothing resolves.
    """
    from .identity import resolve_repo, resolve_repo_selector

    selector = getattr(args, "repo", None)
    return resolve_repo_selector(selector) if selector else resolve_repo()


def _enrich(result: Any) -> Any:
    """Annotate task dict(s) with a display-only ``repo_name`` (the local name
    for the canonical ``repo`` remote, when the registry knows it), and parse the
    stored ``latest_progress`` JSON string into an object for clean at-a-glance
    output."""
    from .identity import name_for_repo

    def one(d: Any) -> Any:
        if not isinstance(d, dict):
            return d
        if "repo" in d and "repo_name" not in d:
            name = name_for_repo(d.get("repo"))
            if name:
                d = {**d, "repo_name": name}
        lp = d.get("latest_progress")
        if isinstance(lp, str) and lp:
            try:
                d = {**d, "latest_progress": json.loads(lp)}
            except (ValueError, TypeError):
                pass
        return d

    if isinstance(result, list):
        return [one(x) for x in result]
    if isinstance(result, dict) and any(k in result for k in ("assigned", "owned")):
        return {k: (_enrich(v) if isinstance(v, list) else v) for k, v in result.items()}
    return one(result)


def _cmd_claim(args: argparse.Namespace) -> int:
    machine, worktree = _identity(args)
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        task = c.claim(
            worker_id=args.worker_id,
            capabilities=args.capability or [],
            repo=repo,
            machine=machine,
            worktree=worktree,
            task_id=args.task,
            lease_seconds=args.lease_seconds,
            evaluation=getattr(args, "evaluation", False),
        )
    if task is None:
        print("no claimable task", file=sys.stderr)
        return 3
    return _emit(_enrich(task))


def _cmd_worktree_status(args: argparse.Namespace) -> int:
    machine, worktree = _identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve worktree identity — pass --machine and --worktree "
            "(agent-worktrees not found, or not inside a worktree)",
            file=sys.stderr,
        )
        return 2
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        inbox = c.mine(machine, worktree, repo=repo)
    return _emit(_enrich({"machine": machine, "worktree": worktree, "repo": repo, **inbox}))


def _cmd_show(args: argparse.Namespace) -> int:
    with _client(args) as c:
        task = c.get(args.task_id)
    from . import tracking

    return _emit(tracking.enrich_task(_enrich(task)))


def _simple(method: str, *arg_names: str):
    """Build a handler that forwards positional args to a client method."""

    def handler(args: argparse.Namespace) -> int:
        with _client(args) as c:
            result = getattr(c, method)(*[getattr(args, n) for n in arg_names])
        return _emit(result)

    return handler


def _cmd_yield(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="yield")
    if worker_id is None:
        return 2
    exclude = args.exclude
    if not exclude and getattr(args, "exclude_self", None):
        machine, worktree = _identity(args)
        if args.exclude_self == "worktree" and worktree:
            exclude = f"worktree:{worktree}"
        elif args.exclude_self == "machine" and machine:
            exclude = f"machine:{machine}"
    with _client(args) as c:
        return _emit(c.yield_task(args.task_id, worker_id, note=args.note, exclude=exclude))


def _owner_from_identity(args: argparse.Namespace) -> str | None:
    """Compose the canonical ``machine/worktree`` owner from the CWD identity.

    Mirrors the coordinator's ``worker_id_for`` so a worker can address its own
    task without typing its owner: ``complete <id>`` (no owner) resolves the same
    ``machine/worktree`` pair it claimed under. Returns None when identity can't
    be resolved (no agent-worktrees, outside a worktree).
    """
    machine, worktree = _identity(args)
    if machine and worktree:
        return f"{machine}/{worktree}"
    return None


def _resolve_owner(args: argparse.Namespace, *, verb: str) -> str | None:
    """Resolve the acting worker's owner for a lease-holding verb.

    Prefers an explicit positional ``worker_id``; otherwise composes
    ``machine/worktree`` from the CWD identity -- the symmetry that lets an
    embodied/taken-over worker drive its whole lifecycle
    (``claim``/``start``/``complete``/``yield``) under its **worktree identity**
    without typing an owner, so the task's owner stays ``machine/worktree`` and
    live-session tracking can join it (see :mod:`tracking`). Prints guidance and
    returns None when neither is available.
    """
    worker_id = getattr(args, "worker_id", None) or _owner_from_identity(args)
    if not worker_id:
        print(
            f"agent-dispatch: could not resolve the owner for {verb}. Pass the "
            f"owner positionally (`{verb} <id> <owner>`) or run inside the "
            "owning worktree so machine/worktree resolves.",
            file=sys.stderr,
        )
    return worker_id


def _cmd_start(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="start")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.start(args.task_id, worker_id))


def _cmd_progress(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="progress")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(
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
        return _emit(rows)

    machine, worktree = _identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve this worktree's identity — run "
            "inside a worktree, or pass --machine and --worktree.",
            file=sys.stderr,
        )
        return 2

    if not args.focus_text:
        # Show this worktree's current focus (its status-core summary).
        mine = [w for w in aw_list_records(machine=machine)
                if w.get("id") == worktree]
        return _emit(_focus_row(mine[0]) if mine and (mine[0].get("summary") or "").strip()
                     else {})

    # Write-through to the status core (never a parallel store). The write
    # always targets the CWD worktree via the `agent-worktrees status` verb.
    if not aw_set_summary(args.focus_text):
        print(
            "agent-dispatch: focus write-through failed (agent-worktrees status "
            "unavailable, or not inside a worktree).",
            file=sys.stderr,
        )
        return 2
    return _emit({
        "machine": machine, "worktree": worktree,
        "focus": args.focus_text.strip(),
    })


def _cmd_complete(args: argparse.Namespace) -> int:
    # Owner is optional: a worker that claimed under its CWD identity can
    # complete with just the task id -- we resolve the same machine/worktree
    # owner. This is what lets a taken-over successor finish a handoff task with
    # one clean command (`agent-dispatch complete <id>`) once the goal is met.
    worker_id = _resolve_owner(args, verb="complete")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.complete(args.task_id, worker_id, result_ref=args.result_ref))


def _cmd_abandon(args: argparse.Namespace) -> int:
    permitted = args.permit
    reason = args.reason
    duplicate_of = getattr(args, "duplicate_of", None)
    if duplicate_of:
        # A duplicate is self-justifying: retiring it is permitted, and the
        # dedup reference is folded into the reason so it lands in the audit
        # trail (never a silent drop).
        permitted = True
        dedup_note = f"duplicate of {duplicate_of}"
        reason = f"{reason}; {dedup_note}" if reason else dedup_note
    with _client(args) as c:
        return _emit(
            c.abandon(
                args.task_id, worker_id=args.worker_id, permitted=permitted, reason=reason
            )
        )


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
            f"agent-dispatch: peer-queue browse of {args.machine!r} unavailable "
            f"({exc})",
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
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    from . import remote_dispatch

    if remote_dispatch.is_peer_machine(getattr(args, "machine", None)):
        return _browse_peer(args, "list", repo=repo)
    with _client(args) as c:
        tasks = c.list(
            repo=repo,
            status=args.status,
            target_machine=args.target_machine,
            target_repo=args.target_repo,
            label=args.label,
            limit=args.limit,
        )
    from . import tracking

    return _emit(tracking.enrich_tasks(_enrich(tasks)))


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
        return _browse_peer(args, "inbox")
    machine = args.machine
    if not machine:
        from .identity import resolve_identity

        machine = resolve_identity()[0]
    if not machine:
        print(
            "agent-dispatch: could not resolve this machine — pass --machine "
            "(agent-worktrees not found, or not inside a worktree)",
            file=sys.stderr,
        )
        return 2
    with _client(args) as c:
        tasks = c.list(repo=None, status=args.status, label=args.label, limit=args.limit)
    from .queue import machine_matches

    inbox = [t for t in tasks if machine_matches(t.get("target_machine"), machine)]
    return _emit(_enrich(inbox))


def _cmd_find(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        return _emit(_enrich(c.find(args.query, repo=repo, limit=args.limit)))


def _cmd_sweep(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        return _emit(_enrich(c.sweep(repo=repo, limit=args.limit)))


def _cmd_watch(args: argparse.Namespace) -> int:
    with _client(args) as c:
        try:
            for event in c.stream_events():
                json.dump(event, sys.stdout)
                sys.stdout.write("\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            return 0
    return 0


def _cmd_payload(args: argparse.Namespace) -> int:
    with _client(args) as c:
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
    return _emit(result)


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

    Transitions are best-effort and idempotent: an already-advanced or
    already-terminal task just prints its payload (never an error), and a task
    the caller can't take ownership of is still read and printed.
    """
    task_id = args.task_id
    defer = getattr(args, "defer_complete", False)
    machine, worktree = _identity(args)
    try:
        repo = _scope_repo(args)
    except Exception:  # lane resolution is best-effort here -- still print payload
        repo = None
    with _client(args) as c:
        try:
            task = c.get(task_id)
        except DispatchError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 1
        status = task.get("status")
        if status not in ("completed", "abandoned"):
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
            elif status in ("claimed", "started"):
                owner = task.get("owner")
            if owner:
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


def _cmd_schedule(args: argparse.Namespace) -> int:
    from .producers import schedule

    if args.schedule_command == "serve":
        _url, _token = _resolve_client_target(args)
        schedule.serve(
            args.spec,
            url=_url,
            token=_token,
            interval=args.interval,
        )
        return 0
    spec = schedule.load_spec(args.spec)
    with _client(args) as c:
        result = schedule.run_tick(c, spec)
    return _emit({
        "created": [_enrich(t) for t in result["created"]],
        "errors": result["errors"],
    })


def _cmd_webhook(args: argparse.Namespace) -> int:
    from .producers import webhook

    config = webhook.load_config(args.config) if args.config else {}
    if args.url:
        config["url"] = args.url
    if args.token:
        config["coordinator_token"] = args.token
    webhook.serve(config, host=args.host, port=args.port)
    return 0


def _cmd_supervise(args: argparse.Namespace) -> int:
    """Run the embody spawn supervisor over the lane (once, or as a loop).

    Turns queued (optionally label-gated) tasks into host embody autopilots,
    exactly once each, via the atomic spawn reservation. See the ``supervisor``
    module for the spawn-at-most-once safety model.
    """
    from .supervisor import Supervisor, make_embody_spawn

    repo = None if getattr(args, "all_repos", False) else _scope_repo(args)
    coordinator_url = _resolve_client_target(args)[0]
    pool = [h for h in (getattr(args, "pool", "") or "").split(",") if h.strip()]
    capacity_gate = None
    if pool:
        from . import remote_dispatch
        from .fleet import FleetSpawner

        origin = getattr(args, "origin", None) or remote_dispatch.local_machine()
        if not origin:
            print(
                "agent-dispatch supervise --pool: could not resolve this machine's "
                "alias for fleet bodies to report back to; pass --origin <alias>.",
                file=sys.stderr,
            )
            return 2
        fleet = FleetSpawner(
            pool,
            origin=origin,
            verify_timeout=getattr(args, "verify_timeout", 0) or 0,
        )
        spawn_fn = fleet
        capacity_gate = fleet.can_spawn
        print(
            f"agent-dispatch supervise: fleet mode -- pool={','.join(fleet.pool)} "
            f"origin={origin}",
            file=sys.stderr,
        )
    else:
        spawn_fn = make_embody_spawn(
            coordinator_url, verify_timeout=getattr(args, "verify_timeout", 0) or 0
        )
    with _client(args) as c:
        sup = Supervisor(
            c,
            spawn_fn=spawn_fn,
            repo=repo,
            labels=args.label or None,
            max_concurrent=args.max_concurrent,
            max_attempts=args.max_attempts,
            heartbeat=not args.no_heartbeat,
            capacity_gate=capacity_gate,
        )
        if args.once:
            return _emit({"spawned": sup.poll_once()})

        def _on_cycle(spawned: list[str]) -> None:
            if spawned:
                print(
                    f"agent-dispatch supervise: spawned {len(spawned)} task(s): "
                    f"{', '.join(spawned)}",
                    file=sys.stderr,
                )

        sup.serve(interval=args.interval, on_cycle=_on_cycle)
    return 0


def _cmd_reservations(args: argparse.Namespace) -> int:
    """Operator visibility + manual control over spawn reservations."""
    with _client(args) as c:
        if args.reservations_command == "list":
            rows = c.list_reservations(
                task_id=args.task, state=args.state, limit=args.limit
            )
            return _emit(rows)
        if args.reservations_command == "fail":
            return _emit(c.fail_spawn(args.key, detail=args.detail))
        if args.reservations_command == "settle":
            return _emit(c.settle_spawn(args.key, detail=args.detail))
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-dispatch", description="Agent task queue + coordinator"
    )
    parser.add_argument("--version", action="version", version=f"agent-dispatch {__version__}")
    parser.add_argument(
        "--url", help="coordinator base URL (default: AGENT_DISPATCH_URL or config)"
    )
    parser.add_argument("--token", help="bearer token (default: AGENT_DISPATCH_TOKEN)")
    parser.add_argument(
        "--shared", action="store_true",
        help="target the SHARED/elected coordinator (AGENT_DISPATCH_SHARED_URL; "
             "facility: the gateway) for cross-machine dispatch, instead of this "
             "host's local coordinator. Authenticated with AGENT_DISPATCH_SHARED_TOKEN.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the per-host coordinator")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--db")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser(
        "create",
        help="enqueue a task (write a self-contained title + --prompt so a "
             "producer sweeping existing tasks can judge duplication)",
    )
    p.add_argument("title", help="short, specific, self-contained summary of the work")
    p.add_argument(
        "--prompt", default="",
        help="the task instruction -- describe the work fully enough to dedup "
             "against and to execute without extra context",
    )
    p.add_argument(
        "--repo",
        help="lane (repo) this task belongs to: a local repo name or a remote "
             "URL. Default: the calling repo resolved from the CWD. Tasks stay "
             "in their producing repo's lane -- for a cross-repo *code* target "
             "use --target-repo and let the lane agent do it via working-cross-repo.",
    )
    p.add_argument("--proposed", action="store_true", help="create as an unclaimable draft")
    p.add_argument(
        "--claim", action="store_true",
        help="atomically create-AND-claim as this worktree (no queued gap). With "
             "--dedup-key <subject>, this is the lazy open-ended-pickup primitive: "
             "either mint the subject as mine, or (on a dedup collision) get back "
             "the row someone else already took -- see 'claimed_by_me' in the "
             "output to tell which.",
    )
    p.add_argument(
        "--require", action="append", help="hard capability/identity token (repeatable)"
    )
    p.add_argument(
        "--exclude", action="append",
        help="hard EXCLUSION token -- a worker whose capabilities/identity match "
             "any exclude is ineligible (anti-affinity; repeatable). E.g. "
             "'machine:lambda-core', 'worktree:foo', 'agent:reviewer'.",
    )
    p.add_argument("--affinity", action="append", help="soft preference key=value (repeatable)")
    p.add_argument("--label", action="append", help="free-form label (repeatable)")
    p.add_argument("--payload-ref")
    p.add_argument("--payload-inline")
    p.add_argument(
        "--payload-file",
        help="read the payload from a file (large payloads spill to a blob "
             "automatically); '-' reads from stdin",
    )
    p.add_argument(
        "--target-machine",
        help="route the task to this machine. With `--spawn --spawn-backend "
             "embody` for another machine, dispatch runs there over the facility "
             "SSH mesh (Phase 8: create+embody land on the target's coordinator).",
    )
    p.add_argument("--target-worktree")
    p.add_argument("--target-repo")
    p.add_argument("--source")
    p.add_argument("--origin-ref")
    p.add_argument("--dedup-key")
    p.add_argument("--not-before", type=float, default=0.0)
    p.add_argument(
        "--spawn", action="store_true",
        help="after creating, spawn a worker to execute it (best effort)",
    )
    p.add_argument(
        "--spawn-backend", choices=["bridge", "embody"], default="bridge",
        help="how to embody the spawned worker: 'embody' = a CLI-backed "
             "autopilot session in a fresh parallel worktree (agent-worktrees "
             "embody -- the 'dispatch an agent to do X' path); 'bridge' "
             "(default) = a headless agent-bridge ACP worker",
    )
    p.add_argument(
        "--spawn-agent", default="task-worker",
        help="agent-bridge agent name to spawn (bridge backend only; "
             "default: task-worker)",
    )
    p.add_argument(
        "--verify-timeout", type=int, default=0,
        help="embody backend: wait up to N seconds for the spawned mux session "
             "to come up before returning (default 0: don't wait)",
    )
    p.add_argument(
        "--async", dest="run_async", action="store_true",
        help="with --spawn, don't wait for the worker (fire-and-forget)",
    )
    p.set_defaults(func=_cmd_create)

    p = sub.add_parser("approve", help="move a proposed task to queued")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("approve", "task_id"))

    p = sub.add_parser(
        "claim", help="atomically lease one eligible task (identity auto-resolved from CWD)"
    )
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.add_argument("--capability", action="append", help="advertised capability (repeatable)")
    p.add_argument("--task", help="claim this specific task id (if eligible)")
    p.add_argument(
        "--repo",
        help="lane to claim from (local name or remote URL). Default: the calling "
             "repo. A worker only claims tasks in its own repo's lane.",
    )
    p.add_argument("--lease-seconds", type=int)
    p.add_argument(
        "--evaluation", action="store_true",
        help="claim under the tight EVALUATION lease (a quick accept/reject "
             "window): a stuck evaluator auto-releases fast, and 'start' then "
             "extends to the full work lease on commit. Decline with "
             "'yield --exclude-self' or 'abandon --duplicate-of'.",
    )
    p.set_defaults(func=_cmd_claim)

    p = sub.add_parser(
        "worktree-status",
        help="this worktree's inbox: tasks assigned to + owned by it (identity auto-resolved)",
    )
    p.add_argument("--machine", help="override the resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.add_argument(
        "--repo",
        help="lane to scope the inbox to (local name or remote URL). Default: the calling repo.",
    )
    p.set_defaults(func=_cmd_worktree_status)

    p = sub.add_parser(
        "start", help="mark a claimed task started (identity auto-resolved from CWD)"
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_cmd_start)

    p = sub.add_parser(
        "yield",
        help="return a held task to queued (with a note; identity auto-resolved)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--note")
    p.add_argument(
        "--exclude-self", "--not-me", choices=("worktree", "machine"), dest="exclude_self",
        help="append a scoped self-EXCLUSION when yielding, so this same "
             "candidate isn't re-offered the task: 'worktree' (narrowest -- this "
             "worktree only) or 'machine' (this whole machine). Prefer the "
             "narrowest scope that is true. (`--not-me` is a deprecated alias.)",
    )
    p.add_argument(
        "--exclude",
        help="append an explicit exclusion token when yielding (e.g. "
             "'agent:reviewer'); overrides --exclude-self.",
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_cmd_yield)

    p = sub.add_parser("complete", help="mark a started task completed")
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?",
        help="owner id (default: the machine/worktree resolved from CWD, so a "
             "worker can `complete <id>` without typing its own owner)",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.add_argument("--result-ref")
    p.set_defaults(func=_cmd_complete)

    p = sub.add_parser(
        "abandon",
        help="terminally abandon a task (requires --permit or --duplicate-of)",
    )
    p.add_argument("task_id")
    p.add_argument("--worker-id")
    p.add_argument("--permit", action="store_true", help="assert abandonment is permitted")
    p.add_argument("--reason")
    p.add_argument(
        "--duplicate-of", dest="duplicate_of", metavar="REF",
        help="retire the task as a DUPLICATE of REF (an existing task id, PR, or "
             "issue). Self-justifying: implies --permit and records the dedup "
             "reference in the reason, so the decision is never a silent drop.",
    )
    p.set_defaults(func=_cmd_abandon)

    p = sub.add_parser("heartbeat", help="extend the lease on a held task")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.set_defaults(func=_simple("heartbeat", "task_id", "worker_id"))

    p = sub.add_parser(
        "progress",
        help="record a brief progress beat toward the goal (also heartbeats the "
             "lease; identity auto-resolved from CWD)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument(
        "--phase", default="",
        help="short phase label (e.g. 'planning', 'implementing', 'PR open')",
    )
    p.add_argument(
        "--summary", required=True,
        help="one-line status toward the goal (hard-capped; keep it a line, not a "
             "transcript)",
    )
    p.add_argument("--blocker", help="a real blocker holding progress, if any")
    p.add_argument("--pr", help="the PR/ref this beat corresponds to, if any")
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_cmd_progress)

    p = sub.add_parser(
        "focus",
        help="set/show this worktree's current focus (its status-core summary "
             "on the worktree record); identity auto-resolved from CWD",
    )
    p.add_argument(
        "focus_text", nargs="?",
        help="one-line focus for this worktree; omit to show the current focus",
    )
    p.add_argument("--list", action="store_true", help="list every worktree's focus")
    p.add_argument("--machine", help="filter --list to a machine / override resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.set_defaults(func=_cmd_focus)

    p = sub.add_parser("detach", help="demote a hard worktree pin to a soft affinity")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("detach", "task_id"))

    p = sub.add_parser("list", help="list tasks (scoped to the calling repo by default)")
    p.add_argument("--repo", help="lane to list (local name or remote URL); default: calling repo")
    p.add_argument(
        "--status",
        help="filter by status; comma-separate for several (e.g. queued,started)",
    )
    p.add_argument("--target-machine")
    p.add_argument("--target-repo")
    p.add_argument("--label")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument(
        "--machine",
        help="read another machine's queue over the SSH mesh (peer browse); "
             "default: this machine's local coordinator",
    )
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser(
        "inbox",
        help="machine-scoped, cross-lane pickable tasks (default: proposed) -- "
             "what this machine can start, across every repo lane",
    )
    p.add_argument(
        "--machine",
        help="machine to scope to; a *remote* machine reads that peer's queue "
             "over the SSH mesh (default: this machine, resolved via agent-worktrees)",
    )
    p.add_argument(
        "--status",
        default="proposed",
        help="status filter; comma-separate for several (default: proposed)",
    )
    p.add_argument("--label")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=_cmd_inbox)

    p = sub.add_parser(
        "find", help="substring search over title/prompt (a quick dedup probe; calling repo)"
    )
    p.add_argument("query")
    p.add_argument(
        "--repo", help="lane to search (local name or remote URL); default: calling repo"
    )
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=_cmd_find)

    p = sub.add_parser(
        "sweep",
        help="the dedup corpus for the calling repo: every non-abandoned task, "
             "newest first -- read these before creating a task to verify the "
             "work doesn't already exist",
    )
    p.add_argument(
        "--repo", help="lane to sweep (local name or remote URL); default: calling repo"
    )
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=_cmd_sweep)

    p = sub.add_parser("show", help="show one task")
    p.add_argument("task_id")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("events", help="show a task's audit trail")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("events", "task_id"))

    p = sub.add_parser("payload", help="show a task's resolved payload (inline or blob)")
    p.add_argument("task_id")
    p.add_argument(
        "--raw", action="store_true", help="print the payload content only (not JSON)"
    )
    p.set_defaults(func=_cmd_payload)

    p = sub.add_parser(
        "consume",
        help="resume-and-consume a handoff: drive it to completed (idempotent) "
        "and print its payload -- the successor's one-command pickup",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--worker-id", dest="worker_id",
        help="owner id (default: from machine/worktree)",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.add_argument(
        "--repo",
        help="lane to consume from (local name or remote URL). Default: the calling repo.",
    )
    p.add_argument("--result-ref", help="result ref recorded on completion")
    p.add_argument(
        "--defer-complete", action="store_true",
        help="takeover pickup: approve->claim->start + print the brief, but do "
             "NOT complete -- the successor completes explicitly when the goal "
             "is reached (deferred completion)",
    )
    p.set_defaults(func=_cmd_consume)

    p = sub.add_parser("recover", help="requeue expired-lease tasks")
    p.set_defaults(func=lambda args: _emit(_client(args).recover()))

    p = sub.add_parser("watch", help="stream task events (SSE) as JSON lines")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser(
        "mcp", help="run the local stdio MCP server (per-agent interaction layer)"
    )
    p.set_defaults(func=_cmd_mcp)

    p = sub.add_parser(
        "schedule",
        help="scheduler/timer producer: turn a JSON schedule spec into deferred "
             "tasks (idempotent per occurrence via not_before + dedup_key)",
    )
    sched_sub = p.add_subparsers(dest="schedule_command", required=True)
    sp = sched_sub.add_parser(
        "tick",
        help="create every currently-due occurrence once, then exit (drive from "
             "cron / a systemd timer / manage_schedule)",
    )
    sp.add_argument("spec", help="path to the JSON schedule spec")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "serve", help="built-in timer: reload the spec and tick every --interval seconds"
    )
    sp.add_argument("spec", help="path to the JSON schedule spec")
    sp.add_argument(
        "--interval", type=float, default=60.0, help="seconds between ticks (default: 60)"
    )
    sp.set_defaults(func=_cmd_schedule)

    p = sub.add_parser(
        "webhook",
        help="reactive producer: serve an HTTP app mapping git-forge PR-merge "
             "and telemetry events onto tasks",
    )
    p.add_argument("--config", help="path to the JSON webhook config (optional)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=9331, help="bind port (default: 9331)")
    p.set_defaults(func=_cmd_webhook)

    p = sub.add_parser(
        "supervise",
        help="embody spawn supervisor: turn queued (label-gated) tasks into host "
             "embody autopilots, exactly once each, via atomic spawn reservations",
    )
    p.add_argument("--repo", help="lane to supervise (default: the calling repo)")
    p.add_argument(
        "--all-repos", action="store_true", help="supervise every lane (no repo scope)"
    )
    p.add_argument(
        "--label", action="append",
        help="only spawn queued tasks carrying this label (repeatable; opt-in gate)",
    )
    p.add_argument(
        "--max-concurrent", type=int, default=1,
        help="cap on in-flight spawns (default: 1)",
    )
    p.add_argument(
        "--max-attempts", type=int, default=3,
        help="dead-letter a task after this many failed spawn attempts "
             "(default: 3; 0 = retry forever)",
    )
    p.add_argument(
        "--no-heartbeat", action="store_true",
        help="don't hold the lease of confirmed-alive embodied workers "
             "(default: heartbeat live workers so a quiet-but-alive session's "
             "lease doesn't expire)",
    )
    p.add_argument(
        "--verify-timeout", type=int, default=0,
        help="embody: wait up to N seconds for the spawned session (0 = don't wait)",
    )
    p.add_argument(
        "--interval", type=float, default=30.0,
        help="serve loop poll interval in seconds (default: 30)",
    )
    p.add_argument(
        "--once", action="store_true", help="run a single supervision cycle and exit"
    )
    p.add_argument(
        "--pool",
        help="fleet mode: comma-separated host aliases to dispatch embody bodies "
             "to (first live host wins). Omit for local spawn on this machine.",
    )
    p.add_argument(
        "--origin",
        help="fleet mode: this coordinator's own SSH alias, which dispatched "
             "bodies report their lease back to (default: the resolved local "
             "machine). Required when the local machine can't be resolved.",
    )
    p.set_defaults(func=_cmd_supervise)

    p = sub.add_parser(
        "reservations", help="inspect / manually control spawn reservations"
    )
    res_sub = p.add_subparsers(dest="reservations_command", required=True)
    rp = res_sub.add_parser("list", help="list spawn reservations")
    rp.add_argument("--task", help="filter by task id")
    rp.add_argument("--state", help="filter by state (comma-list ok)")
    rp.add_argument("--limit", type=int, default=200)
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser(
        "fail", help="mark a reservation failed (releases the task for a fresh attempt)"
    )
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser("settle", help="mark a reservation settled (attempt over)")
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)

    p = sub.add_parser("health", help="check coordinator health")
    p.set_defaults(func=lambda args: _emit(_client(args).health()))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DispatchError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as exc:
        print(f"agent-dispatch: cannot reach coordinator: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
