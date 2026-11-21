"""Retain unverified provider receipts without claiming or recreating a PR."""

from __future__ import annotations

from typing import Any

from . import config as cfg, pr_publication_state, pr_publish, record_machine_identity, tracking
from .providers.base import CreationRefused, PRProvider, PRScope, PullResult
from .providers.github_pull_source import CreatedSourceMismatch
from .receiving_allocation import serialized_receipt


def pending(result: dict[str, Any], target: tracking.PRRecord) -> bool:
    receipt = target.creation
    if receipt.get("provider_outcome") not in ("publishing", "unknown"):
        return False
    number = receipt.get("provider_number", "")
    detail = "PR creation outcome is unverified; reconcile the retained attempt before retrying."
    result.update(
        success=False, state="unknown", error=detail,
        pr_opened=bool(number), number=int(number) if number.isascii() and number.isdecimal() else None,
        url=receipt.get("provider_url", ""), publication_state="unknown",
        pr_identity_verified=False, claimed=False,
        pr_open_error=detail,
    )
    return True


def require_owner(fresh: tracking.WorktreeRecord, record: tracking.WorktreeRecord) -> None:
    path = record.yaml_path
    if (
        record_machine_identity.subject_fence(fresh, path) != record_machine_identity.subject_fence(record, path)
        or fresh.creation_nonce != record.creation_nonce
        or serialized_receipt(fresh) != serialized_receipt(record)
        or fresh.status in ("finalizing", "finalized", "archived")
    ):
        raise ValueError("Tracking owner changed before retaining the provider receipt.")


def _update(
    config: cfg.Config, record: tracking.WorktreeRecord, target: tracking.PRRecord,
    receipt: dict[str, str],
) -> None:
    path = record.yaml_path
    with pr_publish.metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(
        path, require_sidecar=True,
    ):
        fresh = tracking.load_record(path)
        require_owner(fresh, record)
        current = pr_publish._publication_pr(fresh, target)
        if current is None or not pr_publication_state.matches(current, target):
            raise ValueError("PR authority changed before retaining the provider receipt.")
        current.creation.update(receipt)
        current.pr_revision += 1
        tracking.save_record(fresh)
        pr_publication_state.copy_pr(target, current)


def reserve(config: cfg.Config, record: tracking.WorktreeRecord | None, target: tracking.PRRecord) -> None:
    if record is not None:
        _update(config, record, target, {"provider_outcome": "publishing"})


def create(
    result: dict[str, Any], config: cfg.Config, record: tracking.WorktreeRecord | None,
    target: tracking.PRRecord, prcfg: cfg.PRConfig, scope: PRScope,
) -> tuple[PRProvider, str | None, PullResult] | None:
    from . import providers

    try:
        if record is not None:
            pr_publication_state.require_current(record, target)
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(scope.repo, prcfg)
        reserve(config, record, target)
    except (providers.ProviderError, OSError, ValueError, TimeoutError) as exc:
        result.update(success=False, error=str(exc), pr_open_error=str(exc), pr_opened=False, draft=False)
        return None
    try:
        return provider, token, provider.create_pull(scope, token=token)
    except CreationRefused as exc:
        try:
            if record is not None:
                _update(config, record, target, {"provider_outcome": "refused"})
        except (OSError, ValueError, TimeoutError) as persistence_error:
            retain(result, config, record, target, persistence_error)
        else:
            result.update(
                success=False, error=str(exc), pr_open_error=str(exc), pr_opened=False,
                draft=False, publication_state="not-started",
            )
        return None
    except (providers.ProviderError, OSError, ValueError, TypeError, AttributeError) as exc:
        if not retain(result, config, record, target, exc):
            result.update(pr_open_error=str(exc), pr_opened=False, draft=False)
        return None


def retain(
    result: dict[str, Any], config: cfg.Config, record: tracking.WorktreeRecord | None,
    target: tracking.PRRecord, error: Exception, *, pull: PullResult | None = None,
) -> bool:
    if isinstance(error, CreatedSourceMismatch):
        pull = error.pull
    elif record is None and pull is None:
        return False
    receipt = {"provider_outcome": "unknown"}
    if pull is not None:
        receipt.update(provider_number=str(pull.number) if pull.number is not None else "", provider_url=pull.url)
    if record is None:
        target.creation.update(receipt)
    else:
        try:
            _update(config, record, target, receipt)
        except (OSError, ValueError, TimeoutError) as exc:
            result["pr_receipt_tracking_error"] = str(exc)
    detail = str(error) + " PR creation outcome is unknown; reconcile the retained attempt before retrying."
    result.update(
        success=False, state="unknown", error=detail,
        pr_opened=pull is not None, number=pull.number if pull is not None else None,
        url=pull.url if pull is not None else "",
        publication_state="unknown", pr_identity_verified=False, claimed=False,
        pr_open_error=detail,
    )
    return True
