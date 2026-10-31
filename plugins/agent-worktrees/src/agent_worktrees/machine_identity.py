"""Canonicalized "is this the local machine?" check.

The same physical machine can be spelled differently in different contexts:
a claim/codename recorded under a raw COMPUTERNAME before the
``hostname:``-field decoupling landed vs. the current canonical alias, or a
caller passing a ``machines.yaml`` registry key where another caller passes
its alias. A bare string comparison (``name == config.machine``) then
silently misses that the target IS this machine, and dispatches an
unnecessary (and sometimes self-looping, over a devtunnel/SSH mesh) network
call. Every machine-targeted SSH dispatch path should resolve "is this
local?" through :func:`is_local_machine` instead.

This is a thin, agent-worktrees-flavored wrapper over the shared
``machine_transport.is_local_machine`` (vendored, not duplicated -- see its
own module docstring for the full canonicalization algorithm): it supplies
this plugin's own ``Config``/``load_machines_yaml`` shapes so every existing
call site keeps its original ``(name, config)`` signature, and passes
``guest=True`` on a WSL guest so a guest execution identity can never
silently resolve local against its own native-host registry entry.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from machine_transport import (
    MachineEntry, MachineEnvironment, MachineSelection,
    machine_name, resolve_local_execution_context,
)
from machine_transport import resolve_machine_selection, split_machine_environment
from machine_transport import is_local_machine as _mt_is_local_machine
from machine_transport.registry import machine_identity_names

from . import config as cfg


def configured_machine_binding(
    machine_raw: dict, global_raw: dict, capture: dict[str, str] | None = None,
) -> Any:
    """Select the existing configured tier and optionally capture its provenance."""
    value = machine_raw.get("machine") or global_raw.get("machine")
    if capture is not None:
        if "machine" in machine_raw:
            capture["source"], explicit = "project-machine-config", machine_raw["machine"]
        elif "machine" in global_raw:
            capture["source"], explicit = "global-machine-config", global_raw["machine"]
        else:
            capture["source"], explicit = "detected", None
        if capture["source"] != "detected" and (
            not isinstance(explicit, str) or not explicit.strip()
        ):
            raise ValueError("configured machine binding must be a nonempty string")
    return value


def resolve_execution_machine(
    name: str, *, guest: bool, configured: bool,
    load_entries: Callable[[], dict[str, MachineEntry]] | None,
    preserve_native_hostname: bool = False,
) -> str:
    """Resolve local identity without inferring registered-space authority."""
    entries: dict[str, MachineEntry] = {}
    if load_entries is not None:
        try:
            entries = load_entries()
        except FileNotFoundError:
            pass
    context = resolve_local_execution_context(
        entries, hostname=cfg.socket.gethostname() if configured else name,
        environment="wsl" if guest else cfg.detect_platform(),
        configured_machine=name if configured else None,
    )
    selected = entries.get(context.customization_key) if context.customization_key else None
    if not guest:
        # Config.machine is a retained legacy binding, not the new compact ID.
        if preserve_native_hostname and not configured and (
            selected is None or not selected.execution_platform
        ):
            return name.lower()
        if selected is not None and not selected.execution_platform:
            if configured:
                literal = name.strip().casefold() == cfg.socket.gethostname().strip().casefold()
                return name if literal or not split_machine_environment(name).environment else selected.key
            return machine_name(selected)
        if selected is None:
            if configured:
                literal = name.strip().casefold() == cfg.socket.gethostname().strip().casefold()
                return name if literal or not split_machine_environment(name).environment else context.execution_machine
            return context.execution_machine
    if guest and selected is None and any(
        entry.key.strip().casefold() == context.machine.casefold()
        for entry in entries.values()
    ):
        print(
            f"warning: no guest entry for {context.machine!r}; using {context.execution_machine!r}",
            file=sys.stderr,
        )
    return context.execution_machine


def load_execution_registry(
    repo_dir: str | Path, *, config: cfg.Config | None = None,
) -> dict[str, MachineEntry]:
    """Prefer checkout-local registry data, then its proven owning anchor."""
    try:
        return (
            cfg.load_machines_yaml(repo_dir, config=config)
            if config is not None else cfg.load_machines_yaml(repo_dir)
        )
    except FileNotFoundError:
        if config is None:
            raise
        anchor = Path(config.default_repo.anchor)
        checkout = Path(repo_dir)
        if checkout.resolve() == anchor.resolve():
            raise
        from . import front_door_cli

        owning_anchor = front_door_cli._git_toplevel(checkout)
        if owning_anchor is None or owning_anchor.resolve() != anchor.resolve():
            raise
        return cfg.load_machines_yaml(anchor, config=config)


def resolve_execution_context(
    name: str, repo_dir: str | Path, *, environment: str,
    config: cfg.Config | None = None,
) -> MachineSelection[MachineEntry]:
    """Project a registered execution ID into its physical/environment tuple.

    This is metadata selection, not execution-space mutation authority. Unknown
    names retain their exact spelling; suffixes alone cannot admit another host.
    """
    try:
        entries = load_execution_registry(repo_dir, config=config)
    except FileNotFoundError:
        entries = {}
    selection = resolve_machine_selection(entries, name, environment)
    if selection.entry is None:
        return MachineSelection(None, MachineEnvironment(name, environment), selection.exact)
    return selection


def runtime_policy_physical_machine(
    machine: str, repo_dir: str | Path, *, config: cfg.Config | None = None,
) -> str:
    """Keep current observations literal; retain strict non-local selection."""
    try:
        entries = load_execution_registry(repo_dir, config=config)
    except FileNotFoundError:
        entries = {}
    binding = config.configured_machine if config is not None else None
    context = resolve_local_execution_context(
        entries, hostname=cfg.socket.gethostname(), environment=cfg.detect_platform(),
        configured_machine=binding,
    )
    local_names = {context.hostname.casefold(), context.execution_machine.casefold()}
    selected = entries.get(context.customization_key) if context.customization_key else None
    if selected is not None:
        local_names.update((selected.key.strip().casefold(), machine_name(selected).casefold()))
    if binding is not None:
        local_names.add(binding.strip().casefold())
        local_names.add(config.machine.casefold())
    requested = machine.strip().casefold()
    registered_target = any(
        requested in {
            name.strip().casefold() for name in machine_identity_names(entry, include_metadata=False)
        } for entry in entries.values()
    )
    if requested == context.execution_machine.casefold():
        return context.machine
    if selected is not None and requested in {
        name.strip().casefold() for name in machine_identity_names(selected, include_metadata=False)
    }:
        # Reuse observation-aware alias precedence and profile conflict checks.
        return resolve_local_execution_context(
            entries, hostname=context.hostname, environment=context.environment,
            configured_machine=machine,
        ).machine
    if requested in local_names and not registered_target:
        return context.machine
    target = resolve_machine_selection(entries, machine, cfg.detect_platform())
    return target.target.machine if target.entry is not None else machine


def repository_config(repo_dir: Path) -> cfg.Config | None:
    """Load target configuration without changing or depending on ambient project."""
    from . import front_door_cli

    anchor = front_door_cli._git_toplevel(repo_dir)
    project = front_door_cli._reverse_lookup_project(anchor or repo_dir)
    if project is not None:
        return cfg.load_config(
            cfg.project_dir(project) / "config.yaml", project=project,
            include_control_plane_related_pr=False,
        )
    return None


def is_local_machine(name: str, config: cfg.Config) -> bool:
    """True when ``name`` (a machine key, alias, ``hostname`` field, or
    display_name) refers to the CURRENT machine.

    Canonicalizes both sides through the registry (the same key/alias/
    hostname/display_name matching ``config.detect_machine`` itself uses)
    and, as a final ground-truth check, the real local OS hostname, before
    deciding. This read-only classification does not grant mutation authority;
    owned writes must enforce the separate ``execution_spaces`` fences.
    """
    return _mt_is_local_machine(
        name,
        config_machine=config.machine,
        load_entries=lambda: cfg.load_machines_yaml(config.default_repo.anchor),
        guest=cfg.detect_platform() == "wsl",
    )
