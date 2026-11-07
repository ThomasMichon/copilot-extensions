"""Best-effort native writer notifications; never a publication receipt."""

from __future__ import annotations

from pathlib import Path


def notify_worktree_changed(worktree_path: str | None, *, lock_path: Path | None = None) -> bool:
    """Wake an already-serving qualified owner without starting or polling it."""
    try:
        if worktree_path is not None and (
            not isinstance(worktree_path, str) or "\x00" in worktree_path
            or len(worktree_path.encode("utf-8")) > 4096
            or not Path(worktree_path).is_absolute()
        ):
            return False
        from . import hook_ipc

        return hook_ipc.send_best_effort(
            hook_ipc.WORKTREE_CHANGE_KIND, {"cwd": worktree_path},
            lock_path=lock_path, timeout=0.1,
            required_capability=hook_ipc.WORKTREE_CHANGE_CAPABILITY, send_only=True,
        )
    except Exception:
        return False
