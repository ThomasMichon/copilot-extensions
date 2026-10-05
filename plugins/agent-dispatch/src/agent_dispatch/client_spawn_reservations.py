"""``list_reservations``/``get_reservation`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend` /
:mod:`agent_dispatch.client_completion_review`) rather than grow an
already-baselined file.
"""

from __future__ import annotations

from typing import Any


class SpawnReservationClientMixin:
    """``list_reservations``/``get_reservation``, mixed into
    ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
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
        this for).
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
        if latest_only:
            params["latest_only"] = latest_only
        return self._unwrap(self._http.get("/spawn-reservations", params=params))

    def get_reservation(self, key: str) -> dict:
        return self._unwrap(self._http.get(f"/spawn-reservations/{key}"))
