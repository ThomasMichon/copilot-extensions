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
- [ ] Implement fresh checkpoint identity acquisition inside the existing
  repair owner. Without the opt-in producer, preserve the current whole-operation
  behavior and its expiry refusals.
- [ ] Match each fresh verified caller and current enrollment to the original
  identity profile, issuer, subject, audience, proof key/current key binding,
  actor/account, role, installation, fleet, target and generation. Require the
  same exact original request grant and candidate/executor/resource transition;
  a fresh signature cannot substitute a caller, key, scope or terminal choice.
- [ ] Retire only the preceding exact local permit handle, without refunding
  consumed proof evidence. Obtain a distinct current permit before the next
  checkpoint's admitted work, even when the preceding handle has expired
  between checkpoints. No effect is authorized in that gap.
- [ ] Preserve each new permit's 30-second ceiling and final revalidation after
  all original custody, immutable evidence, native process/network and material
  reads. Expiry or withdrawal during that checkpoint refuses and aborts the
  invocation; do not reacquire midway to excuse its failed final boundary.
- [ ] Keep current finite transition/candidate/executor validity, monotonic
  accepted revisions and the original selected outfitting deadline. Fresh
  proofs cannot extend policy, repair authority, native work or resource custody.
- [ ] Retain all original UNKNOWN and pending/terminal publication semantics.
  Before append/commit and after cleanup, repeat fresh authorization and exact
  evidence checks under the same physical lock and journal transaction.
  Pre-commit refusal preserves UNKNOWN; post-commit refusal preserves the one
  immutable terminal decision and requires its supported cleanup recovery.
- [ ] Keep proofs, private keys and credentials out of outcomes and diagnostics.
  Preserve original candidate versus executor provenance, prior history and
  data compatibility without a new checkpoint persistence ledger.
- [ ] Integrate the existing embedding's signer/verifier and current selection
  owners. Remove redundant same-checkpoint acquisition only through those owners,
  retaining before/after source, runtime, key and credential/material checks.
  Neither a self-supplied snapshot nor a cross-checkpoint cache grants authority.
- [ ] Publish accurate installed support/operating docs, then obtain normal
  release/acquisition and separately authorized actual adopter qualification.
  Keep #9735 and every broader parent delivery gate open until actually resolved.

## Validation Plan

- [ ] With an advancing clock, complete status and terminal publication whose
  total duration exceeds 30 seconds but remains inside the unchanged selected
  deadline. Each individual admitted checkpoint must complete within its own
  unextended permit. Verify actual fresh signatures and common admission,
  using one consistently advancing clock rather than mocked permits or a
  frozen authorization clock. Actual wall-time qualification is separate.
- [ ] Refuse reused/wrong-key/wrong-nonce/wrong-action/body proofs, changed
  caller/account/profile/enrollment/resource/generation, withdrawn grants,
  source/runtime/material drift and expired transition or signer authority.
  Include expiry during acquisition and immediately before append/commit.
- [ ] Prove no mutation or publication in permit gaps, no extension of an old
  permit, no refunded proof evidence and no implicit retry after a checkpoint
  refusal. Keep the common admission expiry/replay tests unchanged.
- [ ] Interrupt at the existing immutable/state/journal/cleanup boundaries,
  then restart from the same original request and resources. Assert exactly one
  terminal row, retained original history, current native/key/network custody,
  no apply replay and truthful UNKNOWN or already-terminal outcomes.
- [ ] Exercise old caller/new SDK, new caller/unsupported SDK, supported new/new
  operation and provider rollback through real noneditable installed seams.
  Discover opt-in support from the executing immutable SDK before constructing
  the new embedding; unknown support refuses the new requirement before effects.
- [ ] Count repeated acquisition/callback work deterministically in the bounded
  unit packet. In a separate selected integration lane, measure full cold/warm
  status and terminal publication with actual credential/source subprocess or
  transport boundaries, monotonic wall time and owned cleanup. A lower process
  count, a faster status or an enlarged supervisor timeout is not terminal-path
  acceptance. Unavailable native measurements remain explicitly unqualified.
- [ ] Re-run ordinary owner/admission behavior and secret-safe outputs. Keep
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
checks require exactly four initial resolver calls and six per fresh phase.
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
