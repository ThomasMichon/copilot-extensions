"""Cooperative ``agent-bridge stop``: phases, a grace window, and ``--force``.

``stop <session> --grace S`` first asks the agent to wind down, then enforces:

1. ``requested`` -- a wind-down notice is submitted with ``--queue`` semantics:
   it starts a turn on an idle session, or waits durably behind the running
   turn (it never interrupts one).
2. ``acknowledged`` -- the notice's turn ran and the session settled, within
   ``S`` seconds. An agent that ignores it, or never gets to it, is stopped
   anyway when ``S`` runs out; a notice still queued then is withdrawn, so it
   can't surface when the session is later resumed.
3. ``provider_stopped`` -- the provider stop (``POST /sessions/{id}/stop``).
4. ``confirmed`` -- the session reads ``stopped`` (or is gone).

``--force`` skips the notice and the grace (and kills background sub-agent
tasks). Stopping a session that is already stopped or gone is a no-op success,
so repeating a stop is idempotent.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

STOP_NOTICE = (
    "[agent-bridge stop] This session is being stopped. Wind down now: cancel any "
    "scheduled or looping work you started, save anything you still need, and end "
    "your turn. Don't start new work."
)

#: Exit codes: busy matches ``send``'s (a background sub-agent task blocks a
#: non-forced stop); an unconfirmed stop is a plain failure.
STOP_BUSY_EXIT = 75
STOP_UNCONFIRMED_EXIT = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _status(client: Any, session_id: str) -> dict[str, Any] | None:
    """The session record, or ``None`` when the bridge no longer knows it."""
    from .client import BridgeClientError

    try:
        return client.get_session(session_id) or None
    except BridgeClientError as exc:
        if exc.status == 404:
            return None
        raise


def run_stop(
    client: Any,
    session_id: str,
    *,
    grace: float | None = None,
    force: bool = False,
    reap_host: bool = False,
    confirm_timeout: float = 30.0,
    poll: float = 1.0,
    on_phase: Callable[[str], None] | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Run one stop and return its result document (see the module docstring)."""
    from .client import BridgeClientError

    clock, sleep = clock or time.monotonic, sleep or time.sleep
    result: dict[str, Any] = {"session_id": session_id, "outcome": None,
                              "acknowledged": None, "notice": None, "phases": []}

    def phase(name: str) -> None:
        result["phases"].append({"phase": name, "at": _now_iso()})
        if on_phase:
            on_phase(name)

    session = _status(client, session_id)
    if session is None or (session.get("status") == "stopped" and not reap_host):
        phase("confirmed")
        result["outcome"] = "already_stopped"
        return result

    phase("requested")
    if grace is not None and not force and session.get("status") in ("idle", "running"):
        _cooperate(client, session_id, session, grace, result, phase, clock=clock, sleep=sleep, poll=poll)

    try:
        client.stop_session(session_id, force=force, reap_host=reap_host)
    except BridgeClientError as exc:
        if exc.status == 409:
            result.update(outcome="refused_busy", error=str(exc.detail))
            return result
        if exc.status != 404:
            raise
    phase("provider_stopped")

    deadline = clock() + confirm_timeout
    while True:
        session = _status(client, session_id)
        if session is None or session.get("status") == "stopped":
            phase("confirmed")
            result["outcome"] = "stopped"
            return result
        if clock() >= deadline:
            result.update(outcome="unconfirmed",
                          error=f"session still reads {session.get('status')!r} after the provider stop")
            return result
        sleep(poll)


def _cooperate(client, session_id, session, grace, result, phase, *, clock, sleep, poll) -> None:
    """Submit the notice and wait up to ``grace`` for its turn to settle.

    A queued notice is popped before its turn is marked running, so a status
    read in between can show an idle session with the notice already gone. So
    the dequeue has to be seen on one poll, and the settled session on a later
    one, before it counts as acknowledged."""
    from .client import BridgeClientError

    baseline = int(session.get("turn_count") or 0)
    submitted = client.submit_prompt(session_id, STOP_NOTICE, queue=True)
    queue_id = submitted.get("queue_id") if submitted.get("queued") else None
    result["notice"] = {"queued": queue_id is not None, "queue_id": queue_id, "withdrawn": False}
    dequeued_before = queue_id is None  # an immediate notice is running on return
    deadline = clock() + grace
    while True:
        try:
            dequeued_now = dequeued_before or not any(
                p.get("id") == queue_id for p in client.list_pending_queue(session_id))
        except BridgeClientError as exc:
            if exc.status != 404:
                raise
            break  # the session disappeared during the grace window
        session = _status(client, session_id)
        if session is None:
            break
        if (dequeued_before and session.get("status") == "idle"
                and int(session.get("turn_count") or 0) > baseline):
            result["acknowledged"] = True
            phase("acknowledged")
            return
        dequeued_before = dequeued_now
        if clock() >= deadline:
            break
        sleep(poll)
    result["acknowledged"] = False
    if queue_id is not None:
        try:
            client.remove_pending_prompt(session_id, queue_id)
            result["notice"]["withdrawn"] = True
        except BridgeClientError as exc:
            if exc.status != 404:  # 404: it was dispatched meanwhile; the stop cancels its turn
                raise
