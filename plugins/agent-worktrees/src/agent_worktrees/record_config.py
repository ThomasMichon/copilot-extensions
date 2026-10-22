"""Filesystem-only repository membership for exact record metadata reads."""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config as cfg, repos


def _mapping(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    value = yaml.safe_load(text)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a configuration mapping: {path}")
    return value


def metadata_project(explicit: str | None, *, cwd: Path | None = None) -> str | None:
    """Resolve the metadata project from an explicit name or Git marker files."""
    if explicit:
        return explicit
    from .front_door_cli import _reverse_lookup_project, _safe_cwd

    cwd = cwd if cwd is not None else _safe_cwd()
    if cwd is None:
        return None
    for root in (cwd, *cwd.parents):
        if (root / "HEAD").is_file() and (root / "objects").is_dir():
            registered = _reverse_lookup_project(root)
            if registered:
                return registered
        marker = root / ".git"
        if marker.is_dir():
            return _reverse_lookup_project(root)
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir: "):
                raise ValueError("Invalid Git worktree marker")
            git_dir = (root / text.removeprefix("gitdir: ")).resolve()
            common_file = git_dir / "commondir"
            try:
                common_text = common_file.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                return _reverse_lookup_project(root)
            if not common_text:
                raise ValueError("Empty Git common-directory marker")
            common = (git_dir / common_text).resolve()
            return _reverse_lookup_project(common) or _reverse_lookup_project(common.parent)
    return None


def configured_repository_paths(project: str) -> dict[str, Path]:
    """Read declared anchors without loading repository policy or Git."""
    from .config_dropins import read_operator_config_fragments

    root = cfg.project_dir(project)
    machine = cfg._deep_merge(
        read_operator_config_fragments(root / "config.d"),
        _mapping(root / "config.yaml"),
    )
    global_defaults = _mapping(cfg.global_config_path())
    platform = machine.get("platform") or global_defaults.get("platform") or cfg.detect_platform()
    if not isinstance(platform, str):
        raise ValueError("Configured platform must be a string")
    declared = machine.get("repos")
    if declared is None:
        declared = {}
    if not isinstance(declared, dict):
        raise ValueError("Project repositories must be a mapping")
    registry = repos.read_registry(strict=True)
    names = {project, *declared}
    configured = {}
    for name in names:
        entry = declared.get(name, {})
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ValueError("Project repository entries must be named mappings")
        anchor = entry.get("anchor")
        if anchor is not None and (not isinstance(anchor, str) or not anchor):
            raise ValueError(f"Invalid repository anchor for {name!r}")
        registered = registry.repos.get(name)
        path = anchor or (registered.local_path(platform) if registered is not None else None)
        if path:
            configured[name] = Path(path).resolve()
    if project not in configured:
        raise ValueError(f"No repository configured for active project {project!r}")
    return configured


def configured_repositories(project: str) -> frozenset[str]:
    """Read declared membership without loading repository policy or Git."""
    return frozenset(configured_repository_paths(project))
