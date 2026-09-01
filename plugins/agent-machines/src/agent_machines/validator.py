"""The conflict validator.

Multiple harness repos on one machine may declare overlapping desired state.
Per the operator's model, conflict handling is **detect-and-report, not
auto-arbitrate**: the user owns conflict-avoidance; the validator surfaces
clashes so they can fix their packages.

Two rules, plus one advisory, all computable from manifests alone (no live
``~/.copilot/`` read required):

* **Value-shape rule** -- conflict-proneness follows leaf value shape. Nested
  maps are traversed because the settings surface deep-merges them; their scalar
  leaves are authoritative singletons just like top-level ``model``. Two
  packages enforcing the same scalar leaf to different values is a hard
  **conflict**. A list/opaque collection leaf under ``enforce`` should instead
  be ``ensure-present`` (union), so declaring one is a shape **advisory**.
* **Bootstrap-floor assertion** -- the union of enabled plugins/marketplaces must
  contain the stack-critical set, and no package may set one ``false``; otherwise
  restore could disable its own trigger (a fleet-wide self-heal outage).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import resources as _resources
from .discover import current_platform
from .manifest import (
    BOOTSTRAP_CRITICAL_MARKETPLACES,
    BOOTSTRAP_CRITICAL_PLUGINS,
    RequirementPackage,
)

_SCALAR = (str, int, float, bool)
_OPAQUE_SETTING_MAPS = {
    ("copilot.settings", "enabledPlugins"),
    ("copilot.settings", "extraKnownMarketplaces"),
}


@dataclass
class Finding:
    """One validator result. ``level`` is ``error``, ``advisory``, or ``info``."""

    level: str
    code: str
    message: str


def _managed_values_under(pkg: RequirementPackage, prefix: str):
    """Yield values from the root surface and every grouping below it."""
    for key, spec in pkg.manage.items():
        if (
            key == prefix or key.startswith(prefix + ".")
        ) and spec.get("disposition") in {"enforce", "ensure-present"}:
            yield spec.get("values", spec.get("value"))


def _enabled_plugins_union(packages: list[RequirementPackage]) -> dict[str, bool]:
    """Union of ``manage.copilot.settings.values.enabledPlugins`` across packages."""
    union: dict[str, bool] = {}
    for pkg in packages:
        for values in _managed_values_under(pkg, "copilot.settings"):
            if not isinstance(values, dict):
                continue
            enabled_plugins = values.get("enabledPlugins") or {}
            if not isinstance(enabled_plugins, dict):
                continue
            for name, on in enabled_plugins.items():
                base = str(name).split("@", 1)[0]
                # Record an explicit false anywhere so the floor check catches it.
                union[base] = bool(on) and union.get(base, True)
        for name in pkg.bootstrap_floor.get("plugins") or []:
            union.setdefault(str(name).split("@", 1)[0], True)
    return union


def _marketplace_union(packages: list[RequirementPackage]) -> set[str]:
    out: set[str] = set()
    for pkg in packages:
        for values in _managed_values_under(pkg, "copilot.settings"):
            if isinstance(values, dict):
                marketplaces = values.get("extraKnownMarketplaces") or {}
                if isinstance(marketplaces, dict):
                    out.update(marketplaces.keys())
        out.update(pkg.bootstrap_floor.get("marketplaces") or [])
    return out


def _enforced_nodes(prefix: tuple[str, ...], value: Any):
    """Yield every value node, including maps that restore deep-merges."""
    yield prefix, value
    if isinstance(value, dict) and prefix not in _OPAQUE_SETTING_MAPS:
        for key, child in value.items():
            yield from _enforced_nodes((*prefix, str(key)), child)


def _format_path(path: tuple[str, ...]) -> str:
    """Render an unambiguous setting path without conflating dotted JSON keys."""
    rendered = path[0]
    for component in path[1:]:
        rendered += (
            f".{component}"
            if component.isidentifier()
            else f"[{json.dumps(component)}]"
        )
    return rendered


def _shape(value: Any) -> str:
    if isinstance(value, dict):
        return "map"
    if isinstance(value, list):
        return "list"
    if isinstance(value, _SCALAR):
        return "scalar"
    if value is None:
        return "null"
    return type(value).__name__


def check_scalar_conflicts(packages: list[RequirementPackage]) -> list[Finding]:
    """Detect cross-package ``enforce`` disagreements (value-shape rule).

    The disposition is declared per *surface* (``copilot.settings``), but the
    value-shape rule applies per *leaf setting* inside its ``values``. We recurse
    through dictionaries because the settings surface deep-merges them: nested
    scalar leaves are the conflict domain (differing values across packages =>
    error). A list or opaque collection leaf under an ``enforce`` surface is a
    shape advisory (it belongs under ``ensure-present`` union).
    """
    findings: list[Finding] = []
    enforced_scalar: dict[tuple[str, ...], list[tuple[str, Any]]] = {}
    collection_leaf_owners: dict[tuple[str, ...], set[str]] = {}
    enforced_shapes: dict[tuple[str, ...], list[tuple[str, str]]] = {}

    for pkg in packages:
        for key, spec in pkg.manage.items():
            if spec.get("disposition") != "enforce":
                continue
            if key != "copilot.settings" and not key.startswith("copilot.settings."):
                continue
            values = spec.get("values", spec.get("value"))
            if values is None:
                continue
            # copilot.settings.* suffixes group contributions but do not change
            # their physical settings.json root.
            root = (
                "copilot.settings"
                if key == "copilot.settings" or key.startswith("copilot.settings.")
                else key
            )
            for leaf_key, val in _enforced_nodes((root,), values):
                enforced_shapes.setdefault(leaf_key, []).append((pkg.name, _shape(val)))
                if isinstance(val, _SCALAR):
                    enforced_scalar.setdefault(leaf_key, []).append((pkg.name, val))
                elif (
                    val is not None
                    and (
                        not isinstance(val, dict)
                        or leaf_key in _OPAQUE_SETTING_MAPS
                    )
                ):
                    collection_leaf_owners.setdefault(leaf_key, set()).add(pkg.name)

    for leaf_key, entries in sorted(enforced_shapes.items()):
        shapes = {shape for _, shape in entries}
        if len(shapes) > 1:
            detail = "; ".join(f"{name}={shape}" for name, shape in entries)
            findings.append(
                Finding(
                    "error",
                    "enforce-shape-conflict",
                    f"'{_format_path(leaf_key)}' is enforced with incompatible value shapes "
                    f"across packages: {detail}",
                )
            )
    for leaf_key, owners in sorted(collection_leaf_owners.items()):
        findings.append(
            Finding(
                "advisory",
                "shape-mismatch",
                f"'{_format_path(leaf_key)}' is enforced with a list/collection value by "
                f"{', '.join(sorted(owners))}; collection keys should be "
                f"'ensure-present' (union), not 'enforce'.",
            )
        )
    for leaf_key, entries in sorted(enforced_scalar.items()):
        if len({(type(value), value) for _, value in entries}) > 1:
            detail = "; ".join(f"{name}={value!r}" for name, value in entries)
            findings.append(
                Finding(
                    "error",
                    "enforce-conflict",
                    f"'{_format_path(leaf_key)}' is enforced to conflicting scalar values "
                    f"across packages: {detail}",
                )
            )
    return findings


def check_bootstrap_floor(packages: list[RequirementPackage]) -> list[Finding]:
    """Assert the stack-critical plugins/marketplaces survive the union."""
    findings: list[Finding] = []
    plugins = _enabled_plugins_union(packages)
    markets = _marketplace_union(packages)

    for critical in BOOTSTRAP_CRITICAL_PLUGINS:
        if plugins.get(critical) is False:
            findings.append(
                Finding(
                    "error",
                    "bootstrap-floor",
                    f"a package disables the bootstrap-critical plugin "
                    f"'{critical}' (enforce/union must never remove it -- "
                    f"restore would disable its own trigger).",
                )
            )
    # A missing critical marketplace is only an error when *some* package manages
    # marketplaces at all (otherwise the machine simply isn't managing that surface).
    if markets:
        for critical in BOOTSTRAP_CRITICAL_MARKETPLACES:
            if critical not in markets:
                findings.append(
                    Finding(
                        "error",
                        "bootstrap-floor",
                        f"the managed marketplace union omits the bootstrap-critical "
                        f"marketplace '{critical}'.",
                    )
                )
    return findings


def check_plugin_tombstone_schema(
    packages: list[RequirementPackage],
) -> list[Finding]:
    """Require schema v2 before a package relies on enabled-plugin tombstones."""
    findings: list[Finding] = []
    for pkg in packages:
        if pkg.schema_version >= 2:
            continue
        for values in _managed_values_under(pkg, "copilot.settings"):
            if not isinstance(values, dict):
                continue
            enabled_plugins = values.get("enabledPlugins")
            if not isinstance(enabled_plugins, dict):
                continue
            tombstones = sorted(
                str(name)
                for name, enabled in enabled_plugins.items()
                if enabled is False
            )
            if tombstones:
                findings.append(
                    Finding(
                        "error",
                        "schema-capability",
                        f"package '{pkg.name}' uses enabled-plugin tombstones "
                        f"under schema_version {pkg.schema_version}; "
                        f"schema_version 2 is required: {', '.join(tombstones)}",
                    )
                )
    return findings


def check_resource_conflicts(
    packages: list[RequirementPackage], machine: str = "", plat: str | None = None
) -> list[Finding]:
    """Detect cross-package collisions among declarative ``resources:``.

    Delegated to :func:`agent_machines.resources.detect_conflicts` (which owns
    the per-type merge rules); its :class:`~agent_machines.resources.ResourceFinding`
    results are mapped onto the validator's :class:`Finding` shape so resource
    collisions surface alongside surface/bootstrap findings.
    """
    plat = plat or current_platform()
    return [
        Finding(rf.level, rf.code, rf.message)
        for rf in _resources.detect_conflicts(packages, machine, plat)
    ]


def validate(
    packages: list[RequirementPackage], machine: str = "", plat: str | None = None
) -> list[Finding]:
    """Run every manifest-only validation rule over the resolved package union."""
    findings: list[Finding] = []
    findings.extend(check_scalar_conflicts(packages))
    findings.extend(check_plugin_tombstone_schema(packages))
    findings.extend(check_bootstrap_floor(packages))
    findings.extend(check_resource_conflicts(packages, machine, plat))
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
