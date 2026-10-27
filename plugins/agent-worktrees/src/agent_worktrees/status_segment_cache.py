"""The monitor-owned segment cache and its transient observation custody."""

from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import dataclass, field

from . import config as cfg, git_ops, push_timeout


@dataclass(eq=False)
class _ObservationTicket:
    key: tuple[str, str]
    project: str
    path: str | None
    cleanup: push_timeout.ObservationCleanupError | None = None
    settling: bool = False
    additional: list = field(default_factory=list)
    revision: int = 0


class _StatusSegmentCache:
    def __init__(self, ttl: float = 60.0):
        self.ttl = max(15.0, float(ttl))
        self._entries: dict[str, tuple[float, str]] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()
        self._observations: dict[tuple[str, str], _ObservationTicket] = {}
        self._project_scopes: dict[str, set[tuple[str, str]]] = {}
        self._closing_observations = False

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.realpath(path))

    @staticmethod
    def pending_segment() -> str:
        return "UNKNOWN (cleanup pending)"

    def pending_segment_for_path(self, path: str) -> str:
        return self.pending_segment()

    def _blocked(self, project: str, path: str | None = None) -> bool:
        if self._closing_observations:
            return True
        keys = self._project_scopes.get(project, set())
        target = self._key(path) if path else None
        for ticket in self._observations.values():
            if ticket.project == project or ticket.key in keys:
                return True
            if target is not None and ticket.path:
                try:
                    common = os.path.commonpath((target, ticket.path))
                    if common in (target, ticket.path):
                        return True
                except ValueError:
                    pass
        return False

    def observation_blocked(self, project: str, path: str | None = None) -> bool:
        if not git_ops._windows_observation():
            return False
        with self._lock:
            return self._blocked(project, path)

    def project_for_observation_path(self, path: str) -> str | None:
        target = self._key(path)
        with self._lock:
            for ticket in self._observations.values():
                candidates = [ticket.path]
                if ticket.key[0] == "anchor":
                    candidates.append(ticket.key[1])
                for candidate in candidates:
                    if candidate:
                        try:
                            if os.path.commonpath((target, candidate)) in (target, candidate):
                                return ticket.project
                        except ValueError:
                            pass
        return None

    def begin_observation(self, project: str, anchor: str | None, path: str | None):
        key = ("anchor", self._key(anchor)) if anchor else ("project", project)
        with self._lock:
            self._project_scopes.setdefault(project, set()).add(key)
            if self._blocked(project, path) or key in self._observations:
                raise push_timeout.ObservationCleanupPending("observation scope is not settled")
            ticket = _ObservationTicket(key, project, self._key(path) if path else None)
            self._observations[key] = ticket
            return ticket

    def retain_cleanup(self, ticket, error):
        with self._lock:
            if self._observations.get(ticket.key) is not ticket:
                raise RuntimeError("observation custody ticket changed") from error
            if ticket.cleanup is None:
                ticket.cleanup = error
            elif ticket.cleanup is not error and all(item is not error for item in ticket.additional):
                ticket.additional.append(error)
            ticket.revision += 1
            if ticket.path is None and getattr(error, "observation_path", None):
                ticket.path = self._key(error.observation_path)

    def retain_unclaimed_cleanup(self, project, path, error):
        with self._lock:
            if getattr(error, "_settled", False):
                return
            for ticket in self._observations.values():
                if ticket.cleanup is error or any(item is error for item in ticket.additional):
                    return
            ticket = next(
                (item for item in self._observations.values() if item.project == project), None,
            )
            if ticket is None:
                key = ("project", project)
                ticket = _ObservationTicket(key, project, self._key(path) if path else None)
                self._observations[key] = ticket
            self.retain_cleanup(ticket, error)

    def finish_observation(self, ticket):
        with self._lock:
            if self._observations.get(ticket.key) is ticket and ticket.cleanup is None:
                self._observations.pop(ticket.key)

    def run_observation(self, project, anchor, path, compute):
        if not git_ops._windows_observation():
            return compute()
        ticket = self.begin_observation(project, anchor, path)
        try:
            return compute()
        except push_timeout.ObservationCleanupError as error:
            self.retain_cleanup(ticket, error)
            raise
        finally:
            self.finish_observation(ticket)

    def settle_pending_once(self) -> int:
        with self._lock:
            pending = list(self._observations.values())
        for ticket in pending:
            with self._lock:
                if (self._observations.get(ticket.key) is not ticket
                        or ticket.cleanup is None or ticket.settling):
                    continue
                revision = ticket.revision
                errors = (ticket.cleanup, *ticket.additional)
                ticket.settling = True
            settled = False
            try:
                results = []
                for error in errors:
                    try:
                        results.append(error.try_settle() is True)
                    except Exception:
                        results.append(False)
                settled = all(results)
            finally:
                with self._lock:
                    ticket.settling = False
                    if settled and ticket.revision == revision and self._observations.get(ticket.key) is ticket:
                        self._observations.pop(ticket.key)
        return self.observation_counts()[1]

    def observation_counts(self) -> tuple[int, int]:
        with self._lock:
            pending = sum(ticket.cleanup is not None for ticket in self._observations.values())
            return len(self._observations) - pending, pending

    def close_observation_admission(self) -> None:
        with self._lock:
            self._closing_observations = True

    def open_observation_admission(self) -> None:
        with self._lock:
            self._closing_observations = False

    def custody_observations(self, runtime=None) -> dict[str, int]:
        if not git_ops._windows_observation():
            return {}
        active, pending = self.observation_counts()
        return {
            "observation": active, "observation-cleanup": pending,
            "worktree-status-compute": runtime.active_work_count() if runtime is not None else 0,
        }

    def custody_busy_reasons(self, runtime=None) -> list[str]:
        return [name for name, count in self.custody_observations(runtime).items() if count]

    def observation_busy(self, runtime=None) -> bool:
        return any(self.custody_observations(runtime).values())

    def finish_custody(self, runtime, wait):
        if not git_ops._windows_observation():
            return
        self.close_observation_admission()
        while self.observation_busy(runtime):
            self.settle_pending_once()
            if self.observation_busy(runtime):
                wait()

    @staticmethod
    def degraded_bundle(project, worktree_id, previous=None):
        if not isinstance(previous, dict) or (
            previous.get("project") != project or previous.get("worktree_id") != worktree_id
        ):
            previous = None
        bundle = copy.deepcopy(previous) if previous is not None else {
            "project": project, "worktree_id": worktree_id, "machine": "",
            "started_at": 0.0, "as_of": 0.0,
            "facts": {
                name: {"value": None, "confirmed": False, "observed_at": 0.0}
                for name in ("git_state", "lineage", "session_length", "liveness", "claims", "disposition")
            },
        }
        for fact in bundle.get("facts", {}).values():
            if isinstance(fact, dict):
                fact["confirmed"] = False
        return bundle

    def get(self, path: str) -> str:
        from . import __main__ as core
        from . import status_bar_cli

        project = cfg.project_name() if git_ops._windows_observation() else ""
        input_key = self._key(path)
        with self._lock:
            if self.observation_blocked(project, path):
                return self.pending_segment()
            now = time.monotonic()
            key = self._aliases.get(input_key, input_key)
            cached = self._entries.get(key)
            if cached and now - cached[0] < self.ttl:
                return cached[1]
        record = core._self_override("_find_record_for_path", status_bar_cli._find_record_for_path)(path)
        target = record.worktree_path if record and record.worktree_path else path
        key = self._key(target)
        with self._lock:
            if self.observation_blocked(project, target):
                return self.pending_segment()
            cached = self._entries.get(key)
            if cached and time.monotonic() - cached[0] < self.ttl:
                self._aliases[input_key] = key
                return cached[1]
        try:
            render = core._self_override("_render_status_segment", status_bar_cli._render_status_segment)
            anchor = None
            if git_ops._windows_observation():
                try:
                    anchor = cfg.load_config(include_control_plane_related_pr=False).default_repo.anchor
                except Exception:
                    pass
            value = self.run_observation(
                project, anchor, target,
                lambda: render(
                    target, fetch=False, plain=False, no_title=False, persist_title=True,
                ),
            )
        except push_timeout.ObservationCleanupError as error:
            self.retain_unclaimed_cleanup(project, target, error)
            return self.pending_segment()
        except push_timeout.ObservationCleanupPending:
            return self.pending_segment()
        with self._lock:
            if self.observation_blocked(project, target):
                return self.pending_segment()
            self._aliases[input_key] = key
            self._entries[key] = (time.monotonic(), value)
        return value

    def invalidate(self, cwd: str | None) -> None:
        if not cwd:
            return
        target = self._key(cwd)
        with self._lock:
            removed = set()
            for key in list(self._entries):
                if self.observation_blocked("", key):
                    continue
                try:
                    common = os.path.commonpath((target, key))
                except ValueError:
                    continue
                if common in (key, target):
                    self._entries.pop(key, None)
                    removed.add(key)
            for alias, key in list(self._aliases.items()):
                if self.observation_blocked("", key):
                    continue
                try:
                    common = os.path.commonpath((target, alias))
                except ValueError:
                    common = ""
                if key in removed or common in (alias, target):
                    self._aliases.pop(alias, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._entries = {
                key: value for key, value in self._entries.items()
                if self.observation_blocked("", key)
            }
            self._aliases = {
                alias: key for alias, key in self._aliases.items()
                if self.observation_blocked("", key)
            }
