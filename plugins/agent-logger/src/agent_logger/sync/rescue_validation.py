"""Validation for untrusted provider rescue captures."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_logger.sync.provenance import (
    is_link_or_reparse,
    open_regular_no_follow,
)

SUPPORTED_PROVIDER = "agent-containers"
ALLOWED_MEMBERS = (
    "events.jsonl",
    "workspace.yaml",
    "origin.json",
    "context.json",
    "agent-worktrees.json",
    "checkpoints/index.md",
)
AGENT_WORKTREES_PROJECTION = "agent-worktrees.json"
AGENT_WORKTREES_SCHEMA_VERSION = 1
MAX_AGENT_WORKTREES_BYTES = 128 * 1024
MAX_AGENT_WORKTREES_JSON_DEPTH = 64
SOFT_OPTIONAL_MEMBERS = {AGENT_WORKTREES_PROJECTION}

_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_METADATA_BYTES = 8 * 1024 * 1024


class RescueSourceError(ValueError):
    """A rescue source or checkpoint is unsafe or malformed."""


@dataclass(frozen=True)
class Member:
    """One provider-declared and independently verified session member."""

    relative: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class RescuedSession:
    """One valid session candidate from a complete provider capture."""

    session_id: str
    venue_id: str
    target_id: str
    capture_id: str
    capture_path: str
    capture_order: tuple[float, str]
    captured_at: str
    container: str
    container_instance: str
    container_generation: str
    fleet: str | None
    source_repo: str | None
    members: tuple[Member, ...]
    capture_fingerprint: str

    @property
    def checkpoint_key(self) -> str:
        return f"{SUPPORTED_PROVIDER}|{self.venue_id}|{self.session_id}"

    @property
    def capture_key(self) -> str:
        return f"{SUPPORTED_PROVIDER}|{self.venue_id}|{self.capture_id}"

    @property
    def member_fingerprint(self) -> str:
        payload = [
            {"path": member.relative, "bytes": member.size, "sha256": member.sha256}
            for member in self.members
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CaptureResult:
    """Validated sessions plus explicit capture/session compatibility notes."""

    sessions: tuple[RescuedSession, ...]
    rejected_sessions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    capture_key: str = ""
    capture_fingerprint: str = ""


def lstat_kind(path: Path) -> int:
    """Return an lstat mode or raise a source error."""
    try:
        return path.lstat().st_mode
    except OSError as exc:
        raise RescueSourceError(f"cannot inspect {path}: {exc}") from exc


def require_directory(path: Path, label: str) -> None:
    """Require a real directory rather than a symlink or special file."""
    mode = lstat_kind(path)
    if is_link_or_reparse(path, mode) or not stat.S_ISDIR(mode):
        raise RescueSourceError(f"{label} is not a regular directory: {path}")


def read_regular(path: Path, *, max_bytes: int | None = None) -> bytes:
    """Read one bounded regular file without following a final symlink."""
    mode = lstat_kind(path)
    if is_link_or_reparse(path, mode) or not stat.S_ISREG(mode):
        raise RescueSourceError(f"not a regular file: {path}")
    size = path.lstat().st_size
    if max_bytes is not None and size > max_bytes:
        raise RescueSourceError(f"file exceeds {max_bytes} bytes: {path}")
    try:
        stream = open_regular_no_follow(path)
    except OSError as exc:
        raise RescueSourceError(f"cannot open {path}: {exc}") from exc
    with stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise RescueSourceError(f"not a regular file: {path}")
        if max_bytes is not None and opened.st_size > max_bytes:
            raise RescueSourceError(f"file exceeds {max_bytes} bytes: {path}")
        data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise RescueSourceError(f"file exceeds {max_bytes} bytes: {path}")
        return data


def _hash_regular(path: Path, expected_size: int) -> str:
    mode = lstat_kind(path)
    if is_link_or_reparse(path, mode) or not stat.S_ISREG(mode):
        raise RescueSourceError(f"not a regular file: {path}")
    try:
        stream = open_regular_no_follow(path)
    except OSError as exc:
        raise RescueSourceError(f"cannot open {path}: {exc}") from exc
    digest = hashlib.sha256()
    read_size = 0
    with stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise RescueSourceError(f"not a regular file: {path}")
        if opened.st_size != expected_size:
            raise RescueSourceError(
                f"size mismatch for {path}: expected {expected_size}, got {opened.st_size}"
            )
        while chunk := stream.read(min(1024 * 1024, expected_size - read_size + 1)):
            digest.update(chunk)
            read_size += len(chunk)
            if read_size > expected_size:
                break
    if read_size != expected_size:
        raise RescueSourceError(
            f"size changed while reading {path}: expected {expected_size}, got {read_size}"
        )
    return digest.hexdigest()


def _require_regular_size(path: Path, expected_size: int) -> None:
    mode = lstat_kind(path)
    if is_link_or_reparse(path, mode) or not stat.S_ISREG(mode):
        raise RescueSourceError(f"not a regular file: {path}")
    actual_size = path.lstat().st_size
    if actual_size != expected_size:
        raise RescueSourceError(
            f"size mismatch for {path}: expected {expected_size}, got {actual_size}"
        )


def _validate_events_jsonl(path: Path) -> None:
    """Stream-validate that each nonblank UTF-8 JSONL record is an object."""
    try:
        raw = open_regular_no_follow(path)
    except OSError as exc:
        raise RescueSourceError(f"cannot open events stream {path}: {exc}") from exc
    try:
        opened = os.fstat(raw.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise RescueSourceError(f"events stream is not regular: {path}")
        with raw:
            with io.TextIOWrapper(raw, encoding="utf-8", errors="strict") as text:
                for line_number, line in enumerate(text, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RescueSourceError(
                            f"events.jsonl line {line_number} is malformed JSON"
                        ) from exc
                    if not isinstance(record, dict):
                        raise RescueSourceError(
                            f"events.jsonl line {line_number} is not a JSON object"
                        )
    except UnicodeDecodeError as exc:
        raise RescueSourceError("events.jsonl is not valid UTF-8") from exc


def _validate_agent_worktrees_projection(path: Path, session_id: str) -> None:
    try:
        payload = json.loads(
            read_regular(path, max_bytes=MAX_AGENT_WORKTREES_BYTES).decode("utf-8")
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RescueSourceError("agent-worktrees.json is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RescueSourceError("agent-worktrees.json must be a JSON object")
    version = payload.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
    ):
        raise RescueSourceError(
            "agent-worktrees.json has an unsupported schema version"
        )
    if payload.get("session_id") != session_id:
        raise RescueSourceError(
            "agent-worktrees.json session_id does not match its directory"
        )
    if _json_depth_exceeds(payload, MAX_AGENT_WORKTREES_JSON_DEPTH):
        raise RescueSourceError("agent-worktrees.json exceeds the nesting limit")
    if version > AGENT_WORKTREES_SCHEMA_VERSION:
        return
    relations = payload.get("relations")
    tombstones = payload.get("relation_tombstones", [])
    if (
        not isinstance(relations, list)
        or any(not isinstance(item, dict) for item in relations)
        or not isinstance(tombstones, list)
        or any(not isinstance(item, dict) for item in tombstones)
    ):
        raise RescueSourceError("agent-worktrees.json has an invalid schema")


def _json_depth_exceeds(value: object, maximum: int) -> bool:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > maximum:
            return True
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return False


def safe_component(value: str) -> str:
    """Match the provider's filesystem-safe container component."""
    if _SAFE_COMPONENT_RE.fullmatch(value):
        return value
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def venue_id(prefix: str, container: str) -> str:
    """Return a stable, flat filesystem-safe venue namespace."""
    if not _SAFE_COMPONENT_RE.fullmatch(prefix):
        raise RescueSourceError(f"unsafe target prefix: {prefix!r}")
    value = f"{prefix.lower()}-{safe_component(container).lower()}"
    if "/" in value or "\\" in value or not _SAFE_COMPONENT_RE.fullmatch(value):
        raise RescueSourceError(f"unsafe venue identity: {value!r}")
    return value


def _canonical_session_id(value: object) -> str:
    if not isinstance(value, str):
        raise RescueSourceError("session id must be a string")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise RescueSourceError(f"invalid session id: {value!r}") from exc
    if value != canonical:
        raise RescueSourceError(f"session id is not canonical: {value!r}")
    return canonical


def _capture_order(capture_id: str, captured_at: str) -> tuple[float, str]:
    if not _SAFE_COMPONENT_RE.fullmatch(capture_id):
        raise RescueSourceError(f"unsafe capture id: {capture_id!r}")
    try:
        parsed_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RescueSourceError("metadata captured_at is invalid") from exc
    if parsed_at.tzinfo is None:
        raise RescueSourceError("metadata captured_at must include a timezone")
    return parsed_at.timestamp(), capture_id


def _required_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RescueSourceError(f"metadata {key} must be a non-empty string")
    return value.strip()


def _optional_text(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RescueSourceError(f"metadata {key} must be null or a non-empty string")
    return value.strip()


def _validate_member_parents(path: Path, session_dir: Path, label: str) -> None:
    parent = path.parent
    while True:
        require_directory(parent, f"{label} parent")
        if parent == session_dir:
            return
        if session_dir not in parent.parents:
            raise RescueSourceError(f"member escapes session directory: {label}")
        parent = parent.parent


def parse_capture(
    container_dir: Path,
    capture_dir: Path,
    *,
    target_prefix: str,
) -> CaptureResult:
    """Validate one v1 capture and return independently complete sessions."""
    require_directory(capture_dir, "capture")
    metadata_path = capture_dir / "metadata.json"
    try:
        metadata = json.loads(
            read_regular(metadata_path, max_bytes=_MAX_METADATA_BYTES).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RescueSourceError(f"invalid metadata JSON: {metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise RescueSourceError("metadata must be an object")
    if metadata.get("schema_version") != 1:
        raise RescueSourceError("metadata schema_version must be 1")
    if metadata.get("status") != "verified":
        raise RescueSourceError("metadata status must be verified")
    completeness = metadata.get("completeness")
    if completeness not in {"complete", "partial"}:
        raise RescueSourceError(
            f"metadata completeness must be complete or partial, got {completeness!r}"
        )
    capture_id = _required_text(metadata, "capture_id")
    if capture_id != capture_dir.name:
        raise RescueSourceError("metadata capture_id does not match its directory")
    captured_at = _required_text(metadata, "captured_at")
    order = _capture_order(capture_id, captured_at)
    container = _required_text(metadata, "container")
    if safe_component(container) != container_dir.name:
        raise RescueSourceError("metadata container does not match its directory")
    container_instance = _required_text(metadata, "container_instance")
    container_generation = _required_text(metadata, "container_generation")
    fleet = _optional_text(metadata, "fleet")
    source_repo = _optional_text(metadata, "source_repo")
    if metadata.get("restorable") is not False:
        raise RescueSourceError("metadata restorable must be false")
    if metadata.get("session_state") != "present":
        raise RescueSourceError(
            f"capture session_state is {metadata.get('session_state')!r}, not present"
        )
    excluded = metadata.get("excluded")
    if not isinstance(excluded, dict):
        raise RescueSourceError("metadata excluded must be an object")
    capture_fingerprint = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    capture_key = (
        f"{SUPPORTED_PROVIDER}|{venue_id(target_prefix, container)}|{capture_id}"
    )
    incomplete: dict[str, list[str]] = {}
    warnings: list[str] = []
    excluded_allowlisted = excluded.get("allowlisted", [])
    missing_events = excluded.get("missing_events", [])
    if not isinstance(excluded_allowlisted, list):
        raise RescueSourceError("metadata excluded.allowlisted must be a list")
    if not isinstance(missing_events, list):
        raise RescueSourceError("metadata excluded.missing_events must be a list")
    for item in excluded_allowlisted:
        if not isinstance(item, dict):
            raise RescueSourceError("metadata excluded.allowlisted is invalid")
        session = item.get("session_id")
        member = item.get("member")
        reason = item.get("reason")
        session_id = _canonical_session_id(session)
        if not isinstance(member, str) or member not in ALLOWED_MEMBERS:
            raise RescueSourceError("metadata excluded.allowlisted member is invalid")
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise RescueSourceError("metadata excluded.allowlisted reason is invalid")
        detail = f"{member}: {reason or 'excluded'}"
        if member in SOFT_OPTIONAL_MEMBERS:
            warnings.append(f"{session_id}: ignored optional {detail}")
        else:
            incomplete.setdefault(session_id, []).append(detail)
    for session in missing_events:
        session_id = _canonical_session_id(session)
        incomplete.setdefault(session_id, []).append("events.jsonl: missing")

    raw_sessions = metadata.get("sessions")
    if not isinstance(raw_sessions, dict):
        raise RescueSourceError("metadata sessions must be an object")
    session_count = metadata.get("session_count")
    if isinstance(session_count, bool) or not isinstance(session_count, int):
        raise RescueSourceError("metadata session_count must be an integer")
    if session_count != len(raw_sessions):
        raise RescueSourceError("metadata session_count does not match sessions")
    total_bytes = metadata.get("total_bytes")
    if isinstance(total_bytes, bool) or not isinstance(total_bytes, int) or total_bytes < 0:
        raise RescueSourceError("metadata total_bytes must be a non-negative integer")

    parsed: list[RescuedSession] = []
    rejected_sessions: list[str] = []
    declared_total = 0
    sessions_root = capture_dir / "sessions"
    require_directory(sessions_root, "sessions root")
    for raw_session_id, raw_session in raw_sessions.items():
        session_id = _canonical_session_id(raw_session_id)
        if not isinstance(raw_session, dict):
            raise RescueSourceError(f"session {session_id} metadata must be an object")
        raw_members = raw_session.get("members")
        if not isinstance(raw_members, dict):
            raise RescueSourceError(f"session {session_id} members must be an object")
        unknown_members = sorted(set(raw_members) - set(ALLOWED_MEMBERS))
        if unknown_members:
            warnings.append(
                f"{session_id}: ignored unknown declared member(s): "
                + ", ".join(unknown_members)
            )
        if "events.jsonl" not in raw_members:
            incomplete.setdefault(session_id, []).append("events.jsonl: undeclared")
        validated_members: dict[str, tuple[int, str]] = {}
        for relative, raw_member in raw_members.items():
            if not isinstance(raw_member, dict):
                raise RescueSourceError(f"{session_id}/{relative} metadata is invalid")
            size = raw_member.get("bytes")
            digest = raw_member.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise RescueSourceError(f"{session_id}/{relative} bytes is invalid")
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise RescueSourceError(f"{session_id}/{relative} sha256 is invalid")
            declared_total += size
            if relative not in ALLOWED_MEMBERS:
                continue
            validated_members[relative] = (size, digest)
        reasons = incomplete.get(session_id, [])
        if reasons:
            rejected_sessions.append(f"{session_id}: " + "; ".join(reasons))
            continue
        session_dir = sessions_root / session_id
        members: list[Member] = []
        try:
            require_directory(session_dir, f"session {session_id}")
            for relative in ALLOWED_MEMBERS:
                if relative not in validated_members:
                    continue
                size, digest = validated_members[relative]
                path = session_dir / Path(relative)
                _validate_member_parents(path, session_dir, f"{session_id}/{relative}")
                if (
                    relative == AGENT_WORKTREES_PROJECTION
                    and size > MAX_AGENT_WORKTREES_BYTES
                ):
                    try:
                        _require_regular_size(path, size)
                    except RescueSourceError as exc:
                        if "not a regular file" not in str(exc):
                            raise
                        warnings.append(
                            f"{session_id}: ignored optional {relative}: {exc}"
                        )
                        continue
                    warnings.append(
                        f"{session_id}: ignored optional {relative}: "
                        f"exceeds {MAX_AGENT_WORKTREES_BYTES} bytes"
                    )
                    continue
                try:
                    actual_digest = _hash_regular(path, size)
                except RescueSourceError as exc:
                    if (
                        relative not in SOFT_OPTIONAL_MEMBERS
                        or "not a regular file" not in str(exc)
                    ):
                        raise
                    warnings.append(
                        f"{session_id}: ignored optional {relative}: {exc}"
                    )
                    continue
                if actual_digest != digest:
                    raise RescueSourceError(
                        f"hash mismatch for {session_id}/{relative}"
                    )
                try:
                    if relative == "events.jsonl":
                        _validate_events_jsonl(path)
                    elif relative == AGENT_WORKTREES_PROJECTION:
                        _validate_agent_worktrees_projection(path, session_id)
                except RescueSourceError as exc:
                    if relative not in SOFT_OPTIONAL_MEMBERS:
                        raise
                    warnings.append(
                        f"{session_id}: ignored optional {relative}: {exc}"
                    )
                    continue
                members.append(Member(relative, path, size, digest))
        except RescueSourceError as exc:
            rejected_sessions.append(f"{session_id}: {exc}")
            continue
        parsed.append(
            RescuedSession(
                session_id=session_id,
                venue_id=venue_id(target_prefix, container),
                target_id=f"container:{container}",
                capture_id=capture_id,
                capture_path=str(capture_dir),
                capture_order=order,
                captured_at=captured_at,
                container=container,
                container_instance=container_instance,
                container_generation=container_generation,
                fleet=fleet,
                source_repo=source_repo,
                members=tuple(members),
                capture_fingerprint=capture_fingerprint,
            )
        )
    if declared_total != total_bytes:
        raise RescueSourceError(
            f"metadata total_bytes mismatch: expected {total_bytes}, got {declared_total}"
        )
    return CaptureResult(
        sessions=tuple(parsed),
        rejected_sessions=tuple(rejected_sessions),
        warnings=tuple(warnings),
        capture_key=capture_key,
        capture_fingerprint=capture_fingerprint,
    )
