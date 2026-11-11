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
- **Status:** Done for source scope - plan #11882 and implementation #11962 merged and promoted to `main`; adoption prerequisites documented; no installation, enrollment or adoption.
- **Vision:** [Agent topology](../../../../visions/agent-fabric/agentic-factory/agent-topology/README.md).
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
- [Topology capability](../../../../plugins/agent-roles/skills/managing-agent-topology/SKILL.md)
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
- [x] Land this canonical effort and Proposal through upstream review.
- [x] Resolve review decisions on identity/retirement, attachment admission,
  materialization and migration before their dependent implementation.

### Phase 2 - Persistent task and attachment lifecycle

- [x] Add the standing task/declaration model and idempotent reconciliation,
  retaining stable identity, explicit lanes and all history across restart.
- [x] Implement fenced attachment, detach, yield, unassignment, recovery and
  retirement through existing lifecycle owners; reject finite completion paths.
- [x] Preserve independent finite children, holds, evaluators and existing
  finite/emitter/effort-driver-loop behavior.

### Phase 3 - Materialization and local admission

- [x] Reuse the `agent-roles` materialization/update owner for complete effective
  charters with explicit source and expected/accepted revisions.
- [x] Extend the registrar's local override mechanism with an attachment veto;
  serialize its effective policy with admission and preserve existing occupants.
- [x] Implement capability-selected clients/providers, migration and rollback
  protection before exposing standing records to mixed-version consumers.

### Phase 4 - Topology consumer and complete isolated flow

- [x] Update topology schema, skill and dispatcher charter to reference Dispatch
  evidence without requiring an appointed dispatcher or a second writable map.
- [x] Derive desired, reserved, accepted, held, unknown and retiring coverage
  from native evidence; retain proposed-versus-adopted intent.
- [x] Prove a complete isolated declaration-to-accepted-owner flow, finite child
  completion, owner succession and explicit retirement using real consumer
  boundaries, then publish the source slices. Plan #11882 and implementation
  #11962 merged.
- [x] Record promotion separately from merge. Document installation/adoption
  prerequisites without installing or activating this capability.

### Phase 5 - Close source acceptance

- [x] Reconcile every issue acceptance item to evidence and remaining tracked
  work; update operator-facing docs and the effort index.
- [x] Mark this source effort Done only when every Plan/Validation item is
  resolved or explicitly transferred to a named tracked objective. Keep #11770
  open until its implementation acceptance is actually satisfied.

## Validation Plan

- [x] Contract review: identity, state/transition table, fencing, retirement,
  multi-lane staffing, materialization and deny semantics are coherent.
- [x] Repeat and race declaration discovery, reconcile and restart: exactly one
  durable task per qualified key, preserved history, no false removal on errors.
- [x] Attach/detach/yield/unassign and confirmed lost-owner recovery preserve the
  task; stale generation and unknown liveness never admit another holder.
- [x] All finite terminal paths refuse standing parents, including unverified
  submission and evaluator/verification completion; finite children still finish.
- [x] Retirement with attached, absent and unknown owners preserves obligations
  and history; removal, disable and explicit re-enrollment cannot bypass it.
- [x] Multiple lanes do not share an exclusive claim or double-count staffing.
- [x] Template-only, incomplete and unresolved materializations fail admission;
  source updates do not silently amend an accepted charter.
- [x] Local deny overrides repository enable; unreadable/unsupported policy
  holds; clear does not launch; deny/admit races and existing occupants are tested.
- [x] Interrupt reservation, local admission and acknowledgement; replay events
  and lose replies. Retain operation identities and unknown effects without
  duplicate attachment, timer takeover or forced claim clearing.
- [x] Mixed old/new clients, coordinators and stores fail closed for standing
  work while supported finite work remains compatible; rollback cannot expose a
  standing task as a completable finite task.
- [x] Topology reads agree with native ownership and accepted revisions; idle,
  approval/CI waits, explicit holds and uncertainty are not false vacancies.
- [x] Run changed-code unit tests, focused real CLI/coordinator integration and
  fresh-store/migration checks; record clean-room and external-venue coverage or
  explicit unavailability. Live adoption remains separately authorized.

### Validation evidence and limits

- **Unit and compatibility:** The journal records the passing Dispatch,
  Worktrees and topology packets. `test_queue_standing_sources.py` covers
  repeat/restart/change/last-removal and competing processes;
  `test_standing_charters.py` covers independent lanes, retirement tombstones,
  finite children, old-provider SQL and reservation races;
  `test_standing_admission.py` covers lost replies, rollback, concurrent
  acceptance, succession, materialization drift and serialized local deny.
  Exposure and finite compatibility suites cover refused parent termination.
- **Real consumer boundaries:** The final source/reconciliation packet passed
  61 existing cases. A separate real Git discovery-to-coordinator case passed
  after correcting its fixture and expected repository-derived namespace.
  `test_dispatch_standing_lifecycle.py` passed through the real isolated native
  owner: acceptance, finite-child completion, succession, native conclusion,
  release, settled retirement and restart-preserved history.
- **Fresh state:** The source and native flows use isolated fresh stores and
  exercise restart/migration behavior. This is not a full fresh-OS installer
  clean-room result; no installer behavior changes or installation is claimed.
- **External venue:** No authorized live venue, enrollment or deployment is
  available within this source-only scope. External adoption was not run and
  remains separately authorized, not inferred from passing local evidence.
- **Delivery gates:** #11962 merged to `dev` after an approving exact-head
  review, resolved threads and required CI. One unrelated Windows timeout
  (#11995) passed on rerun; pre-existing failures are tracked separately in
  #11745, #11922, #11986 and #11988. Promotion is recorded in the journal.
  These results do not authorize runtime installation or role appointment.

## Publication impact assessments

### Documentation

The [standing-charter operator guide](../../../../plugins/agent-dispatch/docs/standing-charters.md)
documents declaration discovery, source-set reconciliation, attachment controls,
compatibility and adoption prerequisites. The
[topology skill](../../../../plugins/agent-roles/skills/managing-agent-topology/SKILL.md),
its schema, dispatcher charter and references describe the consuming contract.
This closes existing agent-topology vision intent rather than changing it.
Architecture and installation references remain unchanged: this change adds no
daemon, installer or ownership model.

### Contract

The serving peer advertises `standing_charters` version 1, `finite_child_link`
version 1, `source_set` version 1 and attachment support through `/health`.
Each standing operation checks the serving peer's required capability; old,
missing or malformed support refuses the required standing semantics. Existing
finite wire shapes remain unchanged, and store rollback triggers fence old
writers from treating standing records as finite work.

Adoption is additive and provider-first, followed by optional caller adoption.
No existing finite syntax is removed or requires a new invocation. Exposure,
standing, source and admission tests cover mixed versions, legacy SQL/rollback,
revision conflicts and lost replies. No installer behavior changes.

### Performance

This assessment is source analysis, not a native timing result. Committed-source
discovery captures HEAD, scopes Git status and tree reads to registrar
directories, compares each declaration's blob digest and rechecks HEAD.
Git reads have 5-second and 1-MiB bounds. A successful discovery uses roughly
`8 + D` Git subprocesses for `D` declaration blobs; changed revisions additionally
require an ancestry check, while equal revisions skip it. The per-blob work is
linear and is not claimed to be a latency improvement.

The supervisor groups complete observations by namespace: one retained-repository
query per namespace, then observation and reconciliation per repository. Client
methods also check capabilities. Existing deadline and before-write guards
bound admission to reconciliation, unchanged sets are idempotent and uncertain
observations hold rather than remove work. This uses the existing supervisor
collection cadence, not a new poller or daemon.

The source/reconciliation and real Git-to-coordinator tests named above provide
reproducible isolated correctness evidence. Native cold/warm timing is not
available within this validation scope; no measured latency claim is made.

### Graceful cutover

The existing Dispatch coordinator, supervisor and installer retain their
lifecycle ownership; installation and rollout require separate authorization.
There is no new daemon, installer, routing layer, process-termination path or
lease owner. Mutations use existing durable database, transaction and revision
boundaries. Source reconciliation compares retained observations before writing,
and attachment operation identity preserves recovery after a lost reply.

The common cutover checklist was considered for these writes; this change does
not alter a cutover algorithm. Existing lifecycle documentation remains
authoritative. Isolated interruption/replay tests do not qualify a live cutover
or the complete deployed lifecycle, and neither is claimed here.

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

### 2026-11-10 - Contract accepted

- [#11882](https://github.com/ThomasMichon/copilot-extensions/pull/11882)
  received an approving review with no outstanding findings and passing
  required CI, then merged to `dev` as
  `17aa9b77404e4d886e6daf0d3e2063619450a4f2`.
- The contract-review gate is satisfied. #11770 remains open for implementation;
  no runtime behavior, installation or live adoption changed.
- Next: implement the persistent task and fenced attachment lifecycle, preserving
  finite-task compatibility and the separate materialization/admission gates.

### 2026-11-10 - Persistent reservation implementation

- Implemented standing task identity, revisioned event history and retained
  tombstones, with rollback fences against legacy finite-task transitions.
  Reservation reuses the native assignment relationship owner: restart and lost
  replies retain the original request rather than allocate a second owner.
- Added complete charter materialization/inspection to the topology owner and
  host-local attachment denies using the existing serialized override store.
  Reservation is not acceptance; native-head and materialization admission,
  succession and settlement remain open.
- Focused isolated results: 107 Dispatch standing/relationship tests passed,
  20 override tests passed, and 57 topology tests passed, including actual CLI
  materialization and inspection. Full consumer-flow, clean-room and live-venue
  evidence remain outstanding; no runtime installation or adoption occurred.

### 2026-11-10 - Acceptance, succession and settled release

- Implemented native-head acceptance and succession fences. Unknown custody
  retains the original reservation; a successor must accept the effective
  charter rather than inherit claimed coverage implicitly.
- Settled release requires exact native conclusion evidence and records
  immutable unassignment history while retaining the original relationship
  closure. Lost replies replay the same operation; stale owners, concurrent
  release and transaction failures cannot admit a second attachment.
- Contained source validation passed: 221 Dispatch standing/admission/relationship
  tests and 129 Worktrees incarnation/CLI tests. Corrections to test expectations
  preserved original-request admission and the existing storage exception
  contract rather than weakening production custody checks.
- These are local implementation results, not publication, promotion or rollout.
  Finite-child completion, capability-selected CLI/coordinator integration,
  topology consumption and the complete isolated flow remain open, as do
  clean-room and live-venue qualification. No live enrollment, launch or
  installation occurred.

### 2026-11-10 - Finite-child linkage

- Added an immutable, optional standing-parent reference to ordinary finite
  tasks. Storage triggers and transactional create validation require a
  same-repository, active standing parent; the link never carries the parent's
  exclusive staffing key, and dedup refuses reuse under a different parent.
- Linked children keep their own claim, verification and completion paths and
  finish independently after the parent retires; child completion never
  concludes the parent. Old SQL writers cannot attach or rewrite the link.
- Focused validation: 472 Dispatch tests passed across standing, admission,
  relationship, producer-fence, queue and category suites. One unrelated
  pre-existing failure reproduces on the unmodified base and is tracked in
  #11912. Capability-selected CLI/coordinator exposure, topology consumption
  and the complete isolated flow remain open; nothing was installed or adopted.

### 2026-11-10 - Capability-selected exposure

- Added control-authenticated coordinator routes, a capability-gated client and
  an `agent-dispatch standing` CLI. Clients refuse before effects without
  declared coordinator support or a qualified relationship owner; the CLI names
  exact revisions and never ensures, fails over or retargets a coordinator.
  Finite routes cannot claim or conclude a standing task, and remote dispatch
  forwards standing-parent filters. Operator reference:
  `plugins/agent-dispatch/docs/standing-charters.md`.
- Contained runner validation (`tools/run-plugin-tests.py`, explicit tiers):
  every standing, admission, relationship, override, queue, producer-fence,
  spawn-reservation, remote-dispatch, coordinator and category suite passed.
  The finite compatibility packet (evaluator, verification, submission,
  emitter and effort-driver-loop suites) passed 336 tests. Remaining failures
  are pre-existing and independent of this change: #11912, and an ordering
  dependency on leaked route ownership in the startup admission suite (#11922).
- The exposure flow still uses a test relationship owner. Topology consumption,
  the complete isolated flow through real native boundaries, clean-room and
  live-venue qualification remain open. Nothing was published, installed or
  adopted.

### 2026-11-10 - Derived topology coverage

- Extended the topology schema, skill and dispatcher charter with optional
  Dispatch assignment evidence. The read-only join distinguishes reservations,
  accepted revisions, successor generations, holds, drift and uncertainty;
  neither a quiet owner nor an unsettled predecessor is a vacancy.
- The contained topology packet passed 121 tests, including acceptance and
  succession cases. Ruff F/E9 passed for staged Python changes, and all 29
  changed modules passed the repository's 1,000-line size limit.
- The complete lifecycle through real native owners remains the next gate:
  declaration, acceptance, finite-child completion, succession and retirement.
  Publication, promotion and final acceptance remain open. No installation,
  live materialization, enrollment or adoption occurred.

### 2026-11-10 - Complete isolated consumer flows

- Qualified complete-set registrar discovery and source-revision reconciliation:
  the 61-case source/reconciliation packet passed, followed by a passing real
  Git discovery-to-coordinator case. The latter initially exposed two fixture
  mistakes, corrected without weakening production admission or changing the
  repository-derived namespace contract.
- The real isolated native-owner lifecycle passed acceptance, finite-child
  completion, owner succession, native conclusion, settled release, retirement
  and restart-preserved history. The tests left no owned background work.
- The complete isolated-flow gate is now satisfied; the Phase 4 combined
  publication item remains unchecked until its source is published. No test
  failure from these final consumer checks remains unresolved.
- Recorded unit, real-consumer and fresh-store coverage above, with explicit
  fresh-OS and external-venue limits. Next: finish the registrar/operator
  documentation and required source checks, then review, merge and promotion.
  No live enrollment, materialization, appointment, installation or adoption
  occurred.

### 2026-11-10 - Source delivery complete

- #11962 merged to `dev` at `842e2ef771` after an approving exact-head review,
  resolved threads and required CI; plan #11882 merged earlier at `17aa9b7740`.
  Promotion run `42934747810` then released it to `main` through #12001; every
  changed source file is identical there, and only its changefiles were
  consumed. Promotion is not installation or adoption.
- Added adoption prerequisites to the operator guide: a promoted release at
  every caller, the control credential, a standing attachment owner, complete
  materialization, local deny policy and separately authorized enrollment.
- #11770 acceptance reconciliation:
  - **Reviewed design:** the #11882 Proposal defines states and transitions,
    qualified keys, attachment history, retirement and multi-lane staffing,
    plus dedup, exclusive-key, hold, verification, evaluator and recovery rules.
  - **Registrar kind:** complete-set discovery reconciles idempotently across
    repeats, restarts and competing processes. Removal from an ordered,
    committed source retires the lane with its history; unordered, partial or
    unreadable sources never infer removal.
  - **Focused tests:** reserve and accept (attach); release and cancel (detach,
    yield and unassignment); confirmed succession without timed takeover;
    retirement; independent lanes; and refusal of every finite terminal path.
  - **Topology:** the skill, schema and dispatcher charter reference Dispatch
    evidence and derive coverage from it without a second writable map.
  - **Finite compatibility:** the 336-test evaluator, verification, submission,
    emitter and `effort-driver-loop` packet passed; finite wire shapes are
    unchanged.
  - **Additions:** template-only and incomplete materializations fail
    admission; local deny overrides repository enable; malformed or
    unsupported policy holds; clearing a deny launches nothing; deny/admit
    races and existing occupants are tested; finite children complete
    independently of their standing parent.
- Every Plan and Validation item is resolved. Fresh-OS clean-room and
  external-venue adoption were not run; they belong to the adopter's separately
  authorized installation and are not claimed. No enrollment, materialization,
  appointment, installation, schedule or deployment occurred.
