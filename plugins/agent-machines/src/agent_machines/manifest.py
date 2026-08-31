"""Requirement-package manifest parsing, validation, and per-machine layering.

A **requirement package** is one YAML file under a repo's
``.agent-machines/all/`` or ``.agent-machines/machines/<machine>/``. It declares
desired machine state as a set of
``manage`` entries, each governed by a **disposition** (see ``DISPOSITIONS``).
The plugin defines this schema; each repo supplies the data.

Layering is *within a repo*: a package's ``per-machine.<machine>`` block is a
partial ``manage``-shaped override that is deep-merged onto the base ``manage``
(a ``null`` leaf unsets a key). Resolution is **layer-within-repo first**, then
the engine unions resolved packages across repos (see ``discover``/``reconcile``)
-- so the union input is deterministic and the drift key is reproducible.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Current requirement-package schema version. Bumped only by a deliberate,
#: fixture-guarded migration (see docs/patterns/config-schema-migration.md).
SCHEMA_VERSION = 1

#: The seven dispositions that govern a managed key.
DISPOSITIONS = (
    "enforce",           # manifest is authoritative; overwrite live
    "ensure-present",    # manifest is a floor; live additions preserved, never revoked
    "capture-only",      # observe live, never write; promotable via `capture`
    "ignore",            # default; the manifest is an allowlist -- only declared keys touched
    "exclude",           # hard secret guard; `capture` must never serialize
    "prune",             # opt-in, never-during-reconcile GC of dead entries
    "prerequisite-check",  # assert a prerequisite is satisfied; never store/apply a secret
)

#: The stack-critical plugins/marketplaces the bootstrap-floor assertion protects
#: (a package may add to, but never remove from, the union of these).
BOOTSTRAP_CRITICAL_PLUGINS = ("agent-worktrees", "agent-machines")
BOOTSTRAP_CRITICAL_MARKETPLACES = ("copilot-extensions",)

#: Declarative-resource types the schema recognizes. All four are fully
#: handled today -- ``package``, ``file`` (whole-file and managed-block),
#: ``registry`` (Windows), ``feature`` (Windows optional features /
#: capabilities and Linux/WSL units), and ``power-setting`` (Windows power
#: schemes). See ``resources.py`` for the handlers.
KNOWN_RESOURCE_TYPES = ("package", "file", "registry", "feature", "power-setting")

#: Minimal required identity fields per resource type (checked at load).
REQUIRED_FIELDS = {
    "package": ("id", "manager"),
    "file": ("path",),
    "registry": ("path",),
    "feature": ("id", "manager"),
    "power-setting": ("subgroup", "setting"),
}

#: Accepted values for a resource's ``state`` / ``strategy`` selectors.
RESOURCE_STATES = ("present", "absent")
RESOURCE_STRATEGIES = ("enforce", "ensure-present", "managed-block")

#: Registry value types accepted by the ``registry`` resource (friendly names
#: mapped to ``reg.exe`` ``REG_*`` types in ``resources.py``). Kept here for
#: load-time validation; ``manifest`` must not import ``resources`` (cycle).
REGISTRY_VALUE_TYPES = (
    "String", "ExpandString", "MultiString", "DWord", "QWord", "Binary",
)

#: Feature managers accepted by the ``feature`` resource. Mirrors the
#: ``FEATURE_MANAGERS`` table in ``resources.py`` (duplicated to avoid a cycle).
FEATURE_MANAGER_NAMES = (
    "windows-optional-feature", "windows-capability", "linux-systemd",
)

#: Friendly values accepted by the Windows ``power-setting`` resource. Numeric
#: values remain available for settings whose index is not one of these actions.
POWER_SETTING_SYMBOLS = {
    "do-nothing": 0,
    "sleep": 1,
    "hibernate": 2,
    "shut-down": 3,
    "turn-off-display": 4,
}

#: Stable aliases used by the documented power-setting examples. Canonical
#: identity prevents an alias and its GUID from bypassing collision detection.
POWER_GUID_ALIASES = {
    "scheme_balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "scheme_min": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "scheme_max": "a1841308-3541-4fab-bc81-f71556f20b4a",
    "sub_buttons": "4f971e89-eebd-4455-a8de-9e59040e7347",
    "lidaction": "5ca83367-6e45-459f-a27b-476b1d01c936",
    "pbuttonaction": "7648efa3-dd9c-4e3e-b566-50f929386280",
}

POWER_SETTING_ALLOWED_VALUES = {
    POWER_GUID_ALIASES["lidaction"]: frozenset(range(4)),
    POWER_GUID_ALIASES["pbuttonaction"]: frozenset(range(5)),
}


def canonical_power_token(value: Any) -> str:
    folded = str(value).casefold()
    return POWER_GUID_ALIASES.get(folded, folded)


def normalize_power_setting_value(value: Any) -> int:
    if isinstance(value, str) and value in POWER_SETTING_SYMBOLS:
        return POWER_SETTING_SYMBOLS[value]
    return int(value, 0) if isinstance(value, str) else int(value)


class ManifestError(ValueError):
    """A requirement package is malformed or uses an unsupported schema."""


@dataclass
class RequirementPackage:
    """One parsed requirement-package file."""

    name: str
    schema_version: int
    manage: dict[str, dict[str, Any]]
    gate: list[str] = field(default_factory=list)
    aliases: dict[str, Any] = field(default_factory=dict)
    per_machine: dict[str, Any] = field(default_factory=dict)
    bootstrap_floor: dict[str, Any] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)
    modules: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    source_repo: str = ""
    source_path: Path | None = None
    source_anchor: Path | None = None

    def applies_to(self, machine: str) -> bool:
        """True when this package targets ``machine`` (empty/``*`` gate = all).

        The gate match is **case-insensitive**: ``current_machine()`` returns
        ``platform.node()``, whose casing is the OS hostname's (e.g.
        ``Anomalous-Potato``/``Emancipation-Cube`` on Windows), while manifests conventionally
        list gates in lowercase. Hostnames are case-insensitive, so comparing
        case-sensitively would silently exclude a machine from its own package.
        """
        if not self.gate or "*" in self.gate:
            return True
        return machine.lower() in {g.lower() for g in self.gate}

    def repo_root(self) -> Path | None:
        """Derive the repo root from a canonical or legacy package path."""
        if self.source_path is None:
            return None
        for parent in self.source_path.absolute().parents:
            if parent.name == ".agent-machines":
                return parent.parent
            if parent.name == "machine-state" and parent.parent.name == ".github":
                return parent.parent.parent
        return None

    def repo_anchor(self) -> Path | None:
        """Return the canonical checkout used for repository location classes."""
        anchor = self.source_anchor or self.repo_root()
        return anchor.expanduser().resolve() if anchor is not None else None


def _require(mapping: dict[str, Any], key: str, path: Path) -> Any:
    if key not in mapping:
        raise ManifestError(f"{path}: missing required key '{key}'")
    return mapping[key]


def load_package(
    path: Path,
    source_repo: str = "",
    source_anchor: Path | None = None,
) -> RequirementPackage:
    """Parse and validate a requirement-package YAML file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough of parser detail
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: top-level document must be a mapping")

    schema = _require(raw, "schema_version", path)
    if schema != SCHEMA_VERSION:
        raise ManifestError(
            f"{path}: unsupported schema_version {schema!r} (this engine speaks {SCHEMA_VERSION})"
        )

    name = _require(raw, "package", path)
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{path}: 'package' must be a non-empty string")

    manage = raw.get("manage") or {}
    if not isinstance(manage, dict):
        raise ManifestError(f"{path}: 'manage' must be a mapping")
    for key, spec in manage.items():
        if not isinstance(spec, dict):
            raise ManifestError(f"{path}: manage.{key} must be a mapping")
        disp = spec.get("disposition", "ignore")
        if disp not in DISPOSITIONS:
            raise ManifestError(
                f"{path}: manage.{key}.disposition {disp!r} is not one of {DISPOSITIONS}"
            )

    gate = raw.get("gate") or []
    if not isinstance(gate, list):
        raise ManifestError(f"{path}: 'gate' must be a list of machine names")

    modules = raw.get("modules") or []
    if not isinstance(modules, list):
        raise ManifestError(f"{path}: 'modules' must be a list")
    for mod in modules:
        if not isinstance(mod, dict) or not mod.get("name"):
            raise ManifestError(f"{path}: each module must be a mapping with a 'name'")

    resources = raw.get("resources") or []
    if not isinstance(resources, list):
        raise ManifestError(f"{path}: 'resources' must be a list")
    for res in resources:
        if not isinstance(res, dict):
            raise ManifestError(f"{path}: each resource must be a mapping")
        rtype = res.get("type")
        if rtype not in KNOWN_RESOURCE_TYPES:
            raise ManifestError(
                f"{path}: resource type {rtype!r} is not one of {KNOWN_RESOURCE_TYPES}"
            )
        for req_key in REQUIRED_FIELDS.get(rtype, ()):
            if not res.get(req_key):
                raise ManifestError(
                    f"{path}: resource type {rtype!r} is missing required field '{req_key}'"
                )
        state = res.get("state")
        if state is not None and state not in RESOURCE_STATES:
            raise ManifestError(
                f"{path}: resource state {state!r} must be one of {RESOURCE_STATES}"
            )
        strategy = res.get("strategy")
        if strategy is not None and strategy not in RESOURCE_STRATEGIES:
            raise ManifestError(
                f"{path}: resource strategy {strategy!r} must be one of {RESOURCE_STRATEGIES}"
            )
        if strategy == "managed-block":
            if rtype != "file":
                raise ManifestError(
                    f"{path}: strategy 'managed-block' is only valid for file resources"
                )
            if not res.get("block"):
                raise ManifestError(
                    f"{path}: file resource with strategy 'managed-block' requires a "
                    f"non-empty 'block' identity"
                )
            if res.get("format") == "json":
                raise ManifestError(
                    f"{path}: managed-block is text-only; 'format: json' is not allowed"
                )
        if rtype == "registry":
            vtype = res.get("value_type")
            if vtype is not None and vtype not in REGISTRY_VALUE_TYPES:
                raise ManifestError(
                    f"{path}: registry value_type {vtype!r} must be one of "
                    f"{REGISTRY_VALUE_TYPES}"
                )
        if rtype == "feature":
            mgr = res.get("manager")
            if mgr not in FEATURE_MANAGER_NAMES:
                raise ManifestError(
                    f"{path}: feature manager {mgr!r} must be one of {FEATURE_MANAGER_NAMES}"
                )
        if rtype == "power-setting":
            if "state" in res:
                raise ManifestError(
                    f"{path}: power-setting resource does not support 'state'; "
                    "declare the desired AC/DC indexes instead"
                )
            if "ac" not in res and "dc" not in res:
                raise ManifestError(
                    f"{path}: power-setting resource requires at least one of 'ac' or 'dc'"
                )
            for power_source in ("ac", "dc"):
                if power_source not in res:
                    continue
                value = res[power_source]
                valid = (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 0xFFFFFFFF
                )
                if isinstance(value, str):
                    valid = value in POWER_SETTING_SYMBOLS
                    if not valid:
                        try:
                            parsed = int(value, 0)
                            valid = 0 <= parsed <= 0xFFFFFFFF
                        except ValueError:
                            valid = False
                if not valid:
                    accepted = ", ".join(POWER_SETTING_SYMBOLS)
                    raise ManifestError(
                        f"{path}: power-setting {power_source!r} value {value!r} "
                        f"must be an unsigned integer or one of: {accepted}"
                    )
                normalized = normalize_power_setting_value(value)
                setting = canonical_power_token(res["setting"])
                allowed = POWER_SETTING_ALLOWED_VALUES.get(setting)
                if allowed is not None and normalized not in allowed:
                    raise ManifestError(
                        f"{path}: power-setting {power_source!r} value {value!r} "
                        f"is not supported by setting {res['setting']!r}; "
                        f"allowed indexes are {sorted(allowed)}"
                    )
        process_guard = res.get("process_guard")
        if process_guard is not None:
            if rtype != "package":
                raise ManifestError(
                    f"{path}: process_guard is only valid for package resources"
                )
            if not isinstance(process_guard, dict):
                raise ManifestError(
                    f"{path}: package process_guard must be a mapping"
                )
            unknown = sorted(set(process_guard) - {"names"})
            if unknown:
                raise ManifestError(
                    f"{path}: package process_guard has unsupported fields {unknown}"
                )
            names = process_guard.get("names")
            if (
                not isinstance(names, list)
                or not names
                or any(not isinstance(name, str) or not name.strip() for name in names)
            ):
                raise ManifestError(
                    f"{path}: package process_guard.names must be a non-empty list "
                    f"of process names"
                )

    return RequirementPackage(
        name=name,
        schema_version=schema,
        manage=manage,
        gate=[str(g) for g in gate],
        aliases=raw.get("aliases") or {},
        per_machine=raw.get("per-machine") or raw.get("per_machine") or {},
        bootstrap_floor=raw.get("bootstrap-floor") or raw.get("bootstrap_floor") or {},
        exclude=list(raw.get("exclude") or []),
        modules=modules,
        resources=resources,
        source_repo=source_repo,
        source_path=path,
        source_anchor=source_anchor,
    )


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto a copy of ``base``; a ``None`` leaf unsets."""
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if val is None:
            out.pop(key, None)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def resolve_for_machine(pkg: RequirementPackage, machine: str) -> RequirementPackage:
    """Return a copy of ``pkg`` with its ``per-machine.<machine>`` layer applied.

    The per-machine block is a partial ``manage``-shaped override deep-merged onto
    the base ``manage`` (``null`` leaves unset). Everything else is carried through
    unchanged. This is the *layer-within-repo* step that must precede any
    cross-repo union.
    """
    overlay = pkg.per_machine.get(machine) or {}
    manage_overlay = overlay.get("manage", overlay) if isinstance(overlay, dict) else {}
    if not isinstance(manage_overlay, dict):
        manage_overlay = {}
    resolved_manage = _deep_merge(pkg.manage, manage_overlay)
    return RequirementPackage(
        name=pkg.name,
        schema_version=pkg.schema_version,
        manage=resolved_manage,
        gate=list(pkg.gate),
        aliases=copy.deepcopy(pkg.aliases),
        per_machine={},
        bootstrap_floor=copy.deepcopy(pkg.bootstrap_floor),
        exclude=list(pkg.exclude),
        modules=copy.deepcopy(pkg.modules),
        resources=copy.deepcopy(pkg.resources),
        source_repo=pkg.source_repo,
        source_path=pkg.source_path,
        source_anchor=pkg.source_anchor,
    )
