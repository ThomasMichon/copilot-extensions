"""The ``copilot.settings`` surface -> ``~/.copilot/settings.json``.

Applies every ``copilot.settings*`` contribution from the resolved package union
into the global settings file by disposition: ``ensure-present`` map/list floors
first (union, never clobbering session-accreted state), then ``enforce`` scalars
(authoritative). The values of a managed key are the settings.json top-level keys
(``model``, ``effortLevel``, ``enabledPlugins``, ...). Idempotent, backed up
before write, dry-run-safe.

Invariant (do not regress): this surface enforces **only the declared managed
keys** and merges them into the live file -- it **never rewrites or replaces the
whole ``settings.json``**. Every unmanaged key (``footer``, ``logLevel``, a stray
``subagents`` block, anything session-accreted) passes through untouched. Restore
is a targeted merge of the small subset the manifest declares, not a snapshot
put-back. Locked by ``tests/test_surfaces.py::test_settings_enforce_preserves_unmanaged_keys``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import (
    SurfaceResult,
    backup_file,
    copilot_home,
    diff_keys,
    merge_enforce,
    merge_floor,
    read_json,
    write_json_atomic,
)

SURFACE = "copilot.settings"
SETTINGS_FILE = "settings.json"


def apply(
    contributions: list[tuple[str, dict[str, Any]]],
    home: Path | None = None,
    dry_run: bool = True,
) -> SurfaceResult:
    """Converge ``settings.json`` from ``(disposition, values)`` contributions."""
    home = home or copilot_home()
    path = home / SETTINGS_FILE
    live = read_json(path)
    new = dict(live)
    applied: list[str] = []

    # Floors first (union), then enforce (authoritative overwrite).
    for disposition in ("ensure-present", "enforce"):
        for disp, values in contributions:
            if disp != disposition or not isinstance(values, dict):
                continue
            for key, val in values.items():
                if disposition == "enforce":
                    new[key] = merge_enforce(new.get(key), val)
                else:
                    new[key] = merge_floor(new.get(key), val)
                applied.append(key)

    changed = new != live
    backup = None
    if changed and not dry_run:
        backup = backup_file(path)
        write_json_atomic(path, new)
    return SurfaceResult(
        surface=SURFACE,
        file=SETTINGS_FILE,
        changed=changed,
        dry_run=dry_run,
        applied_keys=sorted(set(applied)),
        changes=diff_keys(live, new, applied),
        backup_path=str(backup) if backup else None,
    )
