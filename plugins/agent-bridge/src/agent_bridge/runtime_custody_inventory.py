"""Full-row metadata observations of known DB cohorts, never native authority."""

from __future__ import annotations

import json
import sqlite3

from .runtime_custody import RuntimeCustodyError


COHORTS = {
    "sessions": ("id", "status", "acp_session_id", "runtime_custody_json"),
    "turns": ("session_id", "turn_index", "acquisition_delivery_id", "completed_at",
              "runtime_custody_json"),
    "workspace_embodiments": ("key_digest", "state", "session_id", "native_session_id", "turn_index"),
    "provider_embodiments": ("key_digest", "state", "session_id", "native_session_id"),
    "acquisition_attempts": ("delivery_id", "target", "worktree_id", "state"),
    "acquisition_effects": ("delivery_id", "stage"),
    "acquisition_bindings": ("session_id", "native_session_id", "worktree_id", "retired"),
    "acquisition_launch_gates": ("worktree_id", "delivery_id", "mode", "session_id"),
    "pending_prompts": ("id", "session_id"),
    "live_sessions": ("session_id", "pid", "process_started_at", "status"),
    "live_messages": ("id", "session_id", "kind", "claimed_at", "outcome", "delivered_at", "consumed_at"),
    "cli_mode_reservations": ("worktree_id", "reservation_id", "claimed_by_session_id"),
    "worktree_ownership": ("worktree_id", "session_id"),
    "remote_acp_relationships": ("id", "session_id", "phase", "uncertain"),
}
OBSERVATION_TABLES = {
    "schema_version", "events", "delivery_cursors", "delivery_cursor_invalidations",
    "live_session_aliases", "remote_acp_relationship_authority", "sqlite_sequence",
    "bridge_runtime_epoch", "bridge_runtime_producers",
}
UNMEDIATED = (
    "acquisition/creation final effects", "start/resume and background producers",
    "inbox/control SDK handoffs", "native-send settlement", "host/native custody",
    "remote relationship custody", "target state compatibility",
)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeCustodyError("Duplicate runtime association field")
        result[key] = value
    return result


def read_inventory(conn: sqlite3.Connection, identity: str) -> dict[str, object]:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    blockers = [f"unmediated:{name}" for name in UNMEDIATED]
    missing = []
    rows_by_table = {}
    epoch = producers = None
    if {"bridge_runtime_epoch", "bridge_runtime_producers"} <= tables:
        row = conn.execute("SELECT * FROM bridge_runtime_epoch WHERE singleton=1").fetchone()
        epoch = dict(row) if row is not None else None
        producers = [dict(row) for row in conn.execute(
            "SELECT * FROM bridge_runtime_producers ORDER BY id"
        )]
        if epoch is None:
            blockers.append("installation-not-bound")
        elif epoch["installation"] != identity:
            raise RuntimeCustodyError("Runtime inventory belongs to another installation")
        if not producers:
            blockers.append("no-enrolled-producer")
    else:
        missing.extend(sorted({"bridge_runtime_epoch", "bridge_runtime_producers"} - tables))
    known_producers = {row["id"]: row for row in producers or []}
    for table, columns in COHORTS.items():
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if table not in tables or not set(columns) <= actual:
            rows_by_table[table] = None
            missing.append(table)
            continue
        rows = [dict(row) for row in conn.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY {','.join(columns[:2])}"
        )]
        rows_by_table[table] = rows
        if table in ("sessions", "turns"):
            for row in rows:
                association = row["runtime_custody_json"]
                if association is None:
                    blockers.append(f"unassociated:{table}:{row[columns[0]]}")
                    continue
                try:
                    value = json.loads(association, object_pairs_hook=_unique)
                except (ValueError, TypeError) as exc:
                    raise RuntimeCustodyError("Malformed retained producer association") from exc
                if (
                    type(value) is not dict or set(value) != {"installation", "epoch", "producer"}
                    or value["installation"] != identity or type(value["epoch"]) is not int
                    or type(value["producer"]) is not str
                ):
                    raise RuntimeCustodyError("Unknown retained producer association")
                producer = known_producers.get(value["producer"])
                if producer is None or (
                    producer["installation"], producer["epoch"]
                ) != (identity, value["epoch"]):
                    blockers.append(f"producer-association-unverified:{table}:{row[columns[0]]}")
                blockers.append(f"native-outcome-unqualified:{table}:{row[columns[0]]}")
        elif rows:
            blockers.append(f"cohort-custody-unqualified:{table}")
    unknown = sorted(tables - COHORTS.keys() - OBSERVATION_TABLES)
    blockers.extend(f"missing-or-legacy-table:{name}" for name in sorted(set(missing)))
    blockers.extend(f"unknown-table:{name}" for name in unknown)
    return {
        "version": 1, "coverage": "bridge-db-session-turn-foundation-v1",
        "epoch": epoch, "producers": producers, "rows": rows_by_table,
        "revision_coverage": ["sessions", "turns", "producer-enrollment", "epoch"],
        "storage_inventory_complete": not missing and not unknown,
        "unmediated_cohorts": list(UNMEDIATED), "blockers": blockers,
        "restore_qualified": False, "acknowledged": False,
    }
