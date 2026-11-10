"""Explicit trusted admission wiring for the ordinary coordinator entrypoint."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .assignment_workspace import AssignmentWorkspace


@dataclass(frozen=True)
class StandingAdmission:
    """Host-owned policy and the selected materialization owner's read command."""

    machine: str
    overrides_path: Path
    materialization_command: tuple[str, ...]
    resolve_materialization: Callable[[str], Path]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.machine, str) or not self.machine.strip()
            or self.machine != self.machine.strip() or "/" in self.machine
            or not isinstance(self.overrides_path, Path) or not self.overrides_path.is_absolute()
            or not isinstance(self.materialization_command, tuple) or not self.materialization_command
            or any(not isinstance(arg, str) or not arg.strip() for arg in self.materialization_command)
            or not Path(self.materialization_command[0]).is_absolute()
            or not callable(self.resolve_materialization)
        ):
            raise ValueError("standing admission requires its host, absolute policy and selected owner")


@dataclass(frozen=True)
class RelationshipAdmission:
    """Independent participant/workspace and original-request admission owners."""

    admit_workspace: Callable[[AssignmentWorkspace, dict], None]
    admit_request: Callable[[AssignmentWorkspace, dict, dict], None]
    standing: StandingAdmission | None = None

    def __post_init__(self) -> None:
        if not callable(self.admit_workspace) or not callable(self.admit_request):
            raise ValueError("relationship admission requires both independent admission owners")
        if self.standing is not None and not isinstance(self.standing, StandingAdmission):
            raise ValueError("standing admission must be supplied by the configured owner")


def configured_writer(spec: str | None, authority: str | None):
    """Fail closed for an explicit selection; absence never selects a writer."""
    if spec is None and authority is None:
        return None
    if (
        not isinstance(spec, str) or not spec.strip()
        or authority not in {"local", "central"}
        or spec.count(":") != 1
    ):
        raise ValueError("relationship admission requires module:factory and local or central authority")
    module, factory_name = spec.split(":")
    if (
        not module or not all(part.isidentifier() for part in module.split("."))
        or not factory_name.isidentifier()
    ):
        raise ValueError("relationship admission requires an explicit module:factory")
    factory = getattr(importlib.import_module(module), factory_name)
    if not callable(factory):
        raise ValueError("relationship admission factory is not callable")
    admission = factory()
    if not isinstance(admission, RelationshipAdmission):
        raise ValueError("relationship admission factory did not return independent admission owners")
    from .assignment_relationship_owner import WorktreeRelationshipWriter

    return WorktreeRelationshipWriter.selected(
        admit_workspace=admission.admit_workspace,
        admit_request=admission.admit_request,
        standing=admission.standing,
    )
