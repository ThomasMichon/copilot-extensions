"""Passive contributor provenance from the owner's complete verified snapshot."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from agenda import decode
from agenda_export import ExportError, _Budget, _verified_export

SCHEMA = "efforts.agenda-export-contributors"
# Failure budgets, not pagination or identity-truncation policies.
MAX_CONTRIBUTORS = 4096
MAX_PROJECTION_BYTES = 1 << 20


@dataclass
class _Contributor:
    actor: str | None
    session: str | None
    qualification: str
    first_revision: int
    last_revision: int
    operations: int = 1


def _project(
    db: sqlite3.Connection, manifest: dict[str, Any], raw: bytes, budget: _Budget,
) -> dict[str, Any]:
    contributors: dict[tuple[str | None, str | None, str], _Contributor] = {}
    for revision, text in db.execute("SELECT revision,request FROM operations ORDER BY revision"):
        budget.check()
        identity = decode(text.encode(), "verified accepted request", limit=262144)["identity"]
        key = (identity["actor"], identity["session"], identity["qualification"])
        if key in contributors:
            entry = contributors[key]
            entry.last_revision = revision
            entry.operations += 1
        else:
            if len(contributors) >= MAX_CONTRIBUTORS:
                raise ExportError("agenda contributor count budget exceeded; no history was truncated")
            contributors[key] = _Contributor(*key, revision, revision)
    projection = {
        "schema": SCHEMA, "version": 1, "completeness": "complete",
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source": {key: manifest[key] for key in ("worktree", "store_id", "revision")},
        "contributors": [asdict(entry) for entry in contributors.values()],
    }
    if len(json.dumps(projection, ensure_ascii=True, allow_nan=False, sort_keys=True).encode()) > MAX_PROJECTION_BYTES:
        raise ExportError("agenda contributor byte budget exceeded; no identities were truncated")
    budget.check()
    return projection


def read_contributors(
    destination: Path, *, worktree: str, store_id: str, expected_revision: int | None = None,
) -> dict[str, Any]:
    """Verify every export member and derive all accepted caller identities at that cut.

    Null and arbitrary owner-valid UTF-8 identities retain their original
    qualification. This does not authenticate callers, resolve live sessions,
    grant collection authority or write a new member into the export.
    """
    with _verified_export(destination, worktree=worktree, store_id=store_id,
                          expected_revision=expected_revision) as (manifest, raw, db, budget):
        return _project(db, manifest, raw, budget)
