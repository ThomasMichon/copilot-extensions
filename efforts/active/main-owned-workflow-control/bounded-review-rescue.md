# Owner-only local-provider Code Review rescue

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Portfolio and retained owners: [shared roadmap](workflow-portfolio-audit.md).
Standing intent: [supported capacity and reversible migration](../../../visions/main-owned-workflow-control/README.md).

## Scope and authority

This is a proposed slice of #6237, coordinated with #7122 and the existing
#3336 controller, not a second effort, publisher or runner controller.
The operator requested periodic runner/queue/failure observation, an
administrative checked-in switchover, bounded native review requeueing, and
release back to hosted routing followed by reassessment. Local providers act
only on behalf of the repository owner, supplying a backup runner set during
congestion. This is a general provider contract: it names no deployment host,
machine inventory, fixed runner-set name or required orchestration product.
The detailed policy and implementation below are **agent-recommended**.

Hosted remains the default. Rescue is an explicitly authorized,
repository-wide temporary mode, not transparent per-job overflow or an ordered
list of hosted/self-hosted runner labels. Plan review permits implementation
through ordinary review; it does not enable observation or mutation, authorize
a live canary, allocate capacity, provision credentials or bypass protection.
Existing runner, CI/release and remediation owners retain their scopes. A local
provider must be explicitly delegated by the repository owner and bound to
that owner's configured repository and admitted workload. This is not a shared
contributor runner pool or authority for collaborators, other repositories or
untrusted PR code to activate or consume local capacity. Ordinary repository
Write/Maintain permission alone is not the required owner delegation.

## Feasibility boundary

Use a provider-supported hosted primary and an explicitly admitted local backup
runner set. Resolve the provider, runner-set identifier and deployment details
from owner-approved configuration, never from a machine name in this plan.
Earlier native hosted/self-hosted reviews and local source-guard checks prove
only individual tested modes, not general provider compatibility, current live
Main admission or an automatic rescue cycle.

No documented native ordered fallback or per-review runner override has been
established. Generic Actions variables, workflow dispatch and runner-group
selection are not evidence of native Code Review compatibility. Ordinary
Actions rerun retains its original SHA/ref/actor; do not assume it adopts a new
default-branch setup definition. The native re-request path must be qualified.

A repository-wide runner switch is only a candidate mechanism: prove that
native/provider admission still restricts backup execution to the accepted
repository-owner delegation. Selecting only a few requeue requests is not
sufficient if unrelated native reviews could also reach that runner set. If
the provider cannot enforce the owner-only boundary, leave rescue disabled
rather than broadening access or claiming the switch meets this contract.

## Lifecycle and policy

`observe -> candidate -> authorized switch -> backup verification -> bounded
requeue window -> hosted restoration -> release verification -> cooldown`

Use one durable cycle and one accepted writer per repository. Reuse the
existing controller/lifecycle owner's scheduling and recovery boundary; do not
install an ad hoc session timer, observer daemon or competing workflow merely
to demonstrate the plan. Observation is read-only; mutations require separate
accepted authority. Restart resumes the same cycle rather than requeueing it.

Before enablement, the owner must approve explicit values for observation
cadence, evidence freshness, sustained queue cutoff, eligible failure classes,
requeue count/attempt caps, rescue duration, restoration deadline, cooldown,
capacity and escalation owner. Missing or ambiguous policy leaves rescue
disabled/blocked with a reason, not guessed thresholds. Observation must be
bounded and cheap rather than holding an allocated runner waiting for work.

### Observe and classify

Read actual native workflow/run/attempt/job identities, PR and current head,
request/queued/start/completion timestamps, assignment and terminal result.
Record default-control revision and fresh qualified runner/capacity readiness.
Distinguish a review-service request queue from an unassigned Actions job queue;
one cannot be inferred from the other.

Only positively classified runner availability/queue failures may nominate
rescue. Human approval or `action_required`, code/test/lint failures, review
findings, Copilot inference quota/outage and unknown failures are not runner
faults. Preserve genuine results; rerunning until green is not recovery.
An online/idle runner is not sufficient admission: approved source, image,
toolchain, owner delegation, repository binding, trust, isolation and configured
shared-capacity limits must also hold.

### Authorize and check in

Use the existing owner-approved critical Main control path for the exact
dedicated review workflow's admitted hosted-to-backup runner change. Re-read
current control and require the approved diff, current-base admission, required
checks and existing author/merge-role boundaries. Helpers, tests and implementation still land
through Dev and qualified promotion. Preserve the same control through later
promotion; no source-plus-control mixture or widened preservation allowlist.

Administrative check-in is not blanket permission to bypass CI/review or
protected Main policy. Automated mutation needs separately accepted scoped
authority and disabled-by-default implementation. Do not reuse review-request
credentials as a general publisher or invent a new check publisher/token.

### Prove control availability before claiming outage rescue

The current Main source gate itself uses hosted capacity. A total hosted outage
can therefore block the PR needed to escape it. Separately qualify an approved
available control/admission path, or hold and escalate for an exact
owner-authorized recovery. Degraded-but-available hosted control and total
hosted unavailability require separate acceptance claims.

Do not spoof a check, weaken its guard or automatically take an administrative
CI bypass. If a separate local-provider control role is proposed, its exact job,
label, trust, toolchain and shared-capacity admission need existing-owner acceptance
and actual execution proof. The review-worker role/credentials must not become
ordinary control CI, and a structurally qualified candidate label is not
approved control capacity.

### Requeue within the window

After authoritative default-control readback confirms this cycle's switch,
use the documented native Copilot reviewer request/re-request operation for a
bounded selected PR/head set. A manual setup dispatch is not a native review.
Prove how old queued/in-flight attempts are cancelled, superseded or drained
before replacement; do not assume request deduplication or retargeting.

Never create two authoritative attempts for one review obligation. Re-read
head/epoch before each request, retain attempt/result identity and reject
stale/duplicate events. External approval and normal trust gates still apply;
no broad review wave or retry of unrelated heads. Verify actual backup-runner
job assignment and full native review completion, not just setup success.

### Restore and release

At window expiry, stop new rescue requests even if restoration is blocked.
Restore the prior approved hosted route through the same authorized
current-base checked-in path. Restore only this cycle's owned routing change:
do not reset a whole tree or overwrite another owner's newer repair. If
ownership/control moved, reconcile with that owner rather than forcing it.

Restoration governs new reviews; already running backup attempts remain tracked
within their existing execution budgets. Verify default hosted control and a
subsequent actual native hosted review before reporting successful release.
Failed or uncertain switch/requeue/restore writes remain visible owned recovery
obligations. A restoration deadline miss escalates; it does not manufacture
all-clear, extend the requeue window or release custody. Preserve cycle state
across restart, then enter approved cooldown before another candidate.

## Implementation and acceptance sequence

1. Existing owners accept the scheduling/control seam, explicit repository-owner
   delegation, provider/repository/workload admission, policy values, recovery
   owner and non-overlapping scope; review this amendment normally.
2. Land disabled policy/state handling and focused tests through Dev. Reuse
   existing control/request adapters where compatible; any necessary extension
   follows their owners' review. No new live scheduler or permission rollout.
3. Prove report-only classification and real Git/admission/recovery contracts.
   Resolve control availability separately for partial and total hosted outage.
4. After explicit authority and canary acceptance, execute one bounded native
   switch/requeue/backup-review/restore/hosted-review cycle within admitted shared
   capacity. A failed step stays held with its exact recovery owner.
5. Only after that proof, separately accept periodic operation and publish
   as-is operating guidance. Measure queue/review effects without claiming
   independent service quota relief or ordinary CI offload.

## Validation obligations

- [ ] Unit: runner-fault classification versus service quota, code/review
  failures, human approval and unknown inputs; missing/disabled policy,
  stale readiness, caps, expiry, cooldown and finite recovery states.
- [ ] Event/authority negatives: duplicate/out-of-order events, head/base/epoch
  movement, old approval, forbidden scope and denied/ambiguous writes never
  silently activate, retry indefinitely or erase a genuine failure.
- [ ] Owner-only admission: missing/revoked owner delegation, collaborator-only
  authority, wrong repository/provider/workload and untrusted head execution
  refuse backup access. Prove unrelated native reviews cannot enter the backup
  set during a repository-wide switch; otherwise that mechanism cannot enable.
- [ ] Real Git/admission: exact Main diff and current-base policy; preservation
  through promotion; mixed source/workflow rejection; concurrent independent
  repair survives switch and restoration.
- [ ] Consumer/restart: documented native request semantics, cancellation or
  supersession, bounded attempt deduplication, restart of an interrupted cycle,
  failed restoration and explicit deadline-miss escalation.
- [ ] Control availability: exercise a failed control gate and total hosted
  unavailability using an accepted independent admission path or a demonstrated
  hold/escalation. Report the latter as no automatic full-outage rescue.
- [ ] Live native canary: actual checked-in switch, actual backup-set assignment,
  completed review, bounded window, verified hosted restoration and subsequent
  native hosted review. Keep total admitted slots and isolation unchanged.
- [ ] Record unit, lightweight consumer, fresh-environment and live Actions
  tiers with explicit unavailable/skipped reasons; planning or manual setup
  success alone closes none of the native execution obligations.

No test coverage, required context, platform/deep lane, production deadline or
existing release hold changes as a consequence of this plan.
