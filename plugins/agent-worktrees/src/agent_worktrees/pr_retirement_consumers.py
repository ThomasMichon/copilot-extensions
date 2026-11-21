"""Receipt qualification before exact cleanup and terminal-conclusion locks."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from . import config as cfg, pr_creation_reconcile, pr_transport_fence, tracking
from . import worktree_lifecycle

if TYPE_CHECKING:
    from .managed_worktree_guard import GuardedRemoval

T = TypeVar("T")


def conclusion_status(record: tracking.WorktreeRecord) -> tracking.WorktreeStatus:
    """Keep terminal receipt owners eligible for later missing-source qualification."""
    if record.status in ("finalized", "archived") and any(
        pr_creation_reconcile.retired(pr) for pr in record.prs
    ):
        return record.status
    return "complete"


def _run(
    records: list[tuple[tracking.WorktreeRecord, Any]], operation: Callable[[], T],
) -> T:
    selected = [
        (record, repo) for record, repo in records
        if any(pr_creation_reconcile.requires_qualification(pr) for pr in record.prs)
    ]
    if not selected:
        return operation()
    owners = sorted(records, key=lambda item: str(item[0].yaml_path.resolve()))
    selected.sort(key=lambda item: str(item[0].yaml_path.resolve()))
    token = pr_transport_fence.current.set(pr_transport_fence.Qualification())
    try:
        try:
            with ExitStack() as lifetime:
                for record, _repo in owners:
                    lifetime.enter_context(worktree_lifecycle.guard(record.yaml_path))
                for record, repo in selected:
                    error = pr_creation_reconcile.finalization_error(record, repo)
                    if error:
                        raise ValueError(error)
        except (OSError, ValueError, TimeoutError) as exc:
            raise pr_transport_fence.QualificationRefused(str(exc)) from exc
        with pr_transport_fence.qualification_lease(
            pr_transport_fence.cleanup_guards([(record, repo) for record, repo in selected]),
        ):
            return operation()
    finally:
        pr_transport_fence.current.reset(token)


def cleanup_one(
    wt_id: str, *, force: bool = False, include_unused: bool = False,
    include_conversations: bool = False, reconcile_prs: bool = False,
    automatic: bool = False,
) -> dict[str, Any]:
    """Qualify one exact owner's retirement before native cleanup."""
    from . import __main__ as core, worktree_identity

    config = cfg.load_config()
    wt_id = worktree_identity._resolve_worktree_id(wt_id)
    path = cfg.tracking_dir() / f"{wt_id}.yaml"
    records = [(tracking.load_record(path), config.default_repo)] if path.exists() else []
    try:
        return _run(records, lambda: core._reap_one(
            wt_id, force=force, include_unused=include_unused,
            include_conversations=include_conversations, reconcile_prs=reconcile_prs,
            automatic=automatic, _config=config,
        ))
    except pr_transport_fence.QualificationRefused as exc:
        return {
            "ok": False, "removed": False, "skipped": True, "worktree_id": wt_id,
            "bucket": "open-pr", "reason": f"Retirement qualification refused: {exc}",
        }


def managed_removal(
    wt_id: str, yaml_path: Path, config: cfg.Config, *, force: bool,
    remove_fn: Callable[..., tuple[bool, list[str]]], tracking_path: Path,
) -> GuardedRemoval:
    """Keep managed removal inside its qualified owner's lifecycle fence."""
    from . import managed_worktree_guard as managed

    record = tracking.load_record(yaml_path)
    repo = managed._resolve_repo(config, record)
    records = [(record, repo)] if repo is not None else []
    try:
        return _run(records, lambda: managed._perform(
            wt_id, yaml_path, config, force=force, remove_fn=remove_fn,
            tracking_path=tracking_path,
        ))
    except pr_transport_fence.QualificationRefused as exc:
        return managed.GuardedRemoval(
            ok=False, message=f"Retirement qualification refused for {wt_id}: {exc}",
        )


def _conclusion_records(record_path: Path, repo: Any) -> list[tuple[tracking.WorktreeRecord, Any]]:
    from . import terminal_conclusion as terminal

    record = tracking.load_record(record_path)
    records = [(record, repo)]
    if sibling := terminal._resolve_pair_sibling(record):
        peer, _path = sibling
        peer_repo = (
            cfg.load_project_config(peer.repo).default_repo
            if any(pr_creation_reconcile.requires_qualification(pr) for pr in peer.prs) else None
        )
        records.append((peer, peer_repo))
    return records


@contextmanager
def conclusion_guard(record_path: Path, repo: Any) -> Iterator[None]:
    """Caller owns the native repository lock before taking lifecycle exclusion."""
    records = _conclusion_records(record_path, repo)
    if not any(pr_creation_reconcile.requires_qualification(pr) for record, _repo in records for pr in record.prs):
        yield
        return
    paths = sorted({record.yaml_path.resolve() for record, _repo in records})
    with ExitStack() as lifetime:
        for path in paths:
            lifetime.enter_context(worktree_lifecycle.guard(path))
        for path in paths:
            lifetime.enter_context(tracking._RecordLock(path, timeout=3, require_sidecar=True))
        fresh = _conclusion_records(record_path, repo)
        if {record.yaml_path.resolve() for record, _repo in fresh} != set(paths):
            raise ValueError("Terminal conclusion owners changed before mutation.")
        with pr_transport_fence.qualification_lease(
            pr_transport_fence.cleanup_guards([(record, owner_repo) for record, owner_repo in fresh]),
        ):
            yield


def conclude(
    record_path: Path, repo: Any, *, session_id: str | None, owner: str,
    policy: str, reservation_key: str | None = None, creation_nonce: str | None = None,
) -> dict[str, Any]:
    """Qualify affected local receipts before native terminal conclusion."""
    from . import terminal_conclusion as terminal
    from .terminal_ownership import CONCLUSION_POLICIES
    if policy not in CONCLUSION_POLICIES:
        raise ValueError(f"unsupported terminal conclusion policy: {policy!r}")
    records = _conclusion_records(record_path, repo)
    try:
        return _run(records, lambda: terminal._conclude_disposable_worktree(
            record_path, repo, session_id=session_id, owner=owner, policy=policy,
            reservation_key=reservation_key, creation_nonce=creation_nonce,
        ))
    except pr_transport_fence.QualificationRefused as exc:
        return {
            "worktree_id": records[0][0].worktree_id, "policy": policy, "action": "skipped",
            "reason": "retirement-qualification-required", "detail": str(exc),
            "session": {"action": "preserved", "reason": "retirement-qualification-required"},
            "managed_gc_eligible": False,
        }
