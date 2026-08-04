# Pattern: durable-vs-versioned-runtime

**Serves:** *Vision plugin-services* §Behaviors/`immutable-versioned-runtime`;
*Vision agent-index* §Behaviors/`warm-durable-engine`, §`capability-matched-engine-runtime`.
**Exemplars:** agent-index (the embedding-engine daemon).

## Problem

Most runtime plugins have exactly one runtime: the **versioned** venv the installer
swaps on every version bump (immutable, junction/symlink-selected — see the
install-contract). But some plugins carry a runtime component whose environment is
**expensive to build and slow to warm** — a heavy dependency stack (e.g. torch +
transformers) and a **loaded model** — that must **outlive routine version cutovers
of the light service**. If that stack lived in the versioned venv, every routine
`update` would rebuild it and cold-restart the warm process, re-paying the cost on
each cutover and on every consumer. It also does not belong on *every* machine —
only where the heavy work is hosted.

## Standard approach

Split the plugin into **two runtimes on separate lifecycles**:

- **The versioned runtime** — the light, swappable service/CLI in the standard
  versioned venv (`~/.<plugin>/versions/<v>` selected by the `current` junction).
  It carries **none** of the heavy stack; a routine `install`/`update` swaps only
  this runtime + junction.
- **The durable runtime** — a **persistent venv outside the versioned tree** (a
  stable `AGENT_<NAME>_..._HOME`, e.g. `~/.agent-index/engine/.venv`) holding the
  heavy stack, supervised as a **warm, always-on daemon** (its own scheduled
  task / systemd user unit). It is provisioned **once** (idempotent, skip-if-present)
  and **preserved across service updates**.

Binding rules that make the split real:

- **`update` never touches the durable runtime.** The service cutover swaps the
  versioned venv + junction and **leaves the durable venv and its warm daemon
  untouched** — no heavy rebuild, no model reload. Re-registering the daemon must
  not restart an already-warm one.
- **The durable runtime updates only by its own explicit path.** A dedicated verb
  (e.g. `engine-update`) rebuilds/upgrades the durable venv **and** restarts its
  daemon — the *one* place a restart is intended — decoupled from the service
  `update`.
- **The light runtime is a pure client of the durable one.** All heavy work routes
  to the daemon over its stable local API (the versioned service defaults to an
  `external` engine mode and embeds nothing in-process), so the service venv stays
  free of the heavy stack and a version-mismatched service still talks to the same
  warm daemon.
- **Presence is config-resolved, capability-matched, never hardcoded.** Whether a
  machine hosts the durable runtime is decided by **adoption** (role config, not a
  machine list in the plugin); the host's device/profile is **matched to real
  capabilities** (accelerator + specs, with a hard floor), and the daemon degrades
  within that floor rather than wedging.

### Gotchas this pattern encodes

- **Don't leak the heavy stack into the versioned venv.** Provisioning the durable
  env must target the durable venv explicitly; installing the heavy extra into the
  swappable venv (even once) re-couples the lifecycles and is the exact mistake this
  pattern exists to prevent.
- **`uv venv` has no pip.** Provision the durable venv with `uv pip install --python
  <durable>` (or `python -m venv` + `-m pip`), not `python -m pip` against a
  `uv`-created venv.
- **Provision is best-effort; the light service never blocks on it.** A failed heavy
  build (no wheel, no accelerator) must leave the torch-free service fully working —
  provision non-fatally and let capability matching / runtime fallback handle it.
- **A warm daemon stays warm.** Registration and service updates must be
  start-only-if-not-serving; only the explicit durable-runtime update restarts it.

## Rationale

Paying a slow, heavy build **once per host** — and never again on a routine service
cutover — is the whole point: version velocity on the light service is decoupled
from the cost of the model stack, the warm process keeps its loaded model across
updates, and consumers that only *use* the capability carry none of the weight. It
is the same immutable-index / immutable-runtime instinct applied to an expensive,
stateful *process*: the durable thing survives the swap of the disposable thing.

## See Also

- Intent: [`visions/plugins/agent-index/`](../../visions/plugins/agent-index/README.md) §warm-durable-engine
- Related: [`service-lifecycle-supervision`](service-lifecycle-supervision.md) ·
  [`install-vs-adopt-boundary`](install-vs-adopt-boundary.md) ·
  the deploy contract [`install-contract.md`](../install-contract.md)
- Hub: [`docs/patterns/`](README.md)
