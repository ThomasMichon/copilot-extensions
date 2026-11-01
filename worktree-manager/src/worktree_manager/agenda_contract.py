"""Bounded admission of display-only agenda publications across the CLI seam."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone

MAX_FRAME_BYTES = 35 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_INTEGER = 2**63 - 1
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_SNAPSHOT_FIELDS = {
    "worktree", "revision", "status", "root", "stack", "next_action",
    "exit_criteria", "declared_outcomes", "queued", "parked", "notes",
    "cursor_version", "deferred", "due_rechecks", "next_revisit_at",
}
_REPLY_FIELDS = {
    "ok", "contract_version", "generation", "sequence", "project",
    "worktree_id", "session_id", "availability", "observation",
}
_TRANSIENT_ERRORS = {"disconnected", "unavailable", "unready", "draining", "superseded"}
_TEMPORARY_CLOSURES = _TRANSIENT_ERRORS - {"superseded"}
CONTRACT_VERSIONS = (1, 2)


class AgendaUnavailable(ValueError):
    def __init__(self, message: str, *, retry: bool = False, code: str | None = None) -> None:
        super().__init__(message)
        self.retry = retry
        self.code = code


def bounded_json(value, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("agenda data exceeds its nesting bound")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 4096 or "\x00" in value:
            raise ValueError("agenda string exceeds its bound")
    elif isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("agenda object exceeds its member bound")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("agenda object has a non-string key")
            bounded_json(key, depth + 1)
            bounded_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise ValueError("agenda array exceeds its member bound")
        for item in value:
            bounded_json(item, depth + 1)
    elif value is not None and type(value) not in {int, bool, float}:
        raise ValueError("agenda data is not JSON")
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("agenda data contains a non-finite number")
    elif type(value) is int and not -MAX_INTEGER <= value <= MAX_INTEGER:
        raise ValueError("agenda data exceeds its integer bound")


def _positive(value) -> bool:
    return type(value) is int and 0 < value <= MAX_INTEGER


def _utc(value) -> bool:
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    except ValueError:
        return False


def validate_snapshot(
    frame: dict, project: str, worktree: str, session: str, *, contract_version: int = 1,
) -> None:
    """Validate read and feed snapshots without importing the domain owner."""
    bounded_json(frame)
    if not isinstance(frame, dict):
        raise ValueError("agenda publication is not an object")
    fields = _REPLY_FIELDS | ({"type"} if "type" in frame else set())
    if set(frame) != fields:
        raise ValueError("agenda publication has invalid fields")
    if len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 > MAX_FRAME_BYTES:
        raise ValueError("agenda frame exceeds its bound")
    if (
        frame.get("ok") is not True or type(frame.get("contract_version")) is not int
        or type(contract_version) is not int or contract_version not in CONTRACT_VERSIONS
        or frame["contract_version"] != contract_version
        or (frame.get("project"), frame.get("worktree_id"), frame.get("session_id"))
        != (project, worktree, session)
        or not isinstance(frame.get("generation"), str) or not _HEX.fullmatch(frame["generation"])
        or type(frame.get("sequence")) is not int or not 0 <= frame["sequence"] <= 2**63 - 1
    ):
        raise ValueError("agenda publication has an invalid contract or selected source")
    availability, observation = frame.get("availability"), frame.get("observation")
    if not isinstance(availability, str):
        raise ValueError("agenda publication has non-string availability")
    if availability in {"not-published", "head-changed"}:
        if observation is not None or "observation" not in frame:
            raise ValueError("unavailable agenda publication retained active data")
        return
    if availability != "observed" or not isinstance(observation, dict):
        raise ValueError("agenda publication has invalid availability or observation")
    bounded_json(observation, depth=4)
    if len(json.dumps(observation, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 > MAX_BODY_BYTES:
        raise ValueError("agenda observation exceeds its body bound")
    source, snapshot = observation.get("source"), observation.get("snapshot")
    if (
        set(observation) != {"schema", "version", "source", "revision", "committed_at", "snapshot"}
        or
        observation.get("schema") != "efforts.agenda-observation"
        or type(observation.get("version")) is not int or observation["version"] not in (1, 2)
        or (contract_version == 1 and observation["version"] != 1)
        or not isinstance(source, dict)
        or set(source) != {"worktree", "session", "store_id"}
        or (source.get("worktree"), source.get("session")) != (worktree, session)
        or not isinstance(source.get("store_id"), str) or not _HEX.fullmatch(source["store_id"])
        or not _positive(observation.get("revision"))
        or not _utc(observation.get("committed_at"))
        or not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS
        or snapshot["worktree"] != worktree or type(snapshot["revision"]) is not int
        or snapshot["revision"] != observation["revision"]
        or not isinstance(snapshot["status"], str) or not snapshot["status"]
        or type(snapshot["cursor_version"]) is not int
        or snapshot["cursor_version"] not in (
            (1, 2) if observation["version"] == 1 else (4,)
        )
    ):
        raise ValueError("agenda observation has an invalid identity, revision or snapshot")
    if observation["version"] == 2 and snapshot["status"] not in {
        "working", "waiting", "complete", "replanning",
    }:
        raise ValueError("agenda observation has an invalid cursor-4 status")
    for field in (
        "stack", "exit_criteria", "queued", "notes", "deferred", "due_rechecks",
    ):
        if not isinstance(snapshot[field], list):
            raise ValueError(f"agenda snapshot has invalid {field}")
    for field in ("stack", "exit_criteria", "queued", "notes", "due_rechecks"):
        if any(not isinstance(item, str) for item in snapshot[field]):
            raise ValueError(f"agenda snapshot has invalid {field} text")
    if (
        not isinstance(snapshot["root"], str)
        or not isinstance(snapshot["declared_outcomes"], dict)
        or any(not isinstance(value, str) for value in snapshot["declared_outcomes"].values())
        or type(snapshot["parked"]) is not int or not 0 <= snapshot["parked"] <= MAX_INTEGER
    ):
        raise ValueError("agenda snapshot has invalid root, outcomes or parked count")
    for field in ("next_action", "next_revisit_at"):
        if snapshot[field] is not None and not isinstance(snapshot[field], str):
            raise ValueError(f"agenda snapshot has invalid {field}")


class AgendaPublicationFence:
    """Bounded evidence for one selected head, independent of display availability."""

    def __init__(self) -> None:
        self.last: dict | None = None
        self.observed: dict | None = None

    @staticmethod
    def _content(value: dict) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    def admit(self, frame: dict) -> None:
        if self.last is not None and frame["generation"] == self.last["generation"]:
            if frame["sequence"] < self.last["sequence"]:
                raise ValueError("agenda reconnect regressed its resident sequence")
            if frame["sequence"] == self.last["sequence"] and self._content(
                {key: value for key, value in frame.items() if key not in {"type", "contract_version"}}
            ) != self._content({
                key: value for key, value in self.last.items() if key not in {"type", "contract_version"}
            }):
                raise ValueError("agenda publication changed without advancing its resident sequence")
        observation = frame["observation"]
        if observation is not None and self.observed is not None:
            if observation["source"]["store_id"] != self.observed["source"]["store_id"]:
                raise ValueError("agenda source store changed for the selected head")
            if observation["revision"] < self.observed["revision"] or (
                observation["revision"] == self.observed["revision"]
                and self._content(observation) != self._content(self.observed)
            ):
                raise ValueError("agenda observation regressed or rewrote its store revision")
        # Commit both fences only after all checks; null availability never
        # clears the last observed store's revision/body evidence.
        self.last = deepcopy(frame)
        if observation is not None:
            self.observed = deepcopy(observation)


class AgendaFeed:
    """One connection, one atomic first snapshot, one resident generation."""

    def __init__(
        self, project: str, worktree: str, session: str, *,
        fence: AgendaPublicationFence | None = None,
        contract_version: int = 1,
    ) -> None:
        self.identity = (project, worktree, session)
        self.current: dict | None = None
        self.closed = False
        self.retired_generation: str | None = None
        self.fence = fence if fence is not None else AgendaPublicationFence()
        self.set_contract_version(contract_version)

    def set_contract_version(self, version: int) -> None:
        if type(version) is not int or version not in CONTRACT_VERSIONS or self.current is not None:
            raise ValueError("invalid or late agenda contract selection")
        self.contract_version = version

    def receive(self, frame: dict) -> dict | None:
        bounded_json(frame)
        if not isinstance(frame, dict) or self.closed:
            raise ValueError("agenda feed emitted an invalid or post-terminal frame")
        kind = frame.get("type")
        if not isinstance(kind, str):
            raise ValueError("agenda feed emitted a non-string frame type")
        if kind == "error":
            error = frame.get("error")
            if (
                set(frame) != {"type", "ok", "contract_version", "error"}
                or frame.get("ok") is not False or type(frame.get("contract_version")) is not int
                or frame["contract_version"] != self.contract_version or not isinstance(error, dict)
                or set(error) != {"code", "message"}
                or not isinstance(error.get("code"), str) or not error["code"]
                or not isinstance(error.get("message"), str) or not error["message"]
            ):
                raise ValueError("agenda feed omitted its structured error")
            if error["code"] in {"superseded", "closed", "shutdown"} and self.current is not None:
                self.retired_generation = self.current["generation"]
            raise AgendaUnavailable(
                f"{error['code']}: {error['message']}", retry=error["code"] in _TRANSIENT_ERRORS,
                code=error["code"],
            )
        if kind in {"heartbeat", "going-away"}:
            if (
                set(frame) != {"type", "generation", "sequence"} | ({"reason"} if kind == "going-away" else set())
                or self.current is None
                or frame.get("generation") != self.current["generation"]
                or type(frame.get("sequence")) is not int
                or frame["sequence"] != self.current["sequence"]
            ):
                raise ValueError("agenda control frame changed generation or sequence")
            if kind == "going-away":
                if not isinstance(frame.get("reason"), str) or not frame["reason"]:
                    raise ValueError("agenda going-away omitted its reason")
                self.closed = True
                if frame["reason"] not in _TEMPORARY_CLOSURES:
                    self.retired_generation = self.current["generation"]
                raise AgendaUnavailable(
                    f"provider going away: {frame['reason']}", retry=True, code=frame["reason"],
                )
            return None
        if kind not in {"snapshot", "update"}:
            raise ValueError("agenda feed emitted an unknown frame type")
        validate_snapshot(frame, *self.identity, contract_version=self.contract_version)
        if self.current is None:
            if kind != "snapshot":
                raise ValueError("agenda feed did not start with an atomic snapshot")
        else:
            previous = self.current
            if (
                kind != "update" or frame["generation"] != previous["generation"]
                or frame["sequence"] <= previous["sequence"]
                or (frame["availability"], frame["observation"])
                == (previous["availability"], previous["observation"])
            ):
                raise ValueError("agenda feed repeated or regressed its publication")
        self.fence.admit(frame)
        self.current = frame
        if frame["availability"] == "head-changed":
            self.closed = True
        return frame
