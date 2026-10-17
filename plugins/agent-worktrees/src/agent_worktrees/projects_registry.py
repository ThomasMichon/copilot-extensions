"""Parse the machine's project registry (``projects.yaml``), telling "no
projects" apart from "no project list"."""

from __future__ import annotations

from pathlib import Path


def load_projects_document(content: str) -> object:
    """Parse safe YAML while rejecting duplicate keys at every mapping level."""
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("invalid mapping node in projects registry")
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError("duplicate mapping key in projects registry")
            result[key] = loader.construct_object(value_node)
        return result

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(content, Loader=StrictLoader)


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
