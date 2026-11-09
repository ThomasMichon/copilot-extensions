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

1. Resolve the adopter's private writable state home and the native dispatcher
   worktree. Respect its repository flow and existing effort/task custody; never
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
   A successful launch receipt alone does not establish that.

## Relay adjustments and inquiries

Resolve the assigned **worktree's current native head**, not a cached session ID.
Persist the question or revised complete charter and a stable follow-up/effect
identity before relaying. Save its `nativeRequest` envelope with the original
session identities, caller/sender, key, exact prompt and mode-specific options.
Use the owner's supported exact-head/idempotent send contract; if the head changes
during admission, reconcile rather than sending to both. Receipt-only recovery
uses the saved original envelope, never a new current-head lookup or reconstructed
prompt. Respect holds and pending effects.

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
The [recovery reference](references/adapters-and-recovery.md) explains adoption,
collisions, interrupted allocation, succession and retirement.

Completion is the worker's actual acceptance gate and native lifecycle outcome,
not an idle session. Temporary workers finish and release through their native
owner after obligations settle. Standing roles follow their explicit exit policy.
Retain assignment/effect history; never delete a worktree or edit a claim database
to make the overview look clean.

## Offline helper

Resolve [the script](scripts/topology.py) from this skill payload. Use a supported
Python 3.10+ interpreter; it imports only its adjacent
[contract module](scripts/topology_contract.py) and the standard library.
No install, service, network or credentials are required.

```text
python <resolved-script> validate <private-topology-json>
python <resolved-script> overview <private-topology-json>
python <resolved-script> placement <private-topology-json> --role <role-id> --kind standing
python <resolved-script> route <private-topology-json> --request <request-id>
python <resolved-script> seed <private-topology-json> --request <request-id>
```

The helper only reads and prints JSON. `validate` checks structure and references;
`overview` derives coverage, holds and pending effects; `placement` ranks supported
eligible capacity; `route` returns the retained worktree and observed current head
or a hold; `seed` exports the complete saved assignment, role and machine context.
There is no allocate, send, reconcile-write or launch command.

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
