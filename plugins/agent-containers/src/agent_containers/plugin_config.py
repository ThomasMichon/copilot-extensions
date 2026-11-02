"""Plugin-shipped ``containers.yaml`` fleet fragments.

An active plugin may ship fleet definitions by declaring, in its own
``plugin.json``::

    "containersConfig": "<relative path to a containers.yaml fragment>"

Fragments form the **lowest** configuration layer, beneath every operator
source :func:`agent_containers.config.load_config` composes (the built-in
defaults plus the repository / machine / repository+machine tiers, or the
single compatibility file selected by ``$AGENT_CONTAINERS_CONFIG`` and the
legacy lookup). Discovery reuses the canonical identity-verified activation
resolver (``plugin_activation.resolve_active_plugins``) for the invoking
repository: machine-wide (global) activation plus that checkout's own
repository settings, which may also disable a globally enabled plugin.

Rules:

* A fragment contributes named ``fleets:`` entries only. ``schema_version`` is
  accepted and ignored; any other top-level key is ignored with a warning, so
  global defaults, relay, dotfiles and harness settings remain operator-owned.
* An empty operator fleet entry (``example:``, ``example: null`` or
  ``example: {}``) removes the plugin-shipped fleet entirely; a higher layer may
  define it again from scratch. Provenance records the removing layer at
  ``/fleets/<name>`` with ``removed: true``.
* Operator configuration overrides a plugin fleet **per key**: the fleet's
  mapping is deep-merged (nested mappings such as ``environment`` merge per
  key; lists and scalars replace). An explicit ``null`` clears a fragment value
  so it falls back to the default, exactly as if neither source had set it.
* When two plugins define the same fleet name, the plugin whose source
  (``name@marketplace``) sorts first wins and the other definition is ignored
  as a whole, with a warning. Definitions are never blended across plugins.
* Relative ``devcontainer_path``, ``host_workspace_path`` and
  ``host_home_path`` values (and ``devcontainer_config`` when the fragment sets
  no ``devcontainer_path``) resolve relative to the fragment file's directory,
  matching how composed operator documents resolve source-relative paths.
* An unreadable, invalid or cyclic fragment/declaration is skipped with a
  warning; it never fails the operator's own config load.

Provenance extends the composition's own ``configuration_provenance`` (JSON
pointer -> owning layer record) with ``tier: plugin`` owner records, and
prepends contributing fragment IDs to ``configuration_layers``.
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
PLUGIN_TIER = "plugin"
PLUGIN_SOURCE_PREFIX = "plugin:"
_SOURCE_RELATIVE_KEYS = ("devcontainer_path", "host_workspace_path", "host_home_path")


@dataclass(frozen=True)
class PluginFragment:
    """One validated plugin-shipped fleet fragment."""

    plugin: str
    path: Path
    fleets: dict[str, dict[str, Any]] = field(default_factory=dict)
    root: Path | None = None

    @property
    def label(self) -> str:
        return f"{PLUGIN_SOURCE_PREFIX}{self.plugin}"

    def owner(self) -> dict[str, Any]:
        """Return a provenance owner record shaped like composed layer owners."""
        record: dict[str, Any] = {
            "id": self.label, "tier": PLUGIN_TIER, "origin": PLUGIN_TIER,
            "plugin": self.plugin,
        }
        if self.root is not None:
            try:
                relative = self.path.relative_to(self.root.resolve()).as_posix()
            except ValueError:
                relative = None
            if relative:
                record["source"] = {"root": str(self.root.resolve()), "path": relative}
        return record

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "path": str(self.path),
            "fleets": list(self.fleets),
        }


def invoking_repository(start: Path | None = None) -> Path | None:
    """Return the checkout containing ``start`` (default: cwd), if any."""
    current = (start or Path.cwd()).absolute()
    for candidate in (current, *current.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            return None
    return None


def _active_plugin_roots(repository: Path | None = None) -> list[tuple[str, list[Path]]]:
    """Return ``(source, live roots)`` for plugins active for ``repository``.

    With a repository this is the canonical current-checkout resolution
    (global activation plus that checkout's settings, identity-verified);
    without one, only machine-wide activation applies.
    """
    try:
        from plugin_activation import resolve_active_plugins
    except ImportError:
        log.debug("plugin_activation unavailable; skipping plugin fleet fragments")
        return []
    try:
        if repository is not None:
            report = resolve_active_plugins(project_root=repository)
        else:
            report = resolve_active_plugins(include_projects=False)
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
    try:
        usable = target.is_file()
    except OSError as exc:
        log.warning("Ignoring %s from %s: %s (%s)", MANIFEST_FIELD, source, target, exc)
        return None
    if not usable:
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


def _cyclic(value: Any, ancestors: frozenset[int] = frozenset()) -> bool:
    """True when ``value`` contains a container that is its own ancestor.

    Shared (acyclic) YAML aliases revisit the same object along different
    paths and are allowed; only a genuine self-reference is a cycle.
    """
    if not isinstance(value, (dict, list)):
        return False
    marker = id(value)
    if marker in ancestors:
        return True
    nested = ancestors | {marker}
    children = value.values() if isinstance(value, dict) else value
    return any(_cyclic(child, nested) for child in children)


def load_fragment(
    source: str, path: Path, root: Path | None = None,
) -> PluginFragment | None:
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
    if _cyclic(data):
        log.warning(
            "Ignoring containers fragment %s from %s: it contains a self-referencing "
            "(cyclic) mapping or list",
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
        for key in _SOURCE_RELATIVE_KEYS:
            if key in fleet:
                fleet[key] = _rebase(fleet[key], base)
        if "devcontainer_path" not in fleet and "devcontainer_config" in fleet:
            fleet["devcontainer_config"] = _rebase(fleet["devcontainer_config"], base)
        fleets[str(name)] = fleet
    return PluginFragment(plugin=source, path=path, fleets=fleets, root=root)


def discover_plugin_fragments(
    roots: Iterable[tuple[str, list[Path]]] | None = None,
    *,
    repository: Path | None = None,
) -> list[PluginFragment]:
    """Load fragments declared by plugins active for ``repository``.

    Fragments are returned in deterministic plugin-source order.
    """
    if roots is None:
        roots = _active_plugin_roots(repository)
    fragments: list[PluginFragment] = []
    for source, live_roots in sorted(roots, key=lambda item: item[0]):
        for root in live_roots:
            declared = _declared_fragment(source, root)
            if declared is None:
                continue
            fragment = load_fragment(source, declared, root)
            if fragment is not None:
                fragments.append(fragment)
            break
    return fragments


def _pointer(parent: str, key: Any) -> str:
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _drop(provenance: dict[str, Any], pointer: str) -> None:
    for key in [k for k in provenance if k == pointer or k.startswith(pointer + "/")]:
        del provenance[key]


def _replay(
    target: dict[str, Any], incoming: dict[str, Any], pointer: str,
    owner: dict[str, Any], provenance: dict[str, Any],
) -> dict[str, Any]:
    """Apply one layer's fleet mapping over ``target`` per key.

    Mappings merge recursively, lists and scalars replace, and ``None``
    removes the key so it falls back to its default -- the clearing layer is
    kept as that pointer's owner. Provenance tracks every surviving value; an
    empty mapping is owned by the layer that left it empty.
    """
    result = dict(target)
    for key, child in incoming.items():
        child_pointer = _pointer(pointer, key)
        if child is None:
            _drop(provenance, child_pointer)
            result.pop(key, None)
            provenance[child_pointer] = copy.deepcopy(owner)
        elif isinstance(child, dict):
            current = result.get(key)
            if isinstance(current, dict):
                provenance.pop(child_pointer, None)
            else:
                _drop(provenance, child_pointer)
                current = {}
            merged = _replay(current, child, child_pointer, owner, provenance)
            result[key] = merged
            if not merged and not any(k.startswith(child_pointer + "/") for k in provenance):
                provenance[child_pointer] = copy.deepcopy(owner)
        else:
            _drop(provenance, child_pointer)
            result[key] = copy.deepcopy(child)
            provenance[child_pointer] = copy.deepcopy(owner)
    return result


def merge_fragments(
    data: dict[str, Any],
    fragments: list[PluginFragment],
    *,
    provenance: dict[str, Any],
    layers: list[str],
    operator_layers: list[tuple[dict[str, Any], Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
    """Merge plugin fragments beneath the operator configuration.

    ``data`` is the effective operator configuration (composed tiers or the
    single compatibility file; may be empty); ``provenance``/``layers`` are
    the composition's own metadata and ``operator_layers`` lists each operator
    layer's ``(owner record, values)`` low to high. Each plugin-backed fleet is
    rebuilt by replaying those layers over the fragment, so a clear in a lower
    tier still removes a shipped value before a higher tier adds to it, and its
    provenance subtree is replaced by the replay's (``tier: plugin`` owners for
    surviving shipped values). Returns ``(data, provenance, layers,
    fragments)``, where ``fragments`` summarizes each fragment's winning and
    shadowed fleets. Other fleets keep the composition's values/provenance.
    """
    plugin_fleets: dict[str, dict[str, Any]] = {}
    owners: dict[str, PluginFragment] = {}
    summaries: list[dict[str, Any]] = []
    for fragment in fragments:
        summary = fragment.to_dict()
        summary["shadowed"] = []
        for name, fleet in fragment.fleets.items():
            if name in plugin_fleets:
                log.warning(
                    "Fleet %r is defined by both %s and %s; using %s",
                    name, owners[name].label, fragment.label, owners[name].label,
                )
                summary["shadowed"].append(name)
                continue
            plugin_fleets[name] = fleet
            owners[name] = fragment
        summary["fleets"] = [n for n in fragment.fleets if n not in summary["shadowed"]]
        summaries.append(summary)

    provenance = dict(provenance)
    contributing = [f.label for f in fragments if any(owners.get(n) is f for n in f.fleets)]
    layers = [*contributing, *[layer for layer in layers if layer not in contributing]]
    if not plugin_fleets:
        return data, provenance, layers, summaries
    user_fleets = data.get("fleets")
    if user_fleets is None:
        user_fleets = {}
    if not isinstance(user_fleets, dict):
        # Preserve existing invalid-config handling; plugin fleets cannot be
        # merged beneath a malformed operator ``fleets`` value.
        return data, provenance, layers, summaries

    merged_fleets: dict[str, Any] = dict(user_fleets)
    removed: dict[str, dict[str, Any]] = {}
    for name, fragment in owners.items():
        effective = user_fleets.get(name)
        if effective is not None and not isinstance(effective, dict):
            continue  # an invalid operator value reaches native validation
        prefix = _pointer("/fleets", name)
        replayed: dict[str, Any] = {}
        value: dict[str, Any] | None = _replay(
            {}, plugin_fleets[name], prefix, fragment.owner(), replayed,
        )
        for owner, values in operator_layers:
            layer_fleets = values.get("fleets") if isinstance(values, dict) else None
            if not isinstance(layer_fleets, dict) or name not in layer_fleets:
                continue
            layer_value = layer_fleets[name]
            if layer_value is None or layer_value == {}:
                # An empty fleet entry removes the plugin-shipped fleet; a
                # higher layer may define it again from scratch.
                replayed.clear()
                replayed[prefix] = {**copy.deepcopy(owner), "removed": True}
                value = None
                continue
            if not isinstance(layer_value, dict):
                replayed.clear()
                value = {}
                continue
            if value is None:
                replayed.clear()
                value = {}
            value = _replay(value, layer_value, prefix, owner, replayed)
        if value is None:
            merged_fleets.pop(name, None)
            _drop(provenance, prefix)
            provenance.update(replayed)
            removed[name] = replayed[prefix]
            continue
        # Operator-owned source-relative paths keep the composition's resolution.
        for key in _SOURCE_RELATIVE_KEYS:
            owner = replayed.get(_pointer(prefix, key))
            if (
                isinstance(effective, dict) and key in effective and key in value
                and isinstance(owner, dict) and owner.get("tier") != PLUGIN_TIER
            ):
                value[key] = effective[key]
        merged_fleets[name] = value
        _drop(provenance, prefix)
        provenance.update(replayed)

    for summary in summaries:
        summary["removed"] = [n for n in summary["fleets"] if n in removed]
    merged = dict(data)
    merged["fleets"] = merged_fleets
    return merged, provenance, layers, summaries


def fleet_source(provenance: dict[str, Any], fleet_name: str | None) -> str | None:
    """Return the ID of the lowest layer contributing to ``fleet_name``."""
    if not fleet_name:
        return None
    prefix = _pointer("/fleets", fleet_name)
    marker = provenance.get(prefix)
    if isinstance(marker, dict) and marker.get("removed"):
        return None
    owners = [
        owner for pointer, owner in provenance.items()
        if (pointer == prefix or pointer.startswith(prefix + "/")) and isinstance(owner, dict)
    ]
    plugin = [owner for owner in owners if owner.get("tier") == PLUGIN_TIER]
    if plugin:
        return plugin[0].get("id")
    order = {"defaults": 0, "repository": 1, "machine": 2, "repository_machine": 3}
    owners.sort(key=lambda owner: order.get(owner.get("tier"), 4))
    return owners[0].get("id") if owners else None


def add_subparsers(sub: Any) -> None:
    """Register ``config-sources`` on the agent-containers CLI."""
    parser = sub.add_parser(
        "config-sources",
        help="Show configuration layers, plugin-shipped fleet fragments, "
        "and which layer supplied each configured value",
    )
    parser.add_argument("--json", action="store_true", help="Emit the provenance as JSON.")
    parser.set_defaults(func=cmd_config_sources)


def cmd_config_sources(args: Any) -> int:
    from .config import load_config

    config = load_config(strict=True)
    view = {
        "layers": config.configuration_layers,
        "plugin_fragments": config.plugin_fragments,
        "provenance": config.configuration_provenance,
    }
    if args.json:
        print(json.dumps(view, indent=2))
        return 0
    print("Layers (low to high): " + (", ".join(view["layers"]) or "(none recorded)"))
    fragments = view["plugin_fragments"]
    print("Plugin fragments:" if fragments else "Plugin fragments: (none)")
    for fragment in fragments:
        line = f"  {fragment['plugin']}: {fragment['path']} (fleets: "
        line += (", ".join(fragment["fleets"]) or "-") + ")"
        if fragment.get("shadowed"):
            line += f" [shadowed: {', '.join(fragment['shadowed'])}]"
        if fragment.get("removed"):
            line += f" [removed by operator: {', '.join(fragment['removed'])}]"
        print(line)
    provenance = view["provenance"]
    print("Provenance:" if provenance else "Provenance: (none recorded)")
    for pointer in sorted(provenance):
        owner = provenance[pointer]
        label = owner.get("id") if isinstance(owner, dict) else owner
        if isinstance(owner, dict) and owner.get("removed"):
            label = f"{label} (removed)"
        print(f"  {pointer}: {label}")
    return 0
