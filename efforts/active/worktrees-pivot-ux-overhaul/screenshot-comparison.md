# Worktrees pivot: then vs. now (Phase 1 baseline comparison)

Part of the `worktrees-pivot-ux-overhaul` effort's Phase 1 (golden-screenshot
baseline for visual regression). Compares the original Textual-picker-era
screenshot against the current production Picker's rendering, both of the
same pivot (Worktrees), to separate **intentional** transformation from
**accidental** drift before the later phases (3, 4, 5, 6) start changing it
further.

## The original — `docs/assets/worktree-picker.png`

Committed 2026-07-25 (squash `6633e843a`), unmodified since. Its own title bar
reads `Agent Worktrees · v1.0.0 ✓` — an early, multi-machine capture (three
machines: Nova, Orbit, Atlas; several environments each), `9 active · 3 recent
· 6 done`.

```
ID     STATE    MACHINE ENV       AGE    SESS   PR        TITLE
── Active ──────────────────────────────────────────────────────
9578   ACTIVE   Nova    WSL       27m    ●1     —         + Ship the ...
...
```

Columns, left to right: **ID · STATE · MACHINE ENV · AGE · SESS · PR ·
TITLE**. `SESS` carries exactly the live-indicator glyph vocabulary the
current `_sess()` helper still produces today (`●1` attached mux client,
`o`/`○` a live-but-unattached mux session, `·` idle) — this is a **stable,
unchanged primitive**, just under a different column name.

## The current rendering — `tests/production_picker/goldens/picker/scenario_mixed.txt`

Single-machine capture (this effort's Phase 1 golden), same design language:

```
ID     STATE    R   AGE    LIVE   T     PR
── Active ──────────────────────────────────────────────────────
m001   ACTIVE       15m    ●1      12   —
```

Columns, left to right: **ID · STATE · R · AGE · LIVE · T · PR** (no
`MACHINE ENV` column in this single-machine view — see below).

## What's intentional vs. what's drifted

| Then (`v1.0.0`) | Now | Assessment |
|---|---|---|
| `SESS` column, glyph vocabulary `●N`/`○`/`·` | Renamed `LIVE`, same glyph vocabulary, same helper (`_sess()`) | **Intentional, cosmetic.** Same primitive, clearer label. |
| No dedicated turn-count column (turns weren't shown at all) | New `T` column (`turn_count`) | **Intentional addition.** Turn count is genuinely new, useful information. |
| No `R` column | New `R` column, currently rendering `—`/blank for every fixture row observed so far | **Undocumented addition — this is exactly wishlist item #5's complaint.** No comparable predecessor exists in the original design to explain its intent; nothing in the codebase's derive/render layer this effort has read so far names a clear semantic for it. Phase 5 should determine its actual source field and either give it a legible label/rendering or fold it into another column (the `T`/`LIVE` pairing is the natural candidate, per wishlist item #6). |
| `MACHINE ENV` column (multi-machine "All" view) | Not present in a single-machine capture | **Not a regression** — the original screenshot's own header shows `All` selected across 3 machines; a single-machine capture (this effort's goldens, and an operator's common case) never needed that column. Worth confirming during Phase 2/3 that the multi-machine "All" view still carries it today (out of this comparison's scope; flagged for a later phase if not). |
| `PR` column, `#NN·op`/`#NNv`/`—` | Same column, same format (`_pr()` unchanged) | **Unchanged.** This is exactly the column Phase 4 standardizes as `CLAIMS` — today it already carries real information, just PR-only (no non-PR claims yet). |
| Active/Recent/Completed section grouping, dim/bright state palette, `N active · N recent · N done` header | Identical | **Unchanged core design language**, two months and hundreds of commits later — the layout has held up well; only the column set has evolved. |

## Conclusion for later phases

The Worktrees pivot's fundamental design (sections, palette, header
counters, ID-first row shape) has been stable since `v1.0.0`. The concrete,
actionable deltas this comparison surfaces:

- **Phase 5** (replace the `R` column) has no historical precedent to
  reconcile with — it's free to redefine or retire the column without
  breaking continuity with the original design.
- **Phase 6** (SESS/TURNS) can look at the original's single combined `SESS`
  column as prior art for a **compact combined indicator**, now that turn
  count (`T`) has grown into its own column alongside it — the original
  never needed to combine session-count with turn-count because it only
  showed session live-state, not history.
- **Phase 4** (CLAIMS) is renaming/standardizing an already-two-month-stable
  column (`PR`), not inventing new data.
