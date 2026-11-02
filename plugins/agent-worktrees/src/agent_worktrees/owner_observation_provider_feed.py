"""Source-fenced relay of the existing bounded observation journal subscription."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from . import owner_observation_contract as c
from .owner_observation_runtime import MAX_SUBSCRIBERS, OwnerObservationRuntime

if TYPE_CHECKING:
    from .owner_observation_adapter import OwnerObservationAdapter


def signature(frame: dict) -> tuple:
    return (
        *(
            frame[key]
            for key in (
                "cursor",
                "observed_at",
                "availability",
                "completeness",
                "durability",
                "head_session_id",
                "source",
                "source_generation",
                "resident_generation",
                "history_floor",
            )
        ),
        c.encode(frame["facts"]),
    )


def stream(
    adapter: OwnerObservationAdapter,
    payload: dict,
    disconnected: Callable[[], bool],
) -> Iterator[dict]:
    from .owner_observation_adapter import ERRORS, fingerprint

    selected = None
    raw = None
    previous = None
    previous_signature = None
    previous_gap = None
    admitted = False
    try:
        selected, runtime = adapter.select(payload)
        key = selected.project, selected.worktree_id
        with adapter._condition:
            if adapter._streams >= MAX_SUBSCRIBERS:
                raise c.ObservationError(
                    "observation subscriber bound reached", "subscriber-limit"
                )
            adapter._streams += 1
            adapter._held[key] = adapter._held.get(key, 0) + 1
            admitted = True
        adapter.gate(selected, runtime)
        raw = runtime.stream(payload, disconnected)
        for frame in raw:
            if disconnected():
                return
            with adapter._condition:
                while (
                    (key in adapter._pending or selected.project in adapter._publishing)
                    and adapter._closed is None
                    and not disconnected()
                ):
                    adapter._condition.wait(0.1)
            if disconnected():
                return
            before = adapter.gate(selected, runtime)
            kind = frame["type"]
            if kind == "error":
                yield frame
                return
            if kind == "going-away":
                yield (
                    frame if previous is None else runtime._notice(previous, kind, frame["reason"])
                )
                return
            if kind == "heartbeat":
                if previous is not None:
                    yield runtime._notice(previous, "heartbeat")
                continue
            if previous is None:
                current, gap = runtime.history.snapshot(selected, payload["after_cursor"])
            else:
                current, gap = runtime.history.delta(selected, previous["cursor"])
            after = adapter.gate(selected, runtime)
            if fingerprint(before) != fingerprint(after):
                continue
            current_signature = signature(current)
            gap_signature = None if gap is None else c.encode(gap)
            changed = current_signature != previous_signature
            changed_gap = gap is not None and gap_signature != previous_gap
            previous_gap = gap_signature
            if not changed and not changed_gap:
                continue
            initial = previous is None
            if changed_gap and not initial:
                yield gap
            if changed:
                yield current
            previous, previous_signature = current, current_signature
            if changed_gap and initial:
                yield gap
            if current["availability"] == "head-changed":
                yield runtime._notice(current, "going-away", "head-changed")
                return
    except ERRORS as error:
        code = error.code if isinstance(error, c.ObservationError) else "unavailable"
        if code == "superseded":
            adapter.close_admission("superseded")
        yield (
            c.error(code, str(error))
            if previous is None
            else OwnerObservationRuntime._notice(
                previous,
                "going-away",
                code,
            )
        )
    finally:
        if raw is not None:
            raw.close()
        if admitted and selected is not None:
            key = selected.project, selected.worktree_id
            with adapter._condition:
                adapter._streams -= 1
                adapter._held[key] -= 1
                if not adapter._held[key]:
                    adapter._held.pop(key)
                adapter._condition.notify_all()
