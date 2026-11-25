"""Optional fresh configuration reads through the existing authenticated owner."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ACTION = "configuration-owner-read"
CAPABILITY = "configuration-owner-read/v1"
VERSION = 1
FRAME_BYTES = 64 * 1024
READ_SECONDS = 1.25


def prepare() -> bool:
    """Load read dependencies before advertising readiness, without reading config."""
    from importlib import import_module

    try:
        for module in (
            "configuration_layers", "configuration_context",
            f"{__package__}.config", f"{__package__}.configuration_identity",
            f"{__package__}.config_sources", f"{__package__}.projects_registry",
            f"{__package__}.record_config", f"{__package__}.state_root",
            f"{__package__}.machine_config", f"{__package__}.machine_identity",
            f"{__package__}.config_dropins",
        ):
            import_module(module)
    except ImportError:
        return False
    return True


def _legacy_request() -> bool:
    return not any(os.environ.get(key, "").strip() for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT",
    ))


def _root() -> Path:
    from .registry_paths import _legacy_root

    return Path(os.path.abspath(_legacy_root()))


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def discovery_fields(port: int) -> dict[str, Any]:
    """Describe the optional reader in the monitor's existing discovery record."""
    return {"configuration_owner": {
        "contract_version": VERSION, "endpoint": f"127.0.0.1:{port}",
    }}


def _read_discovery(root: Path) -> tuple[int, int, str, str] | None:
    with (root / "status-monitor.lock").open(encoding="utf-8") as source:
        raw = source.read(FRAME_BYTES + 1)
    if len(raw.encode("utf-8")) > FRAME_BYTES:
        return None
    lock = json.loads(raw)
    if not isinstance(lock, dict) or lock.get("installReceipt") or lock.get("marketplaceId"):
        return None
    feature = lock.get("configuration_owner")
    if (
        not isinstance(feature, dict) or type(feature.get("contract_version")) is not int
        or feature["contract_version"] != VERSION
        or not isinstance(feature.get("endpoint"), str)
        or type(lock.get("pid")) is not int or lock["pid"] <= 0
        or not isinstance(lock.get("start_time"), str) or not lock["start_time"]
        or not isinstance(lock.get("runtime_generation"), str) or not lock["runtime_generation"]
    ):
        return None
    host, _, port = feature["endpoint"].rpartition(":")
    if host != "127.0.0.1" or not port.isascii() or not port.isdecimal() or not 0 < int(port) < 65536:
        return None
    return int(port), lock["pid"], lock["start_time"], lock["runtime_generation"]


def read_configuration(payload: dict[str, Any], *, runtime_home: Path) -> dict[str, Any]:
    """Reacquire native sources; never reuse a prior report or change project state."""
    unavailable = {"contract_version": VERSION, "status": "unavailable"}
    if (
        not _legacy_request()
        or set(payload) != {"contract_version", "cwd", "registry_root", "platform"}
        or type(payload.get("contract_version")) is not int
        or payload["contract_version"] != VERSION
        or payload.get("platform") != sys.platform
        or not all(isinstance(payload.get(key), str) and payload[key]
                   for key in ("cwd", "registry_root"))
        or not Path(payload["cwd"]).is_absolute()
        or not Path(payload["registry_root"]).is_absolute()
        or not _same_path(payload["registry_root"], str(runtime_home))
        or not _same_path(str(_root()), str(runtime_home))
    ):
        return unavailable
    from . import config as cfg, config_acquisition as acquisition
    from .config_sources import configuration_report
    from .record_config import metadata_project

    try:
        with acquisition.capture_config_observations() as observations:
            cwd = Path(payload["cwd"])
            project = metadata_project(None, cwd=cwd, absolute_only=True)
            if not project:
                return unavailable
            config = cfg.load_configuration_identity(
                cfg.project_dir(project) / "config.yaml",
                fallback_project=project, absolute_only=True,
            )
            report = configuration_report(config, cwd=str(cwd))
        if any(observation.retained for observation in observations):
            return unavailable
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable
    return {"contract_version": VERSION, "status": "ok", "report": report}


def _request(
    endpoint: tuple[int, int, str, str], token: str, action: str,
    payload: dict[str, Any], deadline: float,
) -> dict[str, Any]:
    from types import SimpleNamespace
    from .control_jsonl import Codec, request

    return request(
        SimpleNamespace(host="127.0.0.1", port=endpoint[0], token=token, timeout=READ_SECONDS),
        action, payload,
        Codec(FRAME_BYTES, json.dumps, json.loads, "configuration owner"),
        deadline=deadline,
    )


def try_read(cwd: str) -> dict[str, Any] | None:
    """Use a capable current resident, otherwise retain the native standalone path."""
    if not _legacy_request():
        return None
    root = _root()
    try:
        deadline = time.monotonic() + READ_SECONDS
        endpoint = _read_discovery(root)
        if endpoint is None:
            return None
        with (root / "status-monitor-control.token").open(encoding="utf-8") as source:
            token = source.read(4097).strip()
        if not token or len(token) > 4096:
            return None
        health = _request(endpoint, token, "health", {}, deadline)
        if (
            health.get("status") != "ready" or health.get("published") is not True
            or type(health.get("pid")) is not int or health["pid"] != endpoint[1]
            or health.get("process_start_time") != endpoint[2]
            or health.get("runtime_generation") != endpoint[3]
            or not isinstance(health.get("capabilities"), list)
            or CAPABILITY not in health["capabilities"]
        ):
            return None
        result = _request(endpoint, token, ACTION, {
            "contract_version": VERSION, "cwd": cwd,
            "registry_root": str(root), "platform": sys.platform,
        }, deadline)
        report = result.get("report")
        if (
            type(result.get("contract_version")) is not int
            or result["contract_version"] != VERSION or result.get("status") != "ok"
            or not isinstance(report, dict)
            or report.get("schema") != "agent-worktrees.configuration"
            or type(report.get("version")) is not int or report["version"] != 1
            or _read_discovery(root) != endpoint
        ):
            return None
        return report
    except (OSError, ValueError, TypeError, AttributeError):
        return None
