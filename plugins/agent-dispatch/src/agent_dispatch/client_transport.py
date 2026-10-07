"""Connect-retry wrapper around ``httpx.Client`` for ``DispatchClient``.

Split out of ``client.py`` to keep that module under its grandfathered
module-size ceiling.
"""

from __future__ import annotations

import os
import time

import httpx


def default_connect_retries() -> int:
    """Default connection-level retries every ``DispatchClient`` request gets.
    Scoped to *connection* failures only -- refused/reset/unreachable before
    any byte of the request reached the server -- never a request that
    already arrived (a slow/hung response past ``timeout`` is a read failure,
    not a connect failure, and is never retried here). That is the
    safe-to-retry window: a caller that first confirms a coordinator is live
    (e.g. the CLI's own ``has_live_local_coordinator()``) immediately before
    constructing a client can still land in the gap between that check and
    this request's actual connect if the coordinator cycles (e.g. a
    self-retire-on-supersession cutover) in between -- producing a bare
    ``ConnectError`` with no automatic recovery, even though nothing was ever
    sent to the old generation and retrying against the new one is always
    safe. Overridable via ``AGENT_DISPATCH_HTTP_CONNECT_RETRIES``.
    """
    try:
        return max(0, int(os.environ.get("AGENT_DISPATCH_HTTP_CONNECT_RETRIES", "2")))
    except (TypeError, ValueError):
        return 2


class ConnectRetryClient(httpx.Client):
    """``httpx.Client`` that retries a bare connect failure a bounded number
    of times before giving up.

    Overrides ``send()`` -- the single choke point every public method
    (``get``/``post``/``request``/...) routes through -- instead of supplying
    a custom ``transport``. Passing an explicit ``transport`` to ``httpx.
    Client`` disables its own environment-proxy discovery entirely
    (``allow_env_proxies = trust_env and transport is None``), silently
    breaking ``HTTP_PROXY``/``HTTPS_PROXY``/``NO_PROXY`` for a remote/shared
    coordinator. Retrying here instead leaves Client's normal transport and
    proxy-mount construction completely untouched -- this class changes only
    how many times a bare ``httpx.ConnectError`` is retried, never how a
    request is built or routed.
    """

    def __init__(self, *args, connect_retries: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._connect_retries = (
            connect_retries if connect_retries is not None else default_connect_retries()
        )

    def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        attempts = self._connect_retries + 1
        delay = 0.1
        for attempt in range(attempts):
            try:
                return super().send(request, **kwargs)
            except httpx.ConnectError:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
        raise AssertionError("unreachable")  # pragma: no cover
