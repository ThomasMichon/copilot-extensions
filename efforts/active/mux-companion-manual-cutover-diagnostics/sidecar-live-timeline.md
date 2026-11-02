# Live Sidecar timeline and push-observation implementation

Parent contract: [README.md](README.md), Step 7 and its Validation Plan.
Coordination: #8811. Standing intent: [Mux Companion](../../../visions/mux-companion/README.md).
Related presentation: [timeline guidance](timeline-guidance.md).
Proposed data boundary: [observation contract](sidecar-observation-contract.md).

**Status: reviewed implementation/validation plan; source execution active.**
Plan #9960 merged after current-head approval and required CI. The new widget,
observation feeds and classifier remain subject to the unchecked gates below.
The foundation source now includes a tested alternate Timeline view and
unattached owner projection/evidence primitives. Its first source review does
not complete feed, qualified-measurement, joined-app or native acceptance.

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

- [ ] Implement typed source positions/events/pages, evidence qualifiers, bounded
  window/height index and deterministic actor lanes.
- [ ] Implement virtual line rendering, short-content bottom placement,
  follow/unread behavior, prepend/reflow/expansion anchoring and sticky purpose.
- [ ] Implement the configurable pure compaction classifier and explicit
  unknown/error states without changing runtime thresholds.
- [ ] Preserve Summary/Claims/Agenda/Graph/Recovery and literal field rendering.

### S2 - Worktree and agenda owner observations

- [ ] Expand providers before callers with passive capabilities, selected-scope
  current facts, gap-aware observation history and bounded backward pages.
- [ ] Capture real claim/binding/disposition evidence at existing mutation seams;
  reconcile cross-process/legacy sources without fabricated events.
- [ ] Supply live reported subagent/tool activity and owner-qualified accumulated
  statistics with explicit coverage; preserve unknown current concurrency when
  the native/ACP source does not establish it.
- [ ] Supply rich agenda-history access through its actual owner and an admitted
  locator for supported sources. Test old/missing sources as explicitly
  unavailable/observed-only, not as delivery of rich history.
- [ ] Integrate Manager adapters with independent cursors, atomic view updates,
  stale/head/source replacement and cancellation.

### S3 - Bridge relationships and problem evidence

- [ ] Integrate known-session observation/history with independent observer
  audiences and the existing local/remote/represented boundaries.
- [ ] Add owner-qualified membership snapshot/deltas and retirement semantics,
  retaining relationship/custody authority and remote unknowns.
- [ ] Expose actual pending/cleared input and permission evidence without answer
  or control authority.
- [ ] Deliver qualified pre-compaction evidence from a supported source adapter;
  retain direct start counts/attempt/target metadata but keep unknown until
  actual active-capacity and measurement/correlation qualification exists.

### S4 - Joined Sidecar and source delivery

- [ ] Demonstrate one worktree: owner snapshot, claim/binding transition,
  committed agenda update and an owner-linked Bridge child on the real widget.
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

- [ ] Exact field/enumeration, numeric/boolean, cursor/generation/identity,
  duplicate and malformed-source tests; no render-path I/O or inferred custody.
- [ ] History/live splice and gap tests: coalescing is current state, not lost
  canonical history; retention/replacement/append failure cannot look empty/complete.
- [ ] Prepend, short-content padding, wheel/PageUp/Home/End/scrollbar,
  follow/unread, expansion and wide-character resize tests on real Textual.
- [ ] Enforce the resource limits in the contract structurally; exercise a long
  logical history without materializing it all. Report repaint/CPU/RSS benchmarks
  separately from deterministic CI correctness.
- [ ] At configured 65: 28, 35 and 64.99 are premature; 65 and 75 are not by this
  rule. Test rounding traps, invalid/bool/zero capacity, absent/stale/estimated/
  wrong-session/uncorrelated/model-only/post-only samples and context replacement.
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
