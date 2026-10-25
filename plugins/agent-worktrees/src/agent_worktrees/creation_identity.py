"""Prospective record identities derived from the current local context."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from . import config as cfg, execution_spaces
from .worktree_machine_identity import WorktreeMachineIdentity

IdentityMode = Literal["legacy", "tuple-v1"]


def validate_mode(mode: IdentityMode) -> None:
    if mode not in ("legacy", "tuple-v1"):
        raise ValueError(f"Unsupported creation identity mode: {mode!r}")


def new_machine_identity(
    config: cfg.Config, mode: IdentityMode,
) -> WorktreeMachineIdentity | None:
    """Resolve only new tuple allocations; legacy producers keep their behavior."""
    validate_mode(mode)
    if mode == "legacy":
        return None
    if config.platform != cfg.detect_platform():
        raise execution_spaces.ExecutionSpaceError(
            "tuple creation platform must match the current execution platform",
        )
    context = execution_spaces.current_execution_context(config)
    return WorktreeMachineIdentity(
        context.machine, context.environment, context.execution_machine,
    )


def require_same_machine_identity(
    config: cfg.Config, identity: WorktreeMachineIdentity | None,
) -> None:
    if identity is not None and new_machine_identity(config, "tuple-v1") != identity:
        raise execution_spaces.ExecutionSpaceError("creation machine identity changed before allocation")


def new_tracking_identity(
    mode: IdentityMode, *, repo: str, machine: str, platform_name: str,
    tracking_path: Path, anchor_path: str | None = None, config: cfg.Config | None = None,
) -> WorktreeMachineIdentity | None:
    """Derive a new ledger identity only inside the winning if-absent boundary."""
    validate_mode(mode)
    if mode == "legacy":
        return None
    if config is None:
        config = cfg.load_config()
    if (
        repo != config.repo_name or machine != config.machine or platform_name != config.platform
        or tracking_path.resolve() != cfg.tracking_dir(config.repo_name).resolve()
        or (
            anchor_path is not None
            and Path(anchor_path).resolve() != Path(config.default_repo.anchor).resolve()
        )
    ):
        raise execution_spaces.ExecutionSpaceError(
            "new tracking identity conflicts with the receiving project namespace",
        )
    return new_machine_identity(config, mode)
