"""Machine-gate decision for ``update``: never provision a runtime the launch-time
reconcile would refuse on this machine."""

from __future__ import annotations

from pathlib import Path

from . import config as cfg

MACHINE_GATED_SKIP = "SKIPPED (not allowed on this machine)"


class MachineDetectionError(Exception):
    """The machine gate can't be decided: the enabling repository or the
    machine identity could not be resolved."""


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

    repo_dir = context
    if repo_dir is None:
        # Resolve the active project's anchor directly: falling back to the
        # current directory would let an unrelated checkout with no gate
        # manifest authorize a machine-gated runtime.
        try:
            repo_dir = Path(cfg.load_config().default_repo.anchor)
        except Exception as error:
            raise MachineDetectionError(
                f"could not resolve the repository that enables {name}: {error}"
            ) from error
    try:
        machine = cfg.detect_machine(repo_dir)
    except Exception as error:
        # Never fall back to a bare hostname: detection raises on purpose when
        # an explicit execution space is required or the machine registry is
        # invalid, and launch-time reconcile refuses in that case too.
        raise MachineDetectionError(str(error)) from error
    gate = reconcile.load_runtime_gate(repo_dir)
    gate_present = reconcile.gate_manifest_present(repo_dir)
    return not reconcile.runtime_allowed(
        scope, name, machine, gate, gate_present=gate_present
    )
