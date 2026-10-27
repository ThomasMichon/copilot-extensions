"""Restart discovery from registered current ledgers, never creation history."""

from __future__ import annotations

import os

import yaml

from . import config as cfg, installer, repos, tracking
from .execution_spaces import project_record_config, require_owner_identity
from .repos_registry_reader import read_registry

_PARSE_ERRORS = (KeyError, TypeError, ValueError, OverflowError, yaml.YAMLError)


class AllocationDiscoveryError(ValueError):
    """Malformed source data is unavailable inventory, never an empty ledger."""


def registered_projects() -> set[str]:
    try:
        adopted = installer.read_projects_registry(strict=True)["projects"]
        registered = read_registry(repos._repos_yaml_path(), strict=True).repos
        names = set(adopted) | set(registered)
        if any(not isinstance(name, str) or cfg._PROJECT_NAME_RE.fullmatch(name) is None for name in names):
            raise ValueError("invalid registered project name")
        return names
    except _PARSE_ERRORS as exc:
        raise AllocationDiscoveryError("allocation project registry is invalid") from exc


def current_records(project: str) -> list[tracking.WorktreeRecord]:
    root = cfg.tracking_dir(project)
    try:
        with os.scandir(root) as entries:
            paths = [root / entry.name for entry in entries if entry.name.endswith(".yaml")]
    except FileNotFoundError:
        return []
    try:
        records = []
        for path in sorted(paths):
            if path.is_symlink() or not path.is_file():
                raise ValueError("allocation creator ledger is redirected or unavailable")
            records.append(tracking.load_record(path, copy_result=False))
        return records
    except _PARSE_ERRORS as exc:
        raise AllocationDiscoveryError("current allocation ledger is invalid") from exc


def locally_owned(record: tracking.WorktreeRecord) -> bool:
    try:
        return require_owner_identity(record.execution_machine, project_record_config(record))
    except _PARSE_ERRORS as exc:
        raise AllocationDiscoveryError("allocation creator authority is invalid") from exc
