"""Prove prompt admission from durable rows, not a pre-delivery journal stage."""

from __future__ import annotations

from .acquisition_contract import AcquisitionRequest, AcquisitionResult
from .db import Database


def admitted(
    db: Database, request: AcquisitionRequest, result: AcquisitionResult, prompt: str,
) -> bool:
    if result.mode == "cli":
        rows = db.execute_read(
            "SELECT id FROM live_messages WHERE idempotency_key=? AND session_id=? AND body=?",
            (f"acquisition:{request.delivery_id}", result.session_id, prompt),
        )
    else:
        rows = db.execute_read(
            "SELECT turn_index FROM turns WHERE acquisition_delivery_id=? AND session_id=? AND prompt=?",
            (request.delivery_id, result.session_id, prompt),
        )
    return len(rows) == 1
