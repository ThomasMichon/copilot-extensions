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
- [x] Convert service-bearing plugins, qualifying service, lease, endpoint,
  provider, log, and process identity
  ([#1108](https://github.com/ThomasMichon/copilot-extensions/issues/1108)).

### Phase 5 — Repository configuration and adoption state ([#1109](https://github.com/ThomasMichon/copilot-extensions/issues/1109))

- [x] Move committed plugin configuration toward
  `.copilot-extensions/<plugin>/...` with new-first, legacy-fallback reads.
- [x] Keep committed repository policy distribution-neutral; require an explicit
  overlay for genuinely marketplace-specific behavior.
- [x] Move machine-local project state beneath the adopting installation cell,
  keyed by stable remote identity rather than repository basename alone.

### Phase 6 — Migration, enforcement, and cleanup ([#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110))

- [x] Add user-wide and plugin-scoped parser-free maintenance gates with strict
  ownership sidecars, explicit management-command authorization, draining lease
  behavior, stale-owner diagnostics, and fail-safe remote maintenance probing.
- [x] Provide explicit legacy-state attribution/migration under the legacy
  lock/lease and cell install lock; publish the ownership tombstone and
  generation-pinned activation without an observable mixed-writer interval.
- [x] Add explicit rollback/deactivation that publishes a monotonic
  legacy/deactivated activation before clearing the tombstone under both locks.
  Reserve activation deletion for locked cleanup after companion evidence is
  gone.
- [x] Make every long-running legacy and namespaced loop recheck maintenance,
  tombstone ownership, and activation/install generations at iteration
  boundaries and before mutation.
- [x] Migrate or retire legacy services and global generic binstubs only after
  ownership is proven and the new cell passes health checks.
- [ ] Turn the report-only guards blocking after all runtime plugins conform.
- [x] Document rollback and retention of legacy state and inactive cells.

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

See [`design.md`](design.md), [`installation-mode-governance.md`](installation-mode-governance.md),
[`phase-2-launcher-contracts.md`](phase-2-launcher-contracts.md),
[`phase-3-installation-context.md`](phase-3-installation-context.md), and
[`phase-6-lifecycle.md`](phase-6-lifecycle.md).

## Journal

### 2026-09-10 — Shared peer invocation and CodeSpaces callers

- Extracted dispatch's proven native child boundary into the canonical
  `libs/peer-launch/` source, with byte-identical packaged consumers in dispatch
  and CodeSpaces and CI synchronization/version-bump coverage.
- Converted CodeSpaces worktrees lookups used by project selection, state-root
  configuration, coordination, account selection, leases, and the source map
  hook. Explicit-context refusals propagate through authentication, claims,
  SSH admission, and session-guidance production rather than becoming ambient
  fallback or successful empty guidance writes. No-context behavior remains
  legacy-compatible.
- Annotated only the seven converted callers' tested no-context PATH branches.
  A focused scanner regression now requires those source files to have no
  unexplained sibling-launch findings. This is not a blanket allowance for
  unexamined inventory entries or declarative readiness metadata.
- Both owner and peer must pass active receipt and activation/maintenance
  governance. Owner admission occurs before optional-peer absence and explicit
  caller-argument shortcuts, and is rechecked before peer execution. Only a
  valid owner with a genuinely absent peer may take the optional-peer path.
- Review found and corrected owner-governance and source-hook refusal gaps.
  Updated Windows dispatch/governance coverage passed 63 tests (3 skips);
  POSIX passed 62 (4 skips). Updated CodeSpaces adapter/guidance coverage
  passed 22 tests on Windows (1 skip) and 23 on POSIX. The preceding broader
  CodeSpaces caller selection passed 459 tests on each platform (8 skips each).
  Shared packaging/sync, version, install-contract, payload-generation, and
  headless-launch checks also passed.
- Public review additionally closed refusal propagation through early CLI
  setup, claim-disabled release, best-effort lifecycle catches, and both
  platform guidance wrappers. The shared scrubber also removes bridge
  host-auth nonce and routing-table overrides. Follow-up CodeSpaces
  authorization coverage passed 86 tests on Windows (1 skip) and 87 on POSIX;
  dispatch/governance remained green at 63/62 passed with 3/4 skips.
- Final admission hardening removes inherited worktrees credentials/routing,
  preserves exit 78 before top-level project/tool preflights, and separates
  best-effort obligation bookkeeping from admission so a refused update cannot
  interrupt transport cleanup. The expanded caller selection passed 90 tests
  on Windows (1 skip) and 91 on POSIX.
- This is another bounded conversion slice. The inventory remains report-only;
  neither the rest of the Phase 2/6 launcher/lease work nor the parent effort's
  validation is complete.

### 2026-09-10 — Dispatch slice merged; accidental closing reference corrected

- [PR #2418](https://github.com/ThomasMichon/copilot-extensions/pull/2418)
  merged as `39fb1cbd98351422a8927fc4829786b9f749038f`, delivering the
  dispatch same-cell peer launcher and packaged loop-governance primitive.
  Final required CI passed. Review also required removing the entire
  dispatch-specific environment namespace so credentials and endpoint routing
  cannot propagate to peers; real Windows and WSL subprocess assertions passed
  for both peer commands.
- Post-merge verification found the parent migration tracker closed. The PR's
  live `closingIssuesReferences` explicitly contained that tracker: a negated
  sentence in the PR body still formed a GitHub closing-keyword reference.
  Replaced it with neutral wording that the parent remains open, reopened
  [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110), and
  verified an empty closing-reference list plus the issue's open state.
- Correction to the earlier process diagnosis: a closure event with a null
  commit ID is not sufficient to attribute closure to a delegate's API call.
  That earlier attribution was unproven. For this recurrence, the accidental
  PR closing reference is directly observed. Contribution guidance now requires
  checking closing references before merging any partial effort slice.
- No additional Plan or Validation Plan boxes are checked by this accounting
  update. The effort remains active, including the remaining Phase 2/6
  boundaries, deferred lease migration, Phase 7 intake, and final validation.

### 2026-09-10 — Dispatch same-cell peer invocation

- Replaced dispatch's explicit-context sibling legacy-root lookup with a
  packaged native child boundary for `agent-worktrees` and `agent-bridge`.
  It validates active same-cell receipts with the canonical installation-context
  primitive, checks the peer's own governance, asks that peer's shipped runtime
  resolver for its interpreter, and rebinds context and runtime environment
  before executing isolated Python. Absent context preserves legacy behavior;
  malformed explicit context never authorizes a legacy fallback.
- Kept runtime selection with its existing owner instead of duplicating
  completion-marker or slot-validation algorithms inside dispatch. Arbitrary
  command arguments never cross the PowerShell/POSIX resolver probe.
- Real disposable-venv regressions cover independent cells, adversarial argv,
  context rebinding, inactive/foreign/incomplete receipts, saved-prefix
  revalidation, completion-marker refusal, LKG fallback, and inherited stdio.
  Focused native Windows dispatch/caller coverage passed 199 tests; the WSL
  peer-boundary selection passed 31 tests, including POSIX interpreter symlinks
  and linked-root refusal. A Windows windowless-parent probe exercised two
  launch cycles without observed child windows or focus transitions.
- PR review identified that resident loop governance still loaded a payload-side
  validator. It now imports the same packaged primitive as the peer launcher,
  with a regression proving it loads without a deploy manifest or payload-side
  copy. Follow-up Windows coverage passed 8 tests and WSL coverage passed 33.
  The desktop observation now excludes foreground changes among pre-existing
  windows, which cannot by themselves identify a console created by the probe.
- This is one conversion slice, not Phase 6 completion. The initial full
  inventory classifications remain provisional: a literal legacy fallback
  does not prove a defect, but an allowance also requires evidence that active
  namespaced callers cannot use it. Do not apply bulk suppressions or enable
  strict CI from the classification totals alone. The Phase 2 retirement item,
  deferred lease work, Phase 7, and final validation remain open.

### 2026-09-10 — Item 6 precondition: what "all runtime plugins conform" means

- Read `tools/check-marketplace-isolation.py` directly rather than treating its
  finding count as a literal backlog size. It is a plain regex/heuristic
  scanner over source files (Python/PS1/sh/JS/JSON/YAML), not a
  behavior-aware check: it flags any literal `.agent-<name>` path token,
  `.local/bin` reference, fixed service/task/mutex/pipe/socket/lease/endpoint
  string assignment, or unqualified sibling-command launch, with a single
  escape hatch — an inline `marketplace-isolation: allow <reason>` marker. The
  guard's own docstring already says findings are "the migration baseline" and
  `--strict` should be used "only after the producing phases have landed," not
  once every matching string is gone.
- Sampled the current 681-finding set (`--json`, ~6 findings per category).
  Every sampled hit across `unqualified-runtime-root`, `global-plugin-binstub`,
  `fixed-service-identity`, and `path-sibling-launch` was in a plugin this
  effort has **already converted** (`agent-bridge`, `agent-codespaces`), inside
  intentional, already-cell-aware legacy-fallback code: `LEGACY_INSTALL_DIR`
  constants, `payload-invocation.json`'s declared `legacyRuntimeRoot`,
  `_scoped_identity_suffix`-qualified systemd unit names, and the deliberate
  default-legacy PATH/binstub path this effort's own design requires to keep
  working when no installation context is active. None of the sampled findings
  represented genuinely unconverted plugin surfaces.
- Conclusion: the guard's non-zero count is not evidence of unfinished plugin
  conversion by itself. The real path to satisfying item 6 is **convert
  genuine backlog, then annotate every remaining intentional legacy-fallback
  occurrence with `marketplace-isolation: allow <reason>`** so the count
  becomes zero for the right reason, not by deleting legacy fallback paths the
  effort's own design requires to keep. A future increment must still: (a)
  triage the full 681-finding set (or whatever it is by then) file-by-file,
  since this sample was not exhaustive; (b) confirm each finding is either
  genuine backlog (convert it) or intentional (annotate it); (c) only then
  re-run with `--strict` and flip the guard in CI. This item stays open and
  unchecked; do not flip `--strict` on the strength of this note alone.
- Also confirmed and corrected an unrelated process defect while investigating
  this: issue [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110)
  had been closed directly (not via a merge "Closes #" keyword) around the
  time [#2353](https://github.com/ThomasMichon/copilot-extensions/pull/2353)
  merged, while only 3 of 7 Phase 6 plan items were done and despite that PR's
  own body stating it did not close the issue. Reopened #1110 with an
  explanatory comment. Every future Phase 6 (and later-phase) delegate must be
  told explicitly: never run `gh issue close`; only the effort owner closes an
  issue, and only once every plan item it tracks is concretely verified done.

### 2026-09-10 — Phase 6 item 7: rollback and retained-evidence documentation

- Added [`phase-6-lifecycle.md`](phase-6-lifecycle.md) as the focused Phase 6
  companion covering how explicit legacy attribution, rollback/deactivation,
  and legacy-compatibility retirement interact; which records are temporary
  versus durable; and how to diagnose a deactivated-but-not-cleaned cell
  without re-reading the full install contract. The note links the exact JSON
  schemas back to [`docs/install-contract.md`](../../../docs/install-contract.md)
  rather than duplicating them.
- Updated the normative
  [`docs/install-contract.md`](../../../docs/install-contract.md) record section
  with the current retention and deactivated-cell behavior: tombstones clear
  only on explicit rollback, deactivation and retirement records have no
  age-based expiry or scavenger in the shared implementation, and
  `installation-activation.json` remains as a monotonic `legacy`/`deactivated`
  record until a later cleanup path removes it under the required locks.
- Updated [`installation-mode-governance.md`](installation-mode-governance.md)
  to point at the new lifecycle note and removed the stale "retention duration"
  open choice now that the current implementation behavior is documented.
- Validation: `python tools/check-docs-consistency.py` and `git diff --check`
  passed.
- The guard-enforcement item remains open. I re-read
  `tools/check-marketplace-isolation.py` and the surrounding `tools/` guards:
  for this effort, the Phase 6 blocking gate still refers to the
  marketplace-isolation inventory; the other report-only guard in `tools/`
  (`check-runtime-resolution.py`) belongs to the separate
  `uniform-runtime-resolution` effort. The current marketplace-isolation run
  still reported **681** findings across 1344 operative plugin files
  (`unqualified-runtime-root`: 416, `fixed-service-identity`: 133,
  `global-plugin-binstub`: 80, `path-sibling-launch`: 52), so the "all runtime
  plugins conform" precondition for `--strict` does not hold and the guard stays
  report-only.
- This checks off only the Phase 6 documentation item. It deliberately does
  **not** close [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110),
  because the guard-enforcement item is still unfinished.

### 2026-09-10 — Phase 6 item 5: ownership- and health-gated legacy wrapper retirement

- Merged [#2367](https://github.com/ThomasMichon/copilot-extensions/pull/2367)
  at `772eb13917e61f1dd6a61fccb0153bc91eb1c7a3`, landing the legacy retirement
  gate for [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110).
- The shared `installation-context` primitive now exposes
  `retire_legacy_compatibility(...)`, which fails closed unless the current
  cell can still prove all of the following at retirement time: the install and
  activation generations still match the caller's explicit target, the legacy
  tombstone still belongs to the current active namespaced activation, and the
  replacement runtime contributes an explicit `health.status: "ready"` report.
  On success it removes only the ownership-matched legacy compatibility
  artifacts and writes a durable `legacy-retirement` record under
  `retirements/`; replay of the same target is an idempotent no-op.
- `agent-machines` is now the first concrete exemplar via the explicit
  `cell-retire-legacy` management action. It reuses the command-only exemplar's
  existing health evidence — current/LKG markers, immutable runtime-slot
  completion, and schema-4 deploy-manifest validation — and retires only the
  ownership-matched global generic `agent-machines` binstub compatibility
  surface under `~/.local/bin/` (`agent-machines`, `agent-machines.cmd`, and
  `agent-machines.ps1`, removing only the files actually present and claimed by
  the tombstone).
- Validation:
  `python -m pytest -q libs/installation-context/tests`
  (`17 failed, 510 passed, 130 skipped` on native Windows, with the exact same
  17 failing node IDs reproduced in a detached `origin/main` worktree and still
  tracked under [#2352](https://github.com/ThomasMichon/copilot-extensions/issues/2352));
  `python tools/run-plugin-tests.py agent-machines`
  (`515 passed, 20 skipped`);
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/sync-installation-context.py --check`,
  `python libs/payload-invocation/generate.py --all --check`,
  `python -m pytest -q libs/installer-readiness/tests`
  (`46 passed, 1 skipped`),
  `python tools/check-marketplace-isolation.py` (report-only; 684 findings),
  `python tools/check-docs-consistency.py`,
  changed-file `ruff check --select F,E9`, and `git diff --check` all passed.
  WSL/POSIX changed-surface coverage also passed via
  `python3 tools/run-plugin-tests.py agent-machines -k retire_legacy`
  (`1 passed, 1 skipped, 531 deselected`) and
  `./.test-venvs/linux/agent-machines/bin/python -m pytest -q libs/installation-context/tests/test_installation_mode_governance.py -k retire_legacy_compatibility`
  (`3 passed, 117 deselected`).
- This checks off the Phase 6 legacy-retirement plan item because the shared
  fail-closed retirement gate now exists and is proven end-to-end on one real
  generic wrapper. It does **not** check off the older Phase 2 launcher
  retirement item: the rest of the `phase-2-launcher-contracts.md` inventory
  (generic wrapper publication across other runtime plugins, mixed
  `agent-worktrees` project-command surfaces, durable provider manifests,
  readiness fallbacks, remote transport callers, bootstrap/nudge/generated
  launchers, and credential/askpass fallbacks) remains pending.

### 2026-09-10 — Phase 6 item 4 completion: remaining loop adopters

- Merged [#2365](https://github.com/ThomasMichon/copilot-extensions/pull/2365)
  at `47a85d07d04438e10d5b6e40dc496ad40967b9cb`, completing the remaining
  long-running loop governance recheck scope for
  [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110).
- `agent-worktrees` now applies the shared
  `recheck_loop_governance(...)` contract throughout the resident
  `status-monitor`: at the iteration boundary, immediately before lock renewal,
  before any durable render/publish/cache mutation, and before resident hook-IPC
  responses and handoff-cutover actions that would mutate session/worktree
  state. A generation or ownership flip now leaves the registered session
  queued for the next sweep instead of publishing stale state.
- `agent-dispatch` now applies the same contract across every remaining
  long-running loop in the plugin: coordinator liveness GC and orphan reaping,
  coordinator self-retire and self-update polling, `Supervisor.serve()`, and
  `SupervisorDaemon.serve()`. The spawn supervisor now rechecks both before
  reserving and before launching, releasing a reserved attempt back to the queue
  when governance changes before spawn. While landing the slice, the PR also
  fixed an unrelated required-CI blocker by synchronizing the shipped
  `agent-index` managed-runtime declaration with the current `agent-index`
  version surfaces.
- `agent-bridge` now rechecks governance across its periodic daemon loops:
  periodic GC, heartbeat/liveness note + disconnected-host recovery +
  host-reapable refresh + wedged-session reconciliation, idle shutdown, stranded
  host sweep, idle-session reaping, live-session lease reaping, self-retire
  polling, and periodic worktree discovery. Discovery now revalidates again
  before publishing fresh cache entries, discarding in-flight crawl results when
  ownership or generations change mid-pass.
- Validation:
  `python tools/run-plugin-tests.py agent-worktrees`
  (`1 failed, 578 passed, 2 skipped` on native Windows, with the unchanged
  `tests/test_config.py::TestControlPlaneRelatedPRTier::test_cp_related_pr_map_includes_knowledge_overlay`
  failure reproduced on detached `origin/main` and tracked in
  [#2364](https://github.com/ThomasMichon/copilot-extensions/issues/2364));
  `python tools/run-plugin-tests.py agent-dispatch`
  (`3 failed, 681 passed, 5 skipped`, with the unchanged Windows
  `test_bootstrap_check_reconcile_opt_in.py::{test_sh_skips_spawn_without_opt_in,test_sh_proceeds_with_opt_in}`
  failures reproduced on detached `origin/main` and already tracked in
  [#2327](https://github.com/ThomasMichon/copilot-extensions/issues/2327), and
  the previously unrelated `test_shipped_index_declaration_preserves_version_and_source_authority`
  blocker fixed before merge);
  `python tools/run-plugin-tests.py agent-bridge`
  (`2 failed, 558 passed, 2 skipped`, with the unchanged Windows
  `test_bootstrap_check_reconcile_opt_in.py::{test_sh_skips_spawn_without_opt_in,test_sh_attempts_spawn_with_opt_in}`
  failures reproduced on detached `origin/main` and already tracked in
  [#2327](https://github.com/ThomasMichon/copilot-extensions/issues/2327));
  `python -m pytest -q libs/installation-context/tests`
  (`18 failed, 506 passed, 130 skipped`, where the baseline
  `17 failed, 507 passed, 130 skipped` set reproduced unchanged on detached
  `origin/main` and remains the already-tracked
  [#2352](https://github.com/ThomasMichon/copilot-extensions/issues/2352)
  bucket, while the extra current-branch
  `test_installation_context_posix.py::test_concurrent_first_stamp_leaves_one_untorn_receipt[python-runner_command0]`
  failure did not reproduce when rerun on either this branch or detached
  `origin/main`);
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/sync-installation-context.py --check`,
  `python libs/payload-invocation/generate.py --all --check`,
  `python -m pytest -q libs/installer-readiness/tests`
  (`46 passed, 1 skipped`),
  `python tools/check-marketplace-isolation.py` (report-only; 709 findings),
  `python tools/check-docs-consistency.py`,
  `git diff --check`,
  `python tools/check-agent-bridge-contracts.py --base origin/main`,
  `python -m pytest -q tools/test_check_agent_bridge_contracts.py`
  (`16 passed`), and
  `ruff check --select F,E9` on the changed Python files all passed. WSL/POSIX
  changed-surface coverage also passed via
  `python3 tools/run-plugin-tests.py agent-worktrees -k status_monitor`
  (`80 passed, 1 skipped, 4043 deselected`),
  `python3 tools/run-plugin-tests.py agent-dispatch -k "loop_governance or test_shipped_index_declaration_preserves_version_and_source_authority"`
  (`7 passed, 2266 deselected`),
  `python3 tools/run-plugin-tests.py agent-bridge -k loop_governance`
  (`3 passed, 1 skipped, 2248 deselected`), and
  `./.test-venvs/linux/agent-worktrees/bin/python -m pytest -q libs/installation-context/tests/test_installation_mode_governance.py -k loop_recheck`
  (`4 passed, 113 deselected`).
- With `agent-index` already landed in [#2361](https://github.com/ThomasMichon/copilot-extensions/pull/2361),
  every applicable long-running loop across the current runtime plugin suite now
  rechecks maintenance, tombstone ownership, and activation/install generations
  at the iteration boundary and before mutation, so the Phase 6 plan item is now
  complete.

### 2026-09-10 — Phase 6 item 4: long-running loop governance rechecks

- Merged [#2361](https://github.com/ThomasMichon/copilot-extensions/pull/2361)
  at `64cc326d14db1fe574e70164b98612b30d6f1f32`, landing the next
  long-running loop governance increment for
  [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110).
- The shared `installation-context` primitive now exposes
  `recheck_loop_governance(...)`, which snapshots the active maintenance state,
  tombstone ownership, and activation / namespace / install generations for one
  installation context; returns `ready`, `backoff`, or
  `revalidation-required`; and fails closed when the loop can no longer prove it
  is still operating on the same ownership and generation it started with.
- `agent-index` is now the service-bearing exemplar for the slice. Its
  long-running task-runner loop rechecks governance at the iteration boundary,
  immediately before dequeueing queued work, and immediately before recording a
  launched worker. If governance flips after dequeue but before launch, the
  claimed task is re-queued instead of being left processing under stale
  authority. Vendored `installation-context` copies were synchronized across
  every current adopter.
- Validation:
  `python -m pytest -q libs/installation-context/tests`
  (`17 failed, 507 passed, 130 skipped` on native Windows, with the same
  17 pre-existing failures reproduced unchanged in a detached `origin/main`
  worktree and already tracked under
  [#2352](https://github.com/ThomasMichon/copilot-extensions/issues/2352));
  `python tools/run-plugin-tests.py agent-index`
  (`1 failed, 335 passed, 60 skipped`, with the unchanged Windows
  `test_managed_adapter_runs_real_service_without_plugin_or_engine_provisioning`
  failure reproduced on both this branch and detached `origin/main`, now
  tracked in [#2360](https://github.com/ThomasMichon/copilot-extensions/issues/2360));
  `ruff check --select F,E9` on the changed Python files,
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/sync-installation-context.py --check`,
  `python libs/payload-invocation/generate.py --all --check`,
  `python -m pytest -q libs/installer-readiness/tests`,
  `python tools/check-marketplace-isolation.py`,
  `python tools/check-docs-consistency.py`, and `git diff --check` all passed.
  WSL / POSIX changed-surface coverage also passed via
  `python -m pytest -q libs/installation-context/tests/test_installation_mode_governance.py -k "loop_recheck or maintenance_status_reports_authorization_metadata or attribute_legacy_state or deactivate_installation"`
  (`16 passed, 101 deselected`),
  `python -m pytest -q libs/installation-context/tests/test_vendoring.py`
  (`3 passed`), and
  `python3 tools/run-plugin-tests.py agent-index -k task_runner_governance`
  (`6 passed, 602 deselected`). The full WSL `agent-index` suite hit an
  unrelated pre-existing `WindowsPath` internal-error path outside the new loop
  governance surface, so the POSIX proof here stayed on the changed surfaces.
- This does **not** check off the Phase 6 plan item yet. Additional applicable
  long-running loops still need the same recheck contract, including the
  `agent-worktrees` resident `status-monitor`, the `agent-dispatch`
  coordinator / supervisor / supervisor-daemon loops, and the `agent-bridge`
  periodic daemon loops (GC, heartbeat / reattach, idle and live-session
  reapers, and periodic worktree discovery).

### 2026-09-10 — Phase 6 item 3: explicit rollback / deactivation

- Merged [#2353](https://github.com/ThomasMichon/copilot-extensions/pull/2353)
  at `aa3280056fda5d7db82e8599d0652b2430531524`, landing the explicit
  rollback / deactivation slice for
  [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110).
- The shared `installation-context` primitive now exposes
  `deactivate_installation(...)`, which requires an explicit activation target
  plus either an explicit tombstone generation to roll back or an explicit
  proof that no tombstone exists; reuses maintenance admission plus the
  marketplace genesis / cell install lock discipline; publishes the next
  `legacy`/`deactivated` activation generation; writes an auditable
  per-target record under `deactivations/`; and only then clears the matched
  tombstone. Repeating the same explicit target is an idempotent no-op.
- `agent-machines` is the command-only exemplar for the slice via the explicit
  `cell-deactivate` management action. It derives the declared legacy footprint
  from `payload-invocation.json`, rolls back attributed legacy state only when
  the tombstone target matches exactly, refuses ambiguous untombstoned legacy
  state without mutation, and preserves `deactivations/` during uninstall.
  Vendored `installation-context` copies were synchronized across every current
  adopter.
- Validation:
  changed-surface Windows coverage passed via
  `python -m pytest -q libs/installation-context/tests/test_installation_mode_governance.py -k "deactivate_installation or activation_cas_requires_matching_maintenance_token or attribute_legacy_state"`
  (`12 passed, 101 deselected`) and
  `python tools/run-plugin-tests.py agent-machines`
  (`513 passed, 20 skipped`);
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/sync-installation-context.py --check`,
  `python libs/payload-invocation/generate.py --all --check`,
  `python -m pytest -q libs/installer-readiness/tests`,
  `python tools/check-marketplace-isolation.py`,
  `python tools/check-docs-consistency.py`,
  changed-file `ruff check --select F,E9`, and `git diff --check` all passed;
  WSL targeted rollback/deactivation coverage also passed for the same
  governance and `agent-machines` lifecycle surfaces.
- Native Windows `python -m pytest -q libs/installation-context/tests` still
  reproduces unchanged failures outside the rollback/deactivation surface
  (bootstrap no-op, legacy-entrypoint, and snapshot/concurrency cases). The
  current branch reproduced `17 failed, 503 passed, 130 skipped`; the same
  failing subset was re-run in a detached `origin/main` worktree, including
  `test_runtime_slot_completion_captures_one_concurrently_replaced_build_receipt[python]`.
  The unchanged native-Windows baseline is now tracked in
  [#2352](https://github.com/ThomasMichon/copilot-extensions/issues/2352), so
  this slice relied on the passing changed governance surface plus the green
  PR CI matrix rather than the pre-existing full-suite failures.
- `#1110` remains open. Long-running maintenance rechecks, legacy service and
  binstub retirement, blocking guard enforcement, and rollback/retention
  documentation remain separate follow-on Phase 6 items.

### 2026-09-10 — Phase 6 item 2: explicit legacy attribution / migration

- Merged [#2337](https://github.com/ThomasMichon/copilot-extensions/pull/2337)
  at `b6a980a61a9816aafbe5fe6e58d60edc3413aeb4`, landing the explicit
  legacy-state attribution / migration slice for
  [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110).
- The shared `installation-context` primitive now exposes
  `attribute_legacy_state(...)`, which attributes only clear legacy filesystem
  state to an explicitly named destination cell while holding the legacy
  lock/lease plus the destination cell install lock, writing the ownership
  tombstone before the generation-pinned namespaced activation, preserving
  ambiguous or orphaned state unchanged, and treating repeat attribution of the
  same footprint as an idempotent no-op.
- `agent-machines` is the command-only exemplar for the slice via the explicit
  `cell-attribute-legacy` management action. It inventories the declared
  legacy footprint from `payload-invocation.json`, preserves linked/missing or
  otherwise unattributable state for deliberate resolution, and records the
  exact claimed path set in the tombstone. Vendored `installation-context`
  copies were synchronized across every current adopter.
- Validation:
  `python -m pytest -q libs/installation-context/tests/test_installation_mode_governance.py`
  (`77 passed, 30 skipped`);
  `python tools/run-plugin-tests.py agent-machines`
  (`510 passed, 20 skipped`);
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/sync-installation-context.py --check`,
  `python libs/payload-invocation/generate.py --all --check`,
  `python -m pytest -q libs/installer-readiness/tests`,
  `python tools/check-marketplace-isolation.py`,
  `python tools/check-docs-consistency.py`,
  changed-file `ruff check --select F,E9`, and `git diff --check` all passed;
  PR #2337 CI also passed its plugin matrix for agent-bridge, agent-codespaces,
  agent-containers, agent-index, agent-logger, agent-machines, agent-mcp,
  agent-ssh, agent-vault, and the agent-worktrees collect-only/Windows-launch
  lanes plus the shared guards/lint jobs. WSL targeted coverage also passed for
  the attribution paths and the agent-machines exemplar.
- Native Windows whole-portfolio spot checks still reproduced unchanged
  unrelated failures from `origin/main` in untouched installation-context and
  subprocess-heavy test surfaces, so the merge relied on the passing changed
  governance surface plus the green PR CI matrix rather than the pre-existing
  full-suite failures.
- `#1110` remains open. Rollback/deactivation, long-running maintenance
  rechecks, legacy service and binstub retirement, blocking guard enforcement,
  and rollback/retention documentation remain separate follow-on Phase 6 items.

### 2026-09-10 — Phase 6 item 1: maintenance gates and ownership sidecars

- Merged [#2329](https://github.com/ThomasMichon/copilot-extensions/pull/2329)
  at `f1087819216159ee241b875147a92d24198ea715`, landing the first operative
  Phase 6 slice for [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110).
- The shared `installation-context` primitive now exposes explicit
  `maintenance-enter`, `maintenance-status`, and `maintenance-release`
  actions; writes strict user-wide or plugin-scoped ownership sidecars with a
  random token; refuses management mutation unless the caller presents the
  matching token; reports stale sidecars without clearing them; and treats
  ambiguous or unreachable remote maintenance probes as quiesced/fail-closed.
- `activation-cas` now honors applicable maintenance before publishing a new
  activation receipt, and the first concrete consumer (`agent-machines`
  `cell-repair` / `cell-uninstall`) now requires the exact maintenance token
  when scoped maintenance is active. Vendored copies were synchronized across
  every current adopter, including the newly checked `agent-vault` copy.
- Validation:
  `python -m pytest -q libs/installation-context/tests/test_installation_mode_governance.py libs/installation-context/tests/test_vendoring.py`
  (`75 passed, 30 skipped`);
  `python tools/run-plugin-tests.py agent-machines`
  (`508 passed, 20 skipped`);
  `python tools/check-install-contract.py`,
  `python tools/check-version-consistency.py`,
  `python tools/check-vendored-libs-sync.py`,
  `python tools/sync-installation-context.py --check`,
  `python libs/payload-invocation/generate.py --all --check`,
  `python -m pytest -q libs/installer-readiness/tests`,
  `python tools/check-marketplace-isolation.py`,
  `python tools/check-docs-consistency.py`,
  `git diff --check`, and changed-file `ruff check --select F,E9` all passed;
  PR #2329 CI also passed its plugin matrix for agent-bridge, agent-codespaces,
  agent-containers, agent-index, agent-logger, agent-machines, agent-mcp,
  agent-ssh, agent-vault, and the agent-worktrees collect-only/Windows-launch
  lanes plus the shared guards/lint jobs.
- Native Windows full-suite spot checks still reproduced unchanged unrelated
  failures from `origin/main`: the previously tracked
  [#2159](https://github.com/ThomasMichon/copilot-extensions/issues/2159),
  [#2160](https://github.com/ThomasMichon/copilot-extensions/issues/2160),
  [#2214](https://github.com/ThomasMichon/copilot-extensions/issues/2214), and
  [#2240](https://github.com/ThomasMichon/copilot-extensions/issues/2240), plus
  the newly filed [#2327](https://github.com/ThomasMichon/copilot-extensions/issues/2327)
  and [#2328](https://github.com/ThomasMichon/copilot-extensions/issues/2328).
- `#1110` remains open. The remaining Phase 6 scope is unchanged: explicit
  legacy attribution/migration, rollback/deactivation, long-running maintenance
  rechecks, legacy service and binstub retirement, report-only guard
  enforcement, and rollback/retention documentation still need their own
  follow-on increments.

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
  at that point `#1108` remained open only for the unlanded `agent-dispatch`,
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

### 2026-09-09 — Agent-bridge service-boundary increment

- Merged [#2287](https://github.com/ThomasMichon/copilot-extensions/pull/2287)
  at `b44ddcb5f263a341f324334e9942f5d7cb4c345f`, landing the `agent-bridge`
  portion of `#1108`.
- `agent-bridge` now routes its payload-local command surface through the shared
  installation-context runtime gate and, when an explicit valid installation
  context is active, scopes its runtime root, `sessions.db`, routing table,
  host index, relay-port record, logs, provider registry, and lifecycle
  identities to the selected installation cell. Legacy execution with no
  installation context keeps the historical machine-global root and compatibility
  wrappers unchanged.
- The same increment adds install-root helpers that keep runtime and supervision
  naming aligned across Python, PowerShell, and POSIX install paths; the
  elevated sub-daemon now inherits the primary install identity; provider
  discovery rejects foreign standard `providers.d` roots; and scoped installs no
  longer claim the legacy global binstub reserved for legacy fallback.
- Validation:
  direct changed-surface Windows tests passed (`98 passed`);
  `python tools/run-plugin-tests.py agent-bridge` passed in six contained
  sub-suites (`567/307/320/622/377/29 passed`, `2/2/8/1/1/3 skipped`);
  `python tools/check-agent-bridge-contracts.py` and
  `python -m pytest -q tools/test_check_agent_bridge_contracts.py` passed
  (`16 passed`);
  focused WSL/POSIX changed-surface coverage passed (`48 passed, 3 skipped`);
  CI-targeted POSIX installer/payload coverage passed (`22 passed, 2 skipped`);
  consumer spot checks passed for `agent-codespaces` and `agent-containers`
  (`2 passed` each);
  and install-contract, version-consistency, vendored-lib,
  installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, and changed-file ruff guards passed.
- Audit note:
  the exact direct Windows command
  `PYTHONPATH=plugins/agent-bridge/src python -m pytest -q plugins/agent-bridge/tests`
  still reproduces an unrelated host-environment import mismatch (`ssh_manager`
  missing `CarrierRemoteError`) from unchanged collection paths; this is now
  tracked in [#2286](https://github.com/ThomasMichon/copilot-extensions/issues/2286).
- `#1108` remains open only for the deferred `agent-worktrees` status-monitor /
  Worktree Manager supervision slice.

### 2026-09-09 — Agent-worktrees service-boundary completion

- Merged [#2289](https://github.com/ThomasMichon/copilot-extensions/pull/2289)
  at `33772a7141e0f277b20ab5b234347551afdf4ca6`, landing the deferred
  `agent-worktrees` status-monitor / Worktree Manager supervision slice and
  completing the final remaining implementation scope in `#1108`.
- `agent-worktrees` now scopes the resident status-monitor lock, hook-IPC
  rendezvous record, hook-client runtime selection, and session-lifecycle
  snapshot reads/writes to the validated installation cell whenever explicit
  installation context is active. Cross-cell rendezvous records are rejected
  before dialing, while legacy execution with no installation context keeps the
  historical machine-global monitor and hook behavior unchanged.
- The same increment aligns the Worktree Manager compatibility runtime with the
  selected installation receipt, so the transplanted Picker reads the same
  cell-local `current-version` marker as the resident monitor and its hook
  clients instead of falling back to a checkout or legacy global runtime.
- Validation:
  direct Windows changed-surface coverage passed with
  `PYTHONPATH=plugins/agent-worktrees/src python -m pytest -q plugins/agent-worktrees/tests -k "hook_ipc or status_monitor or registry_paths or session_context_companions"`
  (`151 passed, 9 skipped`) plus the narrower hook/monitor rerun
  (`127 passed, 4 skipped`);
  targeted Worktree Manager compatibility coverage passed with
  `PYTHONPATH=worktree-manager/src python -m pytest -q worktree-manager/tests/test_production_picker_transplant.py -k "engine_runtime or explicit_context"`
  (`2 passed`);
  focused WSL/POSIX coverage passed with
  `uv run --directory plugins/agent-worktrees --extra dev python -m pytest -q tests -k 'hook_ipc or status_monitor or registry_paths or session_context_companions'`
  (`157 passed, 3 skipped`);
  and install-contract, version-consistency, vendored-lib,
  installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, and changed-file ruff guards passed.
- Audit note:
  `python tools/run-plugin-tests.py agent-worktrees` was attempted and again hit
  the known contained-runner wall-clock limit (`[LIMIT] wall-clock limit exceeded (300s)`)
  after sub-suite 5/7, matching the effort's existing validation notes for the
  `agent-worktrees` runner seam rather than a changed-surface test regression.
- No additional version-skew deferral was required for this slice; the Phase 4
  transfer of cell-qualified Git-ref leases to `#1110` remains unchanged.
- `#1108` is now complete and closed. Phase 4 complete; Phase 5 (Repository
  configuration and adoption state, [#1109](https://github.com/ThomasMichon/copilot-extensions/issues/1109))
  is next.

### 2026-09-09 — Agent-codespaces repository configuration and adoption increment

- Merged [#2291](https://github.com/ThomasMichon/copilot-extensions/pull/2291)
  at `cf5a4ce7bb5a8b705185e98a203e96ebfbb38ad3`, landing the
  `agent-codespaces` portion of `#1109`.
- `agent-codespaces` now reads repository policy from
  `.copilot-extensions/agent-codespaces/config.yaml` first, falls back to the
  legacy `.agent-codespaces/config.yaml` and repo-root `codespaces.yaml`
  locations, and accepts an explicit
  `.copilot-extensions/agent-codespaces/marketplaces/<marketplace-id>/config.yaml`
  overlay for marketplace-specific behavior without changing the neutral base
  policy file.
- The same increment moves namespaced machine-local adoption state under the
  owning installation cell's `repos/<stable-repo-id>/agent-codespaces/`
  subtree, keyed by normalized remote identity. Legacy
  `~/.agent-codespaces/adopted-repos.yaml` remains a bounded fallback until a
  repo is explicitly adopted into a cell, and install/update continues to leave
  committed repository configuration untouched.
- Validation:
  `python tools/run-plugin-tests.py agent-codespaces` reproduced the unchanged
  native-Windows failures tracked by
  [#2240](https://github.com/ThomasMichon/copilot-extensions/issues/2240)
  after the changed-surface files passed, so the landed evidence is the green
  changed-surface suite
  (`19 passed, 995 deselected`) plus focused WSL/POSIX coverage
  (`18 passed, 996 deselected`);
  install-contract, version-consistency, vendored-lib,
  installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, docs-consistency, diff-check, and changed-file ruff
  guards all passed.
- `#1109` remains open for the remaining repository-configuration readers and
  adoption surfaces outside `agent-codespaces`, so Phase 5 is still in
  progress and all three plan checkboxes remain open.

### 2026-09-09 — Agent-bridge and agent-index repository configuration increment

- Merged [#2293](https://github.com/ThomasMichon/copilot-extensions/pull/2293)
  at `cc336c01e02961d818c07bcc27d49954fb784b6c`, landing the
  `agent-bridge` and `agent-index` repository-configuration portion of `#1109`.
- `agent-bridge` now reads repo-owned spawn defaults from
  `.copilot-extensions/agent-bridge/config.yaml` first, falls back to legacy
  `.agent-bridge/config.yaml`, and accepts an explicit
  `.copilot-extensions/agent-bridge/marketplaces/<marketplace-id>/config.yaml`
  overlay so marketplace-specific behavior stays opt-in and separate from the
  neutral base policy file.
- `agent-index` now reads repo-owned indexer designation and corpus scope from
  `.copilot-extensions/agent-index/config.yaml` first, falls back to legacy
  `.agent-index/config.yaml`, and accepts an explicit
  `.copilot-extensions/agent-index/marketplaces/<marketplace-id>/config.yaml`
  overlay. Setup and runtime helper paths now publish the canonical file while
  preserving legacy reads, and install/update still never rewrites committed
  repository configuration.
- Inventory follow-up for this slice confirmed that `efforts` already uses the
  neutral `.copilot-extensions/efforts/config.json` path; `visions` and
  `harness-knowledge` do not own committed plugin config or plugin-local
  adoption state; `agent-machines` still carries an unconverted committed
  `.agent-machines/` repo layout; `agent-dispatch` still carries unconverted
  committed `.agent-dispatch/registrar/` and `.agent-dispatch/identities/`
  surfaces; and `agent-worktrees` already covers cell-local adoption-state
  keying from Phase 4 but still has unconverted committed `.agent-worktrees/`
  repo config surfaces.
- Validation:
  `python tools/run-plugin-tests.py agent-index`;
  `python tools/run-plugin-tests.py agent-bridge`;
  focused WSL/POSIX coverage
  (`agent-index` repo-config and bash runtime-gate cases, `agent-bridge`
  in-repo config cases);
  install-contract, version-consistency, vendored-lib,
  installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, docs-consistency, diff-check, changed-file ruff, and
  `check-agent-bridge-contracts --base origin/main` all passed; GitHub Actions
  checks for `#2293` also passed before merge.
- `#1109` remains open. Phase 5 still needs the remaining committed
  repository-configuration surfaces in `agent-worktrees`, `agent-dispatch`, and
  `agent-machines`; no new version-skew enforcement or migration work was
  started here, so the Phase 6 boundary remains unchanged.

### 2026-09-09 — Phase 5 completion: agent-worktrees, agent-dispatch, and agent-machines

- Merged [#2296](https://github.com/ThomasMichon/copilot-extensions/pull/2296)
  at `94f5f09f309f5d7c5a9f7186179bae79d755e657`, completing the remaining
  committed repository-configuration scope in `#1109`.
- `agent-worktrees` now reads repository-owned settings from
  `.copilot-extensions/agent-worktrees/config.yaml` first, keeps legacy
  `.agent-worktrees/config.yaml` and `.agent-worktrees.yaml` readable, and
  reads related-repo config from
  `.copilot-extensions/agent-worktrees/related.yaml` first while preserving
  legacy `.agent-worktrees/related.yaml` compatibility. Both surfaces accept
  explicit opt-in overlays under
  `.copilot-extensions/agent-worktrees/marketplaces/<marketplace-id>/...`.
  Phase 4's machine-local cell-owned project state remains unchanged.
- `agent-dispatch` now reads repo-owned registrar declarations and worker
  identities from `.copilot-extensions/agent-dispatch/registrar/` and
  `.copilot-extensions/agent-dispatch/identities/`, keeps legacy
  `.agent-dispatch/registrar/` and `.agent-dispatch/identities/` readable, and
  accepts explicit opt-in overlays under
  `.copilot-extensions/agent-dispatch/marketplaces/<marketplace-id>/...`.
- `agent-machines` now treats `.copilot-extensions/agent-machines/` as the
  canonical committed package root, keeps legacy `.agent-machines/` and
  `.github/machine-state/` as bounded fallbacks, accepts explicit opt-in
  overlays under
  `.copilot-extensions/agent-machines/marketplaces/<marketplace-id>/`, and
  preserves supplemental knowledge-repo grafting after the
  `agent-worktrees` repo-config move.
- Inventory closure for `#1109`:
  committed plugin configuration now uses the `.copilot-extensions/<plugin>/`
  namespace across all applicable Phase 5 plugins; committed repository policy
  remains distribution-neutral with explicit marketplace overlays only; and the
  machine-local project-state item was already satisfied by the earlier
  `agent-worktrees` / `agent-codespaces` adoption-state work.
- Validation:
  `python tools/run-plugin-tests.py agent-machines`
  (`507 passed, 20 skipped`);
  `python tools/run-plugin-tests.py agent-dispatch`
  reproduced the unchanged pre-existing
  `test_agent_index_managed.py::test_shipped_index_declaration_preserves_version_and_source_authority`
  failure tracked in [#2295](https://github.com/ThomasMichon/copilot-extensions/issues/2295),
  with the failing declaration/version files unchanged in this branch relative
  to `origin/main`;
  `python tools/run-plugin-tests.py agent-worktrees`
  again hit the existing contained-runner wall-clock limit during sub-suite 5/7
  after the changed surfaces passed;
  focused Windows and WSL/POSIX changed-surface coverage passed for all three
  plugins; and install-contract, version-consistency, vendored-lib,
  installation-context, payload-invocation, installer-readiness,
  marketplace-isolation, docs-consistency, diff-check, and changed-file ruff
  guards all passed.
- No new version-skew activation or migration enforcement was started here; the
  remaining maintenance, migration, rollback, and cleanup work stays in
  `#1110`.
- `#1109` is now complete and closed. Phase 5 complete; Phase 6 (Migration,
  enforcement, and cleanup, [#1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110))
  is next.
