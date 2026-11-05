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
The four `timeline-current-*.svg` comparisons add independent pending, cleared,
partial and unknown live-input qualification while retaining the same historical
request and compaction occurrences. They use strict synthetic wire DTOs and the
production summary renderer; a cleared live set does not erase the historical
request, and partial/unknown coverage never means complete absence.
`timeline-session-gap.svg` retains the same historical occurrences and expanded
detail while showing an explicit retention-gap diagnostic and unavailable source.
The purpose, source qualifications, selection, details, controls and unread
count are compared as rendered, not reconstructed from DTOs.

The sparse presentation keeps purpose, compact qualification and work rows in
view, while the collapsed **Evidence** area retains exact diagnostics and
selection controls. The session-gap comparison explicitly opens Evidence;
ordinary session selection pauses membership without manufacturing a failure.
`companion-full.svg` and `companion-narrow.svg` additionally compare the
production app's chrome at 110 x 40 and 60 x 24 cells. `companion-fault.svg`
keeps a genuine preparation-failure indicator and navigation-failure message
visible while Evidence is collapsed; `companion-evidence.svg` opens the bounded
details. These four use fixed synthetic observations and reject owner reads,
not actual provider integration. All deterministic comparisons use an explicit
UTC display zone; the ordinary presenter uses the machine's local zone.

`companion-output.svg` and `companion-output-narrow.svg` compare 46 fixed
three-provider records as eight overview blocks, including two separate
20-record output groups and an unknown diagnostic that stays visible.
The narrow/fault fixtures also require live-following after automatic layout
clamps: resize is not user browsing, and the newest row retains selection.
The output fixtures are synthetic rendered comparisons; the separate actual
EventLog/codec/widget packet checks raw-history and actor boundaries.

These are rendered fixture screens, not native tmux/PSMux screenshots,
installed-runtime evidence or complete application acceptance. Only remote
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
refusal and selected-head retirement. The private peer uses the production semantic route generator, admission,
projection and EventLog wakes on an isolated owner loop, not a running Bridge
HTTP service or SDK actor. The owning CLI negotiates a private fixture peer;
HTTP authentication retains its independent Bridge coverage. Exact subscriber
retirement is observed through the route's bounded lifecycle backstop.

`tests/test_companion_current_requests.py` additionally retains four
`current-*` full-app progress captures from actual parked local callbacks, the
owning CLI and Textual, with no retained events at all. It verifies both input
and permission waiters, clearing, lost coverage and disconnection without focus
transfer or answering. These full-app captures remain progress evidence rather
than deterministic golden comparisons. Dated SVG/PNG progress exports and
curated screenshots are stored separately from source qualification; neither
qualifies an installed runtime or native mux client.

`tests/test_companion_joined_lifecycle.py` retains ordered `sidecar-state` /
`sidecar-svg` pairs for three-owner browsing, an actual expired history boundary
and its explicit Refresh recovery. Its full-app captures use actual private
owner processes and live timestamps; they are progress evidence, not goldens.
The stable gap comparison above instead uses deterministic synthetic records
with the production source-error and summary renderers.
Its healthy/full and healthy/narrow captures now use a separate ordinary
three-owner scenario with contemporary synthetic clocks, observed local waiter
coverage and deliberately unknown root CLI attention. It checks keyboard
selection through Evidence, collapsed error-free source admission, at least
half the terminal height available to the work canvas and exact reader retirement.
It is a healthy integration demonstration, not a negative fixture presented as
normal operation or an installed/native qualification.
