"""Reconcile an already-serving daemon without constructing or activating a slot."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version
from zdd import diagnostics, process_exit, routing

from .routing_state import active_route_is_forward


def _core():
    from . import __main__ as core

    return core


class ServingReconcileError(RuntimeError):
    """The selected slot cannot safely converge an observed serving runtime."""


def compare_serving_version(running: str, target: str) -> str:
    try:
        left, right = Version(running), Version(target)
    except (InvalidVersion, TypeError) as exc:
        raise ServingReconcileError("serving version could not be verified") from exc
    return "current" if left == right else "older" if left < right else "newer"


def existing_auth_token(root: Path) -> str:
    """Read existing control authority; reconcile never creates or repairs it."""
    import yaml

    try:
        data = yaml.safe_load((root / "auth.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ServingReconcileError(
            "existing Bridge credential is unavailable or unreadable; no credential was created"
        ) from exc
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise ServingReconcileError(
            "existing Bridge credential is empty or malformed; no credential was changed"
        )
    return token


@dataclass
class ServingRuntime:
    root: Path
    route: dict
    version: str
    process: process_exit.ProcessExitReference

    def refuse(self, active: dict | None) -> str | None:
        if active != self.route:
            return "serving predecessor route changed; rerun update from fresh evidence"
        if (
            self.process.wait(0)
            or diagnostics.process_start_time(self.process.pid) != self.process.start_time
        ):
            return "serving predecessor process identity changed"
        return None

    def verify_control(self) -> None:
        """Recheck the captured process at its old port before authenticated control."""
        refusal = self.refuse(self.route)
        table = routing.read_table(self.root, strict=True) or {}
        active = table.get("active")
        previous = table.get("previous")
        expected = routing.Endpoint.from_dict(self.route)
        current = routing.Endpoint.from_dict(active) if isinstance(active, dict) else None
        predecessor = routing.Endpoint.from_dict(previous) if isinstance(previous, dict) else None
        if active_route_is_forward(active) or (
            current != expected and (current is None or predecessor != expected)
        ):
            raise ServingReconcileError("serving ownership changed before predecessor control")
        health = _core()._service_health_on_port(self.route["port"])
        slot = health.get("slot") if isinstance(health, dict) else None
        if (
            refusal
            or not isinstance(health, dict)
            or not isinstance(slot, dict)
            or type(slot.get("pid")) is not int
            or slot.get("pid") != self.process.pid
            or compare_serving_version(health.get("version"), self.version) != "current"
        ):
            raise ServingReconcileError(
                refusal or "serving predecessor no longer owns its observed endpoint"
            )


@contextmanager
def observe_serving(root: Path) -> Iterator[ServingRuntime | None]:
    """Capture a native reference only after the routed daemon identifies itself."""
    try:
        table = routing.read_table(root, strict=True)
        if table is None:
            if (root / "agent-bridge.pid").exists() or any(root.glob("agent-bridge*.lock")):
                raise ServingReconcileError("service markers exist without a verified active route")
            yield None
            return
        active = table.get("active")
        if active_route_is_forward(active):
            yield None
            return
        routing._validate_endpoint_rows(table)
        endpoint = routing.Endpoint.from_dict(active) if isinstance(active, dict) else None
        if (
            endpoint is None
            or type(active.get("pid")) is not int
            or type(active.get("generation")) is not int
            or type(endpoint.pid) is not int
            or endpoint.pid <= 0
            or endpoint.generation <= 0
            or not 0 < endpoint.port < 65536
            or endpoint.bind not in ("127.0.0.1", "::1", "0.0.0.0", "::", "")
            or not isinstance(endpoint.version, str)
            or not endpoint.process_start_time
        ):
            raise ServingReconcileError("active route does not identify an adopted local daemon")
        core = _core()
        health = core._service_health_on_port(endpoint.port)
        slot = health.get("slot") if isinstance(health, dict) else None
        if (
            not isinstance(health, dict)
            or health.get("status") != "ok"
            or health.get("service") != "agent-bridge"
            or health.get("ready") is not True
            or health.get("draining") is True
            or not isinstance(health.get("version"), str)
            or not isinstance(slot, dict)
            or type(slot.get("pid")) is not int
            or slot.get("pid") != endpoint.pid
            or slot.get("role") != "active"
            or slot.get("active") != active
            or compare_serving_version(health["version"], endpoint.version) != "current"
            or not core._pid_is_agent_bridge(endpoint.pid)
        ):
            raise ServingReconcileError("routed daemon health, version or ownership is unverified")
        start = diagnostics.process_start_time(endpoint.pid)
        if not start or endpoint.process_start_time != start:
            raise ServingReconcileError("routed daemon native process identity is unverified")
        with process_exit.open_process(endpoint.pid, start) as reference:
            observed = ServingRuntime(root, active.copy(), health["version"], reference)
            refusal = observed.refuse((routing.read_table(root, strict=True) or {}).get("active"))
            if refusal:
                raise ServingReconcileError(refusal)
            yield observed
    except (OSError, ValueError, NotImplementedError, routing.ActivePublicationRefused) as exc:
        raise ServingReconcileError(
            f"serving runtime observation failed ({type(exc).__name__})"
        ) from exc


def guarded_client(serving: ServingRuntime, base_url: str, token: str, timeout: int):
    from .client import BridgeClient

    class ServingClient(BridgeClient):
        def _request(self, method, path, body=None, *, params=None, request_timeout=None):
            if method == "POST" and path in (
                "/api/v1/drain", "/api/v1/undrain", "/api/v1/shutdown",
            ):
                serving.verify_control()
            return super()._request(
                method, path, body, params=params, request_timeout=request_timeout,
            )

    endpoint = routing.Endpoint.from_dict(serving.route)
    client_type = ServingClient if endpoint is not None and base_url == endpoint.base_url else BridgeClient
    return client_type(base_url, token, timeout=timeout)


def reconcile_serving(args: argparse.Namespace) -> None:
    from . import __version__
    from .config import config_dir
    from .venue_cli import _cmd_deploy

    try:
        if getattr(args, "force", False) or getattr(args, "recover", False):
            raise ServingReconcileError("serving reconcile cannot force retirement or run recovery")
        with observe_serving(Path(config_dir())) as serving:
            state = (
                compare_serving_version(serving.version, __version__)
                if serving is not None else "inactive-or-forwarded"
            )
            if state == "newer":
                raise ServingReconcileError("serving daemon is newer than the selected runtime")
            if state == "older":
                _cmd_deploy(args, serving=serving)
                return
            result = {"ok": True, "serving_reconcile": state, "skipped": serving is None}
    except ServingReconcileError as exc:
        result = {"ok": False, "error": str(exc)}
    if getattr(args, "json", False):
        _core()._json_out(result)
    elif result["ok"]:
        print(f"[OK] Serving runtime reconcile: {result['serving_reconcile']} (no cutover).")
    else:
        print(f"[FAIL] Serving runtime reconcile: {result['error']}", file=sys.stderr)
    sys.exit(0 if result["ok"] else 1)
