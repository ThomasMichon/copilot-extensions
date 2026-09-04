# Dispatch-Owned Managed Companion Runtimes

- **Slug:** `dispatch-owned-managed-runtimes`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs
- **Created:** 2026-09-04
- **Status:** Draft
- **Vision:** [plugin-services/`delegated-heavy-companion-runtime`](../../../visions/plugin-services/README.md#delegated-heavy-companion-runtime);
  [agent-index/`lightweight-client-and-declared-host-service`](../../../visions/plugins/agent-index/README.md#lightweight-client-and-declared-host-service)
- **Umbrella issue:** #2007
- **Sub-issues:** Pending per-slice carving

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
- [ ] Land this effort proposal before implementation.

### Slice 3A — Declaration contract

- [ ] Carve and claim a contract-only sub-issue.
- [ ] Add the reusable managed-companion-runtime pattern and narrow the existing
  no-shared-infrastructure/self-provisioning invariants only for this explicit
  capability boundary.
- [ ] Extend attributed `plugin-companion` declarations with strict
  managed-runtime metadata while keeping the daemon non-provisioning.
- [ ] Allow only logical runtime identity, portable version/profile components,
  plugin-relative project inputs, bounded extras and validation imports, and
  named environment bindings.
- [ ] Reject arbitrary installer commands, package-manager flags, indexes,
  credentials, absolute runtime roots, traversal, links/reparse escapes, unknown
  fields, and direct or unattributed registration.
- [ ] Preserve plugin root, version, marketplace provenance, source declaration,
  and activation scopes in the desired runtime authority fingerprint.

**Completion gate:** dispatch can validate and retain managed-runtime intent, but
no package manager, environment builder, or live companion path consumes it.

### Slice 3B — Immutable materialization

- [ ] Carve and claim a materialization sub-issue only after Slice 3A lands.
- [ ] Resolve the physical runtime root and toolchain from dispatch-owned policy,
  never plugin-provided executable authority.
- [ ] Serialize every runtime root with a crash-safe interprocess lock shared
  across supervisor environments.
- [ ] Copy declared plugin-relative inputs into a root-contained immutable
  snapshot, build in unique staging, validate imports, and atomically publish a
  version/profile/content-digest cell.
- [ ] On Windows, select a trusted signed base Python, copy only the required
  runtime files, and verify executable trust before package installation.
- [ ] Reuse an already-valid published cell and preserve an existing cell when
  staging, installation, or validation fails.

**Completion gate:** only the running dispatch service can build or reuse a
validated immutable cell; publication does not yet replace a healthy companion.

### Slice 3C — Safe cutover

- [ ] Prepare and validate a replacement before stopping the current companion.
- [ ] Snapshot exact run, stop, and health argv plus environment against the
  selected immutable cell.
- [ ] Readiness-gate first launch and replacement; if replacement fails, restart
  the prior companion from its still-published cell.
- [ ] Prevent plugin disablement, provider uncertainty, or declaration churn
  from crossing runtime authority boundaries.

**Completion gate:** initial launch and updates use dispatch-owned cells with
prepare-before-stop and rollback to the prior published runtime.

### Slice 3D — Retention

- [ ] Protect active and rollback generations with identity-bound leases that
  remain legible across supervisor environments.
- [ ] Reclaim only cells that are unreferenced, unleased, ownership-valid, and
  outside bounded retention.
- [ ] Preserve foreign live leases and fail closed on ambiguous ownership,
  linked roots, malformed receipts, or unverifiable process identity.

**Completion gate:** bounded garbage collection cannot remove a live or
rollback-required runtime generation.

### Slice 3E — First plugin integration

- [ ] Move one configured host companion from legacy installed-runtime
  supervision to the generic managed-runtime contract.
- [ ] Keep unconfigured repositories, client roles, missing dispatch, and
  unsupported installation contexts inert.
- [ ] Prove that plugin commands and direct CLI calls cannot trigger companion
  package installation.

**Completion gate:** an opted-in host is turn-key through dispatch-owned
provisioning, while every non-host path stays lightweight and inert.

## Validation Plan

- [ ] Contract tests cover unknown fields, unsafe components, plugin-root
  escapes, provenance loss, direct registration, and non-execution in Slice 3A.
- [ ] Materialization tests cover concurrent builders, stale locks, unique
  staging, failed installs, failed validation, atomic publication, idempotent
  reuse, and Windows signed-base verification.
- [ ] Cutover tests cover first launch, prepare-before-stop, readiness failure,
  rollback, provider uncertainty, contributor disablement, and supervisor
  restart.
- [ ] Retention tests cover active and foreign leases, stale leases, rollback
  protection, bounded retention, root-lock serialization, and linked or
  mismatched cells.
- [ ] Integration tests prove missing runtime triggers dispatch provisioning
  only, while plugin-side self-provision paths remain disabled.
- [ ] Run the agent-dispatch and integrated plugin suites, repository guards,
  install-contract checks, and the applicable clean-room provisioning scenario
  for every behavior-changing slice.
- [ ] Require independent review of each slice's authority, containment,
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
