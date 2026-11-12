"""Local observation and strict remote-target selection for resolve."""

from __future__ import annotations

from machine_transport import normalize_environment, resolve_machine_selection, split_machine_environment
from machine_transport.registry import machine_identity_names

from . import config as cfg, machine_identity
from .execution_spaces import current_execution_context, registry_for_config


def retained_target(
    config: cfg.Config, worktree_id: str | None, name: str | None,
    environment: str | None = None,
) -> tuple[cfg.Config, str] | None:
    from . import retained_checkout, session_tracking_cli, tracking
    from .execution_spaces import ExecutionSpaceError, project_record_config

    if not worktree_id or (
        environment and normalize_environment(environment) != cfg.detect_platform()
    ):
        return None
    try:
        path = (
            session_tracking_cli._find_tracking_file_exact(worktree_id)
            or session_tracking_cli._find_tracking_file(worktree_id)
        )
        if path is None:
            return None
        record = tracking.load_record(path)
        receiving = config if record.repo == config.repo_name else project_record_config(record)
        if not retained_checkout.is_local(record, receiving):
            return None
    except (OSError, ValueError, RuntimeError, cfg.yaml.YAMLError):
        return None
    target = receiving, record.worktree_id
    if not name:
        return target
    try:
        entries = registry_for_config(receiving)
    except ExecutionSpaceError:
        entries = None
    if entries is not None and any(
        name.casefold() in {
            value.casefold() for value in machine_identity_names(entry, include_metadata=False)
        } for entry in entries.values()
    ):
        resolve_machine_selection(entries, name, environment, allow_physical_context=True)
        return None
    observed = cfg.socket.gethostname()
    if name.casefold() in {observed.casefold(), observed.split(".")[0].casefold()}:
        return target
    if name.casefold() not in {
        value.casefold() for value in (
            record.machine, record.execution_machine, receiving.machine,
        ) if value
    }:
        return None
    if entries is None:
        return target
    # A retained alias is a fallback, not an override of a declared target.
    selected = resolve_machine_selection(entries, name, environment, allow_physical_context=True)
    return target if selected.entry is None else None


def retained_target_is_local(
    config: cfg.Config, worktree_id: str | None, name: str | None,
    environment: str | None = None,
) -> bool:
    return retained_target(config, worktree_id, name, environment) is not None


def target_is_local(
    config: cfg.Config, name: str | None, environment: str | None = None,
    *, worktree_id: str | None = None,
) -> bool:
    if retained_target_is_local(config, worktree_id, name, environment):
        return True
    if not name:
        return not environment or normalize_environment(environment) == cfg.detect_platform()
    if (
        not environment and config.machine and name.casefold() == config.machine.casefold()
        and name.casefold() not in {
            cfg.socket.gethostname().casefold(), cfg.socket.gethostname().split(".")[0].casefold(),
        }
        and machine_identity.is_local_machine(name, config)
    ):
        return True
    entries = registry_for_config(config)
    named_target = any(
        name.casefold() in {
            value.casefold() for value in machine_identity_names(entry, include_metadata=False)
        } for entry in entries.values()
    )
    if config.machine and not named_target:
        context = current_execution_context(config)
        if name.casefold() in {context.hostname.casefold(), context.execution_machine.casefold()}:
            return not environment or normalize_environment(environment) == context.environment
    selected = resolve_machine_selection(entries, name, environment, allow_physical_context=True)
    target = selected.target
    if entries:
        entry = selected.entry
        if entry is not None:
            if target.environment and target.environment != cfg.detect_platform():
                return False
            observed = cfg.socket.gethostname()
            if (
                named_target and name.casefold() in {
                    observed.casefold(), observed.split(".")[0].casefold(),
                }
                and entry.hostname and entry.hostname.casefold() not in {
                    observed.casefold(), observed.split(".")[0].casefold(),
                }
            ):
                return False
            if machine_identity.is_local_machine(entry.key, config):
                return True
            if entry.execution_platform:
                return False
            target = split_machine_environment(entry.key, target.environment)
    if target.environment and target.environment != cfg.detect_platform():
        return False
    if selected.entry is None:
        from machine_transport import is_observed_local_name

        if is_observed_local_name(
            name, config_machine=config.machine, entries=entries,
            environment=cfg.detect_platform(),
        ):
            return machine_identity.is_local_machine(config.machine, config)
    if target.environment and config.machine:
        current = resolve_machine_selection(entries, config.machine, cfg.detect_platform()).target
        if target.machine.casefold() == current.machine.casefold():
            return target.environment == current.environment
    if not target.environment and config.machine and name.casefold() == config.machine.casefold():
        return True
    return False
