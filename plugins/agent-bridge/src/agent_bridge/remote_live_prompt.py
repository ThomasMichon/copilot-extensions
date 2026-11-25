"""Exact registered-inbox admission without alias following or transport replay."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import http.client
import json
import math
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from ssh_manager import CarrierLease, CarrierProtocolError, CarrierRemoteError, CarrierUnavailable, EnvelopeType

from .client_read_budget import ReadDeadlineExpired, command_deadline, remaining
from .remote_errors import RemoteBridgeError

if TYPE_CHECKING:
    from .client import BridgeClient

OPERATION = "live_session.exact_prompt"
HTTP_CAPABILITY = "remote_exact_live_prompt"
TIMEOUT = 20.0
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$"


class ExactLivePromptBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    invocation_key: str = Field(min_length=1, max_length=256, pattern=_SAFE_ID)
    sender: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    prompt: str = Field(min_length=1, max_length=1_048_576)

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class ExactLivePromptRequest(ExactLivePromptBody):
    session_id: str = Field(min_length=1, max_length=256, pattern=_SAFE_ID)


def parse_request(payload: dict[str, Any]) -> ExactLivePromptRequest:
    """Validate the strict carrier body before acquiring an inbox client."""
    try:
        return ExactLivePromptRequest.model_validate(payload)
    except ValidationError as exc:
        raise RemoteBridgeError(400, "invalid_request", "invalid exact live prompt") from exc


def _binding(request: ExactLivePromptRequest) -> dict[str, Any]:
    return {
        "session_id": request.session_id,
        "invocation_key": request.invocation_key,
        "sender": request.sender,
    }


def _unknown(request: ExactLivePromptRequest, status: int = 502) -> RemoteBridgeError:
    return RemoteBridgeError(
        status, "exact_live_prompt_admission_unknown",
        "registered-inbox admission is unconfirmed; inspect the original key, do not resend",
        details={**_binding(request), "admission_status": "unknown"},
    )


def _matches_binding(value: Any, request: ExactLivePromptRequest) -> bool:
    return isinstance(value, dict) and all(
        value.get(key) == item for key, item in _binding(request).items()
    )


def validate_receipt(value: Any, request: ExactLivePromptRequest) -> dict[str, Any]:
    """Accept only a positive receipt for the original session, sender and key."""
    if not (
        isinstance(value, dict)
        and type(value.get("version")) is int and value["version"] == 1
        and _matches_binding(value, request)
        and value.get("admission") == "accepted"
        and type(value.get("message_id")) is int and value["message_id"] > 0
    ):
        raise _unknown(request)
    return dict(value)


def _submit(client_factory: Callable[[], BridgeClient], request: ExactLivePromptRequest) -> dict[str, Any]:
    from .client import BridgeClientError, BridgeConnectionError
    from .client_protocol_floor import protocol_floored_request

    attempted = False

    def mark_attempt() -> None:
        nonlocal attempted
        attempted = True

    try:
        with command_deadline(TIMEOUT):
            client = client_factory()
            remaining()
            send = protocol_floored_request(client, 1, capabilities={"live_exact_send": 1})
            result = send(
                "POST", f"/api/v1/live-sessions/{quote(request.session_id, safe='')}/messages",
                {
                    "sender": request.sender, "body": request.prompt,
                    "kind": "prompt", "delivery": "steer", "exact_session": True,
                    "expected_session_id": request.session_id,
                    "idempotency_key": request.invocation_key,
                },
                allow_retry=False, _on_request=mark_attempt,
            )
            remaining()
            if not isinstance(result, dict) or result.get("session_id") != request.session_id:
                raise _unknown(request)
            return validate_receipt({
                "version": 1, **_binding(request), "admission": "accepted",
                "message_id": result["message_id"],
            }, request)
    except BridgeClientError as exc:
        if not attempted and exc.status == 426:
            raise RemoteBridgeError(
                426, "exact_live_prompt_refused",
                "hosting daemon does not support exact registered-inbox admission",
                details=_binding(request),
            ) from exc
        raise _unknown(request) from exc
    except ReadDeadlineExpired as exc:
        raise _unknown(request, 504) from exc
    except (
        BridgeConnectionError, OSError,
        http.client.HTTPException, UnicodeError, json.JSONDecodeError,
        KeyError, TypeError,
    ) as exc:
        raise _unknown(request) from exc


async def submit_exact_live(
    payload: dict[str, Any], client_factory: Callable[[], BridgeClient],
) -> dict[str, Any]:
    """Validate before client acquisition and reuse atomic exact inbox admission."""
    request = parse_request({
        key: value for key, value in payload.items() if key not in {"operation", "version"}
    })
    return await asyncio.to_thread(_submit, client_factory, request)


class LivePromptTransport(Protocol):
    async def _lease(self, host: str) -> CarrierLease: ...


class RemoteExactLivePromptMixin:
    async def submit_exact_live_prompt(
        self: LivePromptTransport, host: str, *, session_id: str, invocation_key: str,
        sender: str, prompt: str, timeout: float = TIMEOUT,
    ) -> dict[str, Any]:
        """Make one framed carrier attempt; leave uncertain admission unresolved."""
        request = parse_request({
            "session_id": session_id, "invocation_key": invocation_key,
            "sender": sender, "prompt": prompt,
        })
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise RemoteBridgeError(400, "invalid_request", "invalid exact live prompt timeout")
        attempted = False

        async def once() -> dict[str, Any]:
            nonlocal attempted
            lease = await self._lease(host)
            try:
                encoder = getattr(lease.carrier, "encode_request", None)
                if not callable(encoder):
                    raise RemoteBridgeError(
                        426, "unsupported_version", "carrier request framing preflight is unavailable",
                        details=_binding(request),
                    )
                payload = {"version": 2, "operation": OPERATION, **request.model_dump()}
                request_id = uuid.uuid4().hex
                try:
                    encoder(payload, request_id=request_id)
                except CarrierProtocolError as exc:
                    raise RemoteBridgeError(
                        413, "exact_live_prompt_frame_refused",
                        "registered-inbox prompt exceeds the carrier frame limit",
                        details=_binding(request),
                    ) from exc
                attempted = True
                response = await lease.carrier.request(payload, timeout=timeout, request_id=request_id)
                if response.type != EnvelopeType.RESPONSE or not isinstance(response.payload, dict):
                    raise _unknown(request)
                return validate_receipt(response.payload.get("result"), request)
            finally:
                await lease.release()

        try:
            return await asyncio.wait_for(once(), timeout=timeout)
        except CarrierRemoteError as exc:
            error = RemoteBridgeError.from_carrier(exc)
            if (
                (error.status, error.code) in {
                    (400, "invalid_request"), (426, "unsupported_version"),
                    (501, "unsupported_operation"),
                }
                or (
                    error.code in {"exact_live_prompt_refused", "exact_live_prompt_admission_unknown"}
                    and _matches_binding(error.details, request)
                )
            ):
                raise error from exc
            raise _unknown(request) from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise _unknown(request, 504) from exc
        except (CarrierUnavailable, OSError) as exc:
            raise _unknown(request, 503) from exc
        except RemoteBridgeError as exc:
            if not attempted or exc.code == "exact_live_prompt_admission_unknown":
                raise
            raise _unknown(request) from exc
        except (RuntimeError, AttributeError, TypeError) as exc:
            raise _unknown(request) from exc
