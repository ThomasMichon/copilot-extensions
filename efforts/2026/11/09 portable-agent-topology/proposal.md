# Portable topology contract proposal

[Back to the effort](README.md).

## Shape and ownership

Extend the payload-only `agent-roles` plugin with `managing-agent-topology`.
The skill is the dispatcher runbook, not a runtime service or a hidden worker.
A JSON Schema, fillable charter, synthetic document and small offline Python
helper make its document contract usable outside a particular harness.

The helper validates and derives bounded reports. It does not import sibling
internals, install tools, read credentials, run commands, contact machines,
allocate worktrees, launch agents or write lifecycle state. Native commands are
selected from the installed session catalog and their owning skills at execution
time. Missing support blocks the affected action without speculative commands.

The adopter chooses one private writable state home and one native dispatcher
owner. Existing worktree/effort custody serializes the writer; the document
neither claims that custody nor implements a distributed lock. A competing or
unknown dispatcher owner blocks effects. No public private-fleet inventory and
no second queue or claim ledger are introduced.

## Document

One versioned topology document holds four related kinds of data:

1. **Dispatcher:** stable topology identity, native dispatcher worktree
   reference, original authority reference, scope, supervision limits and
   state-document revision.
2. **Machines and roles:** registry references, purpose, allowed role/assignment
   classes, placement constraints, stated capacity, role meaning and minimum
   count/scope. Inventory and actual resource reservations stay with their owners.
3. **Assignments:** stable assignment/request identity, standing or temporary
   kind, selected role/machine, complete charter and revision, native worktree
   association, optional native task reference, stable creation identity and
   pending-effect references.
4. **Observations:** explicitly dated, bounded native-owner evidence for
   readiness, current head, charter acceptance, load and progress. These are
   replaceable observations, never claims or ownership authority.

A complete charter carries objective, scope, acceptance, permitted actions,
exclusions, resource limits, supervision/exit policy and the original authority
reference. An immutable source/revision reference may accompany the full
assignment charter but cannot substitute an unresolvable pointer for its content.
An assignment's expected charter revision and an owner's accepted revision are
distinct. Validation does not turn either into operator approval.

Unknown schema versions, duplicate IDs, dangling references and inconsistent
effect/native-owner associations fail validation. Unknown liveness is a valid
observation, not a parser error or proof of absence. Schema and helper validation
must agree on structural restrictions; semantic checks add cross-reference and
coverage guarantees. Examples use invented machine and repository identities.

## Dispatcher flow

1. Resolve the real state home, native dispatcher custody, original grant,
   installed capabilities, machine inventory and desired minimum role coverage.
   A setup request alone does not grant live launch or a permanent schedule.
2. Discover existing assignments and native owners. Classify live, idle, waiting,
   dormant and unreachable evidence; adopt in place only with applicable custody
   agreement. An empty map does not prove that the fleet is empty.
3. For an accepted request, find its existing assignment first. Select a role,
   scope and machine by allowed purpose, capability, locality and capacity; only
   then balance eligible load. A minimum deficit is a proposal, not permission
   to exceed bounds or displace another role.
4. Persist the full assignment and selected placement before native allocation.
   Use one stable native creation identity for that assignment. Record the
   returned native worktree before Bridge embodiment and the pending launch
   identity before sending its complete charter.
5. On uncertain allocation/launch, preserve the original identities and resolve
   through the existing owner. Do not retry under a new ID, pick a different
   machine, replace a worker or resend an unknown effect.
6. On adjustments or inquiries, resolve the assigned worktree's current head and
   relay there. Persist the update or question and its bounded delivery state;
   distinguish send admission, consumption, charter acceptance and goal progress.
7. Give an unrelated request a separate temporary assignment and native worker.
   Do not repurpose a standing reviewer/monitor or do its implementation in the
   dispatcher. Coupled follow-ups stay with the original owner.
8. Observe through existing event/watch mechanisms or an explicitly authorized,
   bounded cadence. Respect human-owned decisions and unchanged external waits.
   Completion and cleanup use native owner gates; an idle session is not done.

## Placement and coverage

The offline report is advisory. It lists eligible machines and explains
disqualification; any ordering uses declared capacity and bounded observed load,
not inferred machine capabilities. Unsupported resource dimensions or stale load
are explicit unknowns. The runtime/resource owner must still admit the operation.

Coverage matches role meaning, scope and accepted charter revision to a current
native owner. Count each owner at most once in a role/scope lane; duplicate
assignment aliases cannot satisfy a minimum. Unknown, stale, unreachable,
unaccepted or mismatched owners do not count as qualified coverage, but remain
visible and block automatic replacement until reconciled. The overview includes
qualified coverage, deficits, uncertain owners, pending effects and decisions;
it never reports a numeric deficit as an instruction to launch that many agents.

## Compatibility, validation and rollout

No existing CLI/service signature, database, startup hook or runtime changes.
The new document is explicitly versioned and opt-in. Unsupported versions fail
closed; no implicit adoption or migration of private documents.

Tests invoke the real offline helper and exercise the state transitions above
with synthetic native receipts and observations. Native references remain
opaque, owner-qualified values. Structural assertions alone do not qualify a
live launch, cross-machine delivery or installed recurring supervision.

Review the exact installed native contracts before documenting adapter calls.
If a needed guarantee is unavailable, expose a blocker or explicit owner
handoff; do not build a parallel allocation, claim or delivery engine.

The source delivery ships the role, contract and bounded helper. Release
promotion, installation and live adoption are separate states. This effort's
original source-only limits prohibit fleet activation; record that disposition
explicitly rather than silently claiming the future topology is running.
