"""Surface handlers -- one module per logical ``~/.copilot/`` surface.

A *surface* maps a logical managed key to one or more physical files and knows
how to read live state, diff it against the resolved package union, and apply by
disposition (``enforce`` overwrite / ``ensure-present`` union-floor), with
backup-before-write. Planned surfaces (issue #4006):

* ``copilot.settings``       -> ``~/.copilot/settings.json`` (enforce scalars)
* ``copilot.permissions``    -> ``~/.copilot/permissions-config.json``
  (ensure-present, by location-class)
* ``copilot.trustedFolders`` -> ``~/.copilot/config.json`` ``trustedFolders`` (ensure-present)

The allowlist stance means an undeclared key is ``ignore`` (never touched), and
``exclude`` keys (e.g. ``mcp-oauth-config/**``) are never serialized by capture.

This package is intentionally a stub until issue #4006 -- the engine core
(discover / manifest / layering / validator / plan) lands first.
"""

from __future__ import annotations

#: Logical surface -> physical file(s) under ~/.copilot/. One logical surface may
#: span two files (trustedFolders lives in config.json, not permissions-config.json).
SURFACE_FILES = {
    "copilot.settings": ("settings.json",),
    "copilot.permissions": ("permissions-config.json",),
    "copilot.trustedFolders": ("config.json",),
}
