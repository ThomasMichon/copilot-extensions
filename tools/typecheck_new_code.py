"""Whole-file strict selection without admitting an unchanged legacy project."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from tools.typecheck_bootstrap_views import INPUTS, SELECTOR_INPUTS, require_bootstrap_version
from tools.typecheck_source_views import (
    HARNESS_PREFIX, INSTALLATION_ENTRY, SourceViewError, _reject_directives,
)

if TYPE_CHECKING:
    from tools.typecheck import Project


def git_environment() -> dict[str, str]:
    """Keep repository selectors from redirecting inventory or change comparison."""
    selectors = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in selectors and not key.upper().startswith("GIT_CONFIG")
    }
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    return env


def select_new_code(
    root: Path, owners: dict[str, Project], changes: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """Expand changed physical files to the existing complete program contracts."""
    touched = set(changes)
    selected: dict[str, tuple[str, ...]] = {}
    projects = {owner.name: owner for owner in owners.values()}
    invocation = bool(touched.intersection((*INPUTS, *SELECTOR_INPUTS))) or any(
        path.startswith("libs/payload-invocation/") and path.endswith((".tmpl", ".json"))
        for path in touched
    )
    harness = any(
        path.startswith(HARNESS_PREFIX) and path.endswith((".sh", ".ps1", ".json"))
        for path in touched
    )
    for name, needed in (
        ("lib-payload-invocation", invocation), ("copilot-extensions-maintainer", harness),
    ):
        if needed and name not in projects:
            raise SourceViewError(f"{name}: changed program inputs lack their physical owner")
    for name, project in projects.items():
        files = tuple(path for path, owner in owners.items() if owner.name == name)
        changed = tuple(path for path in files if path in touched)
        complete = False
        if name == "lib-payload-invocation":
            complete = bool(changed) or invocation
            if complete:
                require_bootstrap_version(
                    "strict", (project.python_version, *project.additional_python_versions),
                )
        elif name == "copilot-extensions-maintainer":
            complete = bool(changed) or harness
        if INSTALLATION_ENTRY in files and changed:
            complete = True
        if complete:
            changed = files
        selected_paths = set(changed)
        for entry in files:
            if not entry.endswith(("/installation_context.py", "/_installation_context.py")):
                continue
            prefix = entry.rsplit("/", 1)[0] + "/"
            members = {
                path for path in files if path == entry or (
                    path.startswith(prefix + "_installation_context_")
                    and "/" not in path[len(prefix):]
                    and not path.endswith("_installation_context_worker.py")
                )
            }
            if touched.intersection(members):
                selected_paths.update(members)
        changed = tuple(path for path in files if path in selected_paths)
        if changed:
            for path in changed:
                _reject_directives(path, root.joinpath(*path.split("/")).read_bytes())
            selected[project.name] = changed
    return selected
