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
- [x] A live-mux reattach (`_resolve_resume_context`'s `verdict.mux_live`
      branch, Phase 1) correctly QUEUES a seed instead of losing it, and
      now also DELIVERS it on that exact reattach, not only on a later
      fresh launch/attach: `launch-session.{ps1,sh}`'s "join existing
      session" branch -- the ONE ground-truth point that actually knows a
      reattach, not a fresh launch, is happening -- calls the same
      `Invoke-SeedDeliverySafe`/`_aw_deliver_pending_seed` helper the
      fresh-mux-create path already calls (a no-op when nothing is
      queued). `test_launch_session_unwrap.py`'s drift guard asserts both
      call sites and their relative ordering.
- [ ] **Deferred from Phase 1 (ninth review round):** `claim_pending_seed`
      cannot distinguish "nothing was pending" from "claim failed" (lock
      contention, an unreadable record) -- both return `None`. With an
      explicit seed supplied, the current code proceeds with the explicit
      value regardless, silently leaving an old, undelivered `pending_seed`
      queued if the claim genuinely failed rather than finding nothing --
      that stale prompt can then surface as an unexpected turn on a LATER
      resume. Needs a richer return contract (e.g. a small result object
      distinguishing "empty" / "claimed" / "claim-failed") threaded through
      every existing call site, not a narrow single-file fix -- real design
      surgery, scoped here rather than rushed.
- [ ] **Deferred from Phase 1 (ninth review round):** `_RecordLock.__enter__`
      creating/opening its sidecar file can itself raise `OSError`/
      `PermissionError`, which currently escapes `pending_seed`'s own
      documented "degrade to False, never raise" contract in
      `claim_pending_seed`/`set_pending_seed`/`restore_pending_seed` alike.
      A pre-existing lock-primitive gap (not introduced by this effort),
      but worth hardening alongside the claim-ambiguity item above since
      both touch the same call sites.

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
- **2026-10-06** — `guards + lint` flagged `handoff_cutover.py` at 1001
  lines (the 1000-line module cap is shrink-only, and the prior round's
  dry-run fix added net lines); fixed by tightening the comment's wording
  (no behavior change). A fourth Copilot review pass then surfaced 2
  genuinely new Medium findings (not nits this time) plus 2 already-fixed
  stale references the bot's own "Previously missed" section flagged
  against unchanged code:
  - **Bare-resume + a PERSISTED `pending_seed` still got claimed/injected
    in JSON mode.** The earlier bare-resume guard (this round's first
    entry) only rejected an *explicit* `--seed`; it never checked
    `bare_resume` before claiming/embedding an already-persisted
    `pending_seed` in `_resolve_json_mode`'s own branch, unlike
    `_resolve_resume_context` (the non-JSON sibling), which already
    guarded this correctly. Fixed by wrapping the claim+embed block in
    `if not bare_resume:`, mirroring the sibling path exactly; new test
    `test_bare_resume_leaves_a_persisted_pending_seed_queued_in_json_mode`.
  - **A live mux session silently lost an explicit seed.** When
    `verify_worktree_active()` finds an existing live mux session, the
    external launcher reattaches that pane and never execs the returned
    `launch_cmd` at all (`worktree-manager/bin/launch-session.{sh,ps1}`'s
    own live-mux handling) -- but `_resolve_resume_context` still claimed
    and embedded the seed into that unused command regardless. Fixed by
    detecting `verdict.mux_live` and, in that case, persisting an explicit
    seed via `pending_seed.restore_pending_seed` instead (so the OLDER
    send-keys mechanism, which CAN reach an already-live pane, still
    delivers it) rather than clearing/embedding it into a command nobody
    runs; a pre-existing persisted `pending_seed` is left untouched in
    this case too. New test
    `test_live_mux_resume_queues_explicit_seed_instead_of_embedding_unused_argv`.
  - Re-ran the full keyword sweep: 215 tests passing (213 + 2 new), no
    regressions.
- **2026-10-06** — A fifth Copilot review pass surfaced 2 genuinely new
  **High**-severity findings (the deepest yet -- both trace into
  `worktree-manager`, not just `agent-worktrees`), plus 2 Low nits and 2
  "Previously missed" findings against this PR's own earlier (unchanged
  since) commits:
  - **A relocated/delegated launch discarded the already-claimed seed
    entirely.** `worktree_manager.relocated_launch._run_relocated_mux_launch`
    re-invokes `launch-session.{sh,ps1}` with only `--project`/
    `--worktree-id`/`--bare-resume` -- it never reused the `plan.cmd` the
    earlier `_resolve_for` call already built (with the seed claimed and
    embedded into it), so the script's own SECOND, actually-exec'd
    `resolve --worktree-id --json` call ran with no seed at all: an
    explicit `--seed` was never forwarded, and a persisted `pending_seed`
    had already been claimed (cleared) by the first, discarded resolve.
    Fixed by extracting the already-claimed seed straight out of
    `plan.cmd`'s own `--interactive <value>` pair
    (`relocated_launch._seed_already_claimed_in`) and forwarding it as
    `--seed` on the delegated re-invocation -- covering both the explicit
    and the persisted-and-claimed case uniformly, excluding bare-resume
    (matching the engine's own rejection). Two new tests:
    `test_run_launch_relocated_script_forwards_already_claimed_seed`,
    `test_run_launch_relocated_script_bare_resume_never_forwards_seed`.
  - **A live mux reattach only queued the seed, never actually delivered
    it.** The immediately-prior round's own live-mux fix (above) prevents
    permanent loss, but the reviewer correctly noted it does not actually
    deliver the seed on THIS reattach -- the engine process returns a plan
    and exits; it has no mechanism to type into an already-live pane
    itself (`pane_seed.mux_seed_pane` can block up to minutes polling for
    readiness, which is the wrong shape for a fast, plan-only `resolve`
    call to perform synchronously). Actually delivering on this exact
    reattach needs the external launcher script itself to invoke the
    existing send-keys delivery after its own reattach, not something
    `resolve`'s single process can do from inside this phase's scope --
    **accepted as an explicit Phase 3 follow-up** (the queued seed is
    still delivered correctly on the NEXT fresh launch/attach that reaches
    it, so nothing is lost, only delayed versus the ideal "deliver on this
    exact reattach").
  - **Compatibility-fallback false match (Medium, "Previously missed" --
    pre-existing code from the second round, not yet re-reviewed until
    now):** `worktree_manager.engine_client.resolve_launch_plan`'s
    version-skew retry matched ANY error containing the substring
    `--bare-resume`, including this PR's own deliberate
    `--seed is not supported together with --bare-resume` rejection --
    silently retrying without `--bare-resume` (keeping the seed) instead
    of surfacing the rejection, exactly defeating the guard the second
    round added. Fixed by requiring `"unrecognized arguments"` in the
    error text too, mirroring the sibling `target_machine` fallback's own
    stricter check a few lines below. New test
    `test_resolve_bare_resume_plus_seed_rejection_is_not_treated_as_skew`.
  - **Stale docstring (Low):** `resolve_launch_plan`'s own seed-kwarg
    docstring still said "only meaningful with `new=True`" -- updated to
    describe `worktree_id`-resume seeding too.
  - **Review-provenance nits (Low, x2):** removed "review finding"/"review
    process" language from two test docstrings
    (`test_resolve_seed_delivery.py`), same cleanup as the third round.
  - New changefile for `worktree-manager` (the engine_client/
    relocated_launch fixes land in a different plugin than
    `agent-worktrees`).
  - Full `worktree-manager` suite (1693 tests) and the `agent-worktrees`
    keyword sweep (215 tests) both pass with no regressions.
- **2026-10-06** — A sixth Copilot review pass found real implementation
  bugs in the fifth round's own fixes (not new architectural gaps), plus
  more review-provenance nits:
  - **`_seed_already_claimed_in` scanned the WHOLE argv, not just the
    trailing pair (Medium).** A configured launch/profile argument earlier
    in `plan.cmd` could coincidentally contain the literal `--interactive`
    token, and the full scan would mistake it for the claimed seed,
    silently substituting the wrong value. `embody_resume.with_seed` is
    the sole appender and always appends its pair LAST, so fixed to check
    only `cmd[-2:]`. New unit test
    `test_seed_already_claimed_in_only_checks_the_trailing_pair` (direct,
    plus a no-seed and an empty-cmd case).
  - **Live-mux seed queuing used the wrong primitive and didn't verify
    success (Medium, "Previously missed" from the fifth round -- flagged
    once that round's own fix had landed).** `restore_pending_seed` is a
    rollback primitive that deliberately never overwrites an EXISTING
    queued seed (correct for its own restore-on-failure use case) -- using
    it here meant a genuinely new explicit seed silently lost to an older
    stale one already queued, and the fifth round's fix also printed
    "queued" unconditionally, even on a `False` (lock-contention) return
    where nothing was actually stored. Added a new, distinct primitive,
    `pending_seed.set_pending_seed` (atomic, unconditional overwrite,
    returns success), and switched the live-mux call site to it, checking
    the return value before printing success vs. an explicit "could not
    queue" message. New tests: `test_set_pending_seed_overwrites_an_existing_one`,
    `test_set_pending_seed_reports_failure_on_lock_contention`,
    `test_set_pending_seed_reports_failure_on_unreadable_record`.
  - **Review-provenance nits (Low, x2):** removed "review finding"/"review
    process" language from two more test docstrings
    (`test_engine_client.py`, `test_production_picker_transplant.py`).
  - Full re-run: `agent-worktrees` keyword sweep (218 tests, +3 from the
    new `set_pending_seed` tests) and `worktree-manager`'s
    `test_production_picker_transplant.py`/`test_engine_client.py` (130
    tests) both pass with no regressions.
- **2026-10-06** — The PR drifted behind `dev` while awaiting CI (an
  apparent GitHub-side webhook anomaly left the `pull_request`-triggered
  CI workflow run missing entirely for one push -- confirmed via a manual
  `workflow_dispatch` full-matrix run, which passed cleanly including
  `full - agent-worktrees`); `dev` had also advanced enough in the
  meantime to produce a real merge conflict. Rebased cleanly (backup
  branch taken first per `git-collaboration` convention; one trivial,
  purely-additive conflict in `test_resolve_cli_seed_guard.py`, resolved
  by keeping both sides' new tests) -- all 7 commits replayed without
  further incident; full suites re-confirmed green post-rebase.
  An eighth Copilot review pass (against the rebased branch) surfaced 3
  more new Medium findings, all fixed:
  - **A `set_pending_seed` write failure could abort the whole resume
    command.** Its own `TimeoutError` handling didn't cover a
    `tracking.save_record` failure (disk full, permissions, exhausted
    atomic-replace retries) -- that exception propagated out of the
    function entirely instead of degrading to the documented `False`
    return. Wrapped `save_record` in its own try/except; new test
    `test_set_pending_seed_reports_failure_on_write_failure`.
  - **The JSON launch path (`_resolve_json_mode` -- the Picker's REAL code
    path) had no live-mux detection at all.** Only the non-JSON
    `_resolve_resume_context` path (interactive human CLI) called
    `sessions.verify_worktree_active()`; the actually-used JSON path
    claimed/embedded a seed unconditionally, so a live-mux reattach on
    this path lost the seed ENTIRELY (not even queued) -- strictly worse
    than the already-documented "queued but delayed" limitation. Added the
    identical `verdict.mux_live` detection and `set_pending_seed` fallback
    to `_resolve_json_mode`. New tests:
    `test_live_mux_in_json_mode_queues_explicit_seed_instead_of_embedding_unused_cmd`,
    `test_seed_claimed_is_true_only_when_this_call_actually_embeds_a_seed`.
  - **The trailing-argv heuristic itself was still unsound.** Even
    checking only the trailing pair (the sixth round's own fix), a
    seedless worktree's configured/profile `copilot_args` can legitimately
    produce a `cmd` that ALSO happens to end in `--interactive <value>`
    (e.g. a profile that already supplies the flag) -- the relocated
    launcher would then forward that configured value as `--seed` AND
    separately reconstruct it itself, producing a duplicate
    `--interactive` pair. Fixed properly this time: added explicit
    `seed_claimed: bool` provenance to the JSON contract itself (`resolve
    --json`'s own `launch` payload, `LaunchPlan`'s new field, and
    `launch_plan_from_dict`'s parsing -- defaulting False for an older
    engine predating the field, never a false positive), and
    `_seed_already_claimed_in` now trusts ONLY that metadata, never
    inferring from argv shape. Rewrote
    `test_seed_already_claimed_in_only_checks_the_trailing_pair` ->
    `test_seed_already_claimed_in_trusts_only_explicit_metadata`.
  - Full re-run after all of the above: `agent-worktrees` keyword sweep
    (223 tests) and the FULL `worktree-manager` suite (1707 tests, 13
    skipped) both pass with no regressions.
- **2026-10-06** — `dev` advanced twice more while this PR awaited CI/review
  (one real conflict, resolved by rebasing with a backup branch per
  `git-collaboration` convention -- clean, no further conflicts on a
  second rebase). A ninth Copilot review pass found 3 more real
  robustness gaps, all fixed, plus 2 more review-provenance nits:
  - **A degraded (not failed) liveness probe was treated as a confirmed
    "no live mux."** `verify_worktree_active` degrades to
    `LiveVerdict(mux_live=False, probes_ok=False)` on a mux/reclaim
    hiccup -- not an exception -- so the prior `live_mux` check (`verdict
    is not None and verdict.mux_live`) missed this case entirely and could
    still claim/embed a seed the launcher then discards anyway on an
    actually-live mux. Fixed in BOTH `resolve_cli.py` (JSON path) and
    `resolve_launch_cli.py` (non-JSON path, carefully scoped so a
    deliberately-skipped check -- `--dry-run`, which never mutates
    `pending_seed` -- isn't mistaken for an uncertain one): an unconfirmed
    probe now routes to the same queue-not-embed branch as a confirmed
    live mux. New test
    `test_degraded_liveness_probe_is_treated_as_uncertain_not_confirmed_absent`.
  - **`set_pending_seed`'s own failure wasn't surfaced in the JSON
    contract.** The JSON path's live-mux branch called it without
    checking the return value, so a lock-contention/write failure while
    queuing an explicit seed produced a plain successful plan with no
    signal that the prompt never made it in. Added `seed_queue_failed:
    bool` to the `launch` payload (only present/true on an actual
    failure) -- an honest, non-fatal degradation: the launch itself still
    proceeds (losing only the seed), but the caller can now tell. New
    test `test_live_mux_seed_queue_failure_is_surfaced_not_silently_reported_success`.
  - **Review-provenance nits (Low, x2):** removed the last two "review
    finding" references from a source comment (`resolve_cli.py`) and a
    test docstring (`test_resolve_seed_delivery.py`).
  - **Accepted, narrower Phase-3-adjacent scope (not fixed this round):**
    two related findings -- `_RecordLock.__enter__`'s own sidecar-creation
    `OSError`/`PermissionError` escaping past `pending_seed`'s documented
    "degrade to False" contract, and `claim_pending_seed`'s return value
    being unable to distinguish "nothing was pending" from "claim failed
    on lock contention/an unreadable record" (so an explicit seed can
    proceed while an old, undelivered `pending_seed` silently survives to
    be injected on a LATER resume as a stale, unexpected turn) -- are
    real, but address a pre-existing lock primitive's own failure-mode
    granularity (`_RecordLock`) and `claim_pending_seed`'s long-standing
    two-state return contract (shared by every existing caller across the
    codebase, not something introduced by this effort). Properly fixing
    the latter needs a richer return type (or an out-parameter) threaded
    through every call site, which is real design surgery rather than a
    narrow, scoped Phase 1 fix -- noted here for a future pass rather than
    rushed in this round.
  - Full re-run: `agent-worktrees` keyword sweep (225 tests) passes with
    no regressions (confirmed sufficient; the full `worktree-manager`
    suite was already re-confirmed clean the prior round and this round's
    changes don't touch that plugin).
- **2026-10-06** — Rebased twice more onto two further `dev` advances
  (clean both times, no conflicts) while the PR awaited CI/review; the
  previously-flaky `agent-worktrees (collect-only + guard tests)` job
  passed cleanly this time, and every CI check -- including `PR gate
  (required check)` -- is now green. A tenth Copilot review pass resolved
  4 more findings and surfaced 2 final new Medium ones, both fixed:
  - **The aggregate `probes_ok` flag conflated mux-probe failure with an
    unrelated reclaim/lock-probe failure.** `LiveVerdict.probes_ok` goes
    False when EITHER probe raised, but only the mux probe's own
    success/failure matters for "might a live mux exist and reattach
    instead of exec'ing a fresh command" -- a reclaim failure alongside a
    mux probe that conclusively found NO mux was being treated as
    uncertain too, wrongly routing a confirmed-no-mux resume into the
    queue-not-embed branch. For a `--no-mux` launch specifically this
    loses the prompt entirely (the launcher execs the seedless command
    directly; there is no pane to later deliver a queued seed to). Added
    a narrower `LiveVerdict.mux_probe_ok` field (true unless the MUX probe
    itself raised) and switched both `resolve_cli.py` and
    `resolve_launch_cli.py`'s uncertainty checks to it instead of the
    aggregate. New tests:
    `test_degraded_mux_probe_is_treated_as_uncertain_not_confirmed_absent`
    (renamed from the ninth round's test, now exercising the narrower
    field), `test_reclaim_only_probe_failure_does_not_block_a_confirmed_no_mux_resume`,
    plus updated assertions in `test_verify_worktree_active.py`'s two
    existing degrade-path tests.
  - This round's own `PR gate` pass, with every job green (including a
    now-passing `agent-worktrees (collect-only + guard tests)`), together
    with ten rounds of consistently advisory (never `REQUEST_CHANGES`)
    review verdicts, is treated as the natural landing point per this
    repo's own `commented-review-verdict` fallback policy: every
    Medium/High finding across all ten rounds is now either fixed (with a
    regression test) or an explicitly documented, narrowly-scoped Phase 3
    follow-up (the four items in Phase 3's own plan above) -- continuing
    to chase incrementally narrower edge cases in the same probe-
    classification area past this point would be diminishing returns, not
    due diligence. Proceeding to merge.
  - Full re-run: `agent-worktrees` full keyword sweep including
    `sessions`/`verify_worktree_active` (430 tests) passes with no
    regressions.
- **2026-10-06** — Operator question on resume, worth recording verbatim
  since it's the right question to ask after watching this effort's own
  complexity grow across ten review rounds: *"I'm worried that the seed
  is being tracked too durably. Is `--interactive` not reliable?"*

  Answer: **`--interactive` itself is fully reliable** -- live-verified
  back in Phase 1 (a single `copilot --resume=<id> --interactive
  "<prompt>"` process resumes full history AND auto-executes the prompt
  as the next turn, no caveats). The complexity this effort accumulated
  is NOT compensating for that flag's own unreliability; it exists
  because **multiple code paths can discard the planned command before
  ever executing it with that flag attached**:
  1. A live-mux reattach: the launcher scripts detect an existing pane and
     just reattach to it, NEVER exec'ing the freshly resolved `cmd` at
     all (not a bug -- correct behavior, since spawning a second Copilot
     process into an already-live worktree would fork the conversation).
  2. A delegated/relocated launch: the Worktree Manager re-resolves its
     own plan a second time inside the launcher script, discarding the
     first plan entirely.
  3. A pre-exec launcher failure: the script's own update/preflight work
     can fail before ever reaching `cmd`.

  `pending_seed` persistence is the necessary fallback for exactly these
  cases -- not redundant scaffolding layered on top of an unreliable
  primitive, but the other half of a two-part contract: `resolve` embeds
  the seed in argv when it's confident its own `cmd` will actually run,
  and otherwise durably queues it for whichever mechanism DOES end up
  running. The real, now-closed gap (this session's own work, see above)
  was that the QUEUED half of that contract had a missing link: nothing
  ever told the live-mux-reattach branch to actually go check the queue
  and deliver it -- it only happened to get delivered on a LATER,
  different, fresh-launch attempt that still had the queue to re-check.
  Confirmed and fixed directly at the root: `launch-session.{ps1,sh}`'s
  "join existing session" branch now calls the exact same
  `Invoke-SeedDeliverySafe`/`_aw_deliver_pending_seed` helper the
  fresh-session branch already called -- one new call to pre-existing
  code, not a new mechanism. This was, in fact, the deepest of the three
  Phase-3 items the prior round deferred (the two remaining are narrower
  defensive-programming cases: a pre-existing lock primitive's own
  failure-mode granularity, and `claim_pending_seed`'s long-standing
  ambiguous return contract -- neither is "seed gets silently lost in
  ordinary operation," which is what `--interactive`'s own reliability
  question was really asking about).

  New worktree created for this slice (the Phase 1 worktree was already
  finalized/merged). All 34 `launch_session`/
  `launch_passthrough`/`launch_scripts`-keyword tests pass (including the
  rewritten drift guard), and the full 1707-test `worktree-manager` suite
  passes except 4 nondeterministic failures in `test_mux_daemon.py`/
  `test_mux_daemon_cutover_helper.py` -- confirmed unrelated (a different
  subsystem entirely, work-coalescing-singleton daemon IPC/file-rename
  races, reproduced with DIFFERENT failure signatures across repeated
  reruns with no code changes between them -- this machine's own known
  resource-contention flakiness, not a regression from this slice's
  2-line-per-script change).
