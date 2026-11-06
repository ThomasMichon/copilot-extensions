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
certify preservation by the selected serializer. Its additive
`worktree_record_delegation: 1` declaration now covers coherent selection and
reports the selected installation root through existing health. The actual
Bridge slot and the delegated executing Worktrees build must still be observed
and matched independently.
Session-lock files and Bridge's own database are separate storage boundaries;
their presence is not evidence about the Worktrees record serializer.

The delegated child revalidates the installation receipts and asks the selected
peer's shipped Python resolver, where available, for its actual interpreter.
Older native-only peers retain their compatibility execution path. That interpreter
can belong to a physical `.venv` application environment inside the version
slot, not only a historical direct-slot environment. The shared launcher now
accepts both layouts without accepting redirected slot/environment directories.
This repairs execution selection; it adds no executing-build observation or
cohort admission. Its unchanged dynamic launch prefix is still not an immutable
build binding, and the health root alone cannot establish one.

The installed candidate already publishes its build UUID inside the immutable
slot, independently of root-wide deployment stamps. Bridge's source observation
reads that UUID for legacy selection through the existing canonical resolver,
with selection and metadata rechecks and no child process.
The merged source migration uses the peer-owned Python resolver for both
same-cell execution and child-free selected-interpreter observation, under the
existing receipt/governance fences. Native-only peers remain unknown.
This intentionally changes the native selector's differing edge cases rather
than substituting a Python observation for unchanged native execution.
Even a known selected-slot UUID is
not the delegated process's live serializer response or a pinned future launch.
Independent admission must retain both distinctions before ordinary integration.

The opt-in pinned-prefix source path now retains the selected immutable
Worktrees interpreter through the existing owner/peer launch boundary and
slot-local retention bootstrap. An explicit caller can use the exact same
command for capability qualification and subsequent acquisition/lineage
operations. Runtime selector movement does not silently replace that writer;
current installation governance still applies. The retained prefix binds the
original completion and build-generation UUID, checked under the existing
retention lease before site imports. Missing or same-path recreated slots
refuse without substitution. Native-only or unsupported older bootstraps remain
supported by ordinary legacy calls but cannot satisfy this optional route.
This is a source compatibility seam, not ordinary assembly, independent
workspace/resident admission or running-service qualification.

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
      Legacy and same-cell selected-build observation source is implemented;
      conditional admission and
      changed-selection invalidation remain open. The provider-first optional
      executing-identity requirement is available in the existing locked
      relationship transaction; ordinary consumers and independent
      admission are not yet assembled. The explicit Dispatch adapter now
      conditionally carries that guard and presents the selected identity
      to its admission callback; real cohort assembly, Bridge's delegated
      executing-writer qualification and changed-selection invalidation remain
      open before ordinary preparation or advancement.
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
