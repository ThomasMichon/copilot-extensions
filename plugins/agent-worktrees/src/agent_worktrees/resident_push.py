"""Best-effort bridge from a ``tracking_write`` verb handler (running inside
the resident status-monitor process) to that same process's live
segment-cache/wake-event, for an immediate (push) status-bar refresh instead
of waiting up to ``--interval`` seconds for the next periodic sweep.

``cmd_status_monitor`` (``status_monitor_cli.py``) calls :func:`bind` once at
startup with its own ``segment_cache``/wake ``threading.Event``. A verb
handler that runs IN-PROCESS inside the daemon when it serves a request (e.g.
``tracking_disposition_write.py``'s ``apply_status_disposition``, invoked via
``tracking_write.dispatch``) calls :func:`notify` after a successful write so
OTHER sessions' status bars refresh on the monitor's very next loop iteration
instead of waiting out the full backstop interval. A no-op before
:func:`bind` runs (e.g. the in-process fallback path with no resident daemon
reachable, or a bare unit test) -- always best-effort, never raises.
"""

from __future__ import annotations

import threading

_wake_event: threading.Event | None = None
_segment_cache = None


def bind(wake_event: threading.Event, segment_cache) -> None:
    """Register this process's resident monitor state (called once, at
    ``cmd_status_monitor`` startup)."""
    global _wake_event, _segment_cache
    _wake_event = wake_event
    _segment_cache = segment_cache


def reset() -> None:
    """Clear any bound state (test isolation; also safe as a defensive
    no-op if a future shutdown path wants to un-bind explicitly)."""
    global _wake_event, _segment_cache
    _wake_event = None
    _segment_cache = None


def notify(path: str | None) -> None:
    """Invalidate ``path``'s cached segment and wake the sweep loop early.

    Best-effort; a no-op when unbound. Never raises -- a verb transaction's
    own success must never depend on this purely-advisory side effect.
    """
    try:
        if path and _segment_cache is not None:
            _segment_cache.invalidate(path)
        if _wake_event is not None:
            _wake_event.set()
    except Exception:
        pass
