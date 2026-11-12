"""Pure relocation admission inspection, separate from mutation preflight."""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config as cfg, tracking


def read_record(ledger: Path):
    """Read one bounded, identity-stable ledger without recovery or owner calls."""
    before = ledger.lstat()
    if (
        not stat.S_ISREG(before.st_mode) or ledger.is_symlink()
        or getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or before.st_size > 64 * 1024
    ):
        raise ValueError("relocation ledger is redirected or oversized")
    with ledger.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(64 * 1024 + 1)
        after = os.fstat(stream.fileno())
    named = ledger.lstat()
    if (
        len(raw) > 64 * 1024 or not os.path.samestat(before, opened)
        or not os.path.samestat(opened, after) or not os.path.samestat(opened, named)
        or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        or (opened.st_size, opened.st_mtime_ns) != (named.st_size, named.st_mtime_ns)
    ):
        raise ValueError("relocation record changed while reading its planning snapshot")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("relocation ledger has an unsupported schema")
    return tracking.parse_record_data(data, path=ledger)


def inspect(worktree_id: str, destination: str, config) -> dict:
    """Refuse unqualified passive evidence without contacting an action owner."""
    from . import checkout_relocation as relocation

    relocation._require_native_support()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    record = read_record(ledger)
    if record.worktree_id != worktree_id or record.repo != config.repo_name:
        raise ValueError("relocation record does not match the receiving ledger identity/project")
    relocation._ordinary_shape(record, ledger, allow_receiving=True)
    if record.receiving_allocation_present:
        from .receiving_relocation import inspect as inspect_receiving

        inspect_receiving(record, config)
    elif record.owner_ref and record.creation_nonce:
        from .receiving_allocation import has_retained_receiving_operation

        if has_retained_receiving_operation(record, ledger.parent):
            raise ValueError(
                "read-only proof cannot recover missing receiving allocation metadata; inspect through its owner"
            )
    owner_directory = relocation.launch_seed_state.state_path(ledger).parent
    for name in ("lifecycle.lock", "launch-admission.lock"):
        if os.path.lexists(owner_directory / name):
            raise ValueError("relocation planning cannot confirm idle lifecycle/launch ownership")
    target = Path(destination).expanduser()
    if not target.is_absolute() or target.name != worktree_id:
        raise ValueError("destination must be an absolute managed worktree ID directory")
    # No native passive coverage contract is available. Even an empty registered
    # session list or readable CWD inventory cannot establish complete absence.
    raise ValueError(
        "relocation planning requires qualified passive native owner evidence; "
        "mux, Copilot and process/CWD coverage remain unconfirmed; strict action probes were not run"
    )


def check_live(worktree_id: str, destination: str, config) -> dict:
    """Explicit action-time admission; never a cached permission to apply."""
    from . import checkout_relocation as relocation

    relocation._require_native_support()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    _record, _proof, receiving_guard = relocation._receiving_admission(ledger, config)
    with relocation.FinalizeLock(
        Path(config.default_repo.worktree_root) / ".finalize.lock", timeout=3,
        on_stale=lambda message: print(message, file=sys.stderr),
    ), receiving_guard, relocation.worktree_lifecycle.guard(ledger, relocation=True), (
        relocation.launch_admission.guard(ledger)
    ), relocation.state.owner(ledger):
        report = relocation._action_plan(worktree_id, destination, config)
        if report["phase"] != "planned":
            raise ValueError("live admission requires an unstarted move; inspect the original operation")
        return {
            **report,
            "live_admission": {
                "schema": "agent-worktrees.checkout-live-admission", "version": 1,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "activity_refresh": True, "apply_rechecks": True,
            },
        }
