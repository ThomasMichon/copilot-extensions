# Managed runtime deployment - implementation boundaries

Parent: [Source-Managed Runtime Installs](README.md).

This is the parent effort's Phase 3 implementation and validation map.
The reviewed request separates runtime publication from Copilot plugin delivery;
it does not disable plugin hooks or make Manager a runtime dependency of
standalone plugins. No policy is installed or enabled by this plan.

## Preserve the existing owners

Installation-mode governance owns legacy versus namespaced selection,
activation and transfer. Command binding owns source-qualified installation
identity. Canonical runtime construction/publication owns immutable candidates,
redirects and rollback. Manager orchestrates an explicitly selected install;
Agent Machines owns scheduled invocation.

Managed deployment is an additional authority decision at those existing
publication boundaries, not another installation mode, source of actual-root
truth, supervisor or installer. The temporary bootstrap killswitch is not this
policy: it covers a different trigger and legacy scope.

The concrete reading path is the
[install contract](../../../docs/install-contract.md#installation-mode-governance),
[payload invocation contract](../../../libs/payload-invocation/README.md) and
[runtime primitive](../../../libs/versioned-runtime/README.md).

## Required behavior

| Situation | Required result |
|-----------|-----------------|
| No managed-deployment opt-in | Preserve ordinary standalone bootstrap, first-use and explicit installer behavior. |
| Managed installation, healthy compatible runtime | Plugin hooks and commands continue using its selected installed generation; plugin-version drift does not copy payloads, construct slots or replace redirects. |
| Managed installation, runtime absent or incompatible | Return a precise installation-owned readiness/recovery action without silently provisioning from the plugin payload. Non-deployment hook behavior still executes. |
| Explicit Manager-owned update | Pass the selected scope and pinned source through the same canonical installer, ownership, readiness, activation and retention path. An environment-variable hint alone is not deployment authority. |
| Invalid or unsupported deployment policy | Refuse new publication explicitly rather than falling back to self-provisioning; preserve validated installed operation and independent hook behavior where their contracts allow it. |
| Another source, marketplace or installation | Preserve foreign ownership. Managed deployment never authorizes an ambient same-named command, root or source. |

The gate covers both session bootstrap and indirect first use, including
deferred stamping, native installer helpers and generated payload invocation.
Checking only the session-start entry point is insufficient. Conversely,
returning early from an entire hook or extension can suppress guidance,
registration or other non-deployment behavior the operator explicitly retained.

## Implementation seams - agent-recommended

- Resolve policy against the actual installation and existing source/plugin
  identity before runtime or redirect writes. Preserve explicit opt-out,
  malformed-policy and absent-policy distinctions; do not repurpose the binary
  installation-mode policy.
- Trace one complete bootstrap-to-publication and first-use path before
  partitioning native adopter changes. Put shared decisions in the owning
  canonical helper rather than maintaining independent per-plugin policy
  parsers.
- Keep plugin-content invocation identity distinct from runtime artifact source.
  A marketplace adapter must be able to reach its explicitly managed runtime
  without requiring identical delivery origins, while existing command-binding
  and installation-transfer checks still reject foreign owners. Do not remove
  source-fingerprint checks as a shortcut.
- Preserve thin adapters and declared capability/version-skew readiness.
  Neither a successful handshake nor a compatibility failure grants runtime
  deployment permission.
- Keep current role selection, deferred construction, transaction locks,
  candidate health and failure preservation intact. Policy changes do not
  authorize starting unused services, resetting source or rewriting live slots.

## Validation before claiming Phase 3 complete

- [ ] Exercise the real session-bootstrap and indirect first-use paths with
  absent, standalone, managed, malformed and unsupported policy on both native
  platforms, scoped to the addressed installation.
- [ ] Inventory runtime slots, selected/last-known-good markers, snapshots,
  redirects and deployment manifests before and after managed plugin actions;
  assert no payload publication or redirect replacement.
- [ ] Assert retained non-deployment hook behavior positively, not merely a
  successful process exit or absence of writes.
- [ ] Verify a healthy selected runtime remains callable and a missing or
  incompatible runtime returns the declared readiness outcome without repair.
- [ ] Prove explicit Manager installation still performs the canonical update,
  and an unauthenticated caller hint cannot bypass the gate.
- [ ] Cover source-qualified command binding and foreign ownership negatives
  for both legacy and namespaced contexts without weakening current governance.
- [ ] Exercise newer-plugin/older-runtime and older-plugin/newer-runtime
  combinations at declared capability boundaries, then repeat assembled-use
  and native readiness evidence required by the parent effort.
