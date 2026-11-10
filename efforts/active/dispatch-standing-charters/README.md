# Dispatch Standing Charters

## Goal

Deliver the source contract in [#11770](https://github.com/ThomasMichon/copilot-extensions/issues/11770):
one durable Dispatch task per declared standing-charter lane, with replaceable,
fenced attachments rather than invented completion conditions. Integrate the
record with the existing topology capability, materialization owner and local
attachment controls without changing finite-task behavior.

- **Slug:** `dispatch-standing-charters`
- **Repo:** copilot-extensions
- **Branch(es):** Independent per-slice PRs targeting `dev`.
- **Created:** 2026-11-09
- **Status:** Draft - contract review precedes implementation.
- **Vision:** [Agent topology](../../../visions/agent-fabric/agentic-factory/agent-topology/README.md).
- **Umbrella issue:** [#11770](https://github.com/ThomasMichon/copilot-extensions/issues/11770).

## Guiding Intent

Desired topology stays versioned intent. Dispatch supplies the durable runtime
assignment record through its existing coordinator, task store and event history.
An idle role holder can remain healthy indefinitely; activity, liveness, accepted
role coverage and authority are different facts. A finite unit of work ends;
the standing charter under which it was undertaken does not.

This closes existing topology intent: complete charters, native ownership,
accepted coverage, intent before effects and recovery without duplicate owners.
It does not introduce another assignment ledger, scheduler or daemon.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Contract, implementation, focused validation and PR stewardship | Issue #11770; managed source worktree `oiled-dial` |
| Maintainer and repository review | Review design and source slices through normal repository gates | Linked PRs |
| Adopting operator | Separately authorizes materialization, deployment and live assignments | Adopter-owned policy; not appointed by this effort |

## Coordination

- **Topology:** Independent, dependency-ordered slice PRs; no shared merge queue.
- **Host (owns PRs):** Source driver.
- **Delegates:** None.
- **Handoff:** The issue and this effort retain scope, outstanding Plan and
  Validation items, PRs and ownership. A completed slice is not the whole goal.
- Existing registrar, task lifecycle, native worktree/session custody,
  `agent-roles`, Bridge and release owners remain authoritative in their domains.
  This effort does not replace their active work or confer runtime control.

## Context

- [Issue and design additions](https://github.com/ThomasMichon/copilot-extensions/issues/11770)
  define the durable task, local deny, materialization and finite-child outcomes.
- [Topology capability](../../../plugins/agent-roles/skills/managing-agent-topology/SKILL.md)
  owns desired intent and derived role coverage.
- Dispatch already expands registrar documents, reconciles stable registrations,
  materializes revision-selected worker templates and fences native task owners.
  Those mechanisms are extension points, not competing stores to copy.
- An `effort-driver-loop` emits finite occurrences. A standing charter is not a
  renamed emitter, and its identity must not disappear when an occurrence ends.
- Existing task `detach` changes pinning/affinity. Ending a standing attachment
  needs an explicit charter operation without changing that existing command.

## Request

The operator authorized starting the source build-out:

> Great. Start building this out

The public issue and its design additions define the requested generic outcomes.
The detailed representation below is an agent-recommended implementation
proposal, not an operator mandate for particular field or command names.

## Scope Approvals

The direct operator instruction above authorizes source/design work, issue and
PR delivery, and bounded validation in isolated stores/environments. The first
slice is this reviewed contract; implementation starts after it lands.

No live enrollment, role appointment, live charter materialization, agent
start/stop/replacement, deployment/update, schedules, new access, changed review
permissions or expanded monitoring is authorized. A merged plan, cleared deny,
installed plugin or bound state home does not supply that authority. Preserve
later narrowing and explicit holds; recover missing authority before effects.

## Plan

### Phase 1 - Reviewed contract

- [x] Find and claim the existing issue; ground the proposal in registrar,
  task-lifecycle, template-materialization and topology owners.
- [ ] Land this canonical effort and Proposal through upstream review.
- [ ] Resolve review decisions on identity/retirement, attachment admission,
  materialization and migration before their dependent implementation.

### Phase 2 - Persistent task and attachment lifecycle

- [ ] Add the standing task/declaration model and idempotent reconciliation,
  retaining stable identity, explicit lanes and all history across restart.
- [ ] Implement fenced attachment, detach, yield, unassignment, recovery and
  retirement through existing lifecycle owners; reject finite completion paths.
- [ ] Preserve independent finite children, holds, evaluators and existing
  finite/emitter/effort-driver-loop behavior.

### Phase 3 - Materialization and local admission

- [ ] Reuse the `agent-roles` materialization/update owner for complete effective
  charters with explicit source and expected/accepted revisions.
- [ ] Extend the registrar's local override mechanism with an attachment veto;
  serialize its effective policy with admission and preserve existing occupants.
- [ ] Implement capability-selected clients/providers, migration and rollback
  protection before exposing standing records to mixed-version consumers.

### Phase 4 - Topology consumer and complete isolated flow

- [ ] Update topology schema, skill and dispatcher charter to reference Dispatch
  evidence without requiring an appointed dispatcher or a second writable map.
- [ ] Derive desired, reserved, accepted, held, unknown and retiring coverage
  from native evidence; retain proposed-versus-adopted intent.
- [ ] Prove a complete isolated declaration-to-accepted-owner flow, finite child
  completion, owner succession and explicit retirement using real consumer
  boundaries, then publish the source slices.
- [ ] Record promotion separately from merge. Document installation/adoption
  prerequisites without installing or activating this capability.

### Phase 5 - Close source acceptance

- [ ] Reconcile every issue acceptance item to evidence and remaining tracked
  work; update operator-facing docs and the effort index.
- [ ] Mark this source effort Done only when every Plan/Validation item is
  resolved or explicitly transferred to a named tracked objective. Keep #11770
  open until its implementation acceptance is actually satisfied.

## Validation Plan

- [ ] Contract review: identity, state/transition table, fencing, retirement,
  multi-lane staffing, materialization and deny semantics are coherent.
- [ ] Repeat and race declaration discovery, reconcile and restart: exactly one
  durable task per qualified key, preserved history, no false removal on errors.
- [ ] Attach/detach/yield/unassign and confirmed lost-owner recovery preserve the
  task; stale generation and unknown liveness never admit another holder.
- [ ] All finite terminal paths refuse standing parents, including unverified
  submission and evaluator/verification completion; finite children still finish.
- [ ] Retirement with attached, absent and unknown owners preserves obligations
  and history; removal, disable and explicit re-enrollment cannot bypass it.
- [ ] Multiple lanes do not share an exclusive claim or double-count staffing.
- [ ] Template-only, incomplete and unresolved materializations fail admission;
  source updates do not silently amend an accepted charter.
- [ ] Local deny overrides repository enable; unreadable/unsupported policy
  holds; clear does not launch; deny/admit races and existing occupants are tested.
- [ ] Interrupt reservation, local admission and acknowledgement; replay events
  and lose replies. Retain operation identities and unknown effects without
  duplicate attachment, timer takeover or forced claim clearing.
- [ ] Mixed old/new clients, coordinators and stores fail closed for standing
  work while supported finite work remains compatible; rollback cannot expose a
  standing task as a completable finite task.
- [ ] Topology reads agree with native ownership and accepted revisions; idle,
  approval/CI waits, explicit holds and uncertainty are not false vacancies.
- [ ] Run changed-code unit tests, focused real CLI/coordinator integration and
  fresh-store/migration checks; record clean-room and external-venue coverage or
  explicit unavailability. Live adoption remains separately authorized.

## Proposal

### Identity and storage

Add a distinct standing-charter declaration and task kind. A qualified key is
the declaring owner/namespace plus stable charter key and explicit lane key.
Use one lane for each desired exclusive holder, not a holder count sharing a
claim. Display labels and placement changes do not replace task identity.

Persist the kind, qualified declaration identity, standing-effort reference,
role/materialization reference, expected and accepted revisions, desired
lifecycle and attachment history in the existing coordinator store. Reuse its
transaction/event and owner-generation mechanisms. Charter content remains
owned by `agent-roles`; Dispatch resolves a complete materialization rather than
turning its existing behavior-overlay field into a competing role record.

The qualified key stays reserved while standing, including when unstaffed,
held or retiring. Retirement leaves an identifiable tombstone and history.
Re-enrollment requires settled retirement, addresses that identity explicitly
and advances its declaration generation; rediscovery cannot silently resurrect
it or mint a duplicate.
Finite-task dedup release rules remain unchanged.

### Lifecycle and attachment

These are semantic states; their storage representation must compose with the
native queue rather than creating a parallel scheduler.

| Event | Standing record | Attachment and admission |
|-------|-----------------|--------------------------|
| Valid declaration first reconciles | Open, stable task created | Unstaffed; gates still required |
| Repeated identical reconciliation | Same task and history | No launch or reassignment |
| Eligible native claim/reservation | Remains open | One fenced reservation; not accepted coverage |
| Owner accepts complete effective charter | Remains open | Attached; accepted revision recorded |
| Detach, yield or authorized unassignment | Remains open | End exact attachment after native settlement; reopen eligibility |
| Confirmed native owner loss | Remains open | Recover through existing fencing/obligation rules |
| Owner unreachable, stale observation or quiet wait | Remains open | Retain owner/unknown state; no timed takeover |
| Explicit hold or local deny | Same task and history | Block new admission; do not terminate occupants |
| Explicit retirement intent | Retiring | Close admission; settle owner and obligations through native owners |
| Retirement settlement confirmed | Retired, history retained | No attachment or eligibility |

Normal worker completion, abandonment-as-completion, unverified submission and
evaluator/verification terminal transitions cannot conclude a standing parent.
Return a clear standing-lifecycle refusal directing callers to the appropriate
detach or retirement operation. Existing finite semantics and the existing
affinity-only `detach` command remain intact.

Retirement can be requested by an explicit administrative operation, an explicit
declaration disable, or authoritative removal from a successfully read complete
declaration set belonging to the same source owner. Parse failures, unreachable
sources and incomplete discovery are not removal. Registrar-local attachment
deny is not declaration disable. Pending retirement blocks new attachment but
does not forcibly stop a holder, discard a child or clear an outbound obligation.
Uncertain settlement remains retiring/unknown until the native owner resolves it.

Attachment history records owner/session/generation, operation identity,
materialization accepted and end reason. Succession uses existing native
handover semantics; it neither fabricates acceptance for a successor nor
temporarily advertises vacancy while the prior claim remains unsettled.

### Materialization and admission

A declaration may exist as an unstaffed, blocked record before activation.
Attachment requires an owner-supported materialization in the adopter's state
home: stable identity, explicit source template/revision and complete effective
charter. A floating template link, missing materialization or failed resolution
cannot activate it. Updates are reviewable source changes; expected revision and
the holder's accepted revision stay distinct. Drift is visible, not an implicit
amendment, replacement or grant of authority.

Reuse the registrar's local override store and owner, with selectors scoped to
the declaring namespace, charter and optional lane on the admitting machine.
Local deny wins over repository enable. Unsupported or unreadable local policy
is a reasoned hold, not allow. Clearing it restores eligibility only; it is not
a launch or attachment command. Existing occupants remain visible for separately
authorized, owner-mediated drain; no automatic drain policy is introduced here.

The native local override writer and local admission must share a serialization
point. Admission binds the exact effective policy revision and native reservation
identity before accepting the attachment. A deny ordered before admission
prevents it; a deny ordered after completed admission reports an existing
occupant, not retroactive revocation. Central reservations are not accepted
attachments: a remote coordinator cannot waive the machine-local decision.
Unknown/interrupted admission keeps the reservation recoverable with its
original identity, not eligible for a second allocator. Implementation must
demonstrate this boundary with competing writer/admission tests.

### Finite work and topology

Children are ordinary finite tasks with their own acceptance, verification,
completion and native resource custody. Parent linkage does not inherit the
standing parent's exclusive staffing key, and child completion never concludes
the parent. Detaching or retiring a parent does not silently abandon its children.

The topology schema can reference standing records as assignment evidence even
without an appointed dispatcher. Its overview joins desired declarations,
accepted attachments and native worktree/head evidence. A reservation, live
process or chat presence alone does not count as role coverage. Preserve
unknown/unreachable, waiting, held and revision-drift states instead of collapsing
them into healthy or vacant. The dispatcher remains a consumer of native
allocation and lifecycle owners, not another queue or assignment database.

### Compatibility and performance

Introduce provider support before callers depend on it. New clients select
standing operations only from the running coordinator's declared capability,
not package version or command presence. Unsupported peers return an explicit
hold/error; never reinterpret standing work as finite or ignore unknown flags.

Migrations must preserve existing finite rows and events. Before enabling
standing records, fence older readers/writers that could complete or reclassify
them. A rollback either retains a provider capable of enforcing that fence or
refuses use of the incompatible store with a recovery path; downgrading must
not erase the task-kind distinction. Record the supported mixed-version and
upgrade/rollback window with the implementation.

Reconcile once per normal discovery/change opportunity and use existing task
events and allocation backoff. An unstaffed or idle standing role does not create
a tight emitter loop, per-role watcher, recurring model prompt or new daemon.
Derived overview reads use bounded native queries; no second persistent map.

## Journal

### 2026-11-09 - Contract proposal

- Claimed #11770 for source/design and isolated validation. The first slice is
  this canonical plan; no runtime implementation or adoption is claimed.
- Grounded the proposal in existing declaration expansion, stable registrar
  reconciliation, template materialization and fenced lifecycle owners.
- Separated lifetime from attachment because finite occurrence completion and
  affinity-only detachment cannot preserve a standing role's identity/history.
- Implementation, promotion and live installation remain distinct gates.
