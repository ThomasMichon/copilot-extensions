# plugin-resolve

Shared, **vendorable** plugin resolution for the Copilot CLI **and** Claude Code
conventions — given a **repo directory** or a **marketplace directory**, resolve
plugin config across *both* families, **Copilot-native preferred, Claude
fallback**.

Distribution name `agent-plugin-resolve` (dependency-confusion-safe); import
module `plugin_resolve`. Pure-stdlib, fail-safe. Mirrors the vendored
`config_migrate` / `ssh_manager` / `zdd` libs.

**In dev**, every consumer's `pyproject.toml` references this library through
a `uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1), so there is no per-plugin dev copy to keep in sync. **At release**,
`tools/materialize_main.py` rewrites that pointer into a real, promoted copy
at `plugins/<plugin>/libs/plugin-resolve/` for each consumer -- non-editable,
so a published plugin installs a self-contained source tree.

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
    marketplace_source_kind,# declared marketplace -> LOCAL | REMOTE | INVALID
    load_marketplace,       # marketplace dir -> Marketplace(name, plugin_root, plugins)
    plugin_dir,             # (Marketplace, name) -> local plugin source dir | None
    resolve_repo_plugins,   # repo dir -> ResolvedPlugins(resolved{src->dir}, unresolved[])
)
```

- **`read_repo_settings(repo_dir)`** — merged `enabledPlugins` +
  `extraKnownMarketplaces`, native-first/Claude-fallback. Only JSON booleans are
  accepted as enablement values.
- **`marketplace_source_kind(name, settings)`** — distinguishes supported local
  and remote marketplace declarations from malformed/unknown source kinds.
- **`load_marketplace(dir)` / `plugin_dir(mp, name)`** — locate + parse a
  marketplace manifest (any recognized location) and resolve a plugin's on-disk
  source dir. Relative sources resolve against the **marketplace root** (honoring
  `metadata.pluginRoot`) only when the canonical target remains contained there
  and its manifest has the exact plugin identity. Duplicate names, path/symlink
  escapes, malformed identities, and object sources are unresolved.
- **`resolve_repo_plugins(repo_dir)`** — the high-level answer: which of a repo's
  **enabled** plugins resolve to a local on-disk source dir (via a local
  `directory`/`local` marketplace such as the `.ai` standard), and which don't
  (`unresolved`, e.g. remote). Never fetches, never mutates global config.

## Local registration identity mapping

A same-machine consumer can preserve a local marketplace registration key
while explicitly declaring its expected manifest name:

```json
{
  "extraKnownMarketplaces": {
    "tools-local": {
      "source": {
        "source": "directory",
        "path": "./.ai",
        "manifestName": "tools"
      }
    }
  },
  "enabledPlugins": { "reviewer@tools-local": true }
}
```

`marketplace_manifest_name(key, settings)` supplies the expected identity.
Without `manifestName`, the manifest must still match the registration key.
An explicit empty, non-string, or mismatched mapping remains unresolved;
`stableId` never bypasses identity validation. This mapping applies only to
local directory sources and preserves marketplace-qualified reference keys.
Agent-bridge passes the resolved source directories directly as `--plugin-dir`
arguments on the source machine, without copying or globally enabling plugins.
It does not establish marketplace registration or dependency closure on a remote
machine. Other activation libraries must adopt this contract separately.

## Sources

- Copilot CLI: docs.github.com/.../customize-copilot/plugins-marketplace
- Claude Code: code.claude.com/docs/en/plugin-marketplaces
