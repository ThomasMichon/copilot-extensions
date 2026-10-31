"""DB-owned admission epochs and associations; no lifecycle activation API."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sqlite3

from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeEpoch, RuntimeInstallation,
    RuntimeProducer, encode, nonblank, positive, producer_id,
)


class _RuntimeCustodyMixin:
    @contextmanager
    def _runtime_transaction(
        self, *, write: bool, full_sync: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        if write and self.read_only:
            raise PermissionError("Read-only database cannot change runtime custody")
        with self._write_lock:
            conn = self._get_conn()
            if conn.in_transaction:
                raise RuntimeCustodyError("Runtime custody cannot borrow an unfinished transaction")
            synchronous = (
                conn.execute("PRAGMA synchronous").fetchone()[0] if write and full_sync else None
            )
            if synchronous is not None and synchronous < 2:
                conn.execute("PRAGMA synchronous=FULL")
            try:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                if synchronous is not None and synchronous < 2:
                    conn.execute(f"PRAGMA synchronous={synchronous}")

    def _execute_runtime_admitted_write(
        self, statement: str, parameters: tuple[object, ...],
        admission: RuntimeAdmission | None,
    ) -> sqlite3.Cursor:
        """Sync retained admission without changing ordinary event writes."""
        if admission is None:
            return self.execute_write(statement, parameters)
        with self._runtime_transaction(write=True) as conn:
            return conn.execute(statement, parameters)

    def _runtime_installation(self, installation: RuntimeInstallation) -> str:
        if type(installation) is not RuntimeInstallation:
            raise RuntimeCustodyError("Runtime custody requires installation owner observations")
        if Path(installation.database) != self.db_path.absolute():
            raise RuntimeCustodyError("Runtime custody belongs to another database")
        return installation.document()

    @staticmethod
    def _runtime_epoch(conn: sqlite3.Connection, identity: str) -> RuntimeEpoch:
        row = conn.execute("SELECT * FROM bridge_runtime_epoch WHERE singleton=1").fetchone()
        if row is None or row["installation"] != identity:
            raise RuntimeCustodyError("Runtime custody installation is absent or foreign")
        if row["state"] not in ("open", "closed"):
            raise RuntimeCustodyError("Unknown runtime custody state")
        positive(row["epoch"])
        positive(row["revision"])
        return RuntimeEpoch(identity, row["epoch"], row["revision"],
                            row["state"], row["transition_json"])

    def _runtime_admission(
        self, conn: sqlite3.Connection, identity: str, admission: RuntimeAdmission, *, cohort: str,
    ) -> None:
        if type(admission) is not RuntimeAdmission or admission.installation != identity:
            raise RuntimeCustodyError(f"{cohort} admission belongs to another installation")
        state = self._runtime_epoch(conn, identity)
        if state.state != "open" or state.epoch != admission.epoch:
            raise RuntimeCustodyError(f"{cohort} effects require the original open epoch")
        if conn.execute(
            "SELECT 1 FROM bridge_runtime_producers WHERE id=? AND installation=? AND epoch=?",
            (admission.producer, identity, admission.epoch),
        ).fetchone() is None:
            raise RuntimeCustodyError(f"{cohort} producer is not enrolled")

    def initialize_runtime_epoch(self, installation: RuntimeInstallation) -> RuntimeEpoch:
        """Bind storage explicitly; existing rows remain unqualified history."""
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=True) as conn:
            row = conn.execute("SELECT * FROM bridge_runtime_epoch WHERE singleton=1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO bridge_runtime_epoch VALUES (1,?,1,1,'open',NULL)", (identity,),
                )
            state = self._runtime_epoch(conn, identity)
            if state.state != "open":
                raise RuntimeCustodyError("Closed runtime admission cannot be reinitialized")
            return state

    def runtime_epoch(self, installation: RuntimeInstallation) -> RuntimeEpoch:
        """Read the retained DB generation without enrolling or reopening it."""
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=False) as conn:
            return self._runtime_epoch(conn, identity)

    def enroll_runtime_producer(
        self, installation: RuntimeInstallation, epoch: int, producer: RuntimeProducer,
    ) -> RuntimeAdmission:
        """Retain exact owner observations; this does not authenticate a caller."""
        identity = self._runtime_installation(installation)
        positive(epoch)
        if type(producer) is not RuntimeProducer:
            raise RuntimeCustodyError("Producer enrollment requires known frontend observations")
        key = producer_id(identity, epoch, producer)
        with self._runtime_transaction(write=True) as conn:
            state = self._runtime_epoch(conn, identity)
            if state.state != "open" or state.epoch != epoch:
                raise RuntimeCustodyError("Producer enrollment requires the current open epoch")
            inserted = conn.execute(
                "INSERT OR IGNORE INTO bridge_runtime_producers VALUES (?,?,?,?)",
                (key, identity, epoch, producer.document()),
            ).rowcount
            row = conn.execute(
                "SELECT * FROM bridge_runtime_producers WHERE id=?", (key,),
            ).fetchone()
            if row is None or (
                row["installation"], row["epoch"], row["producer_json"]
            ) != (identity, epoch, producer.document()):
                raise RuntimeCustodyError("Producer enrollment conflicts with retained evidence")
            if inserted:
                conn.execute("UPDATE bridge_runtime_epoch SET revision=revision+1")
        return RuntimeAdmission(identity, epoch, key)

    def close_runtime_epoch(
        self, installation: RuntimeInstallation, expected: RuntimeEpoch, *,
        transition_id: str, target_assembly: str, predecessor: RuntimeProducer,
    ) -> RuntimeEpoch:
        """CAS only DB admission; no claim of complete runtime closure."""
        identity = self._runtime_installation(installation)
        if type(expected) is not RuntimeEpoch or expected.installation != identity:
            raise RuntimeCustodyError("Closure requires the original installation epoch")
        nonblank(transition_id)
        nonblank(target_assembly)
        if type(predecessor) is not RuntimeProducer:
            raise RuntimeCustodyError("Closure requires retained predecessor observations")
        transition = encode({
            "id": transition_id, "target_assembly": target_assembly,
            "predecessor": predecessor.document(), "from_epoch": expected.epoch,
        })
        with self._runtime_transaction(write=True) as conn:
            current = self._runtime_epoch(conn, identity)
            enrolled = conn.execute(
                "SELECT producer_json FROM bridge_runtime_producers WHERE id=?",
                (producer_id(identity, expected.epoch, predecessor),),
            ).fetchone()
            if enrolled is None or enrolled["producer_json"] != predecessor.document():
                raise RuntimeCustodyError("Closure predecessor is not the retained enrolled producer")
            if current.state == "closed":
                if current.epoch == expected.epoch + 1 and current.transition == transition:
                    return current
                raise RuntimeCustodyError("Another transition already closed runtime admission")
            if current != expected:
                raise RuntimeCustodyError("Runtime custody changed before closure")
            changed = conn.execute(
                "UPDATE bridge_runtime_epoch SET epoch=epoch+1, revision=revision+1,"
                "state='closed',transition_json=? WHERE singleton=1 AND installation=? "
                "AND epoch=? AND revision=? AND state='open'",
                (transition, identity, expected.epoch, expected.revision),
            ).rowcount
            if changed != 1:
                raise RuntimeCustodyError("Runtime closure lost its compare-and-swap")
            return self._runtime_epoch(conn, identity)

    def runtime_custody_inventory(
        self, installation: RuntimeInstallation, *, expected: RuntimeEpoch | None = None,
    ) -> dict[str, object]:
        """Read original durable identities, including terminal/legacy rows."""
        from .runtime_custody_inventory import read_inventory

        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=False) as conn:
            result = read_inventory(conn, identity)
            if expected is not None:
                if type(expected) is not RuntimeEpoch or self._runtime_epoch(conn, identity) != expected:
                    raise RuntimeCustodyError("Runtime inventory revision or epoch changed")
            return result
