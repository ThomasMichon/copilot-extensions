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

All three surfaces apply: ``copilot.settings`` (by disposition),
``copilot.permissions`` and ``copilot.trustedFolders`` (by location-class,
``ensure-present`` floors).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..authority import effective_authority
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


def collect_contributions(
    packages: list[RequirementPackage], prefix: str
) -> list[tuple[str, dict[str, Any], str]]:
    """Gather ``(disposition, payload, package)`` under ``prefix``."""
    out: list[
        tuple[int, str, str, str, str, dict[str, Any]]
    ] = []
    for pkg in packages:
        for key, spec in pkg.manage.items():
            if key == prefix or key.startswith(prefix + "."):
                disposition = spec.get("disposition", "ignore")
                payload = (
                    spec.get("keys")
                    if disposition == "ensure-absent"
                    else spec.get("values", spec.get("value"))
                )
                if isinstance(payload, dict):
                    order_authority = (
                        effective_authority(pkg, spec)
                        if disposition == "enforce"
                        else 0
                    )
                    out.append((
                        order_authority,
                        pkg.source_repo,
                        pkg.name,
                        key,
                        disposition,
                        payload,
                    ))
    out.sort(key=lambda item: item[:4])
    return [(disposition, payload, package) for _, _, package, _, disposition, payload in out]


def collect_specs(packages: list[RequirementPackage], prefix: str) -> list[dict[str, Any]]:
    """Gather the raw manage-key specs under ``prefix`` (for by-location-class surfaces)."""
    out: list[dict[str, Any]] = []
    for pkg in packages:
        for key, spec in pkg.manage.items():
            if key == prefix or key.startswith(prefix + "."):
                out.append(spec)
    return out


def _repo_paths(packages: list[RequirementPackage]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for pkg in packages:
        root = pkg.repo_anchor()
        if root is not None:
            paths.setdefault(pkg.source_repo, root)
    return paths


def apply_surfaces(
    packages: list[RequirementPackage],
    home: Path | None = None,
    dry_run: bool = True,
    only: list[str] | None = None,
) -> list[SurfaceResult]:
    """Apply every implemented surface (optionally filtered by ``only`` names)."""
    from . import permissions as _permissions
    from . import settings as _settings
    from . import trusted_folders as _trusted

    def wanted(name: str) -> bool:
        return not only or name in only or name.rsplit(".", 1)[-1] in only

    repo_paths = _repo_paths(packages)
    results: list[SurfaceResult] = []

    if wanted("copilot.settings"):
        contribs = collect_contributions(packages, "copilot.settings")
        if contribs:
            results.append(_settings.apply(contribs, home=home, dry_run=dry_run))
    if wanted("copilot.permissions"):
        specs = collect_specs(packages, "copilot.permissions")
        if specs:
            results.append(_permissions.apply(specs, repo_paths, home=home, dry_run=dry_run))
    if wanted("copilot.trustedFolders"):
        specs = collect_specs(packages, "copilot.trustedFolders")
        if specs:
            results.append(_trusted.apply(specs, repo_paths, home=home, dry_run=dry_run))
    return results
