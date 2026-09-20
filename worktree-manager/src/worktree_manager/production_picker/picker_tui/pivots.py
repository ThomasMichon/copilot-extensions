"""Cross-plugin pivot registry for the Textual picker.

A pivot is a top-level view in the picker (Worktrees, Maintenance, Profiles).
This module lets *another* plugin -- installed in its own separate venv --
contribute an extra pivot without agent-worktrees importing its Python. Because
each plugin installs standalone (its own ``scripts/init.sh``), setuptools
entry-points do **not** cross venvs; a filesystem manifest registry does.

The contract:

* A contributing plugin declares its pivot in a template at
  ``<plugin_root>/pivots/<name>.json`` -- a display ``label``, a position hint
  (``after``), a ``list`` command (an argv template that prints a JSON array
  of entries to stdout), a field mapping so the generic renderer can pull
  id/title/worktree/badges out of each entry, and an ``actions`` set (each an
  argv template).
* ``ensure_pivots``/``scan_pivot_registry`` materialize one **attributed
  pointer** per active (plugin, template) pair into the shared runtime root
  at ``~/.agent-worktrees/pivots/<name>.json`` (overridable for tests via
  ``AGENT_WORKTREES_PIVOTS_DIR``) -- ``{schema_version, plugin, plugin_root,
  template}``, mirroring ``config_dropins.py``'s managed pointer. The pointer
  never carries baked content (no ``list``/``actions``): every scan re-reads
  the template fresh out of the identity-verified ``plugin_root`` and
  re-resolves its commands to absolute paths, so an ordinary plugin content
  or version-directory update needs no on-disk rewrite of the pointer at all,
  and there is exactly **one** file per plugin+template -- never a
  fingerprint-suffixed duplicate.
* The picker scans that directory at startup and renders a generic pivot per
  manifest -- no engine code per new pivot. Data flows only through the
  contributing plugin's CLI on ``PATH`` (never a cross-venv import), so the
  seam stays generic for future pivots (Bridges, Containers, ...).

Everything here is declarative and defensive: a missing directory, a malformed
manifest, or a CLI that never runs must never break the picker -- a bad or
absent pivot simply doesn't appear.
"""

from __future__ import annotations

from .pivot_actions import (
    ConfigSection,
    ManifestError,
    WorktreeAction,
    entry_matches,
    format_form_template,
    format_template,
    parse_config_sections,
    parse_worktree_actions,
    worktree_action_matches,
)
from .pivot_manifest import (
    Column,
    PIVOTS_DIR_ENV,
    PLUGINS_ROOT_ENV,
    PivotAction,
    PivotContribution,
    PivotRegistryReport,
    RegisteredPivot,
    discover_config_sections,
    discover_pivots,
    discover_worktree_actions,
    installed_plugins_dir,
    order_pivots,
    parse_list_payload,
    parse_manifest,
    pivots_dir,
    resolve_path,
)
from .pivot_registry_scan import ensure_pivots, scan_pivot_registry, warn_pivot_findings
def find_claiming_task(pivots, pivot_runtimes, machine, wid, wid4):
    """Phase 4 REVERSE cross-link (agent-dispatch-tasks-pane-ux-overhaul):
    the cached registered-pivot task row (from ``pivots``' descriptor list +
    ``pivot_runtimes``, a ``{reg.name: RegisteredPivotRuntime}`` map) whose
    ``worktree_field`` value equals worktree ``wid`` (the real, full id) or
    ``wid4`` (a short-fixture-style 4-char id, e.g. the demo preview's), or
    ``None``. Deliberately exact equality on both, not a suffix/``endswith``
    match against ``wid4`` -- two distinct full worktree ids can share the
    same trailing 4 hex chars, and a suffix match would silently associate
    a task with the wrong worktree on that collision. Reads each pivot's own
    ``get`` under the SAME scope key its OWN fetches use --
    ``engine.PickerScreen._pivot_scope_key``'s account-scoped pivots cache
    under the empty-string key regardless of machine, so this always uses
    ``machine`` for a machine-scoped registration but ``""`` for an
    ``account_scoped`` one, never a mismatched key that would silently
    never show a match. Read-only (never ``ensure``/``repoll``), so callers
    never trigger a fetch or block on one.

    Returns ``(row, group_field)`` -- the matched pivot's OWN declared
    ``group_field`` (the manifest key its phase actually lives under, e.g.
    agent-dispatch's ``group``; ``None`` when the manifest declares none),
    not a hardcoded ``"group"``, so a caller reads the real phase value
    regardless of the field name a given manifest chose. See
    ``engine.PickerScreen._worktree_claiming_task`` for the caller."""
    if not wid and not wid4:
        return None
    for d in pivots:
        reg = d.get("pivot")
        if reg is None or not getattr(reg, "worktree_field", None):
            continue
        rt = pivot_runtimes.get(reg.name)
        if rt is None:
            continue
        scope = "" if getattr(reg, "account_scoped", False) else machine
        state, rows, _err = rt.get(scope)
        if state != "ready":
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            wt = str(row.get(reg.worktree_field) or "").strip().lower()
            if wt and (wt == wid or (wid4 and wt == wid4)):
                return (row, getattr(reg, "group_field", None))
    return None


# Kept for symmetry with maintenance.py's module layout; the engine imports the
# functions above directly.
__all__ = [
    "Column",
    "ConfigSection",
    "ManifestError",
    "PIVOTS_DIR_ENV",
    "PLUGINS_ROOT_ENV",
    "PivotAction",
    "PivotContribution",
    "PivotRegistryReport",
    "RegisteredPivot",
    "WorktreeAction",
    "discover_config_sections",
    "discover_pivots",
    "discover_worktree_actions",
    "ensure_pivots",
    "entry_matches",
    "find_claiming_task",
    "format_form_template",
    "format_template",
    "installed_plugins_dir",
    "order_pivots",
    "parse_config_sections",
    "parse_list_payload",
    "parse_manifest",
    "parse_worktree_actions",
    "pivots_dir",
    "resolve_path",
    "scan_pivot_registry",
    "warn_pivot_findings",
    "worktree_action_matches",
]
