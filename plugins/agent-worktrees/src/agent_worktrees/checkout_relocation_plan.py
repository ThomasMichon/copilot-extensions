"""Pure relocation admission inspection, separate from mutation preflight."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

import yaml

from . import config as cfg, tracking, launch_seed_state, launch_admission, worktree_lifecycle
from . import checkout_relocation_state as state


def read_snapshot(ledger: Path, *, max_bytes: int | None = 64 * 1024) -> bytes:
    """Read one identity-stable ledger, bounded by default for admission."""
    before = ledger.lstat()
    if (
        not stat.S_ISREG(before.st_mode) or ledger.is_symlink()
        or getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or max_bytes is not None and before.st_size > max_bytes
    ):
        raise ValueError("relocation ledger is redirected or oversized")
    with ledger.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    named = ledger.lstat()
    if (
        max_bytes is not None and len(raw) > max_bytes or not os.path.samestat(before, opened)
        or not os.path.samestat(opened, after) or not os.path.samestat(opened, named)
        or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        or (opened.st_size, opened.st_mtime_ns) != (named.st_size, named.st_mtime_ns)
    ):
        raise ValueError("relocation record changed while reading its planning snapshot")
    return raw


def read_record(ledger: Path) -> tracking.WorktreeRecord:
    data = yaml.safe_load(read_snapshot(ledger))
    if not isinstance(data, dict):
        raise ValueError("relocation ledger has an unsupported schema")
    return tracking.parse_record_data(data, path=ledger)


def inspect(worktree_id: str, destination: str, config: cfg.Config) -> dict[str, Any]:
    """Refuse unqualified passive evidence without contacting an action owner."""
    from . import checkout_relocation as relocation

    relocation._require_native_support()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    record = read_record(ledger)
    from . import record_reporting
    record_reporting.apply_context(record, record_reporting.current_context(config.repo_name))
    if record.worktree_id != worktree_id or record.repo != config.repo_name:
        raise ValueError("relocation record does not match the receiving ledger identity/project")
    relocation._ordinary_shape(record, ledger, allow_receiving=True)
    if record.receiving_allocation_present:
        from .receiving_relocation import inspect as inspect_receiving

        inspect_receiving(record, config)
    elif not record.creation_nonce:
        from .checkout_legacy_identity import inspect_origin

        inspect_origin(record, ledger, lock_snapshot=False)
    elif record.owner_ref:
        from .receiving_allocation import has_retained_receiving_operation

        if has_retained_receiving_operation(record, ledger.parent, lock_snapshot=False):
            raise ValueError(
                "read-only proof cannot recover missing receiving allocation metadata; inspect through its owner"
            )
    owner_directory = launch_seed_state.state_path(ledger).parent
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


def check_live(worktree_id: str, destination: str, config: cfg.Config) -> dict[str, Any]:
    """Explicit action-time admission; never a cached permission to apply."""
    from . import checkout_relocation as relocation

    relocation._require_native_support()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    _record, _proof, receiving_guard = relocation._receiving_admission(ledger, config)
    finalize_lock = cast(Callable[..., AbstractContextManager[Any]], getattr(relocation, "FinalizeLock"))
    with finalize_lock(
        Path(config.default_repo.worktree_root) / ".finalize.lock", timeout=3,
        on_stale=lambda message: print(message, file=sys.stderr),
    ), receiving_guard, worktree_lifecycle.guard(ledger, relocation=True), (
        launch_admission.guard(ledger)
    ), state.owner(ledger):
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
