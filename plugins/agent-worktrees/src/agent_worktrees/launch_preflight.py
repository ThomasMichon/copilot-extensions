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


def require_repository_hosting(path: str | Path) -> None:
    """Translate repository denial into the launch planner's existing refusal."""
    from repository_policy import AgentHostingError

    from . import agent_policy

    try:
        agent_policy.require(path)
    except AgentHostingError as exc:
        raise LaunchPreflightError(str(exc)) from exc
