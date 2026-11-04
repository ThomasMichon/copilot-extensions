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
- [x] Deliver bounded immutable process-local committed-save evidence and
  lock-safe optional notification delivery. #10131 supplies this primitive;
  resident/native index intake and cross-process delivery remain in the
  preceding open item.
- [ ] Supply live reported subagent/tool activity and owner-qualified accumulated
  statistics with explicit coverage; preserve unknown current concurrency when
  the native/ACP source does not establish it.
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
  initialization/export, and #10251 D3's Manager consumer. D4-D5 and native durability
  remain open rather than completing this combined acceptance.
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
- [ ] Expose actual pending/cleared input and permission evidence without answer
  or control authority.
- [ ] Deliver qualified pre-compaction evidence from a supported source adapter;
  retain direct start counts/attempt/target metadata but keep unknown until
  actual active-capacity and measurement/correlation qualification exists.

### S4 - Joined Sidecar and source delivery

- [x] Demonstrate one worktree: owner snapshot, claim/binding transition,
  committed agenda update and an owner-linked Bridge child on the real widget.
  #10392 qualifies this isolated source join; the Bridge carrier/root binding
  are fixture substitutions, not installed or native-client qualification.
- [ ] Demonstrate older-page prepend and live append while browsing, current
  head changes, reconnection/gaps and complete observer cancellation.
- [ ] Drive every source PR through current-head review, required CI and actual
  merge; reread the entire parent gate and journal the exact closed items.

### S5 - Native, performance and remaining parent gates

- [ ] Exercise the real Textual app through actual isolated tmux/PSMux clients,
  not synthetic observer modules or server-side `send-keys` as client proof.
- [ ] Qualify current pane/PID preservation, focus, resize, observer exit,
  mode guides and the separately owned permanent Claims chrome.
- [ ] Record performance/retention/coverage and source versus installed/native
  evidence distinctly. Do not claim rollout or complete the parent from S1-S4.

## Validation Plan

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
- [ ] Exact field/enumeration, numeric/boolean, cursor/generation/identity,
  duplicate and malformed-source tests; no render-path I/O or inferred custody.
- [ ] History/live splice and gap tests: coalescing is current state, not lost
  canonical history; retention/replacement/append failure cannot look empty/complete.
- [ ] Prepend, short-content padding, wheel/PageUp/Home/End/scrollbar,
  follow/unread, expansion and wide-character resize tests on real Textual.
- [ ] Enforce the resource limits in the contract structurally; exercise a long
  logical history without materializing it all. Report repaint/CPU/RSS benchmarks
  separately from deterministic CI correctness.
- [x] At configured 65: 28, 35 and 64.99 are premature; 65 and 75 are not by this
  rule. Test rounding traps, invalid/bool/zero capacity, absent/stale/estimated/
  wrong-session/uncorrelated/model-only/post-only samples and context replacement.
  Pure policy coverage landed in #9979; selected-runtime active-capacity and
  actual producer qualification remain open under S3 and the next item.
- [ ] Test actual producer construction of accepted evidence, not a mock beyond
  it. Unsupported measurement sources remain unknown and do not complete S3.
- [ ] Actual input/permission clearing and read-only attention: no automatic
  answer, head mutation, cutover, resource release or focus stealing.
- [ ] Claims, reported contributions and known running concurrency stay distinct;
  missing native attribution or incomplete retained tool history cannot become
  zero activity, a fabricated parent relationship or lifetime totals.
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
