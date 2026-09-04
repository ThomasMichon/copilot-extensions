"""Reconcile: turn a resolved requirement-package union into machine state.

This module owns the machine-scoped restore flow. The *plan* half (read-only:
enumerate the managed surfaces + a reproducible drift key) is implemented here;
the *apply* half (mutating ``~/.copilot/`` per disposition, backup-before-write)
is delegated to the ``surfaces`` package and is built out per issue #4006.

The drift key hashes effective selected state, so changing only a superseded
losing value does not create drift. A separate provenance hash covers the full
resolved package union, including authority metadata.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import modules as _modules
from . import resources as _resources
from . import validator as _validator
from .authority import (
    AUTHORITY_MODE_OPAQUE_ADDITIVE,
    effective_authority,
    sort_decisions,
)
from .discover import current_platform
from .manifest import RequirementPackage, resolve_for_machine
from .surfaces import SurfaceResult, apply_surfaces, collect_contributions
from .surfaces._common import merge_enforce


@dataclass
class ManagedSurface:
    """A surface (e.g. ``copilot.settings``) and the packages that manage it."""

    key: str
    disposition: str
    contributing_packages: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """A read-only restore plan: what *would* change, and the drift key."""

    machine: str
    surfaces: list[ManagedSurface]
    drift_key: str
    package_names: list[str]
    provenance_hash: str = ""
    modules: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    package_sources: list[dict[str, Any]] = field(default_factory=list)
    package_authorities: list[dict[str, Any]] = field(default_factory=list)
    removals: list[dict[str, Any]] = field(default_factory=list)
    authority_decisions: list[dict[str, Any]] = field(default_factory=list)


def _repo_paths(resolved: list[RequirementPackage]) -> dict[str, Path]:
    """Map each repo name to its canonical location-class anchor."""
    paths: dict[str, Path] = {}
    for pkg in resolved:
        root = pkg.repo_anchor()
        if root is not None:
            paths.setdefault(pkg.source_repo, root)
    return paths


def resolve_union(
    packages: list[RequirementPackage],
    machine: str,
    accepted_machines: tuple[str, ...] | None = None,
) -> list[RequirementPackage]:
    """Layer each package to ``machine`` first, then return the union list.

    Layer-within-repo precedes union-across-repos so the drift key is stable.
    """
    return [
        resolve_for_machine(pkg, machine, accepted_machines)
        for pkg in packages
        if pkg.applies_to(machine, accepted_machines)
    ]


def manifest_hash(resolved: list[RequirementPackage]) -> str:
    """A reproducible content hash over the resolved package union."""
    payload = [
        {
            "package": pkg.name,
            "source_repo": pkg.source_repo,
            "authority": pkg.authority,
            "manage": pkg.manage,
            "exclude": pkg.exclude,
            "aliases": pkg.aliases,
            "bootstrap_floor": pkg.bootstrap_floor,
            "modules": pkg.modules,
            "resources": pkg.resources,
        }
        for pkg in sorted(resolved, key=lambda p: (p.source_repo, p.name))
    ]
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _without_declaration_authority(value: Any) -> Any:
    """Remove only a declaration's top-level authority metadata."""
    copied = copy.deepcopy(value)
    if isinstance(copied, dict):
        copied.pop("authority", None)
    return copied


def _settings_operations(resolved: list[RequirementPackage]) -> dict[str, Any]:
    """Normalize settings behavior without depending on hypothetical live state."""
    floors: list[dict[str, Any]] = []
    enforced: dict[str, Any] = {}
    removals: set[str] = set()
    contributions = collect_contributions(resolved, "copilot.settings")
    for disposition in ("ensure-present", "enforce"):
        for disp, values, _ in contributions:
            if disp != disposition:
                continue
            if disposition == "ensure-present":
                floors.append(copy.deepcopy(values))
                continue
            for key, value in values.items():
                enforced[key] = merge_enforce(enforced.get(key), value)
    for disposition, keys, _ in contributions:
        if disposition == "ensure-absent":
            removals.update(str(item) for item in keys.get("enabledPlugins", []))
    return {
        "ensure-present": floors,
        "enforce": enforced,
        "ensure-absent": {"enabledPlugins": sorted(removals)},
    }


def effective_state_hash(
    resolved: list[RequirementPackage],
    machine: str,
    plat: str,
) -> str:
    """Hash behaviorally effective state, excluding losing authority values."""
    resources, _ = _resources.resolve_resources(resolved, machine, plat)
    non_settings_manage = []
    package_metadata = []
    modules = [
        {
            "package": pkg.name,
            "source_repo": pkg.source_repo,
            "module": _without_declaration_authority(module),
        }
        for pkg, module in _modules.resolve_modules(resolved, machine, plat)
    ]
    for pkg in sorted(resolved, key=lambda item: (item.source_repo, item.name)):
        for key, spec in sorted(pkg.manage.items()):
            if key == "copilot.settings" or key.startswith("copilot.settings."):
                continue
            non_settings_manage.append({
                "package": pkg.name,
                "source_repo": pkg.source_repo,
                "key": key,
                "spec": _without_declaration_authority(spec),
            })
        package_metadata.append({
            "package": pkg.name,
            "source_repo": pkg.source_repo,
            "exclude": pkg.exclude,
            "aliases": pkg.aliases,
            "bootstrap_floor": pkg.bootstrap_floor,
        })
    payload = {
        "settings": _settings_operations(resolved),
        "manage": non_settings_manage,
        "packages": package_metadata,
        "modules": sorted(
            modules,
            key=lambda item: (
                item["source_repo"],
                item["package"],
                str(item["module"].get("name", "")),
            ),
        ),
        "resources": [
            {
                "type": resource.type,
                "identity": [str(part) for part in resource.identity],
                "desired": resource.desired,
            }
            for resource in resources
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def plan(
    packages: list[RequirementPackage],
    machine: str,
    plat: str | None = None,
    accepted_machines: tuple[str, ...] | None = None,
) -> Plan:
    """Build a read-only restore plan for ``machine`` (no mutation)."""
    plat = plat or current_platform()
    resolved = resolve_union(packages, machine, accepted_machines)
    surfaces: dict[str, ManagedSurface] = {}
    for pkg in resolved:
        for key, spec in pkg.manage.items():
            disp = spec.get("disposition", "ignore")
            surface = surfaces.setdefault(key, ManagedSurface(key=key, disposition=disp))
            surface.contributing_packages.append(pkg.name)
            # An enforced surface dominates the reported disposition.
            if disp == "enforce":
                surface.disposition = "enforce"
    for surface in surfaces.values():
        surface.contributing_packages = sorted(set(surface.contributing_packages))
    module_list = [
        {
            "name": str(mod.get("name")),
            "package": pkg.name,
            "source_repo": pkg.source_repo,
            "authority": effective_authority(pkg, mod),
            "authority_mode": AUTHORITY_MODE_OPAQUE_ADDITIVE,
        }
        for pkg, mod in _modules.resolve_modules(resolved, machine, plat)
    ]
    resolved_resources, _ = _resources.resolve_resources(resolved, machine, plat)
    resource_list = [
        {
            "type": res.type,
            "id": res.id,
            "summary": res.summary(),
            "contributors": res.contributors,
            "contributor_details": res.contributor_details,
            "authority_decisions": res.authority_decisions,
        }
        for res in resolved_resources
    ]
    authority_decisions = sort_decisions(
        _validator.settings_authority_decisions(resolved)
        + [
            decision
            for resource in resolved_resources
            for decision in resource.authority_decisions
        ]
    )
    removals: dict[str, set[str]] = {}
    for pkg in resolved:
        for spec in pkg.manage.values():
            if spec.get("disposition") != "ensure-absent":
                continue
            for identity in spec.get("keys", {}).get("enabledPlugins", []):
                removals.setdefault(identity, set()).add(pkg.name)
    return Plan(
        machine=machine,
        surfaces=sorted(surfaces.values(), key=lambda s: s.key),
        drift_key=effective_state_hash(resolved, machine, plat),
        provenance_hash=manifest_hash(resolved),
        package_names=sorted(pkg.name for pkg in resolved),
        modules=module_list,
        resources=resource_list,
        package_sources=[
            {"package": pkg.name, "source_repo": pkg.source_repo}
            for pkg in sorted(resolved, key=lambda item: (item.source_repo, item.name))
        ],
        package_authorities=[
            {
                "package": pkg.name,
                "source_repo": pkg.source_repo,
                "authority": pkg.authority,
            }
            for pkg in sorted(resolved, key=lambda item: (item.source_repo, item.name))
        ],
        removals=[
            {
                "op": "remove",
                "key": "enabledPlugins",
                "item": identity,
                "contributors": sorted(contributors),
            }
            for identity, contributors in sorted(removals.items())
        ],
        authority_decisions=authority_decisions,
    )


def plan_to_dict(p: Plan) -> dict[str, Any]:
    return {
        "machine": p.machine,
        "drift_key": p.drift_key,
        "provenance_hash": p.provenance_hash,
        "packages": p.package_names,
        "package_sources": p.package_sources,
        "package_authorities": p.package_authorities,
        "surfaces": [dataclasses.asdict(s) for s in p.surfaces],
        "modules": p.modules,
        "resources": p.resources,
        "removals": p.removals,
        "authority_decisions": p.authority_decisions,
    }


@dataclass
class RestoreResult:
    """The outcome of a restore: the plan, surface, resource, and module results."""

    plan: Plan
    surface_results: list[SurfaceResult] = field(default_factory=list)
    resource_results: list[_resources.ResourceResult] = field(default_factory=list)
    module_results: list[_modules.ModuleResult] = field(default_factory=list)

    @property
    def surfaces_applied(self) -> bool:
        return any(s.changed and not s.dry_run for s in self.surface_results)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.resource_results) and all(
            r.ok for r in self.module_results
        )


class RestoreValidationError(RuntimeError):
    """Restore was refused because the resolved package union is invalid."""

    def __init__(self, findings: list[Any]):
        self.findings = findings
        detail = "; ".join(
            f"{finding.code}: {finding.message}"
            for finding in findings
            if finding.level == "error"
        )
        super().__init__(f"validator reported errors: {detail}")


def restore(
    packages: list[RequirementPackage],
    machine: str,
    dry_run: bool = True,
    plat: str | None = None,
    home: Any = None,
    only: list[str] | None = None,
    accepted_machines: tuple[str, ...] | None = None,
) -> RestoreResult:
    """Converge ``machine`` to the package union.

    Applies the Copilot **surfaces** (by disposition, backup-before-write), the
    declarative **resources** (packages/files), then runs the repo-local
    **modules**, all honoring the dry-run safety rules. ``only`` restricts the
    run to named surfaces/resources/modules -- the "review a section, then apply
    just that section" flow.
    """
    plat = plat or current_platform()
    resolved = resolve_union(packages, machine, accepted_machines)
    findings = _validator.validate(resolved, machine, plat)
    if _validator.has_errors(findings):
        raise RestoreValidationError(findings)
    p = plan(packages, machine, plat, accepted_machines)
    surfaces = apply_surfaces(resolved, home=home, dry_run=dry_run, only=only)

    resource_names = _resources.resource_only_names(resolved, machine, plat)
    resource_results: list[_resources.ResourceResult] = []
    if _want_resources(only, resource_names):
        home_path = Path(home) if home is not None else Path.home()
        ctx = _resources.ResourceContext(
            home=home_path, repo_paths=_repo_paths(resolved), platform=plat
        )
        resource_results = _resources.apply_resources(
            resolved, machine, plat, ctx, dry_run=dry_run, only=only
        )

    all_modules: list[_modules.ModuleResult] = []
    if _want_modules(only, resource_names):
        all_modules = _modules.run_modules(resolved, machine, plat, dry_run=dry_run)
    results = [r for r in all_modules if not only or r.name in only]
    return RestoreResult(
        plan=p,
        surface_results=surfaces,
        resource_results=resource_results,
        module_results=results,
    )


_SURFACE_ONLY_NAMES = {
    "copilot.settings", "copilot.permissions", "copilot.trustedFolders",
    "settings", "permissions", "trustedFolders",
}


def _want_resources(only: list[str] | None, resource_names: set[str]) -> bool:
    """Run resources unless ``only`` selects surfaces/modules exclusively."""
    if not only:
        return True
    return any(name in resource_names for name in only)


def _want_modules(only: list[str] | None, resource_names: set[str] | None = None) -> bool:
    """Skip modules when ``only`` names surfaces/resources exclusively."""
    if not only:
        return True
    known = _SURFACE_ONLY_NAMES | (resource_names or set())
    return any(name not in known for name in only)


def restore_result_to_dict(result: RestoreResult) -> dict[str, Any]:
    """Serialize a ``RestoreResult`` (plan + surface & module results) for ``--json``.

    Module results include their captured ``stdout_tail``/``stderr_tail`` so a
    dry-run preview is fully machine-readable, not just a one-word status.
    """
    resources = []
    for resource in result.resource_results:
        payload = dataclasses.asdict(resource)
        payload["status"] = resource.status
        resources.append(payload)
    return {
        "plan": plan_to_dict(result.plan),
        "surfaces": [dataclasses.asdict(s) for s in result.surface_results],
        "resources": resources,
        "modules": [dataclasses.asdict(m) for m in result.module_results],
        "authority_decisions": result.plan.authority_decisions,
        "ok": result.ok,
    }
