"""Internal post-publication retirement sequence; no scheduled caller yet."""

from __future__ import annotations

import errno
import os
import stat
import time
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from agent_logger.sync.archive_directory import ArchiveDirectory, archive_directory
from agent_logger.sync.log_archive_publication import publish_required_log_archives
from agent_logger.sync.log_archive_readback import (
    LogArchiveRevision, ProcessLogReadback, verify_required_log_archives,
)
from agent_logger.sync.log_archives import (
    _LOG_NAME, _fingerprint, _hash_stream, _verify_zip_stream,
    LogArchiveSettings, producer_exited, resolve_log_archive_settings,
)
from agent_logger.sync.log_reclamation_policy import (
    LogReclamationSettings, resolve_log_reclamation_settings,
)
from agent_logger.sync.log_reclamation_targets import validate_required_log_targets
from agent_logger.sync.log_retirement_file import (
    LogRetirementRevision, RetirementFile, retirement_file,
)
from agent_logger.sync.publication_directory import publication_directory

PolicyGuard = Callable[[], AbstractContextManager[None]]


@dataclass
class LogReclamationResult:
    inspected: int = 0
    eligible: int = 0
    retained: int = 0
    retired: int = 0
    reclaimed_bytes: int = 0
    committed_unconfirmed: int = 0
    failed: list[str] = field(default_factory=list)


def _eligible(info: os.stat_result, pid: int, age: int, quiet: int) -> bool:
    elapsed = time.time() - info.st_mtime
    return elapsed >= age and elapsed >= quiet and producer_exited(pid)


def _validate_root(requested: Path, root: ArchiveDirectory) -> None:
    current = requested.resolve(strict=True)
    named = current.stat()
    held = os.fstat(root.fd) if root.fd is not None else root.path.stat()
    if current != root.path or (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
        raise ValueError("process-log reclamation source root changed")


def _pin_verified_zip(
    stack: ExitStack, root: ArchiveDirectory, revision: LogArchiveRevision,
) -> None:
    selected = root.stat(revision.archive_name)
    if not stat.S_ISREG(selected.st_mode):
        raise ValueError("process-log replacement ZIP is not a regular file")
    raw = stack.enter_context(root.open(revision.archive_name, protected=True))
    if _fingerprint(os.fstat(raw.fileno())) != _fingerprint(selected):
        raise ValueError("process-log replacement ZIP changed while opening")
    _verify_zip_stream(raw, revision.logical_name, revision.sha256, revision.size)
    if _fingerprint(root.stat(revision.archive_name)) != _fingerprint(selected):
        raise ValueError("process-log replacement ZIP changed during verification")


def _revision(
    root: ArchiveDirectory, name: str, raw: BinaryIO, selected: os.stat_result,
) -> LogRetirementRevision:
    before = os.fstat(raw.fileno())
    if _fingerprint(before) != _fingerprint(selected):
        raise ValueError("process-log source changed while acquiring inspection boundary")
    raw.seek(0)
    digest = _hash_stream(raw, before.st_size)
    if (
        _fingerprint(os.fstat(raw.fileno())) != _fingerprint(before)
        or _fingerprint(root.stat(name)) != _fingerprint(before)
    ):
        raise ValueError("process-log source changed during reclamation inspection")
    return LogRetirementRevision(_fingerprint(before), digest)


def _retire_verified(
    requested: Path, root: ArchiveDirectory, name: str, held: RetirementFile,
    selected: os.stat_result, pid: int, age: int, quiet: int,
    targets: tuple[ProcessLogReadback, ...], policy_guard: PolicyGuard,
) -> None:
    observed = _revision(root, name, held.stream, selected)
    revision = LogArchiveRevision(name, observed.sha256, selected.st_size)
    with ExitStack() as evidence:
        # Snapshotting already ran before ordinary publication. A missing current
        # local revision retains the raw file for a later pass, never substitutes
        # an older ZIP or silently creates evidence during retirement.
        _pin_verified_zip(evidence, root, revision)
        destinations: list[ArchiveDirectory] = []
        for target in targets:
            if target.directory is None:
                raise ValueError("process-log required destination is unsupported")
            # Native directory handles must span publication, not just its later
            # verification: a valid replacement root is still a changed target.
            destinations.append(evidence.enter_context(publication_directory(target.directory)))
        published = publish_required_log_archives(root.path, targets, revision)
        if not published or not all(item.supported and item.verified for item in published):
            raise ValueError("process-log ZIP publication/readback incomplete")
        for destination in destinations:
            _pin_verified_zip(evidence, destination, revision)

        @contextmanager
        def final_guard() -> Iterator[None]:
            with policy_guard():
                _validate_root(requested, root)
                if not _eligible(os.fstat(held.stream.fileno()), pid, age, quiet):
                    raise ValueError("process-log producer or retention eligibility changed")
                yield

        held.retire(observed, commit_guard=final_guard())


def reclaim_process_logs(
    log_root: Path, *, settings: LogReclamationSettings, archives: LogArchiveSettings,
    process_logs_enabled: bool, active_target: str,
    targets: tuple[ProcessLogReadback, ...], policy_guard: PolicyGuard,
    dry_run: bool = False,
) -> LogReclamationResult:
    """Compose all-target evidence with exact-entry retirement under the sync lock.

    The caller must own that lock, supply a local-unfiltered pass and a policy
    guard that revalidates/pins the current machine-local configuration at commit.
    No runtime caller supplies that provenance yet, so configuration still refuses
    enabling reclamation. This function is exercised only by synthetic fixtures.
    """
    archives = resolve_log_archive_settings({
        "enabled": archives.enabled, "local_source": archives.local_source,
        "min_quiet_seconds": archives.min_quiet_seconds,
    })
    settings = resolve_log_reclamation_settings(
        {
            "enabled": settings.enabled, "min_age_seconds": settings.min_age_seconds,
            "required_targets": list(settings.required_targets),
        },
        process_logs_enabled=process_logs_enabled, archives=archives,
        active_target=active_target,
    )
    result = LogReclamationResult()
    if not settings.enabled:
        return result
    age = settings.min_age_seconds
    if age is None:
        raise ValueError("process-log reclamation requires explicit retention age")
    requested = log_root.expanduser().absolute()
    canonical = requested.resolve(strict=True)
    selected_root = canonical.stat()
    validate_required_log_targets(canonical, settings, targets)
    if not dry_run and os.name != "nt":
        raise OSError(errno.ENOTSUP, "exact-file process-log retirement requires native Windows")
    with archive_directory(canonical) as root:
        opened_root = os.fstat(root.fd) if root.fd is not None else root.path.stat()
        if (opened_root.st_dev, opened_root.st_ino) != (
            selected_root.st_dev, selected_root.st_ino,
        ):
            raise ValueError("process-log source root changed after target resolution")
        _validate_root(requested, root)
        for name in root.names():
            if not name.startswith("process-") or not name.endswith(".log"):
                continue
            result.inspected += 1
            match = _LOG_NAME.fullmatch(name)
            if match is None or not 0 < int(match[1]) <= 2**31 - 1:
                result.retained += 1
                continue
            pid = int(match[1])
            held: RetirementFile | None = None
            try:
                selected = root.stat(name)
                if not stat.S_ISREG(selected.st_mode):
                    raise ValueError("process-log reclamation source is not a regular file")
                if not _eligible(selected, pid, age, archives.min_quiet_seconds):
                    result.retained += 1
                    continue
                result.eligible += 1
                if dry_run:
                    with root.open(name, protected=True) as raw:
                        observed = _revision(root, name, raw, selected)
                        revision = LogArchiveRevision(name, observed.sha256, selected.st_size)
                        with ExitStack() as evidence:
                            _pin_verified_zip(evidence, root, revision)
                            readback = verify_required_log_archives(targets, revision)
                            if not readback.verified:
                                raise ValueError("dry-run all-target ZIP evidence unverified")
                        _validate_root(requested, root)
                    if os.name != "nt":
                        raise OSError(errno.ENOTSUP, "retirement unsupported on this platform")
                    result.retained += 1
                    continue
                with retirement_file(root, name) as held:
                    _retire_verified(
                        requested, root, name, held, selected, pid, age,
                        archives.min_quiet_seconds, targets, policy_guard,
                    )
                result.retired += 1
                result.reclaimed_bytes += selected.st_size
            except (
                OSError, ValueError, EOFError, zipfile.BadZipFile, zlib.error, RuntimeError,
            ) as exc:
                committed = held is not None and held.committed
                if committed:
                    result.committed_unconfirmed += 1
                else:
                    result.retained += 1
                suffix = "; retirement committed, acknowledgement failed" if committed else ""
                result.failed.append(f"{name}: {type(exc).__name__}{suffix}")
    return result
