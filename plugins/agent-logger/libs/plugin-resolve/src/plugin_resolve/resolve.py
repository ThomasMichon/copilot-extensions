"""High-level plugin resolution for a repo directory.

Ties together :mod:`plugin_resolve.settings` (a repo's enabled plugins +
marketplaces, native-first/Claude-fallback) and :mod:`plugin_resolve.marketplace`
(resolving a plugin to its on-disk source dir in a local marketplace) into the
one question callers actually ask:

  *For this repo, which enabled plugins resolve to a local on-disk source dir
  (via a local `.ai`/`directory` marketplace), and which don't?*

This is the shared primitive behind agent-bridge's own-plugin ``--plugin-dir``
staging and agent-codespaces' repo-scoped plugin propagation. Every function
fails safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .marketplace import load_marketplace, plugin_dir
from .settings import (
    RepoPluginSettings,
    local_marketplace_path,
    read_repo_settings,
    split_source,
)


@dataclass
class ResolvedPlugins:
    """The outcome of resolving a repo's enabled plugins to local dirs."""

    resolved: dict[str, Path] = field(default_factory=dict)
    """``"<name>@<marketplace>" -> local plugin source dir`` (locally resolvable)."""
    unresolved: list[str] = field(default_factory=list)
    """Enabled sources that could not be resolved to a local dir (remote
    marketplace, missing manifest, etc.)."""


def resolve_repo_plugins(repo_dir: str | Path) -> ResolvedPlugins:
    """Resolve a repo's **enabled** plugins to local on-disk source directories.

    For each enabled ``name@marketplace`` whose marketplace is a **local**
    (``directory`` / ``local``) marketplace declared in the repo's settings,
    resolve the plugin's source dir within that marketplace. Sources backed by a
    remote marketplace -- or otherwise not locally resolvable -- are reported in
    :attr:`ResolvedPlugins.unresolved` (never fetched, never mutated globally).

    ``repo_dir`` is the repo checkout root; its settings are read native-first
    with a Claude fallback. Fail-safe -> empty result.
    """
    result = ResolvedPlugins()
    try:
        settings = read_repo_settings(repo_dir)
    except Exception:
        return result
    # Cache marketplace loads by resolved path.
    loaded: dict[str, object] = {}
    for source in settings.enabled_sources():
        name, marketplace = split_source(source)
        if not name or not marketplace:
            continue
        mp_path = local_marketplace_path(marketplace, settings, repo_dir=repo_dir)
        if mp_path is None:
            result.unresolved.append(source)
            continue
        key = str(mp_path)
        mp = loaded.get(key)
        if mp is None and key not in loaded:
            mp = load_marketplace(mp_path)
            loaded[key] = mp
        d = (
            plugin_dir(mp, name)
            if mp is not None and mp.name == marketplace
            else None
        )
        if d is not None:
            result.resolved[source] = d
        else:
            result.unresolved.append(source)
    return result


__all__ = [
    "RepoPluginSettings",
    "ResolvedPlugins",
    "read_repo_settings",
    "resolve_repo_plugins",
]
