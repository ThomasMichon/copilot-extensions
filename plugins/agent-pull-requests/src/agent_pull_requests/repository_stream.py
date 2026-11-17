"""Bounded local SSE projections over existing registration/delivery custody."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, cast

from .repository_subscriptions import (
    RESPONSE_BYTES, RepositoryHistoryGap, RepositoryRequestConflict, _integer, _page, _text,
)

PROTOCOL = "agent-pull-requests.repository-stream/v1"
MAX_BYTES = 16 * RESPONSE_BYTES
MAX_FRAMES = 256


@dataclass(frozen=True)
class LocalStreamResponse:
    """Local ingress only; never supplied to a finite Fleet response channel."""

    chunks: AsyncGenerator[bytes, None]
    status: int = 200


def capability() -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "transport": "local-sse", "operation": "stream",
        "views": ["status", "pulls", "next-actions"], "ack_on_write": False,
        "resume": "registration-scoped-current-views", "frame_bytes": RESPONSE_BYTES,
        "stream_bytes": MAX_BYTES, "frames": MAX_FRAMES,
    }


def encode(event: str, data: dict[str, Any], cursor: str | None = None) -> bytes:
    raw = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
    result = (f"id: {cursor}\n" if cursor else "").encode()
    result += f"event: {event}\ndata: ".encode() + raw + b"\n\n"
    if len(result) > RESPONSE_BYTES:
        raise ValueError("repository stream frame exceeds its budget")
    return result


class RepositoryStream:
    """Atomic current views and event-delivery progress; no handled/ACK writes."""

    def __init__(
        self, subscriptions: Any, key: str, consumer: str, payload: dict[str, Any],
        last_event_id: str | None,
    ) -> None:
        from .repository_reviewer import page

        if not {"registration_id", "reviewer"} <= payload.keys() or payload.keys() - {
            "registration_id", "reviewer", "max_age_seconds", "limit", "seconds", "interval",
        }:
            raise ValueError("invalid repository stream selection")
        self.subscriptions, self.key, self.consumer = subscriptions, key, consumer
        self.registration_id = _text(payload["registration_id"])
        self.reviewer = payload["reviewer"]
        self.age = _integer(payload.get("max_age_seconds", 1800), 1, 86400)
        self.limit = _integer(payload.get("limit", 16), 1, 16)
        self.seconds = _integer(payload.get("seconds", 60), 1, 3600)
        self.interval = _integer(payload.get("interval", 1), 1, 300)
        self.identity = subscriptions.projection.identities[key]
        # Reuse the query's actual reviewer validation, before any delivery write.
        page({"repositories": [], "next_sequence": 1}, self.identity, consumer,
             reviewer=self.reviewer, now=0, max_age_seconds=self.age)
        selection = [key, consumer, self.registration_id, self.reviewer, self.age, self.limit]
        self.scope = hashlib.sha256(json.dumps(selection, allow_nan=False).encode()).hexdigest()
        self.after: int | None = None
        if last_event_id is not None:
            matched = re.fullmatch(r"v1\.([a-f0-9]{64})\.([0-9]{1,16})", last_event_id)
            if matched is None or matched[1] != self.scope:
                raise RepositoryRequestConflict("stream resume selection differs")
            self.after = _integer(int(matched[2]))

    def sample(self, **authority: Any) -> dict[str, Any]:
        from .repository_reviewer import page

        def transact(document: dict[str, Any], commit: Callable[[], None]) -> dict[str, Any]:
            state = document["repository_cache"]
            now = int(self.subscriptions.store.clock())
            row = next((row for row in state["registrations"] if (
                row["registration_id"], row["consumer"], row["target"]
            ) == (self.registration_id, self.consumer, self.key)), None)
            if row is None:
                raise KeyError("subscription not found in admitted scope")
            after = row["acknowledged"] if self.after is None else self.after
            if not row["acknowledged"] <= after <= row["delivered"]:
                raise RepositoryHistoryGap("original stream delivery position unavailable")
            status = "cancelled" if row["cancelled"] else (
                "expired" if row["expires_at"] <= now else "active"
            )
            events = [event for event in state["events"] if (
                event["target"] == self.key and event["sequence"] > after
                and (status == "active" or event["sequence"] <= row["delivered"])
            )]
            batch = events[:self.limit]
            sequence = batch[-1]["sequence"] if batch else after
            cursor = f"v1.{self.scope}.{sequence}"
            pulls = _page(state, self.identity, limit=self.limit, now=now)
            actions = page(state, self.identity, self.consumer, reviewer=self.reviewer,
                           now=now, limit=self.limit, max_age_seconds=self.age)
            common = {
                "protocol": PROTOCOL, "namespace": self.identity.namespace,
                "repository_key": self.key, "consumer": self.consumer,
                "registration_id": self.registration_id, "reviewer": self.reviewer,
            }
            views = {
                "status": {
                    "status": status, "expires_at": row["expires_at"],
                    "acknowledged": row["acknowledged"], "delivered": max(row["delivered"], sequence),
                    "inventory": pulls["inventory"], "event_cursor": pulls["cursor"],
                    "events": [{name: event[name] for name in (
                        "sequence", "event_id", "number", "kind",
                    )} for event in batch], "more_events": len(events) > len(batch),
                },
                "pulls": pulls, "next-actions": actions,
            }
            # Validate every frame before making delivery durable.
            frames = [(name, {**common, "view": value}) for name, value in views.items()]
            for name, value in frames:
                encode(name, value, cursor)
            if sequence > row["delivered"]:
                row["delivered"] = sequence
                commit()
            return {"frames": frames, "after": sequence, "cursor": cursor,
                    "more": len(events) > len(batch), "status": status}

        result = self.subscriptions.store._review_transaction(
            transact, staged=True, persist=False, **authority,
        )
        self.after = result["after"]
        return cast(dict[str, Any], result)


async def chunks(
    stream: RepositoryStream, initial: dict[str, Any], sample: Callable[[], Any],
    check: Callable[[], None],
) -> AsyncGenerator[bytes, None]:
    """At most one joined transaction and one bounded frame set per connection."""
    deadline = time.monotonic() + stream.seconds
    result = initial
    previous: dict[str, str] = {}
    while True:
        check()
        for name, value in result["frames"]:
            compared = {**value, "view": {key: item for key, item in value["view"].items() if key != "now"}}
            signature = hashlib.sha256(json.dumps(compared, sort_keys=True).encode()).hexdigest()
            if signature != previous.get(name):
                yield encode(name, value, result["cursor"])
                previous[name] = signature
        if result["status"] != "active":
            yield encode("end", {"reason": result["status"]})
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield encode("end", {"reason": "window_elapsed"})
            return
        wake = time.monotonic() + min(.01 if result["more"] else stream.interval, remaining)
        while time.monotonic() < wake:
            await asyncio.sleep(min(.1, max(0, wake - time.monotonic())))
            check()
        if time.monotonic() >= deadline:
            yield encode("end", {"reason": "window_elapsed"})
            return
        pending = asyncio.create_task(asyncio.to_thread(sample))
        try:
            try:
                result = await asyncio.shield(pending)
            except RepositoryHistoryGap:
                yield encode("error", {"error": "repository_history_gap"})
                return
            except KeyError:
                yield encode("error", {"error": "repository_registration_not_found"})
                return
        finally:
            # A disconnect is not proof that the started disk operation stopped.
            if not pending.done():
                await asyncio.gather(pending, return_exceptions=True)
        yield b": keepalive\n\n"
