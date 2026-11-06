"""Read the original journal's executing writer without acquiring or repairing."""

from __future__ import annotations

import json
from functools import partial
from typing import Any

from .acquisition_contract import AcquisitionRefused, AcquisitionRequest
from .acquisition_journal import _digest
from .acquisition_worktree_writer import JOURNAL_KEY, RetainedWriter, _prefix, _qualify
from .config import config_dir
from .self_retire import slot_descriptor

CAPABILITY = "retained_acquisition_writer_inspection"
SCHEMA = "copilot-extensions.acquisition-worktree-writer"


def _instance() -> dict:
    root = config_dir()
    slot = slot_descriptor(root)
    active = slot.get("active")
    if not (
        slot.get("role") == "active"
        and type(slot.get("pid")) is int and slot["pid"] > 0
        and isinstance(active, dict) and active.get("pid") == slot["pid"]
        and type(active.get("generation")) is int and active["generation"] > 0
    ):
        raise AcquisitionRefused(
            "worktree_writer_unconfirmed", "active Bridge instance is unconfirmed",
        )
    return {
        "installation_root": str(root),
        "pid": slot["pid"],
        "generation": active["generation"],
    }


async def inspect_writer(db: Any, delivery_id: str, original: dict) -> dict:
    """Requalify only the retained command; never select a replacement."""
    from .acquisition_authority import read_worktree_record, worktree_command

    before = _instance()
    try:
        data = json.loads(original["data_json"] or "{}")
        request = AcquisitionRequest.model_validate(data["_request"])
        prefix = _prefix(data[JOURNAL_KEY])
    except (ValueError, TypeError, KeyError) as exc:
        raise AcquisitionRefused(
            "worktree_writer_unconfirmed", "original acquisition writer journal is unavailable",
        ) from exc
    if (
        request.worktree_writer is None or request.mode != "acp"
        or request.delivery_id != delivery_id
        or request.worktree_id != original["worktree_id"]
        or original["state"] not in {"pending", "completed"}
        or original["request_digest"] != _digest(request)
    ):
        raise AcquisitionRefused(
            "worktree_writer_unconfirmed", "original acquisition writer binding is unconfirmed",
        )
    support = await _qualify(RetainedWriter(request, prefix))
    record = await read_worktree_record(
        request.worktree_id, request.project,
        command=partial(worktree_command, owner_prefix=prefix),
    )
    if (
        record["repo"] != request.project or record["path"] != request.worktree_path
        or record["creation_nonce"] != request.worktree_writer.creation_nonce
    ):
        raise AcquisitionRefused(
            "worktree_writer_unconfirmed", "original acquisition workspace changed",
        )
    rows = db.execute_read(
        "SELECT a.worktree_id,a.state,a.request_digest,e.stage,e.data_json "
        "FROM acquisition_attempts a LEFT JOIN acquisition_effects e USING(delivery_id) "
        "WHERE a.delivery_id=?",
        (delivery_id,),
    )
    if not rows or dict(rows[0]) != original or _instance() != before:
        raise AcquisitionRefused(
            "worktree_writer_unconfirmed", "acquisition writer owner changed during inspection",
        )
    return {
        "schema": SCHEMA, "version": 1,
        "bridge_instance": before,
        **{
            field: getattr(request, field)
            for field in (
                "delivery_id", "reservation_key", "task_id", "task_generation",
                "worktree_id", "project", "worktree_path",
            )
        },
        "worktree_writer": request.worktree_writer.model_dump(),
        "writer_scope": support["writer_scope"],
        "writer_contract": support["writer_contract"],
        "retained_session_writes": support["retained_session_writes"],
    }
