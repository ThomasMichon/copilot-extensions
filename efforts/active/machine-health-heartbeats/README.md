# Machine Health Heartbeats

- **Slug:** `machine-health-heartbeats`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs targeting `dev`
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [machine heartbeats](../../../visions/telemetry/machine-heartbeats/README.md),
  [user-state carriers](../../../visions/user-state-carriers/README.md)
- **Umbrella issue:** #10873
- **Sub-issues:** #10881 (local report/chart consumer), #10928 (preference preview),
  #11081 (guarded native telemetry adoption), #11119 (local hourly health store),
  #11157 (passive owner observations)

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

The 2026-11-09 continuation adds a by-convention way for installable services to
surface hourly data and telemetry for aggregation. The verbatim follow-up is in
the [request capture](inception-transcript.md#installable-service-contribution-follow-up);
the proposed [service contribution contract](service-contributions.md) is a
review-before-code amendment, not a new resident collector or activation grant.

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
- [x] Prove explicitly confirmed Copilot scalar-floor adoption through native
  configuration, alias scope, validation and the serialized settings owner.
- [x] Expose producer-owned retained telemetry configuration preview/apply
  without provisioning, activation or projected-task authority bypass.
- [x] Prove capability-selected telemetry adoption with accepted-input
  revalidation at the producer's write boundary, preserving custom sinks.
- [x] Prove configured local-replica preference exchange through the normal
  CLI and existing transaction, without claiming selected-provider delivery.
- [ ] Extend the existing Agent Machines configuration owner to publish and
  receive explicitly supported nonsecret core preferences through the selected
  carrier, not through the health-record parser.
- [ ] Reuse normal configuration validation, scope, precedence, preview/apply
  and local authority; preserve existing bindings and machine overrides.
- [ ] Qualify delayed/conflicting preferences, unsupported versions, offline
  operation and safe withdrawal before any adopted application.

### Phase 5 - Hourly health engine and carrier qualification
- [x] Prove explicit typed observation -> local hourly slot -> age-qualified
  reader through the normal CLI (#11119), without claiming probe execution.
- [x] Prove the first passive local owner-listener observation through the
  normal CLI and existing local record/reader flow (#11157), keeping all other
  domains unknown and installed-provider qualification separate.
- [x] Review the installable-service hourly contribution/discovery amendment.
- [x] Prove a bounded versioned contribution through an actual service-owned
  export, selected-runtime declaration and normal machine report consumer (#11823).
- [x] Compose the existing age-qualified local health reader alongside telemetry
  and service namespaces in the normal report/chart, without a new writer or job (#11843).
- [x] Review the [bounded local collection/staging amendment](local-collection.md)
  before implementing the combined hourly operation or native job.
- [ ] Prove the complete explicit local collect -> stage -> reread/report flow,
  keeping present health and prior-hour telemetry correctly attributed.
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
- [ ] Service contributions: installed-owner capability, schema/time/metric
  semantics, partial/unsupported coverage, bounded export/cleanup, privacy
  reduction, namespaced aggregation and a real producer-to-report flow.
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

### 2026-11-06 - Native mapping review corrections
- Refuse malformed/unreadable global, project-local and drop-in binding
  sources using the native acquisition observations, while retaining missing
  and explicitly unset source behavior. Supplied empty portable-ID options
  cannot downgrade to the legacy name-based flow or bypass mixed selectors.
- The focused Windows binding/parser packet passed 61 cases; 17 knowledge
  acquisition controls passed after updating their original-owner fixture
  and typed-error expectations. The actual fresh-root selected-payload
  preview/apply/readback passed again in 25.16 seconds with unchanged budgets.
- Corrected a source-guard inventory bug: HTTP-52 completeness now selects
  its dynamic owner modules from the captured tree rather than future source
  additions. Its regression and the real 1,044-fixture contract check pass;
  no historical evidence was rewritten. Ruff and install contracts pass.
- Required exact-source Linux CI and current-head review remain open.
  No installed update, binding change, carrier effect or activation occurred.

### 2026-11-06 - Portable scope guarded at pointer commit
- Carry the confirmed portable resolution into the registry-locked worker and
  recheck it through the existing pointer-write precondition. Portable apply
  requires the selected payload's additive precondition capability; older
  name-based callers retain their version-2 behavior.
- Normalize effective binding names using the native resolver's whitespace
  rule before resolution and identity comparison. Existing bindings still win.
- The focused Windows owner packet passed 79 tests and the binding-payload
  packet passed 78. Two old-capability refusal controls and the real fresh-root
  success case passed. The new late-global-machine control exposed a missing
  structured error receipt; after correction its residual run passed in
  27.87 seconds, preserving pointer/registry bytes and the original budgets.
- This is isolated source/first-touch proof, not installed-runtime or Linux
  native qualification. Review and exact-source CI remain required; other
  domain adapters, carrier qualification and activation remain open.

### 2026-11-06 - Native mapping owner merged
- #11017 merged as `5df36fe7ea7135f05f4b17ec35eceb27981901ac` after current-head
  Copilot approval with no findings, resolved threads and green required CI.
- Reconciled the four published commits only after confirming 25 changed
  file blobs and two contribution deltas match the squash merge. Independent
  upstream evidence additions were preserved.
- The parent effort remains Active. Next is explicit adoption of typed Copilot
  scalar floors through the existing configuration/settings owner; no received
  document becomes a requirement package or activation grant. Release,
  installed adoption, remaining domain/carrier qualification and activation
  remain separate gates.

### 2026-11-06 - Explicit Copilot floor adapter
- Added a separately selected `preferences copilot` preview/apply over local
  accepted state and policy. Scope is native-owner resolved; typed floors stay
  below native defaults/live values and enforce constraints. Only the two
  existing scalar fields reach the settings merge, not arbitrary requirements,
  other settings, resources, modules, bindings or activation.
- Confirmation binds accepted/local/policy data, native context and packages,
  target/live settings and proposed changes. The existing settings writer
  rechecks the proposal before backup/write; malformed live settings and
  native-owner refusals remain non-mutating and sanitized.
- The Windows focused packet passed 123 cases with six platform-specific
  skips. Final floor/first-touch refusal controls passed 17 cases, followed by
  the isolated owner-refusal sanitization control. Ruff, install contracts
  and documentation consistency passed.
- The positive consumer used the normal in-process CLI, native collector,
  validator and real settings writer with an explicit native-context fixture.
  A fresh normal module process proved absent-owner refusal without bootstrap.
  This is not a configured peer, installed-runtime, native Linux or live-carrier
  qualification. No installer behavior changed; a full clean-room installation
  lane was not exercised. Carrier writes and running activation remain held.

### 2026-11-06 - Copilot adapter review corrections
- Resolve native admitted aliases for the owner-selected logical machine and
  retain that same set through discovery, layering and token confirmation.
  Alias-gated packages, directories and overlays preserve enforcement without
  leaking native-host aliases into guests.
- Serialize all native settings applies at the shared target, not a preference
  state directory, across read/merge/recheck/backup/replacement. Previews remain
  lock-free. A retained local lock is not fleet-state coordination.
- The Windows correction packet passed 48 cases with six platform-specific
  skips. Six alias/source/host-guest controls and a real two-process overlapping
  writer control passed: a stale writer refused, then a refreshed writer
  preserved both adopted fields and unmanaged state.
- Current-head review and CI remain required. These source corrections do not
  activate a carrier, install a runtime or change a running session.

### 2026-11-06 - Stable settings lock identity
- The settings lock resolves only its parent, preserving the final filename.
  The existing linked-target check now refuses a symlinked settings file
  before read/merge/write instead of selecting the referent's lock and then
  switching lock identities after replacement.
- Both focused Windows controls passed without skips: a real file symlink
  remained intact with unchanged referent bytes and no lock creation; the real
  two-process ordinary-target serialization/refreshed-write flow still passed.
  Ruff and whitespace passed. Review and exact-source CI remain open.

### 2026-11-06 - Empty native scope refuses portable adoption
- Refuse `native-defaults-unavailable` when the repository/knowledge requirement
  union is empty. The existing ordinary-default collector cannot carry native
  user or repository-machine floors through that empty union; refusing preserves
  their authority without generating a placeholder requirement.
- Both empty-scope controls with native user/repository-machine defaults and
  the supported-scope normal CLI adoption/withdrawal control passed. Ruff and
  whitespace passed. Empty-union adoption remains unsupported, not silently
  qualified by these refusal tests. Review and current-head CI remain open.

### 2026-11-06 - Deterministic native scalar contributors
- The native ordinary-default collector now orders its scalar fields before
  recording contributors. Set iteration cannot change the manifest digest and
  spuriously invalidate an unchanged Copilot preview in another process.
- A forced field-order regression reproduced the original token mismatch.
  Separate-process CLI preview/apply controls retain native collection,
  validation and the real settings writer, with fixture-resolved context only.
  This does not qualify an installed owner or carrier. Review and exact-source
  CI remain required; the parent effort stays Active.

### 2026-11-06 - Copilot scalar owner merged
- #11037 merged as `c9554dc850856fb118a6396c978c80dd86d19b4f` after current-head
  Copilot approval with no findings, resolved threads and green required CI.
  The final hash-order controls passed without skips; the earlier focused
  correction packet preserved native precedence and unmanaged state.
- Completed only the explicit Copilot scalar-owner checkpoint. Empty native
  requirement unions remain unsupported with a visible refusal. The other
  domain owners, selected-carrier exchange and native/installed qualification
  remain open; release and running adoption are not inferred from this merge.
- Next is owner-controlled configuration-only adoption for retained telemetry,
  preserving preprovisioned exclusive namespaces and custom sink declarations.
  Maintenance and venue preferences retain their existing effect owners.
  Health probes, scheduling and live carrier qualification remain Phase 5;
  no carrier write, enrollment, update or service activation is authorized here.

### 2026-11-06 - Native retained-configuration provider
- Claimed #11050 for the producer-owned configuration prerequisite. Bridge and
  Dispatch now expose v1 runtime support, bounded preview and confirmed atomic
  apply over their native targets. The dependency-free shared planner receives
  the existing operation-lock and strict configuration writer from each owner.
- Preview creates nothing. Apply rechecks current bytes, paths, namespace and
  environment sink under custody, preserves custom/legacy modes and unrelated
  fields, and starts no factory, buffer, coordinator or service.
- The Windows Bridge packet passed 40 cases; its one invalid cross-platform
  fixture was corrected to use a native absolute path and the three preservation
  controls passed. Dispatch passed 31 offline-adoption/retention/legacy cases.
  Both real normal CLI processes exercised preview then confirmed apply in
  fresh isolated configuration roots. Existing retained-loader behavior passed.
  The final adoption packets passed 21 Bridge and three Dispatch cases without
  skips, including strict nonfinite-number refusal and the imported parser.
  Ruff, install contract, module caps, documentation/runbook checks and
  whitespace passed. Review and exact-source CI remain required before merge.
- Unit and lightweight end-to-end lanes use actual native parser/configuration
  and lock/write owners. Fresh roots prove first configuration without bootstrap
  or service activation. An installed/live external lane is not exercised: this
  operation has no external backend, and installed/carrier/activation effects
  remain independently held. Source-only Windows evidence does not qualify
  Linux/macOS native execution or any deployed producer.
- Only providers expand in this slice. Machines must later discover support
  from its selected producer executable before adoption; unsupported or
  rolled-back providers retain a visible pre-effect refusal, never a fallback
  writer. The parent preference/carrier and health-engine gates remain open.

### 2026-11-06 - Provider review corrections
- #11058's first current-head review identified two real prerequisite gaps:
  payload launchers could provision a missing runtime, and byte-based JSON
  decoding accepted encodings the native UTF-8 loaders reject.
- Both producers' Bash/PowerShell runtime gates now resolve telemetry commands
  read-only and refuse missing runtimes before installation. Existing ordinary
  first-use behavior remains unchanged. Strict UTF-8 decoding refuses BOM and
  UTF-16/32 input even when retained settings otherwise match.
- Bridge's corrected packet passed 11 cases with six unavailable native POSIX
  skips, including actual payload refusal in legacy/namespaced roots, existing
  observer behavior, encoding refusal and normal confirmed adoption. Dispatch
  passed three actual PowerShell payload controls with three POSIX skips.
  Fixture corrections exclude PowerShell-owned startup caches from plugin
  state assertions and do not invoke Windows' WSL launcher as native Bash.
- The first CI unit lane passed; its strict type gate rejected three missing
  generic parameters in the new shared API. Added precise callback/lock/parser
  annotations; the admitted library passes Python-3.10 Linux and Windows type
  targets (18 source files each). This is portable type evidence, not native
  Linux execution. Ruff, install and documentation checks remain green.
- Review and exact-source CI must pass after publishing these corrections.
  No live configuration, runtime installation, service activation or carrier
  publication was performed.

### 2026-11-06 - Projected task authority preserved
- The second provider review found that Dispatch's early telemetry route
  bypassed task-only admission. It now uses the same projected-environment
  guard as the adjacent local namespaces and falls through to native admission.
- Nine focused Windows controls passed, including empty projected-field
  admission and a real CLI apply using a valid preview token with projected
  task credentials. Native admission refused the command before any
  configuration, lock or spool write.
- The Bridge contract failure was stale CLI source inventory. Comparison
  with the captured HTTP-52 source shows only offline telemetry registration
  in the parser. Its current execution closure still binds the exact captured
  CLI, so non-semantic inventory refresh was not admissible. Telemetry now
  registers through the existing configuration registrar, preserving the
  imported parser's behavior and restoring the wire-bound CLI bytes.
  Historical inventory, fixtures and provenance remain unchanged.
- The Bridge adoption/configuration packet passed 107 cases with one platform
  skip; the immutable contract checker passed two contracts and 1,051 fixtures.
  Ruff and whitespace passed.
- Required current-head CI and review still gate this provider slice.
  Machines adoption, carrier exchange, probes, scheduling and activation
  remain open; no installed or running qualification is inferred.

### 2026-11-06 - Task-prefixed cold launch remains passive
- Current-head CI passed, but the next review identified a further launcher
  gap: explicit task-worker root options hid the telemetry namespace from
  the cold-start scanner, allowing provisioning before native admission.
- Both Dispatch runtime gates now consume all five task-worker value options
  and their equals forms before classifying telemetry. Payload controls assert
  missing-runtime refusal without installation or configuration-state changes.
- Seven focused Windows controls passed, including ordinary warm-runtime
  availability; five native POSIX cases remain skipped on this host. The
  existing warm-runtime fixture now locates the conditional resolver assignment
  introduced by the configuration-only gate. Ruff and whitespace passed.
- Fresh current-head review and CI remain required. This correction does not
  authorize runtime installation, active capture or carrier publication.

### 2026-11-06 - Retained-configuration providers merged
- #11058 merged as `48e4125655f03440a37d2f531f461a6c707f48c8`
  after actual current-head Copilot approval with no findings, resolved
  threads and green required CI. Source-attribution metadata passed.
- All 20 PR-touched paths exactly matched the verified merge. The managed
  pull-forward safely aborted on squash-history conflicts; a retained backup
  and empty-range rebase reconciled only the proved merged history. No
  unmerged source was discarded.
- Completed only the producer prerequisite. Machines must select a capable
  pinned native producer and revalidate the accepted snapshot, policy and
  local overrides at the actual producer write boundary. A caller-only check
  would leave a stale-authority gap.
- The parent effort remains Active. Carrier exchange, native installed
  qualification, health probes, scheduling and explicitly authorized
  activation are still open. A source merge is not a release or rollout.

### 2026-11-06 - Guarded telemetry adoption implemented
- Claimed #11081 for explicitly confirmed Machines adoption through a pinned
  same-cell producer. The producer's additive input-fence capability binds
  exact validated policy, accepted-snapshot and optional local-override bytes
  to its confirmation and rechecks them inside its native writer lock.
- Custom sinks refuse without overwrite. Disabled/withdrawn contributions
  contact no producer and do not erase adopted values. Unsupported providers
  cannot trigger a fallback writer. Reporting remains visibly unsupported
  for adoption; it has no native configuration field.
- The Machines/codec packet passed 35 cases. The first fixture incorrectly
  launched Bridge inside Machines' Python and exposed the missing Bridge-only
  dependency; the cross-plugin packet now uses the real Dispatch parser and
  transaction. Bridge's own prepared environment passed 29 native adoption
  cases plus three malformed/oversize/duplicate input-fence controls.
- Independent Machines CLI preview/apply processes reached the real Dispatch
  configuration writer in fresh, preprovisioned roots. Only native context and
  peer resolution were fixture-supplied; this is not immutable-slot, installed
  selection or carrier qualification. No installer is needed for this path.
- The shared library passed strict Python-3.10 Linux and Windows type targets
  over 18 source files each. Install contracts, module ceilings, documentation
  consistency, Ruff and whitespace passed.
- Post-write acknowledgement failures preserve an unknown outcome instead of
  claiming an untouched configuration. The final adapter/independent-process
  packet passed ten cases without skips, including malformed native preview
  refusal. A separate actual-write/unknown-acknowledgement control passed
  without retrying the write. Review and required exact-source CI still gate
  this slice.
- These are unit/lightweight process and fresh-configuration controls, not a
  full installer clean-room or native Linux qualification. An installed/live
  lane is not exercised: runtime rollout and carrier/activation effects remain
  held, and the operation itself has no external backend. The parent stays Active.

### 2026-11-06 - Preserve restrictions across telemetry peer handoff
- The #11094 review found that native peer environment cleanup removes task
  identity fields and custom telemetry sink overrides. Producer-side admission
  alone therefore cannot preserve those caller restrictions.
- Machines now refuses Dispatch task environment entries and a selected
  producer's nonempty sink override before peer selection and before every
  capability, preview and apply handoff. The shared launcher is unchanged.
- The 26-case adapter packet passed. A source-resolved peer uses the real
  environment projection and native Dispatch writer in a temporary profile.
  Controls refuse restrictions before selection and after parent preview at
  each handoff, without creating native configuration or its writer lock.
  Bridge sink controls test the same pre-launch refusal without importing
  Bridge into Machines' dependency environment.
- Documentation consistency and whitespace passed. These remain fixture-resolved
  source/process controls, not installed immutable-slot or carrier qualification.
  Fresh exact-head CI and a passing review still gate this correction; no
  rollout, capture, carrier publication or activation is authorized by it.
- The fresh review confirmed both handoff corrections and found an exit-contract
  mismatch: a native invalid/unavailable request was classified as a preservation
  refusal. The CLI now returns `2` for native errors or unknown write outcomes,
  retaining `1` for known refusals. Three focused actual-producer CLI controls
  passed for missing retained storage, custom-sink preservation and an unknown
  post-write acknowledgement; the last still performs exactly one write.
- The next review found that a fenced input could itself name the native
  configuration output. Both producers now reject input paths or file
  identities overlapping their target or writer lock before issuing
  confirmation. A real Machines local-override/Dispatch-target collision
  preserves every input byte without creating a lock. That case and three
  existing changed-input controls passed; Bridge passed five native overlap
  and locked-input controls. Strict Python-3.10 library typing passed both
  Linux and Windows targets over 18 files each. These add no installed-runtime
  or carrier qualification.

### 2026-11-06 - Guarded adoption merged; local health follow-up
- #11094 merged as `55bdbdbdd8c57064e7de296d60975b0e8d6e4ccb`
  after actual current-head Copilot approval, resolved findings and required
  green CI. This closes only the guarded source-adoption checkbox in Phase 4.
  Release availability, installed operation, capture and carrier receipt remain
  separate and unproved; no runtime update or activation occurred.
- Pull-forward aborted on squash-history conflicts. The complete published
  slice matched the merge commit, with no unmerged source delta; a retained
  backup allowed non-destructive reconciliation to the confirmed upstream tree.
- Claimed #11119 for the next complete source-only local flow: strict typed
  observation input, atomic 168-hour slots and an age-qualified reader through
  the normal Machines CLI. Probe collection, native scheduling and carrier
  enrollment/publication remain outside that slice and open in the parent.

### 2026-11-06 - Explicit local hourly store implemented
- #11119 now has an explicit `health record` preview/confirmed apply and
  `health inspect` consumer. A strict separate policy selects publisher
  continuity; the health parser never consumes preferences or executes probes.
- The bounded closed observation vocabulary separates listeners, SSH transport
  and authenticated direction, Gateway, authorization, credential readiness and
  logical agents. Unknown counts remain null; supplied evidence is visibly
  unverified. No arbitrary errors, paths, process inventory or credentials enter
  stored records.
- Complete flushed records occupy 168 absolute-hour slots. Native local writer
  exclusion and exact policy/input/slot-byte confirmation preserve revisions,
  missed parents, future timestamps, publisher mismatch and delayed weekly
  replicas. Reader gaps and old domain proof remain unknown/stale, not uptime.
- The first combined run passed the existing report cases and functional store
  controls, but pytest's generated oversized-byte case ID exceeded Windows'
  environment-variable limit. Short explicit IDs fixed the fixture without
  lowering the 64 KiB bound. The final store/independent-process CLI packet
  passed 23 cases; six additional hardlink/path and reader expiry/foreign/future
  controls passed. No cases skipped.
- Post-publication write or lock-release acknowledgement failures keep an
  unknown outcome and never retry. Fresh preprovisioned temporary stores prove
  the real module CLI -> atomic slot -> independent reader flow, not installed
  selection, native probe execution, full installer clean-room or Linux native
  scheduling. Those lanes and carrier/activation effects remain open.
- Review, exact-source CI and source merge still gate this implementation.
  The parent remains Active; no runtime update, schedule or carrier was activated.
- Number decoding now rejects floating-point input and oversized integer
  strings before conversion, so a malformed stored slot cannot trip Python's
  integer-conversion limit and fail the reader. The exact-source bounded-input
  and normal CLI selection passed eight cases, including independent processes.
- Reader policy/namespace revalidation and the final independent CLI flow
  passed two focused cases. Ruff, documentation consistency, install contracts
  and whitespace checks passed; the Machines patch changefile is included.

### 2026-11-06 - Local store review corrections
- The #11139 review found three valid gaps: a response-output failure could
  misreport a committed slot as unapplied, a required input disappearing
  between validation and read could escape bounded refusal, and synthesized
  unknown domains omitted their nullable count/direction fields.
- Apply-output failures now retain an unknown acknowledgement; required-file
  disappearance raises a bounded error while optional vanished slots stay
  missing. Synthesized domains share the explicit unknown observation shape.
- Eight focused controls passed initially. An additional real buffered
  closed-pipe CLI control then exposed Python's shutdown flush replacing
  exit 2 with 120 after the correct unknown acknowledgement. Only the CLI's
  own failed stdout descriptor is redirected to prevent that second failure;
  embedded caller streams remain untouched.
- The final nine-case packet passed without skips, including actual committed
  storage plus a closed native stdout pipe, in-memory write/flush failures,
  required/optional file races and the independent empty-domain CLI roundtrip.
  Ruff and whitespace passed. Fresh review and exact-head CI still gate this
  correction. No probe, schedule, carrier or running-service effect occurred.

### 2026-11-06 - Local store merged; passive observations remain
- #11139 merged as `bf30dc517d89befcebeff6b85741a4a1342e1c2c`
  after actual current-head Copilot approval, resolved findings and required
  green CI. This closes only the explicit local-storage consumer checkpoint;
  supplied observations are not proof that native probes ran.
- Pull-forward again aborted on squash-history conflicts. Every owned file
  matched the confirmed merge, with no unmerged source delta. A retained
  backup and non-destructive reconciliation preserved the landed result.
- Next is bounded passive collection through the existing native observation
  owners: inventory their supported read-only surfaces and caller restrictions,
  then prove an owner observation -> local record -> age-qualified reader flow.
  Do not invent a second agent inventory or infer authentication from a port.
- Carrier exchange, native scheduling/lifetime, installed qualification,
  enrollment and activation remain open. No runtime was updated or activated;
  the parent remains Active and its worktree must not be finalized.

### 2026-11-06 - Passive listener source consumer
- Claimed #11157 after deduplication. The first passive adapter reads only an
  explicitly selected loopback Bridge `/health` endpoint; its advertised
  baseline contract and service/status fields qualify local listener response,
  not authentication, credentials, Gateway access or logical agents.
- One request has a two-second absolute transport deadline and 64 KiB aggregate
  wire bound. No proxy, redirect, credential lookup, retry, discovery,
  provisioning, service start, remediation or storage effect is performed.
  Additive owner timings and large timestamps are discarded, not interpreted.
- The normal observation CLI emits the existing typed input for separately
  confirmed local recording. Other domains remain unknown. The store still
  labels supplied data unverified and independently ages known proof to stale.
- The initial combined observation/store/report packet passed 64 cases. The
  final observer packet passed 23 cases with no skips, including the original
  Bridge health handler's response from a separately prepared source test
  interpreter, real loopback transport, normal module subprocesses, fresh local
  store and stale readback. Diagnostics were isolated; no installed endpoint
  or full daemon ASGI/lifespan was exercised. Trickle deadline, wire overflow,
  redirect refusal and closed stdout retained bounded outcomes.
- Ruff, documentation consistency, install contracts, module ceilings, effort
  structure and whitespace passed. The first default Python had no Ruff;
  the already available Ruff executable supplied the successful lint check.
  Review, exact-source CI and source merge remain required.
- This advances the machine-heartbeat domain-observation vision without
  changing its intent or existing producer/owner contracts. Linux, installed
  qualification, other domain owners, native scheduling, carrier delivery,
  enrollment and activation remain open; the parent is not complete.

### 2026-11-06 - Passive listener review corrections
- The #11163 review identified a real framing gap: a bounded HTTP read can
  return a valid JSON prefix despite missing declared Content-Length bytes.
  Listener proof now additionally requires no declared body remainder. Reads
  stay bounded; a huge advertised length cannot request a huge allocation.
- Replaced the whitespace-only overflow fixture with an otherwise qualifying
  JSON body padded to the body limit, so only aggregate header/body wire
  enforcement prevents success. Removed a numeric substring assertion that
  could accidentally match an unfrozen observation timestamp.
- The final 26-case observer packet passed without skips, including actual
  owner-response compatibility, real truncated length/chunk and huge-length
  controls, overflow/trickle limits, source CLI/store readback and output
  failure. Fresh current-head review and CI remain required. No operational
  activation or carrier effect occurred.

### 2026-11-06 - Passive listener merged; parent continues
- #11163 merged as `c0d31cd50161e93ec2c231111bdb310b571892a4`
  after actual current-head Copilot approval, all three findings resolved and
  required green CI. A transient provider merge error was reconciled as
  unmerged before one successful supported retry; success was verified.
- The completed checkpoint is listener-only source collection through the
  local record/reader flow. No installed daemon, SSH authentication, Gateway,
  credential provider or logical-agent census is qualified by that result.
  Bridge's known-session observation contract explicitly does not establish
  a current actor census; host/process counts remain unusable substitutes.
- Every owned file matched the confirmed merge and the fetched base before
  non-destructive history reconciliation, with a retained backup. No unmerged
  source delta was discarded.
- Continue the still-open core-preference carrier phase through the existing
  Agent Machines configuration/exchange owner, deduplicating the next bounded
  source objective first. Preserve native adoption, local overrides and
  existing binding authority. Source tests must not become real carrier writes.
- Other domain observations, diagnosed-outage qualification, Linux/installed
  evidence, native scheduling, carrier delivery, enrollment and activation
  remain open. No runtime was updated, job enrolled or service activated;
  the parent remains Active and its worktree must not be finalized.

### 2026-11-07 - Selected local preference-carrier adapter
- Continued #10928 with an explicit local OneDrive replica adapter under
  `preferences carrier publish|receive`. It reuses the existing policy,
  revision/withdrawal rules, local watermarks and confirmed atomic exchange.
  The strict local carrier configuration selects a preprovisioned dedicated
  namespace, account label and participant list; it grants no native adoption.
- A bounded nonrecursive namespace census blocks every unexpected entry.
  Configuration, census, candidate and local state are rechecked under the
  existing local writer lock. Local policy, watermarks, candidates and overrides
  remain outside the synchronized namespace; only the admitted snapshot leaves.
  No discovery, account login, provider API, watcher or enrollment is added.
- The initial codec/exchange packet passed 96 tests. The final carrier/native
  packet passed 35 tests without skips, including fresh separate CLI processes,
  two fixture-owned replicas, shared-knowledge mappings, local overrides,
  withdrawal, offline use, older replicas and closed-output unknown
  acknowledgement. Earlier oversized aggregate tests were split into bounded
  first-use and reconcile controls without increasing deadlines.
- A preceding run passed its tests but failed the host-environment drift audit.
  No host state was rolled back and the audit was not relaxed; the final
  unchanged-audit run passed. This does not establish the drift's cause.
- The fixture explicitly copies local replicas. It is not OneDrive delivery,
  authenticated audience, cloud-placeholder, installed-runtime or native Linux
  qualification. Runtime rollout, real carrier writes and activation remain
  separately held. Other native domains, health probes and scheduling remain
  open; this is a source slice, not completion of Phase 4 or the parent effort.
- Documentation, Ruff, installation contracts and whitespace passed.
  Required exact-source CI, review and source merge still gate this slice.

### 2026-11-07 - Local carrier source merged; qualification remains open
- #11438 merged as `515275f8507609fd48a27dcaae5bf7dcffa36fdb`
  after actual current-head Copilot approval with no findings and green
  required checks. The published description preserved source attribution
  and contained no closing reference to #10928 or the parent #10873.
- Completed only the new local-replica source checkpoint. The selected
  provider's actual publication, synchronization and peer receipt remain
  unqualified. Receiving preferences still does not apply them; native
  maintenance/venue adoption, other domain observations, diagnosis, scheduling
  and platform/installed validation remain open.
- The earlier source and adopter PR claims are terminal. The retained source
  and controller worktrees still own the active parent effort and its
  configuration-only scope. No updater, carrier write, schedule enrollment
  or service activation occurred.
- Next source work is native maintenance-preference preview, using the existing
  resource/authority owner without turning `automatic` data into execution
  consent. The existing independent live-carrier and activation holds remain.

### 2026-11-09 - Resume hourly service contributions
- The operator resumed the parent effort and requested a by-convention way
  for installable services to surface hourly data and telemetry for aggregation.
  The verbatim follow-up is preserved in the request capture.
- This reorders the next source slice from maintenance preferences to
  Phase 5 service contributions; the remaining Phase 4 items are not dropped.
  The Health implementation owner retains custody in the same source worktree.
- The proposed service-contribution amendment reuses installation and native
  service owners, retained telemetry and the bounded report consumer. It defines
  selected-runtime capability, a common hourly data envelope and a real
  producer-to-report acceptance flow, without a second registry, collector,
  writer or service lifecycle authority.
- Claimed this continuation on #10873 after checking open reporting work.
  Existing producer and process-registry owners retain their scope; no
  still-open contributor PR is superseded or pre-patched.
- This is a review-before-code source amendment. Its first protocol/provider
  implementation follows review and required CI; scheduling, real carrier
  writes, installed adoption, service activation and the rest of the parent
  completion gate remain open.

### 2026-11-09 - Contribution convention reviewed; implement the real flow
- #11793 merged as `a0172e394ae3e997ca38c8a692fa03f221193bd9`
  after actual current-head Copilot approval with no findings, green required
  CI, and no closing reference to the still-open parent.
- Completed only the design-review checkbox. The next acceptance criterion
  is an actual service-owned hourly export, qualified selected-runtime support
  and normal Agent Machines report consumer, with missing/partial evidence
  preserved. A protocol validator alone does not satisfy this checkpoint.
- Existing telemetry library, retained service inventory and pinned native
  invocation seams are implementation candidates, not a second telemetry bus,
  service registry or authority. Keep independent owner work and all remaining
  platform, scheduling, carrier and activation gates open.

### 2026-11-09 - First native service contribution implemented locally
- Added the closed hourly protocol and bounded retained-file reducer in the
  existing telemetry library. Dispatch owns the first privacy-reduced native
  export and optional installed-package reporting declaration.
- The normal Machines report reuses same-cell installation selection and
  immutable-runtime retention, qualifies installed metadata before invoking the
  normal service parser, and preserves independent namespaced contributions
  in JSON and the static chart. Missing/newer support stays unsupported without
  a trial new command. Repeated installation selections are not executed twice.
- Dispatch's focused packet passed 57 cases, including actual retained producer
  events, the native export, fixture-selected package metadata, normal report/
  chart, version skew, rollback to absent metadata and unchanged legacy sinks.
  Machines passed 35 existing/new report controls and then 16 focused collection/
  native cleanup controls after strict byte capture was added. Nine actor-free
  wire-codec cases also pass and have explicit required-CI source/node admission.
- The first cleanup experiment did not explicitly inherit the descendant's
  pipes on Windows. The corrected fixture passes actual stdout/stderr handles,
  separately observes root exit versus stream completion, and confirms the
  existing native settlement callback on timeout and output-bound paths.
- This is source/fixture qualification, not a deployed installation inventory,
  a built-wheel release, running capture or activated publication. The full
  clean-room installer and live external lanes were not exercised: this slice
  changes package data and read-only source operations, not install/lifecycle
  machinery; no enrolled external service venue is authorized for this effort.
  The fresh fixture interpreter proves first export/consumer use only.
- Install-contract, touched-Python lint, documentation consistency and headless
  launch guards pass. Final admitted type/unit gates, review and merge remain
  in progress. No service update/reload, schedule, credentials or carrier write
  occurred; all independent parent gates remain open.

### 2026-11-09 - Exact source gates and package delivery
- The final Machines report/native-cleanup packet passed 41 cases. The
  canonical wire/collection policy packet now passes 13 actor-free cases,
  including task-scope refusal, independent namespaces, duplicate installation
  identity and preservation of unsettled native custody.
- All 41 admitted strict typing projects pass for both their Linux and
  Windows target configurations. This is static target checking, not Linux
  native execution. Shared-library release metadata includes every current
  telemetry consumer and the canonical library; changefile presence passes.
- Built the actual Dispatch wheel without build-environment installation and
  independently verified that its reporting declaration matches source bytes
  and that the native exporter is included. This is package-content proof, not
  an installed service update or release.
- The full local Windows unit runner refused six checkout-CRLF template files
  before test execution. Their committed Git blobs match the admitted hashes.
  The ongoing full-packet run uses a canonical committed-source snapshot;
  no source admission hash or machine Git configuration was changed to accept
  those checkout transformations. Required PR CI remains an independent gate.

### 2026-11-09 - Native history priority and deadline corrections
- #11823 opened on the rebased source. Its first current-head required CI was
  green, while two Medium findings correctly blocked review acceptance.
- Archive ordering and early file truncation could hide the active source;
  an old prefix within that active file could likewise exhaust record work.
  Selection now reserves the known active source before archive/directory
  limits, prioritizes archive rotation near the requested hour, and reduces
  bounded snapshot windows newest-line first. Only source event time assigns
  the hour; clipped/partial/oversized fragments remain excluded and explicit.
- Native execution timeout alone did not include retirement/pipe waits.
  Collection now passes the existing absolute `OwnedExecutionBudget`, capped
  by child and aggregate deadlines with cleanup and response reserves inside
  those limits. Unconfirmed native custody still propagates unchanged.
- The corrected Dispatch packet passed 60 cases, including saturated archives
  and active prefixes. Machines passed 19 focused policy/native-cleanup cases;
  native settlement callbacks occurred before their actual absolute deadlines.
  Eighteen actor-free codec/selection/collection/snapshot controls pass, and
  the changed shared library passes both strict target configurations.
- The full prior Windows source packet produced 4,705 passes and three skips;
  its no-skip evidence gate correctly refused completion. A minimal control
  identified exactly the intentionally POSIX-only filesystem/kernel-lease and
  existing-mode cases. No skip, hash or permission was changed to claim a
  Windows all-pass result. Current-head Linux PR CI is the independent full
  unit gate; the new source head still needs fresh CI and review acceptance.

### 2026-11-09 - Duration producer/reducer qualification
- The corrected-head review confirms both runtime fixes and their resolved
  threads. Its remaining Medium observation identifies missing qualification
  of the advertised duration path, not another runtime change.
- Added actual adapter admission cases for missing/boolean/negative/overflow
  timing, foreign components and unsupported stage/outcome/kind; completion
  time rather than start or emit time determines the absolute source hour.
- The required packet now executes the actual span adapter, snapshot reducer
  and shared percentile primitive on raw events with successful, error,
  cancelled and unavailable outcomes. Only the successful population affects
  p50/p95/maximum; a span completing immediately before the hour is excluded.
  All 28 actor-free cases pass.
- The targeted Dispatch adapter/native packet passes 35 cases. The existing
  measurement producer reaches the normal machine report/chart, and native
  retained mixed-outcome export independently confirms the duration summary
  without leaking correlation fields. Fixture source hours are controlled;
  no production performance, service activation or deployed adoption is claimed.

### 2026-11-09 - Native service contribution merged; continue hourly composition
- #11823 merged as `336f55e7f2c3021db482915390af5367dcb33acc`
  after actual current-head Copilot approval with no active issues, resolved
  review threads and green required CI, including freshly executed admitted
  units with coverage. No parent closing reference was present; #10873 remains
  open.
- Completed only the first service-owned contribution/export/consumer source
  checkpoint. Installed-host inventory/adoption, broader native domains,
  scheduling, the selected carrier, hourly publication and peer receipt remain
  open. A built wheel and fixture-resolved installation are not running adoption.
- Pull-forward encountered overlapping commits already integrated by the squash
  merge. All owned runtime/test/effort paths were verified preserved; the only
  differences were independently landed documentation and another module's
  source admission. A local recovery reference was retained and the clean
  worktree advanced without discarding any unmerged change.
- Next source stretch is bounded one-shot hourly health composition through the
  existing observation/slot and native-owner seams, starting with their current
  contracts rather than another collector or registry. Scheduling and carrier
  enrollment remain separate review/qualification and explicit activation gates.
  The remaining Phase 4 preference work and every parent validation item retain
  their existing owners and completion criteria.

### 2026-11-09 - Age-qualified health composed into the normal report
- Implemented the existing-design read-only composition checkpoint:
  `health report --store-dir` reuses the bounded local reader alongside retained
  telemetry and service namespaces. Available means readable, not healthy.
  Stale source evidence, unknown domains, missing/invalid slots and unverified
  input qualification survive; health never becomes an event count or uptime.
- The static chart adds separate slot/domain rows and proof-age details. A chart
  destination inside the exclusive health store refuses before effects.
  Invalid/unavailable store evidence remains an independent gap rather than
  suppressing valid telemetry or service contributions.
- Six actor-free cases cover actual projection, parser/report composition,
  missing evidence, old syntax and namespace preservation. The focused report/
  service/native packet passed 46 cases with one optional producer skip;
  selecting the existing source-bound Bridge interpreter then passed all nine
  native observation cases, including the actual Bridge handler through HTTP,
  confirmed slot storage, normal report/chart and unchanged-store readback.
- This is not a combined observe/record job or a new publication envelope.
  Native job overlap/storage/lifetime, broader domain adapters, installed
  adoption, scheduling, selected carrier, peer receipt and activation remain
  parent gates; no source result authorizes enrollment or service changes.

### 2026-11-09 - Preserve independent output for unresolvable stores
- A cyclic store path can fail canonical resolution before the qualified
  reader runs. Store-resolution failures now remain unavailable evidence for
  a valid external chart; lexical and resolved namespace overlap still refuse.
  An unresolvable chart remains a bounded output refusal rather than an
  uncaught path-bearing exception.
- All nine actor-free composition cases pass, including both runtime and OS
  resolution errors. Three focused native cases pass without skips: an actual
  cyclic filesystem path with independent telemetry/external chart, the
  normal qualified local-listener flow and the actual Bridge-owner flow.
  The existing storage and chart-namespace protections remain intact.

### 2026-11-09 - Read-only composition merged; bounded job remains
- #11843 merged as `fa743aaa4896a7477aa9948c78423a5a97960a89`
  after actual current-head Copilot approval with no active issues, a resolved
  thread and green required CI. The updated head's units executed freshly with
  coverage; the later metadata-triggered run also completed before merge.
  An earlier merge attempt correctly refused its still-pending required gate.
  No CI bypass was used, no parent closing reference was present, and #10873
  remains open.
- Re-read the parent Plan/Validation Plan: this completes read-only report
  composition only. It does not collect all domains, stage a combined hourly
  block, run independently on a schedule or qualify installed/carrier adoption.
  The remaining preference, outage, platform and activation gates are unchanged.
- Normal pull-forward auto-aborted on already-squashed overlapping commits.
  Every owned file was verified present upstream; only an independent testing
  section differed. A recovery reference was retained and the worktree advanced
  over the verified already-merged range without discarding unmerged content.
- Next: inspect the existing Agent Machines one-shot/native-job and health-slot
  owners, then refine the bounded local collection/staging contract before any
  new job or publication-envelope code. Reuse those owners and the new service
  contributions, not another collector, registry or writer. Actual enrollment,
  service changes and carrier publication remain separately authorized.

### 2026-11-09 - Bounded local collection/staging amendment
- Inspected the existing Windows Scheduled Task and Linux systemd-user
  definitions, retained-runtime service collector, observation and slot owners.
  Update jobs use update actions and unbounded execution settings; reuse their
  platform patterns, not their execution/opt-in policy.
- Proposed separate read, collect/preview and stage operations, a reader-first
  versioned envelope through the existing transaction owner, explicit local
  policy, bounded overlap/execution/storage and separately enrolled native jobs.
  Present health and prior-complete-hour telemetry update different absolute
  slots rather than backdating a check or claiming an early snapshot is a total.
- The amendment is pending review. Runtime, native qualification, carrier and
  activation remain unimplemented/unqualified by this design-only change.

### 2026-11-09 - Local collection design reviewed; consumer implementation next
- #11850 merged as `a45651933a` after current-head Copilot approval with no
  active issues and green required CI. No parent closing reference was present;
  #10873 remains open. The source worktree pulled forward successfully.
- Re-read the parent Plan/Validation Plan. Only the amendment's review gate is
  complete: explicit local collection/staging, native/platform and storage
  qualification, broader domains, preferences, carrier and activation remain.
- Next implement the explicit local consumer flow through the existing health
  owner. Preserve strict v1 behavior, attribute current health and prior-hour
  telemetry separately, and prove collect -> confirmed stage -> reread/report.
  Do not register a native job or touch installed services/carrier state.

### 2026-11-09 - Explicit one-hour collection consumer implemented
- Added opt-in local-store v2 and a closed hourly-envelope reader/writer through
  the existing health transaction. Strict observation/store v1 remains unchanged.
  The new explicit collect command emits one candidate, never stages it or
  registers a job. Current health and historical service export use separate
  invocations/hours; automatic two-target execution remains future job work.
- Retained/unselected components and revision checks are enforced at the record
  mutation boundary, not just by the collector. Collection uses a preprovisioned
  nonblocking native run lock and inherited service deadlines. Old/unsupported
  stores refuse without creating state.
- The focused collection/slot/service/native packet passed 66 cases, including
  actual retained producer -> native export -> candidate -> confirmed slot ->
  report/chart, distinct source hours and two-process normal/crash lock release.
  Fifteen actor-free development controls pass separately. Their exact source
  and node admission is expanded without weakening the unit portfolio.
- Native scheduling, installed adoption, broader domain adapters, carrier,
  preferences and activation remain parent gates. Review and final source
  checks for this implementation are pending.
- Final selected native controls pass three cases, including retained service
  evidence after incompatible provider metadata and normal/crash overlap release.
  The combined actor-free development packets pass 43 cases; Ruff, all 41
  admitted strict projects on both target platforms, install contracts and
  documentation consistency pass. Agent Machines' broader type-admission scope
  remains unchanged.
- The local full raw-byte admission check refuses six untouched template files
  with Windows CRLF bytes; every mismatch matches the admitted hash after LF
  normalization. No template or acceptance rule was changed to manufacture a
  pass. Canonical full unit/coverage evidence remains owed by required CI.

### 2026-11-09 - Preserve actual evidence, not merely its wrapper
- Fresh canonical unit/coverage CI passed the implementation head. Review then
  identified two concrete preservation gaps: native exporters can return a
  valid empty contribution, and a directly supplied candidate can do the same.
  The shared rule now checks actual metric, native-health or diagnostic evidence
  at both collection and confirmed-write boundaries.
- Explicit empty hours now refuse instead of becoming current-hour collection;
  the same explicit-hour distinction is retained in the report path.
  Added pure empty-hour/empty-evidence controls and an actual native exporter
  configuration-loss case that must retain the accepted contribution.
  Follow-up validation and current-head review remain pending.
