"""Original local Host-child admission; never a remote database reader or grant."""

from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
from typing import TYPE_CHECKING

from zdd.diagnostics import process_start_time

from .acquisition_contract import AcquisitionRequest
from .acquisition_custody import decode
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer, encode, producer_id,
)

if TYPE_CHECKING:
    from .acquisition_launch_owner import AcquisitionLaunchOwner

CONTEXT_ENV = "AGENT_BRIDGE_ORIGINAL_ACQUISITION"


def context(owner: AcquisitionLaunchOwner, argv: list[str], cwd: str) -> str:
    from .acquisition_journal import _digest

    return encode({
        "version": 1, "installation": asdict(owner.frontend.installation),
        "admission": owner.admission.document(), "producer": asdict(owner.frontend.producer),
        "database_identity": list(owner.frontend.database_identity),
        "delivery_id": owner.request.delivery_id, "request_digest": _digest(owner.request),
        "handoff_id": owner.handoff_id,
        "session_id": owner.session_id, "argv": argv, "cwd": cwd,
    })


def require_child_admission(
    raw: str, *, cwd: str, session_id: str, argv: list[str] | None = None,
) -> None:
    """The actual Host checks again after scheduling, before spawning its child."""
    from .acquisition_journal import AcquisitionJournal, _digest
    from .db import Database

    value = decode(raw)
    if set(value) != {
        "version", "installation", "admission", "producer", "database_identity",
        "delivery_id", "request_digest", "handoff_id", "session_id", "argv", "cwd",
    } or type(value["version"]) is not int or value["version"] != 1:
        raise RuntimeCustodyError("Unsupported original acquisition child context")
    try:
        installation = RuntimeInstallation(**value["installation"])
        admission = RuntimeAdmission.parse(value["admission"])
        producer = RuntimeProducer(**value["producer"])
    except (TypeError, ValueError) as exc:
        raise RuntimeCustodyError("Malformed original acquisition child context") from exc
    stat = Path(installation.database).stat()
    if (
        os.getppid() != producer.pid
        or process_start_time(producer.pid) != producer.process_start
        or producer_id(installation.document(), admission.epoch, producer) != admission.producer
        or [stat.st_dev, stat.st_ino] != value["database_identity"]
        or cwd != value["cwd"] or session_id != value["session_id"]
        or argv is not None and argv != value["argv"]
    ):
        raise RuntimeCustodyError("Original acquisition child owner or workspace changed")
    db = Database(installation.database, read_only=True)
    try:
        identity = db._runtime_installation(installation)
        with db._runtime_transaction(write=False) as conn:
            db._runtime_admission(conn, identity, admission, cohort="Acquisition child")
            row = conn.execute(
                "SELECT producer_json FROM bridge_runtime_producers WHERE id=?", (admission.producer,),
            ).fetchone()
            if row is None or row["producer_json"] != producer.document():
                raise RuntimeCustodyError("Original acquisition child producer changed")
            effect = conn.execute(
                "SELECT e.data_json,e.stage,a.state FROM acquisition_effects e "
                "JOIN acquisition_attempts a USING(delivery_id) WHERE delivery_id=?",
                (value["delivery_id"],),
            ).fetchone()
            if (
                effect is None or effect["state"] != "pending"
                or effect["stage"] not in ("prepared", "launching")
            ):
                raise RuntimeCustodyError("Original acquisition child recovery facts are absent")
            try:
                original = AcquisitionRequest.model_validate(decode(effect["data_json"])["_request"])
            except (TypeError, ValueError, KeyError) as exc:
                raise RuntimeCustodyError("Original acquisition child recovery facts changed") from exc
            if original.delivery_id != value["delivery_id"] or _digest(original) != value["request_digest"]:
                raise RuntimeCustodyError("Original acquisition child request changed")
        report = AcquisitionJournal(db).read_launch(original, value["handoff_id"])
        if (
            report["handoff"]["admission"] != admission.document()
            or report["handoff"]["consumer"]["session_id"] != session_id
            or report["started_at"] is None or report["result"] is not None
        ):
            raise RuntimeCustodyError("Original acquisition child launch is not outstanding")
    finally:
        db.close()
