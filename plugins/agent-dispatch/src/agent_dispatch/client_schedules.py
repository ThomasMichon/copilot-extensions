"""Schedule registry and pinned producer election HTTP client surface."""

from __future__ import annotations

from typing import Any

import httpx


class ScheduleClientMixin:
    _http: httpx.Client

    def _unwrap(self, response: httpx.Response) -> Any:
        raise NotImplementedError

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
        self, scope: str, holder: str, *,
        holder_session: str | None = None, ttl: float | None = None,
    ) -> dict:
        return self._unwrap(self._http.post(
            f"/schedule-leases/{scope}/acquire",
            json={"holder": holder, "holder_session": holder_session, "ttl": ttl},
        ))

    def release_schedule_lease(
        self, scope: str, holder: str, *, force: bool = False,
        expected_renewed_at: float | None = None,
    ) -> dict:
        body: dict[str, object] = {"holder": holder, "force": force}
        endpoint = "release"
        if expected_renewed_at is not None:
            if force:
                raise ValueError("observed lease release cannot override holder identity")
            body = {"holder": holder, "expected_renewed_at": expected_renewed_at}
            # An older coordinator must reject the request, not ignore its fence.
            endpoint = "release-observed"
        return self._unwrap(self._http.post(f"/schedule-leases/{scope}/{endpoint}", json=body))

    def list_schedule_leases(self) -> list[dict]:
        return self._unwrap(self._http.get("/schedule-leases"))

    def get_schedule_lease(self, scope: str) -> dict | None:
        return self._unwrap(self._http.get(f"/schedule-leases/{scope}"))
