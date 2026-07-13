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
from .config import Config, client_token, client_url


def _emit(value: Any) -> int:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _client(args: argparse.Namespace) -> DispatchClient:
    return DispatchClient(args.url or client_url(), token=args.token or client_token())


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
        host=args.host or base.host,
        port=args.port or base.port,
        db_path=args.db or base.db_path,
        token=args.token or base.token,
    )
    serve(cfg)
    return 0


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
    with _client(args) as c:
        task = c.create(
            args.title,
            repo=repo,
            prompt=args.prompt,
            proposed=args.proposed,
            requires=args.require or [],
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
        )
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
        print(
            f"agent-dispatch: remote dispatch on {args.target_machine!r} failed "
            f"(exit {result.returncode}):\n{result.stderr}",
            file=sys.stderr,
        )
        return result.returncode
    return 0


def _spawn_worker_for(args: argparse.Namespace, task: dict) -> None:
    """Spawn a worker for a freshly created task (best effort).

    Two backends select *how* the worker is embodied:

    - ``embody`` -- a **CLI-backed autopilot** session in a fresh parallel
      worktree via ``agent-worktrees embody`` (the "dispatch an agent to do X"
      path: a durable, NF-viewable session that works the task to explicit
      completion). Falls back to the bridge backend if agent-worktrees is
      absent.
    - ``bridge`` (default) -- a **headless** agent-bridge ACP worker.

    Either way, if no spawn mechanism is available the task is simply left
    queued for any worker to claim -- never a hard failure.
    """
    backend = getattr(args, "spawn_backend", "bridge")
    coordinator_url = args.url or client_url()

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
                return
            _report_spawn_result(result, task["id"], "agent-worktrees embody")
            return
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
        return
    _report_spawn_result(result, task["id"], "agent-bridge")


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
    with _client(args) as c:
        return _emit(c.yield_task(args.task_id, worker_id, note=args.note))


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
    if args.list:
        with _client(args) as c:
            return _emit(c.list_focus(machine=args.machine))
    machine, worktree = _identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve this worktree's identity — run "
            "inside a worktree, or pass --machine and --worktree.",
            file=sys.stderr,
        )
        return 2
    if not args.focus_text:
        with _client(args) as c:
            mine = [
                f for f in c.list_focus(machine=machine)
                if f.get("worktree") == worktree
            ]
        return _emit(mine[0] if mine else {})
    with _client(args) as c:
        return _emit(c.set_focus(machine, worktree, args.focus_text))


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
    with _client(args) as c:
        return _emit(
            c.abandon(
                args.task_id, worker_id=args.worker_id, permitted=args.permit, reason=args.reason
            )
        )


def _cmd_list(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
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
    """
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
    inbox = [t for t in tasks if t.get("target_machine") in (None, machine)]
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
        schedule.serve(
            args.spec,
            url=args.url or client_url(),
            token=args.token or client_token(),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-dispatch", description="Agent task queue + coordinator"
    )
    parser.add_argument("--version", action="version", version=f"agent-dispatch {__version__}")
    parser.add_argument(
        "--url", help="coordinator base URL (default: AGENT_DISPATCH_URL or config)"
    )
    parser.add_argument("--token", help="bearer token (default: AGENT_DISPATCH_TOKEN)")
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
        "--require", action="append", help="hard capability/identity token (repeatable)"
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

    p = sub.add_parser("abandon", help="terminally abandon a task (requires --permit)")
    p.add_argument("task_id")
    p.add_argument("--worker-id")
    p.add_argument("--permit", action="store_true", help="assert abandonment is permitted")
    p.add_argument("--reason")
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
        help="set/show this worktree's current focus (cockpit fleet legibility); "
             "identity auto-resolved from CWD",
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
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser(
        "inbox",
        help="machine-scoped, cross-lane pickable tasks (default: proposed) -- "
             "what this machine can start, across every repo lane",
    )
    p.add_argument(
        "--machine",
        help="machine to scope to (default: this machine, resolved via agent-worktrees)",
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
