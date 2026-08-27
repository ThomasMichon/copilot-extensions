# Pattern: codespace-repo-provenance

**Serves:** *Vision agent-codespaces* §Features/`config-by-adoption`,
`headless-agent-hosting`; §Provenance — the venue side of the fabric's
"dispatch a repo you have no local checkout of" golden path.
**Exemplars:** `example-web-harness` (example-marketplace) → `agent-codespaces` (the honorer);
composes with `agent-bridge` for `codespace:<name>` dispatch.
**Related:** [`a-la-carte-independence`](a-la-carte-independence.md) (the
provider-manifest seam agent-codespaces uses to reach agent-bridge);
[`install-vs-adopt-boundary`](install-vs-adopt-boundary.md) (why a provider config
is lowest-precedence and never writes back).

## Problem

A control-harness agent is asked to *"make a change in `<product>`"* for a product
that has **no local checkout** — it lives only as a GitHub CodeSpace built from a
`<product>-codespaces` vessel repo. For a dispatched agent to actually land a
change there, two facts must be known **before** the CodeSpace is reached, and
neither is derivable from the CodeSpace name alone:

1. **Repo provenance** — *which product checkout on the box the agent should work
   in.* A CodeSpace's own repository is the **vessel** (`<org>/<product>-codespaces`),
   but the product is checked out at `/workspaces/<product>`, not
   `/workspaces/<product>-codespaces`. Launch the agent in the wrong folder and its
   tools operate against an empty/vessel directory, or `/home/<user>` (dotfiles#1274).
   The vessel→product link, the machine size, the pinned devcontainer, the ADO
   host for credential relay, and any per-venue provision hooks are all
   **provenance** the resolver needs.
2. **In-venue capability** — *which plugins the agent inside the CodeSpace needs.*
   The dispatched agent wants the product's own operator skills
   (`<product>-agent`: build/test/killswitch/lifecycle lore) loaded **inside** the
   venue — not on the harness.

The naive answer is "author a control-plane repo that adopts a
`.agent-codespaces/config.yaml`." But the golden path — *a colleague enables three
plugins and dispatches* — must work with **no control-plane repo at all**. The
provenance and the in-venue plugin list have to travel **with the product's own
harness plugin**, discovered by convention.

## Standard approach

A **`<repo>-harness`** plugin (see the `authoring-harness-plugins` skill for the
family) carries the venue's provenance and its in-venue plugin list, and
`agent-codespaces` honors both through two dependency-free, convention-discovered
seams. The harness plugin **depends on `agent-codespaces`** (the honorer) and
writes into no repo.

### Seam 1 — repo provenance via the config-provider drop-in (`config.d`)

The harness plugin **ships** its venue policy in place, as a *supplementary*
`.agent-codespaces/config.yaml` fragment under its own `references/`, and makes it
discoverable by dropping a **pointer** into the user-level
`~/.agent-codespaces/config.d/` directory from a `sessionStart` hook:

```
<repo>-harness/
├── plugin.json                              # dependsOn agent-codespaces; codespacePlugins (Seam 2)
├── hooks.json                               # sessionStart -> register-config-provider.{sh,ps1}
├── references/agent-codespaces/config.yaml  # the venue provenance (shipped, read in place)
└── scripts/register-config-provider.{sh,ps1}
```

The hook writes one schema-v1 JSON pointer — normally through
`agent-codespaces/scripts/write-config-dropin.{ps1,sh}` — into
`~/.agent-codespaces/config.d/`. It records the plugin's exact
`name@marketplace`, canonical in-place plugin root, and absolute `config.yaml`
target. `agent-codespaces` verifies that the source is effectively enabled and
still resolves to that exact root before it reads the target, then merges it at
the **lowest precedence** — a provider *default* that any adopted-repo/cwd config
still overrides. The old `<repo>-harness.conf` single-path shape remains a
recognized, advisory legacy format only during migration.

The provenance itself lives under `repos.<vessel>`:

```yaml
# references/agent-codespaces/config.yaml — supplementary; only what convention
# can't derive. Machine/location defaults, /workspaces/<basename>, and the
# github.com + ADO credential relay are all handled automatically.
credentials:
  ado_host: my-org.visualstudio.com          # bare get-access-token host for the org
repos:
  example-org/example-web-codespaces:          # the VESSEL repo (the CodeSpace's own repository)
    workspace_repo: example-web                    # -> agent lands in /workspaces/example-web (the PRODUCT)
    machine_type: largePremiumLinux256gb
    devcontainer_path: .devcontainer/devcontainer.json   # pin when the vessel ships >1
    provision:                                  # per-venue setup deployed on SSH connect
      files: [ ... ]
      on_connect: [ ... ]
```

`workspace_repo` is the crux of provenance: it is what makes
`effective_acp_command_for(<vessel>)` emit `cd /workspaces/example-web && copilot
--acp --stdio --allow-all-tools`, and what `resolved_workspace_folder_for` publishes
to agent-bridge as the ACP `session/new` cwd — so a dispatched agent's tools are
rooted in the product checkout.

**Why a pointer, not a copy, and not a writeback:** the edge stays one-way and
dependency-free. The plugin ships a default and *points at it in place*;
agent-codespaces discovers it dynamically; neither writes into the other's repo; a
plugin update keeps the pointed config live (no stale copy). Because it merges
**last**, it never overrides a consumer who *does* keep an adopted repo — it is the
default when there is none. This keeps the *install/adopt boundary* intact: a
provider default is discovered, not an adopt-time repo mutation.

### Seam 2 — in-venue plugins via `codespacePlugins`

The harness plugin declares, in its `plugin.json`, the plugins to install **into**
a CodeSpace on connect — the in-venue `<repo>-agent` and friends — via a custom
`codespacePlugins` array (an *unrecognized* top-level manifest field the core CLI
ignores; `agent-codespaces` is its sole consumer):

```jsonc
{
  "name": "example-web-harness",
  "dependencies": [ { "name": "agent-codespaces", "marketplace": "copilot-extensions" } ],
  "codespacePlugins": [
    { "source": "example-web-agent@example-marketplace", "enable": true,
      "forWorkspaceRepo": "example-org/example-web*" }
  ]
}
```

`forWorkspaceRepo` (string, list, or omitted) **scopes** an entry to CodeSpaces of
a given workspace repo; omit it and the entry applies to *every* CodeSpace this
harness provisions. `agent-codespaces` (`resolve_codespace_plugins`) sweeps the
installed harness plugins, filters by the target CodeSpace's workspace repo,
de-duplicates, and injects the resolved set into the CodeSpace's **user**
`~/.copilot/settings.json` on connect — so the in-venue agent loads them without a
human touching the CodeSpace.

### The composition (end to end)

```
control harness (3 plugins enabled)                     GitHub CodeSpace
┌─────────────────────────────────────────┐            ┌────────────────────────────┐
│ agent-bridge   ── codespace:<name> ─────▶│  dispatch  │ /workspaces/<product>       │
│ agent-codespaces (honors both seams)     │──────────▶ │  + <product>-agent plugin   │
│ <repo>-harness  ── config.d pointer      │            │    (injected, Seam 2)       │
│                 └─ codespacePlugins       │            │  ACP cwd = provenance folder │
└─────────────────────────────────────────┘            └────────────────────────────┘
     Seam 1 gives provenance ─────────────┘  (workspace_repo -> /workspaces/<product>)
```

- **`agent-bridge`** sources the `codespace:` namespace from `agent-codespaces`
  (the provider-manifest seam, see `a-la-carte-independence`) and dispatches.
- **`agent-codespaces`** resolves the launch: reads the provenance (Seam 1) to
  build the `cd <folder> && copilot …` command and publish the ACP cwd, and injects
  the in-venue plugins (Seam 2).
- **`<repo>-harness`** supplies both — with **no control-plane repo** and **no
  writeback** into any repo.

## Rationale

- **Golden path with no control-plane repo.** The provenance and in-venue plugin
  list are the *product's* facts, so they belong with the *product's* harness
  plugin — versioned with it, updated with it. A colleague enables three plugins and
  dispatches; they never author a hub repo. (The heavier repo-scaffolding path — a
  `setup-venue` that writes config + tools into a control-plane repo for a full
  build-capable venue — stays available for consumers who want it, but is **not
  required** for the golden path.)
- **One-way, dependency-free edges.** Both seams are discovery, not coupling: a
  filesystem pointer and an unrecognized manifest field. `agent-codespaces` never
  imports the harness plugin; the harness plugin never writes into agent-codespaces'
  state beyond its own drop-in pointer. A stale/uninstalled provider is skipped, not
  fatal; it warns with an actionable finding, and `agent-codespaces doctor`
  identifies the exact pointer and cleanup/re-registration remedy under the
  suite-wide
  [`drop-in-registry-hygiene`](drop-in-registry-hygiene.md) contract.
- **Provenance is authoritative, not guessed.** `workspace_repo` makes the
  vessel→product link explicit, so the ACP cwd is the product checkout by
  construction — not a fragile parse of the launch string, and not `/home/<user>`.
- **Precedence keeps adoption sovereign.** The drop-in merges last, so a consumer
  who *does* adopt a repo config always wins; the provider is the floor, never a
  ceiling.

## Anti-patterns

- **Installing a `<repo>-harness` (or its injected `<repo>-agent`) on the venue.**
  The harness plugin is central-harness-only; the `<repo>-agent` is injected into the
  CodeSpace, never enabled on the harness. (See the naming/propagation grammar in
  `authoring-harness-plugins`.)
- **Copying the provider `config.yaml` into `config.d/`** instead of a pointer — a
  copy drifts on plugin update. Ship the config in `references/` and point at it.
- **Relying on the drop-in to *override* a consumer's config.** It is lowest
  precedence by design; put anything that must win in the consumer's adopted repo.
- **Omitting the `agent-codespaces` dependency** from a plugin that uses either
  seam. The honorer must be present for the seams to fire.
- **Baking the workspace folder into a raw `acp_command`** when a `workspace_repo`
  would do — the derived path also feeds the structured ACP cwd; a hand-written
  `cd` does not.

## See also

- Authoring the plugin family: the `authoring-harness-plugins` skill
  (`customizing-copilot`) — this pattern is its CodeSpace-venue provisioning arm.
- The honorer's surfaces: `plugins/agent-codespaces/README.md`
  (§ *Repo provenance & the config-provider seam*), and `config.py`
  (`discover_dropin_configs`, `effective_acp_command_for`,
  `resolved_workspace_folder_for`), `codespace_plugins.py`
  (`resolve_codespace_plugins`).
- The provider-manifest seam agent-codespaces uses to reach agent-bridge:
  [`a-la-carte-independence`](a-la-carte-independence.md).
- Drop-in warning and cleanup semantics:
  [`drop-in-registry-hygiene`](drop-in-registry-hygiene.md).
