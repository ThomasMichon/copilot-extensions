"""Coordinator/service CLI commands extracted from ``__main__.py``.

This is the per-host coordinator command family: serve/cutover/deploy,
federation, and read-only coordinator introspection surfaces. ``__main__.py``
re-exports every moved symbol for backward compatibility, and this module
routes monkeypatch-sensitive cross-calls back through the live
``agent_dispatch.__main__`` module when tests patch that surface.
"""

from __future__ import annotations

import argparse
import json as _json
import os
import socket as _socket
import subprocess as _subprocess
import sys
import sys as _sys
import time
import urllib.request as _urllib
from typing import Any

from . import __version__
from . import config as _config
from .client import DispatchClient
from .config import has_live_local_coordinator
from .loop_commands import _resolve_cli_module


def _proxy(name: str):
    """Delegate to ``agent_dispatch.__main__.<name>`` via ``_resolve_cli_module``."""

    def _fn(*args, **kwargs):
        return getattr(_resolve_cli_module(), name)(*args, **kwargs)

    return _fn


_emit = _proxy("_emit")
_ORIGINAL_CLIENT_TOKEN = _config.client_token
_ORIGINAL_CONFIG_CLASS = _config.Config


def _live_local_coordinator(**kwargs) -> bool:
    """Honor ``agent_dispatch.__main__.has_live_local_coordinator`` monkeypatches."""

    cli = _resolve_cli_module()
    probe = getattr(cli, "has_live_local_coordinator", has_live_local_coordinator)
    return probe(**kwargs)


def _core_helper(name: str, local):
    """Prefer a monkeypatched ``agent_dispatch.__main__`` helper when present."""

    candidate = getattr(_resolve_cli_module(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _client_token_value() -> str | None:
    """Honor both config-level and ``__main__``-level client-token patches."""

    cli = _resolve_cli_module()
    provider = getattr(cli, "client_token", None)
    if (
        callable(provider)
        and provider is not _ORIGINAL_CLIENT_TOKEN
        and provider is not _config.client_token
    ):
        return provider()
    return _config.client_token()


def _config_class():
    """Honor an explicit ``agent_dispatch.__main__.Config`` compatibility patch."""

    cli = _resolve_cli_module()
    candidate = getattr(cli, "Config", None)
    if (
        callable(candidate)
        and candidate is not _ORIGINAL_CONFIG_CLASS
        and candidate is not _config.Config
    ):
        return candidate
    return _config.Config


def _federation_rendezvous(args: argparse.Namespace):
    """Resolve the rendezvous a federation command targets: an explicit ``--url``,
    else the hosted (shared) coordinator. Errors loudly when neither exists."""
    from .federation_runner import build_rendezvous, hosted_rendezvous

    url = getattr(args, "url", None)
    if url:
        return build_rendezvous(url, token=getattr(args, "token", None) or _client_token_value())
    rv = hosted_rendezvous()
    if rv is None:
        print(
            "no hosted coordinator configured -- set AGENT_DISPATCH_SHARED_URL (the "
            "hosted-coordinator endpoint) or pass --url",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return rv


def _cmd_federation_run(args: argparse.Namespace) -> int:
    from .federation_runner import FederationRunner

    rendezvous = _core_helper("_federation_rendezvous", _federation_rendezvous)
    role = args.role or _config.federation_role() or "peer"
    instance = args.instance or _config.federation_instance()
    if not instance:
        print(
            "no instance id -- pass --instance or set AGENT_DISPATCH_FEDERATION_INSTANCE",
            file=sys.stderr,
        )
        return 2
    rv = rendezvous(args)
    runner = FederationRunner(rv, instance, role=role, machine=instance, lease_ttl=args.lease_ttl)
    if args.once:
        return _emit(runner.tick())
    interval = args.interval if args.interval is not None else _config.federation_interval()
    try:
        runner.run(interval=interval)
    except KeyboardInterrupt:
        pass
    finally:
        runner.resign()
    return 0


def _cmd_federation_status(args: argparse.Namespace) -> int:
    """Discovered coordinator + peers, plus a ``self`` role/instance/gate_state
    section (see :func:`agent_dispatch.federation_runner.satellite_self_status`)."""
    from .federation_runner import satellite_self_status

    rendezvous = _core_helper("_federation_rendezvous", _federation_rendezvous)
    rv = rendezvous(args)
    role = getattr(args, "role", None) or _config.federation_role()
    instance = getattr(args, "instance", None) or _config.federation_instance()
    self_info = satellite_self_status(role, instance)
    return _emit(
        {"coordinator": rv.discover_coordinator(), "peers": rv.discover_peers(), "self": self_info}
    )


def _cmd_installer_readiness(args: argparse.Namespace) -> int:
    from .installer_readiness import emit, evaluate

    def probe() -> dict:
        with _resolve_cli_module()._client(args, ensure=False) as client:
            return client.health()

    return emit(evaluate(probe))


def _cmd_health(args: argparse.Namespace) -> int:
    return _emit(_resolve_cli_module()._client(args, ensure=False).health())


def _cmd_print_endpoint(args: argparse.Namespace) -> int:
    """Print this machine's local coordinator base URL (``http://host:port``).

    A peer resolves this over SSH (``ssh <alias> agent-dispatch print-endpoint``)
    to discover the dynamic loopback port to port-forward to for SSH failover.
    Local-only: it reports *this* host's coordinator, never a failover target.
    """
    print(_resolve_cli_module().client_url())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import CoordinatorAlreadyLiveError, serve

    passive = bool(getattr(args, "passive", False))
    force = bool(getattr(args, "force", False))
    base = _config.load_config()
    effective_token = args.token or base.token
    if not passive and not force and _live_local_coordinator(token=effective_token):
        print(
            "agent-dispatch: a coordinator is already live and answering on this "
            "host; refusing to start a second one non-passively (it would seize "
            "the active route without draining the running one -- see "
            "ThomasMichon/copilot-extensions#3066). Use `agent-dispatch deploy` "
            "for a graceful, zero-downtime cutover onto new code, or pass "
            "--force if you intend a deliberate, unmanaged manual restart.",
            file=sys.stderr,
        )
        return 2
    _reroot_serve_cwd()
    cfg = _config_class()(
        host=_resolve_serve_host(args, base),
        port=args.port or base.port,
        db_path=args.db or base.db_path,
        token=effective_token,
        control_token=getattr(args, "control_token", None) or base.control_token,
    )
    try:
        serve(cfg, passive=passive, force=force)
    except CoordinatorAlreadyLiveError as exc:
        if force:
            print(
                f"agent-dispatch: {exc} (ThomasMichon/copilot-extensions#3066). "
                "--force does not bypass this lock -- retry once the other "
                "process finishes, or terminate it if it's genuinely wedged.",
                file=sys.stderr,
            )
        else:
            print(
                f"agent-dispatch: {exc} (ThomasMichon/copilot-extensions#3066). Use "
                "`agent-dispatch deploy` for a graceful, zero-downtime cutover onto "
                "new code, or pass --force if you intend a deliberate, unmanaged "
                "manual restart.",
                file=sys.stderr,
            )
        return 2
    return 0


def _reroot_serve_cwd() -> None:
    # The coordinator must not keep the plugin payload as cwd (#621); on Windows
    # the process cwd locks that directory and blocks payload replacement.
    try:
        from .runtime_version import install_dir

        target = install_dir()
    except Exception as exc:
        print(
            f"agent-dispatch: warning: could not resolve runtime cwd; using home: {exc}",
            file=sys.stderr,
        )
        from pathlib import Path

        target = Path.home()
    try:
        target.mkdir(parents=True, exist_ok=True)
        os.chdir(target)
    except Exception as exc:
        print(
            f"agent-dispatch: warning: could not switch runtime cwd to {target}: {exc}",
            file=sys.stderr,
        )


def _reap_superseded_coordinators(result: Any) -> None:
    try:
        from zdd.routing import read_table

        from .reap import reap_superseded_coordinators

        new_port = getattr(result, "new_port", None)
        table = read_table(_config.routing_dir()) or {}
        active = table.get("active") if isinstance(table, dict) else None
        keep = {os.getpid()}
        if (
            isinstance(active, dict)
            and new_port
            and active.get("port") == new_port
            and active.get("pid")
        ):
            keep.add(int(active["pid"]))
        else:
            result.steps.append(
                "reap skipped: could not confirm the promoted coordinator "
                "against the routing table"
            )
            return
        reap = reap_superseded_coordinators(keep_pids=keep)
        if reap.reaped:
            result.steps.append(
                f"reaped {len(reap.reaped)} superseded coordinator(s): {reap.reaped}"
            )
        for err in reap.errors:
            result.steps.append(f"reap: {err}")
    except Exception as exc:  # best-effort: reap must never fail a cutover
        try:
            result.steps.append(f"reap skipped: {exc}")
        except Exception:
            pass


def _reap_abandoned_passive_impl(
    record: dict | None, *, grace_seconds: float | None = None,
) -> dict:
    """Retire a passive daemon stranded by an abandoned cutover (#5195)."""
    from .reap import reap_abandoned_passive_backstop

    return reap_abandoned_passive_backstop(
        _config.routing_dir(), record=record, grace_seconds=grace_seconds,
    )


def _reap_abandoned_passive(record: dict | None, *, grace_seconds: float | None = None) -> dict:
    return _reap_abandoned_passive_impl(record, grace_seconds=grace_seconds)


def _add_cutover_flags(p: argparse.ArgumentParser) -> None:
    """Flags shared by the ``deploy`` and ``_cutover`` subparsers."""
    p.add_argument(
        "--health-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for the new coordinator's /health to "
        "report ready before rolling back",
    )
    p.add_argument(
        "--drain-timeout",
        type=float,
        default=300.0,
        help="seconds to wait for the old coordinator's in-flight "
        "claim to settle before giving up on a graceful drain",
    )
    p.add_argument(
        "--force", action="store_true", help="flip and retire even if the drain timeout elapses"
    )
    p.add_argument(
        "--recover",
        action="store_true",
        help="only heal a prior aborted cutover left in a drained "
        "state, then exit (does not start a new cutover)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON.")


def _cmd_cutover(args: argparse.Namespace) -> int:
    """Zero-downtime graceful cutover -- shared by ``deploy`` and ``_cutover``."""
    from zdd import breadcrumb
    from zdd.cutover import CutoverOrchestrator

    cli = _resolve_cli_module()
    core_time = getattr(cli, "time", time)
    reap_abandoned = _core_helper("_reap_abandoned_passive", _reap_abandoned_passive)
    reap_superseded = _core_helper("_reap_superseded_coordinators", _reap_superseded_coordinators)
    cfg = _config.load_config()
    token = _client_token_value()
    wildcard_v4 = ".".join(("0", "0", "0", "0"))
    host = cfg.host if cfg.host not in (wildcard_v4, "", "::", "[::]") else "127.0.0.1"
    if cfg.host in ("::", "[::]"):
        host = "::1"
    routing_bind = "::" if cfg.host == "[::]" else cfg.host

    def pick_free_port() -> int:
        family = _socket.AF_INET6 if ":" in host else _socket.AF_INET
        with _socket.socket(family, _socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def spawn_passive(port: int):
        from agent_procutil import detached_kwargs, windowless_python, windowless_python_env

        python = _sys.executable
        cmd = [
            windowless_python(python),
            "-m",
            "agent_dispatch",
            "serve",
            "--host",
            cfg.host,
            "--port",
            str(port),
            "--passive",
        ]
        child_env = dict(os.environ)
        child_env["AGENT_DISPATCH_PORT"] = str(port)
        child_env.update(windowless_python_env(python))
        kwargs: dict[str, Any] = {
            "env": child_env,
            "stdin": _subprocess.DEVNULL,
            "stdout": _subprocess.DEVNULL,
            "stderr": _subprocess.DEVNULL,
        }
        kwargs.update(detached_kwargs())
        return _subprocess.Popen(cmd, **kwargs)  # noqa: S603

    def health_check(check_host: str, port: int) -> bool:
        try:
            req = _urllib.Request(f"http://{check_host}:{port}/health")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with _urllib.urlopen(req, timeout=2) as resp:  # noqa: S310 -- loopback
                if resp.status != 200:
                    return False
                payload = _json.loads(resp.read().decode("utf-8"))
                return payload.get("status") != "draining"
        except Exception:
            return False

    def make_client(base_url: str):
        return DispatchClient(base_url, token=token, timeout=float(args.drain_timeout) + 60.0)

    def liveness_check(check_host: str, port: int) -> bool:
        try:
            req = _urllib.Request(f"http://{check_host}:{port}/health")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with _urllib.urlopen(req, timeout=2) as resp:  # noqa: S310 -- loopback
                return resp.status == 200
        except Exception:
            return False

    from .single_instance import SingleInstance, read_holder_pid

    routing_lock_path = _config.routing_dir() / "serve-start.lock"
    routing_lock = SingleInstance(routing_lock_path)
    acquired = routing_lock.acquire()
    if not acquired:
        deadline = core_time.monotonic() + 30.0
        while core_time.monotonic() < deadline:
            core_time.sleep(0.5)
            acquired = routing_lock.acquire()
            if acquired:
                break
    if not acquired:
        holder_pid = read_holder_pid(routing_lock_path)
        holder_note = f" (held by pid {holder_pid})" if holder_pid else ""
        print(
            "agent-dispatch: another process is concurrently starting or "
            f"cutting over a coordinator on this host{holder_note}; refusing "
            "to race it for the active route after waiting 30s for it to "
            "finish. Retry once it finishes, or -- if that process is "
            "genuinely wedged, not just slow -- terminate it externally "
            "first.",
            file=sys.stderr,
        )
        return 2
    try:
        recovery = breadcrumb.recover_stale_cutover(
            _config.routing_dir(), make_client, health_check=liveness_check
        )
        _pre_recovery_breadcrumb = breadcrumb.read_breadcrumb(_config.routing_dir())
        passive_reap = reap_abandoned(_pre_recovery_breadcrumb)
        if getattr(args, "recover", False):
            recovery["passive_reap"] = passive_reap
            _emit(recovery)
            return 0
        if recovery.get("recovered"):
            print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}", file=sys.stderr)
        if passive_reap.get("reaped"):
            print(
                f"[>] Reaped an abandoned never-promoted passive (pid={passive_reap.get('pid')})",
                file=sys.stderr,
            )

        orch = CutoverOrchestrator(
            _config.routing_dir(),
            bind=routing_bind,
            version=__version__,
            spawn_passive=spawn_passive,
            health_check=health_check,
            make_client=make_client,
            pick_free_port=pick_free_port,
        )
        result = orch.run(
            health_timeout=args.health_timeout,
            drain_timeout=args.drain_timeout,
            force=args.force,
        )
        if result.ok:
            reap_superseded(result)
    finally:
        routing_lock.release()
    if getattr(args, "json", False):
        _emit(result.to_dict())
    else:
        for step in result.steps:
            print(f"  - {step}", file=sys.stderr)
        if result.ok:
            print(f"Cutover complete: coordinator now on port {result.new_port}.", file=sys.stderr)
        elif result.rolled_back:
            print(f"[WARN] Cutover rolled back: {result.error}", file=sys.stderr)
        else:
            print(f"[FAIL] Cutover failed: {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


def _resolve_serve_host(args: argparse.Namespace, base: _config.Config) -> str:
    """The host the coordinator binds when ``agent-dispatch serve`` runs."""
    if args.host:
        return args.host
    if "AGENT_DISPATCH_HOST" in os.environ:
        return base.host
    if sys.platform == "win32":
        from .netinfo import resolve_bind_host

        return _resolve_bind_host_resilient(resolve_bind_host)
    return base.host


def _resolve_bind_host_resilient(
    resolver,
    *,
    retries: int | None = None,
    delay: float | None = None,
    sleep=None,
    log=None,
):
    """Resolve the Windows bind host, tolerating a logon-before-WSL race (#2889)."""
    if retries is None:
        retries = int(os.environ.get("AGENT_DISPATCH_BIND_RETRIES", "20"))
    if delay is None:
        delay = float(os.environ.get("AGENT_DISPATCH_BIND_RETRY_DELAY", "3"))
    if sleep is None:
        sleep = time.sleep
    if log is None:

        def log(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)

    retries = max(1, retries)
    last_err: RuntimeError | None = None
    for attempt in range(1, retries + 1):
        try:
            return resolver()
        except RuntimeError as exc:
            last_err = exc
            log(
                "agent-dispatch: bind host not resolvable yet "
                f"(attempt {attempt}/{retries}): {exc}"
            )
            if attempt < retries:
                sleep(delay)
    raise last_err  # type: ignore[misc]


def _cmd_retire_supervisors(args: argparse.Namespace) -> int:
    """Internal Windows installer seam: retire every supervisor generation."""
    from .supervisor_processes import retire_windows_supervisor_generations

    result = retire_windows_supervisor_generations(args.install_dir)
    payload = {
        "ok": result.ok,
        "selected": result.selected,
        "retired": result.retired,
        "errors": result.errors,
    }
    _emit(payload)
    return 0 if result.ok else 1


def register_coordinator_commands(sub) -> None:
    p = sub.add_parser("serve", help="run the per-host coordinator")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--db")
    p.add_argument(
        "--passive",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="start even if a coordinator is already live and answering on this "
        "host (bypasses the #3066 guard); the running one is left undrained -- "
        "prefer `agent-dispatch deploy` for a graceful cutover instead",
    )
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("_cutover", help=argparse.SUPPRESS)
    _add_cutover_flags(p)
    p.set_defaults(func=_cmd_cutover)

    p = sub.add_parser(
        "deploy",
        help="zero-downtime redeploy to the currently installed code",
        description=(
            "Cut over to the code installed in this interpreter's venv: spawns "
            "a new coordinator on a fresh port, waits for it to report healthy, "
            "flips the routing table so clients follow it, drains the old "
            "coordinator's in-flight claim, then retires it. Run this after "
            "installing new code (e.g. `install.ps1 update` on Windows / "
            "`install.sh update` on POSIX) -- either by hand, "
            "or it is invoked automatically by a running coordinator's own "
            "self-update loop once it notices a newer version has been "
            "published (opt-in via AGENT_DISPATCH_SELF_UPDATE=1)."
        ),
    )
    _add_cutover_flags(p)
    p.set_defaults(func=_cmd_cutover)

    p = sub.add_parser("_retire-supervisors", help=argparse.SUPPRESS)
    p.add_argument("--install-dir", required=True)
    p.set_defaults(func=_cmd_retire_supervisors)

    p = sub.add_parser(
        "federation",
        help="federation runtime: register presence + drive the fenced-epoch "
        "coordinator lease over the rendezvous directory (hosted backend)",
    )
    fed_sub = p.add_subparsers(dest="federation_command", required=True)
    sp = fed_sub.add_parser(
        "run",
        help="run the federation loop (presence + lease) until stopped",
    )
    sp.add_argument(
        "--role",
        choices=sorted(_config.FEDERATION_ROLES),
        help="this node's role (default: AGENT_DISPATCH_FEDERATION_ROLE or peer)",
    )
    sp.add_argument(
        "--instance",
        help="stable directory id (default: AGENT_DISPATCH_FEDERATION_INSTANCE or the machine id)",
    )
    sp.add_argument(
        "--url",
        help="rendezvous coordinator URL (default: the hosted coordinator / "
        "AGENT_DISPATCH_SHARED_URL)",
    )
    sp.add_argument("--token", help="bearer token for --url")
    sp.add_argument(
        "--interval",
        type=float,
        help="seconds between ticks (default: AGENT_DISPATCH_FEDERATION_INTERVAL)",
    )
    sp.add_argument(
        "--lease-ttl",
        type=float,
        dest="lease_ttl",
        help="staleness threshold before a standby fails over (lease-eligible roles)",
    )
    sp.add_argument(
        "--once",
        action="store_true",
        help="run a single tick and exit (print the resulting state)",
    )
    sp.set_defaults(func=_cmd_federation_run)
    sp = fed_sub.add_parser(
        "status", help="coordinator + peers, plus this node's own role/gate state"
    )
    sp.add_argument("--url", help="rendezvous coordinator URL (default: the hosted coordinator)")
    sp.add_argument("--token", help="bearer token for --url")
    sp.add_argument(
        "--role", choices=sorted(_config.FEDERATION_ROLES), help="this node's role, for self.gate_state"
    )
    sp.add_argument("--instance", help="this node's directory id, for self.instance")
    sp.set_defaults(func=_cmd_federation_status)

    p = sub.add_parser("health", help="check coordinator health")
    p.set_defaults(func=_cmd_health)

    p = sub.add_parser(
        "installer-readiness",
        help="emit the plugin-owned installer/readiness contract state as JSON",
    )
    p.set_defaults(func=_cmd_installer_readiness)

    pe = sub.add_parser(
        "print-endpoint",
        help="print this machine's local coordinator base URL (for SSH-failover peer discovery)",
    )
    pe.set_defaults(func=_cmd_print_endpoint)
