"""Row-scoped rollback fences for persistent standing tasks."""

from __future__ import annotations

import json
import sqlite3

from .queue_common import Task
from .queue_execution_handoff import require_execution_admission
from .queue_records import TaskError


STANDING_KIND = "standing-charter"
# Every standing row requires a capability no worker advertises. A rolled-back
# coordinator's untargeted claim skips it as unmatched instead of selecting it
# and aborting on the guards below, so older finite work stays claimable.
LEGACY_CLAIM_FENCE = json.dumps(["standing-charter:explicit-attachment"])
IDENTITY_FIELDS = ("namespace", "charter_key", "lane_key")
PROTECTED_FIELDS = (
    "kind", "repo", "title", "prompt", "status", "owner", "owner_session_id",
    "generation", "standing_charter", "standing_revision", "source", "origin_ref",
    "requires", "excludes", "target_machine", "target_worktree", "target_repo",
    "payload_ref", "payload_inline", "evaluator_ref", "exclusive_key", "dedup_key",
    "require_verification",
)


def require_finite_execution_admission(conn: sqlite3.Connection, task: Task) -> None:
    """Admit finite execution; standing charters attach only through their owner."""
    if task.kind == STANDING_KIND:
        raise TaskError("standing charter requires explicit attachment admission")
    require_execution_admission(conn, task.id)


def migrate_standing_charters(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_events)")}
    if "standing_change" not in columns:
        try:
            conn.execute("ALTER TABLE task_events ADD COLUMN standing_change TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    identity = ", ".join(
        f"json_extract(standing_charter, '$.declaration.{name}')"
        for name in IDENTITY_FIELDS
    )
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_standing_identity ON tasks(repo, {identity})"
        " WHERE kind='standing-charter'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_standing_revision"
        " ON task_events(task_id, json_extract(standing_change, '$.revision'))"
        " WHERE standing_change IS NOT NULL"
    )
    for operation in ("INSERT", "UPDATE"):
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS standing_shape_{operation.lower()}"
            f" BEFORE {operation} ON tasks WHEN"
            " (NEW.kind='finite' AND (NEW.standing_charter IS NOT NULL OR NEW.standing_revision<>0))"
            " OR (NEW.kind='standing-charter' AND ("
            f" NEW.repo IS NULL OR NEW.requires IS NOT '{LEGACY_CLAIM_FENCE}'"
            " OR NOT COALESCE(json_valid(NEW.standing_charter), 0)"
            " OR json_extract(NEW.standing_charter, '$.version') IS NOT 1"
            " OR json_type(NEW.standing_charter, '$.declaration_generation') IS NOT 'integer'"
            " OR json_extract(NEW.standing_charter, '$.declaration_generation')<1"
            " OR COALESCE(json_extract(NEW.standing_charter, '$.lifecycle'), '')"
            " NOT IN ('active', 'retiring', 'retired')"
            + "".join(
                f" OR json_type(NEW.standing_charter, '$.declaration.{name}') IS NOT 'text'"
                f" OR trim(json_extract(NEW.standing_charter, '$.declaration.{name}'))=''"
                for name in IDENTITY_FIELDS
            )
            + " OR NEW.status NOT IN ('queued', 'claimed', 'started', 'suspended')))"
            " BEGIN SELECT RAISE(ABORT, 'invalid standing charter or finite terminal transition'); END"
        )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_insert_retained BEFORE INSERT ON tasks WHEN"
        " EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.id AND t.kind='standing-charter')"
        " OR (NEW.kind='standing-charter' AND (NEW.standing_revision<>0"
        " OR NEW.status<>'queued' OR NEW.owner IS NOT NULL OR NEW.owner_session_id IS NOT NULL"
        " OR EXISTS (SELECT 1 FROM tasks t WHERE t.repo=NEW.repo AND t.kind='standing-charter'"
        + "".join(
            f" AND json_extract(t.standing_charter, '$.declaration.{name}')"
            f" IS json_extract(NEW.standing_charter, '$.declaration.{name}')"
            for name in IDENTITY_FIELDS
        )
        + "))) BEGIN SELECT RAISE(ABORT, 'standing identity is retained'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_identity_retained BEFORE UPDATE ON tasks WHEN"
        " NEW.kind IS NOT OLD.kind OR (OLD.kind='standing-charter' AND ("
        " NEW.id IS NOT OLD.id OR NEW.repo IS NOT OLD.repo"
        + "".join(
            f" OR json_extract(NEW.standing_charter, '$.declaration.{name}')"
            f" IS NOT json_extract(OLD.standing_charter, '$.declaration.{name}')"
            for name in IDENTITY_FIELDS
        )
        + ")) BEGIN SELECT RAISE(ABORT, 'standing identity is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_task_retained BEFORE DELETE ON tasks"
        " WHEN OLD.kind='standing-charter'"
        " BEGIN SELECT RAISE(ABORT, 'standing task and tombstone are retained'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_reservation_insert BEFORE INSERT ON spawn_reservations"
        " WHEN EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id AND kind='standing-charter')"
        " BEGIN SELECT RAISE(ABORT, 'standing charter requires explicit attachment admission'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_reservation_retarget"
        " BEFORE UPDATE OF task_id ON spawn_reservations WHEN NEW.task_id IS NOT OLD.task_id"
        " AND EXISTS (SELECT 1 FROM tasks WHERE id IN (OLD.task_id, NEW.task_id)"
        " AND kind='standing-charter')"
        " BEGIN SELECT RAISE(ABORT, 'standing reservation identity is immutable'); END"
    )
    changed = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in PROTECTED_FIELDS)
    matches = " AND ".join(
        f"json_extract(e.standing_change, '$.after.{name}') IS NEW.{name}"
        for name in PROTECTED_FIELDS
    )
    # Older providers cannot silently drop the new fields and mutate an occupant.
    # Pure SQL keeps their finite-task writes usable without a connection function.
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_transition_guard BEFORE UPDATE ON tasks"
        f" WHEN OLD.kind='standing-charter' AND ({changed}) AND ("
        " NEW.standing_revision<>OLD.standing_revision+1 OR NOT EXISTS ("
        " SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND json_extract(e.standing_change, '$.revision')=NEW.standing_revision"
        f" AND {matches}))"
        " BEGIN SELECT RAISE(ABORT, 'standing charter requires a fenced lifecycle operation'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_history_insert BEFORE INSERT ON task_events WHEN"
        " EXISTS (SELECT 1 FROM task_events e WHERE e.id=NEW.id AND e.standing_change IS NOT NULL)"
        " OR (NEW.standing_change IS NOT NULL AND EXISTS ("
        " SELECT 1 FROM task_events e WHERE e.task_id=NEW.task_id"
        " AND json_extract(e.standing_change, '$.revision')"
        " =json_extract(NEW.standing_change, '$.revision')))"
        " BEGIN SELECT RAISE(ABORT, 'standing charter history is retained'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS standing_history_{operation.lower()}"
            f" BEFORE {operation} ON task_events WHEN OLD.standing_change IS NOT NULL"
            " BEGIN SELECT RAISE(ABORT, 'standing charter history is retained'); END"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_standing_parent ON tasks(standing_parent_id)"
        " WHERE standing_parent_id IS NOT NULL"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_child_insert BEFORE INSERT ON tasks WHEN"
        " NEW.standing_parent_id IS NOT NULL AND (NEW.kind IS NOT 'finite' OR NOT EXISTS ("
        " SELECT 1 FROM tasks p WHERE p.id=NEW.standing_parent_id"
        " AND p.kind='standing-charter' AND p.repo IS NEW.repo))"
        " BEGIN SELECT RAISE(ABORT, 'finite child requires a same-lane standing parent'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS standing_child_link_retained"
        " BEFORE UPDATE OF standing_parent_id ON tasks"
        " WHEN NEW.standing_parent_id IS NOT OLD.standing_parent_id"
        " BEGIN SELECT RAISE(ABORT, 'standing parent link is immutable'); END"
    )
