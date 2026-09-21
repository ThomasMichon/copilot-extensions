"""Per-plugin troubleshooting-category coverage registry.

A plugin may ship a ``troubleshooting-index.json`` at its payload root,
alongside (but independent of) ``instruction-projections.json``. It declares
the "what do I do when X happens" failure-mode categories that plugin claims
to own an ambient answer for -- e.g. ``agent-worktrees`` claiming
``claims-ledger`` and ``resource-obligations``.

This module validates that declaration and checks it against the plugin's own
*static* instruction projections (never against a consumer repo's synced
copies, never against skills): every declared category must have at least one
of its ``markers`` strings actually present in the rendered body of one of the
plugin's declared projection templates. A category that is claimed but not
backed by any ambient pointer is exactly the failure mode
``efforts/active/ambient-guidance-navigability`` calls a "dead end" -- content
that exists only behind a skill trigger an agent has no reason to phrase-match
into.

This module is deliberately independent of ``instruction_projections.py``'s
own (much larger) validation/render/sync machinery -- it only needs to read
already-declared projection templates, never render, sync, or lock them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DECLARATION_SCHEMA = "copilot-extensions.troubleshooting-index"
DECLARATION_VERSION = 1
DECLARATION_FILE = "troubleshooting-index.json"

# instruction_projections.py's own declaration file/schema names, duplicated
# here as plain strings (not imported) to keep this module load-independent
# of that one -- both may load a plugin's instruction-projections.json, but
# neither depends on the other's internals.
PROJECTIONS_FILE = "instruction-projections.json"

MAX_CATEGORIES = 32
MAX_MARKERS_PER_CATEGORY = 8


class TroubleshootingIndexError(ValueError):
    """Raised for a malformed troubleshooting-index.json declaration."""


@dataclass(frozen=True)
class Category:
    id: str
    summary: str
    pointer: str
    markers: tuple[str, ...]


def _require_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TroubleshootingIndexError(
            f"{field_name} must be a non-empty string"
        )
    return value


def load_declaration(path: Path) -> list[Category]:
    """Load and validate a troubleshooting-index.json declaration.

    Raises :class:`TroubleshootingIndexError` on any malformed input --
    validation fails closed rather than skipping bad entries.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TroubleshootingIndexError(f"{path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise TroubleshootingIndexError(f"{path}: declaration must be an object")
    if raw.get("schema") != DECLARATION_SCHEMA:
        raise TroubleshootingIndexError(
            f"{path}: schema must be {DECLARATION_SCHEMA!r}"
        )
    if raw.get("version") != DECLARATION_VERSION:
        raise TroubleshootingIndexError(
            f"{path}: version must be {DECLARATION_VERSION!r}"
        )

    categories_raw = raw.get("categories")
    if not isinstance(categories_raw, list) or not categories_raw:
        raise TroubleshootingIndexError(
            f"{path}: categories must be a non-empty array"
        )
    if len(categories_raw) > MAX_CATEGORIES:
        raise TroubleshootingIndexError(
            f"{path}: categories exceeds max of {MAX_CATEGORIES}"
        )

    seen_ids: set[str] = set()
    categories: list[Category] = []
    for entry in categories_raw:
        if not isinstance(entry, dict):
            raise TroubleshootingIndexError(f"{path}: each category must be an object")
        category_id = _require_str(entry.get("id"), field_name="category id")
        if category_id in seen_ids:
            raise TroubleshootingIndexError(f"{path}: duplicate category id {category_id!r}")
        seen_ids.add(category_id)
        summary = _require_str(entry.get("summary"), field_name=f"{category_id}.summary")
        pointer = _require_str(entry.get("pointer"), field_name=f"{category_id}.pointer")
        markers_raw = entry.get("markers")
        if not isinstance(markers_raw, list) or not markers_raw:
            raise TroubleshootingIndexError(
                f"{path}: {category_id}.markers must be a non-empty array"
            )
        if len(markers_raw) > MAX_MARKERS_PER_CATEGORY:
            raise TroubleshootingIndexError(
                f"{path}: {category_id}.markers exceeds max of "
                f"{MAX_MARKERS_PER_CATEGORY}"
            )
        markers = tuple(
            _require_str(marker, field_name=f"{category_id}.markers[]")
            for marker in markers_raw
        )
        categories.append(
            Category(id=category_id, summary=summary, pointer=pointer, markers=markers)
        )

    return categories


def _projection_template_bodies(plugin_root: Path) -> list[str]:
    """Read the rendered body of every static projection template a plugin
    declares in its own ``instruction-projections.json``.

    Returns an empty list if the plugin declares no static projections at
    all (or the declaration/template is unreadable) -- coverage checking
    treats that the same as "no ambient content backs this category", which
    is the fail-closed behavior the guard test relies on.
    """
    declaration_path = plugin_root / PROJECTIONS_FILE
    if not declaration_path.is_file():
        return []
    try:
        raw = json.loads(declaration_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    projections = raw.get("projections")
    if not isinstance(projections, list):
        return []

    bodies: list[str] = []
    for projection in projections:
        if not isinstance(projection, dict):
            continue
        template = projection.get("template")
        if not isinstance(template, str):
            continue
        template_path = plugin_root / template
        if not template_path.is_file():
            continue
        try:
            bodies.append(template_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return bodies


def uncovered_categories(plugin_root: Path) -> list[Category]:
    """Return every category a plugin's troubleshooting-index.json declares
    that has no marker present in any of that plugin's static projection
    templates.

    Returns an empty list if the plugin has no troubleshooting-index.json at
    all -- the registry is opt-in, not mandatory. Fails closed the moment a
    plugin *does* opt in: any category with zero matching markers is
    reported, never silently dropped.
    """
    declaration_path = plugin_root / DECLARATION_FILE
    if not declaration_path.is_file():
        return []
    categories = load_declaration(declaration_path)
    bodies = _projection_template_bodies(plugin_root)
    combined = "\n".join(bodies)

    uncovered: list[Category] = []
    for category in categories:
        if not any(marker in combined for marker in category.markers):
            uncovered.append(category)
    return uncovered


def iter_plugin_roots(plugins_dir: Path) -> Iterable[Path]:
    if not plugins_dir.is_dir():
        return []
    return sorted(p for p in plugins_dir.iterdir() if p.is_dir())
