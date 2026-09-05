# Dispatch-Owned Managed Companion Runtimes

- **Slug:** `dispatch-owned-managed-runtimes`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs
- **Created:** 2026-09-04
- **Status:** Done
- **Vision:** [plugin-services/`delegated-heavy-companion-runtime`](../../../../visions/plugin-services/README.md#delegated-heavy-companion-runtime);
  [agent-index/`lightweight-client-and-declared-host-service`](../../../../visions/plugins/agent-index/README.md#lightweight-client-and-declared-host-service)
- **Umbrella issue:** #2007
- **Sub-issues:** #2010, #2013, #2081, #2103, #2116
- **Transferred follow-up:** #2118 (clean-room provisioning and lifecycle validation)

## Guiding Intent

Keep optional heavyweight companion dependencies out of ordinary plugin and
session paths without turning plugin-controlled commands into package managers.
An attributed plugin declares what a companion runtime needs; the already-running
trusted dispatch supervisor alone materializes, selects, and retires immutable
runtime cells.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning worktree | Own reviewed intent and carve independent slices | This proposal PR |
| Per-slice worktree | Implement one accepted slice | Fresh worktree and PR linked from #2007 |

## Coordination

- **Topology:** independent per-slice PRs, serialized through `main`
- **Host (owns PRs):** planning worktree for this proposal; a fresh worktree for
  each implementation slice
- **Delegates:** none initially
- **Handoff:** each merged slice updates this effort before the next slice is
  activated

## Context

Generic plugin-companion declaration and live process supervision already let
`agent-dispatch` own an existing plugin process without provisioning it. The
next gap is a generic way to materialize a separately versioned companion
runtime without giving installer authority to plugin code.

The existing plugin-service vision makes self-provisioning the default and
requires plugins to remain independent of an optional control plane. This
effort introduces a deliberately narrow exception for a configured optional
capability whose dependency footprint is too heavy for ordinary first-use or
session-start installation. The plugin remains safe and inert when the
supervisor is absent; it must not masquerade as ready or silently fall back to
self-provisioning.

The quarantined prototype demonstrated useful primitives, but coupled
declaration trust, materialization, live cutover, retention, and one plugin's
integration. It is design and test reference only; implementation restarts from
current `main` in bounded slices.

## Request

Allow the `agent-dispatch` supervisor to materialize separately versioned,
immutable runtimes for plugin-contributed companion services. Package
installation authority must remain inside the trusted dispatch process;
plugin commands and ordinary CLI calls must not be able to provision or update
these runtimes.

## Plan

### Phase 0 — Reviewed intent

- [x] Extend the plugin-service and agent-index visions with the narrow
  `delegated-heavy-companion-runtime` exception.
- [x] Land this effort proposal before implementation.

### Slice 3A — Declaration contract

- [x] Carve and claim a contract-only sub-issue.
- [x] Add the reusable managed-companion-runtime pattern and narrow the existing
  no-shared-infrastructure/self-provisioning invariants only for this explicit
  capability boundary.
- [x] Extend attributed `plugin-companion` declarations with strict
  managed-runtime metadata while keeping the daemon non-provisioning.
- [x] Allow only logical runtime identity, portable version/profile components,
  plugin-relative project inputs, bounded extras and validation imports, and
  named environment bindings.
- [x] Reject arbitrary installer commands, package-manager flags, indexes,
  credentials, absolute runtime roots, lexical traversal, unknown fields, and
  direct or unattributed registration. Filesystem links and reparse points remain
  a materialization-time check.
- [x] Preserve plugin root, version, marketplace provenance, source declaration,
  and activation scopes in the desired runtime authority fingerprint.

**Completion gate:** dispatch can validate and retain managed-runtime intent, but
no package manager, environment builder, or live companion path consumes it.

### Slice 3B — Immutable materialization

- [x] Carve and claim a materialization sub-issue only after Slice 3A lands.
- [x] Resolve the physical runtime root and toolchain from dispatch-owned policy,
  never plugin-provided executable authority.
- [x] Serialize every runtime root with a crash-safe interprocess lock shared
  across supervisor environments.
- [x] Copy declared plugin-relative inputs into a root-contained immutable
  snapshot, build in unique staging, validate imports, and atomically publish a
  version/profile/content-digest cell.
- [x] On Windows, select a trusted signed base Python, copy only the required
  runtime files, and verify executable trust before package installation.
- [x] Reuse an already-valid published cell and preserve an existing cell when
  staging, installation, or validation fails.

**Completion gate:** only the running dispatch service can build or reuse a
validated immutable cell; publication does not yet replace a healthy companion.

### Slice 3C — Safe cutover

- [x] Prepare and validate a replacement before stopping the current companion.
- [x] Snapshot exact run, stop, and health argv plus environment against the
  selected immutable cell.
- [x] Readiness-gate first launch and replacement; if replacement fails, restart
  the prior companion from its still-published cell.
- [x] Prevent plugin disablement, provider uncertainty, or declaration churn
  from crossing runtime authority boundaries.

**Completion gate:** initial launch and updates use dispatch-owned cells with
prepare-before-stop and rollback to the prior published runtime.

### Slice 3D — Retention

- [x] Protect active and rollback generations with identity-bound leases that
  remain legible across supervisor environments.
- [x] Reclaim only cells that are unreferenced, unleased, ownership-valid, and
  outside bounded retention.
- [x] Preserve foreign live leases and fail closed on ambiguous ownership,
  linked roots, malformed receipts, or unverifiable process identity.

**Completion gate:** bounded garbage collection cannot remove a live or
rollback-required runtime generation.

### Slice 3E — First plugin integration

- [x] Move one configured host companion from legacy installed-runtime
  supervision to the generic managed-runtime contract.
- [x] Keep unconfigured repositories, client roles, missing dispatch, and
  unsupported installation contexts inert.
- [x] Prove that plugin commands and direct CLI calls cannot trigger companion
  package installation.

**Completion gate:** an opted-in host is turn-key through dispatch-owned
provisioning, while every non-host path stays lightweight and inert.

## Validation Plan

- [x] Contract tests cover unknown fields, unsafe components, plugin-root
  escapes, provenance loss, direct registration, and non-execution in Slice 3A.
- [x] Materialization tests cover concurrent builders, stale locks, unique
  staging, failed installs, failed validation, atomic publication, idempotent
  reuse, and Windows signed-base verification.
- [x] Cutover tests cover first launch, prepare-before-stop, readiness failure,
  rollback, provider uncertainty, contributor disablement, and supervisor
  restart.
- [x] Retention tests cover active and foreign leases, stale leases, rollback
  protection, bounded retention, root-lock serialization, and linked or
  mismatched cells.
- [x] Integration tests prove missing runtime triggers dispatch provisioning
  only, while plugin-side self-provision paths remain disabled.
- [x] Run the agent-dispatch and integrated plugin suites, repository guards,
  and install-contract checks for every behavior-changing slice. All required
  local/CI suites and gates passed; historical Windows installer-readiness
  baseline failures remain documented in the Journal.
- [x] Deferred to `#2118`: Run the applicable clean-room provisioning scenario
  for every behavior-changing slice when a disposable runner is available.
- [x] Require independent review of each slice's authority, containment,
  rollback, and cleanup boundaries before merge.

## Proposal

Adopt a new `delegated-heavy-companion-runtime` exception rather than weakening
the default self-provisioning contract for runtime plugins.

The exception is valid only when the capability is explicitly configured,
inert without its supervisor, and too heavyweight for ordinary plugin
bootstrap. The plugin contributes declarative package inputs and lifecycle
adapters, but no arbitrary installer command. Dispatch owns physical placement,
toolchain selection, package-manager invocation, immutable publication, live
selection, rollback, and retention.

Implementation begins with a declaration-only slice. The prototype's combined
provisioning and cutover code is not a candidate for wholesale reuse.

## Journal

### 2026-09-04 — Proposal carved

- Opened #2007 from parent #1843 after generic companion supervision landed.
- Split the managed-runtime stretch into declaration, materialization, cutover,
  retention, and first-integration slices.
- Selected a contract-only first slice so reviewed schema and authority
  boundaries land before any dependency download or live lifecycle change.

### 2026-09-04 — Declaration contract implemented

- The reviewed proposal landed as #2009 and Slice 3A was carved as #2010.
- `plugin-companion` declarations now validate a versioned managed-runtime
  contract containing only bounded logical runtime data and plugin-relative
  Python project inputs.
- The complete managed-runtime declaration joins plugin root and version in the
  runtime authority revision. No daemon path consumes it for provisioning or
  launch.
- Added the reusable managed-companion-runtime pattern and its narrow,
  explicit exceptions to ordinary standalone self-provisioning invariants.

### 2026-09-04 — Immutable materialization implemented

- The singleton supervisor now prepares attributed managed-runtime declarations
  asynchronously without changing live companion launch state.
- Dispatch-owned policy selects the physical root, package manager, and base
  Python runtime. Root-wide crash-safe locking, root-contained snapshots,
  disposable install inputs, import validation, and atomic publication produce
  immutable content-addressed cells with complete authority/toolchain receipts.
- Reuse revalidates the full cell, Windows copied-base trust, POSIX external
  runtime identity, and declared imports. Failed builds preserve prior cells.

### 2026-09-05 — Safe cutover implemented

- Implemented #2081 with immutable, authority-bound launch snapshots containing
  exact lifecycle argv, working directory, timeouts, cell identities, and the
  full effective environment. Preparation and published-cell validation precede
  retirement; failed replacement readiness restores the exact prior snapshot
  without rebuilding its cells.
- Gated process receipts and an atomic last-ready selection recover interrupted
  launches and updates. Recovery cannot retain revoked contributor authority,
  endlessly retry a failed historical configuration, or launch beside an
  unconfirmed Windows predecessor. Provider uncertainty preserves only an
  already-live process with unchanged complete authority.
- Replaced preparation-only supervisor expectations with focused cutover,
  churn, disablement, crash-budget, and restart regressions. A real-process
  readiness/rollback/stop test also exercises repeated console-child probes.
- The full agent-dispatch suite passed: **1,960 passed, 4 skipped**. Installer
  readiness fixtures passed (**47 tests**); lint, install contract, version
  consistency, headless launch, documentation consistency, generated payload,
  and installation-context synchronization gates passed. Marketplace isolation
  remained report-only (**725 findings**).
- Native Windows observation from a windowless parent recorded zero owned
  visible windows and zero owned foreground windows across the real-process
  scenario. Clean-room container scenarios were not run because Docker was
  unavailable.
- Slice 3C subsequently merged as #2099. Retention,
  specific-plugin integration, independent engine lifecycle, and multi-host
  failover remain outside this slice.

### 2026-09-05 — Retention implemented

- Implemented #2103, closing the retention portion of
  `plugin-services/delegated-heavy-companion-runtime` without extending its
  intent. Version-2 ownership receipts bind exact cells and roots to complete
  declaration authority. Version-1 generations remain recoverable and are never
  automatically reclaimed; invalid existing cells are preserved in place.
- Root-visible preparation and child-process leases reuse OS process-start
  receipts, bind the PID authority and launch/cell identity, and precede
  predecessor retirement and child gate release. Persistent selected and
  prior-ready rollback pins survive supervisor restart and interrupted
  publication. A gated first-launch receipt retains its discovery lease until
  recovery settles it.
- Cleanup runs off the supervision thread under the materializer's root-wide
  interprocess lock. It preflights all metadata and descendants before removing
  exact, receipt-valid, unreferenced cells beyond the bounded age/count policy.
  Foreign domains, uncertain or reused PIDs, malformed metadata, and
  linked/reparse paths never authorize deletion.
- Independent review identified peer-lease corruption affecting preparation
  release and unmanaged successors blocking stale-lease cleanup. Both findings
  are fixed with focused regressions. Preparation release now addresses only
  its exact lease; a redundant-pin release failure preserves the ready process
  and emits a warning.
- Targeted managed-runtime coverage passed: **127 passed, 1 skipped**. The
  full agent-dispatch suite passed: **2,028 passed, 4 skipped**, across four
  contained sub-suites. An earlier full run stopped after **681 passed,
  2 skipped** because the host-state guard observed persistent Windows
  `User:Path` drift; no host state was rolled back, and the unmodified rerun
  passed. Coverage includes actual cross-process materialization/cleanup
  locking and a native Windows junction.
- Lint, install contract, version consistency, headless-launch, documentation
  consistency/references, vendored-library sync, generated payload, and
  installation-context synchronization gates passed. Marketplace isolation
  remains report-only at **725 findings**.
- Installer-readiness fixtures reproduced the separately confirmed Windows
  baseline: **41 passed, 6 failed**, all six reporting
  `JSON document changed while it was being read`. That library is unchanged.
  Container clean-room scenarios were unavailable because Docker was absent;
  this run provides native Windows evidence, not a native Linux execution.
- Agent-dispatch advances to **0.1.2-dev25**, beyond the concurrently published
  version. Its Python `__version__` already derives from package/build metadata
  and needs no duplicate constant.
- Slice 3D subsequently merged as #2115. Specific-plugin integration,
  independent engine lifecycle, host placement, and failover remain outside
  this slice.

### 2026-09-05 — Agent Index host integration implemented

- Implemented #2116, closing the first-integration portion of
  `agent-index/lightweight-client-and-declared-host-service` and
  `plugin-services/delegated-heavy-companion-runtime` without changing their
  intent. The attributed registrar declaration names one payload-versioned
  `service` / `host` runtime: the plugin's vendored helpers and `[store]`, never
  the independent engine/model extra.
- The provider resolves a configured project host without an installed runtime
  and uses its own read-only installation-context resolver. Non-project,
  unconfigured, client, unattributed, requested, namespaced, and blocked contexts
  do not authorize materialization or launch. Missing dispatch has no fallback.
- Lifecycle adapters consume only the dispatch-selected interpreter and never
  enter the legacy selector or an installer. Public host start/restart/deploy
  refuse. Explicit setup/install/update remain base/client-only, namespaced
  host provisioning is refused before building, and compatibility bootstrap
  and ensure hooks are inert. Client installation governance is preserved.
- Service/worker launches cannot provision, start, or stop the independent
  embedding engine. Managed workers remain within supervisor containment and
  explicitly pass `-B` with `-I`; full-text subprocesses also suppress bytecode,
  preserving receipt-hashed generations. An independent review identified the
  isolated-worker bytecode hazard and the base-only CLI's missing optional MCP
  dependency. The latter now reports unavailable without installing anything;
  the read CLI and hosted HTTP surface remain supported.
- The full Agent Index suite passed: **506 passed, 61 skipped**. It includes a
  real service launch from a windowless Python parent, two health cycles with
  no owned visible/foreground windows observed on native Windows, exact stop,
  and absence of plugin runtime selection or an engine footprint.
- The full Agent Dispatch suite passed: **2,079 passed, 5 skipped**, including
  the shipped declaration/provider through attributed discovery,
  materialization, readiness, recovery authority, and inertness tests.
  The initial run exposed new test-fixture environment restoration removing an
  empty Git config value on Windows; the fixture now leaves unchanged environment
  entries untouched, and the complete rerun passed.
- Ruff, install contract, version consistency, headless launch, documentation
  consistency/references, generated payload, and vendored installation-context
  and shared-library synchronization gates passed. Marketplace isolation
  remains report-only (**728 findings**).
- The unchanged installer-readiness gate reproduced its known Windows
  JSON-stat baseline: **41 passed, 6 failed** with
  `JSON document changed while it was being read`. Docker is unavailable, so
  container clean-room scenarios were not run; this is native Windows evidence,
  not a native Linux or fresh-container installation claim.
- Initial implementation advanced Agent Index to **0.1.0-dev135**, including its
  source fallback and declaration version, and Agent Dispatch to **0.1.2-dev26**.
  Slice 3E subsequently merged as #2117, shipping Agent Index **0.1.0-dev136**.
  Namespaced host migration, independent warm-engine lifecycle, and multi-host policy remain
  outside this integration.

### 2026-09-05 — Effort closed and archived

- All five slices merged: declaration contract #2012, immutable materialization
  #2074, safe cutover #2099, retention #2115, and Agent Index integration #2117.
  Contract coverage and independent review are complete; the required local/CI
  suites and gates passed, with historical baseline failures retained above.
- Transferred unavailable clean-room provisioning and lifecycle validation to
  #2118. No fresh-container or clean-room execution is claimed by this closeout.
- Marked the effort Done, moved it to the dated archive, and removed it from the
  active index. Existing architecture and managed-companion-runtime pattern
  docs remain the durable behavior reference.
