# Worktree Conclusion Tidier

- **Slug:** `worktree-conclusion-tidier`
- **Repo:** copilot-extensions
- **Branch(es):** serial proposal and implementation PRs
- **Created:** 2026-10-10
- **Status:** Draft
- **Status detail:** Design-only proposal; charter agreement, pilot execution,
  and recurring activation require separate operator decisions.
- **Vision:** `visions/plugins/agent-dispatch/README.md` scheduled production
  and durable goals; `visions/plugins/agent-worktrees/README.md` accountable
  worktree-lifetime state.
- **Umbrella issue:** #6060
- **Related issues:** #4101, #4216, #1488, #1918, #1646, #5543
- **Pilot prerequisite:** #6063, stable FINAL display after successful finalize.

## Guiding Intent

Improve garbage collection by helping work reach a truthful conclusion, not
by removing evidence of unfinished responsibility. A bounded agent can
interpret session history and reconcile stale bookkeeping; agent-worktrees
continues to decide whether a workspace is safe to remove.

This proposal composes existing scheduled production and supervision. It
does not add a daemon or give a model a force-delete escape hatch.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| design coordinator | Own the proposal, charter review, and proposal PR | isolated contribution worktree |
| operator | Approve authority, scope, pilot, and any later scheduling | proposal review and explicit decisions |
| lifecycle maintainer | Review ground-layer safety and mutation fencing | repository review |
| dispatch maintainer | Review producer, exclusive worker, and result contract | repository review |

## Coordination

- **Topology:** one proposal, followed by serial approved slices.
- **Host (owns PRs):** design coordinator.
- **Delegates:** none for implementation until the charter is approved.
- **Handoff:** this README names the current gate; a successor must not infer
  implementation permission from the existence or merge of the proposal.
- Existing allocator reclamation remains owned by
  `efforts/active/terminal-worktree-reclamation/README.md`; this effort does
  not replace its session/worktree teardown loop.

## Context

The existing dispatch supervisor can run a declared periodic emitter and a
label-scoped worker lane. Existing worktree commands provide session lineage,
disposition, claims, landing proofs, finalization, and non-forced cleanup.
Those are building blocks, not evidence that this agentic worker already
exists.

The gap is semantic conclusion: merged code and an idle process do not prove
the founding objective is finished. Stale follow-up flags can coexist with
completed work; a genuinely unfinished effort can coexist with clean Git
state. Historical pair/controller failures can block even a concluded
workspace and must remain visible rather than being bypassed.

Related boundaries:

- #4101 and #4216 own ground-layer triage and obligation safety.
- #1488 and #1918 own dispatch-created allocation reclamation.
- #1646 and #5543 track paired-worktree lifecycle problems; the tidier may
  report them, but is not authorized to invent missing sibling state.
- `docs/patterns/README.md` requires attributed command ownership, graceful
  composition, and report-first cleanup.

## Request

Operator request, verbatim:

> One piece of feedback I get about agent-worktrees and the Worktree Manager
> is that our garbage collection isn't very good. Some people are asking for
> an explicit "Remove worktree" feature, which "wouldn't care" about a
> worktree's claims our outstanding work, it would just remove stuff. Before
> we get that extreme, I wonder we can very carefully create an automated
> dispatch worker which runs once a day and helps perform agent-driven cleanup
> of worktrees. Basically something that "tidies up", pulling only "safe"
> cleanup levers, such as what I recommended in this session's charter
> (identify whether last session's work is all done, hoise outstanding
> questions into filed issues or efforts, drive miscellaneous small tasks,
> and follow up on outstanding downstream claims).

The referenced session charter, verbatim:

> Help clean up worktrees on this machine. Start with the oldest, merged
> worktrees, analyze their final states, and help make them eligible for
> cleanup by driving any final bits for conclusion or properly updating
> efforts and durably-filed issues.

Subsequent scope decision, verbatim:

> Produce a design, and we'll work out its charter, than we'll pilot it,
> before making it a default scheduled behavior.

Authority selected for the proposal:

> Reconcile records and finish narrowly authorized bookkeeping; queue
> substantive work for its owner.

Additional operator guidance, verbatim:

> It might be nice to allow this agent to post steering requests to the user,
> to ask what to do about certain things. That said, that risks blocking the
> agent.

> One issue is that somehow, we're still missing the proper "FINAL" status
> from worktrees. When I say "Finalize worktree" and the agent asserts
> finalization, merges the work, cleans up claims, and finalize succeeds, I
> expect the Mux and Picker to say "FINAL". But instead it always says
> "MERGED", forcing me to constantly go back and check older worktrees to see
> if they still need attention. If we fixed that first, that the "cleanup
> agent" would have less to do.

Clarification, verbatim:

> I expect that calling "finalize" on a worktree and having it succeed should
> stamp a worktree with "FINAL", as "finalize" performs that validation. We
> just need to ensure there is some stability after that so the FINAL status
> doesn't get immediately tripped back to "MERGED".

All mechanics and thresholds in [charter.md](charter.md) are
**agent-recommended proposals**, not additional operator authorizations.

## Plan

### Phase 1 - Agree the charter

- [x] Capture the requested intent and design-only scope.
- [x] Identify existing scheduling, lifecycle, and allocator boundaries.
- [x] Draft [charter.md](charter.md), including authority, hold conditions,
  receipts, and staged rollout.
- [ ] Operator approves or revises the charter's authority matrix.
- [ ] Resolve #6063 before the tidier pilot: successful finalize must remain
  visibly FINAL during normal polling, independently of fresh removal checks.
- [ ] Select explicit project scope, budgets, and pilot candidates.
- [ ] Resolve candidate mutation fencing and trusted completion-verification
  requirements before an apply-capable worker is implemented.

### Phase 2 - Implement an audit-only pilot

_Agent-recommended; blocked on Phase 1 approval._

- [ ] Implement deterministic discovery and one exclusive bounded worker using
  the existing registrar/supervisor; no new daemon.
- [ ] Produce evidence-backed per-candidate decisions without lifecycle,
  issue, effort, session, or source mutations.
- [ ] Validate the safety cases below and obtain operator review of the
  proposed actions before any apply-mode pilot.

### Phase 3 - Pilot explicitly approved safe actions

_Agent-recommended; blocked on a separate pilot approval._

- [ ] Enable only the approved bookkeeping and scoped lifecycle operations.
- [ ] Prove durable record updates land before dependent disposition changes.
- [ ] Corroborate every claimed outcome and preserve every refusal.
- [ ] Review false-conclusion rates, retained responsibility, runtime cost,
  and net workspace reduction.

### Phase 4 - Decide recurring adoption

_Agent-recommended; not authorized by the design request._

- [ ] Operator reviews the pilot results and chooses whether to continue.
- [ ] If approved, register once-daily bounded production with pause/disable,
  non-overlap, bounded catch-up, and inspectable results.
- [ ] Consider any default enablement only as a separately reviewed policy
  change; installing a capability never silently activates this worker.

## Validation Plan

_Agent-recommended acceptance cases for the future implementation._

- [ ] Audit-only mode causes no lifecycle or external-record mutations.
- [ ] Live or unknown-liveness worktrees, active owners, ambiguous lineage,
  missing transcripts, pending handoffs, and dirty/unmerged work are retained.
- [ ] A merged PR with an open parent effort is not classified as done.
- [ ] Filing an issue without accepted responsibility transfer does not permit
  clearing a follow-up or effort binding.
- [ ] Concurrent resume, claim, edit, and head change invalidate an action;
  ordinary cleanup re-checks atomic safety at removal.
- [ ] Already-settled claims reconcile only for the selected candidate;
  active/session/remote-unknown claims are never force-released.
- [ ] Missing paired records and downstream lifecycle refusals remain holds.
- [ ] Reticks, duplicate supervisors, restart, timeout, and lost acknowledgments
  do not duplicate workers, forge records, or removal.
- [ ] Repeated holds are deduplicated without starving newer candidates.
- [ ] A bounded run completes honestly with retained candidates; it does not
  extend authority to meet a removal quota.
- [ ] Windows and Linux/WSL pilots preserve local environment boundaries and
  use the owning installed runtime rather than checkout-pinned services.
- [ ] Operator inspection and pause/disable prevent new work while preserving
  in-flight responsibility and receipts.
- [ ] A candidate-specific steering request does not block unrelated
  candidates or silently transfer the original objective.

## Proposal

[charter.md](charter.md) is the reviewable draft. No worker, emitter, schedule,
supervisor registration, or apply-mode pilot has been installed or started by
this effort.

## Journal

### 2026-10-10 - Design-only kickoff

- Confirmed generic upstream ownership and compatible effort adoption.
- Opened #6060 as the design coordination token.
- Operator selected design/charter review before pilot and default scheduling,
  with bookkeeping-only authority and substantive work routed to its owner.
- Drafted a bounded proposal; all implementation and activation gates remain
  unchecked.
- Added optional nonblocking steering and stable explicit-finalization status
  as a prerequisite, following operator feedback; #6063 owns the status fix.
