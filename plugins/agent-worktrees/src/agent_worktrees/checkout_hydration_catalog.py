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

import base64
import binascii
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

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
    if not isinstance(data.get("config_restored", False), bool):
        raise CatalogError("dehydration catalog field config_restored is malformed")
    identity = data.get("identity")
    git = data.get("git")
    if not isinstance(identity, dict) or not isinstance(git, dict):
        raise CatalogError("dehydration catalog entry lacks identity or git sections")
    for section, keys in ((identity, ("worktree_id", "checkout_path", "repo")), (git, ("branch", "head"))):
        for key in keys:
            if not isinstance(section.get(key), str) or not section.get(key):
                raise CatalogError(f"dehydration catalog entry lacks {key}")
    _validate_snapshot(data, identity, git)
    return data


def _optional_str(section: dict[str, Any], key: str) -> None:
    if section.get(key) is not None and not isinstance(section[key], str):
        raise CatalogError(f"dehydration catalog field {key} is malformed")


def _validate_snapshot(data: dict[str, Any], identity: dict[str, Any], git: dict[str, Any]) -> None:
    """Shape-check every field a checkout mutation may consume."""
    for key in ("operation_id", "created_at", "dehydrated_at"):
        _optional_str(data, key)
    if not isinstance(data.get("prior_state", {}), dict):
        raise CatalogError("dehydration catalog prior_state is malformed")
    for key in ("anchor", "creation_nonce", "catalog_path"):
        _optional_str(identity, key)
    if not os.path.isabs(identity["checkout_path"]):
        raise CatalogError("dehydration catalog checkout_path is not absolute")
    head = git["head"]
    if len(head) not in (40, 64) or any(c not in "0123456789abcdef" for c in head):
        raise CatalogError("dehydration catalog head is not a full object id")
    branch = git["branch"]
    if branch.startswith("-") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in branch):
        raise CatalogError("dehydration catalog branch is not a valid branch name")
    config = git.get("worktree_config")
    if config is None:
        return
    if not isinstance(config, dict) or not isinstance(config.get("file_b64"), str):
        raise CatalogError("dehydration catalog worktree_config is malformed")
    try:
        base64.b64decode(config["file_b64"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CatalogError("dehydration catalog worktree_config payload is not base64") from exc
    entries = config.get("entries")
    if not isinstance(entries, list) or not all(
        isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and item[0]
        and (item[1] is None or isinstance(item[1], str))
        for item in entries
    ):
        raise CatalogError("dehydration catalog worktree_config entries are malformed")


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


def _private_open(path: Path) -> IO[str]:
    """Create ``path`` exclusively with owner-only permissions (0o600 on POSIX)."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")


def write_entry(path: Path, data: dict[str, Any]) -> None:
    """Atomically publish ``data`` (temp + fsync + replace + directory barrier)."""
    from . import checkout_durability

    validate(data)
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            path.parent.chmod(0o700)
        # The new directory entry itself must be durable before any removal.
        checkout_durability.directory(path.parent.parent)
    staging = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with _private_open(staging) as stream:
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


ROOTS_FILENAME = "dehydration-roots.json"
_RESERVATION_LOCK = "agent-worktrees-branch-reservation.yaml"  # the lock file is its ``.lock`` sibling


def _roots_file(repo_name: str) -> Path:
    from . import config as cfg

    return cfg.project_dir(repo_name) / ROOTS_FILENAME


def registered_roots(repo_name: str) -> list[Path]:
    """Every worktree root that ever held a catalog for ``repo_name``.

    The registry lives in the project's state home, independent of tracking
    records and the configured root, so a root change plus a lost ledger still
    finds the old catalog. Raises :class:`CatalogError` when it cannot be trusted.
    """
    path = _roots_file(repo_name)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise CatalogError(f"dehydration root registry is unreadable: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"dehydration root registry is corrupt: {exc}") from exc
    roots = data.get("roots") if isinstance(data, dict) else None
    if not isinstance(roots, list) or not all(isinstance(r, str) and os.path.isabs(r) for r in roots):
        raise CatalogError("dehydration root registry is malformed")
    return [Path(r) for r in roots]


def register_root(repo_name: str, worktree_root: str | os.PathLike[str]) -> None:
    """Durably add ``worktree_root`` to the registry (append-only, atomic)."""
    from . import checkout_durability, tracking

    path = _roots_file(repo_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tracking._RecordLock(path, timeout=30, require_sidecar=True):
        roots = registered_roots(repo_name)
        if any(same_path(root, worktree_root) for root in roots):
            return
        payload = {"schema": "agent-worktrees.dehydration-roots", "version": 1,
                   "roots": [str(r) for r in roots] + [os.path.abspath(str(worktree_root))]}
        staging = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with _private_open(staging) as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            checkout_durability.replace(staging, path, existing=True)
            checkout_durability.directory(path.parent)
        finally:
            if staging.exists():
                staging.unlink()


@contextmanager
def reservation_lock(cwd: str | os.PathLike[str]) -> Iterator[None]:
    """Serialize branch-reservation checks plus ref writes with dehydration.

    One cross-process lock per repository (in its common Git dir). Dehydration
    holds it while it publishes the catalog and removes the checkout; managed
    branch moves/deletions hold it across a fresh reservation check and the
    ref write, so neither can interleave with the other.
    """
    from . import tracking

    common = _common_dir(cwd)
    if common is None:
        raise CatalogError("cannot resolve the repository for its branch reservation lock")
    lock = tracking._RecordLock(Path(common) / _RESERVATION_LOCK, timeout=30, require_sidecar=True)
    try:
        lock.__enter__()
    except TimeoutError as exc:
        raise CatalogError(f"branch reservation lock is busy: {exc}") from exc
    from .git_repository_environment import repository_identity_scope

    try:
        # The guarded Git work must target the same repository the lock and the
        # reservation check resolved, never an inherited GIT_DIR-style selector.
        with repository_identity_scope():
            yield
    finally:
        lock.__exit__(None, None, None)


@dataclass(frozen=True)
class CatalogIndex:
    """One parse of a ``.dehydrated`` directory.

    ``claims`` maps each readable entry's normalized checkout path to the
    entry; ``unreadable`` lists entries that exist but cannot be trusted; and
    ``directory_unreadable`` means the directory itself could not be listed.
    """

    claims: dict[str, Path] = field(default_factory=dict)
    branches: tuple[tuple[str, str, str, str], ...] = ()  # (branch, worktree_id, anchor, checkout key)
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
    branches: list[tuple[str, str, str, str]] = []
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
        held = [data["git"]["branch"]]
        # Snapshot PR heads are local branches too; they hold the published commits.
        agency = data.get("agency")
        prs = agency.get("prs") if isinstance(agency, dict) else None
        for pr in prs if isinstance(prs, list) else []:
            name = pr.get("branch") if isinstance(pr, dict) else None
            if isinstance(name, str) and name and name not in held:
                held.append(name)
        for name in held:
            branches.append((
                name, identity["worktree_id"], str(identity.get("anchor") or ""),
                _key(identity["checkout_path"]),
            ))
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


def placement_matches(record: Any, config: Any) -> bool:
    """Prove a dehydrated checkout's original placement for receiving admission.

    A removed checkout cannot supply Git worktree proof, so namespace admission
    may instead accept the catalog entry beside the recorded checkout path. It
    must name the same worktree id, repository, branch, checkout path, creation
    nonce and receiving anchor as the record and as its on-disk receiving
    ledger. Any absence, mismatch or unreadable evidence fails closed.
    """
    from . import config as cfg, tracking

    try:
        worktree_id = record.worktree_id
        checkout = Path(record.worktree_path)
        repo = config.repos.get(record.repo)
        if (
            repo is None or not isinstance(worktree_id, str) or not worktree_id
            or Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id
            or not checkout.is_absolute() or checkout.name != worktree_id
            or os.path.lexists(checkout) or not record.branch
        ):
            return False
        ledger = cfg.project_dir(record.repo) / "worktrees" / f"{worktree_id}.yaml"
        if (
            record.yaml_path is None or ledger.is_symlink() or not ledger.is_file()
            or Path(record.yaml_path).resolve() != ledger.resolve()
        ):
            return False
        proven = tracking.load_record(ledger)
        fields = ("repo", "worktree_id", "worktree_path", "branch", "creation_nonce")
        if any(getattr(proven, name) != getattr(record, name) for name in fields):
            return False
        entry_file = entry_path(checkout.parent, worktree_id)
        if entry_file.is_symlink() or entry_file.parent.is_symlink():
            return False
        entry = read_entry(entry_file)
        if entry is None:
            return False
        identity, git = entry["identity"], entry["git"]
        return bool(
            identity.get("worktree_id") == worktree_id
            and identity.get("repo") == record.repo
            and git.get("branch") == record.branch
            and same_path(str(identity.get("checkout_path") or ""), checkout)
            and (identity.get("creation_nonce") or "") == (record.creation_nonce or "")
            and identity.get("anchor") and same_path(str(identity["anchor"]), str(repo.anchor))
        )
    except (AttributeError, KeyError, TypeError, OSError, ValueError, RuntimeError):
        return False


def _named_repos(config: Any) -> dict[str, Any]:
    """Every configured repository by project name, including the active default."""
    repos: dict[str, Any] = dict(getattr(config, "repos", None) or {})
    name = getattr(config, "repo_name", None)
    if isinstance(name, str) and name and name not in repos:
        try:
            repos[name] = config.default_repo
        except Exception:
            pass
    return repos


def _project_repo(project: str | None, config: Any) -> tuple[str, Any]:
    """``(project name, repo config)`` for ``project``, from that project's own config.

    The invoking config's ``repos`` may omit convention-adopted projects other
    than the active one, so fall back to loading the target project directly.
    Raises when the project cannot be resolved.
    """
    name = project or getattr(config, "repo_name", None)
    if not isinstance(name, str) or not name:
        raise CatalogError("no project to resolve")
    repo = _named_repos(config).get(name)
    if repo is None:
        from . import config as cfg

        repo = cfg.load_project_config(name, include_control_plane_related_pr=False).default_repo
    if repo is None or not getattr(repo, "worktree_root", None):
        raise CatalogError(f"project {name} has no worktree root")
    return name, repo


ENTRY_PRESENT = "present"
ENTRY_ABSENT = "absent"
ENTRY_UNKNOWN = "unknown"


def project_entry_state(project: str | None, worktree_id: str, config: Any) -> str:
    """Whether ``project`` holds a catalog entry for ``worktree_id``.

    Checks the target project's configured root and every registered root.
    Dehydration registers a root before it writes any entry there, so when the
    project's configuration cannot be resolved the registry alone is complete.
    ``unknown`` (never ``absent``) when the registry or a catalog directory
    cannot be read, so a recovery candidate whose ledger was lost is never
    declared gone on missing evidence.
    """
    try:
        name = project or getattr(config, "repo_name", None)
        if not isinstance(name, str) or not name:
            return ENTRY_UNKNOWN
        roots = registered_roots(name)
        try:
            roots.insert(0, Path(_project_repo(name, config)[1].worktree_root))
        except Exception:
            pass
        unknown = False
        for root in roots:
            entry = entry_path(root, worktree_id)
            if os.path.lexists(entry):
                return ENTRY_PRESENT
            index = catalog_index(root)
            if index.directory_unreadable:
                unknown = True
            if any(_key(e) == _key(entry) for e in index.unreadable):
                return ENTRY_PRESENT
        return ENTRY_UNKNOWN if unknown else ENTRY_ABSENT
    except Exception:
        return ENTRY_UNKNOWN


def _common_dir(cwd: str | os.PathLike[str]) -> str | None:
    from . import git_ops

    try:
        result = git_ops.git(
            "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=Path(cwd), check=False, timeout=30,
            isolated_repository=True,
        )
    except Exception:
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None


def _ledgers(config: Any) -> list[tuple[Any, dict[str, Any]]]:
    """``(repo, ledger data)`` for every tracking record of every configured repository."""
    import yaml

    from . import config as cfg

    found: list[tuple[Any, dict[str, Any]]] = []
    for repo_name, repo in _named_repos(config).items():
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


def _placements(
    config: Any, ledgers: list[tuple[Any, dict[str, Any]]],
) -> tuple[list[tuple[Path, str]], list[str]]:
    """Every catalog directory parent to consult, with its repository anchor.

    Today's configured roots, every registered root, and the parent of every
    recorded checkout, so an entry written before a worktree-root change is
    still found even without its ledger. Also returns the anchors of
    repositories whose root registry cannot be trusted.
    """
    seen: dict[str, tuple[Path, str]] = {}
    untrusted: list[str] = []
    for name, repo in _named_repos(config).items():
        anchor = str(getattr(repo, "anchor", "") or "")
        root = getattr(repo, "worktree_root", None)
        if root:
            seen.setdefault(_key(root), (Path(root), anchor))
        try:
            for registered in registered_roots(name):
                seen.setdefault(_key(registered), (registered, anchor))
        except CatalogError:
            untrusted.append(anchor)
    for repo, data in ledgers:
        path = data.get("worktree_path")
        if isinstance(path, str) and path:
            parent = Path(path).parent
            seen.setdefault(_key(parent), (parent, str(getattr(repo, "anchor", "") or "")))
    return list(seen.values()), untrusted


def dehydrated_branch_holder(cwd: str | os.PathLike[str], branch: str, config: Any = None) -> str | None:
    """The dehydrated worktree ID whose retained ``branch`` lives in ``cwd``'s repository.

    Managed branch moves and deletions must refuse such a branch: it is the only
    durable copy of the dehydrated checkout's HEAD. Both the catalog's branch
    and the branch its tracking ledger records are held when they disagree.
    Fails closed: an unreadable entry whose branch cannot be recovered from its
    tracking record, an unlistable catalog directory, or an untrusted root
    registry holds every branch of that repository until it is reconciled.
    Call it under :func:`reservation_lock` immediately before the ref write.
    """
    if config is None:
        try:
            from . import config as cfg

            config = cfg.load_config()
        except Exception as exc:
            raise CatalogError(f"cannot load configuration to check branch reservations: {exc}") from exc
    ledgers = _ledgers(config)
    by_stem = {str(data["_stem"]): data.get("branch") for _repo, data in ledgers}
    by_path = {
        _key(data["worktree_path"]): data.get("branch")
        for _repo, data in ledgers if isinstance(data.get("worktree_path"), str) and data.get("worktree_path")
    }
    placements, untrusted = _placements(config, ledgers)
    holders: list[tuple[str, str]] = [("(untrusted dehydration root registry)", anchor) for anchor in untrusted]
    for root, repo_anchor in placements:
        index = catalog_index(root, fresh=True)
        if index.directory_unreadable:
            holders.append((f"{catalog_dir(root)} (unreadable dehydration catalog)", repo_anchor))
        for held, worktree_id, anchor, checkout in index.branches:
            if branch in (held, by_stem.get(worktree_id), by_path.get(checkout)):
                holders.append((worktree_id, anchor or repo_anchor))
                if anchor and repo_anchor:
                    # A foreign anchor never releases the receiving placement's reservation.
                    holders.append((worktree_id, repo_anchor))
        for entry in index.unreadable:
            known = by_stem.get(entry.stem)
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
