"""Invocation context for the Manager-owned production Picker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_project: str | None = None
_bootstrap: "ProjectBootstrap | None" = None


@dataclass(frozen=True)
class ProjectBootstrap:
    """Authoritative project bootstrap decisions resolved by the engine."""

    project: str
    should_switch_cwd: bool
    cwd: str | None
    default_live: bool

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProjectBootstrap":
        project = str(payload.get("project") or "").strip()
        if not project:
            raise ValueError("bootstrap payload must include a project")
        should_switch = bool(payload.get("should_switch_cwd"))
        raw_cwd = payload.get("cwd")
        cwd = str(raw_cwd).strip() if raw_cwd is not None else None
        if cwd == "":
            cwd = None
        if should_switch and not cwd:
            raise ValueError("bootstrap payload requires cwd when should_switch_cwd is true")
        return cls(
            project=project,
            should_switch_cwd=should_switch,
            cwd=cwd,
            default_live=bool(payload.get("default_live")),
        )


def set_project(project: str) -> None:
    """Bind the explicit project named by the Manager invocation."""
    global _project, _bootstrap
    value = project.strip()
    if not value:
        raise ValueError("project must not be empty")
    _project = value
    _bootstrap = None


def bind_project_bootstrap(payload: Mapping[str, Any]) -> ProjectBootstrap:
    """Bind the authoritative engine bootstrap for downstream consumers."""
    global _project, _bootstrap
    binding = ProjectBootstrap.from_payload(payload)
    _project = binding.project
    _bootstrap = binding
    return binding


def project_bootstrap() -> ProjectBootstrap | None:
    """Return the bound bootstrap record, when one has been recorded."""
    return _bootstrap


def reset() -> None:
    """Clear the bound project and bootstrap state (test helper)."""
    global _project, _bootstrap
    _project = None
    _bootstrap = None


def project() -> str:
    """Return the bound project or fail before invoking a provider."""
    if _bootstrap is not None:
        return _bootstrap.project
    if _project is None:
        raise RuntimeError("the production Picker project is not bound")
    return _project
