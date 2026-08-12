# Pattern: runtime-self-provisioning

**Serves:** *Vision plugin-services* §Features/`self-provisioning-runtime`,
`self-contained-runtime`; §Behaviors/`standalone-reachability`; and the binding
invariant **"Enabling a runtime provisions it"** (`docs/patterns/README.md`) — this
pattern is a **realization** of that invariant that holds **without depending on one
particular sibling being the session launcher**, and **in confined environments**
(the GitHub Copilot app, a cloud agent) where neither a launch-wrapper nor
session-start hooks are guaranteed.
**Exemplar:** agent-codespaces (self-provisioning binstub + `stamp`/`provision`
actions + vendored-uv + pip-index bridge + bootstrap-check auto-stamp + skill
readiness self-check).

## Problem

The invariant says: enabling a runtime plugin provisions it, with **no manual
install step** and **no dependency on one *particular* sibling being the session
launcher**. The realization that shipped first — `agent-worktrees reconcile-plugins`
run from the agent-worktrees launch-wrapper — is a genuine convenience, but it
**couples every runtime's provisioning to agent-worktrees being installed, provisioned,
and wrapping the launch**. That coupling breaks the invariant in exactly the
environments a user is most likely to hit:

- a plain **Copilot CLI** session with only *this* plugin enabled (no agent-worktrees);
- the **GitHub Copilot app** or a **cloud agent**, which may load a plugin's skills
  but not run its session-start hooks, and certainly not agent-worktrees' launcher.

A plugin must provision itself with only what **its own** install brought, using
whatever bootstrap surface the current environment actually offers.

## Standard approach — layered, independent bootstrap (defense in depth)

Provisioning is delivered by **three independent layers**, ordered by how much of
the environment they need. Each works on its own; together they make provisioning
succeed across the widest set of environments. **None depends on a sibling.**

1. **Self-provisioning binstub — the safety net (needs only a shell + the binstub on disk).**
   The plugin's one canonical binstub is a *smart shim*: when its versioned venv is
   present it is a thin exec (fast path); when it is **absent** it **provisions on
   first use**, then dispatches. It must:
   - **announce, never block silently** — a human line on stderr **and** a
     machine-readable signal (e.g. `::agent-provisioning:: plugin=<n> eta_seconds=..`)
     so a *caller* can extend its timeout instead of killing a slow first call;
   - **serialize** concurrent first-invocations (a file lock) so a thundering herd
     builds the venv once;
   - **fail fast with the real cause** (a non-zero exit + actionable message; a
     durable status marker) — never a silent half-state.

2. **Session-start auto-stamp — the optimization (needs the env to fire hooks).**
   The plugin's `bootstrap-check` sessionStart hook, when the runtime is
   unprovisioned, runs the installer's cheap **`stamp`** action — *splat the binstub
   + payload marker only, deferring the venv build to first use*. Stamp is
   grace-window-safe (no venv build on the hook), so the binstub is on PATH *this*
   session and self-provisions when first called. Where hooks don't fire, this layer
   is simply absent — the binstub is stamped by layer 3 instead.

3. **Skill-driven readiness self-check — the broadest reach (needs only that the
   env loads the plugin's skills + lets the agent run a shell).**
   Every operational skill opens with a **fail-closed readiness check**: is the CLI
   ready? If not, it tells the agent to run the plugin's **own** installer
   (`install.sh stamp`, from the plugin payload) to deploy the binstub — which then
   self-provisions on first use. This is the layer that reaches the **Copilot app /
   cloud agent**: it needs no hook, no launch-wrapper, no agent-worktrees — only the
   skill (already loaded) and a shell (which the agent has). Absence of an explicit
   *ready* is treated as *not ready* (never inferred from the absence of an error).

**Toolchain self-acquisition (cross-cutting).** Provisioning must not dead-end on a
missing toolchain. The installer **vendors a standalone `uv`** when uv is absent
(pristine box), and **derives uv's package index from pip's config** (`pip config`,
else the `pip.conf` files) when public PyPI is TLS-blocked (governed box) — so a
first install succeeds with no external setup.

**Cross-plugin reconcile is an optimization, not a dependency.** When
agent-worktrees *is* present, its `provision-check` may reconcile the whole enabled
set at launch — a nice convenience. But no plugin may **require** it: remove
agent-worktrees and every plugin still provisions itself through layers 1–3.

## Rationale

The invariant is about the *user's* experience — "your only action is enabling" —
and it explicitly forbids a particular-sibling-as-launcher dependency. Layering the
bootstrap by environmental need is what makes that true everywhere: the skill layer
covers the confined envs, the hook layer optimizes the CLI, and the binstub layer is
the always-correct floor. It is the à-la-carte principle
([`a-la-carte-independence.md`](a-la-carte-independence.md)) applied to *provisioning
itself*: a lone plugin, in any host, brings up its own runtime.

## See Also

- Invariant + principles: [`README.md`](README.md) ("Enabling a runtime provisions
  it"; principles 1–3, 6).
- Composition side: [`a-la-carte-independence.md`](a-la-carte-independence.md).
- Runtime deploy contract: [`../install-contract.md`](../install-contract.md).
- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
  §self-provisioning-runtime.
