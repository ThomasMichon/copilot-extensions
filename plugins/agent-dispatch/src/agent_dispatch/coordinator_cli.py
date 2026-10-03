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
    from .logging_setup import configure_file_logging
    from .server import CoordinatorAlreadyLiveError, serve

    # This daemon normally runs headless (pythonw.exe, no console) -- without
    # this, every log.info/log.warning/log.exception call across the package
    # is silently dropped. See logging_setup's module docstring
    # (copilot-extensions#4978).
    configure_file_logging("coordinator")

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
        control_token=getattr(args, "control_token", None) or _config.resolve_control_token(),
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
    # Overlay the installed service.env onto this process's own environment
    # *before* deriving the cutover configuration, so a durable host/port (or
    # control-token) pin set only in service.env -- not in the triggering
    # process's own ambient environment -- is honored for the cutover/health-
    # check bind, not just for the replacement process's own spawn below.
    from .install_paths import apply_service_env_overlay
    from .install_paths import install_dir as runtime_install_dir

    apply_service_env_overlay(os.environ, runtime_install_dir())
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

        from .install_paths import apply_service_env_overlay
        from .install_paths import install_dir as runtime_install_dir

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
        # Built from the SAME service.env overlay already captured onto
        # os.environ above -- not a fresh re-read here. Re-reading the file
        # at spawn time (which can happen seconds into a cutover) would risk
        # a TOCTOU split: an operator/installer edit landing between the
        # overlay above (which `cfg`/`token`/health-check config were already
        # derived from) and a second read here could start the replacement
        # on a different token/database than the orchestration around it is
        # using, while the rest of this cutover keeps using the first
        # snapshot.
        child_env = dict(os.environ)
        child_env.update(windowless_python_env(python))
        # AGENT_DISPATCH_PORT must be set *after* the overlay snapshot above:
        # the orchestrator already selected this specific free `port` for the
        # passive process to bind (and passes it explicitly via `--port`
        # above), and a stale port pin captured in that snapshot must never
        # override the fresh selection -- `server._server_bind_port()` reads
        # the env var, not the CLI flag.
