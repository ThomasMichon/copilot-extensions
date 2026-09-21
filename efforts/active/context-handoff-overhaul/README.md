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
- [x] Add a `mode` config key (`auto` / `manual-only` / `off`) to
  `config.mjs` / `.context-handoff/config.yaml`.
- [x] Support absolute-token thresholds as an alternative to percentages.
- [x] Add a user-level config layer beneath the existing repo-level layer.

### Phase 5 — Lineage/diagnostics closure (Goals 6, 7)
- [x] Coordinate with (not duplicate) `handoff-cutover-lifecycle-journal`'s
  remaining open items: populate universal linkage fields, Stage 1
  predecessor framing, the `agent-worktrees handoff-trace` renderer, and
  feeding `health.find_orphaned_handoffs()`.
  **Closed in the owning sibling effort:** see
  `efforts/active/handoff-cutover-lifecycle-journal/README.md` Phase 3/4 and
  the 2026-09-17 Journal entry there for the implementation record.
- [x] Confirm the vision's "single-successor discipline with recovery"
  Behavior is realized once lifecycle-journal's Phase 3/4 land; file any
  residual gap as a narrowly-scoped follow-up rather than re-designing.
  **Confirmed for the mux/CLI resident-monitor path:** the remaining deferred
  item is the optional per-session `handoff-trace.jsonl` mirror, which is an
  archival convenience gap, not a takeover/recovery-semantics gap.

### Phase 6 — Mux pane lifecycle primitives, isolated (Challenge 4)
> Added 2026-09-17 after live production symptoms (panes spawning over each
> other or never becoming foreground, stuck/false-positive pickup signals) on
> the operator's own machine. See `redesign.md` §6 for the reality audit and
> three-bin diff. A sub-division of this effort, not a new one, per the
> operator's explicit direction.
- [x] **`pane_create` primitive.** Extract + generalize `mux_new_window()`'s
  receipt-based bootstrap-confirmation shape into a standalone,
  independently-testable primitive: resolve the worktree's mux server, log
  intent via `activity.log_event` *before* acting (not buried in a caller),
  spawn via a pane-bootstrap script that confirms landing before exec'ing
  the real payload (generalized beyond today's `initial_prompt`-only gate),
  and foreground the pane as part of the same call. **PR #2842.**
- [x] **`pane_terminate` primitive.** Reuse the existing Ctrl-C escalation
  ladder shape, but confirm shutdown by reading the pane's live console
  output (`capture-pane`) for Copilot's own exit signature instead of only
  polling session/pane liveness; hard-kill the pane on a bounded (~30s)
  overall budget if the signature never appears; then clean up the stale
  session lock file the old process left behind. Collapses today's two
  divergent termination code paths
  (`graceful_quit_mux_session`+`restart_worktree_copilot` and
  `mux_retire_pane`) into one. **PR #2842.** The console-output
  shutdown-signature patterns are shipped as **provisional/configurable**
  (liveness polling remains the authoritative primary signal) — still open:
  tighten the exact pattern against a real Copilot clean-exit banner
  captured via the new harness (see next item).
- [x] **Isolated CLI harness for both primitives**, reachable independent of
  `handoff-cutover`, so each can be driven directly against a live mux
  server — closing the gap that made live mux-mutation testing unsafe from
  inside an attached session (`handoff-live-cutover`'s Phase 4 finding).
  **PR #2842** (`agent-worktrees pane-create` / `agent-worktrees
  pane-terminate`). Still open: an actual live-harness run against a
  disposable/throwaway session to confirm the exit-signature patterns above.
- [x] Hermetic test coverage for both primitives (subprocess-mocked, matching
  the existing `test_handoff_cutover.py` convention) plus a manual
  live-validation runbook using the new harness. **PR #2842**
  (`test_pane_lifecycle.py`; runbook added to `redesign.md` §6).
- [x] Rewire `handoff-cutover` (spawn + retire modes) and the Picker
  Stop/Take-over path to call the two hardened primitives instead of their
  current bespoke/duplicated logic. Landed as the follow-up slice after
  PR #2842: `handoff-cutover` now calls `pane_create`/`pane_terminate`
  directly, and `restart_worktree_copilot` routes its graceful stop path
  through `pane_terminate` on the session's active pane (falling back to the
  legacy whole-session hard kill only when the pane primitive cannot
  positively finish the stop, including the intentional `last-window-skip`
  guard mismatch on this caller).
- [x] **Post-landing live-validation fix.** A real Windows/psmux test-drive
  found that bare pane ids like `%1` are session-local there, not
  server-global, so a `-t %N` could silently hit the wrong live session.
  Issue #2886 tracks the finding; PR #2890 tightens the pane lifecycle
  primitives and their callers to build session-qualified
  `session:window.pane` targets when the owning session is known and to fail
  closed on ambiguous bare-id fallback instead of guessing.
- [x] **Session/worktree head-claim tracking fix (gitea aperture-labs#7230).**
  Discovered while live-validating the above: a worktree kept resuming into
  a stale predecessor session instead of its most recent successor, even
  after that predecessor had opened a handoff and successor sessions had
  done real work for ~36 hours without ever becoming head. Not a mux-pane
  bug — `register_session`'s head-claim gating treated any pending handoff
  as blocking ordinary auto-claim, even when the predecessor never
  progressed past "active" (`open_handoff` never marked its own state).
  Fixed with a new `"yielded"` `SessionState` (distinct from
  `handed-off`/`concluded`), a `_HEAD_INELIGIBLE_STATES` split so
  head-resolution-only logic doesn't leak into `conclude_session`/
  `link_handoff`'s stricter "already concluded" checks, and a
  `_pending_handoffs_all_from_yielded()` gate so a genuine in-flight formal
  cutover (predecessor `conclude_session`'d with a still-pending token)
  still blocks ordinary auto-claim — only an explicit bind may supersede
  that. Also added `handoff_diagnostics.stamp_session_state_worktree_binding()`,
  an unconditional best-effort write of
  `~/.copilot/session-state/<sid>/worktree-binding.json` recording every
  session's worktree association (the durable per-session-start recording
  half of the two operator-described wishes). PR #2989.

### Phase 7 -- Opt-in gate + reliability hardening (Challenge 5)
> Added 2026-09-20 after the operator reported context-handoff was
> "materially broken, seemingly everywhere" in live production use --
> plugin load failures, Worktree Manager pickup failures, duplicate spawns,
> stuck predecessors, and unreliable head tracking severe enough to warrant
> shutting automatic behavior off until resolved. Tracked in the facility's
> internal issue tracker (plugin load failures; durable logging +
> cut-me-over/check-the-handoff diagnostic skill; successor fallback
> discoverability; several deduplicated against already-open internal
> items). A related internal item tracks the harness-vs-child-worktree
> knowledge gap this phase's own work surfaced.
- [x] **Opt-in gate.** `.context-handoff/config.yaml`'s `mode` default
  flipped from `auto` to `manual-only`: automated nudges, the force-tier
  auto-trigger, and `trigger_handoff`'s live-cutover wiring (the
  `handoff_requested` activity event agent-worktrees' resident
  status-monitor watches for, and the agent-bridge ping) all require
  explicit `mode: auto` opt-in now. Manual handoffs (compose/save/consume)
  keep working unconditionally under the new default -- only `mode: off`
  blocks them. **PR #3041 (merged `9cdf4f3a0`).**
- [x] Investigate why the `context-handoff` plugin frequently fails to load
  in Copilot CLI sessions at all -- needs controlled experimentation with
  the plugin's manifest/extension shape to isolate the rejection cause.
  **This plugin's own contribution addressed:** confirmed a shared root
  cause with a parallel investigation -- synchronous,
  blocking I/O at extension load time, before `joinSession()`, delaying the
  readiness handshake. `agent-bridge`'s dominant instance (blocking
  subprocess spawns) was fixed separately (PR #3098); this plugin's smaller,
  spawn-free instance (a synchronous config directory walk + file reads) is
  now non-blocking too (see journal below). The plugin-load investigation as
  a *whole* stays open pending confirmation the combined fixes resolve the
  operator's original live symptom.
- [ ] Fix Worktree Manager's `trigger_handoff` pickup reliability: cases
  where a "successful" report corresponds to no actual live pane.
- [ ] Harden the successor-side fallback so an agent with zero working
  context-handoff tools can reliably discover and execute the manual
  consume+resume flow without floundering -- builds on this effort's
  already-merged PR #3016 instructions.md fallback; live agent reports
  suggest it isn't sufficient on its own yet.
- [ ] Confirm/close the duplicate-spawn gap (addressed by PR #3011) and the
  predecessor-termination gap against the operator's exact symptom
  description.
- [ ] Build the dual-source head-session reconciliation + resume-time
  conflict UX: agent-worktrees' sessionStart hook durably journals every
  session it observes starting against a worktree; context-handoff's
  sessionStart hook marks a new head when the worktree was open for a
  pending handoff; on resume, agreement launches directly, disagreement
  surfaces both candidates' session title/id/start/last-updated to the
  operator (Worktree Manager dialog) or the calling agent (agent-bridge
  CLI/API), rather than guessing.
- [ ] Build durable handoff event logging plus a "cut me over" (execute a
  real cutover to the live target pane from a stuck outgoing session) and
  "check the handoff" (reconcile tracking, find the true head, cut over,
  spawning a process if needed) diagnostic skill. instructions.md needs a
  pointer to this troubleshooting guide too, since skills aren't reliably
  loading in the exact failure class this phase exists to fix.

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
  (Unit-level coverage landed: `mode.test.mjs` covers the pure gating policy,
  `config.test.mjs` covers mode parsing plus user-level/repo-level merge
  precedence, `thresholds.test.mjs` covers absolute-token and mixed
  percent/token pressure math + deferred mixed-order validation, and
  `cli-parity.test.mjs` proves the payload-local manual fallback refuses
  `mode: off`. Still open: a real live-session proof that the SDK-wired
  `session.usage_info` handler suppresses nudges/force-tier behavior in
  `manual-only`/`off` -- no local harness exercises a real
  `@github/copilot-sdk` session connection, so that end-to-end piece still
  needs a real session or clean-room scenario before checking this off.)
- [ ] Lineage: `agent-worktrees handoff-trace` reconstructs a
  full chain for a multi-hop handoff after every process in the chain has
  exited, on both a live worktree and one already reaped.
  **Status:** hermetic renderer/health coverage now lives under the owning
  lifecycle-journal effort; live multi-hop and post-reap validation remain
  open there.
- [ ] Clean-room scenario updates: extend `context-handoff-cutover` and
  `context-handoff-eval` to cover the force-tier and the coordinator-fallback
  path.
- [x] Mux pane lifecycle primitives: `pane_create` produces a confirmed,
  foregrounded pane against a live mux server via the new isolated harness
  (not just hermetic mocks); `pane_terminate` correctly distinguishes a
  genuine Copilot shutdown signature from a hung pane and falls back to a
  hard kill + lock-file cleanup within the ~30s budget when it does; both
  behaviors are exercised standalone, without going through the full
  handoff-cutover choreography. **Update after Phase 6 item 5:** the
  production callers are now rewired and covered hermetically (`handoff-cutover`
  spawn/retire + `restart_worktree_copilot`); the full `agent-worktrees`
  suite has one known pre-existing, unrelated failure block
  (`test_doctor.py`, confirmed reproducing on clean `origin/main`) but no
  regression from this rewire.
  **Live-mux validation completed 2026-09-18** on a private facility host
  (Windows/psmux) using disposable `--system` worktrees and the isolated
  CLI harness (never the attached session, per the redesign's own runbook):
  found and fixed two real safety/reliability bugs surfaced only by live
  testing --
  [#2886](https://github.com/ThomasMichon/copilot-extensions/issues/2886)
  (psmux bare pane ids collide across sessions; a bare `%N` target could
  silently resolve to an unrelated live session -- fixed in
  [#2890](https://github.com/ThomasMichon/copilot-extensions/pull/2890) by
  session-qualifying every mux verb and failing closed on genuine
  ambiguity) and
  [#2892](https://github.com/ThomasMichon/copilot-extensions/issues/2892)
  (the session-scoped lookup added by #2890 only saw a session's
  currently-active window, missing a predecessor pane in a non-active
  window -- the actual real handoff-cutover shape -- fixed in
  [#2894](https://github.com/ThomasMichon/copilot-extensions/pull/2894)).
  After both fixes, `pane_create` + `pane_terminate` were verified
  end-to-end against a real two-window disposable session (predecessor pane
  in window 0, successor pane in window 1, mirroring live cutover): the
  predecessor terminated cleanly (`gone: true`), the successor and the
  operator's own attached session were confirmed untouched. A third finding
  ([#2896](https://github.com/ThomasMichon/copilot-extensions/issues/2896):
  `mux_focus_pane`'s bare pane/window id targeting made `pane_create`'s
  foreground step unreliable) was fixed in
  [#2902](https://github.com/ThomasMichon/copilot-extensions/pull/2902),
  per the operator's direction to treat it as necessary hardening given
  real-world scale (dozens of parallel worktrees, each driving multiple
  handoffs). A final end-to-end re-run after all three fixes confirmed
  `pane_create` now succeeds fully (`ok: true`, `foregrounded: true`) for
  both the predecessor and successor pane, with `pane_terminate` cleanly
  retiring the predecessor and leaving the successor and the operator's own
  session untouched.
  **Update 2026-09-18:** the actual `agent-worktrees handoff-cutover`
  command (not just the standalone primitives) is now also live-validated
  end-to-end -- spawn (real seeded Copilot successor) + retire (predecessor)
  against a disposable session -- surfacing and fixing a fifth live bug (a
  false `identity-mismatch-skip` in retire mode's own pre-flight check; see
  the Journal entry below).

## Proposal

_Pending — see [`redesign.md`](redesign.md) for the current level of design
detail; a fuller Proposal will fill in once Phase 0's issue-filing and review
gate land._

## Journal

### 2026-09-17 — Phase 5 coordination closure
- Closed this effort's Phase 5 by updating the owning
  `handoff-cutover-lifecycle-journal` effort in place rather than creating a
  second implementation narrative here.
- Coordinated status: universal lineage fields, the
  `agent-worktrees handoff-trace` renderer, and
  `health.find_orphaned_handoffs()` stalled-stage reporting are now tracked as
  landed on the sibling effort.
- Residual gap check: the only deliberately deferred item is the optional
  per-session `handoff-trace.jsonl` mirror, which does not reopen the vision's
  single-successor / recovery semantics and therefore did not warrant a new
  redesign or follow-up effort here.

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
  server without going through the full handoff choreography.

### 2026-09-17 — Phase 4 (configurability)

- Implemented all three Phase 4 items on a fresh `origin/main` branch:
  `config.mjs` now parses a top-level `mode:` (`auto` / `manual-only` /
  `off`), accepts per-tier `_tokens` as an alternative to `_percent`, and
  loads a lower-priority user-global config from
  `~/.context-handoff/config.yaml` before merging any repo-local
  `.context-handoff/config.yaml` keys over it.
- **Judgment call — `mode: off`:** treated as a repo-level opt-out of the
  *mechanism itself*, not merely the automatic monitor. The extension now
  suppresses every `session.usage_info` pressure response outside `auto`,
  and when `mode: off` it additionally refuses all extension-provided manual
  entry points (`generate_handoff_prompt`, `save_handoff_prompt`,
  `consume_handoff`, `trigger_handoff`, `/handoff-continue`,
  `/consume-handoff`, `/resume-handoff`) plus the payload-local CLI's
  mutating/consuming handoff verbs. Reasoning: a repo owner asking for
  "off" should get a true disable, not a loophole where the mechanism still
  works if someone knows the internal tool names. Read-only diagnostics
  (`facts`, `check-heads`) remain available from the CLI.
- **Judgment call — mixed percent/absolute thresholds:** allowed *per tier*
  (the more permissive design the redesign text allowed), not all-or-
  nothing. Validation remains strict for what is knowable at load time
  (exactly one unit per tier, integer/range checks, same-unit ordering), and
  a mixed config's cross-unit ordering is re-validated at
  `contextPressure()` time once a real token limit exists. Reasoning: a
  config such as `soft_tokens` + `hard_percent` is genuinely useful, but its
  effective ordering sometimes depends on the session's live token window, so
  rejecting every mixed config up front would give up that flexibility for no
  safety gain.
- **Judgment call — user-level path and precedence:** chose
  `~/.context-handoff/config.yaml`, mirroring the repo-local
  `.context-handoff/config.yaml` shape, because this plugin is payload-only
  and does not own an installed runtime root. Merge precedence is per key:
  defaults < user-level < repo-level, including per-tier threshold-unit
  replacement (`hard_tokens` in the repo cleanly replaces a user-level
  `hard_percent` rather than coexisting with it). Reasoning: key-level merge
  preserves a repo's ability to override just `mode` or one threshold tier
  without discarding otherwise-useful personal defaults.
- Added unit coverage: `mode.test.mjs`, expanded `config.test.mjs`,
  `thresholds.test.mjs`, and `cli-parity.test.mjs`. Updated docs:
  `plugins/context-handoff/README.md` and `docs/configuration.md`. Bumped
  `context-handoff` to `0.1.1-dev25`.
  server without the full handoff dance. Only after both are hardened does
  `handoff-cutover` and the Picker Stop/Take-over path get rewired onto them.
- Added Phase 6 to this README's Plan and a matching Validation Plan item.
  **Not yet started:** the actual `pane_create`/`pane_terminate`
  implementation, harness, and rewire -- this entry records the redesign
  and plan only; implementation is the next session's work.

### 2026-09-17 — Phase 6 items 1-4 landed (PR #2842)

- Implemented both primitives in a new `pane_lifecycle.py` module (kept out
  of `sessions.py` to stay under its module-size baseline): `pane_create`
  (mux-server resolution + pre-spawn `activity.log_event` +
  receipt-confirmed payload exec, generalized off the `initial_prompt`-only
  gate + explicit `mux_focus_pane` foreground) and `pane_terminate` (the
  existing Ctrl-C ladder + a new `capture-pane` shutdown-signature check
  layered on top of -- not replacing -- authoritative liveness polling,
  bounded ~30s hard-kill fallback, stale-lock cleanup). Both ship with a
  standalone CLI harness (`agent-worktrees pane-create` /
  `agent-worktrees pane-terminate`) independent of `handoff-cutover`, and
  hermetic subprocess-mocked tests (`test_pane_lifecycle.py`) matching the
  `test_handoff_cutover.py` convention.
- Along the way, found and fixed one **pre-existing, unrelated** CI blocker
  on `main` (`test_pr_ops.py::TestRefreshHeadObservation::
  test_concurrent_reassociation_rejects_returned_observation`), verified
  deterministic (3/3) on a clean `origin/main` checkout before fixing it in
  the same PR, per this effort's established precedent (#2663, #2808).
- Bumped `agent-worktrees` to `1.5.5-dev139`. **PR #2842 merged.**
- **Deliberately deferred to a follow-up slice:** item 5, rewiring
  `cmd_handoff_cutover` (spawn + retire) and the Picker Stop/Take-over path
  (`restart_worktree_copilot`) onto these two primitives -- per the
  effort's own sequencing (land + prove primitives standalone first, then
  a separate, focused rewire PR). Also still open: live-harness capture of
  Copilot's real clean-exit console text to tighten `pane_terminate`'s
  currently-provisional signature patterns.

### 2026-09-17 — Phase 6 item 5 landed

- Rewired the three production callers onto the new primitives without
  changing their public CLI contracts: `_handoff_cutover_spawn_result()`
  now calls `pane_create`, `_handoff_cutover_retire_result()` now calls
  `pane_terminate`, and `restart_worktree_copilot()` now uses
  `pane_terminate` against the session's active pane for the graceful stop
  path.
- Preserved the existing handoff activity-log contract (`handoff_successor_*`,
  `handoff_cutover_spawn`, `handoff_predecessor_retire`) while letting the
  primitive own its single `mux_session_assigned` emission. For the retire
  path, the new `graceful-signature-confirmed` method is treated everywhere
  that matters as equivalent to `graceful` (success classification, process
  reap, predecessor conclusion, and event `outcome="gone"`).
- Restart-path judgment call: `pane_terminate`'s `last-window-skip` guard is
  correct for handoff retirement (never tear down the only live worktree
  window when the caller merely wants to retire an old pane), but it is a
  semantic mismatch for Picker Stop / Neuron-Forge Take-over, whose explicit
  goal is to stop that session. So `restart_worktree_copilot()` treats
  `last-window-skip` as "graceful pane retire declined; continue to the
  legacy whole-session hard kill fallback" rather than reporting success or
  inventing a new external method value.
- Validation for this slice: new/updated hermetic coverage in
  `test_handoff_cutover.py`, `test_restart_copilot.py`,
  `test_mux_live_cache.py`, and `test_profile_assignment.py`; targeted
  rewired-callers regression green; module-size and version-bump guards
  green. Full `python tools/run-plugin-tests.py agent-worktrees --timeout
  600 --plugin-timeout 3600` surfaces 10 pre-existing, unrelated failures in
  `test_doctor.py` — verified to reproduce identically on a clean
  `origin/main` checkout, so not attributed to this change; left untouched
  as out of scope for this PR. The separate isolated live-mux harness
  proof for `pane_create`/`pane_terminate` remains open exactly as noted in
  the Validation Plan above.

### 2026-09-18 — Live-mux validation of Phase 6's isolated harness (2 bugs found + fixed)

- Ran the isolated `pane-create`/`pane-terminate` CLI harness against real
  disposable `--system` worktrees/mux sessions on a private facility host
  (Windows/psmux), per the effort's own manual-validation runbook (never
  the attached session) — the one Validation Plan item this Phase's landed
  work hadn't yet proven.
- **Bug 1 ([#2886](https://github.com/ThomasMichon/copilot-extensions/issues/2886)):**
  psmux pane ids (`%N`) are **not globally unique across sessions** (every
  session's first pane is `%1`, unlike real tmux's server-global counter).
  A diagnostic `capture-pane -t "%1"` run from my own attached session
  silently returned *my own live pane's content* instead of the disposable
  test target — a genuine safety hazard, since `pane_terminate` targeted
  bare pane ids the same way and could have sent Ctrl-C/kill-pane to an
  unrelated, live, attended session. Caught before anything destructive ran
  (per the destructive-action/error-response discipline), filed, then fixed
  in **PR #2890**: every mux verb now resolves a session-qualified
  `session:window.pane` target when the session is known, and fails closed
  with a new `method: "ambiguous-pane-id"` (listing `candidate_sessions`)
  rather than guessing when it isn't. Also caught and fixed, before
  merging, an unrelated accidental regression the fix PR's branch had
  picked up (a revert of #2834's Picker `--project` threading fix) —
  restored to `origin/main`'s content before merge.
- **Bug 2 ([#2892](https://github.com/ThomasMichon/copilot-extensions/issues/2892)),
  found immediately while re-verifying Bug 1's fix:** the new
  session-scoped lookup used `list-panes -t <session>`, which per psmux's
  own semantics only lists that session's *currently-active* window's
  panes — silently missing a predecessor pane sitting in an older,
  non-active window. That's the actual real handoff-cutover shape
  (predecessor in window 0, successor freshly spawned into window 1), so
  the very fix meant to make `pane_terminate` safe for that case couldn't
  reliably find its target. Fixed in **PR #2894**: always list with `-a`
  (every session/window) and filter by session name in code.
- **End-to-end re-verification after both fixes**, against a real
  two-window disposable session (predecessor pane in window 0, successor
  in window 1, mirroring live cutover exactly): `pane_terminate` correctly
  resolved the qualified target, ran the full Ctrl-C ladder, escalated to
  hard-kill (expected — a plain shell doesn't respond to Ctrl-C the way
  Copilot does), and confirmed `gone: true`. Verified afterward that only
  the predecessor pane was removed — the successor pane and the operator's
  own attached session were both untouched.
- **One more, lower-severity finding** filed as
  [#2896](https://github.com/ThomasMichon/copilot-extensions/issues/2896)
  and left open (not blocking): `mux_focus_pane` (the function `redesign.md`
  §6 called "already correct... reference pattern only," explicitly left
  out of scope for the #2886/#2892 fixes) has the *same* bare-pane-id
  targeting gap, making `pane_create`'s "foreground the pane as part of
  the same call" step unreliable whenever a colliding id exists — though it
  fails closed (returns `foregrounded: false`) rather than focusing the
  wrong pane, so it's a reliability gap, not a safety hazard.
- This closes the Phase 6 Validation Plan's mux-pane-lifecycle item: the
  primitives are now proven against a real mux server, not just hermetic
  mocks, and the two bugs the live test surfaced were exactly the kind of
  thing this validation step exists to catch.

### 2026-09-18 — Third bug (#2896) fixed per operator direction: necessary hardening at scale

- The operator's guidance: fix #2896 too, not leave it open — "in practice
  we hit every bug in the book running dozens of parallel worktrees which
  each drive multiple handoffs." At that scale, a foreground step that
  fails closed instead of working is still a real reliability cost, not a
  tolerable edge case.
- Live-verified the exact scope of the gap first: **window ids (`@N`), not
  just pane ids (`%N`), also collide across psmux sessions** (confirmed
  with two disposable sessions, both independently allocating `@1` as their
  first window) — so the fix needed to session-qualify both the pane
  lookup and the `select-window` target, not just the former.
- Fixed `mux_focus_pane` to resolve through the already-fixed,
  all-windows-aware `_mux_qualified_pane_target` helper, then target
  `select-window` with a `<session>:<window_index>` string instead of a
  bare window id. Removed the now-fully-dead `_mux_pane_session_name`
  import and `_mux_target_window_id` helper (no remaining callers).
  Widened `sessions.py`'s module-size baseline by 2 lines (2608 → 2610) for
  the fix's net size, per this effort's established precise-widen
  precedent. **PR #2902.**
- **Final end-to-end re-verification** against a fresh disposable
  two-window session: both `pane_create` calls now return `ok: true` /
  `foregrounded: true` / `error: null` (previously always failed the
  foreground step); `pane_terminate` cleanly retired the predecessor
  (`gone: true`) while the successor pane and the operator's own attached
  session remained untouched. All three live-discovered bugs (#2886,
  #2892, #2896) are now fixed and re-verified live.

### 2026-09-18 — Fourth bug found: real interactive Copilot spawns via the harness

- After all three pane-targeting bugs were fixed, the operator asked to
  test-drive the primitives against a REAL interactive Copilot session
  (not a stand-in shell payload) — spawn one as a new pane in the
  operator's own active session, foreground it live, then retire it.
- The very first attempt with a real `copilot` payload failed
  reproducibly: `pane_create` reported
  `"error": "successor did not confirm a stable pane launch (status: failed:0)"`.
  Live-diagnosed with `remain-on-exit on` (so the crashed pane's own
  diagnostic screen stayed visible instead of vanishing instantly) down to
  the exact literal error: `The term 'c' is not recognized as a name of a
  cmdlet...` — `$rest[0]` was a single **character**, not the word
  `"copilot"`.
- Root cause, isolated precisely via a debug-instrumented copy of the
  wrapper and confirmed in isolation with a bare PowerShell repro:
  `pane-wrapper.ps1`'s flag-stripping loop used
  `$rest = if (cond) { @(...) } else { @() }` (if-as-expression
  assignment). PowerShell silently **unwraps a resulting single-element
  array to a bare scalar** in this exact pattern — even though the branch
  itself forces array typing with `@()`. Once `$rest` narrows to exactly
  one remaining element (the common case: a single-token payload command
  like bare `copilot`, with an initial prompt in transport, which is
  *every* handoff-cutover spawn), it collapsed to a scalar string;
  `$rest += @('--interactive', $prompt)` then did string concatenation
  instead of array append, and the final `& $rest[0] @($rest[1..])`
  indexed a single character, crashing before `copilot` ever launched.
  The wrapper's own crash detection wrote `failed:0` and paused with a
  diagnostic — but that diagnostic pane vanishes with `remain-on-exit`
  at its default (off), so none of this was ever visible to a caller.
- **Plausibly explains a related, previously-unexplained symptom**: any
  real `handoff-cutover` spawn whose launch command reduces to a single
  trailing token would silently fail to start its successor — from the
  operator's view, indistinguishable from "nothing happened," while the
  predecessor session remained alive. Not confirmed as the exact cause of
  any specific historical incident, but a strong, mechanistically precise
  candidate.
- Fixed by moving the array assignment inside each `if`/`else` branch (a
  plain statement, not an expression whose result is captured), which does
  not exhibit the unwrap. Added `test_pane_wrapper_argv.py`: extracts and
  executes the real parsing loop via a live `pwsh` subprocess, covering the
  single-token failing shape, the already-working multi-token shape, and
  the no-control-flags bare-command shape. Verified the new test fails
  against the reverted (buggy) source and passes against the fix (checked
  both ways via `git stash`). **PR #2907.**
- **Final live re-verification**, deployed through the normal
  `agent-worktrees update` path (not a manual file copy): spawned a real
  interactive Copilot session as a new pane in the operator's own active
  worktree via `pane_create` — `ok: true`, `foregrounded: true`, the
  session loaded and responded normally to its seeded prompt — then
  retired it cleanly via `pane_terminate` (`gone: true`, graceful),
  leaving the operator's own pane untouched throughout.

### 2026-09-18 — Fifth bug: full `handoff-cutover` command live-validated end-to-end, one real retire-mode bug found + fixed

- All four bugs above were found by live-testing the two **primitives**
  (`pane-create`/`pane-terminate`) in isolation. Nobody had yet live-tested
  the actual `agent-worktrees handoff-cutover` command itself (spawn mode →
  real seeded successor → retire mode) — the thing operators and the
  extension actually invoke. Per the operator's direction, ran it
  end-to-end against a disposable `--system` worktree: bootstrapped a
  placeholder predecessor pane via `pane-create`, ran `handoff-cutover`
  spawn mode with a real seed (a genuine interactive Copilot process
  launched, seeded, and responded correctly — `ok: true`,
  `foregrounded: true`, `seeded: true`), then ran `handoff-cutover` retire
  mode on the predecessor pane.
- **Bug found:** retire mode reported `identity-mismatch-skip` even though
  the predecessor pane genuinely was in the expected session — a false
  positive that would silently no-op a legitimate retire. Root cause:
  `_handoff_cutover_retire_result`'s own pre-flight identity check called
  `sessions.mux_session_for_pane()` (→ `current_mux_session()`), which runs
  a **bare** `display-message -t <pane_id>` unqualified by session — the
  exact unsafe pattern already fixed elsewhere (#2890/#2892/#2896) for
  `pane_terminate`/`mux_focus_pane`, but never fixed here. Reproduced
  directly (`psmux display-message -p -t "%1" "#{session_name}"` returned
  the *caller's own* attached session, not the session that actually owned
  pane `%1`) with **no genuine id collision present** — a stricter failure
  mode than #2886's collision case: an unqualified `-t <pane_id>` can
  silently resolve to a default/current-session context when run from
  outside the target pane (e.g. an orchestrator retiring a predecessor on
  its behalf, not a predecessor self-retiring), independent of whether any
  other session happens to share that bare id.
- Fixed by adding `_resolve_retire_pane_mux_session()`: checks membership in
  the *expected* session first via the already-safe, session-scoped
  `list-panes -a` filter (`sessions_pane_retire._list_matching_pane_targets`),
  falling back to the (also safe) unscoped resolver only when the pane
  isn't there — never the bare `display-message` lookup. Re-ran the live
  test after the fix: retire mode correctly resolved and retired the
  predecessor (`ok: true`, `gone: true`), while the real successor Copilot
  pane and the operator's own attached session were both confirmed
  untouched.
- **Incidental fix**: found and repaired a pre-existing, unrelated test-file
  defect while adding regression coverage —
  `test_execute_retires_every_stale_predecessor_in_one_pass` in
  `test_handoff_cutover.py` was accidentally nested *inside* another test
  function's body (a stray indentation mistake from an earlier edit),
  making it a dead nested function pytest never collected or ran. Restored
  it as a proper `TestCmdHandoffsCheck` method; it passes.
- Added three focused unit tests for `_resolve_retire_pane_mux_session`
  (scoped-match preferred, unscoped fallback, ambiguous-raises-None) plus a
  precise `tools/module-size-baseline.json` widen (28601 → 28635) for
  `__main__.py`'s net size. Full targeted suite
  (`handoff_cutover or pane_lifecycle or restart_copilot`): 141 passed. Full
  `agent-worktrees` suite: 613 passed, 10 pre-existing `test_doctor.py`
  failures confirmed reproducing identically on a clean `origin/main`
  checkout (unrelated to this change).
- Cleaned up: disposable successor/predecessor panes and the `--system`
  worktree used for the live test were all removed after validation.

### 2026-09-20 — Session/worktree head-claim tracking fix (gitea aperture-labs#7230)

- Root cause: `register_session`'s auto-claim-of-head branch was gated by
  "no pending handoffs at all", so a predecessor that had merely opened a
  handoff (`trigger_handoff`/note-handoff) — but never itself moved past
  `"active"` — permanently blocked head resolution for every later session
  in that worktree. Separately, there was no durable per-session record of
  which worktree a session belongs to, so a stale successor's own
  session-state folder had no way to self-identify.
- Fix: new `SessionState` value `"yielded"` set by `open_handoff()` on its
  predecessor (only from `"active"`); a new `_HEAD_INELIGIBLE_STATES`
  constant used only by head-resolution properties (left
  `_CONCLUDED_SESSION_STATES` and `conclude_session`/`link_handoff`'s
  stricter checks untouched); a new `_pending_handoffs_all_from_yielded()`
  helper so ordinary auto-claim is allowed only when *every* pending
  handoff's predecessor is merely yielded — a genuinely `conclude_session`'d
  predecessor with a still-pending token continues to block ordinary
  auto-claim, preserving the existing protection for a real in-flight
  formal cutover (only an explicit bind may supersede that).
- Also fixed a race this surfaced: `register_session()` gained a
  `candidate_token` parameter so the sessionStart hook's ordinary
  registration call (which precedes its separate
  `associate_handoff_candidate` call) can't itself auto-claim head and
  cancel the very handoff token the candidate association is about to
  reference; and a handoff token's state is now pre-checked before
  `_link_if_fresh`, so a token already cancelled by an unrelated session's
  auto-claim degrades to `None` instead of raising `SessionLifecycleError`
  out of `cmd_register_session` (which would hard-fail sessionStart).
- New `handoff_diagnostics.stamp_session_state_worktree_binding()`: an
  unconditional, best-effort, atomic write of
  `~/.copilot/session-state/<sid>/worktree-binding.json` recording every
  session's worktree/machine association, wired into `cmd_register_session`
  right after successful registration.
- Updated 5 existing test files whose assertions encoded the old
  "predecessor stays active until formally linked" semantics; added
  `test_session_worktree_binding.py` (12 new tests). Full regression across
  all touched + adjacent suites: 385+ passed. One pre-existing, unrelated
  failure confirmed via stash-and-retest against clean `origin/main`
  (`test_status_monitor.py::test_monitor_retire_handoff_predecessor_preserves_identity_guard`)
  — not fixed here, flagged separately.
- PR #2989.

### 2026-09-20 — Symptom-bug sweep + durable instructions.md fallback guidance

- Per operator direction: swept every filed context-handoff/agent-worktrees
  symptom bug across both trackers (aperture-labs Gitea + this repo's GitHub
  issues) to check for a clean, consistent pattern before continuing.
  Notable open items found: GitHub #3006/#3001/#3000/#2830 (unbounded
  cutover-spawn retries, orphaned/stacked panes, three-way state desync
  between mux/agent-worktrees/agent-dispatch); Gitea #7072/#7179 (Windows
  psmux env-var-propagation confirmation gap; successor sessions missing
  `context-handoff` re-triggering an auto-handoff loop forever); #4702/#3454
  (head-pointer zombie regression, deeper root cause: phantom `resources`
  entries marked active/released for pickup attempts never corroborated
  against real `agent-bridge` liveness).
- **Avoided duplicating in-flight work:** began an independent fix for the
  "controller endlessly retries a cutover" bug (GitHub #3006/#3001; a durable
  per-handoff `spawn_attempted_at` field gating the automatic monitor sweep
  to at most one pane per token), then discovered **PR #3011** already open
  addressing the same root cause via a different mechanism
  (activity-log-based `_monitor_already_attempted_handoff_tokens`). Reverted
  the parallel implementation rather than compete; #3011 merged shortly
  after (commit range `89c554554`..`600ae2cf7`, plus a reviewer-flagged
  follow-up already closed out).
- **Shipped PR #3016** for the other, genuinely open half of the ask: harness
  repos' `.github/instructions/*.instructions.md` projections are the "last
  line of defense" (load even when a plugin's skill/MCP tools fail to
  register this session), but both context-handoff's and agent-worktrees'
  projected `session-guidance.instructions.md` files only pointed at a
  dynamically-generated per-session file and carried no fallback content of
  their own -- a dead end if the writer hook never ran. Added two new static
  projections (following the established `worktree-context-guide` precedent
  of a *separate* topic-named projection, per
  `docs/patterns/session-scoped-dynamic-guidance.md`'s "only the computed
  part belongs in the session-folder file" rule -- `session-guidance`
  itself stays untouched):
  - `context-handoff/instructions/handoff-fallback.instructions.md`:
    brief-prep, consume-and-record-as-head, CLI fallback, and a heuristic
    discovery order (dynamic file -> last-resort manual handoff-file glob ->
    agent-worktrees' own ledger -> payload-local CLI) for when the plugin's
    tools are absent entirely -- explicitly routes pane cleanup through
    `agent-worktrees handoffs-check --execute`, never a manual pane kill.
  - `agent-worktrees/instructions/head-claim-fallback.instructions.md`: the
    complementary self-claim-head (`bind-session`) and stuck-cutover
    diagnosis (`handoffs-check`/`doctor --fix`) guidance.
  - Both held under the 4096-byte per-template budget
    `instruction_projections.py` enforces.
  - Merge commit `2fd2dc8de`.
- **Operational hazard found and worked around, not fixed:** the shared
  local worktree checkout used for this work
  (`copilot-extensions.worktrees/manual-context-handoff-overhaul`) had
  unrelated, uncommitted `worktree-manager` picker-TUI changes mixed into it
  mid-session -- almost certainly a concurrent session sharing the same
  checkout. Left that WIP completely untouched; did all rebase/push work for
  PR #3016 from a disposable, isolated `git worktree add` checkout instead of
  risking it. Flagged to the operator; not itself resolved.
- Flagged (not yet filed): the reviewer-identified gap in #3011 (only the
  terminal-success `handoff_cutover_spawn` event gated the retry, not
  `_started`/`_failed`) was addressed before merge per the PR's own
  iteration -- verify on a future pass rather than assuming closed.

### 2026-09-20 (later) -- Operator escalation: opt-in gate + Phase 7 opened

- Operator reported context-handoff "materially broken, seemingly
  everywhere" in real production use, with seven distinct live symptoms
  (see Phase 7 above) and asked to (a) file each in the facility's internal
  tracker, (b) drive more phases in this effort, and (c) immediately gate
  the risky automatic behavior behind explicit opt-in. Filed three new
  internal tracking items (plugin load failures; durable logging +
  diagnostic skill; fallback discoverability); four others deduped against
  already-open internal items.
- Corrected mid-session by the operator: `agent-worktrees bind-session`
  must never be run from *within* a related child-repo checkout (this
  copilot-extensions worktree, worked from a separate harness repo's
  session) -- only a harness worktree binds a session; a child worktree is
  *claimed* via its own repo-scoped CLI (`copilot-extensions create`/
  `push-changes`/`create-pr`) or `agent-worktrees related resolve <name>`.
  Undone via `deregister-session`; filed an internal item tracking that
  this distinction wasn't discoverable anywhere until asked. All
  subsequent git operations in this session routed through
  `agent-worktrees git sync` / `copilot-extensions create-pr` rather than
  raw `git worktree add`/manual push.
- **Shipped the opt-in gate (PR #3041, this phase's first item, merged
  `9cdf4f3a0`):** `.context-handoff/config.yaml`'s `mode` default flipped
  from `auto` to `manual-only`. Found and closed a real gap the existing
  mode config didn't cover on its own: `triggerHandoff()` always emitted the
  `handoff_requested` activity event (agent-worktrees' automatic-spawn
  trigger) and pinged agent-bridge regardless of mode -- now both are
  gated on a new `mode` parameter threaded through from all three call
  sites (MCP tool, payload-local CLI, force-tier auto-trigger). Manual
  compose/save/consume keeps working unconditionally under the new
  default; corrected `HANDOFF_MECHANISM_AWARENESS`'s now-inaccurate
  unconditional "the extension nudges...and forces one" claim in the same
  pass, since the default flip made it false for the common case.
  **Nine rounds of automated review** on this PR caught two genuinely
  serious follow-on gaps beyond the initial fix, both closed before merge:
  (1) `storeHandoff()` itself -- shared by BOTH `save_handoff_prompt` and
  `trigger_handoff` -- unconditionally called `noteHandoffInRecord()`,
  creating a `pending_handoffs` entry agent-worktrees' resident monitor
  could discover and claim independently via its own session-state-file
  fallback, with no activity event required at all; this meant even
  `save_handoff_prompt` alone (documented as never arming pickup) could
  still get auto-launched, completely bypassing the gate. Fixed by
  removing the call from `storeHandoff()` entirely -- only
  `triggerHandoff()` now records it, gated. (2) The static bash/PowerShell
  session-start fallback guidance (`emit-guidance.sh`/`.ps1`) still
  unconditionally claimed live signaling always happens; updated within
  their existing byte budgets. Final `node --test` suite: 112 tests, 110
  passed (2 pre-existing skips), including new structural/behavioral
  regression tests for both fixes.
- Remaining Phase 7 items (plugin-load investigation, Worktree Manager
  pickup reliability, successor fallback hardening, dual-source head
  reconciliation + resume conflict UX, durable event logging + cut-me-over/
  check-the-handoff diagnostic skill) are substantial standalone efforts in
  their own right, each tracked by its own gitea issue above -- left open
  for follow-up sessions rather than rushed in one pass.

### 2026-09-20 — Fourth pane-lifecycle finding: cleanup gap on foreground failure

- Found while independently auditing the merged `pane_lifecycle.pane_create()`
  work for an unrelated question ("is the cutover only valid once the
  successor is genuinely the operator's current pane") that turned out to
  already be exactly what this effort's `mux_focus_pane` work (#2842/#2890/
  #2896) had built. One residual gap surfaced by that audit: `pane_create`
  retires the successor's process tree when its launch receipt never arrives
  (`prompt_received=False`), but did **not** do the same when the launch was
  confirmed yet `mux_focus_pane` still failed to foreground it
  (`foregrounded=False`) -- a live successor process was left running,
  orphaned off a pane the operator's console tab never actually switched to.
- Filed as [#3118](https://github.com/ThomasMichon/copilot-extensions/issues/3118)
  and fixed in the same change: the `foregrounded=False` branch now mirrors
  the existing `prompt_received=False` cleanup (`sessions._mux_pane_process_tree`
  + `sessions._retire_failed_successor`), surfacing the cleanup result on the
  payload the same way. New regression test in `test_pane_lifecycle.py`
  (`test_pane_create_retires_successor_when_foreground_fails`); full
  `agent-worktrees` suite passes aside from a pre-existing, unrelated
  `test_doctor.py` failure batch (confirmed failing identically on
  unmodified `main`, out of scope for this change).

### 2026-09-20 (still later) -- Phase 7 item 1: plugin-load investigation, first pass

- Picked up this effort's internal-tracker issue #7264 (plugin frequently
  fails to load) per this effort's own recommendation that it unblocks
  reasoning about the other four remaining Phase 7 items.
- Structural diff against known-reliable sibling plugins (`agent-worktrees`,
  `agent-bridge`): `plugin.json`, `hooks.json`, and `session-context.json`
  shapes are unremarkable and match the working plugins' conventions
  (`runtimeScope: none` is a legitimate value, not a misconfiguration --
  this plugin has no venv/runtime to manage). Ruled out as the cause.
- Live-load smoke tests, both clean: (1) `copilot --plugin-dir
  .../context-handoff -p "list tools containing 'handoff'"` from an
  isolated scratch directory (no repo root above it) surfaced all four
  tools (`generate_handoff_prompt`, `save_handoff_prompt`,
  `consume_handoff`, `trigger_handoff`) correctly; (2) this very picked-up
  session (a real production worktree, full plugin marketplace, `.git`
  several directories deep) also had all four tools available, confirmed
  via `tool_search_tool`. So the plugin is not *categorically* broken --
  matches the issue's own "frequently", not "always", framing.
- **Leading suspect identified, not yet confirmed:** `extension.mjs` line
  ~30 runs `loadContextHandoffConfig(process.cwd())` synchronously at
  **module top level**, before `joinSession()` and before any hook fires.
  That call chain (`findRepositoryRoot` walking up the directory tree with
  repeated `existsSync`, then `readFileSync`-ing up to two config layers)
  is blocking I/O performed during the extension host's import/registration
  phase. None of the sibling plugins checked (`agent-worktrees`,
  `agent-bridge`) do comparable synchronous work at import time -- their
  `extension.mjs` files only *define* handlers before `joinSession()`. If
  the host enforces any load-time budget per extension (unconfirmed -- the
  SDK internals aren't in this repo to inspect), a slow directory walk or
  file read (e.g. a deeply nested worktree, a network/cloud-sync-backed
  path where `existsSync`/`readFileSync` block on a placeholder hydration)
  would be a plausible, environment-dependent way for *this* plugin
  specifically to lose a race that others structurally can't lose, without
  surfacing any diagnostic (a silent host-side drop, not a thrown error the
  plugin's own try/catch could catch).
  `fs.existsSync` swallows errors and returns `false` rather than throwing,
  so this is a **latency** hypothesis, not an uncaught-exception hypothesis
  -- ruled out crash-on-permission-error as the mechanism.
- **Confirmed by a parallel investigation, same session window:** a
  companion investigation root-caused the dominant instance of this exact
  bug class in `agent-bridge`'s `resolveMetadata()` -- four sequential,
  blocking `execSync`/`execFileSync` subprocess spawns (up to ~29s combined)
  run synchronously before `joinSession()`, freezing the event loop long
  enough that the readiness handshake itself missed its window (fixed
  upstream in copilot-extensions PR #3098: async, parallel, fire-and-forget
  from load-time init). That investigation explicitly flagged this plugin's
  smaller `loadContextHandoffConfig()` directory walk as a secondary,
  lower-risk instance of the same class -- confirming the lead above
  without requiring the deeper live-session instrumentation originally
  called for.
- **Fixed in this same pass:** `loadContextHandoffConfigAsync()` (new,
  `config.mjs`) replaces the synchronous call on `extension.mjs`'s
  load-time path with non-blocking `node:fs/promises` equivalents, resolved
  via `handoffConfigPromise` fired fire-and-forget -- never awaited on the
  path to `joinSession()`/readiness. The synchronous original is unchanged
  and still used by `handoff-cli.mjs` and its existing tests, where
  blocking a short-lived CLI process is harmless. 4 new parity tests
  confirm the async loader agrees with the synchronous one on every
  scenario. `node --test`: 116 tests, 114 pass (2 pre-existing skips), no
  regressions. Live smoke test (`copilot --plugin-dir ... -p "..."`)
  reconfirmed all four tools load correctly after the change.
- Issue #7264 -- this plugin's own contribution is addressed by the fix
  above; the plugin-load investigation as a whole stays open pending
  confirmation that `agent-bridge`'s fix (the dominant contributor)
  resolves the operator's original live symptom.

### 2026-09-21 -- Successor-prompt race investigation + sync-before-trigger fix

- Investigated a distinct, narrower symptom: a successor spawned via
  `--interactive "<prompt>"` can attempt a tool call in its very first turn
  before that tool's extension has actually finished registering, and the
  turn stalls. Traced the actual gate in the CLI's own source
  (`envLoadingComplete`, an `extensions` participant wired to
  `embeddedServer.onExtensionsLoaded`) and confirmed live, via a throwaway
  psmux pane probe, that extension readiness tracking is active for our
  sessions (Worktree Manager and `agent-worktrees` force-enable the
  experimental flag this gate depends on) -- so this narrower race is not
  fully closed out, but isn't as wide-open as first suspected either.
- Separately confirmed (same probe) that `/env`'s Skills panel only lists
  project-sourced skills, never plugin-sourced ones -- a genuine display gap
  in the CLI upstream, but not a functional one: directly asked a probe
  session whether a plugin-sourced skill (`playwright-cli`) was available,
  and it was. No action taken here since the fix is outside this repo.
- **Actioned:** regardless of exactly how tight the extension-readiness race
  is, a successor always inherits the *same on-disk worktree* as its
  predecessor -- so if that worktree is behind the repo's default branch,
  the successor starts on stale plugin code and stale instructions even
  when the CLI's own readiness gate works perfectly. Added a "Sync before
  triggering" step to both trigger flows in the `context-handoff` skill:
  before calling `trigger_handoff`, commit local WIP and sync the worktree
  onto the latest default branch (`agent-worktrees git sync` when
  available, conflict-safe by construction), noting an unresolved conflict
  in the handoff brief rather than blocking on it. Bumped
  `plugin.json`/`marketplace.json` to `0.1.1-dev35` across several review
  rounds (dev33 collided with a concurrent main merge).

### 2026-09-21 (cont.) -- PR #3167 round 9: dirty-check gap, TOCTOU narrowing, stale task-backed re-save

Continuing automated review response on PR #3167 (`context-handoff: sync
worktree onto latest default branch before triggering a handoff`); round 9
surfaced three new HIGH-severity findings, all fixed in this pass:

- **`git status --porcelain` dirty-check gap:** the default invocation can
  report a clean tree even with real untracked content present, if the
  worktree's local git config sets `status.showUntrackedFiles=no` --
  masking exactly the kind of untracked/secret-adjacent file a sync should
  never risk touching. Added `--untracked-files=all`, matching this repo's
  own established dirty-check convention already used in
  `agent-worktrees/scripts/service-utils.ps1`. New regression test sets
  that config explicitly, confirms bare `--porcelain` really is fooled by
  it (so the fix is meaningfully exercised), then confirms
  `attemptWorktreeSync` correctly skips.
- **Rebase-check/sync TOCTOU:** the in-progress-rebase check and the actual
  `agent-worktrees git sync` call were not atomic -- another process could
  start a rebase in the gap between them, and the sync helper's own
  failure-path `git rebase --abort` could then cancel a rebase this
  session never started. A cross-process worktree lock is out of scope
  (not something this plugin owns), so the check (extracted into a
  `rebaseInProgress()` helper) is now re-run a second time immediately
  before each sync exec call, right after the last other `await` --
  narrowing the unavoidable race to the true minimum rather than pretending
  a single check makes it airtight. Structural test confirms the recheck
  call site exists (no DI seam for the underlying git calls, matching this
  file's existing pattern for such checks).
- **Stale task-backed baton on re-save:** `save_handoff_prompt`'s
  agent-dispatch path (`dispatchHandoff`) used a fixed dedup key
  (`handoff-${sid}`) -- `agent-dispatch create --dedup-key K` is idempotent
  per K, so a repeat call with the SAME key silently returns the existing
  nonterminal task **unchanged**, even with different payload content. This
  meant round 7's "always re-save after post-approval sync" fix was a
  no-op for the task-backed storage path specifically: a successor could
  still receive the stale pre-sync brief. Fixed by folding a short content
  hash into the dedup key (`handoffDedupKey()`, new, pure/exported for
  testability): identical content still maps to the same key (a true
  accidental duplicate call still dedupes as before), but genuinely
  different content now earns a fresh task -- and the existing
  `abandonSupersededHandoffs()` call (already wired in, worktree-scoped,
  not dedup-key-scoped) correctly retires whatever it superseded, with no
  further changes needed there. 4 new unit tests cover stability,
  content-sensitivity, session-scoping, and the key's debuggable shape.
- Re-verified the review's other 6 "carried over" items against current
  file state -- all genuinely already resolved in rounds 5-8 (the known
  re-flagging pattern for this repo's automated reviewer), no action
  needed.
- `node --test`: 128 tests, 126 pass (2 pre-existing skips), no
  regressions -- confirmed the 3 bash-dependent `test_emit_guidance.py`
  failures are baseline-identical (same failure on the pre-change commit
  via a stash/restore check), not new. All guards
  (`check-marketplace-isolation`, `check-skills`, `check-docs-consistency`,
  `check-no-internal-identifiers`, `check-version-bump`) pass. Bumped
  `plugin.json`/`marketplace.json` to `0.1.1-dev36`.

### 2026-09-21 (cont.) -- PR #3167 round 10: sanitized Git env, real lock,
### review-safety wording carried into every trigger surface

Round 10 review (against the round-9 commit) surfaced 2 new HIGH findings
and reconfirmed 2 of round 9's fixes as still insufficient per the
reviewer's bar; all fixed in this pass:

- **Ignored files still not covered:** round 9 added
  `--untracked-files=all` but not `--ignored` -- plain `--porcelain` omits
  ignored files entirely regardless. Found the repo's own precedent using
  BOTH flags together
  (`customizing-copilot/skills/reviewing-customizations/scripts/
  scan_plugin_sources.py`'s `_payload_is_clean`, scoped to a narrow
  footprint and explicitly documented as "deliberately over-rejects").
  Added `--ignored` to `attemptWorktreeSync`'s dirty-check, accepting the
  same over-reject tradeoff (an ordinary ignored build artifact also blocks
  a sync attempt) as consistent with this function's already-documented
  fail-closed philosophy.
- **Rebase-check/sync TOCTOU still open:** round 9's "recheck immediately
  before exec" narrows but does not close the race the reviewer flagged.
  Added a per-worktree advisory lock (`withWorktreeSyncLock`, filesystem
  exclusive-create under `.git/context-handoff-sync.lock`) around the
  entire rebase-check-through-sync-exec window, serializing this plugin's
  own concurrent sync attempts (the realistic risk: the force-tier and
  skill-guided paths racing on the same worktree). Documented honestly that
  no lock this plugin creates can compel an external actor (a human running
  `git rebase` by hand) to honor it -- the recheck-immediately-before-exec
  mitigation stays in place for that residual gap.
- **(New) Unsanitized Git environment variables:** the new Git probes and
  the delegated sync inherit `process.env` unmodified, so an inherited
  `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_COMMON_DIR`/etc. could
  silently redirect the safety checks (or the sync itself) to a different
  repository than the one `cwd` names. Ported `agent-worktrees`' own
  `repository_identity_env()` (`git_ops.py`) as `sanitizedGitEnv()`
  (new/exported), used for every git probe and the sync child process; also
  sets `GIT_TERMINAL_PROMPT=0` so an unattended sync can never block on a
  credential prompt.
- **(New) WIP-commit safety condition not carried into tool-response
  prompts:** `SKILL.md` requires inspecting the tree and committing only
  reviewed paths before any sync-related commit, but the emitted
  `generate_handoff_prompt`/`/handoff-continue` tool-response text just
  said "commit local WIP" without that condition -- an agent following the
  tool's own output literally could blanket-commit unrelated edits or
  secrets. Made the inspect/only-reviewed/skip-if-unsafe rule explicit in
  both prompt surfaces (both occurrences in `extension.mjs`).
- Also fixed a genuine gap `save_handoff_prompt`'s own response text had:
  it told the agent to ask-then-trigger for the turn-end path without
  mentioning the sync+re-save step that must happen between "yes" and
  `trigger_handoff` -- an agent following ONLY that tool's own text could
  skip the sync entirely. Added the missing step.
- Corrected the emitted kernel guidance's (`emit-guidance.sh`/`.ps1`)
  ordering: it previously implied "compose and store the baton" happens
  before syncing for the pressure-driven path, backwards from the
  canonical flow. Rewrote (byte-neutral, -1 byte) so sync precedes
  compose/store; hand-verified byte-identical wording between both
  platform scripts, both still within budget (2014/2048 kernel,
  672/700 aggregate). The aggregate string's ordering was already correct
  from round 7 -- that specific re-flagged item was stale.
- Updated the PR description to match the actual shipped diff (was still
  describing an earlier, narrower version of the change and the wrong dev
  number).
- Re-verified round 9's dedup-key fix (`handoffDedupKey`) is NOT actually
  stale-flagged content -- the review comment was pinned to unchanged
  `SKILL.md` text and didn't reflect that the underlying `handoff-core.mjs`
  fix had already landed; left as-is, expecting the next round to clear it.
- 9 new tests (ignored-file detection, lock-serialization, 3
  `sanitizedGitEnv` unit tests, 1 emit-guidance ordering assertion).
  `node --test`: 133 tests, 131 pass (2 pre-existing skips), no
  regressions. All guards pass. No version bump needed (still `0.1.1-dev36`
  from round 9 -- these fixes are additional commits within the same
  unreleased dev version).

### 2026-09-21 (cont.) -- PR #3167 round 11: shared sync entry point, force-tier trigger-race narrowing

Round 11 confirmed all 4 of round 10's fixes resolved and surfaced 2 new
HIGH findings plus 1 "previously missed" (unchanged-code) finding, all
addressed in this pass:

- **Skill-guided sync bypassed the force-tier's own safety machinery:**
  `attemptWorktreeSync`'s lock/rebase-check/sanitized-env only protected the
  JS force-tier path -- the agent-guided skill flow called
  `agent-worktrees git sync` directly, so a force-tier sync and a
  skill-guided sync could still race and rebase the same worktree
  concurrently, and the manual path had no rebase-in-progress check of its
  own at all. Fixed by exposing `attemptWorktreeSync` as a new
  `handoff-cli.mjs sync-worktree` command -- ONE shared, lock-aware entry
  point both paths now go through (the skill's "Sync before triggering"
  step 2 now calls this instead of a bare `agent-worktrees git sync`). The
  skill still owns committing reviewed WIP itself before calling it, which
  matches `attemptWorktreeSync`'s existing already-clean-tree precondition.
- **Force-tier: sync started only after the live trigger signal, not before
  or during it:** `autoForceHandoff` awaited `triggerHandoff` (which arms
  the live-cutover signal a fast monitor could act on) BEFORE starting the
  fire-and-forget sync, so a successor could plausibly be launched before
  the sync had even begun. Fully serializing sync-before-trigger would
  reintroduce the exact regression rounds 6/8 already fixed (a slow/
  unreachable remote delaying or defeating the force-tier's capture
  guarantee), so the fix taken is a middle ground: the sync is now kicked
  off (still fire-and-forget, still un-awaited) immediately before
  `triggerHandoff` rather than after it returns, so the two run
  concurrently and the sync gets a real chance to progress or finish during
  triggerHandoff's own network calls and pickup-wait window, instead of
  only starting once that window has already closed. This narrows the race
  meaningfully without reintroducing the delayed-capture regression;
  documented in-code as a deliberate tradeoff.
- Re-verified round 9's `handoffDedupKey` stale re-flag: confirmed (again)
  the review comment is pinned to unchanged `SKILL.md` prose, not the
  actual (already fixed) `handoff-core.mjs` dedup logic -- expect this to
  clear once the reviewer re-scans the referenced code path.
- 2 new tests (`sync-worktree` CLI command exists and reaches the real
  `attemptWorktreeSync` logic; help text lists it). `node --test`: 134
  tests, 132 pass (2 pre-existing skips), no regressions. All guards pass
  (one transient `bare-agent-command` marketplace-isolation finding from
  the new skill wording was fixed with the established
  `<!-- marketplace-isolation: allow ... -->` marker, back to the 702
  baseline). No version bump needed (still `0.1.1-dev36`).

### 2026-09-21 (cont.) -- PR #3167 round 12: crash-safe lock, plain-git fallback, real trigger gating

Round 12 confirmed round 11's rebase-check fix resolved and surfaced 5 new
HIGH findings plus 1 "previously missed" finding, all addressed:

- **Crash leaves the sync lock permanently stale (previously missed):** the
  lock was removed only by a `finally` block -- a process killed between
  acquiring it and that block running (e.g. mid-fetch) would leave the lock
  file forever, silently skipping every future sync for that worktree.
  Added a staleness check (`STALE_LOCK_MS` = 5 minutes, generous relative
  to the sync's own ~30s worst case): an `EEXIST` on acquire now stats the
  existing lock's mtime and reclaims (unlink + retry the exclusive create)
  anything older than that, rather than honoring an abandoned lock forever.
- **Skill-guided tool-response prompts still said bare `agent-worktrees git
  sync`:** round 11 added the shared `sync-worktree` CLI command but the
  `generate_handoff_prompt` tool response and the `/handoff-continue`
  slash-command prompt (both in `extension.mjs`) still told the agent to
  run the bare command directly, bypassing the lock/rebase-guard/sanitized
  env entirely if followed literally. Repointed both at the exact
  `handoff-cli.mjs sync-worktree` invocation.
- **`sync-worktree` exited 0 on a real sync failure:** the JSON branch
  returned before any exit handling, and the non-JSON branch only exited
  nonzero for a *skipped* sync, not an *attempted-and-failed* one. A caller
  using this as a gate could proceed past a real failure. Fixed: exits
  nonzero for every non-`synced` outcome, in both output modes (documented
  in SKILL.md as expected/non-blocking, not itself a stop condition).
- **No plain-Git fallback when the sibling agent-worktrees payload is
  absent:** a payload-only context-handoff installation had no working sync
  at all -- `attemptWorktreeSync` just reported "unavailable" outright, even
  though the PR description already promised a plain-Git fallback path.
  Added `plainGitSync()` (new, exported for direct testability since
  `resolveSystemCliDescriptor` always resolves the real on-disk sibling
  plugin in this monorepo checkout, making the fallback otherwise
  unreachable in-repo): determines the remote's default branch
  (`origin/HEAD`, falling back to `git remote show origin`), fetches and
  rebases under the same sanitized environment, and aborts cleanly on
  conflict -- same conflict-safety contract as agent-worktrees' own helper.
- **Force-tier: concurrent-start sync still wasn't enough:** round 11's fix
  (start the sync concurrently with `triggerHandoff` rather than after it)
  was judged insufficient -- a fast live-pickup monitor could still launch
  a successor before the sync had even begun. Added a `beforeArmPickup`
  hook to `triggerHandoff` (default no-op, so every other caller is
  unaffected), awaited strictly between the baton being durably stored and
  the live-pickup signal being armed -- and ONLY when a live signal can
  actually fire (manual-only mode, the default, never calls it, so those
  handoffs pay no extra latency). `autoForceHandoff` now passes the SAME
  sync promise it already started (not a second sync attempt) as this
  hook, so auto-mode handoffs genuinely gate the live signal on the sync
  settling, while manual-only handoffs keep the original fire-and-forget
  behavior.
- Updated the README fallback command blocks (bash + PowerShell) and the
  durable `instructions/handoff-fallback.instructions.md` to include
  `sync-worktree`, closing the last surface that still omitted it.
- 6 new/changed tests (stale-lock reclaim, `plainGitSync` success against a
  real local "remote" and honest failure with no origin, updated
  assertions for the now-real agent-worktrees invocation in this
  checkout). `node --test`: 137 tests, 135 pass (2 pre-existing skips), no
  regressions. All guards pass. No version bump needed (still
  `0.1.1-dev36`).

### 2026-09-21 (cont.) -- PR #3167 round 13: lock-stat safety, fallback rebase recheck, honest sync ordering, exit-code truncation

Round 13 confirmed 5 of round 12's fixes resolved and surfaced 4 new HIGH
findings, all fixed:

- **Failed lock stat could remove an active replacement lock:** `acquireLock`
  treated ANY `statSync` failure on an `EEXIST` as "the lock is gone,
  reclaim it" -- but a failed stat doesn't prove that; it could be a
  permission error, a transient FS hiccup, or another process re-creating
  the lock in the exact instant between the failed open and this stat.
  Reclaiming in that ambiguous case could unlink an active replacement lock
  another process just created, letting both run concurrently. Fixed: a
  failed stat now rethrows the original contention error (fails closed)
  instead of falling through to reclaim.
- **Plain-git fallback could abort a rebase it did not start:**
  `plainGitSync` is reached after `attemptWorktreeSyncLocked`'s own earlier
  rebase check, but the remote-discovery + fetch awaits inside it are
  exactly the kind of gap another process could start a rebase in -- and
  the catch block's unconditional `git rebase --abort` could then cancel
  that unrelated rebase. Added the same immediate-before-exec
  `rebaseInProgress` recheck this file's other sync path already used,
  skipping (never touching, never aborting) rather than proceeding.
- **Sync could start before a successful store, and delayed live triggering
  unconditionally:** round 12's fix started the sync promise BEFORE calling
  `triggerHandoff` at all, so a store failure inside `triggerHandoff` still
  left an orphaned sync running -- contradicting the "post-capture only"
  contract. Added a new `afterStore` hook to `triggerHandoff` (default
  no-op), called exactly once the baton is durably stored (right after
  `store()`/`writeSessionState()` both succeed) -- the correct place to
  KICK OFF the side task. `autoForceHandoff` now assigns its sync promise
  inside `afterStore`, and `beforeArmPickup` awaits that SAME (already
  running) promise only in auto mode. This also incidentally documents the
  actual contract precisely: sync starts after store, gates live pickup
  only when live pickup can fire, never delays or depends on anything else.
- **`sync-worktree --json` could truncate its own output:** `process.exit(1)`
  called immediately after a stdout JSON write can terminate the process
  before that write drains to a pipe on some platforms. Switched to
  `process.exitCode = 1` (matching every other JSON-emitting command path
  in this CLI, which never call `process.exit()` directly) so Node lets
  the write flush naturally before exiting with that code.
- 8 new/changed tests (lock stat-failure fail-closed via a structural
  check, `plainGitSync`'s pre-rebase-exec recheck with a real pre-existing
  `rebase-merge` dir left untouched, 3 `triggerHandoff` ordering tests for
  `afterStore`/`beforeArmPickup`, a CLI exit-code assertion). `node --test`:
  142 tests, 140 pass (2 pre-existing skips), no regressions. All guards
  pass. No version bump needed (still `0.1.1-dev36`).

### 2026-09-21 (cont.) -- PR #3167 round 14: plain-git fallback detached-HEAD guard

Round 14 confirmed 4 of round 13's fixes resolved and surfaced 1 new HIGH
finding:

- **Plain-git fallback could rebase a detached worktree:**
  `agent-worktrees`' own managed sync explicitly skips a detached worktree,
  but `plainGitSync`'s `git rebase origin/<branch>` is itself perfectly
  valid while HEAD is detached -- it just moves the detached HEAD, not any
  branch, which is not what a "sync onto the default branch" caller
  expects and can leave commits unreachable once HEAD moves again. Added a
  `git symbolic-ref -q HEAD` guard (throws exactly when detached, no output
  parsing needed) before the fetch/rebase, matching the managed path's own
  precondition rather than just mirroring its conflict-safety contract.
- Re-verified the review's other 8 "carried over" items against current
  file/PR state -- all genuinely already resolved in earlier rounds (the
  known re-flagging pattern for this repo's automated reviewer): the
  shared `sync-worktree` entry point (round 11), the `handoffDedupKey`
  content-hash fix (round 9), the README/instructions fallback mentions
  (round 12), the aggregate guidance ordering (already correct since round
  7), and the PR description's documentation-impact section and dev36
  version reference (round 10's rewrite) -- no action needed.
- 1 new test (detached-HEAD checkout: confirms `plainGitSync` skips with a
  `"detached"` reason and HEAD is provably unchanged afterward). `node
  --test`: 143 tests, 141 pass (2 pre-existing skips), no regressions. All
  guards pass. No version bump needed (still `0.1.1-dev36`).

### 2026-09-21 (cont.) -- PR #3167 round 15: serialize the dirty-tree check with the sync lock

Round 15 confirmed the detached-HEAD fix resolved and surfaced 1
"previously missed" HIGH finding on otherwise-unchanged code:

- **Dirty-tree check ran before the worktree sync lock, not inside it:**
  `attemptWorktreeSync`'s clean-tree probe ran before
  `withWorktreeSyncLock` was even acquired, so the advertised
  check-through-sync serialization was never actually atomic against a
  file becoming dirty in that gap; the plain-git fallback also never
  rechecked cleanliness at all, so a local `rebase.autoStash` config could
  let `git rebase` silently stash/pop unreviewed content instead of
  refusing to run. Extracted the check into `worktreeIsDirty()` and moved
  it to run FIRST inside the now-lock-wrapped `attemptWorktreeSyncLocked`
  (the lock is acquired before ANY check now, not just before the rebase
  check), and added the same immediate-recheck-before-exec pattern to
  `plainGitSync`'s own rebase call.
- 3 new/changed tests (an intentionally-dirty tree behind a held lock
  proves the dirty check now runs after lock acquisition, not before; a
  dirtied clone proves `plainGitSync`'s pre-rebase recheck catches it;
  updated the not-a-git-checkout test for the new failure-order). `node
  --test`: 145 tests, 143 pass (2 pre-existing skips), no regressions. All
  guards pass. No version bump needed (still `0.1.1-dev36`).

Re-verified the review's other 7 "carried over" items again against
current file/PR state -- all still genuinely resolved from earlier rounds
(the same known re-flagging pattern); none required action this round.
