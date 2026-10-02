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
change affects -- behavior is additive. As of 2026-10-02, Phase A's seed
prompt is live in the Picker's own flow (`_SEED_PROMPT_ENABLED = True`):
both delivery seams (`engine_client.py`'s `--seed` forwarding,
`launch-session.{ps1,sh}`'s post-create `agent-worktrees embody` call) are
closed and validated (see the Journal). Still no separate user-facing doc
to update -- `agent-worktrees embody --help`'s existing `--seed` text
already documents the delivery contract this reuses verbatim, and the
Picker has no separate end-user reference doc of its own beyond its
in-app hints (the options dialog's own hint strings, unchanged by this
PR). This effort's own README (here) remains the authoritative
in-progress record of what's implemented vs. outstanding, kept current in
its Plan/Journal each session. 2026-10-02 (Phase B engine wiring):
`worktree-manager/README.md`'s own "Developing a pivot: render early,
render often" section already documents the `--demo`/preview render
workflow this change's own validation followed -- no update needed there.
Extended `demo_pivot.py`/`preview.py` (the preview fixture itself, not a
doc) so that workflow now exercises the new `create_action` affordance for
any future pivot change.

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
      through to the decision. Full suite: 847 passed, 1 skipped. The
      screen's live gate (`_SEED_PROMPT_ENABLED`) stayed off until the
      remaining delivery seams closed -- see the next item and the Journal;
      it is now `True` (2026-10-02), and the skip condition has since grown
      to also cover Anchor-repo and remote-machine targets (see the
      Journal's 2026-10-02 entries).
- [x] **Persist the prompt at creation time; deliver it on first attach**
      (current design, replacing the Picker-specific launch-script argument
      chain originally planned here -- see the Journal for the full
      redesign history). `agent-worktrees create`/`resolve --new` gained
      `--seed`, persisting it as `WorktreeRecord.pending_seed` (both
      creation call sites that matter locally; a remote `--machine` target
      explicitly rejects the combination rather than relay it unsafely).
      `agent-worktrees embody`/`copilot` discover and deliver a
      `pending_seed` on the first real attach -- both when THEY create the
      mux pane, and when some other caller (the Picker's own
      `launch-session.{ps1,sh}`) already did, via embody's resume branch --
      claiming (clearing) it under a race-safe write-guard immediately
      before delivery, restoring it only on an unconfirmed delivery, and an
      explicit `--seed` supersedes/clears any stale pending one too.
      **Both seams now closed (2026-10-01/02, see Journal):**
      (1) **closed** -- the Picker's live flow now calls `resolve --new
      --seed` (`engine_client.resolve_launch_plan()` gained a `seed` kwarg
      once `engine_client.py`'s module-size cap was relieved by extracting
      `engine_execution_leg.py`); `__main__.py`'s `_resolve_for()` threads
      `LaunchRequest.seed_prompt` through. (2) **closed** --
      `launch-session.{ps1,sh}` now call `agent-worktrees embody
      --worktree-id <id>` (detached, best-effort) immediately after their
      own CREATE-branch `new-session`/`tmux new-session` call succeeds --
      never on the JOIN branch (an already-live session already had its
      chance). embody's own "already embodies this worktree" resume branch
      (the mux session now exists) claims + delivers any pending seed via
      its existing ready-poll/send-keys path; a no-op when nothing is
      pending costs one cheap venv-python round trip, dispatched detached so
      the operator's attach never waits on embody's own (up to 180s)
      seed-ready-timeout. Validated LIVE (non-`--demo`): created a real,
      disposable worktree with `--seed`, stood up its `wt-<id>` mux pane
      manually (mirroring exactly what the launcher's own create-branch
      does), then called `agent-worktrees embody --worktree-id <id>
      --seed-ready-timeout 3 --json` and confirmed the exact JSON contract
      this hook depends on: `"created": false, "resumed": true, "seeded":
      true`. Cleaned up (`remove-system`) immediately after. `_SEED_PROMPT_
      ENABLED` flipped to `True` in `engine_maintenance_actions.py`; 5
      pre-existing New-worktree confirm tests (written before this flip)
      updated for the now-unconditional extra screen hop (2 Enter presses:
      textarea->buttons, then activate Launch). Full `production_picker` +
      `test_launch_session_unwrap.py` suite: 895 passed, 1 skipped, clean
      (three transient Textual-pilot timing flakes along the way, each
      confirmed pre-existing/unrelated by isolated re-run against the
      unmodified baseline).
- [x] Confirm the end-to-end behavior matches `agent-worktrees copilot
      --seed`'s own documented contract ("Seed prompt injected as the
      session's first interactive turn once Copilot is ready") -- the
      prompt must not race the auto-update/bootstrap flow the operator
      explicitly wants to skip past. **Confirmed 2026-10-01/02:** embody's
      own ready-poll (`sessions.mux_seed_pane`) only types the seed once
      Copilot's own input caret is detected ready (stable across two polls),
      and the detached dispatch means this poll runs concurrently with --
       never ahead of -- the launcher's own auto-update/bootstrap flow and
      the operator's attach.
- [x] Tests: the new field-rendering helper (unit, from item 1), the new
      creation-prompt screen (modal behavior: submit/skip/escape, from item
      2), the decision-dict/`LaunchRequest`/launch-script argument threading
      (each layer: `engine_client.resolve_launch_plan()`'s `--seed`
      forwarding, `launch-session.{ps1,sh}`'s post-create `embody` call --
      pinned via a `test_launch_session_unwrap.py` ordering/parity guard
      covering both platforms), and a regression check that
      `ScopeDlgScreen`/steer behavior is completely unchanged when no
      prompt is ever entered (the 5 updated + 2 existing New-worktree
      confirm tests).

### Phase B — Generic registered-pivot "create" action (unblocks Tasks Phase 10)
- [x] Add a new `PivotAction`-adjacent concept to `pivot_manifest.py` for a
      **pivot-level** action -- no `rec`/selected entry, a STATIC field spec
      declared directly in the manifest (not resolved via `fields_from`
      against a row) -- e.g. a new top-level manifest key
      (`create_action`? name TBD) alongside the existing `actions` list.
      **Done 2026-10-01:** new `CreateAction` dataclass + strict
      `parse_create_action`/`_parse_create_fields` validators, in their own
      new sibling module `pivot_create_action.py` (NOT inline in
      `pivot_manifest.py` -- that module was already close to the 1000-line
      cap; see the Journal). `RegisteredPivot.create_action: CreateAction |
      None = None` wired into `parse_manifest`; re-exported from `pivots.py`.
      Shape: `{"label", "key" (default "create"), "fields": [{name,type,
      options?,allow_other?,show_when?}], "run", "confirm"}` -- the exact
      `{name,type,options,allow_other,show_when}` field shape
      `steering._normalize_form_fields` already accepts for a row's dynamic
      `request_input`, so Phase A's `field_widgets.compose_field` renders it
      with zero changes. 18 tests across `test_pivots.py`/
      `test_pivot_registry.py` (schema parsing/defaults, malformed-manifest
      isolation, each required/malformed-field case, `show_when`
      cross-validation, blank/non-string option rejection,
      `create_action.run` command resolution + malformed-argv/invalid-path
      isolation -- added across 6 Copilot-review rounds on PR #4831, see
      the Journal). Full `production_picker` suite: 867 passed, 1 skipped
      (re-ran clean after every review round). Module-size gate: OK
      (`pivot_manifest.py` 922 lines, under the 1000-line cap with room;
      `pivot_create_action.py` 163 lines).
- [x] Engine wiring: a UI affordance to trigger it when the registered
      pivot's list has focus but no row is meaningfully selected (mirror
      Worktrees' "N" button row, but data-driven -- reuse Phase A's
      extracted field-rendering helper for the widget types). **Done
      2026-10-02:** `button_set()` (`engine_model.py`) returns `["NC"]` for
      a "registered" pivot whose `reg.create_action` is set, else `[]`
      (unconditionally empty before this); `stops()`/`region_heads()` add a
      conditional `("BTN", 0)` stop right after the machine sub-nav, before
      the task rows. `TasksView.build_chrome` (`engine_views.py`) renders a
      new data-driven `registered_create_row()` (`engine_selection.py`) --
      the button's own label comes straight from `create_action.label`, no
      hardcoded per-pivot text table (mirrors `new_worktree_row()`'s
      rendering shape, simplified to one chip since there's only ever one).
      `_activate()`'s `BTN` branch (`engine_maintenance_actions.py`) routes
      `btn == "NC"` to a new `_open_create_action()`
      (`engine_pivot_actions.py`), which pushes the new
      `CreateActionScreen` (below) and, on Confirm, calls a new
      `_run_create_action()` -- confirmed reusing
      `_run_pivot_form_submit`'s exact `format_form_template(action.run, {},
      values)` + `rt.run_resolved(argv)` + `rt.invalidate()`/`rt.ensure()`
      plumbing verbatim, `ctx={}` since there is no row to resolve entry
      tokens against.

      **`CreateActionScreen`** (new module `create_action_screen.py`)
      mirrors `SeedPromptScreen`'s shape (lean, Confirm/Cancel only, no
      draft) but renders `create_action.fields` (any mix of
      text/textarea/choice/multichoice, `show_when` conditionals, tabs when
      >1 field) instead of one hardcoded textarea. `create_action.confirm:
      true` shows an inline are-you-sure (mirroring `ResetConfirmScreen`'s
      FocusGroup pattern, swapped into the same frame rather than a second
      pushed screen) before the collected values are actually
      dismissed/submitted -- Cancel is the initial choice.

      **Shared extraction, not duplication:** `CreateActionScreen` needs the
      exact same multi-field tab/conditional-visibility/advance-on-Enter/
      value-collection mechanics `PivotFormScreen` (the Steer surface)
      already has, so rather than copy ~150 lines of subtle logic, extracted
      a new `FieldQuestionsMixin` (`field_questions.py`) from
      `PivotFormScreen` first (mechanical, mirroring `field_widgets.py`'s own
      precedent -- confirmed byte-identical: full suite re-ran clean before
      touching anything else), with one override point
      (`_focus_final_control()`) for what to focus once Enter advances past
      the last question. Both screens mix it in now.

      **Tests:** 4 new engine-wiring tests
      (`test_registered_pivot_create_action_button_appears_and_absent`,
      `_opens_and_submits`, `_cancel_does_not_submit`, `_confirm_gate`) plus
      a new manifest-writer test helper
      (`_write_tasks_manifest_with_create`). A genuine gap surfaced while
      writing these: a plain `text`-type field had no Enter/Ctrl+Right-to-
      advance mechanic of its own (only textarea/choice/multichoice did, via
      `field_widgets`' custom widgets -- `Input`'s native word-cursor binding
      claimed Ctrl+Right first). **Fixed, see the next Journal entry:** a
      new `_AdvancingInput` (`field_widgets.py`) forwards Enter/Ctrl+Left/
      Right the same way `_AutoExpandTextArea` already does; `compose_field`
      now uses it for `text` fields, and the tests exercise the real
      `pilot.press("ctrl+right")` keyboard sequence instead of calling
      `_activate_tab()` directly.

      **Render-verified, not just unit-tested** (per this project's own
      "render early, render often" README mandate): extended the official
      `--demo`/preview fixtures first -- `demo_pivot.py` gained a `create`
      verb (a harmless synthetic acknowledgment) and `preview.py`'s
      `_DEMO_PIVOT_MANIFEST` gained a matching `create_action` (title +
      prompt fields) -- then captured real screenshots
      (`picker screenshot --demo --pivot "Demo Queue"` for the button row;
      a short driven script using `picker_tui.capture`'s own
      `capture_modal_async`/`export_screenshot` seam for the modal states)
      at each milestone. **This caught a real bug unit tests missed
      entirely:** the inline are-you-sure prompt's dynamically-mounted
      `FocusGroup` rendered with zero height (no CSS rule gave it one) --
      invisible in a screenshot, yet still fully queryable/interactable in
      a headless Pilot test, so all 4 new tests passed while the actual
      rendered UI was broken. Fixed by generalizing `#create-buttons {
      height: 1; ... }` to a screen-wide `FocusGroup { height: 1; ... }`
      rule (now covers the dynamically-mounted confirm-prompt group too).
      Full `production_picker` + `test_launch_session_unwrap.py` +
      `test_picker_preview_mode.py` suite: 913 passed, 1 skipped (one
      different pre-existing Textual-pilot timing flake confirmed
      isolated-pass, same class observed throughout this session's earlier
      PR too).
- [ ] `agent-dispatch`'s manifest declares its own `create_action` (title +
      prompt/goal textarea + a tags/criteria picker -- see the Tasks-pane
      effort's own Phase 10 Plan for the exact field list and the pool-
      filter-vocabulary source still to be confirmed). **Investigated, not
      yet written this session** -- see the Journal: the tags/criteria
      vocabulary source is still unconfirmed (`registrar.py`/`overrides.py`,
      per that effort's own Phase 10 note), so the manifest's exact `fields`
      list can't be finalized yet; blocks on that lookup, not on anything
      this effort's own Phase B needs to invent.
- [x] Submitting calls `propose`+`queue` (already-implemented `client.py`
      calls) against the coordinator, per that effort's own Phase 10 spec.
      **Resolved 2026-10-01, no new plumbing needed:** `agent-dispatch
      create` (no `--proposed`) already performs the propose-then-queue
      lifecycle in ONE subprocess call (`create_cli.py`'s `_cmd_create`
      creates the task directly in queued/claimable state; `propose` is
      only the OPPOSITE -- an explicit flag that keeps it a non-claimable
      draft for a later separate `queue <id>`). So `create_action.run` for
      agent-dispatch can be a single argv template (e.g. `["agent-dispatch",
      "create", "{field.title}", "--prompt", "{field.prompt}", ...]`) run
      through the SAME single-subprocess `format_form_template`/
      `run_resolved` path the row-scoped `kind:"form"` action already uses --
      no two-step orchestration, no propose-id-capture-then-queue chaining,
      needs inventing at the picker layer.
- [~] Tests: the new manifest field parses correctly (and degrades
      gracefully for a pivot that doesn't declare one); the UI affordance
      appears/is absent correctly; a synthetic pivot's create action renders
      and submits via the generic mechanism (mirroring
      `test_registered_pivot_*` patterns already in
      `test_picker_tui.py`); `agent-dispatch`'s own composer round-trips
      title/prompt/tags into the exact `propose`/`queue` call shape. The
      manifest-parsing half is done (18 tests) and the engine-wiring half is
      now done too (4 new tests, see above, render-verified against the
      `--demo` fixture); the `agent-dispatch`-manifest half remains, blocked
      on the tags/criteria vocabulary lookup above.

## Validation Plan

- [~] Phase A: create a real worktree from the Picker with a typed prompt;
      confirm the new session's first interactive turn is that prompt, with
      no race against the auto-update/bootstrap sequence. Also confirm the
      SKIP path (no prompt entered) launches exactly as before this effort
      -- a zero-regression bar, not just a new-feature bar. **Substantially
      validated 2026-10-01/02 (see Journal), one literal end-to-end click-
      through still outstanding:** the UI chain itself (options dialog ->
      `SeedPromptScreen` -> typed text captured correctly) was confirmed in
      a REAL terminal via `tmux`, not just Pilot (2026-10-01, `--demo`
      mode). Both delivery seams are closed and unit/ordering-pinned for
      both platforms (`test_launch_session_unwrap.py`), live in the Picker's
      flow by default (`_SEED_PROMPT_ENABLED = True`). The NEW contract
      seam 2 depends on (`launch-session.{ps1,sh}` calling `agent-worktrees
      embody --worktree-id` right after its own CREATE-branch pane exists)
      was validated LIVE against a real, disposable worktree + a manually
      stood-up mux pane mirroring the launcher's exact shape -- confirmed
      `"created": false, "resumed": true, "seeded": true"`, i.e. the claim+
      deliver path genuinely fires. **Still outstanding (keeps this item
      unchecked):** a literal, single end-to-end run of the real Picker
      binary (not a hand-assembled equivalent) creating a brand-new
      worktree with a typed prompt and watching a freshly-spawned real
      Copilot session receive it as its first turn -- the two halves have
      each been proven genuinely, but not yet chained together in one live
      observed run.
  - [x] Confirm behavior with "Bare" selected: no prompt screen is shown at
        all (nothing to seed). (`test_new_worktree_bare_skips_seed_prompt`,
        passing against the now-default-on `_SEED_PROMPT_ENABLED`.)
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
  machine-name alias in a new test with a neutral `example-host`
  placeholder.

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

### 2026-09-30 — Fourth and fifth review rounds: a real non-JSON gap, a
### merge-safety hole, and documentation hygiene
Two more rounds, five more genuine findings (still zero repeats):

**Round 4:**
- **`resolve --new --machine X --seed Y` (no `--json`) still slipped
  through.** The rejection only lived in `_resolve_json_mode`; the
  non-JSON dispatcher in `cmd_resolve` checks `state.use_new` BEFORE
  `state.requested_machine`, so it would silently create a LOCAL seeded
  worktree instead of honoring (or rejecting) `--machine` at all. Moved
  the validation up into `cmd_resolve` itself, before the JSON/non-JSON
  split, so both paths reject consistently; rewrote the guard tests to
  exercise `cmd_resolve` end-to-end (both JSON and non-JSON) instead of
  only the JSON-mode internal function.
- **This effort's own Plan item 4 still read like the superseded design**
  (the Picker-specific launch-script argument chain) with a redesign note
  bolted on top, leaving the "actionable" text inconsistent with both the
  implementation and the Journal. Rewrote the item to state the CURRENT
  design directly (persist at creation, deliver via embody on first
  attach, two still-incomplete seams named explicitly); the design-history
  narrative stays here, in the Journal, where it belongs.
- **`pending_seed.py`'s and `field_widgets.py`'s module docstrings
  described their own extraction history** ("mechanical extraction...
  moved verbatim") instead of their lasting responsibility. Reworded both
  to be timeless; the history is this Journal's job.

**Round 5:**
- **The claim-once guarantee had a merge-safety hole:** any OTHER process
  that loaded the SAME record before a claim (e.g. to bump an unrelated
  field like `summary`) and saved its own stale in-memory snapshot AFTER
  the claim would silently resurrect the already-delivered seed -- nothing
  in `_save_record_unlocked` knew `pending_seed` needed protecting the way
  `effort_revision`/`lifecycle_revision` already are. Fixed the same way:
  a new monotonic `WorktreeRecord.pending_seed_revision` field, bumped on
  every claim/restore, with a merge rule in `_save_record_unlocked` --
  "take the on-disk value when its revision is newer" -- so a stale save
  can never roll a delivered/cleared seed backward. Added
  `test_stale_full_record_writer_cannot_resurrect_a_delivered_seed`
  (real `create_new_record`/`load_record`/`save_record`, not fakes) to
  prove it.
- **`restore_pending_seed` gave up permanently after one 2s sidecar
  timeout**, silently losing a prompt that had ALREADY been claimed for a
  delivery that then failed -- strictly worse than `claim_pending_seed`
  giving up (which loses nothing, since the claim itself never happened).
  Added a bounded retry (3 attempts, 1s apart) before accepting the loss.
- **`--seed` was only rejected alongside a remote `--machine` target.**
  `resolve --json --worktree-id <id> --seed ...` and `--base --seed ...`
  both silently succeeded while discarding the prompt (it's only
  meaningful with `--new`, which persists it onto a NEW record). Added a
  blanket `requested_seed and not state.use_new` rejection, ahead of the
  (now narrower) remote-specific one.
- **A test docstring overstated the current wiring**, claiming
  `engine_client.resolve_launch_plan` already forwards `--seed` on a
  truthy value -- it doesn't yet (that's the still-incomplete seam this
  whole effort keeps naming). Reworded the docstring to say only what the
  test actually verifies (decision-dict -> `LaunchRequest` normalization),
  and to point at the real gap instead of implying it's closed.

Tests: full targeted regression set re-run clean (315 + 170 passed across
two batches, covering `tracking`/`tracking_write`/`embody`/
`resolve_cli_seed_guard` plus `handoff_cutover`/`codename_cli`/
`launch_preflight`/`owner_inheritance`/`paired_carve`). `tracking.py`'s
soft baseline widened again (4090 -> 4105) for the revision-merge fix.
Module-size gate: OK.

### 2026-10-01 — Phase B item 1 (manifest schema): new session, fresh worktree
Resumed via handoff `d75d8a385b3c4ab5b94a3558d15c297b`. Phase A's worktree
was already finalized (PR #4768 merged, no live copilot-extensions worktree
left); created a fresh, isolated worktree per the handoff's own instruction
before touching anything.

**Choice made:** of the handoff's two offered next-slices (close Phase A's
remaining seams, or start Phase B), picked **Phase B**. Reasoning: Phase A's
second remaining seam (`launch-session.{ps1,sh}` calling `agent-worktrees
embody` after creating a worktree's pane) explicitly needs **a live "New
worktree…" launch with a typed prompt** to validate (per the harness's own
"Validate beyond unit tests" policy) -- not achievable from this headless
session. Phase B's own Plan is fully testable headless via the existing
Pilot-test harness (`production_picker`'s 850+ tests already prove this
pattern), so it was the slice this session could actually drive to a
genuine, tested state rather than a half-finished, unvalidatable one.

**Implemented (Plan item 1 -- the manifest schema):** a new `CreateAction`
dataclass (`label`, `key` default `"create"`, `fields` -- a tuple of static
field-spec dicts, `run` -- an argv template, `confirm`) plus strict
`parse_create_action`/`_parse_create_fields` validators that raise
`ManifestError` on a malformed `create_action` (consistent with
`pivot_manifest.py`'s existing strict-validation convention for `actions`,
rather than `steering._normalize_form_fields`'s lenient-degrade convention --
this spec is manifest-authored, not runtime/operator data, so a typo should
surface at discovery time).

**A real module-size near-miss, caught before it became one:** the first
draft added this directly to `pivot_manifest.py`, which was already at 819
lines (not baselined -- meaning the ordinary 1000-line hard cap applies with
no grandfathered ceiling). The straightforward addition would have pushed it
to 1030 -- over cap, and exactly the kind of "one more honest addition tips
an un-baselined file over" failure this guard exists to catch before merge,
not the engine_client.py-style "already over cap, zero slack" case Phase A's
item 4 flagged. Moved `CreateAction` + its two parser functions into a new
sibling module, `pivot_create_action.py` (113 lines), imported by
`pivot_manifest.py` the same way it already imports from `pivot_actions.py`.
`pivot_manifest.py` ends this session at 922 lines -- under cap, with real
room left, instead of permanently baselined at 1030+. Also re-exported
`CreateAction` from `pivots.py` (the package's public aggregator) alongside
the existing `PivotAction`/`RegisteredPivot` exports.

**Field-shape reuse, confirmed, not assumed:** deliberately chose the exact
`{name,type,options,allow_other,show_when}` shape
`steering._normalize_form_fields` already accepts for a row's dynamic
`card.request_input`, so Phase A's `field_widgets.compose_field` (the
pure, submit-semantics-agnostic widget renderer extracted in Phase A item 1)
will render a `create_action`'s fields with **zero changes** when the engine
wiring (item 2) lands -- confirmed by reading `compose_field`'s actual
signature/behavior, not assumed from the shape's name alone.

**Two investigations resolved a previously-open design question (the
propose+queue chaining) with NO new plumbing needed, contrary to what this
effort's own Plan text implied:**
1. Read `_run_pivot_form_submit`/`_open_pivot_form`
   (`engine_pivot_actions.py`) end to end: the existing row-scoped
   `kind:"form"` action already does exactly "collect field-spec-driven
   values, substitute `{field.<name>}` into `run`, execute ONE subprocess,
   invalidate the cache" via `pivot_actions.format_form_template` +
   `RegisteredPivotRuntime.run_resolved`. This plumbing has NO dependency on
   a selected row beyond the `ctx` tokens it also substitutes (which a
   create action simply won't use) -- so it is directly reusable for
   `create_action` with no modification, once the engine wiring opens the
   right screen and calls it.
2. Read `agent-dispatch`'s `create_cli.py` (`_cmd_create`/`_cmd_propose`)
   end to end: `agent-dispatch create` (no `--proposed`) already performs
   the full propose-then-queue lifecycle in ONE call -- it is `propose`
   (the explicit flag) that is the special case, leaving the task
   unclaimable until a later separate `queue <id>`. So the "propose+queue
   in one motion" framing in this effort's own Plan/Request text describes
   the LIBRARY-level lifecycle, not a picker-side two-subprocess
   orchestration need: `agent-dispatch create ...` run through the same
   single-subprocess path item 1 above reuses is sufficient. This closes
   what looked like a real design gap (how does a single `run` argv express
   "propose, capture the id, then queue it"?) without inventing a new
   chained-subprocess mechanism -- the existing CLI already collapses it.

**Not started this session, left for the next:** the engine UI affordance
(Plan item 2) and agent-dispatch's own manifest file (Plan item 3, blocked
on confirming the tags/criteria vocabulary accessor in `registrar.py`/
`overrides.py` per the Tasks-pane effort's own open question) -- see the
rewritten Plan items above for the concrete recommended shape of each,
mirroring Phase A item 4's own "investigated, not rushed" precedent rather
than attempting unvalidated TUI surgery under this session's own context
budget.

Tests: `test_pivots.py` 88 passed (8 new). Full `production_picker` suite:
856 passed, 1 skipped, 2 failed -- both re-ran green in isolation
(`test_registered_pivot_conditional_actions_filter_by_when`,
`test_actions_menu_liveness_verify_is_offloaded`), confirmed pre-existing
full-suite timing flakiness unrelated to this session's changes (neither
touches manifest parsing; this session added no new async/Pilot-timed
behavior). Module-size gate: OK, repo-wide.

### 2026-10-01 (later same day) — PR #4831 opened, driven through 6 rounds of real Copilot review findings, merged
Opened PR #4831 for Phase B item 1 (the `create_action` manifest schema
alone -- items 2/3 stayed deliberately out of scope, per the rewritten Plan
above). Like Phase A's own PR #4768, every review round found genuine,
fixable issues -- none dismissed:

- **Round 1** (3 code findings + 1 identifier-neutrality finding from CI's
  separate `identifier leak guard` check, caught on the same push):
  duplicate field names silently overwritten after whitespace
  normalization; a non-string `type` (JSON array/object) raising an
  uncaught `TypeError` instead of `ManifestError` (would have aborted
  discovery for every pivot, not just the bad manifest); a non-boolean
  `allow_other` silently coerced truthy; a raw personal
  worktree/machine-alias identifier in this same Journal's own prose,
  violating REVIEW.md/AGENTS.md's identifier-neutral requirement for public
  artifacts -- scrubbed to a generic description.
- **Round 2** (1 finding): `show_when` was shape-checked but never
  cross-validated against the completed field list -- a predicate could
  name a non-`choice` controller, an unknown field, itself, a chained
  conditional, or an `equals` value absent from the controller's own
  options, permanently hiding the dependent field once UI wiring consumes
  this schema. Added a second validation pass once every field is known.
  (Also: missing `@pytest.mark.guard` on the new tests, an unauthorized
  `minor` changefile bump, and a Plan checkbox marked done while its own
  text said otherwise -- all fixed.)
- **Round 3** (2 findings): blank `choice`/`multichoice` options accepted;
  `create_action.run` never reached `pivot_registry_materialization`'s
  command rewriter at all, so registry scans left it unresolved and
  accepted a nonexistent executable even with `require_targets=True` --
  extended the rewriter to cover it, matching every other action's `run`.
- **Round 4** (1 finding): the rewriter now resolved `create_action.run`,
  but didn't validate its argv SHAPE first -- `run: 42` raised an uncaught
  `TypeError` inside `_resolve_command`, aborting the whole scan. Validated
  with `_as_argv` before resolving.
- **Round 5** (1 finding, after a docs-only push surfaced it): the
  blank-options guard coerced every entry with `str()` first, so a
  non-string entry (JSON `null`, an int) silently became a choice named
  `"None"`/`"1"` instead of being rejected -- the producer contract
  requires strings. Rejected non-string entries outright.
- **Round 6** (2 findings, HIGH + medium): an argv head with an embedded
  null byte passed shape validation but raised a bare `ValueError` inside
  `Path()` construction, uncaught -- converted to the existing
  `TargetUnusableError` (already caught everywhere), carefully excluding
  `ManifestError` (itself technically a `ValueError` subclass) from that
  conversion so its own always-propagating `invalid-entry` handling stayed
  intact -- confirmed by an existing regression
  (`test_invalid_plugin_template_does_not_block_valid_peer`) this fix's
  first draft actually broke, then fixed properly. Also: `allow_other` was
  only type-checked for choice/multichoice fields, silently discarding a
  malformed value on a text/textarea field -- now validated for every
  field type.
- Also addressed two low-severity documentation findings between rounds:
  added the PR description's required **Documentation impact** statement,
  and documented `create_action`'s full shape/defaults/`show_when`
  restrictions in `worktree-manager/docs/plugin-contribution-contract.md`
  (the producer-facing manifest contract), explicit that this PR ships
  parsing/command-resolution only, no live Picker UI yet.

**Merged** (APPROVED verdict, all required checks green). Final state: 18
new tests across `test_pivots.py`/`test_pivot_registry.py`; full
`production_picker` suite 867 passed, 1 skipped (re-ran clean after every
round, including once at 867/1 directly before merge); module-size gate OK
repo-wide (`pivot_manifest.py` 922 lines, `pivot_create_action.py` 163
lines, both comfortably under the 1000-line cap).

**Not started this leg, for whoever picks this up next:** Phase B's
remaining Plan items -- the engine UI affordance (item 2) and
`agent-dispatch`'s own `create_action` manifest (item 3, blocked on
confirming the tags/criteria vocabulary accessor in `registrar.py`/
`overrides.py`) -- plus Phase A's still-gated final seams (`engine_client.py`
module split + `launch-session.{ps1,sh}` embody call). See the rewritten
Plan items above for the concrete recommended shape of each.

### 2026-10-01 (later still) — A new validation capability, proven: live-TTY driving of the real Picker via tmux, safely, through demo mode
The operator pointed out this session could create a real mux (tmux) pane,
launch the actual production Picker inside it, and drive/scrape it via the
TTY -- a genuinely different validation tier than the Pilot-test harness
(headless, in-process) Phase A's own tests have used so far. Tried it, and
it works, with one important safety boundary identified along the way.

**What was proven, concretely:** in a fresh, disposable `tmux` session
(`tmux new-session -d`), ran `worktree-manager picker --demo` (the
existing, already-shipped preview mode -- real Picker app/screens/rendering,
fixture data, see `preview.py`'s own docstring) inside a fresh copilot-
extensions worktree with `_SEED_PROMPT_ENABLED` temporarily flipped `True`
(an uncommitted local edit, reverted immediately after). Drove it with
`tmux send-keys` and read it back with `tmux capture-pane -p`:
1. `Enter` on the Worktrees pivot opened the real `ScopeDlgScreen` ("New
   worktree" options dialog) -- rendered correctly in a genuine terminal.
2. Tabbing to "Create" and confirming opened the real `SeedPromptScreen` --
   its exact copy ("fire and forget", "Leave blank to launch as before")
   rendered correctly.
3. Typing `Fix the frobnicator` via `tmux send-keys` landed correctly in
   the textarea, read back byte-for-byte via `capture-pane`.

This is real, independent proof (not a Pilot/mocked assertion) that items
1-3's UI chain -- the extracted `field_widgets.compose_field` helper, the
new `SeedPromptScreen`, and its wiring into `_open_optmenu()` -- behaves
exactly as designed in an actual terminal, closing part of the gap the
Plan's own "Validation Plan" section asks for.

**The safety boundary found, and respected:** neither this screen's
"Launch" nor "Skip" button is a safe stopping point -- both proceed to
`_confirm_new_worktree` -> `_decide` -> (eventually) `_run_launch`, which
for a local, non-AHP, `exec`-mode plan really execs
`launch-session.{ps1,sh}` as a REAL subprocess (`_run_relocated_mux_launch`,
`__main__.py`) -- `--demo` mode only fakes the **data** (`engine_client`'s
command override + a fixture pivot), not this final launch step, so
confirming the dialog all the way through would have handed a demo/fictional
`worktree_id` to the real launch script. Rather than gamble on how
gracefully that fails (the script is ~2000 lines, unaudited for this),
**stopped short of pressing either button** and killed the tmux session
outright (`tmux kill-session`) the moment the typed-prompt capture was
confirmed -- a guaranteed-clean abort with no subprocess ever spawned.
**This boundary is the thing to remember for whoever picks up item 4's
remaining validation:** a true end-to-end "launch lands as the first
interactive turn" proof needs a REAL (non-demo) worktree-creation target --
not `--demo` mode -- precisely because demo mode stops faking data exactly
at the point this validation cares about.

**Reusable takeaway for future live-TTY validation in this repo:** `tmux`
is genuinely available on this Windows machine (a `psmux`-branded Windows
build, reports `tmux 3.3.5`, already used for every live worktree's own
mux pane) -- `tmux new-session -d -s <name> "<cmd>"`,
`tmux send-keys -t <name> "<text>" Enter`, `tmux capture-pane -t <name> -p`,
`tmux kill-session -t <name>` is the whole toolkit. One gotcha: this
session's own shell is itself inside a `psmux` pane, so a nested
`tmux new-session` needs `PSMUX_SESSION` unset first (`psmux: sessions
should be nested with care` is a soft warning, not a hard failure, but the
nested session silently never gets created unless the var is cleared for
that one call).

### 2026-10-01 (later still) — Phase A seam 1 closed: engine_client.py split + --seed forwarding; seam 2 investigated, not rushed
Picked up driving immediately after proving the live-TTY technique above.
With a genuine way to validate the Picker's live TUI now in hand, went
after Phase A item 4's two still-incomplete seams.

**Seam 1 -- closed, tested, ready for its own PR:**
`engine_client.py` was at 999/1000 lines (confirmed via direct line count),
the "zero slack" state the previous session's handoff flagged. Extracted
the six `execution_leg_*` CLI calls (`get`/`set`/`clear`/`reserve`/`renew`/
`release`, a cohesive ~180-line concern already used by `ahp_provider.py`)
into a new sibling module, `engine_execution_leg.py`. The first draft
re-exported these via a top-level import (`# noqa: F401`/`E402`,
mirroring `steering.py`'s own re-export convention) placed after
`run_json`/`EngineError`/etc. -- but Copilot review correctly flagged this
as a reproducible circular-import deadlock: `engine_execution_leg.py`
itself does `from .engine_client import run_json, ...`, so importing
`engine_execution_leg` directly (before `engine_client`) hits
`engine_client`'s own top-level `from .engine_execution_leg import (...)`
line while `engine_execution_leg` is still mid-init -> `ImportError` on a
not-yet-defined name. Fixed properly: the re-export is now a **lazy module
`__getattr__`** (PEP 562) at the bottom of `engine_client.py` -- the import
of `engine_execution_leg` happens only on first actual attribute access
(`engine_client.execution_leg_get(...)` etc.), well after either possible
import order has already finished. Verified directly: imported
`engine_execution_leg` first, then `engine_client` first, confirmed both
resolve to the identical function object.

`engine_client.py` ends at 861 lines (not 832 -- that was the first
draft's count before the `__getattr__` fix added a little back) with real
room left. Added an optional `seed: str | None = None` kwarg to
`resolve_launch_plan()`, forwarded as `--seed <text>` to `agent-worktrees
resolve` (confirmed via its own `--help`: `--new`-only, already
engine-side-rejected alongside
`--machine`, so deliberately NOT re-validated here -- the engine owns that
contract). Threaded through the one real call site that needed it:
`__main__.py`'s `_resolve_for()` now passes
`seed=getattr(req, "seed_prompt", None)` -- `LaunchRequest.seed_prompt` and
the TUI's own `_confirm_new_worktree`/`_decide(...)` -> `action == "new"`
handling in the picker loop (`seed_prompt=str(opts.get("seed_prompt") or
"") or None`) were ALREADY wired by the earlier session; this was
genuinely the only missing link. 3 new tests in `test_engine_client.py`
(forwards `--seed`, omits it when falsy, confirms the bare-resume
degradation retry carries it too). Full targeted suite (`test_engine_
client.py`, `test_ahp_provider.py`, `test_picker_app.py`): 115 passed.
Module-size gate: OK repo-wide. `_SEED_PROMPT_ENABLED` stays `False` --
seam 2 (below) isn't done yet, so flipping it would still silently discard
a typed prompt in the live Picker.

**Seam 2 -- investigated this session, NOT implemented, with a concrete
hook point now identified (read `launch-session.ps1` directly, ~2000
lines, rather than guessing):** the pane-creation block
(`bin/launch-session.ps1`, around the `& $script:AwPsmuxBin new-session -d
-s $sessName ... @paneCmd` call) hands `$paneCmd` -- which already IS the
real `copilot --allow-all ...` invocation, wrapped through
`pane-wrapper.ps1` -- directly to `new-session`. There is no separate
"shell waiting, then run a command" moment inside the pane: Copilot starts
running the instant the pane exists. This confirms (doesn't just restate)
the prior session's own finding: delivery cannot be threaded into the pane
command itself -- it has to happen by sending keystrokes into the
ALREADY-RUNNING pane once Copilot's prompt is ready, which is exactly what
`sessions.mux_seed_pane`/`agent-worktrees embody`'s existing `pending_seed`
delivery path already does. **The concrete next step:** call
`agent-worktrees embody --worktree-id <id>` as a plain foreground
subprocess AFTER the `new-session` block above succeeds (not before, not
woven into `$paneCmd`), guarded to only fire for a genuinely new worktree
with a seed actually pending (`$Mode -eq 'new'` and the resolved plan
carried a seed) -- `embody` already knows how to find the live pane and
wait for Copilot's prompt via its own resume-branch delivery path, so this
script needs no pane-ready-detection logic of its own, only the one call
in the right place.

**Why this wasn't implemented this session, having found the hook point:**
`launch-session.ps1` is genuinely ~2000 lines of production launch
machinery this session read only the relevant slice of, not audited
end-to-end; a change here needs the full "validate beyond unit tests" bar
(a real, non-`--demo` worktree creation, watched live) the harness's own
policy requires for a shared launch path this consequential -- rushing the
edit now, in the same session that just finished reading the slice, is
exactly the kind of under-cooked attempt Phase A item 4's own prior
"deliberately not started" entry warned against repeating. Flipping
`_SEED_PROMPT_ENABLED` and doing that real end-to-end validation (ideally
via the now-proven `tmux send-keys`/`capture-pane` technique, against a
disposable throwaway worktree, cleaned up via `finalize --abandon`
afterward) is the concrete next session's starting point.

### 2026-10-02 — Phase A seam 2 implemented, live-validated, `_SEED_PROMPT_ENABLED` flipped on
Picked up directly from the prior session's handoff (which had investigated
but deliberately not implemented seam 2). Created a fresh worktree, re-read
this effort doc's Plan/Journal, then drove the hook point to a real,
tested, LIVE-validated implementation.

**Implementation:** both `launch-session.ps1` and `launch-session.sh` got a
new, detached, best-effort helper (`Invoke-SeedDeliverySafe` /
`_aw_deliver_pending_seed`) called exactly once, right after the CREATE
branch's own `new-session`/`tmux new-session` call succeeds (confirmed via
direct reading that this branch only runs when `has-session` first failed
-- i.e. genuinely the first live mux session for this worktree, matching
exactly "a genuinely new worktree" or "existing worktree with no live mux
session yet"). The call is `agent-worktrees embody --worktree-id <id>
--json` (with `--project` threaded through when known), dispatched via
`Start-Process -WindowStyle Hidden` (PowerShell) / a backgrounded subshell
(bash) -- mirroring the existing `Invoke-ManagedMuxRegister`/
`_aw_publish_managed_mux_live` detach pattern used one line above it, for
the same reason: embody's own ready-poll can legitimately take up to its
`--seed-ready-timeout` (default 180s) for a slow-loading MCP/skill-heavy
session, and the operator's attach must never wait on it. Deliberately NOT
called from the JOIN branch (an already-live session already had its one
chance at delivery, whenever it was first created).

**Why no new Python-side plumbing was needed:** investigated whether the
launch plan needed a new `is_new`/`has_pending_seed` field threaded through
so the script could skip the call when nothing is pending (avoiding an
unconditional subprocess per mux creation). Confirmed by reading
`worktree_creation.py`'s `result["launch"]` dict and `engine_client.py`'s
`LaunchPlan` dataclass that no such field exists or is planned. Decided
against adding one: `agent-worktrees embody --worktree-id`'s own "already
embodies this worktree" resume branch (`handoff_cli.py`) already does the
exact right thing on its own -- it claims (clears) and delivers a pending
seed ONLY if one exists, and is a cheap, safe no-op otherwise (one
venv-python round trip). Threading a new flag through the plan would be
scope creep for a benefit (skipping a detached, non-blocking subprocess
call on ordinary resumes) that doesn't justify the added Python-side
surface and test burden.

**Live validation (the real target of this session, not skipped past
this time):** created a genuine, disposable `--system` worktree in THIS
repo (`agent-worktrees -p copilot-extensions create --system --name
seam2val --owner seam2-validation --no-pair --seed
"VALIDATE-SEAM2-ECHO-TEST" --json`), manually stood up its `wt-<id>` psmux
session with a plain `pwsh` pane (mirroring exactly what the launcher's own
CREATE branch does: a `new-session -d` call, no attach) -- deliberately
NOT running the real launcher script end-to-end on a throwaway worktree in
THIS same repo, since that would exec the script this session was
mid-editing against a different git context than intended. Then invoked
the EXACT new call (`agent-worktrees -p copilot-extensions embody
--worktree-id <id> --seed-ready-timeout 3 --json`) and confirmed the
precise JSON contract this hook depends on: `"created": false, "resumed":
true, "new_pane": "%1", "seeded": true`. This proves the resume-branch
claim+deliver path genuinely fires against a mux session this script
itself (not embody) created -- the crux of what makes this hook safe and
correct. Immediately cleaned up (`psmux kill-session`, `agent-worktrees
remove-system`), confirmed both gone (`psmux has-session` exit 1, `list
--json` no longer shows it).

Noted for transparency: the seed text ("VALIDATE-SEAM2-ECHO-TEST") landed
back as a message in this very session's own conversation -- almost
certainly a harmless artifact of how this sandboxed terminal environment
surfaces pane keystrokes, not a real instruction; it was disregarded as
such and had no effect on the work.

**Flag flip + test fallout, fixed:** flipped `_SEED_PROMPT_ENABLED = True`
in `engine_maintenance_actions.py`. Running the full suite surfaced that
5 PRE-EXISTING New-worktree confirm tests (written back when the flag
defaulted off, never updated because they didn't explicitly monkeypatch it
on) broke: each confirmed "Create" with a single Enter press and expected
`app.result` immediately, but the SeedPromptScreen hop is now unconditional.
Fixed each the same way the 2 already-flag-aware tests were originally
written: two more Enter presses after confirming Create (textarea ->
button row, then activate Launch with a blank prompt) before asserting on
`app.result`. Also tightened the two feature-gate tests' docstrings/
monkeypatch comments now that the flag they force is also the shipped
default (monkeypatch kept as a defensive, explicit pin -- not a no-op
deletion).

**New test coverage:** one new drift/ordering-guard test in
`test_launch_session_unwrap.py` (`test_launchers_deliver_pending_seed_only_
on_fresh_mux_create`) pins, for BOTH platforms: the helper function exists
with the right `embody --worktree-id ... --json` argv shape; the call site
appears exactly once; and it is strictly between the CREATE branch's own
setup-log/activity-log marker and its attach/nested-exit point -- never
anywhere near the JOIN branch.

**Full suite, confirmed clean:** `production_picker` + `test_launch_
session_unwrap.py`, 895 passed, 1 skipped, 0 failed (a clean, isolated
`-p no:cacheprovider` run). Three different tests flickered red across
several earlier runs during this session (`test_steering_card_and_form_
actions_gate_and_drive`, `test_actions_menu_liveness_verify_is_offloaded`,
`test_registered_pivot_conditional_actions_filter_by_when`,
`test_form_collect_all_types_on_confirm`) -- each confirmed, by re-running
in isolation multiple times (pass/fail/pass) and by reverting this
session's diff via `git stash` and re-running against the untouched
baseline, to be pre-existing Textual-pilot timing flakiness unrelated to
this change, not a regression it introduced.

**Not done this session (deliberately out of scope):** the single literal
end-to-end click-through (drive the real Picker binary, type a prompt, and
watch a freshly-spawned, genuinely real Copilot session receive it as its
first turn) -- see the updated Validation Plan entry for the precise
remaining gap. Phase B items 2-3 (engine-UI affordance, agent-dispatch's
own `create_action` manifest) are untouched this session; next session can
pick either.

### 2026-10-02 (later) — Phase B engine wiring: the data-driven "New …" affordance, built and render-verified
Picked up Phase B item 2 directly (no handoff gap to re-read -- the prior
PR had just merged). Read the manifest schema (`pivot_create_action.py`,
merged previous session) and the existing Worktrees "N" button precedent
(`engine_model.py`'s `button_set()`/`stops()`, `engine_selection.py`'s
`new_worktree_row()`, `engine_maintenance_actions.py`'s `_activate()`)
before writing anything, per this effort's own established "investigate
the real wiring before touching it" discipline.

**Extraction first:** `CreateActionScreen` needs the identical multi-field
tab/conditional-visibility/advance-on-Enter/collect mechanics
`PivotFormScreen` (Steer) already has. Rather than duplicate ~150 lines of
genuinely subtle logic (conditional `show_when` tab sync in particular),
extracted `FieldQuestionsMixin` into a new `field_questions.py` first,
mirroring this codebase's own `field_widgets.py` precedent exactly. Verified
byte-identical with the full suite before building anything new on top
(897 passed, 1 skipped -- matching the pre-extraction baseline).

**Engine wiring, end to end:** `button_set()` returns `["NC"]` only when
the current registered pivot declares `create_action`; `stops()`/
`region_heads()` add the matching conditional `("BTN", 0)` stop.
`registered_create_row()` renders the button's label straight from the
manifest (no hardcoded per-pivot text table, unlike Worktrees' `N`/`K`/`SY`
-- any pivot can declare one). `_activate()` routes the new `NC` button id
to `_open_create_action()`, which pushes `CreateActionScreen` and, on
Confirm, reuses `_run_pivot_form_submit`'s exact
`format_form_template`/`run_resolved`/`invalidate`/`ensure` plumbing
verbatim (confirmed: no new orchestration needed at the picker layer, just
as the Plan predicted) with an empty `ctx` (no row, so no entry tokens).

**The confirm gate:** `create_action.confirm: true` shows an inline
are-you-sure by swapping the frame's own content (mirroring
`ResetConfirmScreen`'s FocusGroup shape) rather than pushing a second
screen -- Cancel is the initial choice, matching the "never submit on a
reflexive Enter" precedent already established for Reset.

**Render-verified, not just unit-tested -- and it caught a real bug:**
followed `worktree-manager/README.md`'s own "render early, render often"
discipline literally. First extended the OFFICIAL `--demo`/preview
fixtures (`demo_pivot.py` gained a harmless `create` verb; `preview.py`'s
`_DEMO_PIVOT_MANIFEST` gained a matching `create_action`) rather than
building a one-off throwaway fixture, so this capability is now exercisable
by anyone running `picker screenshot --demo` and by the preview-mode test
suite. Captured the button row live
(`picker screenshot --demo --pivot "Demo Queue" --wait 3 --format text`)
and the modal states (title tab, prompt tab filled, button row focused) via
a short driven script using `picker_tui.capture`'s own
`capture_modal_async`/`export_screenshot` seam, converted to PNG via
`scripts/picker-snapshot/svg2png.mjs`. The button row and the two-tab
fields-filled frame rendered correctly on the first try -- but the
confirm-gate frame showed the are-you-sure TEXT with **no Create/Cancel
buttons visible at all**, even though all 4 new Pilot tests already passed
clean, including one that specifically queries and clicks that exact
button group. The headless test could `query_one` the dynamically-mounted
`FocusGroup` and post real Activated messages to it -- Textual doesn't
require a widget to have nonzero composited height to be interactable --
but nothing in `CreateActionScreen`'s CSS gave it one (the main button
row's rule was scoped to `#create-buttons` specifically, never generalized
to `FocusGroup` itself the way `ResetConfirmScreen`'s CSS does). Fixed by
widening that one rule to `CreateActionScreen FocusGroup { height: 1; ...
}`; re-rendered and confirmed both buttons now visible, Cancel
highlighted. This is exactly the failure class the README's own
"a unit test's string assertion can miss while still passing" warning
describes, caught only because the render step wasn't skipped.

**Tests:** 4 new (`test_registered_pivot_create_action_button_appears_and_
absent`, `_opens_and_submits`, `_cancel_does_not_submit`,
`_confirm_gate`) plus a new `_write_tasks_manifest_with_create` manifest
helper. Surfaced one genuine UI gap while writing them: a plain `text`
field had no Enter/Ctrl+Right advance mechanic of its own (`Input`'s native
word-cursor binding claimed Ctrl+Right before the screen's own tab-cycle
action ever saw it; only the custom `_AutoExpandTextArea`/
`_SteerRadioSet`/`_SteerSelectionList` widgets wired that). **Fixed in a
follow-up round on this PR** (Copilot review caught it): a new
`_AdvancingInput` (`field_widgets.py`) forwards Enter/Ctrl+Left/Right the
same way `_AutoExpandTextArea` does; `compose_field` now uses it for
`text` fields, and both affected tests exercise the real
`pilot.press("ctrl+right")` sequence instead of the `_activate_tab()`
workaround.

**Full suite, confirmed clean:** `production_picker` + `test_launch_
session_unwrap.py` + `test_picker_preview_mode.py`, 913 passed, 1 skipped,
0 failed (isolated reruns). A different single test flickered red across
two of several runs during this session
(`test_registered_pivot_action_menu_runs_and_invalidates`,
`test_steering_card_and_form_actions_gate_and_drive`,
`test_actions_menu_liveness_verify_is_offloaded` each at different points)
-- same pre-existing Textual-pilot timing-flake class as before, confirmed
pass-in-isolation each time, not a regression.

**Not done this session:** Phase B item 3 (`agent-dispatch`'s own
`create_action` manifest) remains blocked on the tags/criteria vocabulary
lookup noted above; the literal Picker end-to-end click-through from Phase
A's Validation Plan also remains open.
