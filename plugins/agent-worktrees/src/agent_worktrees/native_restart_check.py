"""Provisional current-worktree checks, never a native launch reservation."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Callable
from uuid import UUID

from . import native_source_context, sessions, tracking
from .session_liveness import session_liveness
from .tracking_session_activations import source_end_authoritative
from .worktree_status_daemon import _is_safe_identity_token


_FIELDS = {
    "project", "worktree_id", "worktree_path", "native_session_id", "worktree_incarnation",
    "head_revision", "owner_revision", "activation_ordinal", "source_identity", "target_epoch",
}


def validate_expected(expected: object) -> dict:
    """Validate all caller input before resolving any tracking path."""
    if not isinstance(expected, dict) or set(expected) != _FIELDS:
        raise ValueError("Incomplete exact restart worktree expectations")
    project = expected["project"]
    if (
        not isinstance(project, str) or not project or len(project) > 4096
        or any(ord(character) < 32 for character in project)
    ):
        raise ValueError("Invalid exact restart project")
    if not isinstance(expected["worktree_id"], str) or not _is_safe_identity_token(
        expected["worktree_id"],
    ):
        raise ValueError("Invalid exact restart worktree identity")
    native_id = expected["native_session_id"]
    if not isinstance(native_id, str) or str(UUID(native_id)) != native_id:
        raise ValueError("Invalid exact restart native UUID")
    for field in ("worktree_incarnation", "source_identity"):
        pattern = r"[0-9a-f]{16}" if field == "worktree_incarnation" else r"native-source-v1:[0-9a-f]{64}"
        if not isinstance(expected[field], str) or re.fullmatch(pattern, expected[field]) is None:
            raise ValueError(f"Invalid restart {field}")
    for field in ("head_revision", "owner_revision", "activation_ordinal"):
        if type(expected[field]) is not int or not (
            (1 if field == "activation_ordinal" else 0)
            <= expected[field] <= tracking.MAX_PERSISTED_COUNTER
        ):
            raise ValueError(f"Invalid restart {field}")
    worktree = expected["worktree_path"]
    if (
        not isinstance(worktree, str) or not worktree or len(worktree) > 4096
        or any(ord(character) < 32 for character in worktree) or not Path(worktree).is_absolute()
    ):
        raise ValueError("Invalid exact restart worktree path")
    epoch = expected["target_epoch"]
    if (
        not isinstance(epoch, dict) or set(epoch) != {"platform", "environment_id", "boot_id"}
        or not isinstance(epoch["platform"], str) or epoch["platform"] not in {"linux", "wsl"}
        or not isinstance(epoch["environment_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", epoch["environment_id"]) is None
        or not isinstance(epoch["boot_id"], str)
        or re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", epoch["boot_id"]) is None
    ):
        raise ValueError("Invalid exact restart target epoch")
    return expected


def _refusal(record: tracking.WorktreeRecord, expected: dict) -> str | None:
    if record.status != "active" or record.completed_at:
        return "terminal-worktree"
    if record.repo != expected["project"]:
        return "project-changed"
    if record.creation_nonce != expected["worktree_incarnation"]:
        return "worktree-incarnation-changed"
    if (
        record.resolved_head_session != expected["native_session_id"]
        or record.head_revision != expected["head_revision"]
    ):
        return "head-changed"
    if record.lifecycle_revision != expected["owner_revision"]:
        return "lifecycle-changed"
    if record.worktree_path != expected["worktree_path"]:
        return "worktree-path-changed"
    if record.platform != expected["target_epoch"]["platform"]:
        return "foreign-environment"
    if record.paused:
        return "paused"
    if record.paused_revision:
        return "pause-history-unqualified"
    if record.pending_handoffs:
        return "pending-handoff"
    if record.pending_seed:
        return "pending-seed"
    if (
        record.execution_leg_opaque or record.session_backend_opaque
        or tracking.derive_execution_leg(record) is not None
    ):
        return "non-native-execution"
    # Provenance and claim-status do not express the current owner's launch policy.
    if record.dispatch_attempt is not None or record.dispatch_attempt_opaque or record.dispatch_attempt_raw_present:
        return "task-policy-unqualified"
    if record.owner_ref or record.resources:
        return "claim-policy-unqualified"
    if record.bound_agent or record.profile_assignment_revision or record.profile_assignments:
        return "profile-policy-unqualified"
    worktree = Path(record.worktree_path)
    if not worktree.is_dir():
        return "missing-worktree-path"
    if worktree.resolve(strict=True) != worktree:
        return "worktree-path-indirect"
    entry = record.session_entry(expected["native_session_id"])
    if entry is None or entry.state != "active":
        return "inactive-native-head"
    if not entry.activations or len(entry.activations) > tracking._MAX_SESSION_ACTIVATIONS:
        return "activation-source-unconfirmed"
    latest = max(entry.activations, key=lambda item: item.ordinal)
    if latest.ordinal != expected["activation_ordinal"] or latest.source_identity != expected["source_identity"]:
        return "activation-changed"
    if (
        latest.source_ambiguous or not source_end_authoritative(entry, latest.source_identity)
        or sum(item.source_identity == latest.source_identity for item in entry.activations) != 1
    ):
        return "activation-source-unconfirmed"
    if entry.ended_at is not None or latest.ended_at is not None:
        return "ended-native-activation"
    if not sessions.session_has_conversation_data(expected["native_session_id"]):
        return "missing-native-conversation"
    mux = sessions._list_mux_sessions()
    if mux is None:
        return "mux-state-unknown"
    if sessions.mux_session_name(record.worktree_id) in mux:
        return "existing-mux"
    state = session_liveness(expected["native_session_id"])
    return None if state == "dead" else f"native-head-{state}"


def check(
    expected: object, *, loader: Callable[[], tracking.WorktreeRecord | None],
) -> dict:
    """Check exact current owner facts without writing, reserving or launching.

    This snapshot is not a capability. Task/claim/profile policy, launch fencing
    and acknowledged seed pickup must be composed before any future restoration.
    Ended shutdown activations remain refused rather than guessing permission.
    """
    expected = validate_expected(expected)
    result = {
        "schema": "native-restart-worktree-check", "version": 1, "expected": expected,
        "worktree_qualified": False, "launch_authorized": False, "automatic_eligible": False,
    }
    record = loader()
    if record is None or record.worktree_id != expected["worktree_id"]:
        return {**result, "reason": "missing-or-mismatched-worktree"}
    before = dataclasses.asdict(record)
    reason = (
        "foreign-environment" if native_source_context.epoch() != expected["target_epoch"]
        else _refusal(record, expected)
    )
    current = loader()
    if current is None or dataclasses.asdict(current) != before:
        return {**result, "reason": "owner-changed"}
    return {
        **result, "worktree_qualified": reason is None,
        "reason": reason or "current-worktree-matches",
    }
