"""Shared physical-machine runtime policy and update configuration boundary."""

from __future__ import annotations

from pathlib import Path

from . import config as cfg

MACHINE_GATED_SKIP = "SKIPPED (not allowed on this machine)"


class MachineDetectionError(Exception):
    """The machine gate can't be decided: the enabling repository or the
    machine identity could not be resolved."""


def runtime_allowed(
    scope: str, name: str, machine: str, gate: dict[str, set[str]], *,
    gate_present: bool = True, repo_dir: Path | None = None,
    config: cfg.Config | None = None,
) -> bool:
    """Apply deployment policy without granting execution-space authority.

    Universal runtimes always reconcile. Machine-gated runtimes use a present
    manifest's explicit physical or execution selectors. With no manifest,
    enabling the plugin permits its local runtime; an omitted entry in a
    present manifest still refuses.
    """
    if scope == "universal":
        return True
    if scope == "machine-gated":
        allowed = gate.get(name)
        if allowed is not None:
            physical_machine = machine
            if repo_dir is not None:
                from .machine_identity import runtime_policy_physical_machine

                physical_machine = runtime_policy_physical_machine(machine, repo_dir, config=config)
            return machine in allowed or physical_machine in allowed
        return not gate_present
    return False


def _machine_gate_blocks(name: str, scope: str, context: Path | None) -> bool:
    """Whether the machine gate forbids reconciling *name*'s runtime here.

    Mirrors the session-start reconcile (``reconcile.build_plan``): only a
    ``machine-gated`` runtime is subject to the gate, the gate manifest and the
    machine identity are resolved from the repository that enables the plugin
    (falling back to the active project's anchor), and the decision itself is
    ``reconcile.runtime_allowed`` -- so ``update`` never provisions a runtime the
    launch-time reconcile would refuse on this machine.
    """
    if scope != "machine-gated":
        return False
    from . import reconcile
    from .machine_identity import repository_config

    try:
        config = repository_config(context) if context is not None else None
        if config is None:
            config = cfg.load_config()
    except Exception as error:
        raise MachineDetectionError(
            f"could not resolve the repository and machine configuration for {name}: {error}"
        ) from error
    repo_dir = context
    if repo_dir is None:
        # Resolve the active project's anchor directly: falling back to the
        # current directory would let an unrelated checkout with no gate
        # manifest authorize a machine-gated runtime.
        try:
            repo_dir = Path(config.default_repo.anchor)
        except Exception as error:
            raise MachineDetectionError(
                f"could not resolve the repository that enables {name}: {error}"
            ) from error
    try:
        machine = cfg.detect_machine(repo_dir, value=config.configured_machine, config=config)
    except Exception as error:
        # Optional topology is not enrollment. Invalid or conflicting local
        # customization must still refuse, just as launch-time reconcile does.
        raise MachineDetectionError(f"could not detect machine identity for {name}: {error}") from error
    gate = reconcile.load_runtime_gate(repo_dir)
    gate_present = reconcile.gate_manifest_present(repo_dir)
    try:
        return not reconcile.runtime_allowed(
            scope, name, machine, gate, gate_present=gate_present, repo_dir=repo_dir,
            config=config,
        )
    except (ValueError, OSError) as error:
        raise MachineDetectionError(
            f"could not resolve physical machine identity for {name}: {error}"
        ) from error
