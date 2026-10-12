"""Convention constants for Copilot CLI + Claude plugin resolution.

Copilot CLI recognizes **both** its own native plugin conventions and the Claude
Code conventions it is compatible with, preferring native and falling back to
Claude. These constants encode the locations for each, in **native-first** order,
so a resolver honors the same precedence.

Sources:
- Copilot CLI plugins/marketplace docs
  (docs.github.com/.../customize-copilot/plugins-marketplace)
- Claude Code plugin-marketplaces docs
  (code.claude.com/docs/en/plugin-marketplaces)
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo settings files (enabledPlugins + extraKnownMarketplaces).
# Native-first: `.github/copilot/settings.json` (+ `.local`), then the Claude
# `.claude/settings.json` (+ `.local`). Within a convention, `settings.local.json`
# overrides `settings.json`. Ordered so a LATER file wins (native last => native
# wins on a key conflict).
# ---------------------------------------------------------------------------
SETTINGS_RELS: tuple[tuple[str, ...], ...] = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)

# ---------------------------------------------------------------------------
# Marketplace-manifest locations within a marketplace directory, in the CLI's
# lookup order. Copilot-native (`.github/plugin`) first, then the bare/`.plugin`
# spellings, then the Claude `.claude-plugin`.
# ---------------------------------------------------------------------------
MARKETPLACE_MANIFEST_RELS: tuple[tuple[str, ...], ...] = (
    (".github", "plugin", "marketplace.json"),
    ("marketplace.json",),
    (".plugin", "marketplace.json"),
    (".claude-plugin", "marketplace.json"),
)

# ---------------------------------------------------------------------------
# Plugin-manifest locations within a plugin directory. Native `plugin.json` (repo
# root of the plugin) first, then the Claude `.claude-plugin/plugin.json`.
# ---------------------------------------------------------------------------
PLUGIN_MANIFEST_RELS: tuple[tuple[str, ...], ...] = (
    ("plugin.json",),
    (".claude-plugin", "plugin.json"),
)

# Local (on-disk) marketplace source spellings. Copilot's `directory` and the
# older `local` both mean "a marketplace directory on this machine".
LOCAL_MARKETPLACE_SOURCE_KINDS: frozenset[str] = frozenset({"directory", "local"})
REMOTE_MARKETPLACE_SOURCE_KINDS: frozenset[str] = frozenset(
    {"github", "git", "git-subdir", "npm", "url"}
)


class MarketplaceSourceKind(str, Enum):
    """Whether a settings marketplace source is local, remote, or malformed."""

    LOCAL = "local"
    REMOTE = "remote"
    INVALID = "invalid"


def first_existing(base: Path, rels: tuple[tuple[str, ...], ...]) -> Path | None:
    """Return ``base`` joined with the first relative path that exists, or None."""
    for rel in rels:
        cand = base.joinpath(*rel)
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return None


def has_plugin_manifest(plugin_dir: Path) -> bool:
    """True when ``plugin_dir`` holds a plugin manifest (native or Claude)."""
    for rel in PLUGIN_MANIFEST_RELS:
        try:
            if plugin_dir.joinpath(*rel).is_file():
                return True
        except OSError:
            continue
    return False
