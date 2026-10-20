"""Explicit current-original hosting inspection, never adoption authority."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable

from . import config as cfg
from . import launch_seed_state, native_hosting_evidence, native_restart_check
from . import native_restart_claims, native_restart_launch, native_restart_receipt
from . import native_source_context, tracking


def inspect(
    request: object, *, loader: Callable[[], tracking.WorktreeRecord | None],
) -> dict:
    """Bind fresh hosting to a frozen original without writes, seed or launch."""
    request = native_restart_launch.validate_request(request)
    expected = request["expected"]
    result = {
        "schema": "native-restart-survivor", "version": 1, "request": request,
        "survivor_confirmed": False, "launch_authorized": False,
        "reuse_authorized": False, "seed_acknowledged": False,
        "restored": False, "automatic_eligible": False,
    }
    if native_source_context.epoch() != expected["target_epoch"]:
        return {**result, "reason": "foreign-environment"}
    record = loader()
    if record is None or record.worktree_id != expected["worktree_id"]:
        return {**result, "reason": "missing-or-mismatched-worktree"}
    before = dataclasses.asdict(record)
    if reason := native_restart_check.policy_refusal(
        record, {**expected, "owner_revision": record.lifecycle_revision},
    ):
        return {**result, "reason": reason}
    if record.lifecycle_revision <= expected["owner_revision"]:
        return {**result, "reason": "fresh-native-activation-unconfirmed"}
    if not native_restart_claims.original_session_only(record, expected["native_session_id"]):
        return {**result, "reason": "claim-policy-unqualified"}
    path = Path(record.worktree_path)
    if not path.is_dir() or path.resolve(strict=True) != path:
        return {**result, "reason": "worktree-path-unconfirmed"}
    record_path = cfg.tracking_dir() / f"{record.worktree_id}.yaml"
    if launch_seed_state.peek(record_path, read_only=True) is not None:
        return {**result, "reason": "pending-seed"}
    if not native_restart_check.original_activation_confirmed(record, expected):
        return {**result, "reason": "original-activation-unconfirmed"}
    observed = native_hosting_evidence.observe(
        record.worktree_id, loader=loader, native_session_id=expected["native_session_id"],
    )
    if observed["confirmed"] is not True or observed["lifecycle_qualified"] is not True:
        return {**result, "reason": observed["reason"]}
    hosting = {
        **{field: observed[field] for field in (
            "native_session_id", "source_identity", "native_incarnation",
            "session_incarnation", "head_revision", "owner_revision", "activation_ordinal",
        )},
        "target_epoch": expected["target_epoch"],
    }
    native_restart_receipt.validate_execution_binding(request, hosting)
    current = loader()
    if (
        current is None or dataclasses.asdict(current) != before
        or native_source_context.epoch() != expected["target_epoch"]
    ):
        return {**result, "reason": "owner-changed"}
    return {
        **result, "survivor_confirmed": True, "hosting": hosting,
        "reason": "original-native-survivor-confirmed",
    }
