"""Explicit STATUS FIRST pickup, proven from the resumed native CLI's ancestry."""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
from uuid import UUID

from . import bridge_acquisition_launch, launch_seed_state, native_hosting_evidence
from . import native_restart_check, native_restart_launch, native_restart_receipt
from . import native_restart_claims
from . import native_source_context, output, tracking
from .worktree_status_daemon import _is_safe_identity_token


def _proof(path: Path, receipt: dict, witness: object) -> dict:
    request = receipt["request"]
    expected = request["expected"]
    if native_source_context.epoch() != expected["target_epoch"]:
        raise ValueError("Restart acknowledgement target epoch changed")
    record = tracking.load_record(path)
    if record.worktree_id != expected["worktree_id"]:
        raise ValueError("Restart acknowledgement worktree changed")
    before = dataclasses.asdict(record)
    if reason := native_restart_check.policy_refusal(
        record, {**expected, "owner_revision": record.lifecycle_revision},
    ):
        raise ValueError(reason)
    if Path(record.worktree_path).resolve(strict=True) != Path(record.worktree_path):
        raise ValueError("worktree-path-indirect")
    if not native_restart_claims.original_session_only(record, expected["native_session_id"]):
        raise ValueError("Restart acknowledgement claim policy is unqualified")
    if launch_seed_state.peek(path, read_only=True) is not None:
        raise ValueError("pending-seed")
    if not native_restart_check.original_activation_confirmed(record, expected):
        raise ValueError("Restart original activation history is unconfirmed")
    observed = native_hosting_evidence.observe(
        record.worktree_id, loader=lambda: tracking.load_record(path),
        native_session_id=expected["native_session_id"],
    )
    tokens = {item[0] for item in native_source_context.candidates(
        expected["native_session_id"], witness,
    )}
    if (
        observed["confirmed"] is not True or observed["lifecycle_qualified"] is not True
        or observed.get("native_session_id") != expected["native_session_id"]
        or observed.get("source_identity") not in tokens
    ):
        raise ValueError("Restart acknowledgement is not from the resumed native process")
    acknowledgement = {
        **{field: observed[field] for field in (
            "native_session_id", "source_identity", "native_incarnation",
            "session_incarnation", "head_revision", "owner_revision", "activation_ordinal",
        )},
        "target_epoch": expected["target_epoch"], "status_reconciled": True,
    }
    native_restart_receipt.validate_acknowledgement(request, acknowledgement)
    if (
        native_source_context.epoch() != expected["target_epoch"]
        or dataclasses.asdict(tracking.load_record(path)) != before
    ):
        raise ValueError("Restart acknowledgement owner changed")
    return acknowledgement


def cmd_acknowledge(args: argparse.Namespace) -> int:
    """Acknowledge without launching, clearing receipts or enabling automation."""
    from . import handoff_cli as cli

    try:
        request_id = getattr(args, "restart_ack", None)
        if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
            raise ValueError("Restart acknowledgement requires a canonical request UUID")
        worktree_id = getattr(args, "worktree_id", None)
        if not isinstance(worktree_id, str) or not _is_safe_identity_token(worktree_id):
            raise ValueError("Restart acknowledgement requires an explicit exact worktree id")
        if (
            getattr(args, "status_reconciled", False) is not True
            or any(getattr(args, field, None) for field in (
                *native_restart_launch._CONFLICTS, "restart_request", "restart_receipt", "dry_run",
            ))
        ):
            raise ValueError("Restart acknowledgement requires reconciled status and no other intent")
        witness = native_source_context.capture()
        if witness.get("qualified") is not True:
            raise ValueError("Restart acknowledgement native source is unqualified")
        path = cli.cfg.tracking_dir() / f"{worktree_id}.yaml"
        config = cli.cfg.load_config()
        record = tracking.load_record(path)
        repo = cli._repo_for_record(config, record)
        if repo is None or repo is not config.default_repo:
            raise ValueError("Restart target does not match the selected repository")
        if getattr(getattr(config, "profile_assignment", None), "armed", False):
            raise ValueError("profile-policy-unqualified")
        with bridge_acquisition_launch.launch_fence(repo, path):
            with tracking._RecordLock(path, require_sidecar=True):
                receipt = native_restart_receipt.read(path)
                if receipt is None or receipt["request"]["request_id"] != request_id:
                    raise ValueError("Restart acknowledgement has no matching reserved request")
                acknowledgement = _proof(path, receipt, witness)
                if receipt["version"] == 2:
                    if receipt["acknowledgement"] != acknowledgement:
                        raise ValueError("Restart acknowledgement execution changed")
                else:
                    receipt = {
                        **receipt, "version": 2, "state": "seed-acknowledged",
                        "seed_acknowledged": True, "acknowledgement": acknowledgement,
                    }
                    native_restart_receipt.write(path, receipt, reserve=False)
        output._json_output({**receipt, "ok": True})
        return 0
    except (OSError, ValueError, TimeoutError) as exc:
        return output._json_error(f"Restart acknowledgement refused: {exc}", exit_code=3)
