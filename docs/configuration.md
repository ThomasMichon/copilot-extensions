# Configuration — In the Repo vs On the Machine

Every piece of configuration in this suite lives in one of two homes, and picking
the right one is the whole game:

- **In the repo** — committed, shared by everyone who uses the repo, and it
  *travels with the repo*. Describes **how the repo is worked** (which plugins,
  what workflow, what topology).
- **Machine-local / user-global** — under `~/` (`~/.copilot`,
  `~/.copilot-extensions/marketplaces/<marketplace-id>/`, and transitional
  `~/.agent-*` / `~/.{project}` roots), **per-user and per-machine**, and
  **never committed**. Holds secrets, absolute paths, machine identity, and
  personal preferences.

This split is not arbitrary — it follows the
[install-vs-adopt boundary](patterns/install-vs-adopt-boundary.md): **`install` /
`update` only ever touch machine-local state** (and may migrate its *schema*),
while **`register` / `adopt` is an explicit integration boundary whose exact
write scope is plugin-specific**. Some adoption commands bootstrap repository
configuration; others only project already-published repository state into
machine-local configuration.

`agent-bridge config adopt` is in the second category: it reads topology from a
repository and writes `~/.agent-bridge/config.yaml`. It does not edit the
repository. Change repository topology in a worktree, publish it through that
repository's contribution flow, deploy/sync the canonical checkout, and only
then re-run adoption when the machine-local projection needs refreshing.

## The rule

| | In the repo | Machine-local / user-global |
|---|-------------|------------------------------|
| **Put here** | Settings that should be **shared** and are **safe to commit** — the plugin set, PR/workflow policy, machine & agent topology, related-repo links | Anything **per-user / per-machine** or **secret** — absolute paths, machine identity, tokens, personal toggles |
| **Written by** | You or a repo-bootstrap command, through the repository's normal contribution flow | `install` / `update`, machine-local `register` / `adopt` commands, and you per machine |
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
| `.github/copilot/settings.json` | Which plugins the repo enables + the marketplace (`enabledPlugins`, `extraKnownMarketplaces`) | `customizing-copilot:installing-plugins` skill / normal repo edit |
| `<repo>/.agent-worktrees/config.yaml` | The repo's own worktree settings — PR mode (`pr:`), workflow, defaults shared by every machine | agent-worktrees repo bootstrap / normal repo edit |
| `<repo>/.agent-logger.yaml` (or documented aliases) | Shared session-log location, naming/template, and optional writer voice seams | agent-logger repo setup / normal repo edit |
| `<repo>/.agent-worktrees/related.yaml` | The related-repo index (role, locus, delegate) from this repo's POV | `related add` |
| `machines.yaml` | SSH machine topology the mesh plugins read (control repo) | normal repo edit; agent-bridge adoption only reads it |
| `<repo>/.agent-codespaces/config.yaml` | **Supplementary** Codespace overrides + credential-relay policy (control repo). Most repos need none — machine defaults, `/workspaces/<basename>`, and the git-credential relay are convention-derived. Legacy repo-root `codespaces.yaml` still read (relocate with `config migrate`). | `codespaces-setup` |
| `containers.yaml` | Container fleet defaults (control repo) | `containers-fleet` |
| `.github/agents/<name>.mcp.yaml` | A **repo-scoped** agent-mcp bridge config | you (per the `agent-mcp:agent-mcp` skill) |
| `<repo>/.context-handoff/config.yaml` | Optional repository-owned soft/hard context utilization percentages | context-handoff repo setup / normal repo edit |
| `<repo>/.copilot-extensions/efforts/config.json` | Exact repository adoption marker for required effort-backed planning (`version: 1`, `enforcement: required`) | `efforts:efforts-setup` / normal repo edit |
| `tools/setup/setup.{ps1,sh}` | The session setup script run before Copilot launches | `create-setup-script` |

### Machine-local / user-global (never committed)

| File | Purpose | Written by |
|------|---------|-----------|
| `~/.copilot/settings.json` | Per-user CLI settings — **experimental mode**, personal plugin toggles | you (once per machine) |
| `~/.copilot-extensions/installation-mode.json` | OS-profile-pinned desired installation mode: legacy by default, with namespaced opt-in and exact marketplace/plugin overrides | configurator / you |
| `~/.copilot-extensions/maintenance` + `maintenance.json` | Existence gate plus strict ownership sidecar that quiesces new plugin activity during user-wide surgical maintenance | maintenance command / you |
| `~/.agent-worktrees/config.yaml` | Machine-wide defaults: `srcroot`, `machine`, `platform`, `copilot_profiles` | `install` |
| `~/.agent-worktrees/repos.yaml` · `projects.yaml` | The repos registry + adopted-projects registry (checkout paths, class) | `repos` / `register` |
| `~/.{project}/config.yaml` | Per-machine overrides + the adapter that makes a *foreign* repo compatible | `register` (machine wiring) |
| `~/.agent-bridge/config.yaml` · `auth.yaml` | Bridge service config + bearer token (**secret**) | `install`, `agent-bridge config adopt`, and the service |
| `~/.agent-logger/config.yaml` | Session-logging config (store dir, sync target) | `install` / you |
| `~/.agent-mcp/bridges/<name>` | A **personal / cross-repo** agent-mcp bridge config | you (per the `agent-mcp:agent-mcp` skill) |
| `~/.budget-guidance/config.json` | Inert, per-user current budget readings and source authority | you / `budget-guidance-setup` |
| `~/.agent-*/deploy-manifest.json`, runtime state | Per-machine runtime footprint (version, source, venv) | `install` / `update` |

### Marketplace installation cells

The target machine-local contract qualifies plugin-owned configuration and
state by globally distinguishing marketplace provenance:

```text
~/.copilot-extensions/
  marketplaces/<marketplace-id>/
    namespace.json
    plugins/<plugin-id>/{install.json,state,run,logs,...}
    repos/<stable-repo-id>/<plugin-id>/...
```

Namespaced placement is not enabled merely because a plugin understands cells.
The OS-profile-pinned
`~/.copilot-extensions/installation-mode.json` flag is default-off and may
enable cells globally or for an exact source-derived marketplace/plugin. It is
resolved independently of ordinary `HOME` and durable-home overrides; repository
`.copilot-extensions/` is never searched for this policy. Policy expresses
desired mode; `<cell>/plugins/<plugin-id>/installation-activation.json` records
the actual authoritative root after safe first install or migration. Existing
unattributed legacy state therefore remains legacy and reports
`migration-required` until explicitly migrated. Removing the flag from an
active cell reports `deactivation-required` rather than switching roots.

The maintenance marker is orthogonal: it suppresses new hooks, reconciliation,
provisioning, service ensure/start, scheduled work, and dispatch while leaving
read-only status/doctor available. A plugin-scoped marker and sidecar may live
beside that plugin's cell state. The stable policy, activation, legacy
tombstone, maintenance, and effective-mode contracts are defined by the
[Install Contract](install-contract.md#installation-mode-governance).

Committed repository policy remains distribution-neutral. New plugin-owned
repository configuration converges on
`<repo>/.copilot-extensions/<plugin-id>/...`; a marketplace-specific overlay is
an explicit adoption decision, never an install-time fork of ordinary committed
configuration.

Machine-local project adoption belongs to the adopting cell and uses stable
repository identity rather than basename alone. Two cells may adopt the same
repository without sharing registry, worktree, session, lease, or generated
invocation state. A singleton committed integration surface must carry
attributable ownership or use an intentionally composable format.

The `~/.agent-*` and `~/.{project}` rows above document the current legacy
layout during migration. New-first, legacy-fallback readers may preserve a
bounded compatibility window, but install/update never claims or merges
unqualified state automatically. See
[marketplace-installation-cells](patterns/marketplace-installation-cells.md).

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
  [agent-worktrees config-reference § Config sources](../plugins/agent-worktrees/docs/config-reference.md#config-sources-layered).

## See also

- [Pattern: install-vs-adopt-boundary](patterns/install-vs-adopt-boundary.md) —
  which lifecycle verb may write what (the rule this page rests on).
- [Vision: plugin-services](../visions/plugin-services/README.md) —
  §`install-adopt-boundary` / §`install-leaves-repos-unaltered`.
- [Install Contract](install-contract.md) — the machine-local runtime deploy +
  schema-migration contract `install`/`update` honor.
- [Pattern: marketplace-installation-cells](patterns/marketplace-installation-cells.md)
  — how globally distinguishing provenance owns machine-local runtime,
  configuration, adoption state, and lifecycle artifacts.
- [Architecture § The control-harness repo](architecture.md#the-control-harness-repo)
  — how a control repo's committed config feeds the mesh plugins.
- [agent-worktrees Configuration Reference](../plugins/agent-worktrees/docs/config-reference.md)
  — every agent-worktrees key and the in-repo overlay.
