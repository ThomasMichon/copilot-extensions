# Resume Prompt: Durable `--interactive` Seed Delivery, No-Mux Parity

- **Slug:** `resume-prompt-durable-seed-and-mux-fix`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-10-05
- **Status:** Active
- **Vision:** `picker` (no existing §Features entry yet -- candidate follow-up
  once Phase 1 lands; mirrors how `picker-new-session-prompt-and-composer`,
  its sibling/predecessor effort, left its own vision entry open)
- **Umbrella issue:** [#5415](https://github.com/ThomasMichon/copilot-extensions/issues/5415)

## Documentation impact

New CLI behavior (`resolve --worktree-id --seed`, the relaxed seed guard,
`embody_resume.with_seed`) is self-documenting at the flag-help level (both
`resolve --help` and the module docstring updated in the implementing
commits); no separate CLI reference doc exists for `resolve`/`embody` beyond
their own `--help` text. No vision, architecture, or operating-procedure doc
describes the Resume launch flow at a level this change affects -- behavior
is additive and this effort's own README remains the authoritative
in-progress record, kept current in its Journal each session.

## Guiding Intent

`picker-new-session-prompt-and-composer` (Status: Done) built a "New
worktree…" prompt field, delivered via a persisted `pending_seed` record
field that `agent-worktrees embody`/`copilot` claim and type into a live mux
pane via send-keys on first attach. That mechanism works, but it has two
real limitations this effort closes:

1. **It only ever worked because `create`/`resolve --new` never launch
   Copilot themselves** ("no launch, no mux" -- there's no live process to
   hand a prompt to at creation time, forcing the persist-then-deliver-later
   design). **Resume has no such constraint**: `launch-session.{ps1,sh}`
   already synchronously builds and execs the real `copilot --resume=<target>
   ...` command in one shot. Live-verified (operator-directed spike,
   2026-10-05): `copilot --resume=<id> --interactive "<prompt>"` run as a
   SINGLE process correctly resumes the full prior conversation history AND
   auto-executes the new prompt as the next turn -- no mux pane send-keys
   needed at all.
2. **A `--no-mux` launch has no mux pane for send-keys to target in the
   first place**, so the old mechanism structurally cannot reach it. Since
   `--interactive`/`--resume=` are plain process arguments, carrying the seed
   durably in the launch command itself (not an ambient, ready-poll-and-type
   side channel) works identically whether the real launcher ends up
   wrapping the command in a mux pane or running it directly.

This effort delivers "Resume prompt" (a Picker Actions-menu entry, symmetric
with "New worktree"'s prompt field) on top of this more robust argv-based
mechanism, and migrates the EXISTING "New worktree"/pending_seed delivery
onto the same durable-argv path wherever the real launch command is built
synchronously (keeping the old send-keys path only as a fallback for a
caller that attaches via some other, truly out-of-band route).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| Driving agent | Authors and drives all phases | The effort's active worktree |

## Coordination

- **Topology:** independent per-phase PRs.
- **Host (owns PRs):** Driving agent.
- **Delegates:** none currently.
- **Handoff:** manual, sequenced-session loop (matching
  `picker-new-session-prompt-and-composer`'s own established pattern) -- each
  session drives until its context fills, updates the Journal below, then
  hands off via `generate_handoff_prompt`/`save_handoff_prompt` (never
  `trigger_handoff` unarmed -- this repo's `context-handoff` config is
  manual-cutover-only). The operator manually pastes the returned prompt into
  a fresh session to continue the loop.

## Context

### Prior art and the corrected finding (read before picking this up)

An earlier investigation session (2026-10-05, same day) spike-tested
`agent-worktrees embody --seed` directly and found it silently reported
`"seeded": true` while delivering nothing -- traced (incorrectly, at the
time) to a bare/unqualified mux pane-id targeting bug. **Re-investigation
when this effort actually began building found that diagnosis was wrong**:
`sessions_pane_retire._mux_qualified_pane_target`/`pane_seed.mux_seed_pane`
already resolve pane targets session-qualified (PR #2890, merged
2026-09-18 -- well before the original spike). The REAL prior bug was a
readiness-detection false-positive (the old heuristic could match a static
"Getting started" onboarding screen's own input-box border glyphs before
Copilot was genuinely ready), independently fixed by PR #4854 ("Detached
launches survive slow resumes: live-region readiness...", merged 2026-10-05
by a different author, hours before this effort's own work began). Live
re-verification on current `dev` confirmed the mechanism now **honestly
reports failure** (`"seed_reason": "not-ready-timeout"`) rather than falsely
claiming success -- the critical property. **No additional pane-targeting
fix was needed from this effort**; this section exists so a future reader
doesn't re-chase an already-closed bug.

What IS this effort's own, newly-identified architectural improvement (not
previously fixed by anyone): carrying the seed durably in the launch
command's own argv, covering `--no-mux` (which the old mechanism structurally
cannot reach at all) and removing the dependency on pane-targeting/readiness
polling for the Resume case entirely.

### Related work

- **`picker-new-session-prompt-and-composer`** (Status: Done) -- the sibling
  effort whose "New worktree" prompt field and `pending_seed` persistence
  this effort builds on and partially supersedes (the delivery mechanism,
  not the UI/persistence). Read its Journal's "Item 4 redesigned" entry
  (2026-09-30) for the original design rationale this effort revises.
- **`worktree-manager-control-plane`** Phase 9 (merged, PR #5232/#5239) --
  the "Launch in new window" relocation/fix immediately preceding this
  effort in the same investigation thread; unrelated in scope but same
  investigation session lineage (`new_window_spawn.py`,
  `LaunchRequest.new_window`).

## Request

> It would be nice if, similar to the "New worktree" prompt, we could have a
> "Resume prompt" option. Usually, when I resume a worktree, the first thing
> I'll do is just say "Resume", "Check status here", or "Sync worktree on
> latest from origin, then let me restart, before we check status here". And
> the final one is that if it was last left at a handoff, I'll execute the
> handoff. I'd love a way, when I want to resume a worktree, to have a quick
> way to view the most-recent messages, and then provide a "Resume prompt".
> We do currently have a way to view the most-recent messages, so let's just
> add the prompt to align with "New worktree" and give that a try.
>
> I don't know if copilot lets us resume a worktree *and* provide a prompt in
> the same breath. It's worth spike-testing.

Follow-up, after an initial (flawed) prototype attempt using the old
pending_seed/pane-send-keys mechanism:

> Shouldn't the seed prompt delivery be passed to copilot on the CLI args via
> `--interactive`? And doesn't that get carried through from the
> `launch_session.ps1` call on *its* CLI targeting the specific worktree?
> Where does the mux pane id even come into this picture?

And the concrete scope-setting instruction that shaped Phase 1's actual
implementation:

> Fix the Mux unique-id issue, and make it so launching with "No mux" also
> works with the seed, meaning the seed has to be carried durably in the args
> and not some ambient property. Be sure to full `--interactive` and not `-i`
> as PowerShell intercepts `-i` itself.

(The "Mux unique-id issue" half of this instruction turned out to already be
fixed upstream by the time this effort's implementation work began -- see
Context above. The "no-mux parity, durable args, full `--interactive`" half
is this effort's actual Phase 1 deliverable.)

## Plan

### Phase 1 — Durable argv-based seed delivery, no-mux parity (Done)
- [x] `embody_resume.with_seed(launch_cmd, seed)`: pure helper appending the
      **full** `--interactive` flag (never the short `-i`) to a launch
      command, composable with the existing `with_resume()` so
      `copilot --resume=<target> --interactive "<seed>"` is built from the
      same two small, independently-testable pieces.
- [x] `resolve_cli.py`'s seed guard relaxed: `--seed` is now valid with
      `--worktree-id` (not only `--new`), still rejected for `--base` and any
      remote `--machine` target (new AND resume alike -- a naive
      space-joined SSH relay is unsafe for arbitrary seed text either way).
- [x] `resolve_cli._resolve_json_mode`'s own inline worktree-id/resume
      branch -- the ACTUAL code path `agent-worktrees resolve --worktree-id
      <id> --json` hits, i.e. what the Picker/`launch-session.{ps1,sh}`
      really call -- appends the seed via `with_seed` onto the SAME returned
      `launch_cmd` that already gets `--resume={last_session}` appended.
      Works identically muxed or `--no-mux`: the mux/no-mux wrapping
      decision happens entirely in the separate launcher script, after this
      plan is already built.
- [x] `resolve_launch_cli._resolve_resume_context` -- the sibling code path
      for the **non-JSON**, interactive-human `agent-worktrees resolve
      --worktree-id <id>` CLI -- gets the identical treatment, for parity.
- [x] An explicit `--seed` on either resume call wins; either way, any
      record-persisted `pending_seed` (queued at creation time by `resolve
      --new --seed`, picked up here since the Picker's own two-hop
      new-worktree flow re-resolves by `--worktree-id`) is claimed (cleared)
      under the existing race-safe write-guard, so `agent-worktrees embody`'s
      own fallback claim-and-send-keys delivery never finds it again and
      double-delivers the same turn.
- [x] `worktree_creation._create_worktree_core`'s OWN returned launch plan
      (used by a DIRECT `resolve --new --json`/`create`-then-exec caller that
      bypasses the Picker's two-hop dance entirely) deliberately does NOT
      also embed the seed -- it only persists `pending_seed` on the
      record, unchanged from pre-Phase-1 behavior. `pending_seed` stays
      the single, unambiguous owner of "queued but not yet delivered";
      embedding it in both this plan's argv and leaving it persisted would
      create two live delivery paths for the same prompt. A direct caller
      that wants immediate, synchronous delivery follows up with
      `resolve --worktree-id --seed` (the real delivery point, above)
      instead.
- [x] `sessions.headless_new_session`'s pre-existing (and already
      argv-based, already-correct-in-spirit) seed delivery updated from the
      short `-i` to the full `--interactive`, for the same PowerShell-safety
      reason.
- [x] Tests: `embody_resume.with_seed`/`with_resume` composition (unit),
      `resolve_cli`'s relaxed guard (both accept and still-reject cases),
      `_resolve_json_mode`'s real end-to-end argv construction against a
      real tracking record (explicit seed, pending-seed pickup +
      clear-not-double-delivered, no-seed-unchanged, and the no-mux-parity
      case explicitly), `_create_worktree_core`'s own direct-caller plan
      (confirming it does NOT embed the seed), and `headless_new_session`'s
      updated flag.

### Phase 2 — Picker UI: "Resume prompt…" Actions-menu entry (Not started)
- [ ] Add a "Resume prompt…" entry to the worktree row's Actions submenu
      (`engine_worktree_actions.py`, sibling to "Launch in new window"/"Bare
      resume"/"Messages"), offered for the same Open/Resume-eligible rows
      "Launch in new window" already uses as a model (reuse its
      `_run_bg`/thread-safety lessons from Phase 9 of
      `worktree-manager-control-plane` if the dispatch ends up calling
      `_run_launch` in-process from a live Picker thread again).
- [ ] A lean prompt-composer dialog reusing `field_widgets.compose_field`'s
      textarea building block (the same one `ScopeDlgScreen(show_prompt=...)`
      already uses for "New worktree" -- see
      `picker-new-session-prompt-and-composer` Phase A item 1's extraction),
      confirming into the resume decision's `options["seed_prompt"]`.
- [ ] Thread `seed_prompt` through `LaunchRequest` (field already exists,
      added for "New worktree") to `engine_client.resolve_launch_plan()`'s
      `seed` kwarg for the `worktree_id=` (resume) case -- confirm it isn't
      still gated to `new=True` only at the Worktree Manager layer (Phase 1
      only fixed the `agent-worktrees` CLI side's own guard).
- [ ] Per the operator's own explicit scope-down ("let's just add the
      prompt to align with 'New worktree', and give that a try"): do NOT
      combine this with the existing "Messages" (recent-messages) viewer
      into one screen for v1 -- they stay separate affordances.
- [ ] Validate beyond unit tests (per `AGENTS.md`'s own policy, and this
      effort's own Phase 1 cautionary tale about trusting a CLI's
      self-reported success alone): an actual live "Resume…" launch from the
      real Picker with a typed prompt, confirmed via `recent-messages`/a
      genuine follow-up answer in the resumed conversation, not just a
      green unit-test suite.

### Phase 3 — Migrate "New worktree"'s own delivery onto the durable path (Not started)
- [ ] `_create_worktree_core`'s own plan deliberately does NOT carry the
      seed in argv (Phase 1 -- single-owner, no double-delivery); evaluate
      whether `launch-session.{ps1,sh}`'s post-create
      `Invoke-SeedDeliverySafe`/`agent-worktrees embody --worktree-id <id>
      --json` call (the pending_seed claim-and-send-keys fallback) could
      instead be simplified by having THAT call itself follow up with a
      `resolve --worktree-id --seed`-style durable delivery, rather than
      the send-keys mechanism, now that the durable-argv path exists for
      resume. Confirm with a live trace before touching the script; do not
      remove a safety-net fallback on an assumption.
- [ ] **Deferred from Phase 1:** `_resolve_json_mode`/
      `_resolve_resume_context` claim (clear) a persisted `pending_seed` at
      PLAN-BUILD time, before the external launcher script has actually
      exec'd the returned command -- a failure in that script before exec
      (its own update/preflight work, or mux-session creation) loses the
      seed with no restore. Teach the launcher scripts to report "failed
      before exec" back through `pending_seed.restore_pending_seed` (the
      same primitive `embody`'s own post-attempt mux-pane delivery already
      uses for its own restore-on-failure case) -- this requires touching
      `launch-session.{ps1,sh}` directly, which is why it's deferred here
      rather than attempted in Phase 1.

## Validation Plan

- **Phase 1:** `embody_resume.with_seed`/`with_resume` compose correctly
  (unit); the relaxed seed guard accepts `--worktree-id` and still rejects
  `--base`/any remote `--machine` target (unit, both JSON and non-JSON
  dispatch); `_resolve_json_mode`'s real worktree-id branch appends
  `--interactive <seed>` to its ACTUAL returned `cmd` for an explicit seed, a
  persisted `pending_seed` (cleared after, not double-delivered), and a
  `--no-mux` request -- all verified against a real on-disk tracking record,
  not a mock of the function under test.
- **Phase 2:** a live Picker "Resume prompt…" launch delivers the typed text
  as the resumed conversation's next turn, confirmed via direct observation
  (recent-messages or pane output), not a CLI JSON's own self-reported
  success flags alone (see Context's own cautionary tale).
- **Phase 3:** if the send-keys fallback is simplified/retired, prove (via a
  live trace, not just code reading) that the Picker's own flow never
  depended on it, and that whatever out-of-band attach case it existed for
  still degrades safely (a clearly reported "unseeded" launch, never a
  silent loss).

## Proposal

_Pending._

## Journal

- **2026-10-05** — Effort created. Phase 1 implemented and tested in the same
  session (130 new/updated tests passing; full agent-worktrees suite run in
  parallel to confirm no regressions -- see the next entry for the result).
  Investigated, and corrected, the predecessor handoff's "pane-id bug" claim
  (see Context above) before writing any pane-targeting fix -- the real,
  already-fixed-upstream root cause turned out to be a readiness-detection
  false positive (PR #4854), not pane-id uniqueness (already fixed by PR
  #2890, well before the original spike). Filed umbrella issue #5415. Next:
  land Phase 1's PR, then pick up Phase 2 (the actual Picker UI).
- **2026-10-05** — PR #5442 (Phase 1) opened against `dev`; Copilot code
  review returned 6 findings (4 Medium, 2 Low), all addressed in the same
  session:
  - **Bare-resume + `--seed` silently discarded the prompt.** Fixed with an
    explicit rejection guard in `cmd_resolve` (shared by both the JSON and
    non-JSON resume dispatch paths), plus two new tests
    (`test_resolve_worktree_id_with_bare_resume_and_seed_is_rejected_json`,
    and a paired "no seed, unaffected" case).
  - **`worktree_creation._create_worktree_core` embedded the seed in its own
    plan WHILE ALSO leaving `pending_seed` persisted** -- a genuine
    double-delivery risk for a direct `resolve --new --json`/`create`-then-
    exec caller (unlike the Picker's own two-hop flow, which re-resolves by
    `--worktree-id` and so only ever reaches the already-claim-guarded
    Phase 1 resume path once). Fixed by reverting
    `_create_worktree_core` to NOT embed the seed at all -- it now only
    persists `pending_seed`, matching its pre-Phase-1 behavior exactly;
    `test_worktree_creation_seed.py` rewritten to assert the no-embed
    behavior.
  - **Premature `pending_seed` consumption** (claimed/cleared at
    plan-build time in both `_resolve_json_mode` and
    `_resolve_resume_context`, before the external launcher
    (`launch-session.{ps1,sh}`) has actually exec'd the command) --
    evaluated at length; **accepted as a documented, narrow Phase-3-deferred
    limitation** rather than fixed in Phase 1. A true fix needs the launcher
    scripts themselves to report "failed before exec" back through
    `pending_seed.restore_pending_seed` (the same primitive `embody`'s own
    post-attempt mux-pane delivery already uses), which means teaching the
    launcher scripts a new contract -- explicitly out of this phase's scope
    (folded into Phase 3's own send-keys-fallback evaluation above). The
    alternative (never clearing here) reintroduces real double-delivery on
    every subsequent resume instead of this narrow, infrequent loss window;
    `resolve` already performs other irreversible side effects
    (`mark_resumed`/`save_record`, activity logging) with the identical
    "external launcher might still fail after this" exposure, so this isn't
    a new risk class. Documented inline at both claim sites in
    `resolve_cli.py` and `resolve_launch_cli.py`.
  - **Changefile requested `minor`**; repo policy defaults to `patch` absent
    explicit maintainer direction -- fixed.
  - **PR description needed the Documentation-impact statement copied in
    directly** (not just referenced via this README) -- fixed.
  - 213 tests across the full seed/resolve/worktree_creation/handoff-cutover
    keyword sweep pass with no regressions after all fixes.
- **2026-10-06** — Pushed the review-round fixes above; a second Copilot
  review pass (after a CI timeout flake on an unrelated `worktrees-smoke`
  job, re-run) returned 3 resolved + 12 new findings, all addressed:
  - **Real bug, fixed:** `handoff_cutover.py`'s headless `--dry-run` plan
    still previewed the stale short `-i <seed>` instead of the full
    `--interactive <seed>` the actual headless launch now sends (a
    different call site than Phase 1 touched) -- `handoff-cutover
    --headless --dry-run` no longer matched the command that would
    actually execute. Fixed the dry-run `cmd` construction and its pinned
    test assertion.
  - **Help text correction:** `--seed`'s own `--help` text claimed `--new`
    carries the seed on the returned launch command "ALSO" (implying
    argv-embedding), contradicting the Phase-1-review decision to revert
    `_create_worktree_core` to persisted-only. Rewrote the help text to
    distinguish `--worktree-id` (argv-based, real delivery) from `--new`
    (persisted-only, since this command never launches Copilot itself for
    `--new`).
  - **Effort doc correction:** the Phase 1 plan's own checklist (4
    locations) still described `_create_worktree_core` as embedding the
    seed, contradicting both the actual (reverted) implementation and this
    Journal's own prior entry. Rewrote those checklist items and Phase 3's
    corresponding follow-up to match reality.
  - **Review-provenance cleanup (10 Low findings):** removed "review
    finding"/"PR #5442" language from source comments and test docstrings
    throughout (`resolve_cli.py`, `resolve_launch_cli.py`,
    `worktree_creation.py`, both seed-guard/creation test files) --
    comments and test documentation now describe the invariant directly,
    without baking in review-round provenance that goes stale once the PR
    merges. (The Documentation-impact finding and the two Medium
    premature-consumption findings were already addressed/accepted in the
    prior entry; the bot's comments API still surfaces resolved findings
    alongside new ones, which is expected.)
  - Re-ran the full 213-test keyword sweep after these edits: still
    passing, no regressions.
