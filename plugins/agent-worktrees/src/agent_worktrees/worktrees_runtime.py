"""Resident current-roster authority on the monitor's authenticated control plane."""

from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path

from . import config as cfg
from . import worktrees_observation_contract as observations
from .worktrees_projection import WorktreesProjection, _identity
from .worktrees_projection_index import (
    ProjectionError,
    ProjectionOwnershipChanged,
    ProjectionUnavailable,
    Selection,
)

CONTRACT_VERSION = 1
READ_ACTION = "worktrees-read"
CAPABILITY = "worktrees-current-v1"
_DEMAND_LINGER_SECONDS = 30.0
_FIELDS = frozenset(
    {
        "contract_version",
        "project",
        "worktree_id",
        "codename",
        "status_filter",
        "platform_filter",
        "include_missing",
    }
)


def request_selection(payload: dict) -> tuple[str, dict]:
    if not isinstance(payload, dict) or set(payload) - _FIELDS:
        raise ProjectionError("invalid current-roster request fields")
    if type(payload.get("contract_version")) is not int:
        raise ProjectionError("missing current-roster contract version")
    if payload["contract_version"] not in (CONTRACT_VERSION, observations.VERSION):
        raise ProjectionError("unsupported current-roster contract version")
    project = _identity(payload.get("project"), name="project")
    if cfg._PROJECT_NAME_RE.fullmatch(project) is None:
        raise ProjectionError("invalid project")
    selection = {}
    for name in ("worktree_id", "codename", "status_filter", "platform_filter"):
        value = payload.get(name)
        selection[name] = value
    include_missing = payload.get("include_missing", True)
    if type(include_missing) is not bool:
        raise ProjectionError("invalid include_missing")
    selection["include_missing"] = include_missing
    Selection(**selection)
    if selection["worktree_id"] is not None and selection["codename"] is not None:
        raise ProjectionError("current-roster selectors are mutually exclusive")
    return project, selection


def owner_generation(db_path: Path) -> str | None:
    """Read one ownership slot, only after the monitor has won election."""
    if not db_path.exists():
        return None
    try:
        with closing(
            sqlite3.connect(
                db_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                timeout=0.1,
            )
        ) as conn:
            row = conn.execute(
                "SELECT generation FROM worktrees_projection_owner WHERE singleton=1",
            ).fetchone()
    except sqlite3.Error as exc:
        raise ProjectionUnavailable(f"current-roster owner unavailable: {exc}") from exc
    if row is None:
        return None
    generation = row[0]
    if (
        not isinstance(generation, str)
        or len(generation) != 32
        or any(c not in "0123456789abcdef" for c in generation)
    ):
        raise ProjectionError("invalid current-roster owner generation")
    return generation


class WorktreesRuntime:
    """No request callback loads records or waits for the owner's state lock."""

    def __init__(self, db_path: Path, *, wake=None, now=time.monotonic, elected=None) -> None:
        from .worktrees_feed import WorktreesFeed

        self.store = WorktreesProjection(db_path)
        self._lock = threading.Lock()
        self._activation_lock = threading.Lock()
        self._terminated = False
        self._admitting = False
        self._reason = "unready"
        self._error: str | None = None
        self._publication_errors: dict[str, str] = {}
        self._demand: dict[str, float] = {}
        self._unready_demand: set[str] = set()
        self._wake = wake
        self._now = now
        self._elected = elected
        self.feed = WorktreesFeed(self)

    def activate(self) -> None:
        with self._activation_lock:
            if self._terminated:
                raise ProjectionUnavailable("current-roster runtime is closed")
            try:
                for attempt in range(2):
                    try:
                        self.store.activate(
                            expected_generation=owner_generation(self.store.db_path),
                            authority_check=self._elected,
                        )
                        break
                    except ProjectionOwnershipChanged:
                        if attempt:
                            raise
                    except ProjectionUnavailable:
                        if (
                            attempt or not self.store.superseded
                            or self._elected is None or not self._elected()
                        ):
                            raise
                        with self._lock:
                            self._admitting = False
                        self.feed.close("superseded")
                        # Rollback elects the resident again, not its fenced writer.
                        self.store = WorktreesProjection(self.store.db_path)
            except (ProjectionError, ProjectionUnavailable, sqlite3.Error, OSError) as exc:
                with self._lock:
                    self._admitting = False
                    self._error = str(exc)
                    self._reason = "unavailable"
                self.feed.close("unavailable")
                if isinstance(exc, (sqlite3.Error, OSError)):
                    raise ProjectionUnavailable(str(exc)) from exc
                raise
            with self._lock:
                self._admitting = True
                self._reason = "ready"
                self._error = None
                self._unready_demand.clear()
            self.feed.activate()
            if self._wake is not None:
                self._wake.set()

    def close_admission(self, reason: str = "draining") -> None:
        with self._lock:
            self._admitting = False
            self._reason = reason
        self.feed.close(reason)

    def health(self) -> dict:
        from .worktrees_feed import FEED_CAPABILITY

        with self._lock:
            return {
                "capabilities": [
                    CAPABILITY, FEED_CAPABILITY, observations.CAPABILITY, "picker-bootstrap-v1",
                ],
                "worktrees": {
                    "contract_version": CONTRACT_VERSION,
                    "status": self._reason,
                    "generation": self.store.generation,
                    "error": self._error,
                },
            }

    def picker_bootstrap(self, payload: dict) -> dict:
        from . import picker_bootstrap_runtime
        from .execution_spaces import ExecutionSpaceError

        if self._elected is not None and not self._elected():
            self.close_admission("superseded")
        with self._lock:
            if not self._admitting:
                return self._failure(self._reason, "resident bootstrap admission closed")
            generation = self.store.generation
        try:
            result = picker_bootstrap_runtime.build(payload)
        except (ExecutionSpaceError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            return self._failure("unavailable", str(error))
        if self._elected is not None and not self._elected():
            self.close_admission("superseded")
        with self._lock:
            if not self._admitting or self.store.generation != generation:
                return self._failure("superseded", "resident changed during bootstrap")
            self._demand[payload["project"]] = self._now()
        if self._wake is not None:
            self._wake.set()
        return {
            "ok": True, "contract_version": 1, "generation": generation,
            "runtime_root": str(cfg.install_dir()), **result,
        }

    def handle(self, payload: dict) -> dict:
        try:
            project, selection = request_selection(payload)
        except ProjectionError as exc:
            return self._failure("invalid-request", str(exc))
        if self._elected is not None and not self._elected():
            self.close_admission("superseded")
        with self._lock:
            if not self._admitting:
                return self._failure(
                    self._reason, self._error or "current-roster admission closed"
                )
            now = self._now()
            previous = self._demand.get(project)
            if previous is None or previous < now - _DEMAND_LINGER_SECONDS:
                self._unready_demand.discard(project)
            self._demand[project] = now
            publication_error = self._publication_errors.get(project)
        try:
            result = self.store.read(
                project, **selection, contract_version=payload["contract_version"],
            )
        except ProjectionUnavailable as exc:
            with self._lock:
                if self._admitting and project not in self._unready_demand:
                    self._unready_demand.add(project)
                    if self._wake is not None:
                        self._wake.set()
            return self._failure("unready", publication_error or str(exc))
        except ProjectionError as exc:
            return self._failure("invalid-request", str(exc))
        result = {"ok": True, "contract_version": payload["contract_version"], **result}
        if payload["contract_version"] == observations.VERSION:
            result["project"] = project
        if publication_error is not None:
            result["availability"] = {"status": "stale", "reason": publication_error}
        return result

    def _failure(self, code: str, message: str) -> dict:
        return {
            "ok": False,
            "contract_version": CONTRACT_VERSION,
            "error": {"code": code, "message": message},
            "generation": self.store.generation,
        }

    def demand_projects(self, *, linger: float = _DEMAND_LINGER_SECONDS) -> set[str]:
        with self._lock:
            cutoff = self._now() - linger
            self._demand = {p: at for p, at in self._demand.items() if at >= cutoff}
            self._unready_demand.intersection_update(self._demand)
            projects = set(self._demand)
        return projects | self.feed.projects()

    def publish(self, project: str, rows, *, observed_at: float) -> None:
        with self._activation_lock:
            with self._lock:
                if not self._admitting:
                    raise ProjectionUnavailable("current-roster publisher is not admitted")
            try:
                self.store.publish(project, rows, observed_at=observed_at)
            except ProjectionUnavailable as exc:
                if self.store.superseded:
                    self.close_admission("superseded")
                    with self._lock:
                        self._error = str(exc)
                    if self._wake is not None:
                        self._wake.set()
                raise
        with self._lock:
            self._publication_errors.pop(project, None)
            self._unready_demand.discard(project)
        self.feed.changed(project)

    def publication_failed(self, project: str, error: Exception) -> None:
        project = _identity(project, name="project")
        with self._lock:
            self._publication_errors[project] = str(error)[:1024] or type(error).__name__
        self.feed.changed(project)

    def producer_failed(self, error: Exception) -> None:
        for project in self.store.published_projects() | self.demand_projects():
            self.publication_failed(project, error)

    def shutdown(self) -> None:
        with self._activation_lock:
            self._terminated = True
            self.close_admission("closed")
            self.store.close()
