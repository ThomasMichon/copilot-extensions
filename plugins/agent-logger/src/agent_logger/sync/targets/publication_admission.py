"""Destination identity-admission gate for :meth:`Target.push`.

Split out of ``targets/filesystem.py`` to stay under its module-size cap
(see ``tools/check-module-size.py``): this is cohesive, self-contained
logic with a single call site, not a widening of the filesystem target
itself.

See the ``session-intelligence-and-accounting`` effort's identity-admission
interface: a ``source_identity`` argument on ``Target.push`` requests
destination identity-admission -- the filesystem target enforces it here
before any write; ``SshTarget``/``IngestTarget`` fail closed instead (no
receiver-side atomic admission exists yet for those transports).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from agent_logger.sync.lock import sync_lock
from agent_logger.sync.provenance import (
    ensure_real_directory,
    fsync_directory,
    short_unique_id,
    windows_extended_path,
)
from agent_logger.sync.targets.base import PushResult, SourceIdentityLike

if TYPE_CHECKING:
    from agent_logger.source_roots import MarkerStamp, SourceIdentity

#: ``O_NOFOLLOW`` has no Windows equivalent; the temp-write path below still
#: gets Windows-safe no-follow semantics for free because it only ever opens
#: a brand-new, exclusively created name (nothing pre-existing to follow).
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

#: Persisted at a claimed destination root once a ``source_identity`` push
#: admits it, using source_roots' schema-v1 metadata contract.
PUBLICATION_IDENTITY_MARKER = ".archive-source.json"

#: The marker only ever holds four short identity strings -- bounds both a
#: pre-existing, destination-controlled marker read (the admission target is
#: by definition one the caller does not yet own) and a new claim write.
MAX_MARKER_BYTES = 1024 * 1024

def _unlink_owned_temp(path: Path, file_id: tuple[int, int]) -> bool:
    try:
        info = os.stat(windows_extended_path(path), follow_symlinks=False)
    except FileNotFoundError:
        return True
    if (
        (info.st_dev, info.st_ino) != file_id
        or not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x00000400
    ):
        return False
    os.unlink(windows_extended_path(path))
    return True


def _destination_has_content(dest: Path) -> bool:
    """Refuse an unowned leaf after its first child, without materializing a list."""
    try:
        with os.scandir(windows_extended_path(dest)) as entries:
            return next(entries, None) is not None
    except FileNotFoundError:
        return False


def _publication_lock_path(dest: Path) -> Path:
    key = os.path.normcase(dest.name).encode("utf-8")
    return dest.parent / f".publication-admission-{hashlib.sha256(key).hexdigest()}.lock"


def _read_marker(
    marker_path: Path,
) -> tuple[SourceIdentity, tuple[str, ...], MarkerStamp] | None:
    """Read through the canonical bounded, schema-validating no-link reader."""
    from agent_logger.source_roots import read_source_metadata

    io_path = Path(windows_extended_path(marker_path))
    try:
        io_path.lstat()
    except FileNotFoundError:
        return None
    return read_source_metadata(io_path)


def _move_marker_no_replace_windows(temp_path: Path, marker_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    # WRITE_THROUGH only: deliberately omit REPLACE_EXISTING.
    if not move_file(
        windows_extended_path(temp_path), windows_extended_path(marker_path), 0x00000008
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _publish_marker_no_replace(temp_path: Path, marker_path: Path) -> None:
    """Durably publish a new marker, never replacing an existing entry."""
    if os.name == "nt":
        _move_marker_no_replace_windows(temp_path, marker_path)
    else:
        os.link(
            windows_extended_path(temp_path),
            windows_extended_path(marker_path),
            follow_symlinks=False,
        )
        fsync_directory(marker_path.parent)


def _check_existing_claim(
    existing: tuple[SourceIdentity, tuple[str, ...], MarkerStamp] | None,
    incoming: SourceIdentity,
    publication_key: str,
    marker_path: Path,
) -> PushResult | None:
    if existing is None:
        return PushResult(ok=False, detail=f"publication marker disappeared: {marker_path}")
    recorded, aliases, _ = existing
    if "/" in publication_key:
        group = publication_key.split("/", 1)[0]
        expected_kind = "container" if group.endswith(".containers") else "codespace"
        if recorded.venue_kind != expected_kind:
            return PushResult(ok=False, detail="provider group disagrees with recorded venue kind")
    normalize = str.casefold if os.name == "nt" else str
    allowed = {normalize(key) for key in (recorded.namespace, *aliases)}
    if normalize(publication_key) not in allowed:
        return PushResult(ok=False, detail="publication key disagrees with recorded namespace/aliases")
    if recorded == incoming:
        return None
    return PushResult(ok=False, detail=f"publication identity mismatch at {marker_path}")


def _resolve_post_race_marker(
    marker_path: Path, incoming: SourceIdentity, publication_key: str,
) -> PushResult | None:
    try:
        existing = _read_marker(marker_path)
    except (OSError, ValueError, RecursionError) as exc:
        return PushResult(ok=False, detail=f"unreadable publication marker: {exc}")
    return _check_existing_claim(existing, incoming, publication_key, marker_path)


def _verify_published_marker(
    marker_path: Path, file_id: tuple[int, int],
    incoming: SourceIdentity, publication_key: str,
) -> PushResult | None:
    try:
        existing = _read_marker(marker_path)
    except (OSError, ValueError, RecursionError) as exc:
        return PushResult(ok=False, detail=f"unreadable published marker: {exc}")
    if existing is None or existing[2][:2] != file_id:
        return PushResult(ok=False, detail="published marker differs from the created file")
    return _check_existing_claim(existing, incoming, publication_key, marker_path)


@contextmanager
def publication_transaction(
    dest: Path,
    identity: SourceIdentityLike | None,
    *,
    publication_key: str,
    supports_identity_admission: bool = True,
) -> Iterator[PushResult | None]:
    """Hold destination admission and payload publication under one lock.

    Returns ``None`` when the push may proceed: legacy ``identity=None``
    behavior, a first claim of an empty destination leaf, or an idempotent
    re-push matching the already-persisted marker. Returns a failing
    :class:`PushResult` when admission must be refused outright.

    *supports_identity_admission* lets a caller whose destination cannot
    actually serialize concurrent cross-writer publication (e.g. a OneDrive
    replica reconciled by cloud sync, not by this process) fail closed
    instead of enforcing a lock that only ever coordinates writers on this
    one host.

    Comparison, any marker write, and the emptiness check all happen under
    one dedicated destination lock so two concurrent publishers on the same
    host cannot race to claim different identities. Atomic no-replace
    publication also refuses a marker created by a non-cooperating writer.
    This advisory lock does not protect against ancestor-directory swaps;
    that separate filesystem containment limitation remains unchanged.
    Never overwrites or guesses: a mismatched marker, or an existing
    nonempty leaf with no marker at all, is refused rather than silently
    adopted.
    """
    if identity is None:
        yield None
        return
    if not supports_identity_admission:
        yield PushResult(
            ok=False,
            detail=(
                f"{dest} target cannot enforce cross-writer identity admission "
                "(no receiver/cloud-side atomic admission for this transport yet)"
            ),
        )
        return
    lock_file = _publication_lock_path(dest)
    with ExitStack() as stack:
        try:
            acquired = stack.enter_context(sync_lock(lock_file, timeout=30))
            failure = _admit_under_lock(dest, identity, publication_key, lock_file, acquired)
        except OSError as exc:
            failure = PushResult(ok=False, detail=f"publication admission lock failed: {exc}")
        yield failure


def check_publication_identity(
    dest: Path,
    identity: SourceIdentityLike | None,
    *,
    publication_key: str,
    supports_identity_admission: bool = True,
) -> PushResult | None:
    """Admit a marker only; payload writers must use publication_transaction."""
    with publication_transaction(
        dest, identity, publication_key=publication_key,
        supports_identity_admission=supports_identity_admission,
    ) as failure:
        return failure


def _admit_under_lock(
    dest: Path,
    identity: SourceIdentityLike,
    publication_key: str,
    lock_file: Path,
    acquired: bool,
) -> PushResult | None:
    if not acquired:
        return PushResult(
            ok=False,
            detail=f"publication admission lock is busy: {lock_file}",
        )
    from agent_logger.source_publication import validate_publication_key
    from agent_logger.source_roots import SourceIdentity, validate_source_key

    try:
        incoming = SourceIdentity(**identity.to_dict())
        validate_source_key(publication_key)
    except (TypeError, ValueError) as exc:
        return PushResult(ok=False, detail=f"invalid publication identity: {exc}")
    payload = json.dumps({"schema_version": 1, **incoming.to_dict()}, sort_keys=True)
    if len(payload.encode("utf-8")) > MAX_MARKER_BYTES:
        return PushResult(ok=False, detail="publication identity payload too large")
    marker_path = dest / PUBLICATION_IDENTITY_MARKER
    try:
        existing = _read_marker(marker_path)
    except (OSError, ValueError, RecursionError) as exc:
        return PushResult(ok=False, detail=f"unreadable publication marker: {exc}")
    if existing is not None:
        return _check_existing_claim(existing, incoming, publication_key, marker_path)
    try:
        validate_publication_key(publication_key, incoming)
    except ValueError as exc:
        return PushResult(ok=False, detail=f"invalid publication key: {exc}")
    try:
        has_content = _destination_has_content(dest)
    except OSError as exc:
        return PushResult(ok=False, detail=f"cannot inspect destination: {exc}")
    if has_content:
        return PushResult(
            ok=False,
            detail=(
                f"destination {dest} has existing content with no "
                "publication marker; refusing to claim an unowned leaf"
            ),
        )
    temp_path = dest / f".{PUBLICATION_IDENTITY_MARKER}.{short_unique_id()}.tmp"
    created_file_id: tuple[int, int] | None = None
    failure: PushResult | None = None
    try:
        ensure_real_directory(dest, durable=True)
        # Write through an exclusively created, brand-new temp name (so
        # there is nothing pre-existing to follow on any platform), fsync
        # its content, then publish no-replace: a race that beats us to
        # marker_path is refused back up for re-resolution, never silently
        # overwritten (see _publish_marker_no_replace).
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
        fd = os.open(windows_extended_path(temp_path), flags, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            created = os.fstat(handle.fileno())
            created_file_id = created.st_dev, created.st_ino
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            _publish_marker_no_replace(temp_path, marker_path)
        except FileExistsError:
            failure = _resolve_post_race_marker(marker_path, incoming, publication_key)
        else:
            failure = _verify_published_marker(
                marker_path, created_file_id, incoming, publication_key
            )
    except OSError as exc:
        failure = PushResult(ok=False, detail=f"cannot claim destination: {exc}")
    finally:
        if created_file_id is not None:
            try:
                if not _unlink_owned_temp(temp_path, created_file_id):
                    detail = "claim temporary file identity changed; replacement left untouched"
                    if failure is not None:
                        detail = f"{failure.detail}; {detail}"
                    failure = PushResult(ok=False, detail=detail)
            except OSError as exc:
                detail = f"cannot remove owned claim temporary file: {exc}"
                if failure is not None:
                    detail = f"{failure.detail}; {detail}"
                failure = PushResult(ok=False, detail=detail)
    return failure
