"""Default connect-retry transport for ``DispatchClient``.

Builds the ``httpx.HTTPTransport`` ``DispatchClient`` uses by default when no
transport is explicitly supplied, adding bounded connection-level retries.
Split out of ``client.py`` to keep that module under its grandfathered
module-size ceiling.
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
    window: a caller that first confirms a coordinator is live (e.g. the CLI's
    own ``has_live_local_coordinator()``) immediately before constructing a
    client can still land in the gap between that check and this request's
    actual connect if the coordinator cycles (e.g. a self-retire-on-
    supersession cutover) in between -- producing a bare ``ConnectError`` with
    no automatic recovery, even though nothing was ever sent to the old
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
    TLS decision once a custom transport is in play. ``trust_env=True`` is
    httpx's own default and is passed explicitly (rather than left implicit)
    so this transport keeps honoring ``HTTP_PROXY``/``HTTPS_PROXY``/``NO_PROXY``
    exactly as ``httpx.Client()``'s own default transport would -- supplying a
    transport bypasses Client's internal default-transport construction, so
    nothing here may silently drop a behavior that path would have kept."""
    return httpx.HTTPTransport(
        verify=verify, retries=default_connect_retries(), trust_env=True
    )
