"""Per-attempt negotiated retained metadata operations without effectful fallback."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .acquisition_contract import AcquisitionRefused
from .client_protocol_floor import protocol_floored_request
from .models import RetainedRecoveryRequest
from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION
from .retained_metadata_lineage import CAPABILITY, COLUMN, adoption
from .retained_metadata_contract import PrepareRetainedMetadataRequest


def _outcome(
    result: Any, predecessor_id: str, journal_key: str, expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .client import BridgeClientError
    import json

    try:
        if not isinstance(result, dict):
            raise ValueError("missing adoption")
        request = RetainedRecoveryRequest.model_validate(result.get("request"))
        boundary = adoption({
            COLUMN: json.dumps(result), "session_id": predecessor_id, "key_digest": journal_key,
            "native_session_id": request.native_session_id,
        })
        if boundary is None or expected is not None and boundary["request"] != expected:
            raise ValueError("changed request")
        return boundary
    except (AcquisitionRefused, ValueError, TypeError) as exc:
        raise BridgeClientError(
            502, "retained metadata adoption outcome is unconfirmed; inspect, do not replay",
        ) from exc


def prepare(
    client: Any, predecessor_id: str, recovery: dict[str, Any], worktree_writer: dict[str, Any],
) -> dict[str, Any]:
    body = PrepareRetainedMetadataRequest.model_validate({
        "recovery": recovery, "worktree_writer": worktree_writer,
    })
    send = protocol_floored_request(
        client, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={CAPABILITY: 1},
    )
    result = send(
        "POST", f"/api/v1/sessions/{quote(predecessor_id, safe='')}/retained-metadata",
        body.model_dump(mode="json"), allow_retry=False,
    )
    return _outcome(result, predecessor_id, body.recovery.journal_key, body.recovery.model_dump())


def inspect(client: Any, predecessor_id: str, journal_key: str) -> dict[str, Any]:
    send = protocol_floored_request(
        client, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={CAPABILITY: 1},
    )
    result = send(
        "GET", f"/api/v1/sessions/{quote(predecessor_id, safe='')}/retained-metadata/"
        + quote(journal_key, safe=""),
    )
    return _outcome(result, predecessor_id, journal_key)
