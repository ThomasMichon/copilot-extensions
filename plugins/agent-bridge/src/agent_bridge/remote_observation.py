"""Exact, cursor-neutral native result observations over the existing carrier."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .client_read_budget import ReadDeadlineExpired, command_deadline, remaining
from .models import DelegatedResultSnapshot
from .remote_errors import RemoteBridgeError
from .result_snapshot import (
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_TEXT_CHARS,
    MAX_MAX_ITEMS,
    MAX_MAX_TEXT_CHARS,
)

if TYPE_CHECKING:
    from .client import BridgeClient

OPERATION = "bridge.result_snapshot"
READ_TIMEOUT = 15.0


class ResultRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    session_id: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$"
    )
    native_session_id: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$"
    )
    represented: bool
    position: str | None = Field(default=None, max_length=2048)
    max_items: int = Field(default=DEFAULT_MAX_ITEMS, ge=1, le=MAX_MAX_ITEMS)
    max_text_chars: int = Field(default=DEFAULT_MAX_TEXT_CHARS, ge=256, le=MAX_MAX_TEXT_CHARS)


def parse_request(payload: dict[str, Any]) -> ResultRequest:
    try:
        return ResultRequest.model_validate(payload)
    except ValidationError as exc:
        raise RemoteBridgeError(
            400, "invalid_request", "invalid native result observation"
        ) from exc


def validate_result(value: Any, request: ResultRequest) -> dict[str, Any]:
    try:
        result = DelegatedResultSnapshot.model_validate_json(
            json.dumps(value, allow_nan=False), strict=True
        )
    except (ValueError, TypeError) as exc:
        raise RemoteBridgeError(
            502, "invalid_remote_result", "invalid native result snapshot"
        ) from exc
    identity = result.identity
    if (
        identity.requested_ref != request.session_id
        or identity.snapshot_session_id != request.session_id
        or identity.native_session_id != request.native_session_id
    ):
        raise RemoteBridgeError(
            409, "bridge_observation_identity_changed", "native result identity changed"
        )
    limits = result.limits
    if (
        limits.max_items != request.max_items
        or limits.max_text_chars != request.max_text_chars
        or not 0 <= limits.used_text_chars <= request.max_text_chars
        or len(result.incremental.items) > request.max_items
    ):
        raise RemoteBridgeError(502, "invalid_remote_result", "native result bounds changed")
    return result.model_dump(mode="json")


def read_native_result(
    client_factory: Callable[[], BridgeClient],
    request: ResultRequest,
) -> dict[str, Any]:
    """Keep health negotiation and the native read inside one HTTP deadline."""
    try:
        with command_deadline(READ_TIMEOUT):
            client = client_factory()
            remaining()
            method = (
                client.get_live_result_snapshot
                if request.represented
                else client.get_result_snapshot
            )
            result = method(
                request.session_id,
                require_native_identity=True,
                position=request.position,
                max_items=request.max_items,
                max_text_chars=request.max_text_chars,
            )
            remaining()
            return validate_result(result, request)
    except ReadDeadlineExpired as exc:
        raise RemoteBridgeError(
            504, "remote_operation_timeout", "native result observation timed out"
        ) from exc


async def read_observation(
    operation: str,
    payload: dict[str, Any],
    client_factory: Callable[[], BridgeClient],
) -> dict[str, Any]:
    if operation == "bridge.inventory":
        return await asyncio.to_thread(
            client_factory().list_bridges, all_projects=True, local_only=True
        )
    observation = parse_request(
        {key: value for key, value in payload.items() if key not in {"operation", "version"}}
    )
    return await asyncio.to_thread(read_native_result, client_factory, observation)


class ResultTransport(Protocol):
    async def _request(
        self,
        host: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...


class RemoteObservationMixin:
    async def bridge_inventory(self: ResultTransport, host: str) -> dict[str, Any]:
        from .bridge_fleet_inventory import validate_report

        response = await self._request(host, {"operation": "bridge.inventory"}, timeout=15.0)
        return validate_report(response.get("result"))

    async def result_snapshot(
        self: ResultTransport,
        host: str,
        *,
        session_id: str,
        native_session_id: str,
        represented: bool,
        position: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    ) -> dict[str, Any]:
        request = parse_request(
            {
                "session_id": session_id,
                "native_session_id": native_session_id,
                "represented": represented,
                "position": position,
                "max_items": max_items,
                "max_text_chars": max_text_chars,
            }
        )
        try:
            response = await asyncio.wait_for(
                self._request(
                    host,
                    {"operation": OPERATION, **request.model_dump()},
                    timeout=READ_TIMEOUT,
                ),
                timeout=READ_TIMEOUT,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise RemoteBridgeError(
                504, "remote_operation_timeout", "native result observation timed out"
            ) from exc
        return validate_result(response.get("result"), request)
