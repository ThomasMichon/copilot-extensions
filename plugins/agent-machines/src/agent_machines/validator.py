"""The conflict validator.

Multiple harness repos on one machine may declare overlapping desired state.
Per the operator's model, conflict handling is **detect-and-report, not
auto-arbitrate**: the user owns conflict-avoidance; the validator surfaces
clashes so they can fix their packages.

Two rules, plus one advisory, all computable from manifests alone (no live
``~/.copilot/`` read required):

* **Value-shape rule** -- conflict-proneness follows value shape. A *scalar*
  ``enforce`` value is a singleton (``model``, ``effortLevel``); two packages
  enforcing the same key to *different* scalars is a hard **conflict**. A
  *map/list* ``enforce`` value should instead be ``ensure-present`` (union), so
  declaring one ``enforce`` is a shape **advisory**, never a conflict.
* **Bootstrap-floor assertion** -- the union of enabled plugins/marketplaces must
  contain the stack-critical set, and no package may set one ``false``; otherwise
  restore could disable its own trigger (a fleet-wide self-heal outage).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .manifest import (
    BOOTSTRAP_CRITICAL_MARKETPLACES,
    BOOTSTRAP_CRITICAL_PLUGINS,
    RequirementPackage,
)

_SCALAR = (str, int, float, bool)


@dataclass
class Finding:
    """One validator result. ``level`` is ``error``, ``advisory``, or ``info``."""

    level: str
    code: str
    message: str


def _managed_values(pkg: RequirementPackage, key: str) -> Any:
    spec = pkg.manage.get(key) or {}
    return spec.get("values", spec.get("value"))


def _enabled_plugins_union(packages: list[RequirementPackage]) -> dict[str, bool]:
    """Union of ``manage.copilot.settings.values.enabledPlugins`` across packages."""
    union: dict[str, bool] = {}
    for pkg in packages:
        values = _managed_values(pkg, "copilot.settings") or {}
        for name, on in (values.get("enabledPlugins") or {}).items():
            base = str(name).split("@", 1)[0]
            # An explicit ``false`` anywhere is recorded so the floor check can catch it.
            union[base] = bool(on) and union.get(base, True)
        for name in pkg.bootstrap_floor.get("plugins") or []:
            union.setdefault(str(name).split("@", 1)[0], True)
    return union


def _marketplace_union(packages: list[RequirementPackage]) -> set[str]:
    out: set[str] = set()
    for pkg in packages:
        values = _managed_values(pkg, "copilot.settings") or {}
        out.update((values.get("extraKnownMarketplaces") or {}).keys())
        out.update(pkg.bootstrap_floor.get("marketplaces") or [])
    return out


def check_scalar_conflicts(packages: list[RequirementPackage]) -> list[Finding]:
    """Detect cross-package ``enforce`` disagreements (value-shape rule).

    The disposition is declared per *surface* (``copilot.settings``), but the
    value-shape rule applies per *leaf setting* inside its ``values`` (``model``
    is a scalar singleton; ``enabledPlugins`` is a map). So we descend into
    ``values``: scalar leaves are the conflict domain (differing values across
    packages => error); a map/list leaf under an ``enforce`` surface is a shape
    advisory (it belongs under ``ensure-present`` union).
    """
    findings: list[Finding] = []
    enforced_scalar: dict[str, list[tuple[str, Any]]] = {}
    map_leaf_owners: dict[str, set[str]] = {}

    for pkg in packages:
        for key, spec in pkg.manage.items():
            if spec.get("disposition") != "enforce":
                continue
            values = spec.get("values", spec.get("value"))
            leaves = values.items() if isinstance(values, dict) else [(None, values)]
            for leaf, val in leaves:
                leaf_key = key if leaf is None else f"{key}.{leaf}"
                if isinstance(val, _SCALAR):
                    enforced_scalar.setdefault(leaf_key, []).append((pkg.name, val))
                elif val is not None:
                    map_leaf_owners.setdefault(leaf_key, set()).add(pkg.name)

    for leaf_key, owners in sorted(map_leaf_owners.items()):
        findings.append(
            Finding(
                "advisory",
                "shape-mismatch",
                f"'{leaf_key}' is enforced with a map/list value by "
                f"{', '.join(sorted(owners))}; map/list keys should be "
                f"'ensure-present' (union), not 'enforce'.",
            )
        )
    for leaf_key, entries in sorted(enforced_scalar.items()):
        if len({v for _, v in entries}) > 1:
            detail = "; ".join(f"{name}={value!r}" for name, value in entries)
            findings.append(
                Finding(
                    "error",
                    "enforce-conflict",
                    f"'{leaf_key}' is enforced to conflicting scalar values "
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


def validate(packages: list[RequirementPackage]) -> list[Finding]:
    """Run every manifest-only validation rule over the resolved package union."""
    findings: list[Finding] = []
    findings.extend(check_scalar_conflicts(packages))
    findings.extend(check_bootstrap_floor(packages))
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
