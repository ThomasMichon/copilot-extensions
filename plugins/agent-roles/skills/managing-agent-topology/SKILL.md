---
name: managing-agent-topology
description: >
  Act as a conversational topology dispatcher over a compatible harness and
  machine mesh: persist complete role assignments, select eligible machines,
  allocate through native worktree owners, launch through Bridge when authorized,
  retain assignment-to-worktree mappings, relay adjustments and status inquiries
  to the current owner, and assess load and minimum semantic role coverage.
  Use for "manage agent topology", "act as topology dispatcher", "set up these
  agent roles", "which agent owns this request", "balance these role assignments",
  or "ensure minimum role coverage". Not a worker, scheduler, task queue or
  permission to activate a fleet.
---

# Managing agent topology

The dispatcher is the operator's conversational front door, not an implementation
worker. Keep a durable map of accepted requests, complete charters and native
worktree owners. Delegate the work, relay follow-ups to the same owner, and report
evidence rather than confusing presence with useful role coverage.

This is an opt-in role over existing capabilities. Enabling this plugin creates
no dispatcher, machine, worker, background watch or recurring schedule.

## Establish one owner and document

1. Resolve the adopter's private writable state home and, if appointed, the native
   dispatcher worktree. Without an appointment, set `dispatcher` and
   `observations.dispatcher` to `null`: read-only validation, materialization and
   reporting remain available, but placement and routing report a hold. Respect
   the repository flow and existing effort/task custody; never
   put a private fleet inventory in a public plugin or use a knowledge anchor as
   a writable worktree.
2. Fill the [dispatcher charter](assets/CHARTER.md) from the original operator
   assignment. Preserve its authority reference, permitted effects, destinations,
   limits, exclusions, accepted revision and stop condition. Clarify only missing
   scope that actually changes an effect; ordinary authorized continuation does
   not require blanket reapproval.
3. Use the [version-1 schema](assets/topology.schema.json) and
   [synthetic example](assets/topology.example.json) to establish one private JSON
   document. Its revision identifies intent, not custody or permission. Keep the
   document under the adopter's existing single native writer; a second or unknown
   dispatcher owner blocks effects. Do not invent a lock file, leader election,
   global assignment database or parallel task queue.
4. Qualify the [native adapters](references/adapters-and-recovery.md). Use exact
   installed command catalogs and owning skills, not remembered PATH commands.
   Missing tooling blocks the affected action; it does not authorize installation.

The JSON document holds dispatcher intent, desired machines/roles, assignments
and dated observations. Machine registry, native worktree, head-session,
resource reservation, task, messaging and approval authorities remain external.
An `authorityRef`, receipt, accepted charter or valid JSON does not grant effects.
When using native Dispatch standing charters, link assignments by `nativeTask`
and derive attachment, acceptance and lifecycle from native reads. Do not maintain
a second writable acceptance or worktree-ownership map in this document.
The dispatcher's own worktree cannot also own a worker assignment.

## Discover before assigning

Read both the saved map and native owners in the selected scope. Classify live,
idle, waiting, dormant, completed and unreachable evidence; record its source and
observation time. An empty map does not prove that the fleet is empty.

Propose adoption in place for existing matching owners. Preserve their original
worktrees, current work, obligations, charters and unresolved effects; obtain any
required owner agreement before changing their assignment. Unknown or conflicting
custody is a hold, never an empty slot. An installed workflow, queue or service
with its own owner stays with that owner.

Define roles by **meaning and scope**, not display labels or process counts.
Each desired role names its minimum qualified owners, required capabilities and
constraints. Resolve machine purpose and allowed role/assignment kinds from
declared topology; record unsupported resource dimensions instead of assuming
they are unlimited.

For tooling and machine upkeep, assign
[`maintain-agent-fleet`](../maintain-agent-fleet/SKILL.md) as a standing role
with explicit machine/environment scope and minimum qualified coverage.
Materialize its complete filled charter in the existing assignment contract;
no role-specific schema extension is needed. Require separate maintenance authority
for updates, cutover and recovery, distinct from monitor steering or dispatcher
launch authority. A live anchor chat is not required: the assigned worktree
retains the charter and current-head continuity.

## Handle an operator request

1. Find the request in the saved map before allocating anything. An inquiry or
   adjustment belongs to the existing assignment. A genuinely unrelated request
   gets a separate `temporary` assignment; do not repurpose a standing reviewer or
   monitor, or do the delegated implementation in the dispatcher.
2. Choose a role/scope and an eligible machine: purpose and allowed work,
   capabilities, locality/resource constraints, then current capacity. Balance
   load only among eligible machines. A minimum coverage shortfall is a proposal,
   not permission to increase concurrency, spend, access or deployment scope.
3. Persist an assignment with stable assignment ID, request ID, native creation
   key, selected machine and **the complete charter** before any allocation.
   Include objective, scope, acceptance, permitted actions, exclusions, resource
   limits, supervision, exit policy and original authority reference. A role name,
   detached pointer or launch prompt alone is not the assignment record.
4. Persist the pending allocation identity, then use the native Worktrees owner
   with that stable creation key. Retain its exact request/receipt and returned
   worktree association before any Bridge launch. Preserve native parent custody,
   paired-state and repository-hosting gates; do not make an untracked checkout.
5. Persist the launch effect and full seed before sending. Its `nativeRequest`
   records the selected mode and an immutable private envelope reference, as
   specified in the [effect protocol](references/adapters-and-recovery.md#state-and-effect-protocol).
   Use Bridge's supported existing-worktree embodiment with retained native
   writer/creation evidence.
   Do not substitute a new workspace or omit a required receipt. Resolve an
   uncertain outcome with its original owner and identities, not another launch.
6. Record admission, consumption, charter acceptance and goal progress separately.
   `active` means an intended active assignment; qualified coverage additionally
   needs a fresh current head and evidence that it accepted the expected charter.
   A successful launch receipt alone does not establish that. For a Dispatch-linked
   standing assignment, use its native accepted materialization and original
   relationship instead of copying acceptance into assignment fields.
   Every coverage entry also reports owner `liveness` and dated `progress`
   evidence (see [owner progress evidence](references/adapters-and-recovery.md#owner-progress-evidence));
   neither qualifies, counts nor routes a lane.

## Relay adjustments and inquiries

Resolve the assigned **worktree's current native head**, not a cached session ID.
Persist the question or revised complete charter and a stable follow-up/effect
identity before relaying. Save its `nativeRequest` envelope with the original
session identities, caller/sender, key, exact prompt and mode-specific options.
Use the owner's supported exact-head/idempotent send contract; if the head changes
during admission, reconcile rather than sending to both. Receipt-only recovery
uses the saved original envelope, never a new current-head lookup or reconstructed
prompt. Respect holds and pending effects.
The read-only `relay` report sequences these persistence, exact-head delivery and
acceptance boundaries; see [follow-up relay and temporary work](references/adapters-and-recovery.md#follow-up-relay-and-temporary-work).

Any semantic change to the role meaning, scope, expected work, restrictions or
acceptance increments the assignment charter revision. Keep the prior revision
and original authority evidence in the adopter's version history. Updating the
expected revision does not imply the worker accepted it. Route status inquiries
to the retained owner even when charter acceptance needs clarification; do not
count that owner as qualified until the mismatch is resolved.

An unrecognized request is not an alias for the busiest, nearest or most recent
worker. Ask one focused disambiguation question when relatedness is genuinely
unclear; otherwise create a separate temporary proposal.

## Supervise and reconcile

Use the adopter's already-admitted native event/watch mechanisms or an explicitly
authorized bounded cadence. Name the next observation event, resource budget and
stop condition. Do not add a per-worker poller, new scheduler or helper daemon.
Compose `monitor-agent-fleet` when the operator actually assigns deeper progress
analysis; this dispatcher need not read every transcript.

Report the root objective, expected versus accepted charter, fresh native state,
material progress, blocking owner and next observation. Keep human decisions,
external waits and agent-actionable failures distinct. Do not duplicate a steer
on unchanged evidence. Unknown effects retain their identity and target across
restart; observe or escalate once through the rightful owner, never replay them
blindly or move to another machine.

Recompute semantic coverage without replacing an uncertain owner. Count each
native owner at most once in a role/scope lane. Stale, unreachable, wrong-scope,
unaccepted or unresolved-effect assignments remain visible but unqualified.
Temporary assignments are reported separately and never satisfy a standing
role's minimum coverage, even when their work is accepted.
With Dispatch observations, report desired, reserved, accepted, held, unknown,
retiring and retired lanes separately. Owner-retaining lanes are not vacancies,
and only a joined, current accepted lane contributes qualified coverage.
The [recovery reference](references/adapters-and-recovery.md) explains adoption,
collisions, interrupted allocation, succession and retirement.
Use the read-only `reconcile` report to distinguish uncovered demand from
retained assignments needing owner coordination. An off-role, stale, dormant or
uncertain owner is not a replacement slot; preserve its original assignment and
effects while coordinating any accepted in-place reassignment.

Completion is the worker's actual acceptance gate and native lifecycle outcome,
not an idle session. Temporary workers finish and release through their native
owner after obligations settle. Standing roles follow their explicit exit policy.
Retain assignment/effect history; never delete a worktree or edit a claim database
to make the overview look clean.

## Offline helper

Resolve [the script](scripts/topology.py) from this skill payload. Use a supported
Python 3.10+ interpreter; it imports only its adjacent
[contract module](scripts/topology_contract.py),
[saved Dispatch observation adapter](scripts/topology_dispatch.py),
[admission report](scripts/topology_admission.py) and the
standard library. Dispatch is optional; the adapter never imports its runtime.
No install, service, network or credentials are required.

```text
python <resolved-script> validate <private-topology-json>
python <resolved-script> overview <private-topology-json>
python <resolved-script> reconcile <private-topology-json>
python <resolved-script> intent <private-topology-json>
python <resolved-script> flow <private-topology-json> --request <request-id>
python <resolved-script> placement <private-topology-json> --role <role-id> --kind standing
python <resolved-script> admission <private-topology-json> --work <private-saved-work-json>
python <resolved-script> route <private-topology-json> --request <request-id>
python <resolved-script> relay <private-topology-json> --request <request-id> --followup <followup-id>
python <resolved-script> seed <private-topology-json> --request <request-id>
python <resolved-script> materialize <private-topology-json> --request <request-id>
python <resolved-script> inspect-materialization <private-materialization-json>
```

The helper only reads and prints JSON. `validate` checks structure and references;
`overview` derives coverage, holds and pending effects; `intent` compares each
standing assignment's checked-in desired charter with the proposed native
declaration and its adopted materialization, without qualifying coverage;
`placement` ranks supported eligible capacity; `route` returns the retained
worktree and observed current head or a hold; `seed` exports the complete saved
assignment, role and machine context.
There is no allocate, send, reconcile-write or launch command.

`admission` ranks saved eligible queued and dormant tasks without claiming or
resuming them. It consumes only a local proposal budget, protecting Operator
tasks, their retained owners, interactive reserve and separate review capacity.
Read the [saved work contract](references/adapters-and-recovery.md#eligible-work-admission)
before preparing its version-1 input. No other command requires that input;
omitting it preserves the existing topology format and reports.

`reconcile` combines coverage and eligible placement into advisory lane
dispositions. It retains unresolved assignments and original effect identities,
proposes in-place adoption of unmapped owners, and reports new standing demand
only when no custody, discovery, retained-assignment or capacity hold blocks it.
Proposal counts describe demand, not reservations or simultaneous launch capacity.
Complete charters, stable identities and serialized native admission remain
required; see [vacancy and drift reconciliation](references/adapters-and-recovery.md#vacancy-and-drift-reconciliation).

`relay` reports the next advisory step for a saved follow-up: reconcile a changed
native charter when needed, persist the immutable request envelope and relay
effect, send to the retained worktree's exact head, then await the response or
joined current-revision acceptance before settlement. It never reads an envelope,
sends a message or treats delivery as acceptance.

`flow` reports the next advisory step for a standing request: persist its complete
assignment and materialization, allocate through Worktrees, reserve through
Dispatch, persist the full launch seed and envelope, start the retained worktree
through Bridge, then accept through Dispatch. It returns the owning adapter and
revision/generation/head fences, or an explicit hold. Unknown effects require
receipt recovery with the original identity, never replay. See the
[native assignment flow](references/adapters-and-recovery.md#native-assignment-flow)
for persistence and acceptance boundaries. The helper performs none of these
steps and cannot verify that a referenced envelope was saved.

`materialize` exports a nonterminal persisted assignment's complete charter, role,
machine constraints and dispatcher context in a versioned envelope. Save that
output only in the adopter's writable state home through its normal publication
flow. `inspect-materialization` validates the saved envelope, completeness and
content revisions; a floating template reference is not a materialization.
Neither command admits an attachment or records worker acceptance.

`materializationRevision` identifies the complete effective content.
`sourceRevision` also includes the topology ID/revision and assignment identity.
Observations and pending effects are excluded from both: a new native head does
not rewrite the charter. Source changes may leave effective content unchanged;
content changes are detected even if a human forgets to increment a revision.
These digests identify content, not authority or trusted provenance. Preserve
expected and accepted revisions separately; never infer acceptance from export,
inspection or an unchanged digest. Recheck native holds and custody at admission.

To opt into native coverage, include `observations.dispatch` with dated snapshots
of native standing tasks and their original relationships. See the
[observation contract](references/adapters-and-recovery.md#native-dispatch-observations)
for the exact join and receipt projection. Omitting that array preserves the
legacy assignment/owner acceptance path; supplying an empty array makes linked
standing assignments unknown, not accepted from old copied fields.

Inputs are limited to 2 MiB. Unsupported versions, duplicate keys/identities,
dangling references and inconsistent native/effect records return exit 2 with an
error. Successful reports return exit 0 **even when held**: consumers must inspect
`holds`, `status` and uncertainty. Unknown liveness is valid data, not a syntax
error. An optional `--at <ISO-time-with-timezone>` supports deterministic offline
analysis; the default is current UTC. Synthetic example observations are historical,
not current readiness.

The bundled schema uses a deliberately small, locally validated vocabulary, not
an arbitrary-schema service. Semantic validation adds reference/effect consistency.
Capacity is expressed in declared slots; opaque constraints require native
admission and are not silently evaluated. Every result is advisory. Recheck native
custody, head, resources and actual authority immediately before a real effect.

## Delivery and disposition

The durable output is the private topology document plus its canonical
charter/effort references and current evidence. Give the operator a concise
assignment/coverage overview, pending decisions and explicit effect disposition.
Source publication, release promotion, installed capability and live adoption are
different states. If assigned only to plan or implement this capability, finish
that deliverable and record **no live activation**; do not launch the example.
