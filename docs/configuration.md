# Configuration — In the Repo vs On the Machine

Every piece of configuration in this suite lives in one of two homes, and picking
the right one is the whole game:

- **In the repo** — committed, shared by everyone who uses the repo, and it
  *travels with the repo*. Describes **how the repo is worked** (which plugins,
  what workflow, what topology).
- **Machine-local / user-global** — under `~/` (`~/.copilot`, `~/.agent-*`,
  `~/.{project}`), **per-user and per-machine**, and **never committed**. Holds
  secrets, absolute paths, machine names, and personal preferences.

This split is not arbitrary — it follows the
[install-vs-adopt boundary](patterns/install-vs-adopt-boundary.md): **`install` /
`update` only ever touch machine-local state** (and may migrate its *schema*),
while **`register` / `adopt` is the only verb that writes into the repo**. So
"where does this setting go?" and "what writes it?" are the same question.

## The rule

| | In the repo | Machine-local / user-global |
|---|-------------|------------------------------|
| **Put here** | Settings that should be **shared** and are **safe to commit** — the plugin set, PR/workflow policy, machine & agent topology, related-repo links | Anything **per-user / per-machine** or **secret** — absolute paths, machine identity, tokens, personal toggles |
| **Written by** | `register` / `adopt` (and you, editing committed files) | `install` / `update` (deploys + migrates schema) and you, per machine |
| **Committed?** | **Yes** | **No** (git-ignored / outside the tree) |
| **Applies to** | Everyone who clones/uses the repo | Only this user on this machine |

> **Ownership tell:** a **committed, in-repo config that declares its own
> workflow** is itself the signal that you *own* the repo — you can only commit
> workflow into a repo you control. A repo you merely *contribute* to keeps any
> such preference **machine-locally** instead (per the install-vs-adopt-boundary
> pattern). This is why `install`/`update` never write repo config: they can't
> know it's yours to change.

## The map

### In-repo (committed)

| File | Purpose | Written by |
|------|---------|-----------|
| `.github/copilot/settings.json` | Which plugins the repo enables + the marketplace (`enabledPlugins`, `extraKnownMarketplaces`) | `installing-plugins` skill / adopt |
| `<repo>/.agent-worktrees/config.yaml` | The repo's own worktree settings — PR mode (`pr:`), workflow, defaults shared by every machine | `register` / adopt |
| `<repo>/.agent-logger.yaml` (or documented aliases) | Shared session-log location, naming/template, and optional writer voice seams | you / adopt |
| `<repo>/.agent-worktrees/related.yaml` | The related-repo index (role, locus, delegate) from this repo's POV | `related add` |
| `machines.yaml` | SSH machine topology the mesh plugins read (control repo) | you / adopt |
| `codespaces.yaml` | Codespace defaults + credential-relay policy (control repo) | `codespaces-setup` |
| `containers.yaml` | Container fleet defaults (control repo) | `containers-fleet` |
| `.github/agents/<name>.mcp.yaml` | A **repo-scoped** agent-mcp bridge config | you (per the `agent-mcp` skill) |
| `tools/setup/setup.{ps1,sh}` | The session setup script run before Copilot launches | `create-setup-script` |

### Machine-local / user-global (never committed)

| File | Purpose | Written by |
|------|---------|-----------|
| `~/.copilot/settings.json` | Per-user CLI settings — **experimental mode**, personal plugin toggles | you (once per machine) |
| `~/.agent-worktrees/config.yaml` | Machine-wide defaults: `srcroot`, `machine`, `platform`, `copilot_profiles` | `install` |
| `~/.agent-worktrees/repos.yaml` · `projects.yaml` | The repos registry + adopted-projects registry (checkout paths, class) | `repos` / `register` |
| `~/.{project}/config.yaml` | Per-machine overrides + the adapter that makes a *foreign* repo compatible | `register` (machine wiring) |
| `~/.agent-bridge/config.yaml` · `auth.yaml` | Bridge service config + bearer token (**secret**) | `install` / the service |
| `~/.agent-logger/config.yaml` | Session-logging config (store dir, sync target) | `install` / you |
| `~/.agent-mcp/bridges/<name>` | A **personal / cross-repo** agent-mcp bridge config | you (per the `agent-mcp` skill) |
| `~/.agent-*/deploy-manifest.json`, runtime state | Per-machine runtime footprint (version, source, venv) | `install` / `update` |

## Two things that trip people up

- **The same capability has both an in-repo and a user-global slot.** `agent-mcp`
  is the clearest case: a **repo-scoped** MCP bridge belongs in
  `.github/agents/<name>.mcp.yaml` (committed, shared with the repo); a
  **personal / cross-repo** one belongs in `~/.agent-mcp/bridges/<name>`
  (machine-local). Same file format, different home, chosen by *who the config is
  for*. The plugin-enable split is analogous: repo-scoped `enabledPlugins` in
  `.github/copilot/settings.json` vs personal ones in `~/.copilot/settings.json`.
- **Layering, not either/or (agent-worktrees).** agent-worktrees actually merges
  **three** tiers per key — machine-local `~/.{project}/config.yaml` (highest) >
  in-repo `<anchor>/.agent-worktrees/config.yaml` > global
  `~/.agent-worktrees/config.yaml` (lowest). A repo designed for this system needs
  **no** machine-local file; you add one only to *override* on a specific machine
  or to adapt a foreign repo. Full precedence rules:
  [agent-worktrees config-reference § Three config sources](../plugins/agent-worktrees/docs/config-reference.md#three-config-sources-layered).

## See also

- [Pattern: install-vs-adopt-boundary](patterns/install-vs-adopt-boundary.md) —
  which lifecycle verb may write what (the rule this page rests on).
- [Vision: plugin-services](../visions/plugin-services/README.md) —
  §`install-adopt-boundary` / §`install-leaves-repos-unaltered`.
- [Install Contract](install-contract.md) — the machine-local runtime deploy +
  schema-migration contract `install`/`update` honor.
- [Architecture § The control-harness repo](architecture.md#the-control-harness-repo)
  — how a control repo's committed config feeds the mesh plugins.
- [agent-worktrees Configuration Reference](../plugins/agent-worktrees/docs/config-reference.md)
  — every agent-worktrees key and the in-repo overlay.
