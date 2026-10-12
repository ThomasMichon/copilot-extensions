"""``stream_events`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_exclude`/``client_suspend``)
rather than grow an already-baselined file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx


class EventStreamClientMixin:
    """``stream_events``, mixed into ``DispatchClient``.

    Relies on ``self._http`` from the composing class. ``DispatchError`` is
    imported lazily (inside the method, not at module scope) since it is
    defined in ``client.py`` itself -- that module is the one importing
    this mixin, so a module-level import here would be circular.
    """

    def stream_events(self, *, ready_frame: bool = False) -> Iterator[dict]:
        """Yield task events from the coordinator's SSE stream (blocking).

        ``ready_frame=False`` (every existing caller, e.g. ``agent-dispatch
        watch``) is byte-for-byte unchanged: no ``ready_frame`` query param is
        ever sent, so no daemon -- old or new -- ever emits that control
        frame to this call, and any ``type: "ready"`` frame that somehow
        still arrives is filtered here regardless, never forwarded to the
        caller. ``ready_frame=True`` (the agent-dispatch CLI relay,
        exclusively -- see ``board_relay.py``) asks the daemon to emit that
        frame immediately after subscription registration and yields it to
        the caller as the first item -- the daemon-side
        registration-vs-real-event race this handshake exists to close. The
        read timeout is unbounded for this one long-lived GET (the
        coordinator emits no periodic keepalive and a quiet board would
        otherwise trip httpx's shared default timeout mid-stream)."""
        from .client import DispatchError

        params = {"ready_frame": "1"} if ready_frame else {}
        timeout = httpx.Timeout(10.0, read=None)
        with self._http.stream(
            "GET", "/events", params=params, timeout=timeout
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise DispatchError(resp.status_code, resp.text)
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[len("data:") :].strip())
                if payload.get("type") == "ready":
                    if ready_frame:
                        yield payload
                        continue
                    # Filtered for every other caller, regardless of whether
                    # this request itself asked for it -- the filtering lives
                    # here, not only in the relay's own consumer, so a future
                    # control frame can never leak to `agent-dispatch watch`
                    # or any other existing `stream_events()` consumer.
                    continue
                yield payload
