"""Original Dispatch assignment evidence, not current placement or cleanup authority."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

SCHEMA = "dispatch-assignment-cohort"
VERSION = 1
TEXT_MAX = 512
MAX_COUNTER = (1 << 63) - 1
MAX_JSON_BYTES = 16 * 1024


def capability() -> dict:
    """Describe the optional creation carrier separately from package versions."""
    return {"schema": SCHEMA, "version": VERSION}


def _reference(value: object, strings: set[str], counters: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != strings | counters:
        raise ValueError("invalid dispatch cohort reference fields")
    if any(
        not isinstance(value[key], str) or not value[key].strip()
        or len(value[key]) > TEXT_MAX or "\0" in value[key]
        for key in strings
    ) or any(
        type(value[key]) is not int or not 0 <= value[key] <= MAX_COUNTER
        for key in counters
    ):
        raise ValueError("invalid dispatch cohort reference values")
    return value


@dataclass(frozen=True)
class DispatchCohort:
    """Retain nested evidence as canonical JSON so frozen attempts stay immutable."""

    encoded: str

    @classmethod
    def from_attempt(cls, attempt: dict[str, object]) -> DispatchCohort:
        """Reject provenance that cannot survive tracking reload before allocation."""
        if (
            type(attempt.get("attempt")) is not int
            or not 1 <= attempt["attempt"] <= MAX_COUNTER
            or any(
                not isinstance(attempt.get(key), str) or not attempt[key].strip()
                or len(attempt[key].strip()) > TEXT_MAX
                for key in ("driver", "supervisor")
            )
        ):
            raise ValueError("invalid dispatch cohort attempt fields")
        return cls.from_fields(
            attempt["cohort"], task_id=attempt["task_id"],
            reservation_key=attempt["reservation_key"],
        )

    @classmethod
    def from_fields(
        cls, value: object, *, task_id: str, reservation_key: str,
    ) -> DispatchCohort:
        """Validate original references against the creating task and reservation."""
        if any(
            not isinstance(item, str) or not item or item != item.strip()
            or len(item) > TEXT_MAX or "\0" in item
            for item in (task_id, reservation_key)
        ):
            raise ValueError("dispatch cohort requires canonical task and reservation IDs")
        if (
            not isinstance(value, dict) or set(value) != {"schema", "version", "emitter", "pool"}
            or value["schema"] != SCHEMA or type(value["version"]) is not int
            or value["version"] != VERSION
        ):
            raise ValueError("unsupported dispatch cohort schema/version")
        emitter, pool = value["emitter"], value["pool"]
        if emitter is not None:
            _reference(
                emitter, {"repo", "source", "request_id", "producer_id", "task_id"}, {"generation"},
            )
            if emitter["task_id"] != task_id:
                raise ValueError("dispatch cohort emitter names a different task")
        if pool is not None:
            _reference(
                pool, {"task_id", "registration_id", "machine", "env", "reservation_key"},
                {"task_generation", "catalog_revision"},
            )
            if pool["task_id"] != task_id or pool["reservation_key"] != reservation_key:
                raise ValueError("dispatch cohort pool names a different task or reservation")
        return cls(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))

    def to_dict(self) -> dict:
        """Return a detached view, never a mutable reference into original evidence."""
        return json.loads(self.encoded)


def parse_json(value: str, *, task_id: str, reservation_key: str) -> DispatchCohort:
    """Bound and reject duplicate JSON fields at the optional CLI boundary."""
    if len(value.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("dispatch cohort JSON exceeds its input bound")

    def unique(pairs: list[tuple[str, object]]) -> dict:
        fields = {}
        for key, item in pairs:
            if key in fields:
                raise ValueError("duplicate dispatch cohort JSON field")
            fields[key] = item
        return fields

    try:
        fields = json.loads(value, object_pairs_hook=unique)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"invalid dispatch cohort JSON: {exc}") from exc
    return DispatchCohort.from_fields(fields, task_id=task_id, reservation_key=reservation_key)


def preserve(record: WorktreeRecord, current: WorktreeRecord | None) -> None:
    """Keep first-save attribution through stale saves, reuse and opaque readers."""
    if current is None:
        return
    original = (
        current.dispatch_attempt.to_dict() if current.dispatch_attempt is not None
        else current.dispatch_attempt_raw
    )
    proposed = record.dispatch_attempt.to_dict() if record.dispatch_attempt is not None else record.dispatch_attempt_raw
    if isinstance(original, dict) and "cohort" in original:
        record.dispatch_attempt = current.dispatch_attempt
        record.dispatch_attempt_opaque = current.dispatch_attempt_opaque
        record.dispatch_attempt_raw = current.dispatch_attempt_raw
        record.dispatch_attempt_raw_present = current.dispatch_attempt_raw_present
    elif isinstance(proposed, dict) and "cohort" in proposed:
        raise ValueError("original dispatch cohort cannot be backfilled on an existing worktree")


def require_record(record: WorktreeRecord, facts: dict) -> None:
    """Require the journal's original evidence, including honest legacy absence."""
    actual = (
        record.dispatch_attempt.cohort.to_dict()
        if record.dispatch_attempt is not None and record.dispatch_attempt.cohort is not None else None
    )
    if actual != facts.get("dispatch_cohort") or (
        actual is not None
        and record.dispatch_attempt.task_id != facts.get("dispatch_task_id")
    ):
        raise ValueError("worktree differs from its original dispatch cohort")
