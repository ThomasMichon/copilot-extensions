"""Installation-scoped exclusion for destructive PR publication authority."""

from __future__ import annotations

import contextlib
import inspect
import os
import threading
from functools import wraps
from pathlib import Path

import yaml

from . import config as cfg, pr_publication_state, pr_publish, publication_deadline, tracking

_held = threading.local()


def publication(function):
    signature = inspect.signature(function)
    @wraps(function)
    def guarded(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs).arguments
        config = arguments["config"]
        prcfg = arguments.get("prcfg", config.default_repo.pr)
        with guard(timeout=publication_deadline.lock_wait(publication_deadline.configured(prcfg))):
            return function(*args, **kwargs)
    return guarded


def registry_transaction(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with guard():
            return function(*args, **kwargs)
    return guarded


class PublicationLock:
    """Acquire finalization before authority and release in reverse order."""

    def __init__(self, finalization, record, pr, *, timeout):
        self.finalization = finalization
        self.record = record
        self.pr = pr
        self.timeout = timeout
        self.authority = None

    def acquire(self) -> None:
        self.finalization.acquire()
        authority = guard(timeout=self.timeout)
        try:
            authority.__enter__()
            self.authority = authority
            pr_publication_state.require_current(self.record, self.pr)
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        try:
            if self.authority is not None:
                authority, self.authority = self.authority, None
                authority.__exit__(None, None, None)
        finally:
            self.finalization.release()


@contextlib.contextmanager
def guard(*, timeout: float | None = None):
    """Exclude PR association writers across all projects in this installation."""
    path = cfg.install_dir() / "pr-rewrite-authority.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(path.resolve()))
    paths = getattr(_held, "paths", None)
    if paths is None:
        paths = _held.paths = set()
    if key in paths:
        yield
        return
    with path.open("a+b") as fh:
        try:
            pr_publish._lock_publish_file(
                fh, timeout=pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S if timeout is None else timeout,
            )
        except TimeoutError as exc:
            raise TimeoutError("Timed out waiting for PR rewrite authority; retry after the active publication.") from exc
        paths.add(key)
        try:
            yield
        finally:
            paths.remove(key)
            pr_publish._unlock_publish_file(fh)


def _projection(record: tracking.WorktreeRecord | None) -> tuple:
    if record is None or not record.prs:
        return ()
    return (record.repo, record.worktree_id, record.worktree_path, tuple(
        pr_publication_state.authority(p)
        for p in record.prs
    ))


def write_record(record: tracking.WorktreeRecord, path: Path, content: str) -> None:
    """Guard every association write, including direct locked tracking writers.

    A writer already holding a short record lock must never wait for the outer
    authority lock: a rewrite may need that record. Refuse immediately instead.
    Ordinary stamps with unchanged PR authority do not contend with network I/O.
    """
    current = tracking.load_record(path) if path.exists() else None
    if _projection(current) == _projection(record):
        tracking._atomic_write(path, content)
    else:
        with guard(timeout=0):
            tracking._atomic_write(path, content)


def write_registry(path: Path, content: str) -> None:
    """Keep registration of pre-existing ledgers outside an admitted rewrite."""
    with guard():
        tracking._atomic_write(path, content)


class _StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError("Duplicate key in PR authority state.")
        result[key] = loader.construct_object(value_node)
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _read(path: Path) -> dict:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read PR authority state: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"PR authority state must be a mapping: {path}")
    return data


def _exists(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError(f"Cannot inspect PR authority state: {path}") from exc
    return True


def _ledgers(project: str) -> dict[Path, str]:
    from . import installer

    path = installer.projects_yaml_path()
    names = {project}
    if _exists(path):
        data = _read(path)
        version = data.get("schema_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise ValueError("Unsupported projects registry schema; rewrite refused.")
        projects = data.get("projects")
        if not isinstance(projects, dict):
            raise ValueError("Malformed projects registry; rewrite refused.")
        for name, entry in projects.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ValueError("Malformed project registration; rewrite refused.")
            if not cfg._PROJECT_NAME_RE.fullmatch(name) or name in (".", ".."):
                raise ValueError("Invalid project name; rewrite refused.")
            names.add(name)
    return {cfg.tracking_dir(name).resolve(): name for name in names}


def _live_prs(data: dict, path: Path) -> list[dict]:
    if not isinstance(data.get("worktree_id"), str) or not data["worktree_id"]:
        raise ValueError(f"Missing worktree identity in PR authority state: {path}")
    if "pr" in data and data["pr"] is not None and not isinstance(data["pr"], dict):
        raise ValueError(f"Malformed legacy tracked PR: {path}")
    prs = data.get("prs", [data["pr"]] if data.get("pr") else [])
    if not isinstance(prs, list) or any(not isinstance(p, dict) for p in prs):
        raise ValueError(f"Malformed tracked PR list: {path}")
    live = []
    for pr in prs:
        for field in ("state", "branch", "repo", "remote", "head_identity", "rewrite_identity"):
            if field in pr and not isinstance(pr[field], str):
                raise ValueError(f"Malformed tracked PR {field}: {path}")
        state = pr.get("state", "")
        if state not in ("", "creating", "open", "active", "merged", "closed", "completed", "abandoned"):
            raise ValueError(f"Unknown tracked PR state: {path}")
        if state not in ("merged", "closed", "completed", "abandoned"):
            live.append(pr)
    return live


def assert_exclusive(config: cfg.Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord) -> None:
    """Strictly scan every adopted ledger while the outer authority guard is held."""
    selected = (cfg.tracking_dir(config.repo_name) / f"{record.worktree_id}.yaml").resolve()
    ledgers = _ledgers(config.repo_name)
    ledgers[selected.parent] = config.repo_name
    for ledger in sorted(ledgers):
        try:
            files = sorted(ledger.iterdir()) if _exists(ledger) else []
        except OSError as exc:
            raise ValueError(f"Cannot enumerate PR authority ledger: {ledger}") from exc
        for path in files:
            if path.suffix != ".yaml":
                continue
            data = _read(path)
            for candidate in _live_prs(data, path):
                if candidate.get("branch") != pr.branch:
                    continue
                if path.resolve() == selected and candidate.get("pr_id", "") == pr.pr_id:
                    continue
                identity = candidate.get("rewrite_identity") or candidate.get("head_identity")
                if not identity:
                    cwd = data.get("worktree_path")
                    if not isinstance(cwd, str) or not Path(cwd).is_dir():
                        raise ValueError("Another tracking record has an ambiguous PR destination; rewrite refused.")
                    remote = candidate.get("remote")
                    if not remote:
                        project = ledgers[ledger]
                        candidate_config = config
                        if project != config.repo_name:
                            _read(cfg.project_dir(project) / "config.yaml")
                            try:
                                candidate_config = cfg.load_project_config(
                                    project, include_control_plane_related_pr=False,
                                )
                            except (OSError, KeyError, TypeError, ValueError) as exc:
                                raise ValueError("Cannot resolve another project's PR destination; rewrite refused.") from exc
                        remote = candidate_config.default_repo.remote
                    identity = pr_publish.push_identity(remote, cwd=cwd)
                if not identity:
                    raise ValueError("Another tracking record has an unreadable PR destination; rewrite refused.")
                if identity == pr.rewrite_identity:
                    raise ValueError("Another worktree tracks this PR head; shared history cannot be rewritten.")
