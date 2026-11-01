"""Generation-fenced optional projection cache owned by WorktreesRuntime."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
from contextlib import closing

from . import agenda_contract as contract
from . import tracking
from .worktrees_projection_index import ProjectionUnavailable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worktrees_agenda_cache (
    project TEXT NOT NULL, worktree_id TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (project, worktree_id)
);
"""
_SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS worktrees_agenda_source (
    project TEXT NOT NULL, worktree_id TEXT NOT NULL, session_id TEXT NOT NULL,
    store_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL,
    PRIMARY KEY (project, worktree_id, session_id)
);
"""
MAX_SOURCES_PER_WORKTREE = 256


class AgendaRuntime:
    def __init__(self, owner) -> None:
        from .agenda_feed import AgendaFeed

        self.owner = owner
        self._lock = threading.RLock()
        self._rows: dict[tuple[str, str], dict] = {}
        self._generation = None
        self._sequence = 0
        self._activation_error = None
        self._project_errors: dict[str, str] = {}
        self._paths = {}
        self._qualified = set()
        self._pending = set()
        self._pending_lock = threading.Lock()
        self._unsubscribe = None
        self.feed = AgendaFeed(self)

    def _fence(self, conn=None, expected=None) -> None:
        owner = self.owner
        if owner._elected is not None and not owner._elected():
            raise contract.AgendaError("resident is no longer elected", "superseded")
        with owner._lock:
            if not owner._admitting:
                raise contract.AgendaError("agenda admission closed", owner._reason)
            if expected is not None and expected != owner.store.generation:
                raise contract.AgendaError("agenda generation changed", "superseded")
        if self._activation_error:
            raise contract.AgendaError(self._activation_error, "unavailable")
        if conn is not None:
            row = conn.execute(
                "SELECT generation, writer_id FROM worktrees_projection_owner WHERE singleton=1",
            ).fetchone()
            if row != (owner.store.generation, owner.store._writer_id):
                raise contract.AgendaError("agenda owner was replaced", "superseded")

    def activate(self) -> None:
        with self._lock:
            initialized = False
            self._activation_error = None
            try:
                if self._generation != self.owner.store.generation:
                    self._rows.clear()
                    self._paths = {}
                    self._qualified.clear()
                    with self._pending_lock:
                        self._pending.clear()
                    self._sequence = 0
                    self._generation = self.owner.store.generation
                    self._project_errors.clear()
                with closing(sqlite3.connect(self.owner.store.db_path, timeout=0.2)) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._fence(conn)
                    conn.execute(_SCHEMA)
                    conn.execute(_SOURCE_SCHEMA)
                if self._unsubscribe is None:
                    from . import record_cache

                    self._unsubscribe = record_cache.observe_writes(self.record_written)
                self._fresh_fence(self._generation)
                self.feed.activate()
                initialized = True
            except (contract.AgendaError, OSError, sqlite3.Error) as exc:
                self._activation_error = str(exc)[:1024]
            finally:
                if not initialized:
                    if self._activation_error is None:
                        self._activation_error = "agenda initialization did not complete"
                    self.feed.close("unavailable")

    def close(self, reason) -> None:
        self.feed.close(reason)
        if reason == "closed" and self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def record_written(self, path, record) -> None:
        key = record.repo, record.worktree_id
        # Never acquire agenda/owner locks from inside a canonical record writer.
        if self._paths.get(key) != path or key not in self._qualified:
            return
        with self._pending_lock:
            self._pending.add(key)
        self.feed.changed(key[0])

    def _refresh_pending(self) -> tuple[set, dict]:
        with self._pending_lock:
            pending, self._pending = self._pending, set()
        return pending, {key: self._refresh_head(key) for key in pending if key in self._rows}

    def _restore_pending(self, pending: set) -> None:
        with self._pending_lock:
            self._pending.update(pending)

    def _refresh_head(self, key) -> dict:
        try:
            head, error = self._head(key), None
        except (OSError, ValueError) as exc:
            head, error = None, str(exc)[:1024]
        return {**self._rows[key], "head": head, "error": error}

    def _expose(self, updates: dict) -> None:
        changed = {key: value for key, value in updates.items() if self._rows[key] != value}
        if changed:
            self._advance()
            self._rows.update(changed)
            for project in {key[0] for key in changed}:
                self.feed.changed(project)

    def _fresh_fence(self, expected) -> None:
        with closing(
            sqlite3.connect(
                self.owner.store.db_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                timeout=0.2,
            )
        ) as conn:
            self._fence(conn, expected)

    def roster_changed(self, project) -> None:
        with self._lock:
            try:
                self.reconcile(project)
            except (OSError, sqlite3.Error, ValueError, ProjectionUnavailable) as exc:
                if self._activation_error is None:
                    self._project_errors[project] = str(exc)[:1024]
                self.feed.changed(project)

    def _request_project(self, project: str) -> None:
        published = project in self.owner.store.published_projects()
        self.owner.register_project_demand(project, unready=not published)
        if not published:
            self._fresh_fence(self._generation)
            raise contract.AgendaError("resident project roster is not published", "unready")
        if project in self._project_errors:
            self._fresh_fence(self._generation)
            raise contract.AgendaError(self._project_errors[project], "unavailable")

    def _advance(self) -> None:
        if self._sequence >= contract.MAX_SEQUENCE:
            raise contract.AgendaError("agenda sequence exhausted", "unavailable")
        self._sequence += 1

    def _head(self, key) -> str | None:
        project, wid = key
        path = tracking._owning_tracking_dir(wid, repo=project) / f"{wid}.yaml"
        if path.is_symlink():
            raise contract.AgendaError("agenda tracking record is linked", "invalid-source")
        record = tracking.load_record(path)
        if record.worktree_id != wid or record.repo != project:
            raise contract.AgendaError("agenda tracking identity mismatch", "invalid-source")
        head = record.resolved_head_session
        if head is not None:
            contract.identity(head, "head session")
        return head

    def reconcile(self, project: str) -> None:
        """Called on roster publication; never scan agenda files or infer heads."""
        rows = self.owner.store.read(project)["worktrees"]
        keys = {(project, row["id"]) for row in rows}
        with self._lock:
            with closing(sqlite3.connect(self.owner.store.db_path, timeout=0.2)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                self._fence(conn)
                old = {key: value for key, value in self._rows.items() if key[0] == project}
                current = {}
                for key in keys:
                    contract.identity(key[1], "roster worktree")
                    cached = conn.execute(
                        "SELECT payload FROM worktrees_agenda_cache "
                        "WHERE project=? AND worktree_id=?",
                        key,
                    ).fetchone()
                    body = (
                        None
                        if cached is None
                        else contract.decode(
                            cached[0].encode("utf-8"),
                            contract.BODY_BYTES,
                        )
                    )
                    if body is not None:
                        contract.observation(body)
                        if body["source"]["worktree"] != key[1]:
                            raise contract.AgendaError(
                                "durable agenda identity mismatch", "unavailable"
                            )
                    head, error = None, None
                    if body is not None or key in self._qualified:
                        try:
                            head = self._head(key)
                        except (OSError, ValueError) as exc:
                            error = str(exc)[:1024]
                    current[key] = {"head": head, "error": error, "body": body}
                for (wid,) in conn.execute(
                    "SELECT worktree_id FROM worktrees_agenda_cache WHERE project=?",
                    (project,),
                ).fetchall():
                    if (project, wid) not in keys:
                        conn.execute(
                            "DELETE FROM worktrees_agenda_cache WHERE project=? AND worktree_id=?",
                            (project, wid),
                        )
                        conn.execute(
                            "DELETE FROM worktrees_agenda_source "
                            "WHERE project=? AND worktree_id=?",
                            (project, wid),
                        )
                self._fence(conn)
            paths = {
                key: tracking._owning_tracking_dir(key[1], repo=key[0]) / f"{key[1]}.yaml"
                for key in (self._rows.keys() - old.keys()) | current.keys()
            }
            self._fresh_fence(self._generation)
            if current != old or project in self._project_errors:
                self._advance()
                for key in old:
                    self._rows.pop(key)
                self._rows.update(current)
                self._qualified.intersection_update(self._rows)
                self._qualified.update(
                    key for key, value in current.items() if value["body"] is not None
                )
                self._paths = paths
                self._project_errors.pop(project, None)
                self.feed.changed(project)

    def _reply(self, request) -> dict:
        project, wid, session = contract.selection(request)
        value = self._rows.get((project, wid))
        if value is None:
            raise contract.AgendaError(
                "worktree is absent from the current owned roster", "not-found"
            )
        if value["error"]:
            raise contract.AgendaError(value["error"], "unavailable")
        body = value["body"]
        availability = "not-published"
        if session is None or value["head"] is None or session != value["head"]:
            availability = "head-changed"
        elif body is not None:
            availability = "observed" if body["source"]["session"] == session else "not-published"
        return {
            "ok": True,
            "contract_version": 1,
            "generation": self._generation,
            "sequence": self._sequence,
            "project": project,
            "worktree_id": wid,
            "session_id": session,
            "availability": availability,
            "observation": copy.deepcopy(body) if availability == "observed" else None,
        }

    def _source_fence(self, conn, key, body, encoded) -> None:
        source = body["source"]
        subject = (*key, source["session"])
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        previous = conn.execute(
            "SELECT store_id, revision, digest FROM worktrees_agenda_source "
            "WHERE project=? AND worktree_id=? AND session_id=?",
            subject,
        ).fetchone()
        if previous is not None:
            store, revision, accepted_digest = previous
            if (
                not isinstance(store, str)
                or len(store) != 32
                or any(c not in "0123456789abcdef" for c in store)
                or type(revision) is not int
                or not 1 <= revision <= contract.MAX_SEQUENCE
                or not isinstance(accepted_digest, str)
                or len(accepted_digest) != 64
                or any(c not in "0123456789abcdef" for c in accepted_digest)
            ):
                raise contract.AgendaError("invalid durable agenda source fence", "unavailable")
            if store != source["store_id"]:
                raise contract.AgendaError(
                    "source store changed without registration", "source-changed"
                )
            if revision > body["revision"]:
                raise contract.AgendaError("stale committed revision", "stale-revision")
            if revision == body["revision"] and accepted_digest != digest:
                raise contract.AgendaError("conflicting committed revision", "revision-conflict")
        else:
            count = conn.execute(
                "SELECT count(*) FROM worktrees_agenda_source WHERE project=? AND worktree_id=?",
                key,
            ).fetchone()[0]
            if count >= MAX_SOURCES_PER_WORKTREE:
                raise contract.AgendaError("agenda source-session bound reached", "source-limit")
        conn.execute(
            "INSERT OR REPLACE INTO worktrees_agenda_source VALUES (?, ?, ?, ?, ?, ?)",
            (*subject, source["store_id"], body["revision"], digest),
        )

    def read(self, request, *, refresh=True) -> dict:
        pending = set()
        try:
            project, wid, _session = contract.selection(request)
            with self.owner._activation_lock, self._lock:
                self._fence(expected=self._generation)
                self._request_project(project)
                pending, updates = self._refresh_pending()
                key = project, wid
                if refresh and key in self._rows:
                    updates[key] = self._refresh_head(key)
                self._fresh_fence(self._generation)
                self._expose(updates)
                if refresh and key in self._rows:
                    self._qualified.add(key)
                return self._reply(request)
        except contract.AgendaError as exc:
            self._restore_pending(pending)
            return contract.failure(exc.code, str(exc))
        except (OSError, sqlite3.Error, ProjectionUnavailable, ValueError) as exc:
            self._restore_pending(pending)
            return contract.failure("unavailable", str(exc))

    def publish(self, payload) -> dict:
        pending = set()
        try:
            contract.exact(
                payload, {"contract_version", "project", "expected_generation", "observation"}
            )
            body = payload["observation"]
            encoded = contract.observation(body)
            body = json.loads(encoded)
            request = {
                "contract_version": payload["contract_version"],
                "project": payload["project"],
                "worktree_id": body["source"]["worktree"],
                "session_id": body["source"]["session"],
            }
            project, wid, session = contract.selection(request)
            expected = contract.hex_id(payload["expected_generation"], "expected generation")
            key = project, wid
            with self.owner._activation_lock, self._lock:
                self._fence(expected=expected)
                self._request_project(project)
                pending, updates = self._refresh_pending()
                if key not in self._rows:
                    raise contract.AgendaError(
                        "worktree is absent from current owned roster", "not-found"
                    )
                path = tracking._owning_tracking_dir(wid, repo=project) / f"{wid}.yaml"
                with tracking._RecordLock(path, timeout=0.2, require_sidecar=True):
                    before = self._head(key)
                    if session is None or before != session:
                        raise contract.AgendaError(
                            "publisher is not the current canonical head", "head-changed"
                        )
                    candidate = {"head": before, "error": None, "body": body}
                    if self._rows[key] != candidate and self._sequence >= contract.MAX_SEQUENCE:
                        raise contract.AgendaError("agenda sequence exhausted", "unavailable")
                    with (
                        closing(sqlite3.connect(self.owner.store.db_path, timeout=0.2)) as conn,
                        conn,
                    ):
                        conn.execute("BEGIN IMMEDIATE")
                        self._fence(conn, expected)
                        old = conn.execute(
                            "SELECT payload FROM worktrees_agenda_cache "
                            "WHERE project=? AND worktree_id=?",
                            key,
                        ).fetchone()
                        previous = None if old is None else contract.decode(old[0].encode("utf-8"))
                        if previous is not None:
                            contract.observation(previous)
                            if previous["source"]["worktree"] != wid:
                                raise contract.AgendaError(
                                    "durable agenda source identity mismatch",
                                    "unavailable",
                                )
                        if previous is not None and previous["source"]["session"] == session:
                            if previous["source"]["store_id"] != body["source"]["store_id"]:
                                raise contract.AgendaError(
                                    "source store changed without registration", "source-changed"
                                )
                            if previous["revision"] > body["revision"]:
                                raise contract.AgendaError(
                                    "stale committed revision", "stale-revision"
                                )
                            if (
                                previous["revision"] == body["revision"]
                                and contract.observation(previous) != encoded
                            ):
                                raise contract.AgendaError(
                                    "conflicting committed revision", "revision-conflict"
                                )
                        self._source_fence(conn, key, body, encoded)
                        conn.execute(
                            "INSERT OR REPLACE INTO worktrees_agenda_cache VALUES (?, ?, ?)",
                            (*key, encoded),
                        )
                        if self._head(key) != before:
                            raise contract.AgendaError(
                                "canonical head changed during publication", "head-changed"
                            )
                        self._fence(conn, expected)
                    # Persist before exposing this immutable publication or notifying readers.
                    self._fresh_fence(expected)
                    updates[key] = candidate
                    self._expose(updates)
                    self._qualified.add(key)
                    return self._reply(request)
        except contract.AgendaError as exc:
            self._restore_pending(pending)
            return contract.failure(exc.code, str(exc))
        except (OSError, sqlite3.Error, ProjectionUnavailable, ValueError) as exc:
            self._restore_pending(pending)
            return contract.failure("unavailable", str(exc))

    def stream(self, payload, disconnected):
        return self.feed.stream(payload, disconnected)
