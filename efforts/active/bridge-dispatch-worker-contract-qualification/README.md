# Bridge and Dispatch Worker Contract Qualification

- **Slug:** `bridge-dispatch-worker-contract-qualification`
- **Repo:** copilot-extensions
- **Branch(es):** planning PR, then independently owned per-slice PRs
- **Created:** 2026-11-06
- **Status:** Draft
- **Vision:** [Bridge ownership and observation](../../../visions/plugins/agent-bridge/README.md); [Dispatch task lifecycle and outcome](../../../visions/plugins/agent-dispatch/README.md)
- **Sub-issues:** #6467 · #3556 · #7268 · #11106 · #11108

## Guiding Intent

Make the existing Bridge/Dispatch execution contracts easy to rely on and
qualify, without consumers building another worker driver to infer progress,
repair ambiguous results or guess effective execution policy.

This is a handoff-ready contract audit and targeted qualification plan.
The motivating failure modes are not evidence that every current native
interface has the same defect. Credit existing mechanisms and tests first;
change only a demonstrated gap under its current owner.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| plan-preparer | File/deduplicate recommendations and prepare this proposal only | Planning PR from managed source worktree `faded-widget` |
| implementation-driver | Unassigned; an operator-selected later agent coordinates the audit | This README and the linked public issues |
| existing-scope-owners | Retain their current implementation, review and resource duties | The existing issue/effort/claim records |

## Coordination

- **Topology:** independent per-slice PRs under existing component owners;
  no shared implementation branch or new orchestration authority.
- **Host (owns PRs):** the preparer owns only the planning PR. A later
  implementation driver must establish its own scope and ownership before work.
- **Delegates:** none started for this effort. Filing or linking an issue is
  not an implementation reservation or transfer.
- **Handoff:** the next driver reads this plan, current issue discussions and
  existing claims, then records accepted responsibility. No acceptance or
  worker availability is inferred here.

### Proposed finite #6467 receiving scope

The original recommendation capture and its Request remain unchanged.
This amendment proposes retaining the complete finite #6467 progress/watchdog
and deadline/recovery contract here, rather than only its two supplemental
vectors. It does not appoint the documentary coordinator or plan-preparer as
the implementation driver. This effort remains Draft, and the next driver's
actual acceptance requirement above still applies.

The existing independent lifecycle owner remains the watchdog consumer;
Bridge/Dispatch, process/pipe cleanup, selected-state and worker-custody owners
retain their respective boundaries. No new daemon, whole-#5301/#5655 completion
prerequisite, generic owner ACK or resource release follows. Existing action,
native and installed qualification need their applicable authority.

#6467 remains OPEN during this proposal. Normal reviewed acceptance of the
complete planned receiving, fresh complete permitted original/ALL-discussion
and current receiving-section/eligibility readback, known inherited retirement
receipts and actual source-specific owner/claim/resource conditions precede
any separate tracker judgment. Any independent physical-OPEN, accepted-handoff
or unresolved applicability condition retains the source OPEN. A draft,
two new rows, a link or unassigned metadata alone is not that acceptance.

## Context

Five portable recommendations map to the following existing and new work:

| Recommendation | Canonical work item | Treatment |
|----------------|---------------------|-----------|
| Distinguish work progress from process/transport liveness | #6467 | Reuse its phase/watchdog owner; add a healthy-heartbeat/no-worker-progress case, not another supervisor |
| Bound deadlines through inherited pipes and owned descendant cleanup | #6467 | Reuse its pipe-drain/cleanup scope and current process primitives |
| Bind results to the exact attempt/input and distinguish submission from completion | #11106 | Audit existing result/evaluator interfaces before claiming a missing guarantee |
| Reconcile late results and uncertain effects before retry/replacement | #3556 and #7268 | Reuse uncertain-consume and acknowledged-handoff owners; adopter effect authority stays outside Dispatch |
| Verify effective worker model and tool profiles | #11108 | Audit Bridge/driver/runtime support and actual enforcement, not requested configuration alone |

The existing issues received supplemental qualification vectors without
replacing their bodies, current scopes or owners:

- [#6467 supplemental progress/pipe cases](https://github.com/ThomasMichon/copilot-extensions/issues/6467#issuecomment-6695070459).
- [#3556 supplemental uncertain-outcome cases](https://github.com/ThomasMichon/copilot-extensions/issues/3556#issuecomment-6695076554).
- [#7268 supplemental late-effect handoff cases](https://github.com/ThomasMichon/copilot-extensions/issues/7268#issuecomment-6695080965).

Other related owners remain separate: #4053 owns its complete remote-command
lifetime/cleanup acceptance; #9709 owns pool/startup retry eligibility; #3681
owns broader task lifecycle/conversation work. This effort does not consolidate,
close, supersede or take over those campaigns.

Use the existing
[service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
for any changed independently deployed boundary. The Bridge vision leaves
session/process lifetime with the selected driver; the Dispatch vision leaves
workflow sub-stages with the selected task/worker charter. Preserve both
boundaries rather than teaching the coordinator one adopter's workflow.

## Request

> File those recommendations you found for agent-bridge and agent-dispatch as issues, and roll then into an effort. I can have another agent tackle them.

The operator confirmed this public repository as the issue destination and
selected reuse of existing issues with new filings only for uncovered topics.
The five recommendations are captured in the table above. Implementation is
for another operator-selected agent, not started by the filing session.

## Scope Approvals

- **Approved now:** public-safe issue/qualification capture, duplicate checks,
  additive context on the existing owners' issues and this handoff proposal.
- **Not granted by this filing:** implementation reservations, live worker or
  process control, claim release, external writes/retries, model/session
  activation, runtime installation or rollout.
- Existing source, claim, review, compatibility, security and resource
  authorities remain effective. A plan or submission receipt is not a grant.

## Plan

### Phase 1 - Capture and ownership map

- [x] Check related open work and reuse #6467, #3556 and #7268 rather than
  duplicating their scopes.
- [x] File #11106 and #11108 as audit/enhancement items, explicitly separating
  proposed qualification from a confirmed native defect.
- [x] Read back both new issues and all three supplemental comments.
- [x] Prepare this public, owner-preserving proposal for a later driver.

### Phase 2 - Establish the current contract and coverage

_Phases 2-4 and the uncompleted validation items are agent-recommended plans
for the next driver, not assertions that these gaps exist or new execution
authority._

- [ ] Reconcile the five recommendation rows with current supported
  interfaces, tests, visions and existing owners' work.
- [ ] For each row, distinguish already covered behavior, a documentation/
  discoverability gap, missing consumer qualification and a reproduced defect.
  Record the actual evidence and the smallest remaining work.
- [ ] Keep any needed schema/capability change with the operation that already
  owns it, including supported legacy/rollback semantics. Do not add a new
  prerequisite RPC or globally require optional inspection support.

### Phase 3 - Deliver only bounded existing-owner follow-ups

- [ ] Coordinate any demonstrated gap with its current owner; do not pre-patch
  or supersede another contributor's active implementation.
- [ ] Add only the missing contract documentation, focused regression coverage
  or surgical owner-side correction. Keep independent slices independently
  reviewable through the normal repository flow.
- [ ] Preserve the current scheduler, task store, driver/process authority,
  evaluator and permission owners. No new SDK worker driver, global profile/
  result registry, watchdog daemon or domain-specific Dispatch state machine.
- [ ] Retain #6467's finite remaining integration with the existing independent
  lifecycle-owned watchdog as the consumer of phase/cycle/receipt progress,
  without another daemon. Credit #6487's bounded whole-cycle, tracked-pool,
  durable uncertain-operation and bounded-drainage repair; qualify only remaining
  consumer/recovery gaps. Preserve selected-state and worker custody, one durable
  in-flight receipt operation, generation revalidation and no duplicate
  embodiment. Original stack attribution and digest-change cause remain unproven.

### Phase 4 - Qualify and return evidence

- [ ] Exercise the relevant supported consumer boundary, not just a helper
  mock or a successful transport request. Select a bounded real-parser,
  process or execution-profile lane where it is applicable and authorized.
- [ ] Record covered cases, actual remaining failures and unqualified lanes
  on the canonical issues. Separate source, synthetic/native, release and
  installed-runtime evidence.
- [ ] Resolve each recommendation with evidence or transfer a genuinely
  remaining item to a named tracked objective accepted by its owner. Do not
  equate a posted result, merged plan or healthy process with completion.

## Validation Plan

- [x] Capture the literal request and the duplicate-reuse decision without
  turning proposed improvements into claims of existing bugs.
- [x] Verify public issue/comment bodies and requested issue labels by
  independent readback; retain original publication identities.
- [ ] Progress case: an alive connection/process and advancing heartbeat do
  not imply eligible work or accepted outcomes are advancing. Preserve a
  concrete idle/gate/unknown explanation and adopter-owned stage meanings.
- [ ] Deadline case: a descendant retaining pipes cannot make post-timeout
  drainage/cleanup unbounded. Unconfirmed cleanup keeps its exact custody;
  timeout or stop acknowledgement alone cannot authorize replacement.
- [ ] Result case: cover identical/conflicting delivery, changed input,
  replaced attempt/owner and late callbacks. A submission cannot silently
  replace another artifact or certify evaluator/adopter completion.
- [ ] Recovery case: cover committed/lost-response mutations at the same
  authority, stale responses, failed reconciliation and accepted-but-unresolved
  effects. No fresh send, forced release or expiry shortcut repairs uncertainty.
- [ ] Profile case: qualify effective allowed/denied tools and correlated
  model evidence at the selected execution seam; distinguish requested,
  confirmed, mismatched and unavailable outcomes.
- [ ] Preserve supported peer/version-skew behavior and existing general-purpose
  profiles. Native/installed checks require an applicable authorized lane;
  this plan adds no all-host deployment or unconditional external-effect test.
- [ ] For #6467's independent lifecycle consumer, fault-inject slow receipt
  integrity checks, root-lock contention, retained pipe handles, interrupted
  publication and restart; prove unrelated lane progress, no duplicate
  operations or embodiment, and generation-fenced recovery retaining selection
  and worker ownership when outcomes remain uncertain. Reuse the direct progress,
  deadline, recovery and authorized-consumer cases above; a healthy heartbeat or
  acknowledged stop is not proof of advancement or settled cleanup.

## Proposal

Review current contracts before implementing. Prefer documentation and existing
test/consumer coverage when the guarantee already exists. Any actual behavior
change must reconcile to its owning vision/pattern and use the repository's
normal worktree, changefile where applicable, validation and PR flow.

The completion gate is coverage of all five recommendation rows, with
evidence-backed closure or an explicit accepted tracked transfer for remaining
work. This proposal is prepared for handoff; implementation ownership is
unassigned and the effort remains Draft.

## Journal

### 2026-11-18 - Complete finite lifecycle receiving proposed

The complete current #6467 original and both discussions were compared with
this entire current ROOT. Existing direct Validation already retains heartbeat
versus progress, descendant-held pipes/native cleanup custody, uncertain effects
and supported consumer/authority boundaries. No duplicate checklist is added
for those cases.

One unchecked Phase 3 Plan and one actual global Validation row directly
retain the remaining existing lifecycle-consumer integration and complete
fault/recovery matrix, with #6487 credit and unproven historical attribution
preserved. The actual recommendation-only Request, Draft status, unassigned
implementation driver and explicit later-driver acceptance remain unchanged.
The scope proposal is documentary receiving, not driver acceptance, execution
authority, whole-provider completion or a source/resource transfer.

#6467 remains OPEN until reviewed complete receiving, current source/inherited/
contract/eligibility and actual condition readback and a separate judgment;
any genuine independent condition remains binding. No implementation, test,
native operation, live effect or tracker disposition occurred.

### 2026-11-06 - Recommendation capture

- The operator requested issue capture and an effort for a different agent.
  Duplicate review reused the existing progress/cleanup and lifecycle/handoff
  owners, adding supplemental regression vectors rather than duplicate issues.
- New audit items #11106 and #11108, plus comments on #6467, #3556 and #7268,
  were independently read back. No implementation was claimed or executed,
  no issue was closed and no runtime was installed, activated or updated.
- The public proposal preserves current owners and the distinction between
  candidate failure modes, current contracts and actual qualification evidence.
