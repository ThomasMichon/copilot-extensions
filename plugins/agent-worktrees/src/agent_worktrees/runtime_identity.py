"""Process-cached generation versus the owner-published slot generation."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from ._build_info import BUILD_INFO

PROCESS_GENERATION = BUILD_INFO.get("runtime_generation")
PROCESS_VERSION = BUILD_INFO.get("runtime_version", BUILD_INFO.get("version"))


def drift_description(entry: dict) -> str:
    """Describe recorded build identities, retaining legacy version-only output."""
    if entry.get("running_generation"):
        running = f"build {entry['running_generation']} ({entry.get('running_slot') or 'unknown slot'})"
        installed = f"{entry['installed_generation']} ({entry.get('installed_slot') or 'unknown slot'})"
    elif entry.get("installed_slot"):
        running, installed = entry["running"], entry["installed_slot"]
    else:
        running, installed = entry["running"], entry["payload"]
    return (
        f"      - {entry['service']}: running {running} but {installed} installed -> "
        f"{entry['service']} service restart"
    )


def monitor_record(root: Path) -> dict | None:
    from . import locks

    record = locks.read_lock(root / "status-monitor.lock")
    if isinstance(record, dict) and locks.lock_is_live(record) and record.get("version"):
        return record
    return None


def generation_drift(deployed: dict | None, running: dict | None) -> dict:
    """Compare only attributable build UUIDs, never a process generation id."""
    deployed, running = deployed or {}, running or {}
    installed = deployed.get("runtime_generation")
    serving = running.get("runtime_generation")
    if not installed or not serving:
        return {}
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)
               for value in (installed, serving)):
        logging.getLogger(__name__).warning("Runtime build generation evidence is malformed")
        return {}
    if installed == serving:
        return {}
    return {
        "running_generation": serving,
        "installed_generation": installed,
        "running_slot": running.get("runtime_slot"),
        "installed_slot": deployed.get("runtime_slot"),
    }


def lag_evidence(payload_dir: Path, root: Path, running: str | None, payload: str | None,
                 deployed: dict | None, record: dict | None, versions_equal) -> dict | None:
    """Keep known package/build drift even if optional slot resolution fails."""
    if running is None or payload is None:
        return None
    from .reconcile_freshness import _resolved_selection, runtime_package_version

    package = runtime_package_version(running)
    drift = generation_drift(deployed, record)
    if not versions_equal(package, payload) or drift:
        return drift
    if package == running:
        return None
    try:
        selected = _resolved_selection(payload_dir, root)
    except (OSError, TypeError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    if (
        selected and not versions_equal(running, selected)
        and versions_equal(package, runtime_package_version(selected))
    ):
        return {"running_slot": running, "installed_slot": selected}
    return None


def slot_generation(slot: Path) -> str | None:
    path = slot / ".runtime-bootstrap" / "runtime-generation"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None  # Legacy runtimes predate generation evidence.
    except OSError as error:
        logging.getLogger(__name__).warning("Runtime generation cannot be read: %s", error)
        return None
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        logging.getLogger(__name__).warning("Runtime generation evidence is malformed: %s", path)
        return None
    return value


def generation_superseded(slot: Path, generation: str | None) -> bool:
    selected = slot_generation(slot)
    return bool(generation and selected and selected != generation)
