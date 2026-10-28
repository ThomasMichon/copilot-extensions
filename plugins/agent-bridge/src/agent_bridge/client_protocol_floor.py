"""Per-attempt protocol fences for versioned requests across daemon cutover."""

from __future__ import annotations

import time
from functools import partial
from typing import Any, Callable

_Request = Callable[..., dict[str, Any] | None]


def negotiate_http_protocol(
    peer_version: int, peer_minimum: int, *, required: int = 1,
) -> int | None:
    """Select the highest shared HTTP contract supporting the required feature."""
    from .protocol import HTTP_PROTOCOL_MIN_SUPPORTED, HTTP_PROTOCOL_VERSION

    if peer_version < 1 or peer_minimum < 0 or peer_minimum > peer_version:
        return None
    low = max(HTTP_PROTOCOL_MIN_SUPPORTED, peer_minimum, required)
    high = min(HTTP_PROTOCOL_VERSION, peer_version)
    return high if low <= high else None


def connection_refused(exc: BaseException) -> bool:
    """Distinguish a refused connection from an ambiguously delivered request."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ConnectionRefusedError) or isinstance(
            getattr(cur, "reason", None), ConnectionRefusedError,
        ):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def protocol_floored_request(
    client: Any, floor: int, *, capabilities: dict[str, int] | None = None,
) -> _Request:
    """Compose with an existing floor without hiding lower-floor retry loops."""
    request = client._request
    required = dict(capabilities or {})
    if isinstance(request, partial) and request.func is _request_with_floor:
        floor = max(floor, request.keywords["floor"])
        for name, version in request.keywords["capabilities"].items():
            required[name] = max(version, required.get(name, 0))
        request = request.keywords["request"]
    return partial(
        _request_with_floor, client=client, floor=floor, request=request,
        capabilities=required,
    )


def _request_with_floor(
    method: str, path: str, *args: Any,
    client: Any, floor: int, request: _Request, capabilities: dict[str, int],
    **kwargs: Any,
) -> dict[str, Any] | None:
    from .client import BridgeClientError, BridgeConnectionError
    from .protocol import HTTP_PROTOCOL_MIN_SUPPORTED, HTTP_PROTOCOL_VERSION

    resolve = getattr(client, "_reresolve", None)
    deadline = time.monotonic() + getattr(client, "_connect_grace", 0.0)
    allow_retry = kwargs.get("allow_retry", True)

    def attempt(*a: Any, **k: Any) -> dict[str, Any] | None:
        # Native retries cannot bypass the fresh protocol check between attempts.
        k["allow_retry"] = False
        return request(*a, **k)

    retrying = False
    last_error: Exception | None = None
    while True:
        client._daemon_proto = None
        if retrying:
            time.sleep(0.5)
            base = resolve() if resolve else None
            if base:
                client._base = base.rstrip("/")
        try:
            health = attempt("GET", "/health") or {}
            version = int(health.get("protocol_version") or 0)
            peer_minimum = int(health.get("min_protocol_version") or 0)
        except (BridgeConnectionError, OSError, TypeError, ValueError):
            unanswered = BridgeConnectionError(
                f"the bridge daemon at {client._base} didn't answer a protocol check in time; "
                "the request was not sent",
            )
            if time.monotonic() >= deadline:
                raise unanswered from None
            retrying, last_error = True, last_error or unanswered
            continue
        if version < floor:
            raise BridgeClientError(
                426, f"the bridge daemon now at {client._base} predates protocol "
                f"{floor}; not sending the request there",
            )
        if negotiate_http_protocol(version, peer_minimum, required=floor) is None:
            raise BridgeClientError(
                426, "no compatible agent-bridge HTTP contract range "
                f"(client {HTTP_PROTOCOL_MIN_SUPPORTED}-{HTTP_PROTOCOL_VERSION}, "
                f"daemon {peer_minimum}-{version}, required {floor}); "
                "the request was not sent",
            )
        advertised = health.get("capabilities")
        for name, minimum in capabilities.items():
            actual = advertised.get(name) if isinstance(advertised, dict) else None
            if type(actual) is not int or actual < minimum:
                raise BridgeClientError(
                    426, f"the bridge daemon now at {client._base} does not support "
                    f"required capability {name} version {minimum}; "
                    "the request was not sent",
                )
        if last_error is not None and time.monotonic() >= deadline:
            raise last_error
        try:
            return attempt(method, path, *args, **kwargs)
        except BridgeConnectionError as exc:
            if not allow_retry or time.monotonic() >= deadline or (
                method not in ("GET", "HEAD") and not connection_refused(exc)
            ):
                raise
            retrying, last_error = True, exc
        except BridgeClientError as exc:
            detail = str(exc.detail).lower()
            retryable = (
                "drain" in detail or "initializing" in detail
                or (method in ("GET", "HEAD") and "merging" in detail)
            )
            if not allow_retry or exc.status != 503 or not retryable \
                    or time.monotonic() >= deadline:
                raise
            retrying, last_error = True, exc
