"""Capability-selected, exact remote native-result reads."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .client_read_budget import command_deadline, remaining
from .remote_errors import RemoteBridgeError
from .remote_observation import HTTP_CAPABILITY, READ_TIMEOUT, parse_request, validate_result


def remote_snapshot(
    client: Any,
    host: str,
    *,
    session_id: str,
    native_session_id: str,
    represented: bool,
    position: str | None,
    max_items: int | None,
    max_text_chars: int | None,
) -> dict[str, Any]:
    from .client import BridgeClientError
    from .client_protocol_floor import protocol_floored_request
    from .protocol import REMOTE_OPERATIONS_PROTOCOL_VERSION

    values: dict[str, Any] = {
        "session_id": session_id,
        "native_session_id": native_session_id,
        "represented": represented,
        "position": position,
    }
    values.update(
        (key, value)
        for key, value in (("max_items", max_items), ("max_text_chars", max_text_chars))
        if value is not None
    )
    try:
        request = parse_request(values)
        params = request.model_dump(exclude={"session_id"}, exclude_none=True)
        params["represented"] = "true" if represented else "false"
        with command_deadline(READ_TIMEOUT):
            send = protocol_floored_request(
                client,
                REMOTE_OPERATIONS_PROTOCOL_VERSION,
                capabilities={HTTP_CAPABILITY: 1},
            )
            result = send(
                "GET",
                client._remote_path(host, f"/sessions/{quote(session_id, safe='')}/result"),
                params=params,
                request_timeout=READ_TIMEOUT,
            )
            remaining()
            validated = validate_result(result, request)
            remaining()
            return validated
    except RemoteBridgeError as exc:
        raise BridgeClientError(exc.status, exc.public_detail()) from exc
