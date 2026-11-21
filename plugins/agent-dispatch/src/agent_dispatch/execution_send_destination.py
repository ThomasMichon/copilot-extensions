"""Transport-owner destinations, frozen in the execution send's existing identity."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from .bridge_remote import normalize_host
from .queue_records import TaskError


@dataclass(frozen=True)
class ExecutionDestination:
    route: Literal["driver", "container", "bridge-session"]
    address: str
    workspace: str | None = None
    native: str | None = None
    version: Literal[1] = 1

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise TaskError("unsupported execution destination version")
        for field in (self.address, self.workspace, self.native):
            if field is not None and (
                not isinstance(field, str) or not field.strip() or "\0" in field
            ):
                raise TaskError("invalid execution destination")
        if not self.address or self.route not in {"driver", "container", "bridge-session"}:
            raise TaskError("execution destination requires its transport route and address")
        if self.route == "driver":
            if not (self.workspace or self.native):
                raise TaskError("driver destination lacks workspace and native")
            object.__setattr__(self, "address", normalize_host(self.address))
        elif self.route == "container":
            if not self.address.startswith("container:") or not self.workspace or self.native:
                raise TaskError("container destination requires the selected venue and workspace")
        elif self.workspace is not None or self.native is not None:
            raise TaskError("Bridge delivery requires its actual session address")

    def check_reservation(self, conn: sqlite3.Connection, reservation: sqlite3.Row) -> None:
        if self.route != "driver":
            return
        from .ordinary_native_bindings import reservation_native_identity

        # A driver route must not discard one of its known target selectors.
        if (
            self.address != normalize_host(reservation["execution_machine"] or "")
            or self.workspace != reservation["worktree"]
            or self.native != reservation_native_identity(conn, reservation)
        ):
            raise TaskError("driver destination conflicts with its admitted reservation")


_UNRESOLVED = "state IN ('pending','unknown')"
# A Bridge session address and a path inside a container are not comparable to
# a managed host/worktree/native tuple. Neither are target-less legacy sends.
_HOST = (
    "CASE WHEN json_valid(identity) THEN CASE WHEN"
    " json_extract(identity,'$.send_id')=send_id"
    " AND json_extract(identity,'$.reservation_key')=reservation_key"
    " AND json_type(identity,'$.task_generation')='integer'"
    " AND json_extract(identity,'$.task_generation')=task_generation"
    " AND json_extract(identity,'$.kind')=kind AND json_type(identity,'$.scope')='object'"
    " AND json_extract(identity,'$.scope')=scope_key"
    " AND json_type(identity,'$.generation')='integer' AND json_extract(identity,'$.generation')=1"
    " AND json_type(identity,'$.runtime_id')='text'"
    " AND length(json_extract(identity,'$.runtime_id'))>0"
    " AND json_type(identity,'$.destination.version')='integer'"
    " AND json_extract(identity,'$.destination.version')=1"
    " AND json_extract(identity,'$.destination.route')='driver'"
    " AND json_type(identity,'$.destination.address')='text'"
    " AND length(json_extract(identity,'$.destination.address'))>0"
    " AND json_type(identity,'$.destination.workspace') IN ('text','null')"
    " AND json_type(identity,'$.destination.native') IN ('text','null')"
    " AND (length(json_extract(identity,'$.destination.workspace'))>0"
    " OR length(json_extract(identity,'$.destination.native'))>0)"
    " THEN json_extract(identity,'$.destination.address') END END"
)


def _key(field: str) -> str:
    return (f"CASE WHEN json_valid(identity)"
            f" THEN json_extract(identity,'$.destination.{field}') END")


def migrate(conn: sqlite3.Connection) -> None:
    for field in ("workspace", "native"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_send_destination_v1_{field}"
            f" ON execution_send_custody({_HOST},{_key(field)}) WHERE {_UNRESOLVED}"
        )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS execution_send_identity_immutable"
        " BEFORE UPDATE OF identity ON execution_send_custody"
        " WHEN NEW.identity IS NOT OLD.identity"
        " BEGIN SELECT RAISE(ABORT,'execution send identity is immutable'); END"
    )


def require_clear(conn: sqlite3.Connection, machine: str, tree: str, native: str) -> None:
    host = normalize_host(machine)
    if conn.execute(
        f"SELECT 1 FROM execution_send_custody WHERE {_UNRESOLVED} AND ({_HOST}) IS NULL LIMIT 1"  # noqa: S608 -- fixed SQL
    ).fetchone():
        raise TaskError("unresolved execution send has no comparable immutable driver destination")
    for field, value in (("workspace", tree), ("native", native)):
        if conn.execute(
            f"SELECT 1 FROM execution_send_custody WHERE {_UNRESOLVED}"  # noqa: S608 -- fixed SQL
            f" AND ({_HOST})=? AND ({_key(field)})=? LIMIT 1",
            (host, value),
        ).fetchone():
            raise TaskError("unresolved execution send targets the captured driver")
