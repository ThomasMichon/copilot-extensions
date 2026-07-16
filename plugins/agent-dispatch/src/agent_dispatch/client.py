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

    def stream_events(self) -> Iterator[dict]:
        """Yield task events from the coordinator's SSE stream (blocking)."""
        with self._http.stream("GET", "/events") as resp:
            if resp.status_code >= 400:
                resp.read()
                raise DispatchError(resp.status_code, resp.text)
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    yield json.loads(line[len("data:") :].strip())
