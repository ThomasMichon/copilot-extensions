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

This package now applies the ``copilot.settings`` surface; the
``copilot.permissions`` and ``copilot.trustedFolders`` surfaces (their
location-class model) are the next slice.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._common import SurfaceResult

if TYPE_CHECKING:  # pragma: no cover
    from ..manifest import RequirementPackage

#: Logical surface -> physical file(s) under ~/.copilot/. One logical surface may
#: span two files (trustedFolders lives in config.json, not permissions-config.json).
SURFACE_FILES = {
    "copilot.settings": ("settings.json",),
    "copilot.permissions": ("permissions-config.json",),
    "copilot.trustedFolders": ("config.json",),
}

#: Surfaces whose apply is not yet implemented (declared, reported, not written).
_PENDING = {
    "copilot.permissions": "permissions surface apply not yet implemented (#4006 follow-up)",
    "copilot.trustedFolders": "trustedFolders surface apply not yet implemented (#4006 follow-up)",
}


def collect_contributions(
    packages: list[RequirementPackage], prefix: str
) -> list[tuple[str, dict[str, Any]]]:
    """Gather ``(disposition, values)`` for every managed key under ``prefix``."""
    out: list[tuple[str, dict[str, Any]]] = []
    for pkg in packages:
        for key, spec in pkg.manage.items():
            if key == prefix or key.startswith(prefix + "."):
                values = spec.get("values", spec.get("value"))
                if isinstance(values, dict):
                    out.append((spec.get("disposition", "ignore"), values))
    return out


def apply_surfaces(
    packages: list[RequirementPackage],
    home: Path | None = None,
    dry_run: bool = True,
) -> list[SurfaceResult]:
    """Apply every implemented surface; report declared-but-pending ones."""
    from . import settings as _settings

    results: list[SurfaceResult] = []
    settings_contribs = collect_contributions(packages, "copilot.settings")
    if settings_contribs:
        results.append(_settings.apply(settings_contribs, home=home, dry_run=dry_run))
    for prefix, note in _PENDING.items():
        if collect_contributions(packages, prefix):
            results.append(
                SurfaceResult(prefix, SURFACE_FILES[prefix][0], changed=False,
                              dry_run=dry_run, skipped_reason=note)
            )
    return results

