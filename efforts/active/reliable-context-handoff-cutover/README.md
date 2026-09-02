# Reliable Context Handoff Cutover

- **Slug:** `reliable-context-handoff-cutover`
- **Repo:** copilot-extensions
- **Branch(es):** Isolated Windows worktree; one PR.
- **Created:** 2026-09-01
- **Status:** Draft
- **Vision:** Agent Fabric §Features/delegate-and-hand-off
- **Umbrella issue:** #1630
- **Sub-issues:** #1632

## Guiding Intent

Make a context handoff a compact, recoverable transfer of active responsibility
whose successor can reliably find the stored brief, assume the correct
worktree/session identity, and retire the predecessor only after pickup is
proven.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Windows worktree | Plan, implementation, validation, and PR ownership | Isolated worktree |

## Coordination

- **Topology:** One worktree and one PR for the coherent context-handoff change.
- **Host (owns PRs):** Windows worktree.
- **Delegates:** None.
- **Handoff:** A successor resumes from this effort, the current Plan slice, and
  the stored handoff pointer rather than copying the full plan into its seed.

## Context

The current extension primarily identifies a mux through inherited
`TMUX_PANE`/`PSMUX_PANE` environment variables. Those variables are not a
reliable source of truth for every Copilot launch path even when the Copilot
process is inside a mux. Agent-worktrees already owns a stronger
`mux_binding_for_session` implementation that resolves the Copilot PID from its
exact session-state lock and matches process ancestry to pane roots, but that
capability is not exposed as a stable query for higher-layer orchestration.

Live-cutover seeds can also grow large because they carry orchestration
instructions and shell chains inline, while ordinary non-live fallback output
does not always make the user action sufficiently clear. The existing consume
path already binds a successor before verified pane retirement and the ground
layer already derives session head and succession state. The remaining work is
to use those primitives consistently, close task/file-path gaps, update the
continuing title through the owning APIs, and make partial cutover safely
re-runnable.

The agent-fabric vision assigns handoff orchestration to context-handoff while
agent-worktrees remains the owner of worktree and session lifecycle. This
effort builds on the shipped foundations from #84 and #910 and remains
compatible with the additional pane-provider direction in #1584 without
claiming those issues as its own scope. It advances #1630.

## Request

Improve context handoff so mux ownership can be recovered from the invoking
session and process ancestry, successor seeds remain short and preserve exact
recovery commands, non-live fallback output clearly tells users what to invoke,
and successful consumption establishes the successor before retiring the
predecessor. Design and test the non-mux terminal-launch contract, but leave
automatic non-mux terminal spawning to a follow-up implementation.

## Plan

### Phase 1 — Resolve execution identity
- [ ] Make `handoff-core.mjs` the single SDK-free implementation used by both
  `extension.mjs` and `handoff-cli.mjs`, deleting duplicated storage, metadata,
  seed, and consume/cutover helpers from the extension.
- [ ] Expose agent-worktrees' existing session-to-process-to-mux binding as a
  bounded `session-binding --session-id <id> --json` query without duplicating
  process ancestry logic in context-handoff.
- [ ] Have context-handoff use that query to record the invoking Copilot process,
  process-creation identity, pane, and mux identity in stored metadata, with
  inherited environment variables retained only as a validated fast path.
- [ ] Keep metadata additions backward-compatible: old records with missing new
  fields still load, while retirement requiring unavailable proof degrades to a
  safe manual cleanup rather than guessing.
- [ ] Keep standalone operation graceful when agent-worktrees, a mux, or a
  session-state record is unavailable.

### Phase 2 — Compact and recoverable successor seeds
- [ ] Replace variable-length inline orchestration prose with the stable
  three-part seed: concise task summary, one `/consume-handoff`
  recommendation, and one raw payload-recovery command.
- [ ] Preserve exact quoting and formatting for task-backed and file-backed
  recovery commands without embedding the full handoff markdown in argv.
- [ ] Enforce a 1024-character seed budget in `cutover-seed.mjs` and a
  single-line launch-safe form while retaining a lossless raw recovery command.
- [ ] Make the non-live fallback explicitly user-facing and clearly delimit the
  exact prompt or command the user must copy.
- [ ] Preserve creation of the claimable task/file record and its record-first
  handoff pointer even when no live cutover is possible.

### Phase 3 — Close consume and cutover gaps
- [ ] Persist a worktree-state cutover checkpoint before task-backed consumption,
  carrying the handoff token, predecessor identity, and completed-step markers
  so retry can converge after the one-time task payload has been claimed.
- [ ] Apply the existing bind/head/succession primitives consistently to both
  task-backed and file-backed consumption before predecessor retirement.
- [ ] Update the mux title from the continuing work stream through the owning
  agent-worktrees lifecycle API rather than calling a mux directly.
- [ ] Make the bind, lineage, head, title, and retirement sequence idempotently
  re-runnable after a mid-sequence failure or already-consumed retry.
- [ ] Retire only the recorded and verified predecessor process/pane after
  successor pickup, including process-creation identity checks that prevent a
  reused or stale PID from being terminated, while preserving the predecessor
  whenever any prerequisite fails.

### Phase 4 — Non-mux launch contract
- [ ] Document the #1632 terminal-launch protocol: persist first, record
  predecessor PID plus creation identity and expected command shape, launch with
  a prompt file or equivalent lossless transport, then let consumption terminate
  only the verified predecessor.
- [ ] Add contract-level tests for the proposed Windows and POSIX boundaries
  without enabling automatic non-mux spawning in this delivery.
- [ ] Deferred to `#1632`: implement automatic non-mux terminal spawning.

### Phase 5 — Documentation and release
- [ ] Update context-handoff reality documentation and skill guidance for the
  compact seed, recovery fallback, lifecycle ordering, and non-mux design
  boundary.
- [ ] Bump context-handoff in its plugin manifest and marketplace entry.
- [ ] Bump agent-worktrees in its plugin manifest, runtime package, marketplace
  entry, and marketplace metadata version.

## Validation Plan

- [ ] Agent-worktrees tests cover the public session-binding query and retain
  existing Windows/POSIX ancestry coverage; context-handoff tests cover query
  consumption, environment fast-path validation, and no-mux degradation.
- [ ] Extension and standalone CLI tests prove byte-equivalent metadata and seed
  generation from the shared handoff core.
- [ ] Mixed-version tests prove older version-1 handoff records without the new
  identity fields remain consumable and never trigger unverified retirement.
- [ ] Seed tests assert a bounded three-part shape, stable auto-title lead,
  a maximum length of 1024 characters, exact task/file recovery commands, and
  no embedded full handoff payload.
- [ ] Fallback-output tests assert an explanatory user instruction plus a
  clearly delimited copyable block.
- [ ] No-mux tests prove the claimable task/file and record-first pointer are
  still created before the manual fallback is returned.
- [ ] Consume/cutover tests prove bind/head/lineage/title operations precede
  predecessor retirement, mid-sequence retries converge, and failures leave the
  predecessor alive.
- [ ] Task-backed retry tests fail between each checkpointed step and prove the
  same successor can converge without replaying the one-time task payload.
- [ ] Retirement tests prove stale or PID-reused predecessors are never killed.
- [ ] Title tests prove the continuing work-stream title is applied through
  agent-worktrees before retirement.
- [ ] Existing retry, one-time consumption, task-backed, file-backed,
  standalone, and cross-platform context-handoff tests remain green.
- [ ] Targeted agent-worktrees session/cutover suites and the context-handoff
  suite pass.
- [ ] Plugin version, documentation, marketplace, and repository consistency
  guards pass.

## Proposal

Proceed in one coherent PR with agent-worktrees exposing its existing
session-binding authority and context-handoff consuming that query through one
shared SDK-free core. Keep the stored handoff as the durable source of detail,
make the successor seed a bounded locator, checkpoint task-backed cutover
progress before one-time consumption, and use only ground-layer lifecycle APIs
for binding, succession, head, title, and verified retirement.

## Journal

### 2026-09-01 — Kickoff
- Claimed #1630 after checking the existing handoff and lifecycle issue set.
- Reconciled the work to Agent Fabric §Features/delegate-and-hand-off and the
  primitives-below/orchestration-above pattern.
- Scoped automatic non-mux terminal spawning to #1632; this effort will define
  and test the safe contract for that later delivery.
- Incorporated plan review: reuse the ground layer's existing ancestry resolver,
  describe only the residual consume/cutover gaps, include both plugin release
  paths, remove private worktree identifiers, and require retry convergence.
- Incorporated the second review: unify the extension and CLI on one handoff
  core, add a durable task-backed cutover checkpoint, require creation-stamped
  PID verification and metadata compatibility, and set a measurable seed budget.
