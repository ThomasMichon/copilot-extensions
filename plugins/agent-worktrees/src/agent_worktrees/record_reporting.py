"""Operation-scoped reporter naming, separate from durable tracking data."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from machine_transport import LocalExecutionContext, MachineEntry, resolve_local_execution_context

from . import config as cfg
from .record_config import RepositoryUnconfigured, configured_registry_anchor, configured_repository_paths

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


@dataclass(frozen=True)
class UnavailableReportingContext(LocalExecutionContext):
    unavailable: bool = True


def current_context(project: str) -> LocalExecutionContext:
    """Resolve the selected reporter without a full Config, Git, or CLI spawn."""
    entries: dict[str, MachineEntry]
    try:
        anchor = configured_registry_anchor(project, configured_repository_paths(project)[project])
    except RepositoryUnconfigured:
        entries = {}
    else:
        entries = cfg.load_machines_yaml(anchor, require_alias=False) if any(
            (anchor / name).is_file() for name in (".agent-worktrees/machines.yaml", "machines.yaml")
        ) else {}
    return resolve_local_execution_context(
        entries, hostname=cfg.socket.gethostname(), environment=cfg.detect_platform(),
    )


def local_context(path: Path, repository: str) -> LocalExecutionContext:
    """Use a receiving project's configuration, never a record's naming fields."""
    candidates = [repository]
    try:
        candidates.insert(0, cfg.project_name())
    except RuntimeError:
        pass
    for project in dict.fromkeys(candidates):
        if not isinstance(project, str) or not cfg._PROJECT_NAME_RE.fullmatch(project):
            continue
        if path.parent.resolve() == (cfg.project_dir(project) / "worktrees").resolve():
            return current_context(project)
    if isinstance(repository, str) and cfg._PROJECT_NAME_RE.fullmatch(repository):
        return current_context(repository)
    return resolve_local_execution_context(
        {}, hostname=cfg.socket.gethostname(), environment=cfg.detect_platform(),
    )


def apply_context(record: WorktreeRecord, context: LocalExecutionContext) -> None:
    """Attach reporting facts only after a local or remote owner supplied them."""
    from .worktree_machine_identity import WorktreeMachineIdentity

    record.machine = context.machine
    record.platform = context.environment
    record.machine_identity = WorktreeMachineIdentity(
        context.machine, context.environment, context.execution_machine,
    )
    record._reporting_context = context


def same_reporter(left: WorktreeRecord, right: WorktreeRecord) -> bool:
    """Different project customizations may describe the same observed execution."""
    contexts = [getattr(record, "_reporting_context", None) for record in (left, right)]
    if all(context is not None for context in contexts):
        first, second = contexts
        return (
            not any(getattr(context, "unavailable", False) for context in contexts)
            and first.hostname.casefold() == second.hostname.casefold()
            and first.environment == second.environment
        )
    return left.execution_machine == right.execution_machine and left.platform == right.platform


def load_local(
    path: Path, *, copy_result: bool = True, reporting_context: LocalExecutionContext | None = None,
) -> WorktreeRecord:
    """Keep naming outside the durable-data cache, including its no-copy path."""
    from . import record_cache, record_machine_identity, tracking
    from .pr_publication_state import loaded

    record = record_cache.cached_load(path, tracking._load_record_uncached, copy_result=copy_result)
    if not copy_result:
        record = copy.copy(record)
    try:
        context = reporting_context or local_context(path, record.repo)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        observed = resolve_local_execution_context(
            {}, hostname=cfg.socket.gethostname(), environment=cfg.detect_platform(),
        )
        context = UnavailableReportingContext(**vars(observed))
    apply_context(record, context)
    if copy_result:
        record = loaded(record)
        record_machine_identity.remember_loaded_subject(record, path)
    return record


def refresh_for_save(record: WorktreeRecord, path: Path, current: WorktreeRecord | None) -> None:
    context = current._reporting_context if current is not None else None
    apply_context(record, context or local_context(path, record.repo))


def require_receiving_subject(record: WorktreeRecord, config: cfg.Config) -> None:
    """Reporter naming cannot substitute for receiving-ledger/checkout proof."""
    from . import execution_spaces, record_cache, record_machine_identity, tracking
    from .legacy_wsl_record_locality import record_namespace_matches, resolve_legacy_wsl_owner_ref

    error = execution_spaces.ExecutionSpaceError
    if record.repo != config.repo_name:
        raise error("record does not belong to the receiving project")
    if (
        not isinstance(record.worktree_id, str)
        or Path(record.worktree_id).name != record.worktree_id
        or "/" in record.worktree_id or "\\" in record.worktree_id
    ):
        raise error("record has no exact receiving ledger identity")
    ledger = cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
    if (
        record.yaml_path is None or record.yaml_path.resolve() != ledger.resolve()
        or ledger.is_symlink() or not ledger.is_file()
    ):
        raise error("record snapshot does not match its receiving ledger")
    proven = record_cache.cached_load(ledger, tracking._load_record_uncached, copy_result=False)
    if (
        record_machine_identity.subject_fence(record, ledger)
        != record_machine_identity.subject_fence(proven, ledger)
        or record.creation_nonce != proven.creation_nonce
    ):
        raise error("record snapshot identity does not match its proven receiving subject")
    if config.platform != cfg.detect_platform():
        raise error("receiving configuration belongs to a different execution platform")
    if record.checkout_managed and not record_namespace_matches(record, config):
        raise error("record conflicts with the receiving worktree namespace")
    if record.owner_ref:
        owner = tracking.parse_claim_ref(record.owner_ref)
        if owner is None or not owner.is_qualified:
            raise error("owned record has an unqualified owner reference")
        if not record.receiving_allocation_present:
            try:
                local_parent = execution_spaces.require_owner_identity(owner.machine, config)
            except error:
                local_parent = False
            if not local_parent:
                from .retained_checkout import resolve_owner

                local_parent = resolve_owner(owner, config, child=record) is not None
            if not local_parent and resolve_legacy_wsl_owner_ref(owner, config) is None:
                raise error("cross-space parent ownership remains unsupported; settlement is blocked")
