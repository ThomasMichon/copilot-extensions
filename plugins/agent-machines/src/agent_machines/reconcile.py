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

import copy
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from . import modules as _modules
from . import resources as _resources
from . import self_update as _self_update
from . import self_update_state as _self_update_state
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


_RUNTIME_CHECK_TIMEOUT = 60
_RUNTIME_INSTALLER_ENV_UNSET = (
    "COPILOT_EXTENSIONS_CONTEXT",
    "COPILOT_PLUGIN_INSTALL_STAGED",
    "COPILOT_PLUGIN_ROOT",
    "COPILOT_PLUGIN_STAGED_FROM",
)


def _repo_paths(resolved: list[RequirementPackage]) -> dict[str, Path]:
    """Map each repo name to its canonical location-class anchor."""
    paths: dict[str, Path] = {}
    for pkg in resolved:
        root = pkg.repo_anchor()
        if root is not None:
            paths.setdefault(pkg.source_repo, root)
    return paths


def _runtime_payload_root(home: Path) -> Path:
    return home / ".copilot" / "installed-plugins" / "copilot-extensions"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _clean_runtime_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in _RUNTIME_INSTALLER_ENV_UNSET:
        env.pop(key, None)
    return env


def _runtime_command_runner(argv: list[str], *, timeout: int) -> _resources.RunOutcome:
    # Same PATHEXT-resolution fix as self_update.default_command_runner: a
    # bare plugin binstub name (e.g. "agent-machines") is really a `.cmd`
    # shim on Windows, and CreateProcess (shell=False) does not search
    # PATHEXT the way cmd.exe does.
    resolved = argv
    if argv:
        binary = shutil.which(argv[0])
        if binary:
            resolved = [binary, *argv[1:]]
    proc = subprocess.run(  # noqa: S603 - argv list rooted in the installed payload/binstub
        resolved,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_clean_runtime_environment(),
        **no_window_kwargs(),
    )
    return _resources.RunOutcome(proc.returncode, proc.stdout or "", proc.stderr or "")


def _repair_runtime_command(payload_root: Path, plat: str) -> list[str] | None:
    scripts = payload_root / "scripts"
    if plat == "windows":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return None
        for name, has_update in (("install.ps1", True), ("init.ps1", False)):
            script = scripts / name
            if script.is_file():
                return [shell, "-File", str(script)] + (["update"] if has_update else [])
        return None
    for name, has_update in (("install.sh", True), ("init.sh", False)):
        script = scripts / name
        if script.is_file():
            return ["bash", str(script)] + (["update"] if has_update else [])
    return None


def _runtime_check_command(name: str, payload: dict[str, Any]) -> list[str]:
    return [name, "installer-readiness"] if payload.get("installerReadiness") else [name, "version"]


def _runtime_detail(outcome: _resources.RunOutcome | None, error: Exception | None = None) -> str:
    if error is not None:
        return str(error)
    if outcome is None:
        return ""
    return (outcome.stderr or outcome.stdout).strip()[:200]


def runtime_spot_check_results(
    *,
    home: Path,
    plat: str,
    dry_run: bool,
    runner=_runtime_command_runner,
) -> list[_resources.ResourceResult]:
    """Spot-check installed runtime plugins during maintenance-safe restores.

    This intentionally uses the installed core-marketplace payload registry plus each
    runtime plugin's own `installer-readiness` CLI (falling back to `version` only when
    the manifest exposes no readiness contract) instead of importing the broader
    installation-cell planner here. The maintenance-safe restore path stays self-contained,
    while still deferring every repair to the owning plugin's existing install/init script.
    """
    payload_root = _runtime_payload_root(home)
    if not payload_root.is_dir():
        return []
    results: list[_resources.ResourceResult] = []
    for plugin_dir in sorted(path for path in payload_root.iterdir() if path.is_dir()):
        payload = _read_json_file(plugin_dir / "plugin.json")
        if not payload:
            continue
        name = payload.get("name")
        scope = payload.get("runtimeScope")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(scope, str)
            or scope == "none"
        ):
            continue
        check_cmd = _runtime_check_command(name, payload)
        repair_cmd = _repair_runtime_command(plugin_dir, plat)
        try:
            check = runner(check_cmd, timeout=_RUNTIME_CHECK_TIMEOUT)
            healthy = check.returncode == 0
            check_error: Exception | None = None
        except (OSError, subprocess.SubprocessError) as exc:
            check = None
            healthy = False
            check_error = exc
        if healthy:
            results.append(
                _resources.ResourceResult(
                    "runtime-spot-check",
                    name,
                    False,
                    dry_run,
                    "none",
                    detail=f"`{' '.join(check_cmd)}` succeeded",
                    commands=[check_cmd],
                )
            )
            continue
        if repair_cmd is None:
            detail = _runtime_detail(check, check_error)
            results.append(
                _resources.ResourceResult(
                    "runtime-spot-check",
                    name,
                    False,
                    dry_run,
                    "error",
                    detail=(
                        "runtime readiness failed and no install/init script was found"
                        + (f": {detail}" if detail else "")
                    ),
                    commands=[check_cmd],
                )
            )
            continue
        if dry_run:
            detail = _runtime_detail(check, check_error)
            results.append(
                _resources.ResourceResult(
                    "runtime-spot-check",
                    name,
                    True,
                    True,
                    "repair",
                    detail=(
                        f"would run {' '.join(repair_cmd)} after failed `{' '.join(check_cmd)}`"
                        + (f": {detail}" if detail else "")
                    ),
                    commands=[check_cmd, repair_cmd],
                )
            )
            continue
        repair = runner(repair_cmd, timeout=_resources.DEFAULT_TIMEOUT)
        if repair.returncode != 0:
            results.append(
                _resources.ResourceResult(
                    "runtime-spot-check",
                    name,
                    True,
                    False,
                    "error",
                    detail=(
                        f"`{' '.join(repair_cmd)}` exited {repair.returncode}: "
                        f"{_runtime_detail(repair)}"
                    ),
                    commands=[check_cmd, repair_cmd],
                )
            )
            continue
        try:
            post = runner(check_cmd, timeout=_RUNTIME_CHECK_TIMEOUT)
            post_error: Exception | None = None
        except (OSError, subprocess.SubprocessError) as exc:
            post = None
            post_error = exc
        if post is None or post.returncode != 0:
            detail = _runtime_detail(post, post_error)
            results.append(
                _resources.ResourceResult(
                    "runtime-spot-check",
                    name,
                    True,
                    False,
                    "error",
                    detail=(
                        f"runtime readiness still failed after {' '.join(repair_cmd)}"
                        + (f": {detail}" if detail else "")
                    ),
                    commands=[check_cmd, repair_cmd],
                )
            )
            continue
        results.append(
            _resources.ResourceResult(
                "runtime-spot-check",
                name,
                True,
                False,
                "repair",
                detail=f"repaired runtime after failed `{' '.join(check_cmd)}`",
                commands=[check_cmd, repair_cmd],
            )
        )
    return results


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
            non_settings_manage.append(
                {
                    "package": pkg.name,
                    "source_repo": pkg.source_repo,
                    "key": key,
                    "spec": _without_declaration_authority(spec),
                }
            )
        package_metadata.append(
            {
                "package": pkg.name,
                "source_repo": pkg.source_repo,
                "exclude": pkg.exclude,
                "aliases": pkg.aliases,
                "bootstrap_floor": pkg.bootstrap_floor,
            }
        )
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
    resource_list = []
    for res in resolved_resources:
        entry = {
            "type": res.type,
            "id": res.id,
            "summary": res.summary(),
            "contributors": res.contributors,
            "contributor_details": res.contributor_details,
            "authority_decisions": res.authority_decisions,
        }
        if res.type == "self-update":
            observed = _self_update_state.observed_plan_fields(res.id)
            entry["observed"] = observed
            entry["summary"] = _self_update.format_plan_summary(
                entry["summary"],
                observed,
            )
        resource_list.append(entry)
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
        return all(r.ok for r in self.resource_results) and all(r.ok for r in self.module_results)


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
    maintenance_safe: bool = False,
    accepted_machines: tuple[str, ...] | None = None,
) -> RestoreResult:
    """Converge ``machine`` to the package union.

    Applies the Copilot **surfaces** (by disposition, backup-before-write), the
    declarative **resources** (packages/files), then runs the repo-local
    **modules**, all honoring the dry-run safety rules. ``only`` restricts the
    run to named surfaces/resources/modules -- the "review a section, then apply
    just that section" flow. ``maintenance_safe`` keeps surface reconciliation
    fully enabled, scopes resources to unattended-safe entries, adds runtime
    spot checks for installed runtime plugins, and -- unless ``only`` names a
    module explicitly -- reports every module as skipped rather than running
    it: modules execute arbitrary repo-local commands with no per-module
    safety opt-in yet, so the whole category stays out of a blanket
    unattended sweep.
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
        if maintenance_safe:
            resource_results.extend(
                runtime_spot_check_results(home=home_path, plat=plat, dry_run=dry_run)
            )
        resource_results.extend(
            _resources.apply_resources(
            resolved,
            machine,
            plat,
            ctx,
            dry_run=dry_run,
            only=only,
            maintenance_safe=maintenance_safe,
            )
        )

    all_modules: list[_modules.ModuleResult] = []
    if _want_modules(only, resource_names):
        if maintenance_safe and not only:
            # Modules run arbitrary repo-local commands with no per-module
            # opt-in equivalent to a resource's `maintenance_safe: true` yet,
            # so the whole category stays out of a blanket unattended sweep.
            # An operator-directed `--only <module>` request is explicit
            # intent and still executes normally even under
            # `--maintenance-safe`.
            all_modules = _modules.maintenance_safe_skip_results(
                resolved, machine, plat, dry_run=dry_run
            )
        else:
            all_modules = _modules.run_modules(resolved, machine, plat, dry_run=dry_run)
    results = [r for r in all_modules if not only or r.name in only]
    return RestoreResult(
        plan=p,
        surface_results=surfaces,
        resource_results=resource_results,
        module_results=results,
    )


_SURFACE_ONLY_NAMES = {
    "copilot.settings",
    "copilot.permissions",
    "copilot.trustedFolders",
    "settings",
    "permissions",
    "trustedFolders",
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
