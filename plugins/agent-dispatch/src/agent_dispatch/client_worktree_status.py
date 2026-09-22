"""Small worktree-status relay client surface, split out for module-size caps."""

from __future__ import annotations


class WorktreeStatusClientMixin:
    """Read-side client methods for the coordinator's worktree relay."""

    def worktree_status_relay(self, repo: str, worktree_id: str) -> dict | None:
        resp = self._http.get(
            "/worktree-status-relay",
            params={"repo": repo, "worktree_id": worktree_id},
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)
