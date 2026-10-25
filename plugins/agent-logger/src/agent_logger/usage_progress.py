"""Rebuildable, content-free transcript progress for bounded scheduled passes."""

from __future__ import annotations

import hashlib
import json
import os
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_logger import usage_transcript
from agent_logger.session_codecs import _file_identity, _file_revision
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.usage_ledger import MAX_BYTES, MAX_RECORDS, UsageEvidenceError, UsageObservations

SCHEMA = "agent-logger.transcript-progress"


@dataclass
class TranscriptBudget:
    remaining: int
    last_session: str | None = None


@dataclass(frozen=True)
class TranscriptProgress:
    records: list[dict]
    complete: bool
    consumed: int
    offset: int
    size: int
    rebuilt: bool = False
    reason: str = "work budget"


def _encode(value: dict) -> bytes:
    output = bytearray()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for part in encoder.iterencode(value):
        output.extend(part.encode("utf-8"))
        if len(output) > MAX_BYTES:
            raise UsageEvidenceError("transcript progress byte limit exceeded")
    return bytes(output)


def _read_state(directory, name: str, limit: int) -> tuple[dict | None, bool]:
    try:
        with directory.open(name, protected=True) as stream:
            if os.fstat(stream.fileno()).st_size > limit:
                return None, True
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, False
    try:
        state = json.loads(raw)
        digest = state.pop("digest")
        if len(raw) > limit or digest != hashlib.sha256(_encode(state)).hexdigest():
            return None, True
        return state, False
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None, True


def _load(directory, name: str, source: str, session_id: str) -> tuple[dict | None, bool]:
    state, rebuilt = _read_state(directory, name, MAX_BYTES)
    if state is None:
        return None, rebuilt
    try:
        if (
            set(state) != {"schema", "version", "source", "session_id", "revision", "offset", "events", "records"}
            or state["schema"] != SCHEMA or type(state["version"]) is not int or state["version"] != 1
            or state["source"] != source or state["session_id"] != session_id
            or not isinstance(state["revision"], list) or len(state["revision"]) != 5
            or any(type(value) is not int or value < 0 for value in state["revision"])
            or type(state["offset"]) is not int
            or not 0 <= state["offset"] <= state["revision"][2]
            or type(state["events"]) is not int or not 0 <= state["events"] <= state["offset"]
            or not isinstance(state["records"], list) or len(state["records"]) > MAX_RECORDS
            or any(not isinstance(row, dict) or row.get("source") != "transcript" for row in state["records"])
        ):
            return None, True
        records = UsageObservations(session_id)
        for row in state["records"]:
            records.add(dict(row, session_id=session_id))
        normalized = records.records()
        if normalized != state["records"] or sum(row["multiplicity"] for row in normalized) > state["events"]:
            return None, True
        return state, False
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None, True


@contextmanager
def capture_order(cache_root: Path | None, source: Path, sessions: list[str], budget: TranscriptBudget):
    if cache_root is None or not sessions:
        yield sessions, False
        return
    identity = os.path.normcase(str(source.absolute()))
    name = "capture-" + hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    with publication_directory(cache_root, create=not cache_root.exists()) as directory:
        with sync_lock(directory.path / f".{name}.lock", timeout=0, wait=False) as acquired:
            if not acquired:
                raise UsageEvidenceError("accounting collection order writer is busy")
            directory.unlink(f"{name}.tmp")
            saved, rebuilt = _read_state(directory, name, 4096)
            if saved is not None and (
                set(saved) != {"schema", "source", "last_session"}
                or saved.get("schema") != "agent-logger.capture-order"
                or saved.get("source") != identity
                or not isinstance(saved.get("last_session"), str)
                or not 1 <= len(saved["last_session"]) <= 128
            ):
                saved, rebuilt = None, True
            index = bisect_right(sessions, saved["last_session"]) if saved else 0
            ordered = sessions[index:] + sessions[:index]
            state = {
                "schema": "agent-logger.capture-order", "source": identity,
                "last_session": ordered[0],
            }
            # Reserve progress before work so even a killed first reader rotates.
            _save(directory, name, state)
            try:
                yield ordered, rebuilt
            finally:
                if budget.last_session is not None and budget.last_session != ordered[0]:
                    _save(directory, name, dict(state, last_session=budget.last_session))


def _save(directory, name: str, state: dict) -> None:
    output = _encode(dict(state, digest=hashlib.sha256(_encode(state)).hexdigest()))
    temporary = f"{name}.tmp"
    try:
        with directory.create(temporary) as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        directory.replace(temporary, name)
    finally:
        directory.unlink(temporary)


def read_transcript_progress(
    session: Path, session_id: str, cache_root: Path, *,
    byte_budget: int | None = None, event_budget: int | None = None,
    total_budget: TranscriptBudget | None = None,
) -> TranscriptProgress:
    """Resume only an unchanged source revision; revalidate changed generations.

    Appends, rewrites and replacements start at byte zero, rather than trusting
    a sampled boundary as proof that an old prefix is unchanged. Stable finite
    histories converge over bounded passes; unchanged completed sources need
    no transcript reads. The cache is disposable and never authorizes compaction.
    """
    byte_budget = usage_transcript.MAX_TRANSCRIPT_BYTES if byte_budget is None else byte_budget
    event_budget = usage_transcript.MAX_EVENTS if event_budget is None else event_budget
    if type(byte_budget) is not int or byte_budget < 0 or type(event_budget) is not int or event_budget < 1:
        raise ValueError("invalid transcript work budget")
    if session.name != session_id:
        raise UsageEvidenceError("transcript session identity mismatch")
    if total_budget is not None:
        if type(total_budget.remaining) is not int or total_budget.remaining < 0:
            raise ValueError("invalid aggregate transcript work budget")
        byte_budget = min(byte_budget, total_budget.remaining)
    source = os.path.normcase(str(session.absolute()))
    name = hashlib.sha256(source.encode("utf-8")).hexdigest() + ".json"
    with publication_directory(cache_root, create=not cache_root.exists()) as cache:
        with sync_lock(cache.path / f".{name}.lock", timeout=0, wait=False) as acquired:
            if not acquired:
                raise UsageEvidenceError("transcript progress writer is busy")
            cache.unlink(f"{name}.tmp")
            saved, rebuilt = _load(cache, name, source, session_id)
            with publication_directory(session, create=False) as directory:
                with directory.open("events.jsonl") as stream:
                    before = os.fstat(stream.fileno())
                    named = directory.stat("events.jsonl")
                    if _file_identity(before) != _file_identity(named):
                        raise UsageEvidenceError("transcript changed while opening")
                    revision = list(_file_revision(before))
                    state = saved if saved and saved["revision"] == revision else {
                        "schema": SCHEMA, "version": 1, "source": source, "session_id": session_id,
                        "revision": revision, "offset": 0, "events": 0, "records": [],
                    }
                    offset = state["offset"]
                    stream.seek(offset)
                    records = UsageObservations(session_id)
                    for row in state["records"]:
                        records.add(dict(row, session_id=session_id))
                    consumed = events = 0
                    reason = "work budget"
                    while offset < before.st_size and consumed < byte_budget and events < event_budget:
                        remaining = byte_budget - consumed
                        line = stream.readline(min(usage_transcript.MAX_LINE_BYTES + 1, remaining))
                        consumed += len(line)
                        if total_budget is not None:
                            total_budget.remaining -= len(line)
                            if line:
                                total_budget.last_session = session_id
                        if not line:
                            raise UsageEvidenceError("transcript ended before its snapshot size")
                        if len(line) > usage_transcript.MAX_LINE_BYTES:
                            raise UsageEvidenceError("transcript read limit exceeded")
                        if not line.endswith(b"\n"):
                            if stream.tell() == before.st_size:
                                reason = "partial tail"
                            break
                        record = usage_transcript.project_usage_line(line, session_id)
                        if record is not None:
                            records.add(record)
                        offset = stream.tell()
                        events += 1
                    if (
                        _file_revision(before) != _file_revision(os.fstat(stream.fileno()))
                        or _file_revision(named) != _file_revision(directory.stat("events.jsonl"))
                    ):
                        raise UsageEvidenceError("transcript changed during accounting capture")
                    state = dict(
                        state, offset=offset, events=state["events"] + events,
                        records=records.records(),
                    )
            if state != saved:
                _save(cache, name, state)
            return TranscriptProgress(
                [dict(row, session_id=session_id) for row in state["records"]],
                offset == before.st_size, consumed, offset, before.st_size, rebuilt, reason,
            )
