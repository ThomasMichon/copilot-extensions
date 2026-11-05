# Participating-writer qualification before assignment admission

Status: **reviewed implementation refinement; ordinary activation remains disabled**.
Parent: [monitor and confirmed-state effort](README.md).
Requirement: [submission responsibility](submission-closeout-amendment.md).
Coordination: [#9709](https://github.com/ThomasMichon/copilot-extensions/issues/9709).

This agent-proposed refinement makes the existing participating-writer gate
concrete; it does not change submission semantics or grant rollout authority.
It closes the existing
[worktree-responsibility intent](../../../visions/plugins/agent-dispatch/task-outputs-and-review/README.md#submission-retains-worktree-responsibility)
using [provider-first contract evolution](../../../docs/patterns/service-contract-evolution.md).
The original Plan and Validation Plan remain binding.
See the [qualification refinement](https://github.com/ThomasMichon/copilot-extensions/pull/10762)
and [dated journal](journal/2026/11.05-submission-resubmission.md#resident-only-observation-source).

## Established boundaries

`WorktreeRelationshipWriter` negotiates the selected executing CLI and requires
an independent `admit_workspace` callback. Its relationship calls use the
registered direct owner; they do not establish another resident's support.
Ordinary coordinator construction still supplies no writer.

Worktrees already exposes the
[incarnation-bound relationship contract](../../../plugins/agent-worktrees/docs/resource-relationship.md#workspace-incarnation-and-executing-writer-support).
Its resident rendezvous advertises its own serializer declaration and version-2
relationship verb. That file is discovery, not a live response or admission
receipt. A newer CLI cannot qualify a resident that still runs older code.

Bridge does not directly serialize the Worktrees YAML record on the traced
local acquisition/lineage path. It delegates through Worktrees commands.
Acquisition and lineage now share the canonical owner prefix and scrubbed
environment. Lineage no longer independently selects
`agent_registry._agent_worktrees_bin`. Isolated source-process controls preserve
an existing protected claim and nonce through delegated registration and
succession, and routing controls refuse a retired legacy owner or missing
same-cell peer without substituting a global owner. These controls do not
qualify a running selection. Bridge's current health capabilities do not yet
certify preservation or coherent selection.
Session-lock files and Bridge's own database are separate storage boundaries;
their presence is not evidence about the Worktrees record serializer.

## Selected admission requirements

The assembling owner must identify the actual participating writers for the
original workspace incarnation: selected CLI executions, the resident, Bridge's
delegated record writes and any other admitted producer. It must retain the
existing authority and writer-selection boundaries, not infer a closed cohort
from an empty process list, package version, source checkout or caller boolean.
An unaccounted writer keeps governed assignment disabled; unrelated supported
legacy operations do not become globally unavailable.

Reuse the existing authenticated resident transport and version-2
`resource_relationship` handler. An exact `inspect` with `allow_missing` and the
original `expected_creation_nonce` is a read-only observation. Its live response
must establish the actual checked nonce and executing serializer contract.
Do not route that observation through a helper that can substitute the calling
CLI's direct fallback: that would answer the wrong writer question. A missing,
old, unreachable or mismatched resident is unknown support, never a successful
cohort observation. No reserve, bind, close, monitor bootstrap or test claim is
needed merely to discover support.

Bridge's participating lineage writes must use its selected Worktrees owner
prefix and scrubbed environment rather than independently choosing a global
binstub. Qualify that implementation through the running Bridge owner and the
commands it actually selects. A declaration about selection behavior is distinct
from the selected Worktrees runtime's preservation support; neither substitutes
for the other. Resolve the generation-bound observation through the existing
owner surfaces before adding ordinary assembly. Do not invent a universal
handshake or put another subprocess preflight on every record save.

Admission is scoped to the original workspace and current participating
selections. A changed resident, delegated command selection, workspace nonce or
unaccounted producer invalidates the relevant observation before new governed
effects. A previously observed response is not a permanent ready permit.
Effect-side nonce checks and the existing retained-request reconciliation remain
mandatory. Unknown responses retain the same assignment and claim.

## Implementation and validation order

- [x] Qualify the actual resident without direct fallback, using its existing
      authenticated inspection. Exercise compatible, prior, missing, changed
      and unavailable providers; inspection must leave record bytes unchanged.
      These are isolated source/transport controls, not installed cohort admission.
- [ ] Reconcile Bridge's acquisition and lineage owner selection, preserving
      its supported legacy behavior and installation isolation. Exercise real
      delegated registration/succession against a record with an existing
      protected claim and nonce, including incompatible selected writers.
      Source selection/preservation controls are implemented; actual
      generation-bound selection and incompatible-writer admission remain open.
- [ ] Expose the minimum generation-bound evidence through the participating
      owners, then construct the conditional assignment writer with real
      independent admission. A copied rendezvous, substituted direct response,
      newer payload or changed selection must not satisfy it.
- [ ] Integrate ordinary preparation/advancement consumers only after their
      selected providers are qualified. Preserve original queue, reservation,
      creation nonce, relationship, task/worktree/native binding and all answers
      through submission, rejection and resubmission.
- [ ] Run a fresh whole-stack case only after its first failed boundary changes.
      Verify repeated independent rejection and acceptance through ordinary
      callers, not internal-method fixtures or a previously failed container.

The initial source/transport controls are not complete cohort qualification.
Ordinary CLI/API/MCP migration, non-circular decline, stopped-native recovery,
confirmation policy and actual running-service acceptance retain their separate
gates. Neither this refinement nor a source merge enables production allocation,
updates a runtime, retires another writer or authorizes cleanup.
