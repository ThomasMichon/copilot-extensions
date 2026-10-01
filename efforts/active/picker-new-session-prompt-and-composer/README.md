# Picker New-Session Prompt + Registered-Pivot Composer

- **Slug:** `picker-new-session-prompt-and-composer`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase model (mirrors `agent-dispatch-tasks-pane-ux-overhaul`'s own convention)
- **Created:** 2026-09-30
- **Status:** Draft
- **Vision:** `picker` (no existing §Features entry yet -- candidate follow-up
  once Phase A lands)

## Documentation impact

New CLI help text (`agent-worktrees create --seed`/`resolve --new --seed`)
is self-documenting at the flag level; no separate CLI reference doc exists
for these commands to update. No vision, architecture, or operating
procedure doc describes the New-worktree creation flow at a level this
change affects -- behavior is additive and, for the Picker's own live flow,
currently gated off (`_SEED_PROMPT_ENABLED = False`) pending the remaining
`launch-session.{ps1,sh}`/`engine_client.py` seams, so no user-facing
documentation yet describes a capability that doesn't yet work end-to-end.
This effort's own README (here) is the authoritative in-progress record of
what's implemented vs. outstanding, kept current in its Plan/Journal each
session.

## Guiding Intent

Two creation flows in the Picker both want the SAME missing capability --
an optional free-text field collected at creation time, before anything is
launched -- but neither one has it, and the underlying declarative
(registered-pivot) and bespoke (Worktrees) UI mechanisms don't share a
reusable way to add one:

1. **"New worktree…"** (Worktrees pane) should let the operator type an
   initial prompt so the freshly created session launches interactively
   with it already queued -- "fire and forget" instead of waiting through
   the lengthy auto-update/bootstrap flow before being able to type anything.
2. **"New task…"** (any registered pivot, starting with agent-dispatch's
   Tasks pane -- see that effort's own Phase 10) needs a real composer:
   title, prompt, and a tags/criteria picker, submitted as `propose`+`queue`.

Both are blocked on the same missing piece: **the Picker has no
generic way to open a field-spec-driven form when NO row is selected** (a
"create" flow, not an "act on this existing entry" flow). Landing that once,
well, unblocks both creation flows rather than solving each bespoke.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| Driving agent | Authors and drives both phases | The effort's active worktree |

## Coordination

- **Topology:** independent per-phase PRs (Phase A can land and ship value on
  its own; Phase B builds on Phase A's extracted field-rendering helper).
- **Host (owns PRs):** Driving agent.
- **Delegates:** none currently.
- **Handoff:** **manual, sequenced-session loop** (matching
  `agent-dispatch-tasks-pane-ux-overhaul`'s own established pattern) -- each
  session drives until its context fills, updates the Runbook below, then
  hands off via `generate_handoff_prompt`/`save_handoff_prompt` (never
  `trigger_handoff` -- this repo's `context-handoff` config is
  manual-cutover-only). The operator manually pastes the returned prompt
  into a fresh session to continue the loop.

## Context

Sibling, already-in-flight work this effort deliberately does **not**
duplicate or block on:
- **`agent-dispatch-tasks-pane-ux-overhaul`** (this repo) -- Phase 10 (New
  Task composer) is the primary CONSUMER of this effort's Phase B. That
  effort's Phase 10 Plan text should be read as depending on this effort
  landing first (added there as a cross-link).
- **`picker-creature-comforts`** (this repo, `efforts/active/`) -- also
  touches the New-worktree / launch flow (detached-launch-into-a-new-window,
  Ctrl+F search, tab-title projection), but targets entirely different
  capabilities (window/terminal management, not a creation-time input
  field). No overlap; both can land independently.
- **`worktrees-pivot-ux-overhaul`** (this repo) -- its own 2026-09-29
  Journal entry records a PLANNED rename of the Worktrees `SESS/T` column to
  `LENGTH` (format `"1s 25t"`). Unrelated to this effort's own scope, but
  worth knowing: `agent-dispatch-tasks-pane-ux-overhaul` already adopted
  that exact format for ITS OWN `LENGTH` column (PR #4714, 2026-09-30) ahead
  of the Worktrees rename landing, specifically so the two stay visually
  consistent whenever that one ships.

### Investigation already done (2026-09-30, grounding this effort's Plan)

A background `explore` agent mapped the full "New worktree…" activation
path before this effort was written, so Phase A's Plan below is grounded in
real code, not assumption:

1. **Activation path:** `engine_maintenance_actions.py`'s `_open_optmenu()`
   (around line 405) builds a plain option-toggle dialog descriptor (Anchor
   repo / Bare / No Mux / AHP / Local model -- no free text) and pushes
   `ScopeDlgScreen` (`engine_dialogs.py:663`, a `ModalScreen[bool]` built from
   `Static` + `SelectionList` + `FocusGroup` -- **no `Input`/`TextArea`
   widget at all**). Confirming calls `_confirm_new_worktree()`
   (`engine_maintenance_actions.py:11-26`), which folds the selected toggles
   into a `_decide({"action": "new", ...})` call.
2. **Launch plan resolution:** the decision becomes a `picker_app
   .LaunchRequest` (`__main__.py:1090-1100`ish), resolved via
   `engine_client.resolve_launch_plan()` (`__main__.py:1188-1211`), which
   shells to `agent-worktrees resolve --new --json` (launch PLANNING only --
   `agent-worktrees create` itself, per its own help text, explicitly
   performs "no launch, no mux").
3. **Actual launch exec:** `_run_relocated_mux_launch()`
   (`__main__.py:~1237-1268`) execs the Manager-owned
   `worktree-manager/bin/launch-session.{ps1,sh}` script -- "the ONE
   canonical muxed-launch implementation" -- passing `--project`/
   `--worktree-id`/`--base`/`--bare-resume`. **That script already supports
   an arbitrary `--` passthrough** (`launch-session.ps1:163`: "everything
   after this separator is copilot passthrough args (e.g. `--acp --stdio`)",
   appended again near line 1227) straight to the real `copilot` CLI process
   it ultimately execs.
4. **A seed-prompt mechanism ALREADY EXISTS, just on a different command:**
   `agent-worktrees copilot` (`plugins/agent-worktrees/src/agent_worktrees
   /copilot_cli.py:37-57`) already has `--seed` ("Seed prompt injected as
   the session's first interactive turn once Copilot is ready") and
   `--seed-ready-timeout`, plus `--new`/`--headed` -- its own comment even
   names "a caller (e.g. the Worktree Manager Picker)" as the reason
   `--headed` exists. **But the Picker's actual "New worktree" creation
   flow does NOT go through this command at all** -- `headed_actions.py`'s
   `open_worktree_cli_headed()` (the ONLY Picker call site that invokes
   `copilot --headed`) is only reachable for an EXISTING worktree row
   ("Launch in new window"), never the creation path. The creation path's
   own `launch-session.ps1`/`.sh` is a separate, older, more elaborate
   script that does NOT currently forward to `agent-worktrees copilot
   --seed` internally (confirmed: no `--seed` reference anywhere in that
   script as of this writing).
5. **Reusable generic form infra exists, but is steer-coupled:**
   `PivotFormScreen` (`steering_form.py:37`) already renders a field-spec
   list (`text`/`textarea`/`choice`/`multichoice` -> `Input`/`TextArea`/
   `RadioSet`/`SelectionList`, see `steering_form.py:154-210`) from a plain
   constructor arg (`fields: list[dict]`) -- exactly the "field-spec-driven
   form" shape both Phase A and Phase B want. **But its button row (Confirm/
   Save/Reset) and submit semantics are hard-wired to the STEER transport**
   (`agent-dispatch steer submit` / `card draft save` / `card draft clear`
   via `on_clear_draft`) -- wrong semantics for either "launch with this
   prompt" (Phase A) or "propose+queue a new task" (Phase B). Reusing it
   verbatim would be wrong; extracting its field-rendering guts
   (`steering_form.py:154-210`'s type->widget mapping) into a shared,
   submit-semantics-agnostic helper both a new lean creation-form screen AND
   the existing steer form can call is the right shape -- matches this same
   file's own "mechanical extraction... no behavior change" convention
   already used once to split it out of `steering.py`.
6. **The registered-pivot `PivotAction`/`kind:"form"` mechanism is row-scoped
   by design:** `pivot_manifest.py`'s `PivotAction` (line 68) is documented
   as "one entry in a registered pivot's **Enter sub-menu**" -- every field
   spec (`fields_from`) is resolved via `_pivots.resolve_path(rec, ...)`
   against an already-selected entry (`engine_pivot_actions.py`'s
   `_open_pivot_form()`, ~line 367). There is no "pivot-level, no row
   selected" action type today. Worktrees' own "+ New worktree…" button
   (the thing Phase 10's Plan literally says to "mirror") turns out to be
   entirely hand-coded UI chrome (`engine_selection.py`'s `new_worktree_row`
   + the `"BTN"` zone's `"N"` case in `engine_maintenance_actions.py`), NOT
   a generic, manifest-driven mechanism any registered pivot can opt into.
   **This is the real, previously-unidentified prerequisite Phase B needs to
   build**, not assume already exists.

## Request

> Operator (2026-09-30), continuing from a status-check on Tasks Phase 10's
> "New task…" button: "Great. In a handoff, let's drive this. We'll need
> pivots to be able to specify additional form widgets. I have already been
> dreaming of adding a 'Prompt' field to 'New worktree', so new worktree
> sessions can be called with `--interactive`, making it users can 'fire and
> forget', and not have to wait for our lengthy auto-update flow before they
> can type a question or command."

## Plan

### Phase A — "New worktree…" gains an optional Prompt field
- [x] Extract `PivotFormScreen`'s field-type-to-widget rendering
      (`steering_form.py:154-210`) into a shared, submit-semantics-agnostic
      helper (mirroring this file's own "mechanical extraction" precedent);
      re-wire `PivotFormScreen` to call it, confirming byte-identical
      behavior (existing steer tests must still pass unchanged). **Done
      2026-09-30:** new `picker_tui/field_widgets.py` module, `compose_field(f,
      i) -> (widgets, rec)` (pure, no `self`/draft/visibility coupling);
      `PivotFormScreen._compose_one` now calls it and layers its own
      `show_when`/`visible` bookkeeping on top. Full `production_picker` suite
      (840 passed, 1 skipped) confirms byte-identical behavior.
- [x] Build a new, lean creation-prompt screen (its own class -- NOT a reuse
      of `PivotFormScreen` itself, whose Confirm/Save/Reset button row and
      draft-persistence semantics don't fit a "collect one optional prompt
      before launch" flow) using the extracted helper for the one `textarea`
      field it needs. **Done 2026-09-30:** new `picker_tui/seed_prompt_screen
      .py`, `SeedPromptScreen(ModalScreen[str])` -- always dismisses with a
      plain `str` (`""` when skipped/escaped/blank-launch), a `Launch`/`Skip`
      `FocusGroup` button row, Enter-from-textarea advances to it (mirroring
      `PivotFormScreen`'s single-question path). 5 new Pilot tests cover
      launch/skip/escape/blank-launch/whitespace-stripping.
- [x] Chain it into `_open_optmenu()`'s flow: after `ScopeDlgScreen`
      confirms (and only when "Bare" is NOT selected -- a bare worktree gets
      no Copilot bootstrap at all, so a seed prompt has nothing to attach
      to), open the new prompt screen; an empty/skipped prompt behaves
      exactly as today (no change to existing behavior when unused). **Done
      2026-09-30:** `_open_optmenu()`'s `_after` callback now checks "Bare"
      and either calls `_confirm_new_worktree(dlg)` directly (Bare) or pushes
      `SeedPromptScreen` and calls `_confirm_new_worktree(dlg,
      seed_prompt=result)` on its dismiss. `_confirm_new_worktree` always
      carries `options["seed_prompt"]` (`""` when none). 5 existing
      New-worktree confirm tests updated for the extra screen hop (each now
      needs 2 Enter presses: textarea->buttons, then activate Launch); 2 new
      tests cover the Bare-skips-the-screen path and a typed prompt carrying
      through to the decision. Full suite: 847 passed, 1 skipped.
- [~] Thread the collected prompt through `_confirm_new_worktree()`'s
      decision dict -> `picker_app.LaunchRequest` -> `_run_relocated_mux_launch()`'s
      `args` list -> `launch-session.{ps1,sh}` -- a NEW optional argument
      (name TBD at implementation time, e.g. `--seed-prompt`) that the
      script forwards to whatever it ultimately execs. **Redesigned +
      mostly implemented 2026-09-30 (see this session's Journal for the
      full reasoning):** the operator's own question ("can this not be a
      standard part of `agent-worktrees create`?") led to a cleaner design
      than Picker-specific argument-threading -- see Journal for what's done
      vs. the one remaining piece (launch-session.{ps1,sh} wiring).
- [ ] Confirm the end-to-end behavior matches `agent-worktrees copilot
      --seed`'s own documented contract ("Seed prompt injected as the
      session's first interactive turn once Copilot is ready") -- the
      prompt must not race the auto-update/bootstrap flow the operator
      explicitly wants to skip past.
- [ ] Tests: the new field-rendering helper (unit), the new creation-prompt
      screen (modal behavior: submit/skip/escape), the decision-dict/
      `LaunchRequest`/launch-script argument threading (each layer, not just
      end-to-end), and a regression check that `ScopeDlgScreen`/steer
      behavior is completely unchanged when no prompt is ever entered.

### Phase B — Generic registered-pivot "create" action (unblocks Tasks Phase 10)
- [ ] Add a new `PivotAction`-adjacent concept to `pivot_manifest.py` for a
      **pivot-level** action -- no `rec`/selected entry, a STATIC field spec
      declared directly in the manifest (not resolved via `fields_from`
      against a row) -- e.g. a new top-level manifest key
      (`create_action`? name TBD) alongside the existing `actions` list.
- [ ] Engine wiring: a UI affordance to trigger it when the registered
      pivot's list has focus but no row is meaningfully selected (mirror
      Worktrees' "N" button row, but data-driven -- reuse Phase A's
      extracted field-rendering helper for the widget types).
- [ ] `agent-dispatch`'s manifest declares its own `create_action` (title +
      prompt/goal textarea + a tags/criteria picker -- see the Tasks-pane
      effort's own Phase 10 Plan for the exact field list and the pool-
      filter-vocabulary source still to be confirmed).
- [ ] Submitting calls `propose`+`queue` (already-implemented `client.py`
      calls) against the coordinator, per that effort's own Phase 10 spec.
- [ ] Tests: the new manifest field parses correctly (and degrades
      gracefully for a pivot that doesn't declare one); the UI affordance
      appears/is absent correctly; a synthetic pivot's create action renders
      and submits via the generic mechanism (mirroring
      `test_registered_pivot_*` patterns already in
      `test_picker_tui.py`); `agent-dispatch`'s own composer round-trips
      title/prompt/tags into the exact `propose`/`queue` call shape.

## Validation Plan

- [ ] Phase A: create a real worktree from the Picker with a typed prompt;
      confirm the new session's first interactive turn is that prompt, with
      no race against the auto-update/bootstrap sequence. Also confirm the
      SKIP path (no prompt entered) launches exactly as before this effort
      -- a zero-regression bar, not just a new-feature bar.
  - [ ] Confirm behavior with "Bare" selected: no prompt screen is shown at
        all (nothing to seed).
- [ ] Phase B: from a live coordinator, use the Tasks pane's new "New
      task…" action to hand-author a task; confirm it appears with the
      exact title/prompt/tags entered, immediately eligible for its
      declared pool per the tags/criteria submitted.
- [ ] Both phases: full `worktree-manager` + `agent-dispatch` test suites
      stay green (baseline: whatever the two packages' full-suite pass
      counts are at the time each phase's PR opens -- record them in that
      PR/Journal entry, not assumed from an earlier session).

## Proposal

_Pending — this effort's plan itself will be submitted for review per the
repo's `pr-self-merge` profile before Phase A implementation begins._

## Journal

### 2026-09-30 — Kickoff, grounded in a full investigation of the New-worktree flow
Created from a live conversation that started as a status check on
`agent-dispatch-tasks-pane-ux-overhaul`'s Phase 10 ("New task…" composer).
Investigating why Phase 10 hadn't been started surfaced a real, previously
undocumented prerequisite gap (see "Investigation already done" above): the
registered-pivot manifest system has no concept of a pivot-level "create"
action at all -- every existing `kind:"form"`/`kind:"card"` action is
row-scoped. In the same conversation, the operator independently raised a
second, related want: an optional Prompt field on Worktrees' own existing
"New worktree…" dialog, so a freshly created session can launch
`--interactive` with a seed prompt already queued ("fire and forget").
Rather than solving either narrowly, folded both into one effort since they
share the same missing capability (a field-spec-driven form opened with no
row selected) and the same reusable widget-rendering code
(`PivotFormScreen`'s field-type mapping, currently coupled to steer-specific
submit semantics).

Investigated the FULL "New worktree" activation-to-launch path before
writing this effort's Plan (five-layer trace: dialog -> decision dict ->
`LaunchRequest` -> launch-plan resolution -> the Manager's own
`launch-session.{ps1,sh}` script) and found a genuinely useful shortcut: a
seed-prompt mechanism (`--seed`/`--seed-ready-timeout`) ALREADY EXISTS on
`agent-worktrees copilot`, just on a command the Picker's creation flow
doesn't currently call (it only reaches an EXISTING worktree's "Launch in
new window" action, never the creation path, which goes through a
different, older script). This significantly de-risks Phase A: no new
seed-prompt PROTOCOL needs inventing, only new plumbing to reach the
existing one (or an equivalent explicit flag on the launch script, exact
mechanism TBD at implementation time).

No code changed yet this session -- this entry (+ the effort doc itself) IS
the handoff artifact. Next session should start at Phase A's first Plan
item (the `PivotFormScreen` field-rendering extraction) since it's shared
groundwork both phases depend on.

### 2026-09-30 — Phase A item 1: field-rendering extraction
Extracted `PivotFormScreen._compose_one`'s field-type -> widget-construction
logic (the `choice`/`multichoice`/`text`/`textarea` branches, `steering_form
.py:154-210`) into a new pure function `compose_field(f, i) -> (widgets, rec)`
in a new sibling module, `picker_tui/field_widgets.py`. It takes no `self`,
touches no draft/visibility/conditional-field state, and returns exactly the
same `rec` shape (`name`/`type`/`options`/`allow_other`/`primary`/`other`)
`PivotFormScreen._q` has always stored, minus the two caller-owned keys
(`show_when`/`visible`) a conditional-fields caller layers on afterward.
`PivotFormScreen._compose_one` now calls it and adds those two keys itself;
`steering_form.py`'s imports were trimmed to drop what moved out (`Input`,
`Widget`, `_AutoExpandTextArea`, `_OTHER_LABEL`, `_SteerRadioSet`,
`_SteerSelectionList` are no longer referenced directly there).

Verified byte-identical behavior: the three steer-form-focused suites
(`test_pivot_steering_modals.py`, `test_picker_steer_form_flow.py`,
`test_picker_steer_button_row.py`, 44 tests) pass unchanged, and the full
`worktree-manager/tests/production_picker` suite (840 passed, 1 skipped --
the skip pre-dates this change) is fully green, confirming no regression
anywhere else that touches this code path.

Next: Phase A item 2 (the new lean creation-prompt screen using
`compose_field` for its one `textarea` field), then item 3 (wiring into
`_open_optmenu()`'s flow) and item 4 (threading the prompt through the
decision dict -> `LaunchRequest` -> launch-script argument chain -- see this
README's own "Investigation already done" §5-6 for the exact call sites).

### 2026-09-30 — Phase A item 2: the new lean creation-prompt screen
Built `SeedPromptScreen` (`picker_tui/seed_prompt_screen.py`), a
`ModalScreen[str]` using `field_widgets.compose_field` for its one `textarea`
field. Deliberately its own class, not a `PivotFormScreen` reuse -- no
Confirm/Save/Reset row, no draft persistence, nothing to resume (a
skipped/blank prompt is exactly today's launch, unchanged). Dismisses with a
plain `str`: the collected (stripped) prompt, or `""` for skip/escape/blank
Launch -- never `None`, so a caller never needs a tri-state check.

**A real bug the mid-session rendering-preview check caught:** the first cut
put `padding: 1 0 0 0` directly on the 1-row-tall `#seed-buttons` `FocusGroup`
to add a visual gap above it -- but that padding eats into the widget's own
fixed `height: 1` content box, pushing its child `Static` row fully outside
the visible area. The Launch/Skip buttons were composed, mounted, and
logically present (`query_one` found them fine) but **invisible** -- headless
Pilot assertions on values/dismiss results never caught this since they don't
inspect rendered pixels. Caught by literally exporting the screen to an SVG
snapshot (`App.export_screenshot()`) and rasterizing it (`resvg-py`; `cairosvg`
needs a native `libcairo` this Windows box doesn't have, `svglib`+`reportlab`
hit the same native-backend gap) to actually look at it. Fixed by moving the
gap to `margin: 1 0 0 0` (space *outside* the widget) instead of `padding`,
matching `PivotFormScreen`'s own convention of a separate spacer `Static`
rather than padding a fixed-height button row. Re-rendered to confirm both
buttons are now visible and correctly styled.

**Takeaway for the rest of this effort (and Phase B's generic create-action
UI):** a CSS-driven modal's layout correctness is not fully covered by
headless Pilot assertions alone (widget presence/values only, not paint
'') -- an SVG-export + raster spot-check is a cheap, repeatable way to catch a
"logically there but invisible" layout bug before it ships. Worth doing once
per new screen, not just once here.

5 new Pilot tests (`test_seed_prompt_screen.py`): Launch with typed text,
Skip button, Escape, blank Launch (⇔ Skip), and whitespace-stripping. Full
`production_picker` suite: 845 passed, 1 skipped (the one unrelated failure
seen mid-run, `test_registered_pivot_action_menu_runs_and_invalidates`, is a
pre-existing timing flake -- passes clean in isolation, confirmed before
concluding this).

**A circular-import bug surfaced and was fixed in the same pass:**
`field_widgets.py` originally imported `_AutoExpandTextArea`/`_OTHER_LABEL`/
`_OTHER_SENTINEL`/`_SteerRadioSet`/`_SteerSelectionList` FROM `.steering` --
but `steering.py` itself imports `PivotFormScreen` from `steering_form.py`,
which imports `compose_field` FROM `field_widgets.py`, so any entry point
that reaches `field_widgets` before `steering`/`engine` have fully
initialized hit a partial-module `ImportError`. Worse, mid-fix (before
`steering.py` was updated to match), the two modules briefly defined their
OWN separate copies of these classes, producing a stealth isinstance-mismatch
failure (`WrongType: Node matching '#q-0'... found _AutoExpandTextArea`) that
only one test caught. Fixed properly, not papered over: the five widget
classes/constants now live ONLY in `field_widgets.py` (a dependency-free base
layer with zero import of `.steering`/`.steering_form`), and `steering.py`
imports them back for backward-compatible re-export. Full suite green after
the fix confirms both the cycle and the duplicate-class issue are resolved,
not just hidden by import order.

Next: Phase A item 3 (wiring `SeedPromptScreen` into `_open_optmenu()`'s
flow, skipped when "Bare" is selected) and item 4 (threading the collected
prompt through the decision dict -> `LaunchRequest` -> launch-script argument
chain).

### 2026-09-30 — Phase A item 3: wired into `_open_optmenu()`'s flow
`_open_optmenu()`'s `ScopeDlgScreen` confirm callback now branches on
whether "Bare" is among the confirmed options: Bare skips straight to
`_confirm_new_worktree(dlg)` exactly as before (nothing to seed -- a bare
worktree gets no Copilot bootstrap at all); otherwise it pushes
`SeedPromptScreen(target=f"{tm} {te}")` and, on its dismiss, calls
`_confirm_new_worktree(dlg, seed_prompt=result)`. `_confirm_new_worktree`
now always includes `options["seed_prompt"]` in the decision dict (`""` when
none was collected) -- `__main__.py`'s decision-handling doesn't read it yet
(that's item 4), so today it's inert but present, ready to thread through.

Updated the 5 pre-existing New-worktree confirm tests in `test_picker_tui.py`
for the new screen hop: each now presses Enter twice after confirming
Create (once to advance focus from the textarea to the button row, once
more to actually activate Launch) -- the same "accept+advance, not
accept+submit" mechanic `_AutoExpandTextArea`/`PivotFormScreen` already use.
Added 2 new tests: `test_new_worktree_bare_skips_seed_prompt` (Bare -> no
screen, `seed_prompt == ""`) and `test_new_worktree_seed_prompt_carries_
through` (a typed prompt reaches `options["seed_prompt"]` unchanged). Full
`production_picker` suite: 847 passed, 1 skipped. Also ran the rest of
`worktree-manager`'s test tree (`tests/` minus `production_picker/`): 643
passed, 5 pre-existing failures confirmed unrelated (mux-daemon,
trusted-materializer-parity, tarball-update tests -- none touch
steering/field_widgets/seed_prompt code).

### Phase A item 4 -- deliberately NOT started this session; a concrete
### recommendation for the next one instead of a rushed attempt
Threading the collected prompt from here to an actual typed keystroke in a
freshly-launched Copilot session is a materially different kind of risk than
items 1-3: those only touched Picker-internal TUI code with a thorough
Pilot-test harness. Item 4's target, `launch-session.{ps1,sh}`, is the ONE
canonical script every real "New worktree…" launch on this machine goes
through (`_run_relocated_mux_launch`'s own docstring: "the ONE canonical
muxed-launch implementation") -- it is ~2000 lines of PowerShell managing
mux-pane creation, bootstrap, and update sequencing, with no equivalent
Pilot-style test harness, and the harness's own "Validate beyond unit tests"
policy (`AGENTS.md`) explicitly calls for more than unit coverage before
landing a change to a shared launch path this consequential.

**What this session's investigation clarified that the original Plan didn't
know yet:** `agent-worktrees copilot --seed` does NOT pass `--seed` to the
`copilot` CLI process itself -- it delegates to `handoff_cli.cmd_embody`,
which (after the real mux pane + copilot process already exist) calls
`sessions.mux_seed_pane(pane, seed, ready_timeout=...)`: a pane-level
primitive that waits for Copilot's input prompt to actually appear inside
the mux pane, THEN sends the seed text as keystrokes. There is no
`copilot --seed` CLI flag to forward through the launch script's existing
`--` passthrough at all -- the effort README's original "confirm whether the
passthrough mechanism can carry it through unchanged" question is now
answered: **it cannot**, because the seeding happens one layer up, against
the pane, after the process is already running and ready.

**Recommended shape for item 4** (not yet implemented): expose the existing
`sessions.mux_seed_pane` primitive as a small, focused `agent-worktrees`
CLI subcommand (e.g. `agent-worktrees internal seed-pane --session <name>
--seed <text> --ready-timeout <n>`), callable from PowerShell/Bash via a
plain subprocess call, the same way the script already shells out to
`agent-worktrees resolve`/`remux`/etc. `launch-session.{ps1,sh}` would call
it once it knows the mux pane it just created holds the live `copilot`
process (right after the point where it currently just returns/attaches),
guarded behind a new optional argument (name TBD, e.g. `--seed-prompt`)
threaded from `_run_relocated_mux_launch`'s `args` <- `LaunchRequest.
seed_prompt` <- `decision["options"]["seed_prompt"]` (the last mile already
built this session). This reuses a primitive already proven correct in
production (`embody`/`handoff-cutover` already depend on it) rather than
reimplementing pane-ready-detection and keystroke-typing a second time in
PowerShell.

**Validation bar for whoever picks this up** (per the harness's own
"Validate beyond unit tests" policy): unit tests for the new CLI subcommand
and the argument-threading layers, PLUS an actual live "New worktree…"
launch from the Picker with a typed prompt, confirmed to land as the
session's real first interactive turn with no race against bootstrap --
this item should not be called done on unit tests alone.

### 2026-09-30 — Item 4 redesigned + mostly implemented, after the operator
### asked a better question than this effort's own original Plan
The operator, reviewing the "new CLI subcommand" recommendation above,
asked: **"Can this not be a standard part of `agent-worktrees create`?"**
That reframing turned out to be architecturally correct and significantly
simplified the implementation -- recorded here in full since it replaces
most of the "recommended shape" written earlier in this same session.

**Why it works:** the Picker's "New worktree…" never calls `agent-worktrees
create` directly -- it calls `agent-worktrees resolve --new --json`
(`engine_client.resolve_launch_plan`) -- but investigation confirmed **both
commands share the exact same core function**, `worktree_creation
._create_worktree_core()`. `create`/`resolve` are both documented as "no
launch, no mux": they can only ever PERSIST a seed as intent, never type it
(no pane/process exists yet at creation time) -- but persisting it on the
worktree's own tracking record, rather than threading it through
Picker-specific decision-dict/LaunchRequest/launch-script plumbing, means
**any** path that later creates a live Copilot session for that worktree
can discover and deliver it, not just the Picker's own launch-session
script. This also makes `--seed` usable directly from a script/automation
context (`agent-worktrees create --seed "..."`), which the original
Picker-only design never would have been.

**Implemented this session** (`plugins/agent-worktrees`):
1. **Persistence:** `tracking.WorktreeRecord` gained `pending_seed: str |
   None` (mirrors `bound_agent`'s "emitted only when set, byte-identical
   legacy YAML" convention exactly -- same read/write pattern in
   `load_record`/the raw-YAML content builder). Threaded through
   `tracking_lifecycle.create_new_record()` -> `worktree_creation
   ._create_worktree_core()` (new `pending_seed` kwarg, end to end).
2. **Both creation CLI surfaces gained `--seed`:** `resolve_cli.py`'s
   `resolve --new --seed <text>` (what the Picker actually calls) and
   `worktree_ops_cli.py`'s `create --seed <text>` (the direct
   agent/script-facing command) -- both pass straight to
   `_create_worktree_core(..., pending_seed=...)`.
3. **TWO independent consumption points**, since there are genuinely two
   ways a live Copilot session gets attached to a freshly created
   worktree, and item 4's Plan text originally only knew about one:
   - **`agent-worktrees embody`/`copilot`'s own "create" path**
     (`handoff_cli.cmd_embody`, the `--new`-or-fresh-worktree branch):
     when no explicit `--seed` is given, it now falls back to the record's
     `pending_seed` (`seed_from_pending` flag) and clears it on confirmed
     delivery (`seed_result["ok"]`) -- left in place on a timeout/failure so
     a later attach can retry, never silently lost.
   - **`cmd_embody`'s own RESUME ("already a live mux session") path** --
     this is the one the original Plan text didn't anticipate needing at
     all: the Picker's `launch-session.{ps1,sh}` creates the exact same
     `wt-<id>` mux-session name `cmd_embody` itself uses
     (`sessions.mux_session_name`), but *without ever calling embody* --
     so from `cmd_embody`'s perspective, calling it after the script has
     already stood up the pane looks exactly like an ordinary **resume**.
     The resume branch previously just reported `resumed: true` and
     returned -- now it ALSO checks for and delivers a `pending_seed`
     against the resolved pane (`mux_copilot_pane`/`mux_active_pane`)
     before returning, with the same confirmed-delivery-clears /
     unconfirmed-leaves-in-place contract as the create path. An explicit
     `--seed` is deliberately NOT delivered on this resume path (unchanged
     "one live session per worktree" contract -- only a *persisted*
     pending prompt is, since that's a leftover obligation from creation,
     not a fresh request).
4. **Picker plumbing, now much smaller than originally planned, but with
   ONE more hard blocker found and deliberately left for next time:**
   `picker_app.LaunchRequest` gained `seed_prompt: str | None`; `__main__
   .py`'s `action == "new"` decision handler reads `decision["options"]
   ["seed_prompt"]` into it -- that much is done and tested. **Reverted
   this session:** threading `seed_prompt` on into `engine_client
   .resolve_launch_plan()` (as a new `seed` kwarg, forwarded as `--seed`
   only when `new=True`) -- `engine_client.py` is at `worktree-manager`'s
   hard 1000-line module-size cap with ZERO slack (999/1000 BEFORE this
   session touched it at all, confirmed via `git show` against this
   session's own starting commit) and, unlike `tracking.py`'s shrink-only
   BASELINE (soft, explicitly wideneable via a reviewed
   `tools/module-size-baseline.json` edit -- done this session, 4078 ->
   4082), this is a hard CAP with no such escape hatch; the tool's own
   message is explicit: "split this module into smaller components." A
   proper split of `engine_client.py` is its own real refactor, not a
   corner to cut mid-feature by deleting unrelated lines elsewhere to buy
   headroom -- so the `seed` kwarg and its `--seed` forwarding were
   reverted back out (confirmed via `git checkout <pre-session-commit> --
   engine_client.py`/its test file) rather than shipped as a line-count
   workaround. **No new argument on `LaunchRequest` needed reaching
   `_run_relocated_mux_launch`'s `args` or `launch-session.{ps1,sh}` at
   all** -- that script's job is simply "exist and create the pane" exactly
   as it always has; something else (today: a dedicated `agent-worktrees
   embody --worktree-id <id>` call) discovers and delivers whatever got
   persisted onto the record.

Tests: `tracking`/`tracking_write` (round-trip unaffected -- 262 passed),
`embody` (6 new cases: pending-seed-on-create delivered+cleared,
unconfirmed-delivery-leaves-it, explicit-seed-doesn't-touch-pending,
pending-seed-on-RESUME delivered+cleared, unconfirmed-on-resume-leaves-it,
plus the pre-existing 46 unaffected -- 48 total). `engine_client`'s own
`seed`-forwarding was implemented, tested (4 new cases), THEN REVERTED this
same session once the module-size gate caught `engine_client.py`'s hard
1000-line cap (see above) -- its test file was reverted alongside it, back
to this session's own starting commit. `test_production_picker_transplant
.py` (2 new, both still valid and kept: `seed_prompt` reaches
`LaunchRequest` from the decision dict, blank normalizes to `None` not
`""` -- this layer doesn't touch `engine_client.py` at all). Targeted
`agent-worktrees` regression set (`tracking`, `tracking_write`,
`codename_cli`, `launch_preflight`, `owner_inheritance`, `paired_carve`,
`embody`, `handoff_cutover`): 480 passed. Full `worktree-manager` suite
(both `tests/` and `tests/production_picker/`): 644 + 847 passed, only the
same pre-existing unrelated failures seen before this session touched
anything (`test_mux_daemon`, `test_trusted_materializer_parity` x2,
`test_update` -- all four about update/tarball/materializer-parity
machinery nothing here touches). One additional pre-existing flake
observed and confirmed unrelated: `test_profile_assignment.py::
test_concurrent_allocation_serializes_bag_positions` races on a Windows
pycache-clearing guard (`plugin_activation`'s own anti-stale-bytecode
check, copilot-extensions #3802) under concurrent subprocess imports on
this busy shared machine -- nothing to do with tracking/create/embody.

**What's genuinely still missing -- TWO remaining pieces now, not one:**
1. `launch-session.{ps1,sh}` itself needs to actually CALL
   `agent-worktrees embody --worktree-id <id>` (no `--seed`
   flag -- let it discover `pending_seed` itself via the resume path just
   built) once it has created the worktree's pane, for a **brand-new**
   worktree creation launch only (never on an ordinary resume launch, where
   there's nothing newly pending in the overwhelming majority of cases,
   though harmlessly a no-op there too since a resume's record has no
   `pending_seed` unless one was persisted and never yet delivered). This
   is still real PowerShell/Bash surgery on the "ONE canonical muxed-launch
   implementation" script with no Pilot-style test harness, and still needs
   a live "New worktree…" launch with a typed prompt to confirm delivery,
   not just unit tests -- the validation bar recorded earlier in this same
   Journal entry family still applies, now narrowed to exactly this one
   call site instead of a whole new CLI subcommand plus multi-layer
   argument threading.
2. **New this session:** `engine_client.resolve_launch_plan()` needs its
   own `seed` kwarg + `--seed` forwarding (reverted here for the
   module-size reason above) -- genuinely needs `engine_client.py` split
   into smaller components first (a real refactor, not a quick follow-on),
   OR a deliberate, separately-reviewed decision to raise its hard
   1000-line cap in `tools/module-size-baseline.json`/wherever that cap is
   actually enforced from (distinct from `tracking.py`'s already-wideneable
   soft baseline) -- whoever picks this up should resolve that choice
   explicitly, not default to widening without discussion.

Without #2, `LaunchRequest.seed_prompt` reaches `_resolve_for()` but is
currently dropped on the floor there (never passed to
`resolve_launch_plan()`, so a locally created worktree's `pending_seed`
never actually gets persisted via the Picker's own flow) -- `--seed` on
`agent-worktrees create`/`resolve --new` directly (bypassing the Picker)
already works today and is the quickest way to verify the persistence +
both consumption paths end-to-end before #1/#2 are tackled. Everything
else in the chain (persistence, both consumption paths, the Picker UI
collecting the prompt and threading it onto `LaunchRequest`) is
implemented and tested.

### 2026-09-30 — PR #4768 opened, then hardened against real Copilot review findings
Pushed and opened PR #4768 for Phase A items 1-4. Its automated review came
back `COMMENTED` with 2 HIGH + 4 MEDIUM/LOW findings, all legitimate --
fixed rather than dismissed:

- **HIGH -- discarded prompt:** confirmed `LaunchRequest.seed_prompt` never
  reaches `resolve_launch_plan()` (the `engine_client.py` module-size
  blocker above), so the live Picker flow would silently drop a typed
  prompt. Fixed by gating `SeedPromptScreen` out of `_open_optmenu()`'s
  live flow behind a new `_SEED_PROMPT_ENABLED = False` module constant
  (`engine_maintenance_actions.py`) until both remaining seams land --
  the screen/helper/persistence/consumption code all stay built and
  tested, just not yet user-visible. The two integration tests
  (`test_new_worktree_bare_skips_seed_prompt`,
  `..._seed_prompt_carries_through`) now force the flag on via
  `monkeypatch` to keep exercising the real wiring; the other five
  pre-existing New-worktree tests reverted to their original (no extra
  screen hop) expectations.
- **HIGH -- unsafe pane targeting:** `cmd_embody`'s resume-path delivery
  used `mux_copilot_pane(wt_id) or mux_active_pane(wt_id)` -- the fallback
  is "whatever pane is currently active" (could be a bare shell), while
  `mux_seed_pane` treats any `❯` as ready and would happily submit the
  prompt as a shell command. Fixed: delivery now requires the
  registry-identified `mux_copilot_pane(wt_id)` specifically; the
  display-only `new_pane` JSON field keeps the friendlier fallback (purely
  informational, never a delivery target).
- **MEDIUM -- duplicate-delivery race:** two concurrent resume attempts
  could both read the same `pending_seed` and both call `mux_seed_pane`.
  Fixed with an explicit claim/restore pattern under `tracking._RecordLock`
  (new `_claim_pending_seed`/`_restore_pending_seed` helpers in
  `handoff_cli.py`): the claim (load -> clear -> save) is a short locked
  RMW per this codebase's own documented lock-scoping convention ("never
  hold the lock across I/O"); the slow `mux_seed_pane` wait happens
  unlocked afterward, with a second short locked RMW to restore the text
  only if delivery goes unconfirmed. Applied to BOTH consumption sites
  (create path and resume path).
- **MEDIUM -- missing real round-trip coverage:** the embody tests all
  replace `load_record`/`save_record` with fakes, so they couldn't catch a
  YAML-serialization bug. Added
  `test_create_new_record_pending_seed_round_trips` (mirrors the existing
  `..._bound_agent_round_trips` test exactly): a multiline, YAML-special-
  character prompt through real `create_new_record`/`load_record`, the
  no-prompt case omits the key entirely, and clearing + re-saving omits it
  again (not an empty/null scalar).
- **LOW fixes:** added this "Documentation impact" section (above) and a
  patch changefile for each touched plugin (`agent-worktrees`,
  `worktree-manager`, via `tools/changefile.py add`); replaced a personal
  machine-name alias (`tmichon-cloud1`) in a new test with a neutral
  `example-host` placeholder.

Also found and fixed two leftover duplicate/stale Plan checkboxes in this
README from an earlier editing pass (a duplicated, unchecked "build a new
lean creation-prompt screen" item sitting right under its own already-
`[x]`'d entry) -- the review flagged the doc at those exact line numbers.

Tests after all fixes: `handoff_cli`/`tracking` targeted regression set
(480 passed, now including the new round-trip test), full
`production_picker` suite (848 passed, 1 pre-existing skip). Both
`handoff_cli.py` and `tracking.py` stayed within their module-size budgets
throughout (1000/1000 and 4082/4082 respectively -- genuinely zero slack
left in `handoff_cli.py` now; any FURTHER addition there needs its own
trim-or-split, same as `engine_client.py` already does).

### 2026-09-30 — A second review round found three more real issues
Pushed the fixes above; the automated review ran again and found three
MORE legitimate issues (none repeats of the first round -- all fixed, not
dismissed):

1. **An explicit `--seed` left a separately-persisted stale `pending_seed`
   behind.** If a worktree had BOTH an unconsumed `pending_seed` (from
   creation) and the caller later ran `embody --seed "..."` explicitly, the
   explicit seed delivered fine but the old `pending_seed` was never
   touched -- a LATER ordinary resume would then re-deliver that stale
   prompt into an already-active conversation as an unwanted later-turn
   injection. Fixed: the create-path now ALWAYS claims (clears) any
   `pending_seed` under the write guard on first attach, regardless of
   whether an explicit `--seed` was also given -- an explicit seed
   supersedes AND consumes the stale one. Only a value that was actually
   *claimed* (not an explicit one) is restored if its own delivery goes
   unconfirmed, so a failed explicit `--seed` never resurrects an unrelated
   old prompt. Renamed the test
   (`test_explicit_seed_wins_and_supersedes_any_stale_pending_seed`) to
   assert the corrected contract with a stateful fake record.
2. **`--seed` was only threaded through ONE of `resolve --new`'s three
   creation call sites.** Fixed the other two: the interactive-TTY path
   (`resolve_launch_cli._resolve_new_context`, used when `--new` is given
   without `--json`) now also passes `pending_seed=getattr(args, "seed",
   None)`. The remote-machine path (`--machine` + `--new`, which relays a
   NAIVELY space-joined command string over SSH with zero shell quoting)
   does NOT attempt to thread `--seed` through that same unsafe
   string-concatenation -- proper quoting for an arbitrary remote shell is
   its own real, security-sensitive task, not a quick addition. Instead
   `--seed` is explicitly rejected when combined with `--machine`, with a
   clear error naming why; 2 new tests (`test_resolve_cli_seed_guard.py`)
   cover the rejection and confirm an ordinary (no-`--seed`) remote `--new`
   is completely unaffected.
3. **`create --seed`'s own help text overpromised delivery.** It said
   "whichever path first attaches a live session... delivers and clears
   it" -- true in intent, but today only `agent-worktrees embody`/`copilot`
   actually implement that contract; an arbitrary direct tmux/psmux attach
   does nothing. Reworded the help text (both `create --seed` and `resolve
   --new --seed`) and `_create_worktree_core`'s own docstring to name the
   actual supported consumer explicitly, matching the gated-off Picker
   flow's own honesty bar from the first review round.

Tests: agent-worktrees targeted regression set (`tracking`,
`tracking_write`, `embody`, `handoff_cutover`, `codename_cli`,
`launch_preflight`, `owner_inheritance`, `paired_carve`, plus the new
`test_resolve_cli_seed_guard.py`): 483 passed. Module-size gate: OK
(`tools/check-module-size.py` run directly, not just inferred from the
earlier pre-push hook output).

### 2026-09-30 — A THIRD review round found three more correctness issues
Three rounds of real findings now, each a genuine issue, none a repeat:

1. **`_RecordLock`'s default mode silently degrades** to an in-process-only
   lock when the cross-process sidecar times out (a deliberate graceful-
   degradation feature for *critical* writers per its own docstring) --
   which defeated the claim-once guarantee entirely under contention: a
   stalled holder and a degraded contender could both read+deliver the
   same seed. Fixed: both `claim_pending_seed`/`restore_pending_seed` now
   pass `require_sidecar=True` (fail closed -- `TimeoutError` caught,
   nothing claimed -- rather than ever degrade).
2. **The claim happened far too early** -- right where the record was
   first loaded, well before dry-run handling, profile/backend validation,
   lifecycle rejection, a concurrent-session check, or even confirming
   `mux_new_session` actually succeeded. Any of those early-return paths
   could permanently consume `pending_seed` with nothing ever delivered.
   Fixed: the create path now only PEEKS (read-only) at the pending seed
   early (for display/dry-run purposes), and does the real claim (lock +
   reload + clear + save) immediately before the actual `mux_seed_pane`
   call, after `mux_new_session` has already succeeded.
3. **`pending_seed`'s YAML serialization was unsafe for arbitrary text.**
   The hand-rolled `_yaml_scalar` helper (shared with simpler fields like
   `bound_agent`) only quotes a LEADING reserved-indicator character --
   a value like `"false"` would round-trip as the YAML boolean `False`,
   and a multiline or `": "`-containing prompt could produce invalid YAML
   entirely. Fixed: `pending_seed` now serializes through `yaml.safe_dump`
   (the same pattern this file already uses for structured fields like
   `dispatch_attempt`), which handles arbitrary scalars correctly.
   Strengthened the round-trip test with an explicit `"false"`-as-string
   case (not just multiline) to prove the fix.

Also extracted `claim_pending_seed`/`restore_pending_seed` into a new
sibling module, `pending_seed.py` (mechanical extraction, matching this
file's and this repo's own established "many small `_cli.py`/helper
modules, not one growing monolith" convention) -- `handoff_cli.py` kept
hitting its own 1000-line hard cap on every one of these fix rounds, and
splitting out a self-contained, independently-testable pair of functions
is the correct response once a module is genuinely full, not another round
of comment-shrinking. `tracking.py`'s own soft baseline was widened again
(4082 -> 4090) for the real `yaml.safe_dump` fix's few extra lines.

Tests: full targeted regression set (`tracking`, `tracking_write`,
`embody`, `handoff_cutover`, `codename_cli`, `launch_preflight`,
`owner_inheritance`, `paired_carve`, `resolve_cli_seed_guard`): 483
passed. Module-size gate: OK.
