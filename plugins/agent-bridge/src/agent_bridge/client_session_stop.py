"""``BridgeClient``'s session-stop and pending-queue calls.

Extracted out of ``client.py`` (at its grandfathered module-size ceiling),
following ``client_worktree_restart.py``'s precedent. The queue calls let a
cooperative ``stop --grace`` withdraw its own wind-down notice if it never ran,
so a stale notice can't surface when the session is later resumed.
"""

from __future__ import annotations

from typing import Any

from .client_worktree_restart import _Base


class SessionStopClientMixin(_Base):
    """Mixin supplying ``BridgeClient``'s stop and pending-queue calls."""

    def stop_session(
        self, session_id: str, *, force: bool = False, reap_host: bool = False
    ) -> None:
        """POST /api/v1/sessions/{id}/stop

        ``force`` maps to the route's ``?force=true`` -- tear down even with
        active background sub-agent tasks (they are killed). See #191.

        ``reap_host`` maps to ``?reap_host=true`` -- additionally FREE the
        Session-Host child immediately instead of only detaching it (the
        idle-reaper primitive). The session stays STOPPED and resumable via
        ``load_session`` replay; use it when the caller never reattaches over
        the bridge and wants the ~280 MB child reclaimed on the spot rather than
        after the idle-reaper TTL (#2960).
        """
        params: dict[str, str] = {}
        if force:
            params["force"] = "true"
        if reap_host:
            params["reap_host"] = "true"
        self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/stop",
            params=params or None,
        )

    def list_pending_queue(self, session_id: str) -> list[dict[str, Any]]:
        """GET /api/v1/sessions/{id}/queue -- the durable pending prompts, FIFO."""
        return list((self._request("GET", f"/api/v1/sessions/{session_id}/queue") or {}).get("pending") or [])

    def submit_stop_notice(self, session_id: str, prompt: str) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/turns with ``queue`` and ``no_resume``: run or
        queue the wind-down notice, but never resume a session that a concurrent
        stop already stopped (409 ``session_stopped``). Needs the daemon's
        ``COOPERATIVE_STOP_PROTOCOL_VERSION``; an older one ignores ``no_resume``."""
        payload = {"prompt": prompt, "queue": True, "no_resume": True}
        return self._request("POST", f"/api/v1/sessions/{session_id}/turns", payload) or {}

    def remove_pending_prompt(self, session_id: str, queue_id: int) -> None:
        """DELETE /api/v1/sessions/{id}/queue/{queue_id} -- drop one queued prompt."""
        self._request("DELETE", f"/api/v1/sessions/{session_id}/queue/{queue_id}")
