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

## Trusted venue preparation

`plugin_resolve.reflection.build_plan` preserves qualified identities, selects
local payloads from the declaring checkout before installed state, and builds
filtered snapshots. The caller's qualified provider/related selection and the
target repository's enabled declarations are the eligibility authority.
Manifest dependency objects are parsed and retained, but never expand that
set or authorize propagation of another plugin. Existing provider filters for
harness-only roles still apply. Required relative assets of selected payloads
retain their source-relative topology. README/contribution links and script
root expressions do not expand the copy to enclosing checkouts. Cache directories,
environment-secret files, linked paths and escapes remain excluded or refused.

`build_prepare_script` carries that plan over an existing script/stdin transport.
An explicitly selected qualified source whose marketplace is unknown on the
host is a deferred target lookup, not an invented marketplace or a host-cache
fallback. Preparation resolves it against the target's own settings/catalog,
including a selected non-default plugin, before publishing local registration.
An undeclared target marketplace or absent selected target payload fails closed.
The target-side `plugin_resolve.preparation.prepare` completes every local copy
and remote installation before registering anything in the target checkout.
Remote plugins use the supported `copilot plugin marketplace add` and
`copilot plugin install` commands with an isolated `COPILOT_HOME` beneath the
checkout's Git-local `copilot-venue-plugins` state directory. The target's own
enabled declarations join host selections, including target-native remote
marketplaces; unrelated host-global plugins do not. Installer success alone is
insufficient: each resulting payload must have the requested manifest identity.
No user-global registration or enablement is required by the launched agent.

Preparation publishes native-valid local subset catalogs, retaining marketplace
owner metadata and each payload's version, and atomically merges
`.github/copilot/settings.local.json` under a checkout lock. It preserves base
settings, unrelated keys, enabled native plugins and explicit local overrides.
An ownership receipt records entries created or previously owned by this
operation; explicit enablement and local payload-path overrides are not
overwritten. A target-native remote declaration in `settings.local.json`
remains the installation authority: after its verified installation, the local
path is published and its exact original declaration is retained in the
Git-local receipt's `marketplaceOrigins`. Repeated preparation retains that
origin instead of falling back to a host payload with the same qualified name.
This localization is reported explicitly. Existing snapshots
are retained, so another launch does not invalidate a running child's paths.
Marketplace authority is reconciled once from the lock-entry operator snapshot,
not from values generated for an earlier plugin in the same transaction.
Active native origins also resolve newly enabled target plugins that are not
yet in the generated subset. Genuine operator-changed local paths remain
authoritative; unchanged base declarations are consulted behind owned projections.
Malformed configuration and missing required payloads fail the launch barrier.
An explicit venue worktree is resolved by its provider using the native
`resolve --json --dry-run` execution plan before invoking the shared engine.
The engine operates only on the supplied checkout; it never discovers a
Worktrees executable or interprets a Worktrees listing.

Both CLI and ACP must establish their existing credential-relay channel, deploy
the normal auth helpers, and export the normal launch prelude before private
marketplace installation. A bare SSH `ConnectionManager` connection is not that
authenticated context. Remote Session Hosts run preparation only after the
relay protocol readiness check, retaining those same supervisors through
publication and child launch. Headed providers use the existing Owner/keeper
or foreground SSH reverse-forward; preparation does not create a second service.

Both CLI and ACP must await preparation. CLI consumes repository settings
without repeating those identities as external `--plugin-dir` entries, which
would load them twice. ACP additionally receives a directory flag for every
effective prepared/native plugin. Surviving-child reattachment does not repeat
preparation. Restricted containers do not receive this projection; static SSH
and elevated launch integration are separate work.

The isolated installation cache, snapshots, locks and ownership receipt remain
inside the checkout's Git metadata (including linked-worktree metadata), not
the tracked product tree. Only the required `settings.local.json` is published
in the checkout, with a normal Git-local exclude entry rather than a tracked
`.gitignore` change. Explicit target-local choices override host enable/disable
requests. An out-of-selection or disabled companion is reported explicitly,
with its qualified identity and the need for a caller-supplied execution owner.
That owner can be an external host: a manifest dependency does not imply that
the companion must run in this agent, and absence alone is not a launch failure.
The engine neither assumes that host authority is available nor silently
enables the companion. Missing explicitly selected payloads and failed selected
copies/installations still block launch.

Direct `prepare` callers can collect these diagnostics through its optional
`diagnostics` list; otherwise they are logged. Script transports carry them
alongside the prepared-directory result, and provider adapters log them without
turning them into additional plugin directories.

## Sources

- Copilot CLI: docs.github.com/.../customize-copilot/plugins-marketplace
- Claude Code: code.claude.com/docs/en/plugin-marketplaces
