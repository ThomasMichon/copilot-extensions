# Performance Telemetry

- **Slug:** `performance-telemetry`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed slices targeting `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** `visions/process-telemetry/README.md`; Picker responsive-by-budget
- **Umbrella issue:** #7935
- **Sub-issues:** #5900

## Guiding Intent

Measure launch and composed-hook latency without making telemetry another
blocking startup dependency. Extend the existing optional Bridge/Dispatch
callable and JSONL seams, preserving standalone use. Diagnostic events are
lossy observations, never durable recovery facts or ownership authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Telemetry implementation owner | Shared interface, bounded emission, optional adapter and reports | #7935 / #5900 |
| Performance campaign coordinator | Remaining native integration, structured signals and release/rollout acceptance | #7935 / #5900 |
| Manager implementation owner | First-frame, current rows, Actions and new/resume call sites | #7938 |
| Static entry implementation owner | Entry and composed-hook call sites | #7943 |

## Coordination

The directly retained #8036 host/native whole-dispatch lane stays with the existing
performance campaign coordinator under #7935/#5900. The finished import-only claim
remains released; future host work requires its own bounded claim. Parser #3677 and all
existing producer, process, maintenance, collector and rollout owners remain separate.

- **Topology:** independent source PRs; one writer per slice.
- **Host (owns PRs):** Telemetry implementation owner.
- **Delegates:** no nested execution delegates.
- **Handoff:** production owners consume the reviewed [interface](interface.md);
  they retain their launch/preparation files.
- Preserve maintenance #7694/#7788, startup #3303, churn #6086, readiness #5215
  and process-registry #5559 ownership. This effort does not close them.
- Execution uses only this effort's native workers or newly created owned
  local workers. Do not contact pre-existing agent sessions, use agent-chat or
  dispatch remote workers. Detect overlap through read-only forge/task state
  and escalate conflicts to the coordinator; retain existing claims and PRs.
- The reviewed producer/export source slice is complete in #8002 and #8035.
  Remaining checklist items stay with the performance campaign coordinator under
  #7935/#5900; neither the effort nor its umbrella issues are closed.
- The next explicitly assigned source episode is the bounded
  [Manager observation slice](manager-observation.md), owned by the Telemetry
  implementation owner. Reserved Manager/entry files and the retained #8036
  qualification remain independently owned and unchanged.

## Context

The completed [Telemetry Measurement Contract](../../2026/10/22%20telemetry-measurement-contract/README.md)
sub-effort (#8102, delivered in #8117) defines proposed QoS/Engagement semantics,
a measurement catalog and future instrumentation sequencing only. It does not take reserved production
files or close this campaign's native, structured-signal or rollout acceptance.

Bridge and Dispatch already have optional dict-shaped telemetry and local
spooling, but call sinks synchronously. Exception handling does not isolate
a slow sink. Manager already has a bounded frame-health writer; normalize its
events through that owner rather than adding another heartbeat or reporter.
Update telemetry owns durable update outcomes and stays separate.

The process-telemetry vision permits a shared extraction of the existing seam,
not a second bus. A shared core must ship with compatibility adapters and real
existing producers. Local measurement/report tooling serves the existing
Picker responsiveness intent; it does not introduce a general APM platform.
Collection, retention and deployment remain consumer-owned.

## Request

Public-safe accepted direction:

> evolve existingseam, extractsharedcore ONLY with concrete compatibility adapters/realfirstconsumer, no orphan framework/secondbus; no strict new persistenceprovenance layer.
>
> Need genuine optional OpenTelemetry adapter + real localreceiver/export validation eventually, not call hand-shaped JSON 'OTel complete'.
>
> Disabled hotpathzeroeffects, boundedqueue/lifecycle, no rawpayload content, standalonewithoutSDK/exporter, local-only; keepdiagnosticvsdurablefacts distinct.
>
> Initial JSONL+offlinequalification is an admitted intermediate, notwholetelemetrydone.

Deployment requires an explicitly selected collector host and authorization;
none is selected by this plan. Pure code and isolated tests may proceed after
plan review. Proposed overhead thresholds are recommendations to baseline,
not pre-existing promises. Publish the interface early; prioritize Manager
startup/new-resume, then hooks.

### Next source-consumer direction

> concrete nativeconsumer adoption/reporting, not more syntheticcanaries.
>
> Reuse existingreporter/queue/lifecycle, no secondbus/writer, no hotpathnetwork/fileflush/spawn/import/provisioning.
>
> Realexecutableentry/firstrefresh/input/authoritativerows evidence distinct; don'tclaimheadedpaint fromheadlessfixture.

The assigned slice is existing Manager frame-health reporting and explicit
source-native observation/report adapters only. Source-owned validation may
export to the separately authorized local endpoint; no installed enrollment or
service changes are authorized. If a reserved constructor/call-site signature
is necessary, propose it to the coordinator first. The
[slice plan](manager-observation.md) preserves these constraints, explicit
sample/drop/missing evidence and the later structured-signal/hook work.

## Plan

### Phase 1 — Reviewed interface
- [x] Review and merge this plan and interface before implementation.
- [x] Publish the stable interface to production owners without editing their
  reserved launch/preparation files.

### Phase 2 — Bounded producer and local qualification
- [x] Evolve the existing seam with compatibility adapters and existing
  lifecycle consumers; retain public functions and content-free reducers.
- [x] Add bounded opt-in buffering with explicit owner lifecycle, drop counters,
  no caller-side sink work and no waiting on shutdown.
- [x] Implement typed operation spans and pure per-stage summaries, retaining
  incomplete samples and unknown measurements.
- [ ] Complete native run envelopes and process measurements through the owning
  producers, including roster and source/installed/serving identity (#7935/#5900).
- [x] Document the performance-impact contract and reproducible native flow
  under #7935, without adding native effects to required unit CI.

### Phase 3 — Optional OpenTelemetry and consumer adoption
- [x] Add a genuine optional SDK/export adapter with no producer dependency
  on the SDK and no implicit environment-selected off-site destination.
- [x] Exercise real export to an isolated local receiver, including outage;
  a synthetic JSON mapping alone does not satisfy this item.
- [ ] Retain structured logging and metric mappings as separate acceptance:
  initial traces-only interoperability does not complete all telemetry.
  Typed span events/counters or an explicit structured adapter must preserve
  the default exclusion of free-form logs and content.
- [ ] Integrate Manager and then hooks through their existing owners; use
  complete end-to-end observations, not sums of overlapping spans.
- [ ] Obtain explicit collector host/lifecycle authorization before shared
  installation or startup; prove receive-to-local-persistence and no egress.

### Phase 4 — Qualification and delivery
- [x] Measure synthetic producer overhead disabled/enabled/blocked under the
  same 5,000-call workload, including drop counts and scheduler outliers.
- [ ] Qualify end-to-end native overhead on the owner-integrated workload;
  synthetic call timings are not a UI/launch overhead percentage (#7935).
- [x] Complete producer/export source review, required checks and merge.
- [ ] Verify release and authorized installed-serving rollout separately (#7935).

### Phase 5 — Manager source observation
- [x] Review and merge the [bounded consumer plan](manager-observation.md).
- [x] Admit source observations once and reuse the existing reporter queue,
  writer and lifecycle without editing reserved Manager/entry files.
- [x] Exercise actual source Picker refresh/input observations in owned native
  processes; distinguish fixtures and unavailable headed/authority evidence.
- [x] Produce complete expected/completed/failure/drop/identity/overhead reports
  and verify actual local receiver readback for the owned observations.
- [x] Pass source gates and merge the bounded slice, retaining broader
  installed-native, structured-log/metric and hook acceptance.

### Plan addendum: retained Windows mention-guard whole-dispatch qualification (#8036)

<!-- backlog-reconciliation:7556:transfer:8036:to:efforts/active/performance-telemetry/README.md -->

Accept this exact remaining native/host-boundary slice under the existing performance
campaign coordinator, leaving all current Plan/Validation/Coordination and independent
producer owners unchanged. #8049's bounded import lane is actually merged and its source
owner explicitly released that lane; strict whole-dispatch acceptance is not complete.

- [ ] Credit #8006's relevance/shared-decision-budget/cleanup controls and #8049's
  deferred subprocess import. Preserve repository/plugin provenance, publication/body
  validation, original decision semantics and normal standalone behavior; a narrower
  scope probe, fail-open body or increased timeout is not an optimization.
- [ ] Measure the ACTUAL Windows preToolUse hooks.json wrapper from host invocation
  through process return and descendant cleanup. Attribute shell startup, interpreter
  selection/import, Git, decision, body I/O and cleanup without summing overlapping
  spans. Source inspection, Python helper duration, unit count and a configured timeout
  are not whole-dispatch observations.
- [ ] Preserve normally-instant1-2second intent and the original honest host/shell-floor
  condition. If an unavoidable floor prevents the target within this plugin, report it
  with the unmet cases and responsible host boundary; a floor report is not a passing
  two-second result or an invented waiver of the retained qualification.
- [ ] Obtain the actual hook host's pre-spawn elapsed/remaining-budget and
  descendant-cleanup accounting contract plus an explicitly owned controlled
  representative native lane. Name unavailable capabilities and owner dependencies; do
  not invent an API or infer permission from this intake.
- [ ] Consider shell-startup avoidance only through an actually supported host-owned
  direct-argv/interpreter-resolution contract that preserves plugin/source provenance,
  stdin, cwd, environment, exact argv and decision parsing. No plugin-local resident
  daemon, unreviewed shared bootstrap/launcher change or second reporter is authorized.
- [ ] Preserve #3677 parser/GraphQL independence, other-hook exclusions,
  source/installed distinction and existing rollout/host/process owners. Any future host
  implementation needs its own bounded eligible claim; the released source lane is not a
  new broad claim.

### Validation addendum for #8036

- [ ] Keep matched baseline/candidate shell/interpreter/source/workload identities,
  cold/warm/first-touch controls and complete original samples/errors. The existing n=3
  observations and shared-host noise are insufficient tail qualification; unavailable
  measurements remain unavailable.
- [ ] Qualify irrelevant, relevant-safe and deny outcomes plus actual blocked-Git and
  slow-body wrapper cases, including descendant cleanup and unchanged scope/body
  decisions. Retain the reported3.312s/2.789s over-budget observations as unresolved,
  not replaced by candidate fast-case maxima or72 unit passes.
- [ ] Report actual end-to-end p50/p95/p99/max with sample/quantile sufficiency,
  expected/completed counts, roster/cardinality, source/installed identities and
  attributable descendant CPU/spawns. No double-counted spans, missing-child-as-zero
  result or unmeasured host-floor claim.
- [ ] Prove normally-instant/ceiling behavior at the actual supported host boundary OR
  explicitly preserve the measured unavoidable floor, unresolved strict cases and
  missing host dependency without claiming qualification. Any budget/contract revision
  requires its existing reviewed owner process, not a silent local weakening.

### Continuing coordination for #8036

The current performance campaign coordinator retains this planned native/host
qualification. Existing collector, process, Manager, entry, maintenance and parser
owners remain unchanged. No live hook, benchmark, process, service or deployment is
authorized by this text. Close #8036 only after this COMPLETE unchecked additive
Plan/Validation/coordination intake is normally reviewed, actually merged and fully read
back with all existing root scope preserved. Until then the source remains OPEN;
afterward it is provenance, not a closed live implementation claim.

## Validation Plan

- [x] Complete the [Manager source-observation validation](manager-observation.md#validation)
  at its stated evidence boundaries; do not turn headless callbacks into headed
  paint or fixture rows into production authority.
- [ ] Complete every directly retained #8036 Plan/Validation clause above at its original scope and evidence
  tier, preserving original alternatives, same owners, holds and current
  portfolio bounds. Tracker intake is not implementation or native acceptance.

- [x] Pure tests: disabled zero effects, schema privacy/bounds, context,
  sampling, clock behavior, honest missing metrics and incomplete reports.
- [x] Bounded integration: throwing/slow/blocked sink, overflow, no-wait close,
  registration/counter races and actual buffered JSONL/OTLP delivery.
- [ ] Complete real spawn/cancellation/producer-exit adoption through the
  independently owned process-provenance slice (#5900).
- [x] Optional SDK absent works; actual local export delivers valid SDK spans;
  unavailable receiver cannot block producers or cause unbounded retries.
- [ ] Windows/Linux process lifecycle measurements include short-lived children
  and creation failure, with unavailable CPU/memory explicitly represented.
- [ ] Native Manager first interactive frame <2s, Actions <1s and key
  acknowledgement <100ms; baseline new/resume before approving a new budget.
- [ ] Composed hooks share one <=2s ceiling including startup, IPC, fallback
  and cleanup. Missing native observations cannot qualify a run.
- [ ] Reports retain roster/cardinality, source/installed/serving identities,
  expected/completed samples, p50/p95/p99/max, descendant CPU/spawns and drops.
- [ ] Overhead A/B reports absolute and relative cost; 1ms/event and 1% total
  are **agent-recommended candidate thresholds**, not established gates.
- [ ] Fresh optional packaging introduces no automatic install/start behavior;
  native/shared deployment is separately authorized and qualified.

## Proposal

Plan/interface approved and merged in #7991. Source implementation proceeds
independently of shared collector deployment. Missing deployment authorization
blocks that acceptance item, not pure implementation.

## Journal

### 2026-10-23 — Next bounded consumer episode admitted

- Resumed the retained source workspace with a fresh scoped task; the completed
  foundational producer/export episode is not reopened.
- Read the existing reporter/callback and real Picker test seams. The source
  observation proposal reuses that writer rather than introducing another bus.
- Preserved the separately retained #8036 whole-dispatch qualification and
  existing source claims. No reserved call-site edit or installed/service change
  has been made. Plan review precedes implementation.

### 2026-10-23 — Manager observation source under validation

- #8096 merged after approving review and successful required checks.
- Implemented once-admitted source observations through the existing reporter,
  source-native process driver and honest report/readback helpers. No reserved
  Manager/entry file or collector/service configuration was changed.
- An initial actual source Picker disabled/enabled pair completed and all five
  enabled source observations reached the authorized local receiver. This is
  headless fixture evidence, not headed/installed budget qualification.
- Preserve the complete #8036 intake from #8086, including every native hook
  host-contract gate. It is not absorbed or qualified by this Manager slice.

### 2026-10-23 — Bounded Manager source adoption merged

- #8147 merged after approval at its exact head and successful required CI.
  The source consumer reuses the existing reporter; reserved Manager/entry
  files and installed/service state remain unchanged.
- Final scoped validation passed 43 tests, including real source processes,
  standalone dependency materialization, Windows windowless-parent cycles,
  descendant cleanup, actual unmount nonblocking behavior and report failures.
- A clean merged-source run completed three disabled and three enabled source
  process samples, with zero failures, drops or incomplete senders. All 15
  generated observation IDs and typed labels were read from the authorized
  local receiver, without duplicates or mismatches.
- Exact boundaries: script entry, real source refresh callback, matching Key
  receipt, headless fixture viewport and UI-stop point. Headed paint,
  installed launcher, production authority and descendant resource measures
  remain unavailable. Payload identity is unknown; selected source and isolated
  serving distribution identities are retained.
- This small shared-host run is not a budget pass or causal overhead result.
  Refresh medians were about 4.03s disabled / 3.34s enabled, with enabled maximum
  6.16s; Key-receipt medians were about 7.24ms / 0.875ms, with enabled maximum
  9.73ms. Paired differences have mixed signs; p99 is unavailable at three samples.
- Earlier input timing included Pilot/handler settling and is superseded by
  receipt timestamps. Observation versus enqueue and off-UI drain timings are
  now separate; malformed samples/readback cannot discard run accounting.
- The performance campaign coordinator retains full native/installed and hook
  qualification, structured log/metric mapping, rollout and every #8036 gate.
  Neither the broader effort nor #7935/#5900 is complete.

### 2026-10-22 - Complete retained acceptance from #8036

- Materialized the complete #8036 scope directly in Plan and Validation,
  retaining incumbent coordination, source/resource custody and all old rows.
  Normal reviewed merge and full current readback precede administrative
  source supersession. No behavior, test, native result or rollout is claimed.

### 2026-10-22 — Scope accepted

- Rechecked #7935/#5900 and open implementation PRs; no conflicting claim found.
- Existing seams, frame-health writer and update-outcome ownership inspected.
- Public direction capture checked against the accepted constraints; private
  identifiers are omitted, not substituted into public examples.
- No implementation, collector installation or service startup performed.

### 2026-10-22 — Reviewed interface and first source slice

- #7991 merged after an approving review and successful required checks.
- Production owners received the interface; their launch/preparation files
  remain untouched by this slice.
- Extracted the existing registration/emission seam with both real compatibility
  consumers, plus opt-in bounded buffering and content-free measurements.
- The pure offline summary is deliberately not a full native qualification
  report. Optional SDK/export, native adoption, overhead baselines and
  collector receive-to-persist proof remain open; no shared deployment occurred.

### 2026-10-23 — Bounded source merged

- #8002 merged after approving review and successful required CI. Native
  standalone snapshot imports for both consumers, registration/counter races
  and integer bounds are covered; no deployed consumer was changed.
- The next source slice adds real optional SDK traces and an isolated receiver.
  A consumer-owned local receiver remains separately authorized and operated;
  no producer install makes it mandatory.
- Isolated Windows SDK 1.45.1 tests decoded actual OTLP protobuf from an owned
  loopback receiver's file, including canary identity, environment exclusion,
  redirect refusal and 503 outage. This is not shared collector persistence.
- A 5,000-call synthetic microbenchmark measured disabled p50/p95 0.5/0.6us;
  enabled no-op consumer 43.5/77.8us; blocked consumer 43.0/82.2us.
  Saturating bursts dropped records as designed, and scheduling outliers reached
  about 19.5ms. These are call-level observations, not a native UI overhead
  percentage or a new enforced threshold.

### 2026-10-23 — Optional source export merged; remaining acceptance retained

- #8035 merged after approving review and successful required CI. The real
  SDK `SpanExporter` extension point and official protobuf encoder avoid
  ambient-header parsing without mutating environment or SDK globals.
- Final targeted core/wire validation passed 61 tests; earlier consumer
  compatibility and native standalone snapshot controls remain intact.
  The declaration audit covered 335 Python declarations with zero age violations.
- Real wire controls cover malformed ambient secret logs, redirects/non-2xx,
  truncated/malformed/oversize/partially rejected acknowledgements and outage.
- The authorized consumer-owned local receiver independently persisted a source
  canary with only the approved attributes. This is not production enrollment
  or complete receiver-retention/native-UI/structured-log/metric qualification.
- Remaining acceptance is explicitly retained by the performance campaign
  coordinator under #7935/#5900. No release or installed-runtime rollout is
  inferred from these source merges.
