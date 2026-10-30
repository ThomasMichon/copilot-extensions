"""Plugin-shipped ``containers.yaml`` fleet fragments.

An active plugin may ship fleet definitions by declaring, in its own
``plugin.json``::

    "containersConfig": "<relative path to a containers.yaml fragment>"

Fragments are merged at the **lowest precedence**, beneath the single
``containers.yaml`` that :func:`agent_containers.config.load_config` selects
(``$AGENT_CONTAINERS_CONFIG`` / repo / cwd / machine-local / knowledge overlay).
Discovery reuses the shared identity-verified activation graph
(``plugin_activation.resolve_active_plugins``), the same mechanism that
agent-codespaces uses for ``codespaceConfig`` and agent-worktrees uses for
plugin-shipped ``related.yaml``.

Rules:

* A fragment contributes named ``fleets:`` entries only. ``schema_version`` is
  accepted and ignored; any other top-level key is ignored with a warning, so
  global defaults, relay, dotfiles and harness settings remain operator-owned.
* The operator's config overrides a plugin fleet **per key**: the fleet's
  mapping is deep-merged (nested mappings such as ``environment`` merge per
  key; lists and scalars replace). An explicit ``null`` clears a fragment value
  so it falls back to the default, exactly as if neither source had set it.
* When two plugins define the same fleet name, the plugin whose source
  (``name@marketplace``) sorts first wins and the other definition is ignored
  as a whole, with a warning. Definitions are never blended across plugins.
* A relative ``devcontainer_path`` (or, when the fragment sets no
  ``devcontainer_path``, a relative ``devcontainer_config``) resolves relative
  to the fragment file's directory.
* An unreadable or invalid fragment/declaration is skipped with a warning; it
  never fails the operator's own config load.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("agent-containers")

MANIFEST_FIELD = "containersConfig"
FRAGMENT_KEYS = frozenset({"fleets"})
IGNORED_FRAGMENT_KEYS = frozenset({"schema_version"})
CONFIG_SOURCE = "config"
PLUGIN_SOURCE_PREFIX = "plugin:"


@dataclass(frozen=True)
class PluginFragment:
    """One validated plugin-shipped fleet fragment."""

    plugin: str
    path: Path
    fleets: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{PLUGIN_SOURCE_PREFIX}{self.plugin}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "path": str(self.path),
            "fleets": list(self.fleets),
        }


def _active_plugin_roots() -> list[tuple[str, list[Path]]]:
    """Return ``(source, live roots)`` for every currently active plugin."""
    try:
        from plugin_activation import resolve_active_plugins
    except ImportError:
        log.debug("plugin_activation unavailable; skipping plugin fleet fragments")
        return []
    try:
        report = resolve_active_plugins()
    except (OSError, ValueError) as exc:
        log.debug("Could not resolve active plugins: %s", exc)
        return []
    return [
        (source, [selected.root for selected in plugin.live_roots])
        for source, plugin in sorted(report.active.items())
    ]


def _declared_fragment(source: str, root: Path) -> Path | None:
    """Return the fragment declared by ``root/plugin.json``, or None."""
    manifest = root / "plugin.json"
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("Ignoring %s from %s: unreadable plugin.json (%s)", MANIFEST_FIELD, source, exc)
        return None
    if not isinstance(raw, dict) or MANIFEST_FIELD not in raw:
        return None
    declared = raw[MANIFEST_FIELD]
    if not isinstance(declared, str) or not declared.strip():
        log.warning(
            "Ignoring %s from %s: must be a non-empty relative path", MANIFEST_FIELD, source
        )
        return None
    relative = Path(declared.strip())
    if relative.is_absolute():
        log.warning(
            "Ignoring %s from %s: %r must be relative to the plugin root",
            MANIFEST_FIELD, source, declared,
        )
        return None
    try:
        base = root.resolve()
        target = (root / relative).resolve()
        target.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        log.warning(
            "Ignoring %s from %s: %r escapes the plugin root", MANIFEST_FIELD, source, declared
        )
        return None
    if not target.is_file():
        log.warning(
            "Ignoring %s from %s: %s is not a file", MANIFEST_FIELD, source, target
        )
        return None
    return target


def _rebase(value: Any, base: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("~") or Path(value).is_absolute():
        return value
    return str(base / value)


def load_fragment(source: str, path: Path) -> PluginFragment | None:
    """Parse and validate one fragment file; warn and return None if unusable."""
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        log.warning("Ignoring containers fragment %s from %s: %s", path, source, exc)
        return None
    data = {} if parsed is None else parsed
    if not isinstance(data, dict):
        log.warning(
            "Ignoring containers fragment %s from %s: top level must be a mapping",
            path, source,
        )
        return None
    ignored = sorted(str(k) for k in data if k not in FRAGMENT_KEYS | IGNORED_FRAGMENT_KEYS)
    if ignored:
        log.warning(
            "Containers fragment %s from %s may only define 'fleets'; ignoring: %s",
            path, source, ", ".join(ignored),
        )
    raw_fleets = data.get("fleets") or {}
    if not isinstance(raw_fleets, dict):
        log.warning(
            "Ignoring containers fragment %s from %s: 'fleets' must be a mapping",
            path, source,
        )
        return None
    base = path.parent
    fleets: dict[str, dict[str, Any]] = {}
    for name, raw in raw_fleets.items():
        if not isinstance(raw, dict):
            log.warning(
                "Ignoring fleet %r in containers fragment %s from %s: must be a mapping",
                name, path, source,
            )
            continue
        fleet = copy.deepcopy(raw)
        if "devcontainer_path" in fleet:
            fleet["devcontainer_path"] = _rebase(fleet["devcontainer_path"], base)
        elif "devcontainer_config" in fleet:
            fleet["devcontainer_config"] = _rebase(fleet["devcontainer_config"], base)
        fleets[str(name)] = fleet
    return PluginFragment(plugin=source, path=path, fleets=fleets)


def discover_plugin_fragments(
    roots: Iterable[tuple[str, list[Path]]] | None = None,
) -> list[PluginFragment]:
    """Load fragments declared by active plugins, in deterministic source order."""
    fragments: list[PluginFragment] = []
    for source, live_roots in sorted(
        _active_plugin_roots() if roots is None else roots, key=lambda item: item[0]
    ):
        for root in live_roots:
            declared = _declared_fragment(source, root)
            if declared is None:
                continue
            fragment = load_fragment(source, declared)
            if fragment is not None:
                fragments.append(fragment)
            break
    return fragments


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` over ``base`` per key; ``None`` removes the key.

    Removing (rather than storing ``None``) makes a cleared key behave exactly
    as if neither source set it, so it falls back to the fleet default or the
    global setting instead of reaching type conversion as a literal null.
    """
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict):
            current = merged.get(key)
            merged[key] = _deep_merge(current if isinstance(current, dict) else {}, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _leaf_sources(mapping: dict[str, Any], label: str, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in mapping.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(_leaf_sources(value, label, f"{path}."))
        else:
            out[path] = label
    return out


def _merged_sources(
    merged: dict[str, Any], override: dict[str, Any], base_label: str, override_label: str
) -> dict[str, str]:
    overridden = _leaf_sources(override, override_label)
    sources = {
        key: override_label if key in overridden else base_label
        for key in _leaf_sources(merged, base_label)
    }
    # Keys cleared with an explicit null stay attributed to the override.
    sources.update({key: label for key, label in overridden.items() if key not in sources})
    return sources


def merge_fragments(
    data: dict[str, Any],
    fragments: list[PluginFragment],
    *,
    config_path: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge plugin fragments beneath ``data``; return ``(merged, provenance)``.

    ``data`` is the parsed operator config (may be empty). The returned
    provenance records the selected config file, every contributing fragment,
    and, per fleet, which source defined it and which source supplied each key
    (dotted paths for nested mappings).
    """
    config_label = f"{CONFIG_SOURCE}:{config_path}" if config_path else CONFIG_SOURCE
    plugin_fleets: dict[str, dict[str, Any]] = {}
    origins: dict[str, str] = {}
    for fragment in fragments:
        for name, fleet in fragment.fleets.items():
            if name in plugin_fleets:
                log.warning(
                    "Fleet %r is defined by both %s and %s; using %s",
                    name, origins[name], fragment.label, origins[name],
                )
                continue
            plugin_fleets[name] = fleet
            origins[name] = fragment.label

    provenance: dict[str, Any] = {
        "config_file": str(config_path) if config_path else None,
        "plugin_fragments": [fragment.to_dict() for fragment in fragments],
        "fleets": {},
    }
    user_fleets = data.get("fleets")
    if user_fleets is None:
        user_fleets = {}
    if not isinstance(user_fleets, dict):
        # Preserve existing invalid-config handling; plugin fleets cannot be
        # merged beneath a malformed operator ``fleets`` value.
        return data, provenance

    merged_fleets: dict[str, Any] = {}
    for name, override in user_fleets.items():
        base = plugin_fleets.get(name)
        if base is None:
            merged_fleets[name] = override
            entry = {"defined_by": config_label, "keys": {}}
            if isinstance(override, dict):
                entry["keys"] = _leaf_sources(override, config_label)
            provenance["fleets"][name] = entry
            continue
        keys = _leaf_sources(base, origins[name])
        if override is None:
            merged_fleets[name] = copy.deepcopy(base)
        elif isinstance(override, dict):
            merged_fleets[name] = _deep_merge(base, override)
            keys = _merged_sources(merged_fleets[name], override, origins[name], config_label)
        else:
            merged_fleets[name] = override
        provenance["fleets"][name] = {
            "defined_by": origins[name],
            "overridden_by": config_label if override is not None else None,
            "keys": keys,
        }
    for name, base in plugin_fleets.items():
        if name in merged_fleets:
            continue
        merged_fleets[name] = copy.deepcopy(base)
        provenance["fleets"][name] = {
            "defined_by": origins[name],
            "overridden_by": None,
            "keys": _leaf_sources(base, origins[name]),
        }

    merged = dict(data)
    if merged_fleets or "fleets" in data:
        merged["fleets"] = merged_fleets
    return merged, provenance



def add_subparsers(sub: Any) -> None:
    """Register ``config-sources`` on the agent-containers CLI."""
    parser = sub.add_parser(
        "config-sources",
        help="Show the selected containers.yaml, plugin-shipped fleet fragments, "
        "and which source supplied each fleet and key",
    )
    parser.add_argument("--json", action="store_true", help="Emit the provenance as JSON.")
    parser.set_defaults(func=cmd_config_sources)


def cmd_config_sources(args: Any) -> int:
    from .config import load_config

    provenance = load_config(strict=True).provenance
    if args.json:
        print(json.dumps(provenance, indent=2))
        return 0
    print(f"Config file: {provenance.get('config_file') or '(none)'}")
    fragments = provenance.get("plugin_fragments") or []
    print("Plugin fragments:" if fragments else "Plugin fragments: (none)")
    for fragment in fragments:
        fleets = ", ".join(fragment["fleets"]) or "-"
        print(f"  {fragment['plugin']}: {fragment['path']} (fleets: {fleets})")
    fleets = provenance.get("fleets") or {}
    print("Fleets:" if fleets else "Fleets: (none)")
    for name, entry in fleets.items():
        line = f"  {name}: defined by {entry['defined_by']}"
        if entry.get("overridden_by"):
            line += f", overridden by {entry['overridden_by']}"
        print(line)
        for key, source in entry.get("keys", {}).items():
            print(f"    {key}: {source}")
    return 0
