# Pattern: install-vs-adopt-boundary

**Serves:** *Vision plugin-services* §Features/`install-adopt-boundary`,
§Behaviors/`install-leaves-repos-unaltered`.
**Exemplars:** agent-worktrees (`install` / `update` vs `register` / `adopt`).

## Problem

The lifecycle verbs a plugin exposes act at two very different scopes:

- **machine-local** state — the installed runtime, per-user config, local services;
- a **repo** — its committed config and its built-in **git hooks**.

Conflating them is a real hazard. If `install`/`update` mutates a repo — e.g.
injects git hooks — it (a) re-touches repo state on every routine deploy, (b) can
change a repo's built-in git behavior the user never asked for, and worst (c) can
do so to a repo the user does **not own** and only contributes to (a repo that
governs its own hooks, typically server-side). Repo mutation must be
**deliberate and consent-taking**, not a side effect of a routine update.

## Standard approach

**Split the verbs by scope. Install never mutates a repo; adoption mutates only
the scopes its command explicitly owns.**

| | `install` / `update` | `register` / `adopt` |
|---|---|---|
| **Scope** | machine-local only — runtime payloads, local user config, local services | explicit integration; repo, machine-local, or both according to the command's contract |
| **Machine-local config** | may migrate it to a newer **schema** (format); never alters **behaviors** | writes / updates it to reflect new repo preferences |
| **Repo config** | **read + warn** on invalid/deprecated conventions; never alters | may alter only when the command explicitly owns repo bootstrap; otherwise reads published repo state |
| **Repo git hooks** | **never touches** | may inject / validate only for a repo-adopting command that explicitly owns git integration |
| **Cadence** | re-runs on every deploy/update; repo-agnostic | run to adopt or **re-adopt** and change wiring |

**Rule:** mutating a repo is never an **install/update** concern. It happens
through the repository's normal contribution flow, optionally initiated by a
repo-bootstrap adoption command whose contract explicitly says it writes repo
state. A machine-local adoption command does not gain repo-write authority from
the word "adopt": for example, `agent-bridge config adopt` reads published
topology and writes only user-level bridge configuration.

### The lifecycle it produces

- **Blank-slate machine:** install the plugins, publish any repository-owned
  configuration through that repository's contribution flow, then run each
  plugin's adoption command to establish the machine-local integration. A
  repo-bootstrap adoption command may author repo state when its documented
  contract says so; a projection command such as `agent-bridge config adopt`
  only records paths in user-level state.
- **Ongoing:** `update` (or a repo-scoped update) refreshes plugins/services and,
  at most, migrates local config **schema** after validation — it does **not**
  change behaviors. Publish repo changes first; **re-adopt** only when the
  machine-local projection must be refreshed.

### Repo mutation requires explicit authority

Repo-write authority comes from the repository's ownership and contribution
flow, plus a command contract that explicitly owns repo integration — not merely
from running a command named `adopt`. A strong implicit signal that a repo is
owned is a **committed, in-repo config that declares its own workflow** — you can
only publish workflow config into a repo you own; a repo you merely contribute
to carries any such preference **machine-locally**, if at all.

## Gotchas this pattern encodes

- **A "uses PRs" flag is not an ownership signal.** A repo you contribute to is
  often PR-gated too (you open PRs *into* it). Gating repo mutation on "uses PRs"
  would wrongly mutate an external repo. Gate on **repo ownership plus an
  explicit repo-write contract**, not on a PR flag or the verb name.
- **A shadowing hooks path silently disables injected hooks.** If a repo's
  `core.hooksPath` points away from where the managed hooks are installed, git
  ignores them and the guard never runs. Reconciling that pointer is a repo
  mutation → use the owning plugin's explicit repo-integration command;
  install/update may only *warn* that it is stale.
- **Idempotent explicit integration, not install-time drift-repair.** When repo
  wiring must be refreshed, use the owning repo-integration command. When only a
  machine-local projection is stale, re-run that projection command against
  published canonical state. Never teach install/update to quietly fix repos.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
  §Features/`install-adopt-boundary`.
- Related: [`install-contract.md`](../install-contract.md) — the runtime
  deploy/version/footprint contract that `install`/`update` honor (machine-local).
- Supervision verbs (a different verb set — starting/stopping a *running service*,
  not mutating a repo): [`service-lifecycle-supervision.md`](service-lifecycle-supervision.md).
