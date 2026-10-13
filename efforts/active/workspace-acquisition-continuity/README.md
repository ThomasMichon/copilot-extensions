# Workspace Acquisition Continuity

- **Slug:** `workspace-acquisition-continuity`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase isolated worktrees and PRs to `dev`
- **Created:** 2026-10-12
- **Status:** Draft
- **Vision:** worktrees `replay-safe-workspace-allocation`, bridge workspace
  allocation/embodiment, dispatch execution continuity and bounded refusal.
- **Umbrella issue:** #6440
- **Related issues:** #6076, #5511, #6239, #6366

## Guiding Intent

Resolve a worker's execution workspace once and retain that repository/worktree
identity through allocation, session acquisition, retry, recovery, and cleanup.
Ground-layer creation allocates space only. Bridge composes that allocation
with embodiment, including the first conversation in a positively sessionless
existing worktree. Stable logical creation identity prevents transport retries
from allocating another workspace or session.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation owner | Proposal, targeted fixes, validation, and PR stewardship | Source worktree codename `rusty-relay` |

## Coordination

- **Topology:** serial per-phase PRs; one implementation owner.
- **Host (owns PRs):** Implementation owner.
- **Delegates:** none.
- **Handoff:** retain this reviewed plan, issue claim, current slice, and pending
  completion gate; never treat an opened PR as completion.
- Preserve separately owned acquisition, legacy-recovery, producer-ownership,
  and performance work. #5511 supplies the related persisted-project recovery
  requirement; coordinate rather than introduce a competing authority.

## Context

Headless allocation can select the registered worker agent's project while
acquisition independently selects the producing task lane. A deterministic
worktree lookup refusal can then be classified as an ambiguous/busy deferral,
outside the failure budget. A task without an exclusive reuse key can allocate
another worktree on each attempt.

Existing bridge acquisition journals and native-holder gates remain the
session authority. #6076 established that boundary. #6239 owns historical
first-conversation recovery, #6366 owns exact-record preflight performance,
and PR #6391 owns general disposable-producer obligations. This effort extends
those owners rather than replacing them.

## Request

> Great. Help correct or update any vision or arch docs, then work on targeted fixes

Settled operator contract, summarized with repository-specific examples
abstracted: worktree creation and bridge creation share one target workspace;
worktree creation starts no session, bridge creation composes allocation and
embodiment, and an existing sessionless worktree is an ordinary embodiment
input. Dispatch must never split execution repositories between those calls.
A caller creation key binds to the returned allocation/session rather than
becoming the worktree ID; retries return that result and logical revision
distinguishes deliberate replacement.

## Plan

### Phase 1 - Review intent and contract

- [x] Reconcile the three owning visions and shared architecture contract,
  keeping should-be intent separate from implemented creation interfaces.
- [ ] Land this proposal through automated review before runtime execution.

### Phase 2 - Correct execution-target and refusal handling

- [ ] Carry the resolved execution project and exact worktree/path from
  preparation to acquisition; reject incompatible owner-returned identity.
- [ ] Preserve that identity for retries and restart recovery, coordinating
  the persisted-project requirement in #5511.
- [ ] Classify positively proven pre-effect refusals as bounded failures while
  retaining ambiguous launch outcomes for same-allocation reconciliation.
- [ ] Preserve native holder, mode, retirement, and first-conversation gates.

### Phase 3 - Replay-safe creation

- [ ] Add optional caller creation identity through the existing worktree and
  bridge creation boundaries, with one owner of allocation facts.
- [ ] Bind the key and effective target facts before effects; replay the
  original result or report explicit pending/recovery, conflict, or terminal
  state. Keep the key distinct from generated worktree/session IDs.
- [ ] Derive a stable dispatch allocation identity from task deduplication
  identity (durable task ID when absent) and explicit logical revision,
  independently of reservation attempt and ordinary claim generation.
- [ ] Keep deliberate unkeyed creation and separately owned producer
  obligations compatible; version safety-affecting wire behavior explicitly.

### Phase 4 - Validate, land, and close ownership

- [ ] Resolve targeted regression failures and update the reality docs to the
  implemented contract. Add per-plugin changefiles and steward PRs to merge.
- [ ] Record release/rollout state accurately; automatic registrars stay
  disabled unless separately authorized.
- [ ] Resolve every Plan/Validation item or explicitly transfer any remaining
  obligation to a named tracked objective, then archive and release this effort.

## Validation Plan

- [ ] Connected queue-project versus execution-project regression proves one
  workspace from allocation through bridge/native acquisition.
- [ ] Deterministic pre-effect refusal spends the bounded failure budget;
  an unknown effect retains the same allocation and does not create again.
- [ ] Repeated and concurrent keyed creation, lost responses, and interrupted
  effects preserve exactly one allocated workspace and native conversation.
- [ ] Changed facts and terminal-key replay refuse; deliberate logical revision
  permits a separately authorized allocation.
- [ ] Existing positively sessionless worktree admits its first conversation;
  foreign/live holders, missing legacy proof, and uncertain launch still refuse.
- [ ] Existing unkeyed creation, CLI/ACP mode, task/lane authority, retirement,
  source-control ownership, and installation-cell isolation stay compatible.
- [ ] Native Windows and portable contained tests exercise real CLI/process
  boundaries; clean-room first-touch checks cover new creation persistence.
- [ ] Unit, simple end-to-end, clean-room, and available live-venue evidence
  are recorded with explicit reasons for skipped tiers.

## Proposal

Submit the vision, pattern, architecture correction, and effort before runtime
changes. Existing per-delivery bridge idempotency is reused, not mistaken for
cross-attempt workspace allocation identity. No timeout increase, forced
takeover, blanket freshness authorization, or parallel session owner is proposed.

## Journal

### 2026-10-12 - Proposal

- Operator confirmed the effort slug and docs-before-fixes sequencing.
- Public coordination was reserved under #6440 after checking adjacent
  acquisition, allocation, legacy-recovery, and producer-ownership work.
- Request capture retains the operator's scope; concrete downstream examples
  are abstracted without changing the single-workspace or stable-key contract.
