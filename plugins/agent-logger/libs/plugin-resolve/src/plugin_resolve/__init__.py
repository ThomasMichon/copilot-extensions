"""``plugin_resolve`` -- shared Copilot CLI + Claude plugin resolution.

Given a **repo directory** or a **marketplace directory**, resolve plugin config
across *both* the Copilot-native and Claude conventions (native preferred, Claude
fallback):

- :func:`read_repo_settings` -- a repo's ``enabledPlugins`` +
  ``extraKnownMarketplaces`` (``.github/copilot/settings.json`` /
  ``.claude/settings.json``, with ``.local`` overrides).
- :func:`load_marketplace` / :func:`plugin_dir` -- a marketplace's manifest
  (``.github/plugin`` / ``.claude-plugin`` / ...) and a plugin's on-disk source
  dir (relative source + ``metadata.pluginRoot``).
- :func:`resolve_repo_plugins` -- the high-level "which of this repo's enabled
  plugins resolve to a local source dir" answer used by plugin-rollup callers.

Pure-stdlib, fail-safe, vendorable (mirrors ``config_migrate`` /
``ssh_manager`` / ``zdd``).
"""

from __future__ import annotations

from .conventions import (
    MARKETPLACE_MANIFEST_RELS,
    PLUGIN_MANIFEST_RELS,
    SETTINGS_RELS,
    MarketplaceSourceKind,
    has_plugin_manifest,
)
from .marketplace import (
    Marketplace,
    MarketplacePlugin,
    load_marketplace,
    marketplace_manifest_path,
    plugin_dir,
)
from .resolve import ResolvedPlugins, resolve_repo_plugins
from .settings import (
    RepoPluginSettings,
    local_marketplace_path,
    marketplace_source_kind,
    read_repo_settings,
    split_source,
)

__all__ = [
    "MARKETPLACE_MANIFEST_RELS",
    "PLUGIN_MANIFEST_RELS",
    "SETTINGS_RELS",
    "Marketplace",
    "MarketplacePlugin",
    "MarketplaceSourceKind",
    "RepoPluginSettings",
    "ResolvedPlugins",
    "has_plugin_manifest",
    "load_marketplace",
    "local_marketplace_path",
    "marketplace_manifest_path",
    "marketplace_source_kind",
    "plugin_dir",
    "read_repo_settings",
    "resolve_repo_plugins",
    "split_source",
]
