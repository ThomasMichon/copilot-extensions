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

### Step 3 — `agent-worktrees`: on-demand claim+spawn+retire verb
- [ ] New CLI verb (name TBD at implementation time, mirroring
      `handoffs-check`'s naming) that runs
      `_monitor_maybe_process_handoff_record` for ONE worktree, on demand,
      regardless of `_status_monitor_enabled()` (an unrelated daemon-process
      toggle, not a mode gate).
- [ ] Tests: claims + spawns via the existing `handoff-cutover` result
      builders (no new spawn logic); a second concurrent on-demand call (or
      the real daemon, if running) safely no-ops via the existing hardlink
      claim.

### Step 4 — `worktree-manager`: Mux Companion UI
- [ ] "Cut over" button, shown only when `pending_handoff` is set; invokes
      Step 3's verb (with Step 2's `--force` upstream of it, as needed for
      the ledger to exist for the verb to find).
- [ ] Session-lineage table gains a head-match marker (does resolved head
      point at the session the operator most recently resumed) and a manual
      refresh key, so reopening Ctrl-K after a `/clear` + paste-seed shows
      updated state without exiting Mux.
- [ ] Explicitly NOT built (per the operator's own request): no
      auto-repair-head or auto-file-a-bug action in the Companion itself --
      a detected mismatch is surfaced for the operator to hand to the
      current session to patch up and file, not acted on by the Companion.

## Validation Plan

- [ ] Live-tested in this operator's own manual-mode worktree: press "Cut
      over" on a genuinely pending handoff, confirm a real successor spawns
      and the predecessor pane retires.
- [ ] Live-tested: after a manual `/clear` + paste-`HANDOFF_SEED` resume
      (no button), reopening the Companion shows the new session as head.
- [ ] Unit tests green per step; `tools/check-module-size.py` clean.

## Journal

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
- **Next up**: Step 3 (agent-worktrees on-demand claim+spawn+retire verb).
