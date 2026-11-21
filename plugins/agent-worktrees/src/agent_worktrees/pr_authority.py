"""Installation-scoped exclusion for destructive PR publication authority."""

from __future__ import annotations

import contextlib
import inspect
import os
import threading
from functools import wraps
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import Any, ParamSpec, Protocol, TypeVar, cast

import yaml

from . import config as cfg, pr_legacy_admission, pr_publication_state, pr_publish, publication_deadline, tracking
from . import record_machine_identity

_held = threading.local()
P = ParamSpec("P")
R = TypeVar("R")


class PublicationAuthorityBusy(TimeoutError):
    """Retryable contention, distinct from provider or record-lock failures."""

    def __init__(self) -> None:
        super().__init__(
            "Timed out waiting for PR rewrite authority (installation-wide publication ownership lock). "
            "Another operation may still hold it; this does not establish a stale lock or failed review. "
            "Agent action: wait briefly, then retry the same canonical command without asking the operator. "
            "For status/claim reconciliation, retry pr-status --json in the same worktree; "
            "do not repeat a push or merge merely to reconcile its result. "
            "If contention persists, retain the pending obligation and defer a retry; "
            "never delete the lock, terminate its owner, or bypass publication checks."
        )

    def status_result(self) -> dict[str, object]:
        return {
            "error": str(self),
            "error_code": "pr_authority_busy",
            "retryable": True,
            "retry_after_seconds": 2,
            "reconciliation_pending": True,
            "next_action": "Wait at least 2 seconds, then retry pr-status --json in the same worktree.",
        }


def publication(function: Callable[P, R]) -> Callable[P, R]:
    signature = inspect.signature(function)
    @wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        arguments = signature.bind(*args, **kwargs).arguments
        config = arguments["config"]
        prcfg = arguments.get("prcfg", config.default_repo.pr)
        with guard(timeout=publication_deadline.lock_wait(publication_deadline.configured(prcfg))):
            target = arguments.get("target_pr")
            if target is not None:
                from . import pr_creation_reconcile
                if pr_creation_reconcile.requires_qualification(target):
                    raise ValueError("A retained local retirement cannot be opened as a provider association.")
            return function(*args, **kwargs)
    return guarded


@contextlib.contextmanager
def publication_lifecycle(
    config: cfg.Config, *, record: tracking.WorktreeRecord | None = None,
    worktree_id: str | None = None, workspace: str | Path | None = None,
) -> Iterator[Callable[[], None]]:
    """Keep admitted publication inside the existing checkout retirement fence."""
    from . import source_path_admission

    with source_path_admission.guard(workspace):
        with _publication_lifecycle(
            config, record=record, worktree_id=worktree_id, workspace=workspace,
        ) as recheck:
            yield recheck


@contextlib.contextmanager
def _publication_lifecycle(
    config: cfg.Config, *, record: tracking.WorktreeRecord | None,
    worktree_id: str | None, workspace: str | Path | None,
) -> Iterator[Callable[[], None]]:
    from . import worktree_lifecycle

    path = record.yaml_path if record is not None else (
        cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml" if worktree_id else None
    )
    if path is None:
        yield lambda: None
        return
    try:
        path.stat()
    except FileNotFoundError:
        if record is not None:
            raise ValueError("The publication owner's ledger is missing.") from None
        yield lambda: None
        return
    root = Path(workspace).resolve() if workspace is not None else None
    def load(candidate: Path) -> tracking.WorktreeRecord:
        if candidate.is_symlink():
            raise ValueError("Source ownership contains an unsafe ledger path.")
        try:
            return tracking.load_record(candidate)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ValueError("Declared source ownership is unavailable.") from exc
    before = {path.resolve(): load(path)}
    routes = _ledgers(config.repo_name) if root is not None else {}
    ledgers = tuple(sorted(routes))
    def discover() -> dict[Path, tracking.WorktreeRecord]:
        from . import source_owner_lookup

        assert root is not None
        return {candidate: load(candidate) for candidate in source_owner_lookup.matching(ledgers, root)}
    if root is not None:
        before.update(discover())
    unowned_source = root is not None and not any(
        Path(item.worktree_path).resolve() == root for item in before.values()
    )
    declared_roots = _source_managed_roots(config, routes) if unowned_source else ()
    if root is not None and unowned_source and any(root.is_relative_to(base) for base in declared_roots):
        raise ValueError("Managed source checkout has no qualified native owner.")
    def identities(items: dict[Path, tracking.WorktreeRecord]) -> dict[Path, tuple[object, ...]]:
        from .receiving_allocation import serialized_receipt

        return {
            entry: (
                record_machine_identity.subject_fence(item, entry), item.creation_nonce,
                serialized_receipt(item),
            )
            for entry, item in items.items()
        }
    expected = identities(before)
    def recheck() -> None:
        fresh = {entry: load(entry) for entry in before}
        if identities(fresh) != expected:
            raise ValueError("Publication caller or source ownership changed.")
        from .execution_spaces import ExecutionSpaceError, project_record_config, require_record_mutation

        for entry, item in fresh.items():
            if entry == path.resolve() and (root is None or Path(item.worktree_path).resolve() != root):
                continue
            if item.repo != routes.get(entry.parent):
                raise ValueError("Source owner conflicts with its receiving project.")
            try:
                require_record_mutation(item, project_record_config(item), recover_receiving=False)
            except ExecutionSpaceError as exc:
                raise ValueError(f"Source receiving authority is unproven: {exc}") from exc
        if unowned_source and (
            _ledgers(config.repo_name) != routes
            or _source_managed_roots(config, routes) != declared_roots or discover()
        ):
            raise ValueError("Source owner routing changed; requalify the unmanaged source.")
        if any(item.status in ("finalizing", "finalized", "archived") for item in fresh.values()):
            raise ValueError("Publication caller or source is closing or finalized; reload its lifecycle.")
    with contextlib.ExitStack() as lifetime:
        for owner_path in sorted(before):
            lifetime.enter_context(worktree_lifecycle.guard(owner_path, reentrant=True))
        recheck()
        yield recheck


def registry_transaction(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        with guard():
            return function(*args, **kwargs)
    return guarded


class FinalizationFence(Protocol):
    def acquire(self) -> None: ...
    def release(self) -> None: ...


class PublicationLock:
    """Acquire finalization before authority and release in reverse order."""

    def __init__(self, finalization: FinalizationFence, record: tracking.WorktreeRecord,
                 pr: tracking.PRRecord, *, timeout: float | None) -> None:
        self.finalization = finalization
        self.record = record
        self.pr = pr
        self.timeout = timeout
        self.authority: contextlib.AbstractContextManager[None] | None = None

    def acquire(self) -> None:
        self.finalization.acquire()
        try:
            authority = guard(timeout=self.timeout)
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


def _source_managed_roots(config: cfg.Config, routes: dict[Path, str]) -> tuple[Path, ...]:
    roots: set[Path] = set()
    for project in sorted(set(routes.values())):
        selected = config if project == config.repo_name else cfg.load_project_config(project)
        for repo in selected.repos.values():
            path = Path(repo.worktree_root)
            if not repo.worktree_root or not path.is_absolute():
                raise ValueError("Native source routing has an unresolved worktree root.")
            roots.add(path.resolve())
    return tuple(sorted(roots))


@contextlib.contextmanager
def guard(*, timeout: float | None = None) -> Iterator[None]:
    """Exclude PR association writers across all projects in this installation."""
    path, seal = pr_legacy_admission.authority_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{os.getpid()}:{os.path.normcase(str(path.resolve()))}"
    paths = getattr(_held, "paths", None)
    if paths is None:
        paths = _held.paths = set()
    if key in paths:
        if seal is not None and pr_legacy_admission.read_seal() != seal:
            raise pr_legacy_admission.LegacyAdmissionError("Legacy authority changed during reentry.")
        yield
        return
    with path.open("a+b") as fh:
        try:
            pr_publish._lock_publish_file(
                fh, timeout=pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S if timeout is None else timeout,
            )
        except pr_publish.PublishLockTimeout as exc:
            raise PublicationAuthorityBusy() from exc
        paths.add(key)
        try:
            pr_legacy_admission.validate_authority(path, fh, seal)
            yield
        finally:
            paths.remove(key)
            pr_publish._unlock_publish_file(fh)


def _projection(record: tracking.WorktreeRecord | None) -> tuple[object, ...]:
    if record is None or not record.prs:
        return ()
    return (record.repo, record.worktree_id, record.worktree_path, record.branch, tuple(
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
    record_machine_identity.require_unchanged_identity(record, current)
    if _projection(current) == _projection(record):
        tracking._atomic_write(path, content)
    else:
        with guard(timeout=0):
            tracking._atomic_write(path, content)
    record_machine_identity.remember_loaded_subject(record, path)


def write_registry(path: Path, content: str) -> None:
    """Keep registration of pre-existing ledgers outside an admitted rewrite."""
    with guard():
        tracking._atomic_write(path, content)


class _StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _StrictLoader, node: yaml.nodes.MappingNode) -> dict[object, Any]:
    loader.flatten_mapping(node)
    construct = cast(Callable[[yaml.nodes.Node], object], loader.construct_object)
    result = {}
    for key_node, value_node in node.value:
        key = construct(key_node)
        if key in result:
            raise ValueError("Duplicate key in PR authority state.")
        result[key] = construct(value_node)
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _read(path: Path, *, context: str = "PR authority state") -> dict[str, Any]:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read {context}: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{context} must be a mapping: {path}")
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


def _live_prs(data: dict[str, Any], path: Path) -> list[dict[str, Any]]:
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


def assert_exclusive(
    config: cfg.Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    *, identity: str = "", recovery: bool = False, destination_identity: str | None = None,
) -> tuple[str, ...]:
    """Strictly scan every adopted ledger while the outer authority guard is held."""
    selected = (cfg.tracking_dir(config.repo_name) / f"{record.worktree_id}.yaml").resolve()
    waived: list[str] = []
    identity_expected = destination_identity or identity or pr.rewrite_identity or pr.head_identity
    if recovery and not identity_expected:
        raise ValueError("Recovery requires an observed, exact publication destination.")
    try:
        ledgers = _ledgers(config.repo_name)
    except ValueError as exc:
        if not recovery:
            raise
        waived.append(f"Historical project ownership registry unavailable: {exc}")
        ledgers = {}
    ledgers[selected.parent] = config.repo_name
    for ledger in sorted(ledgers):
        try:
            files = sorted(ledger.iterdir()) if _exists(ledger) else []
        except (OSError, ValueError) as exc:
            if not recovery or ledger == selected.parent:
                raise ValueError(f"Cannot enumerate PR authority ledger: {ledger}") from exc
            waived.append(f"Historical ownership ledger unavailable: {ledger}")
            continue
        for path in files:
            if path.suffix != ".yaml":
                continue
            try:
                data = _read(path)
                candidates = _live_prs(data, path)
            except ValueError as exc:
                if not recovery or path.resolve() == selected:
                    raise
                waived.append(f"Historical ownership entry unavailable: {exc}")
                continue
            for candidate in candidates:
                if candidate.get("branch") != pr.branch:
                    continue
                if path.resolve() == selected and candidate.get("pr_id", "") == pr.pr_id:
                    continue
                same_pr = (
                    candidate.get("number") == pr.number
                    and candidate.get("repo", "").lower() == pr.repo.lower()
                    and candidate.get("provider", pr.provider) in ("", pr.provider)
                )
                if recovery and same_pr:
                    waived.append(f"Historical same-PR ownership entry: {path}")
                    continue
                candidate_identity = candidate.get("rewrite_identity") or candidate.get("head_identity")
                if not candidate_identity:
                    cwd = data.get("worktree_path")
                    if not isinstance(cwd, str) or not Path(cwd).is_dir():
                        if not recovery:
                            raise ValueError("Another tracking record has an ambiguous PR destination; rewrite refused.")
                        waived.append(f"Historical PR destination unavailable: {path}")
                        continue
                    remote = candidate.get("remote")
                    if not remote:
                        project = ledgers[ledger]
                        candidate_config = config
                        if project != config.repo_name:
                            try:
                                _read(cfg.project_dir(project) / "config.yaml", context="project configuration")
                                candidate_config = cfg.load_project_config(
                                    project, include_control_plane_related_pr=False,
                                )
                            except (OSError, KeyError, TypeError, ValueError) as exc:
                                if not recovery:
                                    raise ValueError("Cannot resolve another project's PR destination; rewrite refused.") from exc
                                waived.append(f"Historical project destination unavailable: {path}")
                                continue
                        remote = candidate_config.default_repo.remote
                    candidate_identity = pr_publish.push_identity(remote, cwd=cwd)
                if not candidate_identity:
                    if not recovery:
                        raise ValueError("Another tracking record has an unreadable PR destination; rewrite refused.")
                    waived.append(f"Historical PR destination unreadable: {path}")
                    continue
                if candidate_identity == identity_expected:
                    raise ValueError("Another worktree tracks this PR head; shared history cannot be rewritten.")
    return tuple(sorted(set(waived)))
