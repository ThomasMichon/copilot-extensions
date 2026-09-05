"""Thin HTTP client for the coordinator -- used by the CLI and by producers.

Every method maps to one coordinator route and returns plain dicts (task
snapshots) so callers stay decoupled from the server-side dataclasses.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import httpx


class DispatchError(RuntimeError):
    """A non-2xx response from the coordinator (carries status + detail)."""

    def __init__(self, status_code: int, detail: object):
        rendered = json.dumps(detail, sort_keys=True) if isinstance(detail, dict) else str(detail)
        super().__init__(f"HTTP {status_code}: {rendered}")
        self.status_code = status_code
        self.detail = detail

    def as_dict(self) -> dict[str, object]:
        if isinstance(self.detail, dict):
            return self.detail
        return {
            "code": "dispatch_http_error",
            "message": str(self.detail),
            "status": self.status_code,
        }


class DispatchUpgradeRequired(DispatchError):
    """The coordinator accepted a request but lacks the required protocol."""

    def __init__(self, detail: str):
        super().__init__(426, detail)


class DispatchClient:
    """A synchronous client for one coordinator base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        control_token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        tunnel: Any = None,
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        verify = not base_url.lower().startswith("http://")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
            verify=verify,
        )
        self._control_token = control_token
        # An optional owned resource (e.g. an SSH failover port-forward) closed
        # together with the HTTP client, so the transport lives exactly as long
        # as the client that rides it.
        self._tunnel = tunnel

    def close(self) -> None:
        self._http.close()
        if self._tunnel is not None:
            try:
                self._tunnel.close()
            finally:
                self._tunnel = None

    def __enter__(self) -> DispatchClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _unwrap(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except (ValueError, AttributeError):
                pass
            raise DispatchError(resp.status_code, detail)
        return resp.json()

    def _control_headers(self) -> dict[str, str]:
        if not self._control_token:
            return {}
        return {"Authorization": f"Bearer {self._control_token}"}

    # -- reads ---------------------------------------------------------------

    def health(self) -> dict:
        return self._unwrap(self._http.get("/health"))

    def get(self, task_id: str) -> dict:
        return self._unwrap(self._http.get(f"/tasks/{task_id}"))

    def events(self, task_id: str) -> list[dict]:
        return self._unwrap(self._http.get(f"/tasks/{task_id}/events"))

    def wakes(self, task_id: str) -> list[dict]:
        """Return a task's durable wake outbox operations."""
        return self._unwrap(self._http.get(f"/tasks/{task_id}/wakes"))

    def progress_log(self, task_id: str) -> list[dict]:
        """The accumulated append-only progress log for a task (oldest first)."""
        return self._unwrap(self._http.get(f"/tasks/{task_id}/progress-log"))

    def payload(self, task_id: str) -> dict:
        return self._unwrap(self._http.get(f"/tasks/{task_id}/payload"))

    def result(self, task_id: str) -> dict:
        return self._unwrap(self._http.get(f"/tasks/{task_id}/result"))

    def list(self, **params: Any) -> list[dict]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._unwrap(self._http.get("/tasks", params=clean))

    def find(self, query: str, *, repo: str | None = None, limit: int = 50) -> list[dict]:
        params: dict[str, Any] = {"q": query, "limit": limit}
        if repo is not None:
            params["repo"] = repo
        return self._unwrap(self._http.get("/tasks", params=params))

    def sweep(self, *, repo: str | None = None, limit: int = 500) -> list[dict]:
        """The dedup corpus: every non-abandoned task in the lane, newest first."""
        params: dict[str, Any] = {"sweep": True, "limit": limit}
        if repo is not None:
            params["repo"] = repo
        return self._unwrap(self._http.get("/tasks", params=params))

    # -- producers / transitions --------------------------------------------

    def create(self, title: str, **kwargs: Any) -> dict:
        return self._unwrap(self._http.post("/tasks", json={"title": title, **kwargs}))

    def propose(self, title: str, **kwargs: Any) -> dict:
        return self.create(title, proposed=True, **kwargs)

    def producer_scope_status(self, repo: str, source: str) -> dict:
        return self._unwrap(
            self._http.get(
                "/producer-scopes/status",
                params={"repo": repo, "source": source},
            )
        )

    def handoff_producer_scope(
        self,
        repo: str,
        source: str,
        *,
        producer_id: str,
        expected_generation: int,
        required_label: str | None = None,
    ) -> dict:
        body: dict[str, object] = {
            "repo": repo,
            "source": source,
            "producer_id": producer_id,
            "expected_generation": expected_generation,
        }
        if required_label is not None:
            body["required_label"] = required_label
        return self._unwrap(
            self._http.post(
                "/producer-scopes/handoff",
                json=body,
                headers=self._control_headers(),
            )
        )

    def approve(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/approve"))

    def claim(
        self,
        worker_id: str | None = None,
        capabilities: Sequence[str] = (),
        *,
        repo: str | None = None,
        all_repos: bool = False,
        machine: str | None = None,
        worktree: str | None = None,
        task_id: str | None = None,
        lease_seconds: int | None = None,
        evaluation: bool = False,
    ) -> dict | None:
        body = {
            "worker_id": worker_id,
            "repo": repo,
            "all_repos": all_repos,
            "machine": machine,
            "worktree": worktree,
            "capabilities": list(capabilities),
            "task_id": task_id,
            "lease_seconds": lease_seconds,
            "evaluation": evaluation,
        }
        return self._unwrap(self._http.post("/claim", json=body))

    def mine(self, machine: str, worktree: str, *, repo: str | None = None) -> dict:
        params: dict[str, Any] = {"machine": machine, "worktree": worktree}
        if repo is not None:
            params["repo"] = repo
        return self._unwrap(self._http.get("/tasks/mine", params=params))

    def start(self, task_id: str, worker_id: str) -> dict:
        return self._unwrap(
            self._http.post(f"/tasks/{task_id}/start", json={"worker_id": worker_id})
        )

    def yield_task(
        self, task_id: str, worker_id: str, *, note: str | None = None,
        exclude: str | None = None, release_spawn: bool = True,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/yield",
                json={
                    "worker_id": worker_id,
                    "note": note,
                    "exclude": exclude,
                    "release_spawn": release_spawn,
                },
            )
        )

    def suspend(self, task_id: str, worker_id: str, *, reason: str) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/suspend",
                json={"worker_id": worker_id, "reason": reason},
            )
        )

    def resume(
        self,
        task_id: str,
        worker_id: str,
        *,
        wake: bool = True,
        message: str | None = None,
        adopt_session: bool = False,
        reuse_session: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/resume",
                json={
                    "worker_id": worker_id,
                    "wake": wake,
                    "message": message,
                    "adopt_session": adopt_session,
                    "reuse_session": reuse_session,
                    "expected_owner_session_id": expected_owner_session_id,
                    "expected_generation": expected_generation,
                },
            )
        )

    def release(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/release",
                json={"worker_id": worker_id, "reason": reason},
            )
        )

    def complete(
        self,
        task_id: str,
        worker_id: str,
        *,
        result_ref: str | None = None,
        result: Any = None,
        expected_status: str | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> dict:
        body = {
            "worker_id": worker_id,
            "result_ref": result_ref,
            "expected_status": expected_status,
            "expected_owner_session_id": expected_owner_session_id,
            "expected_generation": expected_generation,
        }
        if result is not None:
            body["result"] = result
        completed = self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/complete",
                json=body,
            )
        )
        if result is not None:
            expected = json.loads(
                json.dumps(result, ensure_ascii=False, allow_nan=False)
            )
            if completed.get("result") != expected:
                raise DispatchUpgradeRequired(
                    "the coordinator completed the task without recording the "
                    "structured result; upgrade the coordinator and retry the "
                    "same-owner completion to fill the missing result"
                )
        return completed

    def abandon(
        self,
        task_id: str,
        *,
        worker_id: str | None = None,
        permitted: bool = False,
        reason: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/abandon",
                json={"worker_id": worker_id, "permitted": permitted, "reason": reason},
            )
        )

    def heartbeat(self, task_id: str, worker_id: str) -> dict:
        return self._unwrap(
            self._http.post(f"/tasks/{task_id}/heartbeat", json={"worker_id": worker_id})
        )

    def set_activity(
        self, task_id: str, activity: str | None, *, reservation_key: str
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/activity",
                json={"activity": activity, "reservation_key": reservation_key},
            )
        )

    def bind_owner_session(
        self,
        task_id: str,
        worker_id: str,
        owner_session_id: str,
        *,
        expected_generation: int | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/owner-session",
                json={
                    "worker_id": worker_id,
                    "owner_session_id": owner_session_id,
                    "expected_generation": expected_generation,
                },
            )
        )

    def progress(
        self,
        task_id: str,
        worker_id: str,
        *,
        phase: str = "",
        summary: str,
        blocker: str | None = None,
        pr: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/progress",
                json={
                    "worker_id": worker_id,
                    "phase": phase,
                    "summary": summary,
                    "blocker": blocker,
                    "pr": pr,
                },
            )
        )

    def detach(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/detach"))

    # -- steering: card + steer inbox ----------------------------------------

    def set_card(self, task_id: str, worker_id: str, *, card: dict) -> dict:
        """Attach a card to a held task (awaiting-steer if it carries a form)."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/card",
                json={"worker_id": worker_id, "card": card},
            )
        )

    def steer(
        self,
        task_id: str,
        *,
        fields: dict,
        sender: str | None = None,
        wake: bool = True,
        message: str | None = None,
    ) -> dict:
        """Submit an answer and ask the coordinator to resume the task owner."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/steer",
                json={
                    "fields": fields,
                    "sender": sender,
                    "wake": wake,
                    "message": message,
                },
            )
        )

    def steer_take(
        self, task_id: str, worker_id: str, *, all_pending: bool = False
    ) -> dict:
        """Consume the next pending steer (returns ``{task_id, steer}``; steer is
        the payload dict or ``None`` when the inbox is empty). With
        ``all_pending``, drains the inbox and returns ``{task_id, steers}``."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/steer/take",
                json={
                    "worker_id": worker_id,
                    "all_pending": all_pending,
                },
            )
        )

    def steer_log(self, task_id: str) -> list[dict]:
        """The full steer inbox for a task (oldest first)."""
        return self._unwrap(self._http.get(f"/tasks/{task_id}/steer-log"))

    def recover(self) -> dict:
        return self._unwrap(self._http.post("/recover"))

    # -- graceful-cutover seams (zdd CutoverOrchestrator client protocol) -----
    # Internal: the installer's in-process cutover drives these against the OLD
    # coordinator to quiesce it at the safe point (between claims) before retiring
    # it. Not operator-facing. See docs/patterns/graceful-daemon-cutover.md.

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict:
        return self._unwrap(
            self._http.post(
                "/drain", json={"timeout": timeout, "poll": poll, "force": force}
            )
        )

    def undrain(self) -> dict:
        return self._unwrap(self._http.post("/undrain"))

    def shutdown(self) -> dict:
        return self._unwrap(self._http.post("/shutdown"))

    def adopt_relay(self) -> dict:
        return self._unwrap(self._http.post("/adopt-relay"))

    # -- fleet directory (federation awareness plane) ------------------------

    def directory_register(
        self,
        instance: str,
        *,
        role: str = "peer",
        epoch: int = 0,
        machine: str | None = None,
        worktrees: list[str] | None = None,
        capabilities: list[str] | None = None,
        gate_state: str = "open",
        agent_versions: dict[str, str] | None = None,
        status: dict | None = None,
    ) -> dict:
        """Register (or refresh) this instance in the coordinator's fleet
        directory. Idempotent -- a re-register keeps the original
        ``registered_at`` and restamps ``last_seen``."""
        return self._unwrap(
            self._http.post(
                "/directory/register",
                json={
                    "instance": instance,
                    "role": role,
                    "epoch": epoch,
                    "machine": machine,
                    "worktrees": worktrees or [],
                    "capabilities": capabilities or [],
                    "gate_state": gate_state,
                    "agent_versions": agent_versions or {},
                    "status": status or {},
                },
            )
        )

    def directory_heartbeat(
        self,
        instance: str,
        *,
        status: dict | None = None,
        worktrees: list[str] | None = None,
        gate_state: str | None = None,
        role: str | None = None,
        epoch: int | None = None,
    ) -> dict:
        """Refresh a live entry's ``last_seen`` (+ optional fields). Raises
        :class:`DispatchError` with status 404 when the entry is not live, so
        the caller re-registers instead of resurrecting a reaped entry."""
        return self._unwrap(
            self._http.post(
                f"/directory/{instance}/heartbeat",
                json={
                    "status": status,
                    "worktrees": worktrees,
                    "gate_state": gate_state,
                    "role": role,
                    "epoch": epoch,
                },
            )
        )

    def directory_deregister(self, instance: str) -> dict:
        """Explicitly remove this instance from the directory."""
        return self._unwrap(self._http.delete(f"/directory/{instance}"))

    def directory_list(self, *, role: str | None = None) -> list[dict]:
        """All live directory entries (optional ``role`` filter) -- the
        awareness-plane read."""
        params = {"role": role} if role is not None else {}
        return self._unwrap(self._http.get("/directory", params=params))

    def directory_coordinator(self) -> dict | None:
        """The live coordinator entry with the highest epoch, or ``None`` -- the
        claim-plane discovery read."""
        return self._unwrap(self._http.get("/directory/coordinator"))

    # -- spawn reservations --------------------------------------------------

    def reserve_spawn(self, task_id: str, *, reserved_by: str | None = None) -> dict:
        """Atomically reserve the right to spawn an embody worker for a task.

        Returns ``{"reserved": bool, "reservation": {...}}``. When ``reserved``
        is ``False`` an active reservation already exists and the caller must
        **not** spawn.
        """
        return self._unwrap(
            self._http.post(
                "/spawn-reservations",
                json={"task_id": task_id, "reserved_by": reserved_by},
            )
        )

    def record_spawn(
        self,
        key: str,
        *,
        session_handle: str | None = None,
        worktree: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/spawned",
                json={"session_handle": session_handle, "worktree": worktree},
            )
        )

    def record_routing_assignment(self, key: str, assignment: dict[str, Any]) -> dict:
        """Attach one immutable routing decision to a spawn reservation."""
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/routing-assignment",
                json=assignment,
            )
        )

    def transition_routing_assignment(
        self,
        assignment_id: str,
        *,
        event_type: str,
        actor_role: str,
        terminal_disposition: str | None = None,
        reason_code: str | None = None,
        worker_session_ref: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/routing-assignments/{assignment_id}/transition",
                json={
                    "event_type": event_type,
                    "actor_role": actor_role,
                    "terminal_disposition": terminal_disposition,
                    "reason_code": reason_code,
                    "worker_session_ref": worker_session_ref,
                },
            )
        )

    def record_routing_billing_ref(
        self,
        assignment_id: str,
        *,
        event_id: str,
        provider: str,
        provider_billing_event_ref: str,
        actor_role: str,
        occurred_at: float | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/routing-assignments/{assignment_id}/billing-ref",
                json={
                    "event_id": event_id,
                    "provider": provider,
                    "provider_billing_event_ref": provider_billing_event_ref,
                    "actor_role": actor_role,
                    "occurred_at": occurred_at,
                },
            )
        )

    def routing_assignment(self, assignment_id: str) -> dict:
        return self._unwrap(self._http.get(f"/routing-assignments/{assignment_id}"))

    def routing_assignments(
        self,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params = {"limit": limit}
        if task_id is not None:
            params["task_id"] = task_id
        return self._unwrap(self._http.get("/routing-assignments", params=params))

    def routing_assignment_events(self, assignment_id: str) -> list[dict]:
        return self._unwrap(
            self._http.get(f"/routing-assignments/{assignment_id}/events")
        )

    def record_spawn_worktree(
        self,
        key: str,
        worktree: str,
        *,
        ownership: str = "unknown",
        creating_host: str | None = None,
        driver: str | None = None,
    ) -> dict:
        """Record the reserved worktree before launching the worker session."""
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/worktree",
                json={
                    "worktree": worktree,
                    "ownership": ownership,
                    "creating_host": creating_host,
                    "driver": driver,
                },
            )
        )

    def fail_spawn(self, key: str, *, detail: str | None = None) -> dict:
        return self._unwrap(
            self._http.post(f"/spawn-reservations/{key}/fail", json={"detail": detail})
        )

    def defer_spawn(self, key: str, *, detail: str | None = None) -> dict:
        return self._unwrap(
            self._http.post(f"/spawn-reservations/{key}/defer", json={"detail": detail})
        )

    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/release",
                json={"detail": detail, "disposition": disposition},
            )
        )

    def record_cold(self, key: str) -> dict:
        return self._unwrap(
            self._http.post(f"/spawn-reservations/{key}/cold")
        )

    def settle_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/settle",
                json={
                    "detail": detail,
                    "conclusion_state": conclusion_state,
                    "conclusion_detail": conclusion_detail,
                },
            )
        )

    def record_spawn_conclusion(
        self,
        key: str,
        *,
        conclusion_state: str,
        conclusion_detail: str,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/{key}/conclusion",
                json={
                    "conclusion_state": conclusion_state,
                    "conclusion_detail": conclusion_detail,
                },
            )
        )

    def rearm_spawn(
        self,
        task_id: str,
        *,
        permitted: bool = False,
        reason: str | None = None,
        min_failures: int = 3,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/spawn-reservations/tasks/{task_id}/rearm",
                json={
                    "permitted": permitted,
                    "reason": reason,
                    "min_failures": min_failures,
                },
            )
        )

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        limit: int = 200,
    ) -> list[dict]:
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
        return self._unwrap(self._http.get("/spawn-reservations", params=params))

    def get_reservation(self, key: str) -> dict:
        return self._unwrap(self._http.get(f"/spawn-reservations/{key}"))

    # -- schedule registry + job-leases -------------------------------------

    def register_schedule(self, entry: dict) -> dict:
        return self._unwrap(self._http.post("/schedules", json=entry))

    def list_schedules(self, *, include_paused: bool = True) -> list[dict]:
        return self._unwrap(
            self._http.get("/schedules", params={"include_paused": include_paused})
        )

    def get_schedule(self, sid: str) -> dict:
        return self._unwrap(self._http.get(f"/schedules/{sid}"))

    def remove_schedule(self, sid: str) -> dict:
        return self._unwrap(self._http.delete(f"/schedules/{sid}"))

    def set_schedule_paused(self, sid: str, paused: bool) -> dict:
        verb = "pause" if paused else "resume"
        return self._unwrap(self._http.post(f"/schedules/{sid}/{verb}"))

    def acquire_schedule_lease(
        self,
        scope: str,
        holder: str,
        *,
        holder_session: str | None = None,
        ttl: float | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/schedule-leases/{scope}/acquire",
                json={"holder": holder, "holder_session": holder_session, "ttl": ttl},
            )
        )

    def release_schedule_lease(
        self, scope: str, holder: str, *, force: bool = False
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/schedule-leases/{scope}/release",
                json={"holder": holder, "force": force},
            )
        )

    def list_schedule_leases(self) -> list[dict]:
        return self._unwrap(self._http.get("/schedule-leases"))

    def get_schedule_lease(self, scope: str) -> dict | None:
        return self._unwrap(self._http.get(f"/schedule-leases/{scope}"))

    # -- external producer resource reservations ----------------------------

    def acquire_resource_reservation(
        self,
        key: str,
        owner: str,
        *,
        ttl: float,
        token: str | None = None,
    ) -> dict:
        body: dict[str, object] = {
            "key": key,
            "owner": owner,
            "ttl": ttl,
        }
        if token is not None:
            body["token"] = token
        return self._unwrap(
            self._http.post(
                "/resource-reservations/acquire",
                json=body,
            )
        )

    def bind_resource_reservation(
        self, key: str, owner: str, token: str, task_id: str
    ) -> dict:
        return self._unwrap(
            self._http.post(
                "/resource-reservations/bind",
                json={
                    "key": key,
                    "owner": owner,
                    "token": token,
                    "task_id": task_id,
                },
            )
        )

    def release_resource_reservation(
        self, key: str, owner: str, token: str
    ) -> dict:
        return self._unwrap(
            self._http.post(
                "/resource-reservations/release",
                json={"key": key, "owner": owner, "token": token},
            )
        )

    def list_resource_reservations(
        self,
        *,
        owner_prefix: str | None = None,
        task_id: str | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if owner_prefix is not None:
            params["owner_prefix"] = owner_prefix
        if task_id is not None:
            params["task_id"] = task_id
        return self._unwrap(
            self._http.get("/resource-reservations", params=params)
        )

    # -- supervisor registrations -------------------------------------------

    def register_registration(
        self,
        kind: str,
        spec: dict,
        *,
        reg_id: str | None = None,
        machine: str | None = None,
        env: str = "default",
    ) -> dict:
        body = {
            "kind": kind,
            "spec": spec,
            "id": reg_id,
            "machine": machine,
            "env": env,
        }
        return self._unwrap(self._http.post("/registrations", json=body))

    def list_registrations(
        self,
        *,
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[dict]:
        params: dict[str, object] = {"include_paused": include_paused}
        if kind is not None:
            params["kind"] = kind
        if machine is not None:
            params["machine"] = machine
        if env is not None:
            params["env"] = env
        return self._unwrap(self._http.get("/registrations", params=params))

    def get_registration(self, rid: str) -> dict:
        return self._unwrap(self._http.get(f"/registrations/{rid}"))

    def remove_registration(self, rid: str) -> dict:
        return self._unwrap(self._http.delete(f"/registrations/{rid}"))

    def set_registration_status(self, rid: str, status: str) -> dict:
        return self._unwrap(
            self._http.post(f"/registrations/{rid}/status", json={"status": status})
        )

    def stream_events(self) -> Iterator[dict]:
        """Yield task events from the coordinator's SSE stream (blocking)."""
        with self._http.stream("GET", "/events") as resp:
            if resp.status_code >= 400:
                resp.read()
                raise DispatchError(resp.status_code, resp.text)
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    yield json.loads(line[len("data:") :].strip())


class ResolvingDispatchClient:
    """Resolve and open a fresh coordinator client for every operation.

    Long-running supervisors may outlive a zero-downtime coordinator generation.
    A client retained from process startup can keep an old TCP connection alive
    after the advertised dynamic endpoint changes, then fail permanently when
    that retiring generation closes. Re-resolving per operation makes the
    supervisor follow the same live rendezvous as every ordinary CLI command.
    """

    def __init__(self, factory: Callable[[], DispatchClient]):
        self._factory = factory

    def close(self) -> None:
        """No-op; each delegated operation owns and closes its client."""

    def __enter__(self) -> ResolvingDispatchClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        # Proxy only real DispatchClient methods, so attribute semantics stay
        # normal: a missing/typo name raises AttributeError (and hasattr() is
        # honest) instead of silently returning a callable that fails only when
        # invoked. Generator methods (e.g. stream_events) are intentionally not
        # used through this wrapper -- the per-operation client would close
        # before iteration -- and the supervisor never calls them.
        target = getattr(DispatchClient, name, None)
        if not callable(target):
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            )

        def call(*args: Any, **kwargs: Any) -> Any:
            with self._factory() as client:
                return getattr(client, name)(*args, **kwargs)

        return call
