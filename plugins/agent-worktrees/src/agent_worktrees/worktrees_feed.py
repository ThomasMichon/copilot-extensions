"""Held current-roster feed with a one-slot notification barrier per subscriber."""

from __future__ import annotations

import threading
from collections import Counter

from .worktrees_projection import MAX_COLLECTION_ITEMS
from .worktrees_projection_index import ProjectionError
from .worktrees_runtime import CONTRACT_VERSION, request_selection

FEED_ACTION = "worktrees-feed"
FEED_CAPABILITY = "worktrees-feed-v1"
MAX_SUBSCRIBERS = 32
HEARTBEAT_SECONDS = 2.0


def publication_frames(result: dict, previous: dict | None = None):
    """A transaction is visible only after its done/commit barrier."""
    head = result["projection"]
    rows = result["worktrees"]
    old = {} if previous is None else {row["id"]: row for row in previous["worktrees"]}
    current = {row["id"]: row for row in rows}
    yield {
        "type": "begin" if previous is None else "update",
        "contract_version": CONTRACT_VERSION,
        "projection": head,
        "cursor": head["revision"],
        "base_cursor": None if previous is None else previous["projection"]["revision"],
        "availability": result.get("availability"),
        "selected_count": len(rows),
    }
    for row in rows:
        if old.get(row["id"]) != row:
            yield {"type": "worktree", "phase": "current", "wt": row, "projection": head}
    for wid in old.keys() - current.keys():
        yield {"type": "delete", "id": wid, "projection": head}
    if previous is not None:
        identities = list(current)
        for start in range(0, len(identities), MAX_COLLECTION_ITEMS):
            yield {
                "type": "order", "ids": identities[start:start + MAX_COLLECTION_ITEMS],
                "projection": head,
            }
    yield {
        "type": "done" if previous is None else "commit",
        "projection": head,
        "cursor": head["revision"],
    }


class WorktreesFeed:
    """No queued row copies: slow readers diff their last commit against latest."""

    def __init__(self, owner) -> None:
        self._owner = owner
        self._condition = threading.Condition()
        self._subscribers: dict[object, dict] = {}
        self._stamps: Counter = Counter()
        self._closed: str | None = "unready"

    def activate(self) -> None:
        with self._condition:
            self._closed = None

    def changed(self, project: str) -> None:
        with self._condition:
            self._stamps[project] += 1
            self._condition.notify_all()

    def close(self, reason: str) -> None:
        with self._condition:
            self._closed = reason
            for subscriber in self._subscribers.values():
                subscriber["closed"] = reason
            self._condition.notify_all()

    def projects(self) -> set[str]:
        with self._condition:
            return {s["project"] for s in self._subscribers.values()}

    def active_count(self) -> int:
        with self._condition:
            return len(self._subscribers)

    def wait_closed(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: not self._subscribers, timeout=timeout)

    def _notice(self, reason: str, previous: dict | None) -> dict:
        return {
            "type": "going-away",
            "reason": reason,
            "generation": self._owner.store.generation,
            "cursor": None if previous is None else previous["projection"]["revision"],
        }

    def stream(self, payload: dict, disconnected):
        try:
            project, _selection = request_selection(payload)
        except ProjectionError as exc:
            yield {"type": "error", **self._owner._failure("invalid-request", str(exc))}
            return
        key = object()
        with self._condition:
            reason = self._closed
            if reason is None and len(self._subscribers) >= MAX_SUBSCRIBERS:
                reason = "subscriber-limit"
            if reason is None:
                subscriber = {"project": project, "closed": None}
                self._subscribers[key] = subscriber
                stamp = self._stamps[project]
        if reason is not None:
            yield {"type": "error", **self._owner._failure(reason, "feed admission closed")}
            return
        try:
            previous = None
            while not disconnected():
                with self._condition:
                    reason = subscriber["closed"]
                if reason is not None:
                    yield self._notice(reason, previous)
                    return
                result = self._owner.handle(payload)
                if result.get("ok") is not True:
                    with self._condition:
                        reason = subscriber["closed"]
                    yield self._notice(reason, previous) if reason is not None else {
                        "type": "error", **result,
                    }
                    return
                if previous is None or result["projection"] != previous["projection"]:
                    yield from publication_frames(result, previous)
                    previous = result
                elif result.get("availability") != previous.get("availability"):
                    yield {
                        "type": "availability",
                        "projection": result["projection"],
                        "availability": result.get("availability"),
                    }
                    previous = result
                while not disconnected():
                    with self._condition:
                        self._condition.wait_for(
                            lambda seen=stamp: (
                                subscriber["closed"] is not None or self._stamps[project] != seen
                            ),
                            timeout=HEARTBEAT_SECONDS,
                        )
                        changed = self._stamps[project] != stamp
                        stamp = self._stamps[project]
                        reason = subscriber["closed"]
                    if changed or reason is not None:
                        break
                    if self._owner._elected is not None and not self._owner._elected():
                        self._owner.close_admission("superseded")
                        break
                    yield {
                        "type": "heartbeat",
                        "generation": self._owner.store.generation,
                        "cursor": previous["projection"]["revision"],
                    }
        finally:
            with self._condition:
                self._subscribers.pop(key, None)
                self._condition.notify_all()
            # Retain normal bounded demand after releasing the held admission.
            with self._owner._lock:
                self._owner._demand[project] = self._owner._now()
