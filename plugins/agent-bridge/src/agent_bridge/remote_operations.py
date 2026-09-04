"""Narrow remote Bridge operations transported by the persistent SSH carrier."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from ssh_manager import (
    CarrierLease,
    CarrierRemoteError,
    CarrierSubscription,
    CarrierUnavailable,
    ConnectionManager,
    Envelope,
    EnvelopeType,
    SSHProfileSource,
)

from .agent_registry import AgentResolver
from .client import (
    BridgeClient,
    BridgeClientError,
    BridgeConnectionError,
    SseStream,
)
from .protocol import REMOTE_OPERATIONS_PROTOCOL_VERSION

REMOTE_OPERATION_VERSION = 1
MAX_CALLER_ID_LENGTH = 128
MAX_HOST_LENGTH = 128
MAX_SESSION_ID_LENGTH = 256
MAX_CONTINUITY_ID_LENGTH = 128
REMOTE_STREAM_RECONNECT_GRACE = 30.0
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$")
_EOF = object()


class RemoteBridgeError(RuntimeError):
    """A bounded public failure from a remote Bridge operation."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        reconnectable: bool = False,
    ) -> None:
        self.status = status
        self.code = code
        self.details = dict(details or {})
        self.reconnectable = reconnectable
        super().__init__(message)

    @classmethod
    def from_carrier(cls, error: CarrierRemoteError) -> RemoteBridgeError:
        payload = error.payload
        default_status = {
            "unsupported_operation": 501,
            "unsupported_version": 426,
            "invalid_request": 400,
            "session_not_found": 404,
            "cursor_invalidated": 409,
            "cursor_mismatch": 409,
            "replay_gap": 409,
        }.get(error.code, 502)
        try:
            status = int(payload.get("status") or default_status)
        except (TypeError, ValueError):
            status = 502
        details = payload.get("details")
        return cls(
            status,
            error.code,
            str(error),
            details=details if isinstance(details, dict) else None,
            reconnectable=error.reconnectable,
        )

    def public_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            **self.details,
            "reconnectable": self.reconnectable,
        }


async def _require_remote_operations_protocol(client: BridgeClient) -> None:
    supported = await asyncio.to_thread(
        client.daemon_supports, REMOTE_OPERATIONS_PROTOCOL_VERSION
    )
    if supported:
        return
    version, _minimum = await asyncio.to_thread(client.daemon_protocol)
    raise RemoteBridgeError(
        426,
        "unsupported_version",
        "the hosting Agent Bridge does not support remote event operations",
        details={
            "required_version": REMOTE_OPERATIONS_PROTOCOL_VERSION,
            "hosting_version": version,
        },
    )


def _validate_id(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise RemoteBridgeError(400, "invalid_request", f"{field} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or _SAFE_ID.fullmatch(normalized) is None
    ):
        raise RemoteBridgeError(
            400,
            "invalid_request",
            f"{field} must be 1-{maximum} safe ASCII characters",
        )
    return normalized


def validate_caller_id(value: Any) -> str:
    """Validate a stable, caller-supplied consumer identity."""
    if isinstance(value, str) and value.strip() == "__default__":
        raise RemoteBridgeError(
            400,
            "invalid_request",
            "caller_id is reserved for legacy anonymous delivery",
        )
    caller_id = _validate_id(
        value, field="caller_id", maximum=MAX_CALLER_ID_LENGTH
    )
    return caller_id


def validate_host(value: Any) -> str:
    return _validate_id(value, field="host", maximum=MAX_HOST_LENGTH)


def validate_session_id(value: Any) -> str:
    return _validate_id(
        value, field="session_id", maximum=MAX_SESSION_ID_LENGTH
    )


def validate_continuity_id(value: Any) -> str | None:
    """Validate an optional event-log continuity identifier."""
    if value is None:
        return None
    return _validate_id(
        value,
        field="continuity_id",
        maximum=MAX_CONTINUITY_ID_LENGTH,
    )


def _validate_cursor(value: Any, *, field: str = "after") -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise RemoteBridgeError(400, "invalid_request", f"{field} must be an integer")
    try:
        cursor = int(value)
    except (TypeError, ValueError) as exc:
        raise RemoteBridgeError(
            400, "invalid_request", f"{field} must be an integer"
        ) from exc
    if cursor < 0 or cursor > 2**63 - 1:
        raise RemoteBridgeError(
            400, "invalid_request", f"{field} is outside the supported range"
        )
    return cursor


def _error_envelope(
    request: Envelope,
    error: RemoteBridgeError,
) -> Envelope:
    return Envelope(
        EnvelopeType.ERROR,
        request_id=request.request_id,
        subscription_id=request.subscription_id,
        payload={
            "code": error.code,
            "message": str(error),
            "status": error.status,
            "reconnectable": error.reconnectable,
            "details": error.details,
        },
    )


def _bridge_error(error: BridgeClientError) -> RemoteBridgeError:
    detail = error.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or "remote_bridge_error")
        message = str(detail.get("message") or code)
        details = dict(detail)
        details.pop("code", None)
        details.pop("message", None)
    else:
        code = "remote_bridge_error"
        message = str(detail)
        details = {}
    return RemoteBridgeError(error.status, code, message, details=details)


def _next_sse(stream: SseStream) -> object:
    try:
        return next(stream)
    except StopIteration:
        return _EOF


class CarrierRequestRouter:
    """Remote endpoint router that proxies only the reviewed Bridge operations."""

    _OPERATIONS = frozenset(
        {
            "session.status",
            "live_session.resolve",
            "session.events",
            "session.events.ack",
        }
    )

    def __init__(
        self,
        client_factory: Callable[[], BridgeClient] | None = None,
    ) -> None:
        self._client_factory = client_factory or BridgeClient.from_config

    def _client(self) -> BridgeClient:
        try:
            return self._client_factory()
        except SystemExit as exc:
            raise RemoteBridgeError(
                503,
                "remote_bridge_unavailable",
                "the hosting Agent Bridge authentication state is unavailable",
                reconnectable=True,
            ) from exc

    @staticmethod
    def _validate_request(envelope: Envelope) -> tuple[str, dict[str, Any]]:
        payload = envelope.payload
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in CarrierRequestRouter._OPERATIONS:
            raise RemoteBridgeError(
                501,
                "unsupported_operation",
                "carrier operation is not available",
            )
        version = payload.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != REMOTE_OPERATION_VERSION
        ):
            raise RemoteBridgeError(
                426,
                "unsupported_version",
                "remote operation version is not supported",
                details={
                    "supported_version": REMOTE_OPERATION_VERSION,
                    "requested_version": version,
                },
            )
        return operation, payload

    async def __call__(
        self, envelope: Envelope
    ) -> Envelope | AsyncIterator[Envelope]:
        try:
            operation, payload = self._validate_request(envelope)
            session_id = validate_session_id(payload.get("session_id"))
            if operation == "session.status":
                caller_id = validate_caller_id(payload.get("caller_id"))
                result = await asyncio.to_thread(
                    self._client().get_session_status,
                    session_id,
                    caller_id=caller_id,
                )
                return Envelope(EnvelopeType.RESPONSE, payload={"result": result})
            if operation == "live_session.resolve":
                result = await asyncio.to_thread(
                    self._client().get_live_session,
                    session_id,
                )
                if not result:
                    raise RemoteBridgeError(
                        404,
                        "session_not_found",
                        f"live session {session_id} was not found",
                    )
                return Envelope(EnvelopeType.RESPONSE, payload={"result": result})
            caller_id = validate_caller_id(payload.get("caller_id"))
            if operation == "session.events.ack":
                client = self._client()
                await _require_remote_operations_protocol(client)
                last_id = _validate_cursor(payload.get("last_id"), field="last_id")
                if last_id is None:
                    raise RemoteBridgeError(
                        400, "invalid_request", "last_id is required"
                    )
                continuity_id = validate_continuity_id(
                    payload.get("continuity_id")
                )
                info = await asyncio.to_thread(
                    client.get_cursor_info,
                    session_id,
                    caller_id=caller_id,
                )
                current_continuity = info.get("continuity_id")
                if continuity_id is None and current_continuity is not None:
                    raise RemoteBridgeError(
                        400,
                        "invalid_request",
                        "continuity_id is required for a non-empty event log",
                        details={"continuity_id": current_continuity},
                    )
                if continuity_id != current_continuity:
                    raise RemoteBridgeError(
                        409,
                        "cursor_invalidated",
                        "the acknowledgement names a replaced event log",
                        details={
                            "action": "full_reconcile",
                            "prior_continuity_id": continuity_id,
                            "continuity_id": current_continuity,
                        },
                    )
                if last_id > int(info.get("head_id") or 0):
                    raise RemoteBridgeError(
                        409,
                        "replay_gap",
                        "the acknowledgement is beyond the authoritative event head",
                        details={
                            "action": "full_reconcile",
                            "last_id": last_id,
                            "head_id": int(info.get("head_id") or 0),
                        },
                    )
                effective = await asyncio.to_thread(
                    client.ack_cursor,
                    session_id,
                    last_id,
                    caller_id=caller_id,
                    continuity_id=current_continuity,
                )
                return Envelope(
                    EnvelopeType.RESPONSE,
                    payload={"last_acked_id": effective},
                )
            return await self._prepare_events(
                envelope,
                self._client(),
                session_id=session_id,
                caller_id=caller_id,
                after=_validate_cursor(envelope.position),
                continuity_id=payload.get("continuity_id"),
            )
        except BridgeClientError as exc:
            return _error_envelope(envelope, _bridge_error(exc))
        except BridgeConnectionError as exc:
            return _error_envelope(
                envelope,
                RemoteBridgeError(
                    503,
                    "remote_bridge_unavailable",
                    str(exc),
                    reconnectable=True,
                ),
            )
        except RemoteBridgeError as exc:
            return _error_envelope(envelope, exc)

    async def _prepare_events(
        self,
        envelope: Envelope,
        client: BridgeClient,
        *,
        session_id: str,
        caller_id: str,
        after: int | None,
        continuity_id: Any,
    ) -> AsyncIterator[Envelope]:
        await _require_remote_operations_protocol(client)
        continuity_id = validate_continuity_id(continuity_id)
        info = await asyncio.to_thread(
            client.get_cursor_info, session_id, caller_id=caller_id
        )
        invalidation = info.get("invalidation")
        if invalidation:
            raise RemoteBridgeError(
                409,
                "cursor_invalidated",
                "the caller cursor was invalidated by an event-log rebuild",
                details={
                    "action": "full_reconcile",
                    "head_id": int(info.get("head_id") or 0),
                    "continuity_id": info.get("continuity_id"),
                    **invalidation,
                },
            )
        durable_cursor = int(info.get("last_acked_id") or 0)
        if after is not None and after != durable_cursor:
            raise RemoteBridgeError(
                409,
                "cursor_mismatch",
                "the requested start does not match the durable caller cursor",
                details={
                    "action": "full_reconcile",
                    "requested_after": after,
                    "last_acked_id": durable_cursor,
                },
            )
        current_continuity = info.get("continuity_id")
        if continuity_id is not None and continuity_id != current_continuity:
            raise RemoteBridgeError(
                409,
                "cursor_invalidated",
                "the authoritative event log continuity changed",
                details={
                    "action": "full_reconcile",
                    "prior_continuity_id": continuity_id,
                    "continuity_id": current_continuity,
                    "head_id": int(info.get("head_id") or 0),
                },
            )
        if durable_cursor > int(info.get("head_id") or 0):
            raise RemoteBridgeError(
                409,
                "replay_gap",
                "the durable caller cursor is beyond the authoritative event head",
                details={
                    "action": "full_reconcile",
                    "last_acked_id": durable_cursor,
                    "head_id": int(info.get("head_id") or 0),
                    "continuity_id": current_continuity,
                },
            )
        if not info.get("cursor_registered"):
            await asyncio.to_thread(
                client.ack_cursor,
                session_id,
                0,
                caller_id=caller_id,
                continuity_id=current_continuity,
            )
        return self._event_stream(
            client,
            session_id=session_id,
            caller_id=caller_id,
            continuity_id=current_continuity,
            accepted_cursor=durable_cursor,
        )

    async def _event_stream(
        self,
        client: BridgeClient,
        *,
        session_id: str,
        caller_id: str,
        continuity_id: str | None,
        accepted_cursor: int,
    ) -> AsyncIterator[Envelope]:
        yield Envelope(
            EnvelopeType.RESPONSE,
            payload={
                "accepted": True,
                "last_acked_id": accepted_cursor,
                "continuity_id": continuity_id,
            },
        )
        backoff = 0.25
        expected_continuity = continuity_id
        resume_after = accepted_cursor
        retryable_error_since: float | None = None
        while True:
            stream: SseStream | None = None
            try:
                await _require_remote_operations_protocol(client)
                stream = await asyncio.to_thread(
                    client.stream_events,
                    session_id,
                    caller_id=caller_id,
                    controlled=True,
                    continuity_id=expected_continuity,
                    after=resume_after,
                    transient=resume_after != accepted_cursor,
                )
                retryable_error_since = None
                backoff = 0.25
                while True:
                    item = await asyncio.to_thread(_next_sse, stream)
                    if item is _EOF:
                        client.refresh_endpoint()
                        break
                    event = item
                    event_name = str(event.get("event") or "")
                    if event_name == "bridge_control":
                        control = event.get("data")
                        control = control if isinstance(control, dict) else {}
                        yield Envelope(
                            EnvelopeType.ERROR,
                            payload={
                                "code": str(
                                    control.get("code") or "replay_gap"
                                ),
                                "message": str(
                                    control.get("message")
                                    or "remote event continuity was lost"
                                ),
                                "status": 409,
                                "details": control,
                            },
                        )
                        return
                    if event_name in {"_heartbeat", "tool_progress"}:
                        yield Envelope(
                            EnvelopeType.EVENT,
                            payload={
                                "kind": event_name.removeprefix("_"),
                                "data": event.get("data") or {},
                            },
                        )
                        continue
                    raw_id = event.get("id")
                    event_id = _validate_cursor(raw_id, field="event_id")
                    event_continuity = event.get("continuity_id")
                    if isinstance(event_continuity, str):
                        expected_continuity = event_continuity
                    yield Envelope(
                        EnvelopeType.EVENT,
                        payload={
                            "kind": "event",
                            "id": event_id,
                            "event": event_name,
                            "data": event.get("data") or {},
                            "timestamp": event.get("timestamp"),
                            "continuity_id": expected_continuity,
                        },
                    )
                    if event_id is not None:
                        resume_after = event_id
            except RemoteBridgeError as error:
                yield Envelope(
                    EnvelopeType.ERROR,
                    payload={
                        "code": error.code,
                        "message": str(error),
                        "status": error.status,
                        "details": error.details,
                    },
                )
                return
            except BridgeClientError as exc:
                error = _bridge_error(exc)
                if error.status in {404, 503}:
                    now = time.monotonic()
                    if retryable_error_since is None:
                        retryable_error_since = now
                    if (
                        now - retryable_error_since
                        < REMOTE_STREAM_RECONNECT_GRACE
                    ):
                        client.refresh_endpoint()
                    else:
                        yield Envelope(
                            EnvelopeType.ERROR,
                            payload={
                                "code": error.code,
                                "message": str(error),
                                "status": error.status,
                                "details": error.details,
                            },
                        )
                        return
                elif error.status == 409:
                    yield Envelope(
                        EnvelopeType.ERROR,
                        payload={
                            "code": error.code,
                            "message": str(error),
                            "status": error.status,
                            "details": error.details,
                        },
                    )
                    return
                else:
                    yield Envelope(
                        EnvelopeType.ERROR,
                        payload={
                            "code": error.code,
                            "message": str(error),
                            "status": error.status,
                            "details": error.details,
                            "reconnectable": error.reconnectable,
                        },
                    )
                    return
            except (BridgeConnectionError, OSError):
                client.refresh_endpoint()
            finally:
                if stream is not None:
                    await asyncio.to_thread(stream.close)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


@dataclass
class RemoteEventSubscription:
    """A local-daemon subscription retaining its carrier lease until closed."""

    lease: CarrierLease
    subscription: CarrierSubscription
    last_acked_id: int
    continuity_id: str | None

    async def get(self) -> Envelope:
        return await self.subscription.get()

    async def close(self) -> None:
        try:
            await self.subscription.close()
        finally:
            await self.lease.release()


class RemoteOperationService:
    """Local daemon owner for authenticated remote Bridge operations."""

    def __init__(
        self,
        resolver: AgentResolver,
        *,
        manager: ConnectionManager | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self._resolver = resolver
        self._manager = manager
        self._request_timeout = request_timeout

    async def _lease(self, host: str) -> CarrierLease:
        from .carrier import acquire_remote_carrier

        host = validate_host(host)
        try:
            machine, environment = self._resolver.resolve_ssh_environment(host)
        except ValueError as exc:
            raise RemoteBridgeError(404, "host_not_found", str(exc)) from exc
        environments = list(getattr(machine, "ssh_environments", ()))
        if host == getattr(machine, "key", None) and len(environments) > 1:
            alias_matches = [
                item for item in environments if item.alias == host
            ]
            if len(alias_matches) == 1:
                environment = alias_matches[0]
            else:
                raise RemoteBridgeError(
                    400,
                    "ambiguous_host",
                    (
                        f"machine '{host}' has multiple SSH environments; "
                        "use an exact SSH alias"
                    ),
                    details={
                        "ssh_aliases": [
                            item.alias for item in environments if item.alias
                        ]
                    },
                )
        shell = environment.shell.strip().casefold()
        name = environment.name.strip().casefold()
        remote_platform = (
            "windows"
            if name == "windows"
            or shell in {"pwsh", "powershell", "powershell.exe", "cmd", "cmd.exe"}
            else "linux"
        )
        source = SSHProfileSource(
            host_alias=environment.alias,
            user=environment.user,
            port=environment.port,
        )
        try:
            return await acquire_remote_carrier(
                f"carrier:{environment.alias}",
                remote_platform,
                config_source=source,
                manager=self._manager,
            )
        except Exception as exc:
            reconnectable = bool(getattr(exc, "reconnectable", False))
            raise RemoteBridgeError(
                503,
                "carrier_unavailable",
                str(exc),
                reconnectable=reconnectable,
            ) from exc

    async def _request(
        self, host: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        lease = await self._lease(host)
        deadline = time.monotonic() + self._request_timeout
        try:
            while True:
                try:
                    response = await lease.carrier.request(
                        {
                            "version": REMOTE_OPERATION_VERSION,
                            **payload,
                        },
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                    return dict(response.payload)
                except CarrierRemoteError as exc:
                    raise RemoteBridgeError.from_carrier(exc) from exc
                except CarrierUnavailable as exc:
                    if not exc.reconnectable or time.monotonic() >= deadline:
                        raise RemoteBridgeError(
                            503,
                            "carrier_unavailable",
                            str(exc),
                            reconnectable=exc.reconnectable,
                        ) from exc
                    try:
                        await asyncio.wait_for(
                            lease.carrier.ensure_started(),
                            timeout=max(
                                0.1, deadline - time.monotonic()
                            ),
                        )
                    except CarrierUnavailable as reconnect_exc:
                        if (
                            not reconnect_exc.reconnectable
                            or time.monotonic() >= deadline
                        ):
                            raise RemoteBridgeError(
                                503,
                                "carrier_unavailable",
                                str(reconnect_exc),
                                reconnectable=reconnect_exc.reconnectable,
                            ) from reconnect_exc
                    except (TimeoutError, asyncio.TimeoutError) as reconnect_exc:
                        raise RemoteBridgeError(
                            504,
                            "remote_operation_timeout",
                            "the remote carrier did not reconnect in time",
                            reconnectable=True,
                        ) from reconnect_exc
                except (TimeoutError, asyncio.TimeoutError) as exc:
                    raise RemoteBridgeError(
                        504,
                        "remote_operation_timeout",
                        "the remote Bridge operation timed out",
                        reconnectable=True,
                    ) from exc
        finally:
            await lease.release()

    async def session_status(
        self, host: str, session_id: str, caller_id: str
    ) -> dict[str, Any]:
        response = await self._request(
            host,
            {
                "operation": "session.status",
                "session_id": validate_session_id(session_id),
                "caller_id": validate_caller_id(caller_id),
            },
        )
        result = response.get("result")
        return dict(result) if isinstance(result, dict) else {}

    async def resolve_live_session(
        self, host: str, session_id: str
    ) -> dict[str, Any]:
        response = await self._request(
            host,
            {
                "operation": "live_session.resolve",
                "session_id": validate_session_id(session_id),
            },
        )
        result = response.get("result")
        return dict(result) if isinstance(result, dict) else {}

    async def acknowledge(
        self,
        host: str,
        session_id: str,
        *,
        caller_id: str,
        last_id: int,
        continuity_id: str | None,
    ) -> int:
        continuity_id = validate_continuity_id(continuity_id)
        response = await self._request(
            host,
            {
                "operation": "session.events.ack",
                "session_id": validate_session_id(session_id),
                "caller_id": validate_caller_id(caller_id),
                "last_id": _validate_cursor(last_id, field="last_id"),
                "continuity_id": continuity_id,
            },
        )
        return int(response.get("last_acked_id") or 0)

    async def subscribe_events(
        self,
        host: str,
        session_id: str,
        *,
        caller_id: str,
        after: int | None = None,
        continuity_id: str | None = None,
    ) -> RemoteEventSubscription:
        continuity_id = validate_continuity_id(continuity_id)
        lease = await self._lease(host)
        subscription: CarrierSubscription | None = None
        ownership_transferred = False
        try:
            subscription = await lease.carrier.subscribe(
                {
                    "version": REMOTE_OPERATION_VERSION,
                    "operation": "session.events",
                    "session_id": validate_session_id(session_id),
                    "caller_id": validate_caller_id(caller_id),
                    "continuity_id": continuity_id,
                },
                replayable=True,
                progress_timeout=45.0,
                position=_validate_cursor(after),
                retain_buffered_on_reconnect=False,
            )
            accepted = await asyncio.wait_for(
                subscription.get(), timeout=self._request_timeout
            )
            if accepted.type is not EnvelopeType.RESPONSE:
                raise RemoteBridgeError(
                    502,
                    "invalid_remote_response",
                    "remote event subscription was not acknowledged",
                )
            subscription.position = None
            result = RemoteEventSubscription(
                lease=lease,
                subscription=subscription,
                last_acked_id=int(
                    accepted.payload.get("last_acked_id") or 0
                ),
                continuity_id=accepted.payload.get("continuity_id"),
            )
            ownership_transferred = True
            return result
        except CarrierRemoteError as exc:
            raise RemoteBridgeError.from_carrier(exc) from exc
        except CarrierUnavailable as exc:
            raise RemoteBridgeError(
                503,
                "carrier_unavailable",
                str(exc),
                reconnectable=exc.reconnectable,
            ) from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise RemoteBridgeError(
                504,
                "remote_operation_timeout",
                "the remote event subscription was not acknowledged in time",
                reconnectable=True,
            ) from exc
        finally:
            if not ownership_transferred:
                try:
                    if subscription is not None:
                        await subscription.close()
                finally:
                    await lease.release()
