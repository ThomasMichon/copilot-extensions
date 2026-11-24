"""One exact keyed prompt admission over the existing authenticated carrier."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import http.client
import json
import math
from typing import TYPE_CHECKING, Any, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from ssh_manager import CarrierLease, CarrierProtocolError, CarrierRemoteError, CarrierUnavailable, EnvelopeType

from .client_read_budget import ReadDeadlineExpired, command_deadline, remaining
from .remote_errors import RemoteBridgeError

if TYPE_CHECKING:
    from .client import BridgeClient

OPERATION = "session.native_prompt"
HTTP_CAPABILITY = "remote_native_prompt"
TIMEOUT = 20.0
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$"


class NativePromptBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    native_session_id: str = Field(min_length=1, max_length=256, pattern=_SAFE_ID)
    invocation_key: str = Field(min_length=1, max_length=1024)
    prompt: str = Field(min_length=1, max_length=1_048_576)
    caller_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("invocation_key", "prompt", "caller_id")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("native prompt fields must not be blank")
        return value


class NativePromptRequest(NativePromptBody):
    session_id: str = Field(min_length=1, max_length=256, pattern=_SAFE_ID)


def parse_request(payload: dict[str, Any]) -> NativePromptRequest:
    try:
        return NativePromptRequest.model_validate(payload)
    except ValidationError as exc:
        raise RemoteBridgeError(400, "invalid_request", "invalid exact native prompt") from exc


def _binding(request: NativePromptRequest) -> dict[str, Any]:
    return {
        "session_id": request.session_id,
        "native_session_id": request.native_session_id,
        "invocation_key": request.invocation_key,
    }


def _unknown(request: NativePromptRequest, status: int = 502) -> RemoteBridgeError:
    return RemoteBridgeError(
        status, "native_prompt_admission_unknown",
        "native prompt admission is unconfirmed; inspect the original operation, do not resend",
        details={**_binding(request), "admission_status": "unknown"},
    )


def _matches_binding(value: Any, request: NativePromptRequest) -> bool:
    return isinstance(value, dict) and all(
        value.get(key) == item for key, item in _binding(request).items()
    )


def validate_receipt(value: Any, request: NativePromptRequest) -> dict[str, Any]:
    if not (
        isinstance(value, dict)
        and type(value.get("version")) is int and value["version"] == 1
        and _matches_binding(value, request)
        and value.get("admission") == "accepted"
        and value.get("queued") is False
        and type(value.get("turn_index")) is int and value["turn_index"] >= 0
    ):
        raise _unknown(request)
    return dict(value)


def _submit(client_factory: Callable[[], BridgeClient], request: NativePromptRequest) -> dict[str, Any]:
    from .client import BridgeClientError, BridgeConnectionError
    from .native_bound_prompt import NativeSendNotAttempted, submit_native_bound_prompt
    from .send_outcome import SendRefused
    from .startup_observation import StartupObservationError

    try:
        with command_deadline(TIMEOUT):
            client = client_factory()
            remaining()
            result = submit_native_bound_prompt(
                client, request.session_id, request.prompt,
                expected_native_session_id=request.native_session_id,
                caller_id=request.caller_id, idempotency_key=request.invocation_key,
                no_resume=True, require_exact_owner=True,
            )
            remaining()
            return validate_receipt(result, request)
    except SendRefused as exc:
        cause_status = getattr(exc.__cause__, "status", None)
        status = (
            426 if exc.reason == "unsupported_native_fence"
            else cause_status if type(cause_status) is int
            else 503 if isinstance(exc, NativeSendNotAttempted) else 409
        )
        raise RemoteBridgeError(
            status, "native_prompt_refused", "exact native prompt refused",
            details={**_binding(request), "refusal": exc.payload()},
        ) from exc
    except ReadDeadlineExpired as exc:
        raise _unknown(request, 504) from exc
    except (
        StartupObservationError, BridgeClientError, BridgeConnectionError, OSError,
        http.client.HTTPException, UnicodeError, json.JSONDecodeError,
    ) as exc:
        raise _unknown(request) from exc


async def submit_native(
    payload: dict[str, Any], client_factory: Callable[[], BridgeClient],
) -> dict[str, Any]:
    """Validate before client acquisition; submit only through native admission."""
    request = parse_request(
        {key: value for key, value in payload.items() if key not in {"operation", "version"}}
    )
    return await asyncio.to_thread(_submit, client_factory, request)


class PromptTransport(Protocol):
    async def _lease(self, host: str) -> CarrierLease: ...


class RemoteNativePromptMixin:
    async def submit_native_prompt(
        self: PromptTransport, host: str, *, session_id: str, native_session_id: str,
        invocation_key: str, prompt: str, caller_id: str | None = None,
        timeout: float = TIMEOUT,
    ) -> dict[str, Any]:
        """Make one carrier attempt; never reconnect and replay unknown admission."""
        request = parse_request({
            "session_id": session_id, "native_session_id": native_session_id,
            "invocation_key": invocation_key, "prompt": prompt, "caller_id": caller_id,
        })
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise RemoteBridgeError(400, "invalid_request", "invalid native prompt timeout")
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
                        413, "native_prompt_frame_refused", "native prompt exceeds the carrier frame limit",
                        details=_binding(request),
                    ) from exc
                attempted = True
                response = await lease.carrier.request(
                    payload, timeout=timeout, request_id=request_id,
                )
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
                    error.code in {"native_prompt_refused", "native_prompt_admission_unknown"}
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
            if not attempted or exc.code == "native_prompt_admission_unknown":
                raise
            raise _unknown(request) from exc
        except (RuntimeError, AttributeError, TypeError) as exc:
            raise _unknown(request) from exc
