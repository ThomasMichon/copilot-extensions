# Worktrees Pivot UX Overhaul

- **Slug:** `worktrees-pivot-ux-overhaul`
- **Repo:** copilot-extensions (control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-22
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** vision-closing / vision-extending against:
  - [`visions/picker`](../../../visions/picker/README.md) — general Picker
    render/derive contract this pivot must keep honoring.
  - [`visions/venue-pivots-ux`](../../../visions/venue-pivots-ux/README.md) —
    §*claims-pecking-order* explicitly states "every pivot showing a
    claims-list (Worktrees, Tasks, Codespaces, ...) picks it up identically";
    this effort is the **Worktrees-side** half of that statement (the
    Codespaces/Containers half shipped via `picker-venue-pivots`).
  - `visions/mux-companion` (Ctrl-K companion) — v1 (view-only) already
    implemented (`worktree-manager/src/worktree_manager/mux_companion.py`);
    this effort's Phase 8 continues it per its own stated Non-Goals gating
    (break-glass head override, split-screen sub-agents, etc. are explicitly
    *not* v1 and must not be folded in silently).
- **Umbrella issue:** [#3307](https://github.com/ThomasMichon/copilot-extensions/issues/3307)
- **Sub-issues:** filed per-phase as each phase is scoped for execution
  (see Plan; none filed yet — this is the reviewed-plan stage).

## Guiding Intent

The **Worktrees pivot** — the Picker's original, primary pivot, listing an
operator's agent-worktrees-managed worktrees — has fallen behind sibling
pivots and the harness's own accumulated capability in several concrete,
independently-actionable ways: it orders "recent" work by the wrong signal,
carries a column ("R") whose meaning no longer holds up, doesn't yet surface
the claims-pecking-order module already built for other pivots, doesn't show
session/turn counts the way a `LIVE` row needs to, has no guardrail when a
worktree's last-session and handoff-head disagree, has no dedicated way to
browse a worktree's full session history, and has no golden-screenshot
regression harness comparing today's render against the picker's original
Textual-era screenshots. This effort collects those into one coherently
sequenced campaign, explicitly bounded to the Worktrees pivot itself (not a
re-litigation of the broader Picker/Manager extraction, which
`worktree-manager-control-plane` already owns).

## Context

### Triage of prior art (2026-09-22)

A sweep across `gim-home/odsp-web-harness`, `tmichon_microsoft/dotfiles`
(the bound knowledge repo's issue tracker), and `copilot-extensions` itself
found substantial existing groundwork. Nothing below needs to be rebuilt;
this effort's job is to **finish, adopt, or extend** it for the Worktrees
pivot specifically.

### Directly reusable / already-built primitives

- **Claims pecking order — already implemented, not yet Worktrees-adopted.**
  `plugins/agent-worktrees/src/agent_worktrees/claims_rank.py`
  (`rank_claims`/`format_claim`/`summarize_claims`, pure/unit-tested) plus
  `claims_cli.py` (`claims add <kind> <ref>`, valid kinds already include
  `pr`, `codespace`, `container`) is the **shared, single-sourced** module the
  `venue-pivots-ux` vision states every claims-showing pivot must use
  identically. `picker-venue-pivots` (copilot-extensions#3253, merged through
  Phase 5) already wired it into the Codespaces and Containers pivots. The
  **Worktrees pivot's own PRs/claims section does not yet read through this
  module** — that gap is this effort's Phase 4, not new design.
- **Mux Companion (Ctrl-K) — v1 already shipped.**
  `worktree-manager/src/worktree_manager/mux_companion.py` implements a
  read-only, hotkey-summoned companion resolving the current worktree via
  `status-segment --json`, explaining status in plain language, and listing
  session lineage with the head marked. Its own module docstring is explicit
  that split-screen sub-agents, session switching, and head override are
  **out of v1** and must not be folded in silently
  (`visions/mux-companion §Non-Goals`). This effort's Phase 8 is the next
  slice, not a fresh build.
- **Golden-screenshot capture — already exists, one known crash.**
  `worktree-manager/src/worktree_manager/production_picker/picker_tui/capture.py`
  and `worktree-manager/scripts/picker-shot.py` /
  `scripts/preview-picker.ps1`/`.sh` already produce headless SVG captures
  against injected fixture/demo data (mock-data-backed, exactly per the
  wishlist). The blocker is a **known, already-triaged bug**:
  `gim-home/odsp-web-harness#265` / `tmichon_microsoft/dotfiles#2120`
  ("Setup diagnostics: local Worktree Manager screenshot command crashes")
  — an active but not-yet-designed bug-triage effort exists at
  `<knowledge-repo>/efforts/active/odsp-web-harness/setup-diagnostics-worktree-screenshot/README.md`.
  This effort's Phase 1 fixes that crash as a prerequisite, then captures the
  actual comparison baseline.
- **Accelerator (fast claims/status reads) — already built and deployed.**
  `agent-worktrees-external-status-accelerator` (this repo, Phases 1–7 all
  DONE 2026-09-20/21) delivered the resident daemon + cache the wishlist's
  "accelerator mini-db" describes. `visions/plugins/agent-worktrees` already
  documents it (§*resident accelerator* / "warmth, not truth"). This effort
  **consumes** that accelerator for Phase 4/6 reads; it does not build it.

### Related, in-progress efforts to coordinate with (not merge into)

- **`picker-venue-pivots`** (copilot-extensions#3253) — the direct sibling.
  Phases 0–5 done; the shared claims-pecking-order module and the
  driving-worktree cross-link both originate there. Coordinate on any
  `claims_rank.py` change; do not fork a second claims-rendering path.
- **`worktree-manager-control-plane`** (copilot-extensions#352) — the parent
  campaign for the out-of-plugin Manager/Picker extraction itself (installer,
  configurator, Mux/AHP relocation). This effort's Worktrees-pivot changes
  must build on top of whatever `production_picker` shape that effort has
  landed at the time (currently Phase 3 "extracted Picker over the engine
  boundary", in progress) — it does not re-decide that architecture.
- **`agent-worktrees-external-status-accelerator`** Follow-ups section
  (captured, unscheduled) — an audit/verb-consolidation idea and a Defender-
  exclusion idea. Neither blocks this effort; noted only in case Phase 6's
  read path benefits from the audit-verb consolidation.
- **`worktree-state-live-db`** (dotfiles-tracked, copilot-extensions#229) —
  currently redirected from "embedded DB" to "record decomposition (cells) +
  forward-only journal + deterministic head/status derivation." That
  journal/derivation work is the most direct fix for the **root cause**
  behind Phase 7's last-session/handoff-head mismatch warning (dotfiles#1298).
  This effort's Phase 7 should build the **surfaced warning**; if the
  underlying journal/derivation lands first, Phase 7 becomes thinner (read
  the derived state) rather than needing its own reconciliation logic.
- **`consolidated-status-daemon`** (dotfiles-tracked, copilot-extensions#918)
  — Phase 1 (cutover reap+restart) and Phase 4 Slice 1 (list cache) are done;
  Phases 2, 3, 4-Slice-2, and 5 (liveness roots, full catalog reconciliation,
  cache warming, orphan-pane reaper) are still open. Largely superseded in
  spirit by the external-status accelerator for the caching concern; the
  orphan-pane-reaper and record-reconciliation phases remain a distinct,
  unclaimed gap this effort does **not** take on.
- **`make-bulk-worktree-deletion-clear-and-safe`**
  (gim-home/odsp-web-harness#430 / dotfiles#2158) — has its own design sketch
  (multi-select bulk delete + force-flag plumbing + preflight grouping by
  `interpret_descriptor_payload` outcome). Touches the same Worktrees-pivot
  screen but is a distinct capability (destructive bulk action UX, not
  presentation/columns/ordering). Left alone; cross-link only.
- **`gim-home/odsp-web-harness#1998`** ("Worktree Manager: Add
  cross-repository authoritative-agent picker") and **`#181`** ("Textual
  picker keyboard-dead over Windows OpenSSH") — tangential Worktrees-pivot
  bugs/asks unrelated to this effort's scope (input platform bug; a
  different picker altogether). Noted, not adopted.
- **`native-construct-convergence`** (copilot-extensions#985) — a **false
  cognate**, not a duplicate: its "native" means *converging onto the
  Copilot CLI's own native worktree/session constructs* (layout, roots,
  session identity, cloud steering), unrelated to this effort's Phase 2
  question of whether the Worktrees pivot's own **table widget** should be a
  native Textual `DataTable`/`OptionList` rather than a hand-rolled
  `engine_views.py` renderer. No overlap; both may proceed independently.
- **dotfiles#494** ("Worktree Manager misses pending-input hourglass for
  rest-only sessions"), **#1205** ("mux_live never refreshed by periodic
  sweep") — `LIVE`-state freshness bugs that Phase 6's SESS/TURNS work will
  likely touch the same rendering path as; cross-linked, fixed opportunistically
  if in-path, not separately scheduled.

### Net-new — no prior art found

A repo-wide sweep (dotfiles issues, copilot-extensions issues/efforts,
harness issues) found **no** existing tracked work for: the "R" column's
confusing semantics, Recent-section sort-by-most-recently-used (vs.
newest-to-oldest by a status-transition timestamp), or a dedicated
"Sessions" sub-menu per worktree. These are addressed fresh in Phases 3, 5,
and 7 below.

## Request

> Let's start an effort to make improvements to the Worktrees pivot of
> Worktree Manager. [...] Wishlist items:
> 1. Captured, mock-data-backed renders as guiding screenshots for visual
>    regression, compared against the original Textual-picker-era screenshots.
> 2. The Worktrees table isn't using a native Textual component; evaluate
>    "native" components for benefit.
> 3. Fix presentation order: ACTIVE always first, but Recent must sort by
>    most-recently-used, not "newest to oldest" — a long-running worktree
>    that I just used shouldn't sink to the bottom because Copilot happened
>    to exit at a clean cutoff.
> 4. Show all claims in the "PRs" section, standardizing the final top-line
>    column as "CLAIMS" across all pivots, per the claims pecking-order
>    vision and the accelerator mini-db.
> 5. The "R" column makes no sense; represent the idea better.
> 6. `LIVE` needs the session counter too, or a combined SESS/TURNS column
>    (how many sessions a worktree has had; what turn the current session
>    is on).
> 7. Warn when "last session id" and "handoff head session" disagree; add a
>    "Sessions" sub-menu to see all sessions in a worktree.
> 8. Continue building the Mux-backed Companion dialog (Ctrl-K): split-screen
>    into sub-agents, handoff tracking, extended status reporting, and more.
>
> "I'll think of more. But let's collate known issues and efforts, dedupe
> and reconcile with these ideas, and get a grand effort going with this all
> laid out."

## Plan

Phases are ordered by dependency where one exists (Phase 1 unblocks
before/after comparisons for every later phase; Phase 4 needs the
accelerator, which is already done); otherwise independent and
parallelizable across worktrees.

### Phase 1 — Golden-screenshot baseline for visual regression
- [x] Fix the screenshot-command crash blocking `picker-shot.py` /
      `preview-picker.ps1`/`.sh` / `worktree-manager picker screenshot`.
      **Filed as [copilot-extensions#3319](https://github.com/ThomasMichon/copilot-extensions/issues/3319)**
      (2026-09-22, closed 2026-09-23): the originally-reported crash
      (gim-home/odsp-web-harness#265 / dotfiles#2120) traced to
      `picker_tui/engine.py`, which no longer exists (retired with the
      bundled Picker per `worktree-manager-control-plane` Phase 6). Live
      reproduction found a **different, current** root cause instead: the
      #3309/#3313 lazy-dispatch work deferred `agent_worktrees.__main__`'s
      cross-module globals (e.g. `_in_ssh_session`) behind
      `_load_full_command_surface()`, and its regression scans covered
      only in-repo `agent_worktrees` call shapes — not
      `worktree-manager/production_picker/runner.py::_prepare()`'s direct
      `engine_module("__main__")` import, which crashed with
      `AttributeError: module 'agent_worktrees.__main__' has no attribute
      '_in_ssh_session'` on every invocation. Fixed upstream; verified
      `worktree-manager picker screenshot --demo` now captures cleanly.
      Both stale issues cross-linked to #3319.
- [ ] **Blocked on two more findings from verifying the #3319 fix
      (2026-09-23):**
      - [copilot-extensions#3413](https://github.com/ThomasMichon/copilot-extensions/issues/3413)
        — `runner.capture()` (the real, non-demo production-Picker headless
        capture path) has **no mock-data option**; only `live` (SSH) or
        local-real (`data_local`) data. `--demo` *does* produce a working
        mock-data-backed capture, but it renders a different, stale, legacy
        app (`picker_app.WorktreeManagerApp`, last touched 2026-09-10 vs.
        `production_picker`'s daily churn) — **not** the actual current
        Worktrees pivot. There is currently no way to headlessly capture
        the real pivot against deterministic mock data.
      - [copilot-extensions#3418](https://github.com/ThomasMichon/copilot-extensions/issues/3418)
        — chasing why non-demo capture also *hangs* (not just lacks mock
        data) led to the real, generic root cause: `agent-worktrees list
        --classify` itself (which any real capture ultimately shells out
        to) does an unbounded, serial, per-related-repo-anchor `git`
        subprocess walk (`_control_plane_related_pr_map`) that can take
        minutes on a machine with a large/partially-unreachable related-repo
        topology — confirmed via an all-threads `faulthandler` dump, not
        guessed. Unrelated to the Picker/worktree-manager at all; narrows
        and supersedes the initial (incorrect) theory in #3412, which is
        cross-linked and left open for the responsible agent to triage.
      Phase 1's actual baseline capture is on hold until #3413 and/or #3418
      land — whichever unblocks a real, representative capture first (a
      mock-data path for `production_picker`, or a fast/bounded
      `list --classify`).
- [ ] Capture a current, mock-data-backed set of Worktrees-pivot renders
      (the existing `capture.py` injected-source path) across representative
      states (empty, ACTIVE-only, mixed ACTIVE+Recent+unused, claims present,
      long-running worktree).
- [ ] Locate the original Textual-picker-era screenshots (the ones taken
      "when creating the Textual picker in the first place" — check
      `worktree-manager/scripts/picker-snapshot/venue-preview/` history and
      any linked PR/issue attachments) and produce a side-by-side comparison
      documenting the intentional transformation since.
- [ ] Wire the captured baseline into a checked-in golden-comparison step
      (or confirm one already exists via `worktree-manager-control-plane`
      Phase 3's "headless SVG capture for golden checks" and extend it) so
      every later phase in this effort diffs against it before landing.

### Phase 2 — Evaluate native Textual components for the Worktrees table
- [ ] Audit `production_picker/picker_tui/engine_views.py`'s hand-rolled
      Worktrees-table rendering against Textual's native `DataTable` (used
      today only in `mux_companion.py`) and `OptionList`.
- [ ] Name concrete benefits/costs (built-in selection, sorting, scrolling,
      accessibility, theming vs. the current column-declarative
      `pivot_manifest.py` model this pivot and its siblings share).
- [ ] Decide: migrate, partially adopt, or explicitly keep custom with the
      documented rationale recorded in this effort (not silently dropped).

### Phase 3 — Fix Recent-section sort order (most-recently-used, not newest first)
- [ ] Identify the current sort key driving the Recent section (a
      status-transition/creation timestamp) vs. the correct key (last
      session activity / last resumed timestamp, sourced via the
      accelerator).
- [ ] Re-sort Recent by most-recently-used while keeping ACTIVE always
      pinned first.
- [ ] Cross-link dotfiles#1016 (indented sub-row nesting for paired
      worktrees) if the same section-ordering code path is touched.

### Phase 4 — CLAIMS column: adopt the shared pecking-order module
- [ ] Wire the Worktrees pivot's PRs/claims section through
      `agent_worktrees.claims_rank` / `claims_cli`, matching the
      Codespaces/Containers pivots' already-shipped presentation
      (`picker-venue-pivots`).
- [ ] Rename/standardize the final top-line column label to `CLAIMS` on the
      Worktrees pivot, consistent with the other pivots.
- [ ] Read through the accelerator's cached claims graph (no independent
      per-render claim scan), per the vision's *warmth, not truth* rule.

### Phase 5 — Replace the "R" column
- [ ] Determine the "R" column's current source field (expected:
      `resume_count`) and why it reads as meaningless in practice.
- [ ] Decide its replacement — likely folded into Phase 6's SESS/TURNS
      column rather than kept standalone; record the decision and rationale.

### Phase 6 — SESS/TURNS column on LIVE rows
- [ ] Add a combined `SESS/TURNS` (or equivalent) column: total session
      count for the worktree, and the current session's turn number.
- [ ] Fix delegate/child worktrees incorrectly showing 0 turns
      (dotfiles#458) as part of this column's data path, since it is the
      same undercount this column would otherwise inherit.

### Phase 7 — Session/handoff-head mismatch warning + Sessions sub-menu
- [ ] Surface a visible warning when a worktree's recorded "last session id"
      and "handoff head session" disagree (root-cause context:
      dotfiles#1298, `worktree-state-live-db`'s journal/deterministic-head
      direction — reuse its derived state if landed first; otherwise
      implement the comparison directly against current fields).
- [ ] Add a "Sessions" sub-menu/dialog listing all sessions recorded against
      a worktree (id, started/ended, turn count, head marker).

### Phase 8 — Continue the Mux Companion (Ctrl-K) buildout
- [ ] Scope and land the next slice(s) beyond v1's view-only status/lineage
      display: candidates raised for exploration — split-screen into
      sub-agents, handoff tracking, extended status reporting. Each new
      capability gets its own Non-Goals-respecting sub-slice (per
      `visions/mux-companion`), not a single undifferentiated dump.
- [ ] Record which candidate(s) are accepted vs. deferred, with rationale.

### Phase 9 — Reconcile deferred backlog
- [ ] Fold in further wishlist items raised after this effort's initial
      review (the operator flagged "I'll think of more").

## Validation Plan

- [ ] Golden-screenshot diff run before/after every phase in this effort
      (Phase 1's harness), with intentional deltas called out explicitly in
      the phase's own PR description.
- [ ] Existing Worktrees-pivot unit/interaction tests continue to pass;
      each phase adds targeted coverage (sort order, claims rendering,
      column content, mismatch-warning trigger, sub-menu contents).
- [ ] Manual verification against a real multi-worktree mesh state (mix of
      ACTIVE/Recent/unused, at least one long-running worktree, at least one
      worktree with a claimed PR) confirming ordering, CLAIMS column, and
      SESS/TURNS values match ground truth from `agent-worktrees list --json`.
- [ ] No regression in the accelerator's *warmth, not truth* contract — no
      new per-render subprocess/git/claims scan introduced by any phase.

## Proposal

_Pending review. This effort's plan has not yet been executed — filed as a
reviewed-plan PR per the standard effort review gate before Phase 1 begins._

## Journal

### 2026-09-22 — Kickoff: triage sweep + reviewed plan drafted
- Swept `gim-home/odsp-web-harness`, `tmichon_microsoft/dotfiles` (bound
  knowledge repo), and `copilot-extensions` itself for existing issues and
  efforts touching the Worktrees pivot / Textual picker.
- Found and cited the directly reusable prior art: the shipped claims
  pecking-order module + its Codespaces/Containers adoption
  (`picker-venue-pivots`), the shipped external-status accelerator, the v1
  Mux Companion, and the existing (currently crashing) golden-screenshot
  capture harness.
- Identified related-but-out-of-scope efforts to leave alone: bulk-delete
  safety UX, the OpenSSH keyboard-dead bug, the cross-repo
  authoritative-agent-picker ask, and `native-construct-convergence` (a
  same-word-different-meaning non-duplicate).
- Confirmed no prior tracked work exists for the "R" column, recency-sort,
  or a Sessions sub-menu — these are net-new in this effort.
- Filed umbrella issue #3307. Effort authored; plan not yet executed pending
  review.

### 2026-09-22 — Phase 1 investigation: filed the live screenshot-crash bug
- Reproduced the screenshot-capture crash live (both via the deployed
  `worktree-manager` binstub and directly against this repo checkout).
  Confirmed the originally-cited traceback (harness#265/dotfiles#2120) is
  stale — the code it references was retired — and found the current,
  reproducible root cause: a lazy-dispatch coverage gap from #3309/#3313
  that the operator asked to route to the agent responsible for that work
  rather than fix here.
- Filed [copilot-extensions#3319](https://github.com/ThomasMichon/copilot-extensions/issues/3319)
  with the precise root cause and expected fix seam. Cross-linked from
  harness#265 and dotfiles#2120.
- Phase 1 remains blocked on #3319 landing before the actual golden
  captures can be taken. No code changes made in this slice.

### 2026-09-23 — Verified #3319's fix; found two more real gaps blocking capture
- Confirmed #3319 closed/fixed: `worktree-manager picker screenshot --demo`
  now captures cleanly (previously crashed with the `_in_ssh_session`
  `AttributeError`).
- Tried the *real* (non-demo) capture path next, per the wishlist's actual
  ask (a representative capture of the real Worktrees pivot, not a
  stand-in). Found it hangs. Used `faulthandler.dump_traceback(all_threads=
  True)` (not guesswork) to trace the hang to its true root: an unbounded,
  serial per-related-repo-anchor `git rev-parse` walk inside
  `agent-worktrees list --classify` itself
  (`_control_plane_related_pr_map`), unrelated to the Picker/worktree-manager
  at all. Filed precisely as
  [#3418](https://github.com/ThomasMichon/copilot-extensions/issues/3418).
- Separately, confirmed `--demo` mode's mock-data capture renders a
  different, stale legacy app (`picker_app.py`) rather than the actual
  shipped `production_picker`, and that `production_picker`'s own capture
  path has no mock-data option at all. Filed as
  [#3413](https://github.com/ThomasMichon/copilot-extensions/issues/3413).
- Corrected an earlier over-eager theory: initially filed
  [#3412](https://github.com/ThomasMichon/copilot-extensions/issues/3412)
  guessing the hang was Picker-specific (a `data_local`/mount-time blocking
  call); the deeper trace in #3418 disproved that and #3412 was narrowed/
  cross-linked accordingly rather than left stale.
- Phase 1 remains blocked — now on #3413 and/or #3418 — before a real,
  representative golden baseline can be captured. No functional code
  changes made in this slice; investigation and filing only.
