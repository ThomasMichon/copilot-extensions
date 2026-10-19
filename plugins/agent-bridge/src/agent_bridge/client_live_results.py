"""Capability-gated bounded result and receipt reads for represented sessions."""

from __future__ import annotations

from typing import Any

from .client_protocol_floor import protocol_floored_request
from .protocol import (
    LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION,
    REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION,
)


class LiveResultsClientMixin:
    def _require_live_message_receipts(self) -> None:
        from .client import BridgeClientError
        if not self.daemon_supports(LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION, refresh=True):
            raise BridgeClientError(
                426,
                "live-message receipts and post-consumption reply waits require daemon "
                f"protocol {LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION}",
            )

    def get_live_message_receipt(self, session_id: str, message_id: int) -> dict[str, Any]:
        self._require_live_message_receipts()
        return protocol_floored_request(self, LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION)(
            "GET", f"/api/v1/live-sessions/{session_id}/messages/{message_id}/receipt"
        ) or {}

    def _require_represented_result_snapshots(self) -> None:
        from .client import BridgeClientError
        if not self.daemon_supports(REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION, refresh=True):
            version, _minimum = self.daemon_protocol()
            raise BridgeClientError(
                426,
                "represented result snapshots require agent-bridge HTTP "
                f"protocol v{REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION}; "
                f"the daemon advertises v{version}. Update the agent-bridge "
                "plugin + runtime.",
            )

    def get_live_result_snapshot(
        self,
        session_ref: str,
        *,
        position: str | None = None,
        max_items: int | None = None,
        max_text_chars: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/live-sessions/{ref}/result after a capability gate."""
        self._require_represented_result_snapshots()
        params: dict[str, Any] = {}
        if position:
            params["position"] = position
        if max_items is not None:
            params["max_items"] = max_items
        if max_text_chars is not None:
            params["max_text_chars"] = max_text_chars
        return protocol_floored_request(self, REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION)(
            "GET", f"/api/v1/live-sessions/{session_ref}/result",
            params=params or None,
        ) or {}

    def expand_live_result_ref(
        self, session_ref: str, ref: str
    ) -> dict[str, Any]:
        """GET represented result detail for one process-lifetime reference."""
        self._require_represented_result_snapshots()
        return protocol_floored_request(self, REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION)(
            "GET",
            f"/api/v1/live-sessions/{session_ref}/result/detail",
            params={"ref": ref},
        ) or {}
