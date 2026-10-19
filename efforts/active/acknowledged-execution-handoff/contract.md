# Execution handoff contract proposal

Back to the [effort](README.md). **Design only; not a supported API.**
Names below describe required semantics, not installed commands or a committed
wire/storage schema. [#7268](https://github.com/ThomasMichon/copilot-extensions/issues/7268)
coordinates the generic work.

## Scope and authority

An execution scope is selected by an authorized control-plane owner from the
existing installation/coordinator, repository lane and producer registration.
A caller-provided source name or label alone cannot select another owner's work.
Discovery, observation and final effect checks use that same qualified scope.
Independent installations and unrelated scopes remain independent.

Dispatch remains authoritative for natural tasks, claims, workspace allocations,
attachments and execution admission. The adopter remains authoritative for
business policy, effect authorization and effect outcomes. A mode transition
does not grant new credentials, task ownership, consent, merge power or authority
over a live human session. Manual execution is not exempt from these boundaries.

Reuse the existing coordinator and supervisor. The optional adapter adds no
resident, global shutdown switch, parallel scheduler, replacement task store or
direct database-editing workflow. An adopter can expose its own operator UX
while consuming this generic capability.

## Admission and acknowledgement

The prospective transition has four observable stages:

| Stage | Required condition | Permitted next action |
|-------|--------------------|-----------------------|
| Stable execution | One current execution authority for the scope | Request a scoped transition |
| Closing admission | Transition durably recorded; no new old-mode admissions | Reconcile all existing assignments and effects |
| Draining or blocked | Already accepted work is accounted for; unresolved work is visible | Acknowledge only when every prerequisite is satisfied |
| Acknowledged destination | Old execution/effect authority retired; selected destination has current authority | Admit destination execution through ordinary task ownership |

Acceptance of a transition request means only that the request was recorded.
Acknowledgement means the required admission, execution and effect barriers
have all completed for one current scope generation. A timeout, empty response,
unreachable participant or best-effort stop is not acknowledgement.

Closing admission must serialize against task claims, worker spawn, session
start/resume, queued delivery, wake/redrive and concurrent scope changes.
It must account for operations admitted just before closure, not only a snapshot
of running PIDs or new producer creations. Enumerate existing queued, claimed,
starting, running, suspended and pending-wake assignments, retaining their
task/workspace/session and generation associations. Concurrent enumeration
must either converge under the admission barrier or return a visible blocker.

Producer discovery may continue to record natural work while execution is
closed, provided it cannot start old-mode execution. The implementation must
state which producer paths remain enabled and prove that none bypass admission.
Do not retire the old generation merely to obtain a new creation capability:
existing work and current producer-fence rules must be reconciled first.

## Existing writers and the adopter boundary

Generation checks at task creation or preparation are insufficient: a worker
can prepare while authorized and publish much later. Every consequential
adopter mutation must enter an adopter-owned, generation-aware effect boundary,
including final publication and thread mutation in a review adopter.

Revocation and effect admission must serialize. After revocation is
acknowledged, an old generation cannot begin another effect. Effects accepted
before revocation must finish, be safely cancelled with a known outcome, or
remain explicitly unresolved; the transition cannot acknowledge while one
could still land later. A check followed by an unfenced remote write leaves a
race and does not satisfy this contract. Ambiguous remote outcomes require
reconciliation through the effect's existing authority and idempotency rules,
not blind replay or a promise of cross-system transactions.

The adopter supplies capability detection, the admission/revocation adapter
and outcome reconciliation. Dispatch records their current transition result,
not a competing copy of domain truth. An unsupported or unreachable adapter
blocks handoff. Its claimed acknowledgement must be tied to the current scope
and generation; stale or reordered responses cannot authorize a new mode.

Legacy tasks or writers lacking a compatible execution/effect association are
not retroactively safe because a label matches. Refuse acknowledgement until
they complete or an independently authorized, identity-fenced recovery
establishes the boundary. No automatic process kill, task recreation, history
discard, lease theft or global supervisor stop is a substitute.

## Execution identity and manual work

Keep each natural task and allocated workspace. Preserve native history,
findings, author replies, revision accounting, attempt budgets and current
consent. Destination workers use ordinary ownership checks; a scope capability
does not let a coordinator attach a second writer to an actively held task.

A retained reviewer may resume its own native conversation. If an authorized
ownership transfer is necessary, record it as a transfer in the existing
attachment lineage; do not claim that a new native conversation is the old
one. The contract does not turn a delegated review into author consent or
make a parent task terminal after one verdict.

Read-only observers may coexist. A reviewer or operator sees the requested
mode, effective mode, scope generation, unresolved assignments/effects,
acknowledgement state, responsible owner and recovery action. Unknown liveness
and incomplete enumeration stay unknown, not empty or successful.

## Crash recovery, expiry and handback

Transition operations are retryable through durable operation identity.
Retries return the existing transition or its explicit current conflict;
they cannot increment generations twice or replay a retired capability.
Concurrent transition requests have one serial winner and visible conflicts.

After coordinator, supervisor, adopter or manual-controller interruption,
recover the recorded transition and reconcile current participants before
admitting work. A lost acknowledgement is re-observed through its authoritative
owner, not assumed. Replayed, out-of-order or previous-generation messages have
no effect. Generation authority must not move backwards after restore/restart.

Controller expiry exposes a recovery-needed state. It does not silently return
to automatic execution, free a held task or establish that an old writer is
gone. Cancellation of a transition must itself reconcile any revocation or
already admitted destination work before restoring a stable mode.

Handback follows the same barrier in reverse: close manual admission, settle
or visibly block manual assignments/effects, retire manual authority, acknowledge
the current automatic destination, and then wake pending natural tasks through
the ordinary runtime. No old automatic capability becomes valid again. A crash
between acknowledgement and wake is recoverable without duplicated execution.

## Validation matrix

The following is an **agent-recommended implementation obligation**, not a claim
that any rehearsal has run. Use synthetic tasks and a fake effect sink. Preserve
existing unit-only, impact-conditional PR/promotion policy; actual process,
native and external-effect rehearsals run in isolated scheduled/manual
containers, not unconditionally on every PR.

| Case | Required assertion | Smallest suitable tier |
|------|--------------------|------------------------|
| Concurrent claim/spawn/wake and admission closure | No old admission escapes; every previously accepted assignment is accounted for | Deterministic unit interleavings, then isolated process rehearsal |
| Existing suspended, starting, legacy and pending-redrive work | Complete enumeration or explicit blocker; no fabricated replacement task | Unit fixtures and mixed-version rehearsal |
| Revoke between preparation and mutation | Old generation cannot enter the final effect boundary | Unit race with fake effect adapter |
| Effect accepted before revocation; delayed or ambiguous remote response | No acknowledgement until its outcome is settled; retry does not duplicate effects | Fake-sink unit cases, then isolated external-effect rehearsal |
| Stale, replayed or reordered acknowledgements | Wrong scope/generation cannot authorize destination execution | Unit contract cases |
| Crash at every transition boundary; lost reply | Durable retry preserves one operation, generation and task lineage | Unit recovery fixtures and isolated restart rehearsal |
| Controller expiry and cancellation | Visible recovery state; no silent reactivation or owner displacement | Unit contract cases |
| Concurrent manual/automatic work and reverse handback | One execution/effect authority; natural tasks resume once | Isolated process/fake-sink rehearsal |
| Other scopes/installations and read-only observers | Unaffected work progresses; observers gain no mutation authority | Unit isolation cases and bounded platform rehearsal |
| Native reviewer continuation versus explicit transfer | Preserve actual history; distinguish retained native identity from a recorded transfer | Isolated native-session rehearsal |
| Unsupported or missing adopter capability | Explicit unsupported/blocked result, never a successful handoff | Unit capability matrix |

Windows, Linux and macOS must either implement the same semantics or report a
specific unsupported capability before transition admission. Document any
platform exception with its validation and scope; do not infer portable process
identity from Linux-only observations.

## Implementation and adoption gates

The proposal must clear ordinary review first. Implementation then needs its own
explicit authorization and owner, focused tests, plugin changefiles and runtime
documentation. No wire schema, command spelling or storage migration is fixed
by this proposal; those are reviewed with the implementation.

Release, installation and resident adoption are separate gates. A source merge
or a newly advertised capability is not proof that every participating runtime
and adopter enforces it. Require compatibility checks and a separately
authorized scoped rehearsal before live use. Adopter-specific control, policy
and effect fences are implemented and reviewed in their owning repository.
