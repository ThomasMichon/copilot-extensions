"""Strict read-only local tracking scan, separate from legacy best-effort views."""

from __future__ import annotations

import stat
from pathlib import Path

import yaml

from . import config as cfg, installer
from .producer_views import ObservedClaim, issue, ownership_snapshot, qualified_owner
from .projects_registry import load_projects_document as _load_strict_yaml

_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, yaml.YAMLError)


def _stamp(path: Path, *, directory: bool = False) -> tuple[int, ...]:
    value = path.lstat()
    if getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError("reparse-point tracking input is unavailable")
    if not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)):
        raise ValueError("expected a real directory" if directory else "expected a regular non-symlink file")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _read(path: Path) -> tuple[object, tuple[int, ...]]:
    before = _stamp(path)
    data = _load_strict_yaml(path.read_text(encoding="utf-8"))
    if _stamp(path) != before:
        raise ValueError("file changed while reading")
    return data, before


def _component(value: object) -> bool:
    return (
        isinstance(value, str) and value not in {"", ".", ".."}
        and not any(char in "/\\#\x7f" or ord(char) < 32 for char in value)
    )


def _record(data: object, path: Path, project: str, issues: list[dict[str, str]]) -> list[ObservedClaim]:
    if not isinstance(data, dict):
        raise ValueError("record is not a mapping")
    if (
        data.get("worktree_id") != path.stem or data.get("repo") != project
        or not _component(data.get("machine")) or not _component(path.stem)
        or not isinstance(data.get("branch"), str)
    ):
        raise ValueError("record identity does not match its registered project/path")
    owner = f"{data['machine']}/{project}/{path.stem}"
    if not qualified_owner(owner):
        raise ValueError("record owner is not canonical")
    resources = data.get("resources", [])
    if not isinstance(resources, list):
        raise ValueError("record resources is not a list")
    rows: list[ObservedClaim] = []
    refs: set[str] = set()
    for raw in resources:
        if (
            not isinstance(raw, dict) or not isinstance(raw.get("ref"), str)
            or not raw["ref"] or not isinstance(raw.get("kind", "worktree"), str)
        ):
            issues.append(issue("invalid-claim", owner, "malformed resource entry"))
            continue
        ref = raw["ref"]
        if ref in refs:
            issues.append(issue("duplicate-ref", owner, "duplicate resource reference"))
        refs.add(ref)
        if "producer" not in raw:
            continue
        if (
            not raw.get("kind") or not isinstance(raw.get("state", "active"), str)
            or not isinstance(raw.get("handoff_bundle", ""), str)
        ):
            issues.append(issue("invalid-claim", owner, "malformed producer claim fields"))
            continue
        rows.append(ObservedClaim(
            owner, raw["kind"], ref, raw.get("state", "active"),
            raw.get("handoff_bundle", ""), raw["producer"],
        ))
    return rows


def scan_ownership(
    *, owner: str | None = None, kind: str | None = None,
    namespace: str | None = None, identity: dict | None = None,
) -> dict:
    """Read registered tracking files without callbacks, locks, caches or writes."""
    rows: list[ObservedClaim] = []
    issues: list[dict[str, str]] = []
    stamps: list[tuple[Path, tuple[int, ...], bool]] = []
    try:
        registry = installer.projects_yaml_path()
        document, stamp = _read(registry)
        stamps.append((registry, stamp, False))
        if not isinstance(document, dict) or not isinstance(document.get("projects"), dict):
            raise ValueError("registry requires an explicit projects mapping")
        projects = document["projects"]
    except _READ_ERRORS as exc:
        return ownership_snapshot(
            [], [issue("registry-unavailable", "projects", str(exc))],
            owner=owner, kind=kind, namespace=namespace, identity=identity,
        )
    for project in sorted(projects, key=str):
        if not _component(project) or not isinstance(projects[project], dict):
            issues.append(issue("invalid-project", str(project), "invalid project name or registry entry"))
            continue
        try:
            root = cfg.project_dir(project)
            _stamp(root, directory=True)
            directory = root / "worktrees"
            before = _stamp(directory, directory=True)
            stamps.append((directory, before, True))
            paths = sorted(directory.iterdir())
        except _READ_ERRORS as exc:
            issues.append(issue("project-unavailable", project, str(exc)))
            continue
        for path in paths:
            if path.suffix != ".yaml":
                continue
            try:
                data, stamp = _read(path)
                stamps.append((path, stamp, False))
                rows.extend(_record(data, path, project, issues))
            except _READ_ERRORS as exc:
                issues.append(issue("record-unavailable", f"{project}/{path.name}", str(exc)))
    # Detect ordinary replacement/deletion/write races, not a cross-record transaction.
    for path, stamp, directory in stamps:
        try:
            if _stamp(path, directory=directory) != stamp:
                raise ValueError("tracking input changed during scan")
        except _READ_ERRORS as exc:
            issues.append(issue("snapshot-changed", str(path), str(exc)))
    return ownership_snapshot(rows, issues, owner=owner, kind=kind, namespace=namespace, identity=identity)
