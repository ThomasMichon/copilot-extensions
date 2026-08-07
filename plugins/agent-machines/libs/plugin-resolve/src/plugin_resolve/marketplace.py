"""Load a plugin **marketplace** manifest and resolve a plugin's on-disk source
directory, across the Copilot-native and Claude conventions.

A marketplace directory carries a ``marketplace.json`` (at ``.github/plugin/``,
the root, ``.plugin/``, or ``.claude-plugin/`` -- native-first) listing plugins,
each with a ``source``. For a **relative-path** source (``"./my-plugin"`` /
``"my-plugin"``, resolved against the *marketplace root*, optionally under
``metadata.pluginRoot``) this module returns the plugin's directory when it holds
a plugin manifest. Object sources (github/url/git-subdir/npm) are remote and not
resolved to a local dir here.

Every function fails safe: missing / malformed manifests yield ``None`` / empty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .conventions import (
    MARKETPLACE_MANIFEST_RELS,
    first_existing,
    has_plugin_manifest,
)


@dataclass(frozen=True)
class MarketplacePlugin:
    """One entry in a marketplace's ``plugins`` array."""

    name: str
    source: object
    """The raw ``source`` value -- a relative-path ``str`` or an object mapping."""


@dataclass(frozen=True)
class Marketplace:
    """A parsed marketplace manifest + the directory it was loaded from."""

    root: Path
    """The marketplace root directory (sources resolve relative to this)."""
    name: str
    plugin_root: str = ""
    """``metadata.pluginRoot`` -- a base dir prefixed to relative plugin sources."""
    plugins: dict[str, MarketplacePlugin] = field(default_factory=dict)


def _load_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def marketplace_manifest_path(marketplace_dir: str | Path) -> Path | None:
    """The marketplace manifest path within ``marketplace_dir`` (native-first)."""
    return first_existing(Path(marketplace_dir), MARKETPLACE_MANIFEST_RELS)


def load_marketplace(marketplace_dir: str | Path) -> Marketplace | None:
    """Load + parse a marketplace manifest from ``marketplace_dir``.

    Returns ``None`` when no manifest is found or it is malformed. The manifest
    may live at any recognized location; ``root`` is always ``marketplace_dir``
    (plugin sources resolve relative to the root, **not** the manifest's
    subdirectory -- matching both docs).
    """
    root = Path(marketplace_dir)
    manifest_path = marketplace_manifest_path(root)
    if manifest_path is None:
        return None
    data = _load_json(manifest_path)
    if data is None:
        return None
    name = str(data.get("name", "")).strip()
    meta = data.get("metadata")
    plugin_root = ""
    if isinstance(meta, dict):
        pr = meta.get("pluginRoot")
        if isinstance(pr, str):
            plugin_root = pr.strip()
    plugins: dict[str, MarketplacePlugin] = {}
    raw = data.get("plugins")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            pname = str(item.get("name", "")).strip()
            if not pname:
                continue
            plugins[pname] = MarketplacePlugin(name=pname, source=item.get("source"))
    return Marketplace(root=root, name=name, plugin_root=plugin_root, plugins=plugins)


def _relative_source_dir(mp: Marketplace, source: str) -> Path | None:
    """Resolve a relative-path plugin ``source`` to a dir under the marketplace.

    Honors ``metadata.pluginRoot`` as a prefix. A leading ``./`` is optional. An
    absolute source is rejected (relative-only per both specs)."""
    s = source.strip()
    if not s:
        return None
    p = Path(s)
    if p.is_absolute():
        return None
    base = mp.root / mp.plugin_root if mp.plugin_root else mp.root
    return (base / s).resolve()


def plugin_dir(mp: Marketplace, name: str) -> Path | None:
    """The local source directory for plugin ``name`` in marketplace ``mp``.

    Only **relative-path** sources resolve to a local dir (and only when the dir
    holds a plugin manifest). Object sources (github/url/git-subdir/npm) are remote
    -> ``None``. Unknown plugin -> ``None``.
    """
    entry = mp.plugins.get(name)
    if entry is None or not isinstance(entry.source, str):
        return None
    d = _relative_source_dir(mp, entry.source)
    if d is not None and has_plugin_manifest(d):
        return d
    return None
