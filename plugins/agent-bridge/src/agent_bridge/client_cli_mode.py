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

    def wait_live_plugin_readiness(self, session_id: str, expectation: dict[str, Any]) -> dict[str, Any]:
        from .client_protocol_floor import protocol_floored_request
        from .plugin_readiness import PluginReadinessRequest
        from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

        body = PluginReadinessRequest.model_validate(expectation)
        return protocol_floored_request(self, PLUGIN_READINESS_PROTOCOL_VERSION)(
            "POST", f"/api/v1/live-sessions/{session_id}/plugin-readiness", body.model_dump(),
            request_timeout=body.timeout_seconds + 15,
        ) or {}

    def set_live_mode_after_plugins(
        self, session_id: str, mode: str, expectation: dict[str, Any], *, wait_timeout: float = 30,
    ) -> dict[str, Any]:
        from .client_protocol_floor import protocol_floored_request
        from .live_controls import SetModeRequest
        from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

        body = SetModeRequest(
            mode=mode, plugin_readiness=expectation, wait_timeout=wait_timeout,
            expected_session_id=expectation.get("expected_session_id"),
        )
        return protocol_floored_request(self, PLUGIN_READINESS_PROTOCOL_VERSION)(
            "POST", f"/api/v1/live-sessions/{session_id}/mode", body.model_dump(),
            request_timeout=body.plugin_readiness.timeout_seconds + body.wait_timeout + 20,
        ) or {}

    def resolve_live_session(self, handle: str, *, qualified_peer: bool = False) -> dict[str, Any]:
        """Resolve an exact session, legacy local worktree, or qualified peer address."""
        from .client import BridgeClientError
        from .protocol import QUALIFIED_LIVE_PEER_PROTOCOL_VERSION

        if qualified_peer and not self.daemon_supports(QUALIFIED_LIVE_PEER_PROTOCOL_VERSION):
            raise BridgeClientError(426, f"qualified peer addressing requires HTTP protocol {QUALIFIED_LIVE_PEER_PROTOCOL_VERSION}")
        try:
            return self._request(
                "GET", "/api/v1/live-sessions/resolve",
                params={"handle": handle, **({"qualified_peer": True} if qualified_peer else {})},
            ) or {}
        except BridgeClientError as exc:
            if exc.status == 404:
                if handle.startswith("live:") and not self.daemon_supports(QUALIFIED_LIVE_PEER_PROTOCOL_VERSION):
                    raise BridgeClientError(426, f"qualified peer addressing requires HTTP protocol {QUALIFIED_LIVE_PEER_PROTOCOL_VERSION}") from exc
                return {}
            raise

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

    def resolve_remote_live_session(
        self, host: str, target: str, *, qualified_peer: bool = False,
    ) -> dict[str, Any]:
        """Resolve an exact session or an explicitly qualified peer on its host."""
        from .client import BridgeClientError
        from .protocol import QUALIFIED_LIVE_PEER_PROTOCOL_VERSION

        self._require_remote_operations()
        if qualified_peer and not self.daemon_supports(QUALIFIED_LIVE_PEER_PROTOCOL_VERSION):
            raise BridgeClientError(426, f"remote qualified peer resolution requires HTTP protocol {QUALIFIED_LIVE_PEER_PROTOCOL_VERSION}")
        return self._request(
            "GET", self._remote_path(host, "/live-sessions/" + urllib.parse.quote(target, safe="")),
            **({"params": {"qualified_peer": True}} if qualified_peer else {}),
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

    def release_cli_mode_reservation(
        self,
        worktree_id: str,
        *,
        reservation_id: str | None = None,
        unclaimed_only: bool = False,
    ) -> int:
        """DELETE .../cli-mode-reservations/{worktree_id}.

        ``reservation_id`` compare-deletes one exact reservation; ``unclaimed_only``
        additionally refuses to delete a reservation a session already claimed.
        """
        if unclaimed_only:
            from .client import BridgeClientError
            from .protocol import CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION

            if not self.daemon_supports(CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION):
                version, _minimum = self.daemon_protocol()
                raise BridgeClientError(
                    426,
                    "Unclaimed-only CLI-mode reservation release requires "
                    "agent-bridge HTTP protocol "
                    f"v{CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION}; the daemon "
                    f"advertises v{version}. Skipping DELETE so the reservation "
                    "can expire instead of risking removal of a claimed session.",
                )
        params = {}
        if reservation_id:
            params["reservation_id"] = reservation_id
        if unclaimed_only:
            params["unclaimed_only"] = "true"
        query = "?" + urllib.parse.urlencode(params) if params else ""
        resp = self._request("DELETE", f"/api/v1/live-sessions/cli-mode-reservations/{worktree_id}{query}")
        return (resp or {}).get("removed", 0)

    def deregister_live_session(self, session_id: str) -> dict[str, Any]:
        """DELETE /api/v1/live-sessions/{id} -- idempotent; exact id only."""
        return self._request("DELETE", f"/api/v1/live-sessions/{session_id}") or {}
