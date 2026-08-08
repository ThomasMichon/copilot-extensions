"""Thin HTTP client for the coordinator -- used by the CLI and by producers.

Every method maps to one coordinator route and returns plain dicts (task
snapshots) so callers stay decoupled from the server-side dataclasses.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx


class DispatchError(RuntimeError):
    """A non-2xx response from the coordinator (carries status + detail)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class DispatchClient:
    """A synchronous client for one coordinator base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._http.close()

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

    # -- reads ---------------------------------------------------------------

    def health(self) -> dict:
        return self._unwrap(self._http.get("/health"))

    def get(self, task_id: str) -> dict:
        return self._unwrap(self._http.get(f"/tasks/{task_id}"))

    def events(self, task_id: str) -> list[dict]:
        return self._unwrap(self._http.get(f"/tasks/{task_id}/events"))

    def progress_log(self, task_id: str) -> list[dict]:
        """The accumulated append-only progress log for a task (oldest first)."""
        return self._unwrap(self._http.get(f"/tasks/{task_id}/progress-log"))

    def payload(self, task_id: str) -> dict:
        return self._unwrap(self._http.get(f"/tasks/{task_id}/payload"))

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

    def approve(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/approve"))

    def claim(
        self,
        worker_id: str | None = None,
        capabilities: Sequence[str] = (),
        *,
        repo: str | None = None,
        machine: str | None = None,
        worktree: str | None = None,
        task_id: str | None = None,
        lease_seconds: int | None = None,
        evaluation: bool = False,
    ) -> dict | None:
        body = {
            "worker_id": worker_id,
            "repo": repo,
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
        exclude: str | None = None,
    ) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/yield",
                json={"worker_id": worker_id, "note": note, "exclude": exclude},
            )
        )

    def complete(self, task_id: str, worker_id: str, *, result_ref: str | None = None) -> dict:
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/complete",
                json={"worker_id": worker_id, "result_ref": result_ref},
            )
        )

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

    def recover(self) -> dict:
        return self._unwrap(self._http.post("/recover"))

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

    def fail_spawn(self, key: str, *, detail: str | None = None) -> dict:
        return self._unwrap(
            self._http.post(f"/spawn-reservations/{key}/fail", json={"detail": detail})
        )

    def settle_spawn(self, key: str, *, detail: str | None = None) -> dict:
        return self._unwrap(
            self._http.post(f"/spawn-reservations/{key}/settle", json={"detail": detail})
        )

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if task_id is not None:
            params["task_id"] = task_id
        if state is not None:
            params["state"] = state
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
