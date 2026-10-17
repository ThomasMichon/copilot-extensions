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
- [x] Review a bounded specification for template discovery, instantiation and
  consumer injection points against the existing catalog and instance lifecycle.
- [x] Record operator execution authorization and the Planning coordinator's
  integration ownership; no production activation is implied.
- [ ] Review a shared semantic parity matrix for source identity/provenance,
  registration state, task status/category/history and steering/input
  schemas, acknowledgements and failure states across independent clients.

### Phase 2 - Owner-declared template discovery

- [x] Define consistent, documented template homes and provider declaration
  metadata without putting inert templates into active registrar discovery.
- [x] Extend the owning catalog to enumerate builtin, enabled-plugin,
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
- [x] Provide owner-atomic card/ownership steering acceptance, exact-request
  replay and distinct acceptance/consumption/original-wake receipts through
  the existing HTTP, SDK, CLI and native contributions.
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

### 2026-10-17 - Template contract reviewed; read-only discovery next

- #7143 merged at 15:05Z as
  `ce2a8dabaca37b21c28d924a551716fc6e415b22`. The exact reviewed head
  had a Copilot approving verdict with no open issues and successful complete
  normal CI, including `PR gate (required check)`. The parent issue stays open.
- Re-read the Plan and Validation Plan after merge. Marked only the bounded
  specification-review item complete; its proposed interfaces are not runtime
  capabilities yet. Next is isolated read-only provider discovery, with scoped
  completeness/findings and no new external-template mutation affordances.
- Existing builtin instantiation and registrar controls remain the
  compatibility floor. Instantiation, client parity, mapped production,
  promotion and authorized installed proof remain open.

### 2026-10-17 - Read-only provider discovery implemented for review

- Added explicit inert JSON index discovery for verified plugin live roots,
  existing repository layers and installation-local templates. Qualified
  identity/revision, applicability, declared requirements and unsupported-action
  reasons are projected without executable values or new registrations.
- Template completeness/findings remain separate from registrar mutation
  refusals. Error rows reuse the existing Discovery errors group so clients
  retain them while filtering; their scope is explicitly templates.
- Kept activation acquisition lazy and shared across registrar/template reads.
  An indeterminate template-only activation scan cannot introduce a new
  registrar error when there are no plugin registrar candidates.
- The contained catalog, instance, registrar and recipe selection passed
  279 tests. The final 108-test catalog/instance selection includes actual
  first-touch CLI output and the Manager manifest's action-capability contract,
  unchanged source/state bytes and builtin save/publication compatibility.
  Changed Python lint and the 13-plugin install contract passed.
- This implements discovery only. External fragments remain non-instantiable;
  no source activation, provider command, queue access or installed update was
  used. Native CI, normal promotion and authorized installed proof are
  independent gates; the earlier #7020 timeout remains tracked, not waived.

### 2026-10-17 - Discovery refusal and inventory authority corrections

- A regular-file check now precedes index/declaration opening: a FIFO cannot
  block catalog discovery before the byte limit applies. Native POSIX index
  and declaration FIFO cases exercise the real CLI; Windows retains a direct
  non-regular-file refusal case.
- Reuse repository-resource name validation instead of imposing a template
  ID's 80-character limit on repository names. Inert fragments still keep
  their own bounded local IDs.
- A tolerant repository listing cannot attest inventory completeness because
  its reader may omit malformed state. Add an explicit strict read-only
  inventory contract to the repository owner, preserving the default listing.
  Older inventory output leaves template discovery partial while individually
  registered-only roots remain usable. Corrupt/duplicate/non-regular strict
  inventory must fail before emitting an authority marker.
- Required CI caught a forbidden plugin-to-Manager import in the integration
  test. Move the consumer parser/action checks to the Manager-owned suite and
  keep the real dispatch CLI proof in dispatch's suite. The boundary guard is
  unchanged; independent installability does not depend on test imports.
- Both owners' relevant changefiles and operating docs are included. These
  corrections retain scoped findings and all existing activation controls.
- The final contained dispatch selection passed 284 tests with two explicit
  POSIX-only FIFO cases skipped on Windows. Strict inventory and existing
  repository operations passed 161 tests, including actual native CLI output;
  Manager's own compatibility/dependency selection passed three tests.
  Native POSIX FIFO execution and updated-head review/complete CI remain
  prerequisites rather than inferred Windows coverage.
- Strict inventory distinguishes a missing directory entry from a dangling
  registry symlink: only the former is authoritative absence. Entry `lstat`
  precedes target resolution, so an unavailable target produces an explicit
  error without an authority marker. Focused strict-inventory and native CLI
  checks join the existing path-gated guard lane.

### 2026-10-17 - Unavailable template layers remain visible

- Current-head review identified another authoritative-absence distinction:
  the existing repository-layer helper filters broken directory links before
  the template reader can inspect them. Add an explicit audit-only option
  that retains present unavailable entries without changing default registrar
  selection or hiding healthy canonical/fallback/marketplace layers.
- Template discovery now reports scoped invalid-index findings for those
  entries. A shadowed legacy layer remains shadowed, and genuinely missing
  optional layers remain healthy absence. Installation-local root, directory
  and index links use the same missing-versus-unavailable distinction.
- The contained template/instance/registrar/recipe selection passed 293 tests,
  with only the two native POSIX FIFO cases skipped on Windows. Nine new
  native filesystem cases cover broken repository/local entries, healthy
  peers, shadowed fallback selection and first-touch absence. No source bytes,
  registration, queue, service or deployed runtime changed.
- Updated-head review and complete required CI remain merge prerequisites;
  native POSIX execution, promotion and authorized installed proof are not
  inferred from the Windows result.

### 2026-10-17 - Missing children do not hide unavailable ancestors

- The next review found the same absence error one directory higher in both
  owners. A child `lstat` can report missing when an ancestor is a dangling
  link. Audit template candidates through their nearest existing ancestor;
  retain every affected layer as a scoped finding and healthy fallback rows.
  An unavailable shared ancestor may correctly affect both base and overlay.
- The strict repository reader independently verifies the nearest existing
  ancestor before reporting an absent registry. Broken links, link loops and
  non-directory ancestors refuse inventory authority; genuinely missing
  first-touch parents remain a complete empty inventory.
- Native Windows coverage passed 301 coupled dispatch tests and 31 registered
  repository tests. The latter includes actual strict CLI processes for absent,
  dangling, non-directory and loop ancestors. The two POSIX-only FIFO tests
  remain explicit Windows skips. Changed Python lint passed. The initial
  template expectation incorrectly anticipated one finding for a shared
  unavailable ancestor; corrected it to require both affected layers.
- Updated-head review and the full normal aggregate gate remain required.
  No retry, timeout increase, guard widening, installed update or live source
  effect is used to claim completion.

### 2026-10-17 - Filesystem probe failures are layer-scoped

- A further current-head review identified readable fallback loss when the
  default selector's canonical probe raises PermissionError before audit
  isolation runs. Audit mode now acquires each candidate independently;
  a verified existing canonical layer still shadows legacy, while an
  unavailable canonical layer remains a finding beside readable fallback
  evidence. Default registrar/execution selection is unchanged.
- Isolate each layer's directory probe as well as its index reader. Neither
  an unreadable base nor an unreadable overlay suppresses healthy peer rows.
  Browsable inert fragments do not establish effective registration precedence.
- Four fault-injected PermissionError cases retain real healthy filesystem
  reads, scoped findings and source bytes across configuration ancestors,
  canonical, legacy and marketplace probes. The coupled contained dispatch
  selection passed 305 tests with the two POSIX FIFO Windows skips explicit.
- Current-head review, complete native CI, promotion and authorized installed
  proof remain separate gates. No service or deployed runtime changed.

### 2026-10-17 - Approved source; complete-suite deadline correction

- Current head `b9157488775c237a4cfedbd85fdbf9e571beaabe` received a Copilot
  approving verdict with no unresolved issues. CI run 39052822914 then ran all
  nine contained dispatch groups successfully: 5,933 passing tests and 35
  explicit skips. The test step reported PASS at 17:26:17Z, but the job that
  began at 17:14:17Z hit its unchanged 12-minute limit during final reporting
  and cleanup. The required aggregate correctly refused that cancelled job.
- Reconciled the recurrence with existing issue #6052; no competing open
  correction exists. Distribute the same complete file inventory's existing
  25-file process groups across three independent required CI jobs. Retain
  every test, existing containment and all item/sub-suite/plugin/job budgets.
  Local unfiltered invocation and other plugins remain unchanged.
- Partition/runner contracts passed 69 tests with two native-platform skips,
  including exhaustive disjoint coverage, preserved actual runner groups,
  real helper CLI refusal/output and matrix/aggregate wiring. Native CI must
  still exercise the new jobs before merge. This is not a forced retry,
  accepted-flake exception, larger deadline or bypass.
- Unit and lightweight consumer-boundary evidence are isolated. A separate
  deployment clean room is inapplicable to CI-only scheduling; the next real
  required workflow run is the available external venue. No installed runtime,
  source activation or rollout changed.

### 2026-10-17 - Partition-aware promotion qualification

- Native CI run 39054907434 exercised all three dispatch partitions, but both
  platform promotion-contract lanes caught a coupled consumer still expecting
  the former single-plugin matrix. The same two inventory tests failed;
  this is caused by the scheduling change, not an accepted pre-existing failure.
- Qualification now statically reads the helper's literal partition map, requires
  every partition's actual job name and binds normalized workflow plus helper
  bytes into the qualification receipt. Candidate Python is never imported or
  executed to discover this inventory. Unknown matrix shapes, malformed or
  duplicated declarations, missing rows and failed/cancelled partitions refuse.
- Both platform selectors now include helper-only changes. Unit/consumer tests
  compare the real helper matrix with qualification's exact row names, exercise
  each partition's omission/failure/cancellation, and check source identity,
  LF/CRLF equivalence and non-execution. Fresh current-head review and complete
  native CI remain required; no deadline or coverage guard is weakened.
- Isolated qualification/partition/runner/materialization/workflow and receipt
  consumers passed 331 tests and 75 subtests, with eleven explicit native/Bash
  skips. The final matrix/selector contract passed fifteen tests; changed
  Python lint and the thirteen-plugin install contract passed. A separate
  deployment clean room remains inapplicable to CI-only scheduling; the fresh
  normal workflow is the required external proof.
- Current-head CI exposed the path-selector test reader's assumption of a
  single grep expression. It now reads both literal `-e` expressions and
  retains all existing positive/negative scope assertions, adding helper/test
  paths and rejecting their backup-file siblings on both platforms.
- Current-head review then identified scheduling-only changes could omit the
  smoke matrix entirely, skipping the focused completeness/runner contract.
  Move that suite into both existing required native runner lanes, extending
  their selectors for helper/test-only changes. Reuse existing dependency
  preparation, job budgets and aggregate dependencies; do not add a new job,
  dependency or advisory check. The workflow contract proves that independent
  route for helper, test, runner and workflow changes.

### 2026-10-17 - Read-only discovery merged; atomic steering next

- #7144 merged at 18:59Z as `f16efb9e863efaa24a822faa53e422af48b54f6a`.
  Exact head `ca9cbb33475fad0936e26ac71204c1a0f694b03f` received an
  approving Copilot review with no open issues. Normal CI run 39059175005
  completed successfully, including the full `PR gate (required check)`;
  every review thread was resolved before the actor-aware merge.
- Marked only documented template homes and owner-backed enumeration complete.
  Verified multi-root/plugin, repository-layer and local contributions retain
  qualified identity/revision, applicability, requirements and scoped partial/
  invalid states. New provider fragments remain non-instantiable and expose
  no executable values. Existing built-in actions and registrar controls retain
  their authority; no new registry, producer or service was introduced.
- The final native run exercised all three dispatch partitions successfully,
  including reporting/cleanup, in 4m22s, 4m55s and 3m29s. Both independent
  native runner lanes and Windows promotion qualification passed. The complete
  suite retains its test inventory, containment and unchanged deadlines; #6052's
  recurrence is corrected, not retried or bypassed. The separate #7020 native
  PowerShell signature remains unresolved and is not waived by this result.
- Re-read this effort's entire Plan and Validation Plan after merge. Broad
  instantiation, generic process/evaluator proof, mapped-source production,
  common source/actions/history/input semantics, promotion and authorized
  installed proof remain open. No merge-only deployment or live-effect claim.
- Next bounded stretch is #7141: atomic owner acceptance of stale-card steering,
  exact-request retries and request-specific acknowledgement/wake/consumption
  semantics through existing owner/client boundaries. Preserve #6379, #4959
  and #6283 ownership; provenance-safe external-template instantiation remains
  open rather than silently materializing unresolved relative resources.

### 2026-10-17 - Atomic steering acceptance implemented for review

- #7141 now has a bounded owner implementation. A card/ownership revision is
  projected through the existing task reads; its comparison runs inside the
  answer's `BEGIN IMMEDIATE` transaction before any steer, draft, status or
  wake mutation. Each card post has a fresh owner nonce, including identical
  replacements at the same timestamp. Heartbeats and drafts do not stale it.
- Bind retry identity to the complete canonical answer, sender, expected
  status/revision and wake intent/message. Exact committed replay precedes
  the stale-card check; changed or unverifiable legacy requests conflict.
  Store immutable acceptance and original wake identity on the existing
  steer row. Consumption and original wake delivery remain separate
  observations, not evidence inferred from a later task wake.
- Guarded HTTP and SDK calls fail explicitly against an older owner, including
  a downgrade after the capability probe; there is no legacy-write fallback.
  Native CLI exposes the precondition, caller request ID and receipt reads.
  Both Tasks and Maintenance forms forward the owner revision. Ordinary
  manual guidance stays compatible; no workflow states, authentication store,
  producer or second input registry were added.
- This realizes the Tasks-pane vision's immediate-acceptance/visible-delivery
  intent and follows the existing independent-installation, fail-loud and
  proportionate-cooperation patterns. No vision expansion or new service.
- Complete directly coupled owner/card/wake, HTTP/SDK and CLI suites passed
  492 tests with one explicit platform skip. Real concurrent writers prove
  one answer/audit/wake, and native ephemeral HTTP plus actual CLI processes
  prove first-touch read/submit/exact replay/receipt with reserved request-ID
  characters. Existing SQLite rows migrate without invented receipts.
  Changed Python lint, module ceilings and the thirteen-plugin install
  contract passed without widening checks.
- The final forty-test acceptance selection also proves an exact retry retains
  its original no-session/no-wake receipt after an owner session is bound;
  it cannot silently acquire a later wake or rewrite that acknowledgement.
- A separate fresh-machine installer clean room was not run: no installer,
  bootstrap or deployment path changed. Fresh queue/CLI and legacy-schema
  adoption were exercised in isolated state. Current-head review and complete
  native CI remain required external proof; normal release and authorized
  installed proof remain separate gates. Independent graphical adoption and
  the broader parity/production journeys remain open.

### 2026-10-17 - Acceptance-time assertions and historical native fixtures

- Current-head review identified four existing asynchronous HTTP assertions
  reading live wake status as though it were immutable acceptance. Assert the
  stored acceptance-time pending value instead, retaining every independent
  delivered/failed/nonblocking check. A controlled interleaving finishes the
  real queued wake before the route reads its receipt: acceptance stays pending,
  current delivery is delivered, and the answer remains unconsumed.
- Required CI run 39064748697 exposed a native consumer fixture deriving a
  historical schema-v1 template from today's canonical argv. The new revision
  argument changed those reconstructed bytes and correctly tripped its exact
  historical checksum. Freeze the historical steering argv; do not change the
  trusted checksum or production legacy-ownership guard.
- The actual Manager registry now also proves both attributed task forms expand
  the owner-revision argument, including fresh, historical and already-owned
  registry cases. Keep the tests in their owning distribution, not a forbidden
  dispatch-to-Manager import; include the Manager changefile.
- Targeted dispatch receipt/card coverage passed 94 tests. Native Windows
  Manager discovery, historical migration, ownership/refusal and form-expansion
  coverage passed twenty tests; the broader Manager portfolio remains selected
  by normal required CI. Updated-head review and full CI remain merge gates.

### 2026-10-17 - Atomic owner steering merged; graphical adoption next

- #7146 merged at 20:34Z as `30b262e83e8c131af015f282f754fd93e94d198b`.
  Exact head `943e20aea417ecdba012710a6795ae50d26cb2a0` received an
  approving Copilot review with no open issues. Normal CI run 39066375737
  succeeded, including the full `PR gate (required check)` and native Manager
  compatibility portfolio. Every review thread was resolved before merge.
- Marked only the bounded owner-level steering contract complete. Card/
  ownership CAS, whole-request replay, immutable acceptance, original wake
  identity and actual consumption now share existing queue/HTTP/client/CLI
  authority. Both native task contributions forward the owner revision;
  historical migration retains its exact existing trust guard.
- Re-read the full Plan and Validation Plan after merge. Independent graphical
  adoption still needs to send the owner revision, recover exact requests
  after a card changes, and distinguish durable acceptance from current
  delivery/consumption. A client preflight hash is not that transaction.
  Keep #7141 open until that cross-client acceptance is settled.
- Next bounded stretch is consumer adoption through the existing command
  transport, not a second input store or authentication path. Source actions/
  history, durable shared drafts, template instantiation, generic process proof
  and mapped-source production remain open. Normal release and authorized
  installed proof remain separate; no live task or runtime was changed.

### 2026-10-17 - Graphical acceptance adopted; terminal receipt gaps remain

- An independent graphical consumer now sends the owner-reported revision
  and stable exact-request identity through the existing command transport.
  Missing or replaced questions permit original receipt reads only, not
  stale writes. Acceptance, original wake state and actual consumption remain
  separate, with explicit older-owner refusal and no legacy-write fallback.
- Isolated real command/HTTP owner coverage proves response loss after commit,
  disappeared-question recovery without another write, exact replay after
  consumption/card replacement, original wake identity and transactional race
  refusal. Fresh state and legacy capability refusal were exercised without
  an installed queue, external service, actual worker or runtime rollout.
- The consumer's existing transient draft view retains uncertain submissions;
  cancelling a same-answer retry must preserve the original uncertainty and
  receipt recovery. Focused rendered regressions cover that sequence, failed
  receipt checks and known-acceptance retention. This is not evidence of
  restart or cross-client draft durability.
- Re-read the full Plan and Validation Plan after that source merge. No broad
  parity or installed-delivery checkbox is closed. Keep #7141 open: Manager's
  generic form result path truncates command output and shows only its first
  line, while task-only local drafts carry no card/ownership revision.
  `inspect-task` includes task/events/progress/attachments/results, not steering
  receipts or original wakes. Native precondition forwarding alone therefore
  does not prove equal receipt/recovery fidelity.
- Next is the bounded terminal result/recovery and draft-provenance contract,
  reusing existing contributed actions, command transport and draft ownership
  rather than introducing a dispatch-specific Manager backend. Preserve
  #6379, #4959 and #6283 ownership. Template instantiation, process/evaluator
  proof, mapped-record production, promotion and authorized installed proof
  remain open; this effort is Active.
