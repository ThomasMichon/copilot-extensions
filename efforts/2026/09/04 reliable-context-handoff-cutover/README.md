# Reliable Context Handoff Cutover

- **Slug:** `reliable-context-handoff-cutover`
- **Repo:** copilot-extensions
- **Branch(es):** Isolated Windows worktree; one PR.
- **Created:** 2026-09-01
- **Status:** Done
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
recovery locators, non-live fallback output clearly tells users what to invoke,
and successful consumption establishes the successor before retiring the
predecessor. Design and test the non-mux terminal-launch contract, but leave
automatic non-mux terminal spawning to a follow-up implementation.

## Plan

### Phase 1 — Resolve execution identity
- [x] Make `handoff-core.mjs` the single SDK-free implementation used by both
  `extension.mjs` and `handoff-cli.mjs`, deleting duplicated storage, metadata,
  seed, and consume/cutover helpers from the extension.
- [x] Expose agent-worktrees' existing session-to-process-to-mux binding as a
  bounded `session-binding --session-id <id> --json` query without duplicating
  process ancestry logic in context-handoff.
- [x] Have context-handoff use that query to record the invoking Copilot process,
  process-creation identity, pane, and mux identity in stored metadata, with
  inherited environment variables retained only as a validated fast path.
- [x] Keep metadata additions backward-compatible: old records with missing new
  fields still load, while retirement requiring unavailable proof degrades to a
  safe manual cleanup rather than guessing.
- [x] Keep standalone operation graceful when agent-worktrees, a mux, or a
  session-state record is unavailable.

### Phase 2 — Compact and recoverable successor seeds
- [x] Replace variable-length inline orchestration prose with the stable
  three-part seed: concise task summary, one `/consume-handoff`
  recommendation, and one opaque task/file recovery locator.
- [x] Keep executable recovery procedures in the payload-local skill and CLI
  rather than embedding source, paths, or shell-sensitive commands in argv.
- [x] Enforce a 200-character seed budget in `cutover-seed.mjs` and a
  single-line, selection-safe form.
- [x] Make the non-live fallback explicitly user-facing and clearly delimit the
  exact prompt or command the user must copy.
- [x] Preserve creation of the claimable task/file record and its record-first
  handoff pointer even when no live cutover is possible.

### Phase 3 — Close consume and cutover gaps
- [x] Persist a worktree-state cutover checkpoint before task-backed consumption,
  carrying the handoff token, predecessor identity, and completed-step markers
  so retry can converge after the one-time task payload has been claimed.
- [x] Apply the existing bind/head/succession primitives consistently to both
  task-backed and file-backed consumption before predecessor retirement.
- [x] Update the mux title from the continuing work stream through the owning
  agent-worktrees lifecycle API rather than calling a mux directly.
- [x] Make the bind, lineage, head, title, and retirement sequence idempotently
  re-runnable after a mid-sequence failure or already-consumed retry.
- [x] Retire only the recorded and verified predecessor process/pane after
  successor pickup, including process-creation identity checks that prevent a
  reused or stale PID from being terminated, while preserving the predecessor
  whenever any prerequisite fails.

### Phase 4 — Non-mux launch contract
- [x] Document the #1632 terminal-launch protocol: persist first, record
  predecessor PID plus creation identity and expected command shape, launch with
  a prompt file or equivalent lossless transport, then let consumption terminate
  only the verified predecessor.
- [x] Add contract-level tests for the proposed Windows and POSIX boundaries
  without enabling automatic non-mux spawning in this delivery.
- [x] Deferred to `#1632`: implement automatic non-mux terminal spawning.

### Phase 5 — Documentation and release
- [x] Update context-handoff reality documentation and skill guidance for the
  compact seed, recovery fallback, lifecycle ordering, and non-mux design
  boundary.
- [x] Bump context-handoff in its plugin manifest and marketplace entry.
- [x] Bump agent-worktrees in its plugin manifest, runtime package, marketplace
  entry, and marketplace metadata version.

### Phase 6 — Prompt-first startup and efficiency evaluation
- [x] Preserve the runtime lifecycle fact that a successor session does not
  exist until the compact initial prompt is submitted.
- [x] Carry the target worktree and handoff token into successor launch, then
  have sessionStart associate the resulting real session as a candidate without
  moving the predecessor head.
- [x] Make explicit `/consume-handoff` acknowledgement perform takeover with the
  minimum normal-path ground-layer calls: atomic bind/succession/head, title,
  then verified retire.
- [x] Extend the Tier-P context-handoff clean-room scenario with seed
  character/token, one-turn/one-tool budget, and payload-fidelity metrics.
- [x] Add an identity-free, opt-in Tier-E context-handoff eval with fixtures,
  post-check evidence, takeover/retire-or-preserve timing, tool/turn metrics,
  fidelity hashes, and lifecycle assertions.

### Phase 7 — Extension-free payload fallback parity
- [x] Keep context-handoff payload-only: no installer, venv, service, runtime,
  or PATH binstub.
- [x] Add exact, attributable plugin-root resolution to the skill for POSIX and
  PowerShell, preferring `COPILOT_PLUGIN_ROOT` and verifying fallback manifests.
- [x] Make the payload-local Node CLI cover facts, save, cutover/continue,
  task/file consume plus acknowledgement/takeover, retry, and manual fallback.
- [x] Keep extension and CLI storage/lifecycle/retry behavior on the shared
  SDK-free core.

### Phase 8 — Code-review hardening
- [x] Route task and file recovery locators through payload-local
  `handoff-cli.mjs consume --locator` so recovery includes checkpoint,
  acknowledgement, takeover, and retirement.
- [x] Require structured agent-dispatch status, owner, and
  `owner_session_id` proof before a task retry resumes lifecycle.
- [x] Replace embedded absolute plugin paths with a bounded ASCII-safe canonical
  payload resolver that preserves Unicode installation paths.
- [x] Restrict fallback discovery to exact `COPILOT_PLUGIN_ROOT` or the
  provenance-verified `context-handoff@copilot-extensions` payload.
- [x] Derive prompt, turn, and exact consume-tool counts from agent-bridge
  structured result/turn evidence, with false-positive fixtures.

### Phase 9 — Second-review lifecycle and evidence corrections
- [x] Expand actual agent-bridge turn references into `turns.jsonl` and score
  production `turn.prompt` plus exact structured `tool_calls`.
- [x] Add live-shaped fixture coverage proving one prompt/turn/consume call
  passes while duplicate calls or extra turns/prompts fail.
- [x] Keep deferred task handoffs owned after canonical `/consume-handoff`;
  inject the explicit completion command and preserve the checkpoint for crash
  recovery.

### Phase 10 — Final-review transport, process, and fidelity hardening
- [x] Escape Windows `%...%` sequences before cmd dispatch so titles and seeds
  survive without environment expansion.
- [x] Bind verification and termination to one Windows process handle or POSIX
  pidfd for every parent/child; fail closed when the atomic primitive is absent.
- [x] Score exact production `turn.prompt` equality and full byte-for-byte
  payload presence in structured consume output, with wrong-seed and truncation
  false-positive fixtures.

### Phase 11 — Snapshot identity and fresh eval evidence
- [x] Capture Copilot descendant start tokens in the original process-table
  snapshot and require that identity at the bound terminate handle/pidfd.
- [x] Clear root and `run-*` transcript/structured capture artifacts before
  every clean-room drive and advertise only evidence generated by that run.
- [x] Add child-PID-reuse and stale-evidence/capture-failure regressions.

### Phase 12 — Deployed launch regression and strong takeover proof
- [x] Correct successor launch so the mux child resolves the deployed Copilot
  executable without depending on ambient or stale `PATH`.
- [x] Replace provisional launch-receipt acceptance with proof from the
  successor's real session and first submitted user message.
- [x] Re-run a fresh deployed live cutover and prove exact seed submission,
  explicit task consumption, authoritative succession, and predecessor
  retirement while the successor remains live.

## Validation Plan

- [x] Agent-worktrees tests cover the public session-binding query and retain
  existing Windows/POSIX ancestry coverage; context-handoff tests cover query
  consumption, environment fast-path validation, and no-mux degradation.
- [x] Extension and standalone CLI tests prove byte-equivalent metadata and seed
  generation from the shared handoff core.
- [x] Mixed-version tests prove older version-1 handoff records without the new
  identity fields remain consumable and never trigger unverified retirement.
- [x] Seed tests assert a bounded three-part shape, stable auto-title lead,
  a maximum length of 200 characters, exact task/file recovery locators, no
  executable source, and no embedded full handoff payload.
- [x] Fallback-output tests assert an explanatory user instruction plus a
  clearly delimited copyable block.
- [x] No-mux tests prove the claimable task/file and record-first pointer are
  still created before the manual fallback is returned.
- [x] Consume/cutover tests prove bind/head/lineage/title operations precede
  predecessor retirement, mid-sequence retries converge, and failures leave the
  predecessor alive.
- [x] Task-backed retry tests fail between each checkpointed step and prove the
  same successor can converge without replaying the one-time task payload.
- [x] Retirement tests prove stale or PID-reused predecessors are never killed.
- [x] Descendant retirement tests prove the expected creation token comes from
  the original discovery snapshot, not a later lookup of the numeric PID.
- [x] Title tests prove the continuing work-stream title is applied through
  agent-worktrees before retirement.
- [x] Existing retry, one-time consumption, task-backed, file-backed,
  standalone, and cross-platform context-handoff tests remain green.
- [x] Targeted agent-worktrees session/cutover suites and the context-handoff
  suite pass.
- [x] Plugin version, documentation, marketplace, and repository consistency
  guards pass.
- [x] Startup-candidate tests prove no binding occurs before initial submit,
  sessionStart association leaves the predecessor head intact, and explicit
  acknowledgement moves the head.
- [x] Efficiency tests prove the normal acknowledgement path uses one atomic
  bind/head result plus title and retire, without redundant lineage/head
  round-trips.
- [x] The deterministic clean-room seed/eval fixtures emit bounded token,
  turn/tool, timing, fidelity, and lifecycle metrics; the Tier-E live run remains
  opt-in and identity-free.
- [x] Reused clean-room result directories clear all prior structured turn
  evidence before driving, and absent fresh capture fails closed.
- [x] CLI parity tests prove the payload-local command inventory, shared-core
  delegation, verified plugin-root guidance, and absence of runtime/install
  surfaces.
- [x] A fresh deployed successor has a distinct session ID and its first
  `user.message` exactly equals the expected one-line ASCII seed, remains within
  200 characters, contains exactly three semantic parts, and carries the opaque
  recovery locator.
- [x] Authoritative worktree state proves task consumption links the baton to
  the successor, makes it the live head with the continuing title, and only
  then retires the predecessor pane, process, binding, and in-use lock.

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

### 2026-09-01 — Implementation complete
- Added the bounded `session-binding` query over agent-worktrees' existing
  session-lock/process-ancestry authority, including process creation tokens.
- Unified extension and CLI storage, metadata, seed, consume, checkpoint, and
  cutover behavior in the SDK-free handoff core.
- Replaced launch/paste prose with the bounded three-part locator, explicit
  fallback block, and payload-local recovery path.
- Added task-backed pre-consume checkpoints and idempotent
  bind/succession/head/title/verified-retire sequencing, including retries after
  every checkpointed lifecycle boundary and safe handling of legacy metadata.
- Documented the deferred non-mux launcher contract for #1632 and bumped both
  plugin release surfaces.
- Targeted suites, context-handoff's full Python and Node suites, agent-worktrees
  guards, lint, install/version/docs/payload guards, and installer-readiness
  passed. A broad agent-worktrees portfolio run passed the changed handoff/session
  coverage and several large batches; unrelated pre-existing
  `test_provision_hook_cwd.py` expectations still fail in a later batch.

### 2026-09-02 — Operator-directed lifecycle and efficiency extension
- Re-opened the effort to preserve prompt-before-session ordering explicitly:
  launch carries a pending token, sessionStart records only a successor
  candidate after the initial prompt creates the session, and takeover remains
  an explicit consume/acknowledge action.
- Collapsed the normal takeover path to the atomic token bind/head result,
  followed by title and verified retire, while preserving compatibility
  fallbacks for older metadata.
- Added deterministic clean-room efficiency/fidelity metrics and an opt-in,
  identity-free Tier-E scenario for actual turn/tool/timing/fidelity evidence.

### 2026-09-02 — Post-deploy fallback correction
- Live deployment verified session-to-mux recovery for the active Copilot
  session, but the extension-free `facts` command exposed a stale PATH-based
  project-wrapper resolution.
- Changed SDK-free system calls to resolve provenance-checked sibling payload
  commands inside the same marketplace installation cell, so both extension and
  CLI fallback avoid ambient PATH and repository-local wrapper ambiguity.
- Final deterministic probe after review hardening: 736 initial-seed characters,
  184 estimated tokens, exactly three parts, one submitted-prompt / one-turn /
  one-consume-tool target, and byte-identical payload SHA-256. The increase buys
  an ASCII-safe, provenance-checking payload CLI resolver that works under
  Unicode home/install paths. The Tier-E live drive remains deliberately opt-in
  and was not invoked during this implementation pass.

### 2026-09-02 — Payload-local fallback acceptance
- Expanded the extension-free Node CLI to expose basic facts, task/file
  acknowledgement and takeover, and shared-core retry in addition to
  save/cutover/continue.
- Replaced PATH assumptions in skill guidance with exact plugin-root-relative
  POSIX and PowerShell invocations that verify the `context-handoff` manifest.
- Added parity and payload-only tests; no installer/runtime surface was added.

### 2026-09-02 — Code-review findings resolved
- Recovery now always re-enters the payload CLI, including task-backed seeds.
- Task retry refuses free-form error inference; only structured
  state/owner/session proof permits convergence, while outages preserve the
  predecessor.
- Unicode install paths are resolved at runtime from the canonical marketplace
  root without entering the ASCII seed.
- Skill examples no longer scan arbitrary marketplaces with a same-named
  plugin.
- Tier-E metrics now fail closed unless agent-bridge provides one structured
  submitted prompt, one acknowledgement turn, and exactly one consume tool call.

### 2026-09-02 — Second review resolved
- Corrected the eval fixture to the production agent-bridge turn schema
  (`turn.prompt`, `turn.tool_calls`) and extended both host runners to expand all
  actual turn references into structured JSONL evidence.
- Removed immediate complete/yield behavior from deferred canonical consume.
  The successor receives the exact completion command, while task ownership and
  the durable checkpoint survive delivery failure or successor crash.

### 2026-09-02 — Final review resolved
- Windows CLI quoting now protects percent-delimited seed/title text from
  `cmd.exe` environment expansion.
- Reaping now terminates through the same creation-verified Windows handle or a
  POSIX pidfd. Numeric-PID fallback is forbidden for verified handoff retirement,
  so an unavailable atomic primitive or reused PID is spared.
- Tier-E evidence now compares the actual structured prompt to the expected
  seed and proves the completed consume result contains the entire stored
  payload, with explicit negative fixtures for extra turns/prompts/tool calls,
  wrong seeds, and truncated payloads.

### 2026-09-02 — Narrow review resolved
- Descendant expected creation tokens now travel from the same original process
  snapshot that established ancestry; a reused child PID cannot acquire a new
  expected token before identity-bound termination.
- Both clean-room runners purge root and reused-run capture files before each
  drive, write fresh turn JSONL instead of appending, and omit missing capture
  artifacts from run metadata. Fixture regressions prove failed fresh capture
  cannot score stale structured evidence.

### 2026-09-02 — Architecture and hardening consolidation
- Reconciled the shipped lifecycle against the root architecture, plugin
  reality documentation, Agent Fabric vision, and patterns invariants. This is
  documentation of landed behavior rather than a new vision extension.
- Added a focused handoff lifecycle pattern covering persist-first storage,
  bounded locator seeds, prompt-before-session candidate association,
  successor-acknowledged takeover, checkpointed retry, identity-prevalidated mux
  retirement, and creation-bound orphan reaping.
- Corrected the stale Windows batch-transport description. Sibling commands are
  resolved within the same provenance-checked marketplace installation cell;
  their authoritative resolver locates/provisions the runtime, and exact argv
  runs through isolated UTF-8 Python with import-path overrides removed.
- Made explicit that active efforts and knowledge repositories enrich the
  continuation shape but do not own handoff storage or lifecycle.
- Recorded ThomasMichon/copilot-extensions#1729 as the public coordination
  issue for this architecture documentation pass.

### 2026-09-02 — Selection-safe recovery locator
- A real terminal handoff showed that the inline Node resolver, while one
  logical argv-safe line, wrapped across bordered terminal rows and copied with
  gutters/newlines inserted into identifiers and source.
- Replaced executable recovery source with a short opaque `task:<id>` or
  `file:<id>` locator and reduced the seed budget from 1024 to 200 characters.
- Added payload-CLI `consume --locator`; task locators preserve deferred
  completion automatically, while the skill retains provenance-checked CLI
  resolution when extension tools are unavailable.
- Updated deterministic and Tier-E checks to reject executable source in the
  seed and recorded ThomasMichon/copilot-extensions#1757 as the public
  coordination issue.

### 2026-09-02 — Deployed compact takeover verified
- A fresh live cutover on deployed `context-handoff` `0.1.0-dev67` completed
  the full successor-driven path. The startup seed was one 143-character ASCII
  line with exactly the task, `/consume-handoff` recommendation, and opaque task
  locator; it contained no executable source or installed path.
- The task-backed checkpoint preserved the complete standalone baton and its
  Windows metadata with 44 CRLF boundaries, no lone CR/LF, no truncation, and
  the full ordered successor roster and completion gates.
- Authoritative lifecycle evidence showed one-way ordering: task consumption,
  successor bind, succession link, head verification, title update, then
  identity-verified predecessor retirement. The predecessor pane, process
  binding, and session lock were gone while the successor remained live and
  head.
- A preceding invalid trial reinforced the fail-closed boundaries: the
  predecessor continued after launching the successor and consumed its own
  one-time baton, so the resulting checkpoint could not count as takeover
  evidence and same-state retry correctly refused to replay the spent task. A
  fresh baton was required; the canonical rule remains that the predecessor
  stops immediately after cutover and only the actual successor consumes.

### 2026-09-04 — Deployed launch regression corrected and revalidated
- Reopened the effort after the earlier acceptance proved too weak: the
  transport receipt reported `seeded=true`, but later successor panes exited
  before Copilot created a session or submitted the initial prompt.
- Traced the regression to ambient/stale launch resolution rather than removal
  of Copilot's `--interactive` flag. #1972 now launches through the deployed,
  provenance-owned Copilot command instead of trusting ambient `PATH`.
- Deployed `agent-worktrees` `1.5.5-dev1` and `context-handoff`
  `0.1.1-dev1`, then performed a fresh task-backed live cutover.
- The successor created a distinct real session before consumption. Its first
  submitted user message exactly matched the expected 148-character ASCII
  seed: one line, exactly three semantic parts, and the opaque task recovery
  locator.
- Explicit consumption linked the baton to that session, established reciprocal
  succession, made the successor the live worktree head, and applied the
  continuing title before predecessor retirement. The predecessor pane,
  process, binding, and in-use lock were gone while the successor remained live.
- This stronger proof supersedes the provisional wrapper-receipt criterion:
  future cutover validation requires actual session and first-turn evidence,
  not a spawned pane, command line, or `seeded=true` transport receipt.
