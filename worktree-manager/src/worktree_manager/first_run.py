"""Project-independent observations for the standalone Manager front door."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from agent_procutil.owned_process import OwnedCleanupError

from . import harness_state
from .core_install import core_status
from .engine_client import EngineCleanupError, engine_available


@dataclass(frozen=True)
class SetupState:
    engine_available: bool
    projects: tuple[str, ...] | None
    summary: str


def observe() -> SetupState:
    lines = []
    try:
        lines.append(f"Agent-worktrees core: {core_status().state}")
        available = engine_available()
        lines.append(f"Engine command: {'available' if available else 'unavailable'}")
    except (EngineCleanupError, OwnedCleanupError):
        raise
    except Exception as error:
        available = False
        lines.append(f"Installation observation unavailable: {error}")
    try:
        projects = harness_state.build_project_ownership()
        repos = harness_state.repos_registry(strict=True).get("repos", {})
        names = tuple(
            project.name for project in projects
            if (repos.get(project.name) or {}).get("class") != "knowledge"
        )
        lines.append(
            f"Launchable projects reported: {len(names)}" if names else
            "No launchable projects were reported. Adopt a repository through its setup owner."
        )
    except Exception as error:
        names = None
        lines.append(f"Project registry unavailable: {error}\nCorrect the registry and Reload.")
    lines.append("Observation only; no installation, adoption or repair has run.")
    return SetupState(available, names, "\n".join(lines))


def diagnostic_argv(action: str) -> list[str]:
    if action == "setup":
        return [sys.executable, "-B", "-m", "worktree_manager", "setup"]
    if action == "doctor":
        from .installation_updates import configuration_pivot

        diagnostic = next(
            (item for item in configuration_pivot().actions if item.key == "doctor"), None,
        )
        if diagnostic is None or not diagnostic.run:
            raise ValueError("The Manager diagnostic command is unavailable.")
        return [part.replace("{id}", "worktree-manager") for part in diagnostic.run]
    raise ValueError("Unsupported Manager diagnostic")
