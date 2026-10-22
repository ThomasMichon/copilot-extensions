# Acknowledged Execution Handoff

- **Slug:** `acknowledged-execution-handoff`
- **Repo:** copilot-extensions
- **Branch(es):** independent, sequential PRs against `dev`
- **Created:** 2026-10-19
- **Status:** Active
- **Vision:** [Dispatch execution handoff](../../../visions/plugins/agent-dispatch/README.md#acknowledged-execution-handoff), preserving [reviewer lineage](../../../visions/plugins/agent-dispatch/reviewer/README.md#one-review-one-lineage)
- **Umbrella issue:** [#7268](https://github.com/ThomasMichon/copilot-extensions/issues/7268)

## Guiding Intent

Let an authorized adopter transfer execution of a bounded Dispatch scope
without overlapping writers, losing existing tasks or interrupting unrelated
work. An accepted request is not an acknowledged handoff. Dispatch owns task
admission and execution accounting; the adopter owns domain effects and policy.

**Source-and-test implementation is authorized.** Installation, live transitions,
process termination and deployment remain separately gated. A reviewed source
change is not an acknowledged or adopted handoff.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Proposal owner | Generic contract, proposal PR and issue coordination | Assigned owner of #7268 |
| Independent reviewer | Review the proposal through the normal repository gate | Proposal PR review |
| Implementation owner | Generic runtime and validation slices | Assigned owner of #7268 |

## Coordination

- **Topology:** reviewed proposal, followed by independent sequential source PRs.
- **Host (owns PRs):** Proposal owner, continuing as Implementation owner.
- **Delegates:** Independent reviewer owns review only.
- **Handoff:** Record decisions and blockers here and in #7268. Preserve existing
  lifecycle, remote-driver and task-session-resume owners; do not take their work.
- This public effort is canonical for the generic contract. Adopter-specific
  policy, operational permission and effect integration remain adopter-owned.

## Context

Existing task holds and producer-generation fences are useful primitives, but
production fencing is not proof that existing executions and their external
effects are quiescent. Reuse those authorities rather than adding another task
queue, scheduler or process registry.

This is a **vision extension**: the parent Dispatch vision gains an explicit
optional execution-handoff guarantee. The design serves its atomic-claim and
visible-recovery intent and preserves the reviewer vision's
`one-review-one-lineage` and `reuse-before-replacement` guarantees.
It follows [patterns](../../../docs/patterns/README.md): primitives below,
orchestration above; attributable installation ownership; cross-platform parity;
and proportionate safeguards against concrete stale-writer races.

Adjacent work is distinct: #6677 concerns manual task-session exit/resume;
#3208 concerns atomic Bridge end-and-reclaim; #5930 concerns remote-driver
mutation ownership. None is permission to replace an active owner.

## Request

Accepted planning request, quoted as a public-safe excerpt with the private
status preamble omitted:

> May I search for and claim—or create—a public upstream issue and submit a
> design-only effort/contract PR there? It would contain no private references or
> facility details and would not implement, deploy, or activate takeover controls.

The operator confirmed the slug `acknowledged-execution-handoff`.
Subsequent source-and-test authorization is captured here as a public-safe
summary: the same owner may implement the reviewed generic Dispatch adapter
through normal reviewed PRs, after the adopter-owned effect-contract prerequisite
clears review. That prerequisite is complete. No adopter identifiers or private
operational evidence are part of this public effort.
The [contract proposal](contract.md), phased implementation decomposition and
validation matrix below are **agent-recommended**, not additional operational
grants.

## Plan

### Phase 1 - Review the contract
- [x] Land the vision extension, generic [contract](contract.md) and this effort
      through normal proposal review.
- [x] Resolve review findings and identify the existing authorities to extend;
      do not implement a parallel lifecycle or effect store.
- [x] Record proposal landing and the next authorization boundary.

### Phase 2 - Separately authorize implementation
- [x] Obtain explicit implementation scope and assign an implementation owner.
- [ ] Extend the existing coordinator/supervisor admission and drain accounting,
      including complete existing-assignment enumeration and legacy refusal.
      The internal queue construction slice persists closure and inventories
      tasks, reservations and wakes under existing transactions. Control-only
      coordinator scope resolution now binds canonical installation receipts and
      existing managed producers; coordinator enrollment is retained, while
      supervisor enrollment and final launch gates remain open.
      The supervisor cohort follows the
      [logical-machine boundary](../../../visions/plugins/agent-dispatch/README.md#logical-machines-and-local-execution-authority):
      local coordinator authority and independently managed local supervision,
      including self-contained containers. Remote-pool process labels are not
      local runtime provenance or coordinator federation.
      Internal reservation-send custody also serializes first admission
      with closure and retains pending/ambiguous transport outcomes; it is
      wired to coordinator-owned CI repair launch and task delivery in already
      qualified scopes. Both coordinator wake drains use their existing outbox
      identities and delivery tokens for final-send custody; other senders and
      legacy qualification remain open. An empty inventory is never an
      acknowledgement.
- [ ] Define the adopter effect-fence adapter and implement generation-bound
      mutation admission without conflating effect authority with task ownership.
      The first source slice defines and validates the optional
      [effect adapter boundary](../../../plugins/agent-dispatch/docs/execution-handoff.md);
      durable coordinator admission and adopter mutation wiring remain open.
- [ ] Implement durable recovery, observation and explicit handback using the
      same tasks, workspaces and recorded session lineage.

### Phase 3 - Validate and qualify adoption
- [ ] Exercise the [validation matrix](contract.md#validation-matrix) with focused
      unit coverage and isolated periodic process/effect rehearsals.
- [ ] Complete platform and mixed-version capability checks; retain explicit
      unsupported results rather than success-shaped fallback.
- [ ] Obtain separate release/adoption permission, then prove the running
      supported cohort and an authorized handoff/handback before claiming live.

## Validation Plan

- [x] Proposal review confirms scope, owner boundaries, final-effect ordering,
      conservative legacy handling and a validation obligation for each race.
- [ ] Deterministic unit tests prove admission, generations, stale
      acknowledgements, replay, timeout, crash recovery and handback.
- [ ] Concurrent scope enumeration cannot omit queued, claimed, starting,
      running, suspended or pending-wake assignments, including work in flight
      when admission closes; unrelated scopes continue normally.
- [ ] A stale writer cannot begin an effect after revocation acknowledgement;
      already accepted or ambiguous effects block acknowledgement until resolved.
- [ ] Isolated actual-process and fake-external-effect rehearsals prove both
      transition directions, interruption and delayed responses without killing
      unowned processes or executing real adopter effects.
- [ ] Native task/workspace/history identity and author consent survive the
      transition; explicit session transfer is never reported as same-native reuse.
- [ ] Required PR/promotion validation remains focused unit coverage; exhaustive
      process, native and external-effect tests use scheduled/manual containers.
- [ ] Source merge, release, installation and running adoption are reported
      separately; every Plan item is resolved or transferred before Done.

## Proposal

Read [contract.md](contract.md). It is prospective design, not a supported
command or an instruction to operate a running system. The design landed in
[#7270](https://github.com/ThomasMichon/copilot-extensions/pull/7270).
Implementation ownership and source/test scope are recorded under #7268.
Continue Phase 2 through the existing coordinator and supervisor authorities;
do not treat a validated adapter response as complete task admission or live
handoff support.

## Journal

### 2026-10-21 - Clarify logical-machine and supervisor lifetime boundaries
- The coordinator, supervisor master and worker-lane supervisor are distinct
  roles within the same local Dispatch deployment. Retain the separate local
  supervisor service and its ability to survive coordinator upgrades.
- Self-contained container environments own local task authority, required
  session/workspace services and secured remote access. Cross-environment
  federation is between coordinators, not shared task-database ownership.
- This clarifies the Phase 2 implementation boundary, not a running capability.
  Authenticated supervisor enrollment, actual sender mediation, legacy/effect
  integration, handback and release/running adoption remain unresolved.

### 2026-10-21 - Wake sender cohort merged; supervisor integration next
- #7761 merged to `dev` as `47560b585bcded133713591042623f56d9c511ce`
  after current-head Copilot approval, resolution of all five review threads
  and the required checks. The final focused selection passed 356 cases.
- Review follow-through closed direct-task logical-lineage, retry-budget,
  cross-sender final-admission, lock-time lease and unrelated-wake starvation
  gaps without changing the cleanup-only exceptions or replacing the outboxes.
- This closes the qualified coordinator wake cohort only. Continue Phase 2
  through the ordinary supervisor's real runtime/enrollment and final sender
  boundaries. Other sender/legacy qualification, adopter effects, acknowledgement,
  handback and separately authorized running adoption remain open.

### 2026-10-21 - Mediate both coordinator wake outboxes
- Final owner-prompt admission uses each existing wake row and current delivery
  token, with canonical coordinator enrollment in already qualified scopes.
  Pending and unknown transport outcomes survive lease expiry, queue reopening
  and newer wakes without granting another send.
- Accepted owner delivery is not repeated when finishing or detached claim
  release needs a retry. Cleanup-only claim releases remain outside owner
  execution custody, including after closure.
- Review follow-through retains repository/dedup lineage for directly claimed
  tasks without reservations. Both selectors defer shared-lineage conflicts,
  and a late custody refusal preserves the unsent wake's transport retry budget.
- Final reservation-send admission rechecks wake custody even for an earlier
  reservation. Wake selectors also defer reservation-send conflicts without
  starving unrelated work, and final lease checks sample time after locking.
- This advances Phase 2 only. Supervisor enrollment and other senders, legacy
  qualification, adopter effects, acknowledgement and handback remain open.
  Source/test work does not establish release, installation or running adoption.

### 2026-10-21 - Coordinator sender cohort merged; wake mediation next
- #7744 merged to `dev` as `bb5a997270e77a6bccaef794643b4d026da79799`
  after current-head Copilot approval and the required checks. The final
  runtime regression selection passed 199 cases.
- Review exposed and closed a concrete cleanup gap: a known launched session
  must survive custody-settlement failure. The factory now preserves its handle,
  the route requests release without delivering the task turn, and pending
  custody remains unresolved.
- This closes only the coordinator-owned CI repair sender cohort. Continue
  Phase 2 with final-send accounting for both wake outboxes, preserving their
  existing delivery identity, retry ownership and claim-release-only exception.
  Supervisor enrollment/senders, legacy qualification, adopter effects,
  acknowledgement and handback remain open. No installation or running
  adoption is claimed.

### 2026-10-21 - Mediate the coordinator-owned CI repair sender cohort
- The existing CI route now uses canonical process/installation enrollment and
  reservation-bound custody at its actual launch and task-delivery boundaries.
  Independent policy refusal precedes custody; ambiguous transport remains
  unresolved, and closure between stages preserves the release obligation.
- Existing legacy transport behavior is outside this qualified cohort. Historical
  qualification cannot disappear into a fallback, and an in-flight launch cannot
  silently adopt a replacement producer generation.
- This advances Phase 2, not its completion. Supervisor enrollment and senders,
  wakes, legacy qualification, effects, acknowledgement and handback remain open.
  Source review does not authorize installation or a running transition.

### 2026-10-21 - Retry fence merged; actual sender integration next
- #7676 merged to `dev` as `b726e4e09a08ad94a451a7a8a655964c184a4ba8`
  after current-head Copilot approval and the required checks. Unresolved sends
  now fence fresh reservations and recovery mutations without erasing custody.
- This closes the retry prerequisite only. Phase 2 remains open for authenticated
  runtime enrollment and actual launch, delivery, resume, redrive and wake
  mediation, followed by legacy qualification, effects and handback.
- Source landing is not promotion, installation, running adoption or an
  acknowledged transition.

### 2026-10-21 - Preserve unresolved sends across reservation retries
- New reservation attempts, operator rearm and fresh-conversation authorization
  now refuse pending or unknown transport custody under the existing queue write
  transaction. Shared exclusive keys and logical workspace lineage cannot hide
  an earlier task's unresolved send by allocating another attempt.
- Active-reservation observation remains observation-only. Explicit transport
  settlement permits the existing recovery policy without erasing send history,
  changing task generation or claiming process exit.
- #7635 merged retained-runtime custody validation to `dev` as
  `d32355b81c2aa65d13db5789b3022d96c10ac6fe`. Neither slice wires production
  senders, completes supervisor enrollment or proves release/running adoption.
  Continue Phase 2's actual sender, wake, legacy and effect integration.

### 2026-10-21 - Bind fresh custody to retained runtime enrollment
- Fresh reservation-send custody now requires an existing coordinator runtime
  in the exact scope, source, repository and current producer generation under
  the same queue transaction. Caller-selected runtime strings are not enrollment.
- Existing custody replays and settlement remain available without replacing
  legacy participants or granting another send. Complete enrollment, caller
  authentication, actual sender mediation and acknowledgement remain open.
- #7628 merged coordinator runtime accounting to `dev` as
  `bdc81aad6d022b02936dda9da6c7fb2fb856e82a`; source landing does not establish
  release, installation, running adoption or a live handoff.

### 2026-10-21 - Coordinator runtime accounting construction
- Added control-only enrollment of the coordinator's own OS-observed process
  and server-created application incarnation against the canonical installation
  and managed producer binding. Caller-selected processes are not qualification.
- Enrollment and closure share the queue write lock. Exact retries are
  observation-only; closure refuses new incarnations. The inventory retains all
  recorded producer generations and process incarnations without claiming exit.
- Supervisor enrollment and every actual sender/wake boundary remain open,
  together with legacy qualification, adopter effects and handback. Recording
  one coordinator is not complete enrollment, send admission or acknowledgement.
- #7621 merged authenticated scope resolution to `dev` as
  `de1cff37325562ee327da0c14917c1612abd1753`; release and running adoption remain
  separate gates.

### 2026-10-21 - Authenticated scope-binding construction
- Added control-authenticated resolution through canonical installation-governance
  receipt validation and the existing queue/managed-producer authorities.
- Persisted immutable scope/provenance bindings in the same queue transaction.
  Replays preserve identity; generation drift, unqualified legacy callers and
  incompatible historical closure/send scopes remain explicit conflicts.
- Data ingress, task credentials and caller labels cannot supply qualification.
  The endpoint returns no execution capability, runtime enrollment or transition
  acknowledgement. Every production sender, both wake paths, legacy cohorts,
  adopter effects and handback remain Phase 2 obligations.

### 2026-10-21 - Reservation-send custody construction
- Added immutable reservation-stage send identities and same-queue admission
  serialized with closure, without adding a transport, scheduler or live switch.
- Lost replies replay the recorded observation, never fresh send permission;
  another ID cannot repeat the same reservation/task-generation/stage.
- Pending and ambiguous sends survive restart and remain in the complete
  closure inventory, including terminal tasks' history. Known transport
  outcomes remain distinct from process exit and adopter-effect settlement.
- Qualified scope/source bindings apply even when closure precedes custody;
  producer rollover retains older sends without pinning the binding to its
  first producer generation. Fresh operations still require the current producer.
- Deterministic coverage exercises closure/send ordering, durable ambiguity,
  exact replay, conflicting identities and refusal to hide custody by rebinding
  the qualified scope. Runtime enrollment, every actual sender and wake path,
  legacy qualification, adopter effects and handback remain open Phase 2 gates.
- This is source/test construction only, not an acknowledged or adopted handoff.

### 2026-10-20 - Durable queue-closure construction
- Added a same-queue, operation-bound admission closure and transaction-consistent
  inventory without replacing task, allocation, attachment or producer history.
- Queue claims, starts/resumes, new reservations and wake-delivery claims honor
  explicit closure. New natural work may still be recorded; unrelated sources
  continue normally.
- Review follow-through covers create-and-claim, steering-triggered resumes
  and detached-waiter wake claims as well. The consistent inventory retains
  both wake outboxes, including pending and already leased detached deliveries;
  exact accepted creation/steering replays remain intact.
- Evaluator rejection cannot restore submitted-task ownership after closure;
  an exact rejection accepted before closure remains replayable without another
  mutation or wake.
- Cleanup-only detached-waiter claim releases remain deliverable after closure,
  including terminal completion. A regression verifies claim release without
  owner resumption while a genuine wake in the same scope stays blocked.
- Admission closure preserves already-successful start and non-adopting resume
  retries without relaxing owner, generation or session fences. New starts,
  resumes and adoption remain blocked; regression coverage exercises response
  loss followed by closure before replay.
- No HTTP/CLI control, acknowledgement, destination activation or handback is
  exposed. Runtime enrollment, already admitted launches/deliveries, legacy
  qualification and adopter effects remain explicit barriers. Continue those
  existing Phase 2 obligations rather than claiming a live handoff.

### 2026-10-20 - Effect-adapter construction merged; admission integration next
- #7516 merged to `dev` as `3e785cc2851f78424825f1914ec08fac12a5d0b2`
  after current-head Copilot approval and the required PR checks.
- The scoped effect-response boundary and its focused tests are source-merged,
  pending release and separately authorized rollout. No coordinator admission
  or adopter mutation is wired by this slice.
- Continue Phase 2 with durable coordinator admission/drain accounting and
  complete existing-assignment enumeration; retain legacy refusal, the adopter
  effect barrier and the same task/workspace/history authorities. The effort
  and #7268 remain open.

### 2026-10-20 - Source/test scope accepted; adapter construction started
- The existing proposal owner accepted implementation ownership under #7268.
  The adopter-owned effect-contract review prerequisite is complete.
- The initial adapter boundary binds responses to the complete qualified scope,
  operation, execution direction and retiring generation. Unsupported adapters,
  incomplete enumeration, unresolved effects and legacy writers cannot satisfy
  its acknowledgement prerequisite. Transport errors remain errors.
- This construction slice does not wire the coordinator or supervisor, close
  execution admission, grant a task, perform an adopter mutation or advertise a
  live transition command. Those Plan and Validation Plan obligations stay open.

### 2026-10-19 - Proposal landed; implementation gated
- #7270 merged to `dev` as `cb27a321a1f6066fe910176e647e3b72f860091a`
  after current-head Copilot approval and the required PR gate passed.
- Phase 1 and proposal-review validation are resolved. Runtime implementation,
  effect integration, release and adoption remain unperformed and separately
  gated. #7268 remains open; this effort is Blocked, not Done.

### 2026-10-19 - Design-only scope
- Claimed #7268 after checking scoped execution/handoff issues and the active
  effort index. The adjacent pause, reclaim and driver contracts remain owned
  separately.
- Confirmed the effort slug. Captured the public-safe request and separated the
  authorized proposal from future implementation and adoption.
