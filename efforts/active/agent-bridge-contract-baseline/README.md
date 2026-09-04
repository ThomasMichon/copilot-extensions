# agent-bridge Contract Baseline

- **Slug:** `agent-bridge-contract-baseline`
- **Repo:** copilot-extensions
- **Branch(es):** plan PR followed by serial implementation PRs; registry schema
  and checker first, fixture families second
- **Created:** 2026-08-31
- **Status:** Done
- **Vision:** advances the first implementation delta under
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`version-skew-safe-contract-evolution` and
  §Behaviors/`readers-expand-before-writers`
- **Umbrella issue:** [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468)
- **Parent effort:** [`agent-bridge-contract-evolution`](../agent-bridge-contract-evolution/README.md)
  / [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460)
- **Related issues:** [#1308](https://github.com/ThomasMichon/copilot-extensions/issues/1308)
  (AHP 0.8 corpus) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (event identity) ·
  [#954](https://github.com/ThomasMichon/copilot-extensions/issues/954)
  (later mixed-version parity harness)

## Guiding Intent

Freeze the compatibility floor before changing it.

The first implementation stretch adds a machine-readable registry, exact
current and prior-runtime fixtures, source provenance, and a repository checker
for the contracts agent-bridge already serves or persists. A previous protocol
generation is captured only where one actually exists. The stretch changes no
runtime reader, writer, negotiation, capability preference, route, or session
behavior.

The result is a reproducible answer to three questions:

1. What contract does the current source and deployed runtime actually claim?
2. What exact old and current shapes must the next reader preserve?
3. Which owner and evidence gate must approve a future semantic change?

This makes #1460 Phase 1 an implementation against frozen evidence rather than
another reconstruction of current behavior.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Registry driver | Schema, checker, fixture layout, phase sequencing, and PR ownership | Isolated copilot-extensions worktrees |
| Bridge contract historian | Extract exact HTTP, Session Host, target, route, relay, and event shapes from cited source revisions and declared development versions | Read-only source history and runtime artifacts |
| External contract owners | Supply or approve fixtures whose semantics belong to AHP, venue providers, worktree claims, or event identity | Their existing issues and PR lanes |

## Coordination

- **Topology:** land the reviewed plan first, then one registry/checker PR
  followed by independently reviewable fixture-family PRs if the initial corpus
  is too large for one change.
- **Host (owns PRs):** registry driver.
- **Delegates:** extraction may be split by contract family, but one driver owns
  registry schema and duplicate-owner decisions.
- **Handoff:** every fixture contribution includes source/version provenance,
  expected owner, capture method, and a passing checker result. Externally owned
  entries land as non-normative representation references without blocking this
  effort; owner approval is required only before promotion to normative
  semantics or consumption by #1460 Phase 1.

## Context

The current source already has compatibility building blocks, but they are
distributed through code and tests:

- `plugins/agent-bridge/src/agent_bridge/protocol.py` currently advertises HTTP
  protocol generation 5 with minimum supported generation 1.
- `plugins/agent-bridge/src/agent_bridge/session_host/protocol.py` defines
  Session Host generation 1 and its byte framing/message types.
- `plugins/agent-bridge/src/agent_bridge/session_host/host_index.py` persists
  HostIndex version 1 records.
- `plugins/agent-bridge/src/agent_bridge/transport.py` serializes `SpawnTarget`
  records.
- `plugins/agent-bridge/src/agent_bridge/provider_sources.py` accepts provider
  manifest schemas 0 and 1.
- `test_wire_compat.py`, `test_protocol_negotiation.py`, and
  `test_version_skew_scenarios.py` already protect parts of tolerant reading and
  numeric protocol gating.
- `test_provider_sources.py`, `test_relay_profile.py`, `test_relay_state.py`,
  `test_transport.py`, and the Session Host tests contain additional implicit
  fixture shapes.

This repository currently has no release tags or GitHub releases; it publishes
rolling plugin development versions. For this effort, provenance therefore
means the exact source commit, the `plugin.json` and `pyproject.toml` versions at
that commit, and the capture method. An installed-runtime receipt may supplement
that tuple but does not replace it.

Those tests prove selected behavior, but there is no single registry that names
the owner, provenance, support window, fixtures, and removal gate for each
contract. Several fixtures are constructed inline, so a future change can
accidentally update the producer and its test together without preserving an
independent old shape.

The repository already uses root `tools/check-*.py` guards. This effort follows
that convention with a dependency-light checker while keeping authoritative
contract data beside the agent-bridge plugin that owns or consumes it.

## Request

> What is the first stretch of implementation we should start? Carve an effort
> in copilot-extensions for the necessary work.

## Plan

### Phase 0 — Confirm scope and ownership

- [x] Inventory every contract artifact covered by #1460 Phase 0 and classify it
      as bridge-owned, externally owned but bridge-consumed, or deferred.
- [x] Record the exact source commit and declared plugin/runtime version for the
      current HTTP 12/1 and Session Host 1 baselines using the commit plus
      declared plugin/project versions and capture method.
- [x] Separate `previous_generation` from `previous_runtime`. Permit a null
      previous generation only with a required `previous_absent_reason`; still
      identify an earlier runtime speaking the same generation where available.
- [x] Identify prior baselines from source history; do not synthesize a
      "previous" shape from the current model.
- [x] Treat event/cursor shapes as deferrable non-normative representation
      references until #1138 supplies immutable identity semantics.
- [x] Keep #1308 authoritative for the AHP 0.8 corpus; this effort registers a
      `kind: external-reference` owner/source/hash reference rather than copying
      it or defining AHP policy fields. If #1308 lands first, adopt its
      provenance field names rather than forking the format.

### Phase 1 — Land the registry schema and checker

- [x] Add `plugins/agent-bridge/contract/registry.schema.json` and
      `plugins/agent-bridge/contract/registry.json`, following the existing
      singular `contract/` convention.
- [x] Define required fields for id, owner, kind, protocol generation/range,
      capability versions, durable records, support window, bridge-contract
      rollback window, fixture references, source/version provenance,
      mixed-version scenarios, and removal gate.
- [x] Split `declared_range` (mirrored verbatim from production constants) from
      `evidence_window` (the prior runtime/generation actually covered by
      fixtures and tests). A checker may expose missing evidence but never narrow
      the source-declared support policy.
- [x] For generation families, support nullable `previous_generation` plus a
      required `previous_absent_reason`, independently of `previous_runtime`.
- [x] Add per-entry `source_paths` and source-content fingerprints so
      diff-scoped coverage checks have a stable review marker that survives
      squash merging.
- [x] Add `tools/check-agent-bridge-contracts.py` using only repository-standard
      Python dependencies.
- [x] Add focused checker tests covering malformed records, duplicate ids or
      owners, missing fixtures, path escape, invalid hashes, missing provenance,
      unsupported schema versions, and deterministic diagnostics.
- [x] Make the checker treat externally owned contracts as references with an
      explicit owner and source, never as bridge-defined semantics.

### Phase 2 — Freeze HTTP and Session Host baselines

- [x] Capture legacy and current HTTP health, session-create request/response,
      representative error, and unknown-field tolerance fixtures.
- [x] Record HTTP 12/1 as the exact `declared_range` and select exact previous
      generation and prior-runtime evidence for the initial `evidence_window`;
      do not rewrite 1–10 support as a
      narrower tested range.
- [x] Capture Session Host generation 1 framing and each control/data message
      shape as base64 inside JSON without interpreting opaque ACP payload bytes.
- [x] Convert or supplement inline compatibility tests so they consume immutable
      fixture files rather than reconstructing both producer and expected shape
      in one test. Broad conversions may be transferred to a named follow-on
      issue when the current-fixture assertion is already independent.
- [x] Name initial `new-client_old-daemon`, `old-client_new-daemon`,
      `new-frontend_H1-host`, and `unversioned-peer` scenarios without changing
      their runtime execution yet.

### Phase 3 — Freeze durable and integration boundaries

- [x] Deferred to `#1915`: Treat each family in this phase as independently
      deferrable to a named follow-on issue; it does not block the minimum
      viable exit below.
- [x] Deferred to `#1915`: Capture HostIndex version 1, `SpawnTarget`, route/remote-authority, relay
      profile/rendezvous, and bridge-side claim adapter fixtures.
- [x] Deferred to `#1915`: Capture provider manifest schemas 0/1 as externally owned fixture
      references, preserving the provider plugin as semantic owner.
- [x] Deferred to `#1915`: Record which fixtures contain process identity, ownership, authorization,
      fencing, or recovery fields that Phase 1 of #1460 must never permit an old
      writer to erase.
- [x] Deferred to `#1915`: Separate representation fixtures from semantic assertions owned by
      agent-worktrees, venue providers, credential relay, or #1138.

### Phase 4 — Gate repository changes

- [x] Add the checker to `.github/workflows/ci.yml` near the existing repository
      guards.
- [x] Add the same diff-scoped checker to `tools/hooks/pre-push`, following the
      existing local-first guard convention.
- [x] Mark focused registry tests with the existing `guard` pytest marker.
- [x] Make it fail when a registered fixture is missing or changed without its
      hash/provenance entry changing.
- [x] Accept `--base` and fail only when a changed `source_paths` entry lacks a
      corresponding registry/fingerprint or explicit non-semantic fixture
      decision, following the repository's existing PR-diff guard pattern.
- [x] Document the contributor workflow: update source, registry, fixtures,
      provenance, and owner approval together.
- [x] Bump agent-bridge's plugin/runtime/marketplace version in every
      implementation PR that changes files under `plugins/agent-bridge/`.

### Phase 5 — Hand off to tolerant-reader work

- [x] Publish the frozen contract inventory and named scenario list in #1460.
- [x] Deferred to `#1915`: Select the first bridge-owned durable record for the
      tolerant-reader and old-writer-fencing stretch using the registry's risk
      classification.
- [x] Transfer every deferred external semantic question to its named issue and
      leave only an explicit non-normative fixture reference here.
- [x] Mark this effort Done when the schema, checker, checker tests, CI and
      pre-push wiring, current HTTP 12/1 fixtures, current Session Host 1 fixtures,
      and at least one exact prior-runtime fixture are merged. Every remaining
      family must be completed or transferred to a named follow-on issue.

## Validation Plan

- [x] The registry and every fixture validate from a clean checkout without a
      running bridge, network access, or an installed plugin runtime.
- [x] Registry output and errors are deterministic on Windows and POSIX.
- [x] Every fixture path remains inside the declared `contract/` directory and its
      content hash matches the registry.
- [x] Every normative fixture has an owner, source commit, release/runtime
      provenance, capture method, and support/removal policy.
- [x] The registry's `declared_range` exactly matches production constants and
      never narrows source-declared support to the smaller `evidence_window`.
- [x] A contract with no previous generation records a non-empty
      `previous_absent_reason`; prior-runtime evidence remains independently
      representable.
- [x] The current source constants and serialized shapes match their registered
      fixtures without modifying production behavior.
- [x] A deliberately malformed entry, missing fixture, duplicate owner, stale
      hash, or uncovered contract-source change fails the repository checker.
- [x] Existing agent-bridge suites remain behaviorally unchanged; fixture-backed
      tests replace or supplement inline expectations rather than weakening them.
- [x] AHP fixtures remain owned by #1308, event identity remains owned by #1138,
      and provider/worktree semantics remain owned by their plugins.
- [x] Externally owned non-normative references can land without owner response
      and cannot be consumed as normative semantics by #1460 Phase 1.
- [x] Deferred to `#1915`: The next tolerant-reader effort can identify its exact old/new fixtures
      and previous-runtime writer from the registry alone.

## Proposal

Use a JSON-only, dependency-light source layout:

```text
plugins/agent-bridge/contract/
  registry.schema.json
  registry.json
  fixtures/
    http/
    session-host/
    host-index/
    spawn-target/
    route-authority/
    relay/
    claim-adapter/
    provider-manifest/

plugins/agent-bridge/tests/
  test_contract_registry.py

tools/
  check-agent-bridge-contracts.py
```

Fixtures are immutable observations, not generated snapshots that update
automatically with production models. A semantic owner may deliberately replace
one only by adding provenance and a new generation/capability decision; a normal
serializer refactor cannot silently rewrite history.

The registry is repository-side evidence in this stretch. No runtime component
may read it unless a later reviewed change relocates runtime-consumed data under
`src/` and defines its packaging and compatibility contract.

The first implementation PR should land the schema, checker, checker tests, and
the HTTP 12/1 plus Session Host 1 current fixtures. Prior-runtime and remaining
durable/integration families may follow as separate PRs if provenance research
would make the initial review too broad.

## Journal

### 2026-08-31 — Kickoff

- Opened #1468 as the first implementation slice of #1460.
- Chose a behavior-neutral registry/checker/fixture foundation before tolerant
  readers, writer fencing, negotiation, or cutover work.
- Grounded the initial corpus in HTTP 5/1, Session Host generation 1,
  HostIndex, `SpawnTarget`, provider schemas 0/1, relay, claim-adapter, route,
  and event/cursor representation.
- Kept AHP corpus, delegation semantics, event identity, worktree claims, and
  provider semantics with their existing owners.

### 2026-09-03 — Implementation resumed

- Activated the reviewed effort after its plan landed through #1471.
- Claimed #1468 and selected the minimum viable exit in the reviewed order:
  registry/schema/checker, current HTTP and Session Host fixtures, one exact
  prior-runtime fixture, then CI and pre-push gates.
- Kept runtime behavior, tolerant readers, writer fencing, negotiation, and
  externally owned semantics outside this baseline stretch.

### 2026-09-03 — Minimum baseline implemented

- Added a dependency-free schema and checker with canonical contract IDs,
  exact owner/source/runtime provenance, path confinement, source and fixture
  fingerprints, production-constant checks, diff coverage, and deterministic
  diagnostics.
- Froze HTTP generations 9 and 10, agent-bridge runtimes dev423-dev425,
  Session Host generation 1, and the dev150 Session Host implementation.
  Current fixture assertions execute production encoders; historical Session
  Host assertions execute the exact recorded Git revision.
- Added repository CI and pre-push gates and initially bumped agent-bridge to
  `0.4.0-dev426`.
- Combined unchanged-run validation covered every agent-bridge test: aggregate
  groups passed, and the sole budget-expired Windows installer test passed on
  its targeted retry without an intervening source change. The new checker
  suite passed directly and the new fixture guards passed through the canonical
  plugin runner.
- Opened #1915 for HostIndex, SpawnTarget, route authority, relay, claim-adapter,
  and provider-manifest evidence; that inventory owns selection of the first
  tolerant-reader and old-writer-fencing target.

### 2026-09-04 — Current-main reconciliation and completion

- Rebased the baseline through the HTTP generation 11 and 12 changes on current
  `main`, preserved exact generation 11 evidence, and advanced agent-bridge to
  `0.4.0-dev431` after intervening runtime changes consumed dev427-dev430.
- Made every checker Git subprocess ignore ambient `GIT_*` repository and
  configuration state, disable lazy fetching and replacement objects, avoid
  optional locks, and refuse terminal prompting.
- Added a regression test that runs the checker under contaminated Git
  environment variables and still resolves only the intended repository.
- Re-ran the dependency-free checker, focused checker regressions, agent-bridge
  fixture guards, version consistency, lint, and the repository install
  contract on Windows; the rebased Linux CI lane provides the POSIX half of the
  deterministic-output gate.
- Published the frozen inventory and named scenario list to #1460. All remaining
  durable and integration fixture families remain transferred to #1915.
