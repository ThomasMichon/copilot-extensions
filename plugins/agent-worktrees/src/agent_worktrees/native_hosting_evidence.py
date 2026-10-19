"""Read-only owner corroboration for a provider's private hosting journal."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable
from uuid import UUID

from . import config as cfg, native_source_context, sessions, tracking
from .session_liveness import NATIVE_BINDING_PROOF, session_liveness, session_process_owns_hold
from .worktree_status_daemon import _is_safe_identity_token
from .tracking_session_activations import source_end_authoritative


def observe(
    worktree_id: str, *, loader: Callable[[], tracking.WorktreeRecord | None],
    native_session_id: str | None = None,
) -> dict:
    """Read current hosting and an exact requested conversation's lifecycle."""
    if not isinstance(worktree_id, str) or not _is_safe_identity_token(worktree_id):
        raise ValueError("Invalid exact hosting worktree identity")
    if native_session_id is not None and (
        not isinstance(native_session_id, str) or str(UUID(native_session_id)) != native_session_id
    ):
        raise ValueError("Invalid exact lifecycle native UUID")
    result = {
        "schema": "native-hosting-evidence", "version": 1,
        "worktree_id": worktree_id, "confirmed": False,
        "automatic_eligible": False, "liveness": "unknown",
        "binding_proof": None,
        "recovery_binding_capability": None, "worktree_incarnation": None,
        "activation_ordinal": 0, "source_identity": None, "lifecycle_qualified": False,
    }
    record = loader()
    if record is None or record.worktree_id != worktree_id:
        return {**result, "reason": "missing-or-mismatched-worktree"}
    before = dataclasses.asdict(record)
    observed = _hosting(record, dict(result))
    requested = native_session_id or record.resolved_head_session
    entry = record.session_entry(requested) if requested else None
    lifecycle = {
        "schema": "native-activation-evidence", "version": 1,
        "native_session_id": requested, "owner_revision": record.lifecycle_revision,
        "activations": [
            {
                "ordinal": item.ordinal, "source_identity": item.source_identity,
                "ended_at": item.ended_at, "end_reason": item.end_reason,
                "end_source": item.end_source,
                "source_ambiguous": item.source_ambiguous or (
                    item.source_identity is not None
                    and not source_end_authoritative(entry, item.source_identity)
                ),
            }
            for item in entry.activations
        ] if entry is not None and len(entry.activations) <= tracking._MAX_SESSION_ACTIVATIONS else [],
    }
    current = loader()
    if current is None or dataclasses.asdict(current) != before:
        return {**result, "reason": "owner-changed"}
    return {
        **observed, "lifecycle_capability": "source-bound-v1", "lifecycle": lifecycle,
    }


def _hosting(record: tracking.WorktreeRecord, result: dict) -> dict:
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
    if len(entry.activations) > tracking._MAX_SESSION_ACTIVATIONS:
        return {**result, "reason": "native-activation-history-over-budget"}
    latest = max(entry.activations, key=lambda item: item.ordinal, default=None)
    if entry.ended_at is not None or latest is not None and latest.ended_at is not None:
        return {**result, "reason": "ended-native-activation"}
    if (
        record.execution_leg_opaque or record.session_backend_opaque
        or tracking.derive_execution_leg(record) is not None
    ):
        return {**result, "reason": "non-native-execution"}
    if not sessions.session_has_conversation_data(native_id):
        return {**result, "reason": "missing-native-conversation"}
    result.update(native_session_id=native_id, head_revision=record.head_revision)
    nonce = record.creation_nonce
    if (
        isinstance(nonce, str) and len(nonce) == 16
        and all(character in "0123456789abcdef" for character in nonce)
        and type(record.head_revision) is int and record.head_revision >= 0
    ):
        result.update(
            recovery_binding_capability="worktree-head-v1", worktree_incarnation=nonce,
        )
    state = session_liveness(native_id)
    binding = None
    if state == "live":
        binding = sessions.mux_binding_for_session(
            native_id, expected_session_name=sessions.mux_session_name(record.worktree_id),
            include_incarnation=True,
        )
        if (
            not binding or binding.get("session_name") != sessions.mux_session_name(record.worktree_id)
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
    if binding:
        result.update(
            mux_session=binding["session_name"], pane_id=binding["pane_id"],
            session_incarnation=binding["session_incarnation"],
            native_incarnation=f"{binding['copilot_pid']}:{binding['copilot_start_time']}",
            owner_revision=record.lifecycle_revision,
            binding_proof=NATIVE_BINDING_PROOF,
        )
        if latest is not None and latest.source_identity is not None:
            try:
                token = native_source_context.identity(
                    native_id, binding["copilot_pid"], binding["copilot_start_time"],
                    native_source_context.epoch(),
                )
            except (OSError, ValueError, IndexError):
                token = None
            if (
                not source_end_authoritative(entry, latest.source_identity)
                or latest.source_ambiguous or token != latest.source_identity
            ) or sum(
                item.source_identity == token for item in entry.activations
            ) != 1:
                return {**result, "reason": "native-activation-source-unconfirmed"}
            result.update(
                activation_ordinal=latest.ordinal, source_identity=token,
                lifecycle_qualified=True,
            )
    return {
        **result, "liveness": state, "confirmed": state == "live",
        "reason": "native-head-live" if state == "live" else f"native-head-{state}",
    }
