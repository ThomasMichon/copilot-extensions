"""Thin HTTP client for the coordinator -- used by the CLI and by producers.

Every method maps to one coordinator route and returns plain dicts (task
snapshots) so callers stay decoupled from the server-side dataclasses.
"""

from __future__ import annotations

from collections.abc import Sequence
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

    def list(self, **params: Any) -> list[dict]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._unwrap(self._http.get("/tasks", params=clean))

    def find(self, query: str, *, limit: int = 50) -> list[dict]:
        return self._unwrap(self._http.get("/tasks", params={"q": query, "limit": limit}))

    # -- producers / transitions --------------------------------------------

    def create(self, title: str, **kwargs: Any) -> dict:
        return self._unwrap(self._http.post("/tasks", json={"title": title, **kwargs}))

    def propose(self, title: str, **kwargs: Any) -> dict:
        return self.create(title, proposed=True, **kwargs)

    def approve(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/approve"))

    def claim(
        self, worker_id: str, capabilities: Sequence[str] = (), *, lease_seconds: int | None = None
    ) -> dict | None:
        body = {
            "worker_id": worker_id,
            "capabilities": list(capabilities),
            "lease_seconds": lease_seconds,
        }
        return self._unwrap(self._http.post("/claim", json=body))

    def start(self, task_id: str, worker_id: str) -> dict:
        return self._unwrap(
            self._http.post(f"/tasks/{task_id}/start", json={"worker_id": worker_id})
        )

    def yield_task(self, task_id: str, worker_id: str, *, note: str | None = None) -> dict:
        return self._unwrap(
            self._http.post(f"/tasks/{task_id}/yield", json={"worker_id": worker_id, "note": note})
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

    def detach(self, task_id: str) -> dict:
        return self._unwrap(self._http.post(f"/tasks/{task_id}/detach"))

    def recover(self) -> dict:
        return self._unwrap(self._http.post("/recover"))
