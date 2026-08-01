"""The ``copilot.trustedFolders`` surface -> ``~/.copilot/config.json``.

``trustedFolders`` is a list of absolute paths. This surface resolves each
declared location-class to concrete existing paths and unions them into the list
(``ensure-present`` -- never removes an existing trust). Only the
``trustedFolders`` key is touched (allowlist); the rest of ``config.json``
(machine-junk like ``expAssignmentsCache``) is preserved untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..locations import resolve_concrete_paths
from ._common import (
    SurfaceResult,
    backup_file,
    copilot_home,
    read_json,
    write_json_atomic,
)

SURFACE = "copilot.trustedFolders"
CONFIG_FILE = "config.json"


def apply(
    specs: list[dict[str, Any]],
    repo_paths: dict[str, Path],
    home: Path | None = None,
    dry_run: bool = True,
) -> SurfaceResult:
    home = home or copilot_home()
    user_home = home.parent
    path = home / CONFIG_FILE
    live = read_json(path)
    existing = list(live.get("trustedFolders") or [])
    folders = list(existing)
    added: list[str] = []

    for spec in specs:
        if spec.get("disposition") != "ensure-present":
            continue
        for match in spec.get("by-location-class") or []:
            for concrete in resolve_concrete_paths(str(match), user_home, repo_paths):
                s = str(concrete)
                if s not in folders:
                    folders.append(s)
                    added.append(s)

    changed = folders != existing
    backup = None
    if changed and not dry_run:
        backup = backup_file(path)
        new = dict(live)
        new["trustedFolders"] = folders
        write_json_atomic(path, new)
    return SurfaceResult(
        surface=SURFACE,
        file=CONFIG_FILE,
        changed=changed,
        dry_run=dry_run,
        applied_keys=["trustedFolders"] if changed else [],
        changes=[{"key": "trustedFolders", "added": added}] if added else [],
        backup_path=str(backup) if backup else None,
    )
