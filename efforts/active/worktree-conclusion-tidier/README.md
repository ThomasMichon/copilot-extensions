# Worktree Conclusion Tidier

- **Slug:** `worktree-conclusion-tidier`
- **Repo:** copilot-extensions
- **Branch(es):** serial proposal and implementation PRs
- **Created:** 2026-10-10
- **Status:** Draft
- **Status detail:** Report-only capability released through #6358 and its
  installed runtime/charter verified; live pilot parameters/execution and
  later authority graduation remain gated.
- **Vision:** `visions/plugins/agent-dispatch/README.md` scheduled production
  and durable goals; `visions/plugins/agent-worktrees/README.md` accountable
  worktree-lifetime state.
- **Umbrella issue:** #6060
- **Related issues:** #4101, #4216, #1488, #1918, #1646, #5543
- **Pilot prerequisite:** #6063 satisfied by #6070, released through #6233;
  stable FINAL display verified independently of fresh removal authorization.

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
- **Delegates:** no live pilot worker until its explicit scope and parameters
  are approved; audit capability implementation is now authorized.
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

The operator agreed the design-only authority baseline and the four
clarifications recorded in the 2026-10-12 journal entry. Remaining mechanics
and thresholds in [charter.md](charter.md) are **agent-recommended proposals**,
not standing authorization, pilot approval, or scheduling approval.

## Plan

### Phase 1 - Agree the charter

- [x] Capture the requested intent and design-only scope.
- [x] Identify existing scheduling, lifecycle, and allocator boundaries.
- [x] Draft [charter.md](charter.md), including authority, hold conditions,
  receipts, and staged rollout.
- [x] Operator agrees the design-only authority baseline and clarifications;
  an apply pilot still requires its own exact operation allowlist.
- [x] Resolve #6063 before the tidier pilot: successful finalize remains
  visibly FINAL during normal polling, independently of fresh removal checks
  (#6070, released through #6233).
- [ ] Select explicit project scope, budgets, and pilot candidates.
- [ ] Select transcript completeness, trusted completion-verification, and
  private report-destination requirements for the audit pilot.
- [x] Obtain explicit approval to implement the audit-only capability.
- [x] Complete the capability's promotion to main and verify its installed
  runtime/charter before the live pilot (#6358; installed dispatch
  `0.16.9-dev1` verified); a dev merge is not deployment.
- [ ] Obtain explicit approval to run the bounded live audit pilot.

### Phase 2 - Implement an audit-only pilot

_Audit-first capability implementation approved. Live execution remains
blocked on the remaining Phase 1 parameters and explicit pilot approval.
Mutation-fencing implementation is not an audit gate._

- [x] Operator approves starting with a report/steering-oriented dispatch
  charter and gradual, separately approved authority graduation.
- [x] Land the manually invoked report-only recipe and its receipt checker
  on dev (#6303); independent semantic verification remains mandatory.
- [x] Publish that capability through promotion to main (#6358).
- [ ] Implement deterministic discovery and one exclusive bounded worker using
  the existing registrar/supervisor if recurring production is later approved;
  the initial one-off recipe requires an explicit candidate roster.
- [ ] Produce evidence-backed per-candidate decisions without lifecycle,
  issue, effort, session, source, or posted steering mutations for candidates;
  use only the approved worker-run/report state for audit output.
- [ ] Validate the safety cases below and obtain operator review of the
  proposed actions before any apply-mode pilot.

### Phase 3 - Pilot explicitly approved safe actions

_Agent-recommended; blocked on a separate pilot approval._

- [ ] Resolve candidate reservation, expected-revision fencing, and
  unknown-outcome reconciliation before implementing an apply-capable worker;
  audit snapshots never authorize later mutations.
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

- [ ] Audit-only mode mutates no candidate lifecycle or external domain
  records, including steering cards/comments; writes are limited to approved
  worker-run/report state and tool-owned ephemeral evidence collation.
- [ ] Apply-mode tracking records require an explicit filing policy and do
  not allocate execution resources or transfer responsibility by themselves.
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
- [x] Real temporary Git repositories prove FINAL stamp persistence,
  checkout-change invalidation, and the production display/removal split.
- [x] CLI and both MCP recipe surfaces retain report verification and
  project-scoped exclusivity; report-only drive never resolves/unwinds work.

## Proposal

[charter.md](charter.md) is the governing draft. The initial capability is
`agent-dispatch recipes render|kick worktree-audit`, with explicit candidates
and private report parameters. No live worker, emitter, schedule, supervisor
registration, or apply-mode pilot has been started by this effort.

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

### 2026-10-12 - Agreed design clarifications

- Operator agreed the proposed clarifications and directed continuation of
  the design revision; this is not pilot, apply-mode, or scheduling approval.
- Recorded #6063 as satisfied by #6070 and release #6233 after installed
  FINAL behavior was verified; no release work is reopened by this proposal.
- Distinguished forbidden downstream execution allocations from tracking
  records that an apply pilot may explicitly authorize.
- Kept audit steering proposal-only in the approved report destination.
  Posting candidate attention records remains an apply-mode mutation.
- Moved mutation-fencing resolution to the gate before apply-capable
  implementation. An approved read-only audit may identify fencing gaps,
  but its conclusions are snapshots, not future mutation permissions.
- Project scope, budgets, candidates, evidence/evaluator requirements,
  report destination, and explicit audit-pilot approval remain open.

### 2026-10-12 - Audit-first implementation approval

- Operator endorsed audit-only first, with an initial dispatch charter
  producing reports or steering cards and gradual authority graduation.
- Selected the report-only variant for the first capability: proposed
  steering questions stay in the report, preserving nonblocking sweeps.
- Reused dispatch's ordinary recipe/goal/dedup/verification machinery rather
  than adding a daemon or automatically installing a daily producer.
- Added explicit bounded roster/report parameters and a structural receipt
  checker; neither the checker nor a FINAL stamp attests objective completion.
- Strengthened FINAL coverage at real Git and production descriptor/Picker
  boundaries after the prior release exposed stale test assumptions.
- Live candidate selection, run approval, posted candidate cards, apply-mode,
  mutation fencing, and recurring/default activation remain separate gates.

### 2026-10-12 - Report-only dev slice landed

- #6303 merged to dev as `0ca3d894124663652b46fe7f9731512aff3b48a5`
  after a genuine current-head approving review with zero findings and
  successful CI, including both live required checks. No gate was bypassed.
- The one-off recipe, report-only charter, trigger skill, structural receipt
  checker, CLI/local-MCP/HTTP-MCP controls, and focused FINAL coverage are
  merged to dev. Promotion to main and installed capability verification
  remain pending; this is not a release/deployment completion claim.
  Installing them does not activate a worker or producer.
- Review fixes preserved unspecified verification defaults, enforced
  report-file containment before reads, handled older-Python resolution
  loops, and derived recipe creation defaults from the ordinary parser.
- Validation included real non-dry-run CLI task creation against an isolated
  coordinator, real HTTP transport, and real Git stamp/descriptor cases.
  The latest audit/actual-kick guard selection passed 50 tests; native
  Windows symlink creation was explicitly skipped when unavailable.
- Structural report validation and local test receipts are not evidence of
  a successful live agentic audit or of candidate mutation absence. The
  live pilot's remaining safety cases stay open.
- Next gates: finish publication/installed capability verification, then
  approve exact local project/candidate roster, budget, private
  writable report destination, evidence completeness standard, and independent
  reviewer before any live audit. Establish fetch-free evidence reads;
  observer commands that may refresh Git or lifecycle state are not assumed
  read-only merely because their names describe status.
- Apply operations, posted candidate cards, mutation-fencing implementation,
  recurring production, and default activation remain separately unapproved.

### 2026-10-12 - Release and installed capability verified

- The normal validation/promotion pipeline completed successfully in run
  `38305079883`; its generated release PR #6358 first published the
  capability on main as `67661d853e8f5de7a11c078dbc6dcf50cbce06be`.
  The later release #6361 retained it, and installed-code comparison used
  that pinned main snapshot `1fbe70751ef74ffb66cbac9598768a36272110a4`.
  Existing upstream repair
  owners cleared the publication blockers; this effort opened no competing
  pipeline fix and bypassed no gate.
- The authorized canonical unified update refreshed marketplace payloads
  and runtimes. It returned nonzero because unrelated agent-index and
  agent-machines installers refused legacy provenance (tracked in #1611);
  whole-machine convergence is not claimed.
- Independently verified the installed dispatch coordinator as healthy at
  `0.16.9-dev1` after updater exit. Its deployed audit module matches the
  pinned main Git blob `57ae2468f426b3a9bb1bb5a1b0bad2637508ea57`.
  The installed CLI exposes the four required recipe parameters, the
  report-only worker charter, and the receipt-checker command.
- No audit task, candidate operation, producer, or recurring schedule was
  created. Runtime availability and structural coverage do not prove a
  live agentic audit, semantic conclusions, or candidate mutation absence.
- The next gate remains operator selection and approval of the exact local
  project/candidate roster, budget, private writable report destination,
  evidence completeness standard, and independent reviewer. Fetch-free
  reader admission must be established before executing that pilot.
