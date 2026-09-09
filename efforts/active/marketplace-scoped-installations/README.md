# Marketplace-Scoped Installations

- **Slug:** `marketplace-scoped-installations`
- **Repo:** copilot-extensions (PR-required `main`, self-merge)
- **Branch(es):** independent per-phase PRs; every implementation PR preserves
  Windows and POSIX compatibility or is an explicitly non-operative foundation
- **Created:** 2026-08-25
- **Status:** Active
- **Vision:** extends
  [`visions/plugin-services/installation-cells`](../../../visions/plugin-services/installation-cells/README.md)
  — §Features/`marketplace-scoped-runtime-and-state`,
  `source-neutral-installation-home`, `independent-lifecycle`,
  `cell-scoped-project-adoption`, `cell-local-invocation`,
  `attributable-agent-capabilities`, `provenance-safe-transition`; and the
  corresponding Behaviors.
- **Umbrella issue:** [#1096](https://github.com/ThomasMichon/copilot-extensions/issues/1096)
- **Implementation issues:** [#1102](https://github.com/ThomasMichon/copilot-extensions/issues/1102) ·
  [#1103](https://github.com/ThomasMichon/copilot-extensions/issues/1103) ·
  [#1104](https://github.com/ThomasMichon/copilot-extensions/issues/1104) ·
  [#1105](https://github.com/ThomasMichon/copilot-extensions/issues/1105) ·
  [#1106](https://github.com/ThomasMichon/copilot-extensions/issues/1106) ·
  [#1107](https://github.com/ThomasMichon/copilot-extensions/issues/1107) ·
  [#1108](https://github.com/ThomasMichon/copilot-extensions/issues/1108) ·
  [#1109](https://github.com/ThomasMichon/copilot-extensions/issues/1109) ·
  [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110)

## Guiding Intent

Make independently sourced marketplaces true installation boundaries. Two
marketplaces may ship the same plugin names and different runtime versions to
one user account without sharing mutable state, commands, services, endpoints,
registries, project-adoption records, or lifecycle ownership.

The durable host-level concept remains **copilot-extensions**, even though the
primary marketplace carries that same name. Each marketplace contributes an
independent installation cell beneath that concept. Generic plugin commands stay
with the payload that supplied the agent capability; machine-global command
space is reserved for attributable project entry points.

Installation cells are private infrastructure for the runtime-bearing core
plugin identities defined by the `copilot-extensions` suite. They are not a
general plugin facility. An independent source marketplace may carry a copy of
one of those core plugins for coexistence testing, but its unrelated plugins,
payload-only plugins, and other plugins that happen to expose tools or services
remain outside this namespacing model.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Cross-platform implementation driver | Shared contracts, sequencing, and independently green per-phase PRs | One active implementation worktree and serial PRs |
| Windows validation lane | Windows launch, payload replacement, task/pipe/mutex behavior, and clean-room validation | Isolated Windows validation before operative contracts become mandatory |
| Linux/WSL validation lane | POSIX shims, filesystem/service behavior, systemd/socket behavior, and clean-room validation | Isolated Linux/WSL validation per operative phase |

## Coordination

- **Topology:** independent per-phase PRs, sequenced by this effort and #1096.
- **Host (owns sequencing):** the active cross-platform implementation driver;
  Phase 1 is owned by the Linux/WSL lane.
- **Delegates:** Windows and Linux/WSL validation remain required for operative
  phases; either lane may own a shared-library PR after recording it in the
  journal.
- **Handoff:** each PR is independently green and leaves both operating-system
  lanes on a compatible contract. A platform-specific implementation may follow
  in the next PR only when the preceding PR is non-operative foundation and
  cannot change behavior on either platform.
- **Execution boundary:** namespaced installation and usage are exercised only
  in disposable clean-room environments for the duration of this effort.
  Persistent development and production machines remain in legacy mode and
  receive no namespaced activation, runtime, state, service, or migration.

## Context

The current suite installs each runtime below an unqualified `~/.agent-*` root,
publishes generic `agent-*` binstubs to `~/.local/bin`, and uses global service
names, endpoint locations, provider registries, and project-adoption state.
Several plugins also discover siblings through ambient `PATH` or scans of all
installed marketplace payloads. Consequently, installing a same-named plugin
from a second marketplace can overwrite or attach to the first installation.

The existing versioned-runtime, self-provisioning, endpoint-rendezvous,
drop-in-registry, and project-binstub systems are reusable foundations. This
effort changes their ownership boundary rather than replacing them.

Detailed architecture, migration rules, and the affected-system inventory live
in [`design.md`](design.md).

## Request

Allow public, private, local-directory, and other independently sourced
marketplaces to provide same-named copilot-extensions systems without accidental
cross-installation linkage. Runtime location should derive from marketplace
payload provenance at install-from-payload time. Generic agent tool shims should
live in their owning payload, while project binstubs may remain globally
reachable when their ownership is explicit.

The capability remains explicitly opt-in and default-off. During this effort,
all namespaced install, activation, runtime use, lifecycle, migration, and
coexistence testing occurs in disposable clean-room environments; no persistent
machine is an activation or dogfood venue. The implementation applies only to
runtime-bearing core plugin identities from the `copilot-extensions` suite and
must not namespace unrelated downstream, internal, or third-party plugins merely
because they provide tools or services.

## Operating Constraints

- **Opt-in only:** absent policy and every implicit default preserve legacy
  operation. Repository content, marketplace payloads, installers, bootstrap,
  reconciliation, or first use must never enable namespaced mode.
- **Clean-room only:** namespaced cells may be created, activated, run, updated,
  rolled back, repaired, migrated, or removed only inside disposable clean-room
  environments while this effort is active. Persistent machines may perform
  read-only status/doctor checks but must remain locally unused.
- **Suite-private scope:** only runtime-bearing core plugins shipped by the
  `copilot-extensions` suite participate. A second source marketplace may carry
  those plugin identities for isolation proof; payload-only plugins and every
  unrelated plugin in any marketplace remain outside the cell resolver and may
  not gain namespaced state, commands, services, or lifecycle ownership through
  this effort.

## Plan

### Phase 0 — Intent and effort adoption

- [x] Establish #1096 as the public coordination token.
- [x] Add the Marketplace Installation Cells child vision and clarify
  `copilot-extensions` as the durable, source-neutral installation-home concept.
- [x] Enable the visions and efforts plugins for this repository and complete
  the repo-local efforts addendum.
- [x] Record the target design, affected systems, migration boundary, and
  Windows/Linux participant model in this effort.

### Phase 1 — Contract and inventory ([#1102](https://github.com/ThomasMichon/copilot-extensions/issues/1102))

- [x] Add the prescriptive marketplace-installation-cell pattern and revise the
  install/configuration contracts without changing runtime behavior.
- [x] Define the marketplace provenance, installation identity, ownership
  receipt, repo identity, and process-propagation contracts.
- [x] Add report-only guards inventorying unqualified runtime roots, generic
  global plugin binstubs, PATH-based sibling launches, fixed service identities,
  and bare agent-operative command instructions.
- [x] Split #1096 into reviewable implementation issues citing exact vision
  items and phase ownership.

### Phase 2 — Payload-local invocation ([#1103](https://github.com/ThomasMichon/copilot-extensions/issues/1103))

- [x] Add checked-in, payload-local POSIX/PowerShell/CMD shims generated from
  canonical templates.
- [x] Add session-start command-catalog context so skills and agents receive the
  exact payload-owned invocation path; convert operative bare command examples.
- [ ] Stop installing generic `agent-*` commands into `~/.local/bin`; retained
  service, provider, remote, scheduled, startup, and deployment boundaries still
  require attributable external-launch contracts before their compatibility
  wrappers can be retired. The
  [Phase 2 launcher contract inventory](phase-2-launcher-contracts.md) accounts
  for all 86 guard-visible findings, records known guard-invisible callers, and
  maps their Phase 2, Phase 3, Phase 4, and Phase 6 dependencies.
  - [x] Preserve complete default-legacy fallback coverage while migration is
    incomplete: every runtime `agent-*` stamp publishes every declared payload
    command, and agent-logger's multi-command family delegates through durable
    owning-payload snapshots.
- [x] Make project binstubs pin their owning payload and reject silent ownership
  transfer.

### Phase 3 — Installation context and exemplars ([#1104](https://github.com/ThomasMichon/copilot-extensions/issues/1104))

- [x] Land the reviewed
  [installation-context and dual-cell proposal](phase-3-installation-context.md)
  before either platform makes the new root operative.
- [x] Introduce a self-contained, vendorable installation-context primitive
  separate from versioned interpreter resolution.
  - [x] Land the non-operative Windows/PowerShell resolver, receipt validator,
    portable source-identity fixtures, and CI tests.
  - [x] Add the corresponding Python/POSIX primitive and prove fixture parity
    before any runtime root becomes operative.
  - [x] Add cross-platform receipt stamping, lock ownership, generation
    compare-and-swap, and inert exemplar vendoring.
  - [x] Make Agent Machines, Agent Index, and agent-worktrees reconciliation
    inspect an explicitly selected, validated deploy manifest without activating
    or mutating the namespaced runtime root.
  - [x] Implement the reviewed
    [user-local installation-mode governance](installation-mode-governance.md):
    OS-profile-pinned default legacy policy, exact marketplace/plugin overrides,
    sticky actual mode, the normative read-only resolver/status contract, and
    the non-mutating legacy-entrypoint decision probe.
  - [x] Add the tiny shared legacy-entrypoint probe and complete declared
    path/service/task footprints for each exemplar; no exemplar becomes
    operative until every legacy installer/bootstrap mutation refuses
    namespaced-active, orphaned-transfer, and maintenance.
  - [x] Prove activation CAS pins namespace, install, and activation generations
    and that Windows/WSL/POSIX receipts fail closed outside their exact
    environment.
- [x] Persist and validate marketplace, plugin, payload, runtime, and instance
  identity through stamp, snapshot, provision, cutover, rollback, and uninstall.
  - [x] Publish and independently validate immutable, generation-pinned snapshot
    provenance at the exact cell-local snapshot path without creating or
    activating a runtime slot.
  - [ ] Carry validated snapshot identity into provision/runtime-slot ownership,
    cutover, rollback, and uninstall.
    - [x] Establish the non-activating Python reference for immutable,
      generation-pinned runtime-slot ownership.
    - [x] Add dependency-light Bash and PowerShell parity before installer or
      bootstrap adoption.
    - [x] Add explicit non-activating Agent Machines and Agent Index installer
      adapters that require a caller-supplied context and marketplace id,
      bind snapshot provenance to the exact installer payload root/version,
      bypass legacy mutation, and reserve or validate only the payload version's
      empty owned slot.
      - [x] Make Agent Machines the command-only operative exemplar: payload
        invocation and bootstrap accept only an already-active validated cell,
        namespaced first use/update publish owned build completion and cut over
        cell-local runtime markers, and fixed-identity cutover supports historical
        rollback without legacy fallback.
      - [x] Add ownership-checked Agent Machines repair/release and uninstall
        ([#2122](https://github.com/ThomasMichon/copilot-extensions/issues/2122)).
- [x] Prove one on-demand plugin and one service-bearing plugin with two
    simultaneous marketplace cells in disposable clean-room environments before
    broad rollout. Do not activate or use either exemplar namespaced on a
    persistent machine.
    - [x] Add a deterministic cross-platform Tier-P Agent Machines scenario for
      two cells, isolated update/rollback, blocked governance states, and
      unrelated/payload-only eligibility negatives.
    - [x] Implement the Agent Index service-bearing exemplar with
      cell-local runtime, durable state, routing, endpoints, launchers, and
      lifecycle identity, plus a deterministic dual-cell Tier-P scenario for
      concurrent service, isolated update/rollback, fail-closed foreign control,
      and isolated shutdown.
    - [x] Harden Agent Index lifecycle transactions: atomic read drain
      admission, promotion-gated passive services, durable marker+manifest
      recovery, transaction-only namespaced deploy/recovery, exact instance-token
      controls, ownership-attested orphan reconciliation, stale-lock claiming,
      nonblocking session ensure, and PowerShell 5.1-safe launchers.
    - [x] Accept Agent Index as the operative service-bearing exemplar after the
      full Linux lifecycle passes. Smoke mode is a fast diagnostic only, not the
      acceptance lane; the Windows arm was explicitly waived for this effort.
      - [x] Pass the full Linux lifecycle: two live cells, isolated update and
        rollback, all four cutover crash-recovery phases, governance-blocked
        restoration, foreign-control refusal, isolated shutdown, and clean
        cleanup. The implementation landed through
        [#1880](https://github.com/ThomasMichon/copilot-extensions/pull/1880)
        and the first acceptance defects were fixed through
        [#2080](https://github.com/ThomasMichon/copilot-extensions/pull/2080);
        the committed-crash restoration defect was fixed through
        [#2089](https://github.com/ThomasMichon/copilot-extensions/pull/2089),
        and the final shutdown-liveness defect was fixed through
        [#2100](https://github.com/ThomasMichon/copilot-extensions/pull/2100).
      - [x] Windows clean-room arm waived by operator decision on 2026-09-05.
        Earlier attempts stopped before scenario execution because the local
        engine ran Linux containers and the remote Windows-agent launch failed.
    - [x] Run the Agent Machines scenario in a disposable Linux clean-room arm.
    - [x] Windows Agent Machines clean-room arm waived by operator decision on
      2026-09-05; deterministic PowerShell parity tests remain required.

### Phase 4 — Runtime and state rollout

- [x] Convert agent-worktrees and its project/repo registries first
  ([#1105](https://github.com/ThomasMichon/copilot-extensions/issues/1105)) so later
  reconciliation and project entry points are attributable.
  - [x] Runtime, global registries, plugin-global mutable state, stable
    repository identity, project config/tracking/session records, hooks,
    launchers, and project-command arbitration are installation-attributable.
  - [x] Blocked; transferred to `#1110`
    ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1110);
    transfer completed):
    cell-qualified Git-ref leases require the Phase 6 maintenance/migration
    gate so old and new clients cannot hold split-brain leases during version
    skew.
  - [x] Deferred to `#1108`
    ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1108);
    deferral completed):
    Worktree Manager supervision and any remaining fixed service/process
    identity belong to the service-bearing rollout.
  - [x] Deferred to `#1107`
    ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1107);
    deferral completed):
    remote consumers must carry the selected installation and repository
    identity across venue/transport boundaries.
- [x] Convert service-free runtimes in low-risk batches
  ([#1106](https://github.com/ThomasMichon/copilot-extensions/issues/1106)).
  - [x] Transferred to `#1107`
    ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1107);
    transfer completed):
    agent-ssh's managed OpenSSH fragments, dtssh companion, dispatch registrar
    drop-ins, and remote host restoration are transport-boundary work rather
    than low-risk service-free runtime state.
- [x] Convert remote venue and transport plugins, carrying installation identity
  through SSH, CodeSpace, container, and staged-plugin boundaries
  ([#1107](https://github.com/ThomasMichon/copilot-extensions/issues/1107)).
- [ ] Convert service-bearing plugins, qualifying service, lease, endpoint,
  provider, log, and process identity
  ([#1108](https://github.com/ThomasMichon/copilot-extensions/issues/1108)).

### Phase 5 — Repository configuration and adoption state ([#1109](https://github.com/ThomasMichon/copilot-extensions/issues/1109))

- [ ] Move committed plugin configuration toward
  `.copilot-extensions/<plugin>/...` with new-first, legacy-fallback reads.
- [ ] Keep committed repository policy distribution-neutral; require an explicit
  overlay for genuinely marketplace-specific behavior.
- [ ] Move machine-local project state beneath the adopting installation cell,
  keyed by stable remote identity rather than repository basename alone.

### Phase 6 — Migration, enforcement, and cleanup ([#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110))

- [ ] Add user-wide and plugin-scoped parser-free maintenance gates with strict
  ownership sidecars, explicit management-command authorization, draining lease
  behavior, stale-owner diagnostics, and fail-safe remote maintenance probing.
- [ ] Provide explicit legacy-state attribution/migration under the legacy
  lock/lease and cell install lock; publish the ownership tombstone and
  generation-pinned activation without an observable mixed-writer interval.
- [ ] Add explicit rollback/deactivation that publishes a monotonic
  legacy/deactivated activation before clearing the tombstone under both locks.
  Reserve activation deletion for locked cleanup after companion evidence is
  gone.
- [ ] Make every long-running legacy and namespaced loop recheck maintenance,
  tombstone ownership, and activation/install generations at iteration
  boundaries and before mutation.
- [ ] Migrate or retire legacy services and global generic binstubs only after
  ownership is proven and the new cell passes health checks.
- [ ] Turn the report-only guards blocking after all runtime plugins conform.
- [ ] Document rollback and retention of legacy state and inactive cells.

### Phase 7 — Reconcile deferred backlog

- [ ] Accept installation and marketplace candidates only through
      [`migration-intake`](../migration-intake/README.md)'s deduplication and
      ownership gate.
- [ ] Revalidate accepted technical scope against the current installation-cell
      contract; return obsolete or unsafe candidates for explicit disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
      extending this plan before implementation when necessary.
- [ ] Keep examples synthetic and distribution-neutral.

## Validation Plan

- [ ] At each operative phase boundary, use read-only status/doctor checks to
  confirm every persistent development and production machine remains in legacy
  mode with no namespaced activation or cell-owned runtime/service state.
- [ ] Exercise every namespaced install, activation, runtime use, service start,
  update, rollback, repair, migration, and uninstall path only in disposable
  clean-room environments. Unit fixtures may model cells but may not create
  host-local namespaced state.
- [ ] Add negative coverage proving payload-only plugins and plugins from
  downstream, internal, or third-party plugin families never resolve, create,
  activate, or consume `copilot-extensions` installation cells, even when those
  plugins share a source marketplace with a core suite plugin or expose
  executable tools or long-running services.
- [ ] Run two marketplace cells containing the same plugin name and version
  concurrently on Windows and Linux/WSL.
- [ ] Repeat with different versions and concurrent stamp/provision/update
  operations.
- [ ] Assert no overlap in runtime, durable state, cache, logs, endpoints,
  providers, leases, service identities, or project-adoption records.
- [ ] Assert payload-local shims dispatch only to their own version marker and
  never resolve a sibling through ambient `PATH`.
- [ ] Assert project binstub ownership conflicts fail without overwriting the
  incumbent wrapper.
- [ ] Assert endpoint and provider identity mismatches are rejected before
  dialing or launching.
- [ ] Exercise installed marketplace, directory marketplace, staged
  `--plugin-dir`, local checkout, Windows, Linux, WSL, and remote execution
  provenance.
- [ ] Verify concurrent Windows payload update does not fail because a shim
  retains CWD or file handles inside the replaceable payload.
- [ ] Prove migration is idempotent, rollback-safe, and refuses ambiguous legacy
  ownership.
- [ ] Add a two-marketplace clean-room acceptance scenario and make the static
  inventory guards blocking.

## Proposal

See [`design.md`](design.md).

## Journal

### 2026-08-25 — Kickoff

- #1096 and the Marketplace Installation Cells child vision established the
  public intent and coordination boundary.
- A suite-wide audit identified unqualified runtime roots, global plugin
  binstubs, service/endpoint/provider collisions, PATH-based sibling capture,
  global project registries, and hardcoded remote paths as the principal
  cross-marketplace contamination routes.
- Decided that generic plugin shims live in their immutable owning payload.
  Skills and injected context address those shims directly. Only attributable
  project entry points remain in `~/.local/bin`.
- Approved `~/.copilot-extensions/marketplaces/<marketplace-id>/` as the durable
  installation-cell root, with plugin runtimes under `plugins/` and
  marketplace-owned project state under `repos/`.
- Bound the effort to paired Windows and Linux/WSL implementation lanes with
  independently green, sequential PRs.

### 2026-08-25 — Phase 1 execution

- Continued sequencing in the Linux/WSL lane after the original Windows host
  was unavailable. Operative phases still require explicit Windows validation;
  the lane change does not weaken the cross-platform gate.
- Split #1096 into #1102–#1110, covering the Phase 1 contract/inventory,
  payload-local invocation, installation context and exemplars,
  agent-worktrees adoption state, service-free runtimes, remote
  venues/transports, service identities, repository configuration, and
  migration/enforcement.
- Started #1102 with a prescriptive marketplace-installation-cell pattern,
  install/configuration contract revisions, and a report-only inventory guard.
- The first inventory baseline scans 900 operative files and reports 1,346
  findings: 380 unqualified runtime roots, 87 global plugin-binstub surfaces,
  74 PATH-based sibling launches, 88 fixed service identities, and 717
  operative bare commands. The guard remains non-blocking until the producing
  phases burn down those categories.
- Started Phase 2 with a non-breaking payload-invocation foundation and an
  agent-index pilot: canonical POSIX/PowerShell/CMD generation, checked-in
  payload shims, a session command catalog carrying exact `argv`, and operative
  skill guidance that no longer relies on ambient command lookup. The legacy
  global wrapper remains a compatibility surface until explicit management
  context is available for out-of-session callers.
- The first Phase 2 pilot merged in
  [#1120](https://github.com/ThomasMichon/copilot-extensions/pull/1120).
  The next serial slice moved command-catalog generation into the shared
  payload-invocation templates and added an agent-worktrees payload-only command
  under `bin/payload/`, leaving its historical top-level wrapper available for
  legacy global deployment until project-command ownership migration lands.
- That shared-catalog slice merged in
  [#1123](https://github.com/ThomasMichon/copilot-extensions/pull/1123), with
  native Windows validation covering nested shims and catalog emitters on the
  final review head.
- The next service-free batch merged in
  [#1127](https://github.com/ThomasMichon/copilot-extensions/pull/1127), adding
  payload-local commands and operative catalog guidance for agent-machines and
  agent-ssh. Shared generator hardening made installer selection
  manifest-driven and fail-open catalogs explicit; native Windows validation
  also closed PSMux ancestry, PATH repair, and SSH ACL defects exposed by the
  final head.
- The next remote-venue batch merged in
  [#1128](https://github.com/ThomasMichon/copilot-extensions/pull/1128), adding
  a payload-local agent-containers command and converting its agent-facing
  container operations to catalog invocation. The following agent-codespaces
  slice corrects its bridge-dispatch examples back to the explicit
  agent-bridge management command; bridge provider registration and dispatch
  have not yet adopted session catalogs.
- The agent-codespaces slice merged in
  [#1129](https://github.com/ThomasMichon/copilot-extensions/pull/1129), adding
  payload-local lifecycle commands and catalog guidance while preserving the
  bridge provider, connection owner, scheduled work, and remote launchers as
  explicit management boundaries. Linux and native Windows validation covered
  the final review head. The same validation exposed a fallback provisioning
  lock race, tracked separately in
  [#1132](https://github.com/ThomasMichon/copilot-extensions/issues/1132).
- The agent-logger slice merged in
  [#1135](https://github.com/ThomasMichon/copilot-extensions/pull/1135), extending
  the payload-invocation manifest to multiple commands and moving six
  agent-facing logger entry points to exact catalog argv. Scheduled sync,
  installer management, and far-side SSH launches remain explicit management
  boundaries.
- The agent-mcp slice merged in
  [#1147](https://github.com/ThomasMichon/copilot-extensions/pull/1147), moving
  agent-facing shell operations to a payload-local command while preserving
  static `mcp-servers.command` and generated materialized fleets as explicit
  startup and management compatibility boundaries.
- The agent-vault slice merged in
  [#1150](https://github.com/ThomasMichon/copilot-extensions/pull/1150), moving
  agent-facing vault operations to a payload-local command while preserving
  installer/service actions, Git credential-helper registration, and
  `vault-askpass` as explicit out-of-session management boundaries.
- The agent-dispatch slice merged in
  [#1153](https://github.com/ThomasMichon/copilot-extensions/pull/1153), moving
  interactive queue operations and generated focus guidance to a payload-local
  command while preserving service/supervisor, scheduler/webhook, picker,
  remote, startup-seed, provider, and static MCP launchers as explicit
  compatibility boundaries.
- The agent-bridge slice merged in
  [#1162](https://github.com/ThomasMichon/copilot-extensions/pull/1162), moving
  interactive bridge operations and dependent CodeSpace/container dispatch
  guidance to a payload-local command while preserving service, deployment,
  elevated, picker, remote, and provider launchers as explicit boundaries.
- The agent-worktrees guidance slice merged in
  [#1169](https://github.com/ThomasMichon/copilot-extensions/pull/1169), moving
  direct lifecycle, repository, collaboration, repair, setup, and cross-repo
  operations to the payload catalog while keeping project commands and
  deployment verification as explicit entry-point boundaries.
- The shared-skill cleanup merged in
  [#1172](https://github.com/ThomasMichon/copilot-extensions/pull/1172), moving
  current-session calls in payload-only plugins to the runtime catalogs while
  preserving handoff seeds, launch preflight, deployed-runtime diagnostics,
  materialized MCP fleets, and clean-room commands as explicit boundaries.

### 2026-08-26 — Phase 3 proposal resumed

- Kept the active Linux/WSL lane on Phase 2 payload-local invocation and moved
  shared architecture work to the non-overlapping Phase 3 proposal.
- Selected agent-machines as the CLI-only exemplar and agent-index as the
  service-bearing exemplar: both already have payload-local commands, while
  together they exercise simple runtime placement, durable state, endpoint
  publication, service identity, update, and rollback.
- Defined the pre-runtime bootstrap boundary: the payload-local shim can derive
  an installed marketplace slot from its own payload boundary without Python or
  a global command; management surfaces may enrich that identity with a
  normalized source fingerprint, but never silently remap an occupied slot.

### 2026-08-26 — Non-operative Windows foundation

- Added the canonical installation-context library's Windows slice: portable
  source-identity vectors, a PowerShell 5.1+/pwsh resolver and strict receipt
  validator, and focused CI coverage.
- Kept the slice read-only. It computes source-derived cells and durable paths,
  fails closed on missing or conflicting provenance, and reports explicit
  rebind requirements without creating or activating any runtime state.
- Left Python/POSIX parity, vendoring, receipt mutation, locking, runtime-root
  activation, and the two exemplars to later Phase 3 slices.

### 2026-08-26 — Non-operative Python/POSIX parity

- Added the stdlib-only Python installation-context API and a Bash/awk bootstrap
  that does not require Python or `jq`.
- Ran the canonical source vectors and read-only resolution, path, rebind, and
  receipt-validation behavior across PowerShell, Python, and POSIX entry points.
- Kept the primitive non-operative: it computes and validates context without
  creating cells, receipts, locks, runtime roots, or payload state.
- Left vendoring, receipt mutation, locking/CAS, runtime-root activation,
  reconciliation, and both exemplars to later Phase 3 slices.

### 2026-08-27 — Non-operative receipt mutation and vendoring

- Added one cross-platform `stamp` contract for atomic namespace and plugin
  receipt creation/update. Existing mutations require caller-observed
  generations while holding attributable genesis/install directory locks.
- Added live-owner receipts, bounded same-host wait, fail-closed stale-owner
  detection, lock-token revalidation before replacement, and concurrent first-use tests
  across PowerShell, stdlib Python, and the no-Python Bash bootstrap.
- Added byte-identical vendoring into the future `agent-machines` and
  `agent-index` exemplar payloads plus a CI sync gate.

### 2026-08-27 — Windows handoff: activation-governance specification

- Took the Windows-side handoff after cross-platform resolver parity and receipt
  mutation locking landed.
- Specified OS-profile-pinned
  `~/.copilot-extensions/installation-mode.json` as the default-off user policy,
  with source-derived marketplace and extensible exact-plugin overrides.
- Moved the exact policy, `installation-activation.json`, legacy ownership
  tombstone, resolver/status, and effective-mode contracts into
  [`docs/install-contract.md`](../../../docs/install-contract.md#installation-mode-governance).
- Required two-lock migration, generation-pinned activation CAS, explicit
  legacy footprint metadata, environment isolation, and a shared pre-mutation
  probe across every legacy installer/bootstrap entrypoint.
- Specified user-wide and plugin-scoped maintenance markers with strict
  ownership sidecars so active machines can drain and be updated surgically
  over SSH.
- Kept the slice specification-only and non-operative: no policy reader,
  activation/tombstone writer, maintenance command, footprint probe, installer
  gate, or exemplar cutover is claimed implemented by these documentation
  changes.

### 2026-08-27 — Cell-aware reconciliation prerequisite

- Added explicit receipt-selected deploy-manifest inspection to the Agent
  Machines and Agent Index bootstrap checks on POSIX and PowerShell. A context
  for another plugin leaves legacy reconciliation unchanged; malformed or
  matching-invalid evidence fails closed without stamping or invoking a legacy
  installer.
- Made agent-worktrees reconciliation and operator update paths compare the
  selected namespaced manifest but report missing/drifted context runtimes as
  diagnostic-only. Namespaced roots remain read-only until activation governance
  and context-aware installers land.
- Surfaced reconciliation diagnostics through detached provision checks and
  both worktree launchers, while preserving executable legacy updates for
  unrelated plugins.
- Tightened PowerShell receipt parsing and identity comparison to reject
  duplicate, case-conflicting, and case-mismatched identities, then synchronized
  Agent Machines, Agent Index, and the Agent Worktrees management copy.
- Kept agent-worktrees project state, activation policy, service identity,
  migration, and runtime-root mutation unchanged.

### 2026-08-27 — Non-operative activation-governance prerequisite

- Added cross-platform read-only `status` and `probe-legacy` actions to the
  canonical installation-context primitive, including OS-profile policy
  precedence, exact environment binding, activation/tombstone validation,
  maintenance diagnostics, stable reasons, and deterministic probe exit codes.
- Added fixture-backed Python, no-Python POSIX, and PowerShell parity coverage
  for clean pre-activation, active and deactivated receipts, changed
  generations, foreign environments, ownership tombstones, maintenance,
  status precedence, probe decisions, and read-only filesystem behavior.
- Kept the slice non-operative: no activation or tombstone writer, two-lock
  migration, installer/bootstrap caller wiring, declared exemplar footprint,
  payload-invocation change, runtime-root switch, or cutover is implemented.

### 2026-08-27 — Legacy exemplar mutation gating

- Added dependency-light POSIX and PowerShell callers that derive conservative
  path, systemd-user service, and Windows scheduled-task evidence from each
  payload's declared legacy footprint before invoking the canonical
  `probe-legacy` decision.
- Declared complete legacy footprints for the agent-machines CLI-only exemplar
  and the agent-index service-bearing exemplar, including compatibility shims,
  unit files, service identities, and scheduled-task identities.
- Wired every direct installer, bootstrap reconciler, and agent-index service
  ensure boundary before its first mutation or background process launch.
  Self-staged children retain the original payload as provenance, deferred
  Windows snapshots publish that attributable origin through a serialized,
  crash-consistent first-use receipt, and POSIX first-use binstubs probe before
  creating lock or status files.
- Kept malformed footprint metadata conservative, canonically validated an
  inherited context before treating it as another plugin's context, and kept
  agent-index `status` read-only by bypassing its mutating self-stage path.
- Validated the callers with Windows PowerShell 5.1, including native scheduled
  task detection and pre-mutation refusal for namespaced-active, maintenance,
  and orphaned-transfer decisions.
- Kept namespaced runtimes non-operative: this slice adds refusal coverage only;
  it does not write activation, tombstone, maintenance, or namespaced runtime
  state.

### 2026-08-27 — Windows installation-governance clean-room proof

- Added a Tier-P Windows scenario that runs the real PowerShell 5.1
  installation-mode resolver inside a disposable Hyper-V-isolated Windows
  container.
- The scenario covers absent policy, authoritative plugin precedence with
  pre-activation legacy pinning, migration-required legacy state, sticky active
  namespaced state, orphaned ownership transfer, stale maintenance, and the
  read-only filesystem invariant.
- The formal Windows-container arm runs on a dedicated Windows-container host;
  host-side execution remains a fast compatibility probe rather than the
  acceptance proof.
- The formal Hyper-V-isolated Windows-container run passed against commit
  `05235922940fa10eb2ee86ce357db61077680fb1`: 16 assertions passed, zero
  failures, zero jams, and phases 0–6 were represented. The retrieved report's
  SHA-256 was
  `4573A0180814CDF5FB87E1CA2B9A6B8D03A195F3DF53388CBF2BEFD5F75BC4AA`.

### 2026-08-27 — Explicit activation CAS proof

- Added one explicit `activation-cas` transaction across stdlib Python,
  no-Python Bash, and PowerShell 5.1+/pwsh. It acquires the marketplace genesis
  lock before the plugin installation lock, revalidates both context receipts,
  and publishes only when the caller-observed namespace, install, and
  activation generations still match.
- Made stale generations return `revalidation-required` without replacement,
  refused malformed or foreign-environment activation receipts without
  overwriting them, and kept the generation within the portable signed 64-bit
  range.
- Proved exact Windows, native POSIX, and per-distribution WSL environment
  binding, atomic contention winners, byte-for-byte mismatch preservation, and
  post-publication resolver readiness across all three entry points. Tightened
  lock acquisition and just-released-owner handling under contention while
  preserving fail-closed genuine stale-owner diagnostics.
- Kept activation non-automatic: no exemplar installer, bootstrap, payload
  invocation, migration, or runtime launcher calls the primitive. Tombstone
  writing, runtime-root cutover, and dual-cell exemplar operation remain later
  slices.

### 2026-08-26 — Runtime plugin hook audit

- Audited every runtime-bearing `agent-*` marketplace plugin against the
  [bootstrap/glossary matrix](agent-plugin-hook-audit.md).
- Confirmed ten plugins already had complete generated shims, attributable
  command-catalog hooks, and bootstrap hooks. agent-bridge was the sole
  bootstrap-only gap; added its generated payload command and glossary without
  changing runtime roots or service/provider ownership.
- Kept the bridge glossary static: command ownership plus, at most, stable
  machine/repository breadcrumbs. Worktrees and sessions remain live queries
  because an initial-context snapshot would stale immediately.
- Added a roster-wide guard so a future runtime `agent-*` plugin cannot land
  without both-platform bootstrap and glossary wiring.

### 2026-08-26 — Phase 2 ownership and closure audit

- Project-command ownership merged in
  [#1178](https://github.com/ThomasMichon/copilot-extensions/pull/1178).
  Project launchers now pin the payload that created them, carry owner/project
  identity plus exact launcher hashes in receipts, serialize registration and
  reconciliation, preserve unreceipted or modified commands, and require an
  explicit transfer operation before replacement.
- Re-ran the runtime roster, catalog-adopter, and report-only isolation guards.
  All runtime plugins retain their bootstrap and command-glossary wiring, and
  catalog adopters have no unmarked bare agent commands.
- Kept Phase 2 and #1103 open: the isolation inventory still reports 86
  global-plugin-binstub surfaces across 14 plugins. These are the intentionally
  retained external management boundaries; removing them before they receive
  attributable launch contracts would break out-of-session callers rather than
  isolate them.

### 2026-08-26 — Phase 2 launcher dependency map

- Classified all 86 remaining global-plugin-binstub findings in the
  [launcher contract inventory](phase-2-launcher-contracts.md).
- Identified six payload-owned agent-ssh wrapper findings that can move directly
  to their own generated payload command in Phase 2.
- Bound durable provider manifests, remote transport, persisted callbacks, and
  cross-plugin bootstrap to the Phase 3 installation-context and canonical
  launcher contract.
- Kept generic wrapper removal in Phase 6, after cell-local runtime rollout,
  ownership attribution, health proof, and rollback protection. Documentation
  cleanup and the six immediate findings do not make #1103 complete.

### 2026-08-26 — Payload-owned SSH wrappers

- Merged [#1187](https://github.com/ThomasMichon/copilot-extensions/pull/1187),
  moving the `emit-profile` and `verify` compatibility wrappers on POSIX and
  PowerShell from the global `agent-ssh` binstub to their own payload-local
  generated command.
- Added focused cross-platform wrapper tests proving a same-named global shadow
  is not selected.
- Reduced the guard-visible global-plugin-binstub baseline from 86 to 80.
  Durable provider, remote, bootstrap, callback, credential, service, and
  wrapper-retirement contracts remain open, so Phase 2 and #1103 remain active.

### 2026-08-27 — Default-legacy command fallback restored

- Merged [#1251](https://github.com/ThomasMichon/copilot-extensions/pull/1251)
  after reports that agent commands were missing from `PATH`, especially the
  agent-logger auxiliary command family.
- Added a roster-wide contract that runs every runtime `agent-*` plugin's cheap
  stamp under absent/default installation-mode policy and requires a global
  compatibility fallback for every command declared by
  `payload-invocation.json`.
- Agent-logger now publishes all six commands during stamp. Its five auxiliary
  wrappers resolve an immutable versioned payload snapshot and delegate to that
  payload's generated command shim, so ambient `PATH` cannot redirect ownership
  and first-use provisioning remains attributable. Provision/install/update
  preserve the same wrappers instead of replacing them with direct-runtime
  links.
- Snapshot publication is shared across stamp and provision, uses the
  self-staged payload rather than the replaceable marketplace singleton, reuses
  complete same-version snapshots without removing a live command source, and
  was validated after deleting the original payload.
- The compatibility contract remains deliberately one-way: absent/default
  policy keeps legacy wrappers; only a validated namespaced-active result from
  the shared resolver may suppress and ownership-safely retire them. The active
  #1104 resolver slice remains independent and unmodified.
- Validation covered 196 agent-logger tests (1 skipped), 48 shared
  payload-invocation tests (8 skipped), native Windows stamp/provision behavior,
  all install/version/generated/isolation gates, independent design and code
  reviews, and green PR CI. The unrelated installation-context concurrent
  first-stamp diagnostic race recurred once and passed on rerun; it remains
  tracked by [#1228](https://github.com/ThomasMichon/copilot-extensions/issues/1228).

### 2026-08-28 — Snapshot provenance identity

- Added explicit `snapshot-stamp` and `snapshot-validate` actions to the
  canonical Python, dependency-light POSIX, and PowerShell installation-context
  runners.
- Made the sidecar immutable at
  `<snapshotsRoot>/<snapshot-id>/snapshot-provenance.json`, with normalized
  source/fingerprint, marketplace/plugin identity, originating payload
  metadata, canonical receipt references, and pinned namespace/install
  generations.
- The producer holds both receipt locks in canonical order; the consumer
  independently revalidates the receipt chain and rejects stale, copied,
  malformed, unsupported, escaping, or cross-cell evidence without overwriting
  it.
- Kept the slice non-operative: no version slot, activation, migration,
  tombstone, cutover, rollback, or uninstall behavior is created. Provisioning
  and later lifecycle ownership remain unchecked.

### 2026-08-28 — Python runtime-slot ownership reference

- Added explicit Python `slot-provision` and `slot-validate` transactions that
  revalidate the context receipt and snapshot provenance under both receipt
  locks, then atomically reserve one exact cell-local runtime slot with an
  immutable `.runtime-slot-ownership.json` marker.
- Bound the marker to marketplace, plugin, source fingerprint, runtime version,
  snapshot root/provenance and provenance digest, canonical receipt paths, and
  pinned generations.
  Existing markerless, malformed, copied, linked, stale, or conflicting slots
  fail without replacement; matching ownership is idempotent.
- Preserved rollback viability across later receipt generations: new slots
  require current active snapshot provenance, while existing slots validate
  against their immutable snapshot and stable cell identity and reject
  generation regression. Atomic no-replace publication preserves any
  concurrently appearing slot, with hidden staging kept outside
  `versionsRoot` so existing version enumeration cannot observe it.
- Kept the reference non-activating and unadopted. It does not write runtime
  payloads, completion/current/LKG markers, activation receipts, launchers,
  services, state, or tombstones. Bash/PowerShell parity and installer wiring
  remain required before runtime-slot provisioning becomes operative.

### 2026-08-28 — Dependency-light runtime-slot parity

- Added equivalent `slot-provision` and `slot-validate` actions to the Bash and
  PowerShell runners, including strict portable runtime-version validation,
  exact immutable ownership validation, nested receipt-defined versions roots,
  lexical link/reparse rejection, historical owned-slot validation across
  receipt advances, and generation-regression rejection.
- Preserved no-clobber publication with runner-appropriate primitives:
  PowerShell stages outside `versionsRoot` and uses an OS-native atomic
  no-replace directory move; Bash atomically reserves the final slot with
  `mkdir` and publishes the completed ownership marker with a no-replace hard
  link from within that slot, releasing an empty reservation after ordinary
  in-process failure.
  Interrupted markerless or hidden staging artifacts remain fail-closed and
  require later explicit repair/release.
- Promoted first publication, idempotent reuse, inactive/historical state,
  malformed and copied ownership, generation types, canonical paths, nested
  roots, links/reparse points, portable filename attacks, concurrent
  publication, and non-activation behavior into the Python/POSIX/PowerShell
  runner matrix.
- Kept installer and bootstrap adoption out of this slice. The parity-proven
  primitive still writes no payload, completion/current/LKG marker, activation
  receipt, launcher, service, state, or tombstone.
- Review hardening bound historical validation to the exact immutable
  provenance bytes, aligned the Python slot lock wait with the dependency-light
  runners, rejected Windows drive-relative ownership paths, and added full
  producer/consumer interoperability coverage.

### 2026-08-28 — Explicit exemplar slot adapters

- Added matching POSIX and PowerShell `slot-provision` / `slot-validate`
  installer actions to Agent Machines and Agent Index.
- Each adapter requires an explicit context receipt and expected marketplace
  id, supplies its fixed plugin id plus exact payload root and version, and
  delegates to the vendored parity-proven installation-context runner. The
  shared transaction rejects a foreign snapshot payload under the same receipt
  locks. Ambient context and self-stage metadata cannot authorize the action or
  override the executing payload identity.
- The actions bypass both the legacy mutation probe and legacy-root self-stage;
  executable tests invoke them from installed-plugin-shaped paths and prove
  they release the installed-payload CWD even when a staging sentinel is
  inherited and create no legacy root, current/LKG marker, activation receipt,
  payload, or service state.
- Normal stamp, provision, install, bootstrap, and service behavior remains
  legacy and unchanged. Build completion, operative cutover, rollback,
  repair/release, uninstall, and dual-cell proof remain separate slices.

### 2026-08-29 — Ownership-checked runtime cutover primitive

- Added cross-runner `slot-cutover` with explicit context, payload/snapshot
  identity, receipt-generation expectations, and current-version CAS.
- Cutover revalidates immutable slot completion under the genesis and
  installation locks, rejects malformed or linked runtime markers, and returns
  revalidation-required without mutation when generations or current selection
  drift.
- Initial install, forward update, and explicit historical rollback now share
  one marker rule: both current-version and last-known-good name the completed
  selected target. Last-known-good remains resolver fallback, not rollback
  selection state.
- Kept the primitive non-activating. Agent Machines normal-flow adoption,
  runtime gating, dual-cell lifecycle proof, and activation remain in the
  operative exemplar slice.

### 2026-08-31 — Opt-in, clean-room, and suite-scope guardrails

- Reaffirmed namespaced mode as explicit opt-in with legacy behavior for absent
  policy and every implicit default. Repository config, payloads, installers,
  bootstrap, reconciliation, and first use cannot activate it.
- Restricted every namespaced install and usage path during this effort to
  disposable clean-room environments. Persistent development and production
  machines remain locally unused in legacy mode; only read-only status and
  doctor checks may inspect readiness there.
- Bound installation cells to runtime-bearing core plugin identities from the
  `copilot-extensions` suite. Independent marketplaces may carry those same
  identities for source-isolation proof, but payload-only plugins and unrelated
  downstream, internal, or third-party plugin families remain outside this
  mechanism even when they provide tools or services.
- Added validation gates for persistent-host non-activation, clean-room-only
  lifecycle proof, and negative scope coverage before the operative exemplar
  work continues.

### 2026-08-31 — Command-only Agent Machines operative exemplar

- Added payload-invocation schema v2 as an additive contract: v1 generation
  remains unchanged, while required installation context is blocking and
  limited to runtime-bearing core suite identities.
- Converted Agent Machines payload commands to a fixed-identity dispatcher.
  Absent/false policy remains legacy; active validated context selects the
  cell; requested-only, invalid, foreign, maintenance, orphaned, and stale
  evidence fails without legacy fallback.
- Added active-cell-only first-use and bootstrap reconciliation through
  snapshot provenance, slot ownership, build completion, and marker CAS.
  Neither path activates a cell. Added the explicit slot-cutover adapter needed
  for historical rollback.
- Added the cross-platform Tier-P dual-cell scenario, including isolated update
  and rollback plus unrelated/payload-only eligibility negatives. Service
  conversion, repair/release, uninstall, migration, and broad rollout remain.
- The Linux clean-room arm passed the source and eligibility phases, then
  stopped at the explicit `toolchain-uv` gate because the box could not fetch
  PyYAML (`HandshakeFailure`) and no `CR_UV_INDEX` was configured. This host's
  Docker engine is Linux-only, so the Windows-container arm was not run. The
  cross-platform scenario-run checklist remains open.

### 2026-09-01 — Agent Machines operative review hardening

- Moved the Agent Machines cell-root provisioning lock into the complete
  `cell-provision` transaction, so detached bootstrap, first-use dispatch, and
  direct callers cannot concurrently mutate one immutable runtime slot.
- Made plugin-level `slot-cutover` share that lock and atomically republish the
  deploy manifest; failed compare-and-swap leaves the prior manifest unchanged.
- Added POSIX/PowerShell transaction-serialization and rollback-manifest
  assertions. The Linux clean-room lock/eligibility stage passed; the complete
  lifecycle still stops at the already-recorded `toolchain-uv` PyYAML fetch
  gate because this host has no governed Python index configured.
- Split schema-4 deploy manifests into reconciled payload provenance and active
  runtime selection. Historical rollback now preserves the payload provenance
  bootstrap has already reconciled, while a later different payload still
  triggers forward update.
- Staged cell snapshot copy in an owned temporary sibling and made retry reclaim
  only marker-proven, still-unproven publications. POSIX and PowerShell tests
  cover injected interruption, retry, and preservation of unowned final state.

### 2026-09-01 — Agent Index service-bearing implementation

- Converted Agent Index to the explicit installation-context payload contract
  while preserving absent/default/explicit-false policy as legacy and refusing
  invalid, requested-only, foreign, maintenance, orphaned, or stale evidence
  without legacy fallback.
- Added serialized cell provisioning and runtime cutover with snapshot
  provenance, immutable slot ownership/completion, schema-4 deploy manifests,
  installation-local launchers, cell-local service/routing/state/log/cache/config
  roots, OS-assigned endpoints, and no generic service/task/binstub publication.
- Added a cross-platform Tier-P scenario that provisions and starts two
  independently sourced Agent Index cells, updates and rolls one back without
  changing its peer, rejects foreign control, and stops each service through its
  own cell-local boundary.
- The Linux disposable arm passed the source boundary and default/false policy
  stage, then stopped at the classified `toolchain-uv` gate because the box
  could not fetch `setuptools` from public PyPI (`HandshakeFailure`) and no
  `CR_UV_INDEX` was configured. No namespaced state was created on a persistent
  host; the Linux full lifecycle and Windows arm remain open, so the exemplar is
  implementation-ready but not yet operative. Smoke mode remains a fast
  diagnostic; full mode is the acceptance lane. The clean-room-only restriction
  is rollout policy, not a host-detection security boundary.

### 2026-09-05 — Agent Index full Linux acceptance

- Filed and claimed
  [#2083](https://github.com/ThomasMichon/copilot-extensions/issues/2083) after
  the committed cutover-crash case restored prior selection metadata but lost
  the prior service's discovery evidence during target retirement; the fix
  landed through
  [#2089](https://github.com/ThomasMichon/copilot-extensions/pull/2089).
- Extended the selection transaction snapshot to preserve the prior endpoint
  and running-version records, so governance-blocked rollback restores their
  exact ownership before the target exits. Added a committed-crash regression
  proving the prior PID survives, reopens admission, and remains discoverable
  while only the transaction target retires.
- Filed and claimed
  [#2090](https://github.com/ThomasMichon/copilot-extensions/issues/2090) after
  the isolated shutdown phase exposed missing POSIX zombie handling in the
  shared rendezvous liveness helper and a false `still-running` result; the fix
  landed through
  [#2100](https://github.com/ThomasMichon/copilot-extensions/pull/2100). The
  clean-room fixture also now forwards synthetic repository opt-in data through
  every subprocess so the newer effective-config gate does not bypass the
  installation-context assertions.
- The full non-smoke Linux scenario passed all five phases and cleanup against
  a caller-supplied non-public package index: concurrent cells, isolated
  update/rollback, passive/flipped/draining/committed crash recovery,
  governance-blocked restoration, foreign-control refusal, and isolated
  shutdown.
- The Windows arm was attempted but the available Docker engine was running
  Linux containers; the Windows Server Core base image could not resolve for
  that platform. A second attempt dispatched the run to a dedicated
  Windows-container host, but the remote agent connection closed during ACP
  launch before the scenario started. The transport failure is tracked by
  [#2106](https://github.com/ThomasMichon/copilot-extensions/issues/2106).

### 2026-09-05 — Windows waiver and Agent Machines retirement design

- The operator waived the remaining Windows clean-room arms for this effort.
  Agent Index is accepted as the operative service-bearing exemplar on its full
  Linux lifecycle; PowerShell parity remains a deterministic test requirement.
- Filed and claimed
  [#2122](https://github.com/ThomasMichon/copilot-extensions/issues/2122) for the
  next Phase 3 slice: Agent Machines ownership-checked repair/release and
  uninstall.
- Chose receipt-only reservation release: future interrupted slots carry an
  attributable reservation receipt, while existing markerless reservations
  remain protected. Repair is derived-only and never recreates immutable
  completion evidence. Uninstall removes all validated owned runtime slots and
  snapshots but preserves durable state plus plugin and namespace receipts.

### 2026-09-05 — Agent Machines receipt-owned retirement implementation

- Implemented [#2122](https://github.com/ThomasMichon/copilot-extensions/issues/2122)
  as a vision-closing slice of independent installation lifecycle. Shared Python,
  Bash, and PowerShell provisioning publish reservation evidence before ownership;
  explicit release pins the reservation root, generation, and receipt-byte digest
  under both receipt locks. Markerless reservations remain protected.
- Added explicit Agent Machines derived-only repair and all-owned-history
  uninstall through one payload-local management engine and both installer
  adapters. Exact current/LKG and generation CAS, per-delete identity checks,
  full inventory preflight, and active-process refusal protect retirement.
  Durable state, namespace/install/activation evidence, and attributable
  directory structure remain; no namespace garbage collection is implicit.
- Real Linux acceptance exposed uv hardlinks between immutable version slots:
  unlinking one changes another link's ctime without changing its content.
  Retirement now advances only metadata changes caused by its own unlink,
  after rechecking inode, content digest, mode, size, and modification time.
  External cache links remain untouched, and a deterministic regression covers
  historical-slot removal with a surviving external hardlink.
- Corrected the clean-room harness rather than reducing its assertions:
  auth-free Tier-P manifests no longer borrow credentials; the version witness
  accepts the equivalent installed PEP 440 development spelling; update drives
  the explicit cell installer; build fixtures use native disposable storage.
  The scenario now includes interrupted receipt release, repair/refusal, and
  all-history uninstall with retention, replay, and dual-cell isolation.
- Preserved deterministic PowerShell coverage for the waived Windows
  clean-room arm. Windows file validation compares named and opened metadata
  using consistent APIs, including executable permission synthesis and
  directory-entry file identities. Shared bootstrap guards now exercise the
  operative Agent Machines boundary and explicitly assert that retired Agent
  Index compatibility hooks remain non-mutating.
- Acceptance passed the full non-smoke Linux scenario: phase 0's fixture check
  plus all eight numbered stages, including every historical runtime/snapshot
  removal and peer-cell availability. The complete shared foundation suite in a fresh policy-free Linux container passed
  502 tests (163 platform/portfolio skips, including clean-room harness checks).
  The full Agent Machines suite passed 478 tests (17 skips); the Windows shared
  smoke/parity lane passed 22 tests (one platform skip), and the dedicated
  reservation corpus passed 15 tests. Required lint, install-contract,
  documentation, version-consistency/bump, and vendoring checks passed.

### 2026-09-07 — Phase 3 closure and first agent-worktrees runtime slice

- Reconciled the three Phase 3 parent checklist items after both accepted
  exemplars, their identity/lifecycle paths, and the operator-approved Windows
  dispositions were already complete in their nested acceptance records.
- Started Phase 4 issue
  [#1105](https://github.com/ThomasMichon/copilot-extensions/issues/1105) with
  the narrow runtime boundary: the payload-local agent-worktrees command now
  resolves installation governance before runtime selection. Legacy/default
  policy preserves `~/.agent-worktrees`; an active validated context selects
  and first-use provisions only its cell-local versioned runtime, markers,
  wrappers, and deploy manifest.
- Invalid, foreign, inactive, and governance-blocked contexts fail without
  legacy fallback. The session-start bootstrap hook stands down for every
  explicit context so it cannot recreate legacy runtime state before payload
  invocation validates the selected cell.
- Real Windows first use passed through the payload dispatcher from paths
  containing spaces, built the cell-local runtime, and left the legacy root
  absent. The full WSL agent-worktrees portfolio passed; native Windows
  coverage passed after correcting cross-OS tests that had selected the WSL
  launcher for Windows paths or modeled POSIX paths with `WindowsPath`.
- Kept the larger #1105 state migration open. Global project/repository
  registries, per-project worktree/session state, project binstubs, pivots,
  leases, and harness state remain legacy until later attributable slices.

### 2026-09-07 — Agent-worktrees coupled registry-root slice

- Moved the global `config.yaml`, lean `projects.yaml`, and authoritative
  `repos.yaml` readers and writers through one validated registry-root
  selector. Legacy/default operation preserves the exact existing paths;
  explicit context validates the agent-worktrees payload and selects that
  cell's plugin root for all three files.
- Routed config fallbacks, repo CRUD and legacy migration classification,
  doctor reads/fixes, eager schema migration, resident session catalog reads,
  terminal generation, and pivot activation through the same boundary.
  Invalid, foreign, or payload-mismatched context fails without legacy fallback;
  `AGENT_RT_ROOT`, `AGENT_HOME`, `COPILOT_PLUGIN_ROOT`, and per-file path
  overrides cannot independently select a cell.
- Hardened standalone and resident hook paths. Registration nudges stand down
  before legacy reads; anchor/statelessness guards validate the selected root;
  explicit-context hooks bypass the legacy resident, select only the cell
  runtime for session start, deny pre-tool evaluation when context cannot be
  validated, and stand down for session-end deregistration until session state
  moves in a later slice. Every installer and quick-skip drift check now carries
  the registry-root helper with the guards.
- Added two-cell isolation, invalid/foreign context, spoof resistance, doctor
  symmetry, selected-root-only migration, hook, resident-cache, and deployment
  regressions. The full native Windows portfolio passed 3,946 tests with 47
  skips. The final full WSL portfolio passed 3,955 tests with 37 skips after one
  isolated timeout passed on retry. Install-contract, version, generated
  payload, vendored-library, installation-context, headless-launch,
  documentation, marketplace-inventory, and lint guards passed.
- Kept [#1105](https://github.com/ThomasMichon/copilot-extensions/issues/1105)
  open. Per-project worktree/session state, project binstubs, pivots and their
  cross-plugin activation model, leases, and broader harness state remain
  legacy or explicitly inert under namespaced context for later attributable
  slices.

### 2026-09-07 — Cell-local repository and project state

- Added a stable repository identity derived from the normalized registered
  remote. Namespaced adoption publishes a cell-owned `identity.json` at
  `<cell>/repos/<repository-id>/` and stores agent-worktrees project config,
  worktree records, histories, obligations, and session bindings in its
  `agent-worktrees/` child. Legacy/default operation remains exactly
  `~/.<project>`.
- Made receipt publication concurrent-safe and crash-durable: complete bytes
  are synced before atomic no-replace publication, POSIX directory metadata is
  synced, and Windows uses write-through `MoveFileExW`. Invalid, partial,
  mismatched, relative, or unstable identities fail closed. Equivalent GitHub
  remotes and default ports converge on one repository ID; the same remote in
  two cells keeps independent state.
- Prepared repository identity before first project-directory access in both
  install and register flows. Doctor now reports missing/invalid identity,
  persists repaired repo entries before identity repair, and returns unfixed
  findings rather than crashing on registry or filesystem write failures.
- Routed explicit-context session end through the selected cell runtime and
  tracking state. Every project-binstub invocation, including zero arguments,
  enters its pinned payload; bundled-picker fallback selects the validated cell
  launcher and passes a validated recovery anchor instead of re-entering or
  reading the legacy runtime.
- Preserved the host-owned `~/.copilot/session-state` database and deferred
  pivot composition, leases, service identity, and full project-command
  ownership transfer. The full native Windows portfolio passed 3,964 tests with
  47 skips; the final WSL portfolio passed 3,974 tests with 37 skips after one
  corrected POSIX legacy assertion. All publication guards passed, and the
  report-only marketplace-isolation inventory fell to 718 findings.

### 2026-09-07 — Cell-global runtime state and project-command arbitration

- Made the runtime/state root returned by `config.install_dir()` follow the
  validated installation context. Runtime manifests, version slots, wrapper
  support, activity logs, pivots, monitor state, and other plugin-global
  mutable artifacts now stay inside the selected cell; default/legacy behavior
  remains `~/.agent-worktrees`.
- Synchronized all three project-binstub generators. Python, Bash, and
  PowerShell installers now route every project invocation through the exact
  owning payload, including zero arguments, and no longer write a pre-context
  machine-global launch trace or embed a legacy runtime fallback.
- Kept the singleton project-command ledger intentionally shared with
  `~/.local/bin`, but rooted its receipts and locks in the same canonical home
  regardless of `AGENT_HOME`. Ownership now includes the validated
  marketplace ID and install receipt, preventing a replaced source at the same
  cache path or two cells with separate locks from silently taking the command.
- The full WSL portfolio passed 3,976 tests with 37 skips. On Windows, the
  consolidated changed-surface portfolio passed 1,028 tests with 22 skips and
  all individually timed-out modules passed independently; repeated full
  portfolios accumulated subprocess-creation stalls in varying unrelated
  tests, tracked separately by
  [#2214](https://github.com/ThomasMichon/copilot-extensions/issues/2214).
  All publication guards passed and the report-only marketplace-isolation
  inventory fell to 704 findings.
- Kept [#1105](https://github.com/ThomasMichon/copilot-extensions/issues/1105)
  open for cell-qualified distributed lease/resource identity and the remaining
  explicit disposition of host-owned session databases and service identity.

### 2026-09-07 — Agent-worktrees Phase 4 boundary closure

- Re-evaluated direct cell-qualified Git-ref lease activation after the runtime,
  registry, project-state, and command-ownership slices landed. A new client can
  use a cell-specific ref namespace, but an older client cannot observe or honor
  it; immediate activation would permit split-brain ownership during version
  skew. Nested cell refs also break strict legacy wildcard listing.
- Withdrew the unsafe prototype without leaving source changes. Lease
  qualification is blocked and transferred to `#1110`
  ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1110)),
  where maintenance admission, legacy ownership tombstones, and explicit
  migration can prevent old clients from reacquiring while the new namespace
  becomes authoritative.
- Transferred remaining fixed Worktree Manager service/process identity to
  `#1108` ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1108))
  and remote venue propagation to `#1107`
  ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1107)).
  The host-owned Copilot session database remains outside plugin ownership;
  cell-local agent-worktrees session bindings and worktree records are already
  isolated.
- Marked the `#1105` Phase 4 boundary complete through reviewed, merged PRs
  [#2196](https://github.com/ThomasMichon/copilot-extensions/pull/2196),
  [#2197](https://github.com/ThomasMichon/copilot-extensions/pull/2197),
  [#2198](https://github.com/ThomasMichon/copilot-extensions/pull/2198), and
  [#2215](https://github.com/ThomasMichon/copilot-extensions/pull/2215).

### 2026-09-07 — Agent-mcp low-risk runtime closure

- Merged [#2225](https://github.com/ThomasMichon/copilot-extensions/pull/2225)
  at `7aa54ef2c801c6884360dc28cf9706de146a667b`, converting `agent-mcp` as the
  remaining low-risk service-free runtime in `#1106`.
- Payload-local invocation now routes through Windows and POSIX runtime gates
  that validate the selected installation context before launch or first-use
  provisioning. Namespaced operation exports the selected cell as
  `AGENT_MCP_HOME`, scopes plugin-shipped bridge discovery to the owning
  marketplace, and keeps session-start bootstrap quiescent under explicit
  context so it cannot recreate legacy state.
- The validated runtime root now owns agent-mcp's versioned slots, snapshots,
  deploy manifest, token cache, storage stream buffer, materialized launchers,
  serve socket/lease, and other mutable runtime state. Legacy operation with no
  explicit active context remains exactly `~/.agent-mcp`.
- Added two-cell same-name bridge isolation and invalid, foreign, and
  spoofed-context negative coverage across both runtime-gate implementations,
  plus regression checks that scoped bridge resolution fails closed and that the
  runtime gates retain first-use provisioning locks.
- Validation:
  the full native Windows agent-mcp suite passed in two contained sub-suites
  (`334 passed, 15 skipped` and `118 passed, 8 skipped`); WSL focused
  payload-invocation/config coverage passed (`80 passed, 5 skipped`); and
  install-contract, version-consistency, vendored-lib, installation-context,
  payload-invocation, installer-readiness, and marketplace-isolation guards
  passed.
- Transferred the initial `agent-ssh` candidate to `#1107`
  ([issue](https://github.com/ThomasMichon/copilot-extensions/issues/1107))
  once inventory confirmed its managed OpenSSH fragments, dtssh companion,
  dispatch registrar drop-ins, and remote host restoration carry transport
  identity across remote boundaries.

### 2026-09-08 — Agent-ssh transport-boundary increment

- Merged [#2229](https://github.com/ThomasMichon/copilot-extensions/pull/2229)
  at `a3d4df15be23833972b204d405ea293caa4ca95d`, landing the `agent-ssh`
  portion of `#1107`.
- `agent-ssh` now vendors the shared installation-context primitive and routes
  its payload-local entry points through Windows and POSIX runtime gates that
  validate the selected installation context before launch or first-use
  provisioning. Namespaced operation binds host restoration and managed-fragment
  warning state to the selected runtime root while preserving exact legacy
  behavior when no explicit context is active.
- The same increment hardened the public guard surface that the landing needed:
  `check-agent-bridge-contracts.py` now hydrates `origin/main` history in
  shallow CI clones before validating historical evidence, the Agent Bridge
  contract provenance now points at main-history commits, and the shared
  versioned-runtime bootstrap family now stands down under explicit installation
  context across `agent-codespaces`, `agent-containers`, `agent-dispatch`,
  `agent-logger`, and `agent-vault`.
- Validation:
  full native Windows suites passed for `agent-bridge` (`2210 passed,
  14 skipped`) and `agent-ssh` (`139 passed, 15 skipped`); focused WSL
  `agent-ssh` transport/context coverage passed (`72 passed, 5 skipped`);
  `tools/test_check_agent_bridge_contracts.py` passed (`16 passed`); and
  install-contract, version-consistency, vendored-lib, installation-context,
  payload-invocation, installer-readiness, marketplace-isolation, and
  changed-file ruff guards passed.
- `#1107` remains open for the unlanded `agent-codespaces`,
  `agent-containers`, and machine-provider transport paths. The Phase 4 plan
  line stays unchecked until those remote-venue slices merge.

### 2026-09-08 — Remote venue and transport closure

- Merged [#2234](https://github.com/ThomasMichon/copilot-extensions/pull/2234)
  at `d850384eeaaf245c26e4faf27bc79c80ded60e16`, closing `#1107`.
- `agent-codespaces` and `agent-containers` now vendor the shared
  installation-context bootstrap, route payload-local commands through
  installation-context runtime gates on Windows and POSIX, and bind their
  agent-bridge/provider entry points to the exact payload-local shim that owns
  the selected installation cell instead of a mutable machine-global command.
- CodeSpace dispatch now persists its remote launch through the payload-local
  transport shim and stages related-repo plugins into source-qualified
  destination roots, so same-named staged payloads from different marketplaces
  do not collide. Container trusted-session and restricted provider-exec paths
  now carry the same installation root through wrapper, state, and SSH-profile
  commands.
- Audit note:
  the `agent-machines` machine-provider leg was already satisfied by its
  source-qualified payload-command invocation path, so closing `#1107` did not
  require additional `agent-machines` code changes.
- Validation:
  changed-surface native Windows contained runs passed for `agent-codespaces`
  (`74 passed, 8 skipped`) and `agent-containers` (`59 passed, 2 skipped`);
  focused WSL/POSIX coverage passed for `agent-codespaces` (`75 passed,
  7 skipped`) and `agent-containers` (`58 passed, 3 skipped`); install-contract,
  version-consistency, vendored-lib, installation-context, payload-invocation,
  installer-readiness, marketplace-isolation, and changed-file ruff guards
  passed; and PR CI passed `guards + lint`, `agent-codespaces`,
  `agent-containers`, `Git hooks (Windows)`, both test-runner jobs, and the
  out-of-plugin Worktree Manager lane.
- Native full contained suite attempts still reproduced unrelated baseline
  failures outside this change surface in `agent-codespaces`
  (`tests/test_lease.py`, `tests/test_relay_shim.py`) and `agent-containers`
  (`tests/test_private_state.py`, `tests/test_rescue.py`), so the landed
  validation evidence remains the focused changed-surface runs plus green PR
  CI.

### 2026-09-08 — Agent-vault service-boundary increment

- Merged [#2246](https://github.com/ThomasMichon/copilot-extensions/pull/2246)
  at `83535bb0c4c4e2cda954b184eec23d1f7703d158`, landing the `agent-vault`
  portion of `#1108`.
- `agent-vault` now vendors the shared installation-context runtime gate and
  validates the selected cell before launch or first-use provisioning. When an
  explicit active installation context is present, the vault now scopes its
  cache, local/core rendezvous state, log, pid, socket, and named-pipe
  artifacts to the selected installation root instead of sharing machine-global
  service state.
- The same increment qualifies the service's discovery and supervision
  boundaries: rendezvous records now carry installation identity and reject
  cross-cell discovery mismatches, while the Windows Scheduled Task and POSIX
  systemd unit derive installation-scoped lifecycle identities so concurrent
  same-named cells do not share service ownership. Legacy behavior with no
  explicit context remains unchanged.
- Validation:
  the full native Windows `agent-vault` suite passed (`233 passed,
  9 skipped`); focused WSL/POSIX payload and discovery coverage passed
  (`27 passed, 5 skipped`); install-contract, version-consistency,
  vendored-lib, installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, and changed-file ruff guards passed locally; and the
  PR's scoped `agent-vault` lane passed.
- Audit note:
  the PR's broad `guards + lint` job reproduced unrelated pre-existing
  `context-handoff` locator test failures, now tracked by
  [#2250](https://github.com/ThomasMichon/copilot-extensions/issues/2250), so
  `#1108` remains open only for the unlanded `agent-logger`, `agent-dispatch`,
  `agent-bridge`, and deferred `agent-worktrees` Worktree Manager supervision
  slices.

### 2026-09-08 — Agent-logger service-boundary increment

- Merged [#2253](https://github.com/ThomasMichon/copilot-extensions/pull/2253)
  at `35740324db13e1deecd687591972f3461f75ad79`, landing the `agent-logger`
  portion of `#1108`.
- `agent-logger` now vendors the shared installation-context runtime support,
  routes its full multi-command payload surface through installation-aware
  runtime gates, and validates the selected cell before launch or first-use
  provisioning. Helper entrypoints now carry explicit command metadata so
  `collate-session`, `read-session-digest`, `prepare-session-log`,
  `ramp-up-session`, and `session-sync` resolve the same cell-owned runtime as
  `agent-logger`.
- The same increment scopes the plugin's install and supervision boundaries by
  the selected runtime root: scheduled sync now uses install-root-specific
  Windows task and POSIX timer identities, scoped runtimes keep their state
  under the selected root, and non-legacy installs no longer claim the
  machine-global compatibility wrappers reserved for legacy fallback.
- Validation:
  direct changed-surface Windows tests passed
  (`20 passed, 3 skipped`); shared payload-invocation generator tests passed
  (`60 passed, 12 skipped`); focused WSL/POSIX payload coverage passed
  (`19 passed, 4 skipped`); install-contract, version-consistency,
  vendored-lib, installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, and changed-file ruff guards passed locally; and the
  PR CI passed `guards + lint`, `agent-logger`, `Git hooks (Windows)`, both
  test-runner jobs, and the out-of-plugin Worktree Manager lane.
- Audit note:
  the native Windows full `python tools/run-plugin-tests.py agent-logger` lane
  still reproduces the pre-existing failures tracked by
  [#2159](https://github.com/ThomasMichon/copilot-extensions/issues/2159)
  (`chronicle`, `rescue-sync`, and contained `install-binstub` regressions), so
  `#1108` remains open only for the unlanded `agent-dispatch`,
  `agent-bridge`, and deferred `agent-worktrees` Worktree Manager supervision
  slices.

### 2026-09-09 — Agent-dispatch service-boundary increment

- Merged [#2284](https://github.com/ThomasMichon/copilot-extensions/pull/2284)
  at `bbbdefdd34e8c5f31e50d0d50609cd6f4da952bc`, landing the `agent-dispatch`
  portion of `#1108`.
- `agent-dispatch` now resolves installation-scoped local roots through a
  shared helper and carries that scope through registrar drop-ins,
  coordinator/supervisor install identities, managed-runtime materialization,
  retention, telemetry, endpoint/runtime discovery, and remote-dispatch
  handoff paths. Explicit installation context now makes same-named cells write
  and read the same namespaced registrar/runtime state, while legacy execution
  with no installation root remains exactly machine-global.
- The same increment hardened changed-surface subprocess coverage for source-tree
  runs: companion gate launches now normalize inherited `PYTHONPATH` entries
  before changing CWD, the managed-retention real-subprocess test supplies the
  exact plugin root/src search path, and the fleet SSH-fallback test now
  explicitly disables the local bridge fast path so the fallback assertion is
  deterministic.
- Validation:
  direct Windows `PYTHONPATH=plugins/agent-dispatch/src python -m pytest -q plugins/agent-dispatch/tests`
  passed (`2203 passed, 21 skipped`);
  `python tools/run-plugin-tests.py agent-dispatch` passed in four contained
  sub-suites (`688/518/821/194 passed`, `5/9/1/4 skipped`);
  focused WSL/POSIX coverage for `install_paths`, `managed_runtime`,
  `managed_retention`, `registrar_registry`, and `supervisor_install` passed
  (`204 passed, 1 skipped`);
  and install-contract, version-consistency, vendored-lib,
  installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, and changed-file ruff guards passed.
- Audit note:
  the first contained runner attempt hit a transient Windows `os.replace(...)`
  access-denied during `test_configured_index_host_prepares_before_launch_and_freezes_bound_runtime`;
  the required single retry passed cleanly, so the landed evidence is the green
  retry plus green PR CI.
- `#1108` remains open only for the unlanded `agent-bridge` increment and the
  deferred `agent-worktrees` status-monitor / Worktree Manager supervision
  slice.
