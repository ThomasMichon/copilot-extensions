"""Reconcile: turn a resolved requirement-package union into machine state.

This module owns the machine-scoped restore flow. The *plan* half (read-only:
enumerate the managed surfaces + a reproducible drift key) is implemented here;
the *apply* half (mutating ``~/.copilot/`` per disposition, backup-before-write)
is delegated to the ``surfaces`` package and is built out per issue #4006.

The drift key is ``hash(resolved package union)`` so a re-run with no manifest
change does ~zero work -- mirroring agent-worktrees' version-keyed reconcile,
but keyed on **manifest content**, not plugin version (that is why restore is a
distinct stage, not overloaded onto plugin-runtime install).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from . import modules as _modules
from .discover import current_platform
from .manifest import RequirementPackage, resolve_for_machine


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
    modules: list[dict[str, str]] = field(default_factory=list)


def resolve_union(
    packages: list[RequirementPackage], machine: str
) -> list[RequirementPackage]:
    """Layer each package to ``machine`` first, then return the union list.

    Layer-within-repo precedes union-across-repos so the drift key is stable.
    """
    return [resolve_for_machine(pkg, machine) for pkg in packages if pkg.applies_to(machine)]


def manifest_hash(resolved: list[RequirementPackage]) -> str:
    """A reproducible content hash over the resolved package union."""
    payload = [
        {"package": pkg.name, "manage": pkg.manage, "exclude": pkg.exclude}
        for pkg in sorted(resolved, key=lambda p: p.name)
    ]
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def plan(packages: list[RequirementPackage], machine: str, plat: str | None = None) -> Plan:
    """Build a read-only restore plan for ``machine`` (no mutation)."""
    plat = plat or current_platform()
    resolved = resolve_union(packages, machine)
    surfaces: dict[str, ManagedSurface] = {}
    for pkg in resolved:
        for key, spec in pkg.manage.items():
            disp = spec.get("disposition", "ignore")
            surface = surfaces.setdefault(key, ManagedSurface(key=key, disposition=disp))
            surface.contributing_packages.append(pkg.name)
            # An enforced surface dominates the reported disposition.
            if disp == "enforce":
                surface.disposition = "enforce"
    module_list = [
        {"name": str(mod.get("name")), "source_repo": pkg.source_repo}
        for pkg, mod in _modules.resolve_modules(resolved, machine, plat)
    ]
    return Plan(
        machine=machine,
        surfaces=sorted(surfaces.values(), key=lambda s: s.key),
        drift_key=manifest_hash(resolved),
        package_names=sorted(pkg.name for pkg in resolved),
        modules=module_list,
    )


def plan_to_dict(p: Plan) -> dict[str, Any]:
    return {
        "machine": p.machine,
        "drift_key": p.drift_key,
        "packages": p.package_names,
        "surfaces": [dataclasses.asdict(s) for s in p.surfaces],
        "modules": p.modules,
    }


@dataclass
class RestoreResult:
    """The outcome of a restore: the plan plus each module's result."""

    plan: Plan
    module_results: list[_modules.ModuleResult] = field(default_factory=list)
    surfaces_applied: bool = False

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.module_results)


def restore(
    packages: list[RequirementPackage],
    machine: str,
    dry_run: bool = True,
    plat: str | None = None,
) -> RestoreResult:
    """Converge ``machine`` to the package union.

    Runs the repo-local **modules** (the sensitive OS-mutating work, which lives
    in each harness repo) -- respecting the dry-run safety rule. The Copilot
    **surfaces** apply (settings/permissions/trustedFolders) is delivered by the
    ``surfaces`` package (issue #4006) and is currently a no-op placeholder.
    """
    plat = plat or current_platform()
    p = plan(packages, machine, plat)
    resolved = resolve_union(packages, machine)
    results = _modules.run_modules(resolved, machine, plat, dry_run=dry_run)
    return RestoreResult(plan=p, module_results=results, surfaces_applied=False)
