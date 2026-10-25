"""Shared original local frontend observations, not automatic enrollment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from zdd.diagnostics import process_start_time

from .acquisition_custody import decode
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer, producer_id,
)

if TYPE_CHECKING:
    from .db import Database


def frontend_producer(
    db: Database, installation: RuntimeInstallation, admission: RuntimeAdmission,
) -> RuntimeProducer:
    identity = db._runtime_installation(installation)
    with db._runtime_transaction(write=False) as conn:
        db._runtime_admission(conn, identity, admission, cohort="Original frontend")
        row = conn.execute(
            "SELECT producer_json FROM bridge_runtime_producers WHERE id=?", (admission.producer,),
        ).fetchone()
        try:
            producer = RuntimeProducer(**decode(row["producer_json"]))
        except (TypeError, ValueError) as exc:
            raise RuntimeCustodyError("Malformed original frontend producer") from exc
        if row["producer_json"] != producer.document():
            raise RuntimeCustodyError("Original frontend producer document changed")
        return producer


def require_frontend_process(
    db: Database, installation: RuntimeInstallation, admission: RuntimeAdmission,
    producer: RuntimeProducer, database_identity: tuple[int, int],
) -> None:
    stat = Path(installation.database).stat()
    if (
        (stat.st_dev, stat.st_ino) != database_identity
        or os.getpid() != producer.pid
        or process_start_time(producer.pid) != producer.process_start
        or producer_id(installation.document(), admission.epoch, producer) != admission.producer
    ):
        raise RuntimeCustodyError("Original frontend process or database changed")
    if frontend_producer(db, installation, admission) != producer:
        raise RuntimeCustodyError("Original frontend producer history changed")
