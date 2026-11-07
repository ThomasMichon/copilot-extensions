"""Parse projects.yaml without losing original acquisition outcomes."""

from __future__ import annotations

from pathlib import Path

from dropin_registry import ScanAuthority

from . import config_acquisition as acquisition
from .acquisition_report import failed, observe


def load_projects_document(content: str) -> object:
    """Parse safe YAML while rejecting duplicate keys at every mapping level."""
    from .strict_yaml import load_strict_yaml_document

    try:
        value = load_strict_yaml_document(content, label="projects registry")
    except Exception as exc:
        failed(acquisition.current_observations(), "projects-registry-document", "projects-parser",
               "invalid-projects-document", type(exc).__name__)
        raise
    observe(acquisition.current_observations(), "projects-registry-document", "projects-parser", ScanAuthority.COMPLETE)
    return value


_ORIGINAL_DOCUMENT_LOADER = load_projects_document


def _covered(start: int | None, source: str, stage: str) -> bool:
    observations = acquisition.current_observations()
    return observations is None or (start is not None and any(
        o.source == source and o.stage == stage and o.authority is ScanAuthority.COMPLETE
        for o in observations[start:]
    ))


def _finished(path: Path, covered: bool) -> None:
    observations = acquisition.current_observations()
    if covered:
        observe(observations, str(path), "projects-registry", ScanAuthority.COMPLETE)
    else:
        failed(observations, str(path), "projects-registry", "owner-coverage-unavailable",
               "original projects parser or migration coverage is unavailable")


def parse_projects_registry(path: Path) -> dict:
    """Return the original projects mapping, retaining tolerant empty defaults.

    Read, parser and shape failures still raise. Reporting never replays their
    acquisition, and a negative legacy presence check cannot certify absence.
    """
    if not acquisition.source_exists(path, "projects-registry"):
        return {"projects": {}}
    from . import config_migrations

    observations = acquisition.current_observations()
    try:
        loader = load_projects_document
        content = path.read_text(encoding="utf-8")
        observe(observations, str(path), "projects-source-text", ScanAuthority.COMPLETE)
        start = acquisition.registry_call_started()
        data = loader(content)
        covered = loader is _ORIGINAL_DOCUMENT_LOADER and _covered(
            start, "projects-registry-document", "projects-parser",
        )
        if data is None:
            _finished(path, covered)
            return {"projects": {}}
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a mapping")
        # Lazy migration retains the original result, including fallback identity.
        start = acquisition.registry_call_started()
        migrate = config_migrations.migrate_loaded
        original_migrate = getattr(config_migrations, "_ORIGINAL_MIGRATE_LOADED", None)
        data = migrate(data, config_migrations.SCHEMA_PROJECTS)
        covered = covered and migrate is original_migrate and _covered(
            start, config_migrations.SCHEMA_PROJECTS, "config-migration",
        )
        if data.get("projects") is None:
            data["projects"] = {}
        elif not isinstance(data["projects"], dict):
            raise ValueError(f"{path}: 'projects' is not a mapping")
        _finished(path, covered)
        return data
    except Exception as exc:
        failed(observations, str(path), "projects-registry", "invalid-projects-source", type(exc).__name__)
        raise


_ORIGINAL_PROJECTS_PARSER = parse_projects_registry


def write_projects_document(path: Path, content: str, *, managed: bool) -> None:
    """Refresh prepared guard facts only for the selected project registry."""
    from .pr_authority import write_registry

    if managed:
        from .guarded_anchors import managed_write

        managed_write(lambda: write_registry(path, content))
    else:
        write_registry(path, content)


def read_projects_registry(path: Path, *, strict: bool = False) -> dict:
    """Read an already owner-resolved registry path with the normal fallback."""
    start = acquisition.registry_call_started()
    try:
        value = parse_projects_registry(path)
    except Exception as exc:
        acquisition.registry_call_failed(str(path), exc)
        if strict:
            raise
        return {"projects": {}}
    if parse_projects_registry is not _ORIGINAL_PROJECTS_PARSER:
        acquisition.opaque_source(str(path))
    acquisition.registry_call_finished(start, str(path), stage="projects-registry")
    return value


_ORIGINAL_PROJECTS_READER = read_projects_registry


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
