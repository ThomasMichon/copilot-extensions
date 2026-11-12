"""Local observation and strict remote-target selection for resolve."""

from __future__ import annotations

from machine_transport import normalize_environment, resolve_machine_selection, split_machine_environment
from machine_transport.registry import machine_identity_names

from . import config as cfg, machine_identity
from .execution_spaces import current_execution_context, registry_for_config


def retained_target_is_local(
    config: cfg.Config, worktree_id: str | None, name: str | None,
    environment: str | None = None,
) -> bool:
    from . import retained_checkout, tracking, worktree_identity

    if not worktree_id or not name or (
        environment and normalize_environment(environment) != cfg.detect_platform()
    ):
        return False
    identity = worktree_identity._resolve_worktree_id(worktree_id)
    record = tracking.load_record_by_id(identity)
    return bool(
        record is not None
        and name.casefold() in {
            value.casefold() for value in (
                record.machine, record.execution_machine, config.machine, cfg.socket.gethostname(),
            ) if value
        }
        and retained_checkout.is_local(record, config)
    )


def target_is_local(
    config: cfg.Config, name: str | None, environment: str | None = None,
) -> bool:
    if not name:
        return not environment or normalize_environment(environment) == cfg.detect_platform()
    if (
        not environment and config.machine and name.casefold() == config.machine.casefold()
        and name.casefold() != cfg.socket.gethostname().casefold()
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
