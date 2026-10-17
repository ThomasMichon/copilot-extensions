# Dispatch Extension Catalog

- **Slug:** `dispatch-extension-catalog`
- **Repo:** copilot-extensions
- **Branch(es):** Amended planning PR, then authorized dependency-ordered PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active <!-- Implementation underway; activation and installed rollout remain separately authorized. -->
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

- [x] Re-read #6379, #4959 and #6283 and their current source before carving
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

- [x] Expose existing registrar/task-source facts through the same owning
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

### Bounded template contract for Phase 1 review

This section is **agent-recommended, proposed behavior**, not documentation of
an implemented provider format. It realizes the existing vision's
`discoverable-extension-templates` and `extend-any-declaration` intent.
It follows the patterns' independent-installation, source-isolation,
installation-provenance and fail-loud requirements. It adds neither a runtime
service nor a second template/configuration registry.

**Discovery declarations.** A provider supplies one explicit versioned JSON
index. The proposed homes are `dispatch-templates.json` at a verified plugin
payload root, `templates/index.json` within each existing repository
agent-dispatch configuration layer, and `templates/index.json` beneath the
current installation's registrar state root for user-local contributions.
These are inert homes outside active registrar declaration directories.
An index points at existing provider-owned fragments instead of copying their
configuration into a catalog. The initial shape is:

```json
{
  "schema": "agent-dispatch.template-index",
  "version": 1,
  "templates": [
    {
      "id": "record-source",
      "title": "Mapped record source",
      "description": "Create ordinary tasks through a configured source adapter.",
      "kind": "emitter",
      "declaration": "recipes/record-source.json",
      "requirements": ["Configure an adapter command and stable record identity."]
    }
  ]
}
```

The declaration path is relative to the index directory, with the verified
provider root as its containment boundary. Version 1 admits only JSON
objects/fragments, portable nonempty IDs, bounded text and a supported
registrar kind or `composite`. Unknown schema/version, duplicate IDs, invalid
values, unreadable files, and paths escaping that boundary produce attributable
findings. Do not execute provider commands, expand arbitrary directories or
interpret requirements prose as executable policy while browsing. Absence of
an optional index is normal; failure to establish its source is not.

**Roots and applicability.** Reuse one current activation report across catalog
sources and plugin templates. Enumerate every `ActivePlugin.live_roots` value
with its own root kind and applicability, not just the compatibility `root`.
Use current proven-active values; stale/unavailable activation evidence does
not authorize an additional root. Repository discovery reuses registered
repository identities and declared roots, including existing repo pointers,
without requiring an active registrar pointer merely to browse inert material.
An arbitrary directory pointer is not a canonical repository identity.
Canonical/legacy selection and installation marketplace overlays use
`repo_config.layered_repo_surface_dirs`; no new blanket local-wins rule.
Retain separate layer/root variants with their provenance instead of silently
choosing or merging them. Optional repository integration stays optional for
a standalone installation: report unavailable/partial discovery, never install
a sibling or return a falsely complete empty catalog.

**Identity and revision.** Preserve all existing built-in `template:<key>` IDs,
their revisions and `global:` recipe references, deriving them from
`POOL_TEMPLATE` and `GLOBAL_RECIPES`. New IDs qualify provider identity,
installation/source scope, root/layer variant and the provider's local ID.
Names alone cannot collide with or replace a built-in. A revision detects
changes to the index entry, declaration and resolution context; it is neither
an activation receipt nor a running-state assertion. Equal file bytes at
different dependency roots do not establish equivalent templates. Discovery
exposes requirements, provenance, applicability, revision, resolution state
and supported actions without forwarding executable specs or environment values
as ordinary component metadata.

**Discovery before instantiation.** The first implementation slice is
read-only discovery. New provider rows must have `instantiable: false`, a
specific unsupported reason and no Instantiate/Save action. Existing built-in
actions stay intact: `save_instance` currently accepts built-in template IDs
and rejects unmaterialized `extends`. Reporting an available fragment must not
pretend that this command already accepts a new qualified template ID.
Clients use owner-supplied capability facts rather than guessing from `kind`.

The subsequent instantiation slice extends that same save lifecycle. It must
re-resolve the selected qualified source/revision before persistence and
materialize through the existing recipe parameter/deep-merge authority, not
introduce another substitution language or UI-only form engine. Preserve the
current advanced declaration and repository-root fields. Unsupported or
ambiguous dependencies remain non-instantiable until proven safe. Relative
resource meaning must survive materialization: existing inherited
`emitter.spec.cwd` handling alone does not prove reviewer command paths or
repository-derived worker identity. Resolve those cases with #4959's owner,
or refuse the affected action explicitly; never rebase them silently onto the
user-local destination.

Instances retain immutable source provenance, stable IDs, optimistic edit
revisions, equivalent-request retries, collision/dependency validation,
Save-only, explicit publication consent and partial-failure receipts.
Provider updates never rewrite existing drafts or active snapshots
automatically. Activation remains pending until the existing supervisor's
independent observation; no new producer, supervisor or authentication store.

**Finding/action scope.** Add template-discovery completeness and attributable
findings to the owning catalog without changing registrar authority.
Inert-template findings disable the affected new template action, not an
unrelated existing source toggle or local-instance edit. Existing registrar,
stored-draft, publication and component-metadata failures retain their current
mutation refusals. Do not append unrelated inert findings to a shared error
collection that currently disables every toggle, or relax existing safeguards
to accommodate the new catalog rows.

**Implementation acceptance.** The discovery slice must use isolated builtin,
enabled/disabled multi-root plugin, repository-layer and user-local fixtures.
Compare identities, applicability, revisions and explicit partial/error states;
include same-byte/different-root templates, duplicate entries, stale roots,
unsupported versions, path escapes and a standalone installation without
repository integration. Through the real catalog CLI and existing Manager
contribution, prove that healthy rows survive, unsupported rows offer no
mutation, unrelated controls retain their original authority, and browsing
leaves source/state bytes unchanged with no queue, service or provider command.
Keep this a focused path-gated contract suite, not a required exhaustive matrix.
Instantiation requires separate first-touch/save/edit/publication/retry and
provenance-preservation coverage through the real CLI. Record platform and live
venue limitations explicitly; source merge is not promotion or installed proof.

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

### 2026-10-16 - Reviewed plan and first shared catalog projection

- Plan #7095 merged after required CI passed. The owner-authored review
  returned Comment with one Low role-table wording suggestion; the imperative
  assignments were retained with an explanation and the thread resolved.
- The first implementation slice extends the existing catalog rather than
  creating another authority: version/read time, concrete component kinds,
  IDs, provenance and content revisions, plus a distinct component basis.
  Published snapshots remain authoritative when a Save-only draft changes.
- Component metadata excludes executable specs, command argv and environment
  values. Unknown or unreadable publications expose no inferred components.
  The existing terminal contribution gains Kind and readable component details.
- Final contained worker-instance suite passed 57 tests, including direct
  composite/publication checks and real-CLI first-touch projection assertions.
  Changed Python lint and the install-contract guard passed. No source/template
  activation, installed update or delivery claim is made.

### 2026-10-17 - Catalog review and terminal compatibility correction

- Current-head review returned Comment. Scope/container and label-order
  suggestions were checked against the actual registrar: lane scope is a
  validated string and labels are an ordered tuple. A new regression preserves
  both supported contracts and rejects unsupported scope containers.
- The additional component conversion is a bounded, in-memory projection,
  without discovery, processes or network work; no cache or state/override
  refactor was introduced without a demonstrated performance problem.
- Required CI found that the secondary Kind column displaced Description at
  standard width. Preserve all six existing columns at 118 columns and show
  all seven at 150, including space for the existing dropped-column indicator.
- The contained worker-instance suite passed 58 tests. The directly affected
  Configuration backstage file passed 25 tests with one platform-specific
  skip on Windows; changed Python lint passed. Updated-head review and
  required CI remain prerequisites to merge.

### 2026-10-17 - Supported routing shapes and observed file provenance

- Balanced current-head review identified two real projection gaps after the
  earlier speculative findings were dispositioned: non-lane scope/labels
  were stored in kind-specific configurations, and ordinary file-backed
  declarations lost their known source path before catalog projection.
- Normalize evaluator, bare/wrapped schedule and composed-emitter routing.
  Add ordered `repo_scopes` without changing the legacy first-scope string;
  retain lane label order and include every schedule entry/default.
- Attribute the read leaf document in discovery and the known draft/publication
  path in local configuration projection. Observational path attribution does
  not alter the executable registration; a regression compares that complete
  registration before and after attribution.
- Both clients can read richer component prose through the existing card:
  identity, all scopes, labels, source document and content revision. The
  content hash still tracks full desired registration changes without
  returning raw executable fields.
- Extend focused coverage for every routing shape, ordinary verified-plugin
  files and local draft/publication files. Keep review, aggregate CI, normal
  release and separately authorized installed proof as open delivery gates.
- The final contained catalog/instance, discovery and registry selection passed
  170 tests. Changed Python lint, the 13-plugin install contract and all
  non-widened source-module ceilings passed. No service, source activation,
  installed update or live external process was used as a success claim.

### 2026-10-17 - Rejected schedule regression and independent CI blocker

- Updated-head review suggested a missing `replace` import; the existing
  `from dataclasses import dataclass, replace` and file-backed discovery tests
  disprove that finding. No duplicate import was added.
- The suggested empty schedule shape is also rejected by the owning registrar:
  an explicitly supplied schedules collection must be a non-empty object list.
  Added a regression through real source discovery proving empty or malformed
  containers remain visible errors, never fabricated routing metadata, while
  healthy templates and original source bytes remain intact. Activation and
  schedule validation behavior are unchanged.
- The contained catalog/instance, discovery and registry selection passed
  173 tests after correcting the test's initial unsupported fixture assumption.
- Required guards encountered the separately tracked native POSIX PowerShell
  timeout in #7020 (run 39026318754, job 117377895225). Recorded the exact
  recurrence and native-venue limitation there. That issue is not an accepted
  flake or waiver: no timeout increase, test skip, forced retry or CI bypass.
  Normal updated-head checks remain an independent merge gate, not proof that
  the native timeout was diagnosed or fixed.

### 2026-10-17 - Projection isolation and CI-repair routing

- A fresh balanced review found a real boundary gap: schedule validation
  accepts some shapes whose full routing cannot be projected. Isolate that
  projection failure to the affected source/draft/publication row, retain
  healthy catalog entries, and expose both row and summary errors. Unknown
  component metadata never substitutes for independent enablement/activity.
- Disable toggle affordances consistently after all rows are known, including
  healthy rows preceding a later projection error. No registrar validation,
  activation, publication, worker or task lifecycle behavior is changed.
- Reuse a producer-owned CI-repair routing helper for both actual admitted
  task creation and catalog metadata. Cover explicit lane and repository
  fallback against a real isolated queue task; no inferred task labels.
- Add malformed second-schedule and label-container cases through source,
  draft and published catalog rows, retaining original bytes. Extend the real
  CLI first-touch journey to assert successful degraded JSON with healthy
  instances/templates rather than an aborted read.
- The coupled catalog and CI-repair selection passed 128 tests, including the
  real-CLI degraded-read assertion. Changed Python lint and the 13-plugin
  install contract passed. Current-head review and
  aggregate CI remain required; installed rollout and live source effects
  are not part of these isolated fixtures.

### 2026-10-17 - Nullable bindings and attribution-aware compatibility

- Follow the producer's non-null binding selection in catalog routing and
  dependency checks. A null CI-repair binding is absent; a null issue-loop
  binding must not mask an actual effort-driver binding or crash a direct
  command emitter's dependency projection. Only emitters select those bindings.
- Add valid source/catalog cases for all three emitter modes with null
  alternatives, preserving scopes, labels, healthy controls, source bytes
  and executable registrations. Non-emitter prerequisites retain their own spec.
- Required CI exposed two compatibility assertions comparing file-backed
  declarations to a different file or a pure expansion. Update only their
  expected leaf-source attribution and additionally assert complete canonical
  registration equivalence. Do not remove attribution or change dataclass
  equality to make these tests pass.
- The catalog, CI-repair, recipe and reviewer-adoption selection passed
  234 tests. The final catalog selection, including the non-emitter dependency
  regression with its required evaluator reference, passed 86 tests; changed
  Python lint passed. This is a real source-attribution coupling, not a
  pre-existing failure or waiver.
  Fresh-head review and the complete required CI gate remain open.

### 2026-10-17 - Shared catalog source merged; template contract next

- #7128 merged to `dev` at 13:45Z as
  `f2e3cf6027d4698d72dab9ba60f51008888000c7`. The exact final head
  `eebfaed91872e21d6776581feb80b2f26c8ae4a2` had a current Copilot
  approving verdict with no open issues and complete successful normal CI,
  including `PR gate (required check)`. The aggregate's full name matters:
  a filter for only `PR gate` incorrectly omits that completed check.
- Source delivery includes versioned catalog/component metadata, ordered
  routing scopes and labels, observed leaf provenance, publication/draft
  separation, per-row metadata error isolation, null-compatible producer
  selection and economical terminal details. No new registrar or workflow
  engine was introduced.
- The normal final-head guards passed independently after the substantive
  fixes. That does not diagnose or close the earlier native PowerShell
  timeout in #7020. No check bypass, forced retry, timeout increase or
  skipped regression was used.
- Re-read this effort after merge. Existing instance lifecycle and source
  projection are foundations, not completion of provider template discovery,
  instantiation, status/history/steering parity or mapped-record production.
  Normal promotion and authorized installed proof remain open; no updater,
  source activation or fleet cutover was run.
- Preserve the existing configuration/composition/remote owners. Next stretch:
  review the bounded inert-template discovery/instantiation contract using
  verified provider roots and existing repository layers, retaining legacy
  template identities and truthful unsupported/ambiguous states. Atomic
  steering acceptance is separately tracked in #7141.

### 2026-10-17 - Inert template discovery contract proposed

- Added the bounded contract above for review before implementing provider
  discovery: explicit inert indexes, verified multi-root/layer provenance,
  qualified identity/revision, truthful unsupported actions and scoped errors.
  Built-in IDs and the current instance lifecycle remain the compatibility floor.
- Split read-only discovery from provenance-safe instantiation. Current save
  support is deliberately not advertised for external fragments; inheritance
  and relative-path gaps stay with #4959 rather than being hidden by a copy.
- Reconciled with the existing dispatch vision and architecture patterns.
  The contract names isolated real-CLI/Manager acceptance obligations, including
  missing optional repository integration and unchanged unrelated controls.
  No provider format, source registration, service or installed payload changed.
- Both the parent effort and its remaining validation/rollout gates stay open.
