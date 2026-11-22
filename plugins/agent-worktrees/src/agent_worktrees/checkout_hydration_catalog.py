"""Owner-mediated dehydration catalog for reversibly removed managed checkouts.

A dehydrated worktree keeps its tracking record, branch and agency while its
working checkout is removed. The catalog entry
``<worktree-root>/.dehydrated/<worktree-id>.json`` is the durable reverse map:
it is written atomically *before* any removal and retired only *after* a
verified rehydration. Consumers that treat a missing checkout as gone must
consult :func:`is_dehydrated_checkout` / :func:`is_dehydrated` first.

Deliberately light (stdlib plus the durability barrier) so that observation
paths such as ``git_ops.classify_worktree`` can import it lazily.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

SCHEMA = "agent-worktrees.checkout-dehydration"
SCHEMA_VERSION = 1
CATALOG_DIRNAME = ".dehydrated"
STAGE_REMOVING = "removing"
STAGE_DEHYDRATED = "dehydrated"
STAGE_REHYDRATING = "rehydrating"
STAGES = frozenset({STAGE_REMOVING, STAGE_DEHYDRATED, STAGE_REHYDRATING})
# Tracking ``hydration["state"]`` values that mean "do not treat as gone".
HELD_STATES = STAGES
STATE_HYDRATED = "hydrated"
STATE_DEHYDRATED = STAGE_DEHYDRATED


class CatalogError(ValueError):
    """A catalog entry exists but cannot be trusted."""


def catalog_dir(worktree_root: str | os.PathLike[str]) -> Path:
    return Path(worktree_root) / CATALOG_DIRNAME


def entry_path(worktree_root: str | os.PathLike[str], worktree_id: str) -> Path:
    if not worktree_id or any(sep in worktree_id for sep in "/\\") or worktree_id in {".", ".."}:
        raise CatalogError(f"invalid worktree id for dehydration catalog: {worktree_id!r}")
    return catalog_dir(worktree_root) / f"{worktree_id}.json"


def entry_for_checkout(checkout_path: str | os.PathLike[str]) -> Path:
    path = Path(checkout_path)
    return path.parent / CATALOG_DIRNAME / f"{path.name}.json"


def same_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    a = os.path.normcase(os.path.normpath(os.path.abspath(str(left))))
    b = os.path.normcase(os.path.normpath(os.path.abspath(str(right))))
    return a == b


def new_operation_id() -> str:
    return secrets.token_hex(8)


def validate(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CatalogError("dehydration catalog entry is not an object")
    if data.get("schema") != SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError("unsupported dehydration catalog schema")
    if data.get("stage") not in STAGES:
        raise CatalogError(f"unknown dehydration stage: {data.get('stage')!r}")
    identity = data.get("identity")
    git = data.get("git")
    if not isinstance(identity, dict) or not isinstance(git, dict):
        raise CatalogError("dehydration catalog entry lacks identity or git sections")
    for section, keys in ((identity, ("worktree_id", "checkout_path", "repo")), (git, ("branch", "head"))):
        for key in keys:
            if not isinstance(section.get(key), str) or not section.get(key):
                raise CatalogError(f"dehydration catalog entry lacks {key}")
    return data


def read_entry(path: Path) -> dict[str, Any] | None:
    """Return a validated entry, ``None`` when absent, or raise :class:`CatalogError`."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CatalogError(f"dehydration catalog entry is unreadable: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"dehydration catalog entry is corrupt: {exc}") from exc
    return validate(data)


def write_entry(path: Path, data: dict[str, Any]) -> None:
    """Atomically publish ``data`` (temp + fsync + replace + directory barrier)."""
    from . import checkout_durability

    validate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with staging.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        checkout_durability.replace(staging, path, existing=True)
        checkout_durability.directory(path.parent)
    finally:
        if staging.exists():
            staging.unlink()


def delete_entry(path: Path) -> None:
    from . import checkout_durability

    try:
        path.unlink()
    except FileNotFoundError:
        return
    checkout_durability.directory(path.parent)


def iter_entries(worktree_root: str | os.PathLike[str]) -> list[Path]:
    directory = catalog_dir(worktree_root)
    try:
        return sorted(p for p in directory.iterdir() if p.suffix == ".json" and not p.name.startswith("."))
    except (FileNotFoundError, NotADirectoryError):
        return []


def is_dehydrated_checkout(checkout_path: str | os.PathLike[str]) -> bool:
    """Whether a catalog entry claims ``checkout_path``.

    Conservative: an entry that exists but cannot be read still counts, so an
    unreadable catalog never lets a consumer collect the worktree.
    """
    entry = entry_for_checkout(checkout_path)
    try:
        data = read_entry(entry)
    except CatalogError:
        return entry.exists()
    return data is not None and same_path(data["identity"]["checkout_path"], checkout_path)


def record_catalog_path(record: Any) -> Path | None:
    hydration = getattr(record, "hydration", None)
    if isinstance(hydration, dict) and isinstance(hydration.get("catalog"), str) and hydration["catalog"]:
        return Path(hydration["catalog"])
    path = getattr(record, "worktree_path", "")
    return entry_for_checkout(path) if isinstance(path, str) and path else None


def is_dehydrated(record: Any) -> bool:
    """Whether a tracking record's missing checkout is held rather than gone."""
    if record is None:
        return False
    hydration = getattr(record, "hydration", None)
    if isinstance(hydration, dict) and hydration.get("state") in HELD_STATES:
        return True
    catalog = record_catalog_path(record)
    return bool(catalog is not None and catalog.exists())


def _revision(value: object) -> int:
    revision = value.get("revision") if isinstance(value, dict) else None
    return revision if isinstance(revision, int) else 0


def preserve(record: Any, current: Any) -> None:
    """Keep a newer on-disk hydration state across a stale full-record save."""
    theirs = getattr(current, "hydration", None)
    if isinstance(theirs, dict) and _revision(theirs) > _revision(getattr(record, "hydration", None)):
        record.hydration = theirs


def hydration_state(record: Any) -> str:
    """``dehydrated`` (held) or ``hydrated`` for one tracking record."""
    return STATE_DEHYDRATED if is_dehydrated(record) else STATE_HYDRATED
