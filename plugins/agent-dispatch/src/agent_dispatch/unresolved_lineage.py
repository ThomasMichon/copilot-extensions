"""Sparse secondary indexes of the current routing keys of unresolved transport rows."""

import sqlite3
from dataclasses import dataclass

from .queue_common import Task
from .queue_execution_custody import ExecutionSendUnresolved


@dataclass(frozen=True)
class _Owner:
    table: str
    state: str
    primary: tuple[str, ...]
    alternate: tuple[str, ...] = ()

    @property
    def index(self) -> str:
        return "lineage_v1_" + self.table

    @property
    def send(self) -> bool:
        return self.table == "execution_send_custody"

    @property
    def pending(self) -> str:
        return f"{self.state} IN ('pending','unknown')"


_OWNERS = (
    _Owner("wake_outbox", "execution_send_state", ("id",), ("task_id", "generation", "wake_seq")),
    _Owner("run_waiter_wakes", "execution_send_state", ("id",)),
    _Owner("execution_send_custody", "state", ("scope_key", "send_id"),
           ("reservation_key", "task_generation", "kind")),
)


def _equal(fields: tuple[str, ...], ref: str) -> str:
    return " AND ".join(f"{key} IS {ref}.{key}" for key in fields)


def _install(conn: sqlite3.Connection, owner: _Owner) -> None:
    idx = owner.index
    fields = tuple(dict.fromkeys((*owner.primary, "task_id", *owner.alternate)))
    primary = ",".join(owner.primary)
    identities = ",".join(fields)
    # Keep only identity and routing selectors, not a second copy of send state or receipts.
    conn.execute(
        f"CREATE TABLE {idx} ({identities},repo TEXT,dedup_key TEXT,task_excl TEXT,"
        f"res_excl TEXT,PRIMARY KEY({primary}))" + (" WITHOUT ROWID" if owner.send else "")
    )
    if owner.alternate:
        conn.execute(f"CREATE UNIQUE INDEX {idx}_alt ON {idx}({','.join(owner.alternate)})")
    for name, keys in (("task", "task_id"), ("logical", "repo,dedup_key"),
                       ("exclusive", "task_excl"), ("resource", "res_excl")):
        if name != "resource" or owner.send:
            conn.execute(f"CREATE INDEX {idx}_{name} ON {idx}({keys})")

    refs = ",".join("s." + key for key in fields)
    resource = "r.exclusive_key" if owner.send else "NULL"
    join = " LEFT JOIN spawn_reservations r ON r.key=s.reservation_key" if owner.send else ""
    select = (
        f"INSERT INTO {idx} SELECT {refs},t.repo,t.dedup_key,t.exclusive_key,{resource}"  # noqa: S608 -- fixed schema
        f" FROM {owner.table} s LEFT JOIN tasks t ON t.id=s.task_id{join} WHERE s.{owner.pending}"
    )
    conn.execute(select)
    for op in ("INSERT", "UPDATE", "DELETE"):
        conditions = []
        if op != "INSERT":
            conditions.append(_equal(owner.primary, "OLD"))
        if op != "DELETE":
            conditions.append(_equal(owner.primary, "NEW"))
            # REPLACE can skip delete triggers for both primary and alternate key collisions.
            if owner.alternate:
                conditions.append(_equal(owner.alternate, "NEW"))
        refresh = ""
        if op != "DELETE":
            match = " AND ".join(f"s.{key} IS NEW.{key}" for key in owner.primary)
            refresh = f"{select} AND {match};"
        # A legacy rowid-backed outbox accepts NULL primary IDs. Refresh that
        # whole exceptional group when an update/delete removes only one of them.
        if not owner.send and op != "INSERT":
            changed_id = " AND NEW.id IS NOT NULL" if op == "UPDATE" else ""
            refresh += f"{select} AND OLD.id IS NULL{changed_id} AND s.id IS NULL;"
        event = "UPDATE OF " + identities + "," + owner.state if op == "UPDATE" else op
        conn.execute(
            f"CREATE TRIGGER {idx}_{op} AFTER {event} ON {owner.table}"  # noqa: S608 -- fixed schema
            f" BEGIN DELETE FROM {idx} WHERE {' OR '.join(conditions)}; {refresh} END"
        )

    for parent, key, ref, selectors in (
        ("tasks", "id", "task_id", {"repo": "repo", "dedup_key": "dedup_key",
                                   "task_excl": "exclusive_key"}),
        ("spawn_reservations", "key", "reservation_key", {"res_excl": "exclusive_key"}),
    ):
        if parent == "spawn_reservations" and not owner.send:
            continue
        for op in ("INSERT", "UPDATE", "DELETE"):
            updates = ""
            if op != "INSERT":
                reset = ",".join(f"{field}=NULL" for field in selectors)
                updates = f"UPDATE {idx} SET {reset} WHERE {ref}=OLD.{key};"  # noqa: S608 -- fixed schema
            if op != "DELETE":
                assign = ",".join(f"{field}=NEW.{col}" for field, col in selectors.items())
                updates += f"UPDATE {idx} SET {assign} WHERE {ref}=NEW.{key};"  # noqa: S608 -- fixed schema
            event = "UPDATE OF " + ",".join((key, *selectors.values())) if op == "UPDATE" else op
            conn.execute(f"CREATE TRIGGER {idx}_{parent}_{op} AFTER {event} ON {parent}"
                         f" BEGIN {updates} END")


def migrate(conn: sqlite3.Connection) -> None:
    """Install/backfill with the wake schema, under its existing migration write transaction."""
    if not conn.in_transaction:
        raise ValueError("unresolved lineage migration requires a transaction")
    for owner in _OWNERS:
        if not conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",
                            (owner.index,)).fetchone():
            _install(conn, owner)


def require_clear(conn: sqlite3.Connection, task: Task) -> None:
    """The no-creation-prefix fence only; keys follow the current owners, not admission time."""
    if not conn.in_transaction:
        raise ValueError("lineage lookup requires an owner snapshot")
    params = {"id": task.id, "repo": task.repo, "dedup": task.dedup_key,
              "excl": task.exclusive_key}
    for owner in _OWNERS:
        idx = owner.index
        probes = ["i.task_id=:id"]
        # Revalidate the parent by primary key: a plain SQLite REPLACE on a
        # secondary unique key need not fire delete triggers on the displaced parent.
        if task.exclusive_key is not None:
            probes.append("i.task_excl=:excl AND EXISTS (SELECT 1 FROM tasks t"
                          " WHERE t.id=i.task_id AND t.exclusive_key=:excl)")
            if owner.send:
                probes.append("i.res_excl=:excl AND EXISTS (SELECT 1 FROM spawn_reservations r"
                              " WHERE r.key=i.reservation_key AND r.exclusive_key=:excl)")
        if task.repo is not None and task.dedup_key is not None:
            probes.append("i.repo=:repo AND i.dedup_key=:dedup AND EXISTS (SELECT 1 FROM tasks t"
                          " WHERE t.id=i.task_id AND t.repo=:repo AND t.dedup_key=:dedup)")
        for probe in probes:
            if conn.execute(
                f"SELECT 1 FROM {idx} i WHERE {probe} LIMIT 1", params,  # noqa: S608 -- fixed probes
            ).fetchone():
                raise ExecutionSendUnresolved("unresolved transport in driver lineage")
