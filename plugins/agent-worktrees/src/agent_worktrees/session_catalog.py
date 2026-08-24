"""Bounded resident catalog and worktree-record reconciliation."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from . import config as cfg
from . import installer
from . import sessions
from . import tracking

_DEFAULT_RECORD_BUDGET = 16
_DEFAULT_SESSION_BUDGET = 32


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _session_live_pid(entry: Path) -> int | None:
    try:
        lock_files = list(entry.glob("inuse.*.lock"))
    except Exception:
        return None
    for lock_file in lock_files:
        try:
            parts = lock_file.stem.split(".")
            if len(parts) < 2:
                continue
            pid = int(parts[1])
            if sessions._is_copilot_process(pid):
                return pid
        except Exception:
            continue
    return None


def _canonical_timestamp(value) -> str:
    if value is None:
        return ""
    try:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed.strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError, OverflowError):
        return ""


def _session_observation(entry: Path) -> dict | None:
    try:
        if not entry.is_dir() or sessions._is_detached_session(entry):
            return None
        if not (entry / "session.db").exists() and not (entry / "events.jsonl").exists():
            return None
        ws_file = entry / "workspace.yaml"
        if not ws_file.is_file():
            return None
        loaded = yaml.safe_load(ws_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    cwd = loaded.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    summary = loaded.get("summary") or loaded.get("name") or ""
    return {
        "session_id": entry.name,
        "cwd": cwd,
        "started_at": _canonical_timestamp(
            loaded.get("created_at") or loaded.get("updated_at")),
        "summary": summary.strip() if isinstance(summary, str) else "",
        "entry": entry,
    }


class ResidentSessionReconciler:
    """Incrementally catalog sessions and reconcile records within fixed budgets."""

    def __init__(
        self,
        *,
        record_budget: int = _DEFAULT_RECORD_BUDGET,
        session_budget: int = _DEFAULT_SESSION_BUDGET,
        register_monitor_session: Callable[[str, str | None], bool] | None = None,
        mux_max_age: float = 45.0,
    ) -> None:
        self.record_budget = max(1, record_budget)
        self.session_budget = max(1, session_budget)
        self.register_monitor_session = register_monitor_session
        self.mux_max_age = mux_max_age
        self._projects: list[str] = []
        self._project_cursor = 0
        self._record_project: str | None = None
        self._record_iter = None
        self._project_path_keys: dict[str, set[str]] = {}
        self._pending_paths: dict[str, dict[str, tuple[str, str, Path, str]]] = {}
        self._paths: dict[str, tuple[str, str, Path, str]] = {}
        self._project_head_keys: dict[str, set[tuple[str, str]]] = {}
        self._pending_heads: dict[tuple[str, str], str | None] = {}
        self._heads: dict[tuple[str, str], str | None] = {}
        self._session_iter = None
        self._live_mux: set[str] | None = None
        self._live_mux_at: float | None = None

    def observe_mux(self, session_names: set[str]) -> None:
        """Publish a successful full mux observation for later record stamps."""
        self._live_mux = set(session_names)
        self._live_mux_at = time.monotonic()

    @property
    def has_live_worktree_mux(self) -> bool:
        """Whether the latest successful mux catalog contains any ``wt-*`` root."""
        return bool(
            self._live_mux is not None
            and any(name.startswith("wt-") for name in self._live_mux)
        )

    def _refresh_projects(self) -> None:
        try:
            raw = installer.read_projects_registry().get("projects", {})
        except Exception:
            raw = {}
        names = sorted(
            name for name in raw
            if isinstance(name, str) and name and name != "agent-worktrees"
        )
        if names == self._projects:
            return
        removed = set(self._projects) - set(names)
        for project in removed:
            for key in self._project_path_keys.pop(project, set()):
                self._paths.pop(key, None)
            for key in self._project_head_keys.pop(project, set()):
                self._heads.pop(key, None)
        self._projects = names
        self._project_cursor = 0
        self._close_record_iter()

    def _close_record_iter(self, *, commit: bool = False) -> None:
        project = self._record_project
        if self._record_iter is not None:
            try:
                self._record_iter.close()
            except Exception:
                pass
        if project is not None:
            pending = self._pending_paths.pop(project, {})
            if commit:
                for key in self._project_path_keys.pop(project, set()):
                    self._paths.pop(key, None)
                self._project_path_keys[project] = set(pending)
                self._paths.update(pending)
                for key in self._project_head_keys.pop(project, set()):
                    self._heads.pop(key, None)
                head_keys = {
                    key for key in self._pending_heads if key[0] == project}
                self._project_head_keys[project] = head_keys
                for key in head_keys:
                    self._heads[key] = self._pending_heads.pop(key)
            else:
                for key in [
                    key for key in self._pending_heads if key[0] == project
                ]:
                    self._pending_heads.pop(key, None)
        self._record_iter = None
        self._record_project = None

    def _open_next_project(self) -> bool:
        if not self._projects:
            return False
        project = self._projects[self._project_cursor % len(self._projects)]
        self._project_cursor = (self._project_cursor + 1) % len(self._projects)
        try:
            cfg.set_active_project(project)
            tracking_dir = cfg.tracking_dir()
            iterator = os.scandir(tracking_dir)
        except Exception:
            return False
        self._pending_paths[project] = {}
        self._record_project = project
        self._record_iter = iterator
        return True

    def _repair_head(self, record: tracking.WorktreeRecord) -> bool:
        resolved = record.resolved_head_session
        if record.head_session and record.head_session != resolved:
            record.head_session = resolved
            return True
        return False

    def _index_record(self, project: str, yaml_path: Path) -> dict:
        result = {"records": 0, "heads": 0, "mux": 0, "registered_mux": 0}
        try:
            record = tracking.load_record(yaml_path)
        except Exception:
            return result
        if (record.platform != cfg.detect_platform()
                or record.status != "active"
                or not record.worktree_path):
            return result
        result["records"] = 1
        key = _path_key(record.worktree_path)
        self._pending_paths.setdefault(project, {})[key] = (
            project, record.worktree_id, yaml_path, record.worktree_path)
        self._pending_heads[(project, record.worktree_id)] = (
            record.resolved_head_session)

        if self._repair_head(record):
            try:
                with tracking._RecordLock(yaml_path, blocking=False) as lock:
                    if lock.acquired:
                        fresh = tracking.load_record(yaml_path)
                        if self._repair_head(fresh):
                            tracking.save_record(fresh, yaml_path)
                            result["heads"] = 1
            except Exception:
                pass

        mux_fresh = (
            self._live_mux is not None
            and self._live_mux_at is not None
            and time.monotonic() - self._live_mux_at <= self.mux_max_age
        )
        if mux_fresh:
            live = f"wt-{record.worktree_id}" in self._live_mux
            tracking.stamp_mux_live(
                record.worktree_id, live, refresh=live, sync=True)
            result["mux"] = 1
            if live and self.register_monitor_session is not None:
                if self.register_monitor_session(
                    f"wt-{record.worktree_id}", record.worktree_path
                ):
                    result["registered_mux"] = 1
        return result

    def _scan_records(self) -> dict:
        totals = {"records": 0, "heads": 0, "mux": 0, "registered_mux": 0}
        consumed = 0
        opened = 0
        while consumed < self.record_budget:
            if self._record_iter is None:
                if opened >= max(1, len(self._projects)):
                    break
                opened += 1
                if not self._open_next_project():
                    continue
            try:
                entry = next(self._record_iter)
            except StopIteration:
                self._close_record_iter(commit=True)
                continue
            except OSError:
                self._close_record_iter()
                break
            consumed += 1
            try:
                is_record = entry.is_file() and entry.name.endswith(".yaml")
            except Exception:
                continue
            if not is_record:
                continue
            project = self._record_project
            if project is None:
                continue
            cfg.set_active_project(project)
            result = self._index_record(project, Path(entry.path))
            for key in totals:
                totals[key] += result[key]
        return totals

    def _match_worktree(self, cwd: str):
        current = _path_key(cwd)
        while current:
            match = self._paths.get(current)
            if match is not None:
                return match
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        return None

    @staticmethod
    def _insert_session(
        entries: list[tracking.SessionEntry],
        new_entry: tracking.SessionEntry,
    ) -> None:
        if not new_entry.started_at:
            entries.insert(0, new_entry)
            return
        for index, entry in enumerate(entries):
            if entry.started_at and entry.started_at > new_entry.started_at:
                entries.insert(index, new_entry)
                return
        entries.append(new_entry)

    def _reconcile_session(self, observation: dict) -> dict:
        result = {"sessions": 0, "registered": 0, "pids": 0, "heads": 0}
        match = self._match_worktree(observation["cwd"])
        if match is None:
            return result
        project, worktree_id, yaml_path, _worktree_path = match
        cfg.set_active_project(project)
        try:
            with tracking._RecordLock(yaml_path, blocking=False) as lock:
                if not lock.acquired:
                    return result
                record = tracking.load_record(yaml_path)
                if record.status != "active":
                    return result
                if record.sessions is None:
                    record.sessions = []
                result["sessions"] = 1
                if self._repair_head(record):
                    result["heads"] = 1
                    changed = True
                else:
                    changed = False
                protected_head = self._heads.get((project, worktree_id))
                entry = record.session_entry(observation["session_id"])
                if entry is None:
                    entry = tracking.SessionEntry(
                        session_id=observation["session_id"],
                        started_at=observation["started_at"],
                        pid=_session_live_pid(observation["entry"]),
                    )
                    self._insert_session(record.sessions, entry)
                    result["registered"] = 1
                    changed = True
                else:
                    live_pid = _session_live_pid(observation["entry"])
                    if live_pid is not None and (
                        entry.pid != live_pid or entry.ended_at is not None
                    ):
                        entry.pid = live_pid
                        entry.ended_at = None
                        result["pids"] = 1
                        changed = True
                if (protected_head is not None
                        and record.resolved_head_session != protected_head):
                    record.head_session = protected_head
                    result["heads"] = 1
                    changed = True
                if changed:
                    tracking.save_record(record, yaml_path)
        except Exception:
            return result
        return result

    def _scan_sessions(self) -> dict:
        totals = {
            "scanned_sessions": 0,
            "matched_sessions": 0,
            "registered": 0,
            "pids": 0,
            "heads": 0,
            "cycle_complete": False,
        }
        session_dir = sessions._session_state_dir()
        if not session_dir.exists():
            return totals
        if self._session_iter is None:
            try:
                self._session_iter = os.scandir(session_dir)
            except OSError:
                return totals
        while totals["scanned_sessions"] < self.session_budget:
            try:
                entry = next(self._session_iter)
            except StopIteration:
                try:
                    self._session_iter.close()
                except Exception:
                    pass
                self._session_iter = None
                totals["cycle_complete"] = True
                break
            except OSError:
                try:
                    self._session_iter.close()
                except Exception:
                    pass
                self._session_iter = None
                break
            totals["scanned_sessions"] += 1
            try:
                is_session = entry.is_dir()
            except Exception:
                continue
            if not is_session:
                continue
            observation = _session_observation(Path(entry.path))
            if observation is None:
                continue
            result = self._reconcile_session(observation)
            if result["sessions"]:
                totals["matched_sessions"] += 1
            for key in ("registered", "pids", "heads"):
                totals[key] += result[key]
        return totals

    def step(self) -> dict:
        """Advance both cursors by one bounded batch and return repair counts."""
        prior = cfg.active_project()
        try:
            self._refresh_projects()
            result = self._scan_records()
            session_result = self._scan_sessions()
            for key, value in session_result.items():
                if key in result and isinstance(value, int):
                    result[key] += value
                else:
                    result[key] = value
            return result
        finally:
            cfg.set_active_project(prior)
