# Source-Remote Lease Isolation

- **Slug:** `source-remote-lease-isolation`
- **Repo:** copilot-extensions
- **Branch(es):** `worktree/tmichon-cloud1-win-20260831-221938-9ff0`
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** `visions/plugins/agent-worktrees` — private coordination state is explicit and source remotes are not state stores
- **Umbrella issue:** #1528
- **Sub-issues:** none

## Guiding Intent

Keep Git source remotes focused on distributing source and reviewed change
history. Ref-backed resource leases are private coordination state: they may use
an explicitly configured private store, including a bound knowledge repository,
but must never silently accumulate on the repository being worked on.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| implementation worktree | Plan owner, implementation, validation, and PR | `worktree/tmichon-cloud1-win-20260831-221938-9ff0` |

## Coordination

- **Topology:** one worktree, proposal PR followed by implementation PR
- **Host (owns PRs):** implementation worktree
- **Delegates:** none
- **Handoff:** the effort README remains the canonical continuation point

## Context

`agent-worktrees lease` implements cross-machine compare-and-swap leases using
hidden Git refs. `lease_config._resolve_store_target` already prefers a bound
knowledge repository and fails closed when that binding cannot be resolved, but
when no knowledge repository is bound it implicitly selects the current
project's source remote. That default makes an ordinary shared or public
repository an unbounded coordination-state store.

The change advances the agent-worktrees vision's single-owner state model by
making the remote state owner explicit. It also follows the repository patterns
of fail-loud configuration and source/runtime separation: source remotes carry
source; private coordination state requires a deliberately selected state
surface.

## Request

> Block the ref-claim system from being used through source remotes. In general,
> do not claim via shared or public repository remotes.

## Plan

### Phase 1 — Reviewed intent
- [ ] Extend the agent-worktrees vision with the source-remote/state-store boundary.
- [ ] Land this effort and vision revision through the proposal review gate.

### Phase 2 — Store resolution
- [ ] Remove the implicit fallback from lease-store resolution to the current project's source remote.
- [ ] Preserve explicitly supplied lease origins and bound knowledge-repository routing.
- [ ] Make missing explicit private state fail with actionable remediation.

### Phase 3 — Consumer contract
- [ ] Update CLI help and lease documentation to describe explicit private-store requirements.
- [ ] Confirm callers surface configuration failures rather than silently degrading to the source remote.

### Phase 4 — Validation and landing
- [ ] Cover explicit origin, bound knowledge repository, unresolved required state, and unconfigured repository cases.
- [ ] Prove no test path pushes lease refs to an ordinary source `origin` by default.
- [ ] Run the focused agent-worktrees suite and repository contract gates.
- [ ] Bump the agent-worktrees plugin and marketplace versions.
- [ ] Publish, review, merge, deploy, and close #1528.

## Validation Plan

- [ ] Unit tests prove explicit `--origin` / `AGENT_WORKTREES_LEASE_ORIGIN` remains supported.
- [ ] Unit tests prove a bound knowledge repository remains the authoritative remote store.
- [ ] Unit tests prove an unresolved bound knowledge repository fails closed.
- [ ] Unit tests prove no binding and no explicit origin fails instead of resolving the source remote.
- [ ] CLI tests prove the failure is actionable and issue-comment coordination is unaffected.
- [ ] Targeted plugin tests and install-contract checks pass.
- [ ] The merged plugin is deployed through `agent-worktrees update`.

## Proposal

Treat remote ref leases as an opt-in backend rather than an implicit property of
every managed repository:

1. An explicit per-invocation or environment origin remains authoritative.
2. A bound knowledge repository remains the preferred private state backend.
3. With neither configured, lease-store resolution raises `ConfigError`; it
   never examines or falls back to the current project's source remote.

This is intentionally narrower than attempting to infer whether an arbitrary
Git host or repository is public. Privacy and identity suitability cannot be
reliably derived from a URL. Requiring an explicit backend makes the trust
decision visible and keeps the safe default independent of hosting provider.

## Journal

### 2026-08-31 — Kickoff
- Created #1528 as the public coordination token and reserved the work locally.
- Confirmed the target repository has adopted efforts.
- Traced the unsafe default to `lease_config._resolve_store_target`.
- Reconciled the change as an extension of the agent-worktrees vision.
