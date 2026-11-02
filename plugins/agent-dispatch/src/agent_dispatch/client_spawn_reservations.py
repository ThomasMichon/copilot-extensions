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

    def record_spawn_worktree(
        self, key: str, worktree: str, *, ownership: str = "unknown",
        creating_host: str | None = None, execution_machine: str | None = None,
        driver: str | None = None, allocation_project: str | None = None,
        worktree_path: str | None = None,
    ) -> dict:
        """Retain workspace provenance and its independent execution machine."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/worktree",
            json={
                "worktree": worktree, "ownership": ownership,
                "creating_host": creating_host, "driver": driver,
                "allocation_project": allocation_project, "worktree_path": worktree_path,
                **({"execution_machine": execution_machine} if execution_machine is not None else {}),
            },
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
        native_release_pending: bool | None = None,
        pool_accountable: bool = False,
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
        if native_release_pending is not None:
            params["native_release_pending"] = native_release_pending
        if pool_accountable:
            params["pool_accountable"] = True
        if resume_requested is not None:
            params["resume_requested"] = resume_requested
        if task_status is not None:
            params["task_status"] = task_status
        if latest_only:
            params["latest_only"] = latest_only
        response = self._http.get("/spawn-reservations", params=params)
        reservations = self._unwrap(response)
        if native_release_pending is not None:
            from .client import DispatchError
            if response.headers.get("X-Dispatch-Reservation-Query") != "native-release-pending/v1":
                raise DispatchError(426, "coordinator lacks native-release-pending/v1 reservation query")
            if not isinstance(reservations, list) or any(
                not isinstance(row, dict) or row.get("native_release_pending") is not native_release_pending
                for row in reservations
            ):
                raise DispatchError(426, "coordinator did not apply native-release-pending/v1 reservation query")
        if pool_accountable:
            from .client import DispatchError
            if response.headers.get("X-Dispatch-Pool-Accountability") != "pool-accountable/v1":
                raise DispatchError(426, "coordinator lacks pool-accountable/v1 reservation query")
            if not isinstance(reservations, list) or any(
                not isinstance(row, dict) or row.get("pool_accountable") is not True
                for row in reservations
            ):
                raise DispatchError(426, "coordinator did not apply pool-accountable/v1 reservation query")
        return reservations

    def get_reservation(self, key: str) -> dict:
        return self._unwrap(self._http.get(f"/spawn-reservations/{key}"))
