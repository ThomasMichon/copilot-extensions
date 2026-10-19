# Remote ACP Host Ownership

- **Slug:** `remote-acp-host-ownership`
- **Repo:** copilot-extensions
- **Branch(es):** Serial proposal and implementation PRs targeting `dev`
- **Created:** 2026-10-19
- **Status:** Draft
- **Vision:** [externally-hosted-acp-agents](../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents)
- **Umbrella issue:** #7237

## Guiding Intent

A managed worktree that opens an externally hosted ACP conversation owns the
conversation relationship as a durable responsibility. The bridge owns its
connection and conversation records, not the peer's process, workspace or
service. Host succession must preserve the obligation without allocating a
development workspace merely to obtain tracking.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| welded-piston | Proposal, integrated implementation, validation and publication | Public issue #7237 and its claimed managed worktree |

## Coordination

- **Topology:** Reviewed proposal first, then dependent implementation PRs.
- **Host (owns PRs):** welded-piston.
- **Delegates:** None currently; any bounded delegation retains one integration owner.
- **Handoff:** Update this contract and transfer the public claim explicitly.
- #7218/#7231 retain discovery, binding, authentication and remote transport
  scope. Implementation builds on that reviewed contribution, not a parallel
  connector. #7238 retains the separate conversation-domain graduation.
- Preserve #6076 dispatch acquisition, #3443 orphan adoption and #6934
  authoritative head-resume ownership. Do not replace their state machines.

## Context

The normal bridge CLI/client carries `caller_owner_ref`. The remote session
route does not forward it to the remote attachment, and that attachment does
not retain it on its `SpawnTarget`. Caller affinity and remote history identity
therefore do not establish a host-worktree obligation.

The worktree layer already owns qualified references, `bridge` resource
claims, active/at-rest/released dispositions, finalization gates and accepted
claim handoffs. The bridge owns remote session persistence and lifecycle
serialization. Compose these owners through their supported command/provider
boundaries; do not write another plugin's private store.

The existing `resource-producer` ABI explicitly describes disposable-owned
allocation and verified absence. A retained external conversation is not
ownership of a disposable peer service. Do not adopt that ABI merely because
its reserve/bind verbs look convenient; the implementation must establish
compatible relationship semantics without weakening existing producers.

## Request

**Public-safe summary of the operator request:** Allow agents to open and
interact with external ACP sessions that become owned and bound to the
originating host worktree for tracking. Preserve that relationship through
reconnect and host-session succession.

The operator confirmed the slug `remote-acp-host-ownership`. Concrete consumer
systems and private deployment details are omitted without changing the
ownership requirement. The lifecycle design and validation decomposition below
are **agent-recommended**.

**Execution constraint:** Review and merge this proposal before implementation.
Neither this plan nor a `dev` merge authorizes promotion, installed-runtime
updates, daemon restarts, credential grants or live external requests.

## Plan

### Phase 1 - Reviewed relationship contract

- [x] Deduplicate and claim #7237; confirm the effort slug and native adoption.
- [ ] Review and merge the effort and the focused vision clarification.
- [ ] Sync onto the reviewed plan and merged target-contribution prerequisite.

### Phase 2 - Durable owner and claim integration

- [ ] Carry the qualified host owner through ordinary CLI/client/API creation
  into the existing remote session record, separately from caller-session
  attribution, the bridge handle and target-qualified remote identity.
- [ ] Integrate an idempotent relationship claim with existing claim authority
  and inspection; reject unresolved, frozen or conflicting managed owners
  before remote creation. Never infer ownership from a bare caller string.
- [ ] Journal pending/confirmed/uncertain acquisition through the bridge's
  existing persistence and reconcile claim outcomes before reporting success.
  Define compensating failure and cross-owner transfer behavior explicitly.
- [ ] Preserve standalone programmatic use without a worktree: absent managed
  ownership is reported as unowned. Supplied but invalid ownership is a refusal,
  not an unowned fallback.

### Phase 3 - Lifecycle and continuity

- [ ] Preserve the claim through stop, transport loss, reconnect, daemon
  rehydration and same-worktree session succession. Refuse implicit takeover
  by another worktree; use accepted handoff for explicit ownership transfer.
- [ ] Make explicit end settle the bridge relationship only after durable
  local retirement; retain remote history and never terminate the peer service.
  Interrupted or unknown settlement remains visible and retryable.
- [ ] Reconcile failed creation, response loss, concurrent reuse/load/stop/end,
  startup cancellation and stale owners without duplicate creation or silent
  obligation loss.

### Phase 4 - Reviewed delivery and closure

- [ ] Document relationship inspection, finalization blocking, explicit end,
  ownership transfer, standalone policy and older-daemon capability refusal;
  update affected plugin changefiles and contract evidence.
- [ ] Pass the validation matrix and review/CI gates; merge implementation to
  `dev`, then journal exact evidence and archive the resolved effort.
- [ ] Transfer release/rollout or live acceptance to named tracked objectives
  where separately authorized; do not equate a merge with deployment.

## Validation Plan

All detail below is **agent-recommended**.

- [ ] Unit tests prove distinct owner/caller/bridge/target-native identities,
  qualified owner validation, project/binding identity and old-record behavior.
- [ ] Actual ordinary CLI/client/API consumer tests show the relationship in
  session inspection and `claims show`/reverse-owner inspection; unresolved
  active conversations block host finalization.
- [ ] Real command-boundary tests cover successful and refused claim writes,
  already-applied/lost claim responses, pending acquisition recovery, duplicate
  requests and host freezing concurrently with creation.
- [ ] Deterministic races cover start/load/reuse/stop/end and cancellation.
  Known accepted remote IDs reconcile without creating another conversation;
  an unidentifiable remote outcome remains explicitly uncertain without replay.
- [ ] Persist/restart tests preserve owner and claim with the native ID.
  Same-worktree successors retain responsibility; different owners cannot
  steal it. Accepted transfer and terminal settlement retain an audit trail.
- [ ] A contained synthetic TLS peer exercises create/send/stop/reconnect/end
  with temporary worktree authority, normal claim commands and a fresh bridge
  database; assert zero workspace allocation, process launch or peer termination.
- [ ] Standalone bridge tests retain explicit unowned operation; a managed
  request refuses when required claim authority or daemon capability is absent.
- [ ] Existing remote transport/auth/session, worktree obligations/handoff and
  contract suites pass through canonical runners without widening thresholds.
- [ ] Record clean-room and live-venue tier availability with explicit skip
  reasons. No unauthorized endpoint, grant, deployment or restart is a test.
- [ ] Public artifacts contain generic fixtures and masked source attribution;
  documentation-impact review covers the final implementation diff.

## Proposal

Use one bridge-owned relationship record and the existing worktree-owned claim,
not a new session manager, remote workspace or ownership service.

The qualified worktree reference is the stable owner; a caller session is
provenance, not an ownership epoch. Validate the current host authority using
existing worktree commands. Keep target/binding identity and native session ID
separate so URL/name aliases cannot fork a claim and identical native IDs at
different targets cannot collide.

Persist a pending local relationship and obtain a confirmed host claim before
consequential remote creation. Serialize reconciliation with the existing
remote lifecycle lock. If the claim result is unknown, inspect/reconcile it
before proceeding. If the remote accepts creation but its reply is lost, retain
an uncertain active obligation; ACP does not promise an idempotent creation
key, so do not invent exactly-once creation or blindly retry it. When a native
ID is known, preserve and reuse it under the current binding policy.

Stop/disconnect retain responsibility. Explicit end retires the local bridge
relationship, not the remote service or history; finalize remains blocked
until settlement is confirmed. Remote reachability is not a prerequisite for
retiring an idle local relationship, but an uncertain in-flight outcome must
remain disclosed. An accepted worktree claim transfer must update the bridge's
owner relation or remain pending; neither side silently declares a handoff done.

Review the exact claim namespace, capability generation and reconciliation
protocol during Phase 2 against current owners. Any required change to the
generic disposable-producer contract is a separate reviewed design decision,
not an implicit dependency of this effort.

## Journal

### 2026-10-19 - Proposal

- Claimed #7237 and confirmed the effort slug. The native read-only adoption
  probe returned the exact version-1 compatible efforts capability.
- Traced the missing qualified-owner forwarding and existing claim/disposition
  authority. Distinguished external relationship retirement from disposable
  resource deletion and peer-service ownership.
- Prepared the plan and focused vision clarification before implementation.
  No runtime, deployment, grant or live external request is part of this delta.
