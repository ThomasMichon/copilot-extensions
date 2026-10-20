"""Protected publication for the ``local`` and ``onedrive`` sync targets.

Implements ``sync.redact_secrets`` and ``sync.session_files`` for
:class:`~agent_logger.sync.targets.filesystem.FilesystemTarget`: the
file-by-file publication loop that copies only the selected session files
and, when redacting, a derived copy of each one, plus the projection receipts
and the doctor check that go with it. The target dispatches here only when
protection is enabled, so its default publication path is unchanged.

Helpers shared with the default path are looked up on the ``filesystem``
module at call time, so they stay single-sourced (and patchable) there.
"""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from agent_logger.sync import protection as _protection

# ``filesystem`` imports this module while it is still loading; binding the
# partially initialised module is safe because it is only read at call time.
from agent_logger.sync.targets import filesystem as _fs
from agent_logger.sync.targets import publication_admission
from agent_logger.sync.targets.base import (
    SESSION_INDEX_NAMES,
    DoctorResult,
    PushResult,
    SourceIdentityLike,
    is_session_path_included,
)

if TYPE_CHECKING:
    from agent_logger.sync.targets.filesystem import FilesystemTarget


_COMPARE_CHUNK = 1024 * 1024


def _regular_file_tuple(path: Path) -> tuple[int, int] | None:
    """``(size, mtime_ns)`` of a regular, non-link file; else ``None``."""
    try:
        info = _fs._lstat(path)
    except OSError:
        return None
    if _fs.is_link_or_reparse(path, info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    return info.st_size, info.st_mtime_ns


def _same_content(stream: BinaryIO, dst: Path, size: int) -> bool:
    """Whether *dst* already holds exactly the first *size* bytes of *stream*."""
    try:
        existing = _fs.open_regular_no_follow(dst)
    except OSError:
        return False
    try:
        with existing:
            if os.fstat(existing.fileno()).st_size != size:
                return False
            stream.seek(0)
            remaining = size
            while remaining > 0:
                expected = stream.read(min(_COMPARE_CHUNK, remaining))
                if not expected or existing.read(len(expected)) != expected:
                    return False
                remaining -= len(expected)
            return not existing.read(1)
    except OSError:
        return False


def _protected_copy(
    src: Path, dst: Path, member: str, *, compare_existing: bool = False,
) -> tuple[_protection.Outcome, tuple[int, int], tuple[int, int] | None, bool]:
    """Publish the protected derivative of *src* at *dst*, streaming.

    Returns ``(outcome, source (size, mtime_ns), destination (size,
    mtime_ns) or None, replaced)``. Unless the outcome is published nothing
    is written, so *dst* keeps whatever version it already had. The source
    is only read. A locked or changing source raises exactly as in
    :func:`_copy_replace`. With *compare_existing*, an identical destination
    is left in place.
    """
    try:
        source = _fs.open_regular_no_follow(src)
    except OSError as exc:
        if _fs._is_windows_sharing_violation(exc):
            raise _fs._LockedSourceFile(f"source file is locked: {src}") from exc
        raise
    temporary = dst.with_name(f".{dst.name}.{_fs.short_unique_id()}.tmp")
    temporary_io = _fs._windows_extended_path(temporary)
    with source:
        before = os.fstat(source.fileno())
        source_tuple = (before.st_size, before.st_mtime_ns)
        try:
            with open(temporary_io, "xb+") as target:
                try:
                    outcome = _protection.transform_file(
                        member,
                        _protection.BoundedReader(source, before.st_size),
                        before.st_size,
                        target.write,
                    )
                except _protection.SourceChangedError as exc:
                    raise _fs._SourceChangedDuringCopy(f"{exc}: {src}") from exc
                after = os.fstat(source.fileno())
                if (before.st_dev, before.st_ino, before.st_size) != (
                    after.st_dev, after.st_ino, after.st_size,
                ):
                    raise _fs._SourceChangedDuringCopy(
                        "source changed identity or size during copy"
                    )
                if not outcome.published:
                    return outcome, source_tuple, None, False
                target.flush()
                os.fsync(target.fileno())
                if compare_existing and _same_content(target, dst, target.tell()):
                    return outcome, source_tuple, _regular_file_tuple(dst), False
            # A rewritten copy is stamped one second older than its source, so
            # a later unprotected reconcile (size + mtime) restores raw bytes.
            stamp = before.st_mtime_ns - (1_000_000_000 if outcome.changed else 0)
            try:
                os.utime(temporary_io, ns=(before.st_atime_ns, stamp))
                os.chmod(temporary_io, stat.S_IMODE(before.st_mode))
            except OSError:
                pass
            _fs._unlink_replace_target(dst)
            _fs._durable_replace(temporary, dst)
        finally:
            _fs._unlink_replace_target(temporary)
    return outcome, source_tuple, _regular_file_tuple(dst), True


def _read_receipt(dest: Path, session_id: str) -> _protection.Receipt | None:
    """The projection receipt last written for *session_id*, if usable."""
    try:
        directory = _fs._existing_relative_directory(dest, Path(_protection.RECEIPT_DIR))
        if directory is None:
            return None
        with _fs.open_regular_no_follow(directory / f"{session_id}.json") as stream:
            raw = stream.read(_protection.MAX_RECEIPT_BYTES + 1)
    except (OSError, ValueError):
        return None
    return _protection.decode_receipt(raw, session_id)


def _remove_receipt(dest: Path, session_id: str) -> None:
    directory = _fs._existing_relative_directory(dest, Path(_protection.RECEIPT_DIR))
    if directory is not None:
        _fs._unlink_replace_target(directory / f"{session_id}.json")


def _write_receipt(dest: Path, session_id: str, payload: bytes | None) -> None:
    """Durably replace one projection receipt (remove it for ``None``)."""
    if payload is None:
        _remove_receipt(dest, session_id)
        return
    directory = _fs._ensure_relative_directory(dest, Path(_protection.RECEIPT_DIR))
    final = directory / f"{session_id}.json"
    temporary = final.with_name(f".{final.name}.{_fs.short_unique_id()}.tmp")
    try:
        _fs._write_bytes_fsync(temporary, payload)
        _fs._unlink_replace_target(final)
        _fs._durable_replace(temporary, final)
    finally:
        _fs._unlink_replace_target(temporary)


def _protection_entry(rel: Path, reason: str, prior_copy: str) -> dict[str, str]:
    return {"path": rel.as_posix(), "reason": reason, "prior_copy": prior_copy}


_DOCTOR_SCAN_LIMIT = 20_000


def _unprotected_destination_files(
    settings: _protection.ProtectionSettings, dest: Path,
) -> tuple[int, bool]:
    """Count destination files the current protection settings did not produce.

    Returns ``(count, truncated)``. The walk is bounded so doctor stays quick
    on a large destination; it never reads file content beyond receipts.
    """
    stale = sum(1 for name in SESSION_INDEX_NAMES if _regular_file_tuple(dest / name))
    budget = _DOCTOR_SCAN_LIMIT
    if settings.essential_only:
        provenance = _fs._existing_relative_directory(dest, Path("provenance"))
        if provenance is not None:
            for _path in _fs._iter_regular_source_files(provenance):
                budget -= 1
                if budget < 0:
                    return stale, True
                stale += 1
    state = _fs._existing_relative_directory(dest, Path("session-state"))
    if state is None:
        return stale, False
    with os.scandir(_fs._windows_extended_path(state)) as listing:
        session_ids = sorted(
            entry.name for entry in listing if entry.is_dir(follow_symlinks=False)
        )
    for session_id in session_ids:
        session_dir = state / session_id
        receipt = (
            _read_receipt(dest, session_id)
            if settings.redact_secrets and _protection.safe_member(session_id)
            else None
        )
        for path in _fs._iter_regular_source_files(session_dir):
            if _fs._is_excluded_name(path.name):
                continue
            budget -= 1
            if budget < 0:
                return stale, True
            member = path.relative_to(session_dir).as_posix()
            if not settings.member_included(member):
                stale += 1
            elif settings.redact_secrets:
                entry = receipt.files.get(member) if receipt is not None else None
                if (
                    _protection.classify_prior_copy(entry, _regular_file_tuple(path))
                    != _protection.PRIOR_COPY_PROTECTED
                ):
                    stale += 1
    return stale, False


def _protection_doctor_detail(
    settings: _protection.ProtectionSettings, root: Path, machine: str | None = None,
) -> str:
    """Doctor detail for session protection, warning about earlier copies.

    A settings change replaces only differing selected files of sessions that
    are still synced and removes no copy, so a destination can still hold
    files the current settings did not produce.
    """
    detail = settings.describe()
    if not machine:
        return (
            f"{detail}; note: a settings change replaces only differing "
            "selected files of sessions still synced and removes no copy -- "
            "files outside session_files, withheld files and copies of "
            "sessions no longer synced stay as previously published"
        )
    try:
        dest = _fs._existing_relative_directory(root, Path(machine))
        if dest is None:
            return f"{detail}; nothing published for this machine yet"
        stale, truncated = _unprotected_destination_files(settings, dest)
    except (OSError, ValueError):
        return f"{detail}; warning: destination not inspected"
    if stale:
        bound = "at least " if truncated else ""
        return (
            f"{detail}; warning: {bound}{stale} destination file(s) were not "
            "produced under these settings and stay as previously published "
            "(remove them manually if they must not be kept)"
        )
    if truncated:
        return (
            f"{detail}; warning: destination check stopped after "
            f"{_DOCTOR_SCAN_LIMIT} files"
        )
    return f"{detail}; no destination copies outside these settings found"


def receipts_dir(machine_root: Path) -> Path | None:
    """The machine's projection-receipt directory, if one exists."""
    return _fs._existing_relative_directory(machine_root, Path(_protection.RECEIPT_DIR))


def forget_receipt(receipts: Path | None, session_id: str) -> None:
    """Drop the projection receipt of a pruned session."""
    if receipts is not None:
        (receipts / f"{session_id}.json").unlink(missing_ok=True)


def add_doctor_row(target: FilesystemTarget, result: DoctorResult, root: Path) -> None:
    """Report session protection in ``doctor`` when it is enabled."""
    if target.protection is not None and target.protection.enabled:
        result.add(
            "session protection",
            True,
            _protection_doctor_detail(target.protection, root, target.doctor_machine),
        )


def push(
    target: FilesystemTarget,
    source: Path,
    dest: Path,
    machine: str,
    include_sessions: set[str] | None,
    detritus: Any,
    *,
    batch_mode: bool,
    source_identity: SourceIdentityLike | None,
) -> PushResult:
    """Publish under ``sync.redact_secrets`` and/or ``sync.session_files``.

    Mirrors the file-by-file loop of :meth:`FilesystemTarget.push`, but
    copies only the files ``session_files`` selects and, when redacting, a
    derived copy of each one. A file whose protected copy cannot be
    produced keeps whatever version the destination already has: a
    deferred file is retried on the next run, a withheld one is listed.
    Every other file still publishes.
    """
    with ExitStack() as stack:
        if include_sessions is not None and not batch_mode:
            # Same exclusion as the repo-scoped replacement in FilesystemTarget.push().
            lock_file = dest / ".session-sync-rescue.lock"
            try:
                acquired = stack.enter_context(_fs.sync_lock(lock_file, timeout=30))
            except OSError as exc:
                return PushResult(
                    ok=False, detail=f"destination rescue lock failed: {exc}",
                )
            if not acquired:
                return PushResult(
                    ok=False,
                    detail=f"destination rescue lock is busy: {lock_file}",
                )
        return _publish_protected(
            target,
            source,
            dest,
            machine,
            include_sessions,
            detritus,
            batch_mode=batch_mode,
            source_identity=source_identity,
        )


def _publish_protected(
    target: FilesystemTarget,
    source: Path,
    dest: Path,
    machine: str,
    include_sessions: set[str] | None,
    detritus: Any,
    *,
    batch_mode: bool,
    source_identity: SourceIdentityLike | None,
) -> PushResult:
    settings = target.protection
    if settings is None:  # pragma: no cover - guarded by push()
        raise RuntimeError("session protection is not configured")
    redacting = settings.redact_secrets
    fingerprint = settings.transform_fingerprint()
    copied = 0
    nbytes = 0
    current = 0
    redacted = 0
    unselected = 0
    locked_paths: list[Path] = []
    held_back: list[Path] = []
    deferred: list[dict[str, str]] = []
    withheld: list[dict[str, str]] = []
    receipts: dict[str, _protection.Receipt | None] = {}
    entries: dict[str, dict[str, dict[str, Any]]] = {}

    def hold(rel: Path, entry: dict[str, Any]) -> None:
        item = _protection_entry(rel, entry["reason"], entry["prior_copy"])
        if entry["state"] == _protection.DEFERRED:
            deferred.append(item)
            held_back.append(rel)
        else:
            withheld.append(item)

    try:
        destination_detritus = _fs.discover_session_detritus(dest, include_sessions)
    except OSError as exc:
        return PushResult(
            ok=False,
            detail=f"destination detritus discovery failed: {exc}",
        )
    try:
        cleanup_roots = sorted(set(detritus.roots) | set(destination_detritus.roots))
        for relative in cleanup_roots:
            stale = _fs._existing_relative_directory(dest, relative)
            if stale is not None:
                _fs._remove_path_checked(stale)
    except OSError as exc:
        return PushResult(
            ok=False,
            detail=f"detritus cleanup failed for {relative}: {exc}",
        )
    try:
        marker_name_casefold = publication_admission.PUBLICATION_IDENTITY_MARKER.casefold()
        receipt_dir_casefold = _protection.RECEIPT_DIR.casefold()
        for src_file in _fs._iter_regular_source_files(source, detritus.roots):
            rel = src_file.relative_to(source)
            is_root_marker = (
                rel.parent == Path(".")
                and src_file.name.casefold() == marker_name_casefold
            )
            if _fs._is_excluded_name(src_file.name) or (
                source_identity is not None and is_root_marker
            ):
                continue
            if rel.parts[0].casefold() == receipt_dir_casefold:
                continue
            if not is_session_path_included(rel, include_sessions, batch_mode=batch_mode):
                continue
            if not settings.path_included(rel.as_posix()):
                # The index rides along with every batch; count it once
                # per session file instead of once per batch.
                if not (len(rel.parts) == 1 and rel.name in SESSION_INDEX_NAMES):
                    unselected += 1
                continue
            dst_file = dest / rel
            if redacting and len(rel.parts) == 1 and rel.name in SESSION_INDEX_NAMES:
                # The session index is a database: never read, never published.
                hold(rel, {
                    "state": _protection.WITHHELD,
                    "reason": _protection.REASON_DATABASE,
                    "prior_copy": _protection.classify_prior_copy(
                        None, _regular_file_tuple(dst_file),
                    ),
                })
                continue
            try:
                _fs._ensure_relative_directory(dest, rel.parent)
            except OSError as exc:
                return PushResult(ok=False, detail=f"unsafe destination: {exc}")
            if not redacting:
                if _fs._needs_copy(src_file, dst_file):
                    try:
                        _fs._copy_replace(src_file, dst_file)
                    except OSError as exc:
                        if isinstance(exc, (_fs._LockedSourceFile, _fs._SourceChangedDuringCopy)):
                            locked_paths.append(rel)
                            continue
                        return PushResult(
                            ok=False,
                            detail=f"copy failed for {rel}: {exc}",
                        )
                    copied += 1
                    nbytes += os.stat(_fs._windows_extended_path(src_file)).st_size
                current += 1
                continue

            parts = rel.parts
            session_member = len(parts) >= 3 and parts[0] == "session-state"
            sid = parts[1] if session_member else ""
            member = "/".join(parts[2:]) if session_member else rel.name
            previous: dict[str, Any] | None = None
            carried: dict[str, Any] | None = None
            if session_member:
                if sid not in entries:
                    receipts[sid] = _read_receipt(dest, sid)
                    entries[sid] = {}
                receipt = receipts[sid]
                if receipt is not None:
                    previous = receipt.files.get(member)
                    if receipt.settings == fingerprint:
                        carried = previous
                info = _fs._lstat(src_file)
                reuse = _protection.reusable_entry(
                    receipt, fingerprint, member, info.st_size, info.st_mtime_ns,
                )
                if reuse is not None:
                    # Unchanged source under the same transform: the
                    # recorded outcome still holds, so skip re-deriving.
                    dest_tuple = _regular_file_tuple(dst_file)
                    if reuse["state"] != _protection.PUBLISHED:
                        entry = _protection.unpublished_entry(
                            reuse["state"],
                            reuse["reason"],
                            info.st_size,
                            info.st_mtime_ns,
                            _protection.classify_prior_copy(previous, dest_tuple),
                            dest_tuple,
                        )
                        entries[sid][member] = entry
                        hold(rel, entry)
                        continue
                    if dest_tuple == (reuse["dest_size"], reuse["dest_mtime_ns"]):
                        entries[sid][member] = reuse
                        current += 1
                        continue
            try:
                outcome, source_tuple, dest_tuple, replaced = _protected_copy(
                    src_file, dst_file, member, compare_existing=True,
                )
            except OSError as exc:
                if isinstance(exc, (_fs._LockedSourceFile, _fs._SourceChangedDuringCopy)):
                    locked_paths.append(rel)
                    if carried is not None:
                        entries[sid][member] = carried
                    continue
                return PushResult(
                    ok=False,
                    detail=f"copy failed for {rel}: {exc}",
                )
            if outcome.published:
                current += 1
                redacted += outcome.redacted
                if replaced:
                    copied += 1
                    nbytes += source_tuple[0]
                if session_member and dest_tuple is not None:
                    entries[sid][member] = _protection.published_entry(
                        *source_tuple, *dest_tuple, outcome,
                    )
                continue
            dest_tuple = _regular_file_tuple(dst_file)
            entry = _protection.unpublished_entry(
                outcome.state,
                outcome.reason or "",
                *source_tuple,
                _protection.classify_prior_copy(previous, dest_tuple),
                dest_tuple,
            )
            if session_member:
                entries[sid][member] = entry
            hold(rel, entry)
    except OSError as exc:
        return PushResult(ok=False, detail=f"cannot inspect source: {exc}")

    receipt_failures = 0
    for sid, files in entries.items():
        if not _protection.safe_member(sid):
            continue
        payload = _protection.encode_receipt(sid, fingerprint, files) if files else None
        try:
            _write_receipt(dest, sid, payload)
        except OSError:
            # Only costs a re-derivation next run: identical copies are
            # compared, not rewritten.
            receipt_failures += 1

    try:
        latest_detritus = _fs.discover_session_detritus(source, include_sessions)
    except OSError as exc:
        return PushResult(
            ok=False,
            detail=f"detritus revalidation failed: {exc}",
        )
    if latest_detritus.roots != detritus.roots:
        new_roots = set(latest_detritus.roots) - set(detritus.roots)
        try:
            for relative in sorted(new_roots):
                stale = _fs._existing_relative_directory(dest, relative)
                if stale is not None:
                    _fs._remove_path_checked(stale)
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"new detritus cleanup failed for {relative}: {exc}",
            )
        return PushResult(
            ok=False,
            detail="source detritus changed during publication; retry",
        )

    session_count = _fs._count_sessions(dest)
    pending = locked_paths + held_back
    summary = _protection.summarize(
        settings,
        published=current,
        redacted=redacted,
        deferred=deferred,
        withheld=withheld,
        unselected=unselected,
    )
    _fs.write_sync_meta(
        dest,
        machine,
        target.name,
        "partial" if pending else "ok",
        session_count,
        deferred_files=(str(path) for path in pending),
        excluded_roots=(str(root) for root in detritus.roots),
        excluded_file_count=detritus.file_count,
        excluded_byte_count=detritus.byte_count,
        excluded_measurement_complete=detritus.measurement_complete,
        reason="protected-deferral" if held_back else None,
        protection=summary,
    )
    notes: list[str] = []
    if locked_paths:
        examples = ", ".join(str(path) for path in locked_paths[:3])
        notes.append(
            f"skipped {len(locked_paths)} locked file(s), will retry: {examples}"
        )
    if unselected:
        notes.append(f"{unselected} file(s) not in session_files left unpublished")
    if receipt_failures:
        notes.append(f"{receipt_failures} projection receipt(s) not recorded")
    detail = f"-> {dest}"
    if notes:
        detail += " (" + "; ".join(notes) + ")"
    return PushResult(
        ok=True,
        detail=detail,
        file_count=copied,
        byte_count=nbytes,
        excluded_file_count=detritus.file_count,
        excluded_byte_count=detritus.byte_count,
        excluded_roots=tuple(str(root) for root in detritus.roots),
        excluded_measurement_complete=detritus.measurement_complete,
        deferred_sessions=_fs._deferred_session_ids(pending),
        index_deferred=_fs._index_deferred(locked_paths),
        deferred_files=tuple(path.as_posix() for path in pending),
        withheld_files=tuple(item["path"] for item in withheld),
        protection_summary=summary,
    )
