"""Passive, bounded project discovery through the durable-placement owner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from . import project_state
from . import worktree_data_store as owner
from .worktree_data_contract import (
    MAX_INVENTORY_BYTES,
    MAX_PROJECT_WORKTREES,
    DataScope,
    WorktreeDataError,
    WorktreeDataInventory,
    segment,
)


@dataclass(frozen=True)
class ProjectWorktreeDataInventory:
    """Observed registrations, not a transactional export or execution grant."""

    project: str
    repository_identity: str
    scope: DataScope | None
    worktrees: tuple[WorktreeDataInventory, ...]
    live_authority: bool = False

    def to_dict(self) -> dict:
        """Return detached JSON-compatible registration evidence."""
        return {
            **asdict(self),
            "worktrees": [entry.to_dict() for entry in self.worktrees],
        }


def _stamp(metadata: os.stat_result | None, *, contents: bool = True) -> tuple | None:
    if metadata is None:
        return None
    identity = (metadata.st_dev, metadata.st_ino)
    if not contents:
        return identity
    return (*identity, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _registration_fence(root: Path, worktree: str) -> tuple:
    directory = root / owner._HOME / worktree
    records = root / "worktrees"
    return (
        _stamp(owner._directory(directory)),
        _stamp(owner._directory(directory / "agendas")),
        _stamp(owner._regular(directory / owner._DESCRIPTOR)),
        _stamp(owner._directory(records, optional=True), contents=False),
        _stamp(owner._regular(records / f"{worktree}.yaml", optional=True)),
    )


def _unchanged(before: object, after: object) -> None:
    if before != after:
        raise WorktreeDataError(
            "identity-conflict",
            "project registration changed during inventory; read again",
        )


def _recheck(
    root: Path,
    value: WorktreeDataInventory,
    before: tuple,
    lanes: tuple | None = None,
) -> None:
    try:
        _unchanged(before, _registration_fence(root, value.worktree_id))
        if lanes is not None:
            _unchanged(
                lanes,
                tuple(
                    _stamp(owner._directory(Path(entry.state_home)), contents=False)
                    for entry in value.placements
                ),
            )
    except (WorktreeDataError, OSError) as error:
        raise WorktreeDataError(
            "identity-conflict",
            "registration changed during inventory; read again",
        ) from error


def read_project_worktree_data(project: str) -> ProjectWorktreeDataInventory:
    """Discover every retained descriptor or refuse the entire observation.

    Canonical record enumeration cannot find retired descriptors. Enumerate only
    the owner's registration directory, then reuse its exact passive reader.
    No locks, initialization, store-content reads, or filesystem fallback.
    """
    try:
        root, root_metadata = owner._project_root(project, allow_missing=True)
        repository = project_state._normalized_repository_remote(project)
        scope = (
            owner._scope(project, project, root_metadata) if root_metadata is not None else None
        )
        base = root / owner._HOME
        base_stamp = _stamp(owner._directory(base, optional=True))
        names: list[str] = []
        if base_stamp is not None:
            with os.scandir(base) as entries:
                for entry in entries:
                    if len(names) >= MAX_PROJECT_WORKTREES:
                        raise WorktreeDataError(
                            "inventory-limit",
                            "project registration count exceeds inventory limit",
                        )
                    names.append(segment(entry.name, "worktree_id"))
        results: list[WorktreeDataInventory] = []
        fences: list[tuple[tuple, tuple]] = []
        for worktree in sorted(names):
            before = _registration_fence(root, worktree)
            value = owner.read_worktree_data(project, worktree)
            if value is None:
                raise WorktreeDataError(
                    "partial-registration",
                    "enumerated registration is unavailable",
                )
            _unchanged(scope, value.scope)
            _recheck(root, value, before)
            lanes = tuple(
                _stamp(owner._directory(Path(entry.state_home)), contents=False)
                for entry in value.placements
            )
            results.append(value)
            fences.append((before, lanes))
        for value, (before, lanes) in zip(results, fences):
            _recheck(root, value, before, lanes)
        _unchanged(base_stamp, _stamp(owner._directory(base, optional=True)))
        final_root, final_metadata = owner._project_root(project, allow_missing=True)
        _unchanged(
            (root, _stamp(root_metadata, contents=False)),
            (final_root, _stamp(final_metadata, contents=False)),
        )
        _unchanged(repository, project_state._normalized_repository_remote(project))
        if final_metadata is not None:
            _unchanged(scope, owner._scope(project, project, final_metadata))
        result = ProjectWorktreeDataInventory(project, repository, scope, tuple(results))
        raw = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > MAX_INVENTORY_BYTES:
            raise WorktreeDataError("inventory-limit", "project inventory exceeds byte limit")
        return result
    except WorktreeDataError:
        raise
    except Exception as error:
        raise WorktreeDataError(
            "owner-unavailable",
            "project worktree-data inventory cannot be read",
        ) from error
