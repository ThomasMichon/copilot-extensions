"""Atomic agenda frames with one latest-state notification slot per reader."""

from __future__ import annotations

import threading

from . import agenda_contract as contract

MAX_SUBSCRIBERS = 32
HEARTBEAT_SECONDS = 2.0


class AgendaFeed:
    def __init__(self, agendas) -> None:
        self.agendas = agendas
        self._condition = threading.Condition()
        self._subscribers = {}
        self._closed = "unready"

    def activate(self) -> None:
        with self._condition:
            self._closed = None

    def close(self, reason: str) -> None:
        with self._condition:
            self._closed = reason
            for subscriber in self._subscribers.values():
                if subscriber["closed"] not in {"superseded", "shutdown", "closed"}:
                    subscriber["closed"] = reason
            self._condition.notify_all()

    def changed(self, project: str) -> None:
        with self._condition:
            for subscriber in self._subscribers.values():
                if subscriber["project"] == project:
                    subscriber["dirty"] = True
            self._condition.notify_all()

    def active_count(self) -> int:
        with self._condition:
            return len(self._subscribers)

    def projects(self) -> set[str]:
        with self._condition:
            return {value["project"] for value in self._subscribers.values()}

    def wait_closed(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: not self._subscribers, timeout)

    def stream(self, payload, disconnected):
        try:
            project, _wid, _session = contract.selection(payload)
        except contract.AgendaError as exc:
            yield {
                "type": "error",
                **contract.failure(
                    exc.code,
                    str(exc),
                    version=contract.error_version(payload),
                ),
            }
            return
        key = object()
        with self._condition:
            reason = self._closed
            if reason is None and len(self._subscribers) >= MAX_SUBSCRIBERS:
                reason = "subscriber-limit"
            if reason is None:
                subscriber = {"project": project, "closed": None, "dirty": True}
                self._subscribers[key] = subscriber
        if reason is not None:
            yield {
                "type": "error",
                **contract.failure(
                    reason,
                    "agenda feed admission closed",
                    version=contract.error_version(payload),
                ),
            }
            return
        previous = None
        try:
            while not disconnected():
                with self._condition:
                    reason = subscriber["closed"]
                    subscriber["dirty"] = False
                if reason is not None and previous is not None:
                    yield {
                        "type": "going-away",
                        "generation": previous["generation"],
                        "sequence": previous["sequence"],
                        "reason": reason,
                    }
                    return
                result = self.agendas.read(payload, refresh=previous is None)
                with self._condition:
                    reason = subscriber["closed"]
                if reason is not None and previous is not None:
                    yield {
                        "type": "going-away",
                        "generation": previous["generation"],
                        "sequence": previous["sequence"],
                        "reason": reason,
                    }
                    return
                if result.get("ok") is not True:
                    yield {"type": "error", **result}
                    return
                if previous is None or any(
                    result[key] != previous[key] for key in ("availability", "observation")
                ):
                    if previous is not None and (
                        result["generation"] != previous["generation"]
                        or result["sequence"] <= previous["sequence"]
                    ):
                        yield {
                            "type": "error",
                            **contract.failure(
                                "superseded",
                                "agenda feed generation or sequence changed",
                                version=contract.error_version(payload),
                            ),
                        }
                        return
                    yield {"type": "snapshot" if previous is None else "update", **result}
                    previous = result
                while not disconnected():
                    with self._condition:
                        self._condition.wait_for(
                            lambda: subscriber["dirty"] or subscriber["closed"] is not None,
                            timeout=HEARTBEAT_SECONDS,
                        )
                        if subscriber["dirty"] or subscriber["closed"] is not None:
                            break
                    owner = self.agendas.owner
                    if owner._elected is not None and not owner._elected():
                        owner.close_admission("superseded")
                        break
                    yield {
                        "type": "heartbeat",
                        "generation": previous["generation"],
                        "sequence": previous["sequence"],
                    }
        finally:
            with self._condition:
                self._subscribers.pop(key, None)
                self._condition.notify_all()
