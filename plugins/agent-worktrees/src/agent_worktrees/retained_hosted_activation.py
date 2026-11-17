"""Corroborate an explicit retained ACP owner's physical activation."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from . import native_source_context, retained_record_writer

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

MAX_INPUT_BYTES = 8192


def parse(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("Hosted activation input exceeds its budget")
    value = json.loads(raw)
    if not (
        isinstance(value, dict)
        and set(value) == {
            "version", "operation_id", "session_id", "head_revision", "epoch", "host", "child",
        }
        and type(value["version"]) is int and value["version"] == 1
        and isinstance(value["operation_id"], str)
        and value["operation_id"].strip() and "\0" not in value["operation_id"]
        and len(value["operation_id"]) <= 1024
        and isinstance(value["session_id"], str)
        and str(UUID(value["session_id"])) == value["session_id"]
        and type(value["head_revision"]) is int and value["head_revision"] > 0
        and isinstance(value["epoch"], dict)
        and set(value["epoch"]) == {"platform", "environment_id", "boot_id"}
        and all(isinstance(item, str) and item for item in value["epoch"].values())
    ):
        raise ValueError("Invalid hosted activation")
    for name in ("host", "child"):
        process = value[name]
        if not (
            isinstance(process, dict) and set(process) == {"pid", "start_time"}
            and type(process["pid"]) is int and process["pid"] > 0
            and isinstance(process["start_time"], str)
            and process["start_time"].isascii() and process["start_time"].isdecimal()
            and len(process["start_time"]) <= 32
        ):
            raise ValueError("Invalid hosted activation process")
    if value["host"]["pid"] == value["child"]["pid"]:
        raise ValueError("Hosted activation requires distinct Host and child processes")
    return value


def source_identity(
    record: WorktreeRecord, session_id: str, pid: int | None, value: dict[str, Any],
) -> str:
    """Run inside the canonical record lock, before any claim or lineage write.

    The qualified ACP owner supplies the accepted native-load association.
    Worktrees corroborates its physical source; no CLI hold is manufactured.
    """
    value = parse(json.dumps(value))
    entry = record.session_entry(session_id)
    latest = max(entry.activations, key=lambda item: item.ordinal, default=None) if entry else None
    transition = record.replayed_head_transition
    if (
        not retained_record_writer.active() or value["session_id"] != session_id
        or type(pid) is not int or pid != value["child"]["pid"]
        or record.status in {"complete", "completed", "finalized", "finalizing"}
        or record.resolved_head_session != session_id
        or transition is None or transition.revision != value["head_revision"]
        or record.pending_handoffs or entry is None or entry.state != "active"
        or entry.source_history_saturated
        or latest is None or latest.source_identity is None or latest.source_ambiguous
    ):
        raise ValueError("Hosted activation requires its unchanged retained native head and history")
    observed_epoch = native_source_context.epoch()
    observed = {
        name: native_source_context.process(value[name]["pid"], require_live=True)
        for name in ("host", "child")
    }
    if (
        observed_epoch != value["epoch"]
        or any(
            {key: observed[name][key] for key in ("pid", "start_time")} != value[name]
            for name in observed
        )
        or observed["child"]["parent_pid"] != value["host"]["pid"]
        or any(
            native_source_context.process(value[name]["pid"], require_live=True) != observed[name]
            for name in observed
        )
        or native_source_context.epoch() != observed_epoch
    ):
        raise ValueError("Hosted activation process or execution epoch changed")
    identity = [
        session_id, record.creation_nonce, observed_epoch, value["host"], value["child"],
    ]
    token = "hosted-acp-source-v1:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    if latest.ended_at is None:
        if latest.source_identity != token or token in entry.pruned_sources:
            raise ValueError("Hosted activation cannot replace an open source")
    elif any(item.source_identity == token for item in entry.activations):
        raise ValueError("Hosted activation source is already closed")
    elif entry.pruned_sources:
        raise ValueError("A fresh hosted source cannot replace pruned history")
    return token
