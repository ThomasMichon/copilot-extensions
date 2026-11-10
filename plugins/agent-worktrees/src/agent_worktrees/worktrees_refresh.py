"""Bounded, resident-owned point-refresh requests and publication receipts."""

from __future__ import annotations

from collections import OrderedDict
import threading
import time

from .worktrees_projection_index import ProjectionError

CAPABILITY = "worktrees-refresh-v1"
MAX_REQUESTS = 128


class RefreshRejected(ProjectionError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class RefreshRequests:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: OrderedDict[str, dict] = OrderedDict()
        self._mux_superseded: dict[tuple[str, str], tuple[tuple, float]] = {}

    def clear(self) -> None:
        """Retire requests without reviving superseded cached observations."""
        with self._lock:
            self._requests.clear()

    def request(self, project: str, selector: str, request_id: str) -> dict | None:
        with self._lock:
            previous = self._requests.get(request_id)
            if previous is not None:
                if (previous["project"], previous["selector"]) != (project, selector):
                    raise ProjectionError("refresh request identity changed selection")
                self._requests.move_to_end(request_id)
                if previous["error"] is not None:
                    raise RefreshRejected(previous["error"]["message"], previous["error"]["code"])
                return previous["receipt"]
            if len(self._requests) >= MAX_REQUESTS:
                completed = next(
                    (key for key, value in self._requests.items()
                     if value["receipt"] is not None or value["error"] is not None),
                    None,
                )
                if completed is None:
                    raise ProjectionError("resident point-refresh capacity is exhausted")
                del self._requests[completed]
            self._requests[request_id] = {
                "project": project, "selector": selector, "receipt": None, "error": None,
            }
            return None

    def pending(self, project: str) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(
                (key, value["selector"]) for key, value in self._requests.items()
                if value["project"] == project
                and value["receipt"] is None and value["error"] is None
            )

    def reject(self, request_id: str, message: str, *, code: str = "invalid-request") -> None:
        with self._lock:
            item = self._requests.get(request_id)
            if item is not None and item["receipt"] is None:
                item["error"] = {"message": message[:1024], "code": code}

    def supersede_mux(self, project: str, worktree_id: str, entry: dict | None) -> None:
        if entry is None or entry.get("live") is not True:
            return
        from .mux_link import MAPPING_STALE_AFTER_SECONDS

        with self._lock:
            now = time.monotonic()
            self._mux_superseded = {
                key: value for key, value in self._mux_superseded.items() if value[1] > now
            }
            key = project, worktree_id
            if key not in self._mux_superseded and len(self._mux_superseded) >= MAX_REQUESTS:
                raise ProjectionError("resident mux-observation supersession capacity is exhausted")
            self._mux_superseded[key] = (
                (entry["mapping_revision"], entry["received_at"]),
                now + MAPPING_STALE_AFTER_SECONDS,
            )

    def mux_observation_current(self, project: str, worktree_id: str, entry: dict) -> bool:
        with self._lock:
            previous = self._mux_superseded.get((project, worktree_id))
            return (
                previous is None or previous[1] <= time.monotonic()
                or (entry["mapping_revision"], entry["received_at"]) > previous[0]
            )

    def complete(self, project: str, requests, head: dict) -> None:
        with self._lock:
            for request_id, selector in requests:
                item = self._requests.get(request_id)
                if (
                    item is not None and item["error"] is None
                    and (item["project"], item["selector"]) == (project, selector)
                ):
                    item["receipt"] = {
                        "request_id": request_id,
                        "generation": head["generation"],
                        "revision": head["revision"],
                    }
