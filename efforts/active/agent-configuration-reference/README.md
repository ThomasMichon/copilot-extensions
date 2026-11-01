# Agent Configuration Reference

- **Slug:** `agent-configuration-reference`
- **Repo:** copilot-extensions
- **Branch(es):** Reviewed proposal, owner-reference delivery and completion PRs to `dev`
- **Created:** 2026-10-31
- **Status:** Active
- **Vision:** `documentation` and `repository-topology`: owner references and consistent scoped configuration composition
- **Umbrella issue:** #9776
- **Sub-issues:** None initially

## Guiding Intent

Make the available configuration of every `agent-*` plugin discoverable and
complete without making adopters reconstruct its schema or precedence from
source. Keep exact options and schemas with their existing plugin owner.
Explain the optional reusable harness/private knowledge-repository split in
suite documentation, and identify each plugin's actual support for that split
and for defaults, repository, machine and repository+machine configuration.
Converge the actual consumers onto that four-tier contract, including knowledge
configuration over the base harness at the repository tier, rather than only
documenting the existing gaps.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Coverage synthesis, proposal, documentation integration, validation and delivery | Managed source worktree; public coordination on #9776 |
| Workflow evidence | Worktrees, Pull Requests, Logger, Index and Conduct configuration inventory | Bounded read-only evidence assignment |
| Machine evidence | Machines, SSH, Bridge, MCP, Vault and Remote Driver configuration inventory | Bounded read-only evidence assignment |
| Venue evidence | Containers, Codespaces and Dispatch configuration inventory | Bounded read-only evidence assignment |
| Shared contract | Inert Python/Node composition, source ownership and qualification | Bounded library-only implementation assignment |
| Workflow implementation | Workflow consumers, canonical resolved-context adapter and owner references | Exclusive workflow-plugin file ownership; no publication |
| Machine implementation | Machine consumers, requirement schemas and owner references | Exclusive machine-plugin file ownership; no publication |
| Venue implementation | Venue consumers, registrar schemas and owner references | Exclusive venue-plugin file ownership; no publication |

## Coordination

- **Topology:** One canonical source effort and one PR owner; independent
  evidence tracks have non-overlapping plugin scope.
- **Host (owns PRs):** Source driver.
- **Delegates:** Evidence only before proposal acceptance; later implementation
  and documentation assignments retain exclusive file ownership and no git
  publication.
- **Handoff:** This README retains the complete objective, accepted scope,
  evidence gaps and delivery gates. A completed inventory or individual plugin
  reference is not completion.

## Context

`docs/configuration.md` currently maps repository and machine-local ownership
and routes detailed contracts to their component. `docs/adoption.md` includes
a hypothetical portable control repo with private state. The existing
documentation vision requires progressive reading, owner-local references and
source-grounded support claims. The initial documentation request closes that
intent; the explicitly authorized runtime convergence also extends the existing
repository-topology intent without replacing each plugin's schema or authority.

#2350 retains the shared repo/knowledge/repo-targeted activation convention.
#4205 retains consistent project selection across runtime consumers. Their
proposed convergence is not proof that all plugins already compose the same
configuration layers. Existing Index and Logger adopters cannot certify the
entire family. This effort must preserve those separate implementation owners.
External contributor proposals, including #9616 and #9620, remain with their
original author; unmerged behavior must not become an executable example.

### Owner inventory and reference targets

This is the initial checked-out-source inventory, not a claim of converged
support. Each delivery assignment must complete its field-level ledger and
validate the real consumer before changing these support descriptions.

| Plugin | Existing configuration authority | Current split gap | Canonical reference target |
|--------|----------------------------------|-------------------|----------------------------|
| agent-worktrees | `config.py`, acquisition/migration and policy parsers | Knowledge overlay restricts portable keys; unify eligible behavior while retaining protected policy | Existing `docs/config-reference.md` |
| agent-pull-requests | Native Worktrees-owned target/provider policy; watch/review CLI | No independent config layer; qualify delegated consumers | `docs/configuration.md`, linking Worktrees policy |
| agent-logger | `config.py`, config migrations and tenant/origin consumers | Knowledge origin/opt-in is not generic configuration graft | `docs/config-reference.md` |
| agent-index | `config.py`, effective repository layers and `index_config.py` | Repository, machine and installation overlay ordering needs reconciliation | `docs/configuration.md` |
| agent-conduct-guidance | Model-routing resolver and `schemas/model-routing.schema.json` | Repository/operator layers lack generic knowledge and repository+machine composition | `docs/configuration.md`, linking existing schemas |
| agent-machines | Requirement manifests/resources/validator and installer dependency policy | No single plugin-level four-tier composition | `docs/configuration.md`, linking requirement schemas |
| agent-ssh | `mesh.py` and transport/module/registry schemas | First matching topology file rather than knowledge graft | `docs/configuration.md`, linking transport contracts |
| agent-bridge | Machine config/adoption and selected target consumers | Bound knowledge path selection is not consistent four-tier merging | Existing `docs/machine-config.md` plus a configuration reference hub |
| agent-mcp | YAML/JSON config resolver and machine override merger | Machine overlay exists without automatic repository/knowledge layers | `docs/configuration.md` |
| agent-vault | Repository JSON selector, environment and named-vault registry | No generic knowledge graft; keep secret/runtime state out of portable tiers | `docs/configuration.md` |
| agent-remote-driver | Payload/session discovery and explicit environment control | Intentionally global discovery has no ordinary repository configuration tier | `docs/configuration.md`; descriptors remain state, not configuration |
| agent-containers | `config.py` and fleet dataclasses | Bound knowledge is fallback-only; preserve independent fragment/driver work | `docs/configuration.md`, retaining existing fleet examples |
| agent-codespaces | `config.py`, repository/drop-in loaders and provisioning consumers | Knowledge graft is conditional; adopted repository first-wins can shadow selected context | `docs/configuration.md`, retaining existing setup schema/example |
| agent-dispatch | Coordinator environment config and registrar/identity parsers | Registrar machine filtering is not a generic machine configuration tier | `docs/configuration.md`, linking registrar/identity contracts |

All plugin-relative paths above are beneath `plugins/<plugin>/`. Generated
state schemas, credential references and transport/publication authorities are
distinct from ordinary preference composition. A new optional layer must not
reinterpret those records as portable configuration.

## Request

Public-safe capture: the downstream repository name is replaced below; no
functional requirement is removed.

> Oh, we should also probably explain the addtional configuration available for `agent-*` plugins; for example, you can add related repos, change preferred venues, set up per-machine install policies, change PR behavior preferences, allocate container pools and types, define your SSH multi-machine mesh, define dispatch task worker pools and assign registrations per machine, set up local term blocks for PR cleanliness, and more.

> For each of these, we probably need to update the upstream copilot-extensions' plugins docs to have a complete doc on config options and schema, and provide select examples in [downstream harness] with pointers to the upstream docs.

The operator confirmed:

> Every agent-* plugin, including bridge, logger, index, MCP, vault and pull-requests

And selected the slug `agent-configuration-reference`.

> copilot-extensions itself does need to have direct docs on the concept of a harness+knowledge repo split, and call out for each plugin's config which parts support the split and how precedence is handled. We should also call out that are default behaviors, per-repo behaviors (knowlede repos always graft over the base harness repo at this tier), per-machine behaviors, and per repo+machine override behaviors.

After evidence identified unsupported tiers and conditional/fallback-only
knowledge grafts, the operator explicitly selected:

> Also implement consistent four-tier precedence and knowledge grafting across all plugins

This expands the assignment to runtime behavior plus documentation. Preserve
the four configuration tiers without treating ordinary override precedence as
permission to change credential custody, bypass repository vetoes, alter
installation ownership or claim unavailable resources.

## Plan

### Phase 1 - Inventory and review the documentation contract

- [x] Inventory all 14 current `agent-*` plugins, including payload-only
  Conduct and Remote Driver, against their readers, defaults, schemas and
  configuration-focused tests.
- [x] Map supported configuration files, options, types, defaults, aliases,
  validation, merge granularity and precedence to canonical owner references.
- [x] Record split support at all four requested tiers; resolve any conflict
  between the requested graft model and actual consumer behavior.
- [x] Land the complete bounded proposal through review before editing
  configuration references or runtime configuration consumers.

### Phase 2 - Establish and qualify the shared composition contract

- [x] Reuse existing configuration, repository identity, installation-context
  and knowledge-binding owners. If no common composition owner exists,
  extract one narrowly scoped, read-only shared component rather than add
  per-plugin pairing/precedence engines or a service.
- [x] Define the common low-to-high order: built-in defaults; repository
  configuration with bound knowledge over base harness; machine-wide
  configuration; selected repository+machine overrides.
- [x] Preserve explicit command/environment selection and owner-specific
  schema validation; document how those overrides interact with the four
  tiers without confusing data-query selectors with execution repositories.
- [x] Distinguish durable named-machine declarations from machine-local
  runtime settings and repository-targeted local overrides. Preserve logical
  execution-machine identity and existing namespace/registration ownership.
- [x] Define deterministic mapping/list/null behavior, source provenance,
  canonical and legacy path selection, stable repository identity and
  installation-cell separation. Retain source-relative path ownership when
  a knowledge configuration contributes paths.
- [x] Preserve existing activation, hosting-policy, credential, installation,
  service and resource-custody gates. A configuration layer does not grant
  authority, provision dependencies or start a service.
- [x] Prove the common contract with focused tests before migrating consumers,
  including absent/unbound knowledge, explicit binding errors, unsafe files,
  independent same-named repositories and cross-layer conflicts.

### Phase 3 - Converge consumers and complete canonical owner references

- [x] Wire actual configuration consumers and provide complete references for
  Worktrees, Pull Requests, Logger, Index
  and Conduct, reusing their existing detailed documentation and schemas.
- [x] Wire actual configuration consumers and provide complete references for
  Machines, SSH, Bridge, MCP, Vault and
  Remote Driver, including a truthful no-config description where applicable.
- [x] Wire actual configuration consumers and provide complete references for
  Containers, Codespaces and Dispatch,
  including fleet/pool, worker identity and per-machine registration shapes.
- [x] For every configurable surface, state its supported tier, exact
  precedence and merge behavior, selection/override mechanisms, validation,
  legacy compatibility, application/reload boundary and secret-state limits.
- [x] Add generic, source-backed examples without changing the operator's
  active configuration, performing rollout or enlarging existing authority.
- [x] _(agent-recommended)_ Link each schema/options family to its canonical
  reader or schema source so future changes have a traceable maintenance home.

### Phase 4 - Explain the split and route adopters

- [x] Add direct suite documentation for the harness/knowledge split, optional
  versus required binding, writable state homes and the four tier concepts.
- [x] Add a per-plugin support/precedence matrix and route exact options to the
  owner references without duplicating their schemas.
- [x] Update existing configuration, adoption and reference waypoints.
- [x] Qualify the generic examples and stable reference targets for selected
  downstream examples; keep adopter-specific material outside this repository.

### Phase 5 - Validate and close delivery

- [ ] Complete the Validation Plan, address review findings and merge the
  owner-reference and suite-navigation changes with required CI green.
- [ ] Record actual release/publication state without installing or activating
  runtimes; retain any separately scoped runtime gap with its existing owner.
- [ ] Archive this effort as Done after all source acceptance is resolved,
  close #9776, release its completed focus and finalize the controlled worktree.

## Validation Plan

- [x] _(agent-recommended)_ Maintain a source-backed coverage ledger for every
  plugin and configuration family; check completeness against actual readers
  and schemas, not only the topics used in examples.
- [x] _(agent-recommended)_ Validate YAML/JSON examples against existing
  parsers/schema fixtures where safely possible, without provisioning services,
  changing real settings or acquiring credentials.
- [x] _(agent-recommended)_ Run existing documentation/navigation, runbook,
  effort-structure and whitespace checks; verify changed links outside the
  automatically guarded reading path as well.
- [x] _(agent-recommended)_ Exercise focused documentation/schema tests only
  where changed executable fixtures or tooling requires them; document why a
  prose-only change needs no runtime, fresh-machine or live-venue test.
- [x] Prove every migrated plugin's real configuration consumer observes all
  four tiers and knowledge-over-harness repository precedence; parser-only
  success does not demonstrate actual lifecycle or operation selection.
- [x] Retain no-config/default and explicit selector compatibility, protected
  schema/authority refusals, live reload/application boundaries and isolated
  first-touch tests for changed configuration acquisition.
- [x] Run changed-owner suites, touched-code lint, install-contract and
  applicable simple/clean-room checks. Record availability and reasons for
  any unavailable live or native-platform lane without claiming rollout.
- [x] Review every precedence/support claim against consumer evidence,
  distinguish activation from configuration, and preserve repository vetoes,
  secret-state boundaries and unsupported layers.
- [ ] Verify final-head review, required CI and actual merge for each delivery
  PR; an open PR is not completion.
- [ ] Preserve public-neutral examples and one-way downstream traceability;
  publish no private identities, paths, topology, links or operator state.

## Proposal

Prefer owner-local `docs/` references and existing schema sources, linked from
each plugin's README and the suite's existing configuration/reference
waypoints. Decompose large option families rather than producing a second
monolithic manual. A shared conceptual page may explain tier vocabulary,
but plugin-specific precedence must remain explicit and source-backed.
The operator has authorized runtime convergence. One shared read-only
composition mechanism may be necessary; first prove there is no existing
equivalent, then integrate through each plugin's own parser and consumer rather
than replacing its schema, lifecycle or authority. Do not introduce a service,
an installer-time configuration migration or a parallel validation framework.
Keep current support, reviewed intent and completed behavior distinct until
their implementation and validation gates actually pass.

## Journal

### 2026-10-31 - Scope and inventory

- The operator confirmed every current `agent-*` plugin and the effort slug,
  then required direct split documentation and per-plugin four-tier support.
- Resolved a fresh source-only worktree through its existing native owner;
  confirmed compatible effort adoption and the repository-scoped identity.
- Deduplicated reference work and retained #2350/#4205 as separate convergence
  owners. Opened #9776 and took this bounded documentation assignment.
- Three read-only evidence tracks are inventorying non-overlapping plugin
  scopes. The coordinator owns suite concepts, navigation and integration.
  No configuration references, runtime configuration or services are changed.
- Proposal acceptance and all documentation delivery gates remain open.

### 2026-10-31 - Runtime convergence explicitly authorized

- The source audit found restricted Worktrees overlays, narrower Logger
  origin/opt-in behavior, fallback-only or conditional venue grafts and several
  consumers with no general knowledge configuration tier.
- The operator chose runtime convergence across every plugin rather than
  documentation-only exceptions. The plan now includes a common composition
  contract, actual consumer migration and focused behavioral qualification.
- #2350 and #4205 remain separate broader activation/selection owners. The
  migration must reconcile their accepted requirements without claiming that
  ordinary configuration layering completes every activation or selection
  lifecycle objective.
- No runtime implementation has begun. This expanded proposal and its
  repository-topology intent amendment must clear review first.

### 2026-10-31 - Expanded proposal accepted

- #9801 merged at `61f881ec4ebdaceb22087a9d921e72873a5d7b29` after exact-head
  Copilot approval, green required CI and no unresolved review threads.
- Pulled the same source worktree forward and re-read the complete Plan and
  Validation Plan. Phase 1 is resolved; Phase 2 is the next owned slice.
- No runtime configuration implementation, service action, installation or
  rollout has occurred. The source driver continues through common-contract
  qualification, all 14 consumer migrations, complete references and closure.

### 2026-10-31 - Shared composition qualified; consumer migration started

- Added the dependency-free `configuration-layers` Python and Node APIs:
  explicit owner-resolved context, exact repository/machine/cell scope,
  knowledge-over-base ordering, deterministic merging and winning-source
  provenance. The library has no binding registry, service or installation
  behavior.
- Independent validation passed ten focused tests, including actual Node
  execution, native source-relative reads, malformed/absent sources and
  symlink escape/loop controls. Ruff and whitespace checks passed.
- Corrected source-path normalization before migration: relative parent
  segments may remain inside the declaring repository; traversal outside its
  physical root remains refused. Requested document paths remain bounded.
- Workflow, machine and venue implementation assignments now own disjoint
  plugin files. The workflow assignment also owns the minimal canonical
  resolved-context adapter if existing consumers need a shared read seam;
  no group may create a competing binding or identity engine.
- Drafted the suite split reference and routed configuration/adoption readers
  to it. Navigation checks passed. Consumer integration, complete field
  references, publication and final effort closure remain open.
- No operator configuration, runtime installation, service or external venue
  was changed. These are source tests, not installed-host or rollout evidence.

### 2026-10-31 - Consumer qualification and final coverage audit

- Completed owner-local configuration references for all fourteen plugins.
  Added the suite's per-plugin preference/authority matrix and stable owner
  pointers for downstream examples. Remote Driver remains explicitly
  no-config; Pull Requests uses Worktrees policy rather than a second engine.
- The contained venue packet passed 415 tests, with six native-POSIX cases
  excluded on Windows. Three independent first-touch consumer checks passed.
- The workflow packet passed Worktrees 400, Logger 72, Index 49 and Conduct
  56 tests without skips. Independent final consumer checks passed another
  166 selected cases, including actual configuration readers.
- The machine packet passed Machines 198, Bridge 272, SSH 68, MCP 83 and
  Vault 58 tests, plus four Remote Driver controls. Independent final
  Machines/MCP/Vault adapter checks passed 30 cases. Machines' actual
  collector-to-native-surface path proves four-tier scalar defaults while
  retaining live values, collection rules and conflicting-enforcement vetoes.
- Native POSIX permissions/FIFO behavior and a privileged symlink control
  remain platform-specific exclusions. The changed read-only configuration
  paths were exercised with isolated native profiles; no live external venue
  or installed-host rollout was acquired for this source-only assignment.
- Canonical SDK/peer copies and the thirteen-runtime install contract passed.
  Documentation consistency passed; the wider 32-page navigation audit found
  and corrected two links to a renamed installer heading.
- Full no-actor unit validation remains open: an old stateful-repository
  refusal expectation was updated for the requested preference graft without
  loosening protected-key or acquisition-metadata assertions. An isolated
  control-plane fixture now supplies the new composition boundary.
- The final support audit identified a remaining legacy related-source seam
  that still conditions knowledge lookup on required-state policy. Close
  that actual consumer gap, not merely its documentation claim, before final
  source hashing, the complete unit packet and PR publication.
- Added patch changefiles for every modified plugin. No version numbers,
  private configuration, credentials, live registrations or services changed.
  The effort remains Active; review, required CI, actual merges and archival
  are still completion gates.

### 2026-10-31 - Related-source consumer closure

- Closed the legacy related-source gap: the actual native reader composes
  base, explicit bound knowledge, validated machine-wide and exact-project
  configuration in order. Stateful projects graft configuration; unresolved
  bindings and invalid cells refuse, and explicit foreign targets resolve
  their own repository identity and exact local root.
- Retained source-file provenance for direct machine-wide documents.
  Machine-identity callers explicitly select the protected registry family;
  configuration grafting does not change state-write or hosting authority.
- Twenty focused controls and ninety related-source/provenance regressions
  passed through the contained runner. Independent final checks passed all
  thirty-eight graft/consumer cases and six protected-registry controls.
- Updated pure acquisition fixtures for the actual SDK error type, optional
  source-family arguments and the declared composition boundary. Final
  source hashes now bind those fixtures and consumers; full unit validation
  and publication remain open.

### 2026-10-31 - Current-development reconciliation

- Saved the coherent implementation and retained a local pre-sync backup.
  Reconciled the effort index and unit coverage targets with current `dev`,
  preserving the independently added effort and footprint coverage.
- Current `dev` uses the canonical version-3 MCP installed-footprint record.
  Removed an automatically merged legacy history fragment and refreshed
  source digests through the footprint owner's tool. Contract revision 36
  and its footprint semantics remain unchanged; the thirteen-runtime
  install-contract check passes.
- Final unit qualification runs on the reconciled source, not the earlier
  draft bytes. Nothing has been published or rolled out yet.

### 2026-10-31 - Final source validation

- The positively admitted no-actor packet passed all 3,188 nodes, with zero
  failures or skips and coverage recorded once. Its 390-file source closure
  matches the reconciled bytes; the complete packet finished in 267 seconds.
  Non-admitted parameter cases remain intentionally deselected by the
  existing exact whitelist.
- Final documentation consistency, all 32 owner/waypoint pages and the
  downstream example targets passed with zero broken links. Touched-code
  `F,E9` lint, canonical vendor checks and the thirteen-runtime install
  contract passed.
- All local source Validation Plan items are resolved. Native Windows
  consumer and isolated first-touch evidence is recorded above; native
  POSIX/FIFO exclusions and the unacquired live/installed-host lanes remain
  explicit limitations, not claims of rollout.
- Publication, required CI, automated review, actual source/downstream
  merges and final effort archival remain open. A green local packet is not
  completion or merge authority.
