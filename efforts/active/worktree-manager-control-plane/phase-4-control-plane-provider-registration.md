# Phase 4 — Generic control-plane-provider registration for the bare-invocation seam

- **Parent effort:** [`README.md`](README.md) § Phase 4
- **Status:** Landed — design and implementation both complete.
- **Tracks:** the Phase 4 checklist item closed from `README.md`
- **Governing vision:** [`visions/installer`](../../../visions/installer/README.md)
  §`bare-invocation-launches-configurator`,
  §`control-plane-is-optional-plugins-are-self-sufficient`,
  §`knows-the-plugins-without-coupling-to-them`; [`visions/picker`](../../../visions/picker/README.md)
  §`front-door-entry`

## Why this slice exists

Phase 4 already extracted the **interactive front door** out of the
`agent-worktrees` plugin: a bare, no-args project binstub now tries to hand the
session to the standalone Worktree Manager, falling back safely when the Manager
is absent or broken. What remained open was **how the seam discovers the
provider**.

Today that discovery is not truly a provider contract. `agent-worktrees`
health-probes a literal `worktree-manager` binstub on `PATH`, version-gates it,
and otherwise treats the seam as if no provider exists. That works for the
shipped Manager, but it is not a reusable registration another conforming
control-plane provider could satisfy without also being named
`worktree-manager`.

This document closes that design gap **without** widening scope into a
speculative multi-provider marketplace or a new provider-command protocol. The
problem to solve is specifically: **replace the literal binstub-name check with
an explicit registration contract**.

## Reconciliation to existing repo patterns

Use the repo's already-established **consumer-owned manifest registry** pattern,
not a new ambient-`PATH` heuristic:

- `agent-bridge` namespace providers register by writing attributed JSON
  manifests into a consumer-owned `providers.d/` directory; the consumer drives
  the provider's absolute command over a process boundary instead of importing it
  or rediscovering it on `PATH`.
- `agent-worktrees` claim providers use the same broader idea in payload-owned
  form (`claim-providers/*.json`): a static manifest declares capability and the
  consumer validates it before dispatch.
- The installer vision's
  §`knows-the-plugins-without-coupling-to-them` principle already prefers
  **declarative one-way knowledge** over special imports or name-based probing.

So the right shape here is: **a manifest registry owned by the seam's
consumer (`agent-worktrees`), written by the provider's own install/update
flow, and carrying an absolute command plus compatibility metadata.**

## Decision

### 1. Registration mechanism

Adopt a **consumer-owned manifest registry** at:

```text
~/.agent-worktrees/control-plane-providers.d/<provider>.json
```

with test/operator override:

```text
AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR
```

This is the sole discovery surface for the bare-invocation seam. `agent-worktrees`
does **not** look for a literal `worktree-manager` command on `PATH` anymore.

Why this mechanism:

- It mirrors existing repo idioms (`providers.d`-style explicit registration).
- It avoids ambient-`PATH` hijacking as the discovery authority. The manifest
  carries the exact absolute command the provider wants invoked.
- It keeps the dependency one-way. `agent-worktrees` never imports provider code
  and does not need to know where or how the provider installed itself beyond
  the registry entry.
- It preserves current diagnostics naturally: discovery picks a registered
  command, then the existing fast `--version` health probe still decides whether
  that command is usable, broken, incompatible, or too old.

### 2. What a provider must declare

Manifest schema v1:

```json
{
  "schema_version": 1,
  "provider": "worktree-manager",
  "description": "copilot-extensions Worktree Manager",
  "command": ["C:/Users/me/.local/bin/worktree-manager.cmd"],
  "minimum_version": "0.1.0-dev21",
  "provider_root": "C:/Users/me/.worktree-manager"
}
```

Field meaning:

| Field | Required | Meaning |
|---|---|---|
| `schema_version` | yes | Registry schema; currently `1`. |
| `provider` | yes | Stable provider identity, safe-token string; this is the selector, not the filename alone. |
| `description` | no | Human label for diagnostics. |
| `command` | yes | **Absolute argv prefix** the seam invokes. This is the real provider entry point; no `PATH` lookup. |
| `minimum_version` | yes | Provider-authored compatibility floor for the registered command, compared against its `--version` output using the existing `MAJOR.MINOR.PATCH(-devN)?` parse rules. |
| `provider_root` | yes | Stable install root for diagnostics/attribution; not execution authority by itself. |

Important boundary: this slice does **not** invent a second command protocol.
A conforming provider registers a command that already speaks the existing
front-door contract the Worktree Manager speaks today:

- `--version` prints a parseable version string.
- no args opens the provider's no-project front door.
- `--project <name>` opens that project's interactive front door.
- the existing subcommands that already route through the Manager seam
  (`update`, `terminal-fragment`, `profiles`, etc.) keep their current CLI
  shape.

That is deliberate. The open question was **registration**, not a redesign of
the provider-side CLI contract.

### 3. Discovery and precedence

This remains a **one-active-provider-at-a-time** seam, not a speculative
multi-provider broker.

Selection rule:

1. Scan the registry directory for valid manifests.
2. If `AGENT_WORKTREES_CONTROL_PLANE_PROVIDER=<provider>` is set, use that
   manifest only.
3. Otherwise, if exactly one valid manifest exists, use it.
4. Otherwise (zero valid, or multiple valid without an explicit selection),
   treat the seam as having **no usable registered provider** and fall back to
   the bundled Picker / install trigger exactly as Phase 4 already requires.

This is intentionally simple:

- it genuinely permits third-party providers;
- it does not require `agent-worktrees` to invent a ranking marketplace; and
- it fails closed instead of guessing when more than one provider is present.

### 4. Backward compatibility and cutover

`worktree-manager` becomes the **reference implementation** of this contract by
writing the manifest above from its own `self-install` flow. After this change,
the seam finds Worktree Manager **through the registry manifest**, not through a
hardcoded `shutil.which("worktree-manager")`.

The existing Manager-specific launcher-dir probe
(`_usable_worktree_manager_launcher_dir()`) remains **out of scope and
unchanged**. It serves a different purpose: locating the currently selected
version slot's relocated launcher scripts for the Mux/AHP launch path. That is
not the bare-invocation provider-registration seam; it is a Worktree-Manager
runtime-layout probe and remains appropriately Manager-specific.

## Ordered implementation plan

### Step 1 — Define and emit the registration artifact

Add a versioned reference manifest to `worktree-manager`, and extend
`self_install.py` so a real install/update writes
`~/.agent-worktrees/control-plane-providers.d/worktree-manager.json`
atomically with the current absolute binstub path and provider root.

**Validation**

- `worktree-manager` self-install tests prove the manifest is written with the
  expected fields and repaired on reinstall.
- The manifest writer uses the same atomic temp-file + replace pattern already
  used for other provider-manifest registries.

### Step 2 — Cut the seam over to registry discovery

Replace the front door's literal `worktree-manager` PATH lookup with:

- manifest scan,
- deterministic provider selection,
- existing `--version` probe against the manifest's `command`,
- existing user-facing broken/incompatible/older fallback behavior,
- unchanged `_core_helper` override seam.

The real behavior change is only **how the candidate command is found**, not how
its health is judged once found.

**Validation**

- A registered, healthy Worktree Manager is preferred exactly as before.
- Broken / incompatible / older registered providers are rejected with the same
  quality of explanation and the same fallback path.
- An unregistered same-named-or-differently-named command on `PATH` is ignored.

### Step 3 — Prove genericity, then close the effort item

Add `agent-worktrees` tests that:

1. prove `worktree-manager` is discovered via the registry contract;
2. prove a synthetic differently named provider is also discovered when
   registered; and
3. prove multiple/invalid/unregistered states fail closed.

Then update the parent effort README:

- replace the open Phase 4 item with a landed description citing this doc and
  the landing PR; and
- add a dated Journal entry recording the contract choice and why the
  launcher-dir probe stayed separate.

## Consequences

After this slice:

- **Worktree Manager remains the default shipped provider**, but only as the
  first concrete registrant of a generic contract.
- `agent-worktrees` no longer equates "usable control plane provider" with
  "there is a `worktree-manager` command on PATH".
- A future alternative provider can satisfy the seam by doing two things only:
  implement the existing provider CLI contract and write a conforming registry
  manifest.
