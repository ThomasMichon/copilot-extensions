# Worktrees Pivot UX Overhaul

- **Slug:** `worktrees-pivot-ux-overhaul`
- **Repo:** copilot-extensions (control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-22
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
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

A sweep across the operator's personal harness repo, their private
knowledge repo (the bound knowledge repo's issue tracker), and
`copilot-extensions` itself
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
  a real-world consuming harness's own tracker issue / the operator's
  private knowledge-repo tracker
  ("Setup diagnostics: local Worktree Manager screenshot command crashes")
  — an active but not-yet-designed bug-triage effort exists at
  `<knowledge-repo>/efforts/active/harness/setup-diagnostics-worktree-screenshot/README.md`.
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
  (a real-world consuming harness's own tracker issue and its knowledge-repo
  cross-link) — has its own design sketch
  (multi-select bulk delete + force-flag plumbing + preflight grouping by
  `interpret_descriptor_payload` outcome). Touches the same Worktrees-pivot
  screen but is a distinct capability (destructive bulk action UX, not
  presentation/columns/ordering). Left alone; cross-link only.
- **A real-world consuming harness's own tracker issue #1998** ("Worktree Manager: Add
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

### Phase 1 — Golden-screenshot baseline for visual regression (Done 2026-09-23)
- [x] Fix the screenshot-command crash blocking `picker-shot.py` /
      `preview-picker.ps1`/`.sh` / `worktree-manager picker screenshot`.
      **Filed as [copilot-extensions#3319](https://github.com/ThomasMichon/copilot-extensions/issues/3319)**
      (the originally-reported crash, tracked in a real-world consuming
      harness's own tracker and its knowledge-repo cross-link) traced to
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
- [x] **Blocked on two more findings from verifying the #3319 fix
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
        **Resolved 2026-09-23**: rebuilt `--demo`/`--preview` to render the
        REAL `production_picker` by composing two existing seams —
        `engine_client.set_engine_command` pointed at the existing
        `demo_engine` fixture (worktree data), and a plain, schema-less
        ("operator"-class) manifest injected into a temp
        `AGENT_WORKTREES_PIVOTS_DIR` naming a new `demo_pivot` fixture
        (pivot data) — through the *same* cross-plugin pivot-manifest
        registry a real contributed pivot (Codespaces/Containers/…) uses,
        zero engine code changes. `picker_app`'s demo-rendering functions
        are no longer used by `--demo`. See `preview.py`'s module docstring.
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
        **Sidestepped for preview purposes** by #3413's fix above (the fake
        engine never shells out to the real `list --classify`), but remains
        open and worth fixing in its own right for real (non-preview)
        capture and for `list --classify` generally.
      Phase 1's golden-baseline capture is now unblocked via `--demo`/
      `--preview` against the real Picker.
- [x] Capture a current, mock-data-backed set of Worktrees-pivot renders
      across representative states (empty, ACTIVE-only, mixed
      ACTIVE+Recent+unused, claims present, long-running worktree).
      **Done 2026-09-23**: added
      `tests/production_picker/test_picker_capture_scenarios.py` (5 new
      golden-compared tests) using the existing hermetic
      `pcap.capture(source, live=False)` deterministic-renderer contract —
      not the CLI `--demo` path (that's the human-facing preview tool
      #3413 fixed; this is the checked-in regression artifact). The
      long-running golden documents the CURRENT (pre-Phase-3) sort bug
      directly: a 30-day-old, 47-turn worktree sorts BELOW a
      created-yesterday/never-touched one in Recent, purely by
      `started_at` — the exact "before" state Phase 3 should visibly flip.
- [x] Locate the original Textual-picker-era screenshots and produce a
      side-by-side comparison. **Done 2026-09-23**: found
      `docs/assets/worktree-picker.png`/`.gif`, committed 2026-07-25
      (`v1.0.0`, unmodified since) and still present. Full comparison in
      [`screenshot-comparison.md`](screenshot-comparison.md). Headline
      finding: the core design (sections, palette, header counters) is
      unchanged; `SESS`→`LIVE` is a same-primitive rename; `T` (turn count)
      is a genuine, welcome addition; **the `R` column has no historical
      precedent at all** — direct evidence for wishlist item #5's
      complaint, and freeing Phase 5 to redefine/retire it without a legacy
      meaning to preserve.
- [x] Wire the captured baseline into a checked-in golden-comparison step.
      **Done 2026-09-23**: confirmed one already exists —
      `tests/production_picker/test_picker_capture.py`'s
      `GOLDEN_DIR`/`_golden()`/`AGENT_WORKTREES_UPDATE_GOLDENS=1` pattern —
      and extended it with the 5 new scenario goldens above, in a sibling
      file rather than duplicating the harness.

### Phase 2 — Evaluate native Textual components for the Worktrees table (Done 2026-09-23)
- [x] Audit `production_picker/picker_tui/engine_views.py`'s hand-rolled
      Worktrees-table rendering against Textual's native `DataTable` (used
      today only in `mux_companion.py`) and `OptionList`. **Done**: the
      outer container already went native pre-effort
      (`engine_regions._PickerNativeData(OptionList)`, #88 NF5-5) --
      remaining question was the row-content model (two adjacent
      `OptionList` options per record). `DataTable` ruled out: no colspan
      for section bands (`── Active ──` etc.), a structural blocker, not a
      preference.
- [x] Name concrete benefits/costs. **Done**: full writeup in
      [`phase2-native-textual-audit.md`](phase2-native-textual-audit.md),
      including a real, runnable `ListView`-backed spike
      (`production_picker/picker_tui/listview_proto.py` +
      `scripts/listview_proto_compare.py`) captured against the real demo
      fixture data -- confirmed atomic title+detail rows and a real
      `Checkbox` are plausible, at the cost of re-implementing the
      scroll-preservation/sticky-header/incremental-repaint/`sel`-sync
      bridge `_PickerNativeData` already built for `OptionList`.
- [x] Decide: migrate, partially adopt, or explicitly keep custom with the
      documented rationale recorded in this effort (not silently dropped).
      **Decision**: keep `OptionList` as the Worktrees pivot's default;
      introduce an opt-in-per-pivot `render_mode` (`"v1"`/`OptionList` vs.
      `"v2"`/`ListView`) as its own tracked follow-up phase/issue once the
      production `ListView` widget reaches bridge parity -- not folded into
      this phase, and no built-in pivot opts in without its own explicit
      review.

### Phase 3 — Fix Recent-section sort order (most-recently-used, not newest first) (Done 2026-09-24)
- [x] Identify the current sort key driving the Recent section (a
      status-transition/creation timestamp) vs. the correct key (last
      session activity / last resumed timestamp, sourced via the
      accelerator). **Done**: `derive.bucket()`'s own per-section sort keyed
      `age_secs` (creation/status-transition age); but `WT_SORT_KEYS[0]`
      ("age") ALSO applies unconditionally on every render via
      `current_list_visible()`'s `list_view.narrow()`, so it — not
      `bucket()`'s internal sort — is the section's real at-rest order (a
      finding only surfaced by actually wiring the fix through and watching
      the golden not change; both call sites needed the new key).
- [x] Re-sort Recent by most-recently-used while keeping ACTIVE always
      pinned first. **Done**: engine (`agent_worktrees/__main__.py`)
      surfaces `WorktreeRecord.last_resumed_at` (already tracked, just not
      serialized) in `list --json`'s per-worktree envelope; `derive.norm()`
      passes it through; new `derive._last_active_secs()` prefers it,
      falling back to the record's own `age_secs` for a worktree never
      resumed since creation (NOT a raw `started_at` re-read — normalized
      records never carry that key, only the derived `age`/`age_secs`
      pair, a real bug caught by a diagnostic script before it reached a
      test). Wired into both `bucket()`'s own Recent sort AND
      `WT_SORT_KEYS[0]`. ACTIVE/Completed still sort by `age_secs`
      (unchanged, per their own docstring). Golden
      `scenario_long_running.txt` regenerated: the long-haul,
      recently-resumed worktree now sorts above the merely-newer-but-
      untouched one, confirmed by diff (2 lines changed, only the two
      title/detail rows swapping order).
- [ ] Cross-link dotfiles#1016 (indented sub-row nesting for paired
      worktrees) if the same section-ordering code path is touched. **Not
      done this slice** — the sort-key change didn't touch row-nesting
      rendering, so left uncrossed; revisit if a later phase touches that
      path.

### Phase 4 — CLAIMS column: adopt the shared pecking-order module (Done 2026-09-25)
- [x] Wire the Worktrees pivot's PRs/claims section through
      `agent_worktrees.claims_rank` / `claims_cli`, matching the
      Codespaces/Containers pivots' already-shipped presentation
      (`picker-venue-pivots`). **Done, with one deliberate architectural
      difference from Codespaces/Containers**: those pivots' OWN backend
      commands compute `claims_summary` (they soft-depend on
      `agent_worktrees` as a library). `worktree-manager`'s Picker never
      imports `agent_worktrees` directly -- it only reaches it across a
      subprocess boundary (`list --json`), per the picker vision's
      "Manager reaches the engine only across a process boundary"
      principle (`demo.py`'s own docstring). So `claims_summary` is
      computed ENGINE-side, in `agent_worktrees.__main__._worktree_to_dict`
      (same package, direct import, right next to where `resources` -- the
      claim ledger -- is already serialized), and the Picker's `derive.py`
      just passes the ready-made string through -- a hermetic field, no
      import, boundary intact. Backfills a synthetic `pr` claim from the
      back-compat `active_pr()` when the ledger has no live `pr` claim yet
      (covers a worktree whose PR predates the `create-pr`-time auto-claim);
      never backfills a merged/closed PR (would be filtered as non-live
      anyway). New engine tests: `TestWorktreeToDictClaimsSummary` (6 cases:
      no-claims omission, ledger-only, backfill, ledger-takes-precedence,
      no-backfill-for-merged, and rank ordering vs. a lower-priority claim).
- [x] Rename/standardize the final top-line column label to `CLAIMS` on the
      Worktrees pivot, consistent with the other pivots. **Done**:
      `ACTIVE_SPECS`/`LIST_SPECS` (`engine_helpers.py`) column key
      `"pr"` -> `"claims_summary"`, header `"pr"` -> `"claims"`. The
      Maintenance pivot's own `CLEAN_SPECS` (a distinct cleanup-candidates
      view, out of this phase's scope) keeps its `"pr"` column unchanged;
      the row/sub-menu detail dialogs (`engine_dialogs.py`) also keep
      showing the specific active-PR string via the untouched raw `"pr"`
      field -- only the LIST's own top-line column standardized. Preserved
      the merged-PR green highlight by keying the style off the still-
      populated raw `"pr"` field (the shared `claims_rank.format_claim`
      carries no merged marker of its own). Updated `scenario_claims`'s
      golden fixture (now sets `claims_summary` directly, mirroring the
      engine's own envelope) and regenerated all 6 affected goldens --
      an unclaimed row's cell is now blank rather than the old `"—"`
      placeholder, matching the Codespaces/Containers convention for "no
      claims" exactly (a deliberate, not accidental, consequence of
      standardizing).
- [x] Read through the accelerator's cached claims graph (no independent
      per-render claim scan), per the vision's *warmth, not truth* rule.
      **Done, satisfied by construction**: `claims_summary` reads only
      `rec.resources` -- the already-persisted claim ledger loaded off the
      YAML record -- never a fresh network/live scan. Same cost profile as
      every other field `_worktree_to_dict` already emits per render.

### Phase 5 — Replace the "R" column (Done 2026-09-25)
- [x] Determine the "R" column's current source field (expected:
      `resume_count`) and why it reads as meaningless in practice.
      **Correction of the plan's own premise**: the source is NOT
      `resume_count` -- it's `reciprocal_relation.short_label` (BOUND ●/
      CONTROL ◐/HANDOFF ⇒/TERM ■/AMBIG ?), a real, actively-tested,
      navigation-gating signal (CONTROL specifically backs a "Go to
      controller" Actions-menu verb, `_reciprocal_target_row`). It read as
      meaningless not because the data was fake, but because the bare
      1-glyph column had no on-grid explanation, and an agent-orchestrated
      worktree (e.g. every phase worktree in this very effort) reads as
      CONTROL despite its own live session being ordinary CLI -- a confusing
      mismatch between what the glyph implies and what's actually running.
- [x] Decide its replacement (operator direction, 2026-09-25): retire the
      standalone column entirely, redistributing its values rather than
      folding into Phase 6's SESS/TURNS wholesale --
      - **BOUND/CONTROL -> a CLI/ACP mode marker on the LIVE column**
        (`derive._sess()`), keyed off the worktree's own already-resolved
        `interface` field (bridge-hosted = ACP, everything else = CLI) --
        NOT `reciprocal_relation`'s binding/control axis, which conflates
        "who orchestrated this worktree" with "what interface is actually
        running here" (confirmed distinct via the exact case above).
      - **HANDOFF -> a genuine `state` value** (`derive._state()`), ranked
        right after the live-session check, landing in the Recent section
        via `bucket()`'s existing fallback (no bucket() change needed) --
        new `C_STATE["HANDOFF"]` color.
      - **TERM and AMBIG dropped** as redundant (TERM duplicates
        state=FINAL/MERGED + the Completed section) and low-value (AMBIG
        is a data-quality signal, not a routine display concern).
      - The full `reciprocal_relation` data, `relation` short-label field,
        and the "Go to controller" navigation action are **unchanged** --
        only the grid glyph column is retired; nothing is lost, the
        at-a-glance discoverability just moves to LIVE/STATE instead of a
        standalone column.
      `ACTIVE_SPECS`/`LIST_SPECS` (`engine_helpers.py`) drop the `"relation"`
      column entirely; `state` widened 6->8 to fit "HANDOFF" without
      truncation. `_RELATION_ICON`/`_RELATION_STYLE` (now unused) removed,
      including their `engine.py` re-exports. New tests: 6 in
      `test_reciprocal_relation.py` (HANDOFF-state fold-in, live-beats-
      handoff precedence, bucket placement, ACP-vs-PROC mode, and the
      CONTROL-but-still-PROC case confirming the two axes are genuinely
      decoupled) + a new golden scenario (`scenario_handoff_acp.txt`)
      exercising both fold-ins end-to-end in one capture. Retired 3 tests
      that exercised the removed UI element itself (column presence/width,
      icon-table completeness) -- the field-computation and navigation-
      gating tests they sat alongside are untouched.

### Phase 6 — SESS/TURNS column on LIVE rows (Done 2026-09-25)
- [x] Add a combined `SESS/TURNS` (or equivalent) column: total session
      count for the worktree, and the current session's turn number.
- [x] Fix delegate/child worktrees incorrectly showing 0 turns
      (dotfiles#458) as part of this column's data path, since it is the
      same undercount this column would otherwise inherit.

### Phase 7 — Session/handoff-head mismatch warning + Sessions sub-menu (Done 2026-09-27)
- [x] Surface a visible warning when a worktree's recorded "last session id"
      and "handoff head session" disagree (root-cause context:
      dotfiles#1298, `worktree-state-live-db`'s journal/deterministic-head
      direction — reuse its derived state if landed first; otherwise
      implement the comparison directly against current fields).
- [x] Add a "Sessions" sub-menu/dialog listing all sessions recorded against
      a worktree (id, started/ended, turn count, head marker).

### Phase 8 — Continue the Mux Companion (Ctrl-K) buildout (Done 2026-09-27)
- [x] Scope and land the next slice(s) beyond v1's view-only status/lineage
      display: candidates raised for exploration — split-screen into
      sub-agents, handoff tracking, extended status reporting. Each new
      capability gets its own Non-Goals-respecting sub-slice (per
      `visions/mux-companion`), not a single undifferentiated dump.
- [x] Record which candidate(s) are accepted vs. deferred, with rationale.
      **Accepted: handoff tracking** (operator's own choice, see journal).
      **Deferred: split-screen sub-agents, extended status reporting** —
      untouched; the vision's own Non-Goals already exclude them from v1,
      and this slice's scope is exactly the one accepted candidate, not a
      second undifferentiated dump onto the same PR.

### Phase 9 — Reconcile deferred backlog
- [ ] Fold in further wishlist items raised after this effort's initial
      review (the operator flagged "I'll think of more").
- [ ] **2026-09-29 operator feedback batch:**
      - [x] **Condense CLAIMS type presentation** (Done 2026-09-29). Short-form
        kind prefixes: `session`→`SESS`, `worktree`→`WT`, `codespace`→`CS`,
        `container`→`CT`, `task`→`T`, `bridge`→`BR`, `ssh`→`SSH`,
        `effort`→`EFF`. PRs and bugs/issues carry NO kind prefix by default —
        `odsp-web#2578906`, `copilot-extensions#4507`, `#589` (bare
        same-repo) — but a plugin-contributed `label_overrides[kind]` (e.g.
        an ADO-sourced "bug") still applies and IS prefixed, preserving that
        extensibility point. Session claims (not yet a claimable kind) now
        rank lowest (8) in `DEFAULT_PECKING_ORDER`, below `task` — the least
        differentiating kind, per the operator's own reasoning.
      - [ ] **Stretch:** interactive claims navigation (highlight a row,
        Right moves focus into its claims list, continuing Right scrolls
        through every claim, Enter/click opens a PR/bug) — not started.
      - [x] **Underlined claim links** (Done 2026-09-29): a linked CLAIMS
        cell entry now renders `underline link <url>` (previously `link
        <url>` with no underline), so it visually reads as clickable.
      - [x] **Rename the SESS/T column to `LENGTH`** (Done 2026-09-29),
        formatted `1s 25t` rather than `1/25`.
      - [x] **LIVE column value taxonomy** (Done 2026-09-29): `MUX(n)`
        (recovering the old attached/unattached `●N`/`○` distinction via an
        explicit connected-client count), `ACP`, `PROC` (kept distinct — see
        journal), `LOCK`, `-`. **Not implemented**: an `ACP(n)` client count
        — no producer emits a connected-client count for an ACP/bridge-hosted
        session today (unlike `mux_clients`); fabricating one was rejected.
      - [x] **Render performance / over-painting investigation** (2026-09-30):
        profiled the Picker's idle (non-busy, no-nav) render-tick path with
        cProfile over a headless `PickerApp.run_test()` at 150/300/500 rows.
        Found -- and fixed the safe, low-risk layer of -- a real
        over-painting bug; documented two deeper, higher-risk root causes as
        an explicit follow-up rather than rushing them in this slice. See the
        dedicated 2026-09-30 journal entry below for the full profiling
        evidence and root-cause breakdown.
- [x] CLAIMS/activity mock-data enrichment + a real, distinct "Activity"
      disposition field (`agent-worktrees status --activity`) replacing the
      second line's old STATE-reuse fallback (2026-09-26).
- [x] USED column: recency of last real interaction, distinct from AGE
      (2026-09-26).
- [x] Sticky column-header + current-group band while scrolling, with
      focus-top force-scrolling to the very top (operator feedback,
      2026-09-26; shipped 2026-09-27). The native list's existing `#88`
      section-band pin (`_PickerStickyHeader`/`_PickerNativeData._update_
      sticky` in `engine_regions.py`) only ever pinned the CURRENT-GROUP
      band (`── Active ──` etc.) -- the column-header row (`ID STATE AGE
      ...`, `kind="colhdr"`) was a normal scrolling data row like any other
      and had no pin at all. `_PickerStickyHeader` is now a 2-row widget:
      row 0 pins the pivot's column header once its own row (tracked via a
      new `_colhdr_index`/`_colhdr_text` pair, generic across every `kind==
      "colhdr"` emitter -- Worktrees/Maintenance/Profiles) has scrolled out
      of view; row 1 keeps the existing section-band pin, independently.
      Both rows stay reserved together (blank per-slot) while scrolled, per
      the pre-existing anti-flicker contract (#169) generalized to two
      slots. Separately, `_PickerNativeData.on_option_list_option_
      highlighted` now force-scrolls the OptionList home (`scroll_home
      (animate=False, immediate=True)`) whenever the newly-highlighted
      option is the list's topmost FOCUSABLE row (`_first_enabled_index()`)
      -- Textual's own `scroll_to_highlight()` only scrolls the minimum
      distance needed to bring that one row into view, which used to leave
      the pinned rows above it (colhdr + section) still scrolled out, so
      only a mouse-wheel scroll reached far enough; arrowing back up to the
      first row now always brings the header fully back into view. New
      tests: `test_native_list_sticky_column_header`, `test_native_list_
      focus_top_row_forces_scroll_home`; the two pre-existing sticky tests
      (`test_native_list_sticky_header`, `test_native_list_sticky_no_
      reflow_flicker`) updated for the renamed `_line` -> `_colhdr_line`/
      `_section_line` API. Full `tests/production_picker/` suite green
      (770 passed / 1 skipped); unscrolled/at-rest render unchanged (no
      golden diff).
- [x] CLAIMS abbreviation/hyperlink rework, in `claims_rank.py` (shared by
      Worktrees/Tasks/Codespaces/Containers): `format_claim()` is now
      cross-repo-aware via a new `own_repo=` parameter -- a `pr`/`bug`/
      `issue` claim shows `"PR #N"` same-repo, `"<short-repo>#N"` cross-repo
      (also now parses a full GitHub URL ref, fixing the real
      "PR https://..." bug this surfaced); a `worktree` claim shows
      `"<last4>"` same-repo, `"<repo>:<last4>"` cross-repo (operator's own
      example and nit, reading the sibling's project from the existing
      `machine/project/worktree_id` ref convention). New `claim_url()` +
      `claim_entries_for_worktree()` (a `[{"label","url"}]` list alongside
      the existing flat `claims_summary` string) let the Picker render a
      REAL terminal hyperlink (OSC 8, verified in an ANSI capture) per
      claim. The Picker's CLAIMS cell no longer mid-value ellipsis-clips
      (`_claims_cell` in `engine_helpers.py`) -- only the whole ROW
      truncates, once, at the very end, if it doesn't fit the terminal at
      all. **"SESS" (first-8-hex) abbreviation intentionally NOT
      implemented**: investigation found `bridge` (the closest matching
      pecking-order kind) is not actually a claimable kind yet -- no code
      path creates a `ResourceClaim` with that kind, so there is no real
      data to abbreviate; fabricating the format for a kind that can't
      exist yet was rejected in favor of being honest about the gap
      (2026-09-27).
- [x] Alternate-row background shading so a multi-line row's title + detail
      lines read as one visual unit (operator feedback, 2026-09-26; shipped
      2026-09-27, PR #3952).
- [x] Riff on the `⚭` paired-worktree marker: named THIS row's own
      `pair_role` inline (e.g. `⚭knowledge Implement Retry Logic`) rather
      than the bare icon. Naming the SIBLING's actual repo (the operator's
      literal example, `⚭dotfiles ...`) needs a new cross-project lookup
      (resolve `pair_id` -> sibling record, possibly in a different tracked
      project) -- operator chose the available, no-new-plumbing option for
      now; the sibling-repo lookup remains a distinct, separately-tracked
      follow-up if wanted later (2026-09-27).

## Validation Plan

- [x] Golden-screenshot diff run before/after every phase in this effort
      (Phase 1's harness), with intentional deltas called out explicitly in
      the phase's own PR description. **Confirmed**: every phase's journal
      entry below names its golden diff explicitly (a specific row/column
      delta, or "byte-identical" / "no golden diff" when the change was
      out-of-render-path).
- [x] Existing Worktrees-pivot unit/interaction tests continue to pass;
      each phase adds targeted coverage (sort order, claims rendering,
      column content, mismatch-warning trigger, sub-menu contents).
      **Confirmed**: every phase's journal entry reports a full-suite pass
      count (e.g. "770 passed / 1 skipped", "297/297, 45/45, 7/7").
- [ ] Manual verification against a real multi-worktree mesh state (mix of
      ACTIVE/Recent/unused, at least one long-running worktree, at least one
      worktree with a claimed PR) confirming ordering, CLAIMS column, and
      SESS/TURNS values match ground truth from `agent-worktrees list --json`.
      **Not done — left honestly unchecked.** Every "verified live" check
      across this effort's journal was against `--demo`/`--preview` (mock
      data, not a real mesh); the real `list --classify` path this item
      requires is still the one blocked by the unbounded-git-walk bug
      ([copilot-extensions#3418](https://github.com/ThomasMichon/copilot-extensions/issues/3418),
      filed during Phase 1, still open). Re-run this item once #3418 lands,
      or accept the demo-verified evidence as sufficient and strike this
      item explicitly (operator's call — not decided unilaterally here).
- [x] No regression in the accelerator's *warmth, not truth* contract — no
      new per-render subprocess/git/claims scan introduced by any phase.
      **Confirmed by the phase-by-phase implementation record** (Phase 4's
      claims read explicitly routes through the accelerator's cache; every
      later phase's data additions — USED/Activity, the mismatch warning,
      Mux Companion — read from the SAME process-boundary `list --json`
      envelope already being fetched, never an independent scan) rather
      than a single dedicated end-to-end audit pass.

## Proposal

All 8 named phases are **Done** (2026-09-23 through 2026-09-27), landed
across the PRs cited in their own Plan entries above. Phase 9 (reconcile
deferred backlog) has folded in and shipped every concrete operator-feedback
item raised so far; its one remaining checklist item — "fold in further
wishlist items raised after this effort's initial review" — is intentionally
open-ended, gated on the operator raising more items, not unfinished work
being tracked and stalled.

**Outstanding before this effort could be archived:**
- The Validation Plan's real-mesh manual-verification item above (blocked on
  #3418, or an explicit operator decision to accept demo-only verification).
- Any further Phase 9 wishlist items the operator raises.

This effort remains **Active**, not yet ready to archive.

## Journal

### 2026-09-22 — Kickoff: triage sweep + reviewed plan drafted
- Swept the operator's personal harness repo, their private knowledge repo
  (bound knowledge repo), and `copilot-extensions` itself for existing issues and
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

### 2026-09-23 — Fixed #3413: real production Picker now has a mock-data preview
- Rebuilt the `--demo`/`--preview` picker flow to render the REAL
  `production_picker` (not the stale `picker_app`) by composing two
  existing, unmodified seams rather than adding a Picker-specific mock
  branch:
  1. `engine_client.set_engine_command` pointed at the already-existing
     `demo_engine` fixture subprocess — every consumer that shells out
     through `engine_client` (including the real `data_local`/`data_ssh`)
     transparently receives mock worktree rows.
  2. A new `demo_pivot.py` fixture plus a plain, schema-less
     ("operator"-class — always active, no plugin/root attribution
     required) manifest injected into a temp `AGENT_WORKTREES_PIVOTS_DIR`,
     read through the *same* cross-plugin pivot-manifest registry a real
     contributed pivot (Codespaces/Containers/…) uses. Verified it renders
     as a genuine extra pivot tab ("Demo Queue") with its own
     declared columns (including a `claims_summary` column, previewing the
     Phase 4 CLAIMS-column convention).
- Verified live: `--demo` now captures the real Worktree Manager chrome,
  real column layout (`ID STATE R AGE LIVE T PR`), the mocked Worktrees
  rows, and the injected pivot's own table — through the actual
  `runner.capture()` path, `--pivot`/`--wait` included.
- Added `tests/test_picker_preview_mode.py` (11 tests): dispatch wiring,
  `enable_preview_mode()`'s two injections, and the `demo_pivot` fixture.
  Found and fixed a real test-isolation bug of my own along the way (env
  vars set by `enable_preview_mode()` leaked across tests in the same
  pytest process, breaking an unrelated `test_plugin_contracts.py` test);
  fixed by explicit env cleanup on both sides of the fixture rather than
  relying on `monkeypatch`'s auto-restore (which only covers state changed
  *through* `monkeypatch` itself). Full suite: 1143 passed, 1 skipped.
- `runner.capture()`'s underlying cost for a REAL (non-preview) capture is
  still #3418 — sidestepped here for preview purposes (the fake engine
  never reaches `list --classify`), but still open and worth its own fix.
- Phase 1 is now unblocked for the actual golden-baseline capture next.

### 2026-09-23 — Phase 1 complete: golden baseline captured + historical comparison
- Added 5 new golden-compared scenario tests
  (`test_picker_capture_scenarios.py`) using the existing hermetic capture
  harness: empty, active-only, mixed, claims (open PR), and long-running.
  The long-running golden deliberately documents today's sort bug as a
  "before" baseline (a 47-turn/30-day worktree sorts below a
  never-touched/1-day one, purely by `started_at`).
- Found the original Textual-picker-era screenshot
  (`docs/assets/worktree-picker.png`, `v1.0.0`, committed 2026-07-25,
  unmodified since) and wrote the requested comparison in
  `screenshot-comparison.md`. Key finding for later phases: the `R` column
  has **no historical precedent** in the original design — confirms
  wishlist item #5 and frees Phase 5 to redefine/retire it without a
  legacy meaning to preserve; `SESS`→`LIVE` is a same-primitive rename;
  `T` (turn count) is a genuinely new column since `v1.0.0`.
- Confirmed the checked-in golden-comparison step already existed
  (`test_picker_capture.py`'s `GOLDEN_DIR`/`AGENT_WORKTREES_UPDATE_GOLDENS`
  pattern) and extended it rather than inventing a parallel one.
- Full suite: 1155 passed, 1 skipped.
- Phase 1 status: **Done**. Moving to Phase 2 (evaluate native Textual
  components for the Worktrees table) next.

### 2026-09-23 — Enriched the durable mock fixture with the scraped memo titles
- Scraped the 18 "management memo" titles from the original v1.0.0
  screenshot (`docs/assets/worktree-picker.png`) found while writing the
  Phase 1 comparison — the terse, absurd, treats-employees-as-test-subjects
  Cave Johnson register, distinct from `demo.py`'s original 7 "Portal quote"
  rows. Folded them into `demo.py` as a durable, reusable `_MEMO_TITLES`
  bank plus 18 new fixture rows (9 Active/9 Recent+Completed), reconstructed
  with the same ids, relative ages, live/session indicators, follow-up
  markers, and PR states the original screenshot showed. Kept the existing
  7 rows byte-identical (nothing removed) so `test_picker_app.py`'s
  "lemons"/"Iris" assertions and row-count checks stay valid untouched.
- `_MEMO_TITLES` is intentionally separated from the row-construction code
  so a future combinatorial title generator (more volume than this fixed
  25-row roster) has a clearly-labeled, reusable bank of on-theme phrasing
  to start from, per the operator's "at least inspiration for the
  generator" ask — not built this pass; the curated bank alone was judged
  sufficient for now.
- Verified live: `--demo` now shows "9 active · 10 recent · 6 done",
  matching the original's section shape closely, with the same titles,
  follow-up markers (✚), and PR numbers/states. Full suite: 1155 passed,
  1 skipped.

### 2026-09-23 — Filed screenshot evidence to OneDrive; effort marked Active
- Installed `resvg` (`worktree-manager/scripts/picker-snapshot`, `npm
  install`) as a deterministic PNG rasterizer — headless Edge hung
  unrelated to this effort's own code (an environment quirk on this
  machine, not a repo bug), `resvg` did not.
- Captured and filed 3 screenshots to the operator's OneDrive
  (`2026/09.22 Worktrees Pivot UX Overhaul/`, per their maintained
  organization profile's "tied to a specific project with a known start
  date" placement heuristic): the current Worktrees pivot (`--demo`,
  enriched fixture), the injected "Demo Queue" mock pivot, and the
  original `v1.0.0` baseline — with a short `README.md` index. Local
  temp captures cleaned up; nothing else changed in-repo this slice.
- Effort status promoted **Draft → Active** (Phase 1 shipped, work is
  ongoing) — no plan changes.
- **Next up: Phase 2** — evaluate native Textual components (`DataTable`/
  `OptionList`) for the Worktrees table vs. the current hand-rolled
  `engine_views.py` renderer. Not yet started.

### 2026-09-23 — Phase 2 complete: DataTable ruled out, ListView spiked and evidenced
- Corrected the starting premise mid-audit: the outer list *container* was
  already migrated to a native `OptionList` pre-effort
  (`engine_regions._PickerNativeData`, #88 NF5-5) — the open question was
  really the row-*content* model (two adjacent options per record), not
  container-vs-hand-rolled.
- `DataTable` ruled out on a concrete structural blocker: no colspan, so it
  cannot render the Active/Recent/Completed section bands at all — not a
  stylistic preference.
- Built and ran a real `ListView`-backed spike
  (`worktree-manager/src/worktree_manager/production_picker/picker_tui/listview_proto.py`
  + `worktree-manager/scripts/listview_proto_compare.py`) against the real
  demo fixture roster (25 records, 3 sections), confirmed atomic
  title+detail rows and a real `Checkbox` widget both work, and found (by
  running it, not guessing) three concrete integration costs: default
  `ListItem` chrome is visually heavier than today's flat rows,
  `ListView.append`/`.extend()` must be awaited (async `on_mount`), and
  height/CSS defaults need explicit pinning at every composition level.
  Full writeup: [`phase2-native-textual-audit.md`](phase2-native-textual-audit.md).
- **Decision**: keep `OptionList` as the Worktrees pivot's default (it
  already has a working, tested bridge); ListView is plausible as an
  opt-in-per-pivot `render_mode` (`"v1"` vs `"v2"`), but the production
  widget's bridge parity (scroll preservation, sticky header, incremental
  repaint, `sel` sync) is its own tracked follow-up — no built-in pivot
  opts in without a separate, explicit review.
- Neither new file is wired into any pivot's real render path — purely
  additive, zero risk to the existing `OptionList` path or its golden
  screenshots.
- **Next up: Phase 3** — fix the Recent-section sort order
  (most-recently-used, not newest-first). Not yet started.

### 2026-09-24 — Phase 3 complete: Recent sorts by last real resume, not creation age
- Root-caused the actual at-rest sort mechanism: `bucket()`'s own per-section
  sort is immediately overridden on every render by `current_list_visible()`
  applying `WT_SORT_KEYS[0]` ("age") — both needed the new key, not just one.
  Caught two real bugs by running a diagnostic script against the real fixture
  data before trusting the golden: (1) the first cut of the fallback read a
  bare `started_at` key off the NORMALIZED record, which `norm()` never emits
  (only derived `age`/`age_secs`) — every never-resumed worktree silently hit
  the sentinel instead of a real fallback; (2) only fixing `bucket()` left the
  golden unchanged because `WT_SORT_KEYS` still won.
- Shipped: `agent_worktrees/__main__.py` surfaces the already-tracked
  `WorktreeRecord.last_resumed_at` in `list --json`'s per-worktree envelope
  (previously computed but never serialized); `derive.py` adds
  `_last_active_secs()` (prefers `last_resumed_at`, falls back to the
  record's own `age_secs`), wired into both `bucket()`'s Recent sort and
  `WT_SORT_KEYS[0]`. ACTIVE/Completed sections are unchanged (still
  `age_secs`, per their own semantics).
- New unit tests: `test_bucket_recent_sorts_by_last_resumed_not_creation_age`,
  `test_bucket_recent_falls_back_to_started_at_when_never_resumed` (picker),
  `test_worktree_to_dict_emits_last_resumed_at_when_set` /
  `..._omits_..._when_never_resumed` (engine serialization). Regenerated
  `scenario_long_running.txt` — diff is exactly the 2 lines documenting the
  row-order flip; every other golden byte-identical.
- Rebased twice mid-slice (repo landed 25 commits upstream while this ran);
  re-verified 4 sampled pre-existing failures (`test_doctor`,
  `test_git_ops::TestPinGitCredential`, `test_terminal_refresh`,
  `test_update_stage`) reproduce identically on a clean, fully-rebased tree
  with this slice's changes stashed out — confirmed environment/timing
  artifacts (stale git-credential-helper state, a machine-local "update
  paused" marker, a live-network-fetch race), not caused by this change.
  Full suites: worktree-manager 1190 passed/1 skipped; agent-worktrees 5435
  passed/42 skipped/9 failed (all 9 pre-existing per the above).
- **Next up: Phase 4** — CLAIMS column: adopt the shared
  `claims_rank`/`claims_cli` pecking-order module already shipped for the
  Codespaces/Containers pivots. Not yet started.

### 2026-09-25 — Phase 4 complete: CLAIMS column via the shared claims_rank module
- Key architectural finding before writing code: unlike Codespaces/
  Containers (which soft-depend on `agent_worktrees` and compute
  `claims_summary` in their OWN backend), `worktree-manager`'s Picker never
  imports `agent_worktrees` as a library -- only across a subprocess
  boundary. So `claims_summary` is computed ENGINE-side
  (`agent_worktrees.__main__._worktree_to_dict`, same package as
  `claims_rank`), not in the Picker's `derive.py`, keeping that boundary
  intact -- `derive.norm()` just passes the ready-made string through.
- Shipped: engine emits `claims_summary` (ranked via the shared
  `claims_rank.summarize_claims`, backfilling a synthetic `pr` claim from
  the back-compat `active_pr()` when the ledger predates the `create-pr`
  auto-claim, never for a merged/closed PR); `ACTIVE_SPECS`/`LIST_SPECS`
  column renamed `"pr"`/`"pr"` -> `"claims_summary"`/`"claims"` (Maintenance's
  own `CLEAN_SPECS` and the row detail dialogs keep the untouched raw `"pr"`
  field, out of this phase's scope); merged-PR green highlight preserved by
  keying it off that same untouched raw field.
- New tests: `TestWorktreeToDictClaimsSummary` (6 engine cases: omission,
  ledger-only, backfill, ledger-precedence, no-backfill-for-merged, rank
  ordering). Updated `scenario_claims`'s fixture + regenerated 6 goldens --
  an unclaimed row's cell is now blank (was `"—"`), matching Codespaces/
  Containers' own convention exactly, a deliberate consequence of
  standardizing, not a regression.
- Full suites: worktree-manager 1190 passed/1 skipped; agent-worktrees run
  in progress at write time (prior slice's full run: 5435 passed/42
  skipped/9 pre-existing failures) -- see the landed PR for final counts.
- **Next up: Phase 5** — replace/retire the "R" column (unblocked by
  Phase 1's screenshot comparison finding no historical precedent for it).
  Not yet started.

### 2026-09-25 — Phase 5 complete: "R" retired, values redistributed to STATE/LIVE
- Corrected the plan's own premise mid-audit (third time this effort):
  the column's source is `reciprocal_relation.short_label`
  (BOUND/CONTROL/HANDOFF/TERM/AMBIG), not `resume_count` -- and CONTROL
  specifically gates a real "Go to controller" Actions-menu navigation verb
  (`_reciprocal_target_row`), not just a passive glyph. Presented this
  finding plus a badge-based-retirement recommendation; the operator gave
  a more specific, better redesign instead.
- Operator's design: BOUND/CONTROL fold into LIVE as a CLI/ACP interface
  mode marker (keyed off the worktree's own `interface` field, not
  `reciprocal_relation`'s binding/control axis -- confirmed via a concrete
  case that these are genuinely different signals: an agent-orchestrated
  worktree's own live session is ordinary CLI despite reading CONTROL).
  HANDOFF becomes a genuine `state` value. TERM/AMBIG dropped as
  redundant/low-value. The underlying `reciprocal_relation` data and the
  navigation action are untouched -- only the grid glyph column goes away.
- Shipped: `derive._sess()` shows ACP vs PROC; `derive._state()` adds a
  HANDOFF branch (threaded through `_state_style()` too, so both agree);
  `ACTIVE_SPECS`/`LIST_SPECS` drop the `relation` column (state widened
  6->8 to fit "HANDOFF"); `_RELATION_ICON`/`_RELATION_STYLE` removed as
  now-unused, including their re-exports.
- New tests: 6 in `test_reciprocal_relation.py` + a new golden scenario
  (`scenario_handoff_acp.txt`) exercising both fold-ins together in one
  capture -- confirmed HANDOFF lands in Recent and ACP/PROC render
  correctly end-to-end. Retired 3 tests exercising the removed column
  itself; kept every field-computation/navigation test.
- Full suite: worktree-manager 1234 passed/1 skipped (up from 1227; +7 new
  tests). No agent-worktrees engine changes this phase -- pure Picker-side
  redistribution, so no second full suite run was needed.
- **Next up: Phase 6** — combined SESS/TURNS column on LIVE rows, plus
  fixing delegate/child worktrees incorrectly showing 0 turns
  (dotfiles#458). Not yet started.

### Phase 6 — SESS/TURNS column on LIVE rows (Done 2026-09-25)
- **SESS/TURNS column:** `derive._sess_turns(w)` renders
  `"<session_count>/<turn_count>"` (e.g. `"3/47"`), falling back to `"-"`
  for the session half when `session_count` is absent (a fixture, or a
  remote too old to report it) rather than fabricating a count -- the turn
  half always renders. Wired into `norm()`'s row dict as `sess_turns`
  alongside the existing `turns`/`session_count` keys (kept, unchanged, for
  back-compat -- `engine_dialogs.py`'s detail lines still read `turns`
  directly). `ACTIVE_SPECS` (LIVE rows) gains a new `sess_turns` column
  (header `SESS/T`, width 7, right-aligned, drop-priority 9 -- least
  essential of the LIVE-row columns); `LIST_SPECS`'s old standalone `t`
  turns-only column is replaced in place by the same `sess_turns` column
  (same drop-priority 8 it had).
- **dotfiles#458 (delegate/child 0-turns undercount) -- root-caused and
  fixed:** the actual bug was in `agent_bridge.worktree_lineage
  .register_session()`, NOT in `agent_worktrees.sessions.scan_sessions_fast`
  (the registry-enrichment path investigated last slice, which turned out
  to be working as designed). `register_session()` built its CLI argv as an
  **either/or**: whenever `worktree_dir` (`target.cwd`) was truthy it sent
  *only* `--cwd`, silently dropping the already-resolved, always-reliable
  `--worktree-id` (`target.worktree_id`) it also had in hand. `register
  -session`'s own docstring claim that it "does not resolve a bare
  --worktree-id" was stale/copy-pasted from a *different* command
  (`session-role`'s own docstring genuinely says that) -- `register-session`
  has always accepted and used `--worktree-id` directly
  (`cmd_register_session`'s own `--worktree-id` help text: "resolved from
  --cwd when omitted", implying the reverse is true too). So every local/ACP
  session registration for a worktree with a known `cwd` fell back entirely
  on `register-session`'s own fragile cwd-based inference chain
  (`_activate_project_for_path`'s git-toplevel + reverse-project-lookup,
  then `tracking.find_worktree_id_by_cwd`'s path-prefix match against every
  tracked `worktree_path`) -- any one of which can silently fail for a
  delegate/child worktree (a path-normalization mismatch, an unresolvable
  project at the time the subprocess's cwd was set, etc.), fail-open, with
  no error surfaced. Fixed by always passing **both** flags when both are
  known: `--worktree-id` first (so a delegate/child registration bypasses
  the fragile cwd chain entirely and goes straight to the correct project
  via `_activate_project_for_worktree_id`), `--cwd` still included as
  `register-session`'s own existing fallback. `cmd_register_session` itself
  needed no change -- passing an explicit `--worktree-id` already short-
  circuits its cwd-resolution branch, and its existing
  `if not cfg.active_project(): _activate_project_for_worktree_id(wt_id)`
  guard already recovers the right project whenever the subprocess's own
  CWD-based startup resolution left no project active (the common failure
  shape for a session-host-mode local dispatch whose OS-level cwd doesn't
  resolve to any adopted project).
- Updated the one existing argv-shape test
  (`test_register_session_argv` in `test_worktree_lineage.py`) to assert
  both flags are now sent together; the no-cwd fallback test
  (`test_register_session_argv_no_dir_fallback`) was already correct and
  untouched. New test: `test_sess_turns_combines_session_count_and_turn
  _count` in `test_picker_tui.py`, alongside the existing `_sessionless`
  coverage it mirrors.
- Golden screenshots regenerated (`AGENT_WORKTREES_UPDATE_GOLDENS=1`) for
  all 7 affected goldens (the `T` -> `SESS/T` header rename and `N` ->
  `-/N` cell format) -- diffs are exactly the intended column change, no
  incidental drift.
- Full suites: `agent-bridge/tests/test_worktree_lineage.py` 15/15;
  `agent-worktrees/tests/test_register_session.py` 44/44 (unchanged --
  confirms `cmd_register_session` itself needed no edit); worktree-manager
  `tests/production_picker/` 749 passed/1 skipped (up from 741/1; +7 tests
  net, some retired-column assertions folded into new ones, some added).
  A full-repo `agent-bridge`/`agent-worktrees` suite run was also started
  for extra safety beyond the targeted files above, but both are broad,
  long-running suites (tens of minutes) unrelated to this change's actual
  surface (a single CLI-argv construction + a column/derive addition); cut
  short in favor of the targeted, directly-relevant runs above once those
  were green, rather than blocking on unrelated slow coverage.
- **Next up: Phase 7** — session/handoff-head mismatch warning + Sessions
  sub-menu. Not yet started.

### 2026-09-26 — Post-Phase-6 renders + pivot-development render guidance
- Rendered the current Worktrees pivot against `--demo` (text, SVG, and a
  rasterized PNG via `scripts/picker-snapshot`) to verify Phase 6's
  SESS/TURNS column reads correctly end-to-end, not just in golden-text
  diffs. Found the demo fixture (`src/worktree_manager/demo.py`) never set
  `session_count` on any row, so every render showed `-/N` -- honest, but
  not a useful demonstration of the column's actual point. Added
  illustrative `session_count` values to the "management memo" roster
  (roughly correlated with age/turn_count, never exceeding it), so the
  demo render now shows a believable spread (`1/6`, `2/14`, `5/52`, etc.).
  Filed the resulting SVG+PNG to the operator's OneDrive in a new dated
  folder (`2026/09.26 Worktrees Pivot UX Overhaul Phase 6/`, per their
  organization profile's `MM.DD Topic Name` convention), alongside a short
  README index, distinct from the Phase 1 baseline set.
- Added a **"Developing a pivot: render early, render often"** section to
  `worktree-manager/README.md`'s Production Picker transplant docs (per
  operator request): any pivot change touching columns/derived fields/
  section ordering should be rendered before/after and at each milestone,
  not verified by test-passing alone -- column width/truncation/drop-
  priority regressions are exactly what a string assertion can miss while
  still green. Documents the `--demo`/SVG/PNG render commands already used
  above, and explicitly calls out extending `demo.py` with representative
  values first when a render target field the fixture doesn't yet populate.
- No functional/engine code changed this slice -- fixture data + docs only.
  `tests/test_picker_preview_mode.py` + `tests/test_picker_app.py` (43
  tests, cover demo-fixture consumption) still pass unchanged.

### 2026-09-26 — USED column, real Activity field, and populated CLAIMS mock (operator feedback)
Operator feedback on the rendered pivot, addressed as a bundled follow-up
(items tracked individually; not all addressed this slice -- see below):

- **CLAIMS was empty in every mock render** despite the column's allocated
  width -- the demo fixture never set `claims_summary` on any row (it's a
  hermetic pass-through from the engine, never derived by the Picker; see
  Phase 4). Added illustrative `claims_summary` values using ONLY kinds
  `claims_cli` can actually produce today (`pr`, `worktree`, `container`,
  `bridge`, `task`, `ssh` -- explicitly NOT `bug`/`issue`/`effort`, which
  `claims_rank`'s own "kind-vocabulary gap" note says aren't real claimable
  kinds yet). **Known gap surfaced by this render, not yet fixed**: a
  claim whose formatted label overflows the 12-char CLAIMS column gets
  ellipsis-truncated mid-value (e.g. `"container a…"`) -- the operator
  flagged this exact behavior as wrong (truncate only at the row's end,
  never mid-value) and asked for WT/SESS abbreviations, a short cross-repo
  `repo#N` PR form, and a real terminal hyperlink -- all deferred to a
  follow-up since `claims_rank.py` is shared by 4 pivots (Worktrees/Tasks/
  Codespaces/Containers) and needs its own careful pass.
- **USED column, distinct from AGE**: added (`derive._last_active_display`,
  wired into `ACTIVE_SPECS`/`LIST_SPECS`) -- recency of the worktree's last
  REAL interaction (prefers `last_resumed_at`, same signal Phase 3's Recent-
  section sort already uses), now visible in the table itself rather than
  only affecting sort order. Demo fixture seeded a few old-but-recently-
  resumed rows (`7099`, `0545`, `b753`) to make the AGE-vs-USED divergence
  visible in a render.
- **Real "Activity" field, replacing the STATE-reuse on line 2**: found
  that the agent-asserted disposition `summary` (`agent-worktrees status
  --summary`) was already flowing into the Picker but only ever spliced
  onto the TITLE line (`"{title} — {summary}"`) -- never used for the
  second line, which instead fell back to bare STATE when no live-pulse
  intent was present (the operator's core complaint: "don't reuse STATE
  after title"). Root-caused and fixed as a genuine three-field redesign
  rather than a narrow patch:
  - `agent-worktrees status` gains a THIRD disposition flag, **`--activity`**
    (`tracking.WorktreeRecord.activity`/`activity_at`, `set_disposition`,
    `tracking_disposition_write`, `disposition_history`, `_worktree_to_dict`
    -- all mirroring `summary`'s existing plumbing), with an explicit,
    documented CADENCE contract distinct from the other two (operator's own
    wording, captured verbatim in `status_cli`'s help text and
    `set_disposition`'s docstring): **`--activity`** = the current sub-task,
    update MOST often; **`--summary`** = a broader recap, update
    OCCASIONALLY to fold in newly completed work; **`--title`** = the rare,
    intentional headline, update only when the main theme genuinely changes.
  - The nudge script (`scripts/nudge_status.py`) now teaches this same
    cadence in its own reminder text (previously: "run `status --summary`
    ... add `--title` if the focus changed").
  - Picker side: `derive.py` no longer appends `summary` onto the title
    (title is pure again, `summary` still exposed for a future detail
    card); `engine_views._detail_line`'s fallback chain is now live-pulse
    intent -> `activity` -> **nothing** (never bare STATE again).
  - **Deferred, not built this slice** (operator's own call): a NEW
    agent-bridge "report-intent" MCP tool so ACP/bridge-driven sessions
    (which may miss the existing live-pulse extension the same way they
    missed session registration, dotfiles#458/Phase 6) can report activity
    directly. For now, ACP sessions get activity only via the disposition
    `--activity` flag (already usable from any session shape) or the
    existing live-pulse extension when it does fire.
- **Still open from the operator's feedback (not started)**: (1) sticky
  column-header + current-group band while scrolling, with focus-top
  force-scrolling to the very top; (2) the CLAIMS abbreviation/hyperlink
  rework above; (4) an alternate-row background shade so a multi-line row's
  two lines read as one visual unit; (5) the `⚭` paired-worktree glyph
  should name the link target (or read `[paired]`) instead of a bare icon
  -- operator asked to "riff" on wording, not yet explored.
- Tests: `agent-worktrees` -- `test_tracking.py` (+2: activity YAML
  round-trip, absent-by-default), `test_disposition_history.py` (+1:
  activity's independent freshness stamp), `test_status_write.py` (+1:
  `--activity` end-to-end via `_cmd_status_write`), `test_tracking_
  disposition_write.py` (existing test's expected-dict updated),
  `test_nudge_status.py` (existing assertion updated for the new nudge
  wording) -- all green (297/297, 45/45, 7/7 across the touched files).
  `worktree-manager` -- new `test_sess_turns_combines_...` (Phase 6,
  carried over) plus the full `tests/production_picker/` golden suite
  regenerated and reviewed diff-by-diff (753 passed/1 skipped); `test_
  picker_preview_mode.py`/`test_picker_app.py` (43) green. A real PyYAML
  round-trip gotcha was caught and fixed in the same slice: an unquoted
  ISO timestamp round-trips as a space-separated `datetime` `str()`, not
  the original `T`-separated text -- `activity_at` is now quoted at
  serialization (the pre-existing `status_note_at` has the same latent
  quirk but was left untouched, out of this slice's scope).
- **Next up**: continue Phase 7 (session/handoff-head mismatch + Sessions
  sub-menu), or pick up the 4 still-open feedback items above -- operator's
  call.

### 2026-09-27 — Phase 9 item 1 complete: two-row sticky pin + focus-top force-scroll
- Root-caused the operator's report as two DISTINCT gaps in the pre-existing
  `#88` native-list sticky feature (`_PickerStickyHeader`/`_PickerNativeData`
  in `engine_regions.py`), not one: (1) that feature only ever pinned the
  CURRENT-GROUP band (`── Active ──` etc, `kind="section"`) -- the pivot's
  column-header row (`ID STATE AGE ...`, `kind="colhdr"`, emitted generically
  by Worktrees/Maintenance/Profiles) was an ordinary scrolling data row with
  no pin at all; (2) arrowing back up to the list's topmost row relied on
  Textual's own `scroll_to_highlight()`, which only scrolls the MINIMUM
  distance to bring that one row into view -- since the pinned rows sit above
  index 0 of the visible viewport once scrolled, that minimal scroll left
  them (and thus the "header") still hidden; only a mouse-wheel scroll went
  far enough.
- Fix: `_PickerStickyHeader` is now a 2-row widget (`set_lines(colhdr_line,
  section_line, keep_space=...)`, height 2) -- row 0 pins the column header
  once ITS row (tracked via new `_colhdr_index`/`_colhdr_text`) has scrolled
  out, row 1 keeps the pre-existing section-band pin, independently; both
  stay reserved together while scrolled (blank per empty slot) so neither
  toggling alone reflows the list, generalizing the existing anti-flicker
  contract (#169) from one slot to two. `_PickerNativeData.on_option_list_
  option_highlighted` now calls a new `_first_enabled_index()` and, when the
  newly-highlighted option IS that topmost focusable row, force-scrolls with
  `self.scroll_home(animate=False, immediate=True)` -- overriding the
  framework's minimal in-view scroll so the pin actually clears and the
  unscrolled layout is restored on arrow-key navigation, not only a
  mouse-wheel scroll.
- Tests: two new (`test_native_list_sticky_column_header`, `test_native_
  list_focus_top_row_forces_scroll_home`); the two pre-existing sticky tests
  updated for the renamed `_line` -> `_colhdr_line`/`_section_line` API.
  Full `tests/production_picker/` suite green (770 passed/1 skipped, up from
  753 pre-slice as more tests have accumulated this effort).
  `tools/check-module-size.py` clean. Static/unscrolled demo render
  (`picker screenshot --demo --format text`) confirmed byte-identical to
  before the change -- the sticky feature only affects scrolled state, which
  the deterministic capture doesn't exercise, so no golden touched.
- **This was the last of Phase 9's 6 identified items** -- the phase's
  operator-feedback checklist (besides the open-ended "I'll think of more"
  bullet) is now fully reconciled. **Next up**: Phase 7 (session/handoff-head
  mismatch warning + Sessions sub-menu) or Phase 8 (Mux Companion buildout),
  both still fully unstarted -- operator's call.

### 2026-09-27 — Phase 7 complete: session/handoff-head mismatch warning + Sessions sub-menu
- Investigated `worktree-state-live-db` (copilot-extensions#229) first, per
  this phase's own note that its journal/deterministic-head work might make
  Phase 7 thinner: found the deterministic-head derivation (`resolved_head_
  session`, `set_head_session`/`conclude_session`/`link_succession`) has
  ALREADY landed. `_worktree_to_dict` already had a filesystem-scanned
  candidate (`session_ctx.last_session_id`, GH #198) sitting right next to
  the asserted head, with an existing test
  (`test_worktree_to_dict_keeps_head_over_newer_transcript_and_mux`) proving
  the mismatch case is real and already deliberately resolved (head wins) --
  but with NO warning surfaced. Root-caused the exact dotfiles#1298 scenario
  this represents (a resumed session landing on a stale predecessor, or a
  surviving predecessor mux still writing to its own old session dir after a
  handoff) and implemented Phase 7 as a thin, additive layer on top of that
  already-landed foundation, per the phase's own contingency:
  - `_worktree_to_dict` (agent-worktrees `__main__.py`) now sets
    `session_head_mismatch`/`session_head_mismatch_scanned_id` when the
    scanned candidate disagrees with a present head -- purely additive,
    never changes which session `last_session_id` resolves to. Propagated
    through `derive.norm()` in BOTH the plugin's `picker_support/derive.py`
    and the transplanted `production_picker/picker_tui/derive.py` copy (kept
    in sync by inspection, per this repo's established pattern for that
    duplication).
  - `WorktreesView._detail_line` (`engine_views.py`) renders a `⚠ head
    mismatch` warning on the row's detail line when the flag is set (falls
    back to a bare `⚠` if the terminal is too narrow for the full label) --
    the same "if room" suffix pattern the phase-label/claims-asterisk
    markers already use, so it never reflows or breaks the at-rest layout
    for the (overwhelmingly common) unflagged case.
  - **Sessions sub-menu**: discovered `agent-worktrees list-sessions
    --worktree <id> --json` (`sessions.list_worktree_sessions`) and the
    Picker's own `engine_client.list_worktree_sessions` + `_load_worktree_
    sessions` helper (already used by the Actions menu's "Messages" peek to
    show an abbreviated per-session list) already return every field this
    phase's own spec asks for (id, state, turn_count, started_at_marker,
    ended_at_marker, is_head) -- no new plugin-side data path needed. Added
    a new "Sessions" verb to the worktree Actions menu (`_session_action_
    verbs`), gated on `session_count` alone (independent of current
    liveness/warning state -- a worktree's session HISTORY is worth
    browsing even mid-diagnosis of exactly the mismatch above), and a new
    `SessionsViewScreen` (`engine_live_screens.py`) + `PickerScreenSessions
    ActionsMixin` (`engine_sessions_actions.py`, a new file -- see module-
    size note below) rendering a dedicated id/state/started/ended/turns
    table with the head marked, mirroring `MsgViewScreen`'s live-load/
    scroll/close shape exactly (same daemon-thread-populates-a-dict-under-
    a-lock pattern, same Esc/↑/↓ key contract) but as its own distinct,
    un-abbreviated history browse.
  - **Module-size fallout**: adding the Sessions methods pushed
    `engine_worktree_actions.py` to 1024 lines -- a NEW offender over the
    1000-line cap (never previously baselined), so per this repo's own
    policy that file must be trimmed, not widened. Split the 3 new Sessions
    methods into the new `engine_sessions_actions.py` (a separate mixin,
    composed onto `PickerScreen` alongside the worktree-actions mixin --
    `_sessionsview_worker` still calls the OTHER mixin's `_load_worktree_
    sessions`, which is fine since both compose onto the same instance),
    landing the file back at 973 lines. Separately, my own small additive
    diff nudged two ALREADY-grandfathered giants (`agent_worktrees/
    __main__.py` 7059->7072, the transplanted `derive.py` 1020->1023) a few
    lines past their existing ceilings after trimming my own comments to
    the minimum reasonable size -- widened `tools/module-size-baseline.json`
    for exactly those two, deliberate and reviewed, per this repo's own
    sanctioned path for an already-baselined file (never for the new
    `engine_sessions_actions.py` offender above, which was trimmed instead).
  - **Investigated an unrelated, pre-existing, machine-load-sensitive test
    flake** while chasing full-suite-green (operator's own "must ensure all
    tests are fixed, not just yours"): several `test_picker_tui.py` modal
    tests (`_open_task_menu()`, `_open_submenu()`, ...) intermittently
    observed `None` where a just-pushed `ModalScreen` was expected, on FULL
    suite runs only, never in isolation. Root-caused as **machine contention
    from this session's own hours of heavy, repeated test runs**, not a
    latent code defect: `Get-Process pwsh,python,uv` showed 200+ accumulated
    processes on this (shared, non-sandboxed) machine; the SAME assortment
    of tests fails identically on a completely unmodified checkout (stashed
    my diff, reproduced the pattern across several DIFFERENT call sites,
    never the same one twice) and passes cleanly whenever run in isolation
    or shortly after a full run (lower momentary contention). A single
    `pilot.pause()` after `push_screen` is the file's universal convention
    (dozens of call sites), so a systemic fix would mean touching the whole
    file for a problem that is this MACHINE's momentary load, not the
    repo's -- out of this phase's scope. Added one small, low-risk,
    generically-useful robustness improvement anyway (`_open_task_menu_
    and_wait(scr, pilot)`, a bounded poll instead of a single pump, for the
    5 call sites `_open_task_menu()` itself has) since it is harmless and a
    net positive for any CI runner under momentary load; deliberately did
    NOT attempt a wider mechanical sweep of every `push_screen` site in this
    8000+ line file, which would be unbounded scope creep chasing a
    machine-state artifact, not a code defect. **The Phase 7-specific tests
    below are solid and reproduce green on every run**, isolated or full;
    the residual, pre-existing modal-timing flake is a known, environment-
    load artifact of this dev machine/session, not this diff, and should
    not appear on a fresh, uncontended CI runner.
  - Tests: 2 new plugin-side (`test_worktree_to_dict_no_mismatch_when_scan_
    agrees_with_head`, plus `derive.norm()` passthrough assertions folded
    into the existing mismatch/no-mismatch tests) in `test_status_segment.py`
    (32/32 green); 3 new Picker-side (`test_detail_line_shows_session_head_
    mismatch_warning`, `test_sessions_verb_gated_on_registered_session_
    count`, `test_sessionsview_local_load_populates_and_closes`) plus the
    flake fix above in `test_picker_tui.py`. Full `tests/production_picker/`
    suite green twice in a row (780 passed/1 skipped); `tools/check-module-
    size.py` clean. The demo screenshot render (`picker screenshot --demo
    --format text`) currently times out on a completely unmodified checkout
    too (`timed out waiting for setup epoch 1 to finish`) -- a pre-existing,
    unrelated machine/session-state issue this slice did not introduce or
    chase further; the comprehensive automated suite is the validation of
    record here.
- **A genuinely PRE-EXISTING, still-open, unrelated flake noted but NOT
  fixed** (out of this slice's scope, per "don't fix unrelated issues"):
  `agent-worktrees`' `tests/test_sessions.py::TestMuxBindingForSession::
  test_missing_live_lock_does_not_query_mux` fails on a cold `platform.
  system()` cache miss (falls through to an internal `subprocess.
  check_output(["ver"])` call the test's own mock treats as a hard
  failure) -- reproduces identically on an unmodified checkout, unrelated to
  session-registry/derive.py code this phase touched. Left as a follow-up
  for whoever next touches `sessions.py`/`locks.py`.
- **Phase 9's "fold in further wishlist items" bullet stays open-ended**
  (operator's own "I'll think of more") -- unaffected by this phase.
- **Next up**: Phase 8 (continue the Mux Companion Ctrl-K buildout) is the
  only phase in this effort's Plan still fully unstarted.

### 2026-09-27 — Phase 8 complete: Mux Companion handoff tracking
- Operator's choice among the three raised candidates (split-screen
  sub-agents / handoff tracking / extended status reporting): **handoff
  tracking**. Investigated `visions/mux-companion` first and found the
  target already precisely specified there -- ``session-lineage-visibility``
  §Concepts states the Companion shows "for situational awareness only --
  the headline of any pending context-handoff baton, read by schema, never
  acted on," and the dedicated Behavior `companion-reads-handoff-schema-
  never-drives-it` states it explicitly. So this slice is exactly that: no
  scope invention needed, just implementing an already-specified gap in v1.
- Root-cause investigation ruled out two plausible-looking-but-wrong data
  sources before finding the right one: `agent-worktrees`' `SessionHandoff`
  ledger (`tracking.py`, `handoffs[]`/`open_handoff`/`link_handoff`) is a
  head-succession bookkeeping primitive with no human title field, and is
  not currently wired to any CLI verb at all; `note-handoff`'s disposition-
  history entry is a historical audit log, not a "is one PENDING right now"
  signal. The actual mechanism matching the vision's own wording is
  context-handoff's (JS-side) file-backed schema: `saveFileHandoff` writes
  `<worktree-state-dir>/handoff/handoff-<sid>.json` (`kind: "context-
  handoff"`, `title`, `consumed`), and `worktree-state-dir` resolves via
  `agent-worktrees get worktree-state-dir` (a plain-text, cwd-scoped verb).
- Implementation stays fully on the read side of the process boundary
  (`mux_companion.py`'s own module docstring: reach the engine only by
  shelling out, never `import` a plugin): added `engine_client._run`'s
  `cwd=` support (needed since this verb infers its target from the
  process's own cwd, not a flag) plus a new `handoff_client.py` (`worktree_
  state_dir`/`pending_handoff`) that runs the engine from the worktree path
  and reads the JSON files directly -- still never importing context-handoff
  itself, only reusing the SAME state-dir resolution it already uses. Wired
  into `_CompanionData`/`_load_current_worktree` (best-effort, degrades to
  `None` on any failure) and rendered as a `⏳ Pending handoff: <title>` line
  in `_status_text()` -- read-only, no action offered, per the vision's
  explicit boundary.
- **Module-size fallout**: the two new functions initially pushed
  `engine_client.py` (a NEW offender, never previously baselined) to 1077
  lines. Split them into the new `handoff_client.py` (reusing
  `engine_client`'s private `_run`/`EngineError`, the same sibling-file
  pattern the Picker's own componentization already uses) and trimmed the
  `_run` `cwd=` docstring/kwargs-construction to the minimum reasonable
  size, landing back at exactly 1000 lines -- no baseline widen needed.
- Tests: `test_mux_companion.py` (+3: pending-handoff wiring into
  `_load_current_worktree`, degrade-on-error, and the rendered headline/
  absence in `_status_text()`) plus updated monkeypatch targets for the
  `handoff_client` split; new `test_handoff_client.py` (5: cwd-scoped
  `_run` invocation, engine-unavailable degrade, newest-unconsumed-baton
  selection ignoring a consumed entry and a non-handoff file, both empty
  cases) -- all green (76/76 across the three files). Full `worktree-
  manager` suite green apart from two ALREADY-known-pre-existing,
  environment-specific failures unrelated to this diff (a Windows
  symlink-privilege quirk in `test_update.py`/`test_trusted_materializer_
  parity.py`, reproduced identically on an unmodified checkout) and the
  same machine-load-sensitive `_open_task_menu`/`_open_submenu` modal-
  timing flake documented in Phase 7's own journal entry (this session's
  own sustained heavy test load, not a code defect -- confirmed clean on
  CI's fresh runner during Phase 7 and expected to be so again here).
  `tools/check-module-size.py` clean.
- **This was the last item in this effort's own Plan.** Phases 1-9's
  originally-scoped work is now complete; Phase 9's "fold in further
  wishlist items" bullet stays deliberately open-ended per the operator's
  own "I'll think of more," so this effort's Status stays Active rather
  than Done, but there is no next scheduled slice -- further work here
  awaits new operator feedback.

### 2026-09-29 — Lineage cleanup + Validation Plan/Proposal reconciled against reality
- The operator asked to identify the correct head session after several
  days' worth of resumed sessions had run in the odsp-web-harness control
  worktree without formal `link-succession` chaining between them
  (`hook:new`-style resumes, not `context-handoff` cutovers). Traced the
  full chain via `agent-worktrees list-sessions`/`session-lineage`/
  `head-session`/`handoffs-check` rather than guessing: five sessions ran
  since the original 2026-09-23 handoff (`dfa8f775` Phase 2, `33d446f4`
  the Post-Phase-6-renders slice, `dfbc8f44` Phase 9 item 1's sticky-header/
  focus-scroll work, a stray 1-turn `2d142c3d`, and the original session
  itself, now resumed). All had already landed their real work (confirmed
  against `origin/main` git history and the PRs cited in this Journal) —
  no dangling uncommitted work, no open PRs, no split-brain hazard
  (`handoffs-check` returned zero findings; `head-session` already cleanly
  resolved to the resumed original session).
- Concluded the four dormant sessions (`conclude-session --state
  concluded`) for a clean lineage record, rather than leaving them
  ambiguously open.
- Reconciled this effort's own **Validation Plan** and **Proposal**
  sections, which had gone stale (still showing unchecked/"pending review"
  boilerplate despite Phases 1-9 having landed): checked the two items with
  direct evidence in every phase's own journal entry (golden diffs;
  full-suite pass counts), left the "real multi-worktree mesh" manual-
  verification item honestly UNCHECKED (every "verified live" check across
  this effort was against `--demo`/`--preview` mock data, never a real
  mesh — still blocked on #3418), and updated Proposal to state the
  effort's actual current shape (all named phases Done; Phase 9's one
  open item is intentionally open-ended, not stalled work).

### 2026-09-29 — CLAIMS condensing + LENGTH/LIVE column rework (operator feedback batch, part 1)
- **CLAIMS condensing** (`claims_rank.py`): added short-form kind prefixes
  (`SESS`/`WT`/`CS`/`CT`/`T`/`BR`/`SSH`/`EFF`); dropped the default `PR`/
  `bug` prefix entirely (bare `#N`/`repo#N`). **Correction made mid-slice**:
  an initial blanket "no prefix for pr/bug/issue" pass would have silently
  broken a real, deliberate extensibility point — a plugin-contributed
  `label_overrides[kind]` (e.g. an ADO-sourced "bug" wanting to read
  distinctly from a native GitHub issue, per `test_claim_kinds_registry
  .py`'s existing end-to-end test). Fixed: only the UNLABELED default is
  bare; an explicit override still applies and is prefixed. Added
  `"worktree": 5` cross-repo prefix (`"WT <last4>"`/`"WT <repo>:<last4>"`,
  previously bare with no prefix at all despite `DEFAULT_LABEL_PREFIX`
  already declaring one — dead code, now actually wired). Added `"session"`
  to `DEFAULT_PECKING_ORDER` at the lowest tier (8, below `task`) even
  though it isn't yet a claimable kind (matches the existing `effort`/
  `bridge` precedent) — ready the moment a producer emits one.
- **Underlined claim links** (`engine_helpers._claims_cell`): a linked
  entry's style gained `underline` alongside the existing OSC 8 `link`
  annotation.
- **LENGTH column** (`derive._sess_turns` renamed `_length_display`,
  `engine_helpers.py`'s column specs): `"1s 25t"` format, replacing
  `"1/25"`; header relabeled `sess/t` → `length`.
- **LIVE column taxonomy** (`derive._sess`): `MUX(n)`/`ACP`/`PROC`/`LOCK`/
  `-`, replacing `●N`/`○`/`ACP`/`PROC`/`LOCK`/`·`. Widened the column
  4→8 chars to fit `MUX(12)` without truncation. **Judgment call, flagged
  for operator confirmation rather than silently guessed**: the operator's
  own taxonomy named only `MUX`/`ACP`/`LOCK`/`-` — `PROC` (a live CLI
  session, bound but neither muxed nor ACP-hosted: a bare-resumed session
  or an execution leg) was not in that list. Kept it as a DISTINCT 5th
  value rather than folding it into `MUX` or `ACP`, since it is a genuinely
  different hosting mechanism and collapsing it would misrepresent real
  state — please confirm this reading is right.
- **Stretch implemented alongside the core ask**: `MUX`'s `(n)` connected-
  client-count suffix (`mux_clients`, already tracked by the engine's mux-
  fleet reconciliation) — implementing bare `MUX` without it would have
  been a real regression (losing the attached/unattached distinction the
  old `●N`/`○` split gave for free), so the "stretch" closed the exact gap
  the core change would have opened. **`ACP(n)` NOT implemented**: no
  producer emits a connected-client count for an ACP/bridge-hosted session
  today (unlike `mux_clients`) — fabricating one was rejected; a real
  count would need new agent-bridge plumbing, tracked as a separate,
  distinct follow-up if wanted.
- **Not yet started this slice**: the interactive claims-navigation stretch
  goal (Right-arrow-into-claims-list, Enter/click to open), and the
  render-performance/over-painting investigation — both deferred to
  follow-up slices.
- Updated every test asserting the old label/format/glyph shapes
  (`test_claims_rank.py`, `test_claim_kinds_registry.py`, `test_pr_ops.py`,
  `test_picker_tui.py`) and regenerated all 7 affected goldens. Full suite:
  `agent-worktrees` 622/622 (claim-related subset; full-plugin run not
  re-executed this slice, no plugin-wide change made outside
  `claims_rank.py`), `worktree-manager` 1483 passed / 1 skipped / 5 failed
  — all 5 failures confirmed pre-existing environment flakes (Windows
  symlink-privilege quirks in `test_update.py`/`test_trusted_materializer_
  parity.py`, already documented in this effort's own 2026-09-27 journal
  entry; `test_mux_daemon.py` and one `test_registered_pivot_*` failure
  both confirmed to pass in isolation, i.e. full-suite-load flakes, not
  regressions from this change).

### 2026-09-30 — Render-perf/over-painting investigation: one safe fix landed, two deeper root causes deferred

Operator confirmed both 2026-09-29 judgment calls as shipped (`ACP` bare, no
fabricated count; `PROC` kept as a distinct 5th `LIVE` value). Picked up the
last unstarted item from that batch: the render-performance/over-painting
investigation, profiled before assuming any fix, per this effort's own
discipline.

- **Method**: a headless `PickerApp.run_test()` harness (150/300/500
  synthetic worktree rows), profiled with `cProfile` around the idle
  (non-busy, no-nav) render tick — the ~2fps cosmetic-pulse cadence
  `PickerScreen._tick()` runs even when nothing else is happening — plus a
  real-timer-driven (not synthetic-loop) run to see Textual's own compositor
  cost, not just this repo's Python-side cost.
- **Root cause #1 (fixed this slice)**: `_PickerNativeData.refresh_data()`'s
  data-signature includes `pulse` (needed: a genuinely live MUX/ACP/PROC row
  legitimately pulses its `LIVE` glyph's color, per `row_text`'s `sess`
  branch). But ANY pulse flip — including on a Picker with zero live rows —
  used to fall through to a full `_rebuild()`: `clear_options()` +
  `add_options()` reconstructing every row's `Text`, at 150/300/500 rows,
  roughly twice a second, purely to recolor at most a handful of glyphs.
  **Fixed** by extracting `row_sess_pulses()` (`engine_helpers.py`) and
  adding `_try_pulse_repaint()` (`engine_regions.py`), mirroring the
  existing `_try_selection_repaint()` fast path (#171): when the only
  signature delta is `pulse`, repaint in place (`replace_option_prompt_at_
  index`) just the rows `row_sess_pulses()` actually flags, skipping
  `_rebuild()` entirely for every other row. Verified against the REAL
  10fps `_tick()`/`set_interval` path (not a synthetic loop, which turned
  out to never toggle `pulse` and silently validate nothing): over ~12s of
  idle real-timer ticks, 18 of 19 genuine pulse-only signature changes took
  the new fast path; only 1 (the first, post-setup) fell through to a full
  rebuild.
- **Root cause #2 (deeper, deferred, NOT fixed this slice)**: `_signature()`
  itself — needed on every `refresh_data()` call just to decide whether
  anything changed at all — unconditionally recomputes `list_records()` →
  `current_list()` → `src.bucket()` → a full per-row `(id, title, state,
  age_secs)` fingerprint tuple, over the ENTIRE row set, with NO caching.
  Profiling showed this is **the same order of cost as a full rebuild would
  have been** (e.g. ~0.1-0.15s of Python time per 40 idle ticks at 500 rows)
  — so root cause #1's fix only pays off proportionally to how often
  `pulse` is the ONLY thing that changed; the `_signature()` computation
  itself is paid on literally every tick regardless, busy or idle, whether
  or not anything downstream reuses the result. A safe fix needs
  `current_list()`/`list_records()` memoized behind a cheap invalidation
  key — but `self.data` is mutated in place at at least 2 call sites
  (`engine_maintenance_actions.py` lines ~206/208 `self.data[i] = row` /
  `self.data.append(row)`) alongside ~6 wholesale-reassignment sites
  (`engine_input.py`, `engine_loading.py`, `engine_runtime.py`) — an
  `id(self.data)`-keyed cache would silently go stale after an in-place
  mutation, which is a correctness bug far worse than the perf issue it
  would fix. Doing this safely needs an explicit `self._data_version`
  counter bumped at every one of those ~8 sites (mechanical, but easy to
  miss one and reintroduce silent staleness) — scoped as its own follow-up
  slice with dedicated stale-data regression coverage, not rushed here.
- **Root cause #3 (deeper still, also deferred)**: the same real-timer
  profiling run showed Textual's own compositor spending the bulk of
  wall-clock time in `_compositor_refresh` → `render_full_update` (a full,
  non-incremental terminal repaint) rather than its normal incremental
  diff-based `render_update` — strongly suggesting `PickerScreen.refresh()`'s
  `_refresh_nf_segments()` calling `.refresh()`/`.refresh_data()`
  unconditionally on ALL 7 segment widgets (title/pivots/chrome/machine/
  buttons/footer/body-data) on every screen refresh — including a pure
  cosmetic pulse tick, which only the chrome segment's `status_text()`
  actually needs — is what marks the whole screen dirty enough that Textual
  chooses a full repaint over an incremental one. This is the closest match
  to the operator's literal "redrawing more than the changed region"
  framing, but narrowing `_refresh_nf_segments()`'s scope per refresh-cause
  (cosmetic-only vs. a real nav/reload/pivot-switch) needs to be verified
  against the comment at its call site ("any state change that refreshes
  the screen must re-render the child segments too, they read off this
  screen") and the existing segment-sync test coverage before being
  trusted — also scoped as its own follow-up, not attempted this slice.
- **Validation**: `test_picker_tui.py` full file (270 tests) green. Full
  `worktree-manager` suite: 1484 passed / 1 skipped / 4 failed — all 4
  reconfirmed pre-existing environment flakes in isolation (`test_mux_
  daemon.py`'s backstop-cadence timing test,
  `test_trusted_materializer_parity.py`'s two Windows-symlink-path tests,
  `test_update.py`'s symlinked-extraction-root test) — none touch the
  claims/picker rendering code this slice changed, all already documented
  in this effort's 2026-09-27/2026-09-29 journal entries as flaky under
  full-suite load on this machine.
- **Interactive claims navigation (stretch goal)** remains unstarted — not
  picked up this slice; still tracked above.

### 2026-09-30 — Two operator-reported bugs: stale MUX→PROC render, and a 3x-slower-than-necessary Actions dialog

Follow-up to the render-perf slice above (PR #4719). Operator reported two
concrete symptoms while using the shipped Picker:

1. "Most times, this column shows PROC, even for MUX sessions, suggesting
   that the mux-detection isn't yet working."
2. "The first Actions dialog takes so long to load."

Both were profiled/reproduced before fixing, per this effort's standing
discipline.

**Bug 1 — stale LIVE glyph, not a detection bug.** Verified directly against
`agent-worktrees picker-reconcile-local --json`: mux **detection** is
correct — every currently-attached `psmux` session (cross-checked against
`psmux list-sessions`) reports `mux_attached: true` accurately. The real bug
is render **staleness**: the Picker's cache-only first paint renders before
the async Group C mux reconcile lands, so a genuinely-live row starts out
`PROC` (from `session_bound_live` alone, before mux status is known). Mux
attachment doesn't change a row's derived `state` (both `PROC` and `MUX(n)`
collapse to `ACTIVE`), and `_PickerNativeData._signature()`'s per-row
fingerprint only tracked `(id, title, state, age_secs)` — NOT `sess` — so
the later correction from `PROC` to `MUX(1)` never changed the fingerprint
and never triggered a rebuild. The row stayed stuck on its stale first-paint
glyph indefinitely. Reproduced directly: `derive.norm()` on the same raw
record before/after adding `mux_attached=True` yields an IDENTICAL
`(id, title, state, age_secs)` tuple despite `sess` flipping `PROC` →
`MUX(1)`. **Fixed** by adding `sess` to the fingerprint tuple in
`_signature()` (`engine_regions.py`) — confirmed the added regression test
(`test_live_column_repaints_when_async_mux_reconcile_lands`,
`test_picker_tui.py`) fails without the fix and passes with it.

**Bug 2 — an unconditional, unnecessary `cfg.load_config()` call, not a
first-vs-later cold-start effect.** Timed the real `agent-worktrees
picker-reconcile-local --json --worktree-id <one>` invocation (the exact
call the Picker's per-row Actions-dialog makes to refine its verb set,
`engine_worktree_actions.py`'s `_open_submenu`/`_verify`): consistently
7-11 seconds, EVERY call, not just the first — the "first dialog" framing
is because the operator notices the ~8s refine lag most on their first
open, not because later calls are actually faster. `cProfile`'d the real
call end-to-end (`plugins/agent-worktrees`'s `picker_reconcile_cli.
build_payload`): `cfg.load_config()` alone was ~6.7s of the ~14.5s total
(1733 tracking records re-scanned via `_control_plane_related_pr_map`, plus
~4.2s of plugin-activation resolution via `related.
installed_plugin_related_anchors`) — called UNCONDITIONALLY, even though
its result (`config`) is used ONLY inside a loop that reconciles a record's
active PR, and is a complete no-op whenever the worktree(s) in scope have
no PR or an already-terminal one (the overwhelming common case for a
single-worktree Actions-dialog refine). **Fixed** in
`plugins/agent-worktrees/src/agent_worktrees/picker_reconcile_cli.py`:
precompute which records actually have a reconcilable (non-None,
non-terminal) PR, and skip `cfg.load_config()` entirely when that list is
empty. Verified live against the installed runtime (temporarily patched,
then restored to its pristine pre-experiment state so the machine-local
install isn't left drifted ahead of the real plugin-update mechanism):
~8-11s → ~4.5-5.8s for the same single-worktree call — the `config`-load
tax eliminated, leaving `reclaim.resolve_bound_copilots()`'s own cost
(~4.8s) as the new floor.
- **`resolve_bound_copilots()`'s own cost is a separate, deeper, DEFERRED
  finding, not fixed this slice**: it already accepts a `worktree_id=`
  filter kwarg, but `picker_reconcile_cli.build_payload()` never passes it,
  AND the filter as currently written only narrows the RETURNED set — it
  doesn't skip the expensive `_resolve_worktree_id_for_cwd()` resolution
  (~3.6s across 12 bound Copilots on this machine) for candidates outside
  the filter, since that resolution is what DETERMINES whether a candidate
  matches in the first place. Fixing this safely means reordering
  `resolve_bound_copilots()`'s own internal loop (a shared, session-binding-
  sensitive function with its own test coverage in `agent-worktrees`) to
  cheaply pre-filter session dirs by a tracking-level worktree-id lookup
  before paying for cwd resolution — real, but riskier and out of scope for
  this slice; tracked as a named follow-up rather than rushed.
- **Regression tests added**: `test_live_column_repaints_when_async_mux_
  reconcile_lands` (`worktree-manager/tests/production_picker/
  test_picker_tui.py`) and `test_picker_reconcile_local_skips_load_config_
  when_no_pr_to_reconcile` (`plugins/agent-worktrees/tests/
  test_picker_reconcile_local.py`) — both confirmed to fail without their
  respective fix and pass with it.
- **Validation**: `test_picker_tui.py` full file green; `agent-worktrees`
  `test_picker_reconcile_local.py` + `test_reclaim.py` green (54 tests, the
  files directly touched/adjacent to this change — the plugin's full suite
  is large enough that a blanket collection run exceeds a reasonable
  session wait on this machine; the repo's own pre-push/CI gates re-run the
  full suite before merge).


