"""Reservation deferral before a worker or carried allocation is admitted."""

from __future__ import annotations

from .queue_records import SpawnReservation, SpawnState, Status, TaskError
from .queue_spawn_cooling import validate_cooling_publication


class SpawnDeferralMixin:
    def defer_spawn(
        self, key: str, *, detail: str | None = None, now: float | None = None,
        expected_generation: int | None = None,
        startup_revision: int | None = None, cooling_token: str | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``deferred``: the spawn declined, not failed,
        because a carried session (same exclusive key) was confirmed still
        live/busy. Releases the task so a fresh attempt may be reserved
        immediately, but -- unlike :meth:`fail_spawn` -- never counts toward
        dead-lettering, since no attempt actually failed. Restricted startup
        can defer only a generation-fenced, still-unclaimed task before any
        delivery receipt, allocation or body exists."""
        def guard(row):
            if row["state"] in {SpawnState.SPAWNED, SpawnState.COLD}:
                if not row["startup_complete"]:
                    raise TaskError("previous startup remains incomplete")
                if type(startup_revision) is not int or row["startup_revision"] != startup_revision:
                    raise TaskError("deferral requires the current startup revision")
                validate_cooling_publication(row, cooling_token, startup_revision)
                with self._connect() as probe:
                    task = self._fetch(probe, row["task_id"])
                if (
                    row["state"] != SpawnState.COLD or not cooling_token
                    or type(expected_generation) is not int
                    or task is None or task.status != Status.QUEUED
                    or task.owner is not None or task.owner_session_id is not None
                    or task.generation != expected_generation
                ):
                    raise TaskError("cold deferral requires the same unclaimed task and cooling guard")
                return
            if row["driver"] != "restricted-bridge":
                if expected_generation is not None:
                    raise TaskError("generation-fenced deferral is restricted-startup only")
                return
            with self._connect() as probe:
                task = self._fetch(probe, row["task_id"])
            if (
                type(expected_generation) is not int
                or row["state"] != SpawnState.RESERVING
                or row["agent_info"] or row["session_handle"]
                or row["worktree"] or row["allocation_project"]
                or row["conversation_retired"] or row["release_requested"]
                or task is None or task.status != Status.QUEUED
                or task.owner is not None or task.owner_session_id is not None
                or task.generation != expected_generation
            ):
                raise TaskError("restricted deferral requires the same unclaimed task and empty reservation")

        return self._update_reservation(
            key,
            to_state=SpawnState.DEFERRED,
            allowed_from=(SpawnState.ACTIVE - frozenset({SpawnState.RELEASING})),
            detail=detail,
            now=now,
            guard=guard,
            expected_startup_revision=startup_revision,
            cooling_token=cooling_token,
        )
