"""Pure relocation admission inspection, separate from mutation preflight."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import yaml

from . import config as cfg, tracking


def inspect(worktree_id: str, destination: str, config) -> dict:
    """Refuse unqualified passive evidence without contacting an action owner."""
    from . import checkout_relocation as relocation

    relocation._require_native_support()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
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
    record = tracking.parse_record_data(data, path=ledger)
    if record.worktree_id != worktree_id or record.repo != config.repo_name:
        raise ValueError("relocation record does not match the receiving ledger identity/project")
    relocation._ordinary_shape(record, ledger)
    if record.owner_ref and record.creation_nonce:
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
