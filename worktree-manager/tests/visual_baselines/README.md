# Sidecar timeline visual baselines

These SVGs capture the production `CompanionTimelinePanel` and
`SubwayTimeline` through Textual's real compositor at 100 x 32 cells, with
the `textual-dark` theme and deterministic synthetic owner observations.
`timeline-live.svg` follows the newest event; `timeline-browsing.svg`
expands an older event and retains its viewport when one new event arrives.
The purpose, source qualifications, selection, details, controls and unread
count are compared as rendered, not reconstructed from DTOs.

These are rendered fixture screens, not native tmux/PSMux screenshots,
installed-runtime evidence or a complete Companion-app baseline. Only remote
font-loading declarations are omitted; text, styles, coordinates, clipping
and geometry are retained. SVG viewers use their available monospace font.
The regression compares SVG rendering instructions, not browser-rasterized
pixels, so browser/font differences are outside its scope.

From the repository root, run the opt-in packet through the bounded supervisor:

```sh
test-supervisor -- python3 tools/run-plugin-tests.py worktree-manager \
  --test-file tests/test_companion_visual.py --allow-explicit-tiers \
  --timeout 120 --plugin-timeout 120 --junit-dir "$ARTIFACTS"
```

`ARTIFACTS` must be a fresh owned artifact directory. Each JUnit test case
includes `sidecar-state` and `sidecar-svg` properties, even on comparison
failure (pytest warns about these extension properties with its default xunit2
format, but retains them). To inspect actual renders, extract those properties from
`$ARTIFACTS/worktree-manager/*.xml` into new SVG files. No update mode silently
accepts new output: inspect both screens and review the source change before
replacing a baseline in a normal reviewed commit. Preserve mismatch captures
outside the checkout. This packet does not enter the required unit-only lane.
