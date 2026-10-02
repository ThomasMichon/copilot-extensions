"""``BridgeClient``'s CLI-mode session lifecycle calls: a worktree's
reservation (allocate-before-launch, agent-bridge-cli-mode-sessions Phase 2)
and removal of a live session whose process a launcher verified is gone.

Extracted out of ``client.py`` (at its grandfathered module-size ceiling),
following ``client_worktree_restart.py``'s precedent.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from .client_worktree_restart import _Base


class CliModeClientMixin(_Base):
    """Mixin supplying ``BridgeClient``'s CLI-mode reservation and deregister calls."""

    def create_cli_mode_reservation(
        self, worktree_id: str, *, ttl_seconds: float = 300.0,
        venue: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/live-sessions/cli-mode-reservations/{worktree_id}.

        Explicitly, per-request allocates the worktree's next CLI-mode Session
        Host (agent-bridge-cli-mode-sessions Phase 2, §opt-in-not-ambient-default);
        the claiming live session inherits ``venue``. Raises ``BridgeClientError``
        (409) if a not-yet-expired reservation holds it (§one-host-per-cwd-lane).
        """
        body = {"worktree_id": worktree_id, "ttl_seconds": ttl_seconds, "venue": venue}
        return self._request(
            "POST", f"/api/v1/live-sessions/cli-mode-reservations/{worktree_id}", body,
        ) or {}

    def get_cli_mode_reservation(self, worktree_id: str) -> dict[str, Any]:
        """GET /api/v1/live-sessions/cli-mode-reservations/{worktree_id}; {} if none."""
        from .client import BridgeClientError

        try:
            return self._request(
                "GET",
                f"/api/v1/live-sessions/cli-mode-reservations/{worktree_id}",
            ) or {}
        except BridgeClientError as exc:
            if exc.status == 404:
                return {}
            raise

    def release_cli_mode_reservation(self, worktree_id: str, *, reservation_id: str | None = None) -> int:
        """DELETE .../cli-mode-reservations/{worktree_id} (only ``reservation_id`` if given)."""
        query = "?" + urllib.parse.urlencode({"reservation_id": reservation_id}) if reservation_id else ""
        resp = self._request("DELETE", f"/api/v1/live-sessions/cli-mode-reservations/{worktree_id}{query}")
        return (resp or {}).get("removed", 0)

    def deregister_live_session(self, session_id: str) -> dict[str, Any]:
        """DELETE /api/v1/live-sessions/{id} -- idempotent; exact id only."""
        return self._request("DELETE", f"/api/v1/live-sessions/{session_id}") or {}
