"""Bounded, stable, archive-aware transcript evidence reads."""

from __future__ import annotations

import io
import json
import os
import tarfile
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO, NoReturn

from agent_logger.session_codecs import (
    _file_identity, _file_revision, _validate_read_limit, _validate_source_member_name,
)
from agent_logger.sessions import EVENTS_MEMBER, SessionRef, codec_for_archive
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.usage_ledger import UsageEvidenceError
from agent_logger.usage_transcript import MAX_EVENTS, MAX_LINE_BYTES, MAX_TRANSCRIPT_BYTES


@contextmanager
def _live_member(
    ref: SessionRef, member: str, max_bytes: int,
) -> Iterator[BinaryIO | None]:
    with publication_directory(ref.path, create=False) as directory:
        try:
            stream = directory.open(member)
        except FileNotFoundError:
            yield None
            return
        with stream:
            before = os.fstat(stream.fileno())
            named_before = directory.stat(member)
            revision = _file_revision(before)
            if _file_identity(before) != _file_identity(named_before):
                raise UsageEvidenceError("session member changed while opening")
            if before.st_size > max_bytes:
                raise UsageEvidenceError("session member byte limit exceeded")
            yield stream
            if stream.tell() != before.st_size or (
                revision != _file_revision(os.fstat(stream.fileno()))
                or _file_revision(named_before) != _file_revision(directory.stat(member))
            ):
                raise UsageEvidenceError("session member changed during reading")


def read_session_member(
    ref: SessionRef, member: str, *, max_bytes: int,
) -> bytes | None:
    """Read a canonical bare member within a preallocation bound, without extraction.

    Missing members return ``None``. Unsafe paths, changed evidence, unsupported
    codecs, and exhausted limits raise :class:`UsageEvidenceError`.
    Archive reads use the container itself, not an unverified metadata sidecar.
    """
    try:
        _validate_read_limit(max_bytes)
        _validate_source_member_name(member)
        if not member or "/" in member or "\\" in member:
            raise UsageEvidenceError("session member must be a canonical bare filename")
        if ref.kind == "live":
            with _live_member(ref, member, max_bytes) as stream:
                if stream is None:
                    return None
                return stream.read(min(max_bytes, os.fstat(stream.fileno()).st_size) + 1)
        if ref.kind != "archive":
            raise UsageEvidenceError(f"unsupported session reference kind: {ref.kind!r}")
        data = codec_for_archive(ref.path).read_member_bounded(
            ref.path, member, max_bytes=max_bytes,
        )
        if data is not None and (not isinstance(data, bytes) or len(data) > max_bytes):
            raise UsageEvidenceError("codec violated its bounded member read contract")
        return data
    except UsageEvidenceError:
        raise
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, zlib.error) as exc:
        raise UsageEvidenceError(f"cannot read bounded session member: {exc}") from exc


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError("nonfinite JSON constant")


def _read_events(stream: BinaryIO) -> list[dict]:
    events: list[dict] = []
    consumed = 0
    while True:
        # One sentinel byte detects overflow without allocating the next event.
        remaining = min(MAX_LINE_BYTES, MAX_TRANSCRIPT_BYTES - consumed)
        line = stream.readline(1 if len(events) >= MAX_EVENTS else remaining + 1)
        if not line:
            return events
        consumed += len(line)
        if (
            len(line) > MAX_LINE_BYTES
            or consumed > MAX_TRANSCRIPT_BYTES
            or len(events) >= MAX_EVENTS
        ):
            raise UsageEvidenceError("transcript read limit exceeded")
        if not line.endswith(b"\n"):
            raise UsageEvidenceError("transcript has an unstable partial tail")
        try:
            event = json.loads(line.decode("utf-8"), parse_constant=_invalid_constant)
        except UnicodeError as exc:
            raise UsageEvidenceError("transcript contains invalid UTF-8") from exc
        except (ValueError, RecursionError) as exc:
            raise UsageEvidenceError("transcript contains invalid JSON") from exc
        if not isinstance(event, dict):
            raise UsageEvidenceError("transcript event is not an object")
        events.append(event)


def read_session_events(ref: SessionRef) -> list[dict]:
    """Return complete JSONL objects transiently; reject partial or changed evidence."""
    if ref.kind == "live":
        try:
            with _live_member(ref, EVENTS_MEMBER, MAX_TRANSCRIPT_BYTES) as stream:
                if stream is None:
                    raise UsageEvidenceError("session transcript is missing")
                return _read_events(stream)
        except UsageEvidenceError:
            raise
        except (OSError, ValueError) as exc:
            raise UsageEvidenceError(f"cannot read bounded session transcript: {exc}") from exc
    data = read_session_member(ref, EVENTS_MEMBER, max_bytes=MAX_TRANSCRIPT_BYTES)
    if data is None:
        raise UsageEvidenceError("session transcript is missing")
    with io.BytesIO(data) as stream:
        return _read_events(stream)
