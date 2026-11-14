"""Read-only launch-preflight results shared by planning entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state_root import ConfigRoot


@dataclass(frozen=True)
class LaunchPreflight:
    """Read-only validation required before constructing a launch plan."""

    config_root: ConfigRoot | None = None
    policy_error: str | None = None
    checked_profile: str | None = None

    @property
    def error(self) -> str | None:
        if self.policy_error:
            return self.policy_error
        if self.config_root is not None and not self.config_root.path:
            return (
                self.config_root.error or "could not resolve the machine-local configuration root"
            )
        return None

    @property
    def config_root_path(self) -> str:
        return (self.config_root.path or "") if self.config_root else ""


class LaunchPreflightError(RuntimeError):
    """A launch plan cannot be built without mutating unsafe state."""


def new_knowledge_error(config, *, source: str) -> str | None:
    """Validate selected knowledge without requiring a binding for local New."""
    from .state_root import coordination_readiness

    readiness = coordination_readiness(config, cwd=source)
    if not readiness.ready and readiness.code != "knowledge_binding_required":
        return "New authoring requires knowledge readiness before allocation. " + readiness.error
    return None


def new_authoring_error(config, *, source: str) -> str | None:
    return new_authoring_preflight(config, source=source).error


def new_authoring_preflight(config, *, source: str) -> LaunchPreflight:
    """Keep configured-knowledge validation and checked staging input together."""
    from .application_admission import new_application_preflight

    if error := new_knowledge_error(config, source=source):
        return LaunchPreflight(policy_error=error)
    return new_application_preflight(config, source=source)


def require_repository_hosting(path: str | Path) -> None:
    """Translate repository denial into the launch planner's existing refusal."""
    from repository_policy import AgentHostingError

    from . import agent_policy

    try:
        agent_policy.require(path)
    except AgentHostingError as exc:
        raise LaunchPreflightError(str(exc)) from exc
