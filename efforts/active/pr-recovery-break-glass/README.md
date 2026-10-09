# PR Recovery Break Glass

- **Slug:** `pr-recovery-break-glass`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase reviewed PRs off `dev`
- **Created:** 2026-10-09
- **Status:** Draft
- **Vision:** [pull-request capability](../../../visions/plugins/agent-worktrees/pull-requests/README.md)
  `operator-authorized-pr-recovery` and `recovery-does-not-require-perfect-bookkeeping`
- **Coordination issue:** #5817

## Guiding Intent

Make guards assist deliberate PR recovery rather than turn imperfect historical
bookkeeping into an absolute veto. Prefer the source-owning worktree's CWD when
available, retain the ordinary supported publication path, and provide an
explicit, reasoned, auditable operator exception when that context or its
ownership/replay evidence cannot be recovered.

The exception is not a generic force push or a review bypass. It retains the
exact expected remote-head fence and normal repository publication/merge gates.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Operator | Authorizes recovery policy and consequential use | Interactive review |
| Recovery driver | Owns this plan, override implementation, validation and rollout | Normal repository worktree/PR flow |
| Existing publisher driver | Owns the explicit rewrite and compatible legacy-evidence work already underway | #5817 and PR #5835 |

## Coordination

- **Topology:** sequential, independently reviewed PRs; one publisher path.
- **Host:** Recovery driver owns this effort's PRs.
- **Existing work:** coordinate with the publisher driver; extend its publication
  surface rather than build a parallel Git client or race its core changes.
- **Handoff:** journal completed slices and carry the next unresolved Plan item.
  The broader automatic compatibility work under #5817 is not silently claimed
  or declared complete by this effort.

## Context

The supported source-owned rebase guard requires a worktree-associated private
head plus historical base, patch and Git replay evidence. Those are useful
normal-path protections, but they can reject a deliberately recovered existing
PR from another owned worktree after the original context is unavailable.

PR #5835 already develops an explicit exact-lease rewrite surface and
publication authority/serialization. Its #5817 continuation also tracks making
compatible legacy bookkeeping converge through one command. This effort is
the distinct operator-exception slice: do not duplicate that publisher or
mistake its still-strict normal ownership attestation for a break-glass path.

Relevant patterns are `docs/patterns/project-scoped-invocation.md` (explicit
addressing must resolve the same target as CWD discovery) and
`docs/patterns/README.md`'s attributable invocation and pipeline-deployment
invariants.

## Request

Operator, verbatim:

> We need to update the "rules and guards" here to be way less strict. While we
> should call the tool from the CWD of the other worktreee when possible, we need
> some "break-glass" way to overcome this, if we're trying to diagnose and unblock
> things.

The implementation decomposition and retained safety boundaries below are
agent-recommended details, not additional verbatim operator instructions.

## Plan

### Phase 1 - Reviewed intent and coordination
- [ ] Land the vision extension and this plan through the normal review gate.
- [ ] Coordinate the integration boundary with the existing publisher driver,
      preserving one publication surface and avoiding competing core edits.

### Phase 2 - Explicit recovery exception
- [ ] Extend the existing publication command with an explicit per-operation
      break-glass choice and a non-empty reason; no ambient/global bypass.
- [ ] Allow operator-authorized recovery despite missing original-worktree,
      suffix, ownership-attestation or replay bookkeeping. Prefer source CWD
      where available, but support an explicitly selected target from a
      recovery context. Do not automatically choose the exception after refusal.
- [ ] Retain exact expected-head leases, pinned committed source objects,
      destination/provider authentication, publication serialization, real
      hooks, protected/default/release-history restrictions, and normal PR
      review/merge gates. Do not refresh a stale lease to manufacture permission.
- [ ] Report what was overridden and preserve a durable local audit of reason,
      target and pinned source/expected head, without credential-bearing URLs
      or automatically publishing private rationale.
- [ ] Update authoritative CLI/help and worktree recovery guidance together;
      keep normal incremental publication and compatible legacy recovery intact.
- [ ] Add the agent-worktrees changefile and drive the implementation PR to merge.

### Phase 3 - Released and demonstrated recovery
- [ ] Verify normal promotion and install the released change through the
      unified update flow; no edits to deployed files or unmerged installation.
- [ ] Demonstrate a deliberately authorized existing-PR recovery through the
      supported command from a recovery context, verify its actual remote head,
      and preserve the fresh review/merge gate on the changed head.
- [ ] Journal the result, resolve this effort's validation items, archive it,
      and leave unrelated #5817 work with its named publisher driver.

## Validation Plan

- [ ] Normal incremental updates and source-owned rebase publication remain
      supported; a guard refusal never selects an exception on its own.
- [ ] Real isolated Git transport proves reasoned recovery with incomplete
      historical source/ownership/replay evidence and an unchanged exact lease.
- [ ] A moved or deleted remote head is refused even with break-glass selected;
      no plain force or opportunistic lease refresh occurs.
- [ ] Wrong destination/provider, protected/default/release refs and uncommitted
      source are refused; real hooks still run and can reject the push.
- [ ] CLI adapters select the same target from source CWD and explicit recovery
      context; empty reasons and inapplicable new-PR use fail before mutation.
- [ ] Audit and diagnostics distinguish requested, attempted and successful
      overrides; credential-bearing URLs and private reasons are not published.
- [ ] Targeted bounded tests, lint and source/install guards pass; required CI
      and release validation supply truthful platform coverage.
- [ ] A released live recovery supplies provider-confirmed head evidence and
      a new review gate, not merely a local flag or successful mocked transport.

## Proposal

Extend the existing explicit rewrite/publication path with a reasoned recovery
option. Keep exact target and transport fencing separate from historical
ownership heuristics: the operator can waive the latter deliberately, not the
former accidentally. Final flag spelling and integration follow the current
publisher implementation rather than introduce a second command family.

## Journal

### 2026-10-09 - Scope and coordination
- Captured the operator's request without replacing the source-CWD preference
  with an absolute requirement or omitting the requested escape hatch.
- Deduplicated against #5817 and its active publisher work in PR #5835.
  Recorded the distinct recovery-exception slice on the coordination issue;
  implementation has not started and awaits this plan's review gate.
