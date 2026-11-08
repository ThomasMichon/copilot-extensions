"""Record-scoped locality for historical machine aliases.

The shared registry owns label/format mapping. This adapter grants locality
only with an exact project/worktree record, actual execution platform and named
receiving-project namespace. The stored reference and record labels must select
that same logical entry; physical association alone never joins independent
execution spaces. Legacy projection provenance can license a historical bare
host label on a WSL record, but an explicitly foreign entry cannot borrow it.

The old WSL-named entry points remain compatibility wrappers for existing
readers. No stored machine, config or reference string is rewritten.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from machine_transport import (
    MachineEntry, MachineEnvironment, MachineSelection,
    resolve_machine_selection, is_observed_local_name, resolve_local_execution_context,
)
from machine_transport.registry import MissingExecutionSpaceError, PhysicalMachineError

from . import config as cfg
from .execution_spaces import ExecutionSpaceError

if TYPE_CHECKING:
    from .tracking import WorktreeRecord
    from .tracking_claims import ClaimRef

_LOG = logging.getLogger(__name__)
_RESOLUTION_ERRORS = (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, ExecutionSpaceError)


def record_namespace_matches(record: "WorktreeRecord", config: cfg.Config) -> bool:
    """Check checkout identity against the named receiving repository."""
    from .tracking_claims import ANCHOR_ID

    repo = config.repos.get(record.repo)
    if repo is None:
        return False
    checkout = Path(record.worktree_path)
    anchor = record.worktree_id == ANCHOR_ID
    root = Path(repo.anchor if anchor else repo.worktree_root).expanduser()
    if not root.is_absolute() or not checkout.is_absolute():
        return False
    expected = root if anchor else root / record.worktree_id
    resolved_expected = root.resolve() if anchor else root.resolve() / record.worktree_id
    return (
        not checkout.is_symlink()
        and (not checkout.exists() or checkout.is_dir())
        and checkout == expected and checkout.resolve() == resolved_expected
    )


def _local_record_selection(
    config: cfg.Config, entries: dict[str, MachineEntry],
) -> tuple[MachineSelection[MachineEntry], bool]:
    """Keep actual observed/derived bindings local only within the record proof."""
    actual = cfg.detect_platform()
    if getattr(config, "platform", actual) != actual:
        return resolve_machine_selection(entries, config.machine, actual), False
    from .execution_spaces import _current_execution_context

    context = _current_execution_context(config, entries)
    observed = (
        context.customization_key is None
        and config.machine.casefold() in {
            context.hostname.casefold(), context.execution_machine.casefold(),
        }
        and context == resolve_local_execution_context(
            entries, hostname=context.hostname, environment=actual,
            configured_machine=getattr(config, "configured_machine", None),
        )
    )
    if observed:
        return MachineSelection(
            None, MachineEnvironment(context.machine, context.environment), False,
        ), True
    return resolve_machine_selection(entries, config.machine, context.environment), False


def resolve_legacy_wsl_owner_ref(ref: "ClaimRef", config: cfg.Config) -> Path | None:
    """Resolve a historical alias ref with actual-platform and namespace proof.

    Expected configuration and record errors are logged and fail closed.
    The legacy function name is retained for existing callers. Windows, WSL and
    Linux records must match the actual execution and selected receiving entry;
    this never makes a physical-host label a global guest-local identity.
    """
    from . import tracking

    if ref is None or not ref.is_qualified:
        return None
    actual = cfg.detect_platform()
    if actual not in ("windows", "wsl", "linux"):
        return None
    from .execution_spaces import registry_for_config

    try:
        caller_entries = registry_for_config(config)
        current, caller_observed = _local_record_selection(config, caller_entries)
        observed_ref = (
            (caller_observed and ref.machine.casefold() == config.machine.casefold())
            or is_observed_local_name(
                ref.machine, config_machine=config.machine, entries=caller_entries, environment=actual,
            )
        )
        # Exact foreign logical entries are rejected before any ledger lookup.
        try:
            direct = resolve_machine_selection(caller_entries, ref.machine)
        except PhysicalMachineError:
            direct = None
        except MissingExecutionSpaceError:
            if not observed_ref:
                raise
            direct = None
        if direct is not None and direct.entry is not None and direct.exact:
            declared = direct.entry.execution_platform
            if declared and declared != actual and not direct.entry.legacy_key:
                return None
            if (
                declared and not direct.entry.legacy_key and current.entry is not None
                and direct.entry.key.casefold() != current.entry.key.casefold()
            ):
                return None
        if (
            any(entry.execution_platform and not entry.legacy_key for entry in caller_entries.values())
            and not observed_ref
            and (direct is None or direct.entry is None)
            and (direct is None or not direct.target.environment)
            and (
                any(ref.machine.casefold() == entry.physical_host.casefold()
                    for entry in caller_entries.values())
                or not any(
                    ref.machine.casefold() in {entry.hostname.casefold(), entry.display_name.casefold()}
                    for entry in caller_entries.values()
                )
            )
        ):
            return None
    except _RESOLUTION_ERRORS as exc:
        _LOG.warning("Cannot establish record alias execution locality: %s", exc)
        return None
    path = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.resolve().parent != path.parent.resolve():
            return None
        record = tracking.load_record(path)
        if record.worktree_id != ref.worktree_id or record.repo != ref.project:
            return None
        from .execution_spaces import project_record_config, require_owner_identity, require_record_mutation

        receiving_config = project_record_config(record)
        if record.machine_identity is not None:
            require_record_mutation(record, receiving_config)
            if not require_owner_identity(ref.machine, receiving_config):
                return None
        receiver_entries = registry_for_config(receiving_config)
        receiving, receiver_observed = _local_record_selection(receiving_config, receiver_entries)
        caller_key = current.entry.key if current.entry is not None else current.target.logical_machine
        receiving_key = receiving.entry.key if receiving.entry is not None else receiving.target.logical_machine
        if caller_key.casefold() != receiving_key.casefold():
            return None
        if (
            receiving.target != current.target
            and (receiving.target.machine.casefold(), receiving.target.environment)
            != (current.target.machine.casefold(), current.target.environment)
        ):
            return None
        if record.platform != actual:
            return None
        if observed_ref and (
            (receiver_observed and ref.machine.casefold() == receiving_config.machine.casefold())
            or is_observed_local_name(
                ref.machine, config_machine=receiving_config.machine,
                entries=receiver_entries, environment=record.platform,
            )
        ):
            requested = receiving
        else:
            requested = resolve_machine_selection(receiver_entries, ref.machine, record.platform)
        if (
            (
                receiver_observed
                and (record.execution_machine or "").casefold() == receiving_config.machine.casefold()
            )
            or is_observed_local_name(
                record.execution_machine or "", config_machine=receiving_config.machine,
                entries=receiver_entries, environment=record.platform,
            )
        ):
            stored = receiving
        else:
            stored = resolve_machine_selection(receiver_entries, record.execution_machine or "", record.platform)
        if any(
            (
                selected.entry.key.casefold() != receiving.entry.key.casefold()
                if selected.entry is not None and receiving.entry is not None
                else (selected.target.machine.casefold(), selected.target.environment)
                != (receiving.target.machine.casefold(), receiving.target.environment)
            )
            for selected in (requested, stored)
        ):
            return None
        if not record_namespace_matches(record, receiving_config):
            return None
    except _RESOLUTION_ERRORS as exc:
        _LOG.warning("Cannot establish legacy WSL record locality: %s", exc)
        return None
    # The record's own ``machine`` field is the trusted ground truth for
    # "which legacy label it was actually written under" -- but it may name
    # yet another topology label (hostname/alias/key) than the ref does, so
    # resolve it through the same topology check, not a literal string match.
    return path


def legacy_wsl_owner_ref_is_local(ref: "ClaimRef", config: cfg.Config | None = None) -> bool:
    """Use the actual caller's config; the core separately loads receiving authority."""
    if ref is None or not ref.is_qualified:
        return False
    try:
        if config is None:
            config = cfg.load_config(include_control_plane_related_pr=False)
    except _RESOLUTION_ERRORS as exc:
        _LOG.warning("Cannot load legacy WSL ownership configuration: %s", exc)
        return False
    return resolve_legacy_wsl_owner_ref(ref, config) is not None


def claim_refs_equivalent(
    left: "ClaimRef", right: "ClaimRef", *, config: cfg.Config | None = None,
) -> bool:
    """True when two qualified owner-refs name the SAME local worktree
    record, directly (identical machine/project/worktree_id) or via one
    side's record-scoped legacy WSL spelling for the other's live identity.

    Never raises; loads the referenced project's config (fail-closed to ``False`` if
    unavailable). Used where an offer/accept flow must recognize a bundle
    actor recorded under a pre-split legacy label as the same worktree the
    current, newly-qualified actor now names.
    """
    if left is None or right is None:
        return False
    if (left.machine, left.project, left.worktree_id) == (
        right.machine, right.project, right.worktree_id,
    ):
        return True
    if left.project != right.project or left.worktree_id != right.worktree_id:
        return False
    try:
        if config is None or config.repo_name != left.project:
            config = cfg.load_project_config(left.project, include_control_plane_related_pr=False)
    except _RESOLUTION_ERRORS as exc:
        _LOG.warning("Cannot establish legacy WSL claim equivalence: %s", exc)
        return False

    def _resolve(ref: "ClaimRef") -> Path | None:
        if ref.machine == config.machine:
            return cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
        return resolve_legacy_wsl_owner_ref(ref, config)

    left_path, right_path = _resolve(left), _resolve(right)
    return left_path is not None and left_path == right_path
