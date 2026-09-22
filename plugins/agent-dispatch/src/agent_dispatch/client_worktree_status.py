"""Small worktree-status relay client surface, split out for module-size caps."""

from __future__ import annotations

from collections.abc import Sequence


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

    def worktree_status_relays(
        self, refs: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], dict | None]:
        if not refs:
            return {}
        rows = self._unwrap(
            self._http.post(
                "/worktree-status-relays",
                json=[
                    {"repo": repo, "worktree_id": worktree_id}
                    for repo, worktree_id in refs
                ],
            )
        )
        return {
            (row["repo"], row["worktree_id"]): row.get("entry")
            for row in rows
            if isinstance(row, dict)
        }
