"""Owner-supplied DB custody observations, not native or installation grants."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3


class RuntimeCustodyError(ValueError):
    """The retained installation, epoch or producer does not match."""


def nonblank(value: str) -> None:
    if type(value) is not str or not value.strip() or "\0" in value:
        raise RuntimeCustodyError("Custody identity must be a nonblank string")


def positive(value: int) -> None:
    if type(value) is not int or value < 1:
        raise RuntimeCustodyError("Custody generation must be a positive integer")


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class RuntimeInstallation:
    """Exact observations supplied by the installation owner; no discovery here."""

    identity: str
    source_fingerprint: str
    runtime_root: str
    database: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            nonblank(value)
        for value in (self.runtime_root, self.database):
            if not Path(value).is_absolute() or ".." in Path(value).parts:
                raise RuntimeCustodyError("Custody roots must be absolute")

    def document(self) -> str:
        return encode(asdict(self))


@dataclass(frozen=True)
class RuntimeProducer:
    """Retained frontend observations, not proof that all producers enrolled."""

    incarnation: str
    pid: int
    process_start: str
    role: str = "frontend"

    def __post_init__(self) -> None:
        nonblank(self.incarnation)
        nonblank(self.process_start)
        positive(self.pid)
        if self.role != "frontend":
            raise RuntimeCustodyError("Unsupported custody producer role")

    def document(self) -> str:
        return encode(asdict(self))


@dataclass(frozen=True)
class RuntimeAdmission:
    installation: str
    epoch: int
    producer: str

    def __post_init__(self) -> None:
        nonblank(self.installation)
        positive(self.epoch)
        nonblank(self.producer)

    def document(self) -> str:
        return encode(asdict(self))

    @classmethod
    def parse(cls, raw: str) -> RuntimeAdmission:
        """Decode an original association without losing duplicate fields."""
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise RuntimeCustodyError("Duplicate producer association field")
                result[key] = value
            return result

        try:
            value = json.loads(raw, object_pairs_hook=unique)
            if type(value) is not dict or set(value) != {"installation", "epoch", "producer"}:
                raise RuntimeCustodyError("Unknown producer association")
            return cls(**value)
        except (TypeError, ValueError) as exc:
            raise RuntimeCustodyError("Malformed retained producer association") from exc


@dataclass(frozen=True)
class RuntimeEpoch:
    installation: str
    epoch: int
    revision: int
    state: str
    transition: str | None

    def __post_init__(self) -> None:
        nonblank(self.installation)
        positive(self.epoch)
        positive(self.revision)
        if self.state not in ("open", "closed") or (
            (self.state == "open") != (self.transition is None)
        ):
            raise RuntimeCustodyError("Inconsistent runtime epoch state")


def admission_document(value: RuntimeAdmission | None) -> str | None:
    if value is None:
        return None
    if type(value) is not RuntimeAdmission:
        raise RuntimeCustodyError("Admission requires a retained producer association")
    return value.document()


def producer_id(installation: str, epoch: int, producer: RuntimeProducer) -> str:
    return hashlib.sha256(encode([installation, epoch, asdict(producer)]).encode()).hexdigest()


def _schema_definitions() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    tables = {
        "bridge_runtime_epoch": "CREATE TABLE IF NOT EXISTS bridge_runtime_epoch ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
        "installation TEXT NOT NULL, epoch INTEGER NOT NULL CHECK(epoch>0),"
        "revision INTEGER NOT NULL CHECK(revision>0),"
        "state TEXT NOT NULL CHECK(state IN ('open','closed')), transition_json TEXT)",
        "bridge_runtime_producers": "CREATE TABLE IF NOT EXISTS bridge_runtime_producers ("
        "id TEXT PRIMARY KEY, installation TEXT NOT NULL, epoch INTEGER NOT NULL,"
        "producer_json TEXT NOT NULL)",
    }
    return tables, operation_triggers(("sessions", "turns"))


def operation_triggers(tables: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    """Canonical admission, association and history guards for DB-owned operations."""
    triggers = {}
    for table in tables:
        triggers[f"{table}_runtime_admission"] = (table,
            f"CREATE TRIGGER IF NOT EXISTS {table}_runtime_admission "
            f"BEFORE INSERT ON {table} BEGIN "
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM bridge_runtime_epoch WHERE state='closed') "
            "THEN RAISE(ABORT,'bridge_runtime_admission_closed') END;"
            "SELECT CASE WHEN NEW.runtime_custody_json IS NOT NULL "
            "AND NOT json_valid(NEW.runtime_custody_json) "
            "THEN RAISE(ABORT,'bridge_runtime_association_invalid') END;"
            "SELECT CASE WHEN NEW.runtime_custody_json IS NOT NULL AND ("
            "json_type(NEW.runtime_custody_json) != 'object' "
            "OR (SELECT count(*) FROM json_each(NEW.runtime_custody_json)) != 3 "
            "OR json_type(NEW.runtime_custody_json,'$.epoch') IS NOT 'integer' "
            "OR NOT EXISTS (SELECT 1 FROM bridge_runtime_epoch e "
            "JOIN bridge_runtime_producers p ON p.installation=e.installation "
            "AND p.epoch=e.epoch WHERE e.state='open' "
            "AND e.installation=json_extract(NEW.runtime_custody_json,'$.installation') "
            "AND e.epoch=json_extract(NEW.runtime_custody_json,'$.epoch') "
            "AND p.id=json_extract(NEW.runtime_custody_json,'$.producer'))) "
            "THEN RAISE(ABORT,'bridge_runtime_association_invalid') END; END"
        )
        triggers[f"{table}_runtime_association_immutable"] = (table,
            f"CREATE TRIGGER IF NOT EXISTS {table}_runtime_association_immutable "
            f"BEFORE UPDATE OF runtime_custody_json ON {table} "
            "WHEN NEW.runtime_custody_json IS NOT OLD.runtime_custody_json "
            "BEGIN SELECT RAISE(ABORT,'bridge_runtime_association_immutable'); END"
        )
        triggers[f"{table}_runtime_history_retained"] = (table,
            f"CREATE TRIGGER IF NOT EXISTS {table}_runtime_history_retained "
            f"BEFORE DELETE ON {table} WHEN OLD.runtime_custody_json IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT,'bridge_runtime_custody_retained'); END"
        )
        for operation in ("INSERT", "UPDATE", "DELETE"):
            triggers[f"{table}_runtime_revision_{operation.lower()}"] = (table,
                f"CREATE TRIGGER IF NOT EXISTS {table}_runtime_revision_{operation.lower()} "
                f"AFTER {operation} ON {table} BEGIN "
                "UPDATE bridge_runtime_epoch SET revision=revision+1 WHERE singleton=1; END"
            )
    return triggers


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare custody storage through the writable schema owner only."""
    tables, triggers = _schema_definitions()
    for statement in tables.values():
        conn.execute(statement)
    for table in ("sessions", "turns"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "runtime_custody_json" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN runtime_custody_json TEXT")
    for _table, statement in triggers.values():
        conn.execute(statement)


def _normalized_schema(statement: str) -> str:
    return " ".join(statement.split()).replace(" IF NOT EXISTS ", " ")


def validate_schema(conn: sqlite3.Connection) -> None:
    """Refuse incomplete custody storage without provisioning or migration."""
    tables, triggers = _schema_definitions()
    expected = {
        name: ("table", name, statement) for name, statement in tables.items()
    }
    expected.update({
        name: ("trigger", table, statement) for name, (table, statement) in triggers.items()
    })
    for table, names in (
        ("sessions", ("runtime_custody_json",)),
        ("turns", ("runtime_custody_json", "acquisition_delivery_id", "native_admission_json")),
    ):
        columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
        for name in names:
            column = columns.get(name)
            if (
                column is None or column[2].upper() != "TEXT"
                or column[3] != 0 or column[5] != 0
            ):
                raise RuntimeCustodyError(
                    f"Runtime custody schema requires supported writable preparation: {table}.{name}"
                )
    for name, (kind, table, statement) in expected.items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != kind or row[1] != table or not isinstance(row[2], str)
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(
                f"Runtime custody schema requires supported writable preparation: {name}"
            )
