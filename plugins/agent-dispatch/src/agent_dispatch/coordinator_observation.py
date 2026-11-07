"""Observe the local coordinator without starting or changing it."""

from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import math
import os
import re
import socket
from pathlib import Path
from types import SimpleNamespace

from zdd import breadcrumb, diagnostics, routing

from . import rendezvous
from .source_start import accept_readiness, config_identity

SCHEMA = "agent-dispatch.coordinator-serving-observation"
VERSION = 1


class ServingObservationError(RuntimeError):
    """The local coordinator's actual serving state is unconfirmed."""


def _loopback(host: str) -> str:
    if not host:
        return "127.0.0.1"
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host.strip("[]"))
        if address.is_unspecified:
            return "127.0.0.1" if address.version == 4 else "::1"
        if address.is_loopback:
            return str(address)
    except ValueError:
        pass
    raise ServingObservationError("coordinator endpoint is not verified local")


def _legacy_listener(cfg) -> bool:
    host = _loopback(cfg.host)
    if type(cfg.port) is not int or not 0 < cfg.port < 65536:
        raise ServingObservationError("legacy coordinator port is unavailable")
    try:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, cfg.port))
            return False
    except OSError as error:
        if (
            error.errno in (errno.EADDRINUSE, errno.EACCES)
            or getattr(error, "winerror", None) in (10013, 10048)
        ):
            return True
        raise ServingObservationError("legacy coordinator listener is unconfirmed") from None


def _unrouted_evidence(root: Path, run: Path, environment: dict) -> bool:
    return bool(
        os.path.lexists(routing.routing_table_path(root))
        or os.path.lexists(breadcrumb.breadcrumb_path(root))
        or os.path.lexists(rendezvous.endpoint_file(run))
        or environment.get("AGENT_DISPATCH_ENDPOINT")
        or environment.get("AGENT_DISPATCH_URL")
        or str(environment.get("AGENT_DISPATCH_WSL_WINDOWS_CLIENT", "")).strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _local_client(url: str, *, token: str | None, timeout: float):
    import httpx

    from .client import DispatchClient

    return DispatchClient(
        url, token=token, timeout=timeout, transport=httpx.HTTPTransport(trust_env=False),
    )


def observe(
    cfg, routing_root: Path, *, run_dir: Path, token: str | None, environment: dict,
    timeout: float = 2.0,
) -> dict:
    """Confirm an active routed owner, or prove an unevidenced local absence."""
    try:
        root, run = Path(routing_root), Path(run_dir)
        if (
            not root.is_absolute() or not run.is_absolute() or not isinstance(environment, dict)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
        ):
            raise ServingObservationError("coordinator observation inputs are invalid")
        table = routing.read_table(root, strict=True)
        if table is None:
            if (
                _unrouted_evidence(root, run, environment)
                or _legacy_listener(cfg)
                or routing.read_table(root, strict=True) is not None
                or _unrouted_evidence(root, run, environment)
            ):
                raise ServingObservationError("unrouted coordinator ownership is unconfirmed")
            return {"state": "inactive", "generation": None}
        raw = table.get("active")
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if (
            endpoint is None or type(raw.get("port")) is not int or not 0 < endpoint.port < 65536
            or type(raw.get("pid")) is not int or endpoint.pid <= 0
            or type(raw.get("generation")) is not int or endpoint.generation <= 0
            or not isinstance(endpoint.version, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}", endpoint.version) is None
            or endpoint.version.endswith(".") or not endpoint.process_start_time
            or diagnostics.process_start_time(endpoint.pid) != endpoint.process_start_time
        ):
            raise ServingObservationError("routed coordinator ownership is unconfirmed")
        host = _loopback(endpoint.client_host)
        url = f"http://{routing.format_authority(host, endpoint.port)}"
        with _local_client(url, token=token, timeout=min(timeout, 2.0)) as client:
            health = client.health()
        slot = health.get("slot") if isinstance(health, dict) else None
        def poll():
            return None if (
                diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time
            ) else 0

        handle = SimpleNamespace(pid=endpoint.pid, poll=poll)
        if (
            not isinstance(slot, dict) or slot.get("active") != raw
            or not accept_readiness(
                health, handle=handle, version=endpoint.version,
                config_identity=config_identity(cfg), active=True,
            )
            or routing.read_table(root, strict=True) != table
            or diagnostics.process_start_time(endpoint.pid) != endpoint.process_start_time
        ):
            raise ServingObservationError("routed coordinator readiness is unconfirmed")
        return {"state": "ready", "generation": endpoint.version}
    except ServingObservationError:
        raise
    except Exception as error:
        if callable(getattr(error, "retry_cleanup", None)):
            raise
        raise ServingObservationError("coordinator serving state is unavailable") from None


def main(argv: list[str] | None = None) -> int:
    """Probe or reconcile only the selected installation's existing coordinator."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--probe", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
        parser.error("--timeout must be between 0 and 600 seconds")
    stage = "configuration"
    try:
        from . import __version__, config, install_paths, source_start

        root = Path(install_paths.install_dir()).resolve(strict=True)
        if args.apply:
            stage = "selection"
            if not source_start.require_selected_generation().samefile(root):
                raise ServingObservationError("selected coordinator installation changed")
        stage = "configuration"
        captured = {
            key: value for key, value in install_paths.capture_service_environment(root).items()
            if key.upper() not in {
                "UPDATE_DRIVER_WITNESS", "AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER",
            }
        }
        previous = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(captured)
            cfg = config.load_config()
            token = config.client_token()
            if args.apply:
                from . import coordinator_cli

                stage = "reconcile"
                results = []
                code = coordinator_cli._cmd_cutover(
                    argparse.Namespace(
                        json=True, health_timeout=args.timeout, drain_timeout=args.timeout,
                        force=False, recover=False,
                    ),
                    source_environment=captured, reconcile_existing=True,
                    result_sink=results.append,
                )
                if code != 0 or len(results) != 1 or results[0].get("ok") is not True:
                    raise ServingObservationError("coordinator reconcile did not complete")
                stage = "selection"
                source_start.require_selected_generation()
            stage = "observation"
            observed = observe(
                cfg, config.routing_dir(), run_dir=config.run_dir(), token=token,
                environment=captured, timeout=args.timeout,
            )
            if args.apply:
                inactive = results[0].get("status") == "inactive"
                if (
                    (inactive and observed["state"] != "inactive")
                    or (not inactive and (
                        observed["state"] != "ready" or observed["generation"] != __version__
                    ))
                ):
                    raise ServingObservationError("coordinator reconcile serving state changed")
        finally:
            os.environ.clear()
            os.environ.update(previous)
        result = {"schema": SCHEMA, "version": VERSION, "ok": True, **observed}
    except Exception as error:
        if callable(getattr(error, "retry_cleanup", None)):
            raise
        result = {
            "schema": SCHEMA, "version": VERSION, "ok": False, "state": "unknown",
            "reason": stage + "-unavailable",
        }
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
