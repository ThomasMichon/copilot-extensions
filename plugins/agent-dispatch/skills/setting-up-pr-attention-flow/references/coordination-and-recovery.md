# Coordination and recovery

Return to [the setup procedure](../SKILL.md).

## State without a second authority

The reviewable charter names scope, original grant, actors, policy and stop
criteria. Place mutable coordination state in the existing owner-selected
private/durable location outside candidate/source content, unless the repository
already owns a versioned coordination contract. Do not place operational state
in this skill's installed payload or copy it into a public example.

Session SQLite can index active lanes, actual task/reviewer bindings and pending
work; it is not the Dispatch queue. Pairing it with one durable versioned JSON
ledger is an optional conservative reference pattern, not a requirement to
build an engine. SQL is reconstructible and may disappear on new-owner handoff;
JSON survives only when its actual storage/transfer contract does.
Neither grants permission or supplies live forge truth.

Retain:

- Exact provider/account/repository/PR scope, head/base/range/signature,
  completeness and observation times.
- Naturally returned task, reviewer, workspace and lineage bindings; absent
  values are null. Keep opaque identifiers in their original type/bytes.
- Every prior round and **full exact original receipts**, including prepare,
  attempted post, native readback, partial effects and original unknowns.
  Never reconstruct a receipt by picking convenient fields. Owner-permitted
  immutable references may retain complete originals without duplicating them.
- Unresolved findings and author replies/edits/reopens, consent/holds with
  provenance, pending decisions and missing original inputs.
- Actual watcher/subscription IDs, returned cursors, unsettled sends/effects,
  resource obligations and independently observed terminal disposition.

Write state through its existing storage owner, using its supported
serialization/atomic persistence and version handling. Do not invent a new
ledger service, receipt issuer, scheduler or message database. A state-write
failure leaves the next effect unready; it does not license dropping history.
The synthetic asset illustrates these distinctions, not runtime wire fields.

## One PR, one retained reviewer

Allocate up to the charter's positive lane limit through the authorized existing
host/task owner. Unrelated PRs get independent fresh contexts. A PR update
reuses its retained context/actual task and naturally returned handles.
Suspension releases active execution capacity while preserving pending work;
refill promptly without replacing a suspended reviewer or creating overlap.

Treat a relevant event as a reason to refresh authoritative inputs. Source,
base, discussion, policy and check movement have different invalidations.
Reprepare through the write owner and inspect the justified delta where its
lineage/source evidence remains valid; uncertain range, invalid lineage,
escalation or uncovered changes require the full gate. A new head does not
reset the existing owner's failure budget. Compatible official review carry
requires the target's actual policy, not a blanket SHA rule or stale approval.

Reviewers read pinned source statically and never execute candidate code or
fixtures, author fixes, supply consent or merge. Route fixes to the original
author and integrations/decisions to the coordinator without transferring
their PR/worktree/task custody.

## Publication and actual outcomes

The existing assignment owner binds actual scope, author independence and the
retained reviewer handle before the first authorized write, not through a new
grant API. Within that mandate, the reviewer owns analysis, supported addressed
bot-thread reconciliation, direct task/owner-bound verdict posting and official
readback. A qualifying round does not stop at analysis-ready for coordinator
blessing, content co-signing or a release handshake; the coordinator neither
relays reviewer JSON nor rewrites/posts the verdict on its behalf. Return the
actual verified posted receipt or real blocker asynchronously, then let the
coordinator independently audit current forge authority after publication.

Genuine candidate changes stay with the same reviewer for owner-bound
reprepare/delta/full-gate analysis and direct posting. Hold/escalate only actual
required-input, mutation-fence, ownership, authority or resolution gaps; direct
publication never waives those gates, self-review checks or author consent.
A coordinator's full snapshot may supply known fields missing from an adapter's
read tools, but this is an adapter exception, not a mandatory per-round
middleman or a substitute for server mutation fences. Preserve original
operation IDs, complete receipts and unknown outcomes; a native Task completion
or readback does not transfer custody or authorize transcript/credential sharing.

The reviewer performs the following steps through the selected existing owners:

1. Acquire fresh complete paginated candidate, official reviews, threads/
   replies, applicable checks and decision inputs through the selected owner.
   Preserve unavailable/partial sections and their original uncertainty.
2. Prepare the actual current candidate/round through the existing protected
   review tool. Apply its original grant, task/identity and self-review gates.
3. Require supported **server write fences or atomic provider preconditions**
   at mutation time. A client snapshot, matching hash or preflight read is not
   an atomic read-then-write transaction. Missing fencing is a named adapter
   blocker or policy-permitted review-only/manual handoff, not a naked write.
4. Persist the mutation request/intent and prior original receipts through
   their existing owner before effects. Do not precreate a resulting post
   receipt that does not yet exist. Send only the selected allowed comments/
   replies/verdict. Tool-server refusal on movement causes reprepare, not
   editing a receipt to match the new head.
5. After the call, persist its resulting original response/receipt and verify
   official provider authority/readback:
   native review/comment IDs, actual actor, current or policy-valid retained
   candidate, non-dismissed verdict and unresolved findings. Numeric forge
   review IDs must be actual numeric IDs where that provider uses them.
   A comment saying "approved" and an exit-zero response are not a verdict.
6. Reconcile unknown outcomes through the same owner/original operation.
   Null IDs remain null; known IDs from partial replies remain known but do
   not confirm the entire effect. Do not drop old uncertainty, allocate a new
   identity or repost to make the ledger look complete.
7. Resume the same reviewer for genuine author/source updates or missing-input
   follow-ups. Keep prior rounds, replies and compatible carry decisions.
   Resolve findings under the repository rubric, not until a counter reads zero.

Coalesced existing watch subscriptions/events drive waits. Retain actual
returned cursors and acknowledge only the existing owner's event identity.
A timeout or disconnected carrier is not a new revision, failed review or
absent reviewer. Use bounded owner retries and known transient backoff;
reconcile uncertain effects first. No polling/relaunch loop or repetitive
status-message loop. A watcher unsupported by the selected owner is a named
limitation, not permission to start another daemon.

## Refusal controls

| Missing or ambiguous prerequisite | Required disposition |
|----------------------------------|----------------------|
| Original grant | Block the affected effect; obtain its authoritative reference, never infer it from a charter or message |
| Task binding required by selected tool | Retain null and name that adapter blocker; never fabricate a task/claim/session |
| Author consent required by actual merge policy | Wait for actual consent; never infer it from approval or supply it as reviewer |
| Current mutation fence | Block publication or use permitted review-only/manual handoff; fresh read is not a substitute |
| Old owner's effect/custody | Retain unknown and reconcile through the owner; silence, ACK or timeout cannot grant replacement authority |

## Checkpoint, transfer and terminal settlement

Native checkpoint/`new_context` is **same-owner** context continuation, not
process or custody handoff. Preserve the original charter/parent completion
gate, task bindings, live agents, watchers/cursors, claims and uncertain effects.
Use the actual host-supported checkpoint contract; tool availability is not
proof that background work survives a rollover. Observe its state afterward.

For **new-owner** handoff, use `context-handoff:context-handoff` and the existing
task/resource/transport owner. Name the previous and proposed owner, scope,
revision, remaining gates and live obligations. Require explicit accepted
custody transfer through the existing authority. Never infer transfer or
survival from presence, ACK, consumed receipt, idle state or transcript hash.
Quiesce or explicitly settle owned flows as that handoff contract requires;
leave unrelated flows untouched. Preserve unobserved state as unknown.

Review publication, consent, actual merge and cleanup are separate milestones.
Verify actual merged/closed state and settle only owned resources/effects.
Finite drain requires its complete final census and parent gate; continuous
empty census remains attending. A claimed task, merged slice or finalized
workspace alone does not complete the original objective.
