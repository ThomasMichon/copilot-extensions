"""Explicit trusted admission wiring for the ordinary coordinator entrypoint."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass

from .assignment_workspace import AssignmentWorkspace


@dataclass(frozen=True)
class RelationshipAdmission:
    """Independent participant/workspace and original-request admission owners."""

    admit_workspace: Callable[[AssignmentWorkspace, dict], None]
    admit_request: Callable[[AssignmentWorkspace, dict, dict], None]

    def __post_init__(self) -> None:
        if not callable(self.admit_workspace) or not callable(self.admit_request):
            raise ValueError("relationship admission requires both independent admission owners")


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
    )
