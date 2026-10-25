# mux-companion-manual-cutover-diagnostics — Manual Force-Cutover Trigger + Post-Cutover Head Verification

- **Slug:** `mux-companion-manual-cutover-diagnostics`
- **Repo:** copilot-extensions — the `context-handoff` + `agent-worktrees` +
  `worktree-manager` (Mux Companion) packages
- **Branch(es):** per-step worktrees → landed to `dev`
- **Created:** 2026-09-27
- **Status:** Active
- **Vision:** vision-extending against [`visions/mux-companion`](../../../visions/mux-companion/README.md)
- **Umbrella issue:** [#4369](https://github.com/ThomasMichon/copilot-extensions/issues/4369)
- **Sub-issues:** filed per-step as each step is scoped for execution

## Guiding Intent

The operator runs context-handoff in `mode: manual-only` while ironing out
cutover quirks, but still wants an on-demand way to exercise the SAME
claim → spawn-successor → retire-predecessor machinery `mode: auto` would run
automatically — from inside the Mux Companion (Ctrl-K), as an explicit,
human-gated diagnostic trigger. Then, after either that trigger or the classic
manual `/clear` + paste-`HANDOFF_SEED` flow, the operator wants to reopen the
Companion and see, unambiguously, whether the Worktree Picker's resolved head
now correctly points at the new session — with the previous and new session
both listed, so a mismatch is visible immediately rather than discovered later
when Resume launches the wrong one.

This is **not** a new cutover mechanism. The real machinery already exists,
proven end-to-end in `handoff-live-cutover` (#2249) for the automatic path;
this effort exposes an explicit, human-invoked on-demand counterpart, mirrors
the precedent `agent-worktrees handoffs-check --execute` already set for the
retire half, and adds no new behavior when the operator does not press the
button.

## Coordination

The complete direct retained #4369 acceptance below continues through this SAME Active
ROOT's Plan, own and actual global Validation, and Journal under every incumbent source,
integration, native and resource custodian. Administrative supersession retires
duplicate tracking only; historical closed sources cannot serve as live coordination
tokens. If current custody requires the original live token, retain it. No owner, claim,
hold, completed row, accepted alternative or evidence tier is removed, and no
implementation, runtime, credentials, process action, rollout or new blanket approval is
authorized.

The direct #2696 app-input/dismissal lane continues under the SAME Companion source/UI
custodian through this existing Plan, Validation and Journal plus eligible open
bounded-source coordination. The Mux-bind Relay ROOT independently owns its direct
wrapper/popup lane; BOTH complete reviewed intakes and current full readbacks precede
source supersession. Preserve #4369's manual-only/human-gated cutover and actual
source/head/background-work/provider holds. Closing a popup does not trigger cutover,
consume a baton or retire a session.

## Context

### Prior art (directly reused, not rebuilt)

- **`handoff-live-cutover`** (#2249, Active — core complete) — the automatic
  live-cutover mechanism itself: `context-handoff`'s `triggerHandoff()`
  (arms the pending-handoff ledger + activity signal when
  `automaticHandoffEnabled(mode)`), and `agent-worktrees`' resident
  status-monitor (`_monitor_maybe_process_handoff_record` → claim → spawn via
  the existing `handoff-cutover` CLI verb → retire the predecessor pane).
  This effort's Step 3 verb is an on-demand invocation of that SAME function,
  not a reimplementation.
- **A reviewed safety gate this effort must not weaken for the automatic
  path**: `triggerHandoff()`'s `autoEnabled` computation
  (`automaticHandoffEnabled(mode)`) deliberately withholds the
  `pending_handoffs` ledger write in `manual-only` mode — a Copilot review
  finding on PR #3041 ("a 'manual-only' handoff could otherwise still get
  auto-launched by the monitor through this exact path, defeating the entire
  opt-in gate"). Step 2's `force` parameter is an explicit, separate opt-in a
  caller must pass deliberately (never inferred from config) — the automatic
  path's behavior is unchanged.
- **`agent-worktrees handoffs-check --execute`** — the established precedent
  for "an explicit, on-demand counterpart to the resident monitor's own
  automatic sweep" (its own docstring's phrasing), for the retire half only.
  Step 3 is the same pattern, extended to the claim+spawn half.
- **`visions/mux-companion`** — already specifies session-lineage display and
  a read-only pending-handoff headline (built in `worktrees-pivot-ux-overhaul`
  Phase 8); its Non-Goals currently forbid the Companion invoking cutover at
  all, which Step 1 must revise to carve out this narrow, explicit exception.

### Net-new in this effort

No prior art covers: a `force` bypass of the mode:auto gate, an on-demand
single-worktree claim+spawn+retire verb, the Companion's "Cut over" button, or
Companion-side post-cutover head-verification (a refreshable session-lineage
view highlighting whether resolved head matches the just-resumed session).

## Request

**2026-10-24 continuation:** prioritize purpose-first Summary and Claims over
handoff session selection; add a switchable work-topology summary pane,
attention indicators, a permanent claims status line and clickable view tabs.
The public-safe request capture, sequencing and acceptance details are in
[purpose-first.md](purpose-first.md). This expands the same Companion effort;
it does not retire the retained manual-cutover, expected-head or app-input
acceptance below.

> My intent with the Handoff helper in the Companion is that when the agent in
> a worktree says it's made the handoff or called trigger_handoff, I should be
> able to open Ctrl-K, see an entry for "Pending handoff" as a button, and then
> press a button to "Cut over". The Companion should then execute the cutover
> behavior on-demand. I want to use this for diagnostics, while we still have
> handoff in manual mode, so we can iron out the quirks. I also want to be able
> to validate, in the companion, that after I paste the HANDOFF SEED into
> /clear, that when the new session starts, the Companion's Handoff and
> Worktree states accurately show the new session as the one which will be
> resumed by Worktree Picker. It should show me the previous session and the
> new session in a list, and if it failed to set head, I should be able to
> tell the current session to patch it up and file a bug.
>
> [Follow-up, on the arm/spawn split:] context-handoff's flow should have two
> parts: "prepare" and "trigger". "prepare" is called "save" right now, and
> should indicate that a handoff can be picked up and triggered. Then "trigger"
> should actually cause the Worktree Manager or agent-bridge to do it. Our mux
> flow can use a special "force" flow or something, to deal with the state
> where context-handoff is on "manual" mode.

## Plan

### Step 1 — Revise `visions/mux-companion`
- [ ] Add two Features: an explicit, human-gated manual cutover trigger
      (distinct from the already-speced, more dangerous raw
      `break-glass-head-override`), and post-cutover head verification
      (session lineage + a head-match marker, refreshable on demand).
- [ ] Revise Non-Goals: "not a handoff orchestrator" must be scoped to mean
      "never composes, decides, or silently invokes a cutover" — not "can
      never invoke the graceful path on explicit human command." Cross-link
      the PR #3041 safety gate this stays underneath.

### Step 2 — `context-handoff`: explicit `force` bypass (Done 2026-09-28)
- [x] `triggerHandoff()` gains a `force` boolean (default `false`); when set,
      `autoEnabled = automaticHandoffEnabled(mode) || force`. Never inferred
      from config -- an explicit, separate caller-supplied opt-in only.
- [x] `handoff-cli.mjs trigger` gains `--force`, wired through.
- [x] Tests: force arms the ledger/activity signal in `manual-only` mode;
      omitting force preserves today's exact behavior (regression coverage
      for PR #3041's own fix).

### Step 3 — `agent-worktrees`: on-demand claim+spawn+retire verb (Done 2026-09-28)
- [x] New CLI verb `handoff-cutover-trigger --worktree-id <id> [--json]`
      that runs `_monitor_maybe_process_handoff_record` for ONE worktree,
      on demand -- the SAME function the resident daemon calls per tick.
      No `--execute`/preview split (unlike `handoffs-check`): invoking the
      verb IS the explicit human action. Reports resolved head + session
      count before/after so a caller can tell whether anything changed.
- [x] Tests: claims + spawns via the existing, unmodified daemon
      choreography (no new spawn logic written); a second concurrent
      on-demand call (or the real daemon, if running) safely no-ops via the
      existing hardlink claim (unchanged, untouched by this step).

### Step 4 — `worktree-manager`: Mux Companion UI (Done 2026-09-28)
- [x] "Cut over" button, shown only when `pending_handoff` is set (disabled
      otherwise); invokes Step 3's verb directly. Step 3 was extended to
      auto-arm the ledger from the unconditionally-written session-state
      marker itself, so the Companion never needs to separately shell out
      to context-handoff's `--force` -- one call, one process boundary.
- [x] "Refresh" button reloads the whole view (status + lineage + pending-
      handoff + Cut-over enablement) in place, so reopening after a manual
      `/clear` + paste-seed resume, or after Cut over, shows updated state
      without exiting Mux.
- [x] Explicitly NOT built (per the operator's own request): no
      auto-repair-head or auto-file-a-bug action in the Companion itself --
      a detected mismatch is surfaced for the operator to hand to the
      current session to patch up and file, not acted on by the Companion.

### Step 5 — `context-handoff`: quiesce owned background work before triggering (Done 2026-10-06)
- [x] New skill section ("Quiesce owned background work before triggering")
      in `plugins/context-handoff/skills/context-handoff/SKILL.md`, placed
      so it runs BEFORE syncing and composing/saving the brief in both
      trigger paths (quiesce -> sync -> generate/compose -> save ->
      trigger; quiescing before sync matters because a still-running task
      can keep writing into the worktree while the sync inspects, commits,
      or rebases it): capture whatever partial
      progress a background agent/async shell produced, then stop an owned
      shell directly or wait for an owned agent to actually finish
      (an observed completion, not merely a sent closing message), and
      always stop every `manage_schedule` entry the session
      created -- with no exception for one the brief plans to ask the
      successor to re-arm (re-arming is always a separate, successor-side,
      post-cutover action, never a reason to leave something running now).
      A context-pressure-driven handoff that must trigger immediately is
      still not blocked on quiescing agents/shells -- same escape hatch as
      the sync step: note what was left running and why -- but schedules
      have no such exception (stopping one is a single fast call). Mirrored
      into the generated `generate_handoff_prompt`/`save_handoff_prompt`/
      `trigger_handoff`/`/handoff-continue` tool guidance in `extension.mjs`,
      the public `plugins/context-handoff/README.md` trigger-sequence docs,
      the extension-free `instructions/handoff-fallback.instructions.md`,
      and the always-on sessionStart ambient guidance
      (`scripts/emit-guidance.sh`/`.ps1`, budget-constrained to a compact
      "quiesce+sync first" pointer) so no surface describes a path that
      bypasses it. Documented one explicit, scoped exception: the automatic
      force-tier handoff (`mode: auto`, 79% threshold) runs with no agent
      turn in the loop and structurally cannot call the agent-tool-only
      `manage_schedule`/background-task primitives -- recorded in the
      skill's own "Known exception" subsection rather than silently
      left unmentioned.
- [x] Live-test coverage for this new step (see Validation Plan): done
      2026-10-06 in the very session that merged PR #5491 -- a real owned
      background `task` agent and a real `manage_schedule` entry were both
      quiesced correctly (observed completion; unconditional stop) before
      composing this handoff.

### Direct Companion input and dismissal acceptance from #2696

<!-- backlog-reconciliation:7556:transfer:2696:to:efforts/active/mux-companion-manual-cutover-diagnostics/README.md -->

Add directly to this existing Active ROOT Plan/Validation, preserving every original
manual-mode/cutover/head-verification/background-work clause and current source/provider
claim. This app lane does not take the separate Mux-bind Relay implementation. Existing
Exit/Cut over/Refresh and historical prototype observations are baseline, not current
full popup qualification.

#### Plan

- [ ] Choose and document the Companion's Escape policy. IF Escape should dismiss, bind
  it explicitly; Textual has no default Escape-to-quit behavior. Preserve the
  conditional choice rather than inventing a requirement that every key must exit.
- [ ] Deliberately define/document Ctrl+C, Ctrl+Q and keyboard/button activation.
  Preserve the empirical Ctrl+C-toast versus Ctrl+Q/Exit clean-exit distinction unless
  an actual reviewed behavior change supersedes it; a stale relay comment is not proof
  of the chosen input contract.
- [ ] Ensure all chosen clean exit paths terminate only the on-demand Companion, retain
  no persistent Companion state and allow the owning display-popup -E to close without
  another key. Preserve focus/pane return and error/cancellation behavior; dismissal
  never invokes Cut over, consumes a baton or retires a worktree/session.
- [ ] Coordinate the boundary with the relay ROOT's direct2696 wrapper/popup lane under
  the existing owners. No change to explicit human-gated cutover, source/target
  identity, held reservations or provider lifetime is implied; normal source review and
  concrete conflict resolution, not a new generic ACK gate.

#### Validation

- [ ] Exercise the actual app's selected Escape/Ctrl+C/Ctrl+Q/button behavior, clean
  shutdown, error/cancel and focus-return contract. Keep source/inherited
  defaults/prototype results distinct from current real supported popup evidence; no
  unrelated whole-cutover/Manager completion gate is added to this finite exit lane.
- [ ] Preserve the original native/popup observations and explicitly identify any
  unavailable current lane without inferring a fresh installed-build diagnosis. Do not
  use key injection as proof of real mux-client key dispatch.
- [ ] Before administrative source closure, a NEW normal reviewed merge must accept this
  entire direct app section AND the complementary relay ROOT section, followed by
  CURRENT FULL Plan/Validation readback in both roots and continuing Journal
  coordination. A child link, old4371/4405 receipt or proposal is not delivery.

No app launch, native input, popup, handoff, process, runtime, service or configuration
action is authorized by this intake.

<!-- backlog-reconciliation:7556:transfer:4369:to:efforts/active/mux-companion-manual-cutover-diagnostics/README.md -->

### Direct retained #4369 manual diagnostic and independent expected-head acceptance

APPEND without removing/revising ANY incumbent ROOT Plan, completed work, own global
Validation Plan, Participants/custody expressed in Coordination, full historical
Journal, related claims or direct #2696 app-input/dismissal lane. Preserve that lane's
complementary Mux-bind Relay ownership and BOTH-root review/readback condition
independently. This is tracker intake, NOT runtime/head qualification,
source/UI/provider/native ownership transfer or rollout. Original and complete
refinements: https://github.com/ThomasMichon/copilot-extensions/issues/4369 ;
https://github.com/ThomasMichon/copilot-extensions/issues/4369#issuecomment-6083439434 ;
https://github.com/ThomasMichon/copilot-extensions/issues/4369#issuecomment-6321372187 .

#### Direct unchecked Plan

- [ ] P1. Retain all original four steps and earned source delivery: scoped vision,
  explicit one-call context-handoff force option, one-worktree on-demand existing
  claim/spawn/retire invocation, Companion human Cut over and refreshable previous/new
  lineage. Preserve actual retired-predecessor and quiesce/background/schedule evidence
  at its stated scope. Old unchecked prose does not erase merged delivery; old
  build-complete/quiesce wording does not settle the later acknowledged expected-session
  comparison.
- [ ] P2. Keep diagnostic trigger DIRECT-HUMAN ONLY, for an existing pending baton and
  exactly one selected worktree, through the SAME admitted host machinery. Explicit
  mode-force bypass is one call, not automatic arming, config persistence, a second
  cutover implementation, process-force permission or evidence of current operator
  approval. Ordinary manual-only/MCP/non-force paths must retain their existing
  non-arming safety.
- [ ] P3. Independently resolve the expected session most recently actually cut over to
  OR manually resumed and compare it to the durable authoritative Picker-resolved head.
  An is_head dot or immediate returned head_before/head_after alone cannot compare head
  against a separately admitted expected identity. Preserve exact worktree,
  session/incarnation/lineage, pending baton and current owner context; do not compare
  head to the same head-labelled observation and call that verification.
- [ ] P4. Cover BOTH direct Cut over and classic manual clear/paste-seed resume,
  including refresh AND reopening. Show previous/new lineage and a truthful
  match/mismatch/unknown/unavailable comparison to the expected identity; a missing
  expected/owner observation cannot be fabricated into a pass. Use the existing owning
  identity/receipt/read path, not a new persistent Companion state/head authority;
  preserve the incumbent no-persistent-Companion-state app lane.
- [ ] P5. Detection/display ONLY: no automatic repair, report filing, raw head override,
  session switching, baton consumption or session retirement merely from a mismatch.
  Existing separate raw-override and selected-prior-resume work keeps its own
  owner/authority. The operator may direct its existing current session under the normal
  procedure; this feature grants none of that authority to a researcher or observer.
- [ ] P6. Preserve ENTIRE root/manual/native/background-work/provider and source holds
  plus direct #2696 choices, Exit/Cut over/Refresh/dismissal, popup/focus and
  complementary relay ownership. Dismissing/reopening a popup must never trigger
  cutover. Those separate app/relay validation and source-retirement gates remain
  intact; unrelated popup or whole-Manager completion is not a new gate on this finite
  independent-head feature.
- [ ] P7. Preserve current admission, exact
  predecessor/successor/candidate/consumer/native/manual/reservation custody,
  quiesce-before-trigger ordering and existing schedules/background-work rules. Unknown
  identity or process/operation state is a hold, not age/TTL/silence/local-task-based
  expiry, replacement or forced repair permission. Keep source, containing release,
  selected serving and native/manual validation explicit at their original tiers.
- [ ] P8. Journal actual finite progress and the acknowledged comparison residual while
  preserving full prior history. Deliver this COMPLETE direct intake and actual
  own-global Validation entry below through a NEW normal reviewed PR, obtain ACTUAL
  merge, then read back the WHOLE current ROOT before source supersession. Old
  creation/vision, Step4 checkbox, title/link/child/Draft/proposal or quiesce receipt is
  not intake delivery; retain source if any original clause/owner/evidence tier is
  absent or current custody needs its live token.

#### Direct unchecked Validation

- [ ] V1. Preserve the actually delivered original vision/force/one-worktree
  host/Companion action contract and source/recorded native proofs without counting them
  as independent expected-head verification. Credit existing real retire/quiesce tests
  exactly at their scope.
- [ ] V2. Verify direct-human, existing-baton, selected-one-worktree and one-call
  mode-force boundaries; ordinary manual-only/non-force/MCP/automatic sweeps cannot arm
  or invoke cutover from observation alone. No persistent repo config change, second
  mechanism or process-force authority.
- [ ] V3. Through the actual expected-session source and authoritative head read, verify
  separate identities are compared correctly: actual most-recent resumed/cut-over
  session versus Picker-resolved head. Include matching and mismatched
  lineage/current-incarnation cases, not a duplicated is_head marker or same returned
  head used twice.
- [ ] V4. Verify BOTH explicit Cut over and manual clear/paste resume, refresh and
  reopen. Previous/new lineage, retained expected identity and truthful
  missing/stale/unavailable/unknown outcomes remain visible without a new persistent
  Companion registry/state owner. Qualification uses the original
  supported/manual/native lanes when separately authorized, not a new all-host program.
- [ ] V5. Verify mismatch display never writes head repair, files a bug, invokes raw
  override/switch/resume, consumes/creates a baton or retires a process/session.
  Existing popup dismissal and explicit action separation remains; read evidence is not
  mutation permission.
- [ ] V6. Preserve current source/UI/native/host/provider/claims/reservations and full
  direct #2696 app/relay choices/receipt conditions under their owners. Completed
  narrower source or live quiesce scope is not whole independent-comparison acceptance;
  no adjacent feature token is closed by this validation.
- [ ] V7. Keep original appropriate manual/native evidence,
  source/containing-release/selected-serving attribution and
  quiesce/schedule/background-work boundaries. Unknown live state cannot be
  guessed/repaired/expired, and no extra universal native/whole-parent/blanket-ACK gate
  is created for administrative intake or finite source work.
- [ ] V8. After NEW normal reviewed ACTUAL merge, read the WHOLE current ROOT and verify
  P1-P8, V1-V8, C1-C4 and actual own-global Validation entry, together with every
  incumbent Plan/Validation/Journal/owner/claim/hold. Missing any unique
  original/refinement/choice/native tier or current required live source custody means
  retain #4369. Product/head comparison remains unchecked until its own actual finite
  qualification.

#### Continuing Coordination

- C1. SAME existing Companion source/UI custodian and manual-diagnostics integration
  owner retain stewardship. Existing admitted host/handoff/process/native/provider
  owners keep physical claim/spawn/retire and identity custody; the Companion remains a
  caller/observer. No source, implementation, current resource or operational ownership
  is handed off by this tracking text.
- C2. Preserve the entire incumbent original and direct #2696 app-input/dismissal
  Plan/Validation/Journal, its explicit choices and complementary Mux-bind Relay
  lane/conditions. Preserve every current pending baton, predecessor/candidate/consumer,
  native/manual/source/runtime/background-work/provider claim and completed finite
  evidence. Separate raw-head-override/prior-session recovery scopes stay separate.
- C3. Existing driver journals next finite work under this SAME Active ROOT and
  repository's eligible open bounded-source coordination policy; historical closed
  source numbers are provenance, not usable live claims. Recheck
  creator/state/lock/assignee and textual/native/resource holds before
  publication/effects. No age, silence, TTL, empty assignee or missing local task
  settles current custody; if a current holder needs the live #4369 token, KEEP it OPEN
  rather than administratively releasing anything.
- C4. Coordinator alone obtains NEW normal reviewed ACTUAL ROOT intake merge and WHOLE
  current readback before source supersession. The actual own-global Validation entry is
  mandatory. A proposal, old4371/5409/5517 receipt, title/link or partial app subsection
  cannot substitute. No new blanket ACK, original owner-condition weakening,
  implementation, mode change, cutover/head write, SDK/session/native/probe or rollout
  authority is granted.

Public delivery foundations:
https://github.com/ThomasMichon/copilot-extensions/pull/4371 ;
https://github.com/ThomasMichon/copilot-extensions/pull/5409 ;
https://github.com/ThomasMichon/copilot-extensions/pull/5517 .

### Step 6 - Purpose-first Summary and Claims continuation (#8811)

- [x] Land reviewed [purpose-first intent and sequence](purpose-first.md)
      and the revised `visions/mux-companion` before implementation.
- [x] Reuse existing authoritative purpose, summary and claims sources in
      the default Textual dialog; retain recovery as a secondary view.
      Source merged in #8822; installed/native rollout remains separate.
- [ ] Implement a compact topology presentation for phases, sub-agents,
      bridges, downstream worktrees, open PRs and accumulated tool-call stats,
      using the same sources as other companion clients.
- [ ] Implement explicit CLI/summary pane switching with attention indicators
      that preserve the live CLI and its answer boundary.
- [ ] Deliver a permanent second claims status line and clickable view tabs
      through the Mux-bind owner, with tmux/psmux qualification.

## Validation Plan

- [x] Purpose-first: confirm the captured continuation preserves the request
      and distinguishes requested intent from agent-recommended sequencing.
- [x] Purpose-first: unit and real Textual consumer tests for Summary/Claims,
      long values, missing/stale/error sources and secondary recovery actions.
      The 156-test packet includes real process shutdown and target-cwd isolation.
- [ ] Purpose-first: contract tests for graph identities, reported phases,
      agent/bridge/worktree/PR relationships and authoritative tool totals.
- [ ] Purpose-first: prove actual CLI/summary switches preserve the original
      session/process/pane and surface question/idle attention without answers.
- [ ] Purpose-first: qualify two-line status and real click/key dispatch on
      tmux and psmux, including narrow terminals and unrelated sessions.
- [ ] Purpose-first: record source, clean-room, installed/native and rollout
      evidence separately; no passed source test substitutes for a live lane.

- [ ] Complete EVERY directly retained #4369 P1-P8, V1-V8 and C1-C4 clause above at the
  original finite source/manual/native bounds, preserving the entire incumbent Plan, own
  Validation, Participants/Coordination, full historical Journal,
  direct2696/complementary relay conditions, all current owners/claims/holds and
  partial-delivery credit. Administrative tracking is not independent head-comparison or
  runtime acceptance; NEW normal reviewed ACTUAL merge plus WHOLE current ROOT readback
  precede source supersession.

- [ ] Complete every direct #2696 Plan/Validation clause above at its original supported evidence tier. Preserve the complementary root's separate lane and all original owners/holds; administrative consolidation is not implemented popup acceptance.

- [ ] Live-tested in this operator's own manual-mode worktree: press "Cut
      over" on a genuinely pending handoff, confirm a real successor spawns
      and the predecessor pane retires.
- [ ] Live-tested: after a manual `/clear` + paste-`HANDOFF_SEED` resume
      (no button), reopening the Companion shows the new session as head.
- [x] Live re-validated the #5455 predecessor-retire root-cause fix itself
      (not the Companion button, not mocked): a real throwaway worktree, a
      real `note-handoff` + `register-session --handoff-token` +
      `link-succession --handoff-token` cycle against the installed
      `context-handoff@0.5.8-dev1`, and a real psmux predecessor pane.
      Reproduced the OLD bug first (omitting the explicit token on the
      successor's registration silently cancelled the real pending token --
      `link-succession` then rejected a replay with "is cancelled, not
      pending"), then re-ran with the token threaded through as the fix
      does: link succeeded, `handoffs-check` surfaced the real token as a
      retire candidate (previously impossible -- a cancelled entry is
      permanently excluded), and `--execute` genuinely killed the live
      predecessor pane (confirmed gone from `psmux list-panes`). See
      2026-10-06 journal entry for the full command sequence.
- [x] Live-tested: a predecessor with an owned, still-running background
      task/agent (a `task` tool invocation, an async shell) at the moment of
      handoff -- confirmed the new "Quiesce owned background work before
      triggering" skill step is actually followed. As test setup (before any
      handoff composition began), started a real
      background `task` agent (a 5-minute sleep-then-report job), then --
      per the skill's capture-before-stop
      ordering, and before composing anything -- waited for its OBSERVED
      completion (`read_agent` showed
      `status: idle`, turn output `"done"`) rather than sending it a
      closing message and assuming it finished. Captured here, not
      silently dropped.
- [x] Live-tested: a predecessor with an active, self-owned
      `manage_schedule` entry at the moment of handoff -- confirmed it gets
      stopped unconditionally. Created a real `manage_schedule` entry
      (10-minute interval) alongside the background task above (same test
      setup, before composing anything), then
      stopped it (`manage_schedule action: stop`) before composing/
      triggering -- no re-arm was requested since it was a test artifact
      with no successor-side purpose, matching the "re-arm is a deliberate,
      named exception, not a default" rule.
- [x] Unit tests green per step; `tools/check-module-size.py` clean.

## Journal

### 2026-10-24 - Observer review fixes and lifecycle qualification

- Addressed the four findings on #8881: native session-ID option targeting
  for POSIX tmux, visible translation of Windows Job failures, a
  cross-process guard around observer lookup/creation/publication, and
  termination of the observer after its original CLI is confirmed ended.
- The observer pins its original native incarnation before its first poll,
  never adopts a successor, and treats unavailable evidence as unknown.
  Its one-second check reads only the registered CLI's native inventory
  off the UI loop, with no engine or transcript scan.
- The local focused/native packet passed 136 tests with three platform
  skips. It includes two overlapping processes and actual PSMux CLI exit,
  observer termination and subsequent clean CLI relaunch.
- Added the opt-in `mux-companion` Ubuntu portfolio for actual tmux option
  targeting, same-window navigation and CLI-exit cleanup. Its result must
  be recorded before claiming POSIX native qualification; Windows skips
  do not prove that platform. No production rollout is authorized here.

### 2026-10-24 - Same-window observer source and native controller proof

- Added explicit Show Summary / Return to CLI navigation while preserving the
  existing Ctrl+K popup. The installed-runtime observer reuses one pane in the
  same native window; CLI identity comes from launcher registration, never
  an assumed pane index. Stale, missing or ambiguous identities refuse.
- The combined UI, registration, runtime-consumer and explicit native packet
  passed (90 tests, two platform skips). The disposable native PSMux check passed three
  view round trips with one observer, one window, the original CLI pane ID
  and its unchanged process ID, including closing and reopening the observer.
  The observer invocation reached its selected
  synthetic installed prefix through the real bootstrap, including paths
  with spaces and apostrophes. This is not an installed production rollout
  or proof of rendered status chrome or mouse dispatch.
- Native tests rejected two plausible but incorrect assumptions: pane-only
  targets do not select PSMux's per-session server, and pane-scoped user
  options are accepted without persisting. Actions now qualify native pane
  targets with the session and retain the observer reference in a
  session-scoped option; CLI identity remains in the Manager registry.
- Session-level directories can differ from the exact CLI pane's worktree
  directory. Navigation verifies that registered pane's actual directory
  and native incarnation rather than relying on the session directory.
  This does not repair the independent older discovery path.
- The combined switching/attention and native chrome validation rows remain
  open. Continue with authoritative topology and attention observations,
  then two-line chrome/click acceptance; retain all earlier handoff holds.

### 2026-10-24 - Summary/Claims merged; same-window observer next

- #8822 merged after current-head Copilot approval, green required CI and
  resolution of both shutdown and target-cwd findings. Source milestone only:
  the full effort remains Active and installed rollout is not asserted.
- Re-read Step 6, the Validation Plan and continuing Journal. Closed only the
  Summary/Claims source and focused consumer-test rows. Topology, attention,
  same-window view integration and two-line/click acceptance remain open.
- A disposable native psmux 3.3.5 probe repeatedly split, selected and zoomed
  two inert panes inside one window, retaining the original CLI pane ID and
  PID. `status=2` and `status-format[1]` were accepted, with `status 2` read
  back. This proves pane/option state, not rendered status or mouse dispatch.
  Every probe session was cleaned up; no live session or installed slot changed.
- The next source slice is an explicit same-window Summary observer and
  return-to-CLI action, preserving Ctrl+K's existing popup and using the
  Manager's installation-owned invocation path. Never assume pane index zero
  or repurpose session-lineage succession as allocated-sub-agent topology.

### 2026-10-24 - Reviewed intent merged; Summary/Claims source slice

- #8815 merged after Copilot approval and required CI. Re-read the parent
  Step 6 and Validation Plan; the entire effort remains Active.
- Extended the existing Companion with Summary as initial view, actual Claims
  and secondary Recovery. The process-boundary engine client consumes the
  existing independently marked `worktree-status-bundle` contract and rejects
  wrong-worktree/malformed envelopes. No new ledger or phase inference.
- Startup and refresh reads run off the Textual event loop; unavailable facts
  and lineage failures remain visible. The existing human-gated cutover
  action is preserved. These are application tabs, not the requested mux tabs.
- Initial focused source validation: 144 tests passed, including actual
  Textual tab/cutover interaction, partial failures, literal claim labels and
  blocked-producer responsiveness. Further process-boundary and native
  acceptance is retained; no installed runtime or live mux change is claimed.
- Review identified that a default executor thread could keep the popup
  process alive after Escape. Read cancellation now uses the Manager's
  existing owned process/job boundary; startup, refresh and handoff-state
  reads share that cancellation. The expanded packet passed 155 tests,
  including a real Companion process exiting while its producer stays
  blocked, and native Windows renderer/grandchild timeout cleanup. The latter
  fixture now answers the current hosting-policy probe before its intentional
  blocking resolve. No production admission check was weakened.
- Native relay constraint from existing source: the current psmux click bind
  is deliberately withheld because it corrupts the supported root table.
  Do not silently enable it to satisfy view-tab presentation.
- The operator selected `same-window` for the CLI/Summary layout; captured
  verbatim in the linked continuation and reflected in standing intent.
  Native windows are not a substitute. A further review found target-cwd
  loss in the new runner; it now binds every observation read to the selected
  worktree without changing legacy two-argument runner callbacks. A real
  different-process-cwd versus selected-worktree handoff test passes;
  the final focused packet is 156 passed.

### 2026-10-24 - Purpose-first continuation proposed

- Resumed the existing Companion effort and preserved the separate Mux-bind
  relay's ownership. Coordination token: #8811.
- Proposed expanded standing intent and the linked purpose-first sequence.
  Summary and Claims now lead; handoff/session recovery is lower priority,
  not removed. Implementation waits for the normal reviewed intent gate.
- No live bindings, mux servers, runtime installations, claims or sessions
  were changed by this proposal.

- [2026-10-24 - Direct retained acceptance from #4369](<journal/2026/10.24 backlog-intake-90.md>)

### 2026-10-22 - Direct retained #2696 acceptance

- Retained this root's complete independent #2696 lane in Plan and
  Validation under unchanged custody. Both complementary roots need
  ordinary reviewed merge and complete current readback before any
  source supersession; all original capability/native gates remain.

### 2026-10-06 (later) — Live-tested the quiesce step itself; closed its two quiesce-specific Validation Plan items
Continuing the same day's work after PR #5491 merged: picked up the two
remaining quiesce-specific Validation Plan items (owned background task /
owned schedule present at handoff time). Rather than simulate these,
exercised the actual new skill requirement live, in this very session,
as test setup before any handoff composition began:
- Started a real background `task` agent (a 5-minute sleep-then-report job)
  and a real `manage_schedule` entry (10-minute interval) to stand in for
  genuinely owned background work.
- Quiesced both per the skill's capture-before-stop/observed-termination
  rule: waited for the background agent's actual completion (`read_agent`
  returned `status: idle` with turn output `"done"` -- not a sent closing
  message treated as sufficient) and stopped the schedule unconditionally
  (`manage_schedule action: stop`, no re-arm requested since it was a test
  artifact with no successor-side purpose).
- Both quiesce-specific Validation Plan items and Step 5's own follow-up
  checkbox are now checked. Validation Plan items 1-2 (the operator's own
  hands-on Companion-button live-tests) remain open and unaffected by this
  leg -- they need the operator's own worktree/session, not something a
  session can close on its own.

### 2026-10-06 — Live re-validated the #5455 fix; triaged 2 stray claims; new quiesce-before-trigger skill step
Resumed from an earlier handoff, whose two remaining items were:
(1) live re-validate the #5455 predecessor-retire fix
against real mechanism (not the old/broken code the original discovery ran
against), and (2) triage two flagged worktree claims.

**Claims triaged**: both flagged outbound worktree claims (one dead per
`claimant-liveness`; the other with a live claimant but the worktree itself
had 0 live sessions/mux and a terminal controller relation) were confirmed
"no commits and clean tree" by `finalize`'s own dry-run and real run -- both
empty, abandoned duplicates, now finalized/removed.

**Live re-validation**: created a real throwaway `copilot-extensions`
worktree, a real psmux session (`wt-test-predecessor`) with a genuine
multi-window live pane, and exercised the installed `context-handoff@
0.5.8-dev1` fix through actual `agent-worktrees` CLI calls (`note-handoff`,
`register-session`, `link-succession`, `handoffs-check`) -- not mocks.
First reproduced the OLD bug on purpose: registering a successor session
*without* passing `--handoff-token` silently cancelled the real pending
handoff (`link-succession`'s replay then failed with "is cancelled, not
pending"), exactly matching the root cause `handoff-core.mjs`'s
`promoteSuccessorHead` now defends against. Re-ran with the token threaded
through end-to-end as the fix does: `link-succession` succeeded,
`handoffs-check` (read-only) surfaced the real token as a retire candidate
(impossible under the old bug -- a cancelled entry is permanently excluded
from `_pending_handoff_retire_requests`), and `handoffs-check --execute`
genuinely killed the live predecessor psmux pane (confirmed gone from
`psmux list-panes`, whole test session torn down). This is the live,
non-mocked confirmation the Validation Plan's retire-mechanism item needed.
Cleaned up (killed the test psmux session, deregistered the fake sessions,
finalized the throwaway worktree -- clean tree, no commits).

**New scope from the operator mid-session**: two more live-test shapes are
needed before this effort's own Validation Plan is really complete --
cutover with the predecessor holding (a) a live, owned background
task/agent, and (b) an active, owned `manage_schedule` entry. Neither is
exercised by anything above. The operator also asked for a durable behavior
change, not just a future test: the `context-handoff` skill must *require*
quiescing owned background work (stop or wait out background
agents/shells, check in whatever partial results they produced, stop owned
schedules) before `trigger_handoff`, while still allowing the brief to
explicitly ask the successor to re-arm a named schedule/task afterward (the
default is quiesce-and-report, re-arming is a deliberate named exception).
Added a new "Quiesce owned background work before triggering" section to
`plugins/context-handoff/skills/context-handoff/SKILL.md`, mirroring the
existing "Sync before triggering" section's shape and its
context-pressure escape hatch. Tracked the two new live-test shapes as
unchecked Validation Plan items and a new Step 5 sub-item -- deliberately
left for a follow-up leg since they need a real owned background task/
schedule in flight at handoff time, which this leg's own throwaway-worktree
test didn't include.

### 2026-10-05 — Predecessor-retire root cause found and fixed (mux-bind-relay follow-up)
Live-driving this effort's own two Validation Plan items (operator ask,
chained from the mux-bind-relay effort) surfaced the actual predecessor-
retire bug: a full claim -> spawn-successor -> consume cycle correctly
transitioned `resolved_head_session` to the new session, but the
predecessor's pane was never retired, and `handoffs-check --execute` found
nothing to retire. Root cause: `handoff-core.mjs`'s `promoteSuccessorHead`
(called by BOTH `consumeFileHandoff` and `consumeDispatchHandoffTask` on
every successful consume) never passed the real handoff token through to
`agent-worktrees link-succession`. Without `--handoff-token`,
`tracking_lifecycle.link_succession`'s own `"handed-off"` branch invents a
synthetic `manual-<N>` placeholder token instead -- and `open_handoff`'s own
"cancel any other pending handoff for this predecessor" rule fires when that
placeholder is opened, silently cancelling the REAL handoff entry as a side
effect. A cancelled entry is never `linked`, so
`_pending_handoff_retire_requests`'s own `state != "cancelled"` filter
permanently excludes it from ever being retired -- the predecessor pane sits
there forever, correctly superseded in spirit (head already moved) but never
cleaned up. Fixed by threading the real token (`taskId`/`record.id`) through
`safePromoteHead` -> `promoteSuccessorHead` -> `--handoff-token`. Added
regression tests (promoteSuccessorHead now asserts the flag reaches the CLI
call; the no-token shape stays unaffected) -- 103/103 in
`handoff-core.test.mjs`.

### 2026-09-27 — Kickoff
- Effort created out of live operator feedback on the Phase 8 (read-only
  pending-handoff headline) slice of `worktrees-pivot-ux-overhaul`. Original
  ask ("Cut over" button that just works) ran into a real, reviewed safety
  gate (PR #3041) withholding the pending-handoff ledger write in
  `manual-only` mode -- confirmed by reading `triggerHandoff()`'s exact
  gating rather than guessing. Operator confirmed: full-spawn behavior, no
  auto-repair/auto-file (operator will tell the current session to do that
  themselves), revise the vision first, and specifically asked for the
  `save`/`trigger`/`force` three-way split that became Step 2. Tracked as
  its own effort (not reopening the now-closed `worktrees-pivot-ux-overhaul`)
  per the operator's own call, cross-linked to `handoff-live-cutover` (#2249)
  as the mechanism this builds an on-demand counterpart for.

### 2026-09-28 — Step 1 (vision) merged; Step 2 (context-handoff force) complete
- Step 1 landed as PR #4371: `visions/mux-companion` now carries
  `manual-cutover-trigger` and `post-cutover-head-verification` as Features,
  with three new/revised Behaviors precisely bounding the exception (never
  inferred, never authors a handoff, detects-never-repairs).
- Step 2: `triggerHandoff()` gains `force = false`; `autoEnabled =
  automaticHandoffEnabled(mode) || force`. `handoff-cli.mjs trigger` gains
  `--force`, registered as a boolean flag (not a value-taking one).
  Regression-tested against the exact PR #3041 scenario (omitting force
  preserves manual-only's prior behavior byte-for-byte: `noteHandoff`/
  activity/bridge all skipped, `automaticCutoverDisabled: true`); a new
  force=true test proves all three signals now fire.
- **Near-miss worth recording**: an initial CLI-level end-to-end test
  (`trigger --force` via a real spawned subprocess, unmocked) actually
  invoked live `agent-worktrees`/`agent-dispatch` provisioning inside an
  isolated temp HOME -- 164s runtime, a fresh `agent-dispatch` runtime
  install (cryptography/rust bindings and all), and an EPERM cleanup
  failure on a locked `.pyd` from the mid-test install. Removed that test
  in favor of a fast, side-effect-free structural check (the CLI wires
  `force: Boolean(args.force)` through and registers `force` as a boolean
  flag) -- the actual arming behavior is already fully covered by
  `handoff-core.test.mjs`'s properly-injected/mocked tests. Lesson for the
  remaining steps: never spawn the real CLI for a code path that can reach
  live provisioning, even in an "isolated" temp HOME.
- Tests: `handoff-core.test.mjs` 52/52 (2 new); `cli-parity.test.mjs` 8/8
  (1 new, replacing the risky one); full `tests/*.test.mjs` suite 142
  passed/11 skipped/0 failed (unchanged skip count from baseline).
  `tools/check-module-size.py` clean.
- **Step 3 complete**: `handoff-cutover-trigger --worktree-id <id>`, a thin
  wrapper around `_monitor_maybe_process_handoff_record` (unchanged,
  untouched -- the exact function the resident daemon calls per tick).
  Deliberately no `--execute`/preview split like `handoffs-check` has: the
  underlying check functions this wraps (`_monitor_pending_handoff_request`)
  have a one-shot CLAIM side effect baked in, so a "peek first, execute
  later" two-call design would silently self-block on its own claim. Reports
  `head_before`/`head_after`/`session_count_before`/`_after`/`changed` so a
  caller (the Companion, Step 4) can tell whether anything happened without
  needing its own unsafe second call into the daemon's check logic.
  Wired via the same lazy dispatch-table pattern every other verb in
  `__main__.py` uses (`handoff_cli.py` owns the parser + handler; `__main__`
  re-exports it). Nudged the already-grandfathered `__main__.py` ceiling by
  3 lines (7072 -> 7075, `tools/module-size-baseline.json`) -- the same,
  already-established registration boilerplate every other verb pays.
- Tests: 4 new in `test_handoff_cutover.py` (unknown worktree errors;
  no-actionable-handoff is a silent successful no-op; a successful spawn's
  head/session-count change is reported; human-readable output for the
  no-op case) -- all green (107/107 in that file). Broader
  `test_status_segment.py`/`test_tracking.py` unaffected (262/262).
  `tools/check-module-size.py` clean after the baseline nudge.
- **Next up**: Step 4 (Mux Companion "Cut over" button + refreshable
  session-lineage/head-match verification) -- the last step.

### 2026-09-28 — Step 3 revisited + Step 4 complete: this effort's Plan is fully executed
- **Revisited Step 3 before building the Companion on top of it**: the
  original design assumed the Companion would separately call
  context-handoff's `trigger --force` (Step 2) to arm the ledger, THEN call
  `handoff-cutover-trigger`. Building Step 4 surfaced that this means a
  SECOND process boundary (the Companion, Python, would need to locate and
  shell out to a Node CLI too) for something `handoff-cutover-trigger`
  could arm itself, in pure Python, using data ALREADY available to it:
  the session-state `handoff-request.json` marker `writeSessionStateHandoff`
  writes UNCONDITIONALLY (regardless of mode) already carries the exact
  `handoffId`/token `tracking.open_handoff` needs. Added
  `_arm_pending_handoff_from_session_state` to `handoff-cutover-trigger`:
  before running the daemon's own choreography, it scans the worktree's
  registered sessions for an unconsumed session-state marker not yet
  reflected in the ledger, and opens it directly (idempotent -- a token
  already present is left alone). This means `handoff-cutover-trigger`
  now works standing completely alone, whether or not `--force` was ever
  separately invoked -- Step 2's `--force` remains independently valuable
  as a plain CLI escape hatch (matching the operator's own "prepare"/
  "trigger"/"force" mental model) but is no longer load-bearing for the
  Companion's own "Cut over" button. 2 new tests
  (`test_arms_ledger_from_unconsumed_session_state_marker_before_
  processing`, `test_does_not_rearm_an_already_open_token`) -- 6/6 in
  `TestCmdHandoffCutoverTrigger`, 109/109 in the full file.
- **Step 4**: `handoff_client.trigger_cutover()` shells out to
  `handoff-cutover-trigger --json`; the Companion gains a "Cut over" button
  (enabled only when `pending_handoff` is set) and a "Refresh" button, both
  driving a new `_refresh_view()` that reloads `_load_current_worktree()`
  and repaints status/lineage/pending-handoff/button-enablement in place --
  satisfying post-cutover-head-verification's "refreshable on demand, not
  just at popup-open" requirement without needing a separate head-match
  marker (the lineage table's existing `●` head marker already IS that,
  once reloaded). A "Cut over" outcome (or failure) renders as a message
  below the status explanation. Per the operator's own explicit request,
  NO auto-repair or auto-file-a-bug action exists anywhere in the Companion.
- **Found and fixed a genuine pre-existing bug while writing the first-ever
  real Textual mount test for this file**: `mux_companion.py`'s CSS used
  Rich-style numbered grey names (`grey19`/`grey27`/`grey42`/`grey70`),
  which the installed Textual version's CSS parser rejects outright
  ("Did you mean 'grey'?") -- the app had literally never been mounted by
  any test before (the file's own docstring said so explicitly: "not any
  Textual rendering"), so this had shipped broken and unnoticed. Replaced
  every occurrence with equivalent hex (`#303030`/`#454545`/`#6c6c6c`/
  `#b2b2b2`) across the WHOLE file (my own new CSS rules used the same
  broken names, too, copying the existing style). Verified by adding one
  real `run_test()`-driven end-to-end test (compose -> mount -> click
  "Cut over" -> observe the repainted status/lineage/button state) --
  the first genuine integration-level coverage this module has ever had,
  not just its pure-function unit tests.
- Tests: `test_mux_companion.py` 20/20 (6 new: action-message rendering,
  4 pure `_cut_over()` behavior tests, 1 end-to-end `run_test()` test).
  Full `tests/` suite green apart from 3 already-known-pre-existing,
  unrelated failures (the machine-load-sensitive Picker modal-timing flake
  documented in earlier phases; two genuinely unrelated failures --
  `test_mux_daemon.py`'s backstop-cadence test and `test_trusted_
  materializer_parity.py`'s editable-reference test -- both reproduced
  identically on an unmodified checkout via `git stash`). `tools/check-
  module-size.py` clean.
- **This is the last step in this effort's Plan.** The Validation Plan's
  two live-test items are the operator's own to exercise (a real pending
  handoff, a real manual `/clear` + paste-seed resume) -- left unchecked
  here since they need the operator's own worktree/session, not something
  checkable from within a dev session. Everything buildable is built and
  merged.
