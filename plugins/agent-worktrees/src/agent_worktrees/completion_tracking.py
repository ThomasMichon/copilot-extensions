"""Status-only completion, including fenced creation of a missing owner."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
import secrets

from . import config as cfg, git_ops, source_path_admission, tracking


def update(path: Path, worktree_id: str, *, title: str | None, title_only: bool) -> None:
    missing = not path.exists()
    if title_only and not title and not missing:
        return
    admission = source_path_admission.guard(Path.cwd(), timeout=0) if missing else nullcontext()
    with admission, tracking._RecordLock(path, require_sidecar=True):
        if path.exists():
            record = tracking.load_record(path)
        else:
            if not missing:
                raise ValueError("Completion owner disappeared; requalify before creating it.")
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            record = tracking.WorktreeRecord(
                worktree_id=worktree_id, branch=git_ops.get_current_branch("."),
                worktree_path=str(Path.cwd()), repo=cfg.project_name(), machine="", platform="",
                started_at=now, last_resumed_at=now, resume_count=0,
                title=title, title_asserted=title is not None, status="active",
                completed_at=None, creation_nonce=secrets.token_hex(8),
            )
        if title:
            tracking.set_disposition(record, title=title, save=False, tracking_path=path.parent)
        if not title_only:
            tracking.update_status(record, "complete", save=False)
        tracking.save_record(record, path)
