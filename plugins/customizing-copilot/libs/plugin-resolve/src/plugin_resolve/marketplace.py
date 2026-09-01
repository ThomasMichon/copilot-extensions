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
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .conventions import (
    MARKETPLACE_MANIFEST_RELS,
    PLUGIN_MANIFEST_RELS,
    first_existing,
)

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


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
    duplicates: frozenset[str] = frozenset()
    """Plugin names declared more than once; never resolvable."""


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
    raw_name = data.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    name = raw_name.strip()
    meta = data.get("metadata")
    plugin_root = ""
    if isinstance(meta, dict):
        pr = meta.get("pluginRoot")
        if isinstance(pr, str):
            plugin_root = pr.strip()
    plugins: dict[str, MarketplacePlugin] = {}
    duplicates: set[str] = set()
    raw = data.get("plugins")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            raw_plugin_name = item.get("name")
            if not isinstance(raw_plugin_name, str) or not raw_plugin_name.strip():
                continue
            pname = raw_plugin_name.strip()
            if pname in plugins:
                duplicates.add(pname)
                continue
            plugins[pname] = MarketplacePlugin(name=pname, source=item.get("source"))
    return Marketplace(
        root=root,
        name=name,
        plugin_root=plugin_root,
        plugins=plugins,
        duplicates=frozenset(duplicates),
    )


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
    plugin_root = Path(mp.plugin_root) if mp.plugin_root else Path()
    if plugin_root.is_absolute():
        return None
    try:
        root = mp.root.resolve(strict=True)
        candidate = (root / plugin_root / p).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _plugin_manifest_name(plugin_dir: Path) -> str | None:
    for rel in PLUGIN_MANIFEST_RELS:
        path = plugin_dir.joinpath(*rel)
        try:
            info = path.lstat()
        except OSError:
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
        ):
            return None
        data = _load_json(path)
        if data is None:
            return None
        manifest_name = data.get("name")
        return manifest_name.strip() if isinstance(manifest_name, str) else None
    return None


def plugin_dir(mp: Marketplace, name: str) -> Path | None:
    """The local source directory for plugin ``name`` in marketplace ``mp``.

    Only **relative-path** sources resolve to a local dir (and only when the dir
    holds a plugin manifest). Object sources (github/url/git-subdir/npm) are remote
    -> ``None``. Unknown plugin -> ``None``.
    """
    if name in mp.duplicates:
        return None
    entry = mp.plugins.get(name)
    if entry is None or not isinstance(entry.source, str):
        return None
    d = _relative_source_dir(mp, entry.source)
    if d is not None and _plugin_manifest_name(d) == name:
        return d
    return None
