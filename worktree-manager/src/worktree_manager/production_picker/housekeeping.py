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
import time
from datetime import datetime
from pathlib import Path

from .. import mux_mapping_registry
from . import context, monitor_roots
from ._engine_runtime import engine_module

REAP_IDLE_GRACE_SECS = 6 * 3600
_NO_AUTO_CLEAN_ENV = "AGENT_WORKTREES_NO_AUTO_CLEAN"
_AUTO_CLEAN_GRACE_ENV = "AGENT_WORKTREES_AUTO_CLEAN_GRACE_SECS"
_MANAGER_OWNED_EXECUTION_PROVIDERS = frozenset({"ahp"})
_MANAGER_OWNED_LAUNCHER_MARKERS = (
    r"worktree-manager\bin\launch-session",
    "worktree-manager/bin/launch-session",
    r"worktree-manager\bin\pane-wrapper",
    "worktree-manager/bin/pane-wrapper",
)


def _compat(name: str):
    return engine_module(name)


def _cfg():
    return _compat("config")


def _tracking():
    return _compat("tracking")


def _sessions():
    return _compat("sessions")


def _activity():
    return _compat("activity")


def _reap_cli():
    return _compat("reap_cli")


def _gc():
    return _compat("gc")


def _status_monitor_runtime():
    return _compat("status_monitor_runtime")


def _output_ok(message: str) -> None:
    print(f"  ✓ {message}")


def _tracking_path() -> Path:
    return _cfg().tracking_dir()


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
    return float(_gc().SESSION_GC_GRACE_SECS)


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


def manager_owned_worktree_ids(
    records: list | None = None,
    *,
    project: str | None = None,
    root: Path | None = None,
) -> set[str]:
    tracking_mod = _tracking()
    owned: set[str] = {
        worktree_id
        for (entry_project, worktree_id), entry in _manager_mapping_snapshot(root).items()
        if (not project or entry_project == project) and entry.get("live", True)
    }
    for record in records or []:
        try:
            execution_leg = tracking_mod.derive_execution_leg(record)
        except Exception:
            continue
        if execution_leg is None:
            continue
        provider = getattr(execution_leg, "provider", None)
        if provider in _MANAGER_OWNED_EXECUTION_PROVIDERS:
            owned.add(record.worktree_id)
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
    now: float | None = None,
    only_owned: bool = False,
    owned_session_names: set[str] | None = None,
    owned_worktree_ids: set[str] | None = None,
) -> dict:
    sessions_mod = _sessions()
    tracking_mod = _tracking()
    activity_mod = _activity()

    all_sessions = sessions_mod._list_mux_sessions()
    if all_sessions is None:
        return {"available": False, "reaped": [], "skipped": [], "errors": []}

    now = time.time() if now is None else now
    activity_by_name = sessions_mod._mux_session_activity()
    by_id = {
        rec.worktree_id: rec for rec in tracking_mod.list_records(_tracking_path())
    }
    by_session = sessions_mod.mux_session_index(by_id)
    if only_owned:
        project = None
        try:
            project = context.project()
        except Exception:
            project = None
        if owned_session_names is None:
            owned_session_names = manager_owned_mux_session_names(project=project)
        if owned_worktree_ids is None:
            owned_worktree_ids = manager_owned_worktree_ids(
                list(by_id.values()), project=project
            )
    reaped: list[str] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for name, attached in all_sessions.items():
        if not name.startswith("wt-"):
            continue
        wt_id = sessions_mod.worktree_id_from_mux_session(name, index=by_session)
        if only_id is not None and wt_id != only_id:
            continue
        if only_owned and name not in (owned_session_names or set()) and wt_id not in (owned_worktree_ids or set()):
            continue
        if attached and attached > 0:
            skipped.append({"id": wt_id, "reason": "attached"})
            continue
        rec = by_id.get(wt_id)
        if rec is None:
            reason = "untracked"
        elif rec.kind in tracking_mod.MANAGED_KINDS:
            skipped.append({"id": wt_id, "reason": rec.kind})
            continue
        elif rec.status in ("finalized", "complete", "completed"):
            reason = rec.status
        elif not (rec.worktree_path and Path(rec.worktree_path).exists()):
            reason = "gone"
        else:
            skipped.append({"id": wt_id, "reason": "active"})
            continue
        last_active = activity_by_name.get(name)
        if last_active is None and rec is not None:
            last_active = _iso_epoch(rec.last_resumed_at) or _iso_epoch(rec.started_at)
        if last_active is None:
            skipped.append({"id": wt_id, "reason": "activity-unknown"})
            continue
        if now - last_active < idle_grace_secs:
            skipped.append({"id": wt_id, "reason": "busy"})
            continue
        if dry_run:
            reaped.append(wt_id)
            continue
        if sessions_mod.kill_tmux_session(wt_id):
            reaped.append(wt_id)
            tracking_mod.stamp_mux_live(wt_id, False, sync=True)
            try:
                activity_mod.log_event("mux_session_reaped", worktree_id=wt_id, reason=reason)
            except Exception:
                pass
        else:
            errors.append({"id": wt_id, "reason": f"kill failed ({reason})"})
    return {"available": True, "reaped": reaped, "skipped": skipped, "errors": errors}


def reap_orphan_launcher_shells(**kwargs) -> dict:
    return _reap_cli().reap_orphan_launcher_shells(**kwargs)


def sweep_managed_worktrees(**kwargs) -> dict:
    return _reap_cli().sweep_managed_worktrees(**kwargs)


def sweep_finished_session_worktrees(**kwargs) -> dict:
    return _reap_cli().sweep_finished_session_worktrees(**kwargs)


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
    status_monitor_runtime = _status_monitor_runtime()
    if not status_monitor_runtime._status_monitor_enabled():
        return None
    try:
        root = monitor_roots.PickerHeartbeat(
            project or context.project(),
            ensure_monitor=status_monitor_runtime._ensure_status_monitor,
        )
        if not root.start():
            return None
        atexit.register(root.close)
        return root
    except Exception:
        return None


_sweep_managed_on_exit = sweep_managed_on_exit
_sweep_launcher_shells_on_exit = sweep_launcher_shells_on_exit
_sweep_finished_sessions_on_cadence = sweep_finished_sessions_on_cadence
_start_picker_monitor_root = start_picker_monitor_root
