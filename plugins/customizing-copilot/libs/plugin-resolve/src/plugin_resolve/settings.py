"""Read a repo's plugin **settings** across the Copilot-native and Claude
conventions (native preferred, Claude fallback).

A repo declares its plugin set in ``.github/copilot/settings.json`` (Copilot
native) or ``.claude/settings.json`` (Claude), each with an optional
``settings.local.json`` override alongside. This module returns the merged
``enabledPlugins`` + ``extraKnownMarketplaces`` maps, with **native winning over
Claude** on a key conflict and ``settings.local.json`` overriding
``settings.json`` within each convention.

Every function fails safe: missing / malformed files are skipped, never raised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .conventions import (
    LOCAL_MARKETPLACE_SOURCE_KINDS,
    REMOTE_MARKETPLACE_SOURCE_KINDS,
    SETTINGS_RELS,
    MarketplaceSourceKind,
)


@dataclass(frozen=True)
class RepoPluginSettings:
    """A repo's merged plugin settings."""

    enabled: dict[str, bool] = field(default_factory=dict)
    """``enabledPlugins`` -- ``"<name>@<marketplace>" -> bool``."""
    marketplaces: dict[str, dict] = field(default_factory=dict)
    """``extraKnownMarketplaces`` -- ``"<marketplace-name>" -> definition``."""

    def enabled_sources(self) -> list[str]:
        """The ``"<name>@<marketplace>"`` sources that are enabled (value truthy)."""
        return [s for s, on in self.enabled.items() if on and isinstance(s, str)]


def _load_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def read_repo_settings(repo_dir: str | Path) -> RepoPluginSettings:
    """Merge a repo's plugin settings across both conventions (native preferred).

    Reads ``SETTINGS_RELS`` in order (Claude first, native last) and folds each
    file's ``enabledPlugins`` / ``extraKnownMarketplaces`` in with **last-file-wins**
    semantics -- so native overrides Claude, and ``settings.local.json`` overrides
    ``settings.json`` within a convention. Fail-safe -> empty settings.
    """
    base = Path(repo_dir)
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    for rel in SETTINGS_RELS:
        data = _load_json(base.joinpath(*rel))
        if not data:
            continue
        en = data.get("enabledPlugins")
        if isinstance(en, dict):
            for k, v in en.items():
                if isinstance(k, str) and isinstance(v, bool):
                    enabled[k] = v
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            for k, v in mk.items():
                if isinstance(k, str) and isinstance(v, dict):
                    marketplaces[k] = v
    return RepoPluginSettings(enabled=enabled, marketplaces=marketplaces)


def split_source(source: str) -> tuple[str, str]:
    """``"name@marketplace"`` -> ``(name, marketplace)`` (either may be empty)."""
    name, _, marketplace = (source or "").partition("@")
    return name.strip(), marketplace.strip()


def local_marketplace_path(
    marketplace: str, settings: RepoPluginSettings, *, repo_dir: str | Path | None = None
) -> Path | None:
    """Resolve a **local** marketplace (``directory`` / ``local`` source) to a path.

    A relative ``path`` (e.g. the ``.ai`` standard's ``./.ai``) is resolved against
    ``repo_dir`` -- the repo whose settings declared it -- so it works regardless
    of the caller's cwd. A remote (github/git/npm) marketplace yields ``None`` (it
    is not fetched here). Fail-safe -> ``None``.
    """
    if marketplace_source_kind(marketplace, settings) is not MarketplaceSourceKind.LOCAL:
        return None
    entry = settings.marketplaces[marketplace]
    src = entry["source"]
    path = src.get("path")
    if not (isinstance(path, str) and path.strip()):
        return None
    p = Path(path.strip())
    if not p.is_absolute() and repo_dir is not None:
        p = Path(repo_dir) / p
    return p


def marketplace_source_kind(
    marketplace: str,
    settings: RepoPluginSettings,
) -> MarketplaceSourceKind:
    """Classify one declared marketplace without conflating remote and invalid."""
    entry = settings.marketplaces.get(marketplace)
    source = entry.get("source") if isinstance(entry, dict) else None
    kind = source.get("source") if isinstance(source, dict) else None
    if not isinstance(kind, str) or not kind.strip():
        return MarketplaceSourceKind.INVALID
    normalized = kind.strip()
    if normalized in LOCAL_MARKETPLACE_SOURCE_KINDS:
        return MarketplaceSourceKind.LOCAL
    if normalized in REMOTE_MARKETPLACE_SOURCE_KINDS:
        return MarketplaceSourceKind.REMOTE
    return MarketplaceSourceKind.INVALID
