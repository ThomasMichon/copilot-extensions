---
name: installing-plugins
description: >
  Install and enable Copilot CLI plugins -- repo-scoped registration via
  .github/copilot/settings.json (extraKnownMarketplaces + enabledPlugins),
  versus global installs, the in-repo `.ai` local plugin marketplace (directory
  source) for modular in-repo capability, plus the
  payload-vs-runtime model and launch-time reconciliation. Use when installing,
  enabling, or updating a plugin, adding a marketplace, packaging a repo's own
  skills/agents as in-repo plugins, or setting up a repo's or machine's plugin set.
  Trigger phrases include:
  - 'install a plugin'
  - 'enable a plugin'
  - 'enabledPlugins'
  - 'copilot plugin install'
  - 'plugin marketplace'
  - 'extraKnownMarketplaces'
  - 'repo plugins'
  - 'settings.json plugins'
  - 'local marketplace'
  - '.ai plugins'
  - 'directory source'
  - 'in-repo plugins'
---

# Installing Plugins

How to install and enable Copilot CLI plugins. Two registration styles --
**repo-scoped (preferred)** and **global** -- plus the distinction between a
plugin's *payload* and its *runtime*.

Reference: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing

## Decide the scope first (prefer repo-scoped)

**Default to repo-scoped enablement.** Before enabling anything, pick the
narrowest scope that works — almost always the repo:

1. **The plugin belongs to a project/harness → repo-scoped**, declared in that
   repo's committed `.github/copilot/settings.json`. It then travels with the
   repo, stays consistent across machines and forks, and loads **only** in that
   repo's sessions. This is the default.
2. **The plugin is the repo's own modular skill/agent → the in-repo `.ai` local
   marketplace** (a `directory` source) — still repo-scoped, one plugin per
   capability.
3. **Only a truly machine-universal plugin, on a box with no single control repo
   → global.**

### Keep inventory separate from activation

Copilot stores availability and activation in separate places:

| State | Authority | Meaning |
|---|---|---|
| Installed inventory | `~/.copilot/config.json` `installedPlugins[]` | The payload is available locally. An inventory record, cache directory, or marketplace registration does **not** mean the plugin is active. |
| User-global activation | `~/.copilot/settings.json` `enabledPlugins` | A source-qualified key set to `true` loads the plugin for the user across repositories. Absent and `false` are both not user-enabled and must remain distinct during updates. |
| Repository activation | `<repo>/.github/copilot/settings.json` `enabledPlugins` | A source-qualified key set to `true` loads the plugin only for that repository when repository settings are trusted. |
| Repository trust | `~/.copilot/config.json` `trustedFolders` | Controls whether repository settings participate; it does not install or activate a plugin by itself. |

Marketplace refresh and payload update should refresh available inventory
without broadening activation. Preserve the existing user value exactly:
absent stays absent, `false` stays `false`, and `true` stays `true`. Do not infer
activation from an installed payload directory.

> **Do NOT reach for `copilot plugin install` merely to enable a repo-scoped plugin.**
> `copilot plugin install <name>@<market>` is the **global/user-level** path: it
> writes the enablement into the **user** `~/.copilot/settings.json` and vendors
> the payload for *every* repo/session on the machine — so the plugin (and any
> sub-agent/MCP it ships) loads everywhere, including unrelated repos. That is
> exactly the namespace pollution repo-scoping avoids. To enable a plugin
> **repo-scoped, you do not run an install command at all** — you add a
> declarative line to the repo's `.github/copilot/settings.json` (below) and
> Copilot vendors the payload for sessions in that repo. Use `copilot plugin
> install` **only** for the deliberate global case.

## Recommended: register at repo scope

Pin the plugin set to a repo so it travels with the project and stays consistent
across machines.

1. **Declare the marketplace + enable the plugins** in the repo's committed
   `.github/copilot/settings.json`:

   ```json
   {
     "extraKnownMarketplaces": {
       "my-marketplace": {
         "source": { "source": "github", "repo": "owner/my-marketplace-repo" }
       }
     },
     "enabledPlugins": {
       "some-plugin@my-marketplace": true
     }
   }
   ```

   Copilot vendors the enabled plugin **payloads** when a session runs in that
   repo. A plugin's `extensions/` directory is only scanned when it is enabled,
   and a newly enabled plugin may only take effect after **restarting the active
   session** (plugins are scanned at startup).

   In current runtimes, interactive first-load trust is resolved before
   repository plugin loading, so accepting trust activates repo-only hooks in
   that first session. ACP sessions cannot present an interactive trust prompt;
   their repository settings still require persisted trust.

   Current Copilot CLI plugin loading does **not** require experimental mode.
   Some adjacent features (for example MCP registry search) may be experimental,
   but `extraKnownMarketplaces` + `enabledPlugins` is ordinary settings
   configuration.

   **No `copilot plugin install` step is needed for this path** — the declarative
   `enabledPlugins` line *is* the enablement, and Copilot vendors the payload for
   sessions in the repo (a control harness may also reconcile it on launch; see
   *Keeping a repo's plugins fresh automatically*). Running `copilot plugin
   install` here would additionally enable it **globally**, which you don't want.

A repo's `.github/copilot/settings.json` is merged with the user
`~/.copilot/settings.json`; `enabledPlugins` may live in either.

> **`enabledPlugins` is not an ordering list.** It is an
> enablement/precedence map for source-qualified plugin identities. JSON key
> order must never be used to arrange hook execution or make one plugin the
> winner. Relative plugin order is an implementation detail, not an
> author-facing compatibility contract; see `authoring-skills`' hook ordering
> guidance when several plugins emit the same output field.

> **Session extensions vs. skills/payload — a scoping caveat.** Repo-scoped
> `enabledPlugins` reliably governs a plugin's **payload** — its skills, hooks,
> and agents — for sessions in that repo. A plugin's **session extension** (an
> `extensions/<name>/extension.mjs` the plugin contributes) is activated from the
> **user-level** enabled set — `~/.copilot/settings.json` `enabledPlugins` plus
> the persisted install state — which the extension loader currently reads
> *without* merging the repo's `.github/copilot/settings.json`. So a plugin whose
> value depends on a **session extension loading** should be enabled at the
> **user level** (or installed globally), not via repo-scoped `enabledPlugins`
> alone. Skills/payload are unaffected; repo-scoped activation of *session
> extensions* is a known limitation.

> **Hooks over the ACP transport — a second scoping caveat.** A session created
> over **ACP** (e.g. an ACP-mode bridge) does not run the interactive trust prompt;
> it honors only *persisted* folder-trust, and repository-scoped activation is
> folder-trust-gated. So a plugin enabled only via a repo's
> `.github/copilot/settings.json` activates over ACP **iff** the session's `cwd`
> is already persisted-trusted. **In practice it usually is** — a worktree manager
> (e.g. agent-worktrees) auto-adds its worktrees to the trusted-folders store, so
> repo-scoped plugin `sessionStart` hooks **do** fire over ACP for those sessions
> (verified). The gap bites only an ACP `cwd` that was never trusted; for that,
> enable the plugin at the **user level** (not folder-trust-gated) or pre-trust the
> directory. Note a repository's own **`.github/hooks` never load over ACP** at all
> — trusted or not — so an ACP-critical hook belongs in a plugin `hooks.json` or
> user hooks. Interactive-session skills/payload are unaffected.

## Preferred for modular in-repo capability: the `.ai` local marketplace

When a repo's **own** skills/agents should be **modular, individually
toggleable, and travel with the repo** — rather than a single monolithic plugin,
or loose `.github/skills/` / `.github/agents/` trees — package them as plugins in
an **in-repo local plugin marketplace**: an **`.ai/` directory** the repo
declares as a `directory`-source marketplace. This is the standard used across MS
repos (e.g. `SPO.Core`) and is the **preferred pattern** for driving modular
in-repo plugins.

**Layout** (`.ai/` at the repo root):

```
.ai/
├── .claude-plugin/marketplace.json     # lists every plugin (name, description, version, source: "./<name>")
└── <capability>/
    ├── .claude-plugin/plugin.json       # { name, description, version, author }
    ├── skills/<capability>/SKILL.md      # a skill plugin
    └── agents/<capability>.agent.md      # (or) a sub-agent plugin
```

> The CLI looks for the marketplace manifest at `marketplace.json`,
> `.plugin/marketplace.json`, `.github/plugin/marketplace.json`, or
> `.claude-plugin/marketplace.json`; a plugin's own manifest may be
> `.plugin/plugin.json`, `plugin.json`, `.github/plugin/plugin.json`, or
> `.claude-plugin/plugin.json`. The `.ai` + `.claude-plugin` spelling is the
> cross-tool local-marketplace convention used here.

**Declaring the `.ai` marketplace is REQUIRED — the directory alone does
nothing.** The `.ai/` tree is inert until the repo **declares it as a
locally-referenced plugin marketplace** in its own committed
`.github/copilot/settings.json` (the CLI also reads `.claude/settings.json` as a
fallback). Without the `extraKnownMarketplaces` `directory` entry, Copilot never
discovers the marketplace and none of its plugins load — no matter how many
`enabledPlugins` lines you add. So the declaration + per-plugin enable are two
required halves:

```json
{
  "extraKnownMarketplaces": {
    "my-repo-plugins": { "source": { "source": "directory", "path": "./.ai" } }
  },
  "enabledPlugins": {
    "some-capability@my-repo-plugins": true
  }
}
```

- The **`directory` source** with the repo-relative `path: "./.ai"` is what makes
  it a *locally-referenced* marketplace of the *current repo* — the `path` is
  resolved relative to the repo the settings.json lives in.
- Each plugin is then enabled by `"<name>@<marketplace-name>": true`, where
  `<marketplace-name>` is the `extraKnownMarketplaces` key you chose (not the
  directory name).

Because the source is **declarative and repo-relative**, a session launched in
the repo picks the marketplace up from the committed settings.json with **no
per-machine `copilot plugin marketplace add`** — the `.ai` plugins Just Work on a
fresh clone or a fork. (Contrast a global `marketplace add /PATH`, which records
an absolute machine path; prefer the declarative `directory` source for anything
that should travel with the repo.)

**Add a capability:** create `.ai/<name>/.claude-plugin/plugin.json` +
`skills/<name>/SKILL.md` (or `agents/<name>.agent.md`), add a `{name, description,
version, source: "./<name>"}` entry to `.ai/.claude-plugin/marketplace.json`, and
enable `"<name>@<marketplace>": true`. One plugin per capability keeps each
skill/agent independently enable-able and reviewable.

**Why prefer this over loose in-repo skills/agents:** the same plugin loads
whether the repo is launched **directly** (as its own harness) or **consumed by
another harness** (e.g. bound as a state/knowledge repo, or dispatched to by
agent-bridge, whose own-plugin staging resolves a repo's `.ai` `directory`
marketplace by anchor-relative path). Loose `.github/skills/` trees only load for
the launch repo and don't compose across those cases.

## Inspect or narrow activation safely

This skill ships a cross-platform Python helper at
`<skill-directory>/scripts/plugin-activation.py`. It reads both Copilot state
files strictly, preserves the managed leading comment in `config.json`, and
never edits installed payloads.

```bash
python <skill-directory>/scripts/plugin-activation.py inspect \
  some-plugin@my-marketplace --repo /path/to/repo

# Preview only (default)
python <skill-directory>/scripts/plugin-activation.py remove-user-activation \
  some-plugin@my-marketplace

# Apply after reviewing the exact changes
python <skill-directory>/scripts/plugin-activation.py remove-user-activation \
  some-plugin@my-marketplace --apply
```

`inspect` reports installed inventory, the legacy inventory `enabled` value,
user activation, repository activation, trust, and whether the plugin is
installed-but-not-user-enabled. `remove-user-activation` deletes only that
source-qualified key from user `enabledPlugins` and sets the matching
inventory record's legacy `enabled` field to `false`. It preserves inventory,
payload cache, marketplace registration, repository settings, trust, and every
unrelated JSON field. Missing optional files/keys are valid absence for
inspection; malformed JSON, duplicate identities, wrong container types, and
mutation without matching inventory are explicit errors.

## Alternative: global install (last resort — pollutes every session)

Install into the user profile **only** when the plugin is genuinely
machine-universal and there is no single control repo to pin it to. A global
install enables the plugin — and every sub-agent / MCP / hook it ships — for
**all repos and sessions** on the machine, polluting the tool namespace
everywhere; prefer repo-scoped or `.ai` enablement whenever a repo can own it.

```bash
copilot plugin marketplace add owner/my-marketplace-repo
copilot plugin install some-plugin@my-marketplace   # writes USER-level enablement + vendors payload globally
```

Manage with `copilot plugin list`, `copilot plugin update <name>@<market>`, and
`copilot plugin uninstall <name>@<market>`.

> **Fixing an accidental global enablement.** If you ran `copilot plugin install`
> for something that should have been repo-scoped: add the declarative line to the
> repo's `.github/copilot/settings.json`, then use the helper above to remove
> user-global activation without uninstalling inventory. The vendored payload
> remains available for the repository-scoped enablement.

## Payload vs runtime

`copilot plugin install` / `copilot plugin update` move only the plugin's
**payload** -- its source, skills, hooks, agents, and any session extensions --
into `~/.copilot/installed-plugins/`.

- A plugin that ships **only** skills/hooks/agents/extensions is fully deployed
  by the payload install; nothing else to do.
- A plugin that ships a **runtime** (a venv, `~/.local/bin` binstubs, or a
  long-running service) **also** runs its own installer to deploy that runtime
  from the payload. For such a plugin, a full update is two steps: refresh the
  payload, **then** run the plugin's install/setup step. The plugin's own docs
  (or an install skill it ships) say how.

> The CLI's "updated successfully" message after `copilot plugin update` refers
> to the **payload** only; a runtime plugin can read "updated" while its actual
> runtime (venv/binstub/service) is unchanged until its installer runs.

## Delivering a plugin's ambient guidance

Enabling a plugin makes its **skills** available, but skills are **on-demand**:
a skill's guidance applies most strongly the turn it is invoked and fades after
(see `authoring-skills` § Action-sequence vs ambient-guidance skills). So a
plugin that wants a rule to hold for the *rest of the session* cannot rely on a
skill body alone.

Follow `customizing-copilot:authoring-skills` § *sessionStart context
injection*:

1. **Keep ownership authoritative.** Repository identity, configuration,
   irreducible local invariants, and minimal fail-safes stay in the repository's
   `AGENTS.md` / custom instructions. Generic plugin policy stays with the
   plugin.
2. **Inject plugin-owned policy.** The owning plugin emits a concise,
   owner-marked, cwd/config-gated `sessionStart` `additionalContext` kernel and
   points at an on-demand skill for detailed mechanics. Source is
   allow-by-default; exclude only documented incompatible sources.
3. **Keep configuration declarative.** Repository overrides take precedence
   over operator policy only for an explicit plugin-declared allowlist of
   repo-delegable keys, which then precedes plugin defaults. Reject unknown or
   unauthorized keys. Safety, publication, attribution, and sanitization policy
   is non-overridable. Parse data; never execute configuration.
4. **Preserve fallback coverage.** Some headless/cloud launch paths do not load
   plugin hooks. Keep an irreducible static fallback for critical safety and
   publication rules on those paths.

Static `AGENTS.md` rules are still correct for genuinely repository-owned
invariants and minimal fallbacks. Copying a plugin's wholesale standing policy
into every adopting repository is a legacy compatibility path; migrate it only
after the plugin has an equivalent injection hook, then shrink the static copy
without losing its fail-safe. Any setup-written compatibility/fallback prose
must live in a stable marked region or dedicated rule file naming the owning
plugin. Setup reruns reconcile that region idempotently; later versions use the
same marker to shrink or remove it without duplicating or disturbing adjacent
repo-owned prose.

## Keeping a repo's plugins fresh automatically

Some control harnesses reconcile a repo's `enabledPlugins` on each interactive
session launch -- ensuring every enabled plugin's payload is installed and, for
runtime plugins, that the deployed runtime matches the installed payload version
(acting only on drift). Where that exists, booting via the harness's launcher
keeps the plugin set fresh without manual `plugin update` calls. (Headless
`copilot -p --autopilot` runs do **not** merge repo `enabledPlugins`, and some
headless/cloud paths do not load plugin hooks, so those machines need required
plugins and irreducible static policy supplied through their supported launch
path.)
