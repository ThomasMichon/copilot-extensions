# Phase 2 — Native Textual components for the Worktrees table

## Starting premise (corrected mid-audit)

The outer *container* question was already answered before this effort:
`_PickerNativeData(OptionList)` (`engine_regions.py`, #88 NF5-5) already
replaced hand-rolled scrolling/focus with a native Textual `OptionList` --
native cursor, scroll, click, and accessibility are already in place. What
remained genuinely open was the **row content model**: each `Option` is
still one pre-formatted `rich.Text` blob (via `engine_helpers.row_text`),
and a "row" is actually **two adjacent options** (a focusable title option +
a disabled detail option stitched together) rather than one atomic native
item.

## DataTable -- ruled out, structural blocker

Checked concretely against `WorktreesView`'s actual layout, not just in the
abstract:

- **No colspan.** Section bands (`── Active ──`, `── Recent ──`, ...) have no
  `DataTable` equivalent -- it has no full-width decorative/divider row.
  This is a hard structural mismatch, not a preference.
- Two-line-per-record (title + `_detail_line`) doesn't map onto DataTable's
  one-row-per-record grid without an awkward `height=2`-cell hack.
- Migrating the row-content model to `DataTable`'s per-cell schema would
  fork the shared `pivot_manifest.py`/`fit()`/`row_text()` column model away
  from every sibling pivot (built-in and contributed), unless the whole
  shared machinery moved in lockstep -- a much larger, cross-cutting change
  than this phase scopes.

**Not pursued further.**

## ListView -- plausible; spike built and run against real data

Unlike `OptionList` (Rich-renderable options only), Textual's `ListView`
mounts a real composed widget per `ListItem` -- makes two things possible
that `OptionList` cannot do without a bigger rebuild:

1. **One atomic native item per record** (title + detail merged into one
   composed `Vertical(Static, Static)`), instead of today's two adjacent
   options.
2. **A real interactive `Checkbox` widget** for multi-select, instead of a
   painted glyph baked into the row's `Text`.

### Spike

- `worktree-manager/src/worktree_manager/production_picker/picker_tui/listview_proto.py`
  -- a standalone `ListViewPivotProto` (`textual.app.App`) that renders
  `(cols, sections)` through `ListView`/`ListItem`(`Checkbox` +
  `Vertical(Static, Static)`), reusing the SAME shared `fit()`/`row_text()`/
  `header_text()` helpers every pivot already shares. Section bands render
  as disabled `ListItem`s, mirroring `OptionList`'s existing disabled-option
  convention.
- `worktree-manager/scripts/listview_proto_compare.py` -- drives the REAL
  `PickerApp` (v1, `OptionList`) and the spike (v2, `ListView`) against the
  *same* derived fixture (the full Aperture Labs demo roster, `demo.py`),
  and writes `.txt`/`.svg` captures for both, using the existing
  `capture.py` seam so the comparison is apples-to-apples.

Neither file is wired into any pivot's real render path or an opt-in flag
yet -- purely additive, zero risk to the existing `OptionList` path or its
golden screenshots.

### What the spike actually showed (evidence, not guesswork)

- **Works.** All 25 mock records + 3 section bands rendered correctly
  through `ListView`, fed by real `derive.bucket()`/`fit()`/`row_text()`
  output -- confirms the container/composition model is functionally
  compatible with the existing column-declarative data shape.
- **Default `ListItem` chrome is visually heavier than v1.** Every item
  renders with Textual's default top/bottom "rule" border
  (`▊▔▔▔▔▔▎` / `▊▁▁▁▁▁▎`) even when not focused -- v1's rows are flat, with
  only a checkbox glyph in the margin. Suppressing this needs explicit CSS
  (not yet done in the spike) to match today's clean aesthetic.
- **Height/CSS defaults fight the layout.** A naive `Vertical`/`Horizontal`
  composition stretches to fill all remaining vertical space (`height: 1fr`
  inherited defaults) unless every level is pinned to `height: auto`/`1` --
  found and fixed during the spike, but a real integration needs the same
  care applied consistently, plus verifying it holds across terminal
  resizes.
- **`ListView.append()`/`.extend()` return an `AwaitMount`** that must be
  awaited -- a synchronous `on_mount` silently mounts only the first item.
  Any real integration must build `on_mount` (or equivalent) as async.
- **Checkbox works but is visually subtle unchecked** (`▐ ▌`-style glyph,
  easy to miss against a dark theme) -- a normal theming/contrast pass, not
  a blocker.
- Row density (visible records per screen height) came out roughly
  comparable to today's `OptionList` body for the same 118x44 capture size,
  since both are 2-lines-per-record.

### Cost still not incurred by the spike

A real integration additionally needs to re-implement, for `ListView`, the
bridge machinery `_PickerNativeData` already built for `OptionList`:
scroll-position preservation across data rebuilds, the sticky pinned-section
header, the single-row incremental repaint (#171, to avoid a full rebuild on
every live-pulse tick), and the two-way `sel` <-> native-cursor sync. None of
that was attempted in this spike -- it is real, comparable-sized follow-up
work, not a detail that disappears once the container is swapped.

## Decision

**Plausible, not free.** `ListView` is the right native target *if* the
Worktrees pivot (or a future contributed pivot) wants atomic rows +
first-class interactive per-item widgets (native checkboxes now, room for
richer per-row affordances later). `DataTable` is ruled out on structural
grounds (no colspan for section bands). `OptionList` (today's approach)
remains the immediate default -- it already delivers native focus/scroll/
click/a11y and has a working, tested bridge; nothing about this phase's
audit makes today's approach wrong or urgent to replace.

**Recommendation:** introduce a "v2 pivot" render mode, **opt-in per pivot**,
so no existing pivot's behavior, golden screenshots, or bridge machinery
changes by default:

- Add a `render_mode` concept (default `"v1"`/`OptionList`, opt-in `"v2"`/
  `ListView`) to the built-in pivot placement table (alongside
  `PIVOT_PLACEMENT` in `engine_helpers.py`) and/or `pivot_manifest.Column`
  for contributed pivots.
- Land the real `_PickerListViewData` widget (production version of this
  spike) with the bridge parity above (scroll preservation, sticky header,
  incremental repaint, `sel` sync) as its own follow-up phase/issue --
  tracked, not folded into this phase.
- Only once that lands would any BUILT-IN pivot (Worktrees included) be
  considered for opt-in -- and only as an explicit, reviewed decision, not a
  silent default flip.

This phase's own scope (audit + decision, Plan items above) is complete with
this document; the opt-in `render_mode` plumbing + hardened `ListView` widget
is filed as its own follow-up rather than executed inline here, per the
effort's phase-by-phase cadence.
