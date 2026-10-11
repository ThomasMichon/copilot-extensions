# Phase 6: unified dispatch session acquisition

Back to the [canonical effort](README.md#phase-6--unified-dispatch-session-acquisition).
Tracking: [#6076](https://github.com/ThomasMichon/copilot-extensions/issues/6076).
Adjacent work: #4038 (durable CLI workers), #5696 (retained headless
conversation recovery), and PR #6064 (gated CLI-resume stopgap).

## Requested contract

agent-dispatch uses agent-bridge exclusively for acquiring and messaging agent
sessions. An explicit task flag selects CLI or ACP; the bridge handles a new
session versus an existing conversation automatically. CLI remains opt-in,
not an inferred consequence of task age, a worktree's existence, or cleanup
labels.

This is session-boundary consolidation, not transfer of worktree ownership.
agent-worktrees remains responsible for allocation and its session ledger.
CLI/mux, ACP session hosts, and venue providers remain the execution and
transport mechanisms behind bridge acquisition. Dispatch retains task leases,
reservations, evaluation, and existing terminal cleanup responsibilities.
Standalone non-session dispatch remains usable without the optional bridge;
session acquisition then reports that capability unavailable rather than
silently bypassing the bridge.

## Evidence and limits

The headless adapter already has conversation-preserving recovery:
`plugins/agent-dispatch/src/agent_dispatch/bridge.py:spawn_or_resume_worker`
combines carried-session handling with retained-worktree recovery.
`spawn_factories.resume_worktree_eligible` admits directory-level recovery
only for reused allocations whose retirement marker is explicitly false.
This existing strict recovery is prior art, not something to replace.

The CLI stopgap in PR #6064 threads that eligibility decision into
`spawn_embodied_worker`. CLI acquisition still uses an independent worktree
launch boundary; the stopgap does not consolidate session ownership.

`agent-bridge create` historically means a fresh session, whereas bridge
resume/send already recover conversations. The operator's automatic
acquisition expectation requires reconciling these surfaces, not assuming
that cold resume is wholly absent. Inventory current source before coding:
earlier diagnosis against an installed release is not proof of the current
development branch or of which backend an individual incident exercised.

## Coordination

| Participant | Owned slice | Reach |
|---|---|---|
| Plan coordinator | Phase 6 scope and integration | #6076 and the plan PR |
| Implementation owner | Phase 6 runtime and validation, after plan review | Claim #6076 before changing runtime source |

Use serial issue-bound worktrees targeting `dev`. Do not launch a parallel
implementation against these shared modules without recording a disjoint
slice in #6076. A handoff carries the current issue/PR, phase, and unresolved
decisions; this document remains the canonical contract.

## Implementation plan

The following sequencing and compatibility details are **agent-recommended**
ways to realize the operator's requested contract, not additional operator
requirements.

1. Inventory every dispatch session-launch entry point (one-shot, supervisor,
   interactive, and remote fleet), mode selectors, carried handles, worktree
   affinity, retirement evidence, and existing bridge resume/launch contracts.
   Record the mapping here before implementation; do not conflate bridge
   handles with native Copilot conversation IDs.
2. Specify one bridge acquisition request/result. Carry venue, explicit mode,
   authoritative task/worktree affinity, prior conversation identity, and
   freshness intent. Return the exact durable conversation identity, bridge
   handle, mode, and whether acquisition reused, resumed, or created.
   Final flag names and protocol version are chosen from current repository
   conventions during this slice, not invented by this plan.
3. Implement bridge-owned acquisition using existing lifecycle/provider
   mechanisms. Deliver to a live matching holder without spawning another
   process; resume a stopped matching conversation by its native ID; create
   only for a proven new allocation or explicit fresh/retired intent.
   Unknown ownership, missing continuity evidence on a retained allocation,
   or failed strict resume yields a typed refusal/deferral, never success
   disguised as a fresh conversation.
4. Reconcile public `create` semantics explicitly. Preserve intentional fresh
   creation with an explicit freshness control; define worktree-bound
   automatic acquisition and its compatibility/version gate in the bridge.
   Do not silently change unbound callers or turn operator retirement into
   permission to revive the retired transcript.
5. Move dispatch session acquisition and delivery onto that boundary for
   local and remote CLI/ACP paths. Dispatch conveys validated mode, affinity,
   and freshness facts but does not implement another resume/create state
   machine. Worktree allocation and terminal cleanup stay separate. Remove
   direct session-launch fallbacks only after caller coverage proves parity.
6. Document the request/result, mode/freshness compatibility, ownership,
   missing-capability behavior, and source-to-release rollout. Land reviewed
   runtime slices with changefiles; promotion and authorized rollout are
   distinct from a merge. Record exact running-version proof separately
   from unit-level continuity evidence.

## Validation Plan

All cases run for CLI and ACP where the provider advertises that capability;
unsupported provider/mode combinations must refuse explicitly.

| Case | Required observation |
|---|---|
| New allocation | One new native conversation; requested mode and worktree binding |
| Live matching conversation | Delivery to the same native ID; no second process or duplicate prompt |
| Stopped matching conversation | New process, same native ID and prior transcript; follow-up work sees prior context |
| Deliberately retired conversation | Fresh conversation by explicit policy; retired ID never resurrected |
| Fresh task targeted at an existing directory | No unrelated transcript inherited merely because a worktree ID exists |
| Retained allocation with unknown/missing identity or retirement marker | Explicit refusal/deferral unless authoritative owner evidence resolves uncertainty; no silent fresh fallback |
| Rewritten, ambiguous, foreign, or busy owner | Fail closed without takeover, stopping, or replacing the holder |
| Concurrent acquisition and retries | One authoritative holder and idempotent delivery; no duplicate turns |
| Pending or untaken steers | Durable steer remains available and is consumed exactly once |
| Mode/protocol/capability skew | No silent CLI/ACP switch or weakened strict-resume guarantee |
| Missing bridge | Standalone dispatch still works; session acquisition clearly unavailable |
| Local and supported remote venues | Same acquisition/result contract and identity proof across transport boundaries |

Use bounded targeted tests for the changed bridge, dispatch, worktree, and
provider contracts. Add an isolated process-exit/resume scenario that records
native conversation IDs before and after the restart and verifies retained
context; matching task/worktree IDs alone does not pass. Exercise each migrated
entry point, including explicit fresh and retired cases. Run applicable
clean-room/provider scenarios where practical, and disclose any unavailable
venue proof rather than crediting a mock as a live integration result.

## Scope boundary and completion

This phase closes existing session-hosting and remote-interactive-sessions
intent: one coordinated hosting boundary, explicit CLI mode, and no duplicated
replay/retirement protocol. Honor the patterns' optional-capability composition,
one command owner, cross-platform parity, and truthful errors.

No new daemon, session-host wrapper for CLI, queue evaluator rewrite, physical
deployment policy, or unrelated historical effort cleanup belongs in this
slice. If a standing vision genuinely contradicts the selected contract,
resolve that contradiction before runtime implementation.

Phase 6 is complete only when the mapped session-acquisition callers use the
bridge boundary, the validation matrix is resolved with honest evidence,
authoritative docs match, and the runtime changes have merged. Record release
and authorized rollout status without claiming uninstalled code is live.
Completing Phase 6 does not close this effort's unrelated remaining phases.
