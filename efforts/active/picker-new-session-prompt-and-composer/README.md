# Picker New-Session Prompt + Registered-Pivot Composer

- **Slug:** `picker-new-session-prompt-and-composer`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase model (mirrors `agent-dispatch-tasks-pane-ux-overhaul`'s own convention)
- **Created:** 2026-09-30
- **Status:** Draft
- **Vision:** `picker` (no existing §Features entry yet -- candidate follow-up
  once Phase A lands)

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
- [ ] Extract `PivotFormScreen`'s field-type-to-widget rendering
      (`steering_form.py:154-210`) into a shared, submit-semantics-agnostic
      helper (mirroring this file's own "mechanical extraction" precedent);
      re-wire `PivotFormScreen` to call it, confirming byte-identical
      behavior (existing steer tests must still pass unchanged).
- [ ] Build a new, lean creation-prompt screen (its own class -- NOT a reuse
      of `PivotFormScreen` itself, whose Confirm/Save/Reset button row and
      draft-persistence semantics don't fit a "collect one optional prompt
      before launch" flow) using the extracted helper for the one `textarea`
      field it needs.
- [ ] Chain it into `_open_optmenu()`'s flow: after `ScopeDlgScreen`
      confirms (and only when "Bare" is NOT selected -- a bare worktree gets
      no Copilot bootstrap at all, so a seed prompt has nothing to attach
      to), open the new prompt screen; an empty/skipped prompt behaves
      exactly as today (no change to existing behavior when unused).
- [ ] Thread the collected prompt through `_confirm_new_worktree()`'s
      decision dict -> `picker_app.LaunchRequest` -> `_run_relocated_mux_launch()`'s
      `args` list -> `launch-session.{ps1,sh}` -- a NEW optional argument
      (name TBD at implementation time, e.g. `--seed-prompt`) that the
      script forwards to whatever it ultimately execs (confirm at
      implementation time whether that's a raw `copilot` invocation the
      passthrough `--` mechanism can carry it through unchanged, or whether
      the script needs its own explicit new flag wired to an equivalent of
      `agent-worktrees copilot`'s own `--seed`/`--seed-ready-timeout`
      mechanism).
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
