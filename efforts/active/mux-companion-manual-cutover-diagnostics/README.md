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
      so it runs before composing/saving the brief in both trigger paths
      (not merely before `trigger_handoff`): capture whatever partial
      progress a background agent/async shell produced, then stop/wait it
      out, and always stop every `manage_schedule` entry the session
      created -- with no exception for one the brief plans to ask the
      successor to re-arm (re-arming is always a separate, successor-side,
      post-cutover action, never a reason to leave something running now).
      A context-pressure-driven handoff that must trigger immediately is
      still not blocked on this -- same escape hatch as the sync step: note
      what was left running and why. Mirrored into the generated
      `save_handoff_prompt`/`generate_handoff_prompt` tool guidance in
      `extension.mjs` and the public `plugins/context-handoff/README.md`
      trigger-sequence docs so no surface describes a path that bypasses it.
- [ ] Live-test coverage for this new step (see Validation Plan) is
      deliberately left for a follow-up leg: it needs a real predecessor
      session with a genuinely owned, still-running background task or
      schedule at the moment of handoff, which this leg's own throwaway
      worktree test (Step 3/4's retire-mechanism re-validation) did not
      include.

## Validation Plan

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
- [ ] Live-tested: a predecessor with an owned, still-running background
      task/agent (a `task` tool invocation, an async shell) at the moment of
      handoff -- confirm the new "Quiesce owned background work before
      triggering" skill step actually gets followed (stopped or explicitly
      carried forward in the brief), not silently dropped or left to race
      the successor.
- [ ] Live-tested: a predecessor with an active, self-owned
      `manage_schedule` entry at the moment of handoff -- confirm it ALWAYS
      gets stopped before triggering (never left running because it's
      "going to be re-armed"), and -- only when the successor genuinely
      needs it resumed -- the brief additionally, separately names the exact
      restart command as a post-cutover action. Re-arming is never a
      substitute for stopping it now; this item is not satisfied by a
      re-arm note alone.
- [x] Unit tests green per step; `tools/check-module-size.py` clean.

## Journal

### 2026-10-06 — Live re-validated the #5455 fix; triaged 2 stray claims; new quiesce-before-trigger skill step
Picked up via handoff (task `5bad80aac0b7435ea0b3585944d5025a`), whose two
remaining items were: (1) live re-validate the #5455 predecessor-retire fix
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
