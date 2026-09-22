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
from pathlib import Path as Path  # noqa: F401 -- compatibility export for tests
from typing import Any

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
    register_recipes_commands,
)
from .registrations import RegistrationKind  # noqa: F401 -- compatibility export for callers/tests
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

from .execution_registration_cli import register_execution_commands
from .declaration_loops_cli import register_declaration_loop_commands
from .registrar_parser_cli import register_registrar_commands
from .supervise_registration_cli import register_supervise_commands

from .task_lifecycle_registration_cli import register_task_lifecycle_commands
from .task_lifecycle_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_abandon,
    _cmd_claim,
    _cmd_claimant,
    _cmd_complete,
    _cmd_embody_interactive,
    _cmd_focus,
    _cmd_force_stop,
    _cmd_pause,
    _cmd_progress,
    _cmd_reattach,
    _cmd_release,
    _cmd_reset,
    _cmd_resume,
    _cmd_show,
    _cmd_start,
    _cmd_suspend,
    _cmd_unpause,
    _cmd_worktree_status,
    _cmd_yield,
)

from .execution_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_charter_show,
    _cmd_evaluate,
    _cmd_resolve,
    _cmd_run,
    _run_resolution_step,
    _spawn_detached_waiter,
    _suspend_for_detached_wait,
)
from .registrar_runtime_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _cmd_registrar,
    _declaration_summary,
    _reject_worktree_checkout_as_repo_root,
)
from .shared_cli import (  # noqa: F401 -- re-exported for existing call sites/tests
    _DashDashParser,
    _hold_actor,
    _owner_from_identity,
    _read_result,
    _resolve_owner,
    _simple,
    _split_owner,
)


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
    register_registrar_commands(sub)

    register_task_lifecycle_commands(sub)

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

    register_declaration_loop_commands(sub)

    register_webhook_command(sub)

    register_supervise_commands(sub)

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

    register_recipes_commands(sub)

    register_execution_commands(sub)

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
