# context-handoff overhaul

- **Slug:** `context-handoff-overhaul`
- **Repo:** copilot-extensions (the `context-handoff`, `agent-worktrees`, and
  `agent-bridge` plugins)
- **Branch(es):** `effort/context-handoff-overhaul` (Phase 0, merged #2593),
  `effort/context-handoff-overhaul-phase1` (Phase 1, merged #2643),
  `effort/context-handoff-overhaul-phase2` (Phase 2, merged #2663),
  `effort/context-handoff-overhaul-phase3` (Phase 3 slice 1, merged #2669;
  further slices continue on new branches)
- **Created:** 2026-09-13
- **Status:** Active
- **Umbrella issue:** #2594
- **Sub-issues:** #2595 · #2596 · #2597 · #2598 · #2599

## Guiding Intent

Make context-window pressure a non-event for both the agent and the operator.
An agent should be able to work as thoroughly as a task deserves — never
truncating its own diligence for fear of running out of room — because a
reliable, host-agnostic handoff mechanism means it can always continue in a
successor instead. This effort closes the gap between that standing intent
(now stated in `visions/plugins/context-handoff`) and the current
implementation, without discarding the substantial, already-correct work three
prior/in-flight efforts already landed.

## Context

Kicked off by an operator request for a from-scratch architecture pass on
`context-handoff`: an adversarial vision re-embodiment (blind isolated design
derivation vs. reality, per the `envisioning` skill's generativity check),
followed by a redesign addressing three named platform constraints. See
[`redesign.md`](redesign.md) for the full method, the isolated derivation's
transcript reference, and the three-bin diff (already-correct / genuine blind
spot / vision-ahead delta) this effort's Plan is carved from.

This effort's kickoff also **migrated three related effort docs into this
repo** from a private facility repo, where they had been
authored despite being entirely about these plugins (tracked via this repo's
own issues):

- [`efforts/active/handoff-live-cutover`](../handoff-live-cutover/README.md) —
  the interactive-CLI/mux automatic live-cutover mechanism this effort builds
  on and closes the remaining Phase 3 items of.
- [`efforts/active/handoff-cutover-lifecycle-journal`](../handoff-cutover-lifecycle-journal/README.md) —
  the observability/lineage layer this effort depends on for goals 6/7.
- [`efforts/active/handoff-cutover-reload-robustness`](../handoff-cutover-reload-robustness/README.md) —
  the `consume_handoff` startup-race mitigation whose bash-first seed pattern
  this effort's force-tier (§4.1 of the redesign) must reuse, not bypass.
- `efforts/2026/08/02 bridge-native-context-handoff` (archived) — the
  bridge-hosted ACP analogue; prior art for goal 3's "worktree-scoped logical
  agent" framing.

This effort does not re-litigate or duplicate any of the above — it closes
the vision-ahead delta that remains *after* their work is accounted for.

## Request

Verbatim from the operator:

> Let's start an effort to overhaul our context-handoff plugin and flow, yet
> again. Let's start by doing an adversarial vision re-embodiment, and then
> compare/contrast architecture between the new proposal and reality. Let's
> also audit where and how we track state and relationships for worktrees and
> sessions, so we can ensure that all parties have appropriate information
> about session lineage.
>
> The defining challenges are:
> 1. Copilot CLI provides no built-in tool for the agent to invoke /new or
>    /clear automatically on behalf of the user. We can either tell the user
>    to do it (always a good backstop option), or we can just create new
>    Copilot CLI processes using `--interactive` and the successor prompt.
> 2. Copilot CLI provides no way to "end" the current session via tool,
>    either. In fact, the only "end session" concept seems to be the user
>    just using Ctrl+C twice (within a throttle limit) to "gracefully"
>    terminate the Copilot process. Simply killing the process directly
>    leaves a stale lock file in the session folder. Even an Extension
>    doesn't seem to be able to mess with the session or process state.
> 3. While copilot-extensions offers two ways to control worktree agents
>    (Worktree Manager and agent-bridge), we want to be open to other systems
>    being available to support handoffs. So we want to keep the particular
>    hosting mechanism for Copilot out of context-handoff itself. While we
>    can drop files into a folder, we need a way to ensure that the Copilot
>    host sees the file drop and can trigger the cutover. Right now, we have
>    a light IPC that depends on agent-bridge (or ideally we should). We can
>    also consider file-watchers, potentially, as an alternative, though we
>    need to be careful with those.
>
> The goals of context-handoff are:
> 1. Monitor usage of context as the session progresses. At 50% or so, warn
>    the agent about the need to prepare for a handoff. At 65% or so, nudge
>    the agent to hand off at soonest opportunity. At 80%, force-stop the
>    agent and hard handoff, somehow.
> 2. The handoff should provide guidance for the next session to continue the
>    open items and next steps identified from the predecessor session.
>    Whatever the overarching effort was, or the user's initial stated goal,
>    should be carried through if incomplete, and outstanding flows like open
>    processes, watches, intervals, scheduled prompts, etc. need to be
>    carried over so the successor session can continue monitoring seamlessly.
>    PRs, open claims, worktrees, agent bridges, and more all need to be
>    managed by the successor (and future successors). The mandate to
>    perform subsequent handoffs for future open items should *also* be
>    carried through, so an agent worktree will perform multiple handoffs in
>    succession until the overall effort is done. At the start of new
>    worktrees, agents should be made aware of the handoff concept, and that
>    it allows them to drive "without bound" of session context pressure. An
>    agent can feel free to be thorough, knowing that it can just hand off.
> 3. The handoff should be automatic and "seamless", ensuring the successor
>    agent can immediately get to work, and the predecessor agent shuts down
>    without making subsequent modifications. The user should see the new
>    agent take focus (and be visible) and the old agent UX should disappear.
>    In Mux, this means putting the new pane into focus and the old one in
>    the background. In agent-bridge, this means subscribing to and
>    controlling the new session host and shutting down the old one,
>    notifying the caller of the cutover but streaming back events as though
>    it were continuous. Caller agents should perceive it as one "giant
>    session" because callers drive a *worktree*-scoped child agent, and
>    ideally not just a *session*-scoped one.
> 4. context-handoff should be configurable by the user. Using config in the
>    knowledge repo or user-level, users can disable the mechanic fully,
>    enforce manual-only cutovers, change the thresholds, switch to absolute
>    tokens, and more.
> 5. context-handoff needs to provide clear indicators to the user what is
>    going on, during the conversation and when performing the handoff. The
>    agent must always tell the user that a cutover is impending, and always
>    provide instructions for how to perform the handoff manually ("Paste
>    the following after /new or /clear:").
> 6. The lineage of handoffs must be clear in the tracked worktree metadata
>    and session-state history from Copilot. It should be possible to audit
>    the succession of handoffs, both while a worktree is active and even
>    after it has been reaped. This will ensure we can track the health of
>    the handoff system.
> 7. Every session should ideally only have one successor session. Once a
>    successor session is created, the priority should always be to get it
>    in front of the user so the user can continue driving. We need recovery
>    mechanisms and live-auditing flows to ensure the system is healthy. We
>    need logging and possibly a diagnostic watchdog.

## Plan

### Phase 0 — Vision + redesign (this kickoff)
- [x] Isolated adversarial derivation of a context-handoff design from goals/
  constraints alone, no repo access (transcript in Journal).
- [x] Reality audit: current plugin source, four sibling visions, four related
  efforts.
- [x] Three-bin diff (already-correct / genuine blind spot / vision-ahead) —
  see [`redesign.md`](redesign.md).
- [x] Author `visions/plugins/context-handoff/README.md`, folding genuine
  blind spots back as should-be intent; link it from `agent-fabric` and the
  visions index.
- [x] Migrate the three related private-facility-repo effort docs into this
  repo (separate PR, this-repo#2593 — the private repo's own issue for this
  migration is not public).
- [x] File the umbrella issue + sub-issues for Phases 1-5 below.
  - Filed: umbrella #2594; sub-issues #2595 (force tier), #2596 (continuity
    content), #2597 (coordinator fallback), #2598 (configurability), #2599
    (lineage/diagnostics coordination).
- [x] Submit this effort for review (PR, auto-merge) before executing Phase 1
  (merged as #2593, along with an unrelated bundled module-size-baseline fix
  needed to unblock CI for this and every other PR — tracked separately as
  issue #2614).

### Phase 1 — Force-tier (Goal 1)
- [x] Add a third (`force`) threshold tier to `thresholds.mjs` alongside the
  existing soft/hard tiers; keep percentages configurable, not hard-coded.
  Default 79% (preserves the same pre-compaction margin the old hard-tier
  ceiling guaranteed); configurable via `.context-handoff/config.yaml`'s new
  `force_percent` key.
- [x] On crossing `force`: auto-finalize handoff content from whatever draft
  exists, auto-call the bash-first seed path (per
  `handoff-cutover-reload-robustness`), and block further mutating tool calls
  for the remainder of the turn. Implemented as `autoForceHandoff()` in
  `extension.mjs`: reuses `collectHandoffData`/`formatHandoffMarkdown` (no
  LLM composition needed) and calls `handoff-core.mjs`'s `triggerHandoff`
  directly (in-process, not a tool call) -- the same store/trigger path
  `save_handoff_prompt`/`trigger_handoff` use. Tool-call blocking reuses
  `onPermissionRequest` (the only tool-call gate this runtime still honors --
  the SDK's newer `hooks.onPreToolUse` hard-fails here per the file's own
  "SDK hook callbacks are no longer supported" note) with a read-only
  allowlist (kind `read`/`url` always; `shell`/`mcp` only when the SDK's own
  per-request `readOnly` signal says so) extracted to the unit-tested
  `force-tier.mjs`. Lifted only by a successful compaction, so the operator
  can keep working in the same session if they decline the auto-triggered
  successor; at most one auto-handoff per session (`state.handoffGenerated`
  never resets).
- [x] Ensure the force path never routes through `consume_handoff`-as-first-
  tool-call on the successor side (the exact race
  `handoff-cutover-reload-robustness` already mitigated). Satisfied by
  construction: `autoForceHandoff` calls the identical `triggerHandoff()`
  used by the CLI/tool paths, which already produces a bash-first seed.
- [x] Submitted for review as PR #2643; merged.

### Phase 2 — Continuity content (Goal 2)
- [x] Extend the handoff content schema (`generate_handoff_prompt` /
  `save_handoff_prompt`) with an explicit, named class for outstanding
  background flows (watches, polls, scheduled/recurring prompts) and external
  state the predecessor was responsible for (open PRs, held claims/leases,
  peer-agent coordination) — never silently dropped, always either resumable
  or surfaced as an explicit open item. Implemented as a new "Outstanding
  Background Flows & External State" section in both handoff-template.md
  shapes, enforced via `generate_handoff_prompt`'s returned instructions and
  the skill's Rules; the force-tier auto-draft path (no agent composition)
  emits an explicit not-captured open item instead of silently omitting the
  section.
- [x] Make the "handoff mandate" (perpetuation + fresh-session awareness) a
  standing, explicitly-carried element of every seed — not incidental prose.
  Added `HANDOFF_MECHANISM_AWARENESS` (`cutover-seed.mjs`, alongside the
  existing `CONTINUATION_DIRECTIVE`) covering both aspects in one shared
  constant; threaded into every delivered brief (`formatConsumeResult`,
  `buildResumePrompt`) and delivered once, on a session's first turn,
  regardless of whether that session began from a handoff (extension.mjs's
  `awarenessNudgeSent`/`pendingAwareness` first-turn nudge on `user.message`,
  queued and sent on the next `session.idle`).
- [x] Update the `context-handoff` skill so a freshly-started (non-handoff)
  session is told the mechanism exists from its first turn. Added an "Every
  session knows this exists" section near the top of SKILL.md, pointing at
  the extension's first-turn nudge as the structural (not prose-only)
  guarantee.

### Phase 3 — Host-agnostic reliability (Challenge 3)
- [x] Confirm `agent-worktrees handoff-cutover` supports a genuinely headless
  (no-mux) invocation, or build a non-mux launch primitive if it doesn't --
  open question flagged in `redesign.md` §4.3; must resolve before the
  coordinator fallback below can rely on it.
  **Confirmed neither existing path is genuinely mux-less:**
  `handoff-cutover` requires an already-live mux session for the target
  worktree (`sessions.has_mux_session`), and `embody` requires the mux
  binary itself to create a detached session on demand
  (`sessions.mux_new_session` shells to tmux/psmux unconditionally) -- so a
  host with no multiplexer at all (the coordinator-fallback case this phase
  exists for) has no working launch path today. Built the non-mux primitive:
  `sessions.headless_new_session()` (+ `sessions.mux_available()` to detect
  the no-mux-at-all case) spawns the launch command as a fully detached
  background process via `subprocess.Popen` (`DETACHED_PROCESS` + a new
  process group on Windows, `start_new_session` on POSIX -- the same shape
  `agent_dispatch`'s own coordinator autostart uses), with the seed passed
  as a native `-i <seed>` argument directly (no pane-wrapper argv-mangling
  to route around, unlike the mux path). Wired into `handoff-cutover` as an
  explicit opt-in `--headless` flag (bypasses the mux-session-required
  check; rejects anchor-mode cutover explicitly rather than silently doing
  the wrong thing) so existing mux-based callers are unaffected. Along the
  way, found and fixed a real pre-existing regression: the recent
  agent-worktrees module-size split (#2653/#2657/#2658) moved
  `mux_retire_pane`/`_mux_pane_alive` into `sessions_pane_retire.py`, but 5
  `TestMuxRetirePane` tests still monkeypatched the stale `sessions.*`
  re-export, silently no-opping the patch and leaving those tests either
  falsely green (by lucky "tmux not found -> OSError -> False" coincidence)
  or failing outright -- retargeted the monkeypatches to the module that
  actually owns the functions now.
- [x] Extend `agent-dispatch`'s coordinator to reconcile an unclaimed
  `proposed`/`handoff` task past a bounded window into a fallback headless
  successor launch, per `redesign.md` §4.3 (reusing already-supervised
  infrastructure rather than a new standing watchdog). **PR #2808.**
  **Design pivot mid-slice (operator steer):** agent-dispatch is the
  *holder* of the handoff task, not the *implementor* of the handoff
  mechanism -- the coordinator judges staleness and asks the associated
  bridge to replace the stale occupant in place; it never shells to
  `agent-worktrees handoff-cutover` itself. Implemented via agent-bridge's
  existing session-lifecycle `--reclaim` break-glass (the request-body flag
  and server guard bypass already existed from an earlier effort; only the
  `create` CLI plumbing was missing) -- added `agent-bridge create
  --reclaim`, threaded `bridge.spawn_worker(reclaim=...)`, a new
  `handoff_fallback_seed.py` (pure Python port of `cutover-seed.mjs`'s short
  seed contract), `queue_handoff_fallback.py`'s atomic single-attempt claim
  fence (never double-launches), and `coordinator.py`'s
  `_handoff_fallback_loop` (same supervised-cycle shape as
  `_gc_loop`/`_orphan_reap_loop`), gated **default-off**
  (`AGENT_DISPATCH_HANDOFF_FALLBACK`) with a 1h default grace window.
- [x] Evaluate a `userPromptSubmitted` hook that greps the successor's first
  submitted prompt for the expected handoff token, as a deterministic
  "pickup actually happened" signal feeding the lineage trace (complements,
  does not replace, Phase 3's coordinator fallback).
  **Built (not just evaluated):** the runtime has no separate
  `userPromptSubmitted` hook type -- the equivalent live signal is
  `extension.mjs`'s existing `session.on("user.message", ...)` SDK event
  (already used for turn counting). Added a one-shot check there: on the
  session's first turn, `cutover-seed.mjs`'s new
  `extractRecoveryLocatorFromPrompt()` (pure regex grep, no LLM judgment)
  looks for the exact `Recovery: context-handoff <kind>:<id>` clause every
  cutover seed carries; on a match, `handoff-core.mjs`'s new
  `logHandoffPromptReceived()` best-effort logs a
  `context_handoff_prompt_received` event via `agent-worktrees
  activity-log` (a distinct event/field name from agent-worktrees' own
  numbered-handoff `handoff_token` space, to avoid any identity collision).
  The predecessor's existing `pickupSignals()` gained a matching
  `worktreePromptReceived()` reader, folded in as a new `"prompt-received"`
  entry in the `via` array -- a stronger, more direct proof of pickup than
  the existing `spawnInFlight` marker (which only proves a process was
  created, not that Copilot itself received the seed), and immune to the
  skill-load race a mid-session reload can cause (see this effort's own
  awareness-nudge incident). 13 new tests (7 pure-function +
  `logHandoffPromptReceived` + an end-to-end `triggerHandoff` pickup test).
- [x] Close `handoff-live-cutover`'s remaining Phase 3 items (armed
  `session.idle` retirement finish, non-mux/successor-start-failure
  fallback) now that the effort lives in this repo.
  **Reconciled, not rebuilt:** re-verified against current code and found
  two of the three items were already satisfied -- just by a different,
  deliberately-superseded-in-place mechanism (agent-bridge +
  agent-worktrees' resident status-monitor own mux/retire, not the
  extension, per `context-handoff`'s own process-manager-agnostic
  boundary). The third (non-mux/boot-failure fallback) was closed by this
  effort's own Phase 3 slice 1 (`--headless`) plus the pre-existing
  `manualFallbackInstructions` path. Updated that effort's Phase 3
  checklist + journal in place with per-item provenance notes rather than
  leaving it stale. Only its private, non-resolvable stretch validation
  (a live tmux pass) remains open there, unchanged.

### Phase 4 — Configurability (Goal 4)
- [ ] Add a `mode` config key (`auto` / `manual-only` / `off`) to
  `config.mjs` / `.context-handoff/config.yaml`.
- [ ] Support absolute-token thresholds as an alternative to percentages.
- [ ] Add a user-level config layer beneath the existing repo-level layer.

### Phase 5 — Lineage/diagnostics closure (Goals 6, 7)
- [ ] Coordinate with (not duplicate) `handoff-cutover-lifecycle-journal`'s
  remaining open items: populate universal linkage fields, Stage 1
  predecessor framing, the `agent-worktrees handoff-trace` renderer, and
  feeding `health.find_orphaned_handoffs()`.
- [ ] Confirm the vision's "single-successor discipline with recovery"
  Behavior is realized once lifecycle-journal's Phase 3/4 land; file any
  residual gap as a narrowly-scoped follow-up rather than re-designing.

### Phase 6 — Mux pane lifecycle primitives, isolated (Challenge 4)
> Added 2026-09-17 after live production symptoms (panes spawning over each
> other or never becoming foreground, stuck/false-positive pickup signals) on
> the operator's own machine. See `redesign.md` §6 for the reality audit and
> three-bin diff. A sub-division of this effort, not a new one, per the
> operator's explicit direction.
- [ ] **`pane_create` primitive.** Extract + generalize `mux_new_window()`'s
  receipt-based bootstrap-confirmation shape into a standalone,
  independently-testable primitive: resolve the worktree's mux server, log
  intent via `activity.log_event` *before* acting (not buried in a caller),
  spawn via a pane-bootstrap script that confirms landing before exec'ing
  the real payload (generalized beyond today's `initial_prompt`-only gate),
  and foreground the pane as part of the same call.
- [ ] **`pane_terminate` primitive.** Reuse the existing Ctrl-C escalation
  ladder shape, but confirm shutdown by reading the pane's live console
  output (`capture-pane`) for Copilot's own exit signature instead of only
  polling session/pane liveness; hard-kill the pane on a bounded (~30s)
  overall budget if the signature never appears; then clean up the stale
  session lock file the old process left behind. Collapses today's two
  divergent termination code paths
  (`graceful_quit_mux_session`+`restart_worktree_copilot` and
  `mux_retire_pane`) into one.
- [ ] **Isolated CLI harness for both primitives**, reachable independent of
  `handoff-cutover`, so each can be driven directly against a live mux
  server — closing the gap that made live mux-mutation testing unsafe from
  inside an attached session (`handoff-live-cutover`'s Phase 4 finding).
- [ ] Hermetic test coverage for both primitives (subprocess-mocked, matching
  the existing `test_handoff_cutover.py` convention) plus a manual
  live-validation runbook using the new harness.
- [ ] Rewire `handoff-cutover` (spawn + retire modes) and the Picker
  Stop/Take-over path to call the two hardened primitives instead of their
  current bespoke/duplicated logic.

## Validation Plan

- [ ] Force-tier: a live session artificially pushed past the force threshold
  auto-hands-off without further mutating tool calls landing after the
  threshold crossing. (Unit-level coverage landed: `thresholds.test.mjs`
  covers the new tier's math/validation, `force-tier.test.mjs` covers the
  read-only/mutating classification. Still open: an actual live-session
  run -- no local harness exercises the real `@github/copilot-sdk`
  connection `extension.mjs` needs, so this needs a real session or a
  clean-room scenario, not just unit tests, before checking this off.)
- [ ] Continuity content: a handoff seed for a session with an active
  background watch/poll/scheduled-prompt demonstrably carries a resumable
  reference to it, verified in the successor. (Unit-level coverage landed:
  `guidance.test.mjs` covers the schema section's presence in both template
  shapes, the shared `HANDOFF_MECHANISM_AWARENESS` constant's content and its
  wiring into every delivered brief, and the extension's first-turn nudge
  logic. Still open: an actual live session with a real background flow --
  same real-`@github/copilot-sdk`-connection gap as the Phase 1 item above,
  needs a real session or a clean-room scenario before checking this off.)
- [ ] Host-agnostic reliability: a handoff requested with no reachable
  mux/bridge host still produces a running successor within a bounded window
  via the coordinator fallback (or an unambiguous manual recipe if the
  coordinator itself is unreachable).
- [ ] Configurability: `mode=off` and `mode=manual-only` are honored end to
  end; an absolute-token threshold config overrides the percentage default.
- [ ] Lineage: `agent-worktrees handoff-trace` (once landed) reconstructs a
  full chain for a multi-hop handoff after every process in the chain has
  exited, on both a live worktree and one already reaped.
- [ ] Clean-room scenario updates: extend `context-handoff-cutover` and
  `context-handoff-eval` to cover the force-tier and the coordinator-fallback
  path.
- [ ] Mux pane lifecycle primitives: `pane_create` produces a confirmed,
  foregrounded pane against a live mux server via the new isolated harness
  (not just hermetic mocks); `pane_terminate` correctly distinguishes a
  genuine Copilot shutdown signature from a hung pane and falls back to a
  hard kill + lock-file cleanup within the ~30s budget when it does; both
  behaviors are exercised standalone, without going through the full
  handoff-cutover choreography.

## Proposal

_Pending — see [`redesign.md`](redesign.md) for the current level of design
detail; a fuller Proposal will fill in once Phase 0's issue-filing and review
gate land._

## Journal

### 2026-09-13 — Kickoff
- Operator requested a from-scratch overhaul, starting with an adversarial
  vision re-embodiment and a redesign against three named platform
  constraints.
- Ran the isolated derivation (a separate agent, no repo/search/web access,
  given only the seven goals + three constraints) — see this session's
  transcript for the full proposal (six named components: `HandoffCore`,
  `WorktreeLedger`, `HandoffCourier`, `HostAdapter`, `LineageAuditor`,
  `PolicyResolver`; notably converged independently on the same
  policy/hosting split reality already has).
- Audited reality: current plugin source, `agent-fabric` / `session-hosting`
  / `plugins/agent-bridge` / `plugins/agent-worktrees` visions, and the four
  related efforts (three migrated into this repo as part of this kickoff).
- Diffed the derivation against reality in three bins; wrote
  `visions/plugins/context-handoff/README.md` from the genuine blind spots
  plus the operator's seven goals; wrote `redesign.md` for the vision-ahead
  deltas and the explicit non-decisions (declined to build the derivation's
  "Courier" standing-watchdog process — extended `agent-dispatch`'s existing
  coordinator instead, per the `envisioning` skill's extend-before-regenerate
  bias).
- Next: file the umbrella issue + sub-issues, submit this effort for review.

### 2026-09-14 — Phase 0 PR merged; starting Phase 1
- PR #2593 (Phase 0 kickoff) merged after a review round and CI stabilization.
  Verifying/landing it surfaced and fixed a bundled, unrelated problem: the
  repo-wide module-size-baseline split (10 modules, tracked as #2614) that
  had been mechanically extracted into sibling modules without carrying
  their re-exports, breaking `agent-worktrees`/`worktree-manager` tests and
  the `agent_worktrees.__main__`/`tracking` test-facing attribute surface;
  also found and resolved a version-bump merge conflict against `main`
  (`agent-dispatch` had independently bumped past this branch's bump) and a
  CI trigger anomaly (the `pull_request` synchronize webhook silently
  stopped firing under heavy repo-wide Actions load; worked around via
  `workflow_dispatch` for interim validation until a fresh push re-triggered
  it normally).
- Continuing on a fresh branch (`effort/context-handoff-overhaul-phase1`,
  based on post-merge `main`) since the Phase 0 branch was merged and its
  remote ref deleted. Starting Phase 1 (force-tier).
- Implemented Phase 1 (force-tier): a third `force` threshold (79% default)
  in `thresholds.mjs`/`config.mjs`; `extension.mjs` auto-drafts, stores, and
  triggers a handoff on crossing it (reusing `collectHandoffData`/
  `formatHandoffMarkdown` and `handoff-core.mjs`'s `triggerHandoff` directly,
  in-process -- no LLM composition or tool call needed) and denies further
  mutating tool calls via `onPermissionRequest` (confirmed via the installed
  SDK's `.d.ts` that this -- not the newer `hooks.onPreToolUse`, which this
  runtime hard-fails on -- is the live tool-call gate); the read-only/
  mutating classification (kind `read`/`url` always read-only; `shell`/`mcp`
  deferring to the SDK's own per-request `readOnly` signal) was pulled into
  a new `force-tier.mjs` specifically so it's unit-testable without the live
  SDK connection. Added `force-tier.test.mjs` and extended
  `thresholds.test.mjs`/`config.test.mjs` for the new tier; all 75 existing +
  new plugin unit tests pass. Bumped `context-handoff` to 0.1.1-dev20.
- Known gap: no local harness can exercise the real `@github/copilot-sdk`
  connection to validate this against an actual live session -- only unit
  tests of the pure logic exist so far. Left the Validation Plan's force-tier
  item unchecked pending that.

### 2026-09-14 — Phase 2 (continuity content)
- Resumed via handoff. Along the way, `consume_handoff`/`handoff-cli.mjs
  consume` spuriously `ETIMEDOUT` picking up this handoff; root-caused it
  (not this effort's scope, but blocking) to `agent-dispatch consume`'s
  identity/repo resolution shelling out to `agent-worktrees get` twice, each
  costing 6.5-9.5s on this machine -- filed and corrected
  copilot-extensions#2660 with the measured evidence after an initial
  (wrong) "just raise the timeout" filing.
- Implemented Phase 2's three checklist items on a fresh
  `effort/context-handoff-overhaul-phase2` branch (based on post-merge
  `main`): the "Outstanding Background Flows & External State" schema
  section (both `handoff-template.md` shapes, `generate_handoff_prompt`'s
  returned instructions, the skill's Rules, and an explicit not-captured
  open item in the force-tier auto-draft path since it has no agent
  composition step to discover this data); a shared
  `HANDOFF_MECHANISM_AWARENESS` constant (`cutover-seed.mjs`) carrying both
  perpetuation-across-handoffs and fresh-session-awareness, threaded into
  every delivered brief and delivered once on a session's first turn via a
  new `awarenessNudgeSent`/`pendingAwareness` nudge in `extension.mjs`
  regardless of handoff origin; and an explicit "Every session knows this
  exists" section near the top of SKILL.md. Extended `guidance.test.mjs`
  with coverage for all three; all 78 existing + new plugin unit tests pass
  (2 pre-existing skips unrelated to this change). Bumped `context-handoff`
  to 0.1.1-dev21.
- Same known gap as Phase 1: no local harness exercises a real live session,
  so the Validation Plan's continuity-content item stays unchecked pending
  a real-session or clean-room run; unit coverage for the schema/constant/
  wiring is in place.
- PR #2663 merged (squash `bb39c12`); a pre-existing, unrelated CI blocker
  (`agent-machines/resources.py` had grown past its grandfathered
  module-size ceiling via #2661, landed on `main` after this branch forked)
  was fixed in the same PR via a deliberate, precise baseline widen (1982 ->
  2002, its exact current size) rather than an out-of-scope refactor. Branch
  deleted. Next: Phase 3 (host-agnostic reliability) on a fresh branch off
  post-merge `main`.

### 2026-09-14 — Phase 3, slice 1 (non-mux launch primitive)
- Started Phase 3 on `effort/context-handoff-overhaul-phase3` (based on
  post-merge `main`). Read `redesign.md` §4.3 closely (the effort README's
  Phase 3 bullets are only a summary of it).
- Confirmed the open question from kickoff: neither `handoff-cutover`
  (requires an already-live mux session) nor `embody` (requires the mux
  binary present to create one) supports a genuinely mux-less launch --
  verified by reading `_handoff_cutover_spawn_result`'s explicit
  `sessions.has_mux_session` gate and `mux_new_session`'s unconditional
  tmux/psmux shell-out.
- Built `sessions.headless_new_session()` + `sessions.mux_available()` in
  `agent_worktrees/sessions.py`: a detached `subprocess.Popen` launch with
  no pane/mux/session-registry involvement at all, seed passed as a native
  `-i <seed>` arg. Wired as an explicit opt-in `--headless` flag on
  `handoff-cutover` (bypasses the mux-session-required check; explicitly
  rejects `--headless` + anchor-mode rather than silently misbehaving).
  Reused the existing token-association wait (`_wait_for_handoff_candidate`)
  unchanged -- it was already mux-agnostic (only touches mux state when a
  pane id is given).
- Along the way, found and fixed a real regression from the recent
  agent-worktrees module-size split (#2653/#2657/#2658): 5
  `TestMuxRetirePane` tests monkeypatched `sessions._mux_pane_alive` /
  `sessions._mux_last_window_guard`, but `mux_retire_pane` itself now lives
  in `sessions_pane_retire.py` and resolves those names from ITS OWN
  namespace -- so the patches silently no-op'd. One test happened to still
  "pass" by coincidence (the real tmux/psmux call fails with `OSError` on
  this machine, which the function already treats as `alive=False`,
  matching that one test's expected outcome); the other four genuinely
  failed. Retargeted the monkeypatches to `sessions_pane_retire` (the
  module that actually owns the functions) and fixed the fake subprocess
  mock objects to carry a `.stdout` attribute the real code path reads.
  Confirmed via `git stash` that this was pre-existing on `main`, unrelated
  to this session's diff.
- Also confirmed (same `git stash` method) one further pre-existing,
  unrelated failure remains on `main`:
  `TestMuxNewWindow::test_missing_prompt_receipt_retires_successor` -- a
  real behavioral question in `mux_new_window`'s retire-on-missing-receipt
  path (the mocked retire call never fires), not a test-isolation artifact
  like the other five. Left unfixed (out of scope, needs its own
  investigation) but is now the ONE remaining pre-existing failure in
  `test_handoff_cutover.py`, down from 6.
- Added unit tests: `TestHeadlessNewSession` (pure `sessions.py` coverage)
  and four new `TestCmdHandoffCutover` cases (`--headless` bypasses the
  mux-session check, rejects anchor-mode, spawn success, spawn failure,
  dry-run). `test_handoff_cutover.py` now 78/79 passing (was 150/156 before
  this session across the two files run together; the arithmetic differs
  because new tests were added). Full-suite regression run timed out at the
  bounded test-supervisor's 10-minute ceiling (this plugin's suite is large
  enough that real CI shards it across parallel jobs) -- relying on the
  real CI matrix for broader coverage beyond the specifically-touched files,
  which were run to completion and are clean.
- Bumped `agent-worktrees` to 1.5.5-dev112 (`plugin.json` + `pyproject.toml`
  + marketplace.json) -- first attempt (dev111) collided with PR #2666
  landing on `main` mid-session with the same version; rebased and bumped
  past it before pushing.
- **PR #2669 merged** (squash). Next: Phase 3 slice 2 (coordinator-fallback
  wiring) on a fresh branch off post-merge `main`.
- **Not yet done this slice:** the coordinator-fallback wiring itself (item
  2), the `userPromptSubmitted` observability hook (item 3), and closing
  `handoff-live-cutover`'s remaining Phase 3 items (item 4). All three
  remain for a follow-up Phase 3 continuation.

### 2026-09-14 — incidental fix found while resuming Phase 3 slice 2

- Before starting the coordinator-fallback wiring, the operator reported a
  live production defect: a "Skill not found: context-handoff" error and the
  fresh-session awareness message ("This worktree has a context-handoff
  mechanism available from turn one...") appearing to reinject mid-
  conversation, unrelated to the current turn. Root-caused to
  `extension.mjs`'s first-turn awareness nudge (`awarenessNudgeSent`/
  `pendingAwareness`, added in Phase 2/#2663): that module is reimported on
  every session reconnect/refork (documented in its own top-level comment),
  so the in-memory "sent once" guard is not idempotent across a session's
  real lifetime -- a mid-session extension/skill reload replays the nudge
  and races the skill registry, producing the transient lookup failure.
- Per the operator's steer, moved the "mechanism exists" fact out of the
  extension's runtime nudge entirely and into the plugin's existing static,
  hookless session-start guidance (`scripts/emit-guidance.*`, written once
  per real session start) -- a plain file rewrite is naturally idempotent,
  so no reforked module state can replay it. The extension is now scoped to
  only what genuinely requires it: `session.usage_info` monitoring and the
  soft/hard/force threshold nudges.
- Also fixed an unrelated pre-existing version-drift bug found while
  pushing: `marketplace.json`'s `agent-worktrees` entry (`1.5.5-dev113`) was
  stale against `plugin.json`/`pyproject.toml` (`1.5.5-dev114`) already on
  `main`, blocking every push's version-consistency guard.
- Bumped `context-handoff` to `0.1.1-dev22`. **PR #2683** opened (not yet
  merged as of this entry).
- **Phase 3 slice 2's actual objective (coordinator-fallback launch wiring)
  is still not started** -- this detour consumed the session before that
  work began. Next session: branch fresh off `origin/main` (after #2683
  merges) and pick up the coordinator-fallback wiring per the "Next Slice"
  plan already on file (extend `agent_dispatch/coordinator.py`'s
  `_gc_loop`/`_orphan_reap_loop`-style periodic reconciliation to detect an
  unclaimed `proposed`/`handoff`-labeled task past a bounded window and
  launch `agent-worktrees handoff-cutover --headless`, guarded against
  double-launch).

### 2026-09-15/16 — Phase 3 slice 2 (coordinator handoff-fallback reconciliation)

- **PR #2683 merged** (context-handoff awareness fix from the prior detour).
- Investigated status, then per operator direction re-derived the real
  design: **agent-dispatch is the holder of the handoff task, not the
  implementor of the handoff mechanism.** The coordinator's job is only to
  judge staleness and say "this handoff looks stale, please claim it" -- the
  associated bridge (headed or headless) is responsible for actually
  replacing the old session with a new one in place. This corrects the
  original plan (coordinator shelling directly to `agent-worktrees
  handoff-cutover`) -- that would have made agent-dispatch reimplement
  cutover mechanics agent-bridge already owns, and would have blocked any
  future non-Worktree-Manager control plane from reusing the same
  primitives. Investigated agent-bridge's existing session-lifecycle head
  guard (`worktree_head.py`/`_enforce_worktree_head_guard`) and found the
  `reclaim` break-glass **already existed** at the request-body/client
  layer (from an earlier, unrelated effort) but was never wired to the
  `create` CLI subcommand -- so the actual gap was much smaller than
  originally scoped.
- Implemented: `agent-bridge create --reclaim` (CLI flag threaded through
  `_resolve_target`/`_start_agent_session` to `client.start_session`);
  `agent_dispatch.bridge.spawn_worker(reclaim=...)`; a new
  `handoff_fallback_seed.py` (pure Python port of `cutover-seed.mjs`'s short
  successor-seed contract, so the coordinator never needs a Node.js
  runtime); a new `queue_handoff_fallback.py` (`HandoffFallbackMixin`) whose
  `claim_handoff_fallback()` is the atomic single-attempt fence -- an
  `INSERT` into a dedicated table keyed by task id, so a racing
  reconciliation cycle, a second coordinator process, or a legitimate
  just-in-time human/tool pickup can never double-launch (deliberately
  simpler than `spawn_reservations`' retry/attempt-budget machinery: a
  stale handoff gets exactly one fallback attempt, ever); and
  `coordinator.py`'s `_handoff_fallback_loop`, following the exact
  `_gc_loop`/`_orphan_reap_loop` supervised-cycle shape (bounded,
  `/health`-recorded, backed off). Wired into `create_app`/`server.py`
  behind a **default-off** opt-in (`AGENT_DISPATCH_HANDOFF_FALLBACK`,
  1h default grace `AGENT_DISPATCH_HANDOFF_FALLBACK_GRACE`) -- a
  coordinator autonomously spawning a real Copilot process on a time
  heuristic is genuinely safety-relevant, so it ships disabled and
  reviewable rather than default-on.
- Investigated three unrelated open PRs (#1585/#2156/#2311, a third-party
  "Herdr" live-handoff mechanism) per the operator's curiosity about #2311
  specifically: confirmed it is unmergeable (CONFLICTING against `main`),
  has no reported CI, and carries 7 unresolved automated review findings
  (incl. one flagged High severity) with no human approval -- not something
  to build on or wait for; this slice proceeded independently.
- 25 new tests (`test_handoff_fallback.py`) plus reclaim-threading tests in
  both plugins' existing suites plus two new coordinator `/health`
  integration tests. Full targeted regression: agent-dispatch 329 passed (2
  pre-existing/unrelated skips); agent-bridge 56 passed. Guards
  (module-size, version-bump, and the full pre-push suite) all green.
  Bumped `agent-dispatch` to `0.1.2-dev110`, `agent-bridge` to
  `0.4.0-dev490`. **PR #2808** opened, later CI-blocked by two unrelated
  drift bugs surfaced while it sat open (worktree-manager module-size
  baseline, agent-index-service.json version lag) -- both pre-existing on
  `main`, fixed in the same PR per the established pattern, then merged.
- **Remaining for a follow-up slice:** the `userPromptSubmitted` pickup
  hook (Phase 3 item 3) and closing `handoff-live-cutover`'s remaining
  Phase 3 items (item 4). Once those land, Phases 4 (configurability) and 5
  (lineage/diagnostics) remain.

### 2026-09-16 — Phase 3 slice 3 (deterministic pickup signal + closing handoff-live-cutover)

- **Item 3 (`userPromptSubmitted` signal), built:** the runtime has no
  distinct `userPromptSubmitted` hook type -- the live equivalent is
  `extension.mjs`'s existing `session.on("user.message", ...)` SDK event
  (already wired for turn counting). Added a one-shot, first-turn-only
  check: `cutover-seed.mjs`'s new `extractRecoveryLocatorFromPrompt()`
  (pure regex grep for the exact `Recovery: context-handoff <kind>:<id>`
  clause every cutover seed carries -- no LLM judgment) detects a handoff
  pickup; on a match, `handoff-core.mjs`'s new `logHandoffPromptReceived()`
  best-effort logs a `context_handoff_prompt_received` event via
  `agent-worktrees activity-log` (a deliberately distinct event/field name
  from agent-worktrees' own numbered-handoff `handoff_token` identity space,
  to avoid any collision). The predecessor's `pickupSignals()` gained a
  matching reader (`worktreePromptReceived()`), folded in as a new
  `"prompt-received"` entry in the `via` array -- more direct proof of
  pickup than the existing `spawnInFlight` marker (which only proves a
  process was created, not that Copilot itself received the seed), and
  immune to the skill-load race a mid-session reload can cause (the same
  class of bug this effort's PR #2683 fixed for the awareness nudge).
- **Item 4 (closing `handoff-live-cutover`'s Phase 3), reconciled not
  rebuilt:** re-verified its three-item checklist against current code.
  Two items were already satisfied by a *different* mechanism than
  originally described -- the extension was deliberately kept
  process-manager-agnostic (never touches mux, never spawns a successor,
  never retires panes), so mux-detect/self-retire-arm and pane retirement
  are handled by `agent-bridge` (`requestAgentBridgeHandoff`) and
  agent-worktrees' resident status-monitor daemon instead. The third
  (non-mux/boot-failure fallback) was already covered by
  `manualFallbackInstructions` plus this effort's own Phase 3 slice 1
  (`--headless`). Updated `handoff-live-cutover`'s README checklist +
  journal in place with per-item provenance notes rather than leaving it
  stale -- only its private, non-resolvable stretch validation (a live
  tmux pass, #2261†/#2262†) remains open there, unchanged from migration.
- 13 new tests (7 pure `extractRecoveryLocatorFromPrompt` cases in
  `cutover-seed.test.mjs`, 3 `logHandoffPromptReceived` cases, and an
  end-to-end `triggerHandoff` test proving the real (unmocked)
  `pickupSignals` path surfaces `"prompt-received"`). Full
  `context-handoff` JS suite: 91 passed, 2 pre-existing/unrelated skips.
  Python suite: 24 passed, 4 skipped, 3 pre-existing/unrelated local-
  environment failures (confirmed identical before this change).
- **Phase 3 is now fully closed.** Remaining work: Phase 4 (configurability)
  and Phase 5 (lineage/diagnostics closure).

### 2026-09-17 — Phase 6 opened: mux pane lifecycle primitives, isolated

- **Operator-reported production symptoms**, on their own machine, after
  Phase 3 landed: mux panes spawning over each other or never becoming
  foreground; handoffs stuck / never picking up; false-positive pickup
  signals fed back into the live session.
- Investigated first via `~/.agent-worktrees/logs/activity.jsonl` (confirmed
  the logging itself works correctly) and found one concrete, severe cause:
  the resident status-monitor's predecessor-retire sweep had **no terminal
  condition** -- a predecessor whose own process had already exited was
  retried every ~30s forever. Confirmed in production: 3,085 failed retries
  over 3 days on one worktree (contesting a pane six later, unrelated
  sessions had since legitimately reused, including the diagnosing session
  itself), ~3,700 total across 6 worktrees, only 1 ever successful. Killed
  the stuck daemon as immediate mitigation; shipped the code fix separately
  as `fix/handoff-predecessor-retire-infinite-loop`, merged as **PR #2826**
  (5 new tests, one CI flake confirmed via re-run then merged clean).
- That fix closed one specific bug, but the operator's diagnosis is broader:
  **repeatedly patching mechanism inside `context-handoff`/
  `handoff-cutover`'s existing control flow keeps making it more fragile,
  not less**, because the mux create/foreground/terminate steps were never
  built as independent, directly-testable primitives -- every fix has to
  reason about the whole trigger→store→detect→spawn→confirm→retire
  choreography at once. Per the operator's explicit direction, this is a
  **sub-division of this effort** (new Challenge 4 / Phase 6), not a new
  effort -- the same adversarial-redesign-and-reflect flow this effort
  already uses, applied to a narrower, previously-unexamined slice.
- Audited reality (`redesign.md` §6): `mux_new_window()` already has almost
  the right shape (receipt-file bootstrap confirmation, foreground-by-
  default) but is reachable only through `cmd_handoff_cutover`'s single
  ~500-line function, with no standalone verb and no primitive-level
  pre-spawn status recording. Termination has **two independently
  maintained, subtly different code paths** for the same conceptual
  primitive (`graceful_quit_mux_session`+`restart_worktree_copilot` for
  Picker Stop/Take-over vs. `mux_retire_pane` for handoff-cutover retire),
  neither of which confirms shutdown by reading the pane's actual console
  output -- both infer completion from liveness polling alone.
- Decision: extract and generalize two primitives -- `pane_create` (resolve
  mux server, log intent first, bootstrap-confirm before payload exec,
  foreground) and `pane_terminate` (Ctrl-C ladder + console-output shutdown-
  signature confirmation + bounded hard-kill fallback + lock-file cleanup,
  replacing both existing termination paths) -- each shipped with its own
  isolated CLI harness so it can be driven directly against a live mux
  server without the full handoff dance. Only after both are hardened does
  `handoff-cutover` and the Picker Stop/Take-over path get rewired onto them.
- Added Phase 6 to this README's Plan and a matching Validation Plan item.
  **Not yet started:** the actual `pane_create`/`pane_terminate`
  implementation, harness, and rewire -- this entry records the redesign
  and plan only; implementation is the next session's work.
