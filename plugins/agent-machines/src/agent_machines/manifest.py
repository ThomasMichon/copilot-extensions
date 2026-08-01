"""Requirement-package manifest parsing, validation, and per-machine layering.

A **requirement package** is one YAML file under a repo's
``.github/machine-state/``. It declares desired machine state as a set of
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
    source_repo: str = ""
    source_path: Path | None = None

    def applies_to(self, machine: str) -> bool:
        """True when this package targets ``machine`` (empty/``*`` gate = all)."""
        if not self.gate or "*" in self.gate:
            return True
        return machine in self.gate


def _require(mapping: dict[str, Any], key: str, path: Path) -> Any:
    if key not in mapping:
        raise ManifestError(f"{path}: missing required key '{key}'")
    return mapping[key]


def load_package(path: Path, source_repo: str = "") -> RequirementPackage:
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

    return RequirementPackage(
        name=name,
        schema_version=schema,
        manage=manage,
        gate=[str(g) for g in gate],
        aliases=raw.get("aliases") or {},
        per_machine=raw.get("per-machine") or raw.get("per_machine") or {},
        bootstrap_floor=raw.get("bootstrap-floor") or raw.get("bootstrap_floor") or {},
        exclude=list(raw.get("exclude") or []),
        source_repo=source_repo,
        source_path=path,
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
        source_repo=pkg.source_repo,
        source_path=pkg.source_path,
    )
