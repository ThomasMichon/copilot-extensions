"""Selection- and generation-fenced owner observation responses, never recovery."""

from __future__ import annotations

from typing import Any

from . import owner_observation_contract as contract

JsonObject = dict[str, Any]


def decode_json(raw: bytes, *, limit: int = contract.FRAME_BYTES) -> Any:
    try:
        return contract.decode(raw, limit)
    except contract.ObservationError as error:
        raise contract.ObservationError(
            f"invalid owner observation response: {error}", "invalid-response"
        ) from error


def decode_frame(raw: bytes) -> JsonObject:
    value = decode_json(raw)
    if not isinstance(value, dict):
        raise contract.ObservationError("owner response must be an object", "invalid-response")
    try:
        contract.validate_frame(value)
    except contract.ObservationError as error:
        raise contract.ObservationError(
            f"invalid owner observation response: {error}", "invalid-response"
        ) from error
    return value


def source(frame: JsonObject) -> contract.Source:
    return contract.Source(
        *(
            frame[key]
            for key in (
                "project",
                "worktree_id",
                "source",
                "source_generation",
                "resident_generation",
                "head_session_id",
            )
        )
    )


def validate(
    value: JsonObject,
    request: JsonObject,
    generation: str,
    *,
    history: bool = False,
) -> JsonObject:
    try:
        contract.validate_frame(value)
    except contract.ObservationError as error:
        raise contract.ObservationError(
            f"invalid owner observation response: {error}", "invalid-response"
        ) from error
    if value["type"] == "error":
        return value
    if value["project"] != request["project"] or value["worktree_id"] != request["worktree_id"]:
        raise contract.ObservationError("owner response changed selection", "invalid-response")
    if value["resident_generation"] != generation:
        raise contract.ObservationError("resident observation owner changed", "source-replaced")
    if value.get("availability") == "observed" and (
        request["expected_head_session_id"] is None
        or value["head_session_id"] != request["expected_head_session_id"]
    ):
        raise contract.ObservationError(
            "owner response qualified facts for another head", "invalid-response"
        )
    if history and (
        value["type"] != "history-page"
        or value["source_generation"] != request["source_generation"]
        or value["before_cursor"] != request["before_cursor"]
        or len(value["entries"]) > request["limit"]
    ):
        raise contract.ObservationError(
            "owner page changed the requested boundary", "invalid-response"
        )
    return value


class Receiver:
    def __init__(self, request: JsonObject, generation: str) -> None:
        self.request, self.generation = request, generation
        self.previous: JsonObject | None = None
        self.closed = False

    def receive(self, frame: JsonObject) -> None:
        if self.closed:
            raise contract.ObservationError("post-terminal owner frame", "invalid-response")
        validate(frame, self.request, self.generation)
        kind = frame["type"]
        if kind == "error":
            self.closed = True
            return
        if self.previous is None:
            if kind != "snapshot" or frame["base_cursor"] != self.request["after_cursor"]:
                raise contract.ObservationError(
                    "owner feed did not start with the requested snapshot", "invalid-response"
                )
            self.previous = frame
            return
        previous_source, current_source = source(self.previous), source(frame)
        if (
            current_source.source != previous_source.source
            or current_source.source_generation != previous_source.source_generation
        ):
            raise contract.ObservationError("owner journal changed", "source-replaced")
        if kind in {"heartbeat", "going-away"}:
            if (
                frame["cursor"] != self.previous["cursor"]
                or frame["head_session_id"] != self.previous["head_session_id"]
            ):
                raise contract.ObservationError("owner notice changed state", "invalid-response")
            self.closed = kind == "going-away"
            return
        if kind == "gap":
            return
        if kind not in {"snapshot", "delta"}:
            raise contract.ObservationError("unexpected owner feed frame", "invalid-response")
        if frame["base_cursor"] != self.previous["cursor"] or contract.sequence(
            frame["cursor"], current_source
        ) < contract.sequence(self.previous["cursor"], previous_source):
            raise contract.ObservationError(
                "owner feed regressed or lost its base", "invalid-response"
            )
        self.previous = frame
