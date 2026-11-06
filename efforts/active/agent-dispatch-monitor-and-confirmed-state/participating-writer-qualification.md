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
      Source selection/preservation and opt-in retained execution merged in
      #10976, including qualification of the actual receiving session serializer.
      Actual installed generation-bound selection and incompatible-writer
      admission remain open.
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
      to its admission callback. Opt-in Bridge delegated executing-writer source
      qualification merged in #10976; real installed cohort assembly and
      changed-selection invalidation remain open before ordinary preparation
      or advancement.
      The next provider-first refinement uses Bridge's existing exact-delivery
      inspection to requalify the original journal's command and workspace,
      bracketed by its active instance and unchanged journal. This observation
      neither selects a writer nor admits the resident or an unaccounted
      producer; conditional Dispatch assembly remains a separate next step.
      That optional observation merged in #11001 with current-source HTTP,
      process and actor-free controls. The original workspace and complete
      installed cohort remain required before ordinary assembly.
      Queue-owned original-request revalidation merged in #11034: each external
      relationship call now rereads the durable queue, explicit authority, exact
      audited worker and complete original request. This source seam does not
      qualify the actual CLI/resident/Bridge participant set or close the cohort.
      Opt-in selected CLI/resident assembly merged in #11047. It retains the
      original installed command and reobserves the authenticated resident
      before each relationship call, while requiring both independent callbacks.
      Bridge's future keyed creation, original continuation journal and other
      admitted producers remain separate assembly gates. Bounded source controls
      do not qualify installed participants or the complete cohort.
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

## Continuing provider-first slice: retained acquisition execution

This is an **agent-proposed implementation refinement** of the unchecked
delegated-writer item above, not a replacement acceptance gate. Its opt-in
provider/consumer source merged in #10976; unpinned acquisition and lineage
retain their dynamic-selection behavior. The original workspace and actual
installed participating cohort remain unqualified. A health-selected root and
UUID alone cannot qualify their later writes.

1. Expand the existing acquisition provider with an explicitly negotiated,
   opt-in installed-writer requirement. Older request versions and ordinary
   unpinned calls remain unchanged. Unsupported providers must refuse the
   required form before allocation/native effects, never drop its fields or
   retry without the requirement. Use the existing capability and acquisition
   version owners; do not add a separate qualification RPC or service.
2. Resolve the owner through the merged pinned-selection helper, then obtain
   the executing serializer's capability response using that exact command.
   Require its installed root/build identity, original-incarnation support
   and preservation contract. A selector observation alone is insufficient.
   The caller supplies an expected identity, not an executable path, command,
   environment or claimed successful qualification.
3. Retain the provider-selected command inside the existing acquisition
   operation and use it for all participating owner reads and writes, including
   delegated registration and lineage. The same operation retains its original
   workspace, nonce, task/reservation, native conversation and delivery identity.
   Check nonce-bearing record/transaction responses through their existing
   owners. Do not retrofit a historical allocation or create a replacement.
4. Store the requirement and provider-selected original-build binding with
   the existing keyed acquisition/request journal, not a parallel registry.
   A restart or retained retry must recover that same binding or refuse with
   original custody intact. Never recapture a replacement build as the original,
   infer that an unknown operation did not run, or reopen another native.
   Missing or recreated slots retain the merged pre-import refusal.
5. Recheck the relevant Bridge instance and selected-writer evidence through
   existing owners when assembling independent admission. This provider slice
   qualifies only Bridge's delegated writer behavior. It does not enumerate or
   admit the resident, another CLI, or every producer of the shared ledger.
   Keep ordinary Dispatch construction without a writer until the original
   workspace and complete participating cohort are independently admitted.

### Slice validation

- [ ] Compatible installed writer: actual retained command capabilities precede
      delegated registration/succession; the protected claim and original
      creation nonce survive through the existing acquisition workflow.
- [x] Prior/unknown/incompatible writer and unsupported request: refuse before
      owner writes or native effects; baseline request versions still work.
      Bounded source contract controls passed in #10976; this is not an
      installed-provider or closed-cohort observation.
- [ ] Runtime selector movement: later calls use the originally qualified
      build, not the newly selected interpreter. Changed completion, same-path
      recreation and governance refusal remain pre-import failures.
- [x] Lost response, concurrent replay and process recovery: preserve the
      original journal/native/request binding or report unresolved custody,
      with no replacement allocation, conversation or dropped requirement.
      Source journal, new-manager recovery and controlled-transport cases
      passed in #10976. Actual native recovery acceptance remains open.
- [ ] Native continuation and later lineage retain the same writer requirement;
      changed workspace nonce, owner or executing build cannot be adopted.
- [x] Exercise actual process/delegation boundaries in the bounded integration
      lane and retain focused contract units in the existing required packet.
      Source controls do not stand in for the separately required running
      Bridge, resident, closed-cohort or ordinary whole-stack acceptance.
      #10976 binds every finite writer child to its committed entrypoint and
      full source closure, with no sibling-write or provenance-guard exemption.

## Continuing provider-first slice: ordinary keyed creation

This is an **agent-proposed implementation refinement** of the same unchecked
delegated-writer and ordinary-integration items, not an activation decision.
Its documentation-only refinement was reviewed and merged in #11013; opt-in
provider/consumer source subsequently merged in #11022. The bounded source
credit below does not qualify the actual installed participating cohort.
Ordinary managed ACP startup uses keyed `create` on an already prepared
workspace, followed by exact `send` and later strict `resume`. Its owner is
`workspace_creation` and the existing `workspace_embodiments` row, not
`AcquisitionRuntime` or an acquisition delivery journal. The historical shared
caller prefix does not change those owners; its compatibility repair merged
in #11012.

1. Extend the existing keyed local creation operation with an explicitly
   negotiated optional writer requirement, using the existing request/client,
   health capability and per-attempt cutover fences. Initially support only
   ACP creation into an explicitly identified, already prepared local
   worktree. Refuse an unsupported unkeyed, allocating, provider-instance,
   remote or CLI form before allocation, native launch or prompt effects.
   Existing unpinned creation and continuation remain supported unchanged.
2. Accept only expected installed-writer identity and original creation nonce,
   together with the existing exact project/workspace facts. The provider
   selects its own retained command and qualifies its actual executing
   serializer through the existing capability response. Never accept a caller
   command, substitute an ordinary resident or infer support from a payload
   version or selected-root observation.
3. Retain the original requirement, provider-selected command and existing
   creation facts atomically in that same keyed embodiment journal before
   native preparation or any delegated session-record mutation. A matching
   retry or new frontend recovers that original binding; changed requirements,
   keys, project, path or nonce refuse. Do not retrofit an existing unpinned
   attempt, manufacture an acquisition journal or replace the ordinary route.
4. Carry that original owner scope through actual session preparation,
   registration and binding, then recover it for strict native resume and
   later lineage. Reuse shared writer-selection/qualification mechanics where
   appropriate, but keep creation and acquisition journal custody distinct.
   Required receiving commands use the already-qualified executing serializer
   and locked nonce checks. Unpinned auxiliary writes retain their behavior.
5. Preserve the same creation/native/session/first-turn history across response
   loss, process recovery and registration failure. Never recreate the native
   because its writer or original journal is unavailable. Any required
   handoff must retain its original successor and seed outcome in the same
   owning journal before effects; an unknown outcome is not permission for
   another successor or prompt.
6. Expose read-only requalification through the existing exact-session
   observation owner with a separately negotiated optional form. Resolve the
   original keyed journal, not a new selected writer; bracket the original
   workspace/serializer reads with the unchanged journal and active Bridge
   instance. A legacy response cannot silently satisfy required evidence.
   This is not a ready permit or native/delivery proof.

This provider slice does **not** admit the earlier first workspace relationship:
that transaction precedes keyed Bridge creation and still requires independent
original queue/request correlation. It also does not enumerate every producer,
close the participating cohort or supply an ordinary coordinator writer.
Keep ordinary governed assignment disabled until those separate gates pass.
Installation, rollout, allocation and original native-resource holds remain.

The ordinary keyed-create CLI transport subsequently merged in #11040.
Its `--worktree-writer-file` carries the strict file/stdin requirement through
the existing client and capability fence, with early invalid-form, remote/URL
and conflicting-stdin refusal. Bounded actual parser/client and actor-free
controls, plus the 245-control current HTTP capture, qualify that transport
only. Dispatch has not yet assembled or supplied its independent admission;
the original first relationship, actual participating owners and complete
cohort remain separately required.

### Keyed-creation slice validation

- [x] Prove supported new/new and mixed-version refusal through the actual
      parser/client/HTTP owner, including a cutover to an older provider;
      omitted requirements retain legacy behavior.
      #11022 passes the bounded source/ASGI negotiation controls.
- [x] Exercise the actual keyed creation/strict continuation and delegated
      serializer boundaries on a record with its protected claim and original
      nonce. An ordinary-compatible but unqualified resident cannot receive
      required registration, binding or succession.
      #11022 passes finite actual parser/serializer controls with fixture build
      identity and native transport, not installed-runtime acceptance.
- [x] Prove same-key response-loss, concurrent replay and new-frontend recovery
      retain the original command, workspace, native and first turn; prior
      unpinned rows cannot acquire a new requirement.
      #11022 passes source SQLite, journal and controlled-transport recovery.
- [ ] Refuse changed identity/nonce/selection, missing or recreated slots and
      incomplete journal evidence without a replacement allocation, native,
      successor or seed. Preserve registration-pending dormancy.
      #11022 covers source identity/nonce/journal refusal, unknown successor/seed
      retention and registration dormancy. Original installed selection and
      missing/recreated-slot admission remain open under the shared launcher.
- [x] Prove optional exact-session requalification is read-only and refuses
      changed workspace, journal, readiness or Bridge instance, with no
      reselection or acquisition.
      #11022 passes bounded HTTP/SQLite and actor-free observations.
- [x] Keep finite actor-free controls in the admitted packet and bounded
      actual-process/transport controls in their existing integration lane.
      Source passes do not close installed/cohort or ordinary lifecycle gates.
      #11022 passes 4,335 admitted units and a fetchable-source 229-control
      HTTP-52 capture with separate no-write verification.

## Continuing provider-first slice: original relationship request admission

This is an **agent-proposed source refinement** of the existing independent
admission and ordinary-integration items, not ordinary construction or activation.
The documentation-only refinement was reviewed and merged in #11028;
implementation subsequently merged in #11031. The five bounded source controls
below passed; actual original-queue/cohort and installed observations remain open.
The first protected workspace relationship precedes keyed Bridge creation.
`task_events.task_relationship` already owns its original request: workspace,
reservation, task/generation, audited assignment event, request ID, authority and
queue identity/recovery. Queue reconciliation currently passes only workspace,
operation and operation payload to its selected writer; the adapter's independent
callback receives only workspace and executing-writer identity. A successful
post-effect receipt comparison cannot replace admission of that original request
before the first reserve.

1. Add an explicit request-bound path to the existing selected relationship
   adapter. Queue reconciliation supplies an isolated snapshot of its original
   audited request, not a reconstructed current task, caller assertion, copied
   qualification receipt or Bridge acquisition. Keep the existing unbound
   callable contract compatible for supported legacy injected writers.
2. The request-aware form requires its independently supplied admission owner.
   Pass that owner the original request, exact workspace and selected executing
   identity before participating relationship effects. An unbound call cannot
   silently drop required request admission. This source boundary supplies
   context; it does not manufacture an independent successful admission or wire
   the ordinary coordinator with a no-op callback.
3. Retain the same request snapshot for reserve/bind and retirement inspect/
   reserve/close. Refuse mismatched workspace, authority, queue/request identity
   or conflicting operation facts before owner mutation. Do not mix the original
   audited event with a later generation's assignment or mutate the caller's
   request through shared callback dictionaries.
4. Preserve existing nonce/writer effect-side checks, exact receipt matching,
   post-I/O queue reconciliation and unknown-outcome custody. Repeated rejection
   and resubmission continue the original relationship; only completed work or
   confirmed unassignment followed by queued/no-bound-agent evidence can settle
   its responsibility. A recovered request must not be recaptured as new work.
5. Keep original-queue/cohort observation and actual installed-writer admission
   separate from this context-carrying seam. The ordinary server still constructs
   no relationship writer. Do not add a queue, registry, service, generic
   handshake, new Worktrees wire field or preflight on every record save.

### Original-request slice validation

- [x] Before an original reserve, the independent callback receives the exact
      retained request and executing writer, with no owner mutation if it refuses.
      #11031 passes bounded actual-queue and actor-free refusal controls.
- [x] Replay and retirement use that same audited request; changed workspace,
      queue/request identity or conflicting operation facts cannot become
      another assignment. Callback mutation cannot rewrite the original request.
      #11031 passes source recovery, token and isolated/interleaved snapshot controls.
- [x] Request-aware admission cannot be bypassed through an unbound call or
      silently degraded on recovery. Legacy explicit writers retain their
      existing callable behavior; ordinary construction remains disabled.
      #11031 passes source compatibility and required-admission refusal controls.
- [x] Real queue reconciliation retains the protected task relationship through
      submission/rejection/resubmission and accountable retirement, including an
      unknown owner response. Keep existing original-event and post-I/O guards.
      #11031 passes finite SQLite/selected-adapter controls with a synthetic owner,
      not installed native-lifecycle acceptance.
- [x] Retain focused actor-free controls in the admitted packet and bounded
      queue/owner controls in the integration lane. Actual original-queue,
      installed-writer and complete-cohort observations remain separate gates.
      #11031 passes 17 new admitted controls, the complete 4,352-unit packet
      and 106 distinct queue/owner controls. No ordinary writer is constructed.
