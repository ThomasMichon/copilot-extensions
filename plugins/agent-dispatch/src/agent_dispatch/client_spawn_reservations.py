"""Spawn-reservation reads, lifecycle records and legacy-history
reconciliation (fence/adopt) for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend` /
:mod:`agent_dispatch.client_completion_review`) rather than grow an
already-baselined file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


class SpawnReservationClientMixin:
    """``list_reservations``/``get_reservation``, mixed into
    ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    _http: Any
    _unwrap: Callable[[Any], Any]
    _control_headers: Callable[[], dict]
    pool_placement_contract: Callable[[], dict]

    def fence_spawn_startup(self, key: str, *, startup_revision: int) -> dict:
        """Close completed startup admission while retaining resource ownership."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/startup-fence",
            json={"startup_revision": startup_revision},
        ))

    def record_spawn_allocation(
        self, key: str, project: str, *, worktree_writer_requirement: dict | None = None,
    ) -> dict:
        """Persist the selected execution repository before allocation effects."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/allocation", json={
                "project": project,
                **({"worktree_writer_requirement": worktree_writer_requirement}
                   if worktree_writer_requirement is not None else {}),
            },
        ))

    def record_spawn_worktree(
        self, key: str, worktree: str, *, ownership: str = "unknown",
        creating_host: str | None = None, execution_machine: str | None = None,
        driver: str | None = None, allocation_project: str | None = None,
        worktree_path: str | None = None,
        creation_nonce: str | None = None,
        worktree_writer_requirement: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Retain workspace provenance and its independent execution machine."""
        if creation_nonce is not None:
            from .client_task_relationships import require_support
            require_support(self)
        if worktree_writer_requirement is not None:
            from .client_errors import DispatchError
            versions = self.pool_placement_contract().get("prepared_writer_requirement_versions")
            if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
                raise DispatchError(409, "selected task owner cannot retain a prepared writer requirement")
        options = {}
        if timeout is not None:
            selected = httpx.Timeout(self._http.timeout)
            if selected.read is not None:
                selected.read = max(selected.read, timeout)
            options["timeout"] = selected
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/worktree",
            json={
                "worktree": worktree, "ownership": ownership,
                "creating_host": creating_host, "driver": driver,
                "allocation_project": allocation_project, "worktree_path": worktree_path,
                **({"execution_machine": execution_machine} if execution_machine is not None else {}),
                **({"creation_nonce": creation_nonce} if creation_nonce is not None else {}),
                **({"worktree_writer_requirement": worktree_writer_requirement}
                   if worktree_writer_requirement is not None else {}),
            },
            **options,
        ))

    def record_created_agent(
        self, key: str, *, task_generation: int, agent_info: dict,
        worktree_record: dict | None = None, startup_invocation: dict | None = None,
        execution_machine: str | None = None,
    ) -> dict:
        """Persist ordinary Bridge readiness before any first task prompt."""
        body = {"task_generation": task_generation, "agent_info": agent_info}
        if worktree_record is not None:
            body["worktree_record"] = worktree_record
        if startup_invocation is not None:
            body["startup_invocation"] = startup_invocation
        if execution_machine is not None:
            body["execution_machine"] = execution_machine
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/created-agent",
            json=body,
        ))

    def record_resumed_agent(
        self, key: str, *, task_generation: int, session_info: dict, worktree_record: dict,
        startup_invocation: dict | None = None,
        execution_machine: str | None = None,
    ) -> dict:
        """Store actual resumed SessionInfo without asserting a new creation."""
        return self._unwrap(self._http.post(
            f"/spawn-reservations/{key}/resumed-agent",
            json={"task_generation": task_generation, "session_info": session_info,
                  "worktree_record": worktree_record,
                  **({"execution_machine": execution_machine} if execution_machine is not None else {}),
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
        pool_instance: tuple[str, str, str] | None = None,
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
        if pool_instance is not None:
            from .pool_instances import pool_instance_identity
            instance = dict(zip(("registration_id", "machine", "env"), pool_instance))
            if len(pool_instance) != 3:
                raise ValueError("reservation query requires an exact pool instance")
            pool_instance_identity(instance)
            params.update({"pool_" + key: value for key, value in instance.items()})
        if resume_requested is not None:
            params["resume_requested"] = resume_requested
        if task_status is not None:
            params["task_status"] = task_status
        if latest_only:
            params["latest_only"] = latest_only
        response = self._http.get("/spawn-reservations", params=params)
        reservations = self._unwrap(response)
        if pool_instance is not None and response.headers.get("X-Dispatch-Pool-Instance") != "pool-instance/v1":
            from .client import DispatchError
            raise DispatchError(426, "coordinator lacks pool-instance/v1 reservation query")
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

    def fence_legacy_history(
        self, *, before: float, apply: bool = False, permitted: bool = False,
        confirmed_no_live_holder: bool = False, reason: str = "",
        reservation_keys: list[str] | None = None, expected_preview: str | None = None,
    ) -> dict:
        from .client_errors import DispatchError

        body: dict[str, Any] = {
            "before": before, "apply": apply, "permitted": permitted,
            "confirmed_no_live_holder": confirmed_no_live_holder, "reason": reason,
        }
        if reservation_keys is not None or expected_preview is not None:
            if (
                not isinstance(reservation_keys, list) or not reservation_keys
                or any(not isinstance(key, str) or not key or key.strip() != key for key in reservation_keys)
                or len(set(reservation_keys)) != len(reservation_keys)
                or (apply and not expected_preview)
            ):
                raise ValueError("selected history requires explicit keys and an apply preview revision")
            versions = self.pool_placement_contract().get("selected_legacy_history_versions")
            if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
                raise DispatchError(426, "coordinator lacks selected-legacy-history/v1")
            body.update({"reservation_keys": reservation_keys, "expected_preview": expected_preview})
        headers = self._control_headers()
        try:
            result = self._unwrap(self._http.post(
                "/spawn-reservations/history/fence", headers=headers, json=body,
            ))
        except (ValueError, httpx.HTTPError) as exc:
            if reservation_keys is not None and apply:
                raise DispatchError(
                    502, "selected legacy history apply outcome is unknown: response unavailable or undecodable; "
                    "inspect the original reservations and audit before retrying",
                ) from exc
            raise
        except DispatchError as exc:
            if reservation_keys is not None and apply and exc.status_code >= 500:
                raise DispatchError(
                    502, "selected legacy history apply outcome is unknown: provider response failed; "
                    "inspect the original reservations and audit before retrying",
                ) from exc
            raise
        if reservation_keys is not None:
            selection = result.get("selection") if isinstance(result, dict) else None
            sample = result.get("sample") if isinstance(result, dict) else None
            if (
                not isinstance(selection, dict)
                or selection.get("reservation_keys") != sorted(reservation_keys)
                or not isinstance(selection.get("revision"), str)
                or len(selection["revision"]) != 64
                or (apply and selection["revision"] != expected_preview)
                or not isinstance(sample, list)
                or any(not isinstance(key, str) or key not in reservation_keys for key in sample)
            ):
                if apply:
                    raise DispatchError(
                        502, "selected legacy history apply outcome is unknown: invalid scope response; "
                        "inspect the original reservations and audit before retrying",
                    )
                raise DispatchError(426, "coordinator did not preserve selected-legacy-history/v1")
        return result

    def adopt_legacy_capacity(
        self, *, apply: bool = False, permitted: bool = False, reason: str = "",
    ) -> dict:
        return self._unwrap(self._http.post(
            "/spawn-reservations/history/adopt",
            headers=self._control_headers(),
            json={"apply": apply, "permitted": permitted, "reason": reason},
        ))

    def get_reservation(self, key: str) -> dict:
        return self._unwrap(self._http.get(f"/spawn-reservations/{key}"))
