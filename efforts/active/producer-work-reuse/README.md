# Producer Work Reuse

- **Slug:** `producer-work-reuse`
- **Repo:** copilot-extensions
- **Branch(es):** independent implementation PRs after this proposal
- **Created:** 2026-10-22
- **Status:** Draft
- **Vision:** Bridge `unknown-observation-is-not-a-verdict`; Dispatch `discover-and-live-reconcile` and `overrides-take-precedence`; CodeSpaces `single-transport-reach` and `telemetry-grade-session-capture`
- **Sub-issues:** #7948 · #7949 · #7950 · retained declaration/health slice of #2110

## Guiding Intent

Remove repeated producer work, not required observations. Reuse evidence only
within its source, authority, configuration and lifetime generation. Keep
missing, partial and stale evidence distinct from authoritative emptiness.
An unchanged observation timestamp is not a performance improvement.

The [S3 inventory-freshness amendment proposal](inventory-freshness-plan.md)
adds durable all-venue inventory, push-assisted bounded staleness and
representative one-session-per-venue profiles. It is a design/review gate, not
cache/push implementation admission or a change to credential safety.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan author | Bounded source investigation and proposal publication | This effort and issue threads |
| Independent plan reviewer | Check source fit, invariants and measurable acceptance | Plan PR |
| Future slice implementers | Separately admitted work, not allocated by this proposal | The corresponding issue's claim protocol |

## Coordination

- **Topology:** one plan-only PR, then independent per-slice PRs.
- **Host (owns PRs):** plan author owns proposal publication only.
- **Delegates:** no implementation worker is launched by this proposal.
- **Handoff:** the issue receiving each slice retains implementation and
  qualification. A merged plan does not complete those issues.
- **Exclusive edit scope now:** this folder and its index entry. No runtime,
  plugin manifest, installer, service, telemetry adapter or deployed state edits.
- **Implementation boundaries:** [measurement and ownership](validation.md).
  Recheck issue/PR claims immediately before each implementation, not just here.

## Context

Source inspection is pinned to
`e53d8d24e00fc6a4ba82003273398ab680b44347`. Line references in the linked
plans describe that revision; symbols, not line numbers, are the edit contract.

The issue bodies establish this mapping:

| Tracker | Exact retained scope | Design |
|---------|----------------------|--------|
| #7948 | Bridge idle namespace-provider rescans; not consumer roster SSE | [Bridge](bridge.md) |
| #7949 | CodeSpaces account inventory and mux/transport fanout | [CodeSpaces owner wave](codespaces.md) |
| #7950 | Transcript discovery/history-times-offset work | [Transcript index](transcripts.md) |
| #2110 | Registrar declaration reuse and companion health cadence, plus its unchanged aggregate CPU and separately inherited event-latency gates | [Dispatch](dispatch.md) |

#2110's [audit comment](https://github.com/ThomasMichon/copilot-extensions/issues/2110#issuecomment-6350746955)
explicitly retains both declaration and health opportunities. Its body and
earlier reconciliation comments retain the original six-lane Windows,
normal-thirty-second-cadence aggregate CPU target and #2112's independent
production-path latency target. This is not a reopening of the completed
carrier foundation or reassignment of any existing broader owner.

The proposal closes implementation gaps beneath the named visions; it does not
extend their intent. It follows
[performance measurement](../../../docs/patterns/performance-measurement.md),
[registry hygiene](../../../docs/patterns/README.md) and existing
[daemon cutover](../../../docs/patterns/graceful-daemon-cutover.md).
No new resident service, global registry, replay owner or telemetry framework is
proposed.

## Request

**Later direction — public-safe summary, not a quotation:** retain last-known
inventory for all active and inactive/stopped CodeSpaces, with per-source
freshness and per-venue observations; prefer notifications on existing owned
SSH transports and explicit stale/out-of-band reconciliation. Characterize a
configurable two-account, 0/1/4/8-active, one-ACP-or-Mux-session profile, keeping
historical multi-mux stress evidence intact. No universal runtime cap, private
quota premise, new remote stack, credential cache or source-ownership transfer
is implied. The [amendment](inventory-freshness-plan.md) demarcates proposed
storage/policy details and decisions still required before functional work.

Public-safe capture of the requested scope, with private coordination specifics
removed: produce concrete, code-grounded implementation plans for Bridge
namespace caching, Dispatch declarations/health batching, and CodeSpaces
inventory/mux/transcript work; retain #2110 only where its history proves the
same requirement. Preserve freshness, namespace/source/configuration generation,
lifecycle ownership and missing-service truth. Do not obtain gains by merely
aging caches or suppressing health. Do not reparse full transcript history per
poll; handle append, truncation, replacement and partial records. Use fixed
workloads and retain before/after call-count, CPU, network and storage evidence.
Publish a reviewed plan only; do not implement, deploy, inspect existing agents
or run remote probes as part of planning.

The algorithms, proposed APIs, synthetic workloads and staged boundaries below
are **agent-recommended implementation proposals**, not measurements or newly
authorized runtime behavior.

## Plan

### S3 inventory freshness amendment — plan/review only

- [x] Trace current inventory, eligibility storage, availability, transport and
  ACP ownership seams; retain narrow candidate API authorization separately.
- [x] Characterize revised Mux profiles without changing functional algorithms;
  preserve original raw stress evidence and mark ACP/full mixed cost unavailable.
- [ ] Review the [amendment](inventory-freshness-plan.md), record storage/grace/
  action-publication decisions, and obtain exact source-owner seam admission.
- [ ] Implement only after those gates; qualify real cache-hit/push/due/stale/
  disconnected cases and independent ACP/Mux routes before source completion.

### Phase 1 — Source-grounded proposal

- [x] Read the four exact issues and current direct producer/consumer/test paths.
- [x] Correct the issue-to-area mapping without manufacturing another tracker.
- [x] Specify independent slices, invalidations and ownership seams in the linked
  designs; retain counts as targets, not observed improvements.

### Phase 2 — Review and admission

- [x] Independently review the plan and resolve material design findings.
- [ ] Land the plan-only PR through normal review and required CI.
- [ ] Transfer execution to the existing issue claims after explicit admission;
  no functional work starts under this plan-author episode.

### Staged execution contract — not authorized by plan publication

1. **S1 / #7948:** namespace policy and source-generation evidence, then producer
   scheduling. Retain legacy-provider behavior until real change proof exists.
2. **S2 / #2110:** declaration parse cache, then separately reviewed health
   scheduling/schema. No change to registrar reconciliation cadence.
3. **S3 / #7949:** completeness-qualified inventory and one mux batch per venue;
   transport reuse is a later slice, not a prerequisite for inventory savings.
4. **S4 / #7950:** linear lookup first, then durable incremental catalog/cursors.
   Coordinate only the borrowed transport seam with S3.
5. **S5 / respective issues:** identical-workload native qualification followed
   by separate source, release and authorized installed receipts.

S1, S2, S3 and S4 can begin independently once admitted and their precise file
claims are clear. S3 transport reuse and S4 `_read` changes must be sequenced;
neither may create a competing connection owner.

## Validation Plan

- [x] Preserve all issue acceptance, including #2110's two separate metrics.
- [x] Name real consumer-boundary tests and deterministic work-count regressions.
- [x] Record source/release/installed evidence cells and unavailable semantics.
- [x] Validate documentation links and repository documentation consistency.
- [ ] Obtain a clean independent plan review and passing publication gates.
- [x] Deferred to `#7948`: Bridge native cost and membership-latency qualification.
- [x] Deferred to `#2110`: aggregate normal-cadence CPU and actual event-path p95.
- [x] Deferred to `#7949`: owner-wave connection cleanup and no-wake qualification.
- [x] Deferred to `#7950`: real-shell large-history and crash/debt qualification.

## Proposal

Approve the implementation contracts, not a claimed performance result.
Start with S3 inventory/mux batching: it has the clearest bounded 30-to-3
listing-call target, an existing injectable provider boundary and no dependency
on changing a cross-plugin revision contract. S1 requires provider-capability
admission; S2 health requires schema/lifecycle coordination.

## Journal

### 2026-10-23 — Explicit S3 authority amendment

- The user selected [selected admission followed by full publication
  validation](selected-authority-contract.md), accepting that an unrelated
  candidate change may allow an observation/connection wake before rejection.
- This amends S3a/b's authority scope, not selected authentication, ownership,
  no-wake/age, cancellation or performance gates. Existing #5761 ownership is
  retained. Draft #8143 remains unqualified; no runtime or rollout completion
  follows from this contract review.

### 2026-10-22 — Plan-only investigation

- Verified issue bodies and comments; no conflicting issue claims or matching
  open PRs were visible at reservation time.
- Examined only the named producers and their immediate consumers/tests.
- Found preserved foundations worth retaining: Bridge single-flight and fencing;
  Dispatch tier-specific uncertainty; CodeSpaces unknown/no-wake and owner
  cleanup; transcript whole-line continuation and durable push debt.
- Source performance measurements, native workloads, release and installed
  qualification were not run. This is a proposal, not a benchmark receipt.
- Independent read-only plan review found no significant issues. Documentation
  consistency, relative-link/privacy checks and whitespace validation passed.
