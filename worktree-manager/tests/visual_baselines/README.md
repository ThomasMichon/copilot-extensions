# Sidecar timeline visual baselines

These SVGs capture the production `CompanionTimelinePanel` and
`SubwayTimeline` through Textual's real compositor at 100 x 32 cells, with
the `textual-dark` theme and deterministic synthetic owner observations.
`timeline-live.svg` follows the newest event; `timeline-browsing.svg`
expands an older event and retains its viewport when one new event arrives.
`timeline-session.svg` shows the selected-session control, paused membership
evidence, a historical input occurrence and an expanded compaction occurrence
with unknown pre-window qualification and durability. It uses synthetic data,
not a provider-measurement claim.
The purpose, source qualifications, selection, details, controls and unread
count are compared as rendered, not reconstructed from DTOs.

These are rendered fixture screens, not native tmux/PSMux screenshots,
installed-runtime evidence or a complete Companion-app baseline. Only remote
font-loading declarations and blank-line indentation are omitted; text, styles,
coordinates, clipping and geometry are retained. SVG viewers use their available monospace font.
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

The separate `tests/test_companion_bridge_session_source.py` packet retains an
`integration-session` SVG property from the actual full Companion app after
keyboard selection and semantic live delivery through private synthetic
process pipes. It is an in-progress capture, not a deterministic baseline:
its real fixture event timestamps vary. This packet also checks finite history,
independent history cancellation, empty-to-first-event continuity, capability
refusal and selected-head retirement. The private peer uses production semantic
admission/projection and EventLog wakes, not a running Bridge HTTP service or
SDK actor; HTTP authentication and subscriber accounting retain their owning
Bridge test coverage.
