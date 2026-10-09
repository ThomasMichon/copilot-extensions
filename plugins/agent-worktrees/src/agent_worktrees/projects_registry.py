"""Parse the machine's project registry (``projects.yaml``), telling "no
projects" apart from "no project list"."""

from __future__ import annotations

from pathlib import Path


def parse_projects_registry(path: Path) -> dict:
    """The registry as a dict with a ``projects`` mapping.

    A missing or empty file, or one without ``projects``, has no projects:
    ``{"projects": {}}``. A file that can't be read, isn't YAML, or whose
    document or ``projects`` isn't a mapping raises: that is no project list
    at all, never an empty one.
    """
    if not path.exists():
        return {"projects": {}}
    import yaml

    from . import config_migrations

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
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
