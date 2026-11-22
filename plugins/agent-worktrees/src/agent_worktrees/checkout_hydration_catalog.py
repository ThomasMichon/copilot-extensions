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
    stage = data.get("stage")
    if not isinstance(stage, str) or stage not in STAGES:
        raise CatalogError(f"unknown dehydration stage: {stage!r}")
    if not isinstance(data.get("agency", {}), dict) or not isinstance(data.get("recovery_hold") or {}, dict):
        raise CatalogError("dehydration catalog entry has malformed agency or recovery sections")
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
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        # The new directory entry itself must be durable before any removal.
        checkout_durability.directory(path.parent.parent)
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

    Conservative: an entry named for the checkout that cannot be read still
    counts, so an unreadable catalog never lets a consumer collect it.
    """
    path = Path(checkout_path)
    named = entry_for_checkout(path)
    try:
        data = read_entry(named)
    except CatalogError:
        return True
    if data is not None and same_path(data["identity"]["checkout_path"], path):
        return True
    return entry_claiming(path.parent, path) is not None


def entry_claiming(worktree_root: str | os.PathLike[str], checkout_path: str | os.PathLike[str]) -> Path | None:
    """The readable entry under ``worktree_root`` whose identity names ``checkout_path``."""
    for entry in iter_entries(worktree_root):
        try:
            data = read_entry(entry)
        except CatalogError:
            continue
        if data is not None and same_path(data["identity"]["checkout_path"], checkout_path):
            return entry
    return None


def record_entry_path(record: Any) -> Path | None:
    """The entry location persisted by a record's own checkout path.

    The catalog lives beside the recorded checkout (``<parent>/.dehydrated``),
    so a later change of the configured worktree root does not lose it.
    """
    worktree_id = getattr(record, "worktree_id", "")
    path = getattr(record, "worktree_path", "")
    if not isinstance(worktree_id, str) or not isinstance(path, str) or not worktree_id or not path:
        return None
    try:
        return entry_path(Path(path).parent, worktree_id)
    except CatalogError:
        return None


def is_dehydrated(record: Any) -> bool:
    """Whether a tracking record's missing checkout is held rather than gone."""
    if record is None:
        return False
    entry = record_entry_path(record)
    if entry is not None and entry.exists():
        return True
    path = getattr(record, "worktree_path", "")
    return bool(isinstance(path, str) and path and is_dehydrated_checkout(path))


def hydration_state(record: Any) -> str:
    """``dehydrated`` (held) or ``hydrated`` for one tracking record."""
    return STATE_DEHYDRATED if is_dehydrated(record) else STATE_HYDRATED


def _repos(config: Any) -> list[Any]:
    found: list[Any] = list((getattr(config, "repos", None) or {}).values())
    try:
        default = config.default_repo
    except Exception:
        default = None
    if default is not None and all(repo is not default for repo in found):
        found.append(default)
    return found


def project_entry(project: str | None, worktree_id: str, config: Any) -> Path | None:
    """An existing entry for ``worktree_id`` in ``project``'s worktree root.

    Lets a missing tracking record with a catalog entry (a recovery candidate)
    count as held rather than gone.
    """
    try:
        repos = getattr(config, "repos", None) or {}
        repo = repos.get(project) if project else None
        if repo is None and (not project or project == getattr(config, "repo_name", None)):
            repo = config.default_repo
        root = getattr(repo, "worktree_root", None) if repo is not None else None
        if not root:
            return None
        entry = entry_path(root, worktree_id)
    except Exception:
        return None
    return entry if entry.exists() else None


def _common_dir(cwd: str | os.PathLike[str]) -> str | None:
    from . import git_ops

    try:
        result = git_ops.git(
            "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=Path(cwd), check=False, timeout=30,
        )
    except Exception:
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None


def dehydrated_branch_holder(cwd: str | os.PathLike[str], branch: str, config: Any = None) -> str | None:
    """The dehydrated worktree ID whose retained ``branch`` lives in ``cwd``'s repository.

    Managed branch moves and deletions must refuse such a branch: it is the only
    durable copy of the dehydrated checkout's HEAD.
    """
    if config is None:
        try:
            from . import config as cfg

            config = cfg.load_config()
        except Exception:
            return None
    holders: list[tuple[str, str]] = []
    for repo in _repos(config):
        root = getattr(repo, "worktree_root", None)
        if not root:
            continue
        for path in iter_entries(root):
            try:
                data = read_entry(path)
            except CatalogError:
                continue
            if data is not None and data["git"]["branch"] == branch:
                anchor = data["identity"].get("anchor") or getattr(repo, "anchor", "")
                holders.append((data["identity"]["worktree_id"], str(anchor)))
    if not holders:
        return None
    common = _common_dir(cwd)
    for worktree_id, anchor in holders:
        theirs = _common_dir(anchor) if anchor else None
        if common is None or theirs is None or same_path(common, theirs):
            return worktree_id  # same repository, or unprovable: keep the branch
    return None
