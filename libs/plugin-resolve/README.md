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

`plugin_resolve.remote_identity.normalize_remote` owns the shared network/local
Git remote parser. Plugin activation retains its existing compatibility export;
SSH discovery uses the same network normalization without introducing another
runtime dependency. Network keys preserve path case and decode URL paths;
SCP remotes with or without a user are recognized before URI scheme parsing.

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
Environment basenames (`.env` / `.env.*`) are matched case-insensitively, with
the existing `.example`, `.sample` and `.template` suffix exceptions likewise
case-insensitive. No additional filename patterns are inferred.

`selection_spec` normalizes a legacy source string or a mapping with `source`
and optional `enable` (default `True`). It requires a non-empty string and an
actual boolean, retains source spelling (including paths/spaces), and does not
impose qualified-identity syntax. `selection_cli_args` emits two argv elements:
`--stage-plugin SOURCE` when enabled, or `--stage-plugin-disabled SOURCE` for an
install-only selection. Do not encode booleans in source suffixes or flatten
typed selections through enabled-only resolution results. `build_plan` retains
its qualified-identity checks and first-selection-wins ordering; callers compose
provider/related precedence before passing their selections.

`build_prepare_python` emits the self-contained stdlib-only Python program for
streaming to the receiver's Python stdin, independent of its shell or OS.
`build_prepare_script` wraps the same program in the existing POSIX `python3`
heredoc for script/stdin transports. Both require the actual execution checkout
and support the same opt-in prepared-context frame; neither starts a transport
or changes the productive process's account or environment.
`plugin_resolve.receiver` is the shared streamed stdlib supervisor used by
Bridge's static SSH ACP preparation and agent-ssh's headed CLI preparation.
Its existing bounded process-group/Job contract contains installer descendants
and drains both pipes before returning success, error or timeout; transports
leave time for that cleanup instead of treating local SSH-client exit as
receiver quiescence. The module is shipped as source over the existing channel,
not imported from a preinstalled receiver runtime.

An explicitly selected qualified source whose marketplace is unknown on the
host is a deferred target lookup, not an invented marketplace or a host-cache
fallback. Preparation resolves it against the target's own settings/catalog,
including a selected non-default plugin, before publishing local registration.
An undeclared target marketplace or absent selected target payload fails closed.
The target-side `plugin_resolve.preparation.prepare` completes every local copy
and remote installation before registering anything in the target checkout.
Remote plugins use the supported `copilot plugin install` command with an isolated
`COPILOT_HOME` beneath the checkout's Git-local `copilot-venue-plugins` state.
Its private native cache uses the existing short-lived neutral acquisition
directory, avoiding deeply nested native Git cache paths on Windows. Native
catalog metadata is read before that cache is removed and retained in the
completed operation receipt; legacy persisted caches remain readable.
The original structured marketplace declaration is written into
that private operation home's settings **before** acquisition. Native installation
therefore receives its complete source, including revision and location fields;
the engine does not reconstruct a URI or register one source and rewrite its
metadata afterward. The native `marketplaces/` cache supplies the installed
marketplace's catalog metadata and selected entry, using the same publication
normalization as local reflection (including removal of relocated `pluginRoot`).
Each immediate cached clone contributes only its existing native-priority
marketplace manifest; an alternative Claude catalog in the same clone is not a
second marketplace. Nested plugin/test catalogs and cache bookkeeping files are
not candidates. Multiple matching clone roots still fail as ambiguous, and
linked/escaping clone or manifest paths remain refused.
Marketplace ownership is not inferred from optional plugin authorship or a
GitHub account. The operation's installation receipt retains the actual catalog
and entry for cache hits, even after native cache eviction. The owning home's
structured marketplace definition must match the requested origin before any
cache reuse. Native commands run from a scoped private neutral working
directory outside the execution checkout, so its generated marketplace overlay
cannot shadow that installation authority. A matching old source-only receipt
and installed payload hydrate from the actual catalog without reinstalling.
If that catalog is unavailable, only the explicitly named
`copilot plugin marketplace update NAME` refreshes it. A partial install retains
its private declaration and retries installation. Mismatches and native failures
are explicit, never accepted by parsing an "already registered" error.
The target's own
enabled declarations join host selections, including target-native remote
marketplaces; unrelated host-global plugins do not. Installer success alone is
insufficient: each resulting payload must have the requested manifest identity.
No user-global registration or enablement is required by the launched agent.

Preparation publishes native-valid subset catalogs **only for reflected local
sources**, retaining marketplace owner metadata and each payload's version.
Git declarations remain the original Git objects in target settings; an installed
directory is availability evidence, never replacement acquisition authority.
Preparation atomically merges
`.github/copilot/settings.local.json` under a checkout lock. It preserves base
settings, unrelated keys, enabled native plugins and explicit local overrides.
An ownership receipt records entries created or previously owned by this
operation; explicit enablement and local payload-path overrides are not
overwritten. Operator authority is Copilot local > Copilot base > Claude local >
Claude base > host fallback, independently for enablement and marketplace
declarations. Unchanged receipt-owned generated values are projections, not
operator overrides. Base/Claude declarations are read fresh on every preparation
and rechecked under the registration lock; explicit `False` remains authoritative.
A target-native remote origin wins over a host snapshot with the same identity
in every supported layer and is acquired without converting its declaration to
a directory. Legacy unchanged owned directory projections with saved Git origins
migrate back to those original Git declarations; authored operator edits are
not migrated or overwritten. Native local catalogs are read directly in the
execution checkout instead of being copied into a second subset. Existing snapshots
are retained, so another launch does not invalidate a running child's paths.
Marketplace authority is reconciled once from the lock-entry operator snapshot,
not from values generated for an earlier plugin in the same transaction.
Before any catalog, ownership receipt or local-settings publication, every
effectively enabled source must have verified availability under that same lock, including operator
enablement added while installation was running. An unresolved new selection
fails the barrier without publishing partial state or undoing the operator's
edit. Explicitly disabled sources and unselected companion requirements do not
become new requirements; an already-resolvable native selection remains valid.
Active native origins also resolve newly enabled target plugins that are not
yet in the generated subset. Genuine operator-changed local paths remain
authoritative; unchanged base declarations are consulted behind owned projections.
Prepared target-local marketplace declarations are compared with the authored
lock-entry view before publication. A directory-origin change during installation
refuses the old payload rather than republishing its projection or silently
replanning.
Origin validation also covers every effectively enabled identity, including
previously disabled entries omitted from this preparation. An owned directory
projection cannot mask a newly authoritative base/Claude marketplace origin
merely because its old payload still resolves. Remote cache reuse must match
the source/definition-addressed installation home, completed source receipt,
owning-home registration and actual installed manifest. Direct authored local
directories remain usable; a projected local origin needs preparation evidence.
These checks only read existing state under the lock: they never install,
refresh or retry there. Disabled entries and inactive namespaces do not gain
new origin requirements. Refusal preserves prior publication and operator edits.

The local-only `resolve_repo_plugins` API deliberately still reports Git sources
as unresolved. Preparation validates those sources through their completed
private installation records and returns their actual directories for ACP.
This is not a claim that the productive child's home already has native
installation bookkeeping. Headed startup uses native repo-scoped activation
with the original Git declarations; its launch owner must withhold productive
task delivery until the required registered/enabled loadout is ready. Productive
HOME, authentication and preference authority are not switched by preparation.
The compiled readiness context includes manifest versions for local payloads,
but not for remote marketplace declarations. A floating Git declaration can
resolve to a newer version during independent native startup than the isolated
preparation cache contains. That cache's version is not a native activation pin;
the original origin, revision and location remain unchanged in target settings.
Known local marketplace roots and local payload versions remain strict readiness
expectations when native observation advertises them.

Each complete preparation reads ownership and authored settings together under
the checkout lock. Publication prunes unchanged owned enablement observed in
that initial snapshot when absent from both this preparation's payloads and
authored settings. Owned additions or value changes published since the snapshot
are retained for overlapping preparations; a later complete preparation can
prune them. This is an observed-snapshot replacement horizon, not a lease or a
new retry protocol. Pruned entries are excluded from prospective resolution
rather than reintroduced from the old on-disk overlay. Operator-changed values
and authored `True`/`False` decisions survive. An authored enablement may use a
still-owned, contained catalog projection as payload availability when no
authored origin exists; generated enablement alone never authorizes retention.
Catalogs and immutable payload snapshots are not deleted by enablement pruning.
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

## Same-filesystem preparation

`build_plan(..., local_mode="reference")` emits explicit `reference` records
with a marketplace root, payload, and absolute directory declaration. The
matching `prepare` request must opt into `"local_mode": "reference"` too.
These are not copy-mode `local_path` fields with relaxed containment:
registration revalidates each direct reference against its source catalog
and publishes only target settings/ownership. It never writes a generated
catalog into the referenced marketplace or snapshots the payload. Receipts
identify reference registrations explicitly for repeat owned-projection
recovery. Native operator origins, disablement, locking, atomic publication
and effective-selection checks remain the same. Copy mode is still the default
for all existing remote callers.

When controller and native selections use the same marketplace name but
different physical catalogs, native registration stays unchanged. Controller
references use a deterministic operation-owned `<marketplace>-venue-<digest>`
registration with `source.manifestName` identifying the original catalog.
The receipt's `pluginIdentities` maps logical selections to projected qualified
identities; preparation returns logical keys for ACP. Mapping alone never
selects a plugin. Original logical disablement and explicit alias disablement
are honored; contradictory explicit alias enablement and logical disablement
fail without overwriting the operator's edit. Explicit alias-origin edits are
replanned against that authored catalog. Unselected unchanged owned aliases
are pruned, while operator-edited entries remain intact.

Reference mode also preserves installed-only ACP fallback when no marketplace
origin is declared. It validates the qualified payload beneath the child-selected
`COPILOT_HOME/installed-plugins`, and validates registrations and catalogs when
available using the selected cache environment. Absent optional catalog metadata
does not invalidate a previously usable manifest-only ACP payload. This path
neither invents a remote origin nor invokes installation, copies payloads, or
writes source catalogs. Repository-local enablement is still published, and ACP
receives the existing directory. A manifest-only manual inventory is **not**
proof of settings-only native CLI discovery: it remains ACP-only unless the
native owner has a declared marketplace and can confirm the registered loadout.
The selected home's `settings.json` directory declarations are a fallback after
repository/controller authority: a valid registered local catalog resolves its
live original payload even without an installed copy. That grounded catalog can
be registered directly in repository-local settings. Its home provenance is
retained in `inventorySources` so a later launch under a different child home
does not mistake an old generated projection for current inventory authority.

`prepare(..., environment=child_env)` passes that environment to Git and the
isolated native installer after removing Git selectors. Omission retains the
ambient-environment contract; a supplied mapping is the complete child
environment, not an overlay onto the daemon. Git queries and ownership
discovery resolve the Git executable through that execution environment too,
including Windows quotes, `PATHEXT` and relative entries, rather than borrowing
the daemon's executable search path. `check_cancelled` may stop preparation between
bounded operations and before publication; callers using a worker thread must
await its quiescence rather than abandon it.

Relative and empty `PATH` entries are anchored in a copied preparation
environment to the original requested execution directory, even when Git
discovery identifies a repository root above it. The productive environment and
process cwd are not changed. Native installer executable lookup also uses that
execution context before entering the private neutral install directory; default
commands honor `COPILOT_PATH`, while an explicit command prefix retains priority.
Windows lookup uses the child `PATHEXT` and current-directory policy, and selected
batch commands use the existing `COMSPEC` contract. A reserved per-child
environment value transports the literal batch path through one expansion,
avoiding quote stripping and re-expansion of spaces, `&`, `%` or `!` in that
path; it is not a user configuration or another launcher. A missing installer fails
only when installation or refresh actually requires it, not for local-only
preparation or verified cache reuse.

## Target-owned absolute local sources

`build_plan(..., target_checkout=actual_checkout)` can recognize an authored
absolute local marketplace belonging to the same Git common repository. It
records `target_relative: {repository, marketplace_relative}` instead of copying
or pinning the producer's anchor. The receiver validates that binding, resolves
the catalog in its actual execution checkout and publishes a relative local
declaration such as `"path": "./.ai"`. Plugin paths come from that target catalog.
Native operator origins and disablement remain authoritative.

The authored-absolute bit is captured before path normalization. Relative
authored paths and unrelated worktree references do not acquire this exception.
Escapes, linked target paths and nested-repository confusion are rejected.
For a cross-world producer, `target_repository` plus `target_source_roots` may
be supplied only from an existing exact registered/provider repository binding;
basename or remote-URL equality alone is insufficient. The receiver never opens
a producer-only absolute path. Producers without such a binding cannot claim
automatic target-owned relocation.

Trusted Codespace and container producers use explicit repository-registry
bindings for this annotation; Bridge retains the binding when composing provider
and related selections. Registry aliases and declared repository identities
identify registered source roots, not arbitrary checkouts with a matching remote
or basename. Providers query the registry through their existing attributable
Worktrees command boundary, without importing its sibling Python runtime.
Incomplete or unsupported strict inventory is refused rather than replaced by
fallback checkout discovery. Git common-repository checks then establish
membership. Registered bare source anchors are supported without treating a
nested repository as part of its parent. The receiver still requires an actual
execution checkout.
Relative local sources and Git-only selections do not trigger ownership lookup.

Child-inventory settings accept the existing bounded leading-`//` comment-header
convention used by activation readers. This is read-only compatibility, not a
claim of a general JSONC parser; other malformed settings still fail explicitly.

## Sources

- Copilot CLI: docs.github.com/.../customize-copilot/plugins-marketplace
- Claude Code: code.claude.com/docs/en/plugin-marketplaces
