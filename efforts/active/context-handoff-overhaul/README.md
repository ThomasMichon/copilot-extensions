# context-handoff overhaul

- **Slug:** `context-handoff-overhaul`
- **Repo:** copilot-extensions (the `context-handoff`, `agent-worktrees`, and
  `agent-bridge` plugins)
- **Branch(es):** `effort/context-handoff-overhaul` (Phase 0, merged #2593),
  `effort/context-handoff-overhaul-phase1` (Phase 1+)
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

### Phase 2 — Continuity content (Goal 2)
- [ ] Extend the handoff content schema (`generate_handoff_prompt` /
  `save_handoff_prompt`) with an explicit, named class for outstanding
  background flows (watches, polls, scheduled/recurring prompts) and external
  state the predecessor was responsible for (open PRs, held claims/leases,
  peer-agent coordination) — never silently dropped, always either resumable
  or surfaced as an explicit open item.
- [ ] Make the "handoff mandate" (perpetuation + fresh-session awareness) a
  standing, explicitly-carried element of every seed — not incidental prose.
- [ ] Update the `context-handoff` skill so a freshly-started (non-handoff)
  session is told the mechanism exists from its first turn.

### Phase 3 — Host-agnostic reliability (Challenge 3)
- [ ] Confirm `agent-worktrees handoff-cutover` supports a genuinely headless
  (no-mux) invocation, or build a non-mux launch primitive if it doesn't --
  open question flagged in `redesign.md` §4.3; must resolve before the
  coordinator fallback below can rely on it.
- [ ] Extend `agent-dispatch`'s coordinator to reconcile an unclaimed
  `proposed`/`handoff` task past a bounded window into a fallback headless
  successor launch, per `redesign.md` §4.3 (reusing already-supervised
  infrastructure rather than a new standing watchdog).
- [ ] Evaluate a `userPromptSubmitted` hook that greps the successor's first
  submitted prompt for the expected handoff token, as a deterministic
  "pickup actually happened" signal feeding the lineage trace (complements,
  does not replace, Phase 3's coordinator fallback).
- [ ] Close `handoff-live-cutover`'s remaining Phase 3 items (armed
  `session.idle` retirement finish, non-mux/successor-start-failure
  fallback) now that the effort lives in this repo.

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
  reference to it, verified in the successor.
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

