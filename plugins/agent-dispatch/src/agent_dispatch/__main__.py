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
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from . import __version__
from .client import DispatchClient, DispatchError
from .config import Config as Config  # noqa: F401 -- compatibility export for callers/tests
from .config import producer_capability as producer_capability_value  # noqa: F401 -- compatibility export
from .config import (
    client_control_token,
    client_token,
    client_url,
    failover_machine,
    has_live_local_coordinator,
    shared_control_token,
    shared_token,
    shared_url,
)
# Re-exported for backward compatibility: repository-issue-loop commands
# live in loop_commands.py, reviewer-loop ones in reviewer_loop_commands.py;
# build_parser/tests below still reference them by this attribute path.
from .loop_commands import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_repository_issue_loop,
    _repository_issue_loop_declarations,
    _repository_issue_loop_health_path,
    _repository_issue_loop_registrations,
    _repository_issue_loop_setup,
    _repository_issue_loop_status,
    _spawn_attempt_projection,
)
from .reviewer_loop_commands import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_reviewer_loop,
    _reviewer_loop_declarations,
    _reviewer_loop_registrations,
    _reviewer_loop_setup,
    _reviewer_loop_status,
)
from .producers_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_emitter,
    _cmd_reservations,
    _cmd_schedule,
    _cmd_webhook,
    _parse_label_max_attempts,
    register_producer_commands,
    register_reservations_command,
    register_webhook_command,
)

# Re-exported for backward compatibility: the recipes-family CLI commands
# live in recipes_cli.py now (see that module's docstring), but
# build_parser's set_defaults(), _DashDashParser, and tests below still
# reference them by their agent_dispatch.__main__ attribute path.
from .recipes_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_recipes_describe,
    _cmd_recipes_drive,
    _cmd_recipes_kick,
    _cmd_recipes_list,
    _cmd_recipes_render,
    _parse_recipe_params,
    _recipe_create_namespace,
    _recipe_dedup_key,
    _recipe_param_dicts,
)
from .registrations import RegistrationKind

# Re-exported for backward compatibility: the supervise-family CLI commands
# live in supervise_cli.py now (see that module's docstring), but
# build_parser's set_defaults() and tests below still reference them by
# their agent_dispatch.__main__ attribute path.
from .supervise_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _build_registration_spec,
    _cmd_supervise,
    _read_supervisor_runtime_status,
    _registration_scope,
    _spawn_supervisor_daemon_detached,
    _supervisor_runtime_status_path,
)
from .coordinator_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _add_cutover_flags,
    _cmd_cutover,
    _cmd_federation_run,
    _cmd_federation_status,
    _cmd_health,
    _cmd_installer_readiness,
    _cmd_print_endpoint,
    _cmd_retire_supervisors,
    _cmd_serve,
    _federation_rendezvous,
    _reap_abandoned_passive,
    _reap_superseded_coordinators,
    _reroot_serve_cwd,
    _resolve_bind_host_resilient,
    _resolve_serve_host,
    register_coordinator_commands,
)
from .create_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_create,
    _cmd_producer_fence,
    _cmd_propose,
    _create_args_parent,
    _dispatch_cross_machine,
    _do_spawn,
    _embody_handle,
    _read_payload_file,
    _release_failed_created_spawn,
    _report_spawn_result,
    _spawn_route,
    _spawn_worker_for,
    register_create_commands,
)

from .task_query_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _BOARD_ACTIVITY_TTL_SECONDS,
    _BOARD_GROUPS,
    _BOARD_TERMINAL,
    _board_activity,
    _board_group,
    _board_keep,
    _board_sort_key,
    _browse_peer,
    _cmd_consume,
    _cmd_doctor,
    _cmd_find,
    _cmd_inbox,
    _cmd_list,
    _cmd_mcp,
    _cmd_payload,
    _cmd_result,
    _cmd_sweep,
    _cmd_watch,
    _consume_already_spent,
)

from .steering_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_card_draft_clear,
    _cmd_card_draft_save,
    _cmd_card_set,
    _cmd_card_show,
    _cmd_steer,
    _cmd_steer_take,
    register_steering_commands,
)

if TYPE_CHECKING:
    from .registrar import ProfileDeclaration


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
       (``AGENT_DISPATCH_SHARED_URL``; the hosted coordinator) for cross-machine
       dispatch, authenticated with its own ``AGENT_DISPATCH_SHARED_TOKEN``. If no
       shared coordinator is configured, error loudly rather than silently using
       the local queue (which would strand a cross-machine task on one host).
    3. Otherwise the **local** loopback coordinator -- same-machine work, the
       single-machine default that needs no shared service. **Failover:** if a
       shared coordinator is configured (``AGENT_DISPATCH_SHARED_URL``) *and* the
       local coordinator is not live (this environment's coordinator is down),
       transparently fall back to the shared/hosted coordinator so work is
       dispatched onto it (e.g. the standby) rather than stranded on a dead local
       queue. This is opt-in by construction: with no shared URL configured,
       nothing is probed and the local default is unchanged.
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
                "(the hosted-coordinator endpoint) or pass --url",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return surl, (token or shared_token())
    # Default local path, with opt-in failover to the shared coordinator: only
    # probe (and only fall back) when a shared URL is configured, so the common
    # single-machine case pays nothing and behaves exactly as before.
    surl = shared_url()
    if surl and not has_live_local_coordinator():
        return surl, (token or shared_token())
    return client_url(), (token or client_token())


def _should_ssh_failover(args: argparse.Namespace) -> str | None:
    """The peer machine to SSH-failover to for this command, or ``None``.

    Applies only on the **default local path** (no explicit ``--url``/``--shared``)
    when ``AGENT_DISPATCH_FAILOVER_MACHINE`` names a real *peer* and the local
    coordinator is not live. Preferred over the hosted ``AGENT_DISPATCH_SHARED_URL``
    HTTP fallback (per-machine SSH identity, no shared secret). Returns the peer
    machine name, or ``None`` when failover does not apply.
    """
    if getattr(args, "url", None) or getattr(args, "shared", False):
        return None
    machine = failover_machine()
    if not machine:
        return None
    from . import remote_dispatch

    if not remote_dispatch.is_peer_machine(machine):
        return None
    if has_live_local_coordinator():
        return None
    return machine


def _client(args: argparse.Namespace, *, ensure: bool = True) -> DispatchClient:
    if ensure:
        _ensure_local_coordinator(args)
    # SSH-transport failover: local coordinator down + a peer configured -> open
    # an SSH port-forward to the peer's loopback coordinator (per-machine key =
    # identity, tokenless) and run this command against it, keeping local context.
    peer = _should_ssh_failover(args)
    if peer is not None:
        from . import ssh_tunnel

        try:
            tunnel = ssh_tunnel.open_coordinator_tunnel(peer)
        except ssh_tunnel.TunnelUnavailable as exc:
            print(
                f"agent-dispatch: local coordinator down and SSH failover to "
                f"{peer!r} unavailable ({exc})",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        return DispatchClient(
            tunnel.base_url,
            token=None,
            control_token=(getattr(args, "control_token", None) or client_control_token()),
            tunnel=tunnel,
        )
    url, token = _resolve_client_target(args)
    use_shared_control = bool(getattr(args, "shared", False)) or (
        not getattr(args, "url", None) and shared_url() is not None and url == shared_url()
    )
    if use_shared_control:
        control = getattr(args, "control_token", None) or shared_control_token()
    else:
        control = getattr(args, "control_token", None) or client_control_token()
    return DispatchClient(url, token=token, control_token=control)


_AUTOSTART_ENV_OPT_OUT = "AGENT_DISPATCH_NO_AUTOSTART"


def _spawn_coordinator_process() -> None:
    """Launch the local coordinator **detached** (best effort, no wait).

    Runs the coordinator directly as ``<python> -m agent_dispatch serve`` under
    ``DETACHED_PROCESS`` (Windows) / a new session (POSIX) so it outlives this CLI
    process -- a later session then finds it already up. It appends output to
    ``serve-service.log`` and honors ``service.env`` (token / host-port pins) for
    parity with the installed launcher.

    NB: this deliberately does NOT shell out to ``serve-service.ps1`` via
    ``conhost``/``powershell``. That indirection, launched detached from Python,
    proved flaky on Windows (the wrapper exited before ``serve`` bound a listener,
    so no rendezvous was written and discovery never converged). Running the
    interpreter directly is the reliable path.

    On Windows the windowless ``pythonw.exe`` sibling is required in addition to
    ``DETACHED_PROCESS``: a detached venv ``python.exe`` launcher re-execs a base
    console interpreter that allocates a fresh DefTerm console.
    """
    from .install_paths import install_dir as runtime_install_dir

    install_dir = runtime_install_dir()
    from .procutil import (
        detached_kwargs,
        resolve_own_runtime_python,
        windowless_python,
        windowless_python_env,
    )

    # Always the canonically-resolved current-version slot (never sys.executable
    # directly, and never a legacy `.venv` path -- see resolve_own_runtime_python's
    # docstring for the production incident this class of bug caused: a stale
    # fallback here silently spawned an entire duplicate coordinator+supervisor
    # tree under the system Python instead of the installed slot).
    resolved_python = resolve_own_runtime_python()
    python = windowless_python(resolved_python)

    # Honor service.env (token, host/port pins) if present -- parity with the
    # installed launcher, which loads it before running `serve`.
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.update(windowless_python_env(resolved_python))
    env_file = install_dir / "service.env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env[k.strip()] = os.path.expandvars(v.strip())
        except OSError:
            pass

    try:
        log: Any = open(install_dir / "serve-service.log", "ab")
    except OSError:
        log = subprocess.DEVNULL

    kwargs: dict[str, Any] = dict(
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        close_fds=True,
        env=env,
        # Launch the detached coordinator from the runtime root, never the CWD we
        # inherited (a session-start hook's CWD is often the plugin payload dir,
        # which on Windows would lock it against `copilot plugin update`). The
        # daemon also relocates itself (procutil.relocate_off_payload) as a belt.
        cwd=str(install_dir),
    )
    kwargs.update(detached_kwargs())
    try:
        subprocess.Popen([python, "-m", "agent_dispatch", "serve"], **kwargs)  # noqa: S603
    finally:
        if log is not subprocess.DEVNULL:
            try:
                log.close()
            except OSError:
                pass


def _lazy_start_coordinator(*, timeout: float = 20.0) -> bool:
    """Start a local coordinator if none answers, then wait until it does.

    Serialized across concurrent CLI processes via an exclusive lock file so a
    burst of commands can't spawn a *herd* of coordinators (the SQLite queue is
    single-writer). A non-starter waits for whoever holds the lock to bring one
    up. Returns True if a live coordinator is available when we return.
    """
    from . import config

    if config.has_live_local_coordinator():
        return True
    rd = config.run_dir()
    try:
        rd.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    lock = rd / "autostart.lock"
    starter = False
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        starter = True
    except FileExistsError:
        # Another CLI is starting one. Steal a stale lock (older than the timeout
        # with still no coordinator) so a crashed starter can't wedge autostart.
        try:
            if time.time() - lock.stat().st_mtime > timeout:
                lock.unlink()
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                starter = True
        except OSError:
            starter = False
    try:
        if starter and not config.has_live_local_coordinator():
            print(
                "agent-dispatch: no local coordinator answering; starting one...",
                file=sys.stderr,
            )
            _spawn_coordinator_process()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if config.has_live_local_coordinator():
                return True
            time.sleep(0.4)
        return config.has_live_local_coordinator()
    finally:
        if starter:
            try:
                lock.unlink()
            except OSError:
                pass


def _ensure_local_coordinator(args: argparse.Namespace) -> None:
    """Best-effort: ensure a local coordinator is reachable before a client
    command runs, lazily starting one if not.

    No-op for an explicit ``--url``/``--shared`` target (remote/operator choice),
    for a **WSL guest opted in** to Windows-client mode (``AGENT_DISPATCH_WSL_WINDOWS_CLIENT``;
    the Windows host owns that coordinator), or when opted out via
    ``AGENT_DISPATCH_NO_AUTOSTART``. Every failure is swallowed -- the command then
    fails loudly on its own if the coordinator really is unreachable, so autostart
    never converts a hard error into a silent hang.
    """
    if getattr(args, "url", None) or getattr(args, "shared", False):
        return
    if os.environ.get(_AUTOSTART_ENV_OPT_OUT):
        return
    try:
        from .config import wsl_windows_client
        from .netinfo import is_wsl

        if is_wsl() and wsl_windows_client():
            return
    except Exception:
        pass
    try:
        _lazy_start_coordinator()
    except Exception:
        pass


def _cmd_ensure_coordinator(args: argparse.Namespace) -> int:
    """Internal, non-public entrypoint: run the tier-1 user-mode-ensure
    autostart path and report whether a local coordinator is reachable.

    Exists so a service-lifecycle installer (``install.sh``'s ``do_start``)
    can trigger exactly the same lazy autostart every ordinary client command
    already performs via ``_client(..., ensure=True)``/``_ensure_local_coordinator``
    -- without depending on any specific data-bearing subcommand's own side
    effects, output shape, or repo-resolution requirements (several real
    subcommands, e.g. ``list``, resolve/require a repo *before* ever reaching
    the client/autostart call, so they are not safe/general-purpose triggers).
    Not part of the public CLI surface (unlisted; leading underscore).
    """
    _ensure_local_coordinator(args)
    from .config import has_live_local_coordinator

    return 0 if has_live_local_coordinator() else 1


def _cmd_stop_coordinator(args: argparse.Namespace) -> int:
    """Internal, non-public entrypoint: gracefully stop a local coordinator
    that is NOT (or no longer) managed by systemd, via its own HTTP
    ``/shutdown`` route.

    Exists for ``install.sh``'s ``do_stop`` fallback: a coordinator started
    through the tier-1 direct-start fallback (``_cmd_ensure_coordinator``,
    #2524) or plain CLI lazy-autostart is a bare detached process with no
    service-manager entry, so ``systemctl --user stop`` cannot reach it and
    would otherwise leave it running unmanaged. A no-op (exit 0) when no
    local coordinator is reachable at all; fail-soft on any client/transport
    error (a best-effort teardown aid for the installer, never a hard
    requirement). Not part of the public CLI surface.
    """
    from .config import has_live_local_coordinator

    if not has_live_local_coordinator():
        return 0
    try:
        with _client(args, ensure=False) as c:
            c.shutdown()
    except Exception:
        return 1
    return 0


def _parse_affinity(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        key, _, val = item.partition("=")
        out[key.strip()] = val.strip()
    return out




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


def _enrich(result: Any, *, resolve_repo_names: bool = True) -> Any:
    """Annotate task dict(s) with a display-only ``repo_name`` (the local name
    for the canonical ``repo`` remote, when the registry knows it), and parse the
    stored ``latest_progress`` JSON string into an object for clean at-a-glance
    output."""

    def repo_display_name(repo: object) -> str | None:
        value = str(repo or "").rstrip("/")
        if not value:
            return None
        return value.rsplit("/", 1)[-1].removesuffix(".git") or None

    def one(d: Any) -> Any:
        if not isinstance(d, dict):
            return d
        if "repo" in d and "repo_name" not in d:
            if resolve_repo_names:
                from .identity import name_for_repo

                name = name_for_repo(d.get("repo"))
            else:
                name = repo_display_name(d.get("repo"))
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
        return {
            k: (_enrich(v, resolve_repo_names=resolve_repo_names) if isinstance(v, list) else v)
            for k, v in result.items()
        }
    return one(result)


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
    machine, worktree = _identity(args)
    all_repos = bool(getattr(args, "all_repos", False))
    repo = None if all_repos else _scope_repo(args)
    if not all_repos and not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
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
        # Surface the accumulated append-only progress log alongside the task, so
        # a (re-)embodied worker reads its goal, done-criteria, AND recorded
        # progress in one call and resumes from it rather than restarting.
        task = dict(task)
        task["progress_log"] = c.progress_log(args.task_id)
        if getattr(args, "history", False):
            task["attachments"] = c.attachments(args.task_id)
    from . import tracking

    return _emit(tracking.enrich_task(_enrich(task)))


def _split_owner(owner: str | None) -> tuple[str | None, str | None]:
    """Split a ``machine/worktree`` worker id into its parts.

    The inverse of ``queue.worker_id_for``. A malformed or missing value yields
    ``(None, None)`` (or ``(value, None)`` when it has no ``/``), never raising,
    so a reverse-lookup read can't crash on a stray owner string.
    """
    if not owner:
        return (None, None)
    machine, sep, worktree = owner.partition("/")
    if not sep:
        return (owner or None, None)
    return (machine or None, worktree or None)


def _cmd_claimant(args: argparse.Namespace) -> int:
    """task -> claiming worktree: resolve which worktree owns a task.

    The inbound-ledger reverse of ``worktree-status`` (worktree -> its tasks).
    Returns a focused record: the actual claimant (``owner`` = machine/worktree,
    once the task is claimed/started), or -- for a not-yet-claimed task -- the
    pinned ``target`` worktree, with ``claimed`` distinguishing the two.
    """
    with _client(args) as c:
        task = c.get(args.task_id)
    status = task.get("status")
    owner = task.get("owner")
    claimed = bool(owner) and status in ("claimed", "started", "suspended", "completed")
    if claimed:
        machine, worktree = _split_owner(owner)
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
    return _emit(_enrich(result))


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


def _cmd_suspend(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="suspend")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.suspend(args.task_id, worker_id, reason=args.reason))


def _cmd_resume(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="resume")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(
            c.resume(
                args.task_id,
                worker_id,
                wake=args.wake,
                message=args.message,
            )
        )


def _cmd_release(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="release")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.release(args.task_id, worker_id, reason=args.reason))


def _hold_actor(args: argparse.Namespace) -> str:
    """The acting operator identity for a pause/unpause -- an explicit
    ``--actor``, else whatever worktree identity resolves (for audit-trail
    legibility only; pause/unpause are operator actions, never owner-gated)."""
    return getattr(args, "actor", None) or _owner_from_identity(args) or "operator"


def _cmd_pause(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(
            c.set_hold(
                args.task_id,
                reason=args.reason,
                actor=_hold_actor(args),
                expected_status=args.expected_status,
            )
        )


def _cmd_unpause(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(
            c.clear_hold(
                args.task_id,
                actor=_hold_actor(args),
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
    with _client(args) as c:
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
    return _emit(result)


def _cmd_force_stop(args: argparse.Namespace) -> int:
    from .force_stop import ForceStopError, force_stop
    from .identity import resolve_machine

    actor = getattr(args, "actor", None) or _owner_from_identity(args)
    with _client(args) as c:
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
    return _emit(result)


def _cmd_reset(args: argparse.Namespace) -> int:
    if args.to != "proposed":
        print(
            f"agent-dispatch: reset only supports --to proposed today, not {args.to!r}",
            file=sys.stderr,
        )
        return 2
    with _client(args) as c:
        return _emit(
            c.reset(
                args.task_id,
                reason=args.reason,
                expected_status=args.expected_status,
            )
        )


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
        mine = [w for w in aw_list_records(machine=machine) if w.get("id") == worktree]
        return _emit(
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
    return _emit(
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
    worker_id = _resolve_owner(args, verb="complete")
    if worker_id is None:
        return 2
    try:
        result = _read_result(args)
    except (OSError, ValueError) as exc:
        print(f"agent-dispatch: invalid result: {exc}", file=sys.stderr)
        return 2
    with _client(args) as c:
        return _emit(
            c.complete(
                args.task_id,
                worker_id,
                result_ref=args.result_ref,
                result=result,
            )
        )


def _read_result(args: argparse.Namespace) -> object | None:
    """Read and decode the complete command's optional JSON result."""
    raw = args.result_json
    if args.result_file is not None:
        if args.result_file == "-":
            raw = sys.stdin.read()
        else:
            path = Path(args.result_file).expanduser()
            raw = path.read_text(encoding="utf-8-sig")
    if raw is None:
        return None
    raw = raw.removeprefix("\ufeff")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if result is None:
        raise ValueError("result must be a JSON object or array, not null")
    from .queue import ResultTooLargeError, ResultValidationError, encode_result

    try:
        encode_result(result)
    except (ResultValidationError, ResultTooLargeError) as exc:
        raise ValueError(str(exc)) from exc
    return result


def _cmd_abandon(args: argparse.Namespace) -> int:
    from . import reattach as _reattach

    with _client(args) as c:
        try:
            result = _reattach.cli_abandon(c, args)
        except _reattach.AbandonRefused as exc:
            print(f"agent-dispatch abandon: {exc}", file=sys.stderr)
            return 2
    return _emit(result)


def _cmd_reattach(args: argparse.Namespace) -> int:
    """Reattach a terminal task's still-live session (Phase 9 / copilot-extensions#2884)."""
    from . import reattach as _reattach

    with _client(args) as c:
        try:
            result = _reattach.reattach(
                c, args.task_id, args.session_id, host=args.host, resume=not args.no_resume
            )
        except _reattach.ReattachError as exc:
            print(f"agent-dispatch reattach: {exc}", file=sys.stderr)
            return 1
    return _emit(result.as_dict())



#: The `agent-worktrees` worktree-root naming convention observed throughout
#: this harness: `<project-repo-name>.worktrees/<machine>-<os>-<timestamp>-<hex>`
#: (e.g. `dotfiles.worktrees\alice-cloud1-win-20260910-171507-5474`). A
#: worktree checkout's own directory name is per-session/per-machine and is
#: never a stable repo identity.
_WORKTREE_PARENT_SUFFIX = ".worktrees"


def _reject_worktree_checkout_as_repo_root(repo_root: Path) -> None:
    """Refuse when ``repo_root`` looks like a worktree checkout, not a repo's
    registered anchor.

    A worktree's own directory name is per-session and per-machine -- never a
    stable repo identity. If a reviewer-loop declaration is discovered under a
    worktree checkout instead of the repo's registered anchor, deriving the
    registration owner/pointer name from that path (``repo_root.name``) stamps
    every resulting registration with a throwaway, non-reconciling identity:
    every fresh worktree that hits this path registers a brand-new pointer
    that never collides with (and is never cleaned up alongside) the last one,
    so declared registrations -- and the workers the supervisor spawns for
    them -- accumulate without bound (#2417).

    Deliberately a **cheap, dependency-free path-pattern check** (does
    ``repo_root``'s parent directory name end in ``.worktrees``, the naming
    convention every ``agent-worktrees``-managed worktree root in this harness
    follows) rather than an authoritative subprocess probe out to
    ``agent-worktrees``: a per-invocation subprocess round-trip measured ~9s
    on a busy/loaded host (the same process-count-scales-with-activity
    pressure #2417/#2300 describe), which would make every reviewer-loop /
    repository-issue-loop CLI call slow exactly when the host is already
    struggling -- an unacceptable regression for what is meant to be a fast
    safety check. This heuristic is best-effort, not a hard guarantee (a repo
    anchor whose own name happens to end in ``.worktrees`` would be a false
    positive; a worktree root that does NOT follow this harness's naming
    convention would be a false negative) -- but it catches the actual
    observed failure mode with zero added latency and no external dependency,
    consistent with à-la-carte independence.
    """
    if repo_root.parent.name.endswith(_WORKTREE_PARENT_SUFFIX):
        raise ValueError(
            f"{repo_root} looks like a worktree checkout (its parent "
            f"directory, {repo_root.parent.name!r}, follows this harness's "
            "'<repo>.worktrees' naming convention), not a repo's registered "
            "anchor. Run this reviewer-loop command from the anchor checkout "
            "instead: a worktree's directory name is per-session and must "
            "never be recorded as a repo's stable identity (see "
            "visions/plugin-services -- repo/agent identity resolves by "
            "registered NAME only; a filesystem path is never persisted "
            "outside repos.yaml/projects.yaml)."
        )


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
        return _emit(payload)

    results: list[dict] = []
    instructions: list[str] = []
    failed = False
    for step in plan.steps:
        if step.advisory:
            instructions.append(step.description)
            results.append({"kind": step.kind, "ran": False, "advisory": True})
            continue
        res = _run_resolution_step(step)
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
    _emit(payload)
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
        with _client(args) as c:
            worker_id = c.get(spec.task_id).get("owner")
    except Exception:  # noqa: BLE001 -- owner lookup is best-effort, never fatal here
        worker_id = None
    if not worker_id:
        worker_id = _resolve_owner(args, verb="run --detach")
    if worker_id is None:
        return {"error": "could not resolve the owning worker for suspend", "claim": claim}
    try:
        with _client(args) as c:
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
        handle = _spawn_detached_waiter(spec)
        suspended = _suspend_for_detached_wait(args, spec)
        return _emit(
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
    return _emit(report)


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
        return _emit(report)

    with _client(args) as c:
        try:
            report = evaluate_and_apply(
                evaluator, event, creator=c.create, repo=args.repo, apply=True
            )
        except EvaluatorError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 2
    return _emit(report)


def _cmd_charter_show(args: argparse.Namespace) -> int:
    from .worker_charter import charter_text

    try:
        text = charter_text(args.name)
    except KeyError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 0


class _DashDashParser(argparse.ArgumentParser):
    """Top-level parser that captures a verbatim ``-- <command...>`` tail robustly.

    argparse's handling of a ``--`` separator before a ``nargs='*'`` positional
    differs across CPython versions (3.11 raises "unrecognized arguments"; 3.12+
    consumes it), which broke ``recipes drive --execute -- <cmd>`` on 3.11
    runtime slots (#383). Rather than depend on that, split everything after the
    FIRST ``--`` off ourselves, parse the head normally, and expose the verbatim
    tail via ``_dashdash_tail`` for the ``run`` / ``recipes drive`` handlers.

    Scoped to those two commands (the only ones that take a ``-- <command>``
    tail) so every other subcommand keeps argparse's native ``--`` "end of
    options" escape hatch.
    """

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        args = list(sys.argv[1:] if args is None else args)
        if "--" in args:
            idx = args.index("--")
            head, tail = args[:idx], args[idx + 1 :]
            # Resolve the ACTUAL subcommand from the head (peek parse) rather than
            # a token-membership test -- a positional VALUE equal to 'run'/'drive'
            # (e.g. `create run -- ...`) must not trigger interception (#383).
            try:
                peek, _ = super().parse_known_args(head, None)
            except SystemExit:
                peek = None
            if peek is not None and getattr(peek, "func", None) in (_cmd_run, _cmd_recipes_drive):
                ns, extras = super().parse_known_args(head, namespace)
                ns._dashdash_tail = tail
                return ns, extras
        return super().parse_known_args(args, namespace)


def _declaration_summary(decl: ProfileDeclaration) -> dict[str, Any]:
    """A JSON-friendly summary of a discovered declaration (for ``registrar discover``)."""
    ef = decl.effective_filters()
    return {
        "name": decl.name,
        "owner": decl.owner,
        "labels": list(decl.labels),
        "repos": decl.repos,
        "concurrency": decl.concurrency,
        "max_active_processes": decl.concurrency,
        "body": {"type": decl.body.type, "agent": decl.body.agent},
        "filters": {
            "permit": {dim: sorted(vals) for dim, vals in ef.permit.items()},
            "reject": {dim: sorted(vals) for dim, vals in ef.reject.items()},
        },
    }


def _cmd_registrar(args: argparse.Namespace) -> int:
    """The declarative-supervision registrar: manage discovery pointers + read the
    declared profile set. A thin writer/reader over the declared documents (the one
    source of truth) -- no coordinator round-trip."""
    from . import registrar_discovery as rd
    from .registrar import RegistrarError

    try:
        if args.registrar_command == "doctor":
            from .registrar_registry import registrar_dropins_dir

            sources = rd.RegistrarSources()
            report = sources.refresh(emit_warnings=False)
            combined = report.combined
            trusted_names = {declaration.name for declaration in combined.trusted}
            accepted_plugins = [
                contributed
                for contributed in combined.plugins.declarations
                if contributed.declaration.name not in trusted_names
            ]
            plugin_retention_possible = (
                combined.plugins.snapshot.authority.value == "indeterminate"
                or any(finding.status == "indeterminate" for finding in combined.findings)
            )
            payload = {
                "trusted": {
                    "registry": "pointers.json",
                    "path": str(rd.pointers_file()),
                    "authority": report.trusted_authority.value,
                    "error": report.trusted_error,
                    "retention_possible": report.trusted_error is not None,
                    "declarations": [
                        _declaration_summary(declaration) for declaration in combined.trusted
                    ],
                },
                "dropins": {
                    "registry": "registrar.d",
                    "path": str(registrar_dropins_dir()),
                    "authority": combined.plugins.snapshot.authority.value,
                    "active": [
                        {
                            **_declaration_summary(contributed.declaration),
                            "plugin": contributed.plugin,
                            "entry": contributed.source_path,
                            "manifest": contributed.manifest_path,
                        }
                        for contributed in accepted_plugins
                    ],
                    "findings": [finding.to_dict() for finding in combined.findings],
                    "fix_available": False,
                    "active_basis": "current-evidence-only",
                    "retention_possible": plugin_retention_possible,
                },
                "active": [
                    _declaration_summary(declaration) for declaration in combined.declarations
                ],
                "active_basis": "current-evidence-only",
            }
            failed = bool(report.trusted_error or combined.findings)
            if args.json:
                _emit(payload)
            else:
                trusted_label = "[WARN]" if report.trusted_error else "[OK]"
                print(
                    f"{trusted_label} pointers.json is "
                    f"{report.trusted_authority.value}; "
                    f"{len(combined.trusted)} trusted declaration(s) confirmed "
                    "by current evidence."
                )
                if report.trusted_error:
                    print(f"  {report.trusted_error}")
                    print("  A running supervisor may retain its last-known trusted declarations.")
                dropin_label = "[WARN]" if combined.findings else "[OK]"
                print(
                    f"{dropin_label} registrar.d is "
                    f"{combined.plugins.snapshot.authority.value}; "
                    f"{len(accepted_plugins)} plugin declaration(s) confirmed "
                    "active by current evidence."
                )
                if plugin_retention_possible:
                    print(
                        "  A running supervisor may retain matching last-known "
                        "declarations for indeterminate entries."
                    )
                for finding in combined.findings:
                    target = f" -> {finding.target}" if finding.target else ""
                    print(f"  - {finding.reason}: {finding.entry}{target}")
                    if finding.detail:
                        print(f"    {finding.detail}")
                    if finding.remedy:
                        print(f"    {finding.remedy}")
                print("  Cleanup is report-only; no --fix operation is available.")
            return 1 if failed else 0
        if args.registrar_command == "add-pointer":
            pointer = rd.add_pointer(args.name, args.location, kind=args.kind, owner=args.owner)
            return _emit(pointer.to_dict())
        if args.registrar_command == "list":
            return _emit([p.to_dict() for p in rd.load_pointers()])
        if args.registrar_command == "remove":
            return _emit({"removed": rd.remove_pointer(args.name)})
        if args.registrar_command == "discover":
            decls = rd.discover()
            return _emit([_declaration_summary(d) for d in decls])
        if args.registrar_command == "discover-repo":
            decls = rd.discover_repo(args.repo_root, owner=args.owner)
            return _emit([_declaration_summary(d) for d in decls])
    except RegistrarError as exc:
        print(f"agent-dispatch registrar: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled registrar command {args.registrar_command!r}")



def build_parser() -> argparse.ArgumentParser:
    parser = _DashDashParser(prog="agent-dispatch", description="Agent task queue + coordinator")
    parser.add_argument("--version", action="version", version=f"agent-dispatch {__version__}")
    parser.add_argument(
        "--url", help="coordinator base URL (default: AGENT_DISPATCH_URL or config)"
    )
    parser.add_argument("--token", help="bearer token (default: AGENT_DISPATCH_TOKEN)")
    parser.add_argument(
        "--control-token",
        help="separate managed-producer control bearer (default: AGENT_DISPATCH_CONTROL_TOKEN)",
    )
    parser.add_argument(
        "--shared",
        action="store_true",
        help="target the SHARED/elected coordinator (AGENT_DISPATCH_SHARED_URL; "
        "the hosted coordinator) for cross-machine dispatch, instead of this "
        "host's local coordinator. Authenticated with AGENT_DISPATCH_SHARED_TOKEN.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    register_coordinator_commands(sub)
    register_create_commands(sub)


    p = sub.add_parser(
        "registrar",
        help="declarative-supervision registrar: manage discovery pointers and read "
        "the declared profile set (declarations are the one source of truth; "
        "the CLI is a thin writer/reader over them)",
    )
    reg_sub = p.add_subparsers(dest="registrar_command", required=True)
    rp = reg_sub.add_parser(
        "add-pointer",
        help="record (or replace) a pointer to a location of declaration documents",
    )
    rp.add_argument("name", help="unique pointer name (letters, digits, '-', '_')")
    rp.add_argument(
        "location",
        help="directory of declaration docs, or (with --kind repo) a repo root "
        "whose .copilot-extensions/agent-dispatch/registrar/ "
        "(legacy .agent-dispatch/registrar/) is read",
    )
    rp.add_argument(
        "--kind",
        choices=["dir", "repo"],
        default="dir",
        help="'dir' (default) reads the location directly; 'repo' reads its "
        ".copilot-extensions/agent-dispatch/registrar/ subdir "
        "(legacy .agent-dispatch/registrar/ fallback)",
    )
    rp.add_argument("--owner", help="provenance stamped on declarations read here")
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser("list", help="list the recorded discovery pointers")
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser(
        "doctor",
        help="audit trusted pointers and attributed registrar.d candidates",
    )
    rp.add_argument(
        "--json",
        action="store_true",
        help="emit exhaustive structured registrar findings",
    )
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser("remove", help="remove a discovery pointer by name")
    rp.add_argument("name", help="the pointer name to remove")
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser(
        "discover",
        help="read + aggregate the declared profile set across all pointers "
        "(rejects duplicate profile names across sources)",
    )
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser(
        "discover-repo",
        help="read a single synced repo's in-repo "
        ".copilot-extensions/agent-dispatch/registrar/ declarations "
        "(legacy .agent-dispatch/registrar/ fallback; repo-sync discovery unit)",
    )
    rp.add_argument("repo_root", help="path to the repo root to read declarations from")
    rp.add_argument("--owner", help="provenance override (default: repo:<name>)")
    rp.set_defaults(func=_cmd_registrar)

    p = sub.add_parser(
        "claim", help="atomically lease one eligible task (identity auto-resolved from CWD)"
    )
    p.add_argument(
        "task_id",
        nargs="?",
        help="claim THIS specific task id (optional; default: any eligible task). "
        "First-positional task id, consistent with start/complete/yield/abandon.",
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.add_argument(
        "--worker",
        "--as",
        dest="worker_id",
        help="explicit owner/worker id to claim as (rarely needed; default: "
        "composed from machine/worktree). Was the bare positional, now a flag "
        "so it can't be confused with the task id.",
    )
    p.add_argument("--capability", action="append", help="advertised capability (repeatable)")
    p.add_argument(
        "--task",
        help="alias for the positional task id (back-compat)",
    )
    claim_scope = p.add_mutually_exclusive_group()
    claim_scope.add_argument(
        "--repo",
        help="lane to claim from (local name or remote URL). Default: the calling "
        "repo. A worker only claims tasks in its own repo's lane.",
    )
    claim_scope.add_argument(
        "--all-repos",
        action="store_true",
        help="administrative mode: claim across every repo lane explicitly",
    )
    p.add_argument("--lease-seconds", type=int)
    p.add_argument(
        "--evaluation",
        action="store_true",
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
        "suspend",
        help="park a started task as dormant while retaining its owner",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    p.add_argument(
        "--reason",
        required=True,
        help="required meaningful reason recorded in the task audit trail",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.set_defaults(func=_cmd_suspend)

    p = sub.add_parser(
        "resume",
        help="resume a suspended task under the same owner and wake it",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    p.add_argument("--message", help="override the wake nudge text")
    p.add_argument(
        "--no-wake",
        dest="wake",
        action="store_false",
        help="resume the lifecycle without sending an agent-bridge wake nudge",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.set_defaults(func=_cmd_resume, wake=True)

    p = sub.add_parser(
        "release",
        help="release a suspended task to queued for replacement embodiment",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    p.add_argument("--reason", help="optional release note for the audit trail")
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.set_defaults(func=_cmd_release)

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
        "--exclude-self",
        "--not-me",
        choices=("worktree", "machine"),
        dest="exclude_self",
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

    p = sub.add_parser(
        "complete",
        help="mark a started or suspended task completed under its owner",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: the machine/worktree resolved from CWD, so a "
        "worker can `complete <id>` without typing its own owner)",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.add_argument("--result-ref")
    result_group = p.add_mutually_exclusive_group()
    result_group.add_argument(
        "--result-json",
        help="structured completion result as JSON",
    )
    result_group.add_argument(
        "--result-file",
        metavar="PATH",
        help="read the structured completion result from a JSON file; '-' reads stdin",
    )
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
        "--duplicate-of",
        dest="duplicate_of",
        metavar="REF",
        help="retire the task as a DUPLICATE of REF (an existing task id, PR, or "
        "issue). Self-justifying: implies --permit and records the dedup "
        "reference in the reason, so the decision is never a silent drop.",
    )
    p.add_argument(
        "--resolve",
        action="store_true",
        help="also emit the drive-the-worktree-to-resolution plan (the unwind the "
        "worker must run on its own worktree). Advisory -- runs nothing.",
    )
    p.add_argument(
        "--base",
        metavar="BRANCH",
        help="with --resolve, the base branch the worktree unwinds onto "
        "(default: the branch's tracked upstream)",
    )
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    from . import reattach as _reattach
    _reattach.add_abandon_override_live_argument(p)
    p.set_defaults(func=_cmd_abandon)

    p = _reattach.build_reattach_subparser(sub)
    p.set_defaults(func=_cmd_reattach)

    p = sub.add_parser(
        "pause",
        help="set a durable operator hold on a task (Phase 1's Pause primitive)",
    )
    p.add_argument("task_id")
    p.add_argument("--reason", required=True, help="required reason recorded in the audit trail")
    p.add_argument("--actor", help="operator identity recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    p.set_defaults(func=_cmd_pause)

    p = sub.add_parser(
        "unpause",
        help="clear a hold set by `pause`",
    )
    p.add_argument("task_id")
    p.add_argument("--actor", help="operator identity recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row) -- ignored "
        "when the task is already unheld (a harmless no-op)",
    )
    p.set_defaults(func=_cmd_unpause)

    p = sub.add_parser(
        "embody",
        help="Phase 1's interactive-embodiment transaction: open a task into "
        "an interactive CLI-backed session (currently requires --interactive)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--interactive",
        action="store_true",
        required=True,
        help="run the interactive-embodiment transaction (the only mode "
        "implemented so far -- an unattended/autopilot `embody` equivalent "
        "already exists via the supervisor's own spawn path, not this verb)",
    )
    p.add_argument(
        "--machine",
        help="override the resolved machine identity (default: this host, "
        "via agent-worktrees)",
    )
    p.add_argument(
        "--project",
        help="target project name (default: resolved from the task's repo)",
    )
    p.set_defaults(func=_cmd_embody_interactive)

    p = sub.add_parser(
        "force-stop",
        help="terminate a started task's exact current session and park it "
        "suspended, without a durable pause hold",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--machine",
        help="this machine's identity, for deciding whether the task's "
        "session is local or on a fleet host (default: resolved via "
        "agent-worktrees)",
    )
    p.add_argument("--actor", help="operator identity recorded in the audit trail")
    p.set_defaults(func=_cmd_force_stop)

    p = sub.add_parser(
        "reset",
        help="Phase 2's gentler 'not like this': discard the current attempt's "
        "embodiment state and re-admit the task for a fresh attempt",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--to",
        default="proposed",
        help="target state (only 'proposed' is implemented today)",
    )
    p.add_argument("--reason", help="optional note recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    p.set_defaults(func=_cmd_reset)

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
        "--phase",
        default="",
        help="short phase label (e.g. 'planning', 'implementing', 'PR open')",
    )
    p.add_argument(
        "--summary",
        required=True,
        help="one-line status toward the goal (hard-capped; keep it a line, not a transcript)",
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
        "focus_text",
        nargs="?",
        help="one-line focus for this worktree; omit to show the current focus",
    )
    p.add_argument("--list", action="store_true", help="list every worktree's focus")
    p.add_argument("--machine", help="filter --list to a machine / override resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.set_defaults(func=_cmd_focus)

    p = sub.add_parser("detach", help="demote a hard worktree pin to a soft affinity")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("detach", "task_id"))

    register_steering_commands(sub)

    p = sub.add_parser("list", help="list tasks (scoped to the calling repo by default)")
    p.add_argument("--repo", help="lane to list (local name or remote URL); default: calling repo")
    p.add_argument(
        "--status",
        help="filter by status; comma-separate for several (e.g. queued,started)",
    )
    p.add_argument("--target-machine")
    p.add_argument("--target-repo")
    p.add_argument("--label")
    p.add_argument("--evaluator-ref")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument(
        "--machine",
        help="read another machine's queue over the SSH mesh (peer browse); "
        "default: this machine's local coordinator",
    )
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser(
        "doctor",
        help="diagnose held/suspended tasks: distinguish a confirmed-orphaned "
        "task (its worktree provably gone) from ordinary in-flight work or "
        "merely ambiguous liveness, and (with --repair) unbind + re-queue "
        "only the confirmed-orphaned ones",
    )
    p.add_argument(
        "--task",
        help="diagnose exactly this one task id (any status), instead of "
        "sweeping --repo/--label",
    )
    p.add_argument(
        "--repo", help="lane to examine (local name or remote URL); default: calling repo"
    )
    p.add_argument("--label", help="only examine tasks carrying this label")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument(
        "--check-live-sessions",
        action="store_true",
        help="walk each task's full reservation history and probe every "
        "attempt's embody-session liveness by session id -- reports "
        "'earlier_attempt_live' when an earlier attempt is live but shadowed "
        "by a later dead/unknown one. Opt-in (probes the bridge per attempt).",
    )
    p.add_argument(
        "--stale-lease-seconds",
        type=float,
        default=3600.0,
        help="how long a 'started' task's lease may sit expired with no "
        "reported activity before flagging it stale_lease (advisory only, "
        "never auto-repaired; default: 3600 == agent_dispatch.doctor."
        "DEFAULT_STALE_LEASE_GRACE_SECONDS)",
    )
    p.add_argument(
        "--repair",
        action="store_true",
        help="unbind + re-queue every task diagnosed orphaned_worktree_gone "
        "(fails its stale reservation, then releases/yields the task back "
        "to queued); every other diagnosis is left untouched",
    )
    p.set_defaults(func=_cmd_doctor)

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
        help="status filter; comma-separate for several (default: proposed). "
        "Ignored when --awaiting-steer is set.",
    )
    p.add_argument(
        "--awaiting-steer",
        dest="awaiting_steer",
        action="store_true",
        help="show the picker steer surface: pickable (proposed) tasks PLUS any "
        "task blocked on operator steering (a posted card's request_input, "
        "in claimed/started/suspended), and nothing else of the owned queue. "
        "Overrides "
        "--status.",
    )
    p.add_argument(
        "--board",
        action="store_true",
        help="status-grouped board for the picker Tasks pivot: tasks across "
        "proposed/queued/claimed/started/suspended PLUS recently "
        "completed/abandoned, each tagged with a display `group` "
        "(Blocked/Proposed/Started/Queued/Suspended/Completed/Abandoned) "
        "and ordered by that priority. Overrides "
        "--status and --awaiting-steer.",
    )
    p.add_argument(
        "--recent-mins",
        dest="recent_mins",
        type=int,
        default=120,
        help="with --board: include completed/abandoned tasks whose terminal time "
        "is within this many minutes (default: 120).",
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
    p.add_argument(
        "--history",
        action="store_true",
        help="also include the task's durable attachment history "
        "(every session that has ever attached, newest first)",
    )
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser(
        "claimant",
        help="task -> claiming worktree: which worktree owns a task (the inbound "
        "reverse of worktree-status). Reports the actual owner once claimed, "
        "else the pinned target worktree.",
    )
    p.add_argument("task_id")
    p.set_defaults(func=_cmd_claimant)

    p = sub.add_parser(
        "find-by-session",
        help="session -> task/worktree history: the reverse of `show --history` "
        "-- given an arbitrary session id (an agent-bridge escrow id or a "
        "durable ACP UUID), list every task on THIS HOST's coordinator it has "
        "ever attached to, newest first, with the worktree/machine it ran in. "
        "Empty (not an error) means the session id never attached to a task here.",
    )
    p.add_argument("session_id")
    p.set_defaults(func=_simple("tasks_for_session", "session_id"))

    p = sub.add_parser("events", help="show a task's audit trail")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("events", "task_id"))

    p = sub.add_parser("wakes", help="show a task's durable wake outbox operations")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("wakes", "task_id"))

    p = sub.add_parser("payload", help="show a task's resolved payload (inline or blob)")
    p.add_argument("task_id")
    p.add_argument("--raw", action="store_true", help="print the payload content only (not JSON)")
    p.set_defaults(func=_cmd_payload)

    p = sub.add_parser("result", help="show a task's structured completion result")
    p.add_argument("task_id")
    p.add_argument(
        "--raw", action="store_true", help="print the result JSON only (not the envelope)"
    )
    p.set_defaults(func=_cmd_result)

    p = sub.add_parser(
        "consume",
        help="resume-and-consume a handoff: drive it to completed (idempotent; "
        "a spent completed handoff is refused, exit 3, never replayed) "
        "and print its payload -- the successor's one-command pickup",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--worker-id",
        dest="worker_id",
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
        "--defer-complete",
        action="store_true",
        help="takeover pickup: approve->claim->start + print the brief, but do "
        "NOT complete -- the successor completes explicitly when the goal "
        "is reached (deferred completion)",
    )
    p.set_defaults(func=_cmd_consume)

    p = sub.add_parser("recover", help="requeue expired-lease tasks")
    p.set_defaults(func=lambda args: _emit(_client(args).recover()))

    p = sub.add_parser("watch", help="stream task events (SSE) as JSON lines")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser("mcp", help="run the local stdio MCP server (per-agent interaction layer)")
    p.set_defaults(func=_cmd_mcp)

    register_producer_commands(sub)

    p = sub.add_parser(
        "reviewer-loop",
        help="inspect and operate one repository-owned reviewer-loop declaration",
    )
    loop_sub = p.add_subparsers(dest="reviewer_loop_command", required=True)
    lp = loop_sub.add_parser(
        "setup",
        help="register the declaration's repository with the existing registrar",
    )
    lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
    lp.add_argument("--name", help="pointer name (default: repository directory name)")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_reviewer_loop)
    for command, help_text in (
        ("inspect", "expand the declaration and show its effective supervised units"),
        ("status", "join declaration, service, task, and recovery status"),
        ("doctor", "diagnose inactive or unhealthy reviewer-loop state"),
        ("enable", "clear local overrides for every unit in the reviewer loop"),
    ):
        lp = loop_sub.add_parser(command, help=help_text)
        lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
        lp.add_argument("--owner", help="declaration owner override")
        if command in {"status", "doctor"}:
            lp.add_argument(
                "--limit",
                type=int,
                default=200,
                help="maximum associated tasks and failed reservations to inspect",
            )
        lp.set_defaults(func=_cmd_reviewer_loop)
    lp = loop_sub.add_parser(
        "disable",
        help="locally override every unit in the reviewer loop off",
    )
    lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
    lp.add_argument("--reason", help="why the loop is disabled")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_reviewer_loop)
    lp = loop_sub.add_parser(
        "side-load",
        help="send one change through the declaration's emitter-owned path",
    )
    lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
    lp.add_argument("change_ref", help="target change reference")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_reviewer_loop)

    p = sub.add_parser(
        "repository-issue-loop",
        help="inspect and operate a declarative repository issue backlog loop",
    )
    issue_loop_sub = p.add_subparsers(dest="repository_issue_loop_command", required=True)
    lp = issue_loop_sub.add_parser(
        "setup",
        help="register the declaration's repository with the existing registrar",
    )
    lp.add_argument("declaration", help="path to the repository-issue-loop JSON/YAML file")
    lp.add_argument("--name", help="pointer name")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_repository_issue_loop)
    for command, help_text in (
        ("inspect", "expand the declaration and show its supervised units"),
        ("status", "join source, reservation, task, pool, and service status"),
        ("doctor", "diagnose unhealthy repository issue-loop state"),
        ("enable", "clear local overrides for the whole loop"),
        ("discover", "dry-run the current occurrence and eligible issue set"),
    ):
        lp = issue_loop_sub.add_parser(command, help=help_text)
        lp.add_argument(
            "declaration",
            help="path to the repository-issue-loop JSON/YAML file",
        )
        lp.add_argument("--owner", help="declaration owner override")
        if command in {"status", "doctor"}:
            lp.add_argument("--limit", type=int, default=200)
        lp.set_defaults(func=_cmd_repository_issue_loop)
    lp = issue_loop_sub.add_parser("disable", help="locally override the whole loop off")
    lp.add_argument("declaration", help="path to the repository-issue-loop JSON/YAML file")
    lp.add_argument("--reason", help="why the loop is disabled")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_repository_issue_loop)

    register_webhook_command(sub)

    p = sub.add_parser(
        "supervise",
        help="embody spawn supervisor: turn queued (label-gated) tasks into host "
        "embody autopilots, exactly once each, via atomic spawn reservations",
    )
    supervise_scope = p.add_mutually_exclusive_group()
    supervise_scope.add_argument("--repo", help="lane to supervise (default: the calling repo)")
    supervise_scope.add_argument(
        "--all-repos", action="store_true", help="supervise every lane (no repo scope)"
    )
    p.add_argument(
        "--label",
        action="append",
        help="only spawn queued tasks carrying this label (repeatable; opt-in gate)",
    )
    p.add_argument(
        "--max-concurrent",
        "--max-active-processes",
        dest="max_concurrent",
        type=int,
        default=1,
        help="pool-local cap on live/launching worker processes (default: 1)",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="dead-letter a task after this many failed spawn attempts "
        "(default: 3; 0 = retry forever)",
    )
    p.add_argument(
        "--label-max-attempts",
        action="append",
        metavar="LABEL=N",
        help="per-label override of --max-attempts (repeatable), e.g. "
        "--label-max-attempts code-review=3 so raising one "
        "label's bound doesn't revive another label's stale tasks "
        "(N=0 = retry forever for that label)",
    )
    p.add_argument(
        "--no-heartbeat",
        action="store_true",
        help="don't hold the lease of confirmed-alive embodied workers "
        "(default: heartbeat live workers so a quiet-but-alive session's "
        "lease doesn't expire)",
    )
    p.add_argument(
        "--embody-backend",
        choices=["headless", "cli", "script"],
        default="headless",
        help="how the supervisor embodies a claimed task by default: 'headless' "
        "(default) -- a headless agent-bridge ACP session (no mux, no "
        "CLI-start-prompt), the right body for self-contained autonomous "
        "sweeps; 'cli' -- a CLI-backed autopilot worktree session (mux, "
        "attachable); 'script' -- a plain deterministic subprocess that drives "
        "the task lifecycle without an LLM. Per-label overrides: --cli-label "
        "(force CLI when the default is headless/script) / --headless-label "
        "(force headless when the default is cli/script) / --script-label "
        "(force script when the default is headless/cli).",
    )
    p.add_argument(
        "--headless-label",
        action="append",
        metavar="LABEL",
        help="force queued tasks carrying this label to a HEADLESS agent-bridge "
        "ACP session (repeatable). Only meaningful with --embody-backend cli "
        "(headless is already the default); local (non-pool) mode only.",
    )
    p.add_argument(
        "--cli-label",
        action="append",
        metavar="LABEL",
        help="force queued tasks carrying this label to a CLI-backed autopilot "
        "(mux, attachable) instead of the default headless body (repeatable). "
        "The opt-out for a lane that is headless-by-default; local "
        "(non-pool) mode only.",
    )
    p.add_argument(
        "--script-label",
        action="append",
        metavar="LABEL",
        help="force queued tasks carrying this label to a plain deterministic "
        "script subprocess instead of the lane's default body (repeatable; "
        "local non-pool mode only).",
    )
    p.add_argument(
        "--disposable-cli-label",
        action="append",
        metavar="LABEL",
        help="on terminal settlement, conclude the exact recorded CLI session "
        "for this label and prime its clean worktree for conservative "
        "managed GC (repeatable; label-scoped opt-in only)",
    )
    p.add_argument(
        "--no-pair",
        action="store_true",
        help="skip the paired-knowledge carve for every worktree this lane "
        "creates, whether embodied CLI-side or headless agent-bridge "
        "(agent-worktrees create --no-pair) -- for a pool with no bound "
        "knowledge repo to give its workers. Rejected together with "
        "--pool (fleet mode never creates a paired worktree locally, so "
        "there is nothing for this flag to skip).",
    )
    p.add_argument(
        "--headless-agent",
        default="task-worker",
        metavar="AGENT",
        help="agent-bridge agent name used for headless embody bodies (default: task-worker)",
    )
    p.add_argument(
        "--verify-timeout",
        type=int,
        default=0,
        help="embody: wait up to N seconds for the spawned session (0 = don't wait)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="serve loop poll interval in seconds (default: 30)",
    )
    p.add_argument(
        "--no-reactive",
        action="store_true",
        help="disable push-driven Agent Bridge lifecycle wakes and use only "
        "the configured --interval",
    )
    p.add_argument(
        "--reactive-interval",
        type=float,
        default=2.0,
        help="deprecated compatibility value; push wakes never poll",
    )
    p.add_argument("--supervisor-id", help=argparse.SUPPRESS)
    p.add_argument("--once", action="store_true", help="run a single supervision cycle and exit")
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
    p.add_argument(
        "--headless",
        action="store_true",
        help="fleet (--pool) mode: embody fleet bodies as HEADLESS agent-bridge "
        "ACP sessions on the pool host (via --headless-agent) instead of "
        "CLI/mux embody -- sidesteps the CLI startup-seed 'Loading...' hang, "
        "so bounded sweeps embody reliably on a remote pool host with no "
        "human attach. Ignored outside --pool mode.",
    )
    p.add_argument(
        "--evaluator",
        metavar="SPEC",
        help="path to an evaluator spec (JSON). When set, each cycle feeds every "
        "newly-terminal task's lifecycle event to the evaluator and applies "
        "its decisions (emit a follow-up task) -- the service-driven loop-"
        "advancement pass (emitters-and-evaluators). See 'evaluate'.",
    )
    p.add_argument(
        "--evaluator-ref",
        help="consume only terminal tasks stamped with this producer-owned evaluator id",
    )
    p.set_defaults(func=_cmd_supervise)
    # Registration management subcommands (registered-supervision). Optional: the
    # bare `supervise` (no subcommand) remains the transitional foreground loop,
    # while `supervise register|status|list|remove` manage durable registrations
    # that the singleton supervisor daemon runs. See the vision Behavior
    # *supervise-registers-and-returns*.
    sup_sub = p.add_subparsers(dest="supervise_command")
    rp = sup_sub.add_parser(
        "register",
        help="add a durable supervision registration and RETURN its handle "
        "(does not run the loop; the singleton supervisor runs it)",
    )
    rp.add_argument(
        "--kind",
        choices=sorted(RegistrationKind.DIRECT),
        default="supervised-lane",
        help="the unit kind to register (default: supervised-lane)",
    )
    rp.add_argument(
        "--id",
        help="explicit registration id (default: derived deterministically "
        "from kind+scope+spec, so re-registering upserts)",
    )
    rp.add_argument(
        "--spec",
        metavar="JSON|@FILE",
        help="the unit's spec as inline JSON or @path; required for non-lane "
        "kinds. For supervised-lane, omit it to build the spec from the lane "
        "convenience flags below.",
    )
    rp.add_argument(
        "--machine",
        help="scope the registration to this machine (default: this host's resolved alias)",
    )
    rp.add_argument(
        "--env",
        help="scope the registration to this environment "
        "(default: $AGENT_DISPATCH_ENV or 'default')",
    )
    # supervised-lane convenience flags (used when --spec is omitted)
    rp.add_argument("--repo", help="lane to supervise (default: the calling repo)")
    rp.add_argument(
        "--all-repos", action="store_true", help="supervise every lane (no repo scope)"
    )
    rp.add_argument(
        "--label", action="append", help="only spawn queued tasks carrying this label (repeatable)"
    )
    rp.add_argument(
        "--max-concurrent",
        "--max-active-processes",
        dest="max_concurrent",
        type=int,
        default=1,
        help="pool-local cap on live/launching worker processes (default: 1)",
    )
    rp.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="dead-letter a task after this many failed spawn attempts "
        "(default: 3; 0 = retry forever)",
    )
    rp.add_argument(
        "--label-max-attempts",
        action="append",
        metavar="LABEL=N",
        help="per-label override of --max-attempts (repeatable)",
    )
    rp.add_argument(
        "--embody-backend",
        choices=["headless", "cli", "script"],
        default="headless",
        help="default embody body for the lane: 'headless' (default) "
        "agent-bridge ACP, 'cli' autopilot worktree session, or 'script' "
        "deterministic subprocess",
    )
    rp.add_argument(
        "--headless-label",
        action="append",
        metavar="LABEL",
        help="force tasks carrying this label to a headless agent-bridge "
        "ACP session (repeatable; use when --embody-backend cli)",
    )
    rp.add_argument(
        "--cli-label",
        action="append",
        metavar="LABEL",
        help="force tasks carrying this label to a CLI autopilot instead "
        "of the default headless body (repeatable)",
    )
    rp.add_argument(
        "--script-label",
        action="append",
        metavar="LABEL",
        help="force tasks carrying this label to a deterministic script "
        "subprocess instead of the lane's default body (repeatable)",
    )
    rp.add_argument(
        "--disposable-cli-label",
        action="append",
        metavar="LABEL",
        help="on terminal settlement, conclude this label's exact CLI session "
        "and prime its clean worktree for managed GC (repeatable)",
    )
    rp.add_argument(
        "--headless-agent",
        metavar="AGENT",
        help="agent-bridge agent name for headless embody bodies",
    )
    rp.add_argument(
        "--evaluator",
        metavar="SPEC",
        help="path to an evaluator spec (JSON) folded into the lane spec",
    )
    rp.add_argument(
        "--evaluator-ref",
        help="producer-owned evaluator identity; consume only tasks stamped with it",
    )
    rp.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="serve loop poll interval in seconds (default: 30)",
    )
    rp.add_argument(
        "--ensure",
        action="store_true",
        help="after registering, ensure the singleton supervisor daemon "
        "is running for this (machine, env) -- start it detached if "
        "not (best-effort; a running daemon is a no-op)",
    )
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser("status", help="query a registration by its handle")
    rp.add_argument("id", help="registration id")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser("list", help="list registrations")
    rp.add_argument("--kind", choices=sorted(RegistrationKind.ALL), help="filter by kind")
    rp.add_argument("--machine", help="filter by machine")
    rp.add_argument("--env", help="filter by environment")
    rp.add_argument("--active", action="store_true", help="only active (non-paused) registrations")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser("remove", help="remove a registration by its handle")
    rp.add_argument("id", help="registration id")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser(
        "serve",
        help="run the singleton supervisor daemon (foreground): reconcile the "
        "registration registry into per-unit subprocesses, one master per "
        "(machine, env), single-instance-guarded",
    )
    rp.add_argument("--machine", help="scope: this machine (default: resolved alias)")
    rp.add_argument(
        "--env", help="scope: this environment (default: $AGENT_DISPATCH_ENV or 'default')"
    )
    rp.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="reconcile poll interval in seconds (default: 5)",
    )
    rp.add_argument(
        "--once",
        action="store_true",
        help="reconcile a single time and exit (still lease-guarded)",
    )
    rp.add_argument(
        "--no-single-instance",
        action="store_true",
        help="skip the singleton election (deliberately unguarded; for tests / diagnostics only)",
    )
    rp.add_argument(
        "--no-declared",
        action="store_true",
        help="do not supervise the registrar's DECLARED profile set "
        "(discovered pointers); run only store-backed registrations",
    )
    rp.add_argument(
        "--legacy-env",
        action="store_true",
        help="ALSO supervise legacy AGENT_DISPATCH_SUPERVISE_* env "
        "profiles (supervisor.env + supervisors/*.env) as declarations "
        "-- the Phase 4 migration back-compat bridge, so switching a "
        "host's supervisor unit to `serve` keeps its existing profiles "
        "running until each is migrated to a first-class declaration. A "
        "declaration of the same name wins over a legacy profile.",
    )
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser(
        "daemon-status",
        help="show whether a supervisor daemon holds this (machine, env) scope "
        "and the registrations it would run",
    )
    rp.add_argument("--machine", help="scope: this machine (default: resolved alias)")
    rp.add_argument(
        "--env", help="scope: this environment (default: $AGENT_DISPATCH_ENV or 'default')"
    )
    rp.set_defaults(func=_cmd_supervise)
    op = sup_sub.add_parser(
        "override",
        help="operator kill-switch: locally disable/enable one supervised unit "
        "(by registration id), out of band and taking precedence over its "
        "declaration + the discovery layer (a re-sync does not undo it)",
    )
    op_sub = op.add_subparsers(dest="override_command")
    od = op_sub.add_parser(
        "disable",
        help="disable a supervised unit now: the daemon winds it down on the next "
        "reconcile and keeps it down until re-enabled",
    )
    od.add_argument(
        "id",
        help="registration id of the unit to disable (see `supervise daemon-status` / `list`)",
    )
    od.add_argument("--reason", help="why it is disabled (recorded for legibility)")
    od.set_defaults(func=_cmd_supervise)
    oe = op_sub.add_parser(
        "enable",
        help="clear a unit's override, returning it to its declared/registered state",
    )
    oe.add_argument("id", help="registration id of the unit to re-enable")
    oe.set_defaults(func=_cmd_supervise)
    ol = op_sub.add_parser("list", help="list the current operator overrides")
    ol.set_defaults(func=_cmd_supervise)
    op.set_defaults(func=_cmd_supervise)
    register_reservations_command(sub)

    p = sub.add_parser(
        "_ensure-coordinator",
        help=argparse.SUPPRESS,  # internal: installer-only tier-1 ensure entrypoint
    )
    p.set_defaults(func=_cmd_ensure_coordinator)

    p = sub.add_parser(
        "_stop-coordinator",
        help=argparse.SUPPRESS,  # internal: installer-only direct-stop entrypoint
    )
    p.set_defaults(func=_cmd_stop_coordinator)

    # -- Worker charter: on-demand full behavioral policy prose -----------
    cp = sub.add_parser(
        "charter",
        help="the shared 'how to behave as an agent-dispatch worker' policy "
        "prose, pulled on demand instead of always inlined in a seed",
    )
    csub = cp.add_subparsers(dest="charter_command", required=True)
    csp = csub.add_parser("show", help="print a named charter's full text")
    csp.add_argument("name", help="charter name, e.g. 'autopilot'")
    csp.set_defaults(func=_cmd_charter_show)

    # -- Loop recipes: list / describe / render / kick --------------------
    rp = sub.add_parser(
        "recipes",
        help="loop recipes -- the packaged shapes of long-running agentic work "
        "(reviewer / conflict-resolution / goal-driven), kickable ad-hoc",
    )
    rsub = rp.add_subparsers(dest="recipes_command", required=True)

    lp = rsub.add_parser("list", help="list the available recipes")
    lp.set_defaults(func=_cmd_recipes_list)

    dp = rsub.add_parser("describe", help="show a recipe's full descriptor")
    dp.add_argument("name", help="recipe name (see 'recipes list')")
    dp.set_defaults(func=_cmd_recipes_describe)

    rr = rsub.add_parser(
        "render",
        help="render a recipe with parameters (prints the fields; creates nothing)",
    )
    rr.add_argument("name")
    rr.add_argument(
        "--param",
        action="append",
        metavar="KEY=VALUE",
        help="a recipe parameter (repeatable), e.g. --param repo=owner/name --param pr=42",
    )
    rr.set_defaults(func=_cmd_recipes_render)

    kp = rsub.add_parser(
        "kick",
        help="carve an ad-hoc task from a recipe (optionally spawn a worker to "
        "drive it) -- the no-wrapper-service path",
    )
    kp.add_argument("name")
    kp.add_argument(
        "--param",
        action="append",
        metavar="KEY=VALUE",
        help="a recipe parameter (repeatable)",
    )
    kp.add_argument(
        "--repo",
        help="lane (repo) for the task: a local repo name or remote URL "
        "(default: the calling repo)",
    )
    kp.add_argument("--dedup-key", help="override the derived reserved-work dedup key")
    kp.add_argument(
        "--label",
        action="append",
        metavar="LABEL",
        help="extra label(s) to stamp on the kicked task (repeatable), merged "
        "with the recipe's own labels -- e.g. route the task onto a "
        "supervisor pool with '--label general'",
    )
    kp.add_argument(
        "--spawn",
        action="store_true",
        help="after creating, spawn a worker to drive the loop (best effort)",
    )
    kp.add_argument(
        "--spawn-backend",
        choices=["bridge", "embody"],
        default="embody",
        help="how to embody the worker: 'embody' (default) = a CLI autopilot in a "
        "fresh worktree with a full checkout (the right body for a recipe); "
        "'bridge' = a headless ACP worker",
    )
    kp.add_argument("--spawn-agent", default="task-worker")
    kp.add_argument(
        "--async",
        dest="run_async",
        action="store_true",
        help="with --spawn, don't wait for the worker (fire-and-forget)",
    )
    kp.add_argument("--verify-timeout", type=int, default=0)
    kp.add_argument(
        "--dry-run",
        action="store_true",
        help="print the create call the kick would make, without enqueuing it",
    )
    kp.set_defaults(func=_cmd_recipes_kick)

    # -- Drive-the-worktree-to-resolution: the enforced clean-up verb ------
    rvp = sub.add_parser(
        "resolve",
        help="drive THIS worktree to a clean, resolved final state after a loop "
        "(landed -> verify clean; abandoned -> unwind to base + reconcile source)",
    )
    rvp.add_argument(
        "--outcome",
        required=True,
        choices=["landed", "abandoned"],
        help="how the work ended: 'landed' (merged) or 'abandoned' (unwind to base)",
    )
    rvp.add_argument(
        "--base",
        metavar="BRANCH",
        help="base branch to unwind onto for --outcome abandoned "
        "(default: the branch's tracked upstream)",
    )
    rvp.add_argument(
        "--source",
        metavar="REF",
        help="the change/issue this worker was driving, folded into the "
        "source-reconcile instruction",
    )
    rvp.add_argument("--reason", help="abandonment reason (recorded on the plan)")
    rvp.add_argument(
        "--execute",
        action="store_true",
        help="perform the plan (destructive steps discard working-tree state); "
        "without it, the plan is printed and nothing runs",
    )
    rvp.set_defaults(func=_cmd_resolve)

    # -- Hibernate-the-wait: hand a blocking wait to the layer -------------
    rnp = sub.add_parser(
        "run",
        help="hand a blocking wait to the layer (hibernate-the-wait): run "
        "'-- <cmd>' to completion, then resume the worktree-affinitied "
        "worker via agent-bridge",
    )
    rnp.add_argument(
        "--resume",
        metavar="WORKTREE",
        help="the worker (worktree handle) to resume when the wait resolves "
        "(agent-bridge routes to whichever session is live then)",
    )
    rnp.add_argument(
        "--task",
        metavar="ID",
        help="task id, folded into the resume nudge; with --detach, also the "
        "task atomically suspended once the detached waiter is confirmed live",
    )
    rnp.add_argument("--message", help="override the resume nudge text")
    rnp.add_argument(
        "--detach",
        action="store_true",
        help="run the wait in a detached process that outlives this one, so the "
        "kicking worker can be torn down while it waits (true hibernation)",
    )
    rnp.add_argument(
        "--machine",
        help="override the resolved machine identity (for --detach --task's "
        "suspend call; default: resolved from the CWD worktree)",
    )
    rnp.add_argument(
        "--worktree",
        help="override the resolved worktree identity (for --detach --task's "
        "suspend call; default: resolved from the CWD worktree)",
    )
    rnp.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="the blocking wait command, after '--' (e.g. -- agent-worktrees pr-watch 42)",
    )
    rnp.set_defaults(func=_cmd_run)

    # -- Evaluator: a producer's lifecycle handler ------------------------
    evp = sub.add_parser(
        "evaluate",
        help="feed one task lifecycle event through a declarative evaluator and "
        "apply its decisions (emit a follow-up task, or nothing)",
    )
    evp.add_argument("--spec", required=True, metavar="FILE", help="evaluator spec (JSON)")
    evp.add_argument(
        "--event-file",
        metavar="FILE",
        help="lifecycle event JSON (default: read from stdin)",
    )
    evp.add_argument(
        "--repo", help="lane for any emitted follow-up task (a local name or remote URL)"
    )
    evp.add_argument(
        "--dry-run",
        action="store_true",
        help="print the decisions without creating any follow-up task",
    )
    evp.set_defaults(func=_cmd_evaluate)

    dr = rsub.add_parser(
        "drive",
        help="decide the next loop step for a recipe given a --signal (the "
        "executable work/suspend/resolve rhythm); --execute performs the "
        "suspend + resolve legs",
    )
    dr.add_argument("name")
    dr.add_argument(
        "--signal",
        required=True,
        help="what just happened: 'start', a suspend-on event (e.g. change-updated), "
        "'work-done'/'idle', or a terminal signal (merged/landed/abandoned/closed)",
    )
    dr.add_argument("--resume", metavar="WORKTREE", help="worker to resume on a SUSPEND leg")
    dr.add_argument("--task", metavar="ID", help="task id, folded into a SUSPEND resume")
    dr.add_argument("--base", metavar="BRANCH", help="base branch for a RESOLVE unwind")
    dr.add_argument("--source", metavar="REF", help="change/issue for a RESOLVE reconcile")
    dr.add_argument(
        "--execute",
        action="store_true",
        help="perform the prescribed action (SUSPEND: spawn the detached waiter; "
        "RESOLVE: run the unwind). Needs --resume and a '--' wait command for SUSPEND.",
    )
    dr.add_argument(
        "wait_cmd",
        nargs="*",
        help="for --execute on a SUSPEND, the blocking wait command after '--'",
    )
    dr.set_defaults(func=_cmd_recipes_drive)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DispatchError as exc:
        detail = exc.as_dict()
        if detail.get("code", "").startswith("producer_"):
            json.dump({"error": detail}, sys.stderr, sort_keys=True)
            sys.stderr.write("\n")
        else:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as exc:
        print(f"agent-dispatch: cannot reach coordinator: {exc}", file=sys.stderr)
        return 1
    except httpx.TransportError as exc:
        print(f"agent-dispatch: cannot reach coordinator: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
