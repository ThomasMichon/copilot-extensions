"""Parse the machine's project registry (``projects.yaml``), telling "no
projects" apart from "no project list"."""

from __future__ import annotations

from pathlib import Path


def load_projects_document(content: str) -> object:
    """Parse safe YAML while rejecting duplicate keys at every mapping level."""
    from .strict_yaml import load_strict_yaml_document

    return load_strict_yaml_document(content, label="projects registry")


def parse_projects_registry(path: Path) -> dict:
    """The registry as a dict with a ``projects`` mapping.

    A missing or empty file, or one without ``projects``, has no projects:
    ``{"projects": {}}``. A file that can't be read, isn't YAML, or whose
    document or ``projects`` isn't a mapping raises. Duplicate mapping keys,
    including nested metadata, raise before migration or defaulting: that is
    no project list at all, never an empty one.
    """
    if not path.exists():
        return {"projects": {}}
    from . import config_migrations

    data = load_projects_document(path.read_text(encoding="utf-8"))
    if data is None:
        return {"projects": {}}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    # Lazy schema migration (in memory, never persists / never raises).
    data = config_migrations.migrate_loaded(data, config_migrations.SCHEMA_PROJECTS)
    if data.get("projects") is None:
        data["projects"] = {}
    elif not isinstance(data["projects"], dict):
        raise ValueError(f"{path}: 'projects' is not a mapping")
    return data


def read_projects_registry(path: Path, *, strict: bool = False) -> dict:
    """Read an already owner-resolved registry path with the normal fallback."""
    try:
        return parse_projects_registry(path)
    except Exception:
        if strict:
            raise
        return {"projects": {}}


def project_for_anchor(anchor: Path, *, read_projects, read_repos, platform, normalize):
    """Resolve registered membership without importing a command dispatcher."""
    target = normalize(str(anchor))
    try:
        projects = read_projects().get("projects", {})
    except Exception:
        projects = {}
    for name, entry in projects.items():
        path = entry.get("anchor") if isinstance(entry, dict) else None
        if path and normalize(str(Path(path))) == target:
            return name
    try:
        registry = read_repos()
        selected_platform = platform()
        for name in registry.repos:
            path = registry.repos[name].local_path(selected_platform)
            if path and normalize(str(Path(path))) == target:
                return name
    except Exception:
        pass
    return None
