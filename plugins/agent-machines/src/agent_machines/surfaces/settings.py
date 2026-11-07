"""The ``copilot.settings`` surface -> ``~/.copilot/settings.json``.

Applies every ``copilot.settings*`` contribution from the resolved package union
into the global settings file by disposition: ``ensure-present`` map/list floors
first, then ``enforce`` values (authoritative). An ``enabledPlugins`` floor has
one deliberate tombstone rule: ``false`` authoritatively disables that plugin,
while ``true`` remains additive and preserves an existing operator ``false``.
The values of a managed key are the settings.json top-level keys (``model``,
``effortLevel``, ``enabledPlugins``, ...). Idempotent, backed up before write,
dry-run-safe.

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

from plugin_activation import (
    PluginStateError,
    read_json_object,
    remove_activation_entries,
)

from ._common import (
    SurfaceStateError,
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


def _merge_settings_floor(key: str, live: Any, manifest: Any) -> Any:
    """Apply the enabled-plugin tombstone without changing other floor semantics."""
    if key != "enabledPlugins" or not isinstance(live, dict) or not isinstance(manifest, dict):
        return merge_floor(live, manifest)
    out = dict(live)
    for plugin, enabled in manifest.items():
        if enabled is False:
            out[plugin] = False
        elif plugin not in out or out[plugin] is None:
            out[plugin] = enabled
    return out


def apply(
    contributions: list[
        tuple[str, dict[str, Any]] | tuple[str, dict[str, Any], str]
    ],
    home: Path | None = None,
    dry_run: bool = True,
) -> SurfaceResult:
    """Converge ``settings.json`` from ``(disposition, values)`` contributions."""
    home = home or copilot_home()
    path = home / SETTINGS_FILE
    normalized = [
        (item[0], item[1], item[2] if len(item) == 3 else "")
        for item in contributions
    ]
    has_removals = any(disp == "ensure-absent" for disp, _, _ in normalized)
    try:
        _, live = read_json_object(path) if has_removals else ("", read_json(path))
    except PluginStateError as exc:
        raise SurfaceStateError(str(exc)) from exc
    new = dict(live)
    applied: list[str] = []
    removal_changes: list[dict[str, Any]] = []

    # Floors first (union), then enforce (authoritative overwrite).
    for disposition in ("ensure-present", "enforce"):
        for disp, values, _ in normalized:
            if disp != disposition or not isinstance(values, dict):
                continue
            for key, val in values.items():
                if disposition == "enforce":
                    new[key] = merge_enforce(new.get(key), val)
                else:
                    new[key] = _merge_settings_floor(key, new.get(key), val)
                applied.append(key)

    # Desired absence is deliberately last. Validation rejects any declaration
    # that also manages the same identity to a value.
    removal_requests: dict[str, set[str]] = {}
    for disp, keys, contributor in normalized:
        if disp == "ensure-absent":
            for identity in keys.get("enabledPlugins", []):
                removal_requests.setdefault(identity, set())
                if contributor:
                    removal_requests[identity].add(contributor)
    if removal_requests:
        try:
            new, removed = remove_activation_entries(
                new,
                removal_requests,
                path=path,
            )
        except PluginStateError as exc:
            raise SurfaceStateError(str(exc)) from exc
        for identity in removed:
            removal_changes.append(
                {
                    "op": "remove",
                    "key": "enabledPlugins",
                    "items": [identity],
                    "contributors": sorted(removal_requests[identity]),
                }
            )
            applied.append("enabledPlugins")

    changed = new != live
    backup = None
    if changed and not dry_run:
        backup = backup_file(path)
        write_json_atomic(path, new)
    changes = diff_keys(live, new, applied)
    if removal_changes:
        changes = [change for change in changes if change["key"] != "enabledPlugins"]
        old_enabled = live.get("enabledPlugins", {})
        new_enabled = new.get("enabledPlugins", {})
        if isinstance(old_enabled, dict) and isinstance(new_enabled, dict):
            removed = {
                item
                for change in removal_changes
                for item in change["items"]
            }
            for identity in sorted(set(old_enabled) | set(new_enabled)):
                if identity in removed:
                    continue
                before = old_enabled.get(identity)
                after = new_enabled.get(identity)
                if before != after:
                    changes.append(
                        {
                            "key": f"enabledPlugins.{identity}",
                            "before": before,
                            "after": after,
                        }
                    )
        changes.extend(removal_changes)
    return SurfaceResult(
        surface=SURFACE,
        file=SETTINGS_FILE,
        changed=changed,
        dry_run=dry_run,
        applied_keys=sorted(set(applied)),
        changes=changes,
        backup_path=str(backup) if backup else None,
    )
