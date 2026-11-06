# Machine Health Heartbeats

- **Slug:** `machine-health-heartbeats`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs targeting `dev`
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [machine heartbeats](../../../visions/telemetry/machine-heartbeats/README.md),
  [user-state carriers](../../../visions/user-state-carriers/README.md)
- **Umbrella issue:** #10873
- **Sub-issues:** #10881 (local report/chart consumer), #10928 (preference preview)

## Guiding Intent

Make machine outages diagnosable without making monitoring another essential
service. Agent Machines propagates supported core machine preferences through
an explicitly selected OneDrive carrier and aggregates telemetry/health back.
Reuse existing retained local telemetry, add bounded hourly reports, and
eventually publish seven days of health through that out-of-band carrier.
Observation remains useful without Manager, an interactive
agent, SSH or Gateway availability.

The immediate delivery is a reviewed architecture and first complete local
capture-to-report implementation, followed by adopter-owned configuration-only
rollout. Further outage rounds refine the probe/carrier design before their
activation. A completed source slice is not the whole effort's completion.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Health implementation owner | Plan, bounded reports, health engine and reviewed source PRs | #10873 and this effort |
| Existing telemetry owners | Producer schemas, retained sinks, optional SDK/OTLP boundaries | #7935 / #5900 |
| Adopter configuration owner | Private defaults, selected-machine configuration and activation decisions | Existing adopter configuration and machine owners |

## Coordination

- **Topology:** independent per-slice PRs; merge reviewed design before code.
- **Host (owns PRs):** Health implementation owner.
- **Delegates:** none; existing producer and maintenance work keeps its owners.
- **Handoff:** update this canonical effort, source issues and validation before
  transferring a slice. No public links to private deployment records.
- Preserve #7935/#5900 producer/native/OTLP acceptance, #5215 readiness and
  #5559 process-registry ownership. Do not pre-patch or depend on open #8932.

## Context

#10860 established the standing intent, not an implementation. The source
inventory and owner decision are in [architecture.md](architecture.md); concrete
interfaces and outage controls are in [design.md](design.md).

Bridge and Dispatch already support explicit retained local JSONL sinks using
`process_telemetry.configured_spool`, one existing bounded writer, and 30-day
local diagnostic retention. The library already supplies measurements,
percentile summaries and optional genuine SDK/OTLP adapters. JSONL capture is
not OTLP reception, and existing application logs are not optional event capture.

Agent Machines already owns machine configuration and native scheduled jobs.
Its old hourly self-update watchdog is transport-specific and must not be
revived for this feature. Manager and Worktrees are optional observation or
presentation consumers, not prerequisites for machine-health publication.

## Request

The accepted request is to plan the architecture/design, obtain review, start
implementation, incorporate outage lessons, enable local telemetry through
adopter defaults, and derive useful performance/reliability charts while avoiding
an unnecessary service. The original, public-safe wording and later narrowing
are preserved in [inception-transcript.md](inception-transcript.md).

The operator subsequently endorsed Agent Machines ownership. The detailed
division and first-slice sequencing below remain **agent recommendations**
submitted for review, not additional operational authority.

## Scope Approvals

The operator authorizes source planning, review and implementation. The adopter
has separately selected default configuration and an initial visible Windows
cohort. Its explicit latest boundary is **configuration-only: ask before
activating any running service**.

No live service reload/restart, scheduled-job enrollment, Linux rollout,
credential acquisition, new collector, transport setup, cloud-storage selection
or remote recovery follows from plan approval. Existing custom sinks and active
sessions must survive. Exact deployment selectors and original private approval
evidence remain with the adopter; this public plan does not manufacture a grant.

## Plan

### Phase 1 - Reviewed architecture and design
- [x] Inventory existing sinks, local reports, machine schedulers and overlapping work.
- [x] Capture the request, separate recommendations and record activation limits.
- [x] Review and merge this effort, architecture, design and outage-control matrix.

### Phase 2 - First complete local capture-to-report flow
- [x] Claim the bounded source slice and implement an Agent Machines report
  command over existing typed local telemetry, without a new daemon or SDK
  requirement.
- [x] Produce 168 hourly, coverage-qualified summaries and a self-contained
  chart from real existing sink output; preserve loss, invalid and missing data.
- [x] Prove the existing producer/configured sink -> file -> report/chart
  consumer flow, not only pure aggregation functions.
- [x] Pass targeted tests, required review/CI and merge; update reality docs.

### Phase 3 - Adopter default configuration, no activation
- [x] Provide the supported configuration-only setup contract, preserving
  pre-existing custom sinks and selecting owner-resolved local namespaces.
- [x] Have the adopter land its versioned defaults and configure the currently
  visible selected Windows machines through existing owners.
- [x] Record configured, already-capturing, unavailable and pending-activation
  outcomes separately. Do not reload or restart a running service.

### Phase 4 - Core preference propagation
- [x] Review the [four-domain preference amendment](preferences.md), including
  first-class harness-to-knowledge defaults and machine-local override/adoption.
- [x] Prove strict explicit-snapshot preview and machine-local mapping selection
  through the normal CLI, without native adoption or carrier effects.
- [x] Prove policy-governed explicit local acceptance/publication, offline
  watermarks, withdrawal and changed-preview refusal without carrier enrollment.
- [x] Prove native canonical mapping resolution and explicitly confirmed
  existing-repository binding through the selected owner, without automatic adoption.
- [ ] Extend the existing Agent Machines configuration owner to publish and
  receive explicitly supported nonsecret core preferences through the selected
  carrier, not through the health-record parser.
- [ ] Reuse normal configuration validation, scope, precedence, preview/apply
  and local authority; preserve existing bindings and machine overrides.
- [ ] Qualify delayed/conflicting preferences, unsupported versions, offline
  operation and safe withdrawal before any adopted application.

### Phase 5 - Hourly health engine and carrier qualification
- [ ] Refine diagnosed-outage controls with operator input; retain symptoms,
  hypotheses and confirmed causes separately.
- [ ] Implement bounded domain probes and the 168-slot store, using exact
  existing owners and no remediation or new resident process.
- [ ] Add independent native scheduled execution through Agent Machines after
  source review, retaining a separate explicit enrollment/activation gate.
- [ ] Qualify the selected carrier's delayed delivery, weekly slot reuse,
  conflicts, offline catch-up and reader-side outage reporting.
- [ ] Obtain activation authority for the selected cohort, prove actual hourly
  publication and peer receipt, and preserve local-only operation.

## Validation Plan

- [ ] Source: strict bounded input, mixed schema/timestamp units, malformed and
  partial records, tail rotation, loss/unknown accounting and output privacy.
- [x] Consumer: real retained sink output reaches the normal report command;
  charts reflect observations without inventing uptime or unobserved zeroes.
- [x] Statistics: group homogeneous stages, show population/coverage, preserve
  absent durations, and do not qualify unsupported p99 or causal claims.
- [ ] Operations: no producer-side extra I/O, network, process or writer; measure
  report cost and bound storage, scan work, runtime and scheduled overlap.
- [ ] Outages: exercise the [design controls](design.md#outage-derived-controls),
  including a live process with an unresponsive SSH path and an impaired carrier.
- [ ] Platforms: portable unit contracts plus isolated Windows/Linux native
  one-shot, scheduling, headlessness and cleanup qualification before enrollment.
- [x] Deployment: default configuration round-trip, custom-sink preservation,
  real readback, no running-service activation and explicit unavailable hosts.
- [ ] Preferences: separate typed input, permitted settings only, existing
  validation/precedence and local authority, with visible conflicts rather than
  last-write-wins or command execution from synchronized data.
- [ ] Acceptance: required review/CI, source merge, release availability,
  configured state, active capture and carrier receipt remain separate facts.

## Proposal

**Agent-recommended:** Agent Machines owns the bounded machine-health job and
its native periodic registration, not a resident service. Existing producers
retain their local sinks; the shared telemetry library retains event/statistic
semantics; Manager renders cached reports; Worktrees contributes observations.
The [owner comparison](architecture.md#owner-decision) explains this choice.

## Journal

### 2026-11-05 - Inception and source inventory
- Claimed #10873 after deduplicating heartbeat/capture work.
- Confirmed existing retained sinks and optional SDK/OTLP support; no second
  telemetry bus, collector or runtime activation is required for the first flow.
- Recorded the operator's configuration-only narrowing. Plan review is pending;
  no runtime implementation or adopter configuration has changed.
- The operator endorsed Agent Machines propagating core preferences through
  OneDrive and aggregating health/telemetry back. Added the separate preference
  phase without widening activation authority.

### 2026-11-05 - Plan reviewed; first source slice admitted
- #10877 merged after Copilot approval with no findings and required green CI.
  Pulled forward onto the reviewed plan before runtime edits.
- Claimed #10881 for the bounded Agent Machines local report/chart consumer.
  Existing producer and sink code remains unchanged; no service activation,
  scheduler enrollment or carrier publication is part of this source slice.

### 2026-11-05 - Local report consumer implemented
- Implemented the normal Agent Machines report command, bounded typed-event
  scanner, 168 UTC-hour projection and separate static timing/outcome charts.
- The managed Windows packet passed 37 focused report/CLI/restore cases after
  correcting obsolete plain-object package fixtures to real package dataclasses.
  Final report/native controls passed 15 cases, including added truncated and
  replaced-input controls. The real retained sink and normal Python module CLI
  executed in isolated state; native service/carrier activation was not exercised.
- Ruff F/E9, install contracts, documentation consistency and whitespace pass.
  Code review, required CI, source merge and adopter configuration remain open.

### 2026-11-05 - First implementation merged; configuration-only adoption
- #10888 merged as `289cb49960c174e7037ea6993c5f8491f562d9b7` after current-head
  Copilot approval, resolved feedback and green required CI. Review corrections
  preserve standalone malformed/partial coverage, absolute UTC context and all
  actual task state transitions without admitting metadata-only events.
- The final focused report/native packet passed 21 cases. The earlier combined
  CLI/restore packet passed 37; shared-contract preparation passed 58 tests.
  #10894 fixed the independently exposed missing HTTP-52 preparation through
  the existing exact-checkpoint owner, without weakening contract verification.
- The adopter landed its default declarations and completed the selected
  Windows configuration-only cohort through the existing setup owner. Independent
  readback distinguished retained configuration, pre-existing state, unavailable
  targets and unknown running capture. No service activation, schedule or
  carrier publication was performed. Exact private deployment details are not
  part of this public artifact.
- Phase 4 is next: refine the supported core-preference exchange through the
  existing configuration engine. Phase 5 retains probe/carrier/native
  qualification and its explicit activation gate. The effort remains Active;
  source release availability and installed report adoption are not inferred.

### 2026-11-06 - Portable preference scope refined
- The operator selected a carrier home and all four preference categories:
  telemetry/reporting, model/reasoning, knowledge/venue and maintenance/update.
  Selection creates no carrier files and does not authorize service activation.
- The operator explicitly requires stored harness-to-knowledge default mappings
  on the carrier, with per-machine overrides. The amendment distinguishes those
  defaults from effective local bindings and reuses native identity, registration
  and binding transactions. Existing ordinary-default eligibility remains narrow.
- The linked proposal and its conflict/withdrawal/native-consumer controls await
  review before implementation. Exact adopter folder/account choices remain
  private; the parent effort stays Active.

### 2026-11-06 - Preference amendment reviewed; bounded consumer first
- #10924 merged after current-head Copilot approval with no findings and green
  required CI. Pulled forward before code; the parent effort remains Active.
- Claimed #10928 for strict explicit local snapshots and a read-only normal CLI
  preview. Machine-local mapping overrides and suppression are projected as
  preferences, not treated as effective bindings or native identity validation.
- This first consumer does not persist acceptance, publish carrier files or
  apply any domain. Native adoption, first-use/platform/carrier qualification
  and explicit activation remain open, not satisfied by a preview.
- The contained Windows packet passed 40 focused snapshot, CLI-scope and fresh
  module/file consumer tests. Ruff F/E9, install contracts, documentation
  consistency, effort structure and whitespace passed. The subprocess used
  fresh isolated state, but no installed runtime, Linux native lane, binding
  transaction or live carrier was exercised.

### 2026-11-06 - Read-only preference consumer merged
- #10933 merged as `4b5efce814772df02d88de580d8075155c76c15a` after the reviewed
  decoder correction, a current-head clean Comment with no open code findings,
  the required CLI contract assessment and green required CI.
- The added overlong-integer normal-CLI regression passed with all 28 affected
  tests; the earlier combined packet passed 40. Native identity/adoption and
  carrier effects were not added to this read-only source slice.
- Reconciled the confirmed squash merge without dropping any source content.
  #10928 remains open for acceptance/exchange follow-through. Next is explicit
  local accepted-state/publication and native-owner mapping resolution, not
  activation. Release, installed adoption, OneDrive publication and peer receipt
  remain distinct, unproved gates; this effort stays Active.

### 2026-11-06 - Explicit local exchange implemented
- Added separate local acceptance and publication-intent watermarks under an
  explicit participant/publisher/domain policy. Both operations preview first,
  recheck confirmed inputs under the native local writer lock and replace
  complete snapshots atomically. No native configuration or binding is applied.
- The contained Windows packet passed 68 focused preference/exchange/CLI cases.
  Final offline-directory and hardlink-alias controls passed with all 29
  exchange cases. Three fresh-process native tests passed, including a complete
  publisher-to-receiver/offline flow and two real concurrent writers refusing
  the stale preview. Ruff F/E9 and install contracts passed.
- This proves explicit local files, not a synchronized provider. No installed
  runtime, Linux native lane, real carrier, native owner adoption, enrollment
  or activation was exercised. Those validation and Phase 4/5 gates stay open.

### 2026-11-06 - Local exchange reviewed and merged
- #10949 merged as `827acd1855b2728d2a5d48dbf8b04e1f61160230` after current-head
  Copilot approval, resolved feedback and green required CI.
- Review corrections reject parent traversal before normalization, consume
  validated lock/write paths and sync POSIX watermark directories before
  publication or success, including retries. The path/native packet passed
  38 tests; the final exchange packet passed 39 after durability controls.
- Confirmed every changed file matched the squash merge before reconciling
  local history. Native canonical-ID mapping resolution and explicit adoption
  are next; the parent effort remains Active. Source release, installed
  adoption, platform/carrier qualification and activation remain separate gates.

### 2026-11-06 - Native mapping owner implemented and qualified locally
- Centralized the existing canonical repository-ID codec for legacy/namespaced
  readers, added exact registered-ID mapping resolution and extended the native
  existing-binding preview/apply transaction. Existing local bindings remain
  authoritative; unresolved/ambiguous/stale registrations never use a basename.
- The focused native/configuration packet passed 37 cases. The final adapter
  and real selected-payload packet passed 30 after stream-capture qualification.
  The real first-touch flow used fresh Git roots and a credential-free example
  provider, committed the pointer through the existing owner and read back
  registration/state-root readiness. It passed in 41.497 seconds end to end
  with the original 18/26-second operation budgets unchanged.
- Diagnosed the actual apply deadline before changing it: repeated cold CLI
  reads inside the already-locked worker consumed its budget. An optional
  private read-only adapter now runs the same native handlers/rechecks in
  process, using the existing real-stdout JSON capture seam. Payload baseline
  and optional-adapter controls passed with all 73 binding-owner tests.
- An existing six-call catalog test hit the per-item deadline; its six
  independent real states now have named cases, identical assertions/process
  count and unchanged deadlines. Each passed in 3.6-4.9 seconds. Linux native,
  release/installed adoption, remaining domain adapters and carrier activation
  are still open; this is source/isolated validation, not a live rollout.
