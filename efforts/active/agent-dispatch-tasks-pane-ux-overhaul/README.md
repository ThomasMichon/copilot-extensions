# Agent-Dispatch Tasks Pane UX Overhaul

- **Slug:** `agent-dispatch-tasks-pane-ux-overhaul`
- **Repo:** copilot-extensions
- **Branch(es):** `worktree/tmichon-cloud1-win-20260917-003059-332c` (design +
  preview tooling); implementation phases land on their own per-phase
  worktrees once the design below is approved.
- **Created:** 2026-09-17
- **Status:** Active — rubber-duck design review (2026-09-17) surfaced 3
  blockers around backend ownership/state contracts; resolutions agreed with
  the operator and the Plan re-sequenced below. Backend-contract phases
  (new Phase 1) must land before any UI-facing lifecycle-control phase.
- **Vision:** [`visions/plugins/agent-dispatch/tasks-pane-ux`](../../../visions/plugins/agent-dispatch/tasks-pane-ux/README.md)
- **Umbrella issue:** _TBD — file once the preview below is approved._
- **Sub-issues:** _TBD, one per Plan phase._

## Guiding Intent

The Worktrees pane in the Worktree Manager is a disciplined table view:
declarative columns, a state-derived colour palette shared with the status
bar, compact status markers, and a rich per-row action menu. The **Tasks**
pivot (agent-dispatch's registered pivot, rendered by `TasksView` in
`engine.py`) has none of that discipline today: no phase-based colour
grouping, no visible link to an assigned worktree's live status, no surfaced
artifacts (PRs/issues), no repo-scoped column, and a thin action menu.

This effort brings the Tasks pane to parity with Worktrees' presentation
quality, and layers on task-specific controls: steering blocked tasks,
forcing an abandon/reset, inspecting a task's charter, drilling into an
embodied task's worktree (a new Worktree Status card), and stopping/pausing
the agent embodying it. It also adds a Configuration → Registrars
viewer/editor so the operator can see every registrar/source of tasks and
flip the existing user-level enable/disable override — the master pause —
without being able to add or remove registrations (that stays an
agent-chat-driven flow).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| tmichon-cloud1 (this session) | Design, grounding against real code, preview-rendering tooling, effort/vision authoring, Phase 1 implementation | `copilot-extensions.worktrees/tmichon-cloud1-win-20260917-003059-332c` |

## Coordination

- **Topology:** independent per-phase PRs once the design is approved (each
  Plan phase below is a self-contained, reviewable slice). **Phases 0-3
  landed as one combined PR (#2913)** rather than split retroactively —
  see the Journal's "single PR" decision entry; going forward, **land each
  remaining phase's implementation PR before starting the next phase** —
  do not begin the next phase's implementation while the current phase's
  PR is unopened or unlanded, unless a session explicitly journals why and
  for how long that is deferred (see `ThomasMichon/copilot-extensions#2908`,
  filed to get upstream `efforts` guidance enforcing exactly this gate).
- **Host (owns PRs):** whichever worktree lands each phase.
- **Delegates:** none yet.
- **Handoff:** the preview tooling and captured screenshots in this worktree
  are the artifact an operator reviews before any implementation PR opens.
  Implementation work follows a **manual sequenced-session handoff loop**
  (below): a session drives the effort until its context fills, then saves
  a handoff prompt via `save_handoff_prompt` for the operator to manually
  paste into a fresh session, which reads the Runbook, picks up exactly
  where the prior session left off, and repeats.

## Runbook — for whichever session is currently driving this effort

**Read this section FIRST in any new session picking up this effort.** It is
kept up to date at the end of every session (or handoff point) so a fresh
session never has to re-derive "what's already done" from the Journal alone.

- **Worktree:** `C:\Data\Src\copilot-extensions.worktrees\tmichon-cloud1-win-20260917-003059-332c`
  is the effort's established driving worktree. **Phases 0-3 are merged to
  `main`** (PR #2913, squash-merged 2026-09-19), so a fresh session should
  pick explicitly rather than default: (a) resume in that worktree,
  rebasing it onto current `main` before continuing Phase 4, or (b) create
  a fresh worktree off current `main` when only landing an isolated,
  already-committed doc/fix — never both, and never treat "resume Phase 4
  in the established worktree" and "spin up a new worktree" as
  interchangeable. Whichever worktree is driving Phase 4 carries several
  unrelated stash entries that pre-date this effort — never run
  `git stash pop`/`git stash apply` without an explicit `stash@{N}` naming
  the entry you intend, and never clear the stash list.
- **Current phase:** **Phase 3 is now COMPLETE and MERGED to `main`** (the
  manifest + column fields, the "+N more" column-drop indicator, and all
  16 PR-review findings from #2913 -- see below). Phases 4-9 remain (see
  the Plan section).
- **Build/test commands** (agent-dispatch package):
  ```powershell
  cd plugins\agent-dispatch
  uv venv .venv                                            # one-time
  uv pip install --python .venv\Scripts\python.exe -e ".[dev]"  # one-time
  .venv\Scripts\python.exe -m pytest tests\test_queue.py -q      # fast, targeted
  .venv\Scripts\python.exe -m pytest tests -q                    # full suite, ~15-20 min
  ```
  The full suite has 2 known, PRE-EXISTING, unrelated failures on this
  Windows machine (`test_bootstrap_check_reconcile_opt_in.py::test_sh_*` —
  a Windows-path-into-bash mangling issue in an unrelated bootstrap-check
  script test, not touched by this effort). 2879 passed / 2 failed / 8
  skipped is the expected baseline; anything else is a real regression.
  Two other tests were each independently seen to fail exactly once under
  full-suite load and pass consistently otherwise, in isolation and as
  part of their own file: `test_supervisor.py::test_requeued_task_is_not_
  double_spawned` and `test_managed_companion.py::test_real_managed_
  companion_readiness_rollback_and_stop` (a real-subprocess-startup-timeout
  test, inherently load-sensitive). Logged here as known occasional flakes,
  not real regressions, in case either recurs.
- **What's done (Phase 1, item 1 — the pause hold):**
  `plugins/agent-dispatch/src/agent_dispatch/queue.py` — additive
  `hold_reason`/`hold_actor`/`hold_at` columns, `TaskQueue.set_hold`/
  `clear_hold`, `_transition(..., reject_if_held=True)` wired into `resume`
  and `release_suspended`, and a `hold_reason IS NULL` gate in `claim_one`.
  5 new tests in `tests/test_queue.py` (search `test_set_hold`). Wake-
  delivery/supervisor-spawn hold gating remains unimplemented (a narrower,
  separate follow-on from item 4's fencing, which is about a stale UI row,
  not the hold gate itself) — not currently blocking any Plan item.
- **What's done (Phase 1, item 2 — auto-suspend on dead liveness),
  committed:**
  `plugins/agent-dispatch/src/agent_dispatch/queue_liveness.py` —
  `TaskQueue.reconcile_liveness` now classifies each held task by looking
  up its **active headless spawn reservation** (`_active_headless_handle`,
  mirroring `queue.py`'s existing `_has_headless_reservation` query): a
  `local-body:`/`fleet-body:`-prefixed handle means a headless body, probed
  directly via new `headless_local_verdict`/`headless_fleet_verdict`
  callables (defaulting to `spawn_factories._default_local_body_verdict`/
  `_default_fleet_verdict`, i.e. `embody.local_body_verdict`/
  `fleet_body_verdict` — the same direct-by-session-id probes the
  supervisor's own recovery already uses); **no** matching reservation means
  a CLI-embodied (or not-yet-identifiable) owner, still probed via the
  worktree-keyed `resolver` (`tracking.liveness_verdict`) exactly as before.
  On a confirmed-`gone` verdict: a headless body keeps the pre-existing
  requeue/dead-letter behavior UNCHANGED; a CLI-embodied `started` task is
  instead auto-transitioned to `suspended` (a `claimed`-but-not-`started`
  CLI-embodied task keeps the old requeue/dead-letter behavior too, since
  `task_state_machine.py`'s `suspend` transition is only defined from
  `started`). The suspend write clears `lease_expires_at`/`last_liveness`
  and keeps owner/owner-session identity intact (mirrors `TaskQueue
  .suspend()`) so a later `resume(..., adopt_owner_session_id=...)` — which
  item 3 now actually does — can rebind it. New `counts["suspended"]` key.
  Tests: two new ones in `test_gc.py`
  (`test_reconcile_gone_cli_embodied_task_is_suspended_not_requeued`,
  `test_reconcile_gone_headless_task_still_requeues`) plus updates to 4
  pre-existing `test_gc.py`/`test_supervisor.py` tests that used a bare
  (non-headless-tagged) claim+start or `_ok_spawn()` handle to simulate the
  generic/CLI path — those now either assert the new `suspended` outcome or
  were switched to a `local-body:`-prefixed handle to keep exercising the
  unchanged headless path (see each test's own comment for which).
- **What's done (Phase 1, item 3 — the interactive embodiment
  transaction), committed:** new module
  `plugins/agent-dispatch/src/agent_dispatch/interactive_embody.py` —
  `launch_interactive_embodiment(client, task_id, *, machine, ...)`. Flow:
  (1) `get`/approve-if-`proposed`; reject anything other than
  `queued`/`suspended`; (2) `reserve_spawn` + `embody
  .prepare_reusable_worktree` (the SAME resolve-or-create logic
  `Supervisor._prepare_spawn_task` uses) to create-or-reuse the worktree;
  (3) a **queued** task is `claim`ed (worktree identity, no session yet)
  BEFORE the launch — closing the real "a pool worker claims it first"
  race — while a **suspended** task (never claimable by anyone else) skips
  this and binds AFTER the launch instead, since it needs the new session
  id anyway; (4) compose the new
  `embody_prompts.interactive_worker_prompt` seed (deliberately lighter
  than `autopilot_worker_prompt`: no worker identity, no pool/recipe
  framing, non-railroaded — the agent may stop and ask the operator
  anytime, and must leave the task's state honest — complete/suspend/
  abandon — whenever the session ends, never keep working unattended); (5)
  launch via `embody.spawn_embodied_worker` (extended with a new optional
  `seed` override param — every existing call site unaffected) — the SAME
  CLI-backed `agent-worktrees embody` mechanism, no new launch primitive;
  (6) `record_spawn` the new session handle, then finalize ownership:
  `start(..., owner_session_id=<new session>)` for the queued path, or
  `resume(..., adopt_owner_session_id=<new session>, expected_generation=,
  expected_owner_session_id=)` for the suspended path (atomically rebinding
  off the stale dead session in one step). **Required a small, additive
  backend extension**: `ResumeBody`/`DispatchClient.resume()` gained a new
  `adopt_owner_session_id` field alongside the existing `adopt_session`
  (which resolves the *caller's own* current session — wrong direction for
  a transaction that resumes a task into a session it just launched but
  isn't itself running inside of); mutually exclusive with `adopt_session`,
  additive and backward-compatible.
  Failure/recovery (see the module's own docstring for the full story):
  worktree-prep failure, a lost pre-launch claim race, and any launch
  failure all release the spawn reservation (`fail_spawn`); a launch
  failure on the **queued** path additionally undoes the pre-launch claim
  (`yield_task`, `release_spawn=False`) so the task returns to `queued`
  rather than being stranded `claimed` with no session (a state item 2's
  liveness GC cannot recover — it never escalates an uncaptured
  `owner_session_id` to `gone`). The ONE edge NOT self-healed: launch
  succeeds but the follow-up start/resume bind itself fails (a resume
  generation race, or a hold discovered only at bind time) — left as a
  live, unbound session; documented as future work, not a correctness
  hazard (nothing else can claim ownership of a `suspended`/`claimed` task
  out from under it). New tests: `tests/test_interactive_embody.py` (11
  tests: fresh/proposed/suspended happy paths, ineligible-status rejection,
  claim-race, launch-failure, worktree-prep-failure, and a resume
  generation-race — all against a real `TaskQueue` with `embody`'s
  subprocess-shelling functions mocked, no real `agent-worktrees`/Copilot
  process involved).
- **What's done (Phase 1, item 4 — universal mutating-action fencing),
  committed — Phase 1 is now COMPLETE:** added a new
  `_check_expected_status(task, expected_status)` helper
  (`queue.py`) raising a uniform **"task changed; refresh and retry"**
  outcome, wired into: (a) `_transition` itself (so `abandon` and every
  other `_transition`-backed verb gets it for free, checked atomically
  inside the same transaction as the write — distinct from and
  complementary to `_transition`'s pre-existing `expected_generation`/
  `expected_owner_session_id` identity fencing, which is handoff-specific
  and does NOT bump on ordinary transitions like `suspend`/`abandon`/
  `complete`, so it alone cannot detect "the status changed since I read
  it"); (b) `abandon()` — now exposes `expected_status`,
  `expected_generation`, `expected_owner_session_id` (all pass through to
  `_transition` verbatim); (c) `set_hold`/`clear_hold` (the pause/unpause
  primitives) — `expected_status`, with `clear_hold`'s already-unheld
  idempotent no-op checked BEFORE the fence (unpausing an already-unpaused
  task satisfies the operator's intent regardless of status drift); (d)
  `submit_steer` — `expected_status`. Also threaded end-to-end through the
  coordinator (`SteerBody`/`AbandonBody` gained the new fields;
  `/tasks/{id}/steer`/`/abandon` routes pass them through) and
  `DispatchClient.steer`/`.abandon()`. `force-stop`/`reset` are Phase 2 CLI
  verbs that don't exist as queue primitives yet — nothing to fence today;
  Phase 2 should reuse `_transition`'s now-verified pattern when it lands
  them, not invent a new one. 7 new tests in `tests/test_queue.py` (search
  "item 4"). Full suite re-verified: 2851 passed / 2 pre-existing unrelated
  failed / 8 skipped — see the flake note above for the one test that
  failed once under full-suite load and passed on every other run.
- **What's next: Phase 2** (the CLI verbs — `agent-dispatch embody
  --interactive` wiring item 3's transaction to a real CLI surface,
  `pause`/`unpause` wiring `set_hold`/`clear_hold`, `force-stop`, `reset`),
  each with its own unit tests against `task_state_machine.py`'s transition
  table + Phase 1's fencing (now fully landed). See the Plan section below
  for the full Phase 2 checklist.
- **What's done — Phase 2 is now COMPLETE, committed:**
  - `agent-dispatch pause <task> --reason ... [--actor ...]` /
    `unpause <task>`: thin CLI wrappers over `client.set_hold`/`clear_hold`
    (new `DispatchClient` methods + `/tasks/{id}/hold`/`/unhold` coordinator
    routes + `HoldBody`/`UnholdBody`). `--actor` defaults to the resolved
    worktree identity, else the literal `"operator"`.
  - `agent-dispatch embody <task> --interactive [--machine] [--project]`:
    resolves this machine (via `agent-worktrees`, or `--machine`) and calls
    item 3's `launch_interactive_embodiment` directly (client-side
    orchestration, like `Supervisor` — not a coordinator HTTP route, since
    the actual `agent-worktrees`/Copilot subprocess launch has to run on
    the machine invoking the CLI). `--interactive` is currently required
    (the only implemented mode; an unattended-autopilot `embody` already
    exists via the supervisor's own spawn path under a different name).
  - `agent-dispatch force-stop <task> [--machine] [--actor]`: new module
    `plugins/agent-dispatch/src/agent_dispatch/force_stop.py`
    (`force_stop`/`ForceStopError`) — only eligible from `started` (not
    `claimed`: a claimed-not-started task never had a live session to stop;
    use `yield`/`abandon`). Best-effort session termination: a **local**
    session (same machine) via new `bridge.force_end_session` (`agent-bridge
    end <id> --force`, unconditional — distinct from the existing
    `end_worker`'s idle-gated `--if-idle`); a **fleet** one (task's
    `owner`'s machine differs from the local one) via the EXISTING
    `embody.stop_fleet_body` over the SSH mesh, no new mechanism. The state
    transition (`suspend`, now fenced by the exact status/generation/
    owner-session read at the top of the call) proceeds regardless of
    whether termination could be confirmed — the operator's actual intent
    (stop treating this task as running) is honored either way. Threaded
    `expected_status`/`expected_generation`/`expected_owner_session_id`
    through `suspend()` itself (previously had none) plus its coordinator
    route/client method, since force-stop's own fencing depends on it.
  - `agent-dispatch reset <task> [--reason]` (only `--to proposed`
    implemented, matching the Plan's own scope): a genuinely NEW
    `task_state_machine.py` transition (`reset`, `{queued, claimed,
    started, suspended} -> proposed`) plus `TaskQueue.reset()` — discards
    the current attempt's embodiment state (owner, owner-session, lease,
    activity, card/steer-block state; `_transition`'s existing `to not in
    HELD` clears activity for free) while preserving the task's identity,
    prompt, and durable goal/done_criteria/progress_log (an operator wants
    a fresh attempt at the SAME task, not to delete and recreate it).
    Not owner-gated (an operator action, like `abandon`); refuses on a
    terminal or **held** task (mirrors `resume`/`release_suspended`'s
    `reject_if_held` — clear the hold first, a reset should never silently
    override an explicit pause). Full `expected_status`/`expected_generation`/
    `expected_owner_session_id` fencing via `_transition`, reused verbatim.
    Threaded through `ResetBody`/`/tasks/{id}/reset`/`DispatchClient.reset()`.
  - **Tests:** `tests/test_force_stop.py` (6, local/fleet/no-session/
    ineligible-status/termination-failure-still-suspends), 9 new
    `test_queue.py` tests for `reset` (goal/progress-log preservation,
    every legal from-state, terminal/held refusal, both fencing
    dimensions), 2 new `test_coordinator.py` tests each for hold/unhold and
    reset, and CLI-layer tests in `test_cli.py` for all four verbs
    (`pause`/`unpause`, `embody --interactive` success + error + missing-
    machine, `force-stop` success + error, `reset` success + unsupported
    `--to`).
  - **Validated:** full suite **2879 passed / 2 pre-existing unrelated
    failed / 8 skipped** — see the flake note above for the two
    load-sensitive tests seen to fail once each and pass on every other
    run/isolation.
- **What's done — Phase 3 (task schema + column contract), committed
  (mostly — one item deferred, see below):**
  - Landed the declarative `columns` on the REAL
    `plugins/agent-dispatch/pivots/agent-dispatch.json` (a pre-existing,
    simpler manifest predating this effort — replaced with Phase 0's
    proposed design, now that the actions it references are actually
    implemented: dropped every "PROPOSED — not yet implemented" disclaimer
    from `open-cli`/`pause`/`force-stop`/`reset-proposed`'s descriptions).
    Every column carries an explicit `priority` (lower = harder to drop):
    `title=1` (flex), `id=2`, `group/PHASE=3`, `target_worktree/WT=4`,
    `wt_live/LIVE=5`, `repo_name/REPO=6`, `turn_count/T=7`,
    `artifacts_summary/ARTIFACTS=8` (drops first — least valuable while
    it's still a placeholder, see below).
  - `board_cli.py`'s `_build()` now computes `wt_live` and
    `artifacts_summary` per row. **Design call (confirmed with the
    operator):** `wt_live` reuses the row's already-computed `activity`/
    `activity_updated_at` (a headless body's own `set_activity` self-report)
    rather than a fresh subprocess/bridge probe — this board client is
    stdlib-only and re-runs on every Picker refresh, so a per-row liveness
    probe was explicitly ruled out. Renders `"active"` / `"stalled Nm"`, or
    `None` (blank) when there's no headless signal -- **including for a
    CLI-embodied task**, which never calls `set_activity`. A blank `wt_live`
    cell is therefore "no headless liveness signal available here," not a
    confirmed "not live" — a caveat worth remembering when Phase 8 builds
    the Worktree Status card's own richer liveness view.
    `artifacts_summary` is landed as an explicit **placeholder** (always
    `None`) — Phase 3 owns only the column plumbing; Phase 5 owns the real
    claims/artifacts-tracking computation (per the Plan's own note that the
    two phases must not both claim this field).
  - **Landed (this session) — the deferred "+N more" column-drop
    indicator, Phase 3 is now COMPLETE:** `worktree-manager`'s shared
    `picker_tui/engine.py` (`_column_header`) gained an optional `dropped`
    param -- a compact right-aligned `+N` rendered at the end of the
    column-header row when `dropped > 0` and there's spare width (silently
    omitted otherwise, never wraps the header). The build-data call site
    computes `dropped = len(reg.columns) - len(cols)` from
    `_fitted_columns`'s already-fitted result and threads it through --
    infra shared by every registered pivot (Tasks, CodeSpaces, ...), not
    Tasks-specific. New tests in
    `worktree-manager/tests/production_picker/test_picker_tui.py`:
    `test_fitted_columns_drops_low_priority_when_narrow` (the column-fit
    algorithm itself, direct unit test against a `SimpleNamespace` `reg`)
    and `test_column_header_renders_dropped_count_indicator` (the `+N`
    indicator renders, fills the full row width, and is omitted rather
    than wrapped when there's no spare width) -- both resolve the render
    holder class by shape, matching `test_banner_line_helper_levels`'s
    existing pattern rather than hardcoding a class name.
  - **Tests:** `tests/test_board_cli.py` gained 2 new tests
    (`test_build_wt_live_reflects_headless_activity_only`,
    `test_build_artifacts_summary_is_a_phase3_placeholder`); updated
    `test_cli.py`'s existing manifest-badges test to read the new columns
    without breaking (the pre-existing `badges` declaration was left
    untouched — this phase never touched it, table columns are additive).
  - **Validated:** full suite **2880 passed / 2 pre-existing unrelated
    failed / 8 skipped** (plus the same `test_supervisor.py::
    test_requeued_task_is_not_double_spawned` occasional flake, confirmed
    passing in isolation again). `worktree-manager`'s
    `tests/production_picker` suite: **557 passed / 1 skipped** (baseline
    unchanged) after the `_column_header`/`_fitted_columns` change.
- **Handoff protocol:** when this session's context is filling and there's
  a natural stopping point, call `generate_handoff_prompt` (with a summary
  + next_steps) then `save_handoff_prompt`, update THIS Runbook section
  with the new current position before ending the turn, and commit. The
  operator manually pastes the returned handoff prompt into a fresh session
  to continue the loop — do not rely on an automatic cutover for this
  effort.

## Context

- **Tasks today (`engine.py::TasksView`, `tasks.py`):** the pivot is a
  `RegisteredPivotRuntime`-backed, declarative-columns render (`reg.columns`,
  `reg.group_field`, `reg.badge_fields`, `reg.subtitle_field`). Rows are plain
  dicts from the provider; `group_field` already sections rows by phase
  (`entry.group`, matching `agent-dispatch-board`'s Blocked/Proposed/Queued/
  Started/Suspended/Completed/Abandoned), and `_palette_style(col.palette,
  val)` already exists for per-value cell colouring — the mechanism existed,
  it was just never populated with a task-shaped palette.
- **Worktrees today (`engine.py::WorktreesView`, `derive.py`):** state is
  derived once per row into a canonical label (ACTIVE/FINAL/MERGED/etc,
  mirroring the PSMux/TMux status segment's `_SEGMENT_STYLE`), then coloured
  via `C_STATE`. This is the palette the new `task_phase` palette (added
  this session, see Plan Phase 1) reuses colour-for-colour without touching
  `C_STATE`/`_STATE_PALETTE` itself.
- **Task lifecycle (`agent_dispatch/task_state_machine.py`,
  `agent_dispatch/board_cli.py`):** the authoritative phase vocabulary is
  `agent-dispatch-board`'s `GROUPS` — Blocked, Proposed, Queued, Started,
  Suspended, Completed, Abandoned (`_group()` projects the raw `Status`
  enum + `awaiting_steer` onto these). The Tasks pivot manifest's
  `entry.group` already carries this; this effort's palette colours it.
- **Worktree cross-link:** `RegisteredPivot.worktree_field` (manifest key
  `entry.worktree`, defaulting to `target_worktree`) already exists and is
  already used for the account-scoped claim correlation
  (`_enrich_pivot_rows`). This effort's contribution is *displaying* that
  field as a real column, not inventing the join.
- **Read-only card views:** `kind:"card"` actions (`PivotCardScreen`,
  `pivots.py`'s `_classify`/`resolve_path`) already exist and were built for
  a different steering-adjacent use case, but are schema-generic
  (`title_from`/`status_from`/`link_from`/`body_from` dotted paths against
  the entry). This effort reuses them unmodified for the charter viewer and
  the new Worktree Status card — **no new modal class needed.**
- **Registrar configuration (`agent_dispatch/registrar.py`,
  `agent_dispatch/overrides.py`):** `ProfileDeclaration` already carries
  name/kind/description/owner/source_path/plugin_root/concurrency/
  max_attempts/verify_timeout/etc — everything the Configuration →
  Registrars view needs to show. `overrides.py`'s
  `set_override`/`clear_override`/`overridden_off_ids` is the existing
  user-level enable/disable store the new UI reads/writes — not a new
  persistence layer.

## Request

> Time to do an overhaul on the UX of the "Tasks" pane in the Worktree
> Manager for agent-dispatch. [...] identify the schema fields for tasks and
> how to represent them — id, status, title, live-ness, turns, etc.
>
> 1. Phase — reuse the Worktrees colour coding.
> 2. At-most-one worktree/agent assignment, cross-linked both ways (task
>    shows the worktree's 4-digit id; worktree status shows turn count,
>    liveness, dirty/final, etc).
> 3. Associated artifacts (PRs, bugs, etc.) — a drill-in graph of entity
>    types/ids, with 1–2 prominent ones surfaced inline.
> 4. Source repo — independently groupable/filterable from machine or status.
>
> Task menu: free-text steer for blocked tasks; force-abandon or
> reset-to-Proposed; view the task's charter/raw content via the markdown
> formatter; for embodied tasks, a Worktree Status sub-viewer (session
> lineage, expanded status descriptor, claims viewer) and agent controls
> (force-stop, and an explicit user-side "pause" that also blocks
> re-queue until unpaused).
>
> Configuration menu: a Dispatcher configuration viewer/editor — every
> registrar/source of tasks and pools, what it's for, its recipe/template,
> its source repo (or local config) origin, and schema metadata (schedule
> interval, max task count, max concurrency), with enable/disable per
> registration (the master pause) — never add/delete, which stays
> agent-chat-driven.
>
> *(Follow-up)* Prepare preview launches or image-renderings of the new UX
> proposals so I can provide feedback and iterate before checkin. Put the
> effort and vision buildout upstream in copilot-extensions.

## Plan

### Phase 0 — Design + review-ready previews (this worktree)
- [x] Ground the schema against the real code (`task_state_machine.py`,
      `board_cli.py`, `registrar.py`, `overrides.py`).
- [x] Add a `task_phase` palette to `engine.py` reusing `C_STATE` colours
      (Proposed/Queued → UNUSED grey, Started → ACTIVE blue, Blocked → WIP
      amber, Suspended → CONVO teal, Completed → FINAL green, Abandoned →
      GONE dark grey).
- [x] Build a hermetic preview tool
      (`worktree-manager/scripts/picker-snapshot/tasks-preview/`) that drives
      the REAL `PickerApp`/`TasksView`/`TaskMenuScreen`/`PivotCardScreen`
      against a fixed demo fleet and a **proposed** pivot manifest
      (`agent-dispatch.proposed.json`), and captures real screenshots:
      the redesigned Tasks table, the task action menu for every phase, the
      charter viewer, the Worktree Status card, and the existing steer modal
      reused with a task-shaped card. See `tasks-preview/README.md` for how
      to rerun it.
- [x] **Operator feedback round 1 (2026-09-17):** "Open into a CLI session"
      must not be offered for an embodied task with a LIVE headless agent
      (Blocked/Started — CLI and ACP can't co-drive one session), and
      ideally not for a Queued task already claimed by a pool. Fixed in the
      preview via a board-computed `cli_openable` gate (true only for
      Proposed / Queued-with-no-pool / Suspended); the manifest's `when`
      stays a flat `{"cli_openable": "true"}` equality check rather than
      encoding the branch-conditional rule declaratively. Also specified:
      the CLI session must launch with `--interactive` and a succinct
      prompt giving the agent the task's charter/phase, framed so the agent
      keeps driving the task toward its next phase while the *operator*
      decides when to wrap up — and when they do, the agent's job is to
      leave the task's recorded state honest (complete/abandon/reset-to-
      proposed/re-queue), never to keep working unattended after the
      session ends. Captured against Blocked/Started (excluded),
      Suspended/Proposed (included), and Queued+pooled (excluded) rows to
      demonstrate the full rule matrix.
- [x] **Operator feedback round 2 (2026-09-17):** the Tasks table line-wrapped
      (declared column widths summed wider than the render viewport), and
      separately, ANY full list rebuild reset scroll to the top — the second
      bug landing hardest on Tasks, since its background poll tick
      (`_maybe_repoll_pivot`) triggers a full rebuild independent of operator
      input, so scrolling down to a task near the bottom kept getting bumped
      back up. Both are **real `engine.py` fixes** (not preview-only),
      already committed in this worktree and covered by the full existing
      test suite (883 passed):
      - Added `Column.priority` (`pivots.py` + `plugin_contracts.py`) and a
        `TasksView._fitted_columns()` helper that runs the pivot's declared
        `columns` through the SAME `fit()` drop/shrink algorithm the
        Worktrees list already uses, so a declarative pivot's row can never
        exceed its render width. Wired into `_column_header`/`_column_row`/
        `build_data`. Verified at both a narrow (118-col, graceful
        column-drop) and a realistic (160-col, all columns shown) capture
        width.
      - `_PickerNativeData._rebuild()` now captures the OptionList's scroll
        offset before `clear_options()` and restores it after, instead of
        leaving every full rebuild at scroll-top. This is a general
        `OptionList`-backed list fix, not Tasks-specific — it benefits any
        pivot's full rebuild, though `_try_selection_repaint`'s existing
        O(delta) in-place path (Worktrees cursor moves only) still avoids
        even needing it for that one case.
- [ ] Operator review of the captured previews; iterate the manifest/palette
      before any implementation PR.
- [x] **Rubber-duck design review (2026-09-17)** — see the dedicated Journal
      entry below for the full review and the operator's resolutions. Net
      effect: the Plan below is re-sequenced so backend ownership/liveness/
      state contracts land BEFORE any phase that wires a lifecycle control
      (CLI-open, pause, force-stop, reset) to them. The display-only work
      (palette, columns, column-fit, charter/worktree-status cards, steer)
      is unaffected and can proceed independently.

### Phase 1 — Backend ownership/liveness/state contracts (NEW — blocking)
This phase produces no picker UI; it is the ground truth every later
lifecycle-control phase (7) reads from. Nothing in Phase 7 is implementable
without it.

- [ ] **Liveness-based `cli_openable`, not a `group`/status projection.**
      Define one backend-owned eligibility predicate over: raw task
      `Status`, whether an active spawn reservation/session exists, that
      session's actual liveness (heartbeat/lease), worktree availability,
      pool assignment, and any user hold (below). Return both a boolean and
      a machine-readable reason a UI can show. Per the operator: a Started
      (or Blocked) task IS eligible in principle if its live session (CLI
      or headless) has actually terminated — the correctness fix is not
      "special-case CLI-embodied Started tasks" but **the coordinator must
      never let that state persist**: any task whose active session/lease
      is found dead is auto-transitioned to Suspended (see next item) as
      part of normal liveness reconciliation, the same reconciliation that
      already exists for other stale-lease classes
      (`reconcile_reserving`/Phase 9 of `review-automation-reliability`).
      Once that invariant holds, `board_cli.py` computing `cli_openable`
      from the projected group (Proposed/Queued-no-pool/Suspended) is a
      correct, cheap approximation — not a shortcut around the real check.
- [x] **Auto-transition to Suspended on detected loss of liveness.** Extend
      whatever reconciliation loop already detects a dead
      reservation/session (`agent_dispatch/queue.py`) to cover a CLI-embodied
      session too (not just headless), transitioning the task to `SUSPENDED`
      the moment its active session is confirmed gone — never leaving
      Started/Blocked with no live owner. This is the invariant Phase 1's
      `cli_openable` approximation depends on. **Landed 2026-09-18** — see
      the detailed entry below (same item, checked there too).
- [x] **The "interactive embodiment transaction."** A new, coordinator-owned,
      atomic operation (not a picker navigation verb) that:
      1. Creates a new worktree if the task has none, else selects/resumes
         its existing worktree.
      2. Binds that worktree to the task and auto-claims the task to it
         (the SAME ownership/generation/CAS fencing a normal pool claim
         uses — ownership must be unambiguous either way, so a concurrent
         pool claim on the same task can never race this).
      3. Composes and returns the succinct `--interactive` seed prompt: the
         task's charter + current phase + its state-management tooling. The
         agent is told to set to work toward the task's next phase, but —
         being explicitly non-railroaded (no worker identity/pool rails) —
         may pause and ask the operator directly for instructions at any
         point; this is expected, not an escape hatch.
      Never resolves a pool or a named worker identity
      (`worker_identities.py`) at any point in this transaction — see the
      existing "never route CLI-open through the pool or a worker identity"
      requirement, now anchored to a concrete transaction rather than a bare
      picker verb. Define the failure/recovery story explicitly: worktree
      created but owner-bind fails; owner bound but the picker/launcher exits
      before the session actually starts.
      **Landed 2026-09-18** in `plugins/agent-dispatch/src/agent_dispatch/
      interactive_embody.py` (`launch_interactive_embodiment`) — see the
      Runbook above for the full design (bind-before-launch for a queued
      task's real claim race, bind-after-launch for a suspended task's
      atomic session-adopt, and the failure/recovery story per case). Needed
      one small additive backend extension: `ResumeBody`/`DispatchClient
      .resume()` gained an explicit `adopt_owner_session_id` field (the
      existing `adopt_session` boolean resolves the CALLER's own current
      session, the wrong direction for this transaction). 11 new tests in
      `tests/test_interactive_embody.py`; full suite re-verified with no
      regressions.
- [x] **A real, durable per-task user-pause hold — not `overrides.py` and
      not undifferentiated `Suspended`.** `overrides.py` disables a
      registrar/pool machine-wide, not one task; existing `Suspended` is a
      system-recoverable hold that normal resume/recovery paths already
      reopen. Add an explicit hold record (actor, reason, timestamp,
      expected generation/owner) that is a **gate every part of the flow
      must check and honor**: claim, resume, wake-delivery, liveness
      recovery/auto-transition (above), release, and supervisor spawning.
      Force-stop is a separate, narrower primitive: terminate the exact
      current reservation/session; it does not itself imply or require a
      hold. Render the hold's reason distinctly from system-Suspended and
      from Blocked/awaiting-steer in the UI (Phase 7) — three visually
      distinct situations, not one overloaded label.
      **Landed 2026-09-17** in `plugins/agent-dispatch/src/agent_dispatch/
      queue.py`: additive `hold_reason`/`hold_actor`/`hold_at` columns
      (`_COLUMNS` + `Task` dataclass — automatically included in
      `_TASK_DB_COLUMNS`/`_TASK_SELECT` since those derive from
      `dataclasses.fields(Task)`), `TaskQueue.set_hold`/`clear_hold`
      (transactional, idempotent, audited, refuses on a terminal task), a
      new `_transition(..., reject_if_held=True)` parameter wired into
      `resume` and `release_suspended`, and a `hold_reason IS NULL` gate
      added to `claim_one`'s eligibility query (both the targeted-`task_id`
      and general-scan branches). 5 new tests in `tests/test_queue.py`
      (`test_set_hold_*`); full existing suite re-verified with no
      regressions. **Not yet done:** wake-delivery and supervisor-spawn
      gating (`queue_liveness.py`/`supervisor.py`) still need their own
      hold check — see the Runbook below for exactly where.
- [x] **Auto-transition to Suspended on detected loss of liveness** —
      **Landed 2026-09-18** in `plugins/agent-dispatch/src/agent_dispatch/
      queue_liveness.py`: `LivenessMixin.reconcile_liveness` now classifies
      each held task by its active spawn reservation
      (`_active_headless_handle`, mirroring `queue.py`'s existing
      `_has_headless_reservation`): a `local-body:`/`fleet-body:`-prefixed
      handle is a headless body, probed directly by session id via new
      `headless_local_verdict`/`headless_fleet_verdict` callables
      (defaulting to `embody.local_body_verdict`/`fleet_body_verdict` —
      the supervisor's own existing direct body-liveness probes, no new
      vocabulary); no matching reservation means a CLI-embodied (or
      not-yet-identifiable) owner, still probed via the pre-existing
      worktree-keyed `tracking.liveness_verdict`. On confirmed-`gone`: a
      headless body keeps the pre-existing requeue/dead-letter behavior
      UNCHANGED; a CLI-embodied `started` task auto-transitions to
      `suspended` instead (a `claimed`-but-not-`started` CLI-embodied task
      keeps the old requeue/dead-letter behavior, since `suspend` is only a
      legal transition from `started`). The write clears
      `lease_expires_at`/`last_liveness`, keeps owner/owner-session identity
      (mirrors `TaskQueue.suspend()`) so a later `resume(...,
      adopt_owner_session_id=...)` — e.g. item 3's interactive-embodiment
      transaction — can rebind it, and preserves the existing
      generation/owner-session CAS fencing exactly. 2 new tests in
      `test_gc.py`; 4 pre-existing `test_gc.py`/`test_supervisor.py` tests
      updated (see the Runbook for which and why). Full suite re-verified:
      2835 passed / 2 pre-existing-unrelated failed / 8 skipped, no
      regressions.
- [x] Every mutating action (steer submit, abandon, reset, pause, force-stop,
      the interactive-embodiment transaction) must accept and check an
      expected status/generation/owner/owner-session — reject with a clear
      "task changed; refresh and retry" outcome on mismatch, since the
      Picker's action menu is built from a cached row that can be stale by
      the time the operator commits to an action. **Landed 2026-09-18** —
      see the Runbook above for the full design (a new
      `_check_expected_status` helper wired into `_transition` itself, plus
      `abandon`/`set_hold`/`clear_hold`/`submit_steer`, and threaded through
      the coordinator + `DispatchClient`). `reset`/`force-stop` don't exist
      as queue primitives yet (Phase 2 CLI verbs) — nothing to fence there
      today; item 3's interactive-embodiment transaction already fences its
      own `resume`/`start` calls (see its own module docstring). Phase 1 is
      now complete.

### Phase 2 — Coordinator APIs implementing Phase 1's contracts (COMPLETE, 2026-09-18)
- [x] `agent-dispatch embody --interactive` (or equivalent): runs the Phase 1
      interactive-embodiment transaction and prints/launches the seed.
- [x] `agent-dispatch pause <task> --reason ...` / `agent-dispatch unpause
      <task>`: sets/clears the Phase 1 user-pause hold.
- [x] `agent-dispatch force-stop <task>`: terminates the exact current
      reservation/session (fenced by generation/owner, per Phase 1's last
      item).
- [x] `agent-dispatch reset <task> --to proposed`: the gentler
      "not like this" — validated against `task_state_machine.py`'s
      transition table.
- [x] Unit tests for all four against the state machine + the Phase 1
      concurrency fencing (a stale caller must be rejected, not silently
      succeed against the wrong incarnation of the task).

  See the Runbook above for the full design/implementation summary of each
  verb, the files touched, and the exact test counts.

### Phase 3 — Task schema + column contract (display-only; UNBLOCKED)
- [x] The `task_phase` palette, `Column.priority`, and the column-fit wiring
      (`TasksView._fitted_columns`) are already landed in this worktree
      (generic `worktree-manager`/`agent-worktrees` engine code, usable by
      any registered pivot) — they ship with whichever PR lands this phase,
      not as a separate change.
- [x] Land the declarative `columns` on the real
      `plugins/agent-dispatch/pivots/agent-dispatch.json` manifest (ID,
      PHASE, REPO, TITLE, WT, T, LIVE, ARTIFACTS), sourced from
      `agent-dispatch-board`'s actual row shape — add `wt_live` /
      `artifacts_summary` fields to `board_cli.py`'s `_build()` (owned here
      only; Phase 5 below does NOT also claim `artifacts_summary` — see the
      rubber-duck finding about the two phases contradicting each other).
      **Landed 2026-09-18** — see the Runbook above for the confirmed
      `wt_live` data-source design (reuses `activity`/`activity_updated_at`,
      never a fresh subprocess probe) and `artifacts_summary`'s explicit
      placeholder status pending Phase 5.
- [x] Give every declared column an explicit `priority` rather than relying
      on the declaration-order default. **Landed 2026-09-18** alongside the
      manifest above.
- [x] Add a compact "+like `N more…`" affordance (or a minimal always-shown
      artifact-count glyph) when a column is dropped at a narrow width — an
      operator must be able to tell "no artifacts" from "artifacts column
      unavailable here," per the rubber-duck's column-drop finding.
      **Landed** — see the Runbook above (Phase 3 is now fully complete).

### Phase 4 — Worktree cross-link
- [ ] Confirm the WT column round-trips against a live coordinator (not just
      the preview's fixed fleet); no engine.py change expected beyond
      Phase 3.
- [ ] Decide and scope the vision's promised REVERSE projection (task
      identity/phase visible from the Worktrees side) as its own follow-on
      phase, or narrow the vision to one-way task→worktree — the rubber-duck
      review flagged this as promised-but-unowned; do not leave it silently
      unresolved.

### Bug (filed, unscheduled) — navigating into the Tasks pivot freezes the Picker UI
Reported by the operator 2026-09-18 (during PR #2913 review triage), not yet
investigated to root cause. Symptom: switching the machine-tab focus onto
Tasks blocks/freezes the whole Manager UX momentarily, rather than switching
immediately and showing a loading state — suggesting somewhere in the
Tasks-pivot render/switch path is doing blocking I/O on the UI thread instead
of going through the pivot's own background-thread runtime.

**What's already confirmed NOT the cause** (checked while triaging where to
file this, so a follow-up session doesn't re-tread the same ground):
- `RegisteredPivotRuntime.ensure`/`.repoll` (`tasks.py`) already spawn a
  background `threading.Thread` for the `list` subprocess and return
  immediately; `PickerScreen._task_state()` only reads whatever's already
  cached (`idle|loading|ready|error`) — this is the *intended* async path and
  looks correct on inspection.
- `_enrich_pivot_rows`/`_worktree_title_map` (`engine.py`) are pure in-memory
  dict lookups over already-loaded worktree records — no I/O.

**Where to look next:**
- `_pivot_machine_id`/`_machine_key_map` (`engine.py`) — reads
  `machines.yaml` via `data_ssh.machine_key_map()` and is cached, but the
  *first* call per session isn't; confirm it's cheap/local and not doing
  anything SSH-shaped synchronously.
- Whatever runs on the actual tab-switch keypress that lands on Tasks
  (`self.htab = ...` call sites) — confirm nothing there calls
  `pivots_mod.scan_pivot_registry()`/`ensure_pivots()` or any other
  filesystem/subprocess scan synchronously outside the already-async
  `on_mount` skeleton path.
- Whether `agent-dispatch-board`'s own subprocess (invoked inside the
  background thread) is itself slow enough on first run (cold coordinator
  discovery, `_endpoint()`'s file probes) that the *thread* takes a while —
  which wouldn't freeze the UI by itself (it's backgrounded) but would
  explain a prolonged "loading" state if something else *is* blocking
  waiting on it.
- Confirm the Tasks tab actually paints its `state == "loading"` row
  (`_status_row`) immediately on first switch, rather than the whole screen
  waiting on `_task_state()`'s first non-idle result before rendering
  anything at all.

Not scheduled into a numbered Plan phase yet — triage and fix (or fold into
whichever phase turns out to own the real cause) before this effort's
Validation Plan can claim a smooth pivot switch.

### Phase 5 — Artifacts (claims) surface
- [ ] Land `artifacts_summary` computation in `board_cli.py` (or wherever
      agent-dispatch tracks claims) and the drill-in claims viewer content
      for the Worktree Status card. (Not duplicated with Phase 3 — Phase 3
      only lands the column plumbing/manifest; the actual claims-tracking
      computation is owned here.)

### Phase 6 — Source-repo grouping/filtering
- [ ] The REPO column ships in Phase 3. A dedicated repo filter chip
      (parallel to the Worktrees machine filter) is real, more invasive
      `engine.py` work — scope and design it as its own phase once the
      table itself is validated.

### Phase 7 — Task menu: wire lifecycle controls to Phase 2's APIs
Everything here is UI wiring against the ALREADY-BUILT Phase 1/2 contracts —
no lifecycle-control logic invented at this layer.
- [ ] Land the `charter`/`worktree-status` `kind:"card"` actions (no backend
      dependency — read-only, can land any time after Phase 3).
- [ ] Wire "Open into a CLI session" to Phase 2's `embody --interactive`.
      `cli_openable` (Phase 1) gates visibility; the action itself performs
      no logic beyond invoking the coordinator transaction and launching the
      returned session. For Proposed/Queued (no existing worktree) the
      transaction creates one; for Suspended it resumes the existing one.
- [ ] Wire Pause/Unpause, Force-stop, and Reset-to-Proposed to Phase 2's
      corresponding verbs. Render the user-pause hold as its own distinct,
      visible, filterable phase/badge — never conflated with system-Suspended
      or Blocked/awaiting-steer (Phase 1's hold design).
- [ ] Force-abandon (already declared in the preview manifest) needs no new
      backend work beyond the Phase 1 concurrency-fencing requirement.

### Phase 8 — Worktree Status card (implementation)
- [ ] Populate `worktree_status.body` from real session-lineage/claims/
      commit data instead of the preview's fixed fixture.

### Phase 9 — Configuration → Registrars viewer/editor (implementation)
- [ ] Build the Configuration-menu view listing every registration
      (`ProfileDeclaration` fields) and wire enable/disable to
      `overrides.py`'s existing store. No add/delete affordance. Note: the
      existing `ConfigSection` contract only *runs an external command*; a
      real inline read/write viewer is new `engine.py` surface, not a
      manifest-only addition — scope that explicitly when this phase starts.

## Validation Plan

- [ ] Unit tests for the Phase 1 liveness-auto-transition-to-Suspended
      reconciliation path (a CLI-embodied session's death is detected and
      transitions the task, exactly like the existing headless-session
      case).
- [ ] Unit tests for the Phase 1 interactive-embodiment transaction's
      concurrency fencing: a pool claim and an interactive-embodiment
      transaction racing the same task must never both succeed.
- [ ] Unit tests for the Phase 1 user-pause hold: claim/resume/wake-delivery/
      recovery/release/supervisor-spawn all reject a held task; force-stop
      does not itself set or require a hold.
- [ ] Unit tests for the `task_phase` palette and the manifest's declarative
      columns (mirroring `tests/production_picker/test_pivots.py` patterns).
- [ ] Unit tests for `board_cli.py`'s new `artifacts_summary`/`wt_live`
      fields.
- [ ] Unit tests for `pause`/`unpause`/`force-stop`/`reset` CLI verbs against
      `task_state_machine.py`'s transition table AND the Phase 1 concurrency
      fencing (a stale caller must be rejected, not silently succeed).
- [ ] Unit tests for the Configuration → Registrars view against
      `overrides.py` (`set_override`/`clear_override` round-trip).
- [ ] A picker-TUI snapshot/golden test (per `tests/production_picker/
      goldens/`) covering the shipped Tasks pane layout.
- [ ] Manual pass against a live coordinator: steer a real blocked task,
      force-abandon a task, pause and unpause an embodied task (confirm
      agent-dispatch does not re-queue it while paused), open a Proposed
      task into a CLI session and confirm it never resolves a pool or
      worker identity, then kill that CLI session and confirm the task
      auto-transitions to Suspended rather than lingering as Started.

## Proposal

_Pending operator review of the Phase 0 previews._

## Journal

### 2026-09-17 — Kickoff + Phase 0 previews
- Effort + vision moved upstream from a private knowledge-repo draft into
  `copilot-extensions` per the operator's request that the buildout live
  here.
- Grounded the design against `TasksView`/`WorktreesView` in `engine.py`,
  `task_state_machine.py`'s `Status` enum, `board_cli.py`'s `GROUPS`/
  `_group()`, `registrar.py`'s `ProfileDeclaration`, and `overrides.py`'s
  existing enable/disable store.
- Added a `task_phase` palette to `engine.py` (additive; `C_STATE`/
  `_STATE_PALETTE` untouched).
- Built `worktree-manager/scripts/picker-snapshot/tasks-preview/` — a
  hermetic demo fleet + a proposed pivot manifest + a driver script — and
  captured six real screenshots of the redesigned Tasks pane, the task
  action menu, the charter viewer, the Worktree Status card, and the steer
  modal, all rendered through the actual Textual `PickerApp` (no mockup
  rendering engine). Discovered along the way: a pivot manifest filename
  matching a real installed plugin (`agent-dispatch.json`) is
  identity-checked against that plugin's own template and dropped if it
  diverges (`pivots.py`'s `_KNOWN_LEGACY_PIVOTS`) — the preview manifest
  uses a distinct filename (`agent-dispatch-preview.json`) to avoid that;
  and a `kind:"card"` action's `status_from`/`link_from` default to
  `card.status`/`card.link` when unset, so a card rooted at a different
  dotted path should declare them explicitly (fixed in the preview
  manifest) to avoid leaking an unrelated card's status/link.

### 2026-09-17 — Feedback round 1: "Open into a CLI session" gating
- Operator caught that the preview offered "Open into a CLI session" for an
  embodied task with a LIVE headless agent (Blocked/Started) — impossible in
  practice, since CLI and ACP cannot co-drive one session. Corrected the gate
  to a board-computed `cli_openable` field: true only for Proposed, a Queued
  task with no `pool` assigned, or Suspended; false for Blocked/Started
  (live embodied) and for any terminal phase. Re-captured the task menu for
  all five reachable phases (Blocked, Started, Suspended, Queued+pooled,
  Proposed) to show the corrected matrix.
- Also specified the intended `--interactive` launch behavior for this
  action: a succinct prompt seeds the agent with the task's charter and
  current phase, framed so the agent keeps driving the task toward its next
  phase while the **operator** decides when to wrap up — and when they do,
  the agent's job is to leave the task's recorded state honest
  (complete/abandon/reset-to-proposed/re-queue), not to keep working
  unattended after the session ends. Captured as a Phase 5 implementation
  requirement (`board_cli.py`'s `cli_openable` + the actual prompt
  composition); the preview's `open-cli` action description now states this
  intent directly.

### 2026-09-17 — Feedback round 2: line-wrap + scroll-reset (real engine fixes)
- Operator flagged that titles/cells line-wrapped in the Tasks table
  screenshot, and separately (broader complaint) that redrawing the table
  jumps scroll back to the top — particularly disruptive for Tasks, whose
  background poll can rebuild the list while the operator is reading a task
  near the bottom. Root-caused and fixed both as **real, generalizable
  `engine.py`/`pivots.py`/`plugin_contracts.py` changes** (not preview-only
  scaffolding), verified against the full existing worktree-manager test
  suite (883 passed, 1 skipped, no regressions):
  1. **Line-wrap:** `Column` gained an optional `priority` field (defaults to
     declaration order); `TasksView._fitted_columns()` runs a pivot's
     declared columns through the SAME `fit()` drop/shrink algorithm the
     Worktrees list already uses before rendering the header/rows, so a
     manifest whose columns sum wider than the render viewport now
     shrinks/drops columns (lowest-priority — e.g. ARTIFACTS — first)
     instead of silently wrapping. Verified at both a narrow 118-col capture
     (ARTIFACTS gracefully dropped) and a realistic 160-col capture (every
     column shown) — `tasks-list.png` / `tasks-list-wide.png`.
  2. **Scroll reset:** `_PickerNativeData._rebuild()` (the native
     `OptionList` wrapper backing both Worktrees and Tasks) now captures the
     widget's scroll offset before `clear_options()` wipes it and restores
     it after the rebuild completes, instead of leaving every full rebuild
     parked at the top. `_try_selection_repaint`'s existing O(delta)
     in-place repaint (Worktrees-only, cursor-move-only) still bypasses this
     entirely for that one case; this fix covers every OTHER rebuild path
     (pivot switch, data change, and — the one that motivated this fix —
     the Tasks pivot's background `list` poll tick).
- Both fixes are cross-cutting engine improvements (benefit any registered
  pivot's declarative-columns table and any full list rebuild), landed here
  because this effort's preview work is what surfaced them, not because
  they're Tasks-specific in implementation.

### 2026-09-17 — Feedback round 3: never route CLI-open through the pool/identity
- Operator specified an important constraint: opening a task into a CLI
  session must **never** use the pool or a dedicated worker identity
  (`worker_identities.py`'s named acting-theme/focus/rules bundle) for that
  task. Rationale — a pool/identity assignment applies rails to keep an
  *unattended* headless agent on target; a CLI session is the opposite
  case, operator-driven and deliberately open-ended, so those rails are
  actively unwanted there. Updated the preview's `open-cli` action
  description, `fake_board.py`'s module docstring, and Phase 5's Plan item
  to state this as a hard implementation constraint: the CLI-open path
  spins up a bare interactive session seeded only with the succinct context
  prompt (charter + phase + task-state tooling) — never "assign the pool's
  dedicated worker, then attach a terminal to it" — for every
  `cli_openable` phase, including a currently-unpooled Queued task (the CLI
  path never acquires a pool on its way in).

### 2026-09-17 — Rubber-duck design review: 3 blockers, resolved before Phase 1
Before starting any implementation PR, ran a full design review (via a
`rubber-duck` sub-agent, given the effort/vision docs, the proposed manifest,
the demo fixture, the real `engine.py`/`pivots.py`/`plugin_contracts.py` diff,
and read access to the actual `agent_dispatch` package) specifically to
stress-test the lifecycle-control design before committing to backend work.
**Verdict: no-go on the previously-numbered Phase 1 as sequenced** — the
display/table work is sound and already tested, but three lifecycle
affordances were specified as UI affordances ahead of the backend ownership/
state contracts they need to be safe. Full findings and the operator's
resolutions:

1. **[blocker] `cli_openable` was derived from the presentation `group`
   (Blocked/Proposed/etc.), not authoritative liveness.** The review noted
   the fixture's own "Blocked" task carries `status: suspended` with a live
   agent, and asked whether a Started task with a since-terminated CLI
   session should also be eligible.
   **Resolution (operator):** Agreed this is a sub-state analysis, and in
   principle a Started task IS eligible once its live session (CLI or
   headless) has actually died. Rather than special-casing that in
   `cli_openable`'s own logic, the coordinator must **never let that state
   persist**: extend the existing dead-lease reconciliation to also detect a
   dead CLI session and auto-transition the task to Suspended. Once that
   invariant holds, `board_cli.py` computing `cli_openable` from the
   projected group is a correct, cheap approximation of the real liveness
   check — not a shortcut around it. Captured as new Phase 1's first two
   items (the auto-transition invariant + the approximation it licenses).
2. **[blocker] The proposed CLI-open flow isn't implementable as a picker
   navigation verb, and bypassing the pool currently means bypassing the
   whole ownership/claim protocol too** (race with a pool claim, duplicate
   worktrees, orphaned lineage).
   **Resolution (operator):** Agreed. Confirmed the exact flow: (1) create a
   new worktree if none is assigned yet, else use the existing one; (2) bind
   the worktree to the task and auto-claim the task to it (the SAME
   ownership/generation fencing a pool claim uses, so a concurrent pool claim
   can never race it); (3) pass an `--interactive` prompt that informs the
   CLI agent of the task binding up front — the agent should set to work,
   but, being genuinely non-railroaded, may pause and ask the operator
   directly for instructions at any point (expected, not an escape hatch).
   Confirmed this needs a real "interactive embodiment transaction" in the
   coordinator, exactly as the review proposed. Captured as new Phase 1's
   third item, with Phase 2 building the CLI verb and Phase 7 only wiring
   the Picker action to it.
3. **[blocker] Pause can't reuse `overrides.py`** (registrar/pool-wide, not
   per-task) **or plain `Suspended`** (already a system-recoverable hold,
   and the fixture's own `paused_by_user` flag alongside `group: Suspended`
   is exactly the "Suspended means two different things" trap the review
   called out).
   **Resolution (operator):** Agreed — pause needs to be genuine system
   state. The UX provides the *control* surface for it, but every part of
   the flow (claim, resume, wake-delivery, liveness recovery, release,
   supervisor spawning) must check the hold and honor it, not just the
   Picker's own display. Captured as new Phase 1's fourth item (a durable
   per-task hold record, distinct from system-Suspended and from
   Blocked/awaiting-steer) and Phase 2's `pause`/`unpause` verbs.

The remaining "significant" findings (phase-sequencing contradictions,
stale-menu concurrency, the vision's unowned reverse task↔worktree
projection, silent column-dropping, and the `kind:"card"` default-inheritance
footgun) are folded directly into the re-sequenced Plan above (new Phases
1–9) rather than repeated here — see each phase's own bullets for where they
landed. No code changed as a result of this review; it is pure design/plan
revision. The already-landed generic engine work (palette, column-fit,
scroll-preserve) is unaffected and ships with Phase 3 as planned.

### 2026-09-17 — Phase 1 implementation begins: the pause hold lands
Operator: "let's dive in" — began real implementation against the
re-sequenced Plan, in this same worktree (all Phase 0/1 context/tooling
lives on this branch; splitting into a per-phase worktree happens once a
phase is ready to submit for review, per the Coordination section).

- Surveyed `agent_dispatch`'s `queue.py`/`queue_liveness.py`/`lease.py`/
  `task_state_machine.py`/`tracking.py`/`embody.py`/`overrides.py` in
  detail (a background `explore` sub-agent report) to ground Phase 1's
  design against the REAL transition/fencing/liveness machinery rather than
  guessing. Key findings folded into the Runbook above: `CoordinatorLease`
  is unrelated (coordinator election, not task/session liveness);
  `LivenessMixin.reconcile_liveness` already detects a dead owner session
  and requeues/dead-letters it, but has no `-> suspended` branch; the
  package already distinguishes CLI-backed ("live-sessions" registry) from
  headless ("sessions" registry) embodiment in `tracking.py`; `overrides.py`
  confirmed unsuitable for a per-task hold (machine-local, registrar-scoped,
  outside the SQLite task transaction).
- **Landed Phase 1, item 1 (the pause hold) for real** in
  `plugins/agent-dispatch/src/agent_dispatch/queue.py`: additive
  `hold_reason`/`hold_actor`/`hold_at` columns (via `_COLUMNS` + the `Task`
  dataclass — `_TASK_DB_COLUMNS`/`_TASK_SELECT` derive from
  `dataclasses.fields(Task)`, so no separate column-list sync was needed),
  `TaskQueue.set_hold`/`clear_hold` (transactional, idempotent, audited,
  refuses on a terminal task), a new `_transition(...,
  reject_if_held=True)` parameter wired into `resume` and
  `release_suspended`, and a `hold_reason IS NULL` gate added to
  `claim_one`'s eligibility query. 5 new tests added to `tests/test_queue.py`
  (`test_set_hold_blocks_resume_release_and_reclaim`,
  `test_set_hold_on_queued_task_prevents_claim`,
  `test_set_hold_requires_a_reason`, `test_set_hold_refuses_terminal_task`,
  `test_set_hold_is_idempotent_and_clear_hold_is_a_noop_when_unheld`).
- **Validated:** built the `agent-dispatch` package's own `.venv` (it hadn't
  been built in this worktree before) and ran its full test suite:
  **2834 passed, 2 failed, 8 skipped** in ~15 minutes. The 2 failures
  (`test_bootstrap_check_reconcile_opt_in.py::test_sh_skips_spawn_without_
  opt_in` / `test_sh_proceeds_with_opt_in`) are a **pre-existing, unrelated**
  Windows/bash path-mangling issue in a bootstrap-check script test (a
  Windows path gets its backslashes eaten when handed to `bash.EXE`) —
  confirmed unrelated to this change (different file, different subsystem,
  fails identically regardless of the queue.py edits). This is the
  established baseline for this worktree on this machine going forward.
- **Not done yet:** Phase 1 items 2–4 (auto-suspend-on-dead-liveness, the
  interactive embodiment transaction, and universal mutating-action
  fencing) — see the Runbook above for exact next steps, entry points, and
  design notes so the next session (or this one, resumed) does not have to
  redo this survey.
- Added the **Runbook** section (above, right after Coordination) as the
  durable "how to pick this up" artifact for the manual sequenced-session
  handoff loop the operator asked for: any fresh session reads it first,
  finds the exact current position, and continues without re-deriving
  context from the Journal alone. It will be updated at every handoff
  point going forward.

### 2026-09-18 — Phase 1, item 2: auto-suspend on dead liveness lands
Resumed from the prior session's handoff (`handoff-0f4b6106-...`), same
worktree, per the Runbook.

- Surveyed `queue_liveness.py`'s `reconcile_liveness`, `tracking.py`'s
  `liveness_verdict`/`resolve_live_session` (worktree-keyed, the bridge's
  interactive `live_sessions` registry only), `supervisor.py`'s
  `_has_headless_reservation` query pattern, and `spawn_factories.py`'s
  `_parse_local_body_handle`/`_parse_fleet_body_handle`/
  `_default_local_body_verdict`/`_default_fleet_verdict` (already the
  established convention supervisor.py itself uses for direct
  by-session-id liveness probes). Confirmed `make_embody_spawn`
  (`agent-worktrees ... embody`, an interactive/CLI-driven worker) is the
  CURRENT production spawn path whose reservations carry a **bare**
  session id (no `local-body:`/`fleet-body:` prefix) — i.e. exactly the
  "CLI-embodied" case the resolver-keyed `tracking.liveness_verdict` path
  is for, while `make_headless_spawn`/`FleetSpawner` always prefix their
  handle.
- **Landed Phase 1, item 2** in `queue_liveness.py`: `reconcile_liveness`
  now looks up each held task's active spawn reservation
  (`_active_headless_handle`, mirroring `queue.py`'s
  `_has_headless_reservation`) to classify it before probing. A
  `local-body:`/`fleet-body:`-tagged reservation is headless — probed
  directly by session id via new `headless_local_verdict`/
  `headless_fleet_verdict` params (defaulting to
  `spawn_factories._default_local_body_verdict`/`_default_fleet_verdict`)
  — and its confirmed-`gone` outcome is UNCHANGED (requeue, or
  dead-letter past `max_attempts`). No matching reservation is CLI-embodied
  (or not yet identifiable) — still probed via the pre-existing
  worktree-keyed `resolver`/`tracking.liveness_verdict` — and its
  confirmed-`gone` **`started`** task now auto-suspends instead (a
  `claimed`-not-`started` CLI task keeps the old behavior, since `suspend`
  has no legal `claimed` origin in `task_state_machine.py`). The suspend
  write mirrors `TaskQueue.suspend()` (clears `lease_expires_at`/
  `last_liveness`, keeps owner/owner-session identity for a later `resume`
  to rebind) and keeps the exact pre-existing generation/owner-session CAS
  fencing. New `counts["suspended"]`.
- **Tests:** added `test_reconcile_gone_cli_embodied_task_is_suspended_not_
  requeued` and `test_reconcile_gone_headless_task_still_requeues` (plus a
  `_claim_and_start_headless` helper) to `test_gc.py`. Updated 4
  pre-existing tests whose fixtures/assertions encoded the OLD
  (pre-distinction) behavior for a scenario now correctly reclassified:
  `test_gc.py`'s identity/fencing/dead-letter-cap tests (renamed/adjusted
  to assert `suspended`, or switched to the new `_claim_and_start_headless`
  helper to keep exercising the unchanged requeue path) and
  `test_supervisor.py`'s `test_requeued_task_is_not_double_spawned`,
  `test_recover_gone_releases_stale_reservation_and_respawns`, and
  `test_recover_leaves_live_or_unknown` (their `_ok_spawn()` handle switched
  to a `local-body:`-prefixed one, since supervisor's own automatic
  respawn-on-gone logic is a headless-body behavior; also added the
  matching `local_body_verdict_fn` overrides so `Supervisor.recover_gone`'s
  own headless-specific probe routes through the test's verdict too).
- **Validated:** full suite re-run twice (once mid-change to catch the
  supervisor-test fallout, once final) — **2835 passed, 2 failed
  (the same pre-existing, unrelated `test_bootstrap_check_reconcile_opt_in
  .py` Windows/bash path-mangling failures), 8 skipped**, ~13.5 minutes.
  No other regressions.
- **Not done yet:** Phase 1 items 3–4 (the interactive embodiment
  transaction; universal mutating-action fencing) — see the Runbook above.

### 2026-09-18 — Phase 1, item 3: the interactive embodiment transaction lands
Continued the same session (operator chose "continue now into item 3" when
asked, given item 3's real design work).

- Surveyed `embody.py` (`spawn_embodied_worker`, `prepare_reusable_worktree`,
  `create_worktree`, `resolve_worktree`), `supervisor.py`'s
  `_prepare_spawn_task` (the exact resolve-or-create-worktree +
  record_spawn_worktree + spawn_fn sequence to mirror), `client.py`'s
  `claim`/`start`/`resume`/`reserve_spawn`/`record_spawn*` methods, and the
  coordinator's `/claim`/`/resume` routes. Key finding: `make_embody_spawn`
  (launch via `agent-worktrees embody`, a real interactive/attachable mux+
  Copilot session) is the ALREADY-EXISTING "CLI-embodied" spawn path
  `tracking.liveness_verdict` (item 2's CLI branch) is built for — its
  reservation handle carries a bare session id, never a `local-body:`/
  `fleet-body:` prefix, confirming item 2's classifier is exactly right.
- Worked out the bind-vs-launch ordering by first principles: `start()`
  optionally captures `owner_session_id` (only known once Copilot has
  actually launched), but `resume()` must adopt a session id in the SAME
  atomic transition. A **queued** task IS claimable by a competing pool
  worker, so `claim` (identity only, no session) has to happen BEFORE the
  launch to close that race; a **suspended** task has no such race (`claim`
  only ever picks from `queued`), so its single-step `resume(...,
  adopt_owner_session_id=...)` bind runs AFTER the launch instead, where it
  can adopt the just-launched session atomically. This became the module's
  documented design rationale.
- Discovered mid-implementation that `resume`'s CAS fencing checks
  `expected_generation` AND `owner_session_id == expected_owner_session_id`
  together (not generation alone) — an early version failed with "ownership
  incarnation changed" until `expected_owner_session_id` (captured from the
  read at the top of the transaction) was threaded through too.
- Discovered a second gap while designing the failure story: a **queued**
  task whose pre-launch `claim` succeeded but whose embody launch then
  failed would be stranded `claimed` forever with no session and no active
  reservation -- exactly the state item 2's own liveness GC cannot recover
  (an uncaptured `owner_session_id` never escalates to `gone`). Added
  `_release_prelaunch_claim` (`yield_task`, `release_spawn=False`, since
  `fail_spawn` already released the reservation) for this one case; the
  suspended/resume path never pre-claims, so it has nothing to release
  there — its one unresolved edge (launch succeeds, bind itself fails) is
  documented as intentionally not self-healed (see the module docstring).
- **Landed** `plugins/agent-dispatch/src/agent_dispatch/interactive_embody
  .py` (`launch_interactive_embodiment` + `InteractiveEmbodimentError`),
  a new `interactive_worker_prompt` in `embody_prompts.py` (deliberately
  lighter than `autopilot_worker_prompt`: no worker identity, no pool/
  recipe framing, explicitly non-railroaded, and tells the agent to leave
  the task's state honest -- complete/suspend/abandon -- whenever the
  session ends per the operator's Phase 0 feedback-round-1 framing), a new
  optional `seed` override param on `embody.spawn_embodied_worker` (every
  existing call site unaffected), and a small additive backend extension:
  `ResumeBody`/`DispatchClient.resume()` gained `adopt_owner_session_id`
  (explicit, caller-named target) alongside the existing `adopt_session`
  (which resolves the CALLER's OWN current session -- the wrong direction
  for a transaction resuming a task into a session it just launched but
  isn't itself running inside of); mutually exclusive, additive,
  backward-compatible.
- **Tests:** `tests/test_interactive_embody.py`, 11 new tests covering the
  fresh/proposed/suspended happy paths, ineligible-status rejection, the
  claim-race, launch-failure (and its claim-release), worktree-prep
  failure, and a resume generation-race -- all against a real `TaskQueue`
  with `embody`'s subprocess-shelling functions mocked (no real
  `agent-worktrees`/Copilot process). Extended `test_supervisor.py`'s
  `QueueBackedClient` test double locally (subclassed, not modified in
  place) with the `approve`/`claim`/`start` verbs it never needed before.
- **Validated:** full suite re-run — **2846 passed, 2 pre-existing
  unrelated failures (the same Windows/bash `test_bootstrap_check_reconcile
  _opt_in.py` mangling), 8 skipped**, ~14 minutes. No other regressions.
- **Not done yet:** Phase 1 item 4 (universal mutating-action fencing) --
  the last Phase 1 item; Phase 2 (the CLI verbs, including `agent-dispatch
  embody --interactive` wiring this transaction to a real CLI surface) is
  next after that. See the Runbook above.

### 2026-09-18 — Phase 1, item 4: universal mutating-action fencing lands (Phase 1 COMPLETE)
Continued the same session (operator chose "continue now into item 4").

- Surveyed `_transition`'s existing `expected_generation`/
  `expected_owner_session_id` fencing and confirmed a real gap it does NOT
  close: `generation` only bumps on a handoff-style adopt (`resume(...,
  adopt_owner_session_id=...)`) -- ordinary transitions like `suspend`/
  `abandon`/`complete` never touch it, so it cannot by itself detect "the
  status changed since the operator's UI cached this row" (item 4's actual
  target scenario). Concluded `expected_status` is a distinct, needed
  fencing dimension alongside the existing identity fencing, not a
  duplicate of it.
- Added `_check_expected_status(task, expected_status)` (`queue.py`) --a
  small shared helper raising a uniform "task changed; refresh and retry" --
  and wired it into `_transition` itself (atomic with the rest of the
  write, so every `_transition`-backed verb gets it for free) plus three
  methods that don't go through `_transition`: `set_hold`/`clear_hold`
  (the pause/unpause primitives) and `submit_steer`. Exposed
  `expected_status`/`expected_generation`/`expected_owner_session_id` on
  `abandon()` (previously had none of the three). `clear_hold`'s
  already-unheld idempotent no-op is checked BEFORE the new fence
  (unpausing an already-unpaused task satisfies the operator's intent
  regardless of status drift -- mirrors item 1's own idempotency framing).
- Threaded the new fields end-to-end: `SteerBody`/`AbandonBody` (coordinator
  Pydantic models) gained the new fields, the `/tasks/{id}/steer` and
  `/tasks/{id}/abandon` routes pass them through, and
  `DispatchClient.steer()`/`.abandon()` expose them. `force-stop`/`reset`
  are Phase 2 CLI verbs with no existing queue primitive to fence yet --
  explicitly left for Phase 2 to wire against this now-established pattern
  rather than inventing something new; item 3's interactive-embodiment
  transaction already fences its own `resume`/`start` calls (unchanged).
- **Tests:** 7 new tests in `tests/test_queue.py` -- `set_hold`/`clear_hold`
  stale-status rejection (and the already-unheld idempotent-no-op
  exception), `abandon` stale-status rejection, `abandon`'s existing
  identity fencing reused verbatim, and `submit_steer` stale-status
  rejection.
- **Validated:** full suite run **twice**. First run: 2850 passed, 3 failed
  -- the 2 known pre-existing unrelated failures plus one single flake in
  `test_supervisor.py::test_requeued_task_is_not_double_spawned`. Re-ran
  that one test alone (passed) and the full `test_supervisor.py` file alone
  (224/224 passed) to rule out a real regression, then re-ran the FULL
  suite a second time end-to-end: **2851 passed, 2 pre-existing unrelated
  failed (the same Windows/bash mangling), 8 skipped**, no other failures --
  confirming the single earlier failure was an occasional flake (logged in
  the Runbook in case it recurs), not something this change introduced.
- **Phase 1 is now COMPLETE** (all 4 items landed, tested, committed).
  Phase 2 (the CLI verbs) is next -- see the Runbook above for the full
  pointer and the Plan section for the checklist.

### 2026-09-18 — Phase 2: the four CLI verbs land (Phase 2 COMPLETE)
Continued the same session (operator chose "continue now into Phase 2").

- **`pause`/`unpause`**: thin CLI wrappers over `client.set_hold`/
  `clear_hold`. Needed new `DispatchClient.set_hold`/`.clear_hold` methods
  and `/tasks/{id}/hold`/`/unhold` coordinator routes (`HoldBody`/
  `UnholdBody`) -- Phase 1 built the backend primitive but never exposed it
  over HTTP.
- **`embody --interactive`**: resolves this machine (agent-worktrees, or
  `--machine`) and calls item 3's `launch_interactive_embodiment` directly
  -- client-side orchestration like `Supervisor`, not a coordinator route,
  since the actual `agent-worktrees`/Copilot subprocess launch has to run
  on the invoking machine. `--interactive` is required (the only mode
  implemented; distinguishes this verb from the supervisor's own
  unattended-autopilot spawn path, which already exists under a different
  name).
- **`force-stop`**: new module `force_stop.py`. Surveyed `bridge.py`'s
  `end_worker` (idle-gated `--if-idle`, wrong for a deliberate hard stop)
  and `embody.py`'s `stop_fleet_body` (already unconditional, reusable
  verbatim for a fleet-hosted session) -- added `bridge.force_end_session`
  (`agent-bridge end <id> --force`) as the missing local-unconditional
  counterpart. Local-vs-fleet is decided by comparing the task's
  `owner`'s machine to the caller's own. Only eligible from `started`
  (mirrors `suspend`'s own legal domain -- a merely-`claimed` task never
  had a live session to stop). Session termination is explicitly
  best-effort: the `suspend` state transition proceeds regardless of
  whether termination could be confirmed, since the operator's actual
  intent (stop treating this as running) doesn't depend on a clean
  teardown. This required threading `expected_status`/
  `expected_generation`/`expected_owner_session_id` through `suspend()`
  itself (had none before) plus its coordinator route/client method.
- **`reset --to proposed`**: the one verb needing genuinely new state-
  machine surface -- no existing transition went back to `proposed` from
  anywhere. Added a declared `reset` transition
  (`{queued,claimed,started,suspended} -> proposed`) to
  `task_state_machine.py` and `TaskQueue.reset()`. Design call: preserve
  the durable goal/done_criteria/progress_log (an operator wants a fresh
  attempt at the SAME task, not a new one) while discarding owner/session/
  lease/activity/card state; refuse on a held task (mirrors `resume`'s
  `reject_if_held` -- an explicit pause should never be silently
  overridden by a reset). Full expected-status/generation/owner-session
  fencing via `_transition`, reused verbatim -- no new fencing mechanism
  needed for any of the four verbs; Phase 1's machinery covered all of
  them once exposed.
- **Tests**: `tests/test_force_stop.py` (6 new: local/fleet/no-session/
  ineligible-status/termination-failure-still-suspends), 9 new
  `test_queue.py` tests for `reset`, 4 new `test_coordinator.py` tests
  (hold/unhold x2, reset x2), and 9 new CLI-layer tests in `test_cli.py`
  covering all four verbs' success and error paths.
- **Validated**: full suite run twice. First run: 2878 passed, 3 failed --
  the 2 known pre-existing failures plus one single flake in
  `test_managed_companion.py::test_real_managed_companion_readiness_
  rollback_and_stop` (a real-subprocess-startup-timeout test, inherently
  load-sensitive -- confirmed by re-running it alone: passed). Second full
  run: **2879 passed, 2 pre-existing unrelated failed, 8 skipped**, no
  other failures.
- **Phase 2 is now COMPLETE.** Phase 3 (task schema + column contract,
  display-only, already partially landed in Phase 0) is next and does not
  depend on Phase 2 -- see the Plan section.

### 2026-09-18 — Phase 3: manifest + column fields land (one item deferred)
Continued the same session (operator chose "discuss" first for the
`wt_live`/`artifacts_summary` data-source design, then approved: reuse
existing `activity`/`activity_updated_at` for `wt_live`; land
`artifacts_summary` as an explicit placeholder pending Phase 5).

- Discovered `plugins/agent-dispatch/pivots/agent-dispatch.json` already
  EXISTED -- an older, simpler manifest predating this effort (no columns,
  fewer actions, three action descriptions not yet updated for the
  now-implemented backend). Replaced its content with Phase 0's proposed
  design (columns, updated action descriptions dropping every "PROPOSED —
  not yet implemented" disclaimer), preserving the pre-existing `entry
  .badges` list (`["activity", "labels"]`) unchanged since this phase never
  touched badges and table columns are additive, not a replacement for the
  flat entry shape.
- Confirmed (via a quick trace of `pivots.py`'s `_KNOWN_LEGACY_PIVOTS`) that
  the manifest filename's own identity-check-against-the-installed-plugin
  mechanism is about detecting an ORPHANED file at that name once a plugin
  properly manages its pivot -- not a blocker for editing the plugin's own
  tracked source file, which is exactly what install.sh copies out.
- `board_cli.py`'s `_build()` gained `wt_live` (`"active"` / `"stalled Nm"`
  / `None`, reusing the already-computed `activity`/`activity_updated_at`
  -- confirmed with the operator as the right tradeoff: this board client
  is stdlib-only and re-runs on every Picker refresh, so a fresh
  subprocess/bridge liveness probe was ruled out; the real limitation --
  blank for a CLI-embodied task with no headless self-report -- is
  documented, not silently hidden) and `artifacts_summary` (always `None`,
  an explicit Phase-3-owns-plumbing-only placeholder; Phase 5 owns the
  real computation, per the Plan's own note against the two phases both
  claiming this field).
- Gave every manifest column an explicit `priority` (title=1 hardest to
  drop; artifacts=8 drops first, matching its current placeholder value).
- **Deferred, explicitly, not silently:** the "+N more"/artifact-count-
  glyph column-drop affordance. It needs a small addition to
  `worktree-manager`'s shared `engine.py` (`_fitted_columns`/
  `_column_header`) -- infra shared by every registered pivot -- which felt
  like the wrong thing to rush this late in an already very long session.
  Concrete entry points recorded in the Runbook for whoever picks it up.
- **Tests:** 2 new `test_board_cli.py` tests; updated one pre-existing
  `test_cli.py` manifest test's assertion to the (unchanged) badges list
  after confirming it still matches.
- **Validated:** full suite **2880 passed, 2 pre-existing unrelated
  failed, 8 skipped** (plus the same `test_supervisor.py::
  test_requeued_task_is_not_double_spawned` occasional flake seen before --
  reconfirmed passing in isolation, not a regression).

### 2026-09-18 — Phase 3's deferred item lands: the "+N more" column-drop indicator (Phase 3 COMPLETE)
Picked up via a task-backed handoff (`consume_handoff`/`generate_handoff_
prompt` extension tools both failed with "Extension disconnected before
responding to tool call" again -- the same known CAR bug; fell back to the
`agent-dispatch consume <id> --defer-complete` CLI directly, which
succeeded in ~30s, longer than `handoff-core.mjs`'s own 20s
`runAgentDispatchConsume` timeout -- worth a heads-up to whoever owns that
extension, since it means the in-session `consume_handoff` tool can fail
on a legitimately-slow-but-successful consume, not just on the CAR
extension-disconnect bug). Operator chose to finish Phase 3's deferred item
before starting Phase 4.

- `worktree-manager/src/worktree_manager/production_picker/picker_tui
  /engine.py`'s `_column_header(cols, width, dropped=0)` gained the new
  `dropped` param: when positive, appends a compact `+N` right-aligned at
  the end of the header row, spare width permitting; silently omitted
  (never truncated/wrapped) when there isn't room. The `build_data` call
  site computes `dropped = len(reg.columns) - len(cols)` from
  `_fitted_columns`'s already-fitted result (no new column-fit logic
  needed -- `_fitted_columns` already drops lowest-priority columns first,
  this only surfaces the count that were dropped).
- **Tests:** 2 new direct unit tests in `worktree-manager/tests
  /production_picker/test_picker_tui.py`
  (`test_fitted_columns_drops_low_priority_when_narrow`,
  `test_column_header_renders_dropped_count_indicator`), both resolving
  the render-holder class by shape (mirrors the existing
  `test_banner_line_helper_levels` pattern) rather than a hardcoded class
  name.
- **Validated:** `worktree-manager`'s `tests/production_picker` suite:
  **557 passed, 1 skipped** (matches the pre-existing baseline; no
  regressions from the `_column_header` signature change, since the new
  param defaults to `0` and every other call site is untouched).
- **Phase 3 is now fully COMPLETE.** Not yet pushed/PR'd -- 6 local commits
  on this branch (Phase 1 items 2/3/4, Phase 2, Phase 3's manifest landing,
  and this session's deferred-item follow-up).

### 2026-09-18 — Caught via the preview screenshots: the "+N" indicator never actually had room to render
Operator asked to see screenshots of the just-landed "+N more" indicator in
practice, using the existing `tasks-preview` snapshot tooling
(`worktree-manager/scripts/picker-snapshot/tasks-preview/`). The first
re-render (`tasks-list.png`, the canonical 118-col capture) showed ARTIFACTS
correctly dropped -- but **no `+1` indicator anywhere in the header row**,
even though `dropped=1` was being computed and passed through correctly.

Root cause: `_fitted_columns`'s flex (`title`) column, by design, absorbs
**100%** of any width left over after fitting the other columns -- that's
the whole point of a flex column. But it meant `_column_header`'s row
always exactly filled the render width whenever anything was dropped,
leaving **zero** spare width for the `+N` indicator to occupy. The feature
landed in the prior session, its unit tests passed (they called
`_column_header` directly with a hand-picked `dropped` count against
columns that happened to leave slack), but it could never actually surface
in a real render -- a gap only the screenshot tooling exposed, not the
narrower unit tests.

**Fix:** `_fitted_columns` now runs a two-pass fit. An unreserved trial fit
decides whether anything gets dropped at all; only when it does, a second
fit reruns against `width - 1 - _DROP_INDICATOR_RESERVE` (a new class
constant, 4 cols: leading space + up to 3 glyphs, e.g. `+99`), so the flex
column leaves the header's own later `_column_header` call actual room for
the indicator. A trial fit that drops nothing returns as-is -- no
reservation, no wasted space, unaffected by this change.

- **Tests:** 2 more tests in `test_picker_tui.py`:
  `test_fitted_columns_reserves_room_for_drop_indicator` (direct unit test
  proving `_fitted_columns` + `_column_header` together actually render
  `+N`, not just that `_column_header` can given a manually-supplied count)
  and `test_registered_pivot_narrow_width_renders_drop_indicator` (a real
  `PickerApp` end-to-end render at a narrow width, asserting `+` appears in
  the captured screen text -- the kind of integration check the screenshot
  tooling caught that a narrower unit test missed).
- **Validated:** `tests/production_picker` full suite: 558 passed / 1
  skipped, plus one isolated flake
  (`test_profiles_active_row_label_highlighted`, confirmed passing alone --
  matches this effort's existing pattern of occasional flakes under
  full-suite load, not a regression).
- Regenerated all 10 `tasks-preview` PNGs; `tasks-list.png` now visibly
  shows `+2` (LIVE + ARTIFACTS both dropped) at the 118-col width;
  `tasks-list-wide.png` (160 cols) shows every column with no indicator, as
  expected when nothing is dropped.
- **Lesson for the Runbook / future phases:** a hand-crafted unit test that
  calls a render helper directly with a manually-chosen argument can pass
  while the real call site never actually produces that argument in a way
  that renders anything -- an end-to-end screenshot or full-app render
  test is what actually caught this. Worth reaching for the existing
  `tasks-preview` tooling (or an app-level render test) whenever a change
  touches the column-fit/header path again, not just direct unit tests
  against the helpers.

### 2026-09-18 — Bug filed (unscheduled): Tasks pivot navigation freezes the UI
Operator report: switching machine-tab focus onto Tasks freezes/blocks the
whole Picker UX momentarily instead of switching immediately with a loading
state -- suggesting a blocking-I/O call somewhere in the Tasks-pivot
render/switch path rather than going through the pivot's own background
runtime. Filed as a Plan item (see the new "Bug (filed, unscheduled)" entry
above, between Phase 4 and Phase 5) rather than investigated to completion
this session -- a quick look confirmed `RegisteredPivotRuntime.ensure`/
`.repoll` and the row-enrichment helpers are NOT the cause (already
async/in-memory on inspection), narrowing the search for whoever picks this
up next to `_machine_key_map`'s first-call cost and the tab-switch
keypress's own call path. Not yet reproduced/root-caused; no fix attempted.

### 2026-09-18/19 — PR #2913 opened, reviewed, and MERGED (Phases 0-3 landed to `main`)
Operator asked to push the accumulated work. This branch had drifted 66
commits behind `main` since Phase 0 (never landed incrementally -- see the
new Coordination note and `ThomasMichon/copilot-extensions#2908`, filed
upstream against the `efforts` plugin to get guidance that actually
enforces per-phase PR landing going forward). Rebasing surfaced two real
conflicts, both resolved by hand: `origin/main` had independently landed
an equivalent OptionList scroll-preservation fix (this branch's redundant
copy was dropped); `pivots.py`'s `Column`/`RegisteredPivot` classes had
moved to a new `pivot_manifest.py` module upstream (`Column.priority` was
re-applied there instead). Operator chose a single combined PR rather than
splitting retroactively into per-phase PRs.

**PR #2913 opened**, then Copilot's automated review returned 16 findings
(5 High/Critical, 7 Medium, 4 Low) -- all real, all fixed in this session:
- **Backend correctness:** `start()` now honors held-task fencing;
  `reset()` releases any active spawn reservation instead of orphaning a
  live session; `suspend()` gained `reject_pending_steer=False` so
  force-stop can deliberately override a pending card instead of failing
  after already killing the session; `reserve_spawn` gained
  `allow_suspended_reembodiment` (deriving the carried worktree from a
  suspended task's own `owner` when no reservation carries it) and
  `interactive_embody.py` now actually checks the `reserved` flag it was
  previously ignoring (a real "steal an active reservation" bug);
  `force_end_session`/`prepare_reusable_worktree` callers now catch
  `OSError`/`TimeoutExpired`; auto-suspend clears stale `activity` fields;
  supervisor excludes held tasks from spawn eligibility; `--expected-status`
  wired through `reset`/`abandon`/`steer submit`.
- **Manifest scope correction (the one Critical finding needing a design
  call, not just a bugfix):** the manifest's `open-cli` action reused the
  Worktrees pane's GENERIC internal verb, which has no idea about Phase 1
  item 3's ownership transaction and would mis-handle Proposed/Queued/
  Suspended tasks. Rather than rushing a dedicated internal verb (real
  Phase 7 scope), `board_cli.py` now computes `has_worktree`/`embodied`/
  `held` from real task fields, while `cli_openable`/`has_charter` stay
  honestly hard-`False` with an explanatory comment each -- schema-visible,
  never actually reachable, until their owning phases (7/5) land the real
  wiring. Also added the missing `unpause` action.
- **Housekeeping:** version bump; the preview screenshots' synthetic
  fixture identifiers (org/repo/host placeholders) were replaced with
  clearly-generic ones so no personal or internal identifier appears in
  captured evidence; README count fix.

**CI unblocking (two more real issues, neither review findings):** a
pre-existing, unrelated `agent-index` version-drift test was already
broken on `main` before this PR touched anything (synced its stale
declaration, which itself needed `agent-index`'s own version bump per
`check-version-bump.py`); and this effort's own commits pushed 6 files
further past their shrink-only module-size ceilings plus 2 new files
(`embody.py`, `queue_spawn_reservations.py`) past the flat 1000-line hard
cap for the first time -- widened deliberately via the tool's own
documented `--refresh-baseline --allow-widen` escape hatch (existing
entries) plus 2 manual new entries (new crossings aren't eligible for the
automated ratchet). Splitting these modules remains real, separate
follow-on work, not attempted here.

Survived two more `main`-drift rebases while landing (once for an
unrelated agent-worktrees PR, once for the module-size baseline lagging
behind main's own widen automation).

**Final validation:** 2961 passed / 2 known pre-existing unrelated failed
(`test_bootstrap_check_reconcile_opt_in.py::test_sh_*`) / 1 known occasional
flake (`test_supervisor.py::test_requeued_task_is_not_double_spawned`,
reconfirmed passing in isolation) / 9 skipped. All CI green,
`mergeStateStatus: CLEAN`.

**PR #2913 squash-merged 2026-09-19.** Phases 0-3 are now on `main`.

