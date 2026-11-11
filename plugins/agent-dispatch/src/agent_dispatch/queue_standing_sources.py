"""Fenced reconciliation of one declaring source's complete standing-charter set."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict

from .queue_records import TaskError
from .queue_standing_charters import StandingCharterDeclaration, _encoded
from .queue_standing_schema import IDENTITY_FIELDS, STANDING_KIND


def migrate_standing_sources(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS standing_source_observations ("
        " repo TEXT NOT NULL, namespace TEXT NOT NULL, revision TEXT NOT NULL,"
        " observed_at REAL NOT NULL, PRIMARY KEY (repo, namespace))"
    )


def _identity(document: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(document[name] for name in IDENTITY_FIELDS)


def _source_text(value: str, name: str) -> None:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > 1024 or any(ord(char) < 32 for char in value)
    ):
        raise TaskError(f"standing source {name} must be bounded nonempty text")


def _entries(namespace: str, entries: Iterable[Mapping]) -> dict[tuple[str, ...], tuple[str, dict, bool]]:
    out: dict[tuple[str, ...], tuple[str, dict, bool]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TaskError("standing source entry must be a mapping")
        title, enabled = entry.get("title"), entry.get("enabled", True)
        if not isinstance(title, str) or not title.strip() or len(title) > 1024:
            raise TaskError("standing title must be bounded nonempty text")
        if not isinstance(enabled, bool):
            raise TaskError("standing source entry enabled must be a boolean")
        declaration = entry.get("declaration")
        if not isinstance(declaration, Mapping):
            raise TaskError("standing source entry needs a declaration mapping")
        try:
            document = asdict(StandingCharterDeclaration(**dict(declaration)))
        except TypeError as exc:
            raise TaskError("standing source declaration has invalid fields") from exc
        if document["namespace"] != namespace:
            raise TaskError("standing source entry belongs to a different namespace")
        if _identity(document) in out:
            raise TaskError("standing source set declares one qualified key twice")
        out[_identity(document)] = (title, document, enabled)
    return out


class QueueStandingSourcesMixin:
    def standing_source_repositories(self, namespace: str) -> list[str]:
        """Find retained source lanes, including empty fences and unobserved charters."""
        _source_text(namespace, "namespace")
        with self._connect() as conn:
            return [row["repo"] for row in conn.execute(
                "SELECT repo FROM standing_source_observations WHERE namespace=?"
                " UNION SELECT repo FROM tasks WHERE kind=?"
                " AND json_extract(standing_charter, '$.declaration.namespace')=?"
                " ORDER BY repo",
                (namespace, STANDING_KIND, namespace),
            ).fetchall()]

    def standing_source_observation(self, repo: str, namespace: str) -> str | None:
        """Read the retained ordering fence without qualifying a new source read."""
        _source_text(namespace, "namespace")
        canonical_repo = self._canonical_repo(repo)
        if canonical_repo is None:
            raise TaskError("standing charter requires a repository lane")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision FROM standing_source_observations WHERE repo=? AND namespace=?",
                (canonical_repo, namespace),
            ).fetchone()
        return None if row is None else row["revision"]

    def reconcile_standing_source_set(
        self, repo: str, namespace: str, entries: Iterable[Mapping], *,
        revision: str | None, expected_observation: str | None, now: float | None = None,
    ) -> dict:
        """Apply one complete source read; only a fenced newer read may change or retire.

        ``revision`` is the source revision the caller proved descends from
        ``expected_observation``; ``None`` means unordered, so only enrollment
        and no-op reconciliation are applied. Absence retires only after a prior
        fenced observation for the same repository lane and namespace.
        """
        _source_text(namespace, "namespace")
        if revision is not None:
            _source_text(revision, "revision")
        if expected_observation is not None:
            _source_text(expected_observation, "expected observation")
        declared = _entries(namespace, entries)
        canonical_repo = self._canonical_repo(repo)
        if canonical_repo is None:
            raise TaskError("standing charter requires a repository lane")
        ts = self._now(now)
        result: dict[str, list] = {
            "enrolled": [], "changed": [], "retired": [], "unchanged": [], "deferred": [],
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_standing_source(conn, canonical_repo)
            row = conn.execute(
                "SELECT revision FROM standing_source_observations WHERE repo=? AND namespace=?",
                (canonical_repo, namespace),
            ).fetchone()
            observed = None if row is None else row["revision"]
            if observed != expected_observation:
                raise TaskError("standing source observation changed; refresh before retrying")
            fenced = revision is not None
            existing = {}
            for found in conn.execute(
                "SELECT id FROM tasks WHERE repo=? AND kind=?"
                " AND json_extract(standing_charter, '$.declaration.namespace')=?",
                (canonical_repo, STANDING_KIND, namespace),
            ).fetchall():
                task = self._fetch(conn, found["id"])
                existing[_identity(task.standing_charter["declaration"])] = task
            for key, (title, document, enabled) in sorted(declared.items()):
                task = existing.get(key)
                if task is None:
                    if enabled:
                        result["enrolled"].append(
                            self._enroll_standing(conn, title, document, canonical_repo, ts).id
                        )
                    continue
                standing = task.standing_charter
                if standing["lifecycle"] != "active":
                    bucket = "deferred" if enabled else "unchanged"
                    result[bucket].append({"id": task.id, "reason": "reenrollment-required"}
                                          if enabled else task.id)
                    continue
                if not enabled:
                    self._retire_from_source(conn, task, ts, fenced, result, "disabled")
                elif standing["declaration"] == document and task.title == title:
                    result["unchanged"].append(task.id)
                elif not fenced:
                    result["deferred"].append({"id": task.id, "reason": "unordered-source-change"})
                else:
                    self._change_standing(conn, task, {
                        "title": title,
                        "standing_charter": _encoded({**standing, "declaration": document}),
                    }, action="reconcile", ts=ts)
                    result["changed"].append(task.id)
            for key, task in sorted(existing.items()):
                if key in declared or task.standing_charter["lifecycle"] != "active":
                    continue
                if observed is None:
                    result["deferred"].append({"id": task.id, "reason": "first-source-observation"})
                else:
                    self._retire_from_source(conn, task, ts, fenced, result, "removed")
            if fenced:
                conn.execute(
                    "INSERT INTO standing_source_observations(repo, namespace, revision, observed_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(repo, namespace) DO UPDATE SET"
                    " revision=excluded.revision, observed_at=excluded.observed_at",
                    (canonical_repo, namespace, revision, ts),
                )
        result["observation"] = revision if fenced else observed
        return result

    def _retire_from_source(
        self, conn: sqlite3.Connection, task, ts: float, fenced: bool, result: dict, reason: str,
    ) -> None:
        if not fenced:
            result["deferred"].append({"id": task.id, "reason": f"unordered-source-{reason}"})
            return
        standing = json.loads(json.dumps(task.standing_charter))
        self._change_standing(conn, task, {"standing_charter": _encoded({
            **standing, "lifecycle": "retiring",
        })}, action=f"retire-{reason}", ts=ts)
        result["retired"].append({"id": task.id, "reason": reason})
