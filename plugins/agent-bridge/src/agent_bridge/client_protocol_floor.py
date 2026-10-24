"""Per-attempt protocol fences for versioned requests across daemon cutover."""

from __future__ import annotations

import time
from functools import partial
from typing import Any, Callable

_Request = Callable[..., dict[str, Any] | None]


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

    resolve = getattr(client, "_reresolve", None)
    deadline = time.monotonic() + getattr(client, "_connect_grace", 0.0)

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
            if time.monotonic() >= deadline or (
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
            if exc.status != 503 or not retryable \
                    or time.monotonic() >= deadline:
                raise
            retrying, last_error = True, exc
