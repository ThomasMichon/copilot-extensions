"""Resident current-roster authority on the monitor's authenticated control plane."""

from __future__ import annotations

import math
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
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
_FRESHNESS_FLOOR_SECONDS = 45.0
_CHANGED_PATH_LIMIT = 256
_CHANGED_RETRY_SWEEPS = 3
_FIELDS = frozenset(
    {
        "contract_version",
        "project",
        "worktree_id",
        "codename",
        "status_filter",
        "platform_filter",
        "include_missing",
        "bootstrap",
        "passive",
        "refresh_id",
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
    for flag in ("bootstrap", "passive"):
        if flag in payload and type(payload[flag]) is not bool:
            raise ProjectionError(f"invalid {flag}")
    Selection(**selection)
    if selection["worktree_id"] is not None and selection["codename"] is not None:
        raise ProjectionError("current-roster selectors are mutually exclusive")
    if "refresh_id" in payload:
        refresh_id = payload["refresh_id"]
        if (
            not isinstance(refresh_id, str) or len(refresh_id) != 32
            or any(c not in "0123456789abcdef" for c in refresh_id)
            or selection["worktree_id"] is None
            or payload.get("passive") is True or payload.get("bootstrap") is True
        ):
            raise ProjectionError("point refresh requires an exact selection and a valid request ID")
    return project, selection


def freshness_bound_for(interval: float) -> float:
    """How long a project's last publication may prove a passive read current."""
    return max(_FRESHNESS_FLOOR_SECONDS, 3.0 * float(interval))


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

    @classmethod
    def for_monitor(cls, wake, admitted, context, *, interval: float = 30.0):
        import os

        from . import status_monitor_cutover, status_monitor_runtime
        from .hook_facts_publication import HookFactsPublisher
        from .runtime_identity import PROCESS_GENERATION

        root = status_monitor_runtime._aw_runtime_home()
        return cls(
            root / "worktrees-current.sqlite3", wake=wake, admitted=admitted,
            elected=lambda: status_monitor_cutover.active_generation_for_pid(os.getpid()) is not None,
            hook_facts=HookFactsPublisher(
                root, PROCESS_GENERATION, project_root=cfg.project_dir, context=context,
            ),
            freshness_bound=freshness_bound_for(interval),
        )

    def __init__(
        self, db_path: Path, *, wake=None, now=time.monotonic, elected=None, admitted=None,
        hook_facts=None, freshness_bound: float = freshness_bound_for(30.0),
    ) -> None:
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
        # Monotonic start of each project's last successful publication by this
        # resident. Passive reads compare it with the bound at request time, so
        # a hung sweep loop ages out instead of vouching for an old roster.
        self._refreshed: dict[str, float] = {}
        self._freshness_bound = float(freshness_bound)
        self._changed_paths: set[str] = set()
        self._changed_all = False
        # Changed projects awaiting a successful publication -> sweeps taken.
        self._changed_pending: dict[str, int] = {}
        self._wake = wake
        self._now = now
        self._elected = elected
        self._admitted = admitted
        self.hook_facts = hook_facts
        self.feed = WorktreesFeed(self)
        from .agenda_runtime import AgendaRuntime

        self.agendas = AgendaRuntime(self)
        from .owner_observation_adapter import OwnerObservationAdapter

        self.observations = OwnerObservationAdapter(self)
        from .owner_observation_native_intake import NativeIntake

        self.native_observations = NativeIntake(self)
        from .worktrees_refresh import RefreshRequests

        self.refreshes = RefreshRequests()

    @property
    def _read_elected(self):
        return self._elected if self._admitted is None else self._admitted

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
                        self.close_admission("superseded")
                        # Rollback elects the resident again, not its fenced writer.
                        self.store = WorktreesProjection(self.store.db_path)
            except (ProjectionError, ProjectionUnavailable, sqlite3.Error, OSError) as exc:
                with self._lock:
                    self._admitting = False
                    self._error = str(exc)
                    self._reason = "unavailable"
                self.close_admission("unavailable")
                if isinstance(exc, (sqlite3.Error, OSError)):
                    raise ProjectionUnavailable(str(exc)) from exc
                raise
            with self._lock:
                self._admitting = True
                self._reason = "ready"
                self._error = None
                self._unready_demand.clear()
                # A (re)elected resident proves freshness only by its own publishes.
                self._refreshed.clear()
                self._changed_pending.clear()
                self.refreshes.clear()
            self.feed.activate()
            self.agendas.activate()
            self.observations.activate()
            self.native_observations.activate()
            if self.hook_facts is not None:
                self.hook_facts.activate()
            if self._wake is not None:
                self._wake.set()

    def close_admission(self, reason: str = "draining") -> None:
        with self._lock:
            self._admitting = False
            self._reason = reason
            self._changed_pending.clear()
            self.refreshes.clear()
        self.feed.close(reason)
        self.native_observations.close_admission()
        self.agendas.close(reason)
        self.observations.close_admission(reason)
        if self.hook_facts is not None:
            self.hook_facts.close()

    def health(self) -> dict:
        from .worktrees_feed import BOOTSTRAP_CAPABILITY, FEED_CAPABILITY
        from .worktrees_refresh import CAPABILITY as REFRESH_CAPABILITY

        with self._lock:
            result = {
                "capabilities": [
                    CAPABILITY, FEED_CAPABILITY, BOOTSTRAP_CAPABILITY, REFRESH_CAPABILITY,
                    observations.CAPABILITY, "picker-bootstrap-v1",
                    "worktree-agendas-v1",
                    "worktree-agendas-v2",
                    "worktree-agendas-graph-v1",
                ],
                "worktrees": {
                    "contract_version": CONTRACT_VERSION,
                    "status": self._reason,
                    "generation": self.store.generation,
                    "error": self._error,
                },
            }
        result["capabilities"].extend(self.observations.capabilities())
        result["worktree_observations"] = self.observation_status()
        result["worktree_observations"]["native_receipts"] = self.native_observations.status()
        if self.hook_facts is not None:
            result["worktrees"]["hook_facts"] = {
                "version": 2, "generation": self.hook_facts.generation,
                "quality": "advisory", "error": self.hook_facts.error,
            }
        return result

    def observation_read(self, payload: dict) -> dict:
        return self.observations.point(payload)

    def observation_history(self, payload: dict) -> dict:
        return self.observations.point(payload, history=True)

    def observation_status(self) -> dict:
        return self.observations.status()

    def native_compute(self, kind: str, payload: dict) -> dict:
        return self.native_observations.compute(kind, payload)

    def observation_counts(self, server) -> dict[str, int]:
        from .owner_observation_transport import FEED_ACTION, HISTORY_ACTION, READ_ACTION

        counts = self.observations.counts()
        return {
            FEED_ACTION: counts["streams"],
            READ_ACTION: server.current_read_count(READ_ACTION),
            HISTORY_ACTION: server.current_read_count(HISTORY_ACTION),
            "worktree-observations-call": counts["readers"],
            "worktree-observations-producer": counts["producer"],
            "worktree-observations-publication": counts["publication"],
            "worktree-observations-demand": counts["demand"],
            **self.native_observations.counts(),
        }

    def observation_busy(self, server) -> list[str]:
        return [key for key, count in self.observation_counts(server).items() if count]

    def observation_wait_closed(self, timeout: float) -> None:
        from .owner_observation_contract import ObservationError

        deadline = time.monotonic() + timeout
        if not self.observations.wait_closed(timeout) or not self.native_observations.wait_closed(
            max(0.0, deadline - time.monotonic()),
        ):
            raise ObservationError("observation requests have not settled", "unavailable")

    def agenda_handle(self, action: str, payload: dict) -> dict:
        return (
            self.agendas.publish(payload) if action == "agenda-publish"
            else self.agendas.read(payload)
        )

    def stream(
        self, action: str, payload: dict, disconnected: Callable[[], bool],
    ) -> Iterator[dict]:
        from .owner_observation_contract import error
        from .owner_observation_transport import FEED_ACTION

        if action == FEED_ACTION:
            return self.observations.stream(payload, disconnected)
        if action == "agenda-feed":
            return self.agendas.stream(payload, disconnected)
        if action == "worktrees-feed":
            return self.feed.stream(payload, disconnected)
        return iter((error("unsupported-action", "action is not a held observation feed"),))

    def agenda_counts(self, server) -> dict:
        return {
            "agenda-feed": self.agendas.feed.active_count(),
            "agenda-read": server.current_read_count("agenda-read"),
            "agenda-publish": server.current_read_count("agenda-publish"),
        }

    def agenda_busy(self, server) -> list[str]:
        return [key for key, value in self.agenda_counts(server).items() if value]

    def picker_bootstrap(self, payload: dict) -> dict:
        from . import picker_bootstrap_runtime
        from .execution_spaces import ExecutionSpaceError

        if self._read_elected is not None and not self._read_elected():
            self.close_admission("superseded")
        with self._lock:
            if not self._admitting:
                return self._failure(self._reason, "resident bootstrap admission closed")
            generation = self.store.generation
        try:
            result = picker_bootstrap_runtime.build(payload)
        except (
            ExecutionSpaceError, OSError, ValueError, KeyError, subprocess.SubprocessError,
        ) as error:
            return self._failure("unavailable", str(error))
        if self._read_elected is not None and not self._read_elected():
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

    def _record_demand_locked(self, project: str) -> None:
        now = self._now()
        previous = self._demand.get(project)
        if previous is None or previous < now - _DEMAND_LINGER_SECONDS:
            self._unready_demand.discard(project)
        self._demand[project] = now

    def register_project_demand(self, project: str, *, unready: bool = False) -> None:
        """Renew admitted producer demand, coalescing wakes for unpublished projects."""
        with self._lock:
            if not self._admitting:
                return
            self._record_demand_locked(project)
            if unready and project not in self._unready_demand:
                self._unready_demand.add(project)
                if self._wake is not None:
                    self._wake.set()

    def handle(self, payload: dict) -> dict:
        try:
            project, selection = request_selection(payload)
        except ProjectionError as exc:
            return self._failure("invalid-request", str(exc))
        # A passive read neither renews producer demand nor wakes the producer,
        # so unattended sweeps cannot start scans or hold off idle retirement.
        demand = payload.get("passive") is not True
        if self._read_elected is not None and not self._read_elected():
            self.close_admission("superseded")
        with self._lock:
            if not self._admitting:
                return self._failure(
                    self._reason, self._error or "current-roster admission closed"
                )
            if demand:
                self._record_demand_locked(project)
            publication_error = self._publication_errors.get(project)
            refreshed_at = self._refreshed.get(project)
            refresh_receipt = None
            if "refresh_id" in payload:
                try:
                    refresh_receipt = self.refreshes.request(
                        project, selection["worktree_id"], payload["refresh_id"],
                    )
                except ProjectionError as exc:
                    return self._failure(getattr(exc, "code", "invalid-request"), str(exc))
                if refresh_receipt is None:
                    if self._wake is not None:
                        self._wake.set()
                    return self._failure("unready", "requested point refresh is awaiting publication")
        try:
            result = self.store.read(
                project, **selection, contract_version=payload["contract_version"],
            )
        except ProjectionUnavailable as exc:
            if demand:
                self.register_project_demand(project, unready=True)
            return self._failure(
                "unavailable" if publication_error else "unready", publication_error or str(exc),
            )
        except ProjectionError as exc:
            return self._failure("invalid-request", str(exc))
        result = {"ok": True, "contract_version": payload["contract_version"], **result}
        if refresh_receipt is not None:
            result["point_refresh"] = refresh_receipt
        if payload["contract_version"] == observations.VERSION:
            result["project"] = project
        if publication_error is not None:
            result["availability"] = {"status": "stale", "reason": publication_error}
        if not demand:
            self._label_passive_freshness(result, refreshed_at)
        return result

    def _label_passive_freshness(self, result: dict, refreshed_at: float | None) -> None:
        """Prove a demand-free read current only from this resident's recent publish.

        Passive readers renew no demand, so a project whose demand lapsed (or
        whose producer hung after publishing) keeps serving its last rows. The
        age is measured here, on the reader's thread, so it grows even when the
        sweep loop makes no progress.
        """
        bound = self._freshness_bound
        age = None if refreshed_at is None else max(0.0, self._now() - refreshed_at)
        result["projection"] = {
            **result["projection"],
            "freshness": {
                "refreshed_age_s": None if age is None else round(age, 3),
                "bound_s": bound,
            },
        }
        if "availability" in result:
            return
        if age is None:
            reason = "current roster has not been refreshed by this resident"
        elif age > bound:
            reason = f"current roster refresh overdue ({age:.0f}s > {bound:.0f}s)"
        else:
            return
        result["availability"] = {"status": "stale", "reason": reason}

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
        return (
            projects | self.feed.projects() | self.agendas.feed.projects()
            | self.observations.projects()
        )

    def clock(self) -> float:
        """This runtime's monotonic clock, for a producer to mark when it
        starts collecting the facts it will :meth:`publish`."""
        return self._now()

    def publish(
        self, project: str, rows, *, observed_at: float, record_heads=None,
        collected_from: float | None = None,
        refresh_requests=(),
    ) -> None:
        project = _identity(project, name="project")
        started = self._now()
        if collected_from is not None:
            # Freshness ages from when collection began (a :meth:`clock`
            # reading), so a stalled collection cannot publish at age zero.
            if type(collected_from) not in (int, float) or not math.isfinite(collected_from):
                raise ValueError("collected_from must be a finite clock reading")
            started = min(started, collected_from)
        with self._activation_lock:
            with self._lock:
                if not self._admitting:
                    raise ProjectionUnavailable("current-roster publisher is not admitted")
            try:
                with self.observations.publication(project):
                    self.store.publish(
                        project, rows, observed_at=observed_at, authority_check=self._elected,
                    )
                    self.native_observations.accepted(project)
                    self.observations.accepted(project)
                    if self.hook_facts is not None:
                        self.hook_facts.publish(
                            project, self.store.read(project, contract_version=2),
                            record_heads=record_heads,
                        )
                self.agendas.roster_changed(project)
            except ProjectionUnavailable as exc:
                if self.store.superseded:
                    self.close_admission("superseded")
                    with self._lock:
                        self._error = str(exc)
                    if self._wake is not None:
                        self._wake.set()
                raise
            # Still under the activation lock: a re-election that clears the
            # freshness map cannot interleave with this publication's mark.
            with self._lock:
                self._publication_errors.pop(project, None)
                self._unready_demand.discard(project)
                self._refreshed[project] = started
                self._changed_pending.pop(project, None)
                if refresh_requests:
                    head = self.store.read(
                        project, worktree_id=refresh_requests[0][1],
                    )["projection"]
                    self.refreshes.complete(project, refresh_requests, head)
        self.feed.changed(project)

    def note_changed(self, path: str | None) -> None:
        """Queue a worktree state-change event for the next sweep's publish set.

        Unlike a read, this records no project demand, so events alone never
        keep an idle resident alive; they only make the next woken sweep
        republish the affected project. ``None`` (or overflow) means "every
        project this resident publishes".
        """
        with self._lock:
            if not self._admitting:
                return
            key = None if path is None else str(path)
            # Only a new distinct path past capacity overflows; duplicates are no-ops.
            if key is None or (
                key not in self._changed_paths
                and len(self._changed_paths) >= _CHANGED_PATH_LIMIT
            ):
                self._changed_all = True
                self._changed_paths.clear()
            elif not self._changed_all:
                self._changed_paths.add(key)
        if self._wake is not None:
            self._wake.set()

    def take_changed_projects(self, resolve: Callable[[str], str | None]) -> set[str]:
        """Return projects with unpublished change events; unresolved paths mean all.

        A changed project stays pending until a publication succeeds, so a
        sweep that fails or defers before publishing (for example a transient
        mux-listing failure) does not lose its event. Retries are bounded to
        ``_CHANGED_RETRY_SWEEPS`` sweeps per event; after that the project
        simply ages to stale like any unrefreshed roster.
        """
        with self._lock:
            paths, everything = self._changed_paths, self._changed_all
            self._changed_paths, self._changed_all = set(), False
            published = set(self._refreshed)
        projects: set[str] = set()
        if paths or everything:
            published |= self.store.published_projects()
            unresolved = everything
            for path in () if everything else paths:
                try:
                    project = resolve(path)
                except Exception:
                    project = None
                if isinstance(project, str) and project:
                    projects.add(project)
                else:
                    unresolved = True
            if unresolved:
                projects |= published
        with self._lock:
            if not self._admitting:
                return set()
            for project in projects:
                self._changed_pending[project] = 0
            for project, sweeps in list(self._changed_pending.items()):
                if sweeps >= _CHANGED_RETRY_SWEEPS:
                    del self._changed_pending[project]
                else:
                    self._changed_pending[project] = sweeps + 1
            return set(self._changed_pending)

    def publication_failed(self, project: str, error: Exception) -> None:
        project = _identity(project, name="project")
        with self._lock:
            self._publication_errors[project] = str(error)[:1024] or type(error).__name__
        self.observations.publication_failed(project, error)
        if self.hook_facts is not None:
            self.hook_facts.withdraw(project)
        self.feed.changed(project)

    def producer_failed(self, error: Exception) -> None:
        for project in self.store.published_projects() | self.demand_projects():
            self.publication_failed(project, error)

    def shutdown(self) -> None:
        with self._activation_lock:
            self._terminated = True
            self.close_admission("closed")
            self.observations.shutdown()
            self.store.close()
