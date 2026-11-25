"""One capability-selected HTTP attempt for an exact keyed native prompt."""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import quote

from .client_read_budget import ReadDeadlineExpired, command_deadline, remaining
from .remote_errors import RemoteBridgeError
from .remote_native_prompt import HTTP_CAPABILITY, TIMEOUT, parse_request, validate_receipt


def submit_remote(
    client: Any, host: str, *, session_id: str, native_session_id: str,
    invocation_key: str, prompt: str, caller_id: str | None,
) -> dict[str, Any]:
    from .client import BridgeClientError, BridgeConnectionError
    from .client_protocol_floor import protocol_floored_request
    from .protocol import REMOTE_OPERATIONS_PROTOCOL_VERSION
    from .remote_operations import validate_host

    attempted = False
    binding = {
        "host": host, "session_id": session_id, "native_session_id": native_session_id,
        "invocation_key": invocation_key,
    }

    def mark_attempt() -> None:
        nonlocal attempted
        attempted = True

    def unknown(status: int = 502) -> BridgeClientError:
        return BridgeClientError(status, {
            "code": "native_prompt_admission_unknown", **binding,
            "message": "native prompt admission is unconfirmed; inspect the original operation, do not resend",
            "admission_status": "unknown", "reconnectable": False,
        })

    try:
        if validate_host(host) != host:
            raise RemoteBridgeError(400, "invalid_request", "host must be an exact host selector")
        selected = parse_request({
            "session_id": session_id, "native_session_id": native_session_id,
            "invocation_key": invocation_key, "prompt": prompt, "caller_id": caller_id,
        })
        with command_deadline(TIMEOUT):
            send = protocol_floored_request(
                client, REMOTE_OPERATIONS_PROTOCOL_VERSION,
                capabilities={HTTP_CAPABILITY: 1},
            )
            result = send(
                "POST",
                client._remote_path(host, f"/sessions/{quote(session_id, safe='')}/native-prompt"),
                selected.model_dump(exclude={"session_id"}),
                request_timeout=TIMEOUT, allow_retry=False, _on_request=mark_attempt,
            )
            remaining()
            if not isinstance(result, dict) or result.get("host") != host:
                raise unknown()
            validated = validate_receipt(result, selected)
            remaining()
            return validated
    except BridgeClientError as exc:
        if not attempted:
            raise
        detail = exc.detail
        if (
            isinstance(detail, dict)
            and all(detail.get(key) == value for key, value in binding.items())
            and (exc.status, detail.get("code")) in {
                (400, "invalid_request"), (400, "ambiguous_host"),
                (404, "host_not_found"), (413, "native_prompt_frame_refused"),
                (426, "unsupported_version"), (501, "unsupported_operation"),
                *(
                    (status, code)
                    for status in (400, 404, 409, 426, 500, 502, 503, 504)
                    for code in ("native_prompt_refused", "native_prompt_admission_unknown")
                ),
            }
            and detail.get("reconnectable") is False
        ):
            raise
        raise unknown() from exc
    except KeyboardInterrupt as exc:
        if not attempted:
            raise
        raise unknown() from exc
    except RemoteBridgeError as exc:
        if attempted:
            raise unknown(exc.status) from exc
        raise BridgeClientError(exc.status, exc.public_detail()) from exc
    except ReadDeadlineExpired as exc:
        if not attempted:
            raise
        raise unknown(504) from exc
    except (
        BridgeConnectionError, OSError, http.client.HTTPException,
        UnicodeError, json.JSONDecodeError,
    ) as exc:
        if not attempted:
            raise
        raise unknown() from exc
