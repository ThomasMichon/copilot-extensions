# Fresh authorization for original-attempt repair checkpoints

Back to [Linux role fleet](README.md) and its
[original-attempt repair amendment](komodo-substrate-lifecycle.md#original-attempt-sdk-repair-amendment).
Tracked by #9735 under the existing #8860/#6030 appointment.

## Request and boundary

The operator selected:

> Design and review fresh checkpoint authorization through existing SDK/signing owners, preserving each 30-second permit and the exact original

This selects source design and review, not live activation. The mechanisms and
validation below are **agent-recommended**. Existing source implementation and
adopter deployment authorizations remain separate from this plan's approval.

The existing repair owner retains one nonrenewable permit across complete
inspection and terminal publication. Legitimate current-authority acquisition
can make those cumulative checkpoints exceed that permit's lifetime. A
fixed-clock fixture does not prove a composed operation fits. Increasing the
permit lifetime, caching an allow or omitting a final check is not the remedy.

The common admission owner already supports distinct fresh proofs and permits
for the same canonical request. Revalidation never extends a permit. This
slice composes that existing behavior into the appointed repair owner; it does
not change common admission, ordinary convergence or Gateway routes.

## Existing owners and proposed contract

The existing `KomodoRepairOwner` keeps the physical lock, original attempt,
native observations, immutable outcomes and sole `NodeJournal`.
The embedding's existing verifier/signer and selected configuration, runtime,
credential and material owners keep authentication and currentness.
No new issuer, grant store, scheduler, installer or native writer is appointed.

Propose an explicit opt-in installed support identity,
`fleet-manager.komodo-original-repair-checkpoint-auth.v1`, alongside the existing
original-repair contract. An optional code-registered identity reacquisition
producer supplies a freshly authenticated caller for the exact original body
and action at each SDK repair checkpoint. It returns neither a permit nor
serialized authority. A request/configuration cannot choose the producer,
verifier, signer, module or callback path.

The SDK still issues and owns permits through the existing `authorize`,
`revalidate`, `complete` and exact-handle `discard` operations. A fresh
authorization is a new decision, not an extension of the preceding capability.
An unavailable producer or failed current verification refuses with no cached
identity, old-proof or baseline-operation fallback.

## Plan

- [x] Review this opt-in amendment before changing repair behavior (#9778).
- [x] Implement fresh checkpoint identity acquisition inside the existing
  repair owner. Without the opt-in producer, preserve the current whole-operation
  behavior and its expiry refusals.
- [x] Match each fresh verified caller and current enrollment to the original
  identity profile, issuer, subject, audience, proof key/current key binding,
  actor/account, role, installation, fleet, target and generation. Require the
  same exact original request grant and candidate/executor/resource transition;
  a fresh signature cannot substitute a caller, key, scope or terminal choice.
- [x] Retire only the preceding exact local permit handle, without refunding
  consumed proof evidence. Obtain a distinct current permit before the next
  checkpoint's admitted work, even when the preceding handle has expired
  between checkpoints. No effect is authorized in that gap.
- [x] Preserve each new permit's 30-second ceiling and final revalidation after
  all original custody, immutable evidence, native process/network and material
  reads. Expiry or withdrawal during that checkpoint refuses and aborts the
  invocation; do not reacquire midway to excuse its failed final boundary.
- [x] Keep current finite transition/candidate/executor validity, monotonic
  accepted revisions and the original selected outfitting deadline. Fresh
  proofs cannot extend policy, repair authority, native work or resource custody.
- [x] Retain all original UNKNOWN and pending/terminal publication semantics.
  Before append/commit and after cleanup, repeat fresh authorization and exact
  evidence checks under the same physical lock and journal transaction.
  Pre-commit refusal preserves UNKNOWN; post-commit refusal preserves the one
  immutable terminal decision and requires its supported cleanup recovery.
- [x] Keep proofs, private keys and credentials out of outcomes and diagnostics.
  Preserve original candidate versus executor provenance, prior history and
  data compatibility without a new checkpoint persistence ledger.
- [x] Integrate the existing embedding's signer/verifier and current selection
  owners. Remove redundant same-checkpoint acquisition only through those owners,
  retaining before/after source, runtime, key and credential/material checks.
  Neither a self-supplied snapshot nor a cross-checkpoint cache grants authority.
  The embedding source and local noneditable checkpoint composition are qualified;
  actual adopter wall-time and original-attempt acceptance remain below.
- [ ] Publish accurate installed support/operating docs, then obtain normal
  release/acquisition and separately authorized actual adopter qualification.
  Keep #9735 and every broader parent delivery gate open until actually resolved.

## Validation Plan

- [x] With an advancing clock, complete status and terminal publication whose
  total duration exceeds 30 seconds but remains inside the unchanged selected
  deadline. Each individual admitted checkpoint must complete within its own
  unextended permit. Verify actual fresh signatures and common admission,
  using one consistently advancing clock rather than mocked permits or a
  frozen authorization clock. Actual wall-time qualification is separate.
- [x] Refuse reused/wrong-key/wrong-nonce/wrong-action/body proofs, changed
  caller/account/profile/enrollment/resource/generation, withdrawn grants,
  source/runtime/material drift and expired transition or signer authority.
  Include expiry during acquisition and immediately before append/commit.
- [x] Prove no mutation or publication in permit gaps, no extension of an old
  permit, no refunded proof evidence and no implicit retry after a checkpoint
  refusal. Keep the common admission expiry/replay tests unchanged.
- [x] Interrupt at the existing immutable/state/journal/cleanup boundaries,
  then restart from the same original request and resources. Assert exactly one
  terminal row, retained original history, current native/key/network custody,
  no apply replay and truthful UNKNOWN or already-terminal outcomes.
- [x] Exercise old caller/new SDK, new caller/unsupported SDK, supported new/new
  operation and provider rollback through real noneditable installed seams.
  Discover opt-in support from the executing immutable SDK before constructing
  the new embedding; unknown support refuses the new requirement before effects.
- [ ] Count repeated acquisition/callback work deterministically in the bounded
  unit packet. In a separate selected integration lane, measure full cold/warm
  status and terminal publication with actual credential/source subprocess or
  transport boundaries, monotonic wall time and owned cleanup. A lower process
  count, a faster status or an enlarged supervisor timeout is not terminal-path
  acceptance. Unavailable native measurements remain explicitly unqualified.
- [x] Re-run ordinary owner/admission behavior and secret-safe outputs. Keep
  exhaustive process/native matrices out of required unit-only PR CI.
- [ ] After authenticated release and executor selection, qualify one authorized
  actual original attempt without changing its candidate, generation, keys,
  data, native resources, journal owner/location or deadline. Only the supported
  original terminal publication changes its receipt. This is not ordinary
  deployment convergence or completion of Linux Role Fleet.

## Journal

### 2026-10-31 - Checkpoint authorization proposal reviewed

#9778 merged as `dba8729ff61c83f639b2f73022eead714ce40989` after the actual
current-head Copilot approval, no inline or blocking findings and all required
checks. Managed completion verified the merge and reconciled the same source
worktree while retaining its pre-complete backup. The complete parent Plan and
Validation Plan were re-read; only this proposal's review gate closes.

Implement the opt-in fresh-identity composition through the existing repair,
signing and common-admission owners next. Keep the baseline expiry behavior,
exact original custody, each permit's unextended lifetime and the complete
advancing-clock/installed/native validation obligations. No executing support,
runtime change, actual original repair or broader delivery is claimed.
The #9735 and #6030 trackers remain open. #8860 was previously consolidated
through #9475 into this effort's complete substrate Plan and Validation Plan;
its administrative closure is not technical completion or an ownership change.

### 2026-10-31 - Opt-in source composition awaiting review

The local SDK source composes fresh identities into explicit admitted phases
through the existing repair and common-admission owners. The baseline
whole-operation behavior remains unchanged. Every fresh phase retains exact
original caller/enrollment/transition bindings, full paired custody checks and
its unextended permit; journal transitions validate the preceding phase before
admitting append or commit. An aborted opt-in status phase preserves its
original refusal rather than producing a report under a replacement permit.

All 131 opt-in synthetic native/HTTP/journal controls pass, including advancing
full status and terminal publication, expiry between completed phases,
late pre-commit withdrawal and interrupted terminal cleanup. The 28 separate
real-machine-proof/common-admission controls pass. Deterministic composition
checks require exactly four initial resolver calls and six per fresh phase,
followed by two calls for common completion after the final full custody boundary.
The positively admitted unit-only packet adds one dependency-light work-count
case. Its complete canonical packet passes all 3,175 admitted units with
9,525 successful phase reports, no actor effects and one coverage capture,
within the unchanged five-minute packet budget. All 129 baseline repair
controls also pass without the producer.
The native fixture uses a short, private, context-managed socket path so an
owner-stamped temporary root does not exceed the Unix socket address bound.

Source review, normal release/acquisition, installed mixed-version witnesses,
the existing embedding's producer integration and actual cold/warm adopter
qualification remain open. No actual original terminal repair or broader
delivery is credited. Keep the complete parent Plan and Validation Plan active.

### 2026-10-31 - Final completion custody correction

#9791's review identified that completing authority inside the final phase
suppressed its closing full custody check. Common completion validates
authority, not the original journal, immutable evidence, native resources or
material. Finish and status now close their full custody phase before
consuming its retained permit; completion cannot suppress that final check.

Eight late final-phase key, native-process, credential and profile drift
controls cover status and terminal publication. Refusal retains UNKNOWN or
the single committed terminal receipt; exact restoration permits supported
verification without another apply or terminal row. The focused 40-case
packet passes, including those controls, advancing composed paths and real
machine proofs. Final common completion adds two current resolver calls;
the original permit ceiling and selected native deadline stay unchanged.

Normal current-head review, required CI, release, installed compatibility and
actual embedding/adopter qualification remain open. This correction is source
work, not an actual original repair or broader delivery credit.

### 2026-10-31 - Checkpoint SDK implementation merged

#9791 merged as `7a4e378ba114a4a3b22d8c87dad427c1cd36e3e7` after the
current-head Copilot approval, actual resolution of the final-custody finding
and all required checks. Managed completion verified the merge and reconciled
the same source worktree while retaining its pre-complete backup. The complete
parent Plan and Validation Plan were reconciled; only the source and contained
validation items actually delivered by this slice close.

The corrected canonical packet passes 3,175 units and all 9,525 phases without
actor effects. The focused final-completion and interrupted-cleanup packets
pass 40 and nine controls. Earlier whole baseline and opt-in repair packets
retain their 129 and 131 controls; eight final-phase drift cases add status and
terminal protection. Every permit and selected native deadline remains bounded.

Continue the existing embedding's producer integration and selected installed
compatibility qualification, then normal release/acquisition and actual
cold/warm adopter proof. #9735 and #6030 remain open. Source merge is not
released support, live activation, original terminal repair or fleet delivery.

### 2026-10-31 - Installed checkpoint compatibility qualified locally

The selected installed packet passes all 16 cases: two ordinary bootstrap
controls, seven original-repair controls retaining whole-operation admission
and seven using the checkpoint producer. Its existing build owner constructs
three real noneditable SDK cells from the current source and pinned committed
pre-repair and pre-checkpoint repair sources. Runtime guards deny checkout
reads, verify complete installed module bytes and require every distribution
file to remain inside its cell.

An older caller continues through the new SDK's baseline repair contract.
A caller requiring checkpoint support refuses the actual older repair SDK
before owner construction or native work, preserving original state and
observations. Supported status and terminal publication use distinct real
machine proofs; one advancing resolver/native/health clock charges complete
work beyond 30 seconds within the original synthetic 180-second deadline.
Whole-operation fixture bounds remain unchanged. Interrupted state/journal/
partial publication resumes in a fresh process, retaining one native apply,
one terminal row, original profile, journal inode, immutable evidence and data.

These are actual noneditable SDK and contained synthetic protocol/process
witnesses, not authenticated release artifacts, official images, actual
adopter credentials/source acquisition or native cold/warm wall-time proof.
The selected packet's whole supervisor bound remains separate from each
permit and the original native deadline. Current source review, existing
embedding integration, normal release/acquisition and actual adopter
qualification remain open; #9735 and #6030 stay open.

### 2026-10-31 - Installed checkpoint compatibility merged

#9802 merged as `304cb373a05959dae37a8df87c38ec63d79ff022` after the actual
current-head Copilot approval, no inline or active findings and required green
CI. Managed completion verified the merge and reconciled the same source
worktree with its pre-complete backup retained. Both complete parent Plan and
Validation Plan were re-read; only the installed compatibility item closes.

All 16 selected installed controls retain their real noneditable SDK cells,
checkout denial, machine signatures, common admission and contained native/
HTTP/journal boundaries. They are not actual adopter source/credential latency,
official-image readiness, authenticated release admission or production repair.
Continue the existing embedding and ordinary release/acquisition owners, then
qualify the actual selected executor and full cold/warm original status and
terminal publication. #9735 and #6030 remain open; no wider fleet gate closes.

### 2026-10-31 - Released SDK and embedding composition qualified locally

The normal Main promotion selects checkpoint source
`7a4e378ba114a4a3b22d8c87dad427c1cd36e3e7`. The signed index and complete
schema-4 standalone bundle passed hosted-workflow attestation and the existing
artifact owner's complete-source/build-resource validation. The selected
closure contains Fleet `0.1.72.dev1`, process `0.1.12.dev1` and ZDD
`0.1.1.dev1`; an older identically versioned library artifact is not substituted.

The existing embedding's source integration has passed its normal review and
merge. Its strict opt-in discovers support from the executing SDK before
protected-source acquisition or signing. Its original identity anchor, fresh
verified caller, registered signer/verifier and current-selection callbacks
retain full before/after custody and common admission without a new issuer,
permit cache or native writer.

A local Linux x86-64 source-independent environment qualifies that noneditable
embedding against the authenticated three-wheel SDK closure and retained
runtime owner. Complete external installed files match their five exact wheels;
cold publication and warm reuse pass. The complete packet passed 389 controls,
then its three unresolved runtime-reader controls passed after correcting only
caller-owned local input-file modes. This is aggregate 392-case qualification,
not a second complete packet or actual adopter credentials/native wall-time.

Both complete parent Plan and Validation Plan remain active. Only the
embedding-source integration and local installed composition close here.
Actual selected ARM64 publication, finite current transition, complete cold/warm
status and terminal publication, and one authorized original-attempt
qualification remain unresolved. No original receipt, resource, key, credential,
data or selected native deadline changed; #9735 and #6030 remain open.
