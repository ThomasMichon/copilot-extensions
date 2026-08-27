# Pattern: runtime-self-provisioning

**Serves:** *Vision plugin-services* §Features/`self-provisioning-runtime`,
`self-contained-runtime`; §Behaviors/`standalone-reachability`; and the binding
invariant **"Enabling a runtime provisions it"** (`docs/patterns/README.md`) — this
pattern is a **realization** of that invariant that holds **without depending on one
particular sibling being the session launcher**, and **in confined environments**
(the GitHub Copilot app, a cloud agent) where neither a launch-wrapper nor
session-start hooks are guaranteed.
**Exemplar:** agent-codespaces (payload-local self-provisioning shim + `stamp`/`provision`
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

1. **Payload-local self-provisioning shim — the safety net (needs only a shell + the payload).**
   The plugin's generated payload command is a *smart shim*: when its versioned venv is
   present it is a thin exec (fast path); when it is **absent** it **provisions on
   first use**, then dispatches. It must:
   - **announce, never block silently** — a human line on stderr **and** a
     machine-readable signal (e.g. `::agent-provisioning:: plugin=<n> eta_seconds=..`)
     so a *caller* can extend its timeout instead of killing a slow first call;
   - **serialize** concurrent first-invocations (a file lock) so a thundering herd
     builds the venv once;
   - **fail fast with the real cause** (a non-zero exit + actionable message; a
     durable status marker) — never a silent half-state.

2. **Session-start bootstrap — the optimization (needs the env to fire hooks).**
   The plugin's `bootstrap-check` sessionStart hook, when the runtime is
   unprovisioned, runs the installer's cheap **`stamp`** action — record the
   payload/snapshot and any compatibility management wrapper while deferring the
   venv build to first use. The separate command-catalog hook maps the logical
   command to the payload-local shim already present in the installed payload.
   Stamp is grace-window-safe because it builds no venv.

3. **Skill-driven payload fallback — the broadest reach (needs only that the
   env loads the plugin's skills + lets the agent run a shell).**
   Every operational skill uses the exact command-catalog `argv`. If hooks did
   not publish the catalog, the skill uses the host's plugin-management surface
   to select an explicit `plugin@marketplace` payload and invokes that payload's
   generated shim directly. It never scans every installed marketplace or
   chooses the first match. An `availability: unavailable` entry—or absence of
   an explicit `ready` entry—is not ready and fails closed rather than
   improvising an install. The shim self-provisions on first use. This reaches
   the **Copilot app / cloud agent** with no sibling launcher or agent-worktrees
   dependency.

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
covers the confined envs, the hook layer optimizes the CLI, and the payload-shim
layer is the always-correct floor. It is the à-la-carte principle
([`a-la-carte-independence.md`](a-la-carte-independence.md)) applied to *provisioning
itself*: a lone plugin, in any host, brings up its own runtime.

## See Also

- Invariant + principles: [`README.md`](README.md) ("Enabling a runtime provisions
  it"; principles 1–3, 6).
- Composition side: [`a-la-carte-independence.md`](a-la-carte-independence.md).
- Runtime deploy contract: [`../install-contract.md`](../install-contract.md).
- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
  §self-provisioning-runtime.
