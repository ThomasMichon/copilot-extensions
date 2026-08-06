# plugin-resolve

Shared, **vendorable** plugin resolution for the Copilot CLI **and** Claude Code
conventions — given a **repo directory** or a **marketplace directory**, resolve
plugin config across *both* families, **Copilot-native preferred, Claude
fallback**.

Distribution name `agent-plugin-resolve` (dependency-confusion-safe); import
module `plugin_resolve`. Pure-stdlib, fail-safe. Mirrors the vendored
`config_migrate` / `ssh_manager` / `zdd` libs.

## Why

Copilot CLI recognizes both its own conventions and the Claude conventions it is
compatible with:

| Concern | Copilot-native | Claude (fallback) |
|---------|----------------|-------------------|
| Repo plugin settings | `.github/copilot/settings.json` (+ `.local`) | `.claude/settings.json` (+ `.local`) |
| Marketplace manifest | `.github/plugin/marketplace.json` | `.claude-plugin/marketplace.json` |
| Plugin manifest | `plugin.json` | `.claude-plugin/plugin.json` |

Our plugin-rollup logic (agent-bridge own-plugin `--plugin-dir` staging;
agent-codespaces repo-scoped propagation) must resolve **both**. This lib is the
one place that encodes the locations + precedence, so the callers don't each
re-implement it.

**Precedence:** Copilot-native wins over Claude on a key conflict; within a
convention, `settings.local.json` overrides `settings.json`.

## API

```python
from plugin_resolve import (
    read_repo_settings,     # repo dir -> RepoPluginSettings(enabled, marketplaces)
    load_marketplace,       # marketplace dir -> Marketplace(name, plugin_root, plugins)
    plugin_dir,             # (Marketplace, name) -> local plugin source dir | None
    resolve_repo_plugins,   # repo dir -> ResolvedPlugins(resolved{src->dir}, unresolved[])
)
```

- **`read_repo_settings(repo_dir)`** — merged `enabledPlugins` +
  `extraKnownMarketplaces`, native-first/Claude-fallback.
- **`load_marketplace(dir)` / `plugin_dir(mp, name)`** — locate + parse a
  marketplace manifest (any recognized location) and resolve a plugin's on-disk
  source dir. Relative sources resolve against the **marketplace root** (honoring
  `metadata.pluginRoot`); object sources (github/url/git-subdir/npm) are remote →
  `None`.
- **`resolve_repo_plugins(repo_dir)`** — the high-level answer: which of a repo's
  **enabled** plugins resolve to a local on-disk source dir (via a local
  `directory`/`local` marketplace such as the `.ai` standard), and which don't
  (`unresolved`, e.g. remote). Never fetches, never mutates global config.

## Sources

- Copilot CLI: docs.github.com/.../customize-copilot/plugins-marketplace
- Claude Code: code.claude.com/docs/en/plugin-marketplaces
