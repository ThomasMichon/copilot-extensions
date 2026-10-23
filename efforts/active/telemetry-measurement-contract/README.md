# Telemetry Measurement Contract

- **Slug:** `telemetry-measurement-contract`
- **Repo:** copilot-extensions
- **Branch(es):** independent documentation slice targeting `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** [Portable Telemetry](../../../visions/telemetry/README.md)
- **Umbrella issue:** #8102
- **Sub-issues:** none; existing implementation owners remain independent

## Guiding Intent

Agree on what is measured, how it maps to OpenTelemetry, and which meaningful
producer boundaries must eventually be instrumented. This effort delivers a
contract, catalog and reviewed future plan, **not runtime hooks or rollout**.
It is a bounded design sub-effort of
[Performance Telemetry](../performance-telemetry/README.md), not a second bus,
campaign coordinator or replacement for its retained acceptance.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Measurement contract author | Schema, catalog, evidence and design publication | #8102 |
| Existing production owners | Future call-site implementation, outside this deliverable | #7935/#5900, #7938/#7943, #5215, #6819 |

## Coordination

- **Topology:** one documentation writer; no production call-site edits.
- **Host (owns PRs):** Measurement contract author, scoped to #8102.
- **Delegates:** read-only source evidence only; no independent implementation.
- **Handoff:** reviewed catalog/contract to existing performance, Manager,
  Worktrees, Bridge, Dispatch and Logger owners through their existing issues.
- Preserve reserved constructors, launch/preparation, entry/hook and reporter
  files. No existing-agent contact, collector mutation or live enrollment.

## Context

Existing `libs/process-telemetry` has `agent.performance.v1` typed measurements,
bounded sink delivery and a real optional traces-only OTLP adapter. The retained
[interface](../performance-telemetry/interface.md) and
[Manager observation slice](../performance-telemetry/manager-observation.md)
already distinguish source callbacks from native headed/authority evidence.
Do not reimplement them or mistake that partial adoption for all signals.

Priority evidence is a bounded recent issue sample, not a frequency census:
#7938 (first paint), #7939 (Actions), #7935 (composed hooks), #7961 (configuration
reuse), #8051 (startup observation clocks), #7977 (unsettled waits). #6819
retains session-derived accounting; #5215 retains Bridge readiness ownership.

Source inspection used revision `0e6a1da286c17d5e62d9d0f12631ae617fa9bff8`
for the existing producer/OTLP/Manager contracts. No live benchmarks or real
session/accounting data were collected.

## Request

Summary of accepted direction: retain the supplied QoS and Engagement concepts,
align with actual OpenTelemetry, cover every requested measurement family and
extend the list where issue evidence justifies it. Expected and unexpected
failures remain distinguishable without hiding actual failures from OTel.
The operator selected **contract/catalog/prioritized plan first**, not hook
implementation, and confirmed this effort slug.
Later clarification replaces the initial update-excluded creation request:
use an all-inclusive creation span and separate phase/update child spans, with
no special subtraction-based metric.

The [inception record](inception-transcript.md) preserves the operator's words;
private repository/product/organization references are redacted rather than
published. Agent-recommended additions are labeled in the catalog and plan.

## Plan

Sequencing, artifact layout and the validation methodology below are
_(agent-recommended)_ within the operator-selected design-only scope. The Request
and inception record distinguish the operator's measurements and clarifications.

### Phase 1 - Evidence and contract

- [x] Find prior art, retain existing campaign/producer ownership and inspect
  source boundaries plus public performance/reliability complaints.
- [x] Define QoS/Engagement shapes, OTel mapping, result classification,
  timing/denominators, privacy, sampling and incomplete-observation semantics.
- [x] Draft a machine-readable proposal schema without enabling a runtime.

### Phase 2 - Catalog and future plan

- [x] Cover Manager, Worktrees/hooks, Bridge, Dispatch and session measurements
  with stable names and meaningful success boundaries.
- [x] Separate requested measurements from agent-recommended extra coverage;
  document native host/admission/accounting dependencies and source gaps.
- [x] Draft issue-backed priority, owner and validation sequencing without
  authorizing edits to reserved production sites.
- [x] Validate schema positive/negative cases, links, structure and coverage of
  the full request; pass the repository's changed-document gates.

### Phase 3 - Review and delivery

- [ ] Publish the design through review and required CI, addressing findings.
- [ ] Verify actual merge, record delivered artifacts and conclude/archive this
  design-only sub-effort without closing broader implementation acceptance.

## Validation Plan

- [x] Match the catalog against all six request groups, including every subitem.
- [x] Validate all three record kinds and reject incomplete/invalid terminal
  shapes, wrong units/examples, bad IDs and arbitrary top-level fields.
- [x] Check the ExpectedFailure mapping and context-dependent result examples
  against the cited OTel specifications; distinguish stable/development guidance.
- [x] Check source links and evidence limitations, issue ownership and public
  content; do not claim model admission, native credits or paint from proxies.
- [ ] Verify no runtime/plugin/configuration/SDK dependency changes, then pass
  applicable document/schema/effort gates and confirm review/CI/merge.

## Proposal

- [QoS and Engagement contract](../../../docs/patterns/telemetry-qos-engagement.md)
- [Proposed JSON Schema](../../../schemas/telemetry-qos-engagement.schema.json)
- [Event catalog](../../../docs/telemetry-event-catalog.md)
- [Future instrumentation plan](instrumentation-plan.md)

## Journal

### 2026-10-22 - Contract-first intake

- Confirmed the delivery scope and slug; preserved the full measurement request
  and the clarification to align with OTel.
- Found existing Performance Telemetry ownership and real optional trace export;
  retained its unresolved native/log/metric/rollout acceptance.
- Corrected the initial expectation mapping: a genuinely failed operation,
  even anticipated, maps to OTel Error with a custom result classification.
- Drafted the design artifacts. No hooks, live services or collection changed.
- Verified the later creation clarification: ordinary all-inclusive parent and
  phase/update children, with no subtraction-based metric.
- Passed schema validation: nine positive cases, 21 negative cases, two
  non-finite JSON controls and all 58 unique catalog names.
- Passed 22 new local links/anchors, request coverage, effort structure,
  documentation consistency, runbook references and whitespace checks.
- Schema validation used an ephemeral tool environment after the existing
  interpreter reported no `jsonschema`; no runtime dependency was added.
- Runtime/native/clean-room/export tests are not exercised by this design-only
  change. Their exact acceptance remains in the future instrumentation plan.
