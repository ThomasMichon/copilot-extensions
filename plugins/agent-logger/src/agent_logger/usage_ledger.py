"""Content-free, source-qualified session accounting evidence; never an invoice."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

from agent_logger.sync.lock import sync_lock
from agent_logger.sync.provenance import short_unique_id
from agent_logger.sync.publication_directory import publication_directory

ARTIFACT = "usage-evidence.json"
SCHEMA = "agent-logger.usage-evidence"
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 20000
TEXT_FIELDS = frozenset({
    "created_at", "model", "agent_id", "parent_tool_call_id", "api_call_id",
    "usage_id", "source_session_id", "event_id",
})
INTEGER_FIELDS = frozenset({
    "turn_index", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_nano_aiu", "sequence",
})
NUMBER_FIELDS = frozenset({"duration_ms", "request_multiplier"})
IDENTITY_FIELDS = (
    "created_at", "model", "agent_id", "parent_tool_call_id", "turn_index",
    "api_call_id", "source_session_id", "sequence", "usage_id", "event_id",
)


class UsageEvidenceError(ValueError):
    """Unsafe, incompatible, conflicting or incomplete accounting evidence."""


def evidence_lock_path(session: Path) -> Path:
    """Keep producer-only lock writes outside the signed session tree."""
    return session.with_name(f".{session.name}.usage-evidence.lock")


def validate_values(values: object) -> dict:
    """Reject extra fields and invalid scalars rather than transporting content."""
    if not isinstance(values, dict) or not values:
        raise UsageEvidenceError("accounting values must be a nonempty object")
    for key, value in values.items():
        if key not in TEXT_FIELDS | INTEGER_FIELDS | NUMBER_FIELDS:
            raise UsageEvidenceError("accounting field is outside the scalar allowlist")
        if value is None:
            continue
        if key in TEXT_FIELDS:
            if not isinstance(value, str) or len(value) > 2048:
                raise UsageEvidenceError("accounting text field is invalid")
        elif key in INTEGER_FIELDS:
            if type(value) is not int or value < 0:
                raise UsageEvidenceError("accounting integer field is invalid")
        elif (
            type(value) not in (int, float) or value < 0 or value > sys.float_info.max
            or (type(value) is float and not math.isfinite(value))
        ):
            raise UsageEvidenceError("accounting numeric field is invalid")
    return dict(values)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def normalize_record(record: object, session_id: str) -> dict:
    if not isinstance(record, dict) or record.get("session_id") != session_id:
        raise UsageEvidenceError("accounting session identity mismatch")
    source = record.get("source")
    if source not in ("session-store", "transcript"):
        raise UsageEvidenceError("unknown accounting source")
    values = validate_values(record.get("values"))
    if not values.get("created_at"):
        raise UsageEvidenceError("accounting timestamp is unavailable")
    if values.get("usage_id"):
        identity = {key: values.get(key) for key in ("usage_id", "source_session_id")}
    elif values.get("event_id"):
        identity = {"event_id": values["event_id"]}
    else:
        identity = {key: values.get(key) for key in IDENTITY_FIELDS}
    key = hashlib.sha256(_canonical([source, session_id, identity]).encode()).hexdigest()
    multiplicity = record.get("multiplicity", 1)
    if type(multiplicity) is not int or not 1 <= multiplicity <= MAX_RECORDS:
        raise UsageEvidenceError("invalid observation multiplicity")
    return {"key": key, "source": source, "values": values, "multiplicity": multiplicity}


class UsageObservations:
    """Keyed aggregation of one source snapshot, with linear per-row work."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._records: dict[str, dict] = {}

    def add(self, record: dict) -> None:
        self._add_normalized(normalize_record(record, self.session_id), replay=False)

    def _add_normalized(self, record: dict, *, replay: bool) -> None:
        key = record["key"]
        previous = self._records.get(key)
        if previous is None:
            self._records[key] = dict(record, values=dict(record["values"]))
            if len(self._records) > MAX_RECORDS:
                raise UsageEvidenceError("accounting record limit exceeded")
            return
        for field, value in record["values"].items():
            old = previous["values"].get(field)
            if old is not None and value is not None and old != value:
                raise UsageEvidenceError("conflicting source-qualified accounting observation")
            if old is None:
                previous["values"][field] = value
        native = any(record["values"].get(field) for field in ("usage_id", "event_id", "api_call_id"))
        count = (
            max(previous["multiplicity"], record["multiplicity"]) if replay or native
            else previous["multiplicity"] + record["multiplicity"]
        )
        if count > MAX_RECORDS:
            raise UsageEvidenceError("too many identical accounting observations")
        previous["multiplicity"] = count

    def merge_snapshot(self, incoming: UsageObservations) -> None:
        if incoming.session_id != self.session_id:
            raise UsageEvidenceError("accounting session identity mismatch")
        for record in incoming._records.values():
            self._add_normalized(record, replay=True)

    def records(self) -> list[dict]:
        return [self._records[key] for key in sorted(self._records)]


def _merge(records: list, incoming: list, session_id: str) -> list[dict]:
    saved = UsageObservations(session_id)
    replay = UsageObservations(session_id)
    for batch, accumulator, is_replay in ((records, saved, False), (incoming, replay, True)):
        for original in batch:
            if not isinstance(original, dict):
                raise UsageEvidenceError("accounting observation is not an object")
            if is_replay and original.get("session_id") != session_id:
                raise UsageEvidenceError("accounting session identity mismatch")
            accumulator.add(dict(original, session_id=session_id))
    saved.merge_snapshot(replay)
    return saved.records()


def merge_session_evidence(
    session: Path, session_id: str, records: list[dict], *, coverage: dict[str, str],
) -> bool:
    """Atomically retain prior evidence; identical publication preserves mtime.

    Native IDs remain source-qualified. Weak identity collisions are explicit
    conflicts, and identical weak rows retain their observed multiplicity.
    Callers must not sum competing database/transcript observations.
    """
    if session.name != session_id:
        raise UsageEvidenceError("accounting destination identity mismatch")
    if set(coverage) != {"session-store", "transcript"} or any(
        value not in ("captured", "missing") for value in coverage.values()
    ):
        raise UsageEvidenceError("invalid accounting coverage")
    with publication_directory(session, create=False) as directory:
        with sync_lock(evidence_lock_path(session), timeout=0, wait=False) as acquired:
            if not acquired:
                raise UsageEvidenceError("accounting evidence writer is busy")
            prior: bytes | None = None
            try:
                with directory.open(ARTIFACT, protected=True) as stream:
                    if os.fstat(stream.fileno()).st_size > MAX_BYTES:
                        raise UsageEvidenceError("accounting artifact byte limit exceeded")
                    prior = stream.read(MAX_BYTES + 1)
                if len(prior) > MAX_BYTES:
                    raise UsageEvidenceError("accounting artifact byte limit exceeded")
                saved = json.loads(prior)
                if (
                    not isinstance(saved, dict) or saved.get("schema") != SCHEMA
                    or saved.get("version") != 1 or saved.get("session_id") != session_id
                    or not isinstance(saved.get("records"), list)
                    or len(saved["records"]) > MAX_RECORDS
                ):
                    raise UsageEvidenceError("accounting artifact schema is invalid")
                old = saved["records"]
                if (
                    not isinstance(saved.get("coverage"), dict)
                    or set(saved["coverage"]) != {"session-store", "transcript"}
                    or any(value not in ("captured", "missing") for value in saved["coverage"].values())
                ):
                    raise UsageEvidenceError("accounting artifact coverage is invalid")
            except FileNotFoundError:
                old = []
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise UsageEvidenceError("accounting artifact is corrupt") from exc
            combined = _merge(old, records, session_id)
            payload = {
                "schema": SCHEMA, "version": 1, "session_id": session_id,
                "coverage": coverage, "records": combined,
                "deduplication": "source-qualified; competing sources are not additive",
            }
            output = bytearray()
            encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2)
            for part in encoder.iterencode(payload):
                output.extend(part.encode("utf-8"))
                if len(output) >= MAX_BYTES:
                    raise UsageEvidenceError("accounting artifact byte limit exceeded")
            output.extend(b"\n")
            if bytes(output) == prior:
                return False
            temporary = f"usage-evidence.json.{short_unique_id()}.tmp"
            try:
                with directory.create(temporary) as stream:
                    stream.write(output)
                    stream.flush()
                    os.fsync(stream.fileno())
                directory.replace(temporary, ARTIFACT)
            finally:
                directory.unlink(temporary)
            return True
