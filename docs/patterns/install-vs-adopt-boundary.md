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

**Split the verbs by scope, and let only adoption mutate a repo.**

| | `install` / `update` | `register` / `adopt` |
|---|---|---|
| **Scope** | machine-local only — runtime payloads, local user config, local services | the repo + its per-machine wiring |
| **Machine-local config** | may migrate it to a newer **schema** (format); never alters **behaviors** | writes / updates it to reflect new repo preferences |
| **Repo config** | **read + warn** on invalid/deprecated conventions; never alters | **explicitly alters** (takes preferences; writes repo + local) |
| **Repo git hooks** | **never touches** | **injects / validates** (the only repo-git mutation) |
| **Cadence** | re-runs on every deploy/update; repo-agnostic | run to adopt or **re-adopt** and change wiring |

**Rule:** mutating a repo — or changing user *behaviors* — is an **adopt**
concern, never an **install/update** one. install/update is machine-local and,
w.r.t. behaviors, read-only: it may migrate config *schema* and *warn* on stale
repo conventions, but it never changes what the config *means*.

### The lifecycle it produces

- **Blank-slate machine:** install the plugins → open an agent in the target repo
  → **install** (runtime payloads + a local-config scaffold) then **adopt** (after
  taking the user's preferences: repo-specific hooks + local/repo config, wiring
  the repo up). Adopt writes back into **both** the repo and machine-local config
  as needed.
- **Ongoing:** `update` (or a repo-scoped update) refreshes plugins/services and,
  at most, migrates local config **schema** after validation — it does **not**
  change behaviors. To change how a repo is wired, **re-adopt**.

### Ownership falls out of adopt

Because adoption is the only repo-mutating verb and you adopt only repos you own,
the "may we modify this repo's git?" question is answered structurally: **owned +
adopted → yes; contributed-to-but-not-adopted → no.** No separate ownership flag
is required (though one may be made explicit). A strong implicit signal that a
repo is owned is a **committed, in-repo config that declares its own workflow** —
you can only commit workflow config into a repo you own; a repo you merely
contribute to carries any such preference **machine-locally**, if at all.

## Gotchas this pattern encodes

- **A "uses PRs" flag is not an ownership signal.** A repo you contribute to is
  often PR-gated too (you open PRs *into* it). Gating repo mutation on "uses PRs"
  would wrongly mutate an external repo. Gate on **adoption / ownership**, not on
  a PR flag.
- **A shadowing hooks path silently disables injected hooks.** If a repo's
  `core.hooksPath` points away from where the managed hooks are installed, git
  ignores them and the guard never runs. Reconciling that pointer is a repo
  mutation → an **adopt** step; install/update may only *warn* that it is stale.
- **Idempotent re-adopt, not install-time drift-repair.** When repo wiring must be
  refreshed (a convention changed, a stale pointer), do it by **re-adopting** —
  never by teaching install/update to quietly fix repos.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
  §Features/`install-adopt-boundary`.
- Related: [`install-contract.md`](../install-contract.md) — the runtime
  deploy/version/footprint contract that `install`/`update` honor (machine-local).
- Supervision verbs (a different verb set — starting/stopping a *running service*,
  not mutating a repo): [`service-lifecycle-supervision.md`](service-lifecycle-supervision.md).
