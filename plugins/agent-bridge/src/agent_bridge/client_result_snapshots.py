"""Owned result requests with optional per-attempt native identity qualification."""

from __future__ import annotations

from typing import Any, Protocol


class OwnedResultClient(Protocol):
    def _require_result_snapshots(self) -> None: ...

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...


def owned_snapshot(
    client: OwnedResultClient,
    session_ref: str,
    *,
    position: str | None,
    max_items: int | None,
    max_text_chars: int | None,
    require_native_identity: bool,
) -> dict[str, Any]:
    """Keep legacy result reads while qualifying explicitly adopted semantics."""
    client._require_result_snapshots()
    params: dict[str, Any] = {}
    if position:
        params["position"] = position
    if max_items is not None:
        params["max_items"] = max_items
    if max_text_chars is not None:
        params["max_text_chars"] = max_text_chars
    send = client._request
    if require_native_identity:
        from .client_protocol_floor import protocol_floored_request
        from .protocol import RESULT_SNAPSHOT_PROTOCOL_VERSION

        params["native_only"] = "true"
        send = protocol_floored_request(
            client,
            RESULT_SNAPSHOT_PROTOCOL_VERSION,
            capabilities={"result_native_identity": 1},
        )
    return (
        send(
            "GET",
            f"/api/v1/sessions/{session_ref}/result",
            params=params or None,
        )
        or {}
    )
