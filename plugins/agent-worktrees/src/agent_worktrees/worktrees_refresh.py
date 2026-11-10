"""Bounded, resident-owned point-refresh requests and publication receipts."""

from __future__ import annotations

from collections import OrderedDict
import threading

from .worktrees_projection_index import ProjectionError

CAPABILITY = "worktrees-refresh-v1"
MAX_REQUESTS = 128


class RefreshRequests:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: OrderedDict[str, dict] = OrderedDict()

    def clear(self) -> None:
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
                    raise ProjectionError(previous["error"])
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

    def reject(self, request_id: str, message: str) -> None:
        with self._lock:
            item = self._requests.get(request_id)
            if item is not None and item["receipt"] is None:
                item["error"] = message[:1024]

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
