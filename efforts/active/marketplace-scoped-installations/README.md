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

- [ ] Add checked-in, payload-local POSIX/PowerShell/CMD shims generated from
  canonical templates.
- [ ] Add session-start command-catalog context so skills and agents receive the
  exact payload-owned invocation path; convert operative bare command examples.
- [ ] Stop installing generic `agent-*` commands into `~/.local/bin`.
- [ ] Make project binstubs pin their owning payload and reject silent ownership
  transfer.

### Phase 3 — Installation context and exemplars ([#1104](https://github.com/ThomasMichon/copilot-extensions/issues/1104))

- [ ] Land the reviewed
  [installation-context and dual-cell proposal](phase-3-installation-context.md)
  before either platform makes the new root operative.
- [ ] Introduce a self-contained, vendorable installation-context primitive
  separate from versioned interpreter resolution.
- [ ] Persist and validate marketplace, plugin, payload, runtime, and instance
  identity through stamp, snapshot, provision, cutover, rollback, and uninstall.
- [ ] Prove one on-demand plugin and one service-bearing plugin with two
  simultaneous marketplace cells before broad rollout.

### Phase 4 — Runtime and state rollout

- [ ] Convert agent-worktrees and its project/repo registries first
  ([#1105](https://github.com/ThomasMichon/copilot-extensions/issues/1105)) so later
  reconciliation and project entry points are attributable.
- [ ] Convert service-free runtimes in low-risk batches
  ([#1106](https://github.com/ThomasMichon/copilot-extensions/issues/1106)).
- [ ] Convert remote venue and transport plugins, carrying installation identity
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

- [ ] Provide an explicit legacy-state attribution/migration command; ambiguous
  `~/.agent-*` state is preserved and never claimed automatically.
- [ ] Migrate or retire legacy services and global generic binstubs only after
  ownership is proven and the new cell passes health checks.
- [ ] Turn the report-only guards blocking after all runtime plugins conform.
- [ ] Document rollback and retention of legacy state and inactive cells.

## Validation Plan

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
