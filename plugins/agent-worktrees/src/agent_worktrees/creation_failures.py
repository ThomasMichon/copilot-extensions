"""Retain an allocated worktree's identity when launch preparation fails."""

from __future__ import annotations


class LaunchSeedStagingFailure(RuntimeError):
    """Creation succeeded, but launch preparation did not produce a confirmed result."""

    def __init__(self, record, seed_id: str, cause: Exception):
        from .tracking_write import AmbiguousWriteOutcome

        self.worktree = {
            "id": record.worktree_id,
            "path": record.worktree_path,
            "branch": record.branch,
            "repo": record.repo,
        }
        self.seed_id = seed_id
        self.ambiguous = isinstance(cause, AmbiguousWriteOutcome)
        super().__init__(
            f"Worktree {record.worktree_id} was created at {record.worktree_path}, "
            f"but launch preparation did not confirm: {cause}. "
            f"Do not retry --new; recover this existing --worktree-id {record.worktree_id}."
        )

    def payload(self) -> dict:
        return {
            "error": str(self),
            "created": True,
            "worktree": self.worktree,
            "launch_seed": {
                "seed_id": self.seed_id,
                "status": "unknown" if self.ambiguous else "unconfirmed",
            },
            "recovery": {
                "action": "inspect-and-retry-existing",
                "worktree_id": self.worktree["id"],
                "repeat_new": False,
            },
        }

    def emit(self, *, json_out: bool) -> int:
        from . import output

        if json_out:
            output._json_output(self.payload())
        else:
            output.err(str(self))
        return 3
