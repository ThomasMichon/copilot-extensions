# picker-snapshot — the standing picker render flow

The reproducible way to turn a picker state into a **crisp PNG** for A/B
comparison, demos, audit, or sharing (agent-worktrees #88 NF work).

It is a thin two-step pipeline over the deterministic
[`picker_tui.capture`](../../src/agent_worktrees/picker_tui/capture.py) seam
(vision item **A** — auditable-testable-rendering):

1. **Capture → SVG** (Python). `capture.capture(...)` renders a picker state
   headlessly to a Rich SVG; `capture.capture_modal(...)` does the same for a
   native `ModalScreen` (composited app). Same inputs → same grid.
2. **SVG → PNG** (`svg2png.mjs`, Node + `@resvg/resvg-js`), rendered with the
   SVG's **own Fira Code** font at 3× zoom.

## Always render with Fira Code (the one rule)

Rich lays out the SVG by placing every glyph at an x-coordinate computed from
**Fira Code**'s metrics and names it via a CDN `@font-face`. If you rasterize
with a *different* monospace (e.g. Consolas), its glyph advance won't match
Rich's cell grid, and the **box-drawing border characters stop tiling** — the
borders render choppy/gappy. So `svg2png.mjs` always renders with Fira Code
(auto-downloaded and cached to `./.fonts`), and `loadSystemFonts` only backfills
the few glyphs Fira Code lacks (the ⚙ gear). Do **not** "fix" an offline-font
error by substituting the font; supply Fira Code.

(The picker itself and the capture SVG are unaffected either way — a real
terminal always paints on its own cell grid. This only matters when rasterizing
the SVG to a bitmap.)

## Usage

Once, to install the Node dependency and cache the font:

```bash
cd plugins/agent-worktrees/scripts/picker-snapshot
npm install
```

Then, end-to-end (needs a Python env with `textual` / `rich` / `pyyaml`):

```bash
python render.py home.png                 # picker home screen
python render.py cfg.png --modal cfg      # with the Configuration modal open
python render.py steer.png --modal steer  # Markdown card + recommended defaults
python render.py verdict.png --modal steer-verdict  # recommended verdict selected
python render.py reject.png --modal steer-reject  # rejected-feedback follow-up
python render.py prior.png --modal steer --card-json tasks.json --task-id <id>
python render.py home.png --zoom 4        # crisper, larger file
```

Or rasterize an SVG you already captured:

```bash
node svg2png.mjs some-capture.svg out.png 3
```

`--card-json` accepts a raw card object, one task object containing `card`, or a
task-list JSON array. Use `--task-id` to select a specific task from a list. This
keeps recovered real-world examples out of source while still rendering them
through the worktree implementation. It applies only to `--modal steer`;
`steer-verdict` and `steer-reject` intentionally use the synthetic fixture.
Never commit a recovered-card raster to this public repository; committed demos
must use the identifier-neutral synthetic fixture.

## Files

- `render.py` — end-to-end: capture a demo state → SVG → PNG.
- `svg2png.mjs` — the SVG→PNG rasterizer (Fira Code, 3×). Reusable for any
  Rich/Textual capture SVG.
- `package.json` — the `@resvg/resvg-js` dependency.
- `.fonts/`, `node_modules/`, and rendered `*.png` / `*.svg` are gitignored.
