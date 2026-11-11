# Native adapters and interrupted-flow recovery

The [dispatcher runbook](../SKILL.md) composes native owners, not their private
databases. Qualify installed capabilities before effects. The offline helper
never invokes an adapter and its copied observations do not authenticate one.

## Adapter qualification

| Concern | Existing owner and required evidence |
|---------|--------------------------------------|
| State home and writer | Harness/knowledge binding and native worktree/effort custody; one writable task workspace, plus an accepted dispatcher owner before effects |
| Machine selection | Machine/related-repository registry and venue owner; canonical machine IDs, purpose, permitted work, resource admission and supported transport |
| Discovery | Worktrees' bounded status/current-head readers, plus the selected venue's inventory; preserve observation times, coverage gaps and existing obligations |
| Allocation | Worktrees' native keyed creation; persist one logical creation key and retain remote request IDs, exact destination and returned writer/worktree association |
| Embodiment | Bridge's supported creation into that existing worktree, including the native writer evidence required by the selected receiving path |
| Follow-up | Current-head resolution plus Bridge's qualified exact-head/idempotent send and receipt-only read where supported |
| Task lifecycle | Optional native Dispatch standing declarations, original attachments, accepted materializations and holds; preserve native claims and task identity rather than a second writable lifecycle map |
| Supervision and exit | Existing native events/watches and lifecycle/claim owners, within the charter's observation budget and original effect limits |

Use `agent-worktrees`' and `agent-bridge`'s exact session command catalogs and
their owning skills. Do not infer agent-hosting permission from a repository's
presence or a working SSH connection. A dispatcher may prepare source or reports
without permission to launch agents in that repository.

Current advertised Worktrees contracts include `create --creation-key` for a
logical allocation identity, `--creation-request-id` for recovery of an existing
remote allocation, and `--bridge` for an agent-owned worktree without launching
it. `head-session` and `worktree-status-bundle` expose native current-owner/status
reads. Use canonical full IDs and the selected repository/machine context, not a
short suffix resolved in the wrong project.

Current Bridge contracts include existing-worktree creation with `--worktree-id`,
keyed creation and retained `--worktree-writer-file` evidence on paths that require
it. Obtain that writer evidence through the native Worktrees/Bridge procedure;
never manufacture the JSON, drop the requirement or choose an untracked anchor
instead. The receiving path may require its own minimum protocol and repository
agent-hosting checks. Use the installed owner's complete recipe for that path.

For follow-ups, the current live path exposes a stable `--idempotency-key` and
`--expected-session-id` admission check. The ordinary native path has its own
`--expected-native-session-id`, keyed invocation and `--receipt-only` contracts.
These are different modes: select the one the resolved target actually supports,
not a bag of flags to combine. A worktree handle resolves to its current live
session; recheck it before sending, and retain exact native receipts afterward.
Never use `--force` or an interrupt to repair a delivery uncertainty.

These names are compatibility landmarks, not a frozen CLI recipe. Read current
installed help and owner guidance. If exact keyed recovery, current-head binding
or required receiving support is unavailable, record the missing capability and
use an explicit owner handoff; do not build a replacement allocator or transport.
Optional safeguards are not universal new grants, and this document does not
authorize installing or upgrading a missing adapter.

## Native Dispatch observations

The optional `observations.dispatch` array contains saved reads, not commands.
Each entry has `observedAt`, `sourceRef`, a native standing-charter `task` and
its original `relationship` (or `null` when not observed). Preserve native field
names and the original request's task, event, generation and workspace identity.
Project a relationship receipt to **only** `{"ref": "<native-reference>"}` or
`null`; never copy receipt tokens into topology. Native authorities retain the
full record. An assignment's `nativeTask` selects its observation; both links and
observations must have unique task IDs.

Coverage joins the task's active lifecycle, started state, unheld status, bound
relationship, accepted materialization and a fresh current native head. Match
request/event IDs, original assignment generation and relationship reference.
Acceptance must match the current task generation and precede its standing
revision. First acceptance retains its expected generation; succession advances
it by one with a distinct `head.predecessor_session_id`. The relationship's
assignment generation stays unchanged across succession. The observed head needs
both `head` and `headRevision`, matching acceptance and the task's owner session.
Native owner/workspace, role/scope, policy and pending effects must also agree.

The declaration and acceptance must match the current materialization's source
and effective revisions. A declaration content update does not increment its
enrollment generation or imply acceptance; retain the old accepted record until
the native owner accepts the update. Native reconcile may advance the standing
revision after acceptance, so coverage does not require the acceptance to be the
immediately preceding mutation.

These are advisory joins, not native admission or authenticated proof. Missing,
stale, contradictory or future-dated evidence cannot qualify coverage. Reservations,
held/unknown owners and retiring attachments retain their workspace for
reconciliation; do not treat them as free slots or replace them. A closed detached
relationship does not resurrect its former owner. Unmapped native tasks hold
placement until reconciled. The overview derives status counts and owner-retaining
counts, while routes still require a fresh native-head recheck before effects.

No dispatcher appointment is required to inspect this evidence or export a
materialization. A null dispatcher or missing dispatcher custody holds placement
and routes; it does not erase valid observed coverage. Without the optional array,
legacy assignments continue using their existing acceptance evidence.

## Owner progress evidence

An owner observation may carry an optional saved `progress` read: `observedAt`,
`sourceRef`, `state` (`advancing`, `waiting`, `blocked`, `complete` or `unknown`),
`workRef`, `lastAdvancedAt` and `blockingOwner`. It comes from the adopter's
existing native readers or an assigned `monitor-agent-fleet` analysis, never a
new poller. `lastAdvancedAt` cannot follow its own `observedAt`.

Each qualified and unqualified coverage entry reports `liveness` (the owner
observation's state, freshness, time and source, or `unobserved`) beside
`progress`, and each role reports `progressCounts`. Missing, stale or
future-dated progress is `unknown` with an explicit reason, retaining any
last-known work and blocking-owner references for reconciliation.
`sinceAdvancedSeconds` is derived only from fresh evidence.

Presence, acceptance and progress stay separate. Advancing progress does not
qualify a reserved, held or unreachable lane, and blocked or stalled progress does
not unqualify an accepted one or authorize replacement. Use these fields to
report material progress and the blocking owner, then observe or escalate
through the rightful owner.

## Desired, proposed and adopted intent

`intent` reports one lane per standing assignment with three separate records:
`desired` (the checked-in assignment and its current materialization's source
and effective revisions, or `null` revisions when terminal), `proposed` (the
saved native standing-charter declaration, lifecycle, status and freshness) and
`adopted` (the native accepted materialization and its accepting head). It never
copies the relationship or its receipt and writes no ledger.

`proposedMatchesDesired`, `adoptedMatchesProposed` and `adoptedMatchesDesired`
compare revisions only. Each is `null` when either side is missing, stale,
future-dated, incomplete or desired-terminal; a `null` comparison is unknown, not
a mismatch. `state` is `aligned`, `adoption-pending` (not adopted, or adopted
content differs from the proposal), `proposal-pending` (the declaration differs
from desired intent), `proposal-inactive` (the native lane is not active),
`unknown`, `unlinked` or `desired-terminal`, with explicit reasons.
`proposedWithoutDesired` lists native standing charters no assignment links.

An adopted record is not current-head, presence or progress proof, and
`aligned` does not qualify coverage: `overview` remains the coverage authority.
No state grants effect authority. Resolve a pending proposal or adoption through
the native declaration owner and its accepting head, never by editing saved
observations, and recheck current native evidence before any effect.

## Native assignment flow

`flow <private-topology-json> --request <request-id>` reads the saved standing
assignment and native observations to recommend one next step. Its `phase`,
`holds` and `nextStep` distinguish preparation, native admission and acceptance.
`nextStep` names the owner and operation; it is not an executable command or
authorization. Obtain current commands and recheck custody, authority, resource
admission and the returned fences through that native owner before acting.

The sequence preserves these boundaries:

1. Persist the complete assignment before allocation. Export and durably save
   its materialization before declaring or reconciling the native standing task.
   A missing assignment reports `persist-assignment`; a missing native link
   reports the declaration step, not permission to create a worker.
2. Persist an allocation effect and stable creation key before keyed Worktrees
   allocation. Save the returned workspace, then reserve the standing charter
   through Dispatch with `expectedRevision` and exact workspace evidence. A
   confirmed allocation maps only its exact retained workspace before a
   Dispatch relationship exists; unrelated or unconfirmed owners still hold
   placement.
3. After the native relationship is bound, persist the launch effect and the
   full `seed` in the native request envelope. Retain `assignmentGeneration`
   from the reservation and the expected `charterRevision`; a role label or
   regenerated prompt does not substitute for the saved seed.
4. Start only the retained workspace through Bridge. The proposal carries
   `expectedRevision` and `expectedGeneration`; the saved effect must match the
   current reservation generation and charter revision before this step.
5. Once delivery is admitted or consumed and a fresh current head is observed,
   accept through Dispatch using its revision, generation, original workspace,
   `ownerSessionId` and positive `expectedHeadRevision` fences. Launch success
   is not acceptance. A joined native accepted materialization permits `settle-launch`
   to record that acceptance for the matching effect; `accepted` then has no
   further step.

`assignmentGeneration` is an optional nonnegative integer (or `null`) on saved
effects for backward-compatible document reads, but this flow requires a
positive integer on a launch effect matching the positive native task generation.
Native standing revisions must also be positive before the flow proposes a
fenced operation. The assignment generation identifies the original native
reservation, not the document or charter revision. Replacing a reservation
invalidates the old launch generation;
never rewrite that old effect to make it current. Succession retains the
original assignment generation in accepted evidence rather than relaunching.

Saving a prepared allocation or launch can advance the topology document revision
without changing the effective charter. For a single prepared allocation on an
unowned queued/proposed task, or a single prepared/admitted/consumed launch on a
claimed task, `flow` may first propose a source-only `reconcile-standing-charter`.
It verifies the effective revision and reproduces the old source digest by varying
only `topologyRevision` over at most the preceding 256 revisions. Changed source
identity, effective content or unrecognized/older digests remain held; matching
effective content alone is not enough. This bounded offline check is not native
authority. Persist the materialization and use the native reconciliation owner,
retaining the effect, envelope, creation key, reservation and generation unchanged.
Re-read native evidence to obtain the new standing revision before proceeding.
Already-started tasks retain their owner-reconciliation hold.

A single unknown effect proposes receipt recovery with `replayAllowed: false`
and the original effect record. Multiple unknown effects, unsettled conflicting
operations, stale native evidence, incompatible revisions or ambiguous owners
hold the flow. Reconcile through the existing owner instead of allocating a
replacement or regenerating a launch. The helper reads neither envelope content
nor receipt authority: durable full-seed preservation must be established by the
writer and native consumer, not inferred from a valid reference or this report.

## Follow-up relay and temporary work

`relay --request <request-id> --followup <followup-id>` sequences a status
inquiry or adjustment for an existing assignment; it never creates another
worker or performs an effect. Persist the complete follow-up and, for an
adjustment, the full revised charter before using the report:

1. Reconcile a changed native standing charter through its existing owner,
   retaining the worktree and original assignment generation. Re-read native
   evidence afterward; an older accepted revision is not acceptance of the
   adjustment.
2. Save an immutable native request envelope and its linked relay effect before
   sending. Preserve the exact prompt, mode, sender/caller, destination and
   revision fences, not just a reference that could later resolve to new bytes.
3. Use the supported exact-head send contract. The proposal requires
   `envelopeHeadMatchRequired: true` and `retargetAllowed: false`: the native
   consumer must compare the saved envelope's destination with the fenced head.
   The offline helper cannot read or authenticate that content. Missing,
   mismatched or uncertain envelope evidence is a hold, not permission to
   regenerate the prompt or retarget the original effect after succession.
4. Admission or consumption waits for the inquiry response or, for an
   adjustment, positive joined acceptance of the current charter. An absent
   rejection reason is not acceptance. Validate owner evidence and conflicting
   effects before settlement, even when the expected charter is already accepted.
   Unknown outcomes require recovery with the original envelope and identity,
   never another send.

Saving the follow-up/effect can advance the document revision without changing
the charter. For a retained started lane, `relay` tolerates that native
declaration difference without proposing another reconciliation only when it
proves the prior source digest by varying only the bounded document revision
and all other owner/effect fences still hold. Retain the envelope, effect and
generation unchanged. This relay bookkeeping exception does not relax `flow`'s
reconciliation hold for an already-started launch.

Unrelated work gets a separate `temporary` proposal with its own complete
charter, authority limits, native owner and exit policy. The dispatcher worktree
cannot own that worker assignment. Temporary work remains visible in overview
but never contributes to a standing role's minimum coverage. Completion is not
release: retain the assignment until effects settle, obligations are resolved
and native retirement is confirmed. Never retire or replace a standing owner
to make room without the original owner's authority.

## State and effect protocol

Persist intent under the existing native single writer, using the adopter's
normal versioned save/publication flow. Do not perform an effect until its
assignment and pending operation are durably saved. Increment document revision
on intent changes; do not launder old observations by refreshing their timestamp.
Store the original native source and time of each observation.

Assignments are `proposed`, `active`, `completed` or `released`.
`active` alone is not qualified coverage; `completed` alone is not release.
An adopted existing assignment may have no allocation/launch effects in this map:
its native owner and explicit adoption evidence still need reconciliation.

Lifecycle effects (`allocate`, `retire`) use `prepared`, `unknown`, `confirmed`
or `failed`. Message effects (`launch`, `relay`) use `prepared`, `unknown`,
`admitted`, `consumed`, `accepted` or `failed`. Admission and consumption remain
unsettled for this assignment's acceptance purposes. A `failed` outcome must be
known, not inferred from a timeout. Preserve the native reference and original
operation ID; a retry policy comes from the original grant and native owner, not
from an offline status label.

Every launch or relay effect requires `nativeRequest`: its selected native
`mode` and `envelopeRef` to a durable, immutable/version-addressed private request
saved **before** sending. Preserve the exact original destination and session
identities (Bridge and native IDs where applicable), key, caller/sender, complete
prompt with unchanged text/bytes, writer/creation evidence and mode-specific
options such as no-resume. Use the native owner's envelope format rather than
inventing a second transport protocol. Do not retain credentials in this record.
Lifecycle effects may use the same reference or `null`; their native creation
and retirement recovery requirements still apply.

The helper validates only this reference's shape; it never fetches it or proves
immutability, access, authority or receipt-reader compatibility. The dispatcher
must confirm the original content is durably recoverable before effects.
Missing or uncertain content blocks the affected action. Preserve the reference
and selected mode after succession, charter revisions and observation refreshes.
Receipt-only recovery uses the original session, sender/caller, key and exact
prompt, not the current worktree alias or a regenerated seed. A new authorized
follow-up gets a new envelope after unresolved prior effects are reconciled.

Persist a follow-up's complete question or adjustment and link it to its own
relay effect. Preserve the full revised charter and prior revisions. Native
acceptance evidence qualifies a revision; a changed file, delivery receipt,
healthy process or idle worker does not.

`released` requires settled effects and confirmed native retirement in this
contract. It never authorizes retirement. A native refusal caused by open PRs,
claims, a live successor or incomplete objective remains unresolved; do not
hand-delete directories or clear a claim.

## Recovery cases

| Observation | Required next action |
|-------------|----------------------|
| Empty assignment map, existing native owners | Propose adoption/reconciliation in place; no assumption of an empty fleet |
| Competing or unknown dispatcher custody | Preserve both references and stop conflicting effects; resolve through the existing owner, not a topology-file lock |
| Saved proposal, no allocation attempted | Recheck authority and native admission before the first keyed allocation |
| Allocation pending or response lost | Retain creation key, destination and remote request ID; use native lookup/recovery, never a new key or machine |
| Worktree allocated, launch not attempted | Persist launch identity and complete charter; embody only that returned worktree through the supported owner |
| Launch/send outcome unknown | Read the saved original request envelope and inspect its exact native receipt; no resend, alternate target, replacement or new session |
| Head succeeded inside the same worktree | Preserve old request envelopes for receipt-only recovery; after unsettled effects are reconciled, resolve the new native head and admit a new authorized follow-up with a new envelope |
| Saved relay envelope targets a different head | Hold admission; reconcile the original effect before preparing a separately authorized follow-up |
| Adjustment delivered, old charter still accepted | Retain the owner and pending relay; await joined acceptance of the current revision, not another delivery |
| Expected charter differs from accepted revision | Retain the owner; relay or inspect the already-pending update, and report coverage as unqualified until accepted |
| Unreachable, stale, dormant or unaccepted owner | Retain an explicit unknown/unqualified entry; a shortfall is not automatic replacement authority |
| New unrelated request | Separate temporary assignment and native worker; preserve standing-role coverage and custody |
| Native task/worker complete, obligations still open | Follow native completion/cleanup gates; do not mark released |

Restart first restores the document, native dispatcher custody, original grants,
pending identities and outstanding watches/claims. Reconcile only the missing
phase; a restart is not permission to replay the sequence. A leadership transfer
requires acceptance through existing owners and carries the whole objective and
unresolved effects, not just a copied JSON document.

## Observation and reporting limits

Coverage requires matching role ID/meaning, exact scope and accepted charter
revision on a fresh current native owner. The runbook requires semantic changes
to bump that charter revision. Count an owner once per role/scope lane, not once
per alias. Freshness uses each observation's own timezone-aware timestamp;
stale, missing and future-dated evidence are unknown.

The helper's placement model supports declared slot capacity and observed used
slots. Additional constraints (memory, locality, provider quotas or other resource
dimensions) remain blocked for native admission, not interpreted as free capacity.
Pending allocation effects also exclude the affected machine until reconciled.
Candidate order is only a load-balancing suggestion, not a reservation or effect.
Concurrent callers must still use the resource owner's native admission.

An overview includes role shortfalls, unqualified owners, pending effects and
discovery/custody holds. It is not a second dashboard database. Store decisions
with their canonical effort/authority owner; regenerate the view from the map
and dated native evidence.
