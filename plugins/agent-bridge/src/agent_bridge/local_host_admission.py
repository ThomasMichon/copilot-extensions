"""Shared local Host-child identity checks; operation journals remain distinct."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
from typing import TYPE_CHECKING

from zdd.diagnostics import process_start_time

from .acquisition_custody import decode
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer, encode, producer_id,
)

if TYPE_CHECKING:
    from .local_launch_owner import LocalLaunchOwner


def context_fields(owner: LocalLaunchOwner, argv: list[str], cwd: str) -> dict:
    return {
        "version": 1, "installation": asdict(owner.frontend.installation),
        "admission": owner.admission.document(), "producer": asdict(owner.frontend.producer),
        "database_identity": list(owner.frontend.database_identity),
        "handoff_id": owner.handoff_id,
        "session_id": owner.session_id, "argv": argv, "cwd": cwd,
    }


@contextmanager
def child_database(value: dict, *, cwd: str, session_id: str, argv: list[str] | None):
    from .db import Database

    try:
        installation = RuntimeInstallation(**value["installation"])
        admission = RuntimeAdmission.parse(value["admission"])
        producer = RuntimeProducer(**value["producer"])
    except (TypeError, ValueError) as exc:
        raise RuntimeCustodyError("Malformed original local child context") from exc
    stat = Path(installation.database).stat()
    if (
        os.getppid() != producer.pid
        or process_start_time(producer.pid) != producer.process_start
        or producer_id(installation.document(), admission.epoch, producer) != admission.producer
        or [stat.st_dev, stat.st_ino] != value["database_identity"]
        or cwd != value["cwd"] or session_id != value["session_id"]
        or argv is not None and argv != value["argv"]
    ):
        raise RuntimeCustodyError("Original local child owner or workspace changed")
    db = Database(installation.database, read_only=True)
    try:
        identity = db._runtime_installation(installation)
        with db._runtime_transaction(write=False) as conn:
            db._runtime_admission(conn, identity, admission, cohort="Local child")
            row = conn.execute(
                "SELECT producer_json FROM bridge_runtime_producers WHERE id=?", (admission.producer,),
            ).fetchone()
            if row is None or row["producer_json"] != producer.document():
                raise RuntimeCustodyError("Original local child producer changed")
        yield db, admission
    finally:
        db.close()


def require_child_admission(
    raw: str, *, cwd: str, session_id: str, argv: list[str] | None = None,
) -> None:
    value = decode(raw)
    if value.get("operation") == "workspace-creation":
        from .creation_launch_owner import require_child_admission as require_creation

        require_creation(value, cwd=cwd, session_id=session_id, argv=argv)
    else:
        from .acquisition_host_admission import require_child_admission as require_acquisition

        require_acquisition(encode(value), cwd=cwd, session_id=session_id, argv=argv)
