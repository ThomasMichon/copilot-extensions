# Live Sidecar timeline and push-observation implementation

Parent contract: [README.md](README.md), Step 7 and its Validation Plan.
Coordination: #8811. Standing intent: [Mux Companion](../../../visions/mux-companion/README.md).
Related presentation: [timeline guidance](timeline-guidance.md).
Proposed data boundary: [observation contract](sidecar-observation-contract.md).

**Status: reviewed implementation/validation plan; source execution active.**
Plan #9960 merged after current-head approval and required CI. The new widget,
observation feeds and classifier remain subject to the unchecked gates below.
The foundation source merged in #9979 as
`74d9254007c1b06c0f058e20bc6a8cad05f3a8ad`, including the tested alternate
Timeline view and unattached owner projection/evidence primitives. This closes
S1 source delivery, not feed, qualified-measurement, joined-app or native
acceptance.

The next source slice merged in #10131 as
`3acbd63fd221d7d82857772fee485087a68ae6c0`: the selected worktree Manager
adapter, rich agenda owner reader, safe process-local committed-save capture
and known-session Bridge provider. The [merge readback](journal/2026/11.02-sidecar-owner-consumers.md)
records their exact source tiers and continuing gaps; it does not close S2-S5.

#10199 merged as `a9645aa98b9a4217274cc61aa36a073806413a34`, delivering
qualified resident mutation intake, compact native evidence in Manager,
bounded Bridge membership and the reviewed data-lifetime design.
The [source readback](journal/2026/11.03-sidecar-native-membership.md) retains
the separate consumer, archival, measurement, native and original parent gates.

#10224 merged as `3da6f1c3e534fd2df0b65775ee17c6d252ec9c42`, delivering
worktree-owned durable placement and passive exact-store discovery.
The [D1 source readback](journal/2026/11.03-sidecar-durable-placement.md) retains
native durability, efforts initialization/export, consumer and archival gates.

#10238 merged as `ca938fd8f998933624accae2ce8371001f58f24c`, delivering
explicit new managed agenda initialization and complete consistent export/
readback. The [D2 source readback](journal/2026/11.03-sidecar-managed-agenda-export.md)
retains Manager/Bridge consumers, logger/rescue, native and original parent gates.

#10251 merged as `8b01db9aff9064434c839d2275eada0d27f876da`, delivering D3's
managed rich Agenda consumer through the existing single permanent subscription.
The [source readback](journal/2026/11.03-sidecar-rich-agenda-consumer.md) records
294 coupled controls, required unit qualification and the actual approved merge.
Its remaining Bridge consumption and three-source work is continued below.

#10392 merged as `e9a45cf979fc65a9fc4194fa02cf72325f898e67`, delivering
passive Bridge membership consumption, independent source admission and the
first isolated three-owner demonstration. The
[source readback](journal/2026/11.04-sidecar-bridge-membership-consumer.md)
records 296 coupled controls and synthetic visual baselines. Known-session
event/history consumption follows in #10435 below; logger/rescue, native and
original parent gates remain open.

#10435 merged as `03548e10d689419f3e887b0ac625c1df3c5669df`, delivering
the supported selected-session semantic consumer, independent backward/live
positions and selected-head/reader retirement controls. The
[source readback](journal/2026/11.04-sidecar-known-session-consumer.md) records
174 coupled cases and the final 41-case pending-admission correction without
claiming current answerability, qualified PRE, installed or native acceptance.

#10447 merged as `c3437ffe05261e7b71c2763517133b6f36f46d45`, delivering D4's
policy-qualified typed export collection/readback library and retained-namespace
fencing. The [source readback](journal/2026/11.04-sidecar-collection-library.md)
records 876 coupled controls, separate depth/Windows-API controls and continuing
owner-discovery/orchestration, archival, rescue and native gates.

#10488 merged as `afb014880eccdb19762c122f50b213845acd7de5`, delivering passive
project-wide retained-descriptor inventory through the existing Worktrees owner.
The [source readback](journal/2026/11.04-sidecar-retained-inventory.md) records
166 coupled controls and the unchanged admitted unit packet. Contributor
consumption and existing-tick integration remain open; D4 is not complete.

#10495 merged as `becc1eae724aeb717e4aba941704a71f0c6b406a`, delivering
Efforts-owned complete contributor provenance from the same verified export
snapshot while preserving the original manifest and export/readback contracts.
The [source readback](journal/2026/11.04-sidecar-export-contributors.md) records
49 owner controls and the existing independent Logger client's compatibility
packet. Logger provenance consumption and automatic discovery/orchestration
remain open; no actual collection or native/installed acceptance is implied.

#10500 merged as `2bdc57d5119bf331a5b265daeacca6e613a32d54`, delivering Logger's
optional complete provenance retention and revision-bound fresh local readback
without changing original v1 calls. The
[source readback](journal/2026/11.04-sidecar-logger-contributors.md) records 62
isolated cases, including negotiated owner-process joins. Actual selected-provider
discovery and existing-tick integration remain open; D4 is not complete.

#10503 merged as `3690c993d9e519b8d065d29853e11eff38bdfaa5`, delivering explicit
passive exact-repository retained-inventory and effective Efforts-provider
discovery. The [source readback](journal/2026/11.04-sidecar-logger-provider-discovery.md)
records 120 isolated controls, the corrected lossless origin-identity boundary
and actual reviewed merge. Automatic export/collection and existing-tick
orchestration remain open; this does not complete D4 or authorize live effects.

#10512 merged as `f2143b1467c72bab36ca30839f4ab0b8d8b93773`, delivering explicit
policy-first managed export/collection composition and fresh local readback.
The [source readback](journal/2026/11.04-sidecar-managed-export-composition.md)
records 224 isolated controls, the corrected shared origin-probe deadline and
actual reviewed merge.

#10520 merged as `00e34cfad46186b9107b6febe6b3103f40d58bc6`, connecting the
same composed boundary to the existing sync tick with strict machine-local
opt-in, shared execution limits and independent collection-context cursors.
The [source readback](journal/2026/11.04-sidecar-existing-tick-collection.md)
closes D4's source integration, not D5 legacy preservation, actual archive
acknowledgment, installed/native qualification or any original parent gate.

#10528 merged as `7b48858d2b21dfa819c14ca34013dff973c14b69`, delivering
conservative checkout-local agenda preservation and actual isolated retirement
of checkouts with independent managed data. The
[source readback](journal/2026/11.04-sidecar-checkout-preservation.md) closes
that source preservation criterion, not D5 rescue, restoration/reclamation,
actual archive acknowledgment or native/installed acceptance.

#10539 merged as `20c140f142b273ec132b8d740240ed0dd3135e02`, qualifying
supported checkout-local format-2 export/readback before actual isolated
finalization without releasing the original data hold. The
[source readback](journal/2026/11.04-sidecar-rescue-qualification.md) preserves
D5's operational gates and continues S3's live input/permission evidence work.

#10579 merged as `e335da6d7baee5a0569b17a4a0978f4eb9d05a29`, completing the
supported local live-input source chain from #10551/#10557 through the selected
CLI and actual Manager display. The [source readback](journal/2026/11.04-sidecar-live-request-contract.md)
retains qualified PRE, complete source coverage, joined/native/performance and
every original parent gate.

#10595 merged as `71d213e1964dbea3ce0f8f161520a4734b75fe2c`, qualifying
supported private-source browsing, blocked history/live overlap, actual waiter
clearing, gap recovery and observer retirement on the joined app. The
[source readback](journal/2026/11.04-sidecar-joined-lifecycle.md) preserves
complete source coverage, qualified PRE, installed/native/performance and
every original parent gate.

#10605 merged as `03ba13942a119499df30338719dbc1f771ba07fc`, delivering
bounded geometry/reference reuse, short-content upward-wheel history intent
and reproducible source-process scale observations. The
[source readback](journal/2026/11.04-sidecar-bounded-render.md) distinguishes
deterministic resource/viewport acceptance from still-open native, installed
and complete performance qualification.

#10612 merged as `e6d154b53ecf38a8434a2c2e3ac2d7ded9376df2`, qualifying
actual app input through an attached private tmux client with a synthetic
wire-valid owner. The [source readback](journal/2026/11.04-sidecar-native-client.md)
records browsing, push, resize, return/reopen, pane/PID and cleanup controls;
Windows/PSMux, popup, installed and native performance remain separate.

#10642 merged as `4a66209b675326368420c3a564b764beb57882a9`, delivering
the requested sparse concept-fidelity correction and diagnosed source/error
handling fixes. The [source readback](journal/2026/11.05-sidecar-sparse-presentation.md)
records healthy ordinary/narrow demonstrations, twelve rendered comparisons
and exact evidence limits without closing S2-S5 or original parent acceptance.

#10670 merged as `19b14d213389ed519ac1881487bb17471d254073`, qualifying
actual private tmux popup input, owned-read cancellation and original CLI
return with a complete pre-dispatch invocation log and negative control.
The [source readback](journal/2026/11.05-companion-popup-input.md) preserves
installed/native Windows, Relay and original manual/head acceptance.

#10680 merged as `43a7a76bc0a030c499f31b7bfd784962e907c14c`, carrying
reported tool/agent labels through the actual semantic source and virtual
rows. The [source readback](journal/2026/11.05-sidecar-reported-work-labels.md)
records literal-label fallback, mixed-provider absence and source-bound
HTTP-52 qualification without turning occurrences into current concurrency
or lifetime totals.

#10686 merged as `aef3a5940200739d096dbb293a9241bee4d37768`, preserving
valid reported subagent identities in distinct source-scoped lanes without
inventing current state or hierarchy. The
[source readback](journal/2026/11.05-sidecar-reported-actors.md) retains
authoritative totals, full coverage, native and original parent gates.

#10689 merged as `e608ec4ba8163eab26f5db56b4b753a200c4cae9`, admitting
the [bounded source-output presentation amendment](sidecar-output-presentation.md)
before implementation. The [plan readback](journal/2026/11.05-sidecar-output-plan.md)
retains all source, native and original parent completion gates.

#10698 merged as `0f6de221b4658c2db1408fa0216e35d1d93db5df`, delivering
bounded known-output groups, raw Evidence, explicit single-page history demand
and stable group/navigation anchors. The
[source readback](journal/2026/11.05-sidecar-output-groups.md) records 108
checks, fourteen rendered comparisons and immediate Follow-live reflow without
claiming accumulated tool statistics or installed/native acceptance.

#10707 merged as `d15ec3d21d462dadc7b3c1923d16fa3f3c9be159`, delivering
the [statistics amendment](sidecar-tool-statistics.md)'s internal owner
projection at existing EventLog construction/lifecycle seams. The
[source readback](journal/2026/11.05-sidecar-tool-statistics-owner.md) records
143 selected checks and source-bound HTTP-52 capture/verification.
Optional provider/CLI negotiation and Manager consumption remain pending;
this is not lifetime accounting, current concurrency or whole-S2 acceptance.

#10712 merged as `6abf06b3ed22290d2f84f6b293a00e004400729a`, expanding
the same statistics projection through independently optional parser/HTTP/CLI
support. The [provider readback](journal/2026/11.05-sidecar-tool-statistics-provider.md)
records 123 focused cases, the 91-case current HTTP-52 capture/verification and
the corrected existing pure-unit admission. Manager consumption follows in
#10718 below; the whole S2 row is not closed.

#10718 merged as `742c755ff45fb75c960900d499c8955ed35ff856`, consuming
the negotiated statistics on the existing held reader with one shared passive
declaration, independent optional rollback and coalesced qualified labels.
The [source readback](journal/2026/11.05-sidecar-tool-statistics-manager.md)
records actual large-range/default-window eviction and healthy ordinary/narrow
controls. The bounded statistics amendment closes, not whole S2, lifetime
accounting, current concurrency, qualified PRE or installed/native acceptance.

#10835 merged as `2faf32de089180615aea3c145b114ed8c4e8f46e`, adding an
optional actual-call cold membership diagnostic and correcting the native
fixture's DELETE-sharing, cancelled-predecessor wait and asynchronous menu-key
boundaries. The [diagnostic readback](journal/2026/11.05-sidecar-membership-diagnostics.md)
records exact-head Windows 120, Linux Companion 117 and root-attention 43
passing controls. It does not explain the earlier 50 ms read failure or change
production budgets, retries or cancellation; the reliability gap stays open.

#10848 merged as `16d1c78f5049e52cda978fbf5d93faeae42f8afe`, qualifying
exact-owner indexed lookups through ordinary writable bootstrap, with unchanged
passive old-store behavior and read budgets. The same diagnostic readback
records source-bound HTTP52 capture and current Windows lookup/client proof;
the separate cold timeout and complete source/native acceptance remain open.

#10855 merged as `fb57382abb5ee47b1db29980fa3c2ee49f0be05f`, qualifying
the supported owner-codec packet and correcting contradictory membership
replay admission. The [codec readback](journal/2026/11.05-sidecar-codec-boundaries.md)
records 242 passing source controls, real global-sequence skips and preserved
unknown custody. Complete source coverage, history/append-failure acceptance
and every remaining native/installed/original parent gate stay open.

#10858 merged as `03272eb713c7f9bbfaa441e777f8f30ccfde298d`, reconciling
274 supported-source history/live splice and gap controls. Its
[readback](journal/2026/11.05-sidecar-history-gaps.md) distinguishes
owner append failure, Agenda atomic rollback, partial Bridge observations
and actual three-owner recovery without extending installed/native acceptance.

#10907 merged as `32b14dbc315c180bbd7c6d1f089ddb1a10f67d5b`, correcting
the separate Windows deadline-quantization defect reproduced by #10901.
The [diagnostic readback](journal/2026/11.05-sidecar-membership-diagnostics.md)
records unchanged 50 ms/VM limits, source-bound capture and exact-source
Windows creation/no-early-refusal qualification. Historical cold-read causality,
startup stability, complete source coverage and original parent gates stay open.

## Guiding intent

Make the existing same-window Sidecar a useful live work overview, rather than
a second low-level chat view or a larger static tree. Its current effort/purpose
leads; original creation context remains expandable provenance. A subway-style
timeline keeps the live head at the bottom and lets the operator browse older
history without incoming updates pulling the viewport away.

Reuse the worktree, agenda and Bridge owners. The Sidecar consumes evidence; it
does not become another claims, session, progress, accounting or event authority.

## Request

The operator requested:

> Great. We might as will dig into the Mux Companion work; take stock of where it's at and what the next steps are. I'd morphed the effort from just having a dialog overlay to also have a worktree-manager sidecar app that could be swapped into the mux pane for a given Copilot worktree; the user can then flip back and forth within the Mux session. Compared to Copilot's low-level chat view, the Sidecar would show an agenda-level "live" view, provide indicators for claims, parallelism, sub-agents, bridge-outs, etc, and also have a notification for when the Copilot session was blocked waiting for input, or was experiencing problems like getting compacted prematurely (I've seen this happen a lot lately). The Sidecar presents a subway-map timeline view (you designed this already) which has a live head at the bottom, and virtual-scrolls upward to see history. This will really stress our Textual capabilities. The Sidecar should be able to subscribe to the worktree-manager, agent-worktrees, and agent-bridge state feeds from the worktree, to receive push-updates; it should barely ever have to poll.

The operator clarified:

> Sounds good. I have been seeing Compaction events occur at 28%, 35%, etc, not the 65% or 75% context usage I expect. Anything below 65% I consider a premature compaction.

Then authorized planning and execution:

> Right. Continue buildout out your implementation plan and start work when ready

The generic detector has a configurable policy. The 65% value is the explicitly
selected observer policy in this request, not a new universal runtime compaction
or handoff threshold. The plan below is the coordinator's implementation proposal.

### Operator correction after viewing progress captures, 2026-11-04

> The screenshots show a very messy UX. We want to be closer to the information-sparseness of the original concept ASCII illustrations

> The screenshots also show a ton of data errors

The [original concept examples](timeline-guidance.md) remain the presentation
target, not the diagnostic-heavy progress captures. The following correction is
the coordinator's implementation proposal within that existing intent:

- Purpose and compact agenda/subway rows lead. Short human-readable time and
  work labels replace repeated ISO timestamps, event-enum names and source IDs
  in collapsed rows. Exact provenance, clocks, qualifiers and raw fields remain
  available through explicit bounded details; do not infer missing agenda work.
- Normal Timeline chrome uses compact current-input, source-health and unread
  indicators. Move verbose owner diagnostics and the advanced feed selector
  into an explicit evidence disclosure. Retain visible warnings for actual
  unavailable/error/gap states without filling the canvas with their internals.
- Diagnose the displayed errors before presentation changes. Intentional
  fixture faults, unsupported optional providers, deliberately paused
  membership discovery and actual integration defects are different states.
  Normal reader selection must not manufacture a source-error result or make
  retained evidence look current.
- Demonstrate a coherent healthy full-app scenario, plus separate intentional
  degraded/gap cases, at ordinary and narrow sizes. Retain dated progress
  captures and reviewed visual comparisons for both; do not market a negative
  test fixture as the normal experience.

This correction preserves owner facts, literal untrusted text, unknown versus
zero, independent current-input/history qualification, virtual scrolling,
viewport anchors and every original completion/effect gate. It authorizes no
installed rollout, live rebinding, new source inference or hidden error waiver.

## Scope approvals and retained custody

- Source plans, vision amendments, implementation, bounded tests and normal
  reviewed publication are authorized for this Sidecar continuation.
- No installed rollout, live user-Mux rebinding, service restart, process action,
  automatic answer, head repair or handoff is authorized. Isolated source/native
  tests use disposable, explicitly owned fixtures and the bounded test runner.
- All original manual-cutover, independent expected-head, popup/dismissal,
  background-work, relay and native obligations in the parent remain intact.
  A new timeline does not complete or supersede those lanes.
- Worktree Manager owns the presenter and pane lifetime; agent-worktrees owns
  worktree/claim/binding evidence; efforts owns agenda records; Bridge owns
  session/relationship/usage evidence. Existing custodians retain operational
  authority. No Sidecar reader may mutate their ledgers or start an absent host.
- Preserve the current CLI pane, native incarnation and process across view
  switching. The Ctrl+K popup and explicit return-to-CLI remain separate actions.
- Comparison uses unrounded, qualified **pre-compaction** utilization. Missing,
  stale, estimated, wrong-session or uncorrelated evidence is unknown, never zero
  or a normal-looking default. Compaction, explicit context rollover and handoff
  remain different event classes.

## Participants and integration

| Participant | Bounded responsibility | Integration boundary |
| --- | --- | --- |
| Sidecar source coordinator | Canonical plan, observer model/policy, integration, PR stewardship and completion readback | Retained source workspace under the external harness |
| Timeline contributor | Virtual line layout/widget and Textual tests | No provider, ownership or Git authority |
| Worktree observation contributor | Owner observation/history and Manager read adapters | Existing agent-worktrees/efforts boundaries; no UI ledger |
| Bridge evidence contributor | Owner membership/session observations and measurement qualification | Existing Bridge/session/relationship authority |
| Incumbent relay/native custodians | Existing pane delivery, chrome, manual/head and installed qualification | Parent and Mux-bind Relay contracts remain authoritative |

Shared Git mutations belong only to the coordinator. Worker edit scopes are
non-overlapping; parser/wire or model changes are coordinated before integration.
An owned agenda may guide this continuing worktree through the existing source
CLI in an explicit fresh compartment. It is a process aide, not another effort
completion ledger, and does not migrate or overwrite a completed legacy lane.

## Architecture decisions

### Observer state is not canonical history

Keep the existing current-roster and coarse agenda feeds. Add separately
negotiated owner observation/history capabilities where needed. A current-state
feed can coalesce; it cannot reconstruct an event that happened between samples.

Owner history is **gap-aware**, not a promise of lossless cross-store transactions.
Existing claim/disposition/activity journals are incomplete or best-effort.
New owner projections may materialize bounded entries in the existing owner
store; Manager writes none of them. Entries distinguish persisted mutation
evidence, identified retained records, observed changes and reported lifecycle.

A failed ancillary append must not undo a successful canonical mutation or
advertise complete history. Expose a gap/error and retain the last-qualified
facts. Crash-atomic YAML-and-index history is not silently promised. A stronger
guarantee requires its own reviewed owner transaction design.

Agenda state is an advisory navigation aid, not an execution authority. Missing
or rewound planning state must not halt otherwise authorized work: the continuing
agent can recover its root, current action and saved return from context,
session/worktree history and present reality. Declared identity, revision and
last-update information aid judgment; age alone is not a rigid trust gate.
The efforts-owned guidance governs reconciliation without fabricated history,
replayed external actions or cosmetic resets of explicit constraints.

Importing an old journal cannot mint stable IDs from reusable line numbers.
Source incarnation, retained range and completeness remain explicit. State
sampling can yield a labelled observed change, never a fabricated claim addition.

### Minimal source composition

At most three long-lived source workers serve the selected worktree:
worktree observations, agenda observation, and Bridge observations. History and
detail pages are cancellable bounded reads, not more permanent workers.

- Worktree adapters reuse the resident/current-feed process boundary and
  selected-head fences; they do not rescan the fleet in each panel.
- Reported native/ACP subagent and tool observations keep coverage, continuity
  and retained-range qualifiers. Extend an owning producer where live native
  evidence is missing; neither claim counts nor historical start marks establish
  active parallelism. Accumulated statistics come from their owner, not sums of
  the currently loaded viewport.
- Agenda observation remains coarse until its owner supplies rich record/history
  access. A registered or explicitly allocated reader locator is required;
  neither Manager nor another owner guesses a private agenda path. An optional
  owner history reader must match the observed store and use the attributed
  owner command, not publisher-supplied executable arguments.
- Known Bridge sessions reuse their real event/history paths. Owned,
  represented-interactive and remote sessions retain different continuity,
  retention and permission contracts. Add a Bridge-owned membership projection
  to discover new delegates; cached listings are not subscriptions.
- Authentication and passive capability discovery stay with the owning command.
  Manager imports no independently installed sibling package, copies no token,
  starts no service and infers no API support from a package version.

Each source retains its own cursor and generation. Viewing or backward paging
does not consume a controller's delivery cursor or accept work. Heartbeats are
not timeline milestones. Registry disappearance is not claim release or proof
of session retirement.

### Real virtualization

Implement a custom Textual Line-API `ScrollView`, using public APIs supported by
the declared Textual minimum. Do not mount one widget per historical event or
render the entire graph into a `Static`.

Separate the bounded event window, width-sensitive line/height index, widget and
cancellable controller. `render_line` performs no I/O and materializes only
requested viewport lines. Actor lanes and parent/return links need explicit
source relationships; reported parent-tool metadata does not establish custody.

Live-follow mode pins the newest head to the bottom, including short-content
top padding. Browsing disengages follow mode. Incoming events retain the
selected event/logical-line/screen-row anchor and increment an unread indicator.
Prepend, resize and expansion preserve the logical anchor. If a collapse removes
it, use the nearest visible ancestor according to a tested rule.

Keep purpose/current effort outside the scrolling canvas. Creation context is
bounded, expandable and owner-provided, not inferred from a worktree title.
Source uncertainty and history gaps stay visible. Raw evidence is on demand;
arbitrary source prose is not executed or treated as Markdown/Rich markup.

### Truthful attention and compaction

Input/permission occurrence and current answerability are separate. Consume
owner-qualified pending/cleared evidence; a historical request, idle pulse or
open socket alone cannot prove that the session is currently awaiting input.
An attention action navigates to an admitted interaction surface; it never
submits an answer, grants permission or takes session custody.

Bridge already forwards actual usage and compaction fields, but current
translation/status merging does not establish a qualified pre-compaction link.
The current public SDK also defines optional start-event `currentTokens` as
total context tokens, including system/conversation/tool definitions, and
`compactionAttemptId`. Preserve these stronger same-event fields rather than
summing component counts or reconstructing the value after compaction.

The start-event `tokenLimit` is documented as the window being targeted, not an
unconditional guarantee of active pre-compaction capacity. Preserve model,
switch-target and target-budget evidence; optional-field absence does not prove
that no model/window change occurred. A target-only denominator remains
diagnostic until the admitted source/runtime qualifies its pre-window identity.

Retain raw diagnostic observations and add a gated evidence contract. A recent
or adjacent usage event, a model-only update, a rounded display percentage or
post-compaction token count is insufficient.

Definitive classification requires a documented direct or producer-linked
pre-event measurement for the same session/context epoch, with valid counts,
positive active-window capacity and current qualification. Qualify actual optional
field population and denominator identity for the selected SDK/runtime; public
type declarations alone do not establish installed coverage. No SDK getter or
ordering guarantee is invented. Until that relationship is supported, show compaction
observed with pre-usage unknown and keep definitive-warning acceptance open.

For the configured example threshold 65, compare
`used_tokens * 100 < capacity_tokens * 65` without rounding. Equality is
not premature by this rule. Classification is observer-derived and does not
change Bridge, Copilot or context-handoff trigger settings.

### View and lifetime compatibility

Add Timeline as an alternate view in the existing app; preserve Summary as the
initial view and the current popup/pane actions for the first source slice.
Making Timeline the initial Sidecar presentation is an explicit later policy
choice after its consumer/native qualification, not a hidden default change.

Reuse caller-owned process cancellation and data/request-epoch fences. Hide
pauses rendering and releases optional demand under the source's admitted
policy; it cannot retain unbounded queues. Reopen/reconnect obtains an atomic
snapshot and splices pages/updates without duplicate or lost qualified events.
Observer streams can affect Bridge idle-reaping: document that existing effect
and verify cancellation, rather than silently changing the hosting policy.

## Implementation Plan

### S0 - Reviewed admission

- [x] Land this plan, its proposed contract and the vision extension through
  normal review before source execution.
- [x] Retain existing source/native claims and bind the coordinator to the
  parent's declared Step 7. A planning binding is not execution admission.

### S1 - Bounded model, renderer and policy

- [x] Implement typed source positions/events/pages, evidence qualifiers, bounded
  window/height index and deterministic actor lanes.
- [x] Implement virtual line rendering, short-content bottom placement,
  follow/unread behavior, prepend/reflow/expansion anchoring and sticky purpose.
- [x] Implement the configurable pure compaction classifier and explicit
  unknown/error states without changing runtime thresholds.
- [x] Preserve Summary/Claims/Agenda/Graph/Recovery and literal field rendering.

Source receipt: #9979. Its separately qualified packets cover the Manager,
owner-index primitives and Bridge translation/evidence; no source feed is
attached by this foundation. Current-source integration begins at S2.

### S2 - Worktree and agenda owner observations

The [durable worktree-state proposal](worktree-state-lifetime.md) records the
operator's shared-discovery and archival requirement. It is a producer-first
design prerequisite, not authorization to guess a home or move existing data.

The first attachment consumes actual elected-owner roster observations and
exposes qualified read/history/feed transport. Its generic observed-change
history has partial coverage; it does not deliver native mutation capture.
The source implementation is described in the
[owning provider contract](../../../plugins/agent-worktrees/docs/worktree-observations.md);
publication and installed support retain their separate qualification.

- [x] Deliver the first selected worktree receiver's source read/history/feed
  contract with partial observed-change coverage. #10097 merged as
  `d55ee9a7f0a480cbcadd838b0783b75df11372f3`; native mutation receipts,
  rich agenda delivery and Manager adapters are not included in this receipt.
- [ ] Expand providers before callers with passive capabilities, selected-scope
  current facts, gap-aware observation history and bounded backward pages.
  The worktree receiver portion is delivered; remaining owner and consumer
  expansion stays open.
- [ ] Capture real claim/binding/disposition evidence at existing mutation seams;
  reconcile cross-process/legacy sources without fabricated events.
  Isolate optional post-save callback failures so a completed canonical save is
  not reported as failed and remaining notifications still run. Capture under
  record/pair locks is bounded only; publication/RPC happens after release.
  #10199 delivers qualified resident-executed saves and native index intake;
  direct fallback, pre-intake death and unsupported/legacy writers retain
  partial or unknown coverage rather than universal mutation capture.
  The [maintenance-aware resident recovery amendment](resident-recovery.md)
  records the operator's selected next approach: check maintenance, briefly
  retry and use ordinary CLI wake behavior rather than adding offline capture
  storage. Passive Sidecar reads remain no-wake.
  #10744 delivers that normal-write source behavior and actual private
  canonical-transaction/intake proof. Its [source readback](journal/2026/11.05-sidecar-resident-recovery.md)
  distinguishes the injected startup callback from the additional actual
  private normal-ensure process controls, and retains unsupported/direct-fallback
  observation qualification.
  #10758 merges those actual-process controls after independent approval,
  required CI and whole-parent readback. The finite recovery amendment closes;
  broader source coverage is not silently declared complete.
- [x] Deliver bounded immutable process-local committed-save evidence and
  lock-safe optional notification delivery. #10131 supplies this primitive;
  resident/native index intake and cross-process delivery remain in the
  preceding open item.
- [x] Supply live reported subagent/tool activity and owner-qualified accumulated
  statistics with explicit coverage; preserve unknown current concurrency when
  the native/ACP source does not establish it.
  The [bounded tool-statistics amendment](sidecar-tool-statistics.md), reviewed in #10704, admits
  one source-range occurrence counter, not unique calls or lifetime totals.
  #10707 supplies the internal owner projection; #10712 adds negotiated optional
  provider/parser/CLI support and compatibility qualification. #10718 adds the
  actual Manager consumer and large-range, eviction and ordinary/narrow controls.
  #10915 qualifies the actual callback-to-view contribution path and explicit
  local/remote/represented/retained-file coverage. Together these close the
  supported reported-activity and range-occurrence source criterion, not
  universal source coverage, unique-call/lifetime totals or current execution.
- [x] Supply rich agenda-history access through its actual owner and an admitted
  locator for supported sources. Test old/missing sources as explicitly
  unavailable/observed-only, not as delivery of rich history.
  #10131 delivers the passive owner CLI with explicit state/store/worktree
  selection; Manager locator admission and consumption remain below.
- [ ] Review and deliver the owner-managed placement/discovery, retained-data
  lifetime and archival acceptance in the
  [linked worktree-state design](worktree-state-lifetime.md). Existing source
  approvals do not authorize actual data relocation, collection or deletion.
  #10224 delivers D1's allocator/discovery source and #10238 D2's explicit
  initialization/export, and #10251 D3's Manager consumer. #10777 qualifies
  the private native Windows allocation/retry and complete-export filesystem
  controls. Combined native integration, archival acknowledgment and D5 remain
  open rather than completing this combined acceptance.
- [x] Attach the selected worktree provider to the actual Manager Timeline
  through the owning process boundary, with independent page/live cursors and
  request/source/head fences. #10131 includes the resident-to-CLI-to-Textual
  join, explicit integrity reconciliation and typed retirement custody.
- [ ] Integrate Manager adapters with independent cursors, atomic view updates,
  stale/head/source replacement and cancellation.
  The selected worktree and rich Agenda adapters are delivered, including their
  real isolated process/Textual joins in #10131/#10251. #10392 adds passive
  membership and the first three-owner join; #10435 adds supported known-session
  consumption. Complete source coverage and the full joined acceptance remain open.

### S3 - Bridge relationships and problem evidence

- [ ] Receive the direct #8776 LR1-LR4 source/consumer contract in the
  [parent Plan](README.md#direct-represented-session-creator-and-lifecycle-source-8776):
  optional known creator registration/list/detail, caller-asserted rather
  than authoritative relations, normalized subagent lifecycle/configured
  metadata and bounded notifications. Bridge retains producer custody;
  no inferred edge, new poller, permission or accepted ownership transfer.
  Existing reported-event credits below do not complete this source contract.
- [x] Deliver the Bridge-owned known-session observation/history provider and
  passive owning CLI. #10131 preserves existing local, represented and locally
  hosted remote evidence bounds; arbitrary federated handles, canonical head,
  current answerability and active pre-window capacity remain unqualified.
- [x] Integrate known-session observation/history with independent observer
  audiences and the existing local/remote/represented boundaries.
  #10435 delivers the selected owning-CLI consumer for admitted owned/represented
  sources, preserving locally hosted transport metadata and weaker represented
  evidence. Arbitrary federated acquisition, concurrent per-delegate readers,
  installed/native qualification and current answerability are not implied.
- [x] Add owner-qualified membership snapshot/deltas and retirement semantics,
  retaining relationship/custody authority and remote unknowns.
  #10199 delivers the HTTP-52 provider and passive owning CLI. This is source
  delivery, not Manager membership consumption or installed adoption.
- [x] Consume passive owner membership in Manager with selected-head fencing,
  bounded replay and owned-reader retirement. #10392 delivers this source
  consumer; known-session event/history and installed adoption remain separate.
- [x] Expose actual pending/cleared input and permission evidence without answer
  or control authority.
  The [live-request amendment](sidecar-current-requests.md), reviewed in #10545,
  specifies the bounded
  producer snapshot, optional version-skew-safe wire and existing-reader
  consumer. #10551 delivers the bounded local producer and 161 focused source
  controls. #10557 delivers the optional provider/CLI wire, exact independent
  follower retirement, heartbeat lifetime and 59-case source-bound HTTP-52
  capture. #10579 delivers the conditional Manager consumer and independent
  pending/cleared/partial/unknown display, including empty-history and retirement
  controls. This closes supported local source acceptance, not installed/native
  qualification. Retained request history remains separately qualified and
  unsupported sources stay unknown.
- [ ] Deliver qualified pre-compaction evidence from a supported source adapter;
  retain direct start counts/attempt/target metadata but keep unknown until
  actual active-capacity and measurement/correlation qualification exists.
  The [source-only RPC investigation](journal/2026/11.05-sidecar-context-metadata-boundary.md)
  identifies current-context metadata and its actual client dispatch, but does
  not establish that missing pre-event relationship. No getter-based fallback
  or selected-runtime qualification is implied.

### S4 - Joined Sidecar and source delivery

- [x] Demonstrate one worktree: owner snapshot, claim/binding transition,
  committed agenda update and an owner-linked Bridge child on the real widget.
  #10392 qualifies this isolated source join; the Bridge carrier/root binding
  are fixture substitutions, not installed or native-client qualification.
- [x] Demonstrate older-page prepend and live append while browsing, current
  head changes, reconnection/gaps and complete observer cancellation.
  The opt-in `test_companion_joined_lifecycle.py` source packet now exercises
  the private three-owner/selected-session join with a blocked backward page,
  live events, committed purpose/agenda updates and actual waiter clearing.
  It checks retained viewport/selection/focus, hide/head-loss cancellation,
  an expired actual page boundary and explicit Refresh recovery. #10595 merged
  after current-head independent approval and required green CI; this closes
  the supported private-source demonstration, not installed or native acceptance.
- [ ] Drive every source PR through current-head review, required CI and actual
  merge; reread the entire parent gate and journal the exact closed items.

### S5 - Native, performance and remaining parent gates

- [ ] Exercise the real Textual app through actual isolated tmux/PSMux clients,
  not synthetic observer modules or server-side `send-keys` as client proof.
  #10612 delivers the private POSIX tmux/client app portion, not PSMux or the
  original popup/Relay qualification.
  #10670 adds the private POSIX popup/app-input controls; installed/native
  Windows and complementary Relay qualification remain separate.
  #10723 additionally qualifies the private Worktrees/Agenda/Bridge source
  owners through an attached native client, including sparse full/narrow views
  and live history/count separation. Its
  [source readback](journal/2026/11.05-sidecar-native-owner-composition.md)
  retains fixture discovery, carrier and geometry-observation boundaries rather
  than qualifying installed behavior, pixels or frame performance.
  The separate [Windows client packet](journal/2026/11.05-sidecar-windows-client.md)
  uses the actual PSMux/ConPTY client and unmodified app with a synthetic owner.
  #10766's reviewed native packet passes 84 cases with zero skips, including
  actual input/history/resize/return and strict owned cleanup. The initial
  startup-banner failure remains unexplained; this is not startup-stability,
  installed, popup/Relay, headed-pixel or native-performance acceptance.
  The manual Windows packet also selects the private actual-owner composition
  in `test_companion_joined_psmux.py`. #10784's reviewed source passes the
  112-case Windows packet with zero skips, including actual owner history/live
  input and exact reader/observer exit before original CLI input. Its
  [readback](journal/2026/11.05-sidecar-windows-owner-composition.md) closes
  that finite native join, not startup stability, installed behavior, pixels,
  performance or the original popup/Claims/Relay and manual/head gates.
  The separate cold membership-read budget failure remains an open source
  reliability gap under this effort and #8811.
- [ ] Qualify current pane/PID preservation, focus, resize, observer exit,
  mode guides and the separately owned permanent Claims chrome.
- [ ] Record performance/retention/coverage and source versus installed/native
  evidence distinctly. Do not claim rollout or complete the parent from S1-S4.

### Presentation correction - sparse concept fidelity

- [x] Trace the displayed data/error states to their actual producers and
  distinguish a deliberately paused reader from a failed source. Fix confirmed
  source/consumer defects without suppressing genuine uncertainty.
- [x] Deliver compact purpose-first chrome, readable collapsed timeline rows
  and explicit bounded evidence disclosure, matching the original ASCII
  concept's information hierarchy rather than a raw event-log console.
- [x] Qualify the actual full app at ordinary and narrow sizes, healthy and
  degraded, with viewport/input/cancellation controls and reviewed visual
  comparisons. Keep installed/native and whole-parent acceptance separate.

Source receipt: #10642. The actual private three-owner app, deterministic
production-chrome comparisons and separately selected native-client controls
retain their distinct tiers. No installed/native-performance or popup acceptance
is inferred from these presentation checks.

### Output-noise follow-up - actual source workloads

- [x] Review and deliver the [bounded output-presentation amendment](sidecar-output-presentation.md).
  Known message/thought chunks must not overwhelm the agenda overview as
  individual normal work rows. Preserve raw history/identity/proofs, explicit
  Evidence, visible work/diagnostics and bounded history/anchor behavior.
  This adds source qualification, not installed/native or parent completion.
  #10698 closes this bounded source follow-up after actual current-head
  independent approval, green CI, merge and full parent/contract readback.

## Validation Plan

- [ ] LRV1 (#8776). Exercise actual registration/persistence/list/detail and
  launcher construction of the optional caller/creator link, old clients,
  absent/invalid/unknown inputs and caller-asserted semantics. No invented
  source actor or inferred origin, ownership, authority or accepted transfer.
- [ ] LRV2 (#8776). Exercise actual source normalization and consumer mapping
  of supported subagent start/completion/failure and configured metadata,
  cross-turn background completion, notifications and tool-completion
  negatives. Cover allowlisted, unsupported, unknown and permission-sensitive
  payloads without control authority. Keep the issue's administrative LRV3
  readback distinct; the [parent actual global Validation](README.md#validation-plan)
  requires LR1-LR4 AND both technical rows, not a readback receipt alone.

- [x] Use precise monotonic elapsed time for the bounded membership deadline
  without increasing its time/VM limits or excluding scheduling and blocking.
  #10907's current Windows packet verifies actual budget creation and sixteen
  delayed checks with no early refusal, alongside 129 Companion and two lookup
  controls with zero test skips. This closes only the reproduced precision
  defect, not historical cold-read causality or the full native/source gate.

- [x] Bound exact-owner membership work independently of unrelated retained
  registry size using owner-prepared lookups, while retaining passive
  non-preparation, malformed legacy JSON handling and old-store VM cutoffs.
  #10848 qualifies source controls and native Windows SQLite/bootstrap
  behavior; its unchanged actual-client packet passes 120 cases, separately
  from the two new controls. This does not close cold scheduling reliability,
  complete source coverage, native performance or the parent.

- [x] Qualify the isolated Windows PSMux/ConPTY client with the actual private
  Worktrees/managed-Agenda/Bridge source owners, sparse full/narrow views,
  independent history, live purpose/count/unread updates, Follow-live and
  complete birth-qualified reader/observer exit before unchanged CLI input.
  #10784's reviewed source passes run `42140565596`, Windows job
  `127639558279`: 112 passed, zero skips. All ten earlier unsuccessful runs
  remain retained. The separate cold membership-read budget gap, startup
  stability, installed behavior, performance and original parent gates stay open.

- [x] Qualify the actual private Windows PSMux/ConPTY client over the unmodified
  Companion with a synthetic wire-valid owner, independent history/live input,
  resize, Follow-live, Escape and original CLI pane/PID/session preservation.
  #10766's exact reviewed source passes the 84-case Windows packet with zero
  skips and mandatory reader/observer/Job/handle cleanup. Retain the earlier
  failed startup attempt and unknown cause; whole S5, installed, popup/Relay,
  startup stability and native performance remain open.

- [x] Qualify the isolated POSIX attached-client/private-three-owner composition,
  sparse full/narrow views, independently paged history, live purpose/count
  updates while browsing, Follow-live and exact pane/reader retirement.
  #10723's nine-case native packet and explicit geometry/fixture boundaries
  close this finite source/native-client criterion, not the combined S5 or
  original parent/Windows/installed/performance gates.

- [x] Sparse presentation: actual healthy three-owner full/narrow app controls,
  separately degraded and collapsed-error comparisons, literal diagnostics,
  paused-versus-failed state, successful-admission recovery and missing/boundary
  clocks. #10642 preserves source identities, viewport/input/cancellation
  contracts and unknown evidence; this is not whole S2-S5 acceptance.

- [x] Qualify the selected worktree's real private resident, owning CLI and
  Textual join, including live publication, backward history and observer
  cleanup. #10131's final recovery packet also covers explicit fresh baselines,
  retained exhausted-window proof, expected transport failures and terminal
  resource custody independent of replaceable task slots. This is not the
  three-source or native-client acceptance below.
- [x] Qualify the first Worktrees/rich-Agenda/Bridge-membership source join,
  including blocked optional bootstrap, actual Refresh, independent live
  updates and simultaneous evidence reconciliation. #10392's final packet
  passes 296 cases; selected-session evidence follows below, while native and
  full source coverage remain open.
- [x] Qualify the supported selected-session semantic CLI/Manager consumer,
  independent history/live positions, real first-event continuity, exact page
  ends, hidden/visible Refresh and active/pending selected-head retirement.
  #10435's 174 coupled controls and final 41-case affected lifecycle packet are
  source/private-process/Textual evidence, not installed or native acceptance.
- [x] Exact field/enumeration, numeric/boolean, cursor/generation/identity,
  duplicate and malformed-source tests; no render-path I/O or inferred custody.
  #10855 qualifies 242 controls across the supported Worktrees, Agenda,
  membership and selected-session codecs plus the actual membership/source
  join. This is source/private-process/Textual qualification, not universal
  source coverage or native/installed behavior.
- [x] History/live splice and gap tests: coalescing is current state, not lost
  canonical history; retention/replacement/append failure cannot look empty/complete.
  The [supported-source qualification](journal/2026/11.05-sidecar-history-gaps.md)
  passes 274 owner/model/actual-three-source controls. Auxiliary journal
  failure, canonical Agenda rollback, partial Bridge observations and explicit
  consumer reconciliation retain different meanings; universal source coverage
  and independently installed/native behavior remain separate open acceptance.
- [x] Prepend, short-content padding, wheel/PageUp/Home/End/scrollbar,
  follow/unread, expansion and wide-character resize tests on real Textual.
  #10605's 78-case packet includes the short/scrollable wheel regression and
  full-window Unicode expansion/resize. This is actual framework source
  qualification, not native mux-client dispatch.
- [x] Enforce the resource limits in the contract structurally; exercise a long
  logical history without materializing it all. Report repaint/CPU/RSS benchmarks
  separately from deterministic CI correctness.
  The opt-in `test_companion_timeline_scale.py` source packet now selects 5,000
  rows from a synthetic 1,000,000-event logical source, checks bounded real
  Textual paint/update/resize behavior and retains separate raw paint and
  delta-to-Pilot-idle samples. Repeated immutable-reference validation and
  unchanged text wrapping are removed from the hot path. #10605 merged after
  current-head independent approval and required green CI; native-frame,
  installed, source-tail and complete repaint qualification remain separate.
- [x] At configured 65: 28, 35 and 64.99 are premature; 65 and 75 are not by this
  rule. Test rounding traps, invalid/bool/zero capacity, absent/stale/estimated/
  wrong-session/uncorrelated/model-only/post-only samples and context replacement.
  Pure policy coverage landed in #9979; selected-runtime active-capacity and
  actual producer qualification remain open under S3 and the next item.
- [ ] Test actual producer construction of accepted evidence, not a mock beyond
  it. Unsupported measurement sources remain unknown and do not complete S3.
- [x] Actual input/permission clearing and read-only attention: no automatic
  answer, head mutation, cutover, resource release or focus stealing.
  #10579 qualifies the actual callback/route/CLI/Textual source chain and
  supported local waiters, with independent history and late-callback fences.
  Native-client and installed qualification remain separate.
- [x] Claims, reported contributions and known running concurrency stay distinct;
  missing native attribution or incomplete retained tool history cannot become
  zero activity, a fabricated parent relationship or lifetime totals.
  #10915 qualifies actual callback-to-layout, represented and missing/invalid
  attribution controls with explicit current execution/concurrency unknown.
  This closes the finite evidence-distinction criterion, not current runtime
  execution or universal source coverage.
- [ ] Real process/three-source/UI joins, slow reads, late replies, cancellation,
  history restart and independently installed old/unsupported providers.
- [ ] Actual isolated native-client/app tests and observer cleanup; preserve all
  original parent and Relay qualification clauses.
- [ ] Required changefiles, documentation, type/contract gates, real review/CI
  merges and whole-parent readback. Record remaining gates under their true owners.

## Initial limits and measurement

These are proposed engineering limits for review, not operator credit/resource
ceilings or claims about current implementation:

| Resource | Initial bound |
| --- | --- |
| Wire frame / encoded body | 35 KiB / 32 KiB |
| History page | At most 64 entries within the body bound |
| Loaded display window | At most 5,000 events and 8 MiB encoded payload |
| Strip cache | At most 4 MiB |
| Visible actor columns | 32, with explicit bounded overflow grouping |
| Expanded detail groups | 16; detail materialization remains bounded |
| Long-lived source workers | 3; no separate permanent history/detail poller |

Materialize visible lines plus at most eight overscan lines, with at most 64
detail lines for one expanded event and an explicit omission marker. Preserve
the owner payload reference for deeper raw evidence. This is a renderer/window
limit, not deletion or truncation of canonical source history.

Retain high-water/source-fence evidence outside evictable display pages.
Completeness applies to the identified retained range, not lifetime totals.
The renderer should scale with viewport work; a 16 ms p95 repaint at 120x40 is
a benchmark target to report, not a universal wall-clock CI assertion.

No second per-panel fleet poller is introduced. Existing owner/native backstops
remain separately qualified; an elapsed ticker computes from cached source
times without writing status or requesting another snapshot.
Communication silence and process lifetime keep independent budgets. Received
target updates, including unknown event types and owner keepalives, renew the
communication clock; local ticks do not. Lost observation marks freshness/
coverage without killing or replaying the underlying work.

## Completion and release boundary

This slice is accepted only at its actual source/consumer/native evidence tiers.
Unqualified measurement or native coverage remains open or is transferred to a
named tracked objective with its actual owner; never relabel it implemented.
The parent remains Active while any original or added Plan/Validation item is
unresolved. A component PR, completed worker or source merge is not whole-effort
completion, promotion or installed adoption.
