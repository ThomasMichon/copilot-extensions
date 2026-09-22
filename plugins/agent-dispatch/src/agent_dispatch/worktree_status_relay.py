"""Transient relay cache + render helpers for worktree-status board data.

The coordinator keeps this cache outside any task row: a rebuildable,
non-authoritative per-``(repo, worktree_id)`` relay of the owning machine's
`agent-worktrees worktree-status-bundle` projection. The board then reads the
relay over the coordinator HTTP API instead of probing agent-worktrees on the
render path.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

from .queue import worker_id_for

log = logging.getLogger("agent-dispatch.worktree-status-relay")

_BUSY_TIMEOUT_MS = 5000
DEFAULT_STALE_AFTER_SECONDS = 40.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worktree_status_relay (
    repo TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (repo, worktree_id)
)
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path), timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    return conn


class WorktreeStatusRelayStore:
    """Small WAL-backed relay keyed by canonical ``(repo, worktree_id)``."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._migrate()

    def _migrate(self) -> None:
        with _connect(self.db_path):
            return

    def get(self, repo: str, worktree_id: str) -> dict[str, Any] | None:
        try:
            with _connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT entry_json FROM worktree_status_relay"
                    " WHERE repo = ? AND worktree_id = ?",
                    (repo, worktree_id),
                ).fetchone()
        except (sqlite3.Error, OSError):
            log.debug("relay read failed", exc_info=True)
            return None
        if row is None:
            return None
        try:
            entry = json.loads(row["entry_json"])
        except (TypeError, ValueError):
            return None
        return entry if isinstance(entry, dict) else None

    def put(
        self,
        repo: str,
        worktree_id: str,
        bundle: dict[str, Any],
        *,
        fetched_at: float,
        poll_interval_seconds: float,
    ) -> None:
        entry = {
            "repo": repo,
            "worktree_id": worktree_id,
            "bundle": bundle,
            "fetched_at": fetched_at,
            "poll_interval_seconds": poll_interval_seconds,
        }
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO worktree_status_relay (repo, worktree_id, entry_json, fetched_at)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(repo, worktree_id) DO UPDATE SET"
                    " entry_json = excluded.entry_json,"
                    " fetched_at = excluded.fetched_at"
                    " WHERE excluded.fetched_at >= worktree_status_relay.fetched_at",
                    (repo, worktree_id, json.dumps(entry), fetched_at),
                )
        except (sqlite3.Error, OSError):
            log.warning("relay write failed for %s/%s", repo, worktree_id, exc_info=True)


def claimed_identity(task: Mapping[str, object]) -> tuple[str | None, str | None]:
    """The task's actual claimant identity, preferring ``owner`` over pins."""
    owner = str(task.get("owner") or "")
    if owner:
        machine, _sep, worktree = owner.partition("/")
        if machine and worktree:
            return machine, worktree
    machine = str(task.get("target_machine") or "").strip() or None
    worktree = str(task.get("target_worktree") or "").strip() or None
    return machine, worktree


def claimed_worker_id(task: Mapping[str, object]) -> str | None:
    machine, worktree = claimed_identity(task)
    if not machine or not worktree:
        return None
    return worker_id_for(machine, worktree)


def entry_is_fresh(entry: Mapping[str, object] | None, *, now: float) -> bool:
    if not entry:
        return False
    try:
        fetched_at = float(entry.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        return False
    try:
        poll_interval = float(entry.get("poll_interval_seconds") or 0.0)
    except (TypeError, ValueError):
        poll_interval = 0.0
    stale_after = max(DEFAULT_STALE_AFTER_SECONDS, poll_interval * 2.0)
    return now - fetched_at <= stale_after


def _repo_display_name(repo: object) -> str:
    text = str(repo or "").rstrip("/")
    return text.rsplit("/", 1)[-1].removesuffix(".git") if text else "repo"


def _render_json_block(value: object) -> str:
    if value is None:
        return "_unknown_"
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    return f"```json\n{rendered}\n```"


def _fact_body(name: str, fact: Mapping[str, object] | None, *, stale: bool) -> str:
    if stale:
        return f"## {name}\n_unknown (relay stale or cold)_"
    if not fact:
        return f"## {name}\n_unknown_"
    confirmed = bool(fact.get("confirmed"))
    status = "confirmed" if confirmed else "unconfirmed"
    observed_at = fact.get("observed_at")
    observed = ""
    try:
        observed = f" — observed {int(max(0.0, time.time() - float(observed_at)))}s ago"
    except (TypeError, ValueError):
        observed = ""
    return (
        f"## {name}\n"
        f"_fact status: {status}{observed}_\n\n"
        f"{_render_json_block(fact.get('value'))}"
    )


def _claims_summary(claims_fact: Mapping[str, object] | None, *, stale: bool) -> str | None:
    if stale:
        return "stale/unknown"
    if not claims_fact:
        return None
    if not bool(claims_fact.get("confirmed")):
        return "unconfirmed"
    value = claims_fact.get("value")
    if not isinstance(value, Mapping):
        return None
    resources = value.get("resources")
    resource_count = len(resources) if isinstance(resources, list) else 0
    owner_ref = value.get("owner_ref")
    parts: list[str] = []
    if resource_count:
        noun = "claim" if resource_count == 1 else "claims"
        parts.append(f"{resource_count} {noun}")
    if owner_ref:
        parts.append("owner ref")
    return ", ".join(parts) if parts else "none"


def board_fields_for_task(
    task: Mapping[str, object],
    relay_entry: Mapping[str, object] | None,
    *,
    now: float,
) -> dict[str, object]:
    """Render ``worktree_status`` + ``artifacts_summary`` for one board row."""
    repo = str(task.get("repo") or "")
    repo_name = str(task.get("repo_name") or _repo_display_name(repo))
    _machine, worktree_id = claimed_identity(task)
    if not worktree_id:
        return {
            "artifacts_summary": None,
            "worktree_status": {
                "title": "Worktree status unavailable",
                "status": "unknown",
                "link": None,
                "body": "No claiming worktree is recorded for this task.",
            },
        }
    stale = not entry_is_fresh(relay_entry, now=now)
    bundle = relay_entry.get("bundle") if isinstance(relay_entry, Mapping) else None
    facts = bundle.get("facts") if isinstance(bundle, Mapping) else None
    if not isinstance(facts, Mapping):
        facts = {}
    claims_fact = facts.get("claims") if isinstance(facts, Mapping) else None
    any_unconfirmed = any(
        isinstance(fact, Mapping) and not bool(fact.get("confirmed"))
        for fact in facts.values()
    )
    status = "stale" if stale else ("partially unconfirmed" if any_unconfirmed else "fresh")
    age_note = ""
    if relay_entry and relay_entry.get("fetched_at") is not None:
        try:
            age_note = (
                f"\n- Relay fetched {int(max(0.0, now - float(relay_entry['fetched_at'])))}s ago"
            )
        except (TypeError, ValueError):
            age_note = ""
    body = "\n\n".join(
        [
            f"- Repo: `{repo_name}`\n- Worktree: `{worktree_id}`{age_note}",
            _fact_body("Git state", facts.get("git_state"), stale=stale),
            _fact_body("Liveness", facts.get("liveness"), stale=stale),
            _fact_body("Session lineage", facts.get("lineage"), stale=stale),
            _fact_body("Claims", claims_fact, stale=stale),
            _fact_body("Disposition", facts.get("disposition"), stale=stale),
        ]
    )
    return {
        "artifacts_summary": _claims_summary(claims_fact, stale=stale),
        "worktree_status": {
            "title": f"Worktree {worktree_id} ({repo_name})",
            "status": status,
            "link": None,
            "body": body,
        },
    }
