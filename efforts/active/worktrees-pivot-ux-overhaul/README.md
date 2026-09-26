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
  "lemons"/"GLaDOS" assertions and row-count checks stay valid untouched.
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


