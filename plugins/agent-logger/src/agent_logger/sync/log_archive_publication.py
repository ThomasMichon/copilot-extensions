"""Bounded exact-ZIP publication, without raw-log or session-state fanout."""

from __future__ import annotations

import os
import stat
import zipfile
import zlib
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from agent_logger.sync.log_archive_readback import LogArchiveRevision, ProcessLogReadback
from agent_logger.sync.log_archives import (
    _CHUNK_BYTES, _fingerprint, _hash_stream, _verify_archive, _verify_zip_stream,
)
from agent_logger.sync.publication_directory import (
    PublicationDirectory, publication_directory,
)
from agent_logger.sync.targets.claim_file import create_claim_file


@dataclass(frozen=True)
class LogArchivePublicationResult:
    target_name: str
    revision: LogArchiveRevision
    supported: bool
    published: bool
    verified: bool
    detail: str


def _validate_source(
    root: PublicationDirectory, raw: BinaryIO, name: str, selected: os.stat_result,
) -> None:
    if (
        _fingerprint(os.fstat(raw.fileno())) != _fingerprint(selected)
        or _fingerprint(root.stat(name)) != _fingerprint(selected)
    ):
        raise ValueError("process-log source ZIP changed during publication")


def _publish_opened(
    root: PublicationDirectory, raw: BinaryIO, selected: os.stat_result,
    destination: PublicationDirectory, revision: LogArchiveRevision,
) -> bool:
    name = revision.archive_name
    _verify_zip_stream(raw, revision.logical_name, revision.sha256, revision.size)
    raw.seek(0)
    archive_hash = _hash_stream(raw, selected.st_size)
    _validate_source(root, raw, name, selected)
    try:
        destination.stat(name)
    except FileNotFoundError:
        pass
    else:
        _verify_archive(
            destination, name, revision.logical_name, revision.sha256, revision.size,
        )
        _validate_source(root, raw, name, selected)
        return False

    with create_claim_file(
        destination.path, directory_fd=destination.fd, mode=0o600,
    ) as claim:
        output = claim.stream
        raw.seek(0)
        remaining = selected.st_size
        while remaining:
            chunk = raw.read(min(_CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError("process-log source ZIP truncated during publication")
            output.write(chunk)
            remaining -= len(chunk)
        if raw.read(1):
            raise ValueError("process-log source ZIP grew during publication")
        output.flush()
        os.fsync(output.fileno())
        _verify_zip_stream(output, revision.logical_name, revision.sha256, revision.size)
        output.seek(0)
        if _hash_stream(output, selected.st_size) != archive_hash:
            raise ValueError("process-log ZIP copy differs from the source archive")
        _validate_source(root, raw, name, selected)
        claim.publish(destination.path / name)
        named = destination.stat(name)
        if (named.st_dev, named.st_ino) != claim.file_id:
            raise ValueError("process-log ZIP identity changed during publication")
        _validate_source(root, raw, name, selected)
    return True


def publish_log_archive(
    source_directory: Path, target: ProcessLogReadback,
    revision: LogArchiveRevision, *, dry_run: bool = False,
) -> LogArchivePublicationResult:
    """Publish one archive, then independently verify its configured destination.

    The caller owns the sync lock and resolves the complete required-target set.
    Results are pass-local observations, never source-retirement authority.
    Failures preserve any already-published ZIP; no rollback unlinks a pathname.
    """
    if target.directory is None:
        return LogArchivePublicationResult(
            target.target_name, revision, False, False, False,
            "target does not support exact process-log ZIP publication",
        )
    published = False
    try:
        source = source_directory.expanduser().absolute()
        destination = target.directory.expanduser().absolute()
        if source.is_relative_to(destination) or destination.is_relative_to(source):
            raise ValueError("process-log ZIP source and destination overlap")
        with publication_directory(source, create=False) as root:
            selected = root.stat(revision.archive_name)
            if not stat.S_ISREG(selected.st_mode):
                raise ValueError("process-log source ZIP is not a regular file")
            with root.open(revision.archive_name, protected=True) as raw:
                with ExitStack() as stack:
                    _validate_source(root, raw, revision.archive_name, selected)
                    if dry_run:
                        _verify_zip_stream(
                            raw, revision.logical_name, revision.sha256, revision.size,
                        )
                    else:
                        output = stack.enter_context(publication_directory(destination))
                        guard = stack.enter_context(
                            publication_directory(destination, create=False),
                        )
                        if output.fd is not None and guard.fd is not None:
                            created, guarded = os.fstat(output.fd), os.fstat(guard.fd)
                            if (created.st_dev, created.st_ino) != (
                                guarded.st_dev, guarded.st_ino,
                            ):
                                raise ValueError("process-log destination changed while pinning")
                        published = _publish_opened(root, raw, selected, output, revision)
                    # Close the claim before protected Windows readback, but hold
                    # the original destination ancestry through fresh verification.
                    readback = target.verify(revision)
                    _validate_source(root, raw, revision.archive_name, selected)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, zlib.error, RuntimeError) as exc:
        operation = "dry-run verification" if dry_run else "publication/readback"
        return LogArchivePublicationResult(
            target.target_name, revision, True, published, False,
            f"{revision.archive_name}: {operation} failed ({type(exc).__name__}); "
            "no ZIP rollback attempted",
        )
    if not readback.verified:
        return LogArchivePublicationResult(
            target.target_name, revision, True, published, False, readback.detail,
        )
    outcome = "dry-run readback" if dry_run else "published" if published else "existing ZIP"
    return LogArchivePublicationResult(
        target.target_name, revision, True, published, True,
        f"{revision.archive_name}: {outcome} verified",
    )


def publish_required_log_archives(
    source_directory: Path, targets: tuple[ProcessLogReadback, ...],
    revision: LogArchiveRevision, *, dry_run: bool = False,
) -> tuple[LogArchivePublicationResult, ...]:
    """Attempt every required destination; one failure must not hide later results."""
    return tuple(
        publish_log_archive(source_directory, target, revision, dry_run=dry_run)
        for target in targets
    )
