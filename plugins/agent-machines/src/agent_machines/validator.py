"""The conflict validator.

Multiple harness repos on one machine may declare overlapping desired state.
Schema-v4 authority resolves a disagreement only when the highest authority is
unique in value and shape. Equal-highest contradictions remain fail-loud.

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
from .authority import contributor, effective_authority, sort_decisions
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
PLUGIN_TOMBSTONE_GROUP = "copilot.settings.plugin-tombstones"


@dataclass
class Finding:
    """One validator result. ``level`` is ``error``, ``advisory``, or ``info``."""

    level: str
    code: str
    message: str


@dataclass(frozen=True)
class _SettingCandidate:
    package: RequirementPackage
    manage_key: str
    spec: dict[str, Any]
    value: Any

    @property
    def authority(self) -> int:
        return effective_authority(self.package, self.spec)

    @property
    def label(self) -> str:
        return self.package.name

    @property
    def provenance(self) -> dict[str, Any]:
        return contributor(self.package, self.spec)


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


def _candidate_key(candidate: _SettingCandidate) -> tuple[str, str, str]:
    return (
        candidate.package.source_repo,
        candidate.package.name,
        candidate.manage_key,
    )


def _semantic_key(value: Any) -> tuple[str, str]:
    """Compare JSON-shaped settings independently of mapping insertion order."""
    return (
        type(value).__name__,
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    )


def _authority_resolution(
    path: tuple[str, ...],
    candidates: list[_SettingCandidate],
    semantic_value,
) -> tuple[list[_SettingCandidate], list[_SettingCandidate], bool]:
    highest = max(candidate.authority for candidate in candidates)
    selected = sorted(
        [candidate for candidate in candidates if candidate.authority == highest],
        key=_candidate_key,
    )
    values = {_semantic_key(semantic_value(candidate)) for candidate in selected}
    if len(values) > 1:
        return selected, [], True
    selected_value = next(iter(values))
    superseded = sorted(
        [
            candidate
            for candidate in candidates
            if candidate.authority < highest
            and _semantic_key(semantic_value(candidate)) != selected_value
        ],
        key=_candidate_key,
    )
    return selected, superseded, False


def _settings_authority_analysis(
    packages: list[RequirementPackage],
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Detect cross-package ``enforce`` disagreements (value-shape rule).

    The disposition is declared per *surface* (``copilot.settings``), but the
    value-shape rule applies per *leaf setting* inside its ``values``. We recurse
    through dictionaries because the settings surface deep-merges them: nested
    scalar leaves are the conflict domain (differing values across packages =>
    error). A list or opaque collection leaf under an ``enforce`` surface is a
    shape advisory (it belongs under ``ensure-present`` union).
    """
    findings: list[Finding] = []
    enforced_scalar: dict[tuple[str, ...], list[_SettingCandidate]] = {}
    collection_leaves: dict[tuple[str, ...], list[_SettingCandidate]] = {}
    enforced_shapes: dict[tuple[str, ...], list[_SettingCandidate]] = {}
    decisions: list[dict[str, Any]] = []

    for pkg in packages:
        for key, spec in pkg.manage.items():
            if spec.get("disposition") != "enforce":
                continue
            if key != "copilot.settings" and not key.startswith("copilot.settings."):
                continue
            values = spec.get("values", spec.get("value"))
            if values is None:
                continue
            if key == PLUGIN_TOMBSTONE_GROUP:
                enabled_plugins = (
                    values.get("enabledPlugins")
                    if isinstance(values, dict)
                    else None
                )
                if isinstance(enabled_plugins, dict):
                    for plugin, enabled in enabled_plugins.items():
                        leaf_key = (
                            "copilot.settings",
                            "enabledPlugins",
                            str(plugin),
                        )
                        candidate = _SettingCandidate(pkg, key, spec, enabled)
                        enforced_shapes.setdefault(leaf_key, []).append(candidate)
                        if isinstance(enabled, _SCALAR):
                            enforced_scalar.setdefault(leaf_key, []).append(candidate)
                continue
            # copilot.settings.* suffixes group contributions but do not change
            # their physical settings.json root.
            root = (
                "copilot.settings"
                if key == "copilot.settings" or key.startswith("copilot.settings.")
                else key
            )
            for leaf_key, val in _enforced_nodes((root,), values):
                candidate = _SettingCandidate(pkg, key, spec, val)
                enforced_shapes.setdefault(leaf_key, []).append(candidate)
                if isinstance(val, _SCALAR):
                    enforced_scalar.setdefault(leaf_key, []).append(candidate)
                elif (
                    val is not None
                    and (
                        not isinstance(val, dict)
                        or leaf_key in _OPAQUE_SETTING_MAPS
                    )
                ):
                    collection_leaves.setdefault(leaf_key, []).append(candidate)

    decision_paths: set[tuple[str, ...]] = set()
    for leaf_key, candidates in sorted(enforced_shapes.items()):
        shapes = {_shape(candidate.value) for candidate in candidates}
        if len(shapes) > 1:
            detail = "; ".join(
                f"{candidate.label}={_shape(candidate.value)}"
                for candidate in sorted(candidates, key=_candidate_key)
            )
            findings.append(
                Finding(
                    "error",
                    "enforce-shape-conflict",
                    f"'{_format_path(leaf_key)}' is enforced with incompatible value shapes "
                    f"across packages: {detail}",
                )
            )
    for leaf_key, candidates in sorted(collection_leaves.items()):
        selected, superseded, conflict = _authority_resolution(
            leaf_key, candidates, lambda candidate: candidate.value
        )
        highest = max(candidate.authority for candidate in candidates)
        owners = {
            candidate.label
            for candidate in candidates
            if candidate.authority == highest
        }
        findings.append(
            Finding(
                "advisory",
                "shape-mismatch",
                f"'{_format_path(leaf_key)}' is enforced with a list/collection value by "
                f"{', '.join(sorted(owners))}; collection keys should be "
                f"'ensure-present' (union), not 'enforce'.",
            )
        )
        if superseded and not conflict:
            decisions.append({
                "domain": "settings",
                "identity": _format_path(leaf_key),
                "selected": [candidate.provenance for candidate in selected],
                "superseded": [candidate.provenance for candidate in superseded],
            })
            findings.append(Finding(
                "info",
                "authority-supersession",
                f"'{_format_path(leaf_key)}' uses authority "
                f"{selected[0].authority} from "
                f"{', '.join(candidate.label for candidate in selected)} over "
                f"{', '.join(candidate.label for candidate in superseded)}",
            ))
    for leaf_key, candidates in sorted(enforced_scalar.items()):
        selected, superseded, conflict = _authority_resolution(
            leaf_key, candidates, lambda candidate: candidate.value
        )
        if conflict:
            detail = "; ".join(
                f"{candidate.label}={candidate.value!r}"
                for candidate in selected
            )
            findings.append(
                Finding(
                    "error",
                    "enforce-conflict",
                    f"'{_format_path(leaf_key)}' is enforced to conflicting scalar values "
                    f"across packages: {detail}",
                )
            )
        elif superseded:
            decision = {
                "domain": "settings",
                "identity": _format_path(leaf_key),
                "selected": [candidate.provenance for candidate in selected],
                "superseded": [candidate.provenance for candidate in superseded],
            }
            if leaf_key in decision_paths:
                decisions = [
                    existing
                    for existing in decisions
                    if not (
                        existing["domain"] == "settings"
                        and existing["identity"] == decision["identity"]
                    )
                ]
                findings = [
                    finding
                    for finding in findings
                    if not (
                        finding.code == "authority-supersession"
                        and f"'{decision['identity']}'" in finding.message
                    )
                ]
            decisions.append(decision)
            findings.append(Finding(
                "info",
                "authority-supersession",
                f"'{_format_path(leaf_key)}' uses authority "
                f"{selected[0].authority} from "
                f"{', '.join(candidate.label for candidate in selected)} over "
                f"{', '.join(candidate.label for candidate in superseded)}",
            ))
    return findings, sort_decisions(decisions)


def check_scalar_conflicts(packages: list[RequirementPackage]) -> list[Finding]:
    """Detect authority-aware cross-package ``enforce`` disagreements."""
    return _settings_authority_analysis(packages)[0]


def settings_authority_decisions(
    packages: list[RequirementPackage],
) -> list[dict[str, Any]]:
    """Return stable settings selections that supersede lower authority."""
    return _settings_authority_analysis(packages)[1]


def check_plugin_tombstone_group(
    packages: list[RequirementPackage],
) -> list[Finding]:
    """Validate the backward-compatible false-only plugin tombstone group."""
    findings: list[Finding] = []
    for pkg in packages:
        spec = pkg.manage.get(PLUGIN_TOMBSTONE_GROUP)
        if spec is None:
            continue
        values = spec.get("values", spec.get("value"))
        enabled_plugins = (
            values.get("enabledPlugins")
            if isinstance(values, dict) and set(values) == {"enabledPlugins"}
            else None
        )
        valid = (
            spec.get("disposition") == "enforce"
            and isinstance(enabled_plugins, dict)
            and all(
                isinstance(name, str)
                and bool(name)
                and enabled is False
                for name, enabled in enabled_plugins.items()
            )
        )
        if not valid:
            findings.append(
                Finding(
                    "error",
                    "plugin-tombstone-contract",
                    f"package '{pkg.name}' must declare "
                    f"'{PLUGIN_TOMBSTONE_GROUP}' as an enforce contribution "
                    "containing only enabledPlugins.<plugin>: false entries",
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


def check_plugin_activation_removals(
    packages: list[RequirementPackage],
) -> list[Finding]:
    """Validate composable desired-absence declarations."""
    findings: list[Finding] = []
    removals: dict[str, set[str]] = {}
    declarations: dict[str, set[str]] = {}
    protected = set(BOOTSTRAP_CRITICAL_PLUGINS)

    for pkg in packages:
        protected.update(
            str(name).split("@", 1)[0]
            for name in pkg.bootstrap_floor.get("plugins") or []
        )
        for key, spec in pkg.manage.items():
            disposition = spec.get("disposition")
            if disposition == "ensure-absent":
                for identity in spec.get("keys", {}).get("enabledPlugins", []):
                    removals.setdefault(str(identity), set()).add(pkg.name)
                continue
            if (
                key == "copilot.settings"
                or key.startswith("copilot.settings.")
            ) and disposition in {"enforce", "ensure-present"}:
                values = spec.get("values", spec.get("value"))
                enabled = values.get("enabledPlugins") if isinstance(values, dict) else None
                if isinstance(enabled, dict):
                    for identity in enabled:
                        declarations.setdefault(str(identity), set()).add(pkg.name)

    for identity, owners in sorted(removals.items()):
        base = identity.split("@", 1)[0]
        if base in protected:
            findings.append(
                Finding(
                    "error",
                    "bootstrap-floor",
                    f"packages {', '.join(sorted(owners))} remove bootstrap-protected "
                    f"plugin '{identity}'",
                )
            )
        conflicting = declarations.get(identity)
        if conflicting:
            findings.append(
                Finding(
                    "error",
                    "plugin-activation-conflict",
                    f"plugin '{identity}' is declared both ensure-absent by "
                    f"{', '.join(sorted(owners))} and value-managed by "
                    f"{', '.join(sorted(conflicting))}",
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
        for key, spec in pkg.manage.items():
            if (
                key != "copilot.settings"
                and not key.startswith("copilot.settings.")
            ) or spec.get("disposition") != "ensure-present":
                continue
            values = spec.get("values", spec.get("value"))
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
    findings.extend(check_plugin_tombstone_group(packages))
    findings.extend(check_plugin_tombstone_schema(packages))
    findings.extend(check_plugin_activation_removals(packages))
    findings.extend(check_bootstrap_floor(packages))
    findings.extend(check_resource_conflicts(packages, machine, plat))
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
