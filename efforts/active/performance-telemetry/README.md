# Performance Telemetry

- **Slug:** `performance-telemetry`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed slices targeting `dev`
- **Created:** 2026-10-22
- **Status:** Draft
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
| Manager implementation owner | First-frame, current rows, Actions and new/resume call sites | #7938 |
| Static entry implementation owner | Entry and composed-hook call sites | #7943 |

## Coordination

- **Topology:** independent source PRs; one writer per slice.
- **Host (owns PRs):** Telemetry implementation owner.
- **Delegates:** no nested execution delegates.
- **Handoff:** production owners consume the reviewed [interface](interface.md);
  they retain their launch/preparation files.
- Preserve maintenance #7694/#7788, startup #3303, churn #6086, readiness #5215
  and process-registry #5559 ownership. This effort does not close them.

## Context

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

## Plan

### Phase 1 — Reviewed interface
- [ ] Review and merge this plan and interface before implementation.
- [ ] Publish the stable interface to production owners without editing their
  reserved launch/preparation files.

### Phase 2 — Bounded producer and local qualification
- [ ] Evolve the existing seam with compatibility adapters and existing
  lifecycle consumers; retain public functions and content-free reducers.
- [ ] Add bounded opt-in buffering with explicit owner lifecycle, drop counters,
  no caller-side sink work and no waiting on shutdown.
- [ ] Implement typed launch/process measurements and pure offline reports,
  retaining incomplete samples and unknown measurements.
- [ ] Document the performance-impact contract and reproducible native flow
  under #7935, without adding native effects to required unit CI.

### Phase 3 — Optional OpenTelemetry and consumer adoption
- [ ] Add a genuine optional SDK/export adapter with no producer dependency
  on the SDK and no implicit environment-selected off-site destination.
- [ ] Exercise real export to an isolated local receiver, including outage;
  a synthetic JSON mapping alone does not satisfy this item.
- [ ] Integrate Manager and then hooks through their existing owners; use
  complete end-to-end observations, not sums of overlapping spans.
- [ ] Obtain explicit collector host/lifecycle authorization before shared
  installation or startup; prove receive-to-local-persistence and no egress.

### Phase 4 — Qualification and delivery
- [ ] Measure telemetry overhead disabled/enabled/outage under the same workload.
- [ ] Complete review, required checks, merge and release/rollout accounting.
  Source merge, release and installed serving evidence are distinct outcomes.

## Validation Plan

- [ ] Pure tests: disabled zero effects, schema privacy/bounds, context,
  sampling, clock behavior, honest missing metrics and incomplete reports.
- [ ] Bounded integration: throwing/slow/blocked sink, overflow, close,
  cancellation, producer exit and no per-event process creation.
- [ ] Optional SDK absent works; actual local export delivers valid SDK spans;
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

Review this bounded producer/interface plan first. Source implementation can
proceed independently of shared collector deployment. Unavailable deployment
authorization blocks that acceptance item, not pure implementation.

## Journal

### 2026-10-22 — Scope accepted

- Rechecked #7935/#5900 and open implementation PRs; no conflicting claim found.
- Existing seams, frame-health writer and update-outcome ownership inspected.
- Public direction capture checked against the accepted constraints; private
  identifiers are omitted, not substituted into public examples.
- No implementation, collector installation or service startup performed.
