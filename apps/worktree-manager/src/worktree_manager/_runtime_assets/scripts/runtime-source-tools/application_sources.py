"""Explicit application inputs to the existing first-party wheel profile."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from python_project_inventory import contained_path

_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_SELECTOR = re.compile(r"(?:standalone:)?[a-z0-9]+(?:-[a-z0-9]+)*")

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def project_locations(metadata: dict) -> dict[str, str]:
    """Read repository-owned application locations without discovering extra projects."""
    tool = metadata.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError("repository tool settings must be a table")
    settings = tool.get("copilot-extensions", {})
    if not isinstance(settings, dict):
        raise ValueError("application source settings must be a table")
    locations = settings.get("python-projects", {})
    if not isinstance(locations, dict):
        raise ValueError("Python project locations must be a table")
    result = {}
    for name, relative in locations.items():
        if (
            not isinstance(name, str) or not _SELECTOR.fullmatch(name)
            or name.startswith("standalone:")
        ):
            raise ValueError("Python project location requires an ordinary project name")
        if (
            not isinstance(relative, str) or not relative
            or "\\" in relative or ":" in relative or "\0" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part.lower() in {"", ".", "..", ".git"} for part in relative.split("/"))
        ):
            raise ValueError("Python project location must be a contained relative directory")
        if "libs" in {part.lower() for part in relative.split("/")}:
            raise ValueError("Python application project cannot be a vendored library")
        if relative in result.values():
            raise ValueError("Python project locations alias the same directory")
        result[name] = relative
    return result


def declared_projects(root: Path) -> dict[str, Path]:
    from standalone_python_artifacts import COMPONENTS

    manifest = root / "pyproject.toml"
    if not manifest.exists() and not manifest.is_symlink():
        return {}
    manifest = contained_path(manifest, root)
    locations = project_locations(tomllib.loads(manifest.read_text(encoding="utf-8")))
    builtin = set((root / "plugins").glob("*/pyproject.toml"))
    builtin_directories = {path.parent for path in builtin}
    builtin_directories.update(root / relative for relative in COMPONENTS.values())
    result = {}
    for name, relative in locations.items():
        directory = contained_path(root / relative, root)
        if (
            name in COMPONENTS or (root / "plugins" / name / "pyproject.toml").exists()
            or directory in builtin_directories or directory in result.values()
        ):
            raise ValueError("Python project location collides with a built-in project")
        result[name] = directory
    return result


def artifact_project(root: Path, selector: str) -> tuple[Path, dict]:
    directory = canonical_application(root, selector)
    identity = {"plugin": selector}
    if directory != root / "plugins" / selector:
        identity["project_path"] = directory.relative_to(root).as_posix()
    return directory, identity


def application_sources(metadata: dict) -> dict[str, str]:
    """Read explicit distribution-to-project associations without resolving inputs."""
    from packaging.utils import canonicalize_name

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
        target = declared_projects(root).get(selector, root / "plugins" / selector)
    return contained_path(target, root)


def is_application(root: Path, directory: Path) -> bool:
    """Exclude library/vendor consumers from application-profile source attribution."""
    from standalone_python_artifacts import COMPONENTS

    directory = contained_path(directory, root)
    relative = directory.relative_to(root)
    return (
        len(relative.parts) == 2 and relative.parts[0] == "plugins"
        and _SELECTOR.fullmatch(relative.parts[1]) is not None
    ) or relative in {Path(value) for value in COMPONENTS.values()} or directory in declared_projects(root).values()
