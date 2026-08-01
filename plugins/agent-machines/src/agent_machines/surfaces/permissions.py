"""The ``copilot.permissions`` surface -> ``~/.copilot/permissions-config.json``.

Live shape: ``{"locations": {"<abs-path>": {"tool_approvals": [ {...}, ... ]}}}``.
A package declares approvals **by location class**::

    copilot.permissions:
      disposition: ensure-present
      by-location-class:
        - match: "$REPO(my-repo)"
          tool_approvals:
            - {kind: commands, commandIdentifiers: [git, gh, pwsh]}
        - match: "$WORKTREES/my-repo/*"
          tool_approvals: [ ... ]

Apply resolves each class to concrete existing paths and unions the declared
approvals into that location's ``tool_approvals`` (``ensure-present`` -- a floor,
never revoking a live grant). A worktree-glob applies to worktrees that exist
now; a future worktree picks the grant up at its next restore. Secret files
(``mcp-oauth-config``) are the vault's domain and are never touched here.
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

SURFACE = "copilot.permissions"
PERMISSIONS_FILE = "permissions-config.json"


def apply(
    specs: list[dict[str, Any]],
    repo_paths: dict[str, Path],
    home: Path | None = None,
    dry_run: bool = True,
) -> SurfaceResult:
    home = home or copilot_home()
    user_home = home.parent
    path = home / PERMISSIONS_FILE
    live = read_json(path)
    live_locations = live.get("locations") or {}
    # Deep-ish copy of the locations we might touch.
    locations = {loc: dict(spec) for loc, spec in live_locations.items()}
    changes: list[dict[str, Any]] = []

    for spec in specs:
        if spec.get("disposition") != "ensure-present":
            continue
        for entry in spec.get("by-location-class") or []:
            approvals = entry.get("tool_approvals") or []
            match = str(entry.get("match", ""))
            for concrete in resolve_concrete_paths(match, user_home, repo_paths):
                loc_key = str(concrete)
                loc = dict(locations.get(loc_key) or {})
                current = list(loc.get("tool_approvals") or [])
                added = [a for a in approvals if a not in current]
                if added:
                    loc["tool_approvals"] = current + added
                    locations[loc_key] = loc
                    changes.append({"location": loc_key, "added": added})

    changed = locations != live_locations
    backup = None
    if changed and not dry_run:
        backup = backup_file(path)
        new = dict(live)
        new["locations"] = locations
        write_json_atomic(path, new)
    return SurfaceResult(
        surface=SURFACE,
        file=PERMISSIONS_FILE,
        changed=changed,
        dry_run=dry_run,
        applied_keys=["locations"] if changed else [],
        changes=changes,
        backup_path=str(backup) if backup else None,
    )
