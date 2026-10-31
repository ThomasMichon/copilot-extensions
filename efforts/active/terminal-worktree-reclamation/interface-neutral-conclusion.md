# Interface-neutral terminal worker conclusion

Back to [Terminal Worktree Reclamation](README.md). Bounded source slice of
#1488; proposed contract, not implementation or operational authorization.

## Requested outcome

Dispatch requests shutdown from Bridge when its worker is terminal. Bridge
shuts down the agent and releases its active workspace ownership. Worktrees
then evaluates safe workspace conclusion and cleanup. A worktree's CLI/ACP
interface records how a session was used; it neither grants nor vetoes
ownership, session shutdown or cleanup.

This closes the Dispatch vision's `terminal-worktree-reclamation` and
`allocator-reclaims-what-it-creates` behaviors. It retains the existing
producer/Dispatch/Bridge/Worktrees boundaries and does not add a cleanup daemon,
alternate ownership database or interface conversion.

## Current mismatch

The local terminal path already captures native identity before asking Bridge
to end an idle worker. Its older label-scoped conclusion subsequently invokes
Worktrees' `disposable-cli` policy, whose interface check refuses ACP records
before session conclusion. It also writes `interface=cli` while priming a
record. Neither behavior establishes disposal authority.

The distinct `dispatch-attempt` path validates created-allocation provenance.
It preserves reused and targeted workspaces; replacing all terminal calls with
that policy would not solve the original mismatch and must not manufacture
ownership for a legacy or shared workspace.

## Contract

1. Retain the current terminal-task and configured-policy admission. Task
   submission awaiting verification is not terminal. Fence unresolved startup
   before retirement, and retain the original reservation/session/workspace
   identity across restart or response loss.
2. Delegate agent shutdown to Bridge using its existing terminal shutdown
   operation. Its idle/queued-prompt/background-work serialization remains
   authoritative. A resumable detach or failed/unknown stop is not proof of
   physical exit. Never kill a process directly from Dispatch or Worktrees.
3. Checkpoint the exact native session before shutdown can remove Bridge's
   live-session row. Keep shutdown and workspace cleanup as distinct visible
   outcomes. An already stopped/absent exact body is idempotent, not permission
   to infer a missing native identity or stop a successor.
4. Worktrees' existing disposable-worker conclusion becomes interface-neutral. Preserve the
   recorded interface; stop rejecting ACP records or rewriting them to CLI.
   Keep the historical `disposable-cli` policy spelling for wire compatibility,
   not as an interface or ownership rule. No new verb, policy value or
   prerequisite RPC is needed to fix behavior owned by this operation.
5. After Bridge releases active control, Worktrees independently checks the
   exact lifecycle head and fresh liveness, dirty/unmerged content, branch
   identity, handoffs, efforts/follow-up, PRs, pairing and other claims. Bridge
   stop alone does not release another owner's obligations or prove a shared
   workspace disposable. A new owner/resume or changed identity wins cleanup.
6. Preserve original created-allocation provenance and the distinct
   `dispatch-attempt` authority. Legacy label opt-in is an explicit terminal
   cleanup policy, not retroactive proof of creation. Do not silently classify a
   targeted or reused workspace as created or adopt historical holds.
7. Unsupported receivers and inconclusive shutdown retain a visible blocked
   cleanup outcome. Older Worktrees receivers continue to return
   `not-cli-worktree` for ACP records; do not treat that refusal as success,
   retry another policy or alter the worktree's interface to obtain acceptance.
   Upgrade the receiving operation through separately authorized rollout.

Exact implementation/test organization below is agent-recommended. The outcome
and ownership split above are the requested contract.

## Plan

- [x] Qualify the current Bridge terminal-shutdown operation and Dispatch
      ordering; extend only the missing boundary, rather than inventing a
      parallel Stop implementation.
- [x] Make existing ground conclusion interface-neutral without
      changing any independent preservation check or allocation provenance.
- [x] Qualify Dispatch's existing terminal caller against the corrected ground
      operation, preserving exact pre-shutdown identity, failure visibility and
      durable retry state. Change routing only if a missing boundary is proven.
- [ ] Update CLI and lifecycle docs, then land source through normal review.
      Keep rollout and historical cleanup separately authorized.

## Validation Plan

- [x] CLI, ACP and legacy-interface records with equivalent identity, content
      and obligations produce equivalent safe conclusion/preservation results;
      the recorded interface survives unchanged.
- [x] Bridge shutdown precedes Worktrees cleanup. Busy turns, queued prompts,
      background work, stop failure, timeout and unknown liveness cannot
      produce successful cleanup or stop a newer owner.
- [x] The native identity remains available across Bridge teardown and a
      supervisor restart; missing identity stays visibly blocked.
- [x] Fresh live bodies, changed heads, dirty/unmerged work, handoffs, efforts,
      PRs, pairs and unrelated resource claims still prevent removal.
- [x] Repeated shutdown/conclusion is idempotent and a reused/targeted
      allocation never acquires created-allocation authority.
- [x] Strict old-receiver rejection is visible; no interface rewrite,
      permissive fallback or production operation occurs during validation.

No historical-cohort adoption, bulk deletion, held-cleanup rearm, forced stop,
service update or runtime activation is authorized by this source plan.

## Source qualification

The plan merged in #9305 before implementation. The focused source packet
removes the ground interface veto and preserves the effective interface before
kind conversion; it retains the existing shutdown operation and wire syntax.
Worktrees' terminal-conclusion packet passes 158 controls, Dispatch's selected
terminal/disposable packet passes 36, and eight existing Bridge controls cover
idle shutdown, busy/queued/background refusals, admission serialization and
actual isolated Session Host/child exit. These are source tests with synthetic
state, not production shutdown, historical adoption or rollout evidence.
