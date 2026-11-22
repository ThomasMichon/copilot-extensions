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
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class CatalogIndex:
    """One parse of a ``.dehydrated`` directory.

    ``claims`` maps each readable entry's normalized checkout path to the
    entry; ``unreadable`` lists entries that exist but cannot be trusted; and
    ``directory_unreadable`` means the directory itself could not be listed.
    """

    claims: dict[str, Path] = field(default_factory=dict)
    branches: tuple[tuple[str, str, str], ...] = ()  # (branch, worktree_id, anchor)
    unreadable: tuple[Path, ...] = ()
    directory_unreadable: bool = False


_EMPTY = CatalogIndex()
_Signature = tuple[tuple[str, int, int, int], ...]
_INDEX_CACHE: dict[str, tuple[_Signature, CatalogIndex]] = {}


def _key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _signature(directory: Path) -> _Signature:
    """Per-entry (name, size, mtime_ns, inode); raises when the directory is absent.

    Atomic replacement gives an entry a new inode, so any publication or
    retirement changes the signature even within one mtime tick.
    """
    items: list[tuple[str, int, int, int]] = []
    with os.scandir(directory) as listing:
        for item in listing:
            if item.name.endswith(".json") and not item.name.startswith("."):
                stat = item.stat(follow_symlinks=False)
                items.append((item.name, stat.st_size, stat.st_mtime_ns, item.inode()))
    return tuple(sorted(items))


def _build_index(directory: Path) -> CatalogIndex:
    claims: dict[str, Path] = {}
    branches: list[tuple[str, str, str]] = []
    unreadable: list[Path] = []
    for path in iter_entries(directory.parent):
        try:
            data = read_entry(path)
        except CatalogError:
            unreadable.append(path)
            continue
        if data is None:
            continue
        identity = data["identity"]
        claims[_key(identity["checkout_path"])] = path
        branches.append((data["git"]["branch"], identity["worktree_id"], str(identity.get("anchor") or "")))
    return CatalogIndex(claims, tuple(branches), tuple(unreadable))


def catalog_index(worktree_root: str | os.PathLike[str], *, fresh: bool = False) -> CatalogIndex:
    """The (cached) index of one root's catalog.

    Observation batches such as ``prune.assess`` over every inactive record
    reuse one parse per root; the cache is revalidated by a cheap directory
    signature on every call, and ``fresh=True`` (mutation boundaries) always
    re-reads every entry.
    """
    directory = catalog_dir(worktree_root)
    try:
        signature = _signature(directory)
    except (FileNotFoundError, NotADirectoryError):
        _INDEX_CACHE.pop(_key(directory), None)
        return _EMPTY
    except OSError:
        return CatalogIndex(directory_unreadable=True)
    if not signature:
        return _EMPTY
    cached = _INDEX_CACHE.get(_key(directory))
    if not fresh and cached is not None and cached[0] == signature:
        return cached[1]
    index = _build_index(directory)
    _INDEX_CACHE[_key(directory)] = (signature, index)
    return index


def is_dehydrated_checkout(checkout_path: str | os.PathLike[str]) -> bool:
    """Whether a catalog entry claims ``checkout_path``.

    Conservative: an entry named for the checkout that cannot be read (or an
    unlistable catalog directory) still counts, so an unreadable catalog never
    lets a consumer collect it.
    """
    path = Path(checkout_path)
    index = catalog_index(path.parent)
    if index.directory_unreadable or _key(path) in index.claims:
        return True
    named = _key(entry_for_checkout(path))
    return any(_key(entry) == named for entry in index.unreadable)


def entry_claiming(worktree_root: str | os.PathLike[str], checkout_path: str | os.PathLike[str]) -> Path | None:
    """The readable entry under ``worktree_root`` whose identity names ``checkout_path``."""
    return catalog_index(worktree_root).claims.get(_key(checkout_path))

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


def _ledgers(config: Any) -> list[tuple[Any, dict[str, Any]]]:
    """``(repo, ledger data)`` for every tracking record of every configured repository."""
    import yaml

    from . import config as cfg

    repos: dict[str, Any] = dict(getattr(config, "repos", None) or {})
    name = getattr(config, "repo_name", None)
    if isinstance(name, str) and name and name not in repos:
        try:
            repos[name] = config.default_repo
        except Exception:
            pass
    found: list[tuple[Any, dict[str, Any]]] = []
    for repo_name, repo in repos.items():
        try:
            ledgers = sorted(cfg.tracking_dir(repo_name).glob("*.yaml"))
        except Exception:
            continue
        for ledger in ledgers:
            try:
                # Only two top-level scalars are needed; skip parsing the whole record.
                lines = [
                    line for line in ledger.read_text(encoding="utf-8").splitlines()
                    if line.startswith(("branch:", "worktree_path:"))
                ]
                data = yaml.safe_load("\n".join(lines))
            except Exception:
                continue
            if isinstance(data, dict):
                found.append((repo, {**data, "_stem": ledger.stem}))
    return found


def _placements(config: Any, ledgers: list[tuple[Any, dict[str, Any]]]) -> list[tuple[Path, str]]:
    """Every catalog directory parent to consult, with its repository anchor.

    Today's configured roots plus the parent of every recorded checkout, so an
    entry written before a worktree-root change is still found.
    """
    seen: dict[str, tuple[Path, str]] = {}
    for repo in _repos(config):
        root = getattr(repo, "worktree_root", None)
        if root:
            seen.setdefault(_key(root), (Path(root), str(getattr(repo, "anchor", "") or "")))
    for repo, data in ledgers:
        path = data.get("worktree_path")
        if isinstance(path, str) and path:
            parent = Path(path).parent
            seen.setdefault(_key(parent), (parent, str(getattr(repo, "anchor", "") or "")))
    return list(seen.values())


def dehydrated_branch_holder(cwd: str | os.PathLike[str], branch: str, config: Any = None) -> str | None:
    """The dehydrated worktree ID whose retained ``branch`` lives in ``cwd``'s repository.

    Managed branch moves and deletions must refuse such a branch: it is the only
    durable copy of the dehydrated checkout's HEAD. Fails closed: an unreadable
    entry whose branch cannot be recovered from its tracking record, or an
    unlistable catalog directory, holds every branch of that repository until
    it is reconciled.
    """
    if config is None:
        try:
            from . import config as cfg

            config = cfg.load_config()
        except Exception:
            return None
    ledgers = _ledgers(config)
    ledger_branch = {str(data["_stem"]): data.get("branch") for _repo, data in ledgers}
    holders: list[tuple[str, str]] = []
    for root, repo_anchor in _placements(config, ledgers):
        index = catalog_index(root, fresh=True)
        if index.directory_unreadable:
            holders.append((f"{catalog_dir(root)} (unreadable dehydration catalog)", repo_anchor))
        for held, worktree_id, anchor in index.branches:
            if held == branch:
                holders.append((worktree_id, anchor or repo_anchor))
        for entry in index.unreadable:
            known = ledger_branch.get(entry.stem)
            if not isinstance(known, str) or not known or known == branch:
                holders.append((f"{entry.stem} (unreadable dehydration catalog)", repo_anchor))
    if not holders:
        return None
    common = _common_dir(cwd)
    for worktree_id, anchor in holders:
        theirs = _common_dir(anchor) if anchor else None
        if common is None or theirs is None or same_path(common, theirs):
            return worktree_id  # same repository, or unprovable: keep the branch
    return None