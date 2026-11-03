"""Explicit new data-home allocation and passive checkout-independent discovery."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path

from . import config as cfg
from . import project_state, record_io, registry_paths, tracking
from .worktree_data_contract import (
    MAX_DESCRIPTOR_BYTES,
    MAX_PLACEMENTS,
    SCHEMA,
    VERSION,
    DataDescriptor,
    DataScope,
    PlacementRegistration,
    WorktreeDataError,
    WorktreeDataInventory,
    WorktreeDataPlacement,
    decode_descriptor,
    encode_descriptor,
    generation,
    lane_identity,
    segment,
    store_identity,
)

__all__ = [
    "SCHEMA",
    "VERSION",
    "WorktreeDataError",
    "WorktreeDataInventory",
    "WorktreeDataPlacement",
    "allocate_worktree_data",
    "read_worktree_data",
]

_HOME = "worktree-state"
_DESCRIPTOR = "descriptor.json"
_WINDOWS = os.name == "nt"


def _metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise WorktreeDataError("unsafe-path", f"data child is a filesystem alias: {path.name}")
    return metadata


def _directory(path: Path, *, optional: bool = False) -> os.stat_result | None:
    metadata = _metadata(path)
    if metadata is None:
        if optional:
            return None
        raise WorktreeDataError("partial-registration", f"data directory is missing: {path.name}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorktreeDataError("unsafe-path", f"data child is not a directory: {path.name}")
    return metadata


def _regular(path: Path, *, optional: bool = False) -> os.stat_result | None:
    metadata = _metadata(path)
    if metadata is None:
        if optional:
            return None
        raise WorktreeDataError("partial-registration", f"data file is missing: {path.name}")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise WorktreeDataError("unsafe-path", f"data file is not an unaliased file: {path.name}")
    return metadata


def _project_root(project: str) -> tuple[Path, os.stat_result | None]:
    segment(project, "project")
    root = Path(cfg.project_dir(project))
    if not root.is_absolute():
        raise WorktreeDataError("owner-unavailable", "project-data owner returned a relative root")
    try:
        # The existing project owner admits the root; configured root aliases are valid.
        metadata = root.stat()
    except FileNotFoundError:
        return root, None
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorktreeDataError("owner-unavailable", "project-data root is not a directory")
    return root, metadata


def _canonical_record(
    root: Path,
    worktree_id: str,
    project: str,
) -> tracking.WorktreeRecord | None:
    directory = root / "worktrees"
    if _directory(directory, optional=True) is None:
        return None
    path = directory / f"{worktree_id}.yaml"
    if _regular(path, optional=True) is None:
        return None
    try:
        mapping = record_io.read_record_mapping(path)
        for field in ("worktree_id", "creation_nonce", "repo"):
            segment(mapping.get(field), f"canonical {field}")
        if mapping["worktree_id"] != worktree_id:
            raise WorktreeDataError("identity-conflict", "canonical worktree ID does not match")
        if mapping["repo"] != project:
            raise WorktreeDataError(
                "identity-conflict", "canonical repository does not match selected project"
            )
        return tracking.parse_record_data(mapping, path=path)
    except WorktreeDataError:
        raise
    except Exception as error:
        raise WorktreeDataError(
            "invalid-record", "canonical tracking record is unreadable"
        ) from error


def _scope(
    project: str,
    repository: str,
    root_metadata: os.stat_result,
) -> DataScope:
    segment(repository, "repository")
    if repository != project:
        raise WorktreeDataError(
            "identity-conflict", "data repository does not match selected project"
        )
    remote = project_state._normalized_repository_remote(project)
    repository_id = "repo-" + hashlib.sha256(remote.encode("utf-8")).hexdigest()[:24]
    context = registry_paths.installation_context()
    marketplace = None if context is None else context["marketplaceId"]
    if marketplace is not None:
        segment(marketplace, "marketplace_id")
    return DataScope(
        project,
        repository,
        repository_id,
        marketplace,
        root_metadata.st_dev,
        root_metadata.st_ino,
    )


def _same_directory(path: Path, previous: os.stat_result) -> None:
    current = path.stat()
    if (current.st_dev, current.st_ino) != (previous.st_dev, previous.st_ino):
        raise WorktreeDataError("identity-conflict", "project-data owner changed during operation")


def _read_descriptor(path: Path) -> DataDescriptor:
    before = _regular(path)
    assert before is not None
    if before.st_size > MAX_DESCRIPTOR_BYTES:
        raise WorktreeDataError("descriptor-limit", "descriptor exceeds byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise WorktreeDataError("unsafe-path", "descriptor changed during read")
        raw = handle.read(MAX_DESCRIPTOR_BYTES + 1)
    return decode_descriptor(raw)


def _members(path: Path, expected: set[str], limit: int) -> None:
    found: set[str] = set()
    with os.scandir(path) as entries:
        for entry in entries:
            found.add(entry.name)
            if len(found) > limit:
                raise WorktreeDataError("descriptor-limit", "data member inventory exceeds limit")
    if found != expected:
        raise WorktreeDataError("partial-registration", "data member inventory is not admitted")


def _validate_homes(directory: Path, descriptor: DataDescriptor) -> None:
    _members(directory, {_DESCRIPTOR, "agendas"}, 2)
    agendas = directory / "agendas"
    _directory(agendas)
    _members(agendas, {entry.lane for entry in descriptor.placements}, MAX_PLACEMENTS)
    for entry in descriptor.placements:
        _directory(agendas / entry.lane)


def _load(
    project: str,
    worktree_id: str,
) -> tuple[Path, os.stat_result | None, tracking.WorktreeRecord | None, DataDescriptor | None]:
    segment(worktree_id, "worktree_id")
    root, root_metadata = _project_root(project)
    record = _canonical_record(root, worktree_id, project)
    base = root / _HOME
    if _directory(base, optional=True) is None:
        return root, root_metadata, record, None
    directory = base / worktree_id
    if _directory(directory, optional=True) is None:
        return root, root_metadata, record, None
    descriptor = _read_descriptor(directory / _DESCRIPTOR)
    if descriptor.worktree_id != worktree_id:
        raise WorktreeDataError("identity-conflict", "descriptor worktree ID does not match")
    if root_metadata is None:
        raise WorktreeDataError("owner-unavailable", "project-data root is unavailable")
    expected_scope = _scope(project, descriptor.scope.repository, root_metadata)
    if descriptor.scope != expected_scope:
        raise WorktreeDataError(
            "identity-conflict", "descriptor project/repository/cell does not match"
        )
    if record is not None and (
        record.creation_nonce != descriptor.creation_nonce
        or record.repo != descriptor.scope.repository
    ):
        raise WorktreeDataError(
            "identity-conflict", "descriptor does not match canonical incarnation"
        )
    _validate_homes(directory, descriptor)
    _same_directory(root, root_metadata)
    return root, root_metadata, record, descriptor


def _placement(
    root: Path,
    descriptor: DataDescriptor,
    entry: PlacementRegistration,
) -> WorktreeDataPlacement:
    return WorktreeDataPlacement(
        descriptor.scope,
        descriptor.worktree_id,
        descriptor.creation_nonce,
        entry.domain,
        entry.lane,
        entry.store_id,
        entry.placement_generation,
        str(root / _HOME / descriptor.worktree_id / entry.relative_home),
    )


def read_worktree_data(
    project: str,
    worktree_id: str,
    *,
    store_id: str | None = None,
    creation_nonce: str | None = None,
    placement_generation: int | None = None,
) -> WorktreeDataInventory | None:
    """Read admitted inventory or one store, without writes, checkout or session.

    Missing canonical records permit retained-data discovery, not execution.
    No registration is the only empty result; malformed or partial state refuses.
    """
    try:
        if store_id is not None:
            store_identity(store_id)
        if creation_nonce is not None:
            segment(creation_nonce, "creation_nonce")
        if placement_generation is not None:
            generation(placement_generation)
        root, _, record, descriptor = _load(project, worktree_id)
        if record is not None and (
            creation_nonce is not None and creation_nonce != record.creation_nonce
        ):
            raise WorktreeDataError(
                "identity-conflict", "requested canonical incarnation does not match"
            )
        if descriptor is None:
            return None
        if creation_nonce is not None and creation_nonce != descriptor.creation_nonce:
            raise WorktreeDataError(
                "identity-conflict", "requested data incarnation does not match"
            )
        entries = tuple(
            entry
            for entry in descriptor.placements
            if store_id is None or entry.store_id == store_id
        )
        if not entries:
            return None
        if placement_generation is not None and any(
            entry.placement_generation != placement_generation for entry in entries
        ):
            raise WorktreeDataError(
                "identity-conflict", "requested placement generation does not match"
            )
        return WorktreeDataInventory(
            descriptor.scope,
            worktree_id,
            descriptor.creation_nonce,
            tuple(_placement(root, descriptor, entry) for entry in entries),
            "matching" if record is not None else "missing",
        )
    except WorktreeDataError:
        raise
    except Exception as error:
        raise WorktreeDataError(
            "owner-unavailable", "worktree-data owner cannot be read"
        ) from error


def _mkdir(path: Path) -> None:
    path.mkdir()
    _directory(path)
    _sync_directory(path)
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    if _WINDOWS:
        _windows_flush_directory(path)
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _windows_flush_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Match the established directory barrier: GENERIC_WRITE, sharing, OPEN_EXISTING,
    # BACKUP_SEMANTICS. This opens the owned directory, never an elevated volume.
    handle = kernel.CreateFileW(str(path), 0x40000000, 0x7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def _require_durability(root: Path) -> None:
    try:
        _sync_directory(root)
    except OSError as error:
        raise WorktreeDataError(
            "durability-unavailable", "project-data filesystem cannot flush directory entries"
        ) from error


def _publish_descriptor(path: Path, raw: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=".descriptor-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if _WINDOWS:
            from zdd.routing_persistence import windows_replace

            windows_replace(Path(temporary), path)
        else:
            tracking._replace_with_retry(temporary, str(path))
        _sync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _sync_registered_home(root: Path, descriptor: DataDescriptor) -> None:
    directory = root / _HOME / descriptor.worktree_id
    path = directory / _DESCRIPTOR
    _regular(path)
    flags = os.O_RDWR if _WINDOWS else os.O_RDONLY
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    # A previous caller may have failed after rename, before acknowledging it.
    for entry in descriptor.placements:
        _sync_directory(directory / entry.relative_home)
    for parent in (directory / "agendas", directory, directory.parent, root):
        _sync_directory(parent)


def allocate_worktree_data(
    project: str,
    worktree_id: str,
    *,
    creation_nonce: str,
    domain: str,
    lane: str,
    store_id: str,
) -> WorktreeDataPlacement:
    """Explicitly admit an empty new home under the exact canonical record lock.

    An exact retry is idempotent. Foreign homes and interrupted publication are
    retained and refused, never adopted, overwritten or repaired automatically.
    Success requires file and directory durability barriers. A filesystem unable
    to flush directory entries refuses allocation before creating state.
    """
    try:
        segment(worktree_id, "worktree_id")
        segment(creation_nonce, "creation_nonce")
        store_identity(store_id)
        lane_identity(domain, lane)
        root, root_metadata = _project_root(project)
        if root_metadata is None or _canonical_record(root, worktree_id, project) is None:
            raise WorktreeDataError(
                "owner-unavailable", "allocation requires a canonical tracking record"
            )
        _require_durability(root)
        record_path = root / "worktrees" / f"{worktree_id}.yaml"
        _regular(record_path.with_suffix(".lock"), optional=True)
        with tracking._RecordLock(record_path, require_sidecar=True):
            _same_directory(root, root_metadata)
            _, selected_metadata, record, descriptor = _load(project, worktree_id)
            if selected_metadata is None or (
                selected_metadata.st_dev,
                selected_metadata.st_ino,
            ) != (root_metadata.st_dev, root_metadata.st_ino):
                raise WorktreeDataError(
                    "identity-conflict", "selected project-data owner changed while locking"
                )
            if record is None:
                raise WorktreeDataError(
                    "owner-unavailable", "canonical tracking record disappeared"
                )
            if record.creation_nonce != creation_nonce:
                raise WorktreeDataError(
                    "identity-conflict", "allocation incarnation does not match"
                )
            scope = _scope(project, record.repo, root_metadata)
            if descriptor is None:
                descriptor = DataDescriptor(scope, worktree_id, creation_nonce, ())
            for entry in descriptor.placements:
                if entry.store_id == store_id or (entry.domain, entry.lane) == (domain, lane):
                    if (entry.domain, entry.lane, entry.store_id) != (domain, lane, store_id):
                        raise WorktreeDataError(
                            "identity-conflict", "lane or store is already allocated"
                        )
                    _sync_registered_home(root, descriptor)
                    return _placement(root, descriptor, entry)
            if len(descriptor.placements) >= MAX_PLACEMENTS:
                raise WorktreeDataError("descriptor-limit", "placement inventory is full")
            entry = PlacementRegistration(domain, lane, store_id, 1, f"{domain}/{lane}")
            updated = replace(descriptor, placements=(*descriptor.placements, entry))
            raw = encode_descriptor(updated)
            base = root / _HOME
            if _directory(base, optional=True) is None:
                try:
                    _mkdir(base)
                except FileExistsError:
                    _directory(base)
            directory = base / worktree_id
            if not descriptor.placements:
                _mkdir(directory)
                _mkdir(directory / domain)
            _directory(directory)
            _directory(directory / domain)
            _mkdir(directory / entry.relative_home)
            _same_directory(root, root_metadata)
            _publish_descriptor(directory / _DESCRIPTOR, raw)
            return _placement(root, updated, entry)
    except WorktreeDataError:
        raise
    except TimeoutError as error:
        raise WorktreeDataError("owner-busy", "canonical record lock is unavailable") from error
    except Exception as error:
        raise WorktreeDataError(
            "allocation-failed", "data-home allocation did not complete"
        ) from error
