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

import json
import os
from pathlib import Path

from agent_logger.sync.lock import sync_lock
from agent_logger.sync.provenance import (
    ensure_real_directory,
    fsync_directory,
    is_link_or_reparse,
    open_regular_no_follow,
    short_unique_id,
    windows_extended_path,
)
from agent_logger.sync.targets.base import PushResult, SourceIdentityLike

#: ``O_NOFOLLOW`` has no Windows equivalent; the temp-write path below still
#: gets Windows-safe no-follow semantics for free because it only ever opens
#: a brand-new, exclusively created name (nothing pre-existing to follow).
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

#: Persisted at a claimed destination root once a ``source_identity`` push
#: admits it -- the same bounded JSON shape the (forthcoming, peer-owned)
#: ``source_publication`` module's ``.archive-source.json`` CLI input uses.
#: If that upstream schema lands with different field names, treat this as
#: the contract correction to record in the effort README, not a silent
#: local divergence.
PUBLICATION_IDENTITY_MARKER = ".archive-source.json"

#: The marker only ever holds four short identity strings -- bounds both a
#: pre-existing, destination-controlled marker read (the admission target is
#: by definition one the caller does not yet own) and a new claim write.
MAX_MARKER_BYTES = 64 * 1024

def _unlink_if_exists(path: Path) -> None:
    try:
        os.unlink(windows_extended_path(path))
    except FileNotFoundError:
        pass


def _dest_entry_names(dest: Path) -> list[str]:
    """List *dest*'s direct children through an extended-path-safe scan."""
    try:
        with os.scandir(windows_extended_path(dest)) as entries:
            return [entry.name for entry in entries]
    except FileNotFoundError:
        return []


def _read_marker(marker_path: Path) -> dict | None:
    """Read an existing marker without ever following a link/reparse point
    at *marker_path* on any platform (POSIX ``O_NOFOLLOW`` and Windows
    reparse-point handling are both covered by ``open_regular_no_follow``,
    not just the POSIX-only flag) -- a symlink sitting at the marker path
    is never legitimate ownership state, so it is refused, not followed.
    The read is bounded: the admission target is by definition a
    destination the caller does not yet own, so a pre-existing oversized
    marker must not be read to EOF."""
    try:
        mode = os.stat(windows_extended_path(marker_path), follow_symlinks=False).st_mode
    except FileNotFoundError:
        return None
    if is_link_or_reparse(marker_path, mode):
        raise OSError(f"publication marker at {marker_path} is a link/reparse point")
    with open_regular_no_follow(marker_path) as handle:
        raw = handle.read(MAX_MARKER_BYTES + 1)
    if len(raw) > MAX_MARKER_BYTES:
        raise OSError(f"publication marker at {marker_path} exceeds {MAX_MARKER_BYTES} bytes")
    return json.loads(raw.decode("utf-8"))


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


def _resolve_post_race_marker(marker_path: Path, incoming: dict) -> PushResult | None:
    try:
        existing = _read_marker(marker_path)
    except (OSError, ValueError, RecursionError) as exc:
        return PushResult(ok=False, detail=f"unreadable publication marker: {exc}")
    if existing == incoming:
        return None  # the race's winner happened to claim the same identity
    return PushResult(
        ok=False,
        detail=(
            f"publication identity mismatch at {marker_path}: "
            f"destination already claimed by {existing}"
        ),
    )


def check_publication_identity(
    dest: Path,
    identity: SourceIdentityLike | None,
    *,
    supports_identity_admission: bool = True,
) -> PushResult | None:
    """Enforce destination identity-admission before any write under *dest*.

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
        return None
    if not supports_identity_admission:
        return PushResult(
            ok=False,
            detail=(
                f"{dest} target cannot enforce cross-writer identity admission "
                "(no receiver/cloud-side atomic admission for this transport yet)"
            ),
        )
    lock_file = dest.parent / f".{dest.name}.publication-admission.lock"
    try:
        with sync_lock(lock_file, timeout=30) as acquired:
            return _admit_under_lock(dest, identity, lock_file, acquired)
    except OSError as exc:
        return PushResult(ok=False, detail=f"publication admission lock failed: {exc}")


def _admit_under_lock(
    dest: Path,
    identity: SourceIdentityLike,
    lock_file: Path,
    acquired: bool,
) -> PushResult | None:
    if not acquired:
        return PushResult(
            ok=False,
            detail=f"publication admission lock is busy: {lock_file}",
        )
    incoming = {
        "provider": identity.provider,
        "host": identity.host,
        "repository": identity.repository,
        "venue": identity.venue,
    }
    payload = json.dumps(incoming, sort_keys=True)
    if len(payload.encode("utf-8")) > MAX_MARKER_BYTES:
        return PushResult(ok=False, detail="publication identity payload too large")
    marker_path = dest / PUBLICATION_IDENTITY_MARKER
    try:
        existing = _read_marker(marker_path)
    except (OSError, ValueError, RecursionError) as exc:
        return PushResult(ok=False, detail=f"unreadable publication marker: {exc}")
    if existing is not None:
        if existing == incoming:
            return None  # idempotent re-push of the same identity
        return PushResult(
            ok=False,
            detail=(
                f"publication identity mismatch at {marker_path}: "
                f"destination already claimed by {existing}"
            ),
        )
    try:
        names = _dest_entry_names(dest)
        has_content = bool(names)
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
    created_temp = False
    failure: PushResult | None = None
    try:
        ensure_real_directory(dest)
        # Write through an exclusively created, brand-new temp name (so
        # there is nothing pre-existing to follow on any platform), fsync
        # its content, then publish no-replace: a race that beats us to
        # marker_path is refused back up for re-resolution, never silently
        # overwritten (see _publish_marker_no_replace).
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
        fd = os.open(windows_extended_path(temp_path), flags, 0o644)
        created_temp = True
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            _publish_marker_no_replace(temp_path, marker_path)
        except FileExistsError:
            failure = _resolve_post_race_marker(marker_path, incoming)
    except OSError as exc:
        failure = PushResult(ok=False, detail=f"cannot claim destination: {exc}")
    finally:
        if created_temp:
            try:
                _unlink_if_exists(temp_path)
            except OSError as exc:
                detail = f"cannot remove owned claim temporary file: {exc}"
                if failure is not None:
                    detail = f"{failure.detail}; {detail}"
                failure = PushResult(ok=False, detail=detail)
    return failure
