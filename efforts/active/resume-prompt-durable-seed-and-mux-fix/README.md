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
      bypasses the Picker's two-hop dance entirely) ALSO gets the seed
      embedded via `with_seed` for consistency -- `pending_seed` stays
      persisted on the record regardless (the Picker's own flow still needs
      it there for the later re-resolve).
- [x] `sessions.headless_new_session`'s pre-existing (and already
      argv-based, already-correct-in-spirit) seed delivery updated from the
      short `-i` to the full `--interactive`, for the same PowerShell-safety
      reason.
- [x] Tests: `embody_resume.with_seed`/`with_resume` composition (unit),
      `resolve_cli`'s relaxed guard (both accept and still-reject cases),
      `_resolve_json_mode`'s real end-to-end argv construction against a
      real tracking record (explicit seed, pending-seed pickup +
      clear-not-double-delivered, no-seed-unchanged, and the no-mux-parity
      case explicitly), `_create_worktree_core`'s own direct-caller argv,
      and `headless_new_session`'s updated flag.

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
- [ ] Now that Phase 1 makes `_create_worktree_core`'s own plan carry the
      seed in argv too, evaluate whether `launch-session.{ps1,sh}`'s
      post-create `Invoke-SeedDeliverySafe`/`agent-worktrees embody
      --worktree-id <id> --json` call (the pending_seed claim-and-send-keys
      fallback) can be simplified or retired now that the Picker's own
      two-hop re-resolve (`_resolve_json_mode`'s worktree-id branch, Phase 1)
      already delivers the SAME `pending_seed` via argv before that script
      ever runs -- i.e. is the send-keys fallback now provably always a
      no-op for the Picker's own flow, kept only for a genuinely
      out-of-band attach? Confirm with a live trace before touching the
      script; do not remove a safety-net fallback on an assumption.

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
