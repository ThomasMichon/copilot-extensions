"""Invocation context for the Manager-owned production Picker."""

from __future__ import annotations

_project: str | None = None


def set_project(project: str) -> None:
    """Bind the explicit project named by the Manager invocation."""
    global _project
    value = project.strip()
    if not value:
        raise ValueError("project must not be empty")
    _project = value


def project() -> str:
    """Return the bound project or fail before invoking a provider."""
    if _project is None:
        raise RuntimeError("the production Picker project is not bound")
    return _project
