# Creator routing at cancelled-handoff closeout

[Canonical effort](README.md)

## Measured failure before correction

The reviewed plan #13597 merged as
`511a6c324287ba4fe3b099a17a25150310ad6515`.
Against unchanged production source, the focused terminal-release witness
uses the actual disposable Dispatch queue to abandon an unconsumed task:
abandoned, zero attempts, no owner. It then invokes the delivered release
adapter through the real Worktrees claim CLI and locked claim writer.

The same-project control passes. Two different-project controls fail:

- The coordinator's current project receives the release, leaving the
  originating creator's claim active and its ordinary obligation gate blocked.
- When another project has the same leaf worktree ID and matching claim ref,
  that other record changes instead of remaining protected.

This is two causal failures and one passing baseline, not a live incident.
The first fixture attempt failed on an inconsistent test project selector
before reaching this behavior; that setup failure is not product evidence.
The corrected fixture keeps the real mutation/finalization guards.

That initial witness was not the full Node save/trigger/manual-only abort
flow. At the decision point, that lifecycle and partial-retry acceptance
remained open and no production correction or live task, ledger, service or
installed runtime had been exercised. The effort's dated journal records
subsequent source-only qualification separately.

## Why the existing selector is insufficient

The release adapter receives a worktree ID but resolves its project through
the coordinator's own context. A matching leaf ID does not select the
originating creator when projects differ.

The saved handoff already carries optional `authorIdentity` observations,
including project and creation nonce. That interface explicitly makes these
observations evidence, not consume/head authority. Do not silently reinterpret
an optional observation as permission to settle a claim. Actual task-terminal
confirmation and the Worktrees owner's current claim/mutation checks remain
the authorities whichever routing alternative is selected.

## Alternatives

1. **Record the exact creator claim binding, recommendation.** At successful
   claim journaling, retain the owning project/worktree/record-generation
   binding in the existing handoff/task metadata, not a new registry.
   Closeout targets that exact binding through its owning operation, after
   actual task-owner confirmation. Missing, stale, contradictory or
   legacy-unbound evidence remains pending and needs an explicitly
   owner-qualified retry. This adds durable precision and avoids a whole
   ownership scan at every closeout; older ambiguous tasks do not
   automatically inherit the new proof.
2. **Resolve a unique current owner for every closeout.** Use the existing
   strict native ownership reader over the exact task ref, require complete
   and unique target-qualified evidence, and pin the selected record at
   mutation. This can support older tasks without new binding metadata, but
   does more discovery work and must retain every obligation when discovery
   is incomplete or multiple projects match. Current ownership and original
   creator responsibility must not be conflated across an accepted transfer.

Both alternatives preserve candidate/producer/handoff fences, truthful
partial outcomes, idempotent retry, independent plugins and all other custody
lanes. Neither permits caller-CWD guessing, forced release, expiry, a new
ledger, daemon or universal owner-acknowledgement protocol.

## Decision boundary

Choose the creator-routing and legacy behavior for this finite source lane.
The selection still needs the actual complete lifecycle witness, negative/
replacement/partial-retry controls, exact-source qualification, normal review
and required CI. No option authorizes a live abort, settlement, finalization,
provider/resource operation, installation, cleanup or rollout.

## Accepted decision

The operator selected `recorded-creator-binding`, alternative 1. Retain the
exact creator binding in existing handoff metadata; older tasks without
sufficient proof remain protected until an explicitly owner-qualified retry.
The unique-current-owner-discovery alternative is not selected.

Actual task-owner confirmation and the receiving Worktrees owner's current
claim/mutation checks remain authoritative. The binding selects the exact
claim; it is not permission by itself. This decision changes no live task,
resource, service, installed runtime, cleanup or rollout authority.
