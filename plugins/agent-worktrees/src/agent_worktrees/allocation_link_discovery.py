"""Restart discovery from registered current ledgers, never creation history."""

from __future__ import annotations

import os

from . import config as cfg, installer, repos, tracking
from .execution_spaces import project_record_config, require_owner_identity
from .repos_registry_reader import read_registry


def registered_projects() -> set[str]:
    adopted = installer.read_projects_registry(strict=True)["projects"]
    registered = read_registry(repos._repos_yaml_path(), strict=True).repos
    names = set(adopted) | set(registered)
    if any(not isinstance(name, str) or cfg._PROJECT_NAME_RE.fullmatch(name) is None for name in names):
        raise ValueError("allocation discovery requires valid registered project names")
    return names


def current_records(project: str) -> list[tracking.WorktreeRecord]:
    root = cfg.tracking_dir(project)
    try:
        with os.scandir(root) as entries:
            paths = [root / entry.name for entry in entries if entry.name.endswith(".yaml")]
    except FileNotFoundError:
        return []
    records = []
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError("allocation creator ledger is redirected or unavailable")
        records.append(tracking.load_record(path, copy_result=False))
    return records


def locally_owned(record: tracking.WorktreeRecord) -> bool:
    return require_owner_identity(record.execution_machine, project_record_config(record))
