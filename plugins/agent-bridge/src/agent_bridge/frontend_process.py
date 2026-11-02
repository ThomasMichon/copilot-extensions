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
    *, effect: bool = True,
) -> RuntimeProducer:
    identity = db._runtime_installation(installation)
    with db._runtime_transaction(write=False) as conn:
        if effect:
            db._runtime_admission(conn, identity, admission, cohort="Original frontend")
        elif type(admission) is not RuntimeAdmission or admission.installation != identity:
            raise RuntimeCustodyError("Original frontend observation belongs to another installation")
        else:
            db._runtime_epoch(conn, identity)
        row = conn.execute(
            "SELECT installation,epoch,producer_json FROM bridge_runtime_producers WHERE id=?",
            (admission.producer,),
        ).fetchone()
        if row is None or (row["installation"], row["epoch"]) != (identity, admission.epoch):
            raise RuntimeCustodyError("Original frontend producer history changed")
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
    *, effect: bool = True,
) -> None:
    stat = Path(installation.database).stat()
    if (
        (stat.st_dev, stat.st_ino) != database_identity
        or os.getpid() != producer.pid
        or process_start_time(producer.pid) != producer.process_start
        or producer_id(installation.document(), admission.epoch, producer) != admission.producer
    ):
        raise RuntimeCustodyError("Original frontend process or database changed")
    if frontend_producer(db, installation, admission, effect=effect) != producer:
        raise RuntimeCustodyError("Original frontend producer history changed")
