# mux-companion-manual-cutover-diagnostics — Manual Force-Cutover Trigger + Post-Cutover Head Verification

- **Slug:** `mux-companion-manual-cutover-diagnostics`
- **Repo:** copilot-extensions — the `context-handoff` + `agent-worktrees` +
  `worktree-manager` (Mux Companion) packages
- **Branch(es):** per-step worktrees → landed to `dev`
- **Created:** 2026-09-27
- **Status:** Active
- **Vision:** vision-extending against [`visions/mux-companion`](../../../visions/mux-companion/README.md)
- **Umbrella issue:** [#4369](https://github.com/ThomasMichon/copilot-extensions/issues/4369)
- **Current Sidecar coordination:** [#8811](https://github.com/ThomasMichon/copilot-extensions/issues/8811)
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

The purpose-first continuation also owns the same-window live Sidecar: an
agenda-level subway timeline, owner-qualified push observations, work topology
and truthful input/compaction indicators. Its current implementation amendment
is [Step 7](sidecar-live-timeline.md). This adds source work without retiring the
original recovery, manual/head, popup or Relay acceptance.

## Participants

| Participant | Scope | Reached via |
| --- | --- | --- |
| Sidecar source coordinator | Step 7 plan admission, bounded source integration, review and whole-root completion readback | Retained source workspace under the external harness |
| Incumbent diagnostics and Relay custodians | Every original Step 1-6, direct intake, manual/head/native and current resource responsibility | Existing Coordination and complementary Mux-bind Relay root |

Step 7's bounded contributors and non-overlapping integration roles are declared
in its linked plan. A participant label does not transfer another owner's claim.

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

### Current Step 7 source approval

The [local resident recovery amendment](resident-recovery.md) captures the
operator's maintenance-check/backoff/normal-CLI direction. It changes the next
source approach, not the no-rollout or passive-reader effect boundaries.

The operator authorized continuing the implementation plan and starting source
work when ready. [Step 7](sidecar-live-timeline.md) captures the exact request,
strict pre-compaction warning definition and retained scope. Its plan/vision
amendment must clear normal review before source execution. It authorizes no
installed rollout, live user-Mux rebinding, answer automation, head repair,
runtime threshold change, cutover or unrelated resource action.

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

**Storage/discovery clarification:** the operator requested design/architecture
for durable agenda/history retention and predictable discovery shared by Manager,
logging and archival, including the risk of checkout cleanup deleting the data.
The [verbatim capture and proposed lifetime design](worktree-state-lifetime.md)
preserve both clarifications. No live relocation, cleanup or collection is
authorized by that design request.

**2026-11-02 continuation:** turn the subway-map guidance into a real live
Sidecar with bottom-head/upward-history virtualization, owner push feeds and
input/problem indicators. The operator defines compaction below 65% of
qualified pre-context usage as premature and authorized plan completion followed
by source execution. Exact capture, configurable-policy interpretation and
implementation/validation are in [Step 7](sidecar-live-timeline.md).

**2026-11-04 visual evidence:** retain dated progress captures and curated
synthetic rendered screens as repeatable repository comparisons. The
[first visual packet](../../../worktree-manager/tests/visual_baselines/README.md)
does not replace native-client acceptance or installed evidence.
The operator subsequently rejected the diagnostic-heavy captures as messy and
error-laden. [Step 7's captured correction](sidecar-live-timeline.md#operator-correction-after-viewing-progress-captures-2026-11-04)
restores the original sparse concept hierarchy and requires a separate healthy
full-app demonstration without hiding genuine source failures.

**2026-10-24 continuation:** prioritize purpose-first Summary and Claims over
handoff session selection; add a switchable work-topology summary pane,
attention indicators, a permanent claims status line and clickable view tabs.
The 2026-10-25 operator revision replaces required clickable view tabs with
visible mode-switching hotkey guides; the two-row and same-window requirements
remain.
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

- [x] Choose and document the Companion's Escape policy. IF Escape should dismiss, bind
  it explicitly; Textual has no default Escape-to-quit behavior. Preserve the
  conditional choice rather than inventing a requirement that every key must exit.
  The existing explicit Escape binding is retained and documented in the
  [Manager guide](../../../worktree-manager/README.md#in-session-mux-companion);
  the [current source controls](journal/2026/11.05-companion-popup-input.md)
  do not qualify installed or Windows clients.
- [x] Deliberately define/document Ctrl+C, Ctrl+Q and keyboard/button activation.
  Preserve the empirical Ctrl+C-toast versus Ctrl+Q/Exit clean-exit distinction unless
  an actual reviewed behavior change supersedes it; a stale relay comment is not proof
  of the chosen input contract.
  The same guide now records the unchanged Ctrl+C quit hint, Ctrl+Q clean quit,
  Tab/Shift+Tab and Enter/Space button activation. Actual attached-client source
  tests exercise Ctrl+C followed by Escape, Ctrl+Q or Exit keyboard activation.
- [ ] Ensure all chosen clean exit paths terminate only the on-demand Companion, retain
  no persistent Companion state and allow the owning display-popup -E to close without
  another key. Preserve focus/pane return and error/cancellation behavior; dismissal
  never invokes Cut over, consumes a baton or retires a worktree/session.
  Private POSIX source controls cover ready, blocked and failed owner reads,
  clean zero exit, held-reader retirement and original CLI input/focus return.
  They are not installed/native Windows or complementary Relay acceptance.
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

- The [independent expected-head source sequence](expected-head-observation.md)
  realizes P3/P4 through the existing owner receipts and an optional passive
  comparison. Plan #10865 is merged; #10870 captures optional original/consumer
  identity in source. #10884 supplies the passive independent comparison reader;
  #10893 supplies the optional Recovery consumer with 200 isolated controls.
  Legacy identity stays unknown and all original clauses below remain unchanged.

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

The [timeline/subway and live-worktree guidance](timeline-guidance.md) supplies
generic presentation examples for this same continuation. It mixes lifecycle
events with work packages while keeping the current bound effort primary and
creation context secondary. This documentation contribution does not close
topology, native chrome, manual/head, app-input, relay or rollout acceptance.

- [x] Land reviewed [purpose-first intent and sequence](purpose-first.md)
      and the revised `visions/mux-companion` before implementation.
- [x] Reuse existing authoritative purpose, summary and claims sources in
      the default Textual dialog; retain recovery as a secondary view.
      Source merged in #8822; installed/native rollout remains separate.
- [ ] Implement a compact topology presentation for phases, sub-agents,
      bridges, downstream worktrees, open PRs and accumulated tool-call stats,
      using the same sources as other companion clients.
      The [owner-qualified PR-state realization](sidecar-pr-state.md), reviewed
      in #10920, plans
      an optional exact-association, explicit passive point-read using the
      existing snapshot owner. #10935 delivers R1's declared on-demand
      association fact; #11194 delivers R2's optional one-PR consumer.
      Claims alone remain unknown PR-state evidence;
      full live topology and every native/installed gate remain open.
- [x] Implement explicit CLI/summary pane switching with attention indicators
      that preserve the live CLI and its answer boundary.
      Source merged in #8881 and #8941; installed/native-client attention
      acceptance remains separately unchecked in Validation.
- [ ] Deliver a permanent second claims status line and actionable mode-switch
      hotkey guides
      through the Mux-bind owner, with tmux/psmux qualification.

### Step 7 - Live Sidecar timeline and owner observations (#8811)

- [ ] Complete S0-S5 and the own Validation Plan of the
      [live Sidecar implementation](sidecar-live-timeline.md), using its
      [proposed observation contract](sidecar-observation-contract.md).
      Preserve every original parent clause, owner and evidence tier; a model,
      widget, provider or source PR alone does not complete this root.
      S0, S1, the selected worktree receiver/Manager adapter, rich agenda
      owner reader, safe process-local save capture and known-session Bridge
      provider are merged. #10199 also delivers qualified resident mutation
      intake, bounded Bridge membership and the reviewed data-lifetime design.
      #10224 delivers D1's durable placement and passive discovery source.
      #10238 delivers D2's explicit managed initialization and complete export.
      #10251 delivers D3's rich Agenda consumer and its real isolated join.
      #10392 delivers passive Bridge membership consumption and the first
      isolated three-owner demonstration. #10435 delivers the supported
      selected-session Bridge event/history consumer and lifecycle controls.
      #10447 delivers D4's policy-gated local collection/readback library and
      namespace/retention fencing, not owner discovery or scheduled collection.
      #10488 delivers passive project-wide retained-descriptor inventory at the
      Worktrees owner. #10495 delivers Efforts-owned contributor provenance from
      the same verified export cut. #10500 delivers Logger's optional complete
      provenance retention and fresh local readback. #10503 delivers explicit
      passive exact-repository Worktrees/Efforts provider discovery. #10512
      delivers explicit policy-first managed export/collection composition with
      finite owner commands and fresh local readback. #10520 connects that boundary
      to the existing tick through strict machine-local opt-in, shared bounded
      execution and independent collection-context scheduling. D4's source
      integration is complete. #10528 delivers D5's conservative checkout-local
      preservation and independent managed-data retirement source controls;
      #10539 qualifies supported format-2 local rescue/readback before actual
      isolated finalization without releasing the original checkout hold.
      #10551 delivers S3's bounded local ACP waiter projection; #10557 delivers
      its negotiated optional provider/CLI wire and state-only delivery.
      #10579 delivers its conditional Manager consumer, independent live-input
      display and source-fixture visual comparisons. Supported local current-input
      source acceptance closes; installed/native and unsupported-source coverage
      remain separate.
      #10595 qualifies supported private-source history/live browsing, actual
      waiter clearing, gap/Refresh recovery and complete observer cancellation
      on the joined app. Bounded render/performance and native acceptance remain
      separate.
      #10605 qualifies deterministic full-window resource/viewport controls and
      source-process performance observations, with geometry/reference reuse
      and short-content wheel history intent. Native/installed performance and
      the complete repaint target remain separate.
      #10612 qualifies the actual app through a private attached tmux client,
      with history/live browsing, resize, pane/PID retention and owned cleanup.
      The wire-valid synthetic owner and server-buffer captures do not qualify
      installed behavior, Windows/PSMux, popup or native-frame performance.
      #10642 delivers the requested sparse presentation correction, truthful
      pause/error/recovery indicators and healthy ordinary/narrow source
      demonstrations with twelve reviewed rendered comparisons. This closes
      only the linked presentation-correction items, not S2-S5 or this root.
      #10670 qualifies the actual private tmux popup's unchanged input policy,
      blocked/failed-read dismissal and original CLI return. Its complete
      invocation log includes a rejected-attempt negative control. Native
      Windows/PSMux, installed, Relay and original manual/head gates stay open.
      #10680 carries reported work labels through the actual Bridge semantic
      source and Manager codec, including invalid-display-text fallback and
      refreshed source-bound HTTP-52 evidence. It does not qualify current
      concurrency, allocated custody, lifetime totals or whole-source coverage.
      #10686 gives valid reported subagent IDs distinct source-scoped lanes,
      with reporter fallback and no inferred parent edge or current state.
      This is finite consumer attribution, not accumulated-statistics or
      whole-S2 qualification.
      The [owner-qualified statistics follow-up](sidecar-tool-statistics.md)
      admits a source-range count of reported tool-start occurrences.
      Plan #10704 and internal owner projection #10707 are merged.
      The latter qualifies bounded construction/lifecycle statistics and
      atomic native-range reads without changing the observation wire.
      #10712 adds the negotiated optional provider/parser/CLI and exact
      atomic native-range qualification. #10718 consumes that projection on
      the existing selected reader, with independent optional rollback,
      strict source-range checks, coalesced callbacks and sparse labels.
      Its actual large-range/default-window and ordinary/narrow controls
      close only the bounded statistics amendment. Unique-call totals,
      session-lifetime activity and current concurrency remain unknown.
      #10723 qualifies the sparse source application through an attached tmux
      client with the actual private Worktrees/Agenda/Bridge owners, independent
      history, live purpose/count updates and exact reader/pane retirement.
      Its current-context RPC research establishes no qualified PRE relationship;
      original source/native/manual/Relay and installed gates remain open.
      #10744 delivers the
      [maintenance-aware ordinary-write recovery](resident-recovery.md):
      maintenance admission, brief discovery retry, normal ensure, explicit
      refusal envelopes and preserved post-effect child/retirement custody.
      Its private canonical transaction reaches resident mutation intake;
      #10758 adds the actual private normal-ensure process packet and its
      maintenance/no-spawn, warm reuse and exact cleanup controls. This closes
      the finite recovery amendment, not an installed daemon wake, lossless
      legacy history or the whole S2 row.
      #10766 qualifies the actual private Windows PSMux/ConPTY client over
      the unmodified app with a synthetic owner: history/live browsing,
      resize, Follow-live, original CLI identity and exact reader/Job cleanup.
      Its 84-case native packet has zero skips. The first missing-banner
      startup failure remains unexplained; this is not startup-stability,
      installed, popup/Relay, headed-pixel or native-performance acceptance.
      #10777 qualifies private native Windows new-home allocation/exact retry,
      failed final-barrier acknowledgment/retry and complete agenda export with
      copied readback. Its three native controls have zero skips. Those
      controls do not qualify combined native owner composition, installed
      operation, archive acknowledgment or D5 operational acceptance.
      #10784 qualifies the private actual Worktrees/Agenda/Bridge source owners
      through the Windows PSMux/ConPTY client, including history/live keyboard
      behavior, healthy full/narrow views and exact reader/observer exit before
      further original-CLI input. Its reviewed Windows packet passes 112 cases
      with zero skips. The [readback](journal/2026/11.05-sidecar-windows-owner-composition.md)
      retains all ten earlier failed runs, a separate cold membership-read
      budget reliability gap, startup-stability uncertainty and every original
      installed, performance, popup/Claims/Relay, manual/head and D5 gate.
      #10835 adds the optional actual-call membership diagnostic and corrects
      three native fixture boundaries. Its exact source passes 120 Windows,
      117 Linux Companion and 43 root-attention controls. The
      [diagnostic readback](journal/2026/11.05-sidecar-membership-diagnostics.md)
      retains every failed run and the unexplained 50 ms cold-read gap;
      production budgets and retries are unchanged.
      #10848 closes the separately reproduced unrelated-history scan gap
      with writable-bootstrap owner indexes and current native lookup/client
      qualification. The same readback retains passive old-store behavior,
      the original budgets and the unexplained one-row cold timeout.
      #10901 adds paired elapsed-clock diagnostics and reproduces a separate
      CPython 3.12 Windows deadline-quantization defect in the unchanged budget.
      Its 129-case native packet and two lookup controls pass with zero test
      skips; the historical cold-read cause remains unproven. #10907 delivers
      the precise-clock correction with the same 50 ms/VM limits and current
      Windows qualification of exact creation and sixteen non-early checks.
      #10855 qualifies the supported strict owner-codec boundaries and
      rejects contradictory membership replay without imposing contiguous
      owner-local sequence numbers. Its 242 source controls do not complete
      history/append-failure, universal-source or native/installed acceptance.
      #10858 separately qualifies the supported history/live splice and gap
      criterion through 274 owner/model/actual-three-source controls. It does
      not extend universal source coverage or the original parent gates.
      The [known-output presentation follow-up](sidecar-output-presentation.md)
      addresses actual message/thought bursts that still overwhelm normal
      work rows. Plan #10689 and implementation #10698 are merged.
      The latter qualifies bounded groups, raw Evidence, explicit one-page
      history demand, stable group anchors and immediate Follow-live alignment
      through 108 source checks and fourteen rendered comparisons.
      It grants no new effect
      authority or closure of the retained source/native/manual gates.
      D5 actual relocation, restoration/reclamation, archive acknowledgment and
      installed/native qualification remain open.
      Current answerability/PRE qualification, archival/rescue, combined native integration and
      whole S2-S5 and original root gates remain open.

## Validation Plan

- [x] Step 6 finite PR-association provider (R1 of the
      [PR-state realization](sidecar-pr-state.md)): the optional declared
      `--pr-associations` status read projects exact stored associations and
      fail-closed target bindings without acquiring provider state. #10935
      merged after current-head independent review and every required green
      check; the complete root and six linked contracts were read immediately
      afterward. R2's consumer, joint PR-state validation and every original
      parent gate remain open.
- [x] Step 6 finite PR-association consumer (R2 of the
      [PR-state realization](sidecar-pr-state.md)): the Graph presents recorded
      associations separately and performs one explicit, cancellable passive
      `pr-watch snapshot` point read per operator selection. #11194 merged after
      independent review and every required green check. Joint PR-state
      validation rows 1-4, full live topology and every original parent gate
      remain open.

- [x] Step 7 finite activity-evidence distinction: actual ACP callback reports,
      owner-qualified start occurrences and canonical resource claims retain
      separate meanings; missing/represented attribution does not imply a
      running count or parent edge. #10915 qualifies 133 isolated controls and
      explicit unknown execution/concurrency in expanded Evidence. Current
      execution remains unqualified; complete source coverage and original
      parent gates remain open.

- [x] Step 7 finite membership deadline precision: reject true elapsed and VM
      exhaustion without coarse Windows tick-driven early expiry. #10907
      qualifies three fail-before decisions, unchanged writer/read fences,
      source-bound HTTP52 capture and current Windows budget creation/no-early
      controls. Historical cold-read causality, startup, installed operation
      and every original parent gate remain open.

- [x] Independent expected-head H3 optional Recovery consumer: 200 isolated
      process/codec/Textual controls qualify independent facts, Refresh/reopen,
      optional rollback/errors, selection/late-result fences and exact
      native Linux blocked-child cleanup. #10893 merged after current-head
      independent approval and every required green check; the complete root
      and five linked contracts were read immediately afterward. Original
      direct-human/manual/head, installed/native and whole-parent gates remain.

- [x] Independent expected-head H2 passive provider: 171 isolated source
      controls qualify actual consume receipts, raw activation and delivery
      ordering, distinct durable/Picker facts, bounded no-wake reads and
      explicit unknown/error outcomes. #10884 merged after exact-head
      independent approval and every required green check; the complete root
      and five linked contracts were read immediately afterward. H3 and all
      original manual/head/native gates remain open.

- [x] Independent expected-head H1 identity capture in existing owner records:
      36 actual binding and 147 handoff/CLI controls preserve exact optional
      identity, unknown outcomes, legacy replay and deduplicated original
      metadata. #10870 merged after current-head independent approval and every
      required green check; the complete root and five linked contracts were
      read immediately afterward. H2 comparison, H3 display and all original
      manual/head/native gates remain open.

- [x] Step 7 finite supported history/live splice and gap qualification:
      274 owner/model/actual-three-source controls preserve explicit
      retention/replacement/append failure and distinguish coalesced
      observations from canonical accepted history. See the
      [source readback](journal/2026/11.05-sidecar-history-gaps.md).
      Universal source, installed/native and whole-parent gates remain open.

- [x] Step 7 finite supported owner-codec boundaries: exact fields and
      enumerations, numeric/boolean distinctions, cursor/source identity,
      duplicate/malformed data and no inferred rendering/custody authority.
      #10855 qualifies 242 source/private-process/Textual controls, including
      actual two-owner sequence gaps. Universal source coverage and the
      native/installed and original parent gates stay open.

- [x] Step 7 finite exact-owner read scaling: avoid unrelated-registry scans
      through ordinary writable-bootstrap indexes; preserve read-only and
      existing-only non-preparation, exact owner normalization, malformed
      legacy JSON and old-store VM cutoffs. #10848 qualifies isolated source
      controls and two native Windows lookup/bootstrap controls alongside
      the unchanged 120-case actual-client packet. This does not establish
      the cause of the separate cold timeout or complete S2-S5.

- [ ] Step 7: verify the complete source, real consumer, qualified pre-context,
      history/viewport, native and bounded-performance acceptance in
      [the linked plan](sidecar-live-timeline.md#validation-plan). Unsupported
      evidence stays unknown/open; original manual/head/Relay gates remain.

- [x] Step 7 presentation correction: distinguish paused, missing and genuinely
      failed sources; preserve actual diagnostics and qualified recovery;
      demonstrate sparse healthy/full/narrow and separately degraded views.
      #10642 qualifies source/private-process/Textual comparisons, not installed
      behavior, native performance or the original popup/manual/head gates.

- [x] Step 7 finite Windows private-owner/client composition: actual Worktrees,
      managed Agenda and Bridge source owners, independent history/live input,
      sparse full/narrow views, retained native exit handles and unchanged
      original CLI identity accepting further input. #10784's reviewed source
      passes 112 Windows cases with zero skips. This is not whole S5,
      startup-stability, installed, native-performance or original Relay
      acceptance; the separate cold membership-read budget gap remains open.

- [x] Purpose-first: confirm the captured continuation preserves the request
      and distinguishes requested intent from agent-recommended sequencing.
- [x] Purpose-first: unit and real Textual consumer tests for Summary/Claims,
      long values, missing/stale/error sources and secondary recovery actions.
      The 156-test packet includes real process shutdown and target-cwd isolation.
- [ ] Purpose-first: contract tests for graph identities, reported phases,
      agent/bridge/worktree/PR relationships and authoritative tool totals.
      The [PR-state proposal's validation](sidecar-pr-state.md#validation-plan)
      preserves distinct claim/record/provider facts and unchanged action gates.
- [ ] Purpose-first: prove actual CLI/summary switches preserve the original
      session/process/pane and surface question/idle attention without answers.
- [ ] Purpose-first: qualify two-line status and real mode-switch key dispatch on
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

- [2026-11-08 - PR record, cross-version process and snapshot codec controls](sidecar-pr-state.md#2026-11-08---actual-record-cross-version-process-and-snapshot-codec-controls)
- [2026-11-08 - PR narrow rendering and blocked-read dismissal controls](sidecar-pr-state.md#2026-11-08---narrow-rendering-and-blocked-read-dismissal-controls)
- [2026-11-06 - R2 PR-association consumer merged](sidecar-pr-state.md#2026-11-06---r2-consumer-source-merged)
- [2026-11-06 - R1 PR-association provider merged](sidecar-pr-state.md#2026-11-06---r1-provider-source-merged)
- [2026-11-05 - Optional expected-head Recovery consumer](journal/2026/11.05-companion-expected-head-display.md)
- [2026-11-05 - Passive independent expected-head provider](journal/2026/11.05-companion-expected-head-reader.md)
- [2026-11-05 - Original handoff and consumer identity capture](journal/2026/11.05-companion-expected-head-identity.md)
- [2026-11-05 - Reviewed independent expected-head source sequence](journal/2026/11.05-companion-expected-head-plan.md)
- [2026-11-05 - Supported history/live splice and gap qualification](journal/2026/11.05-sidecar-history-gaps.md)
- [2026-11-05 - Strict codec and retained membership replay boundaries](journal/2026/11.05-sidecar-codec-boundaries.md)
- [2026-11-05 - Cold membership diagnostics and native fixture corrections](journal/2026/11.05-sidecar-membership-diagnostics.md)
- [2026-11-05 - Windows actual-owner client qualification and retained read-budget gap](journal/2026/11.05-sidecar-windows-owner-composition.md)
- [2026-11-05 - Native Windows allocation/export qualification and remaining owner composition](journal/2026/11.05-sidecar-windows-durability.md)
- [2026-11-05 - Actual Windows client qualification and retained startup uncertainty](journal/2026/11.05-sidecar-windows-client.md)
- [2026-11-05 - Maintenance-aware resident recovery and retained child custody](journal/2026/11.05-sidecar-resident-recovery.md)
- [2026-11-05 - Native three-owner composition and retained PRE boundary](journal/2026/11.05-sidecar-native-owner-composition.md)
- [2026-11-05 - Current-context RPC is not pre-compaction evidence](journal/2026/11.05-sidecar-context-metadata-boundary.md)
- [2026-11-05 - Manager owner-qualified statistics and sparse labels](journal/2026/11.05-sidecar-tool-statistics-manager.md)
- [2026-11-05 - Optional owner-qualified tool-statistics provider](journal/2026/11.05-sidecar-tool-statistics-provider.md)
- [2026-11-05 - Internal owner-qualified tool-start projection](journal/2026/11.05-sidecar-tool-statistics-owner.md)
- [2026-11-05 - Reviewed owner-qualified tool-observation plan](journal/2026/11.05-sidecar-tool-statistics-plan.md)
- [2026-11-05 - Bounded output groups and stable short-canvas navigation](journal/2026/11.05-sidecar-output-groups.md)
- [2026-11-05 - Reviewed bounded source-output presentation amendment](journal/2026/11.05-sidecar-output-plan.md)
- [2026-11-05 - Reported actor attribution without runtime inference](journal/2026/11.05-sidecar-reported-actors.md)
- [2026-11-05 - Reported work labels on the actual Bridge source path](journal/2026/11.05-sidecar-reported-work-labels.md)
- [2026-11-05 - Actual private popup input and clean exit](journal/2026/11.05-companion-popup-input.md)
- [2026-11-05 - Sparse presentation correction and truthful source diagnostics](journal/2026/11.05-sidecar-sparse-presentation.md)
- [2026-11-04 - Actual Companion through an isolated native tmux client](journal/2026/11.04-sidecar-native-client.md)
- [2026-11-04 - Bounded rendering source qualification and performance observations](journal/2026/11.04-sidecar-bounded-render.md)
- [2026-11-04 - Joined history/live and observer lifecycle source qualification](journal/2026/11.04-sidecar-joined-lifecycle.md)
- [2026-11-04 - Live-request contract and bounded producer source](journal/2026/11.04-sidecar-live-request-contract.md)
- [2026-11-04 - Supported local rescue qualified; live request evidence next](journal/2026/11.04-sidecar-rescue-qualification.md)
- [2026-11-04 - Checkout preservation merged; rescue qualification continues](journal/2026/11.04-sidecar-checkout-preservation.md)
- [2026-11-04 - Existing-tick collection merged; legacy preservation next](journal/2026/11.04-sidecar-existing-tick-collection.md)
- [2026-11-04 - Explicit managed export composed; existing-tick integration next](journal/2026/11.04-sidecar-managed-export-composition.md)
- [2026-11-04 - Passive archival providers merged; existing-tick orchestration next](journal/2026/11.04-sidecar-logger-provider-discovery.md)
- [2026-11-04 - Logger contributors merged; selected-provider orchestration next](journal/2026/11.04-sidecar-logger-contributors.md)
- [2026-11-04 - Export contributors merged; Logger integration remains open](journal/2026/11.04-sidecar-export-contributors.md)
- [2026-11-04 - Retained inventory merged; contributor discovery next](journal/2026/11.04-sidecar-retained-inventory.md)
- [2026-11-04 - Typed collection library merged; owner orchestration remains open](journal/2026/11.04-sidecar-collection-library.md)
- [2026-11-04 - Selected-session Bridge consumer merged; archival and qualification next](journal/2026/11.04-sidecar-known-session-consumer.md)
- [2026-11-04 - Bridge membership consumer merged; known-session observations next](journal/2026/11.04-sidecar-bridge-membership-consumer.md)
- [2026-11-03 - Rich Agenda consumer merged; Bridge composition and archival remain open](journal/2026/11.03-sidecar-rich-agenda-consumer.md)
- [2026-11-03 - Managed agenda initialization/export merged; Manager consumers next](journal/2026/11.03-sidecar-managed-agenda-export.md)
- [2026-11-03 - Durable placement source merged; efforts integration next](journal/2026/11.03-sidecar-durable-placement.md)
- [2026-11-03 - Native intake and membership merged; durable discovery next](journal/2026/11.03-sidecar-native-membership.md)
- [2026-11-02 - Owner readers and first Manager attachment merged; native intake next](journal/2026/11.02-sidecar-owner-consumers.md)
- [2026-11-02 - Selected worktree receiver merged; source joins next](journal/2026/11.02-sidecar-observation-receiver.md)

### 2026-11-02 - Predictable durable agenda discovery requires an owner contract

- The rich-reader consumer reached an actual admission gap: coarse publication
  identifies the store but deliberately carries no state-home locator. The
  resident does not retain the transient home used by the agenda publisher.
- The operator clarified that Manager and later logging/history need somewhere
  predictably discoverable and archivable. Ephemeral invocation inputs and a
  Manager-only path map are not the selected design foundation.
- Added the linked lifetime/discovery proposal and standing vision extension
  before storage implementation. Placement remains worktree-owned, agenda
  semantics/export efforts-owned, and archival logger-owned.
- The proposal retains legacy rescue, separate checkout/data lifetimes,
  revision-bound archive verification and explicit migration/deletion authority.
  Existing native receipt and Bridge membership source work continues; the
  agenda consumer awaits reviewed producer discovery support.

### 2026-11-02 - First S2 observation receiver source qualification

- Attached the optional observation index to the existing elected roster owner
  and SQLite path, with separate running-receiver capability/readiness metadata.
  Added strict selected read, retained-history and held-feed control actions plus
  passive CLI handlers; no new listener, fleet poller or host-start fallback.
- Facts and history remain explicitly sampled/partial. Stable facts do not turn
  timestamps or heartbeats into milestones. Native claim/binding/disposition
  capture, rich agenda history and Manager/Bridge adapters remain open.
- Point/history/feed requests require exact stored worktree identity. New
  regressions reproduced accidental adoption of a unique legacy roster suffix;
  the receiver now rejects that alias before materialization while preserving
  existing roster suffix behavior and valid exact matches.
- Qualified source packets passed: 331 joined owner/provider/CLI/retirement
  cases, an overlapping 74-case exact-identity follow-up, 258 existing
  roster/producer/client/feed/agenda cases, 10 explicit-tier POSIX dispatcher
  cases and four fresh CLI processes with unchanged private-home contents.
  PowerShell was unavailable for ten dispatcher cases; no Windows qualification
  is claimed. Malformed boolean versions remain distinct from unsupported
  integer versions.
- The CLI shares bounded raw JSONL transport with the existing agenda relay.
  Authentication, generation/selection/cursor checks and producer/request/feed
  retirement accounting remain explicit; optional index errors do not gate
  ordinary roster or canonical operations.
- This is source awaiting publication/review, not an installed receiver or
  whole-S2 completion. No canonical-save hook, capture spool, Agenda storage
  move, archive, cleanup change, native action or rollout was performed.

### 2026-11-02 - Advisory recovery guidance merged; first S2 receiver slice

- #10066 received current-head Copilot approval with no open findings, passed
  required CI and merged as `8b16ba2152f86f96ceaa960783c056b872e5edf7`.
  Re-read this full root and both linked documents after pulling the same
  retained source workspace forward.
- The efforts vision, agenda skill and managed reference now state judgment-led
  recovery from unavailable, stale or rewound planning state. Freshness clues
  do not become rigid expiry or execution gates; strict data-write validation
  and independent permissions, constraints and canonical completion remain.
- First S2 source work attaches selected owner-roster observations to the elected
  resident and strict read/history/feed transport. These are qualified observed
  changes with partial coverage, not fabricated claim/binding/disposition events.
  Native mutation receipts and cross-process capture remain later open S2 work.
- No new canonical-save hook, capture spool, agenda-home migration, logger
  archival, cleanup policy or installed rollout is implied by this slice.
  S2-S5 and every original manual/head/native/Relay acceptance remain open.

### 2026-11-02 - Sidecar foundation merged; owner integration next

- #9979 merged as `74d9254007c1b06c0f058e20bc6a8cad05f3a8ad` through the
  authorized Maintainer self-merge path after concrete corrections and required
  green CI. No separate human-review layer or human acceptance claim was added.
- Pulled the retained source workspace forward, then re-read this entire root
  and both linked implementation/observation documents. Close only S1 source
  delivery and the pure classifier criterion; retain every original root,
  source-transport, selected-measurement, native, manual and Relay gate.
- Source-head packets separately qualified 128 Manager, 226 owner-index and
  111 Bridge cases. The new controller also passed strict checking. These are
  source results, not installed or native-client evidence.
- The merged foundation includes the virtual timeline, cooperative reader
  lifetime, owner-qualified history and durable index admission intents. It
  advertises no new provider capability and attaches no actual owner transport.
- Continue with the advisory-agenda recovery/freshness guidance revision, then
  S2's elected-owner capture, passive provider handlers and consumer integration.
  Agenda relocation, archival and cleanup changes remain a separate design
  proposal; unavailable planning state must not become an execution authority.
- The whole effort remains Active. No rollout, live Mux change, head action,
  resource release or original manual/native acceptance was performed.

### 2026-11-02 - Durable admission-intent design amendment

- Selected journal-qualified durable admission intents rather than downgrading
  every unsealed owner interval. Validate admission before the intent commit;
  materialization and resolution of the matching intent commit atomically.
- An unresolved admitted operation survives takeover as explicit history
  uncertainty. Recovery does not replay canonical work, invent events or equate
  a fresh current capture with complete retained history.
- The observation contract records this source-level transaction protocol and
  its pre-intake/first-intent persistence limits. Canonical worktree writes and
  the auxiliary index remain separate; no crash-atomic cross-store guarantee
  or installed rollout is implied.
- Implementation and isolated failure/recovery qualification are in progress.
  The foundation PR and all original parent/native/Relay gates remain open.

### 2026-11-02 - Durable admission-intent implementation qualification

- Added auxiliary index protocol 1 with journal-qualified intent and gap tables.
  Initialization, refresh, replacement, publication and admitted overflow retain
  their exact owner/source/base-cursor and capture evidence across commits.
- Successful materialization and intent resolution commit together. Failed gap
  persistence leaves the admitted intent discoverable; takeover preserves
  retained-history uncertainty without replaying canonical work.
- The focused owner packet passed 226 cases, including interrupted admission,
  stage-two SQL/connection failure, failed gap persistence, rollback of update
  plus resolution, owner takeover fences and fresh-capture/history separation.
  A known durable gap is partial coverage; original capture completeness remains
  stored independently. Missing legacy intent coverage is explicitly unknown.
- Independent protocol review is pending. No new capability is advertised and
  no producer transport is attached. Agenda-home migration, logger archival and
  cleanup-policy changes remain a separate design proposal, not implementation.

### 2026-11-02 - Within-batch identity admission

- Overflow admission checks the complete detached batch for conflicting native
  identities, not only identities already stored in the journal. Matching
  duplicates remain valid; null native IDs retain batch-offset identity.
- Bounded streamed canonical JSON digests preserve boolean/integer/float/signed
  zero distinctions without allocating an unrestricted oversized serialization.
  Comparison-budget exhaustion rejects explicitly without changing facts or
  sending gap notifications.
- The expanded owner packet passed 200 cases, covering new and mixed identities,
  combined/individual overflow, typed content conflicts, healthy/failed state,
  duplicate controls, mutation during lock waits and comparison exhaustion.
- This remains a source-review correction; provider attachment, native evidence,
  deployment and the original whole-parent gates remain open.

### 2026-11-02 - Conflict admission before overflow invalidation

- Private size-only preflight failures retain detached, structurally validated
  entries. Failure reconciliation checks accepted batch and native identities
  inside the fenced owner transaction before changing projection availability.
- Oversized reuse of an accepted batch returns `batch-conflict`; provably changed
  accepted native evidence returns `event-conflict`. Existing facts, failure
  qualification and subscriber notifications remain unchanged in both cases.
- The focused owner packet passed 151 cases, including direct-history/runtime
  entry and delta overflow, existing gaps, identity conflicts, admission refusals,
  lock-wait caller-data mutation and unrepresentable reconciliation. Genuine
  qualified new-batch overflow still yields unavailable/gap evidence.
- This is another correction in the open source PR, not source feed attachment,
  native acceptance, deployment or whole-parent completion.

### 2026-11-02 - Cooperative observer teardown

- Reproduced a controller race: a page already in its adapter's cleanup was
  interrupted by teardown's task cancellation. Both close and selection
  replacement failed the new regression while the prior ten cases passed.
- Internal adapters now receive a request-local stop event for observation and
  history. Teardown signals those events rather than canceling adapter tasks,
  and retains all handles until their bounded owned cleanup finishes.
- Late results remain revision-fenced. Cleanup errors after selection retirement
  propagate through teardown rather than disappearing with stale callbacks.
- All 14 controller cases passed, including cleanup already in progress, natural
  observer completion, repeated caller cancellation and explicit cleanup failure.
  The changed controller also passed strict checking.
- This expands the unpublished internal adapter protocol; no provider transport,
  CLI/resident capability, installed rollout or whole-parent gate is closed.

### 2026-11-02 - Journal-local pending failures

- Pending invalidation records the verified journal generation and source.
  Snapshots, gaps, archived pages, initialization and append/recovery consume
  only a matching failure; replacement B cannot contaminate healthy retained A.
- Replacement persists A's own preexisting gap before retiring its pending
  failure. B's failure/recovery neither changes that coverage nor erases it.
  Recovery clears only the matching materialized target's pending failure.
- Legacy key-based failure recording acquires the current owner/journal fence;
  invalid reason metadata is rejected before any state change.
- The focused owner packet passed 119 cases, including healthy and failed
  predecessors across replacement failure/recovery and rejected legacy failure
  admission. This remains source awaiting review, not attached feeds, rollout,
  native qualification or whole-parent completion.

### 2026-11-02 - Admission-qualified database failure handling

- Connection and transaction-lock failures before source/cursor/replacement
  checks report operation errors without invalidating existing facts or waking
  subscribers as changed. Runtime wrappers consume an internal invalidation
  qualification rather than repeating unconditional failure handling.
- Failures after admitted mutation recheck the captured journal boundary or
  successfully materialized target before withholding facts. A source that has
  advanced, or an admission fence that cannot be reestablished, is not guessed
  to match; the operation reports explicit unavailability.
- The focused owner packet passed 116 cases. Real private SQLite lock fixtures
  cover current, stale, wrong-source and replacement requests before admission;
  SQL-trigger failures verify admitted refresh/replacement gaps still reach held
  readers. A locked duplicate attempt preserves existing failed-newer state.
- This is an open source-review correction, not installed source attachment,
  native-client acceptance or whole-parent completion.

### 2026-11-02 - Reject malformed batch metadata before invalidation

- Runtime publication and reconciliation validate the batch reference before
  capture/entry preflight or unchanged-capture refresh. Invalid batch metadata
  cannot trigger oversized-delivery invalidation, alter current facts or wake
  held subscribers.
- Added malformed batch cases covering non-text, whitespace and overlong
  identities with a matching current cursor and an otherwise oversized entry.
- The focused owner contract/history/runtime packet passed 104 cases, including
  unchanged complete/unknown reconciliation captures with invalid batch metadata.
- This is a further review correction in the open foundation PR. Source
  integration, native qualification and whole-parent gates remain open.

### 2026-11-02 - Bounded change evidence and journal-local recovery

- Large changed captures retain explicit before/after omission, capture-source
  references, content hashes and byte sizes when full detail cannot fit the
  entry budget. Unrepresentable admitted publication rechecks the current
  source/cursor before invalidation and subscriber notification.
- Journal rows preserve the capture's original completeness. Read frames alone
  overlay retained-gap coverage, so unchanged recovery captures do not conflict
  or append spurious observed-change history.
- Successful fenced source replacement clears pending invalidation for the new
  journal while retaining the old journal's gap. Same-source refreshes,
  duplicate acknowledgements and rejected replacement fences cannot clear it.
- The focused owner packet passed 98 cases, including changed large facts,
  direct oversized publication, current/stale failure fences, complete/unknown
  recovery and repeated/new reconciliation batch identities. Capture-valid
  deeply nested facts use explicit bounded omission; malformed metadata remains
  an error and cannot be hidden by the fallback.
- This remains an open source review; no provider attachment, native result,
  rollout or whole-parent completion is implied.

### 2026-11-02 - Detached captures and visible keyboard selection

- Publication, initialization and reconciliation detach validated nested capture
  data before writer-lock waits. Hashes, fences and persisted facts all use that
  detached input, not a caller-owned dictionary that can change during the wait.
- Duplicate-only acknowledgements preserve failed-newer-capture invalidation.
  Actual successful materialization can restore availability while retaining
  history-gap qualification. Locator changes conflict under the same native
  source/generation/record identity rather than creating another event.
- Held readers receive refreshed snapshot metadata even when the event cursor
  does not advance. Qualifier-only changes do not duplicate unchanged large facts.
- Keyboard scrolling selects a visible event; Enter expands that selection.
  Mouse selection is highlighted, Home requests bounded older history and
  End/Follow selects the live bottom without changing focus or Escape handling.
- Focused isolated packets passed: 86 owner contract/history/runtime cases and
  80 Manager model/layout/widget/app cases. The unchanged controller, core/policy
  and Bridge packets remain separately qualified.
- These are further corrections in the open foundation PR, not attached source
  feeds, native-client qualification, deployment or whole-effort completion.

### 2026-11-02 - Observation freshness and viewer lifetime fences

- Availability and completeness participate in both unchanged-capture and
  duplicate-evidence checks; qualification changes require observed-change
  publication rather than acknowledging stale observed facts.
- Internal batch hashes use separately bounded inputs. Actual admitted
  delivery overflow invalidates only the matching current source/cursor and
  wakes subscribers with explicit unavailable/gap evidence.
- Viewer teardown retains its reader/page handles and shields awaited cleanup
  through caller cancellation. A concurrent close cannot start replacement readers.
- Snapshot refresh merges preserve provider order and retained history after
  display eviction. An unchanged effective before-boundary retains its pending
  history demand, so a matching page remains admissible.
- Updated focused packets passed: 88 Manager model/layout/widget/app/controller
  cases and 73 owner cases; unchanged contract/policy and Bridge coverage remains
  qualified. The independent four-module strict check also passed.
- These source corrections remain in the same open foundation PR. No feed,
  native, measurement or whole-effort gate is closed by this journal entry.

### 2026-11-02 - Sidecar foundation source qualification

- Authored the bounded typed timeline, virtual Line-API renderer and caller-owned
  controller. Timeline is an alternate Companion tab; Summary remains initial.
  Owner feeds are not attached yet, so current facts are not fabricated as history.
- Added owner-fenced observation/history primitives and preserved optional SDK
  start metadata with bounded evidence. Neither owner capability is advertised
  by this foundation; writer, CLI, membership and consumer attachment remain open.
- Focused isolated packets passed: 39 core/policy/controller cases, 65
  model/layout/widget/app cases, 62 owner projection cases and 111 Bridge cases.
  Required install-contract checks and the admitted type cohort passed; the four
  independent new timeline core/model modules also passed strict checking.
- Fixed actual Textual mount-context and delegated-message sender failures,
  teardown ordering, stale sticky context, source-generation lane reservations
  and a full-window scan in line painting. Source tests retain byte-limited
  history continuation, explicit integrity reconciliation and unknown capacity.
- This is tested source awaiting review, not a source merge or deployment.
  Qualified pre-window measurement, attached push feeds, native clients,
  performance measurement and all original parent/Relay gates remain open.

### 2026-11-02 - Reviewed Sidecar source admission

- Plan [#9960](https://github.com/ThomasMichon/copilot-extensions/pull/9960)
  received current-head Copilot approval with no open issues, passed required
  CI and merged as `c05dcb4dca059e4fff482a3c8816633814c54fa6`.
- Re-read this complete parent contract and both linked plan/observation
  documents, then pulled the retained source workspace forward. S0 admission
  is complete; the bounded model/renderer/policy source slice can begin.
- Source execution does not settle any original manual/head/native/Relay gate
  or qualify an unknown pre-window denominator. No installed rollout, live
  rebinding, runtime threshold change or process action was performed.

### 2026-11-02 - Live Sidecar implementation amendment

- Continued under #8811 after verifying the retained source venue, current
  coordination and unchanged Companion core. Reused the existing workspace
  and its controller; no new source-hosted agent or parallel canonical effort.
- Added the linked implementation/validation and proposed observation contract
  for true virtualized history, push-first owner adapters and truthful
  notifications. The vision is extended before source execution; normal
  plan review remains the admission gate.
- The configurable warning uses strict unrounded pre-context utilization.
  Current public SDK start fields can retain total token counts, but targeted
  window capacity is not silently equated with active pre-window capacity.
  Selected-runtime/denominator qualification remains explicit.
- Preserve every incumbent manual/head, popup, relay/native and resource
  responsibility. No source implementation, installed rollout, live rebinding,
  threshold mutation or process action is claimed by this planning amendment.

### 2026-10-31 - Timeline and current-worktree presentation guidance

- Added generic fictional timeline/subway examples, expandable parallel Task
  work and child agendas, blocked/deferred paths and current-position variants
  to this existing effort's purpose-first guidance.
- Current accepted effort purpose leads the heading; initial request is a
  summarized, expandable creation-context record. Worktree/claim/binding/PR
  events retain their owner meaning and do not become completion or approval.
- This is a bounded documentation contribution under #8811. Existing source/UI,
  native/relay and manual/head custodians retain the complete Active root;
  no implementation, runtime, source replay, native action or rollout occurred.

### 2026-10-25 - Operator selected keyboard guides instead of click tabs

- The operator revised the purpose-first chrome request: "We can instead
  show some hotkey guides to switch modes." Update standing intent and this
  continuation through normal review before implementing the revised chrome.
- The second permanent Claims row and same-window CLI/Summary panes remain
  required. Guides must describe actual registered, context-appropriate
  actions, retaining the distinction between Ctrl+K's popup and persistent
  pane navigation. Do not advertise an unimplemented direct-toggle shortcut.
- This withdraws the new clickable-mode-tab requirement, not the relay
  root's historical accepted click deferral or any existing app-input,
  manual/head, source/provider, native or resource hold. No unreleased mux,
  live rebinding, runtime installation or rollout was selected.

### 2026-10-25 - Optional attention consumer merged; native chrome boundary

- #8941 merged after current-head Copilot approval, resolution of its
  reviewed refresh single-flight race and required CI. Re-read this entire
  root. Close only the Step 6 switching/attention source row; preserve the
  actual native-client, rollout, topology and every retained manual/head,
  app-input, relay and resource acceptance item under its existing owner.
- Final source `5ec8862bc112ee71e5f14b0edfdc69a0dc5ac23b` passed 117
  graph/attention/native tests plus 43 provider tests on Ubuntu 24.04 / tmux
  3.4, and 41 attention/native tests on Windows / disposable PSMux 3.3.6 in
  run `40183515001`. Neither platform packet skipped tests. The focused
  post-review local packet passed 80 tests; the blocked-replacement
  regression failed before the fix and passed after it. The earlier
  integrated Windows packet passed 237 tests with one POSIX-only skip.
- The optional indicator is explicitly last-reported and continuity-unknown.
  Its independent worker revalidates one displayed head, uses bounded pulse
  reads, failure backoff and stale-selection fencing, and cancels its owned
  child on exit. Changed heads require Refresh. Hidden/unknown observer
  visibility does not poll the engine. Nudges navigate only, never answer
  or approve input.
- Native qualification found that PSMux 3.3.6 can synthesize a client listing
  while detached; the new visibility reader uses exact-pane
  `session_attached` evidence instead. PSMux 3.3.5 setup/CLI/directory
  instability remains separately unqualified under #8811; clean controls
  did not establish causality. Pinned 3.3.6 results do not qualify the older
  build, and no installed compatibility floor or runtime was changed.
- Further isolated native research proved two stored status rows on PSMux
  3.3.6 through its own state dump with options targeted to that disposable
  session's server; a separate control retained one row. Indexed option
  getters were not authoritative. This is stored-state/isolation evidence,
  not rendered status or real-client mouse dispatch.
- Released PSMux 3.4.0 still treats custom tmux mouse key names as unmodelled
  and only routes status-format ranges to native windows. Its unreleased
  development branch has a mouse-key routing implementation, but that is
  not a released/qualified runtime. Native windows are not the selected
  same-window pane design. Keep the withheld mouse-bind safety boundary;
  do not silently enable it, substitute a layout or declare click delivery.
- Continue native chrome through the existing Mux-bind owner after selecting
  the supported-backend path. No live bindings, user mux, runtime update,
  manual cutover or rollout was exercised. The interrupted keyed workspace
  allocation remains separately owned and pending.

### 2026-10-25 - Root-attention provider merged; optional UI consumer next

- #8928 merged after current-head Copilot approval and required CI. Re-read
  this entire root; retain every direct manual/head, app-input, relay,
  source/provider and native hold. The combined switching/attention row
  stays unchecked until the optional indicator and its consumer tests land.
- Added the versioned read-only `session-attention` provider over one bounded
  root live-pulse register. It exposes only last-reported busy, idle or
  awaiting-operator state and transition time. Runtime continuity remains
  unknown; initial busy may lack a transition timestamp. No intent,
  question, transcript, event-tail, TTL or legacy-idle inference is used.
- Final staged Windows packet: 145 passed, Ruff clean, install-contract
  clean for 13 plugins. All 197 admitted source hashes matched after the
  one reviewed additive routing hash change; inventory and budgets stayed
  unchanged. Actual fresh-home CLI tests retained source-hosting denial and
  whole-fixture byte/no-write controls.
- Exact source `9a173d0ebead8d8a85c9ee063274fcc770a92018` passed 43 attention
  provider/process tests and 77 graph/native tests, zero skips in either
  packet, on Ubuntu 24.04 / tmux 3.4 in run `40179632871`.
  This is isolated source/native evidence, not human input, status rendering
  or a production deployment. No live repair or rollout was performed.
- Continue with one optional cancellable attention consumer, selected-head
  fencing and explicit uncertainty, then the outstanding native chrome and
  broader status work. Unsupported providers must preserve all existing
  views; the indicator may nudge a return to CLI but never send an answer.
  The interrupted keyed workspace allocation remains separately owned and
  pending; this merge does not settle it or grant manual state repair.

### 2026-10-25 - Graph consumer merged; attention is the next source slice

- #8916 merged after current-head Copilot approval and required CI. Re-read
  this entire root and retain its complete manual/head, app-input and
  complementary relay acceptance. The current task worktree was pulled
  forward onto the confirmed merged source for continued work, not finalized.
- Final graph/legacy consumer packet: 111 passed. Exact source
  `1709949607a213d97f992d495ea2b86dcf7b22af` passed 77 tests with zero skips
  on Ubuntu 24.04 / actual tmux 3.4 in workflow run `40177027971`, including
  native pane navigation, graph contracts and real Textual consumers.
  The earlier standalone combined Windows packet passed 343 tests with
  three platform skips. These are source/isolated-native tiers, not rollout.
- Fixed the reviewed missing-snapshot-key crash while retaining explicit
  null for unavailable history. The PR description now records contract,
  performance and documentation impact; production cold/warm latency is
  unmeasured, not inferred from test duration.
- Graph remains evidence-scoped: reported lifecycle is not allocation,
  retained records are not lifetime totals, and a PR claim is not current
  open/closed evidence. The broader topology row remains open for its
  remaining source/status qualification; no narrower merge completes it.
- Continue with lightweight, versioned root attention observations and a
  cancellable single-session UI indicator, without rescanning history or
  guessing questions from transcripts. Then qualify native two-line/click
  chrome under the relay's existing safety boundary. No installation or
  live configuration change is authorized by these source milestones.
- A separate next-workspace allocation was interrupted before a tracking
  record existed. Keyed replay correctly remains pending and targeted
  creation repair reports the missing record, so no duplicate allocation or
  manual state edit was attempted. Its creating session retains that
  outstanding allocation; continuing in this existing task worktree does
  not claim it settled.

### 2026-10-25 - Same-window observer merged; native evidence retained

- #8881 merged after current-head Copilot approval, required CI and native
  qualification. Re-read this entire root before continuing. The combined
  switching/attention row stays unchecked because question/idle attention is
  not implemented; no historical manual/head or app-input hold is closed.
- Final local packet: 270 passed, three platform skips, including actual
  PSMux switching, original CLI PID retention, observer cleanup, fresh CLI
  relaunch and pane-aware relaunch registration. Reconnect and detached
  creation callbacks use snapshot/native-owner fences; unavailable metadata
  cannot revive a successor or indefinitely orphan a bound observer.
- Exact source `98a60ab111ab2a5c9aa3ec4dc6757be0a16b8b8c` passed the isolated
  Ubuntu workflow run `40175168608`: actual tmux 3.4, one native test passed,
  zero skips. This is not proof of rendered production chrome, real-client
  mouse dispatch, first installation or rollout.
- A separate disposable PSMux 3.3.5 option probe round-tripped `status=2`.
  Indexed `status-format[0]` and `[1]` setters returned success but their
  indexed reads were empty; scalar `status-format` did persist a value.
  This is an unresolved rendering/capability qualification, not proof that
  two-line chrome works or that every possible format is unsupported. Both
  uniquely named probe sessions were cleaned up. The existing withheld
  click-binding safety boundary and all native chrome acceptance remain.
- The observer worktree was finalized through its owner. Continue with the
  Graph consumer on top of the merged source, then attention and native chrome.

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
### 2026-10-25 - Bounded graph consumer source validation

- Added a secondary Graph view for reported disposition history, typed
  directed resource references and optional retained-session observations.
  Inbound owners, outbound claims and reported subagents remain distinct;
  claims alone do not establish whether a PR is open.
- The graph uses the versioned #8905 provider only for one unambiguous
  selected head session. Reads are on demand, off the UI loop, cancel on
  shutdown and cannot replace a refreshed snapshot with an older result.
  Unsupported or partial evidence cannot disable Summary, Claims or Recovery.
- The focused packet passed 108 tests: literal/narrow/long graph rendering,
  bounded fan-out and duplicates, schema/uncertainty handling, actual JSON
  subprocesses with target-cwd isolation, and real Textual activation,
  refresh, optional-source failure and blocked-read dismissal. Ruff and the
  13-plugin install-contract gate passed.
- After integrating the merged observer, the standalone combined packet
  passed 343 tests with three platform skips and a clean host-state audit.
  An earlier concurrent-provisioning run had the same passing assertions but
  failed its machine-PATH drift audit; no shared environment was rolled back
  and the drift's source was not established. A further actual Textual
  late-result/refresh regression passed in the five-test UI packet.
- This is source validation only. Question/idle attention, native mux view
  tabs, two-line claims chrome, real mux-client dispatch and installed/rollout
  evidence remain separate, unchecked acceptance. The parent stays Active.

### 2026-10-25 - Retained session metadata provider merged

- #8905 merged after current-head Copilot approval and required CI. Re-read
  this entire parent effort; the topology, attention, mux chrome and retained
  manual/native acceptance remain open. The earlier #8822 Summary/Claims
  source delivery and its 156-test consumer packet now close only their
  corresponding Step 6 and source-validation rows.
- Added the optional, versioned `session-observations` engine command, with
  streamed retained-file tool-event counts and reported subagent lifecycle.
  Counts are not lifetime totals; incomplete history, missing producer fields
  and unavailable evidence remain explicit. Reported completion does not mean
  allocation release, retirement or resource custody.
- The final focused provider packet passed 74 tests, including real CLI
  subprocesses over fresh synthetic state, interrupted-read byte coverage,
  launching-task metadata and unchanged source-hosting denial. Ruff and the
  13-plugin install-contract gate passed. CI executed the unchanged admitted
  unit inventory after its project-routing source hash was refreshed.
- This is provider-first source delivery, not UI integration or deployment.
  No runtime was installed. The next slice consumes the optional contract in
  the bounded graph presenter; unsupported providers retain existing views.

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
