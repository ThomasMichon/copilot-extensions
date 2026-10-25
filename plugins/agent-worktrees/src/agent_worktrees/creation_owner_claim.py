"""Reciprocal owner-ledger claims for prospective worktree creation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

from . import config as cfg, obligations, tracking
from .creation_identity import require_same_machine_identity
from .execution_spaces import (
    ExecutionSpaceError, current_execution_context, project_record_config,
    require_owner_identity, require_record_mutation,
)
from .worktree_machine_identity import WorktreeMachineIdentity


def journal_owner_claim(
    config: cfg.Config,
    worktree_id: str,
    owner_ref: str,
    *,
    resolve_owner: Callable[[str, cfg.Config], tuple[Path | None, str, str | None]],
    owner_locked: bool = False,
    machine_identity: WorktreeMachineIdentity | None = None,
) -> bool:
    """Write the forward claim that the child's later settlement matches.

    Cross-machine or unavailable owners return false. Existing best-effort
    reporting is retained; the creation coordinator requires a successful
    claim before carving an owned worktree. Repeated writes deduplicate by ref.
    """
    try:
        owner_path, _owner_wt, _err = resolve_owner(owner_ref, config)
        if owner_path is None or not owner_path.exists():
            return False
        require_same_machine_identity(config, machine_identity)
        child_ref = tracking.format_claim_ref(
            machine_identity.execution_machine if machine_identity is not None else config.machine,
            config.repo_name,
            worktree_id,
        )

        def write_claim() -> None:
            owner_rec = tracking.load_record(owner_path)
            receiving_config = project_record_config(owner_rec)
            require_record_mutation(owner_rec, receiving_config)
            if not require_owner_identity(
                current_execution_context(config).execution_machine, receiving_config,
            ):
                raise ExecutionSpaceError("caller is not local to the parent receiving execution space")
            tracking.add_resource_claim(
                owner_rec,
                tracking.ResourceClaim(
                    kind="worktree",
                    ref=child_ref,
                    created_at=tracking._now_iso(),
                    state=obligations.ACTIVE,
                ),
                save=False,
            )
            tracking.save_record(owner_rec, owner_path)

        if owner_locked:
            write_claim()
        else:
            with tracking._RecordLock(owner_path, require_sidecar=True):
                write_claim()
        print(f"Journaled this worktree as an obligation on {owner_ref}.", file=sys.stderr)
        return True
    except Exception as exc:  # Retain the existing best-effort reporting contract.
        print(f"owner-claim journaling failed (non-fatal): {exc}", file=sys.stderr)
        return False
