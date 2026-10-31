"""Read installed planning facts and retain bounded advertised request logs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

MAX_LOG_BYTES = 4 * 1024 * 1024
# Observer capabilities, NOT the applicable portfolio. The installed planner
# chooses that portfolio. Unknown/new residents refuse acceptance.
MARKERS = {
    "agent-worktrees": "status-monitor.lock", "agent-bridge": "running-version.json",
    "agent-codespaces": "connection-owner.live.json", "agent-dispatch": "running-version.json",
    "agent-index": "running-version.json", "agent-vault": r"run\agent-vault-service.pid",
    "worktree-manager": "mux-daemon.lock",
    "agent-index/engine": "engine.pid",
}


@contextmanager
def _consumer_demand(name: str, root: Path):
    endpoint = None
    next_refresh = 0.0
    client_id = None

    def refresh() -> None:
        nonlocal endpoint, next_refresh, client_id
        if time.monotonic() < next_refresh:
            return
        if name == "agent-worktrees":
            from agent_worktrees import list_cache
            list_cache.note_demand("native-suite", argparse.Namespace(),
                                   project="example", tracking_status="active")
        elif name == "worktree-manager":
            from work_coalescing_singleton import client
            from worktree_manager import mux_daemon_cutover as control
            route = control.routed_endpoint(root)
            if route is None:
                raise RuntimeError("Manager consumer cannot resolve the current endpoint")
            connection = control.ControlClient(route.base_url, root=root, timeout=1)
            selected = (connection.host, connection.port, connection.token)
            if client_id is None:
                client_id = client.new_client_id()
            client.subscribe(*selected, client_id, timeout=1)
            if endpoint is not None and endpoint != selected:
                client.release(*endpoint, client_id, timeout=1)
            endpoint = selected
        next_refresh = time.monotonic() + 5

    try:
        yield refresh
    finally:
        if endpoint is not None:
            from work_coalescing_singleton import client
            client.release(*endpoint, client_id, timeout=1)


def _record(root: Path, name: str) -> tuple[dict[str, Any], int]:
    if name in {"worktree-manager", "agent-worktrees", "agent-bridge", "agent-dispatch", "agent-index"}:
        from zdd.routing import read_active_endpoint

        route = read_active_endpoint(root / {
            "worktree-manager": "mux-daemon-routing", "agent-worktrees": "status-monitor-routing",
        }.get(name, ""))
        if route is None:
            raise FileNotFoundError(f"{name}: published resident route missing at {root}")
        return {"pid": route.pid}, 0
    marker = MARKERS.get(name)
    if marker is None:
        raise RuntimeError(f"{name}: no resident marker recognizer; do not reduce the portfolio")
    path = root / marker
    if not path.is_file():
        raise FileNotFoundError(
            f"{name}: missing real resident marker {path}; canonical lifecycle/observer prerequisite unmet",
        )
    value = {"pid": int(path.read_text().strip())} if name in {
        "agent-vault", "agent-index/engine",
    } else json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value.get("pid"), int) or value["pid"] <= 0:
        raise RuntimeError(f"{name}: unsupported resident PID evidence at {path}")
    return value, path.stat().st_mtime_ns


def _resident_probe(name: str, root: Path, expected_pid: int) -> dict[str, Any]:
    """Run inside the installed interpreter; never substitute selected version."""
    record, _ = _record(root, name)
    if record["pid"] != expected_pid:
        raise RuntimeError("Resident changed before health observation")
    if name == "worktree-manager":
        from worktree_manager import mux_daemon_cutover as control
        route = control.routed_endpoint(root)
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Manager route does not name the observed PID")
        status = control.ControlClient(route.base_url, root=root, timeout=1).health()
        ready = status.get("status") == "ready"
        generation = status.get("runtime_generation")
    elif name == "agent-worktrees":
        from agent_worktrees import status_monitor_cutover as control
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(control.routing_dir(root))
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Worktrees route does not name the observed PID")
        status = control.ControlClient(route.base_url, config_dir=root, timeout=1).health()
        ready = status.get("status") == "ready"
        generation = status.get("runtime_generation")
    elif name == "agent-bridge":
        from agent_bridge.client import BridgeClient
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(root)
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Bridge route does not name the observed PID")
        client = BridgeClient.from_config()
        client._timeout, client._connect_grace = 1, 0
        status = client.health()
        ready = status.get("ready") is True and not status.get("draining")
        if status.get("slot", {}).get("pid") != expected_pid:
            raise RuntimeError("Bridge health PID mismatch")
        generation = record.get("generation_id")
    elif name == "agent-dispatch":
        from agent_dispatch import config
        from agent_dispatch.client import DispatchClient
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(config.routing_dir())
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Dispatch route does not name the observed PID")
        with DispatchClient(route.base_url, token=config.client_token(), timeout=1) as client:
            status = client.health()
        ready = status.get("status") == "ok" and not status.get("draining")
        if status.get("slot", {}).get("pid") != expected_pid:
            raise RuntimeError("Dispatch health PID mismatch")
        generation = str(route.generation)
    elif name == "agent-index":
        from agent_index import config
        from agent_index.__main__ import _owned_service_client
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(config.routing_dir())
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Index route does not name the observed PID")
        status, _client = _owned_service_client(route.base_url, timeout=1)
        ready = status.get("status") == "ok"
        if status.get("pid") != expected_pid:
            raise RuntimeError("Index health PID mismatch")
        generation = status.get("runtimeVersion", status.get("version"))
    elif name == "agent-vault":
        from agent_vault.service import send_command
        status = send_command({"action": "ping"})
        ready = isinstance(status, dict) and status.get("ok") is True
        if not ready or status.get("pid") != expected_pid:
            raise RuntimeError("Vault ping did not authenticate the observed service PID")
        generation = status.get("runtime_generation")
    elif name == "agent-index/engine":
        from agent_index.engine import daemon
        status = daemon.health(*daemon.engine_endpoint(), timeout=1)
        if status.get("pid") != expected_pid:
            raise RuntimeError(
                "agent-index engine health must attest its exact PID; owner: "
                "plugins/agent-index/server/src/agent_index_engine/app.py::HealthResponse/health",
            )
        ready = status.get("status") == "ok"
        generation = status.get("generation")
    elif name == "agent-codespaces":
        from agent_codespaces.connection_owner import read_liveness
        beacon = read_liveness()
        if beacon is None or beacon.pid != expected_pid:
            raise RuntimeError("Codespaces beacon PID mismatch")
        ready = beacon.is_fresh()
        status = {"version": beacon.version}
        generation = record.get("process_identity")
    else:
        raise RuntimeError(f"{name}: resident health adapter missing")
    if not status.get("version"):
        raise RuntimeError(
            f"{name}: running producer lacks attributable version; "
            "VaultService ping / connection_owner._write_liveness must publish the running build",
        )
    if not ready:
        raise RuntimeError(f"{name}: actual resident health is not ready")
    return {
        "pid": expected_pid, "version": status["version"],
        "runtime_generation": generation, "ready": True, "ready_observed_ns": time.time_ns(),
        "dependencies": {key: status[key] for key in (
            "gpu_deps_installed", "cuda_available", "device", "model_loaded", "cli",
        ) if key in status},
    }


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError(f"Sealed generation missing or indirect: {path}")
    return {str(item.relative_to(path)): [hashlib.sha256(item.read_bytes()).hexdigest(),
            item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_dev, item.stat().st_ino]
            for item in sorted(path.rglob("*")) if item.is_file() and not item.is_symlink()}


def _engine_probe() -> dict[str, Any]:
    import agent_worktrees
    from agent_worktrees import config, reconcile, update_runtime, update_runtime_plan

    config.set_active_project("example")
    payload = reconcile.core_installed_payload_dir("agent-worktrees")
    targets = update_runtime._registered_plugin_targets()
    engine, modules, registered = update_runtime_plan.plan_portfolio(
        payload, "windows", None, force=True, targets=targets, force_runtimes=(),
    )
    steps = ([engine] if engine is not None else []) + list(modules) + list(registered)
    return {
        "engine": {"root": str(config.install_dir()), "prefix": sys.prefix,
                   "module": agent_worktrees.__file__},
        "portfolio": {step.name: {
            "eligible": step.eligible, "status": step.status,
            "root": str(step.runtime_root) if step.runtime_root else None,
            "payload": str(step.payload), "argv": list(step.argv),
        } for step in steps},
    }


def retain_driver_logs(case: Path, output: str) -> None:
    identifiers = sorted(set(re.findall(r"\bUpdate request ([0-9a-f]{32})\b", output)))
    if not identifiers:
        return
    root = case / "h" / ".copilot-extensions" / "update-driver"
    destination = case / "driver-logs"
    records = []
    for identifier in identifiers:
        record: dict = {"request_id": identifier}
        try:
            source = root / (identifier + ".log")
            if source.is_symlink() or not source.resolve().is_relative_to(case.resolve()):
                raise ValueError("Request log escapes the owned fixture")
            with source.open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - MAX_LOG_BYTES))
                data = stream.read(MAX_LOG_BYTES)
            destination.mkdir(exist_ok=True)
            (destination / source.name).write_bytes(data)
            record.update(bytes=size, retained_bytes=len(data), truncated=size > len(data))
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
        records.append(record)
    try:
        (case / "driver-diagnostics.json").write_text(
            json.dumps(records, indent=2), encoding="utf-8",
        )
    except OSError as error:
        print(f"Driver diagnostic write failed: {error}", file=sys.stderr)


def retain_resident_logs(case: Path, *, incumbent: bool = False) -> None:
    """Keep only known fixture daemon outputs, never scan durable application data."""
    for component, names in {
        "agent-bridge": ("agent-bridge.log", "agent-bridge-err.log", "agent-bridge-watchdog.log",
                         "logs/diagnostics/daemon.jsonl"),
        "agent-codespaces": ("logs/owner.log", "logs/owner-stdio.log"),
    }.items():
        for name in names:
            source = case / "h" / ("." + component) / name
            try:
                if any(path.is_symlink() for path in (source, *source.parents)):
                    raise ValueError("Resident log is indirect")
                if not source.resolve().is_relative_to(case.resolve()):
                    raise ValueError("Resident log escapes the owned fixture")
                with source.open("rb") as stream:
                    size = stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, size - MAX_LOG_BYTES))
                    data = stream.read(MAX_LOG_BYTES)
                relative = Path(name)
                if incumbent:
                    relative = relative.with_name("incumbent-" + relative.name)
                destination = case / "resident-logs" / component / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            except FileNotFoundError:
                continue
            except Exception as error:
                print(f"Resident diagnostic unavailable ({component}/{name}): {error}",
                      file=sys.stderr)
