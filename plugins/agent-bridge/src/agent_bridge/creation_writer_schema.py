"""Writable preparation and validation of ordinary creation's writer custody."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema

COLUMNS = (
    "creation_writer_json", "creation_handoffs_json", "creation_recoveries_json",
    "creation_adoption_json",
)
ADOPTION_INDEX = "idx_workspace_creation_adoption_session"
_ADOPTION_INDEX_SQL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ADOPTION_INDEX} ON workspace_embodiments"
    "(json_extract(creation_adoption_json,'$.session_id'))"
)
ORIGINAL_RECOVERY_HELD = """
EXISTS (
    SELECT 1 FROM workspace_embodiments AS embodiment
    WHERE embodiment.session_id=sessions.id
    AND embodiment.creation_recoveries_json IS NOT NULL
    AND CASE
        WHEN json_valid(embodiment.creation_recoveries_json) IS NOT 1 THEN 1
        WHEN json_type(embodiment.creation_recoveries_json) != 'object' THEN 1
        ELSE EXISTS (
            SELECT 1 FROM json_each(embodiment.creation_recoveries_json) AS recovery
            WHERE CASE WHEN recovery.type != 'object' THEN 1
                ELSE COALESCE(json_extract(recovery.value,'$.state'),'unknown')
                     NOT IN ('complete','not-started')
            END
        )
    END
)
"""
LEGACY_RETAINED_RECOVERY_HELD = ORIGINAL_RECOVERY_HELD.replace(
    "embodiment.session_id=sessions.id",
    "(embodiment.session_id=sessions.id OR embodiment.key_digest=CASE "
    "WHEN json_valid(sessions.target_json) THEN "
    "json_extract(sessions.target_json,'$.venue._agent_bridge_creation_writer') END)",
)


def _valid_recovery_request() -> str:
    request = "$.request."

    def value(name: str) -> str:
        return f"json_extract(recovery.value,'{request}{name}')"

    checks = [
        "(SELECT COUNT(*) FROM json_each(recovery.value))=2",
        "json_type(recovery.value,'$.state')='text'",
        "json_type(recovery.value,'$.request')='object'",
        "(SELECT COUNT(*) FROM json_each(recovery.value,'$.request'))=6",
    ]
    for name in ("version", "head_revision"):
        checks.append(f"json_type(recovery.value,'{request}{name}')='integer'")
    checks.extend((f"{value('version')}=1", f"{value('head_revision')}>0"))
    for name, maximum in (
        ("operation_id", 1024), ("journal_key", 64),
        ("native_session_id", 1024), ("bridge_installation", 4096),
    ):
        checks.extend((
            f"json_type(recovery.value,'{request}{name}')='text'",
            f"length({value(name)}) BETWEEN 1 AND {maximum}",
        ))
    checks.extend((
        f"{value('operation_id')}=recovery.key",
        f"{value('journal_key')}=embodiment.key_digest",
        f"length({value('journal_key')})=64",
        f"{value('journal_key')} NOT GLOB '*[^0-9a-f]*'",
    ))
    forbidden = (*range(33), 133, 160, 5760, *range(8192, 8203), 8232, 8233, 8239, 8287, 12288)
    checks.extend(f"instr({value('operation_id')},char({code}))=0" for code in forbidden)
    return " AND ".join(checks)


RETAINED_RECOVERY_HELD = LEGACY_RETAINED_RECOVERY_HELD.replace(
    "ELSE COALESCE(json_extract(recovery.value,'$.state'),'unknown')",
    f"WHEN ({_valid_recovery_request()}) IS NOT 1 THEN 1 "
    "ELSE COALESCE(json_extract(recovery.value,'$.state'),'unknown')",
)
_METADATA_RECOVERY_COMPLETE = """
EXISTS (
        SELECT 1 FROM json_each(embodiment.creation_recoveries_json) AS recovery
        WHERE recovery.type='object' AND (%s)
        AND json_extract(recovery.value,'$.state')='complete'
        AND recovery.key NOT IN (
            SELECT value FROM json_each(embodiment.creation_adoption_json,'$.prior_operations')
        )
)
""" % _valid_recovery_request()
# Remove the column's TEXT affinity so SQLite can use the JSON expression index.
METADATA_RECOVERY_HELD = f"""
EXISTS (
    SELECT 1 FROM workspace_embodiments AS embodiment
    WHERE embodiment.creation_adoption_json IS NOT NULL AND embodiment.state!='terminal'
    AND json_extract(embodiment.creation_adoption_json,'$.session_id')=+sessions.id
    AND NOT ({_METADATA_RECOVERY_COMPLETE})
)
"""
TRIGGERS = {
    "metadata_adoption_retained": (
        "BEFORE UPDATE OF creation_adoption_json ON workspace_embodiments "
        "WHEN OLD.creation_adoption_json IS NOT NULL "
        "AND NEW.creation_adoption_json IS NOT OLD.creation_adoption_json"
    ),
    "metadata_session_namespace_retained": (
        "BEFORE INSERT ON sessions WHEN EXISTS ("
        "SELECT 1 FROM workspace_embodiments AS embodiment "
        "WHERE embodiment.creation_adoption_json IS NOT NULL "
        "AND (embodiment.session_id=NEW.id "
        "OR json_extract(embodiment.creation_adoption_json,'$.session_id')=NEW.id "
        "OR (embodiment.state!='terminal' AND json_valid(NEW.target_json) "
        "AND json_extract(embodiment.worktree_json,'$.id')=json_extract(NEW.target_json,'$.worktree_id') "
        "AND json_extract(embodiment.worktree_json,'$.repo')=json_extract(NEW.target_json,'$.project') "
        f"AND NOT ({_METADATA_RECOVERY_COMPLETE} AND EXISTS ("
        "SELECT 1 FROM json_each(embodiment.creation_handoffs_json) AS handoff "
        "WHERE handoff.type='object' AND json_extract(handoff.value,'$.successor_id')=NEW.id "
        "AND json_extract(handoff.value,'$.stage')='spawning' "
        "AND json_extract(NEW.target_json,'$.venue._agent_bridge_creation_writer')=embodiment.key_digest "
        "AND json_extract(NEW.target_json,'$.venue._agent_bridge_metadata_epoch')"
        "=json_extract(embodiment.creation_adoption_json,'$.session_id'))))))"
    ),
    "metadata_session_control_retained": (
        "BEFORE DELETE ON sessions WHEN " + METADATA_RECOVERY_HELD.replace("sessions.", "OLD.")
    ),
    "metadata_session_identity_retained": (
        "BEFORE UPDATE OF id,acp_session_id,target_json,config_json ON sessions WHEN EXISTS ("
        "SELECT 1 FROM workspace_embodiments WHERE creation_adoption_json IS NOT NULL "
        "AND json_extract(creation_adoption_json,'$.session_id')=OLD.id AND ("
        "NEW.id IS NOT OLD.id OR NEW.acp_session_id IS NOT native_session_id "
        "OR json_valid(NEW.target_json) IS NOT 1 OR json_valid(NEW.config_json) IS NOT 1 "
        "OR json_extract(NEW.target_json,'$.venue._agent_bridge_metadata_epoch') IS NOT OLD.id "
        "OR json_extract(NEW.target_json,'$.venue._agent_bridge_creation_writer') IS NOT key_digest "
        "OR json_extract(NEW.target_json,'$.worktree_id') IS NOT json_extract(worktree_json,'$.id') "
        "OR json_extract(NEW.target_json,'$.project') IS NOT json_extract(worktree_json,'$.repo') "
        "OR json_extract(NEW.config_json,'$.retained_metadata_adoption') "
        "IS NOT json_extract(OLD.config_json,'$.retained_metadata_adoption') "
        "OR json_extract(NEW.config_json,'$.dispatch_origin') "
        "IS NOT json_extract(OLD.config_json,'$.dispatch_origin')))"
    ),
    "creation_recovery_session_retained": (
        "BEFORE DELETE ON sessions WHEN " + ORIGINAL_RECOVERY_HELD.replace("sessions.", "OLD.")
    ),
    "creation_recovery_lineage_retained": (
        "BEFORE DELETE ON sessions WHEN " + LEGACY_RETAINED_RECOVERY_HELD.replace("sessions.", "OLD.")
    ),
    "creation_recovery_integrity_retained": (
        "BEFORE DELETE ON sessions WHEN " + RETAINED_RECOVERY_HELD.replace("sessions.", "OLD.")
    ),
    "workspace_writer_retained": (
        "BEFORE UPDATE OF key_digest,facts_digest,session_id,worktree_json,creation_writer_json "
        "ON workspace_embodiments WHEN OLD.creation_writer_json IS NOT NULL AND ("
        "NEW.key_digest IS NOT OLD.key_digest OR NEW.facts_digest IS NOT OLD.facts_digest OR "
        "NEW.session_id IS NOT OLD.session_id OR NEW.worktree_json IS NOT OLD.worktree_json OR "
        "NEW.creation_writer_json IS NOT OLD.creation_writer_json)"
    ),
    "workspace_writer_no_retrofit": (
        "BEFORE UPDATE OF creation_writer_json ON workspace_embodiments "
        "WHEN OLD.creation_writer_json IS NULL AND NEW.creation_writer_json IS NOT NULL"
    ),
    "workspace_writer_native_retained": (
        "BEFORE UPDATE OF native_session_id ON workspace_embodiments "
        "WHEN OLD.creation_writer_json IS NOT NULL AND OLD.native_session_id IS NOT NULL "
        "AND NEW.native_session_id IS NOT OLD.native_session_id"
    ),
    "workspace_writer_row_retained": (
        "BEFORE DELETE ON workspace_embodiments WHEN OLD.creation_writer_json IS NOT NULL"
    ),
    "workspace_writer_no_replace": (
        "BEFORE INSERT ON workspace_embodiments WHEN EXISTS ("
        "SELECT 1 FROM workspace_embodiments WHERE key_digest=NEW.key_digest "
        "AND creation_writer_json IS NOT NULL)"
    ),
}


def statements() -> dict[str, str]:
    result = {}
    for name, body in TRIGGERS.items():
        reason = "bridge_metadata_adoption_retained" if name.startswith("metadata_") else "bridge_creation_writer_retained"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} {body} "
            f"BEGIN SELECT RAISE(ABORT,'{reason}'); END"
        )
    return result


def ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(workspace_embodiments)")}
    for column in COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE workspace_embodiments ADD COLUMN {column} TEXT")
    conn.execute(_ADOPTION_INDEX_SQL)
    for statement in statements().values():
        conn.execute(statement)


def migrate_metadata(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        ensure_schema(conn)
        validate_schema(conn)
        conn.execute("UPDATE schema_version SET version=?", (49,))


def validate_schema(
    conn: sqlite3.Connection, *, require_recovery: bool = True, require_metadata: bool | None = None,
) -> None:
    require_metadata = require_recovery if require_metadata is None else require_metadata
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(workspace_embodiments)")}
    required = COLUMNS if require_metadata else COLUMNS[:3] if require_recovery else COLUMNS[:2]
    for column in required:
        row = columns.get(column)
        if row is None or row[2].upper() != "TEXT" or row[3] != 0 or row[5] != 0:
            raise RuntimeCustodyError(f"Creation writer requires writable preparation: {column}")
    if require_metadata:
        index = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (ADOPTION_INDEX,),
        ).fetchone()
        if (
            index is None or index[0] != "index" or index[1] != "workspace_embodiments"
            or type(index[2]) is not str
            or _normalized_schema(index[2]) != _normalized_schema(_ADOPTION_INDEX_SQL)
        ):
            raise RuntimeCustodyError(f"Creation writer requires writable preparation: {ADOPTION_INDEX}")
    for name, statement in statements().items():
        if not require_metadata and name.startswith("metadata_"):
            continue
        if not require_recovery and name.startswith("creation_recovery_"):
            continue
        table = "sessions" if name.startswith("metadata_session_") or name in {
            "creation_recovery_session_retained", "creation_recovery_lineage_retained",
            "creation_recovery_integrity_retained",
        } else "workspace_embodiments"
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != table
            or type(row[2]) is not str or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Creation writer requires writable preparation: {name}")
