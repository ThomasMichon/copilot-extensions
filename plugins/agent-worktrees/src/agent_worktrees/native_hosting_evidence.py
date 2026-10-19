"""Read-only owner corroboration for a provider's private hosting journal."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable
from uuid import UUID

from . import config as cfg, sessions, tracking
from .session_liveness import NATIVE_BINDING_PROOF, session_liveness, session_process_owns_hold
from .worktree_status_daemon import _is_safe_identity_token


def observe(
    worktree_id: str, *, loader: Callable[[], tracking.WorktreeRecord | None],
) -> dict:
    """Confirm the current exact native head without acquiring or changing it."""
    if not isinstance(worktree_id, str) or not _is_safe_identity_token(worktree_id):
        raise ValueError("Invalid exact hosting worktree identity")
    result = {
        "schema": "native-hosting-evidence", "version": 1,
        "worktree_id": worktree_id, "confirmed": False,
        "automatic_eligible": False, "liveness": "unknown",
        "binding_proof": None,
    }
    record = loader()
    if record is None or record.worktree_id != worktree_id:
        return {**result, "reason": "missing-or-mismatched-worktree"}
    before = dataclasses.asdict(record)
    result.update(worktree_path=record.worktree_path, platform=record.platform)
    if record.status != "active" or record.completed_at:
        return {**result, "reason": "terminal-worktree"}
    if record.paused:
        return {**result, "reason": "paused"}
    if record.platform != cfg.detect_platform():
        return {**result, "reason": "foreign-environment"}
    if record.platform not in {"linux", "wsl"}:
        return {**result, "reason": "native-hosting-unqualified"}
    if not Path(record.worktree_path).is_dir():
        return {**result, "reason": "missing-worktree-path"}
    native_id = record.resolved_head_session
    if not native_id:
        return {**result, "reason": "no-native-head"}
    try:
        if str(UUID(native_id)) != native_id:
            raise ValueError("noncanonical native UUID")
    except (ValueError, TypeError, AttributeError):
        return {**result, "reason": "invalid-native-head"}
    entry = record.session_entry(native_id)
    if entry is None or entry.state != "active":
        return {**result, "reason": "inactive-native-head"}
    if (
        record.execution_leg_opaque or record.session_backend_opaque
        or tracking.derive_execution_leg(record) is not None
    ):
        return {**result, "reason": "non-native-execution"}
    if not sessions.session_has_conversation_data(native_id):
        return {**result, "reason": "missing-native-conversation"}
    result.update(native_session_id=native_id, head_revision=record.head_revision)
    state = session_liveness(native_id)
    binding = None
    if state == "live":
        binding = sessions.mux_binding_for_session(
            native_id, expected_session_name=sessions.mux_session_name(worktree_id),
            include_incarnation=True,
        )
        if (
            not binding or binding.get("session_name") != sessions.mux_session_name(worktree_id)
            or not binding.get("copilot_start_time") or not binding.get("pane_start_time")
            or not isinstance(binding.get("session_incarnation"), str)
            or not binding["session_incarnation"]
            or type(binding.get("copilot_pid")) is not int or binding["copilot_pid"] <= 0
        ):
            return {**result, "reason": "native-host-unconfirmed", "liveness": "unknown"}
        if not session_process_owns_hold(
            native_id, binding["copilot_pid"], binding["copilot_start_time"],
        ):
            return {**result, "reason": "native-session-process-unconfirmed", "liveness": "unknown"}
    current = loader()
    if current is None or dataclasses.asdict(current) != before:
        return {**result, "reason": "owner-changed"}
    if binding:
        result.update(
            mux_session=binding["session_name"], pane_id=binding["pane_id"],
            session_incarnation=binding["session_incarnation"],
            native_incarnation=f"{binding['copilot_pid']}:{binding['copilot_start_time']}",
            owner_revision=record.lifecycle_revision,
            binding_proof=NATIVE_BINDING_PROOF,
        )
    return {
        **result, "liveness": state, "confirmed": state == "live",
        "reason": "native-head-live" if state == "live" else f"native-head-{state}",
    }
