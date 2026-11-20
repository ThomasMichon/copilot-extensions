# Shared Repository Context

## Goal

Extract the read-only core of project/registered-repository configuration,
state-root mapping and repository-context resolution into one general-purpose
shared library, seeded from the existing vendored `configuration_context`
package. Reuse the existing configuration-layer owner rather than creating a
parallel preferences engine.

This is the receiving objective from #12920, not a new execution grant. The
complete source contract and the finite #12042 registry-default residual below
remain unfinished.

- **Slug:** `shared-repository-context`
- **Repo:** copilot-extensions
- **Branch(es):** independent, provider-first source contributions after plan review
- **Created:** 2026-11-20
- **Status:** Draft
- **Vision:** [standalone Vault](../../../visions/vault/README.md#central-fleet-and-standalone-local); [repository identity authority](../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity)
- **Umbrella issue:** #12920
- **Source issue:** #12042; existing native default-owner contribution #12681

## Guiding Intent

One versioned reader should interpret the existing configuration and repository
identity sources in-process. Worktrees remains the authority for registry
writes, registration, installation cells and lifecycle effects; Vault remains
the authority for its selection, credentials and service/cache custody.
A library import must not become a second registry, preferences engine,
installation authority or way around a required configuration layer.

This plan closes an existing independence and version-skew delta. It does not
change the standing visions or declare that current readers already meet it.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Planning steward | Documentary reconciliation and reviewed receiving only | #7556 and this plan |
| Existing configuration-layer/read owners | Retain schema, layer composition and shared-reader authority | #12920 and the owning configuration references |
| Existing Worktrees owners | Retain registry writes, repository identity and installation-cell/lifecycle authority | #12920; existing Worktrees coordination |
| Existing Vault default-owner claimant | Retains the bounded native selection claim and its original custody | #12042 and #12681 |
| Existing cache-maintenance owner | Retains independent cache policy and service work | #12900 |

No implementer, venue or execution budget is appointed by this table.

## Coordination

- **Topology:** one canonical read-core plan; separately reviewed provider and
  consumer slices under their existing owners.
- **Host (owns PRs):** the planning steward owns this documentary contribution
  only. Later source drivers retain their own contributions and acceptance.
- **Delegates:** none appointed.
- **Handoff:** provider support, consumer migration, promoted schema/copies and
  adopter evidence remain separate, attributable results in this plan and
  #12920. A child PR or tracking retirement is not completion.
- **Retained claims:** #12681's explicit native-default selection is delivered
  source, not a replacement implementation target. Its original claimant,
  configuration precedence, fail-closed invalid-selection behavior, cwd and
  database/cache/service/installation custody remain unchanged.
- **Registry-default receiving:** P12042/V12042/G12042 retain the complete
  original and both refinements. Normal reviewed complete plan/intake delivery
  and current applicable original/receiver/custody readback precede any separate
  tracking judgment. #12042 remains OPEN during proposal; #12920 remains the
  OPEN canonical tracker. No generic acknowledgment, claim expiry, source
  takeover or implementation-before-planned-receiving gate is introduced.
- **Boundary:** discovery and read-only tests do not acquire live credentials,
  change OS settings, mutate repositories or transition a service. Promotion
  and source publication never authorize adopter rollout.

## Context

#12920 identifies a real architectural dependency: the vendored
`configuration_context.read_owner_context` can invoke a selected Worktrees
runtime for `state-root --configuration --json`. A missing executable can
therefore make valid explicit repository context unavailable. #5616 is related
prior art, not proof of the same executing failure or its cause.

#12042 additionally asks for registry-derived machine-default selection when
no higher-precedence project/context selects an owner. Its complete original
requires in-process configuration grafting, native neutral-directory and
minimal-PATH behavior, parity with Worktrees and actionable fail-fast absence.
Its refinements preserve delivered/current-checkout precedence, validation of
an installer-recorded default and the daemon launcher's equivalent resolution.

#12681 supplies useful explicit native `default_configuration_root` selection
without Git or a Worktrees subprocess. Its description expressly excludes
implicit aggregation of registered owners. That credit does not complete
#12042's registry-derived family or #12920's shared schema/migration outcome.
#12940 and #12982's cache-target/maintenance work remains independent.

The [configuration ownership map](../../../docs/configuration.md) is not a
second schema. Existing owner references and the
[contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md)
govern the extraction and supported skew.

## Request

Public source #12920 states the receiving Goal above and requires:

> First inventory existing schema/read owners and submit a bounded reviewed
> extraction/migration plan; then land provider-first library and consumer
> slices with focused tests, documentation, normal review/CI/changefiles and
> promotion. Track source, release and adopter qualification separately.

This source contract is historical task evidence, not fresh operational
authorization. The following decomposition is agent-recommended. It retains
the complete original #12920 acceptance and #12042 original/discussions rather
than treating a merged neighboring PR as whole fulfillment.

## Plan

### Phase 1 - Existing-owner inventory and reviewed contract

- [ ] **S1:** Inventory actual schemas, layer/read owners, registry and
  state-root inputs, consumer calls and native-default/cache boundaries.
  Reconcile active claims before selecting implementation; retain existing
  owner references rather than inventing a second configuration specification.
- [ ] **S2:** Review the bounded extraction/migration contract before source
  changes. Declare a common versioned result schema and explicit account home,
  registry/config locations, cwd and selected configuration-root inputs.
  Specify supported schema generations, truthful unavailable/invalid/
  unsupported outcomes and the provider/consumer/rollback window.

### Phase 2 - Provider first, without mutation authority

- [ ] **S3:** Extract the reader from the existing vendored
  `configuration_context` and configuration-layer owner into one canonical
  shared source. Preserve required layer composition, bound/unbound/unresolved
  distinctions, native source custody, foreign-repository isolation and
  explicit/resident/delivered/caller/default precedence. Failed discovery is
  never absent configuration; no required state-root layer is silently dropped.
- [ ] **S4:** Make Worktrees' existing read commands thin adapters to the
  supported reader. Keep registry/config writes, installation-cell validation,
  registration and lifecycle authority with their current owners. The shared
  reader neither allocates nor rewrites adoption, selection or repository data.

### Phase 3 - Vault consumer and complete registry-default residual

- [ ] **S5:** Migrate Vault's capable repository/state-root consumers to the
  shared in-process reader without launching Worktrees or an arbitrary ambient
  runtime. Preserve native explicit-default selection, cache-maintenance
  contracts, caller cwd, database registrations/keys, endpoints and custody.
  Explicit `--configuration-root` must not retain an executable dependency.
- [ ] **P12042:** Resolve a single configured machine-default owner from the
  local project registry's existing Vault configuration/graft semantics when
  no higher-precedence selection applies. Explicit project/configuration-root,
  resident/delivered context and an actual cwd project keep their precedence;
  preserve #12681's declared native selection and invalid-selection refusal.
  Review their exact composition through the existing owner, not a second
  embedder-specific routing policy or unconditional root prefix. Validate an
  installer-recorded root as a real adopted checkout/project before using it;
  a package-staging snapshot is not context authority. Retain the requested
  registry/conventional-location fallback only through the supported owner,
  without guessing an owner or silently switching a selected credential scope.
- [ ] **V12042:** Qualify actual CLI/credential-helper and daemon-launcher
  consumers on Linux and Windows from a neutral root, an unrelated service
  working directory and minimal PATH without Git or Worktrees execution.
  Explicit selection still wins. Compare the canonical reader and Worktrees
  against the same graft/precedence fixtures. No default gives one actionable
  set-default/pass-configuration-root error and a distinct fail-fast exit;
  invalid, ambiguous or unavailable required context is not another owner's
  successful selection. Retain process cwd and database/cache bytes.

### Phase 4 - Promoted schema and consumer qualification

- [ ] **S6:** Deliver canonical source and byte-identical vendored consumers
  through the existing dev-to-main promotion flow, pinning schema and copies.
  Document supported generations and migration/rollback selection; package
  version alone does not establish support. Retain prior supported read-command
  behavior and report an unsupported required contract before effects.
- [ ] **S7:** Qualify real standalone and consumer entrypoints with explicit
  and neutral/foreign directories, linked worktrees, malformed/version-skewed
  registries and absent external executables. Compare actual Worktrees reads
  with in-process results, and preserve complete required-layer failures.
  Source, containing release and adopter qualification remain separate.

## Validation Plan

- [ ] **G12920:** Resolve S1-S7 at their actual schema, read-command and consumer
  boundaries. A capable consumer resolves the same supported repository/
  state-root context from explicit inputs without launching Worktrees;
  Worktrees agrees while retaining exclusive writes and cell/lifecycle
  authority. Invalid/unavailable/unsupported required context fails honestly.
- [ ] **G12042:** Resolve P12042/V12042 and continuing Coordination against the
  complete registry-default original/refinements: Linux AND Windows neutral/
  service/minimal-PATH consumers, CLI AND daemon equivalence, precedence,
  valid-checkout default admission, graft parity and actionable distinct
  fail-fast absence. Explicit native-default source alone is not this exit.
- [ ] **GSkew:** Verify supported old/new readers, consumers and rollback,
  malformed/missing declarations and every promoted vendored copy's exact
  schema/core. No ambient executable, guessed package-version compatibility
  or silently lost required layer supplies acceptance.
- [ ] **GCustody:** Keep read-only fixtures isolated and credential-free;
  prove no registry/config/repository, OS or service mutation. Preserve
  original source/consumer/cache/native owners, unknown outcomes and all real
  holds. Native/installed/adopter evidence is not inferred from source tests.

## Proposal

Keep #12920 OPEN as the canonical whole structural outcome. Receive the finite
#12042 registry-default residual here without duplicating #12681's delivered
native-selection implementation or #12900's cache work. All technical checks
remain unchecked. This proposal supplies no execution, deployment, credential,
access, spending, cleanup or source/resource-transfer authority.

## Journal

### 2026-11-20 - Complete shared-reader and registry-default proposal

The [campaign source crosswalk](../backlog-reconciliation/journal/2026/11.20-shared-context-and-registry-default-receiving.md)
preserves the two complete current originals and discussions, narrower merged
native-default credit and #13075's separate historical timeout. Normal review,
complete receiving readback and separate source judgments remain; no fix,
tracker retirement or owner transfer is claimed.
