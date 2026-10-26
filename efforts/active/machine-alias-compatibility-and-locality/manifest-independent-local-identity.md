# Manifest-Independent Local Identity - Phase 3d Amendment

Parent: [Machine Alias Compatibility and Locality](README.md).
Related contract: [physical/environment boundaries](physical-machine-environment-boundary.md).
Tracking: [#5674](https://github.com/ThomasMichon/copilot-extensions/issues/5674).

## Operator requirement

> control-plane repositories should not need to declare *any* machines in their maps in order to be able to register, load, and launch the harness on the current machine. The machine manifest is purely for enabling *additional customizations*, such as friendly-name mapping, per-machine service loadouts, ssh roles, dispatch assignments, etc.

This supersedes the gated producer's registry-required assumption and the
proposed remedy of requiring related projects to bind a control-plane map.
Ordinary local use is foundational; the optional manifest customizes it.
The standing intent is
[`local-first-machine-customization`](../../../visions/agent-fabric/README.md#local-first-machine-customization).

## Boundaries

No manifest, an empty manifest, or one containing only other machines must not
disable current-machine registration, configuration loading, local creation,
lookup, resume or Manager launch. This is not a silent legacy-format fallback:
tuple-first new records remain the selected intent.

Use the existing local execution and configured-identity authorities. An
optional matching map can supply a friendly physical name and execution key;
without such customization, local OS/substrate identity and actual environment
remain usable. Do not require copied maps, a new registry, manual enrollment of
every project, a declared SSH role or a running fleet service for local use.

Local execution authority does not extend to another requested machine, guest,
project, namespace or owner. A suffix, shared hostname, display name, stored
record or incumbent owner cannot make a foreign execution space local.
Keep explicit mapping conflicts and invalid declarations visible rather than
concealing them as absence. Preserve existing identifiers, relationship bytes,
allocation receipts and their receiving-side provenance.

## Implementation sequence

The realization below is **agent-recommended** and requires ordinary plan review
before implementation.

1. Extend the existing shared identity seam with current-machine context
   independent of optional topology. Keep physical identity, observed hostname,
   actual environment and one-term execution key distinct. Preserve literal
   physical names ending in platform-like text; do not trim them heuristically.
2. Adapt registration/configuration, new tuple derivation and receiving-side
   tuple/ownership checks to that same context. An unrelated explicit map must
   not require a declaration of the current machine. Retain genuine foreign,
   ambiguous and conflicting execution-space refusals.
3. Check Manager and actual creation/lookup/resume entry points against the same
   authority. Apply optional names, service loadouts, SSH roles and dispatch
   assignments only where declared; missing customization does not deny the
   underlying local capability. Do not take over those wider consumer campaigns.
4. Keep ordinary producer defaults legacy while this corrective source slice is
   reviewed, qualified and verified through installed opt-in boundaries.
   Review ordinary-default activation separately after those gates pass.

### Shared-context foundation

The additive `machine_transport.resolve_local_execution_context` API separates
current OS/configuration observations from target selection. Its immutable
result preserves observed hostname, physical name, actual environment,
one-term execution identity and optional customization key. Pure source cases
cover Windows, WSL and Linux with empty, unrelated and matching maps, literal
platform-like hostname suffixes, projected legacy names, explicit configured
selection, ambiguity and generated-key collisions. Target selection retains its
authority boundaries; mixed-registry legacy guest lookup and conflicting named
profiles have focused compatibility regressions.

Worktrees configuration now consumes this context through its existing local
detection adapter. Absent, empty and unrelated-only maps allow configuration
loading on Windows, WSL and Linux; matching customization, native legacy binding
strings, literal OS suffixes and explicit conflict refusals have direct loader
regressions. Update and launch consumers share that naming adapter while
retaining the independent runtime manifest policy.

Prospective tuple producers now derive through the same current-context owner,
including actual session/bridge/system creation and new anchor/external records.
Receiving tuple checks compare all three identity dimensions with that validated
context rather than requiring optional map enrollment. Canonical explicit keys,
actual platform, receiving project, snapshot and parent-owner checks remain
independent gates. Completed creation-key replay retains its original result
under unchanged customization and either supported producer mode.
This bounded source slice merged in #8812 after exact-head approval and passing
required CI. It also preserves explicit registration-binding provenance,
record-proven ordinary legacy lifecycle paths, supported legacy platform/path
overrides and actual-platform fallback refusals. An unsupported mixed
legacy-parent spelling cannot admit a tuple child that its lifecycle rejects.

Registration and Manager entry-point source proof and retained-allocation
compatibility after later customization are recorded below. The corrective
source qualified and released in natural run `40185301956`; the complete
installed validation matrix remains open.
Observed naming facts are not receiving-project or
allocation authority; existing admission receipts and identifiers must not be
regenerated from a later optional customization.

The completed creation-key sub-slice now distinguishes read-only journal replay
from ordinary mutation. It retains the original OS/substrate observation inside
new completed receipts, checks the actual receiving namespace and unchanged
record/nonce/relationships, and returns the original allocation after optional
customization. Older receipts remain unchanged; raw OS-derived owners can be
reconstructed, while an unproven former custom owner still refuses rather than
allocating a replacement. This sub-slice merged in #8852 after current-head
approval and required CI, including unchanged legacy platform-override replay.
Completed launch-admission replay merged in #8857 with the same receipt-bound
compatibility, original allocation/nonce/relationship and receiving-namespace
proof. Fresh/unstarted creation, first completion, Resume and actual seed
writes retain ordinary mutation authority.

The interrupted-tail source implementation now proves the original actual
record, receiving project, nonce, Git checkout/branch, stopped state and retained
New before bounded OS-derived naming compatibility. It independently checks
the companion's receiving project and repeats original allocation/seed checks
inside companion allocation. Existing parent/companion references remain
unchanged; foreign owners, namespaces and changed intent refuse.
This slice merged in #8865 after current-head approval and required CI,
including equivalent resolved paths and independently checked historical
parents without nonce backfill. Manager entry-point proof and the installed
validation gates below remain open.

Actual configuration-loader JSON New, short-ID lookup and Resume proof merged
in #8886. Observed hostname and derived local owner selection now work across
absent, empty, unrelated, matching, unscoped and sibling-only maps without
borrowing registered foreign-target authority. Managed tuple records retain
their independent receiving namespace check; real adopted external Git
worktrees remain usable outside the managed directory. Ordinary defaults stay
legacy; Manager and installed proof remain open.

The project registration/installation source slice now uses the same local
context before state preparation, separating absent customization from an
invalid declaration. Actual command tests retain configuration detection,
scaffolding and subsequent loading, while containing runtime and terminal
effects. The three-platform matrix covers all six map shapes, explicit binding
provenance, literal observed suffixes and byte-preserved user configuration;
invalid and foreign-platform controls refuse before writes. This source slice
merged in #8898 after current-head approval and required CI. Manager entry-point
and installed proof are open.
Admission checks additionally validate the effective retained global,
project and drop-in binding, including force precedence. New global scaffolds
do not turn a detected physical projection into an explicit binding. Scoped
adoption checks prospective retained state through the existing repository
identity and receipt before publishing registration or creating state.

Manager source/entry proof merged in #8929 after current-head approval and
required CI. Both real engine bootstrap producers preserve configured-binding
provenance; actual Manager readers, local-only runner entry, source/row/Profiles
identity and synchronous action routing share the same local context.
Worker setup publishes the selected tuple; expired or invalidated configuration
never re-enters the UI event loop. Full OS hostnames, supported legacy keys and
normalized guest targets retain their separate meanings. The affected Manager
packet passes 1,031 cases, with an additional 91-case task/launch/refusal packet.
These are contained source proofs, not installed native/guest acceptance.

Installed entry observation subsequently passed every registered native/guest
project through the real Manager preparation/bootstrap and optional-map
readers. Local sources remain ready without SSH, Profiles and row identity
agree, and current publication IDs are preserved. The
[installed-acceptance journal](journal/2026/10.25%20progress.md) separately records
these results, existing-record lookup and the later actual allocation/Resume
packet, without inferring ordinary activation.

The remaining read-only guidance consumers merged in #9110. Both structured
session guidance and the legacy command now use the shared local context,
including actual explicit-binding provenance and visible invalid-map refusals.
Natural qualification `40378983487` and Main #9116 deliver the exact source.
Both supported updates complete successfully, and actual selected Worktrees
`1.24.297-dev1` guidance agrees across all eight registered native/guest
project contexts without changing their tracking bytes. All four low-level
omitted-mode APIs remain legacy. The separately reviewed ordinary-selection
proposal #9114 does not activate those call sites or authorize live rollout.

## Validation Plan

- [x] Cross Windows, WSL and Linux registration/load source cases with absent,
  empty, unrelated-only and matching customized manifests; ordinary local use
  works without adding entries. Actual command/configuration proof merged
  in #8898; no installed or Manager acceptance is inferred.
- [x] Cross Manager local-source selection and launch-entry source behavior with
  the same optional-map shapes, preserving configured names and actual execution
  authority. Actual bootstrap/readers/runner and UI-routing proof merged in
  #8929; installed process and native/guest acceptance remain separate below.
- [x] Drive actual new tuple creation, tracking, JSON, lookup and resume through
  the corrected boundary, retaining physical/environment/one-term agreement.
  Source matrix merged in #8886; installed native/guest acceptance remains a
  separate gate below, not inferred from contained metadata cases.
- [x] Preserve custom names and execution keys without losing uncustomized local
  operation; retain explicit ambiguity, malformed declaration, platform,
  foreign-guest, receiving-project and namespace refusals.
  The reviewed local-context/configuration/creation/Manager source matrices and
  #9110's actual guidance readers retain these controls. Installed guidance
  covers matching and map-free registered contexts; no live invalid map is
  introduced merely to exercise a source refusal.
- [x] Replay admitted legacy and tuple allocations unchanged, including when
  optional customization is added later; no implicit relabel or owner transfer.
  Bounded source replay/repair merged in #8852, #8857 and #8865; installed
  opt-in acceptance remains independently required below.
- [x] Pass bounded decision/ownership regressions and existing guarded Source CI.
  Keep actual native, installer, Git and process evidence outside promotion.
- [x] Qualify and release the corrective source. Natural run `40185301956`
  binds Dev `4eb0f1909d3dc6b99336ba9612219a5aebac035d` to the exact candidate
  and unit packet; #8956 lands that qualified candidate on Main.
- [x] Verify installed opt-in behavior on all registered native/guest projects.
  Eight actual Git allocations and sixteen keyed replays preserve the original
  tuples and bytes. Six allowed non-preview Resume plans preserve ID, path and
  nonce; the two source-only contexts retain explicit hosting refusals.
  Real Manager entry passes all eight contexts. All disposable checkouts and
  branches are normally removed, with finalized records and receipts retained.
- [ ] Review ordinary activation separately. Installed opt-in acceptance is not
  ordinary-default or rollout authorization. Preserve the low-level omitted-mode
  legacy contract; activate ordinary call sites only through separately reviewed
  [selection](ordinary-callsite-activation.md).
