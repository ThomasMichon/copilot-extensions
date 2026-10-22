# picker-snapshot — the standing picker render flow

The reproducible way to turn a Worktree Manager Picker state into a **crisp
PNG** for A/B comparison, demos, audit, or sharing.

It is a thin two-step pipeline over the deterministic
[`production_picker.picker_tui.capture`](../../src/worktree_manager/production_picker/picker_tui/capture.py)
seam
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
cd worktree-manager/scripts/picker-snapshot
python install.py
```

This checks every locked dependency's seven-day age and artifact integrity
through npm's configured registry before installation. Do not substitute plain
`npm install`/`npm ci` or a different registry when admission fails.

The same entry point admits other locked tooling projects. From the repository
root, workflow helpers use:

```bash
python worktree-manager/scripts/picker-snapshot/install.py --directory .github/actions --health-module commonmark --health-module parse5
```

An explicit project requires explicit health modules; success means each
module imported after the complete locked closure passed age and strong SRI
admission. Missing or SHA-1-only registry integrity is bound to the lock's
strong digest through npm's script-disabled tarball verification.
The installer runs npm against a private snapshot of the admitted manifests
and local npm configuration, then publishes only health-checked dependencies.
Failures or concurrent input changes preserve an existing dependency tree.
Locked installation explicitly disables npm's implicit advisory audit requests:
an unavailable advisory endpoint must not delay dependency preparation.
Configured-registry release-age and strong-integrity admission, disabled
lifecycle scripts, strict peer checks and selected-module health checks remain
required. Advisory auditing is a separate operation, not this installer's
success signal.

Then, end-to-end (needs a Python env with `textual` / `rich` / `pyyaml`):

```bash
python render.py home.png                 # picker home screen
python render.py cfg.png --modal cfg      # Configuration backstage
python render.py guide --configuration-guide --zoom 1  # all Configuration steps
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

`--configuration-guide` renders Profiles, its Apply confirmation, Workers
loading/catalog/actions/details, instantiation/declaration/editing, effect
confirmation, pending receipt, discovery error, and an 80x24 catalog. It needs
the agent-dispatch Python dependencies as well as the Manager's; it reuses
provider-owned scaffolds and effects. Discovery and submission are patched to
identifier-neutral fixtures, so no operator state is read or changed and no
worker is activated. The current Workers manifest and production Textual
compositor are used, including full-content `Screen` editors and receipts.
The [illustrated guide](../../../docs/configuration-workbench.md) records source
revisions and the single regeneration command for its committed PNG assets.

## Files

- `render.py` — end-to-end: capture a demo state → SVG → PNG.
- `configuration.py` — Configuration guide scenarios (UI-only fixtures).
- `svg2png.mjs` — the SVG→PNG rasterizer (Fira Code, 3×). Reusable for any
  Rich/Textual capture SVG.
- `package.json` — the `@resvg/resvg-js` dependency.
- `install.py` — configured-registry age/integrity admission and locked install.
- `.fonts/`, `node_modules/`, and rendered `*.png` / `*.svg` are gitignored.
