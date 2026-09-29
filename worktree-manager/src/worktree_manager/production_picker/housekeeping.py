"""Manager-owned Group B Picker lifecycle housekeeping.

This ports the production Picker's Group B lifecycle helpers out of direct
``agent_worktrees.__main__`` calls and into Worktree Manager-owned code.
Step 3 keeps the live runner on the old compatibility path, so this module is
additive for now; Step 4 will switch the foreground Picker to these functions.

The implementation deliberately centralizes the remaining engine-backed pieces
behind local helper functions so the ownership split can tighten in one place
when Step 4 activates the Manager-owned lane and teaches the engine-side
sweepers to skip Manager-owned targets.
"""

from __future__ import annotations

import atexit
import os
from datetime import datetime
from pathlib import Path
import yaml

from .. import mux_mapping_registry
from . import context, engine_group_d, monitor_roots, project_config

REAP_IDLE_GRACE_SECS = 6 * 3600
_NO_AUTO_CLEAN_ENV = "AGENT_WORKTREES_NO_AUTO_CLEAN"
_AUTO_CLEAN_GRACE_ENV = "AGENT_WORKTREES_AUTO_CLEAN_GRACE_SECS"
_DEFAULT_SESSION_GC_GRACE_SECS = 48 * 3600
_MANAGER_OWNED_EXECUTION_PROVIDERS = frozenset({"ahp"})
_MANAGER_OWNED_LAUNCHER_MARKERS = (
    r"worktree-manager\bin\launch-session",
    "worktree-manager/bin/launch-session",
    r"worktree-manager\bin\pane-wrapper",
    "worktree-manager/bin/pane-wrapper",
)


def _output_ok(message: str) -> None:
    print(f"  ✓ {message}")


def _tracking_path() -> Path:
    return project_config.tracking_dir()


def _iso_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def auto_clean_enabled() -> bool:
    return not os.environ.get(_NO_AUTO_CLEAN_ENV)


def _auto_clean_grace_secs() -> float:
    raw = os.environ.get(_AUTO_CLEAN_GRACE_ENV)
    if raw:
        try:
            val = float(raw)
            if val >= 0:
                return val
        except (TypeError, ValueError):
            pass
    return float(_DEFAULT_SESSION_GC_GRACE_SECS)


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _tracking_rows() -> list[dict]:
    rows: list[dict] = []
    try:
        paths = sorted(_tracking_path().glob("*.yaml"))
    except OSError:
        return rows
    for path in paths:
        data = _read_yaml(path)
        if not data:
            continue
        worktree_id = str(data.get("worktree_id") or path.stem).strip()
        if not worktree_id:
            continue
        execution_leg = data.get("execution_leg")
        provider = ""
        if isinstance(execution_leg, dict):
            provider = str(execution_leg.get("provider") or "").strip()
        if not provider:
            session_backend = data.get("session_backend")
            if (
                isinstance(session_backend, dict)
                and str(session_backend.get("kind") or "").strip() == "ahp"
            ):
                provider = "ahp"
        rows.append(
            {
                "worktree_id": worktree_id,
                "execution_leg_provider": provider,
            }
        )
    return rows


def _manager_mapping_snapshot(root: Path | None = None) -> dict[tuple[str, str], dict]:
    registry = mux_mapping_registry.MuxMappingRegistry(
        mux_mapping_registry.registry_path(root)
    )
    return registry.snapshot()


def manager_owned_mux_session_names(
    *,
    project: str | None = None,
    root: Path | None = None,
) -> set[str]:
    names: set[str] = set()
    for (entry_project, _worktree_id), entry in _manager_mapping_snapshot(root).items():
        if project and entry_project != project:
            continue
        session_name = entry.get("mux_session")
        if isinstance(session_name, str) and session_name and entry.get("live", True):
            names.add(session_name)
    return names


def _worktree_id(record) -> str | None:
    value = getattr(record, "worktree_id", None)
    if isinstance(value, str) and value:
        return value
    if isinstance(record, dict):
        value = record.get("worktree_id")
        if isinstance(value, str) and value:
            return value
    return None


def _execution_leg_provider(record) -> str | None:
    execution_leg = getattr(record, "execution_leg", None)
    provider = getattr(execution_leg, "provider", None)
    if isinstance(provider, str) and provider:
        return provider
    if isinstance(record, dict):
        provider = record.get("execution_leg_provider")
        if isinstance(provider, str) and provider:
            return provider
        execution_leg = record.get("execution_leg")
        if isinstance(execution_leg, dict):
            provider = execution_leg.get("provider")
            if isinstance(provider, str) and provider:
                return provider
    return None


def manager_owned_worktree_ids(
    records: list | None = None,
    *,
    project: str | None = None,
    root: Path | None = None,
) -> set[str]:
    owned: set[str] = {
        worktree_id
        for (entry_project, worktree_id), entry in _manager_mapping_snapshot(root).items()
        if (not project or entry_project == project) and entry.get("live", True)
    }
    for record in records or _tracking_rows():
        provider = _execution_leg_provider(record)
        if provider in _MANAGER_OWNED_EXECUTION_PROVIDERS:
            worktree_id = _worktree_id(record)
            if worktree_id:
                owned.add(worktree_id)
    return owned


def is_manager_owned_launcher_shell(cmdline: str | None) -> bool:
    if not isinstance(cmdline, str):
        return False
    lowered = cmdline.casefold()
    return any(marker in lowered for marker in _MANAGER_OWNED_LAUNCHER_MARKERS)


def reap_orphan_mux_sessions(
    *,
    dry_run: bool = False,
    only_id: str | None = None,
    idle_grace_secs: float = REAP_IDLE_GRACE_SECS,
    only_owned: bool = False,
    owned_session_names: set[str] | None = None,
    owned_worktree_ids: set[str] | None = None,
) -> dict:
    project = context.project()
    if only_owned:
        if owned_session_names is None:
            owned_session_names = manager_owned_mux_session_names(project=project)
        if owned_worktree_ids is None:
            owned_worktree_ids = manager_owned_worktree_ids(project=project)
    filtered_ids = set(owned_worktree_ids or ())
    if only_id is not None:
        filtered_ids.add(only_id)
    if only_owned and not filtered_ids and not (owned_session_names or set()):
        return {"available": True, "reaped": [], "skipped": [], "errors": []}
    return engine_group_d.reap_orphan_mux_sessions(
        project,
        worktree_ids=sorted(filtered_ids) if filtered_ids else None,
        dry_run=dry_run,
        idle_grace_secs=idle_grace_secs,
    )


def reap_orphan_launcher_shells(**kwargs) -> dict:
    dry_run = bool(kwargs.pop("dry_run", False))
    if kwargs:
        raise TypeError("launcher-shell sweep wrapper accepts only dry_run")
    if dry_run:
        raise TypeError("launcher-shell sweep wrapper does not support dry-run mode")
    return engine_group_d.reap_orphan_launcher_shells(context.project())


def sweep_managed_worktrees(**kwargs) -> dict:
    if kwargs:
        raise TypeError("managed-worktree sweep wrapper does not accept custom kwargs")
    return engine_group_d.sweep_managed_worktrees(context.project())


def sweep_finished_session_worktrees(**kwargs) -> dict:
    if kwargs:
        raise TypeError("finished-session sweep wrapper does not accept custom kwargs")
    return engine_group_d.sweep_finished_session_worktrees(context.project())


def sweep_managed_on_exit() -> None:
    try:
        report = sweep_managed_worktrees()
        removed = report.get("removed") or []
        if removed:
            _output_ok(
                f"GC'd {len(removed)} leaked managed worktree(s): "
                + ", ".join(x["id"] for x in removed)
            )
    except Exception:
        pass


def sweep_launcher_shells_on_exit() -> None:
    try:
        payload = reap_orphan_launcher_shells(dry_run=False)
        reaped = payload.get("reaped") or []
        if reaped:
            _output_ok(
                f"Reaped {len(reaped)} orphaned launcher shell(s): "
                + ", ".join(str(pid) for pid in reaped)
            )
    except Exception:
        pass


def sweep_finished_sessions_on_cadence() -> None:
    if not auto_clean_enabled():
        return
    try:
        report = sweep_finished_session_worktrees()
        removed = report.get("removed") or []
        if removed:
            _output_ok(
                f"Auto-cleaned {len(removed)} finished worktree(s): "
                + ", ".join(x["id"] for x in removed)
            )
    except Exception:
        pass


def start_picker_monitor_root(project: str | None = None):
    root = monitor_roots.start_picker_monitor_root(project or context.project())
    if root is not None:
        atexit.register(root.close)
    return root


_sweep_managed_on_exit = sweep_managed_on_exit
_sweep_launcher_shells_on_exit = sweep_launcher_shells_on_exit
_sweep_finished_sessions_on_cadence = sweep_finished_sessions_on_cadence
_start_picker_monitor_root = start_picker_monitor_root
