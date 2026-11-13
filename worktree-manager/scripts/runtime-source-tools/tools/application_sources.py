"""Explicit application inputs to the existing first-party wheel profile."""
from __future__ import annotations

from pathlib import Path
import re

from packaging.utils import canonicalize_name

from python_project_inventory import contained_path

_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_SELECTOR = re.compile(r"(?:standalone:)?[a-z0-9]+(?:-[a-z0-9]+)*")


def application_sources(metadata: dict) -> dict[str, str]:
    """Read explicit distribution-to-project associations without resolving inputs."""
    settings = metadata.get("tool", {}).get("copilot-extensions", {})
    if not isinstance(settings, dict):
        raise ValueError("application source settings must be a table")
    sources = settings.get("application-sources", {})
    if not isinstance(sources, dict):
        raise ValueError("application sources must be a table")
    result = {}
    for name, selector in sources.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("application source has an invalid distribution name")
        name = canonicalize_name(name)
        if name in result:
            raise ValueError("application source distribution names collide")
        if not isinstance(selector, str) or not _SELECTOR.fullmatch(selector):
            raise ValueError("application source must name one canonical project")
        result[name] = selector
    return result


def canonical_application(root: Path, selector: str) -> Path:
    """Resolve a declared application through the existing canonical project roster."""
    from standalone_python_artifacts import COMPONENTS

    if not isinstance(selector, str) or not _SELECTOR.fullmatch(selector):
        raise ValueError("application source must name one canonical project")
    if selector.startswith("standalone:"):
        relative = COMPONENTS.get(selector.removeprefix("standalone:"))
        if relative is None:
            raise ValueError("application source names an unregistered standalone")
        target = root / relative
    else:
        target = root / "plugins" / selector
    return contained_path(target, root)


def is_application(root: Path, directory: Path) -> bool:
    """Exclude library/vendor consumers from application-profile source attribution."""
    from standalone_python_artifacts import COMPONENTS

    directory = contained_path(directory, root)
    relative = directory.relative_to(root)
    return (
        len(relative.parts) == 2 and relative.parts[0] == "plugins"
        and _SELECTOR.fullmatch(relative.parts[1]) is not None
    ) or relative in {Path(value) for value in COMPONENTS.values()}
