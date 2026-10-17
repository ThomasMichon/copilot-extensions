# Dispatch Extension Catalog

- **Slug:** `dispatch-extension-catalog`
- **Repo:** copilot-extensions
- **Branch(es):** Amended planning PR, then authorized dependency-ordered PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active <!-- Execution authorized; implementation waits for the amended plan's review and merge. -->
- **Vision:** [agent-dispatch](../../../visions/plugins/agent-dispatch/README.md):
  discoverable-extension-templates, extend-any-declaration,
  task-or-charter-owns-the-workflow
- **Umbrella issue:** #7091
- **Sub-issues:** Carve bounded deltas after contract review and ownership reconciliation

## Guiding Intent

Make reusable emitter, evaluator and worker-pool templates easy to discover,
understand and instantiate through their owning configuration authority.
Consumers should inject their own execution procedures without teaching
dispatch a domain's workflow or sub-stage model. The task or selected worker
charter owns that logic; dispatch retains allocation, lifecycle and review.

The operator authorized driving convergence on 2026-10-16. Implementation
starts after the amended proposal clears review and merges. Production
activation, fleet cutover and live external-source effects remain separately
authorized operations.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning coordinator | Own contract reconciliation, implementation sequencing, integration and completion | Managed worktree; issue #7091 |
| Catalog implementer, unassigned | Own only separately authorized template discovery and configuration contract slices | Dedicated managed worktree after assignment |
| Consumer integrator, unassigned | Prove the generic external-process task contract without adding domain semantics | Dedicated managed worktree after assignment |

## Coordination

- **Topology:** Amended plan review first, then dependency-ordered
  implementation PRs. Concurrent workers must have disjoint slices.
- **Host (owns PRs):** Planning coordinator owns execution and integration
  following the operator's 2026-10-16 authorization.
- **Delegates:** None assigned or launched.
- **Handoff:** Use this canonical effort and issue #7091 for public-safe
  contracts, evidence and current ownership. No downstream-private references.
- Configuration Backstage and Worker Instances (#6379) retains its existing
  catalog/local-instance implementation and outstanding release/installed-proof
  work. This effort extends that foundation, not its ownership.
- Recipe Composability (#4959) retains inheritance/resource-resolution work;
  remote worker configuration remains #6283. No competing implementations.

## Context

The existing worker catalog already distinguishes templates, source declarations
and local instances. Draft/published snapshots, revision checks, explicit
activation consent, registrar discovery and supervisor reconciliation are
foundations to reuse, not replacement targets.

Template enumeration currently starts from built-in mappings. The new delta is
discoverable provider-owned template material and a reusable consumer contract,
not another queue, instance store or workflow engine.

Sources:

- Vision alignment merged in #7084; its vision-only issue #7082 is not this
  implementation campaign's umbrella.
- [Configuration Backstage and Worker Instances](../configuration-backstage-and-worker-instances/README.md).
- [Recipe Composability](../agent-dispatch-recipe-composability/README.md).
- [Worker instance lifecycle](../../../plugins/agent-dispatch/docs/worker-instances.md).
- `plugins/agent-dispatch/src/agent_dispatch/workers_catalog.py`,
  `worker_instances.py`, `registrar_recipes.py` and `registrar_discovery.py`.

## Request

Operator request, verbatim:

> Great. Draft efforts to drive toward convergence

Settled operator boundary, verbatim fragment:

> "the task or charter owns the workflow and sub-stage logic"

Operator confirmed the name `dispatch-extension-catalog` and the split between
this generic upstream effort and consumer-owned integration. Consumer-specific
identifiers are intentionally excluded. The standing vision carries the shared
intent; the implementation sequence below is a proposal.

Subsequent operator direction, public-safe summary:

The operator authorized taking the plans through review and driving
implementation. Independent graphical and terminal clients should have equal
semantic fidelity for existing task sources/registrations, task status and
steering/input while retaining their different presentation styles. A reusable
mapped-record source extension should support task production from external
tabular lists; authenticated service access remains with the source adapter.
Consumer-specific names and service details are excluded from this public plan.

## Plan

The phase structure, contract details and validation design below are
**agent-recommended**. They do not grant execution or rollout authority.
The operator's subsequent execution authorization is recorded above; the
implementation and validation choices remain proposed means.

### Phase 1 - Reconcile owners and review the delta

- [ ] Re-read #6379, #4959 and #6283 and their current source before carving
  issues; retain existing owners and exclude already delivered behavior.
- [ ] Inventory built-in template mappings, provider declarations, setup
  examples and contributed catalogs; classify reusable templates separately
  from concrete desired registrations.
- [ ] Review a bounded specification for template discovery, instantiation and
  consumer injection points against the existing catalog and instance lifecycle.
- [x] Record operator execution authorization and the Planning coordinator's
  integration ownership; no production activation is implied.
- [ ] Review a shared semantic parity matrix for source identity/provenance,
  registration state, task status/category/history and steering/input
  schemas, acknowledgements and failure states across independent clients.

### Phase 2 - Owner-declared template discovery

- [ ] Define consistent, documented template homes and provider declaration
  metadata without putting inert templates into active registrar discovery.
- [ ] Extend the owning catalog to enumerate builtin, enabled-plugin,
  repository and supported local contributions with qualified identity,
  revision, provenance, input requirements and explicit invalid/ambiguous states.
- [ ] Preserve current activation checks, source isolation, inheritance and
  precedence; explain selected, shadowed or rejected sources without inventing
  a blanket local-wins rule.
- [ ] Migrate built-in enumeration without a second manually maintained
  template authority; preserve existing template and recipe references.

### Phase 3 - Shared configuration and extension contracts

- [ ] Expose sufficient machine-readable inputs and prerequisites for clients
  to instantiate offered emitter, evaluator and pool templates; retain an
  advanced declaration path where appropriate.
- [ ] Reuse stable local-instance identity, optimistic revisions, Save-only,
  publication, activation consent and partial-failure receipts.
- [ ] Specify a process-neutral task/charter contract for selecting an execution
  role, acquiring external instructions and reporting progress/results under
  existing completion and review rules.
- [ ] Prove current producer/evaluator seams first. Add only evidenced missing
  injection points; retain direct manual tasks without a required emitter.
- [ ] Keep external workflow graphs, stages, definition catalogs and domain
  run history with their consumer; use only process-neutral references where
  cross-owner correlation is required.

### Phase 4 - Compatibility, documentation and adoption

- [ ] Exercise a generic external-process runner fixture through both manual
  task creation and a declared emitter/evaluator/pool composition.
- [ ] Publish one task-oriented discovery and configuration guide, with exact
  paths, formats, commands and migration steps in owner documentation.
- [ ] Preserve legacy references and existing active registration identities;
  make incompatibility explicit rather than silently ignoring unknown contracts.

### Phase 5 - Mapped sources and client semantic parity

- [ ] Expose existing registrar/task-source facts through the same owning
  projection consumed by graphical and terminal clients; avoid a new registry
  or a UI-owned state machine.
- [ ] Establish a common acceptance matrix for source applicability,
  declared/enabled/observed state, task lifecycle/category/history, structured
  questions, steering delivery and errors. Presentation may differ; facts
  and supported operations must not silently disappear.
- [ ] Reuse the existing command-emitter task-output contract for mapped
  external records. Extend only demonstrated gaps in stable record identity,
  column/field mapping, deduplication and acceptance receipts.
- [ ] Keep authentication, external-service protocols and source-specific
  approvals in consumer adapters, not the generic dispatch package.
- [ ] Validate the generic record-source contract and shared client semantics
  with isolated providers before any separately authorized live adoption.
- [ ] Clear current-head review and required CI, confirm normal release, and
  verify an explicitly authorized installed consumer before declaring delivery.
- [ ] Reconcile every Plan and Validation Plan item or transfer it to a named
  tracked objective before marking this implementation effort Done.

## Validation Plan

All items below are **agent-recommended acceptance checks** for execution.

- [ ] Enumerate built-in, enabled/disabled plugin, repository and local fixtures;
  verify qualified identities, stable revisions, source applicability,
  inheritance and precedence against registrar authority.
- [ ] Reject conflicting identities, stale roots, unavailable dependencies and
  malformed contributions visibly while retaining healthy browse results;
  ambiguous discovery must not authorize mutation.
- [ ] Prove browsing and Save-only create no registrations, tasks or agents.
  Explicit activation preserves stable identity, retry safety, unchanged
  source bytes and truthful persistence-versus-publication outcomes.
- [ ] Exercise a real catalog-to-instance CLI/subprocess boundary, stale edits,
  failed publication and restart recovery, not only mocked helper functions.
- [ ] Run one generic process through manual and emitted tasks. Verify worker
  instructions, progress, suspension/recovery, result and independent review
  remain ordinary task behavior without adding domain stage states.
- [ ] Preserve existing recipe references and active registrations during
  migration; template refresh must not silently activate or retarget work.
- [ ] Exercise the changed code's unit tests and consumer-boundary integration;
  use clean-room first-touch coverage for new adoption behavior and native
  Windows/Linux coverage where applicable. Record a reason for any unavailable
  live external-venue lane rather than treating it as passed.
- [ ] Verify promoted source and the authorized installed consumer separately;
  no merge-only deployment claim. Preserve other efforts' outstanding rollout.
- [ ] Feed identical owner-backed source/task/steering fixtures to independent
  graphical and terminal consumers. Assert semantic parity, including structured
  inputs and failure/unknown states, without requiring matching layouts.
- [ ] Exercise mapped-record emission with missing/renamed columns, stable
  source identity, paging, repeated polls and lost responses. Prove malformed
  records remain visible and retries do not duplicate accepted work.

## Proposal

Extend the current catalog and configuration lifecycle with owner-declared,
discoverable templates and sufficient reusable injection contracts. Do not
create a new registrar, template registry with duplicate truth, or consumer
workflow engine. Exact schemas and paths remain subject to Phase 1 review.

Implementation is authorized after amended-plan review and merge. External
adapter specifics stay with their owner. Production migration, deployment and
automation activation still require their applicable operational authorization.

## Journal

### 2026-10-16 - Draft prepared

- Confirmed compatible effort adoption and the operator-selected effort name.
- Separated the generic delta from consumer integration and retained existing
  configuration, recipe-composition and remote-configuration owners.
- Vision prerequisite #7084 merged. Created issue #7091 for this bounded draft.
- Captured the request and task/charter boundary; marked proposed implementation
  and validation details agent-recommended. No execution slices assigned.

### 2026-10-16 - Execution authorized and parity scope clarified

- Operator authorized taking the plans through review and driving convergence.
- Added existing source/registration visibility, equal graphical/terminal task
  semantics and a generic mapped-record source extension to the acceptance scope.
- Retained process-neutral dispatch ownership and consumer-owned authentication.
  Implementation remains behind the amended plan's review/merge gate.
