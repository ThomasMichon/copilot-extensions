"""Retain unverified provider receipts without claiming or recreating a PR."""

from __future__ import annotations

from typing import Any

from . import config as cfg, pr_publication_state, pr_publish, record_machine_identity, tracking
from .providers.github_pull_source import CreatedSourceMismatch
from .receiving_allocation import serialized_receipt


def pending(result: dict[str, Any], target: tracking.PRRecord) -> bool:
    receipt = target.creation
    if receipt.get("provider_outcome") != "unknown":
        return False
    number = receipt.get("provider_number", "")
    detail = "Created PR identity is unverified; reconcile the retained receipt before retrying."
    result.update(
        success=False, state="unknown", error=detail,
        pr_opened=True, number=int(number) if number.isascii() and number.isdecimal() else None,
        url=receipt.get("provider_url", ""), publication_state="unknown",
        pr_identity_verified=False, claimed=False,
        pr_open_error=detail,
    )
    return True


def retain(
    result: dict[str, Any], config: cfg.Config, record: tracking.WorktreeRecord | None,
    target: tracking.PRRecord, error: Exception,
) -> bool:
    if not isinstance(error, CreatedSourceMismatch):
        return False
    receipt = {
        "provider_outcome": "unknown", "provider_number": str(error.pull.number),
        "provider_url": error.pull.url,
    }
    if record is None:
        target.creation.update(receipt)
    if record is not None:
        path = record.yaml_path
        try:
            with pr_publish.metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(
                path, require_sidecar=True,
            ):
                fresh = tracking.load_record(path)
                current = pr_publish._publication_pr(fresh, target)
                if (
                    record_machine_identity.subject_fence(fresh, path) != record_machine_identity.subject_fence(record, path)
                    or fresh.creation_nonce != record.creation_nonce
                    or serialized_receipt(fresh) != serialized_receipt(record)
                    or fresh.status in ("finalizing", "finalized", "archived")
                    or current is None or not pr_publication_state.matches(current, target)
                ):
                    raise ValueError("Tracking owner or PR authority changed before retaining the unverified provider receipt.")
                current.creation.update(receipt)
                current.pr_revision += 1
                tracking.save_record(fresh)
                pr_publication_state.copy_pr(target, current)
        except (OSError, ValueError, TimeoutError) as exc:
            result["pr_receipt_tracking_error"] = str(exc)
    result.update(
        success=False, state="unknown", error=str(error),
        pr_opened=True, number=error.pull.number, url=error.pull.url,
        publication_state="unknown", pr_identity_verified=False, claimed=False,
        pr_open_error=str(error),
    )
    return True
