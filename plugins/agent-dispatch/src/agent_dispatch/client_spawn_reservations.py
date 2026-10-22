"""``list_reservations``/``get_reservation`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend` /
:mod:`agent_dispatch.client_completion_review`) rather than grow an
already-baselined file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class SpawnReservationClientMixin:
    """``list_reservations``/``get_reservation``, mixed into
    ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    _http: Any
    _unwrap: Callable[[Any], Any]

    def fence_spawn_startup(self, key: str, *, startup_revision: int) -> dict:
        """Close completed startup admission while retaining resource ownership."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/startup-fence",
            json={"startup_revision": startup_revision},
        ))

    def record_spawn_allocation(self, key: str, project: str) -> dict:
        """Persist the selected execution repository before allocation effects."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/allocation", json={"project": project},
        ))

    def record_created_agent(
        self, key: str, *, task_generation: int, agent_info: dict,
        worktree_record: dict | None = None, startup_invocation: dict | None = None,
    ) -> dict:
        """Persist ordinary Bridge readiness before any first task prompt."""
        body = {"task_generation": task_generation, "agent_info": agent_info}
        if worktree_record is not None:
            body["worktree_record"] = worktree_record
        if startup_invocation is not None:
            body["startup_invocation"] = startup_invocation
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/created-agent",
            json=body,
        ))

    def record_resumed_agent(
        self, key: str, *, task_generation: int, session_info: dict, worktree_record: dict,
        startup_invocation: dict | None = None,
    ) -> dict:
        """Store actual resumed SessionInfo without asserting a new creation."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/resumed-agent",
            json={"task_generation": task_generation, "session_info": session_info,
                  "worktree_record": worktree_record,
                  **({"startup_invocation": startup_invocation} if startup_invocation is not None else {})},
        ))

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        task_status: str | None = None,
        latest_only: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        """List spawn reservations, newest first, optionally filtered.

        ``latest_only`` restricts the result to each task's single
        highest-``attempt`` reservation row -- see
        :meth:`agent_dispatch.queue_claim_queries.QueueClaimQueriesMixin
        .list_reservations` for why this matters: without it, a retried
        task's older, already-superseded failed attempts can crowd a busy
        fleet's bounded ``limit`` and hide an older, genuinely-still-stuck
        task further back in the newest-first ordering (the exact gap
        :func:`agent_dispatch.doctor.find_stuck_queued_reservations` needs
        this for). ``task_status`` additionally filters to the owning
        task's current status (e.g. ``"queued"``) -- even with duplicate
        attempts collapsed, a non-queued task's current ``FAILED``
        reservation would otherwise still consume the same bounded
        ``limit``.
        """
        params: dict[str, Any] = {"limit": limit}
        if task_id is not None:
            params["task_id"] = task_id
        if state is not None:
            params["state"] = state
        if repo is not None:
            params["repo"] = repo
        if label is not None:
            params["label"] = label
        if conclusion_state is not None:
            params["conclusion_state"] = conclusion_state
        if resume_requested is not None:
            params["resume_requested"] = resume_requested
        if task_status is not None:
            params["task_status"] = task_status
        if latest_only:
            params["latest_only"] = latest_only
        return self._unwrap(self._http.get("/spawn-reservations", params=params))

    def get_reservation(self, key: str) -> dict:
        return self._unwrap(self._http.get(f"/spawn-reservations/{key}"))
