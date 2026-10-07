"""Default connect-retry transport for ``DispatchClient``.

Extracted from ``client.py`` to keep that module under its grandfathered
module-size ceiling (mechanical extraction, no behavior change).
"""

from __future__ import annotations

import os

import httpx


def default_connect_retries() -> int:
    """Default connection-level retries for every request ``DispatchClient``
    makes (``httpx.HTTPTransport(retries=...)``). This is narrowly scoped to
    *connection* failures -- refused/reset/unreachable before any byte of the
    request body was sent -- never to a request that reached the server (a
    slow/hung response past ``timeout`` is a read failure, not a connect
    failure, and is never retried here). That is exactly the safe-to-retry
    window: the CLI's own ``_client()`` already confirms a coordinator is live
    (``has_live_local_coordinator()``) immediately before constructing a
    client, but a coordinator self-retire-on-supersession cutover can land in
    the gap between that check and this request's actual connect, producing a
    bare ``ConnectError`` the operator sees as an opaque, silent "Steer could
    not be delivered" (odsp-web-harness steering-card delivery-failure report)
    with no automatic recovery -- even though nothing was ever sent to the old
    generation and retrying against the new one is always safe. Overridable
    for tests (an explicit ``transport=`` skips this default entirely) and via
    ``AGENT_DISPATCH_HTTP_CONNECT_RETRIES`` for an operator who wants it tuned.
    """
    try:
        return max(0, int(os.environ.get("AGENT_DISPATCH_HTTP_CONNECT_RETRIES", "2")))
    except (TypeError, ValueError):
        return 2


def default_transport(*, verify: bool) -> httpx.HTTPTransport:
    """Build the transport ``DispatchClient`` uses when the caller doesn't
    supply its own (tests, a mock). Bakes `verify` in here since it owns the
    TLS decision once a custom transport is in play."""
    return httpx.HTTPTransport(verify=verify, retries=default_connect_retries())
