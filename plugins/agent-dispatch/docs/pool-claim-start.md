# Explicit pool claim and worker acceptance

The explicit version-1 pool placement provider atomically binds an assignment,
occupies capacity and moves a task from `QUEUED` to `CLAIMED` before any workspace
or native process exists. The normal supervisor still uses its legacy
reservation/worker-claim path where no cohort has been projected. The managed
ordinary caller has a separate default-off opt-in described below. Once a cohort is projected, the queue fences
fresh legacy spawning as described below.
See the [lifecycle architecture](spawn-supervisor.md#assignment-lifecycle-current-versus-target)
and [complete catalog owner](pool-publication.md) for those separate boundaries.
The separate [worker decline provider](pool-worker-decline.md) retains a
current decision and failed-closeout blocker, with an explicit coordinator-owned
local observation/requeue boundary. Ordinary consumers negotiate this boundary;
remote closeout and installed/native qualification remain separate.

## Configured-cohort legacy reservation fence

The running `pool_placement/v1` descriptor advertises
`configured_cohort_reservation_fencing_versions: [1]`. After the first configured
cohort observation is projected, fresh queued spawn reservations must use the
explicit pool placement operation. The existing reservation transaction reads
the coordinator's retained cohort revision under the same SQLite write lock
used by projection and reservation. A concurrent operation either commits its
legacy reservation first, or observes the projected cohort and refuses; a
pre-transaction inspection cannot authorize a later bypass.

Both legacy `POST /spawn-reservations` and
`POST /spawn-reservations/revisions` return `409` with
`detail.code: configured_pool_placement_required`, an explanatory `reason` and
`retryable: false`. Changing the driver, allocator, allocation revision or
suspended-reembodiment flag cannot turn queued work into a compatible legacy
request. No task, reservation, assignment or startup counter is changed by
this refusal. A caller must not reinterpret it as permission to spawn,
downgrade placement or switch coordinators.

This fence depends on projection, not a local configuration file, publisher
enrollment or current catalog readiness. Complete nonempty, confirmed empty,
unconfirmed and unavailable projected observations all fence legacy admission.
Later uncertainty, expired metadata, missing members and a queue reopen do not
silently reenable the old path. An unprojected queue retains its existing
legacy contract. Removing or rolling back configuration is not an implicit
request to clear its retained projection.

Existing active/exclusive reservation replay still returns `reserved=false`,
without fresh spawn permission or adoption into pool capacity. Existing
suspended re-embodiment and direct interactive claim/start retain their own
contracts and fences. The separately admitted coordinator CI-repair lane keeps
its existing reservation and launch authority; a `ci-repair` source label alone
still cannot pass that lane's independently verified admission.

This is a provider-side mixed-caller boundary, not automatic publication or
ordinary startup/prompt migration. Projecting a cohort before those consumers
are qualified intentionally blocks their fresh legacy spawns rather than
pretending they enforce pool selection. An older or rolled-back provider that
does not advertise this capability is **not qualified** for configured-cohort
adoption. No code here installs, downgrades or activates such a provider.

## Allocator caller

`DispatchClient.place_pool_task(task_id, reserved_by=..., catalog_revision=...)`
first reads the running coordinator's control-authorized
`GET /pool-placements/contract`. It requires `pool_placement/v1`, version 1,
the requested driver, atomic claim-before-spawn and affirmative worker start.
The default driver remains `cli`. Explicit `driver="ordinary-bridge"` placement
also requires `ordinary_initial_start_versions: [1]`; it is never relabeled
as CLI placement.
The POST supplies the whole catalog's revision, not a preferred pool, a subset
or a caller-computed capacity count. The provider rechecks its complete
configured catalog, task routing and occupied capacity in its write transaction.

Only a validated `reserved=true` result permits that caller's fresh startup.
The result must bind the requested task, allocator, catalog revision,
reservation and claim generation. An active reservation replay returns
`reserved=false`; it is custody evidence, **not** permission to spawn again.
Such a replay can carry its original catalog revision after the catalog moves.
The client does not retry unknown outcomes, choose a newer revision or fall
back to the legacy reservation API. A malformed successful reply reports
HTTP-style `502` with an explicitly unknown outcome; a committed claim remains
retained at its provider.

Fresh ordinary Bridge pool startup allocates its workspace through the existing
Dispatch-aware Worktrees creation owner before asking Bridge to embody the
exact workspace. The original task, reservation, attempt, supervisor and driver
are recorded at creation; the coordinator records `created` ownership before
any native process. The existing per-allocation no-pair option is applied by
that same owner, not forwarded as an unsupported Bridge argument.
Both owners retain the original creation key. A conflicting older creation or
an unconfirmed owner reply retains the claim without another key, replacement
workspace or native launch. Existing retained agents keep their original
invocation and allocation; missing historical attribution is not retrofitted.
The same-key Bridge owner retains the original positively sessionless allocation
snapshot. A lost creation reply or uncommitted coordinator storage does not
force a new sessionless read against the native just created: recovery first
asks that original owner for its keyed result, then validates the complete
original attempt, incarnation and returned native before storing or sending.
Bridge still refuses an unrelated live holder, changed head or conflicting key.
Owned creation also rechecks current task admission immediately before calling
Bridge, including when hosting lookup retained a workspace but the task became
held. A keyed recovery receipt cannot authorize a newly held task's launch.

### Conditional ordinary writer consumer

The prepared-workspace provider can retain the same optional
`worktree_writer_requirement` in `POST /spawn-reservations/{key}/worktree`,
atomically with the original nonce and audited relationship request, before
the first external relationship call. This is distinct from later allocation
storage: its capability is `prepared_writer_requirement_versions: [1]` in the
existing placement descriptor. The client negotiates that support before
sending the field; an older provider is not retried without it.
The requirement must match the original ordinary creation key, complete
workspace and nonce. Replays preserve it when omitted, reject replacement,
and cannot retrofit it after an unpinned relationship or native exists.
A lost relationship reply retains the same expectation and original request.

The ordinary new-workspace consumer negotiates preparation storage support
before workspace creation, then observes the executing Bridge CLI contracts
and passive active-instance selection. The existing preparation owner combines
that selected expectation with the authoritative creation nonce and retains it
through this endpoint before the first relationship. An explicit caller
expectation must agree with the selection and prepared nonce. A pending
relationship reply is reconciled through the same prepared-workspace owner on
retry, preserving its original requirement and creation key; a changed current
selection cannot replace them. Already prepared historical workspaces without
an original requirement are not retrofitted.

This conditional composition stores an expectation, not successful admission.
Independent queue/request, ground-owner and complete participating-writer
checks still govern the first relationship. A future acquisition journal or
caller-ready boolean cannot establish those checks. No admission policy is
installed and no complete participating cohort is qualified by this consumer.

The internal ordinary Bridge caller accepts an explicit `worktree_writer`
expectation for an already prepared workspace. It is the existing version-1
Bridge keyed-creation requirement: installed writer identity and original
creation nonce, not a command, success receipt or cohort permit.
Ordinary coordinator construction still supplies no relationship writer;
this conditional consumer does not enable governed assignment or allocation.

Before a qualified first create, the caller uses `version --contracts-json` to
check the executing CLI's `agent-bridge.cli-contracts/v1` create/status/resume capabilities and reads passive
running Bridge health. The actual active instance must advertise retained
keyed-creation/inspection support and select the expected Worktrees build.
The existing keyed `create --worktree-writer-file -` carries that requirement
on stdin, without a model prompt. Bridge's existing per-attempt capability
negotiation and effect-side retained serializer/nonce guards remain mandatory;
missing support is not permission to retry unpinned.

Before any create request, the requirement is retained against the reservation's
original creation key through the existing allocation-recording endpoint.
Its additive reservation field is independent of `agent_info`, which does not
exist when a successful create reply is lost. Subsequent agent facts must
match that intent; retries of the same allocation carry it forward.
The running coordinator advertises `ordinary_writer_requirement_versions: [1]`
through its existing `pool_placement/v1` descriptor. The conditional caller
requires that support before owner/native effects; an older provider that
merely preserves an unknown JSON field cannot establish its retention semantics.
Storage rejects dropping, changing or retrofitting it and rejects an unqualified
ordinary agent on a governed reservation. Read-back must preserve it. A retry
inherits the original requirement even when the caller omits its optional
argument. Historical unpinned agents are not upgraded in place.
Keyed recovery still checks executing CLI and active daemon support, but does
not require the mutable current selection to equal the retained original writer.
Bridge's original-key journal and effect-side guards decide that recovery;
the caller never substitutes a worktree-head resume.

Before strict resume or a new send, exact-session full status requests
`--worktree-writer` and checks the original keyed journal's workspace, native,
serializer contract, retained requirement and active Bridge instance.
Strict exact-session resume carries the original requirement through the same
`--worktree-writer-file -` transport. Its client negotiates the distinct
`retained_workspace_creation_writer_resume: 1` capability on every attempt;
an earlier successful inspection cannot qualify a replacement daemon.
This path deliberately does not adopt the daemon's newer dynamic Worktrees
selection: a still-qualified original retained build can continue after that
selector moves. Without a stored Bridge identity, the caller can only replay
its original qualified creation key; a workspace head alone cannot authorize
strict resume. The keyed provider must recover the same original journal or
refuse, never retrofit an unpinned attempt or create a replacement conversation.
Read-only recovery of an already delivered native invocation remains available
without new native effects.

The first protected relationship precedes Bridge creation. Independent original
queue/request admission, actual resident qualification and accounting for every
other admitted producer remain separate prerequisites. A future-create
expectation cannot substitute for them, and a later keyed journal cannot
retroactively admit that first relationship. No new service, RPC, installer
replacement veto or per-record-save preflight is introduced.

## Managed ordinary allocator (opt-in)

`AGENT_DISPATCH_POOL_ALLOCATION` is default **off**, independent of
`AGENT_DISPATCH_POOL_PUBLICATION`. Enabling source publication or retaining a
projected cohort does not enable automatic allocation. Both remain disabled
unless explicitly configured; source coverage is not installed qualification.
The existing master passes its actual registration ID, machine and environment
to its same-runtime lane child. The strict parser retains this instance scope
instead of substituting the child's ambient environment; a different or unknown
execution host refuses before launch.
Admission first reads Dispatch's existing installed `machine` binding, then
the configured `AGENT_DISPATCH_SUPERVISE_MACHINE` entry in `supervisor.env`.
These durable service settings do not require a repository working directory.
When neither is configured, the existing Worktrees project lookup remains the
fallback. A child's ambient machine variable or detected hostname cannot admit
a managed instance; an unreadable installed binding refuses rather than falling
through to a different authority. Registration scope remains literal: a physical
host name is not silently equated with a distinct execution-environment identity.

The managed caller first reads the ordinary data-authorized
`/health.pool_cohort` version-1 revision observation. A positively unprojected
queue needs no control-token catalog read and retains legacy supervision.
Configured or unknown state never authorizes that fallback. A cohort projected
after an unprojected cycle's observation is refused by the transactional legacy
fence; the caller cannot upgrade to pool placement midway through that cycle.

For configured state, the caller reads the existing complete catalog and negotiates
`instance_bound_placement_versions: [1]` against the running placement owner.
It supplies `expected_instance` (`registration_id`, `machine`, `env`) and its
configured positive finite `startup_timeout` to `POST /pool-placements`.
These optional fields preserve existing explicit callers' request shape and
600-second default. An old strict provider refuses the new body; missing live
support refuses before POST, with no legacy reservation fallback.

Selected Bridge executable/daemon origin discovery completes before the caller
refreshes the catalog revision it will submit. Discovery may span a publication
renewal; the earlier catalog used to recognize configured mode is not retained
as the later placement snapshot. Loss of catalog readiness during discovery
refuses before POST. The provider still revalidates the submitted revision;
this adds no automatic replay of a refused or unknown placement.

The provider still selects globally across the complete priority-ordered set,
including actual routing and accountable capacity. The expected instance is
**not** a candidate filter: if another instance wins, the transaction refuses
before inserting a reservation or claim, with visible waiting. A matching
registration name on another machine or environment does not own the result.
Replay also checks the instance, never granting fresh launch permission.
Before preparing the ordinary first prompt, the caller reads back the original
assignment and actual post-claim task generation. Lost, stale or conflicting
read-back retains the claim without native creation.

`instance_scoped_reservation_versions: [1]` advertises the adjacent query
contract. `GET /spawn-reservations` accepts all three of `pool_registration_id`,
`pool_machine`, `pool_env` and returns
`X-Dispatch-Pool-Instance: pool-instance/v1`. Filtering uses immutable reservation
bindings before `limit`; it excludes foreign bound instances while preserving
unbound legacy rows and their existing lifecycle checks. Current-task matching
does not mistake an older historical own reservation for current ownership.
Missing query support blocks instance-scoped reconciliation rather than silently
using an unfiltered response. An allocation-disabled configured lane retains its
instance-scoped reconciliation, but cannot create new claims.

The transaction, not label-wide supervisor counts or legacy failed-spawn
dead-letter counters, owns configured ordinary capacity and retry admission.
Separately admitted CI-repair retains its legacy budget and launch path.
Managed CLI backends use their explicit `cli` placement driver, including
per-label CLI overrides in mixed lanes. The caller negotiates explicit start
support before placing a CLI claim, refreshes the actual claimed generation,
and records the workspace with that driver before Bridge acquisition. Its
first prompt evaluates the existing assignment and uses `start
--pool-generation`; it never asks for a second evaluation claim. Bridge
acquisition supplies the actual native identity for that start command.
`cli_initial_start_versions: [1]` additionally qualifies the existing
reservation read-back's `cli_initial_start_error` decision. The coordinator
checks the current startup/decision deadline, assignment and reservation fences
with its own clock. Managed CLI callers require that support before placement
and consume the current decision before workspace preparation and again before
acquisition. A slow workspace return cannot authorize launch from an expired
local task snapshot. Missing metadata is unavailable admission, not permission
to guess the provider's clock.
Provider rollback or lost/conflicting read-back retains the claim without a
legacy fallback or replacement launch. Unbound explicit placement keeps its
previous provider compatibility; these stricter requirements govern
instance-bound CLI placement and its first seed.
An ambiguous acquisition or refused initial seed returns a retained outcome,
not legacy `DEFERRED`. The provider also refuses deferring an unowned
allocator-`CLAIMED` reservation, so pending workspace or older-caller deferrals
cannot bypass startup supervision. Deadline exhaustion can still requeue the
task while its `RELEASING` reservation retains unresolved capacity.

Script and restricted backends are not inferred to be qualified CLI workers
from their workspace interface and remain refused by this caller. Unprojected
queues keep legacy admission. Decline/closeout, complete configured-definition
adoption and installed qualification remain separate gates, so this opt-in is
not a claim that the complete lifecycle is ready for rollout.

## Affirmative worker caller

A worker that has read its allocator-claimed task and independently decided to
accept can use the existing `start` command:

```text
agent-dispatch start <task-id> --pool-generation <current-task-generation> --owner-session-id <actual-native-session-id>
```

Its own machine/worktree identity is resolved normally; the explicit owner
argument remains available. The caller must use the **actual current** native
identity, already recorded through startup, not a generated placeholder.
Do not run the legacy `claim --evaluation` step for an allocator-owned claim.
Decline/closeout and automatic worker-prompt migration remain separate work.

## Explicit ordinary first prompt

The ordinary Bridge create/store/send path can consume an explicitly placed
`ordinary-bridge` assignment. Reservation read-back includes its actual pool
assignment and claim generation. The initial caller requires the running
placement descriptor's `ordinary_initial_start_versions: [1]`, the current
unowned `CLAIMED` task, unexpired startup decision and its assigned local
execution machine before creating or resuming an agent. The existing reservation
admission read exposes deadline ineligibility; storage rechecks the deadline
transactionally so an expiry during creation cannot deliver a first prompt.
It does not choose another pool or forward a local create to a
different host. The managed allocator opt-in remains disabled by default;
driver-correct startup supervision is described below.

Ordinary initial startup resolves that execution machine through Dispatch's
durable installed machine binding, then its configured supervisor file. Only
when both are absent does it use Worktrees project discovery. Unreadable or
conflicting installed settings refuse before create/send; ambient machine
values and detected hostnames do not override the binding or equate a physical
host with a separate guest execution identity.

`ordinary_invocation.startup_seed` selects a distinct allocator-claimed worker
prompt for this assignment. It retains coordinator routing, the charter and
lane-aware duplicate checks, but never emits `claim --evaluation` or a second
evaluation lease. The worker must independently accept through
`start --pool-generation` under its real machine/worktree identity and the
retained actual native identity. A worker that cannot accept retains the claim
and reports its blocker; the prompt does not present legacy yield/abandon as a
pool-aware decline/closeout implementation.

The existing created/resumed-agent storage operation accepts the caller's
`execution_machine` for this path. Its write transaction rechecks the current
assignment, generation, placed driver, holds, startup fence, routing, workspace
and native history. It stores the exact invocation and host before the first
send; read-back must confirm both. No workspace or agent-storage write may
relabel a placed driver. Legacy queued ordinary creation retains its prior
request shape and evaluation prompt; the new field is omitted there.

Initial delivery keeps the existing keyed native invocation, frozen first-turn
position and receipt-only recovery. Delivery still leaves the task `CLAIMED`;
only current-worker acceptance moves it to `STARTED`. A lost storage reply
retains the created agent without sending. An exact historical initial receipt
can be observed after acceptance without replaying the prompt or resetting a
budget. Unsupported or rolled-back providers do not cause a legacy claim,
reservation or send fallback.

Initial admission and later startup supervision remain distinct operations.
The `pool_startup/v1` descriptor advertises `ordinary-bridge` together with
`ordinary_same_native_retry: true`. New callers require both before ordinary
retry effects; older providers retain the claim without a CLI or legacy
fallback. Decline/closeout, complete mixed-driver adoption, publication/allocation
activation and installed/native qualification remain open gates.

## Worker acceptance transport

`DispatchClient.start_pool_task(...)` implements explicit worker acceptance. It reads
`/health.pool_placement` and requires `explicit_start_versions: [1]` in addition
to the placement contract. This descriptor uses ordinary API-read authority;
workers need no control credential. It sends `pool_placement_version: 1`,
the exact generation, worker and native identity to `/tasks/{id}/pool-start`.
This explicit route reuses the start handler and transition owner; an older
provider has no matching route and refuses before mutation even if rollback
occurs after the health read. The caller never retries it as `/start`.
The transaction requires an actual pool assignment and all current startup,
workspace, native, routing and owner fences. It cannot silently start a
legacy interactive claim instead. Response validation requires `STARTED`
under those exact identities; delivery or reachability never substitutes.
An unchanged current-native request can observe its already-accepted result.

Ordinary `start` without `--pool-generation` keeps its existing request and
behavior. Task-only projected workers retain their existing scoped start;
they reject this fleet-worker option rather than silently ignoring it.
Missing or incompatible runtime declarations refuse the explicit operation
before its write. Authentication and provider failures stay visible without
downgrading or changing credentials. No package-version comparison or cached
declaration authorizes a later operation after provider rollback.

## Durable startup budget

The queue's existing pool assignment owns durable startup counters and clocks;
it does not use a separate retry registry. Each explicit claim episode admits
up to three same-reservation startup invocations; advancing requires the prior
invocation's completed return. The count since
successful acceptance survives new episodes; an episode baseline limits each
episode without deleting earlier attempt history. Reachability and transport
delivery never reset either counter.

The original explicit placement API gives each startup invocation a 600-second
deadline, matching the existing supervisor's default reservation window. The
negotiated startup placement API instead accepts a positive finite configured
timeout and persists it in the same claim transaction. A placement replay
does not change it; subsequent same-reservation invocations use that stored
timeout, not a restarted supervisor's current settings. An explicitly
observed current reachable worker instead receives a separate 30-minute
decision deadline. The queue can atomically allocate five-minute nudge slots
for that same recorded native; taking a slot is **not** delivery or acceptance.
Only the successful current `STARTED` write resets the attempt and task-wide
cooldown counters. Acceptance checks the deadline inside the write lock.
Legacy pool assignments without recorded clocks require reconciliation, not
invented historical attempts. Ordinary non-pool worker start is unchanged.

The queue's explicit exhaustion operation returns an expired allocator claim
to `QUEUED`, fences its task generation and applies task-wide 1/3/6/12-hour
cooldowns while preserving any later `not_before`. Its reservation remains
`RELEASING`: missing workspace, native or receipt is not absence. An unresolved
original caller can still publish its late current-revision return for normal
retirement. No timeout itself releases capacity, changes retained pool routing,
erases attachments or permits replacement execution.

The original caller can durably report `startup_failed`, `startup_rejected` or
`startup_unknown` against the exact task, generation, reservation, startup
revision and actual recorded native (including an explicitly unknown native).
These are startup outcomes, not worker declines or proof that no process exists.
A completed failure closes that caller's invocation without freeing its slot.
An unknown outcome leaves the invocation incomplete. An exact replay does not
renew the clock; the original unknown caller can report its later completed
failure. Completed conflicting outcomes refuse. The existing task audit retains
each classification when a new invocation clears the current outcome fields.

Three completed failed/rejected invocations can be exhausted immediately as
`startup_attempts_exhausted`. An incomplete invocation, fewer than three failures,
a stale revision or an already-reachable worker cannot manufacture that result.
Deadline exhaustion remains separately classified as `startup_timeout` or
`decision_timeout`; all three use the same task-wide cooldown and
capacity-preserving release owner.

## Negotiated startup supervision transport

`DispatchClient.pool_startup_contract()` reads the running coordinator's
control-authorized `GET /pool-placements/startup/v1/contract`. This separate
`pool_startup/v1` family advertises durable budgets, configured timeouts,
classified outcomes, same-native decision clocks and capacity-preserving
exhaustion. Its strict version-1 routes do not exist on older providers, so
rollback between discovery and mutation cannot silently invoke an older
placement, start or reservation operation. No package-version comparison,
cached contract, response corruption or transport failure permits a fallback.

| Client operation | Route beneath `/pool-placements/startup/v1` | Meaning |
|---|---|---|
| `place_pool_startup(task_id, reserved_by=..., catalog_revision=..., startup_timeout=...)` | `POST /place` | Atomic configured-timeout claim; only validated `reserved=true` permits initial startup |
| `pool_startup_snapshot(task_id)` | `GET /tasks/{id}` | Coherent budget, reservation, task and actual native observation |
| `observe_pool_reachable(task_id, key, generation=..., startup_revision=..., native_id=...)` | `POST /tasks/{id}/reachable` | Independently observed current worker; not acceptance |
| `take_pool_nudge(...)` | `POST /tasks/{id}/nudge` | One atomic due same-native slot; `None` means no slot, not delivery |
| `record_pool_startup_outcome(..., outcome_kind=..., detail=..., native_id=...)` | `POST /tasks/{id}/outcome` | Original caller's classified outcome; not native absence |
| `retry_pool_startup(..., native_id=..., worktree=..., machine=..., reserved_by=...)` | `POST /tasks/{id}/retry` | One next same-native invocation after a completed failure; not transport |
| `complete_pool_startup(..., outcome_kind=..., detail=...)` | `POST /tasks/{id}/complete` | Exact retry caller return, including an unresolved or late outcome |
| `exhaust_pool_startup(..., reason="deadline"\|"attempts")` | `POST /tasks/{id}/exhaust` | Fenced `QUEUED` with cooldown; unresolved capacity retained |

Every mutation rechecks its exact task and incarnation in the queue transaction.
Responses bind those same facts; malformed successful replies are explicitly
unknown and never replayed automatically. A snapshot with no pool assignment is
not proof of no native effects. Rejected configured placement returns the
structured `pool_claim_rejected` code; admission inhibition keeps its existing
structured owner result. Old explicit placement/start callers and ordinary
non-pool claims remain supported with their original wire forms and defaults.

This is an explicit supervision transport, **not** automatic placement
migration. Suspended recovery uses the separate continuation path below;
these startup operations do not request recovery or override its holds.

## Affirmative suspended recovery

For an existing pool assignment, `resume`, an elapsed cooldown and submitted
steering request continuation without changing `SUSPENDED` to `STARTED`.
The assignment retains its task generation, owner and actual native session.
Its monotonic `recovery_revision` identifies this request; replaying the
request does not restart its clocks. No replacement worker is created.
An intentional monitor or renewed suspension invalidates the old recovery
request without erasing startup debt. Steering submitted under a hold remains
durable but does not open a recovery episode or spend an attempt.

The existing coordinator GC cadence reconciles recovery, using the existing
assignment startup budget, task-wide cooldown stages and wake outbox.
Up to three admitted startup wakes are allowed. A completed, known-not-sent
attempt can admit a new wake; an unknown send retains its original custody
and cannot be replayed as a new attempt. Exact delivery proves reachability,
not acceptance. It starts the 30-minute decision window, with five-minute
same-native nudges. A failed nudge does not turn a reachable worker into an
exhausted startup. Holds freeze these policy clocks and do not spend attempts.
They do not revive a budget that expired before the hold. Reachability uses
only hold time preceding delivery, not the later time spent waiting to clear it.
There is no independent recovery timer, queue or identity registry.

The worker must affirm the current request through the existing start command:

```text
agent-dispatch start <task-id> --pool-generation <generation> --pool-recovery-revision <revision> --owner-session-id <native-session>
```

The wake carries the current revision. The client checks the actual
coordinator's `pool_placement.suspended_recovery_versions` support before
posting to `/tasks/{id}/pool-start`; unsupported providers refuse without an
effectful fallback. Task-only clients discover this through the existing
`GET /tasks/{id}/pool-start/contract`, which returns only capabilities for
their authorized task, not global health or backlog. The queue checks the exact owner, native, generation,
recovery revision and deadline inside its write transaction. Only successful
affirmation restores the lease, writes `STARTED` and resets startup/cooldown
counters. Successful process resume or prompt delivery alone never does.
Task-scoped authority permits only this narrow suspended recovery start,
not other suspended-worker mutations.

Exhaustion returns the task to `QUEUED`, fences its generation and applies
the same 1/3/6/12-hour task-wide cooldown while preserving a later
`not_before`. Its reservation remains `RELEASING` with unresolved capacity
retained; the native attachment history is preserved. Late affirmation and
stale queued sends refuse. Exhaustion is not physical stop, retirement,
absence evidence or permission to free the slot.

Non-pool interactive and supervised continuation retain their legacy behavior.
Full installed, mixed-lane and cohort qualification remains an
[integration gate](#integration-boundary), not a claim made by these source
tests.

## Existing supervisor consumer

The existing reserving and spawned-worker reconciliation points recognize an
unowned allocator `CLAIMED` task through its negotiated coherent startup
snapshot. The recorded allocator supervises that same reservation; a different
lane, held task or changed incarnation is retained without action. An explicit
no-assignment observation preserves the legacy path. Unsupported, malformed or
unavailable startup observations do not authorize legacy gone-worker recovery.

A completed successful invocation needs positive liveness for the retained
native, workspace and execution machine before the supervisor records
reachability. A different or missing live-session result is unknown, not
absence. A failed, rejected or unknown original invocation is not reclassified
as successful merely because its process is live. For CLI workers, the durable
five-minute slot controls notification through the existing host-bound Bridge
transport and its [distinct exact-native informational operation](../../agent-bridge/docs/delegation-contract.md#exact-live-cli-inbox-admission-and-observation).
The provider refuses classified failed, rejected or unknown startup outcomes
before opening the reachable decision clock. The notification independently
negotiates its running receiver's capability and cannot follow a rollover alias,
including after the liveness probe. The message is
`notify`-kind: it does not enqueue work, perform legacy evaluation claiming,
start another native or accept the task.

Failed/unknown delivery consumes only the selected notification slot; a restart
does not immediately resend it or renew the thirty-minute decision deadline.
Expired startup/decision clocks and three completed failures use negotiated
exhaustion, preserving `RELEASING` capacity and the original caller's unresolved
late return. The legacy confirmed-gone path cannot retire this allocator claim.
Admission inhibition and installation governance also preserve it.

Completed failed/rejected calls can admit the next same-native retry only after
an exact native/workspace/host liveness probe. The existing spawn write binds
task, generation, reservation, startup revision, native, workspace, execution
machine and allocator, increments the existing assignment budget and retains the
immutable invocation before transport. Competing or stale retry calls cannot
charge or send a second invocation. An incomplete or unknown original call is
not retry eligibility. Legacy spawn/outcome calls cannot complete or advance an
invocation acquired through the explicit retry route.

The CLI transport uses Bridge's [exact live-CLI admission and keyed receipt
observation](../../agent-bridge/docs/delegation-contract.md#exact-live-cli-inbox-admission-and-observation),
not the ACP native-turn helper or the legacy queued redrive prompt. It sends only
after a validated new retry admission. Its prompt uses `--pool-generation`, not
legacy evaluation claiming. After a lost admission reply, transport response or
supervisor restart, only an observation of the retained invocation is permitted.
Missing, malformed or unsupported receipt evidence remains unknown; it cannot
authorize a send replay, new native or replacement capacity.

Ordinary ACP retains both its Bridge session ID and its distinct native UUID in
the same retry descriptor. Its exact full Bridge status must match the stored
native, workspace, project and path on the recorded execution machine before
reachability or a new retry. It never uses the CLI liveness lookup or follows a
worktree alias. Publication preserves the `local-body:<bridge-id>` handle; the
native UUID is not substituted for that handle.

ACP retry and decision prods use the existing native-bound ordinary
`send --expected-native-session-id --idempotency-key` operation, not the live-CLI
inbox or a keyed first-turn invocation. Each first observes that key's ordinary
`--receipt-only` result. Only the original acknowledged new admission may send
after a qualified missing-receipt response. Recovery is observation-only even
on a receipt miss. Exact accepted receipts settle delivery; a proven busy-turn
refusal counts as `startup_rejected`, not a worker decline, while malformed,
unsupported and lost responses retain an unknown effect. ACP decision prods can
start a turn on the same idle native, but cannot queue, force, resume, replace
or accept work. A lost due-slot reply does not authorize that prod.
Both observation and send include `--no-resume`, negotiated through the running
Bridge's `native_send_no_resume: 1` capability; the receiver enforces stopped-
native refusal under its turn lock, not through a client status-check race.
Idle `at_rest=true` is a valid reachable projection, not evidence of a stopped
process. Retry and decision prompts carry the retained original startup seed,
including actual coordinator routing and charter-loading instructions, because
a refused first send may never have delivered that seed.

The original caller reports `startup_succeeded`, `startup_failed`,
`startup_rejected` or `startup_unknown` through exact completion. Success closes
only startup transport, not task acceptance. The same receipt-only reconciliation
also runs after the worker accepted `STARTED`, completed to `SUBMITTED` or
`COMPLETED`, or the claim exhausted to `QUEUED`. A concluded task must retain
the original native and generation and the assigned completing worker, recorded
in `completed_by` or unambiguous authoritative completion events. Acceptance and
receipt completion use the same normalized-host, exact-worktree comparison;
host case variants do not change the stored worker, native or generation.
Task holds,
steering waits, retired conversations and startup fences still refuse the write.
An exact late completion preserves the task generation, reset counters or
cooldown already established by those independent transitions and keeps a
`RELEASING` reservation's capacity and release intent. Conflicting completion
and unrelated caller identities refuse.

Complete configured-definition adoption and installed qualification still gate
the complete policy. These consumers do not activate allocation/publication.

## Integration boundary

The descriptor still reports `automatic_lane_migration: false` and
`startup_retry_policy_complete: false`. Source support for ordinary ACP retries,
decision clocks, nudges and exhaustion, or managed CLI/mixed-lane initial
prompts, does not qualify decline/closeout, complete configured-definition
adoption or installed activation.
Neither these callers nor their source tests
authorize installed activation, free unresolved effects or qualify the full
[assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md).
