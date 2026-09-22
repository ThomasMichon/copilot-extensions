"""Daemon liveness and process-state helpers for ``agent-bridge`` CLI."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _core():
    from . import __main__ as core

    return core


def _active_endpoint():
    """The daemon endpoint recorded in ``active.json``, or ``None``."""
    core = _core()
    try:
        from zdd.routing import read_active_endpoint

        return read_active_endpoint(core._INSTALL_DIR, verify_listener=False)
    except Exception:
        return None


def _active_endpoint_port() -> int | None:
    """The routed daemon port, or ``None`` when no route has been published."""
    endpoint = _active_endpoint()
    if endpoint is not None and endpoint.port:
        return int(endpoint.port)
    return None


def _service_port() -> int:
    """Resolved bridge port: live routing table > config > platform default."""
    from .models import default_port

    live = _active_endpoint_port()
    if live:
        return live

    core = _core()
    cfg_path = os.path.join(core._INSTALL_DIR, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            import yaml

            data = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
            return int(data.get("port") or default_port())
        except Exception:
            pass
    return default_port()


def _service_is_running() -> bool:
    """Quiet health probe -- direct GET, no client error spam."""
    core = _core()
    return core._service_health_on_port(core._service_port()) is not None


def _service_health_on_port(
    port: int, *, timeout: float = 2.0
) -> dict[str, Any] | None:
    """Return one verified agent-bridge health payload for *port*."""
    import http.client
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8"))
    except (
        OSError,
        ValueError,
        UnicodeError,
        http.client.HTTPException,
        urllib.error.URLError,
    ):
        return None
    if (
        not isinstance(body, dict)
        or body.get("status") != "ok"
        or body.get("service") != "agent-bridge"
        or body.get("ready") is not True
    ):
        return None
    return body


def _listening_ports_for_pid(pid: int) -> list[int]:
    """Best-effort listener census for one daemon process."""
    import re
    import subprocess as sp

    if pid <= 0:
        return []
    if sys.platform == "win32":
        command = (
            "@(Get-NetTCPConnection -State Listen -OwningProcess "
            f"{pid} -ErrorAction SilentlyContinue).LocalPort | "
            "Sort-Object -Unique"
        )
        try:
            result = sp.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, sp.TimeoutExpired):
            return []
        return sorted(
            {
                int(line.strip())
                for line in (result.stdout or "").splitlines()
                if line.strip().isdigit()
            }
        )

    try:
        result = sp.run(
            ["ss", "-lptnH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, sp.TimeoutExpired):
        return []
    ports: set[int] = set()
    for line in (result.stdout or "").splitlines():
        if f"pid={pid}," not in line:
            continue
        match = re.search(r":(\d+)\s", line)
        if match:
            ports.add(int(match.group(1)))
    return sorted(ports)


def _undrain_service_at(port: int) -> bool:
    """Release one directly addressed daemon's drain gate."""
    from .client import (
        BridgeClient,
        BridgeClientError,
        BridgeConnectionError,
    )
    from .config import load_or_create_auth_token

    client = BridgeClient(
        f"http://127.0.0.1:{port}",
        load_or_create_auth_token(),
        timeout=10,
    )
    try:
        client.undrain()
    except (
        BridgeClientError,
        BridgeConnectionError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        return False
    return True


def _matching_routed_version(pid: int, port: int) -> str | None:
    """Version from a route entry that names this exact daemon."""
    core = _core()
    from zdd.routing import Endpoint, read_table

    table = read_table(core._INSTALL_DIR) or {}
    for key in ("active", "previous"):
        raw = table.get(key)
        endpoint = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if (
            endpoint is not None
            and endpoint.pid == pid
            and endpoint.port == port
        ):
            return endpoint.version
    return None


def _same_endpoint(left, right) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.bind,
        left.port,
        left.pid,
        left.generation,
    ) == (
        right.bind,
        right.port,
        right.pid,
        right.generation,
    )


def _reconcile_live_dynamic_daemon() -> bool:
    """Adopt a healthy port-0 singleton stranded behind stale routing."""
    core = _core()
    stale = core._active_endpoint()
    pid = core._pid_from_lock(0)
    if not pid:
        return False
    candidate: tuple[int, dict[str, Any]] | None = None
    for port in core._listening_ports_for_pid(pid):
        health = core._service_health_on_port(port)
        if health is not None:
            candidate = (port, health)
            break
    if candidate is None:
        return False
    port, _health = candidate

    from zdd import routing

    with routing._routing_lock(core._INSTALL_DIR):
        table = routing.read_table(core._INSTALL_DIR) or {}
        active_raw = table.get("active")
        current = (
            routing.Endpoint.from_dict(active_raw)
            if isinstance(active_raw, dict)
            else None
        )
        if not core._same_endpoint(current, stale):
            return False
        if (
            current is not None
            and core._service_health_on_port(current.port, timeout=0.25)
            is not None
        ):
            return False
        health = core._service_health_on_port(port)
        if health is None:
            return False
        if health.get("draining") is True:
            if not core._undrain_service_at(port):
                return False
            health = core._service_health_on_port(port)
            if health is None or health.get("draining") is True:
                return False
        version = health.get("version") or core._matching_routed_version(pid, port)
        active, _previous = routing._publish_active_unlocked(
            core._INSTALL_DIR,
            bind=stale.bind if stale is not None else "127.0.0.1",
            port=port,
            pid=pid,
            version=version,
        )
    core._reconcile_service_marker(pid, active.version)
    return True


def _read_pid_file() -> int | None:
    core = _core()
    try:
        with open(core._PID_FILE, encoding="utf-8") as fh:
            return int((fh.read() or "").strip())
    except (OSError, ValueError):
        return None


def _service_pid() -> int | None:
    """Resolve the daemon PID from routing before stale service side files."""
    core = _core()
    endpoint = core._active_endpoint()
    if endpoint is not None and endpoint.pid:
        return int(endpoint.pid)
    port_pid = core._pid_on_port(core._service_port())
    if port_pid:
        return port_pid
    return core._read_pid_file()


def _service_process_is_live(*, probe_timeout: float = 1.0) -> bool:
    """Whether routing or service markers identify a live bridge process."""
    core = _core()
    endpoint = core._active_endpoint()
    candidates = {
        endpoint.pid if endpoint is not None else None,
        core._read_pid_file(),
        core._pid_from_lock(0),
    }
    candidates.discard(None)
    return any(
        core._pid_is_agent_bridge(int(pid), probe_timeout) for pid in candidates
    )


def _wait_for_service_start(
    *,
    timeout: int = 120,
    launch_grace: int = 15,
) -> bool:
    """Wait for liveness while a confirmed daemon process is still starting."""
    import time

    core = _core()
    timeout = max(1, timeout)
    launch_grace = max(1, launch_grace)
    deadline = time.monotonic() + timeout
    no_process_deadline = time.monotonic() + launch_grace
    saw_live_process = False
    while time.monotonic() < deadline:
        if core._service_is_running():
            return True
        probe_timeout = max(
            0.1, min(1.0, deadline - time.monotonic())
        )
        live_process = core._service_process_is_live(
            probe_timeout=probe_timeout
        )
        if live_process:
            saw_live_process = True
            no_process_deadline = time.monotonic() + launch_grace
        elif saw_live_process:
            if time.monotonic() >= no_process_deadline:
                return False
        elif time.monotonic() >= no_process_deadline:
            return False
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1.0, remaining))
    return core._service_is_running()
