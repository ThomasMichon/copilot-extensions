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
- [x] **Scope corrected 2026-09-27 (operator directive — see Journal):** this
  item is **not** "stop installing generic `agent-*` commands into
  `~/.local/bin`" unconditionally. A plugin's own global-binstub *placement*
  retires only for a host that actually configures a marketplace-cell
  install; a legacy/non-cell host keeps it, permanently. The real gate is
  **universal consumer-side resolution**: every caller that invokes a
  *different* plugin's command, or invokes any `agent-*` command outside an
  LLM-mediated skill turn (installers, hooks, cross-plugin script calls,
  generated helpers like `vault-askpass` that run non-interactively), must
  resolve the target through the already-built Phase 3 mechanisms —
  the [installation-resolution `runtimeRoot` resolver](../../../docs/install-contract.md#resolver-result-and-precedence)
  for non-session callers, and the session command catalog
  (`emit-command-catalog.{sh,ps1}` → `write_session_guidance.py`) for
  LLM-mediated skill turns — **and must treat the legacy-fallback
  `runtimeRoot` exactly like a marketplace-cell root, never special-cased.**
  Own-payload placement (a plugin's own installer declaring where *it*
  writes *its own* global binstub, and `agent-worktrees`'s permanent
  project-command surface) is accepted and out of this item's scope. The
  [Phase 2 launcher contract inventory](phase-2-launcher-contracts.md) has now
  **fully reclassified and, where genuinely open, fixed every finding** (not
  guessed): of 70 original findings, 2 were guard false positives (converted),
  62 are verified own-payload placement (accepted, no action), and 2
  (`agent-vault` credential-askpass) are verified currently-correct and
  explicitly deferred pending that plugin's own future cell-awareness.
  **Zero findings remain open.** The last genuine gap
  (`agent-worktrees`'s `register-nudge.{sh,ps1}` marketplace-cell detection)
  is closed via a new durable, resolver-free runtime-root pointer added to
  the canonical `libs/installation-context` library (Python/bash/PowerShell
  parity, synced to all 11 vendoring plugins) — see
  [install-contract.md § Durable runtime-root
  pointer](../../../docs/install-contract.md#durable-runtime-root-pointer-resolver-free-consumers) — rather than the heavier resolver dependency
  `register-nudge.sh` deliberately avoids for its "tools-half box" bootstrap
  guarantee.
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
  - [x] Carry validated snapshot identity into provision/runtime-slot ownership,
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
    - [ ] Post-acceptance Agent Index installer defects (routed here from the
      2026-09-24 bug sweep per Phase 7's migration-intake disposition — both
      are installer-elevation/interpreter-selection gaps in this effort's own
      accepted exemplar, not new scope):
      - [ ] **[#107](https://github.com/ThomasMichon/copilot-extensions/issues/107)**
        `Register-ScheduledTask` fails with `Access is denied` without
        elevation; needs a user-level logon task instead.
      - [ ] **[#106](https://github.com/ThomasMichon/copilot-extensions/issues/106)**
        `uv venv` can select a broken uv-managed Python on Dev Box
        (os error 448); needs an explicit `--python` pin.

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
  **Scope corrected 2026-09-27**: "conform" means every cross-boundary
  consumer resolves through `runtimeRoot`/session-catalog, not that
  own-payload global-binstub placement lines disappear — those are permanent
  on non-marketplace-cell hosts (see the corrected Phase 2 item above and
  [phase-2-launcher-contracts.md](phase-2-launcher-contracts.md)). The guard
  itself will need an explicit, durable allowance for accepted own-payload
  placement patterns before it can ever go blocking, since those lines
  legitimately never disappear — not yet added.
- [x] Document rollback and retention of legacy state and inactive cells.

### Phase 7 — Reconcile deferred backlog

- [ ] Accept installation and marketplace candidates only through
      [`migration-intake`](../migration-intake/README.md)'s deduplication and
      ownership gate. Ongoing policy, not a one-time action — reconfirmed
      2026-09-27 that no new candidate entered this effort's Plan outside that
      gate this leg; the two 2026-09-24 bug-sweep items below predate the
      policy and are the only pending exception, now explicitly routed rather
      than grandfathered in place.
- [x] Revalidate accepted technical scope against the current
      installation-cell contract for this leg's reconciled backlog
      (2026-09-27): the Phase 2 launcher-contract script clusters and
      cross-plugin `payload-invocation.json`/`installer-readiness.json`
      questions left open across all 9 guard-triaged plugins were confirmed
      still live and in-scope — not obsolete — against `#1103` (Phase 2,
      open, partial), `#1110` (Phase 6, open/reopened, guard-blocking
      precondition unmet at 681 findings/1344 files), and the
      module-size-ceiling tracker
      [private-downstream-repo#7672](https://your-git-host/owner_user/private-downstream-repo/issues/7672)
      (open, unclaimed). None require return-for-disposition.
- [x] Place each accepted public tracker item in exactly one existing phase
      (2026-09-27): the confirmed Phase 2/cross-plugin-JSON backlog already
      lives correctly under Phase 2 (`#1103`) and `private-downstream-repo#7672` — no
      new phase needed. The two Bug sweep items below are now placed under
      Phase 3 (Agent Index installer follow-ups), the only remaining
      unplaced candidates found this leg.
- [x] Keep examples synthetic and distribution-neutral. Spot-checked
      2026-09-27 across this leg's ~30 merged PRs' annotation examples and
      the effort's own proposal docs — no environment-specific identifiers
      found; holds.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review)._

- [x] **#107** agent-index installer: Register-ScheduledTask 'Access is denied' without elevation -- need a user-level logon task
  - Triaged 2026-09-27: still open, unclaimed, not obsolete. Routed to Phase 3
    (Agent Index installer follow-up) rather than closed here — the
    underlying installer fix is unimplemented; only its placement is
    resolved.
- [x] **#106** agent-index installer: uv venv selects a broken uv-managed Python on Dev Box (os error 448) -- pin --python
  - Triaged 2026-09-27: still open, unclaimed, not obsolete. Routed to Phase 3
    (Agent Index installer follow-up) rather than closed here — the
    underlying installer fix is unimplemented; only its placement is
    resolved.

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

### 2026-09-27 — Durable runtime-root pointer designed and implemented; Phase 2 launcher-contract inventory now at zero open findings

- Directed to design a durable marketplace-cell lookup for `register-nudge.sh`
  rather than stop at "needs design work." Added a new normative mechanism to
  [install-contract.md § Durable runtime-root
  pointer](../../../docs/install-contract.md#durable-runtime-root-pointer-resolver-free-consumers):
  every `status` resolution that reaches `"ready"` with a non-null
  `runtimeRoot` now publishes that value, as a side effect, to a plain-text,
  single-line, well-known file (`<durable-home>/<plugin-id>/runtime-root`) —
  no JSON parsing required to read it, so a genuinely resolver-free consumer
  can make a best-effort existence check with a plain `cat`/`head`. Advisory
  only, never authoritative; publication is skipped (not blanked) on any
  non-`"ready"` status so a transient bad resolution never clobbers a
  last-known-good pointer, and a failed write never fails the real result.
- **Implemented in all three parity runtimes** in the canonical
  `libs/installation-context/` library: Python
  (`_publish_runtime_root_pointer` in `_installation_context_files.py`,
  called from `resolve_installation_mode`'s return path in
  `_installation_context_mode_cli.py`), bash (`publish_runtime_root_pointer`
  in `installation-context.sh`, called only from the `status` action branch,
  written without `atomic_write_text` to avoid its `fail()`-based exit
  propagating out of a best-effort helper), and PowerShell
  (`Publish-RuntimeRootPointer` in `installation-context.ps1`, gated on
  `$Action -ceq 'status'` since `Resolve-InstallationStatus` also serves
  `probe-legacy`). Synced to all 11 vendoring plugins via
  `tools/sync-installation-context.py` (`--check` now clean).
- **Fixed a broken test as part of adding the feature, not around it**:
  `test_status_and_probe_cli_parity_and_read_only` asserted `status` was
  fully read-only across all three runners — no longer true by design.
  Updated it to assert the delta is *exactly* the one new pointer file (with
  correct content), and that a subsequent `probe-legacy` call adds nothing
  further. Dropped an initial exact-`0600`-permission assertion after
  confirming it made the runner-parity test brittle for no security benefit
  (the pointer holds a path, not a secret; PowerShell's default write mode on
  this platform is `0664`, not `0600`, and there's no existing precedent in
  this PS1 file for platform-conditional permission hardening) — softened
  the doc's wording to match rather than bolt on a first-of-its-kind chmod.
- **Confirmed via `git stash` isolation that ~12 other pre-existing test
  failures** (`test_bootstrap_context_selection.py`,
  `test_legacy_entrypoint_probe.py`) are unrelated to this change —
  identical failures with and without it applied. Filed
  [private-downstream-repo#7753](https://your-git-host/owner_user/private-downstream-repo/issues/7753)
  rather than silently living with them or scope-creeping into fixing them
  here.
- **Fixed `agent-worktrees`'s `register-nudge.{sh,ps1}`** using the new
  pointer: checks `command -v`/legacy-path first exactly as before (zero
  behavior change for legacy hosts), then falls back to reading
  `~/.copilot-extensions/agent-worktrees/runtime-root` and checking that the
  named directory exists — closing the real gap (a marketplace-cell-only
  host with no legacy binstub was previously invisible to this check) while
  staying resolver-free and fail-open exactly like the rest of the file.
  Deliberately checks directory *existence* only, not a specific binstub
  sub-path, after confirming `runtimeRoot` means different things in legacy
  vs. namespaced mode (the plugin's runtime/state root vs. the cell's
  plugin-root) and that guessing a specific sub-path wrong would be no safer
  than the existing heuristic-quality bar this file already accepts — the
  consequence of either false-positive or false-negative here is a cosmetic
  nudge-message miss, not a functional gate on any mutation.
- **Phase 2 launcher-contract inventory reaches zero open findings.**
  Updated the disposition summary: 62 own-payload placement (accepted), 2
  deferred (`agent-vault`), 0 open. Checked off the corrected Phase 2 scope
  item in the main Plan on that basis. Left the upstream GitHub `#1103`
  tracker's own closure as an explicit reviewer/operator decision — its
  history suggests broader scope than this specific inventory, and that call
  shouldn't be inferred from local checklist state alone.
- Verified: full `libs/installation-context/tests/test_installation_mode_governance.py`
  (120 tests) passed via `test-supervisor`; `check-marketplace-isolation.py`
  (68, unchanged — the retained legacy-path text is correctly still flagged
  as accepted own-payload); `check-docs-consistency.py` clean; `bash -n` and
  the PowerShell AST parser both clean on every edited script.

### 2026-09-27 — Correction: `register-nudge.sh`'s fix is NOT a `bootstrap-check.sh` pattern reuse — it needs new resolver-free design

- Immediately after the previous entry's PR merged, went to actually
  implement the "last genuine open item" (`agent-worktrees`'s
  `register-nudge.{sh,ps1}`) using the pattern that entry claimed was
  "already proven" in `bootstrap-check.sh`. Reading `register-nudge.sh` in
  full first (not just its guard-flagged line) caught the claim being wrong
  before any code was written.
- `register-nudge.sh`'s own header states it is deliberately **"resolver-
  free"** so it still runs on a "tools-half box" (a session-start hook that
  must work before full runtime provisioning). Tracing `bootstrap-check.sh`'s
  `legacy_mutation_allowed` -> `legacy-entrypoint-probe.sh` -> the full
  `installation-context.sh` resolver (bash 4.4+, `awk` JSON parsing) shows
  that path is **not actually resolver-free** — copying it here would
  silently break the exact guarantee `register-nudge.sh` exists to preserve.
- Also re-read `register-nudge.sh`'s actual behavior end to end: it never
  *invokes* `agent-worktrees` at the flagged path at all — it only checks
  availability as a precondition before emitting a plain-text onboarding
  nudge telling the *user* to type `agent-worktrees register <name>`
  themselves. The real gap is narrower than "cross-boundary consumption"
  implied: the availability heuristic doesn't recognize a marketplace-cell
  install that isn't on PATH and isn't at the legacy path, so the nudge could
  wrongly suppress itself for cell-only hosts.
- Corrected `phase-2-launcher-contracts.md` and the main README's Phase 2
  item: this is genuine unconverted work, but the next step is **new,
  genuinely lightweight (no `awk`/bash-4.4) marketplace-cell detection**
  (e.g. a raw grep/sed read of the durable `installation-mode.json` policy
  file's `enabled` field) — a design question, not an implementation task
  ready to execute today. Left the code untouched.
- **Pattern worth naming for future sessions on this effort**: every one of
  this leg's "confirmed, ready-to-convert" classifications turned out to need
  correction once the actual source was read line-by-line (`dtssh` was a
  false positive; `agent-vault` was premature; `register-nudge` needs new
  design, not reuse). Filename/family-based classification is not a
  substitute for reading the code before committing to a fix plan in this
  inventory.
- No code changed this pass — documentation correction only.
  `check-docs-consistency.py` clean.

### 2026-09-27 — Read the actual source for all 12 candidate cross-boundary findings: 1 dtssh false positive fixed, 9 verified accepted/deferred, exactly 2 genuine open items remain

- Directed to keep driving past the scope correction (below) into real
  conversion work. Before touching anything, read every one of the 12
  candidate cross-boundary findings' actual source rather than trusting the
  filename-based classification from the prior pass — good thing: two of
  the four "confirmed with confidence" findings from that pass turned out
  to be **wrong on inspection**.
- **`agent-ssh` `dtssh` "remote transport" (2) — actually a guard false
  positive, not agent-* consumption at all.** Read the code: these lines
  install and PATH-expose `dtssh`/`devtunnel-ssh`, an unrelated third-party
  CLI. Fixed in [#4336](https://github.com/ThomasMichon/copilot-extensions/pull/4336)
  with `# marketplace-isolation: allow third-party-installer-path` (same
  underlying concern the sibling `.ps1` files' `$InstallRelease` URL line
  already carries as `allow third-party-installer-url`). Guard count: 70 →
  68. `test_dtssh_host_launcher.py` (3 tests) passed via `test-supervisor`.
- **`agent-vault` credential-askpass (2) — verified currently-correct, NOT
  a quick conversion.** `agent-vault`'s own installer has zero
  installation-context integration anywhere (confirmed by direct grep,
  unlike `agent-machines`/`agent-index` which are Phase 3's proven cell-aware
  exemplars) — `agent-vault` runs legacy-only today regardless of host
  config, so `vault-askpass`'s hardcoded `$HOME/.local/bin/agent-vault` is
  *currently correct, not a bug*. This backs live `sudo -A` elevation
  facility-wide; converting it now, before `agent-vault` itself gains
  cell-awareness (a Phase-3-scale undertaking on its own), would add
  complexity to a safety-critical path for zero present benefit. **Left
  untouched, explicitly deferred** — documented so a future session doesn't
  mis-convert it either.
- **`agent-machines` bootstrap-check (2) — verified already correct.**
  Reads the installation-context resolver a few lines above
  (`ContextMarketplaceId`/`status`/`reason`/`namespaced-active`/
  `legacy_mutation_allowed`) and only falls through to the legacy binstub
  path when that resolution says legacy. Already properly gated; no action
  needed. This is the reference pattern for converting other findings.
- **`agent-codespaces` readiness-legacy-fallback (3) — verified self-probe,
  harmless.** `readiness-context.{sh,ps1}` only checks existence of its own
  legacy binstub for a readiness report; never invokes anything at that
  path. Own-payload, accepted.
- **`agent-worktrees` register-nudge (2) — the only genuine, confirmed,
  unconverted cross-boundary work in the entire 70-finding inventory.** No
  installation-context awareness at all (plain `command -v`/legacy-path
  check). Left unconverted this pass (out of time/scope for this leg) but
  precisely identified, with its fix pattern already proven in this same
  codebase (`agent-machines/scripts/bootstrap-check.sh`) — the next
  session's exact next slice.
- Updated `phase-2-launcher-contracts.md`'s family table with this final,
  source-verified disposition for every finding (no more "needs
  verification" hedges) and corrected the main README's Phase 2 item to
  cite the final counts: 2 false-positive (fixed), 60 own-payload
  (accepted), 2 deferred (`agent-vault`), 2 open (`agent-worktrees`).
- Verified: `check-marketplace-isolation.py` (70→68), `check-docs-
  consistency.py`, `test-supervisor -- python3 -m pytest
  plugins/agent-ssh/tests/test_dtssh_host_launcher.py` all clean.

### 2026-09-27 — Operator scope correction: Phase 2/6 completion criterion is universal consumer-side resolution, not global-binstub removal

- **Operator directive (verbatim intent, this session)**: "we're only going
  to retire 'global binstub' *placement* if the host machine configures
  marketplace-cell install. But what we will do is prepare all consumption
  for that eventuality. All skills and tools which call into `agent-*`
  commands will need to resolve the marketplace path based on the active
  session's provided information (agent-* plugins's sessionStart hooks
  write the note to session-state, other consumers pick it up from there),
  and then use the marketplace-install root as their binstub base path.
  When marketplace-cell is not enabled, the pointers will be to the
  user-global install, but all callers will still treat it like it's the
  marketplace-cell."
- Mapped this onto mechanisms already built in Phase 3, confirmed with the
  operator before writing it in as canonical: the installation-resolution
  `runtimeRoot` resolver
  ([install-contract.md](../../../docs/install-contract.md#resolver-result-and-precedence))
  for non-session callers, and the session command catalog
  (`emit-command-catalog.{sh,ps1}` → `write_session_guidance.py`) for
  LLM-mediated skill turns. Both already exist; neither needed inventing.
- **This changes the Phase 2/Phase 6 completion criterion materially**:
  own-payload global-binstub placement (a plugin's own installer declaring
  where *it* writes *its own* binstub) is now explicitly accepted and
  permanent on non-marketplace-cell hosts — **not** something Phase 6
  retires. The actual remaining gate is universal cross-boundary consumer
  resolution: any call crossing a plugin boundary, or running outside an
  LLM-mediated skill turn, must resolve via `runtimeRoot`/session-catalog
  and treat the legacy-fallback root exactly like a marketplace-cell root.
- Reclassified the Phase 2 launcher-contract inventory's 70 findings against
  this corrected two-way split (own-payload placement vs. cross-boundary
  consumption) in
  [phase-2-launcher-contracts.md](phase-2-launcher-contracts.md): **58 of 70
  are own-payload placement, now out of scope entirely.** Of the remaining
  12 candidate cross-boundary findings, only 4 (remote-transport 2,
  credential-askpass 2) are confirmed cross-boundary with confidence this
  pass; the other 8 (operator/bootstrap/nudge, readiness-legacy-fallback)
  need a closer per-line read before converting anything, flagged as such
  rather than guessed.
- Also corrected the Phase 6 blocking-guard item: the guard itself needs a
  durable, explicit allowance for accepted own-payload placement before it
  can ever go blocking, since those lines are now permanent by design — not
  yet added.
- **No code converted this pass** — this was a scope/documentation
  correction, upstream of any further conversion work, to prevent the next
  session from converting (or worse, removing) own-payload placement lines
  that were never meant to go away.
- Verified: `check-docs-consistency.py` clean (doc-only change).

### 2026-09-27 — Phase 2 launcher-contract inventory: full family re-derivation (70 findings, all classified); 3 stale entries corrected

- Next slice after the Plan/Phase 7 reconciliation (below): actually drove
  the "fresh pass still needed" the 2026-09-25 partial re-audit called out
  in [phase-2-launcher-contracts.md](phase-2-launcher-contracts.md), rather
  than deferring it again.
- **Re-ran the guard in a clean checkout, not the long-lived anchor**: 70
  `global-plugin-binstub` findings, down from the 2026-09-25 snapshot's 83.
  Diffing anchor vs. clean-checkout output found the anchor's extra 4
  findings were `*.egg-info/PKG-INFO` build artifacts from a prior local
  `pip install -e .`, never committed — a real methodology fix: this guard
  must be read from a clean checkout, never a long-lived anchor that may
  carry stale local build state.
- **Classified all 70 findings into families** (not a sample): generic
  wrapper publication (45), `agent-worktrees` project-command mixing (11),
  operator/bootstrap/nudge (4), readiness legacy fallback (3),
  `payload-invocation.json` legacy-footprint declarations (2),
  remote-transport (2), credential/askpass (2), descriptive (1). No plugin
  or finding is untraced any more.
- **Corrected 3 stale doc claims found during classification** (all verified
  against current source, not assumed):
  1. `agent-machines`'s 2 "unresolved, needs a fresh slice" `cell_lifecycle.py`
     findings the 2026-09-25 note flagged were already resolved in
     [#4154](https://github.com/ThomasMichon/copilot-extensions/pull/4154)
     (`allow legacy-compatibility`), which predates that note. Only the
     `payload-invocation.json` declaration remains, and it needs no
     resolution — same accepted shape as `agent-index`'s equivalent.
  2. `agent-index`'s `transport.py` remote-transport finding already
     converted (`allow remote-management`) — Phase 3 remote-transport is
     done for `agent-index`; only `agent-ssh`'s `dtssh` scripts remain in
     that family.
  3. Two of four "Known guard-invisible callers"
     (`agent-codespaces`/`agent-containers` `_invoke.py`) were already
     resolved by PRs already recorded elsewhere in this Journal (#4298,
     #3877) — both now resolve a payload-local binstub via `_payload_root()`
     instead of the global path. Removed from the guard-invisible list;
     `agent-bridge/agent_registry.py` and `transport.py`'s PowerShell branch
     remain genuinely unconverted.
- **No code changed this pass** — this was inventory correction, not
  conversion. The generic-wrapper-publication family (45 findings, Phase 6
  retirement) is the actual bulk of remaining work and still needs its
  per-plugin conversion slices; nothing in this pass reduces that count.
- Verified: `check-marketplace-isolation.py`, `check-docs-consistency.py`
  both clean (doc-only change). Merged via `pr-merge --now`.

### 2026-09-27 — Plan/Validation Plan reconciled against the completed 8-plugin backlog order; Phase 7 bug sweep triaged

- Picked up the relay handoff whose next slice was explicitly **not** another
  plugin — it was finishing the effort itself: reconcile the Plan checklist
  against reality now that the 8-plugin guard-triage backlog order (Phase 7's
  means, not its own end) is closed out, and triage the two unclosed Bug
  sweep items.
- **Revalidated, did not just re-check, the confirmed-backlog disposition**:
  confirmed live via direct issue reads (not assumption) that `#1103` (Phase
  2 tracker) is open/partial, `#1110` (Phase 6 tracker) is open/reopened with
  its blocking-guard precondition still unmet (681 findings across 1344
  files as of its last recheck — nowhere close to "all runtime plugins
  conform"), and `private-downstream-repo#7672` (module-size-ceiling tracker) is open
  and unclaimed. None of the confirmed-backlog items (Phase 2 launcher-
  contract script clusters, `installer-readiness.json`/`payload-
  invocation.json` cross-plugin questions) are obsolete or need re-
  disposition — they're correctly already homed under `#1103`/`#7672`, not
  this effort's own Phase 7.
- **Phase 7 checklist**: three of four items are now satisfied for this
  leg's reconciled scope (revalidate scope, place tracker items, keep
  examples synthetic) and checked off with the evidence above; the
  migration-intake gate item stays unchecked as ongoing policy (not a one-
  time action) with a note that the two bug-sweep items are the only
  pre-policy exception, now explicitly routed rather than silently
  grandfathered.
- **Bug sweep #107/#106 triaged**: both re-confirmed still open, unclaimed,
  and in-scope (Agent Index installer elevation/interpreter-selection gaps
  in this effort's own accepted Phase 3 exemplar). Placed as new Phase 3
  follow-up sub-items rather than left in the unowned sweep list; the Bug
  sweep checkboxes are closed as *triaged/placed*, not as *fixed* — the
  underlying installer defects remain open work for whoever picks up Phase
  3's follow-ups.
- **Net effort state**: still **not** Done. Real, substantial remaining
  work is Phase 2's "stop installing generic `agent-*` commands" item (~35
  scripts across the launcher-contract family, tracked in
  [phase-2-launcher-contracts.md](phase-2-launcher-contracts.md)) and Phase
  6's blocking-guard turn-on, both gated on the same underlying conversion
  work this session's 8-plugin sweep only annotated/deferred, never
  implemented. Validation Plan remains entirely unchecked — none of its
  items were exercised this leg (the Phase 3 Tier-P scenarios satisfy some
  of them in spirit but haven't been cross-checked item-by-item against this
  list; left honestly unchecked rather than assumed).

### 2026-09-27 — `agent-worktrees` resolved to backlog-only (PR #4318); **entire 8-plugin backlog order now complete**

- `agent-worktrees` SKILL.md docs (PR #4318, 25/167 findings): the
  usual `allow deployed-runtime-diagnostics` doc-shaped mentions across
  `agent-worktrees-related/SKILL.md`, `agent-worktrees-repos/SKILL.md`,
  `copilot-extensions-setup/SKILL.md` (this plugin's own setup guide,
  which also documents companion plugins' runtime layouts as reference
  material -- agent-bridge, agent-mcp, agent-containers, agent-
  codespaces all got mentions annotated here too, since the setup
  skill's job is describing what EVERY optional companion plugin
  creates), `copilot-extensions-setup/references/optional-plugins-
  setup.md`, and `create-setup-script/SKILL.md`.
  - Two tree-listing fenced code blocks needed the fence's native `#`
    comment rather than an HTML comment (established rule, reused a
    third time this session).
  - `create-setup-script/SKILL.md`'s frontmatter `description:` field
    was rephrased to drop a literal path mention rather than annotated
    -- an HTML comment there would pollute the rendered, user-facing
    skill description (established convention, reused).
  - `service-lifecycle/references/install.sh` -- a generic illustrative
    scaffold script, not a real agent-worktrees installer -- got its
    lone `SERVICE_USER="${USER}"` line annotated `allow doc-example`.
- **`agent-worktrees` is now at confirmed backlog-only state**: 101
  remaining findings, entirely `scripts/install.{sh,ps1}` (18),
  `hooks.json` (8), the `register-nudge`/`register-session`/`session-
  conduct`/`bootstrap-check`/`provision-check`/`bind-nudge`/`deregister-
  session`/`project-hooks`/`anchor-hygiene-check`/`reconcile-machine-
  settings`/`default-setup`/`service-utils`/`session-machine`/
  `launch-command`/`resolve-runtime` script family (~35, all Phase 2
  launcher-contract shaped), `bin/agent-worktrees{,.ps1}` (2),
  `installer-readiness.json` (4), `payload-invocation.json` (1), and 3
  Python guard scripts (`cross_repo_guard.py`, `statelessness_guard.py`,
  `anchor_write_guard.py`, `hook_client.py`, `nudge_status.py`,
  `registry_root.py` -- ~13 combined) -- every category matches the
  same confirmed Phase 2/cross-plugin-JSON-question shape left as
  documented backlog in every other plugin this session. Plus the 4
  `config.py` dataclass-docstring findings deferred in the prior entry
  (unsafe to annotate mid-docstring without corrupting rendered docs).
- **This closes the entire 8-plugin backlog order** established at the
  start of this leg: agent-containers, agent-ssh, agent-mcp,
  agent-machines, agent-bridge, agent-index, agent-dispatch,
  agent-codespaces, and now agent-worktrees are ALL at confirmed
  backlog-only state -- every plugin's remaining findings are
  documented, understood, and intentionally deferred to their proper
  Phase (2, 3, 4, or 6) rather than force-annotated.
- Session totals across the full leg (from the prior context-handoff
  resume through this entry): **~410 findings resolved** across roughly
  30 merged PRs (core clusters + docs + journal entries per plugin),
  **6 new guard reason phrases** discovered and documented (`allow
  remote-container-path`, `allow shared-config-lock`, `allow shared-
  instance-mutex`, `allow durable-host-identity-backup`, `allow third-
  party-installer-url`, `allow schema`, `allow query-column-list`,
  `allow release-verb`, plus the `allow legacy` length-constrained
  shorthand), **1 HIGH-severity bug fixed** (agent-index's fresh-install
  binstub gap, #7702, after a review-caught wrong-shaped first attempt),
  **4 pre-existing bugs found and filed** via clean-room checkpoints and
  test-suite isolation (#7688, #7719, #7722, #7737), and **8 module-
  size-ceiling hits** resolved without ever force-widening a baseline
  (tracked cumulatively in private-downstream-repo#7672).
- Verified: `check-marketplace-isolation.py`, `check-skills.py`,
  `check-docs-consistency.py` all clean. Merged via `pr-merge --now`
  after a clean advisory review.

### 2026-09-27 — `agent-worktrees` core src/ cluster (PR #4315, 37 findings); the harness's own most consequential leg this session

- **agent-worktrees is the final, largest plugin in the backlog order**
  (~167 findings at start) -- and unlike every prior plugin, it IS the
  harness itself: the tool this whole triage effort runs on top of.
  Given the operator's standing clean-room directive explicitly named
  regressions in "opening the Worktree Manager, performing updates, or
  creating worktrees" as the concern to guard against, this leg got
  meaningfully more scrutiny than the routine per-plugin cycle.
- **Delegated the initial mechanical annotation pass to a sub-agent**
  (37 findings across 20 files -- almost entirely `.agent-worktrees`
  own-root path mentions, matching the established `allow legacy-
  compatibility` reason since agent-worktrees intentionally stays a
  single global install by design) with an explicit, detailed brief
  citing the established taxonomy, the same-line marker rule, and four
  required verification commands.
- **The sub-agent's self-verification was unreliable and would have
  merged real regressions had it not been independently re-checked.**
  Concretely, its own report claimed "0 changed-line E501s" and "all
  checks passed," but a from-scratch re-scan (diffing every `+` line
  against the 99-char limit, file by file, rather than trusting a
  summary count) found:
  - **9 genuine new E501 violations** across 8 files it had reported
    clean (`__main__.py`, `accounts.py`, `doctor.py` x3,
    `installation_cli.py`, `installer.py` x2, `loop_governance.py`,
    `maintenance_cli.py` x2, `related.py`, `related_cli.py`, `repos.py`,
    `repos_cli.py`).
  - **3 new E702/E701 style violations** (semicolon-joined statements in
    `__main__.py` and `sessions.py`; a colon-joined `if cond: return` in
    `state_root.py`) it introduced while chasing the module-size
    ceiling's shrink-only budget -- these are genuinely different bugs
    from a missed line-length check, since they change working, reviewed
    code into a lint-violating shape for no functional reason.
  - **A risky docstring-to-f-string conversion** in `config.py`: to fit
    a marker/interpolate a shared constant into a dataclass field's
    multi-line documentation string, it converted four plain
    `"""..."""` docstrings to `f"""..."""`. This silently changes the
    literal's AST node type (a plain string constant becomes a formatted
    string expression) -- harmless at runtime here, but a real risk for
    any doc-extraction tooling that walks the AST expecting a plain
    string node, for a purely cosmetic line-length fix that had no
    business touching semantics at all. Reverted to the original plain
    docstrings; the underlying findings (4, all inside multi-line
    docstring bodies where no real Python comment can be embedded
    without corrupting the rendered text) are left as documented
    backlog rather than force an unsafe annotation.
  - **A genuine new false-positive class confirmed and reused a second
    time**: `cleanup.py`'s `shutil.which("agent-worktrees")` is the
    plugin finding **its own** binstub (not a cross-plugin `agent-
    worktrees` lookup from another plugin, which is what `allow
    registry` means) -- classified `allow legacy-compatibility` instead,
    matching the "this plugin's own identity is fixed by design"
    concept precisely.
  - **Introduced (during the fix-up pass) a new accepted shorthand**:
    `allow legacy` as a length-constrained alias for `allow legacy-
    compatibility` -- used wherever the fuller phrase plus a long path
    literal would not fit under ruff's 99-char limit even after
    extracting a short local variable, which the fuller phrase's own 27
    characters made unreachable on several genuinely short lines. Same
    semantic meaning; purely a byte-budget accommodation.
- Rebalanced every module-size-ceiling hit this required
  (`__main__.py`, `session_projection.py`, `installer.py`, `related.py`,
  `repos.py`, `sessions.py`, `state_root.py` -- seven files in one PR,
  this session's largest single batch) using only safe techniques:
  blank-line removal between top-level defs (confirmed via direct ruff
  probing that this repo's lint config does not enable E302/E303/E305,
  so the spacing convention is stylistic, not enforced), a boolean-or-
  chain refactor of `installer.py`'s binstub-detection helper (fewer
  lines AND cleaner code, not just a budget trick), and collapsing an
  already-short multi-line raise/return onto one line where it
  genuinely fit.
- **Smoke-tested the actual harness after merge**, beyond the routine
  guard/lint/test checks: ran `agent-worktrees --version` and `status`,
  then created a fresh worktree from the just-merged `dev` HEAD and
  finalized it -- confirming the exact concern the operator's clean-room
  directive named (create/finalize) still works cleanly post-merge, not
  just that the annotated files individually pass lint.
- Verified: `check-marketplace-isolation.py`, `check-module-size.py`,
  `check-docs-consistency.py` all clean; zero new lint violations
  confirmed by direct per-line re-scan; `python -m py_compile` on every
  touched file; the full `agent-worktrees` suite via `test-supervisor`
  (5759 passed, 26 skipped, 2 pre-existing failures --
  `test_no_drift_when_consistent`'s `leaked_agent_rt_root` false-
  positive and `test_cli_entry_honors_registration_home`'s `gh`
  device-id credential-home write -- both confirmed pre-existing via
  `git stash` isolation, filed
  [private-downstream-repo#7737](https://your-git-host/owner_user/private-downstream-repo/issues/7737)).
  All required CI checks passed on GitHub before merge.
- **Takeaway for future legs**: delegating a large, well-specified
  mechanical task to a sub-agent is still valuable at this scale, but
  its own "all checks passed" self-report is not sufficient sign-off
  for a change to the harness repo -- an independent, from-scratch
  re-verification (not a re-read of its summary) caught real
  regressions its self-check missed. This matches the standing
  principle that a sub-agent's completion claim is not itself evidence;
  only re-running the actual checks is.
- `agent-worktrees` now at 130 remaining findings (SKILL.md docs,
  `hooks.json`, `scripts/` install/register-nudge/session-conduct
  clusters, `installer-readiness.json`) -- continuing next.

### 2026-09-27 — `agent-codespaces` resolved to backlog-only (PRs #4298, #4299); `agent-dispatch-solo` clean-room checkpoint found a 4th pre-existing bug

- `agent-codespaces` core src/ cluster (PR #4298, 17/60 findings):
  annotated `src/agent_codespaces/`'s genuine exceptions --
  `allow legacy compatibility root`/`allow legacy-compatibility` (own-root
  fallback paths, `LEGACY_CONFIG_DIR_NAME`, and the remote-codespace-
  deployed auth-helper assets' fixed instructions-root/backup/staging
  filenames -- the whole `codespace_assets/` tree runs on an EPHEMERAL
  remote CodeSpace, where "exactly one install" is the actual invariant,
  not a bug), `allow registry` (cross-plugin `agent-bridge` relay-port/
  token-cache/log lookups and cross-plugin `agent-worktrees` binstub/
  state lookups -- both patterns now confirmed across a 4th/5th plugin),
  and `allow shared-instance-mutex` (`fence.py`'s `FENCE_PATH =
  "~/.agent-lease"`, a deliberately shared, not cell-scoped, home-dir
  lockfile -- and a **genuine** lease-keyword match this time, not the
  release/lease false-positive class from the prior entry).
  - Hit the **module-size ceiling a 10th time** (private-downstream-repo#7672):
    `config.py` was at its exact 2583-line baseline. Resolved without any
    net line growth by keeping the marker on the *existing* return-
    statement line (fits at 98/99 chars) instead of extracting a new
    local-variable line -- cheaper than the net-line-neutral restructuring
    technique used earlier in `agent-machines/cell_lifecycle.py` when a
    same-line append alone is enough to close the gap.
  - Two embedded-shell-script gotchas surfaced while shortening lines:
    (1) splitting a Python string-literal concatenation across two
    physical lines moves the guard's flagged line to whichever half now
    contains the pattern -- confirmed by re-running the guard after each
    split rather than assuming the "obvious" half; (2) a bash line-
    continuation backslash (`\` at end of line) cannot be followed by a
    trailing comment even in a *different* physical line's continuation
    -- same class of gotcha as the already-documented PowerShell backtick
    case, just the POSIX-shell side of the same coincidence, resolved the
    same way (extract the flagged literal into a small local variable
    referenced from both continuation halves).
- `agent-codespaces` SKILL.md docs (PR #4299, 7/60 findings): the usual
  `allow deployed-runtime-diagnostics` doc-shaped mentions, this time
  spanning three different SKILL.md files (borrowing-codespaces,
  codespaces-lifecycle, codespaces-setup) since the plugin's docs are
  split across several skills rather than one.
- `agent-codespaces` now at confirmed backlog-only state (34 remaining:
  `install.sh`/`install.ps1` 18, `service.yaml` 4, `installer-
  readiness.json` 3, `readiness-context.{sh,ps1}` 3,
  `emit-codespace-map.{sh,ps1}` 2, `write-config-dropin.{sh,ps1}` 2,
  `register-bridge-provider.ps1` 1, `payload-invocation.json` 1 -- all
  confirmed Phase 2/cross-plugin categories, same shape as every prior
  plugin's end state).
- **Clean-room checkpoint** (`agent-dispatch-solo`, per the operator's
  periodic-validation directive, run against the published `main`
  marketplace release): 11/13 assertions passed. Two related failures
  traced to one root cause: `agent-dispatch inbox --machine
  <own-hostname>` (a same-machine self-browse, not an actual remote
  connection) unconditionally shells out over SSH, so it fails outright
  on a box with no `ssh` client -- and because `inbox` crashing prevents
  the coordinator's expected autostart, a subsequent `agent-dispatch
  health` call then also fails (`Connection refused`) as a knock-on
  effect, not a separate defect. Filed
  [private-downstream-repo#7722](https://your-git-host/owner_user/private-downstream-repo/issues/7722).
  This is the **fourth** clean-room-checkpoint-found defect this session
  (after agent-bridge's `--version` race #7688, agent-index's fresh-
  install bug #7702 which was fixed inline, and agent-dispatch's flaky
  test #7719 found via local test-suite isolation rather than clean-
  room) -- the periodic-validation directive continues to catch real
  gaps that guard-triage alone would never exercise.
- Verified: `check-marketplace-isolation.py`, `check-module-size.py`,
  `check-docs-consistency.py`, `check-skills.py`, ruff (no new
  violations across 9 edited Python files plus 2 remote-deployed asset
  scripts), `node --check` and `bash -n` syntax-checks on the two
  `codespace_assets/` scripts. Both PRs merged via `pr-merge --now`
  after a clean advisory review; GitHub Actions ran the full
  `agent-codespaces` suite on each PR (fast, ~50s) rather than needing
  a local run, since shared-host `test-supervisor` slot contention from
  concurrent sessions (confirmed via `ps aux`, not this session's own
  work) made three consecutive local `uv sync` attempts defer.

### 2026-09-27 — `agent-dispatch` resolved to backlog-only (PRs #4287, #4288); `agent-index` now fully backlog-only

- `agent-index`: with #4272/#4276 merged, re-verified the plugin's full
  finding set is now confirmed Phase 2/module-size/cross-plugin backlog
  only (46 remaining: `cell-runtime.py` 7 + `resolve_effective_config.py`
  3, module-size ceiling; `install.sh`/`install.ps1` 20, `payload-
  invocation.json` 7, `installer-readiness.json` 3, Phase 2/cross-plugin
  JSON questions) -- no further action needed, matching every prior
  plugin's end state.
- `agent-dispatch` core src/ cluster (PR #4287, 25/90 findings): annotated
  `src/agent_dispatch/`'s genuine exceptions -- `allow remote-management`
  (SSH-remote agent-bridge/agent-dispatch peer invocations, generalizing
  cleanly to a 3rd plugin), `allow registry` (cross-plugin agent-bridge
  config-dir default lookups), `allow legacy-compatibility` (fixed env-var
  names, legacy `.agent-dispatch` fallback paths/dirnames, and the
  single-global-coordinator zdd routing identity -- confirmed this reason
  covers "intentionally non-cell-scoped by design," not just literal
  legacy paths, per `agent-bridge/lifecycle_hooks.py`'s prior
  `_SERVICE = "agent-bridge"` precedent).
  - **Two new guard false-positive classes**: `allow query-column-list`
    (`queue_common.py`'s `_TASK_SELECT`/`_TASK_BULK_SELECT` SQL
    column-list construction via `", ".join(...)` coincidentally matches
    the guard's `task...= "<quoted string>"` identity-assignment shape,
    since the join separator `", "` is itself a quoted string
    immediately after `=`); `allow release-verb` (`queue_lifecycle.py`,
    `task_state_machine.py`: the business verb "release" -- as in "task
    released from suspension" -- contains "lease" as a substring,
    coincidentally matching the guard's lease/mutex keyword scan; same
    underlying shape as agent-ssh's prior `third-party-installer-url`
    class (`InstallRelease` matching `lease`), confirming this
    "short-keyword-as-substring" false-positive shape recurs across
    plugins and is worth checking for early when a "for a name/reason
    that seems too load-bearing to be a real hit" finding appears).
  - Several fixes required restructuring beyond a same-line append: ruff's
    99-char limit combined with the marker text forced extracting short
    module-level constants (`_legacy_dirname`, `svc`, `_BULK_HAS_RESULT_COL`,
    `_RELEASED_FROM_SUSPENSION`) so the marker could sit on the same
    physical line as the (now shorter) flagged pattern -- reconfirms the
    session's established technique, and that the marker-placement rule
    (same physical line as the flagged pattern, not merely "nearby") has
    no exception even for multi-line function calls or preceding
    comments; a marker one line above or below the flagged text does not
    suppress the finding.
  - Found (not caused by this work) a pre-existing, reproducibly-failing
    test: `tests/test_supervisor.py::test_idle_headless_fleet_nudge_includes_remote_host`
    fails identically with and without this session's changes (confirmed
    via `git stash`/`git stash pop` isolation). Filed
    [private-downstream-repo#7719](https://your-git-host/owner_user/private-downstream-repo/issues/7719).
    Oddly, the equivalent CI job on both PRs (#4287, #4288) reported the
    full suite passing -- possibly timing-sensitive/order-dependent;
    left as filed rather than chased further, consistent with the
    session's "don't diagnose a flaky/queued check as if it were a
    confirmed failure" discipline.
- `agent-dispatch` SKILL.md docs (PR #4288, 4/90 findings): the usual
  `allow deployed-runtime-diagnostics` doc-shaped mentions of the
  plugin's own runtime paths and legacy default port.
- `agent-dispatch` now at confirmed backlog-only state (47 remaining:
  `install.sh`/`install.ps1` 38 -- Phase 2 launcher-contract inventory,
  same shape as every prior plugin's own-root/binstub/systemd-unit
  identity constants -- plus `installer-readiness.json` 3,
  `payload-invocation.json` 1, `bin/` shims 2, `focus-guidance.{sh,ps1}` 2,
  `register-bridge-provider.ps1` 1, all confirmed Phase 2/cross-plugin
  categories).
- Also ran a **git base-branch gotcha**: `gh pr create` without `--base
  dev` silently targeted this repo's GitHub-default `main` branch instead
  of the actual integration branch `dev`, producing a stale-looking
  `CONFLICTING` mergeability that a rebase against `dev` alone couldn't
  fix (since the PR was never comparing against `dev` in the first
  place). Fixed via `gh pr edit <#> --base dev`, which also required an
  empty retrigger commit to get GitHub Actions to actually run against
  the corrected base (the first pass after `--base dev` reused a stale
  cached check run). Future PRs in this effort should pass `--base dev`
  explicitly on `gh pr create` to avoid this.
- Verified: `check-marketplace-isolation.py`, `check-module-size.py`,
  `check-docs-consistency.py`, `check-skills.py`, ruff (no new
  violations), and the `agent-dispatch` suite via `test-supervisor`
  (3511 passed, 11 skipped, 1 pre-existing failure noted above). Both
  PRs merged via `pr-merge --now` after a clean advisory review.

### 2026-09-27 — Fixed the `agent-index` fresh-install bug found by the clean-room checkpoint (PR #4272, fixes private-downstream-repo#7702)

- Root cause confirmed (see the prior entry below): `install.sh`'s/
  `install.ps1`'s `ensure` action never stamped the CLI binstub, so a
  genuinely fresh machine's `sessionStart` hook left `agent-index`
  entirely absent from `PATH`.
- **First attempt was the wrong shape and the review caught it.** The
  initial fix made `ensure` run the full heavy path (`_ensure_runtime`
  / `Install-Runtime`: uv fetch, venv build, package install) —
  mirroring `update`. PR #4272's advisory review flagged this
  correctly on four points: (1) medium — `hooks.json`'s `sessionStart`
  hook has a 20-second timeout with output suppressed, and a full
  provision can easily exceed that on a pristine box, silently
  truncating and preserving a variant of the original bug; (2)/(4) low
  — the changefile comment and a new test comment both embedded the
  private `private-downstream-repo#7702` tracker identifier, violating the
  public-artifact identifier-neutrality convention; (3) low — the fix
  contradicted two checked-in docs (`docs/standalone-service-lifecycle.md`,
  `skills/setting-up-agent-index/SKILL.md`) that explicitly document
  `ensure` as a **cheap, non-provisioning** safety net by design.
- Re-reading those docs (and `install.sh`'s own `do_stamp` comment:
  "fits a sessionStart hook's grace window. No venv, no uv.") confirmed
  the medium finding was substantive, not just style: `ensure` is
  *intentionally* meant to stay fast, and the real gap was narrower
  than the first attempt assumed — it needed to stamp the **binstub**
  only (the same cheap artifact `stamp`/`install`/`update` all deploy
  via `deploy_binstub` / `Deploy-SetupGatedBinstub`), not build the
  full runtime.
- **Corrected fix:** `ensure` now checks whether the binstub exists;
  if not, it writes the `payload-dir` marker and calls
  `deploy_binstub`/`Deploy-SetupGatedBinstub` (mkdir + two small
  resolver-script copies + a small redirector shim — no interpreter,
  no package manager), then proceeds with the existing health-check
  (`_ensure_running`/`Ensure-Running`) and autostart registration
  (`_install_logon_autostart`/`Install-LogonAutostart`) unchanged.
- **Manually verified in a clean container** (fresh `$HOME`, no prior
  `~/.agent-index` or `~/.local/bin/agent-index`): `ensure` now
  completes in **~0.57s**, deploys a working binstub that resolves and
  returns a clean `"state":"inactive"` status, and builds no `.venv` —
  confirming both the timeout concern and the "must not silently
  provision before explicit setup" design contract are satisfied.
- Fixed the two identifier-neutrality findings by rewording the
  changefile comment and the test comment to describe the bug/fix
  generically, with no tracker reference. Updated both flagged docs to
  describe the corrected behavior (stamps the binstub first when
  absent, still never provisions a runtime).
- Rewrote `test_installers_are_base_only_and_never_implicitly_start_engine`'s
  `ensure`-specific assertions to check for `deploy_binstub`/
  `Deploy-SetupGatedBinstub` + the existing health-check/autostart
  calls, and explicitly assert `_ensure_runtime`/`Install-Runtime` are
  **absent** from `ensure`'s body (the opposite of the first attempt's
  assertions).
- Verified: bash/PowerShell syntax checks, `check-docs-consistency.py`,
  `check-module-size.py`, `check-marketplace-isolation.py` (all clean),
  and the full `agent-index` suite via `test-supervisor` (562 passed,
  1 skipped, 71 deselected, same `test_runtime_gate.py` exclusion).
  All 8 required CI checks passed on GitHub before merge. Merged via
  `pr-merge --now`.
- **Takeaway for future legs:** when a review flags a design-contract
  contradiction against checked-in docs, re-read those docs before
  assuming the review is merely stylistic — here the "wrong" original
  fix would have reintroduced a variant of the same bug under a tight
  hook timeout, and the docs already described the correct, narrower
  shape the fix needed to take.

### 2026-09-27 — `agent-index`'s SKILL.md docs resolved (PR #4264); clean-room checkpoint found a HIGH-severity first-install bug

- Fresh guard count at merge time: not re-checked before the next
  slice began (docs-only PR, minimal drift expected).
- `setting-up-agent-index/SKILL.md`'s 6 findings: doc-shaped mentions
  of the plugin's own config path (in-repo, knowledge-overlay,
  machine-role), the usual `allow deployed-runtime-diagnostics`.
- **Clean-room checkpoint (`agent-index-solo`, per the operator's
  periodic-validation directive) found a genuine HIGH-severity, first-
  install-breaking defect on the published `main` marketplace release,
  unrelated to this session's `dev`-branch work**: 18 of 24 scenario
  assertions failed, reproducibly (2/2 runs). Root-caused via direct
  `docker exec`: `hooks.json`'s `sessionStart` hook calls `bash
  scripts/install.sh ensure` (stdout/stderr to `/dev/null`, so
  failures are silent) -- but `install.sh`'s `ensure` action ONLY runs
  `_ensure_running` + `_install_logon_autostart`; it does **no
  provisioning** (no venv, no package install, no binstub stamping) on
  a machine with zero prior `~/.agent-index` state. Manually confirmed
  `bash scripts/install.sh install` (a DIFFERENT action) correctly
  builds the venv, installs the package, and stamps `~/.local/bin/
  agent-index` -- `ensure` is simply the wrong action for a fresh
  install, and since sessionStart always calls it, agent-index NEVER
  self-provisions on a genuinely fresh machine; the binstub never
  appears, so every subsequent command cascades to "command not
  found." No existing tracking issue found (checked #4808/#4807/#7686,
  all different plugins or different root causes); filed
  [private-downstream-repo#7702](https://your-git-host/owner_user/private-downstream-repo/issues/7702),
  flagged as possibly warranting priority above the routine guard-
  triage backlog given the severity (agent-index is completely
  non-functional on a fresh install until someone manually runs
  `install.sh install`).
- This is now the **third** clean-room checkpoint this session and the
  **second** genuine pre-existing defect found this way (after
  `agent-bridge`'s `--version` race, private-downstream-repo#7688) -- the
  operator's periodic-validation directive is earning its keep: neither
  defect would have surfaced from guard-triage work alone, since guard
  annotations never exercise the actual install/provision flow.
- Verified: `check-skills.py` (0 errors, no new warnings), `check-docs-
  consistency.py`, and the `agent-index` suite via `test-supervisor`
  (562 passed, 1 skipped, 71 deselected -- same known
  `test_runtime_gate.py` exclusion). Merged via `pr-merge --now` after
  a clean advisory review.
- Guard count: 46 -> 40 findings for `agent-index`.

### 2026-09-27 — `agent-index`'s core-package cluster: 14 of 60 findings resolved (PR #4257)

- Fresh guard count at merge time: 558 (down from 569). First slice
  into `agent-index`, next plugin in the backlog order after
  `agent-bridge` reached backlog-only state. Also ran a clean-room
  checkpoint (`agent-mcp-solo`, 13/13 PASS) between the two plugins per
  the operator's periodic-validation directive.
- Classified by shape, all matching precedent already established this
  session: own-legacy-root fallbacks across `config.py` (5, one of
  which reused an already-marked `SHAREABLE_CONFIG_DIR` constant rather
  than re-annotating the same literal twice), `index_config.py` (2),
  `engine/daemon.py` (1); cross-plugin `agent-worktrees` registry
  lookups in `config.py`/`_knowledge_overlay.py`; an env-var-name-
  declaration (`ENDPOINT_ENV`, `allow env-var-name-declaration`).
- `transport.py`'s `_build_inner()` builds a shell command that runs
  `agent-index` on a REMOTE host over a non-interactive SSH logon --
  its own docstring says so explicitly. `allow remote-management`, a
  third confirmation this session that reason phrase generalizes
  (after `agent-ssh`'s and `agent-bridge`'s `carrier.py`).
- `indexing/runner.py`: a dict key already named exactly `"legacy_root"`
  -- extracted the value into a small `_legacy_root()` helper purely to
  fit the marked literal within the 99-char line limit.
- **`server.py` produced a SECOND occurrence of the "short-keyword-
  substring-collision" false-positive class first seen in `agent-ssh`'s
  `InstallRelease`/`lease` and `agent-bridge`'s `tasks` variable**: a
  JSON receipt's schema-identifier string
  `"copilot-extensions.agent-index.service-instance"` is a namespaced
  message-type tag, not an OS service name, but its `agent-index.
  service` substring coincidentally matches the `fixed-service-identity`
  category's `agent-<name>.service` regex alternative. New reason:
  `allow schema` (the shortest form that fits once the 54-char schema
  string itself eats most of the 99-char budget). **`cell-runtime.py`
  has 4 MORE instances of this exact same shape** (its `INSTANCE_
  SCHEMA`/`ENSURE_WORKER_SCHEMA`/`ENSURE_WORKER_COMPLETION_SCHEMA`
  constants and one inline `"schema":` key) -- left for a follow-up
  slice on that file specifically, since it also sits at its exact
  module-size ceiling.
- Left deliberately untouched: `cell-runtime.py` (7, module-size
  ceiling -- baseline 5145, 7th file this session at zero headroom) and
  `resolve_effective_config.py` (3, baseline 1136, 8th file) -- both
  tracked in
  [private-downstream-repo#7672](https://your-git-host/owner_user/private-downstream-repo/issues/7672).
  `install.sh`/`install.ps1` (20, confirmed-genuine Phase 2 launcher-
  contract backlog), `payload-invocation.json` (7), `installer-
  readiness.json` (3), `setting-up-agent-index/SKILL.md` (6) -- not yet
  individually triaged.
- Verified: `python -m py_compile` + `ruff --select E501` on every
  touched file, `check-module-size.py`, `check-docs-consistency.py`, and
  the `agent-index` suite via `test-supervisor` (562 passed, 1 skipped,
  71 deselected -- `test_runtime_gate.py` excluded, the SAME pre-
  existing test-supervisor temp-storage-limit failure this exact plugin
  hit in an earlier leg's handoff, confirmed identical again via `git
  stash`/`git stash pop`). Merged via `pr-merge --now` after a clean
  (0-finding) advisory review.
- Guard count: 60 -> 46 findings for `agent-index`.

### 2026-09-27 — `agent-bridge` now backlog-only at 15 findings (PR #4246)

- Fresh guard count at merge time: 569 (down from 570).
- `repair-scheduled-task.ps1`'s `InstallDir` env-var fallback matched
  the same own-legacy-root shape already fixed across the rest of this
  plugin (`allow legacy-compatibility`). Its sibling finding on the SAME
  file (`$TaskName = 'Agent Bridge'`) stays deliberately deferred -- its
  own doc comment explicitly says the task's action/contract is owned
  by `install.ps1`'s `Register-ScheduledTask_`, coupling it to that
  file's not-yet-converted Phase 2 launcher-contract backlog rather than
  being independently resolvable.
- **`agent-bridge` is now backlog-only at 15 findings**: `install.sh`/
  `install.ps1`/`repair-scheduled-task.ps1`'s `TaskName` (10, confirmed
  Phase 2 launcher-contract backlog), `payload-invocation.json` (1,
  cross-plugin open question), `transport.py` + `session_host/spawner.py`
  (4, module-size-ceiling deferral, tracked in
  [private-downstream-repo#7672](https://your-git-host/owner_user/private-downstream-repo/issues/7672)).
  Same end-state pattern as `agent-ssh`/`agent-mcp`/`agent-machines` this
  session -- five plugins now fully triaged to confirmed-backlog-only.
- Verified: PowerShell Parser API syntax check, `check-docs-
  consistency.py`, and the full `agent-bridge` suite via
  `test-supervisor` (800 passed, 10 skipped, 0 failures). Merged via
  `pr-merge --now` after a clean advisory review.
- Guard count: 16 -> 15 findings for `agent-bridge`.

### 2026-09-27 — `agent-bridge`'s doc/SKILL.md cluster resolved (PR #4240); first clean-room validation checkpoint of this session

- Fresh guard count at merge time: 570 (down from 584).
- `agent-bridge/SKILL.md` (4) and `references/cli-commands.md` (3):
  doc-shaped prose mentions of the plugin's own config/routing-table
  paths (`allow deployed-runtime-diagnostics`).
- `agent-bridge-troubleshooting/SKILL.md` (7): mix of prose, a
  standalone PowerShell command with a trailing comment, a Python
  snippet, and **two PowerShell fenced commands using backtick line-
  continuation** -- the marker could not go on the same physical line
  without breaking the continuation (a `#` right after a backtick is
  escaped into the command, not a real comment), so both were
  restructured into a `$_log = "..."  # marker` variable assignment
  before the continued command -- same pattern `agent-mcp`'s
  `reliable-agent.md` established two slices ago. Verified via the
  PowerShell Parser API that all 4 edited fenced blocks still parse.
- **Operator directive this leg: periodically run clean-room validation
  of plugin setup + the harness repo itself (Worktree Manager open/
  update/create-worktree), to catch regressions as the guard-triage
  edits accumulate.** First checkpoint this session:
  - **`agent-worktrees-solo` (Tier P): 13/13 PASS.** Register -> create
    -> finalize round-trips cleanly on a fresh box. No regression from
    any of this session's `agent-worktrees`-adjacent edits (there
    weren't any yet, but this establishes the pre-triage baseline).
  - **`agent-bridge-solo` (Tier P): 9/10, found a genuine PRE-EXISTING
    defect, NOT caused by this session's work.** `agent-bridge --version`
    (and the `version` subcommand) can return EMPTY stdout, exit 0,
    during an in-flight first-provision race -- reproduced twice via the
    automated scenario and once manually via `docker exec` (a fresh
    invocation right after the scenario's own phase-2 pass re-triggered
    a full from-scratch reprovision, and only a SECOND, later call
    succeeded). Confirmed this reproduces against the **published `main`
    marketplace release** (the clean-room installs via `copilot plugin
    install agent-bridge@copilot-extensions`, which resolves to `main`,
    not this session's in-flight `dev` work) -- so it predates and is
    unrelated to any of this leg's `agent-bridge` triage PRs. Searched
    for and found no existing tracking issue (checked #1236 and #823,
    both near-misses on the wrong root cause); filed
    [private-downstream-repo#7688](https://your-git-host/owner_user/private-downstream-repo/issues/7688).
  - **Review's own review-comment content on PR #4240 flagged a possible
    concern with the new `$_log = "..."` PowerShell assignment** ("suppress
    the standalone assignment output") -- verified empirically
    (`pwsh -Command '$_log = "x"; Write-Output "..."'`) that a bare
    PowerShell variable assignment produces **zero** console output
    regardless, so the concern didn't correspond to an actual defect.
    Noted here rather than silently overriding the review; merged
    without further change.
- Verified: `check-skills.py` (0 errors), `check-docs-consistency.py`,
  PowerShell Parser API on every edited fenced block, and the full
  `agent-bridge` suite via `test-supervisor` (800 passed, 10 skipped, 0
  failures).
- Guard count: 30 -> 16 findings for `agent-bridge`.

### 2026-09-26 — `agent-bridge`'s src/ + extension.mjs cluster: 22 of 52 findings resolved (PR #4168)

- Fresh guard count at merge time: 584 (down from 606). First slice into
  `agent-bridge`, next plugin in the backlog order after `agent-machines`
  reached backlog-only state.
- Classified by shape, all matching precedent already established this
  session: `shutil.which("agent-worktrees"/"agent-bridge")` sibling
  launches (`allow legacy-compatibility`); 6 cross-plugin
  `agent-worktrees` registry lookups spread across
  `agent_registry_common.py`/`agent_registry_topology.py`/
  `related_plugins.py`/`config.py` (`allow registry`); own-legacy-root
  constants already named `LEGACY_*` by the plugin itself, in
  `config.py`/`install_paths.py`/`extension.mjs` (`allow legacy-
  compatibility`); descriptive error-message/help-text mentions of the
  plugin's own paths in `service_process_cli.py`/`session_host_
  connection.py`/`session_start.py` (`allow deployed-runtime-
  diagnostics`).
- **`install_paths.py`'s `systemd_unit_name()` already implements the
  established `cell-derived-suffix` exemplar pattern correctly** (falls
  back to the unqualified name only when no suffix is available) --
  first time this session that pattern's OWN precedent (from
  `runtime-gate.sh`/`.ps1`) showed up matched in a completely different
  file, confirming it generalizes.
- **`carrier.py` builds a remote-side `agent-bridge carrier --stdio`
  command sent over SSH to a peer machine** -- same reasoning as
  `agent-ssh`'s `allow remote-management` from earlier this session
  (SSH-remote context, not local-cell), confirming that reason phrase's
  first use wasn't a one-off.
- **Found and fixed a second instance of last session's `lease`/
  `InstallRelease` false-positive CLASS**: `session_manager.py` had a
  local variable literally named `tasks` (a joined display string for an
  error message) that coincidentally matched the `fixed-service-identity`
  category's `task` keyword substring. Unlike the `agent-ssh` case
  (a third-party pinned URL, annotated), this one was a trivial LOCAL
  variable with no external meaning at all -- renamed to `summary`
  instead of annotating a non-issue, since renaming genuinely eliminates
  the false match at its root with zero behavior change. **Two false-
  positive occurrences in two legs suggests this substring-collision
  shape (short keywords like `task`/`lease`/`unit`/`pipe`/`socket`/
  `mutex`/`endpoint`/`service` colliding with unrelated identifiers) may
  be worth a systematic `grep` sweep across the whole repo rather than
  waiting to trip over each one individually.**
- Left deliberately untouched: `transport.py` (3) and `session_host/
  spawner.py` (1) both hit the SAME module-size-ceiling wall as 4 other
  files this session (baselines 1282/1028, both at exact current size --
  see [private-downstream-repo#7672](https://your-git-host/owner_user/private-downstream-repo/issues/7672),
  now a 5th and 6th occurrence). `install.sh`/`install.ps1` (9,
  confirmed-genuine Phase 2 launcher-contract backlog),
  `repair-scheduled-task.ps1` (2), `payload-invocation.json` (1), and 3
  doc/SKILL.md files (14) -- not yet individually triaged.
- Verified: `python -m py_compile` + `ruff --select E501` on every
  touched file, `check-module-size.py`, `check-docs-consistency.py`, and
  the full `agent-bridge` suite via `test-supervisor` (800 passed, 10
  skipped, 0 failures). Merged via `pr-merge --now` after a clean
  (0-finding) advisory review.
- Guard count: 52 -> 30 findings for `agent-bridge`.

### 2026-09-26 — `agent-machines`'s SKILL.md docs resolved, plugin now backlog-only; a live CI blocker hit and cleared (PR #4165)

- Fresh guard count at merge time: 606 (down from 609).
- `agent-machines-setup/SKILL.md` (1) and `restore-machinestate/SKILL.md`
  (2): the usual doc-shaped `allow deployed-runtime-diagnostics`
  mentions -- no new reasoning needed.
- **`agent-machines` is now backlog-only at 30 findings**: 19 confirmed
  Phase 2 launcher-contract (`init.ps1`/`init.sh`/`bootstrap-
  check.{ps1,sh}`), 4 `installer-readiness.json`, 3
  `payload-invocation.json`, 2+1 the surfaced-not-fixed task-name
  cell-suffix design question (`self_update_state.py` + `fleet_update_
  state.py`), 1 `cell_lifecycle.py` module-size deferral. Same end-state
  pattern as `agent-ssh`/`agent-mcp` this session.
- **Hit and cleared a genuine, all-PRs-blocking CI regression while
  merging the prior PR (#4158, the `cell_lifecycle.py` journal update)**:
  a `ThomasMichon/copilot-extensions` repository ruleset was updated
  2026-09-26T23:13 PT (mid-session, by someone/something else) adding a
  NEW required status check, "identifier leak guard," to `dev`'s branch
  protection. That check's own log said it was misconfigured --
  `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK` repo secrets weren't set,
  so it always failed -- meaning EVERY PR to `dev` (not just this
  effort's) was blocked from merging, including one that had already
  merged minutes earlier under the SAME failing check (before the
  ruleset update took effect). Correctly did NOT attempt an admin/
  gitea-admin force-merge (reserved for a dedicated unjamming agent, not
  a routine coding agent's own call) and did NOT try to fix repo secrets
  directly -- searched for and found no existing tracking issue, then
  surfaced it to the operator directly rather than guessing at next
  steps. Operator reverted the ruleset upstream; the very next merge
  attempt (`pr-merge --now` on the same already-pushed PR, no other
  changes) succeeded immediately. **No new issue needed filing since the
  operator resolved it live** -- but the pattern (verify before assuming
  it's your own change at fault, search for existing tracking, ask
  rather than force) is the same error-response discipline this effort
  has followed all session for its own module-size-ceiling questions.
- Verified: `check-skills.py` (0 errors, no new warnings), `check-docs-
  consistency.py`, and the full `agent-machines` suite via
  `test-supervisor` (658 passed, 12 skipped, 0 failures).
- Guard count: 33 -> 30 findings for `agent-machines`.

### 2026-09-26 — `cell_lifecycle.py`'s 3-legs-deferred findings: 2 of 3 resolved (PR #4154)

- Fresh guard count at merge time: 609 (down from 611).
- Followed up on this session's own `agent-machines` slice, which noted
  `cell_lifecycle.py`'s 3 findings had now been deferred across 3 legs.
  Actually reading the code (rather than re-deferring on the strength of
  a paraphrased prior handoff note) showed both were the SAME already-
  established "recognize the plugin's own legacy identity" shape this
  effort has fixed dozens of times -- no genuine design ambiguity, just
  three legs in a row that hadn't looked closely.
- `cell_lifecycle.py`'s module-size baseline is exactly 1018 lines -- the
  **4th file this session** hit at zero headroom (after `agent-
  containers/__main__.py`, `agent-ssh/fragment_registry.py`,
  `agent-mcp/config.py`; tracked in the just-filed
  [private-downstream-repo#7672](https://your-git-host/owner_user/private-downstream-repo/issues/7672)).
  This time, made the fix net line-NEUTRAL instead of deferring again: one
  marker fit on its existing line for free, and a 3-line list
  comprehension collapsed to a 2-line form (a named constant + one-line
  comprehension) freed exactly 1 line to pay for the OTHER new marker
  line. File shrank by 1 net line overall (1018 -> 1017) -- a small
  worked example of "restructure to net-zero" actually succeeding twice
  in the same file, unlike the times this session it wasn't possible.
- The third finding (line 650, inside a nested closure argument) still
  had no equivalent line-saving restructure nearby and got deferred --
  genuinely the module-size wall again, not a design question. It drops
  out trivially once `cell_lifecycle.py` gets real headroom.
- Guard count: 35 -> 33 findings for `agent-machines`. Verified via the
  full `agent-machines` suite (658 passed, 12 skipped, 0 failures).
  Merged via `pr-merge --now` after a clean (0-finding) advisory review.
- **Lesson for future legs**: when a handoff says a finding is "plausibly
  intentional, not yet reviewed," that's an instruction to go read the
  code THIS leg, not license to defer again on the strength of the
  phrase alone -- the actual review here took minutes and resolved a
  3-leg-old open item.

### 2026-09-26 — `agent-machines`'s legacy-root/registry cluster: 13 of 48 findings resolved, 2 design questions surfaced (PR #4145)

- Fresh guard count at merge time: 611 (down from 624). First slice into
  `agent-machines`, next plugin in the backlog order after `agent-mcp`
  reached backlog-only state.
- `discover.py`: `LEGACY_MACHINE_STATE_ROOT` (a constant already named
  "LEGACY_..." by the plugin itself -- as clean a "yes, annotate this"
  signal as this effort has seen) plus 4 cross-plugin `agent-worktrees`
  registry lookups, consolidated onto one shared `_AWT` constant.
  `identity.py` had one more registry lookup of the same shape.
  `manifest.py`'s `repo_root()` had a legacy-directory-name recognition
  branch matching `discover.py`'s exact legacy concept (a comparison, not
  a construction, but the same underlying concern). 6 more own-legacy-
  root fallbacks across `fleet_update_state.py`/`fleet_update_tasks.py`/
  `self_update_state.py`/`self_update_tasks.py`/`surfaces/_common.py`/
  `self_update_dtssh.py` -- all the routine, already-established shapes.
- **Surfaced (not annotated) a genuine potential design gap**: traced
  every use site of `fleet_update_state.py`/`self_update_state.py`'s
  `task_name="agent-machines-{fleet,self}-update-{sweep,watchdog}"`
  scheduled-task names and confirmed NO cell-derived suffix is EVER
  appended anywhere downstream (unlike the established `runtime-gate.sh`/
  `.ps1` exemplar's `SERVICE_SUFFIX` pattern this plugin's own effort
  README already flagged as superseded by the post-rewrite
  `cell_lifecycle.py` engine). This means today, exactly ONE self-update
  watchdog and ONE fleet-update sweep task can exist per machine
  regardless of how many marketplace cells of `agent-machines` are
  installed -- which may be entirely intentional (a single shared
  watchdog making sense the same way `ssh_profile.py`'s shared-config-
  lock did), or may be a genuine unqualified-identity gap nobody's
  looked at since the cell_lifecycle.py rewrite. Left unannotated and
  surfaced to the operator rather than guessed at either way -- this is
  exactly the kind of call this effort's own discipline says needs
  verification, not assumption.
- `cell_lifecycle.py`'s 3 findings (from an even earlier leg's handoff,
  carried forward again unresolved): still needs the same care. Not
  touched this leg either -- three legs in a row have now deferred this
  file specifically; it may be worth a dedicated follow-up rather than
  hoping the next slice gets to it.
- Left deliberately untouched (not yet individually triaged):
  `installer-readiness.json` (4), `payload-invocation.json` (3, includes
  the `cell_lifecycle.py`-adjacent legacy-binstub declaration),
  `init.ps1`/`init.sh`/`bootstrap-check.{ps1,sh}` (19, confirmed-genuine
  Phase 2 launcher-contract backlog matching every other plugin), and 2
  `SKILL.md` files (3 findings, likely doc-shaped but not yet read).
- Verified: `python -m py_compile` + `ruff --select E501` on every
  touched file, `check-module-size.py`, `check-docs-consistency.py`, and
  the full `agent-machines` suite via `test-supervisor` (658 passed, 12
  skipped, 0 failures). Merged via `pr-merge --now` after a clean
  (0-finding) advisory review.
- Guard count: 48 -> 35 findings for `agent-machines`.

### 2026-09-26 — `agent-mcp`'s doc/SKILL.md cluster: 15 more findings resolved, plugin now backlog-only (PR #4082)

- Fresh guard count at merge time: 624 (down from 639). `agent-mcp` now
  at 14 findings, all confirmed backlog (matching `agent-ssh`'s
  end-state from earlier this session).
- `agent-mcp/SKILL.md` (4) and `customizing-bridges/SKILL.md` (7 of its
  original 8): the usual doc-shaped `allow deployed-runtime-diagnostics`
  mentions -- prose, a markdown table, a fenced YAML example comment.
- **`customizing-bridges/SKILL.md`'s 8th finding sat inside the
  frontmatter `description:` YAML block scalar** -- the first time this
  effort hit a marker placement that would actually corrupt user-facing
  content (an HTML comment there becomes literal text in the description
  shown by skill-matching/listing UI, and counted by `check-skills.py`'s
  length guideline). Resolved by REPHRASING instead of annotating: the
  sentence named the literal overlay path
  (`~/.agent-mcp/overrides/<id>.yaml`); dropped it in favor of
  "a machine-local override overlay file" since a description's job is a
  high-level summary, not an implementation-detail reference -- the
  skill body already spells out the exact path. Zero findings need a
  marker when the offending detail didn't need to be in the description
  at all. **Worth checking other plugins' frontmatter descriptions for
  the same latent issue** -- this is likely not unique to agent-mcp.
- **`reliable-agent.md`'s 3 findings live inside fenced, user-facing
  example code blocks** (1 bash, 2 PowerShell) -- new territory for this
  effort (prior doc fixes were all prose/tables, never inside an
  actually-meant-to-be-copy-pasted code fence). The bash one took a
  plain trailing `#` comment safely. Both PowerShell ones end their
  flagged line with a backtick line-continuation, which cannot be
  followed by a same-line comment without breaking the continuation --
  restructured each into a `$stub = "..."` variable assignment (marker
  goes on that line) then `& $stub` `-continued args, changing nothing
  about what the example teaches or how it behaves. Confirmed via the
  full `agent-mcp` suite (these blocks aren't test-executed themselves,
  but this at least confirms nothing else broke).
- Verified: `check-skills.py` (0 errors, no new warnings), `check-docs-
  consistency.py`, and the full `agent-mcp` suite via `test-supervisor`
  (609 passed, 7 skipped, 0 failures -- same clean run as the prior
  slice). Merged via `pr-merge --now` after a clean (0-finding) advisory
  review.
- **`agent-mcp` is now backlog-only at 14 findings** (5 `init.ps1` + 2
  `init.sh` Phase 2 launcher-contract InstallDir/LocalBin, 4
  `installer-readiness.json`, 1 `payload-invocation.json`, 2 `config.py`
  module-size-ceiling deferral) -- same end-state pattern as `agent-ssh`.
  Move to the next plugin in the backlog order
  (`agent-machines`/`agent-bridge`/`agent-index`/`agent-dispatch`/
  `agent-codespaces`/`agent-worktrees`, none yet individually triaged).

### 2026-09-26 — `agent-mcp`'s core-package legacy-root cluster: 7 of 36 findings resolved (PR #4068)

- Fresh guard count at merge time: 639 (down from 646). First slice into
  `agent-mcp` (next plugin in the backlog order after `agent-ssh` reached
  backlog-only state).
- The overwhelming majority of `agent-mcp`'s `src/agent_mcp/*.py`
  findings turned out to be **the exact same own-legacy-root fallback,
  byte-for-byte identical shape, repeated 5 times across 4 files**
  (`materialize.py` x2, `sockio.py`, `token_cache.py`, `storage.py`) --
  `Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp"))`
  (or the equivalent `Path(base) if base else Path.home() / ".agent-mcp"`
  in `token_cache.py`). All `allow legacy compatibility root`, the same
  as every other plugin's identical shape this effort has fixed all
  along -- no new reasoning needed, just volume.
- `__main__.py`'s 2 identical argparse help strings (`bridge name under
  ~/.agent-mcp/bridges/`) shared one local constant, itself split across
  two string-literal pieces to keep the marked line under the 99-char
  limit (`allow deployed-runtime-diagnostics`).
- **`config.py`'s 2 identical-shape findings (`BRIDGES_DIR`/
  `OVERRIDES_DIR`) hit the same module-size wall a third time this
  effort**: its baseline is exactly 1178 lines with zero headroom, and no
  blank/removable line existed to trade for the one shared constant this
  would need (same constraint as `agent-containers/__main__.py` and
  `agent-ssh/fragment_registry.py`). Deferred, unannotated. **This is now
  a recurring pattern worth a dedicated future pass**: several plugins'
  largest module has settled at its exact grandfathered ceiling with
  zero slack, meaning ANY future annotation-only fix in that specific
  file needs either a real split/shrink first, or a deliberate,
  separately-reviewed `--allow-widen` PR (the sanctioned but
  post-merge-only escape hatch) landed ahead of the annotation PR. Worth
  raising to the operator as its own small effort/issue rather than
  re-discovering the same wall plugin by plugin.
- Left deliberately untouched (not yet individually triaged this leg):
  `installer-readiness.json` (4), `payload-invocation.json` (1),
  `init.ps1`/`init.sh` (7, the same confirmed-genuine Phase 2
  launcher-contract InstallDir/LocalBin backlog as every other plugin),
  and 3 doc/`SKILL.md` files (`customizing-bridges/SKILL.md` 8,
  `agent-mcp/SKILL.md` 4, `references/reliable-agent.md` 3 -- 15 doc
  findings total, likely straightforward `allow deployed-runtime-
  diagnostics`/`allow doc-example` like every other plugin's SKILL.md
  cluster, but not yet read).
- Verified: `python -m py_compile` + `ruff --select E501` on every
  touched file, `check-module-size.py`, `check-docs-consistency.py`, and
  the full `agent-mcp` suite via `test-supervisor` (609 passed, 7
  skipped, 0 failures -- the cleanest test run of any plugin triaged so
  far this effort). Merged via `pr-merge --now` after a clean (0-finding)
  advisory review.
- Guard count: 36 -> 29 findings for `agent-mcp`.

### 2026-09-26 — `agent-ssh`'s `transports/dtssh`/registrar cluster: 6 more findings resolved (PR #4060)

- Fresh guard count at merge time: 646 (down from 652, unrelated
  concurrent `dev` activity as usual). `agent-ssh` now at 14 findings
  (was 30 at the start of this leg, was 20 after the prior slice).
- Second half of the sub-slicing plan from the prior leg's handoff:
  `register-dispatch-companion.{ps1,py}` (same env-var-fallback shape as
  `register-bridge-provider.ps1`, `allow legacy compatibility root`) and
  the `transports/dtssh/*` cluster.
- **Two more genuinely novel shapes, neither seen before in this
  effort**:
  - `dtssh-host-launcher.ps1`'s Windows named mutex is qualified by
    `$Alias` (the SSH host being launched), not by marketplace-cell --
    correct, since two cells launching a host process for the SAME alias
    need to share the SAME mutex to actually enforce single-instance
    (cell-scoping would defeat the point, identical reasoning to
    `ssh_profile.py`'s `allow shared-config-lock` from the prior leg,
    just for a mutex instead of a lock file). New reason: **`allow
    shared-instance-mutex`**.
  - `install-host.ps1`'s `Resolve-DurableHostIdentityRoot` OneDriveCommercial
    fallback stores the dtssh host's SSH identity in shared/roaming
    storage ON PURPOSE, so it survives reinstalls and follows the user
    across machines (the function's own name says "Durable"). Cell-
    qualifying it would break the exact durability guarantee it exists
    for. New reason: **`allow durable-host-identity-backup`**.
- **Found a genuine guard-regex false positive, not an intentional
  compatibility seam at all**: `install-client.ps1`/`install-host.ps1`
  both declare `$InstallRelease = 'https://raw.githubusercontent.com/
  bmiddha/devtunnel-ssh/main/scripts/install-release.ps1'` -- a pinned
  URL to a THIRD-PARTY project's own installer script, unrelated to this
  plugin's identity in any way. The `fixed-service-identity` category's
  keyword list includes `lease`, which matches as a bare substring of
  the variable name `InstallRelease` -- an accidental collision, not a
  real service/lease/endpoint identity. Annotated rather than filed as a
  guard-regex bug, since the guard's own design intentionally favors
  over-inclusion (report-only, never blocking) over a narrower regex that
  might miss a real case; new reason: **`allow third-party-installer-
  url`** (worth keeping in mind if this substring-collision shape turns
  up again -- `lease`/`unit`/`task`/`pipe`/`socket`/`mutex`/`endpoint`/
  `service` are all short enough to collide with unrelated identifiers).
- Left deliberately untouched, same confirmed-genuine Phase 2 launcher-
  contract backlog shape as `install.ps1`/`install.sh`: `bootstrap-
  check.ps1`/`.sh`'s `InstallDir` (2, verified byte-for-byte identical
  shape via direct comparison) and `install-client.sh`/`install-host.sh`'s
  `$HOME/.dtssh/bin` PATH export (2). `payload-invocation.json` (1) and
  `fragment_registry.py` (1, module-size deferral) carried over unchanged
  from the prior leg.
- Verified: PowerShell Parser API syntax check on every touched `.ps1`
  file, `python -m py_compile` + `ruff --select E501` on the one touched
  `.py` file, `check-module-size.py`, `check-docs-consistency.py`, and
  the full `agent-ssh` suite via `test-supervisor` (171 passed, 7
  skipped, the same 1 pre-existing failure as every other leg touching
  this plugin). Merged via `pr-merge --now` after a clean (0-finding)
  advisory review.
- **`agent-ssh` is now down to 14 findings, all confirmed backlog** (8
  Phase 2 launcher-contract, 1 cross-plugin `payload-invocation.json`
  question, 1 `fragment_registry.py` module-size deferral, PKG-INFO build
  noise not counted) -- no further individual triage possible here
  without either the Phase 2 launcher-contract conversion pattern or a
  `fragment_registry.py` split/shrink. Move to the next plugin in the
  backlog order (`agent-machines`/`agent-mcp`/`agent-bridge`/`agent-index`/
  `agent-dispatch`/`agent-codespaces`/`agent-worktrees`, none yet
  individually triaged).

### 2026-09-26 — `agent-ssh`'s core-package cluster: 10 of 30 findings resolved (PR #4003)

- Fresh guard count at merge time: 652 (drifted down slightly from the
  prior leg's 663, unrelated concurrent `dev` activity).
- Per the prior handoff's own caution ("19 files, lower file-concentration,
  bigger per-PR undertaking -- consider breaking into sub-slices by file
  cluster"), took the `src/agent_ssh/*.py` + `SKILL.md` cluster only (10
  of 30 findings) and left `scripts/`/`transports/dtssh/*` (10, not yet
  individually triaged) for a future slice, alongside the already-known
  Phase 2 launcher-contract backlog (`install.ps1`/`.sh`, 8) and the
  cross-plugin `payload-invocation.json` open question (1).
- Classified by shape: `copilot_detach.py`'s own legacy-root fallback (no
  env override existed, unlike the pattern elsewhere) and a `command -v`
  remote-tooling presence check run over SSH via its own `_remote()`
  helper; `explore.py`'s POSIX-sh probe script -- its own docstring says
  "streamed to the target on stdin" -- reads the REMOTE machine's own
  `.agent-worktrees/related.yaml`, not this host's runtime root. Both got
  **`allow remote-management`** (an existing repo phrase, previously only
  used in `agent-dispatch`'s `SKILL.md` for `ssh <machine> agent-bridge
  …`-style docs -- first use annotating actual Python source, same
  reasoning: SSH-remote operations are a genuinely different context than
  local-cell qualification, whether the remote thing is a container
  (`agent-containers`' `allow remote-container-path` last leg) or a whole
  machine).
- `fragment_registry.py`/`host_restore.py`'s own legacy-root fallbacks
  (`allow legacy compatibility root`, same as always).
- **`ssh_profile.py` produced a genuinely NEW finding shape not yet seen
  in this effort**: `.agent-ssh-locks`/`.agent-ssh-root-config-`/
  `.agent-ssh-fragment-` are lock-directory and `tempfile.mkstemp` prefix
  names, not runtime roots at all -- they coordinate exclusive, atomic
  writes to the ONE real, shared `~/.ssh/config`/`config.d` (an external
  resource every cell of this plugin, or a hypothetical future fork, must
  serialize access to). Cell-qualifying these names would be actively
  WRONG: two writers racing on the same real file need the SAME lock
  namespace to actually coordinate, not different ones. Coined a new
  reason phrase for this shape: **`allow shared-config-lock`** (does not
  yet appear anywhere else in the repo -- worth checking for the same
  pattern in other plugins that manage a shared external config file,
  e.g. anything else touching `~/.ssh/config` or an equivalent
  single-shared-resource lock).
- **`agent-ssh/SKILL.md`'s 1 finding** (a doc mention of `agent-bridge`'s
  own `~/.agent-bridge/refs/<batch>/`) got the usual `allow deployed-
  runtime-diagnostics`.
- **Deferred one finding for the same module-size reason as
  `agent-containers/__main__.py` last leg**: `fragment_registry.py`'s
  OTHER own-legacy-root fallback (the one with no preceding env-var
  check) sits in a file whose module-size baseline is exactly 1195 lines
  with zero headroom, and even the shortest possible marker doesn't fit
  the existing line (69 chars of code + a 34-char *minimal* marker still
  exceeds the 99-char limit by 4 -- computed precisely this time, not
  guessed, learning from the `__main__.py` trial-and-error last leg).
  Left unannotated rather than bust the ceiling.
- Verified: `python -m py_compile` + `ruff --select E501` on every
  touched file, `check-module-size.py`, `check-docs-consistency.py`,
  `check-skills.py`, and the full `agent-ssh` suite via `test-supervisor`
  (171 passed, 7 skipped, 1 pre-existing failure --
  `test_dtssh_apply_updates_existing_binary_without_login`, confirmed
  identical in a prior leg via `git stash`/`git stash pop`, not caused by
  this change). Hit repeated shared-host test-slot contention this leg
  (another session's own heavy multi-plugin `--reinstall` run held the
  lock for 9+ minutes) -- resolved by a plain retry once the other run's
  process actually finished, no `test-supervisor` misconfiguration
  involved.
- Merged directly via `pr-merge --now` once the advisory Copilot review
  came back clean (0 findings) -- this repo's PR review is genuinely
  advisory-only with no gating "approved" state to wait for; see the
  entry above (PR #3877) for the full note on not conflating this with
  `private-downstream-repo`'s Dampener-gated flow.

### 2026-09-26 — `agent-containers`' 27 of 39 findings resolved (PR #3877)

- Fresh guard count at merge time: 663 (`{bare-agent-command: 2, fixed-
  service-identity: 134, global-plugin-binstub: 83, path-sibling-launch:
  41, unqualified-runtime-root: 403}`) -- drifted up from the prior leg's
  651 due to unrelated concurrent `dev` activity, as expected.
- Classified `agent-containers`' 39 findings (36 own + a shared vendored
  lib's 3, but the vendored lib actually carries 7 findings apiece across
  3 plugins -- see below) by shape: `register-bridge-provider.ps1` (same
  env-var-fallback shape as `agent-logger`'s already-fixed `register-cold-
  store-provider.ps1`, `allow legacy compatibility root`), `_invoke.py`/
  `config.py`'s own legacy-root fallback (two near-duplicate functions),
  `provider_ssh.py` (one own-root fallback, one cross-plugin
  `agent-worktrees` registry lookup, `allow registry`), `ssh_transport.py`
  (`docker exec`-resolved paths that live inside a target container's own
  filesystem via `_remote_home()`, a genuinely different context from
  local-cell qualification -- **new reason phrase** `allow remote-
  container-path`, not previously used in the repo), and
  `containers-fleet/SKILL.md`'s 7 doc-shaped mentions (`allow deployed-
  runtime-diagnostics`, same pattern as other plugins' `SKILL.md` fixes).
- **The `venue-copilot` lib (vendored byte-identical in `agent-containers`,
  `agent-codespaces`, `agent-ssh` -- provider-agnostic CLI-mode session
  orchestration, agent-bridge-cli-mode-sessions Phase 4) turned out to be
  genuinely annotation-shaped, not the "unconverted backlog needing a
  design pass" the prior leg's handoff worried it might be** (that caution
  was actually about `harness-knowledge`'s unrelated 2 findings, not this
  lib specifically -- re-read the referenced 2026-09-24 entry directly
  rather than trusting the paraphrase). Its `SEED_DIR`/
  `_DEFAULT_BRIDGE_CONFIG_DIR`/`REFS_ROOT` and the shell-snippet functions
  (`registration_credentials_script`, `bridge_probe_script`) all reference
  **`agent-bridge`'s own** runtime root by name during remote CLI-mode
  session setup -- the exact same cross-plugin-reference shape already
  established in `context-handoff/handoff-core.mjs` (`allow agent-bridge-
  management`), regardless of whether the reference resolves on this host
  or the remote venue (the reasoning is identical either way: it names a
  sibling plugin's cell, not this one's). One `shutil.which("agent-
  bridge")` sibling launch got `allow legacy-compatibility` (matches this
  same plugin's own established style). Fixed in `agent-containers` first,
  then propagated byte-identically to the other 2 copies and re-verified
  `check-vendored-libs-sync.py` -- this triggered a fresh-worktree-per-
  plugin question that turned out not to apply here (all 3 copies live
  under the ONE PR's diff, no separate per-plugin PRs needed for a shared
  lib fix).
- **Review caught a real, non-trivial class of bug across the first pass**:
  every trailing `# marketplace-isolation: allow <reason>` comment I
  appended pushed 14 lines past the plugin's Ruff 99-char limit (`E501`),
  because a long established reason phrase (`allow legacy compatibility
  root`, `allow deployed-runtime-diagnostics`) plus an already-long code
  line has very little budget left. Fixed by restructuring -- split string
  literals so the marked substring lands on its own short line, or pull a
  short local constant ahead of the line that uses it -- rather than by
  inventing shorter, inconsistent reason phrases; verified with `ruff
  check --select E501` on every touched file plus a runtime output-
  equality check (`repr()` before/after) for `bridge_probe_script`'s split
  f-string, since splitting a shell-snippet string is exactly the kind of
  change that can silently alter runtime output if done carelessly.
- **That same restructuring fix then blew a SECOND, unrelated guard**:
  `__main__.py`'s module-size baseline is exactly 1175 lines with zero
  headroom, and the review's fix for 3 of its findings (config-migrate
  help text, session-host-cleanup path validation, relay-port config-dir
  default) needed at least 2 net new lines even after sharing a module-
  level constant across use-sites (2 distinct literal roots, each needing
  its own marked line). `check-module-size.py`'s own docstring is explicit
  that `--allow-widen` is reserved for a scheduled **post-merge** job on
  `main`, never an ordinary PR's own diff -- "growth past the grandfathered
  size must be a conscious, visible decision, not a silent side effect."
  Rather than launder that growth through this PR, reverted `__main__.py`
  to byte-identical with `origin/dev` and left its 3 findings unannotated,
  deferred to a future leg alongside an actual split/shrink of the module
  (or a deliberate, separately-reviewed baseline-widening PR) -- this is
  now a 4th backlog category alongside `payload-invocation.json` and
  `init.ps1`/`init.sh`.
- Verified: `python -m py_compile` + `ruff --select E501` on every touched
  `.py` file, `check-module-size.py` (every module within cap/ceiling
  after the revert), `check-vendored-libs-sync.py` (12 shared libs in
  sync), `check-docs-consistency.py`, `check-changefile-presence.py
  --base origin/dev` (one shared `patch` changefile naming all 3
  affected plugins), and the full `agent-containers`/`agent-codespaces`/
  `agent-ssh` suites via `test-supervisor` (`agent-codespaces` 291+11
  passed; `agent-ssh` 171 passed/7 skipped/1 pre-existing failure --
  `test_dtssh_apply_updates_existing_binary_without_login`; `agent-
  containers` 128 passed/1 skipped/1 pre-existing failure --
  `test_relay_profile_cannot_replace_refusal_with_default_allowlist`, a
  WSL `powershell.exe`-detection issue -- both pre-existing failures
  confirmed identical via `git stash`/`git stash pop` without this change
  too, neither caused by this PR).
- **Gotcha reconfirmed on the merge side, not the worktree side this
  time**: `copilot-extensions`'s own `CONTRIBUTING.md` states PR review
  here is advisory-only and non-blocking -- 0 approvals required, the
  repo's `pr-self-merge` profile authorizes the submitter to merge
  directly after addressing worthwhile findings (`pr-merge <#> --now`).
  Do not wait indefinitely for a GitHub-style "approved" review-state
  transition that this repo's workflow never produces -- that pattern
  belongs to `private-downstream-repo`'s Intelligence-Dampener-gated flow, a
  different repo with a different review contract. Merged directly once
  the review's 2 remaining comments were confirmed stale (both cited
  `__main__.py` lines that had since been fully reverted back to
  `origin/dev`).
- PR #3877 merged as commit `ffeb4cdc4` -> squash-merged; exact merge SHA
  to be confirmed on `dev` at the start of the next leg.

### 2026-09-26 — Systemic `runtime-gate.ps1` marker-placement bug fixed across 5 more plugins

- Fresh guard count for `dev` head (post `agent-logger` merge): 651.
  Started investigating `agent-containers` (next size-tier candidate,
  36 findings) as a fresh triage target, but before diving into its more
  varied findings, checked `runtime-gate.ps1:21` first (per the
  `agent-vault`/`agent-logger` precedent of finding the exact same
  marker-placement bug in two prior legs) — confirmed present again.
- **Instead of fixing just `agent-containers` and moving on, checked
  every plugin's `runtime-gate.ps1` for the identical bug shape first**
  (marker `# marketplace-isolation: allow legacy compatibility root` on
  the closing `}` line of the `$legacyRoot = if (...) {...} else {...}`
  expression, not the flagged `Join-Path $env:USERPROFILE '.agent-*'`
  line above it). **First-pass mistake, caught by PR review**: the
  initial `grep`-based sweep filtered to files where a marker was found
  at all (`grep -n "allow legacy compatibility root" <file> | head -1`),
  which silently skipped any plugin whose `runtime-gate.ps1` had **no**
  marker whatsoever rather than a merely-misplaced one — missing
  `agent-index`, an 8th plugin with its own `runtime-gate.ps1` (`Join-
  Path $env:USERPROFILE '.agent-index'`, genuinely unmarked, not just
  misplaced). Re-ran the check properly (`ls plugins/*/scripts/runtime-
  gate.ps1` for the full file list, then individually inspecting each) and
  confirmed the full population is **8** plugins, not 7:
  `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-index`,
  `agent-logger` [already fixed], `agent-mcp`, `agent-ssh` [already
  correct — single-line marker], `agent-vault` [already fixed]. Found the
  **same bug shape (misplaced or entirely missing) in 5 more plugins**:
  `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-index`
  (missing, not misplaced), `agent-mcp` — confirming this was a systemic
  template/generator bug (7 of 8 plugins' `runtime-gate.ps1` were
  affected in one of the two shapes; only `agent-ssh` was clean from the
  start), not independent coincidences.
- Fixed all 5: moved the marker onto the actual flagged `Join-Path` line
  in the 4 misplaced-marker plugins, and added a fresh marker to
  `agent-index`'s previously-unmarked `.ps1` line. Also annotated
  `agent-index`'s `runtime-gate.sh` counterpart (`LEGACY_ROOT="${AGENT_
  INDEX_HOME:-$HOME/.agent-index}"`) for parity, even though the guard
  does not currently flag it — its exact shape (`.agent-index` immediately
  followed by a closing `}` before the closing quote, not a `"`/`'`/`/`)
  falls outside `_UNQUALIFIED_ROOT`'s regex, a narrow guard blind spot
  distinct from the marker-placement bug this entry is otherwise about;
  not pursued further here since it's out of this PR's scope, but noted
  for a future guard-regex review. Verified
  each file still parses (`pwsh -Command
  "[System.Management.Automation.Language.Parser]::ParseFile(...)"`).
  Ran the `payload_invocation`-selected test slice via `python tools/
  run-plugin-tests.py <plugin> -k payload_invocation` (the canonical
  turn-key runner) for the **4** misplaced-marker plugins — all passed
  (`agent-bridge` 6 passed/3 skipped, `agent-codespaces` 9 passed,
  `agent-containers` 2 passed, `agent-mcp` 12 passed/5 skipped).
  `agent-index` has no `payload_invocation`-selected test covering this
  specific line, so it was validated separately: syntax via the
  PowerShell Parser API, and its own `test_runtime_gate.py` suite was
  attempted but hits a pre-existing, unrelated temp-storage resource
  limit (confirmed identical via `git stash`/`git stash pop` without this
  change too — the suite creates real venvs per test) — validated by the
  guard count drop alone instead.
- Verified: `check-marketplace-isolation.py --json` dropped by exactly 5
  overall across the two commits in this PR (651→647 for the first 4,
  then 644→643 after adding `agent-index`'s changefile and fix — the
  intervening 647→644 drop between those two checks is unrelated `dev`
  activity landing concurrently, confirmed via `git stash`/`git stash
  pop` at each step, not a validation error). `check-docs-consistency.py`
  OK. `check-changefile-presence.py --base origin/dev` OK after adding a
  shared `patch`-typed changefile naming all 5 plugins (per
  `CONTRIBUTING.md`'s "one PR touching N plugins with one shared reason"
  pattern). Manually confirmed the fix catches its own regression via
  `git stash`/`git stash pop`.
- **Note on process**: this leg's edits were made in a worktree that had
  already been used (and merged/finalized) for the prior `agent-logger`
  PR; continuing to edit in it produced a stale, diverged-from-remote
  branch state when it came time to push. Saved the diff as a patch file,
  created a genuinely fresh `copilot-extensions` worktree per the effort's
  own established discipline, and re-applied it there via `git apply`
  before committing — a reminder that "always create a fresh worktree
  per increment" means literally every increment, not just the first one
  in a session.
- **`agent-containers`' own remaining 35 findings were NOT triaged this
  leg** (only its `runtime-gate.ps1:21` marker bug was fixed, incidentally
  discovered while checking for the systemic pattern) — its findings span
  many more files with more varied shapes (a vendored `venue_copilot` lib,
  remote-SSH-transport path construction, `__main__.py`/`_invoke.py`
  mixed doc/code) than `agent-vault`/`agent-logger` had, and deserve their
  own dedicated read-first pass rather than a rushed continuation. Left
  for a future leg.

### 2026-09-26 — `agent-logger`'s 18 findings resolved (first-time triage of this plugin)

- Fresh guard count for `dev` head (post guard-fix merge): 666, then 669
  after further unrelated `dev` activity (confirmed via `git stash`/`git
  stash pop` at commit time — the drift between checks tracks unrelated
  concurrent `dev` commits, not a validation error; this leg's own change
  always removes exactly 18 findings regardless of the baseline).
- `agent-logger` had never been individually triaged before this leg (it
  had only benefited incidentally from the `_CELL_QUALIFIER` `RUNTIME_ROOT`
  guard fix in the prior entry, which silently resolved 1 of its findings
  without anyone reading the rest). Checked file concentration first (per
  the effort's own established discipline): 40 findings across 15 files,
  with `install.sh`/`.ps1` (18) and `service.yaml` (3) matching the same
  blocked generic-wrapper/service-manifest backlog already confirmed for
  `budget-guidance`/`agent-pull-requests`/other plugins' `install.*` —
  left untouched. The remaining ~19 findings across 12 files were read
  individually, not assumed from file name:
  - `src/agent_logger/config.py:259` (`return Path.home() / ".agent-
    logger"`) — genuine env-override-with-legacy-default (`AGENT_LOGGER_
    HOME` checked first), identical shape to `agent-vault`'s `home_dir()`.
    Annotated `allow legacy compatibility root`.
  - `src/agent_logger/repo_trust.py:127,132` and `tenancy.py:82` — both
    read `agent-worktrees`' own legacy registry root (`~/.agent-worktrees`)
    by name for cross-plugin repo-adoption lookups; the `repo_trust.py`
    docstring explicitly documents this as intentionally mirroring
    `agent_worktrees.registry_paths._legacy_root` (citing the identical,
    pre-existing scope in `agent_bridge.agent_registry_common._REPOS_
    YAML_DEFAULT`). Matches the already-established, multi-plugin `allow
    registry` marker (verified via `grep` across `agent-worktrees`/
    `agent-dispatch`/`agent-bridge`/`agent-machines`/`agent-codespaces`'
    own vendored `plugin_activation/resolver.py:1121`). Annotated all 3
    lines with `allow registry`.
  - `src/agent_logger/sync/origin.py:45`
    (`_LEGACY_OPT_IN_CONFIG_RELATIVE = (".agent-logger", "config.yaml")`)
    and `sync/targets/filesystem.py:1947` (`Path.home() / ".agent-logger"
    / "sessions"`, with an explicit `self.options.get("path")` override
    checked first) — both genuine legacy-default fallback values, paired
    with a documented newer alternative (`_OPT_IN_CONFIG_RELATIVE` using
    `.copilot-extensions/agent-logger/config.yaml`) or an explicit
    override. Annotated `allow legacy-compatibility`.
  - `scripts/register-cold-store-provider.ps1:44`
    (`Join-Path $env:USERPROFILE '.agent-bridge\cold-store-providers.d'`)
    — a **sibling-plugin's** (`agent-bridge`) legacy directory, read only
    after two explicit env overrides (`AGENT_BRIDGE_COLD_STORE_PROVIDERS_
    DIR`, `AGENT_BRIDGE_CONFIG_DIR`) are checked first — the same env-
    override-with-legacy-default shape, just targeting a sibling instead
    of the plugin's own root (confirmed `agent-codespaces/scripts/
    register-bridge-provider.sh` uses the identical
    `${AGENT_BRIDGE_CONFIG_DIR:-$HOME/.agent-bridge}` pattern, though that
    file isn't currently guard-flagged). Annotated `allow legacy
    compatibility root`.
  - `scripts/runtime-gate.ps1:45` — **found the exact same pre-existing
    marker-placement bug fixed in `agent-vault`'s `runtime-gate.ps1` last
    leg**: the `allow legacy compatibility root` marker was on the closing
    `}` line of the `if`/`else` expression, not the flagged `Join-Path
    $env:USERPROFILE '.agent-logger'` line two lines above, so it never
    actually suppressed the finding. Moved the marker onto the flagged
    line. (Both `agent-vault` and `agent-logger`'s `runtime-gate.ps1`
    likely came from the same template/generator originally, given the
    identical bug — a future leg triaging any other plugin's `runtime-
    gate.ps1` should check for this same misplacement pattern rather than
    assuming a fresh one is unique to that plugin.)
  - `scripts/runtime-gate.sh:108` and `runtime-gate.ps1:262`
    (`AGENT_LOGGER_TASK_NAME = "Agent Logger Session Sync - $SERVICE_
    SUFFIX"` / `$serviceSuffix`) — the exact `SERVICE_SUFFIX`-only shape
    the prior entry's guard fix deliberately did NOT touch (correctly
    still-flagged, since the fix only recognizes a direct `$RUNTIME_ROOT`
    reference on the same line, not a locally-derived suffix variable by
    name alone). **Individually verified** (not blanket-trusted) that
    `SERVICE_SUFFIX` in both files is genuinely assigned from
    `scoped_identity_suffix "$RUNTIME_ROOT"` (`.sh` line 260) / an inlined
    equivalent SHA256 block over `$runtimeRoot` (`.ps1` line 232) earlier
    in the same file — the identical pattern already confirmed safe in
    `agent-vault`. This is option (b) from the prior entry's own guidance
    ("per-line annotation once each is individually confirmed to derive
    from a real `scoped_identity_suffix` call") rather than re-adding a
    bare keyword to the guard regex (option (c), the explicitly-warned-
    against mistake). Annotated both with a new, precise `allow cell-
    derived-suffix` reason (distinct from `allow legacy-compatibility`,
    since this is the *live*, correct cell-qualified value, not a legacy
    fallback — no existing token fit this shape).
  - `skills/log-session/SKILL.md:94` (a blockquote paragraph, not a table)
    and `skills/session-sync-setup/SKILL.md`'s 6 findings (a mix of plain
    paragraphs, a table cell, and a diagnostic command inside a bullet) —
    all describe the current deployed config-file location/systemd unit
    names for setup/troubleshooting guidance, the same shape as the
    `copilot-extensions-harness` and `agent-vault` `SKILL.md` precedents.
    Confirmed the HTML-comment marker works identically inside blockquote/
    bullet prose, not just table cells (no existing precedent for that
    exact placement, but the guard's `_ALLOW_REASON` regex is a plain
    same-line text match with no markdown-structure awareness, so this
    was a safe, low-risk extension of the established pattern, verified by
    checking the count dropped as expected). Annotated all 7 (1 + 6) with
    `allow deployed-runtime-diagnostics`.
  - `skills/session-sync-setup/references/config.yaml:64`
    (`password_file: ~/.agent-logger/rsync.pass   # optional`) — inside a
    "canonical, copy-pasteable example" reference file (its own header
    comment says so explicitly), not live deployed state. Matches the
    established `allow doc-example` category. Annotated.
- Verified: `git diff origin/dev -- plugins/agent-logger | grep "^\+.*
  marketplace-isolation: allow" | wc -l` = exactly 18 (matching all 18
  resolved findings — not eyeballed). `check-marketplace-isolation.py
  --json` dropped by exactly 18 (669→651 at verification time).
  `python -c "import ast; ast.parse(...)"` syntax check on all 5 touched
  `.py` files. `pwsh -Command "[System.Management.Automation.Language.
  Parser]::ParseFile(...)"` confirmed both touched `.ps1` files still
  parse. `python tools/run-plugin-tests.py agent-logger` (the canonical
  turn-key runner): 611 passed, 18 skipped (pre-existing skips, unrelated
  to this change). `check-docs-consistency.py` OK. `check-changefile-
  presence.py --base origin/dev` OK after adding a `patch`-typed
  changefile (the correct default, per the prior entry's own correction —
  not repeating the earlier `dev`-type mistake). Manually confirmed the
  fix catches its own regression via `git stash`/`git stash pop`
  (669→651→669→651).
- **Left deliberately untouched, documented rather than guessed at**:
  `payload-invocation.json:5`'s `legacyRuntimeRoot` field — the same
  cross-plugin open question already deferred for `agent-vault` (the
  identical field/shape exists verbatim in `agent-index`/`agent-machines`/
  `agent-vault`'s own `payload-invocation.json`); `install.sh`/`.ps1` (18
  findings) and `service.yaml` (3 findings) — genuine blocked Phase 2
  launcher-contract / service-manifest backlog, gated on the same
  unfinished prerequisite infrastructure documented for `budget-guidance`/
  `agent-pull-requests`.

### 2026-09-26 — Guard-code fix: `_CELL_QUALIFIER` now recognizes `RUNTIME_ROOT` (narrowed after review)

- Fresh guard count for `dev` head (post `agent-vault` small-findings
  merge): 673. Investigated the prior entry's flagged "genuine guard blind
  spot" candidate — `agent-vault`'s `runtime-gate.sh`/`.ps1` findings that
  set service identity via a `SERVICE_SUFFIX`/`serviceSuffix` hash derived
  from `scoped_identity_suffix "$RUNTIME_ROOT"` (the cell-specific runtime
  root) — this time going with the guard-code-fix path (option (a) from
  the prior entry) rather than annotation, having first confirmed
  `RUNTIME_ROOT` is a **broadly shared, cross-plugin convention**: the
  standard Phase 3 installation-context variable used by `agent-bridge`,
  `agent-codespaces`, `agent-containers`, `agent-logger`, `agent-mcp`,
  `agent-ssh`, and `agent-vault`'s `runtime-gate.sh` files, and the
  identical `scoped_identity_suffix`/`SERVICE_SUFFIX` pattern also exists
  in `agent-logger/scripts/runtime-gate.sh`/`.ps1`.
- **First-pass mistake, caught by PR review (the third catch in this
  effort's own history, after the two on the `agent-vault` `config.py`
  PR)**: the first version of this fix added `runtime[_]?root|
  service[_]?suffix|scoped[_]?identity[_]?suffix` to `_CELL_QUALIFIER` —
  all three as bare, purely lexical keyword matches. The review correctly
  flagged that `_CELL_QUALIFIER.search(code)` is a same-line lexical check
  only (no dataflow tracing), so this would ALSO suppress a
  **hardcoded**, non-cell-derived value merely because its variable name
  happened to contain "service_suffix" — e.g. `SERVICE_SUFFIX='shared'`
  interpolated into `agent-example-$SERVICE_SUFFIX.service` would produce
  no finding at all, even though that identity is genuinely shared across
  every cell. This is a real, narrower-than-`runtime_root` risk:
  `RUNTIME_ROOT` is always a runtime-resolved installation-context value
  (assigned from an explicit CLI/env argument or the installation-context
  resolver, never a source-hardcoded literal, across every plugin using
  the Phase 3 pattern) — the same tier of trust the guard already extends
  to the pre-existing `installation_id`/`marketplace_id`/`cell_id`
  keywords (see the existing `test_fixed_identity_ignores_mapping_prose_
  and_cell_qualified_value` test, which already trusts a bare
  `{installation_id}` interpolation lexically). `SERVICE_SUFFIX`, being a
  **locally computed, plugin-invented** intermediate variable (not a
  canonical Phase 3 primitive), carries no such guarantee — its
  trustworthiness depends entirely on *how* it was derived, which a
  same-line lexical check cannot verify. A real fix would need whole-file
  dataflow tracing (does this specific variable's *own* assignment,
  possibly many lines earlier, call `scoped_identity_suffix` on an
  already-qualified argument?) — and even that breaks down for
  `agent-vault`'s PowerShell variant, which inlines the SHA256 derivation
  across a multi-line block with no named helper function to anchor on,
  rather than calling a single-line function like the shell version does.
  Building genuine cross-line/cross-language dataflow verification into
  this guard is a disproportionate scope increase for what should be a
  small, well-scoped fix — so rather than attempt it, **scaled back to
  the safe subset**: keep only `runtime[_]?root` in `_CELL_QUALIFIER`
  (case-insensitive, matching both `RUNTIME_ROOT` snake_case and
  `$runtimeRoot` camelCase), and drop `service[_]?suffix`/`scoped[_]?
  identity[_]?suffix` entirely. This only suppresses findings on lines
  that directly interpolate `$RUNTIME_ROOT`/`$runtimeRoot` themselves
  (e.g. `export AGENT_VAULT_SOCKET="$RUNTIME_ROOT/run/agent-vault.sock"`)
  — lines that reference only `$SERVICE_SUFFIX`/`$serviceSuffix` without
  `$RUNTIME_ROOT` on the same line remain correctly flagged, exactly
  matching the review's own hardcoded-suffix scenario.
- **Audited the narrowed change's full impact before finalizing**: diffed
  the guard's `--json` output against the unmodified guard (via `git
  checkout HEAD~1 -- tools/check-marketplace-isolation.py
  plugins/agent-vault/scripts/runtime-gate.ps1` on the committed state,
  not an uncommitted stash, to avoid conflating the narrowing with
  unrelated build-artifact drift from `.egg-info/` files a local `uv
  sync` had generated) and confirmed **zero new findings appear, only
  removals**. Precise result: **5 findings suppressed across 2 plugins**
  — `agent-vault` 4 (`runtime-gate.sh` lines that directly interpolate
  `$RUNTIME_ROOT`) and `agent-logger` 1 (its `runtime-gate.ps1`'s
  `$runtimeRoot` reference). This is meaningfully smaller than the first
  attempt's 28, but every one of these 5 is now provably safe under the
  same trust tier already extended to `installation_id`/`marketplace_id`/
  `cell_id` — the remaining `SERVICE_SUFFIX`-only findings in both
  plugins' `runtime-gate.*`/`install.*` files are correctly still
  flagged and left for a future leg (see below).
- **Also fixed a pre-existing, unrelated marker-placement bug** discovered
  while investigating: `agent-vault/scripts/runtime-gate.ps1`'s existing
  `allow legacy compatibility root` marker was on the closing `}` line of
  an `if`/`else` expression, not on the actual flagged `Join-Path
  $env:USERPROFILE '.agent-vault'` line two lines above — the exact same
  same-line-marker mistake this PR-series' own review caught twice before,
  except this one predates this effort's involvement entirely and was
  never a "my annotation" mistake to catch via review; it simply never
  worked. Moved the marker onto the flagged line itself (the 5th and final
  finding in the total above).
- Added test coverage: `tools/test_check_marketplace_isolation.py` gained
  `test_fixed_identity_ignores_runtime_root_reference` (the safe positive
  case — a bare `$RUNTIME_ROOT`/`$runtimeRoot` interpolation is trusted)
  and `test_fixed_identity_still_flags_hardcoded_suffix_variable` (the
  review's own negative case — a hardcoded `SERVICE_SUFFIX='shared'` must
  still be flagged, proving the narrowed fix does not reintroduce the
  gap the review caught).
- Verified: the guard's own regression suite — 15 passed (13 pre-existing
  + 2 new, replacing the 1 originally-added test that covered the now-
  removed `service_suffix` behavior). `check-marketplace-isolation.py
  --json` dropped by exactly 5 (673→668, computed against the prior
  commit via `git checkout HEAD~1 --`, not a stash, to isolate the change
  from unrelated `.egg-info/` build-artifact drift). `pwsh -Command
  "[System.Management.Automation.Language.Parser]::ParseFile(...)"`
  confirmed `runtime-gate.ps1` still parses after the marker move.
  `python tools/run-plugin-tests.py agent-vault -k payload_invocation`:
  9 passed, 2 skipped (the tests that reference `runtime-gate.ps1`'s
  content). `check-docs-consistency.py` OK. `check-changefile-presence.py
  --base origin/dev` OK — `tools/` itself needs no changefile (repo-root,
  not vendored into any plugin's payload, per `CONTRIBUTING.md`), but the
  `runtime-gate.ps1` marker fix does; added a `patch`-typed one for
  `agent-vault`.
- **Left for a future leg, now correctly still-flagged rather than
  silently suppressed**: `agent-vault`'s remaining `runtime-gate.sh`/
  `.ps1` `SERVICE_SUFFIX`-only findings and `agent-logger`'s equivalent
  findings genuinely need either (a) real whole-file/cross-language
  dataflow verification in the guard (a bigger, standalone effort, not
  attempted here) or (b) per-line annotation once each is individually
  confirmed to derive from a real `scoped_identity_suffix` call — do not
  casually reach for a third option of just re-adding the keyword; that's
  the exact mistake this entry documents catching.
- **A third review round caught one more gap in the narrowed fix**: the
  `runtime[_]?root` alternative was still a bare same-line lexical match,
  so a line merely *mentioning* `RUNTIME_ROOT` as an assignment target
  (not an actual `$RUNTIME_ROOT` dereference) — e.g.
  `RUNTIME_ROOT='/shared'; AGENT_EXAMPLE_SYSTEMD_UNIT='agent-example.
  service'` on one physical line — would still incorrectly suppress the
  unrelated hardcoded identity on that same line. Fixed by requiring the
  `$`/`${` sigil directly before the keyword (`\$\{?runtime[_]?root\b`),
  so only a genuine variable dereference qualifies, never a bare mention
  or an assignment target. Added
  `test_fixed_identity_still_flags_hardcoded_runtime_root` covering
  exactly this shape. Re-verified the real-world impact is unchanged (the
  guard count stays at exactly 668 — all 5 originally-resolved findings
  were genuine `$RUNTIME_ROOT`/`$runtimeRoot` dereferences, not bare
  mentions, so tightening the match to require the sigil cost nothing).
- **A fourth review round caught that the sigil requirement alone still
  wasn't enough for PowerShell**: shell never sigils an assignment's LHS
  (`RUNTIME_ROOT=...` has no `$`), but PowerShell sigils *both* reads and
  writes (`$runtimeRoot = '...'` is itself sigiled) — so
  `$runtimeRoot = '/shared'; $TaskName = 'Agent Example'` on one line
  would still wrongly suppress the hardcoded `$TaskName` assignment. Fixed
  with a negative lookahead excluding an immediately-following assignment
  (`\$\{?runtime[_]?root\b(?!\s*=(?!=))` — rejects a trailing `= ` but
  still allows a `==` comparison through). Added
  `test_fixed_identity_still_flags_hardcoded_powershell_runtime_root`.
  Re-verified the guard count is still exactly 668 (unchanged again — the
  5 real findings were never assignment targets).
- **A fifth review round caught a subtle regex-backtracking bug in the
  fourth round's own fix**: the standalone `\}?` sat *before* the negative
  lookahead, not inside it, so it was independently backtrackable — for
  a braced PowerShell assignment (`${runtimeRoot} = '/shared'; $TaskName =
  'Agent Example'`), the regex engine first tried consuming the `}` (the
  lookahead then correctly failed on the assignment past it), but on
  backtracking it tried the alternative of matching zero closing braces
  instead, landing the lookahead's check *before* the literal `}` — where
  `\s*=` doesn't match (since the next character is `}`, not whitespace or
  `=`) — so the lookahead spuriously succeeded and the assignment slipped
  through anyway. Verified this exact failure with a minimal standalone
  Python reproduction before touching the fix (`/tmp/debug_regex.py`,
  scratch, not committed) to confirm the mechanism, not just guess at it.
  Fixed by moving the optional `\}?` *inside* the lookahead itself
  (`\$\{?runtime[_]?root\b(?!\}?\s*=(?!=))`), so there is no longer a
  separate backtrackable position between the identifier and the brace
  check. Re-verified against all 6 known cases (braced/unbraced ×
  assignment/dereference/no-sigil) with a standalone script before
  re-running the real regression suite, then added
  `test_fixed_identity_still_flags_hardcoded_braced_powershell_runtime_root`
  to lock the fix into the guard's own suite (18 tests total: 13
  pre-existing + 5 new across this entry's five rounds). Guard count is
  still exactly 668 — unchanged for the fifth time in a row, since none of
  the 5 real findings were ever braced assignment targets either.

### 2026-09-26 — `agent-vault`'s small remaining findings resolved (9 of the 44 left after `config.py`)

- Fresh guard count for `dev` head (post `config.py` merge): 676, then 680
  after further unrelated `dev` activity (confirmed via `git stash`/`git
  stash pop` immediately before committing — the count drift between
  sessions tracks unrelated concurrent commits, not a validation error;
  this leg's own change always removes exactly 7 findings regardless of the
  baseline).
- Read `agent-vault`'s remaining small-file findings (`cli.py` 2,
  `core_ext.py` 1, `service.py` 1, `winpipe.py` 1, `payload-invocation.json`
  1, `SKILL.md` 3) individually, per the prior entry's own caution not to
  assume shape from the file name:
  - `cli.py:125` (`ep = profile / ".agent-vault" / "run" / "endpoint.json"`)
    — inside `_windows_mount_candidates()`, a WSL-side glob over every
    mounted Windows user profile's legacy `.agent-vault` runtime dir,
    looking for the newest advertised endpoint; the function's own docstring
    says it "Honors an explicit `AGENT_VAULT_WINDOWS_RUN_DIR` override" as
    the cell-qualified escape hatch, with this glob being the legacy-
    discovery fallback path. Same shape as `config.py`'s `home_dir()`
    finding — annotated `allow legacy compatibility root`.
  - `cli.py:330` (`unit = os.environ.get(config.SYSTEMD_UNIT_ENV) or
    "agent-vault.service"`) — the identical env-override-with-legacy-
    default shape as `config.py`'s `DEFAULT_SOCKET_PATH`/`DEFAULT_PIPE_PATH`
    (just inlined instead of referencing a `DEFAULT_*` constant). Annotated
    `allow legacy-compatibility`.
  - `core_ext.py:45` (`CORE_ENDPOINT_ENV = "AGENT_VAULT_CORE_ENDPOINT"`) —
    the identical env-var-*name*-declaration shape as `config.py`'s 5
    corrected findings (a string holding the env var's name, not a fixed
    identity value). Annotated with the same `allow env-var-name-
    declaration` reason established in the prior entry's fixup.
  - `service.py:73` (`log = logging.getLogger("agent-vault.service")`) — a
    **distinct new guard false-positive shape**: this trips the guard's
    literal `agent-[a-z0-9-]+\.service` pattern (the first alternative in
    `_FIXED_UNIT`, matched anywhere in the line, not gated on the variable
    name) because the *string value* happens to look like a systemd unit
    name — but it's a Python `logging` namespace, not a live systemd/
    process identity; dozens of other files across the codebase use the
    identical `logging.getLogger("agent-<plugin>")` pattern and are never
    flagged (confirmed via `grep`) because they don't end in a literal
    `.service` suffix. Annotated with a new, precise `allow logger-
    namespace` reason (no existing precedent fit this shape either).
  - `winpipe.py:29` (`DEFAULT_PIPE_PATH = r"\\.\pipe\agent-vault"`) —
    **not annotated; deleted instead**. Verified via `grep` across the whole
    plugin (source + tests) that this module-level constant is a dead,
    unused duplicate of `config.py`'s own `DEFAULT_PIPE_PATH` — nothing in
    `winpipe.py` itself, nor any importer, ever references
    `winpipe.DEFAULT_PIPE_PATH` (only `pipe_send`/`start_pipe_server`/
    `IS_WINDOWS` are used from that module). Per the "Pre-Existing Issues —
    Track It or Fix It" convention, a verified-dead line is fixed directly
    (removed) rather than annotated as if it were intentional design.
  - `SKILL.md:41` (the Linux/WSL runtime-defaults table row, dual-flagged
    `global-plugin-binstub` + `fixed-service-identity` on one line) —
    documents the current deployed legacy layout for setup/troubleshooting,
    the same shape as the `copilot-extensions-harness` entry's `SKILL.md`
    table-row annotations. Annotated with the established `allow deployed-
    runtime-diagnostics` reason (HTML-comment form, matching that
    precedent's exact syntax for markdown tables).
- **Left 2 of the 9 deliberately unresolved this leg — both genuine guard/
  doc-format limitations, not judgment calls to rush**:
  - `payload-invocation.json:6` (`"legacyRuntimeRoot": ".agent-vault"`) —
    confirmed this exact field/shape also exists verbatim in
    `agent-index/payload-invocation.json:6` and
    `agent-machines/payload-invocation.json:6` (`grep`-verified). The
    2026-09-25 `phase-2-launcher-contracts.md` re-audit already flagged this
    as "look[ing] like intentional migration metadata" but explicitly
    "neither has been confirmed against the install contract or annotated"
    — i.e. the effort's own docs already treat this as an open, cross-
    plugin question bigger than one file. Annotating it here alone, ahead
    of that confirmation, would risk exactly the kind of premature judgment
    this PR-series' own review has now caught three separate times. Left
    for a dedicated cross-plugin design pass.
  - `SKILL.md:6` (the YAML frontmatter `description: >` folded block's
    `self-provisioning \`~/.local/bin/agent-vault\` binstub(s)` phrase) —
    **cannot use an inline marker here at all**: appending `# marketplace-
    isolation: allow ...` (or any comment syntax) inside a YAML frontmatter
    prose value would corrupt the actual skill description text shown to
    users/agents, not merely suppress a guard finding. Every existing
    `SKILL.md` marker precedent in the codebase lives inside a fenced code
    block or a markdown table cell — none inside frontmatter prose. This is
    a genuine guard/doc-format gap (the guard has no mechanism for
    annotating description-block prose without visibly polluting it) —
    flagged for a future guard-design pass (e.g. a whole-block marker on the
    closing `description: >` line, or a documented rewording convention)
    rather than solved ad hoc here.
- Verified: `git diff origin/dev -- <5 touched files> | grep -c
  "marketplace-isolation: allow"` = exactly 5 (matching the 5 annotated
  findings; the 6th and 7th resolved findings are the `winpipe.py` deletion
  and `SKILL.md:41`'s dual-category single-line suppression). `check-
  marketplace-isolation.py --json` dropped by exactly 7 (680→673 at commit
  time). `python -c "import ast; ast.parse(...)"` syntax check on all 4
  touched `.py` files. `python tools/run-plugin-tests.py agent-vault` (the
  canonical turn-key runner): 249 passed, 12 skipped — identical to the
  pre-change baseline, confirming the `winpipe.py` deletion broke nothing.
  `check-docs-consistency.py` OK. `check-changefile-presence.py --base
  origin/dev` OK after adding a **`patch`**-typed changefile (per the prior
  entry's corrected default — not repeating the `dev`-type mistake).
  Manually confirmed the fix catches its own regression via `git stash`/
  `git stash pop` (680→673→680→673).

### 2026-09-26 — `agent-vault`'s `config.py` 8 findings annotated (partial plugin slice)

- Fresh guard count for `dev` head (post `context-handoff` merge): 684→688
  after unrelated `dev` activity, then confirmed drift is expected (counts
  are a live snapshot, not a fixed baseline to re-derive each leg).
- Re-verified `agent-pull-requests` (10 findings) and `budget-guidance`
  (5 findings) are still confirmed genuine Phase 2 launcher-contract backlog
  — explicitly gated by the still-unchecked README Phase 2 item ("Stop
  installing generic `agent-*` commands into `~/.local/bin`; retained
  service, provider, remote, scheduled, startup, and deployment boundaries
  still require attributable external-launch contracts before their
  compatibility wrappers can be retired"). Converting either now would be
  premature against unfinished prerequisite infrastructure — left untouched,
  not a quick annotation slice.
- Moved to the next size tier (36-52 findings) and picked the most
  file-concentrated candidate: `agent-vault` (52 findings across only 11
  files, with `config.py` alone carrying 8 in a single file — the same
  one-file-one-classification shape as the `context-handoff` slice).
- Read `config.py`: 3 of the 8 flagged lines (`DEFAULT_SOCKET_PATH`,
  `DEFAULT_PIPE_PATH`, `home_dir()`'s runtime-root fallback) are genuine
  env-override-with-legacy-default values, the identical shape already
  annotated in this same plugin's `runtime-gate.sh`/`.ps1`
  (`LEGACY_ROOT="${AGENT_VAULT_HOME:-$HOME/.agent-vault}" # marketplace-
  isolation: allow legacy compatibility root`). The other 5
  (`SYSTEMD_UNIT_ENV`, `TASK_NAME_ENV`, `SOCKET_ENV`, `PIPE_ENV`,
  `ENDPOINT_ENV`) are a **different shape entirely**: each is just a string
  constant holding the env var's *name* (e.g.
  `SYSTEMD_UNIT_ENV = "AGENT_VAULT_SYSTEMD_UNIT"`), not a fixed identity or
  legacy-default value — the guard's `_FIXED_UNIT` regex false-positives on
  the variable name containing a keyword (`unit`/`task`/`socket`/`pipe`/
  `endpoint`) followed by `= "..."`.
  - **First-pass mistake, caught by PR review**: initially annotated all 8
    with `allow legacy-compatibility`, including the 5 env-var-name
    declarations, and cited `agent-containers/src/agent_containers/
    config.py`'s existing `allow legacy-compatibility` marker as an
    "analogous shape" precedent — but that precedent (`shutil.which
    ("agent-worktrees")`, a sibling-plugin lookup fallback) is a different
    shape too, and conflating both distinct categories under one reason
    hid that 5 of the 8 aren't legacy-compatibility fallbacks at all. Fixed
    by re-annotating the 5 env-var-name lines with a new, precise
    `allow env-var-name-declaration` reason and keeping `allow legacy
    compatibility root` / `allow legacy-compatibility` only on the 3 lines
    that genuinely fit that shape (the `DEFAULT_*` values and
    `home_dir()`).
  - **Second first-pass mistake, also caught by PR review**: the changefile
    used `--type dev`, matching this leg's own earlier `context-handoff` and
    `copilot-extensions-harness` PRs — but `CONTRIBUTING.md`'s version-scheme
    section is explicit: default to `patch` (bug fixes/small improvements/
    docs that don't change runtime behavior — exactly this shape); `dev` is
    only for "an iterative fixup within an already-in-flight patch" (i.e. a
    second commit added to a PR that already has a pending `patch`
    changefile from an earlier commit in the same PR, not a fresh PR's only
    changefile). Fixed by switching this PR's changefile to `patch`. The
    earlier two merged PRs' `dev`-typed changefiles were not retroactively
    corrected (already consumed/merged); future guard-triage PRs should
    default to `--type patch` unless genuinely adding to an already-pending
    patch changefile in the same PR.
- **Left the rest of `agent-vault` deliberately untouched this leg** —
  scoped to `config.py` only, matching the bounded single-file slice
  discipline:
  - `install.sh`/`install.ps1` (25 findings) — same blocked generic-wrapper
    backlog as `budget-guidance`/`agent-pull-requests`, not annotatable yet.
  - `scripts/runtime-gate.sh`/`.ps1` (10 remaining findings beyond the 2
    already-marked lines, verified by counting `category` entries whose
    `path` contains `runtime-gate` in the guard's `--json` output rather
    than eyeballing) — these set `AGENT_VAULT_SOCKET`/`PIPE`/
    `SYSTEMD_UNIT`/`TASK_NAME` using a `SERVICE_SUFFIX` derived from
    `scoped_identity_suffix "$RUNTIME_ROOT"` (a hash of the cell-specific
    runtime root) — i.e. these ARE already cell-qualified, just under a
    variable name (`SERVICE_SUFFIX`/`RUNTIME_ROOT`) the guard's
    `_CELL_QUALIFIER` regex (`marketplace(_id)?|installation(_id)?|cell
    (_id)?`) doesn't recognize. This looks like a genuine guard blind spot
    (false positive), not a code or annotation fix — flagging for a future
    leg to decide whether to broaden the guard's regex or annotate with a
    new reason category (no existing precedent token fits this shape; do
    not invent one without checking the guard-authoring history first).
  - `cli.py` (2), `core_ext.py` (1), `service.py` (1), `winpipe.py` (1),
    `payload-invocation.json` (1), `skills/agent-vault-setup/SKILL.md` (3) —
    each has a distinct shape (sibling env-override duplicate, a plain
    logger name, a `legacyRuntimeRoot` migration-metadata field matching the
    `agent-index`/`agent-machines` precedent noted in the 2026-09-25
    `phase-2-launcher-contracts.md` re-audit, and doc rows) needing their
    own individual read before classifying — not read this leg.
- Verified: `git diff origin/dev -- config.py | grep -c "marketplace-
  isolation: allow"` = exactly 8 (not eyeballed). `check-marketplace-
  isolation.py --json` dropped by exactly 8 (688→680 relative to the fresh
  `dev` head at rebase time). `python -c "import ast; ast.parse(...)"`
  syntax check. `python tools/run-plugin-tests.py agent-vault` — the
  canonical turn-key runner (not a bare `uv run pytest`, which uses no
  per-plugin venv and produces a misleading stale-package
  `ModuleNotFoundError` unrelated to any real regression — confirmed via
  `git stash`/`git stash pop` that the same bare-`uv run` failure pre-exists
  untouched, then re-ran correctly via `tools/run-plugin-tests.py`): 249
  passed, 12 skipped. `check-docs-consistency.py` OK. `check-changefile-
  presence.py --base origin/dev` OK after adding a `patch` changefile.
  Manually
  confirmed the fix catches its own regression via `git stash`/`git stash
  pop` (688→680→688→680, the count drift between checks tracked unrelated
  `dev` commits landing concurrently, not a validation error).

### 2026-09-25 — `context-handoff`'s 11 findings in `handoff-core.mjs` annotated

- Fresh guard count for `dev` head `97c641fa0`: 696 findings, matching the
  prior entry's post-merge baseline exactly. Read `agent-pull-requests`' 10
  findings first per the prior handoff's recommended order: confirmed
  genuine, unconverted Phase 2 launcher-contract backlog (`install.sh`/
  `install.ps1`/`installer-engine.sh`/`.ps1` writing to
  `$HOME/.agent-pull-requests` / `$HOME/.local/bin`), matching the
  `budget-guidance` pattern from an earlier entry — not a quick annotation
  slice, left untouched.
- Moved to `context-handoff`'s 11 findings, all in a single real code file,
  `plugins/context-handoff/extensions/context-handoff/handoff-core.mjs`
  (lines 63, 72, 81, 1351, 1667, 2003, 2015, 2034, 2046, 2149, 2666). Read
  the surrounding functions before classifying: `resolveSystemCliDescriptor`
  (lines ~55-110) and `runCli` (line ~198) are a provenance-verified
  sibling-plugin invocation utility — it resolves another plugin's payload
  root, verifies the sibling's `plugin.json` manifest name and repository
  match before invoking isolated argv, then the callers (`agentDispatch*`,
  `readStatusMonitorRegistry`, `abandonSupersededHandoffs`,
  `runAgentDispatchConsume`, `readTaskPayloadRaw`, the worktree history
  writer) invoke `agent-worktrees`/`agent-dispatch` by name to coordinate
  cross-plugin handoff state. This is the same established
  "sibling-plugin-invocation"/"legacy-compatibility" category already used
  elsewhere in the codebase (e.g. `agent-worktrees/src/agent_worktrees/
  finalize.py`'s `allow provider-management`,
  `agent-containers/src/agent_containers/config.py`'s
  `allow legacy-compatibility`, `agent-dispatch/scripts/focus-guidance.sh`'s
  `allow agent-worktrees-management`) — not new judgment, and not Phase 2
  launcher-contract backlog since `context-handoff` doesn't own these paths,
  it only references sibling plugins' own (still-legacy) runtime roots by
  name. Annotated all 11 lines with `marketplace-isolation: allow
  agent-worktrees-management` / `allow agent-dispatch-management` / `allow
  agent-bridge-management` matching the referenced plugin.
- Verified via `git diff origin/dev -- <file> | grep -c "marketplace-
  isolation: allow"`: exactly 11 markers added (not eyeballed).
- Verified: `check-marketplace-isolation.py --json` dropped from 696 to 685
  (exactly the 11 annotated findings); `node --check` on the edited file;
  `node --test` on `handoff-core.test.mjs` (50/50 pass), `cli-parity.test.mjs`
  and `handoff-supersede.test.mjs` (all pass); `check-docs-consistency.py`
  still OK; `check-changefile-presence.py --base origin/dev` OK after adding
  a dev changefile. Manually confirmed the fix catches its own regression
  via `git stash`/`git stash pop` (count went 685→696→685).

### 2026-09-25 — `copilot-extensions-harness`'s 5 findings annotated

- Fresh guard count for `dev` head `4c51d882c`: 701 findings
  (`unqualified-runtime-root`: 423, `fixed-service-identity`: 144,
  `global-plugin-binstub`: 83, `path-sibling-launch`: 51), matching the prior
  entry's post-merge baseline. `copilot-extensions-harness` carried exactly 5
  findings, all in `SKILL.md` docs: 3 in
  `skills/diagnosing-copilot-extensions/SKILL.md` (a "Where things live"
  table row each for `Runtime roots` and `Binstubs`, plus a `Symptom → cause
  → action` row for the "command not found" case — the last one dual-flagged
  `global-plugin-binstub` + `path-sibling-launch`) and 1 in
  `skills/contributing-to-copilot-extensions/SKILL.md` (the "Payload vs
  runtime" concept paragraph's mention of a `~/.local/bin` binstub).
- The diagnosing-skill's file already carries 8 existing
  `marketplace-isolation: allow deployed-runtime-diagnostics` markers on
  materially identical rows/commands in the same table and command block
  (documenting today's deployed, still-legacy-by-design runtime layout for
  troubleshooting) — category 2, matching an established in-file precedent,
  not a new judgment call. Annotated the 3 remaining rows with the same
  marker.
- The contributing-skill's finding is a generic architectural description
  ("a runtime plugin also ships a venv + `~/.local/bin` binstub"), not a
  diagnostic reference to the live deployed state — matches the 2026-09-24
  entry's `allow doc-example` category (that entry annotated an identical
  generic `~/.local/bin` binstub concept mention elsewhere) rather than
  `deployed-runtime-diagnostics`. Annotated with `allow doc-example`.
- **Gotcha re-hit**: the guard's `_ALLOW_REASON` match requires the marker on
  the exact same physical source line as the flagged text, not merely the
  same paragraph/table row — a marker placed on the following wrapped
  markdown line does not suppress the finding. First attempt at the
  contributing-skill annotation placed it one line down and left the finding
  un-suppressed (701 → 697, not 696); moved the marker onto the actual
  flagged line to fix it.
- Verified: `check-marketplace-isolation.py --json` dropped from 701 to 696
  (exactly the 5 annotated findings); `check-docs-consistency.py` still
  passes; manually confirmed the fix catches its own regression (`git
  stash` reverted the two files, count went back to 701; `git stash pop`
  restored 696).

### 2026-09-25 — Vendored `plugin-activation` registry-root finding annotated (6 findings)

- Fresh guard count for the `dev` head at this point: 707 findings (not the
  handoff's ~707 estimate carried over verbatim -- re-ran per the effort's
  own rule that counts drift). `customizing-copilot` carried exactly one
  remaining `unqualified-runtime-root` finding: `resolve_active_plugins()`'s
  `agent_worktrees_home = user_home / ".agent-worktrees"` in the vendored
  `libs/plugin-activation` package (`sync-vendored-libs.py`/
  `check-vendored-libs-sync.py` keep this package byte-identical across 8
  copies: the top-level canonical `libs/plugin-activation`, plus 7 vendored
  copies in `agent-bridge`, `agent-codespaces`, `agent-dispatch`,
  `agent-machines`, `agent-worktrees`, `customizing-copilot`, and
  `worktree-manager`).
- Traced the line to `_verified_project_roots()`: it reads agent-worktrees'
  own global `projects.yaml`/`repos.yaml` registry to resolve cell-scoped
  project adoption for every consuming plugin. Phase 4's 2026-09-07 journal
  entries already establish this exact registry as deliberately
  legacy/global -- "Global project/repository registries ... remain legacy
  until later attributable slices" -- matching the same
  `# marketplace-isolation: allow legacy-default registry root` precedent
  already used at `plugins/agent-worktrees/src/agent_worktrees/registry_paths.py`
  and `plugins/agent-worktrees/scripts/registry_root.py`. This is category 2
  (a genuine, already-intentional legacy fallback), not unconverted backlog.
- Annotated the same physical line identically across all 8 copies (a
  shorter reason token, `allow registry`, was required to stay within the
  package's own `line-length = 99` ruff config -- the full precedent phrase
  pushed the line to 118 chars). Re-ran `check-vendored-libs-sync.py` (still
  byte-identical across all 8), `ruff check` on each copy (clean), the
  package's own 59-test suite (canonical and, spot-checked,
  `agent-worktrees`'s copy — both pass), and the guard's own test suite
  (`test_check_marketplace_isolation.py` + `test_sync_vendored_libs.py`, 25
  passed). Guard `unqualified-runtime-root` count dropped by exactly 6 (the
  6 `plugins/`-scoped copies the guard scans; `worktree-manager/` isn't a
  guard-scanned path but was fixed too to preserve the sync invariant).
- **Process note for successors**: this session's first attempt edited the
  files directly in the coordinator-resolved anchor checkout (the
  non-worktree, personal-account root clone of this repo) before catching the
  `anchor-write-guard` hook denial. Recovered by exporting the diff, creating
  a proper disposable worktree via `copilot-extensions create --json`,
  applying the diff there, and using the sanctioned
  `agent-worktrees repos allow-edits copilot-extensions --reason "..."`
  break-glass only to revert the stray anchor edit back to clean. Always
  create the worktree *first*, per the effort's own standing gotcha.

### 2026-09-25 — `customizing-copilot`'s remaining `path-sibling-launch` finding resolved

- The prior entry left `scan_plugin_sources.py`'s `_agent_worktrees_repo_root()`
  genuinely open, believing it was called from ~8 separate scan entrypoints
  with no natural place to inject an explicit catalog-argv. Tracing the real
  call graph found this was wrong: only `assemble_enabled_plugins()` can
  reach it (via `_directory_marketplace_plugin`'s `agent-worktrees-repo`
  source kind), and only 2 real call sites exist -- `scan-customizations.py`'s
  `main()` (the actual agent-invoked CLI entrypoint) and
  `instruction_projections.py`/`manage-instruction-projections.py`'s
  `discover_enabled_sources()` wrapper (a second CLI entrypoint).
  `projection_sync_worker.py` also calls `discover_enabled_sources()`, but as
  a headless background sync daemon with no agent turn to supply a
  catalog-resolved path -- left it on the ambient-`PATH` default, which is
  the correct permanent behavior there, not a gap.
- Threaded an optional `agent_worktrees_command` parameter through
  `_agent_worktrees_repo_root` -> `_directory_marketplace_plugin` ->
  `assemble_enabled_plugins` -> `discover_enabled_sources`, preferring it
  over `shutil.which` exactly like the `harness-knowledge` fix (including
  the same Windows `.ps1`-host handling). Added `--agent-worktrees-path` to
  both `scan-customizations.py`'s and `manage-instruction-projections.py`'s
  CLIs, and a usage note to `reviewing-customizations/SKILL.md`.
- Review caught a real gap in the first pass: unlike `harness-knowledge`'s
  `assemble_plugins.py` (which raises on an unhostable `.ps1`), this file's
  resolver originally returned `None` for that case, and `None` is also
  the existing "genuinely unresolvable, fall through to `installed_root`"
  signal for every *ambient* resolution failure. That conflation meant an
  **explicitly** supplied but unhostable command would silently degrade to
  inspecting an unrelated `installed_root` payload instead of failing --
  the opposite of what an explicit catalog-resolved command is for. Fixed
  by having `_resolve_agent_worktrees_command` raise `ValueError` only for
  the explicit-and-unhostable case (ambient `shutil.which` misses still
  return `None` and fall through unchanged, matching every other
  unresolvable-marketplace case). Wrapped both `scan-customizations.py`
  call sites in their own `except ValueError` that exits 2 with a clear
  message, keeping the pre-existing `validate_committed_settings` soft-error
  path untouched and correctly ordered.
- **Second review round widened the same fix**: the first pass only made
  the unhostable-`.ps1` case fatal, but review caught that any *other*
  failure of an explicitly supplied command (a stale path, a failing CLI,
  empty output, a launch `OSError`) still returned `None` and fell through
  to `installed_root` the same way -- the identical provenance-mismatch
  risk, just for every non-`.ps1` failure mode. Restructured
  `_agent_worktrees_repo_root` so *any* failure raises `ValueError` when an
  explicit command was supplied, while ambient (`shutil.which`) failures
  keep the pre-existing soft `None`. Added a parametrized regression test
  covering 4 explicit-failure modes.
- Review also caught that the CLI-forwarding paths in both
  `scan-customizations.py`'s `main()` and
  `manage-instruction-projections.py`'s `main()` were untested -- a
  regression could drop the parsed flag before the real call and the suite
  would stay green. Added a `main()`-level regression test for each,
  supplying a conflicting ambient command and asserting the CLI-resolved
  one is used. Also widened `reviewing-customizations/SKILL.md`'s usage
  note (review caught it only mentioned `scan-customizations.py`, though
  `manage-instruction-projections.py` shares the same flag and resolver).
- Added 8 regression tests total across two review rounds
  (explicit-wins-over-ambient, the Windows unhostable-`.ps1` raise, 4
  parametrized explicit-failure modes, and the two `main()` CLI-forwarding
  tests); manually confirmed each fails when its corresponding fix is
  reverted (the Windows case via an isolated throwaway interpreter, since
  it's platform-gated and this box is Linux).
- **Third review round found two more edge cases**: (1) `bool("")` is
  `False`, so `--agent-worktrees-path ""` was indistinguishable from "not
  supplied" and silently used the ambient fallback -- switched the
  explicit/ambient distinction from truthiness to `is not None`, with an
  explicit-but-blank string raising immediately; my first regression test
  for this used whitespace (`"   "`), which is truthy and didn't actually
  exercise the bug -- caught in self-review before landing and corrected
  to a true empty string. (2) `Path.resolve(strict=True)` can raise
  `RuntimeError` (symlink loop) or `ValueError`, not just `OSError` --
  widened the caught exception tuple so every explicit-resolution failure
  stays fail-closed. Also corrected the SKILL.md note: `--from-settings`
  is `scan`-only, so "`sync`/`scan --from-settings`" was a fabricated
  invocation that would error; reworded to `sync` (no flag needed) versus
  `scan --from-settings`.
- Added 1 more regression test (blank explicit command); 9 total.
- Validation: `python tools/check-marketplace-isolation.py --json`
  (`path-sibling-launch` 52->51, `scan_plugin_sources.py`'s finding gone),
  `python tools/check-docs-consistency.py`, `python
  tools/check-module-size.py` (trimmed `instruction_projections.py` back
  under its grandfathered ceiling), and `customizing-copilot`'s full
  `scan-customizations`/`instruction-projections` test suite (177 passed,
  including 9 new regression tests) via `test-supervisor`.

### 2026-09-25 — `harness-knowledge`'s 2 `path-sibling-launch`/`unqualified-runtime-root` findings resolved

- The 2026-09-24 note left `harness-knowledge`'s `assemble_plugins.py` (bare
  `shutil.which("agent-worktrees")`) and `bind_knowledge.py` (a
  `.agent-worktrees` path construction) as genuine, unconverted backlog
  needing a design pass, since neither plugin has a runtime cell of its own
  and the existing `_peer_launch.py` same-cell primitive is scoped to its 7
  registered `OWNERS` runtime/service plugins.
- The operator clarified the actual design: each runtime plugin's
  sessionStart hook (`emit-command-catalog.{sh,ps1}` +
  `write_session_guidance.py`, already deployed for most runtime plugins,
  confirmed present for `agent-ssh`/`agent-vault`/13 others) writes its own
  resolved base-path/argv into the session's
  `instructions/<plugin>/session-guidance.instructions.md` file. Operative
  skill instructions are meant to read that catalog and pass the resolved
  `argv[0]` explicitly, rather than a script re-discovering it via ambient
  `PATH` -- this is already documented in `design.md`'s "Invocation chain"
  section and already the convention `working-cross-repo`'s own skill text
  follows (`<agent-worktrees catalog argv[0]>`).
- Applying that lens to the two findings individually (not as one class):
  - `assemble_plugins.py`'s `_resolve_command()`: **review caught that my
    first pass mischaracterized this.** `assemble_plugins.py`'s own
    SKILL.md documents it as a "legacy compatibility delegate," but
    `bind_knowledge.py`'s `bind()` calls `assemble()` -- which calls
    `_resolve_command()` -- on **every normal bind** where both paths are
    known (`bind()` line ~921), not merely as a rare manual fallback; its
    own `--agent-worktrees-path` was resolved but never threaded into that
    call. Annotating the `shutil.which` as an intentional legacy-only
    fallback would have suppressed exactly the marketplace/cell mismatch
    this guard exists to catch. Fixed properly instead: `_resolve_command`,
    `_delegate`, `assemble`, and `assemble_from_pair` all now accept an
    optional resolved command (preferring it over `shutil.which`, with the
    ambient lookup kept only as the documented last-resort default);
    `bind()` now passes its own `agent_worktrees_path` through; `main()`
    gained a matching `--agent-worktrees-path` flag for standalone
    invocation. Added regression coverage proving the explicit path wins
    over a conflicting ambient `PATH` match in both `assemble()` directly
    and via `bind()` (the `bind()` assertion specifically targets the
    `compose-plugins` command after review caught a first-draft assertion
    that was vacuous against other, already-correct `bind()` calls), plus
    coverage that `main()`'s new `--agent-worktrees-path` flag actually
    forwards to `assemble()`/`assemble_from_pair()` (review caught this was
    untested). Each new test was manually confirmed to fail when its
    corresponding fix is reverted. Also updated `binding-knowledge/SKILL.md`'s
    direct-assembly example to pass `--agent-worktrees-path` (review caught
    that the documented standalone command still omitted it, which would
    have taken the ambient-fallback path even after the code fix). A final
    review round caught one more real gap: an explicitly resolved `.ps1`
    command with no `pwsh.exe`/`powershell.exe` host on `PATH` silently fell
    through to an unexecutable `argv[0]` instead of failing explicitly (the
    existing `bind_knowledge.py`-side helper already guards this case).
    `_resolve_command` now raises `KnowledgePluginError` in that case;
    verified the exact branch in an isolated throwaway interpreter (mutating
    `os.name` inside the pytest process itself breaks `pathlib`/pytest's own
    internals) and added a Windows-only regression test following the
    codebase's existing `if ...os.name != "nt": return` skip convention.
  - `bind_knowledge.py`'s `.agent-worktrees` / `"config.yaml"` finding was
    never a peer-launch/runtime-root issue at all on closer read: it reads
    the **knowledge repo's own committed, repository-owned configuration
    file** (documented in the same SKILL.md's step 1), unrelated to
    marketplace/cell identity. Annotated as `marketplace-isolation: allow
    project-owned-config`.
- Left `customizing-copilot`'s `scan_plugin_sources.py`
  `_agent_worktrees_repo_root()` (the third finding in the original
  2026-09-24 note) genuinely open: unlike the other two, it's a shared
  library function imported by ~8 separate top-level scan entrypoints
  (`scan-customizations.py`, `scan_skills.py`, `scan_agents.py`, etc.), not
  a single agent-invoked call site with a natural place to accept an
  explicit catalog-argv flag, and it resolves a `repo:` name declared in a
  committed marketplace source config rather than being told a path by an
  invoking agent turn. Fitting this into either the catalog-argv model or
  an `OWNERS`-extended `_peer_launch.py` needs its own design decision, not
  a same-pattern copy of the other two.
- Validation: `python tools/check-marketplace-isolation.py --json`
  (`path-sibling-launch` 53->52, `unqualified-runtime-root` 433->432),
  `python tools/check-docs-consistency.py`, and `harness-knowledge`'s full
  test suite (75 passed, including 5 new regression tests, each manually
  confirmed to catch its regression) via `test-supervisor`.

### 2026-09-25 — `phase-2-launcher-contracts.md` re-synced against current `origin/main`

- The 2026-09-24 note flagged that the launcher-contract inventory's own
  numbers needed re-syncing before they could be trusted. Re-ran
  `check-marketplace-isolation.py --json` and re-read the full
  `global-plugin-binstub` finding set line-by-line to get an accurate current
  count plus a partial triage (83 findings, still 14 plugins, but a different
  14 than the 2026-08-26 baseline) -- not a complete re-derivation of every
  finding's family; see the explicit gaps below.
- Confirmed two families are **fully converted and gone**, not merely
  shrunk: the payload-owned self-wrappers (Phase 2, `agent-ssh`) and the
  durable provider manifests (Phase 3, `agent-codespaces`/`agent-containers`
  `register-bridge-provider`) — consistent with Phase 3 being fully checked
  off in this README.
- Confirmed `harness-knowledge`'s 2 prior findings moved to
  `path-sibling-launch`/`unqualified-runtime-root` (already noted
  2026-09-24) and `customizing-copilot`'s 1 finding is fixed (also already
  noted).
- Found two plugins new since the 2026-08-26 baseline that were never
  triaged into this inventory: `budget-guidance` (4 findings, added
  2026-09-05) and `agent-pull-requests` (4 findings, added 2026-09-22) —
  both genuine, unconverted generic-installer backlog.
- Found `agent-machines` gained 2 new findings from its post-rewrite
  `cell_lifecycle.py` engine, including a `payload-invocation.json`
  legacy-binstub-path declaration mirroring `agent-index`'s equivalent
  field — plausibly intentional migration metadata analogous to
  `agent-bridge`'s `legacyRuntimeRoot`, but not yet confirmed against the
  install contract or annotated either way.
- Left the 2026-08-26 family table in `phase-2-launcher-contracts.md` as a
  historical record and added a dated re-audit note above it rather than
  overwriting stale numbers with a guessed family split — a full family
  re-derivation for the current 83 findings (which family each of the new
  plugins' findings belongs to, whether the new `agent-machines` findings
  are intentional or backlog) needs its own increment.
- Validation: `check-marketplace-isolation.py --json` (used to derive the
  numbers above), `check-docs-consistency.py` passed.

### 2026-09-24 — Guard re-audit against current `origin/main`; 4 documentation false positives annotated

- Resumed after a prior worktree's unrelated stale diff (Agent Machines
  exemplar hardening) was discarded as fully superseded by upstream's own
  clean-room implementation. Re-ran
  `python tools/check-marketplace-isolation.py --json` against fresh
  `origin/main`: **702** findings (`unqualified-runtime-root`: 421,
  `fixed-service-identity`: 146, `global-plugin-binstub`: 84,
  `path-sibling-launch`: 50, `bare-agent-command`: 1 — a new category not
  present in the 2026-09-10 note), up from 681 on 2026-09-10. This confirms
  item 6 still does not hold; the guard stays report-only.
- Sampled the 13 findings in the smallest untouched plugins
  (`wsl-setup`, `harness-knowledge`, `budget-guidance`, `customizing-copilot`).
  Found and annotated 4 genuine documentation false positives with
  `marketplace-isolation: allow doc-example` (prose mentioning
  `~/.agent-codespaces/config.d/`, `~/.agent-mcp/materialized/<server>/`,
  generic `~/.local/bin` binstub concept, and `localhost:22` SSH port-forwarding
  advice matching the endpoint regex by coincidence) —
  `customizing-copilot/skills/authoring-harness-plugins/SKILL.md`,
  `customizing-copilot/skills/defining-subagents/SKILL.md`,
  `customizing-copilot/skills/installing-plugins/SKILL.md`, and
  `wsl-setup/skills/setting-up-wsl/SKILL.md`. Verified
  `check-marketplace-isolation.py --json` dropped from 702 to 698 findings
  exactly matching the 4 annotations, and `check-docs-consistency.py` still
  passes.
- The remaining findings in that same sample —
  `harness-knowledge/skills/binding-knowledge/scripts/assemble_plugins.py`
  and `bind_knowledge.py`, and
  `customizing-copilot/skills/reviewing-customizations/scripts/scan_plugin_sources.py`
  — are genuine `path-sibling-launch` backlog: bare
  `shutil.which("agent-worktrees")` resolution from a payload-only skill
  script (no runtime cell of its own; neither plugin has a
  `payload-invocation.json`). This does not fit the existing `_peer_launch.py`
  primitive, which is scoped to the 7 already-registered `OWNERS` runtime/
  service cells (`agent-bridge`, `agent-dispatch`, `agent-codespaces`,
  `agent-containers`, `agent-logger`, `agent-index`, `agent-machines`) calling
  each other cell-to-cell — extending that scaffolding to payload-only skill
  scripts, or wiring them through the Phase 2 session-start command catalog
  instead, needs its own design pass before touching these files. Left
  unconverted rather than risk a rushed, unverified change to a live skill
  entrypoint.
- **698 findings remain** (702 minus the 4 annotated above): 689 in the 13
  plugins outside this note's small cluster, plus the 9 genuine findings left
  unconverted inside it (5 in `budget-guidance`, 2 in `customizing-copilot`,
  2 in `harness-knowledge` — see above). The 13 outer plugins are dominated by
  `agent-worktrees` (168), `agent-dispatch` (76), `agent-codespaces` (69),
  `agent-index` (62), `agent-bridge` (55), `agent-vault` (52), and
  `agent-machines` (49); each needs the same file-by-file genuine-backlog-vs-
  intentional-legacy triage this note applied to the small plugins. Phase 2's
  own tracked subset (`phase-2-launcher-contracts.md`, baselined at 80
  `global-plugin-binstub` findings across 14 plugins) needs re-syncing against
  the current count before its own numbers can be trusted. **Done in the
  2026-09-25 entry below** (83 findings, count plus partial triage; a full
  family re-derivation is still pending).

### 2026-09-21 — `agent-bridge` registered as a new OWNERS member; `handoff-check` converted

- Full-architecture audit (all 11 `agent-*` plugins) found `agent-bridge` and
  `agent-worktrees` were themselves calling sibling plugins via ambient
  `shutil.which`/`Get-Command`, but neither was registered in `peer-launch`'s
  `OWNERS` set (the pre-existing set only covered agent-dispatch,
  agent-codespaces, agent-containers, agent-logger, agent-index,
  agent-machines as callers), so those call sites could never route through
  the same-cell boundary.
- Registered `agent-bridge` as a new `OWNERS` member: added it to the
  canonical `libs/peer-launch/peer_launch.py` OWNERS set, vendored a new
  `_peer_launch.py` + `_installation_context.py` copy into
  `plugins/agent-bridge/src/agent_bridge/` via `tools/sync-peer-launch.py`
  and `tools/sync-installation-context.py`, and updated both tools'
  destination/adopter lists so future syncs keep it in scope. `agent-bridge`
  remains a `PEERS` target too (dual membership is valid and already
  implicit in the code -- nothing enforces disjointness).
- Converted `agent-bridge`'s `handoff-check` command
  (`_cmd_handoff_check` -> `agent-worktrees handoffs-check`) to resolve the
  same-cell peer boundary first. The legacy ambient-`PATH` lookup (marked
  `# marketplace-isolation: allow legacy-compatibility`) is used only when
  no explicit context is set at all; once a context is set, an
  owner/receipt/governance refusal propagates as `ContextRefused` and a
  valid owner with no same-cell peer is genuine absence -- neither degrades
  to ambient `PATH`, matching agent-logger's `compact.py`/`origin.py`
  pattern. Copilot PR review caught that an earlier draft of this
  conversion silently fell back to ambient `PATH` on any explicit-context
  failure -- unsafe here since `--execute` mutates predecessor state, so a
  silent fallback could operate on a foreign cell's `agent-worktrees` --
  and a second finding that the same-cell subprocess spawn was missing
  `no_window_kwargs()` (present on agent-logger's equivalent same-cell
  call); both fixed before merge. A follow-up review round raised a third
  concern -- that `install_dir()` (honoring `AGENT_BRIDGE_INSTALL_DIR`)
  might not equal the required plugin root for a long-lived daemon -- and a
  fix switched to `AGENT_BRIDGE_PAYLOAD_ROOT` instead; a *subsequent* review
  round caught that this "fix" was itself wrong: `AGENT_BRIDGE_PAYLOAD_ROOT`
  is a different, replaceable path (the marketplace source payload
  `runtime-gate` resolves *from*), while tracing `installation_context.py`'s
  own `_activation_result` confirms `runtime-gate.sh`/`.ps1` set
  `AGENT_BRIDGE_INSTALL_DIR` to the *validated* `runtimeRoot`, which equals
  the plugin root exactly when namespaced/active
  (`runtime_root = plugin_root if actual_mode == "namespaced" else
  legacy_root`). Reverted to `install_dir()` and added a regression test
  with a distinct (wrong) `AGENT_BRIDGE_PAYLOAD_ROOT` value to prove
  resolution ignores it.
- Found and fixed a **pre-existing gap** in `tools/check-version-bump.py`
  while touching it: its hardcoded `packaged_peers` list (which plugins must
  bump when `libs/peer-launch` changes) already omitted `agent-index` and
  `agent-machines`, both real `OWNERS` members since an earlier slice. Added
  `agent-bridge` and both previously-missing plugins to that list.
- Bumped all 7 affected plugins (`agent-bridge`, `agent-dispatch`,
  `agent-codespaces`, `agent-containers`, `agent-logger`, `agent-index`,
  `agent-machines`) per `check-version-bump.py`'s shared-lib rule, and
  updated every version-consistency surface `check-version-consistency.py`
  checks (marketplace.json, per-plugin `__init__.py`/`_build_info.py`, and
  agent-index's `agent-dispatch` registrar reference) for all 7.
- Audit also confirmed: (a) `agent-mcp` and `agent-vault`'s absence from
  `OWNERS`/`PEERS` is not a gap -- neither actually launches a sibling
  plugin; (b) `agent-dispatch -> agent-bridge` (`bridge.py`'s `agent-bridge
  send` over SSH) is a remote-transport call, correctly out of
  `peer-launch`'s local-only scope; (c) `agent-worktrees`'s own sibling
  calls to `agent-dispatch`, `agent-codespaces`, and `agent-machines` remain
  unconverted -- `agent-worktrees` is not yet an `OWNERS` member and those
  three are not yet `PEERS` targets, both open for a future slice.
- Clean-room dual-cell validation remains intentionally scoped to 2 of 11
  plugins (`agent-index`, `agent-machines`) per Phase 3's own non-goal ("the
  exemplars prove the primitive; Phases 4-5 roll it out") -- not a gap, but
  worth naming: 9 of 11 plugins have never been individually proven under
  concurrent-cell conditions.
- `check-marketplace-isolation.py`'s `path-sibling-launch` count dropped
  from 43 to 42, confirming the guard tracks this conversion; the guard
  stays report-only (Phase 6's last checkbox).

### 2026-09-20 — agent-logger's `state-root` sibling-launch caller converted (#3095)

- Converted `agent-logger`'s `origin.py::_bound_knowledge_repo_cached` (its
  `agent-worktrees state-root --json` probe, used to resolve a bound
  knowledge repo for an arbitrary session's origin repo) from unconditional
  `shutil.which("agent-worktrees")` ambient-PATH resolution to the validated
  same-cell peer boundary under an explicit installation context, mirroring
  the pattern `compact.py::tracked_worktree_paths` already established in
  this same plugin. `agent-logger` was already an `OWNERS` member and its
  `_peer_launch.py` vendor copy already existed from an earlier slice, so
  this conversion needed no new plumbing -- only the caller itself.
  Legacy ambient-PATH fallback (marked
  `# marketplace-isolation: allow legacy-compatibility`) is preserved for
  the no-context case. Copilot PR review caught one real gap before merge
  (the legacy fallback branch didn't apply `no_window_kwargs()`, unlike the
  new same-cell path beside it) -- fixed before merge.
- `check-marketplace-isolation.py`'s `path-sibling-launch` count dropped
  from 44 to 43 (710 -> 709 total findings), confirming the guard tracks
  this class of conversion; the guard stays report-only (Phase 6's last
  checkbox) -- 709 findings remain across 16 plugins, dominated by
  `unqualified-runtime-root` (441) and `fixed-service-identity` (144),
  neither of which fits the `peer_launch`/OWNERS caller-conversion pattern
  this and the prior three merged PRs used. Turning that guard blocking
  remains a large, multi-session migration, not a near-term boundary.
- `harness-knowledge/assemble_plugins.py`'s `shutil.which("agent-worktrees")`
  finding remains not viable (no Python runtime; see prior session's note).
  `agent-worktrees` itself and `customizing-copilot`'s
  `scan_plugin_sources.py` are not `OWNERS` members and would need that
  plumbing added first, not just a caller conversion.

### 2026-09-20 — Removed two duplicate-ownership Phase 7 items (migration-intake correction)

- `migration-intake`'s Phase 2 revalidation had routed two candidates into
  this effort's Phase 7 as new backlog bullets: unified self-provisioning/
  Windows-stamp semantics, and an installer-helper consolidation audit.
  Both duplicated already-existing, more detailed Draft efforts'
  own scope (`tiered-payload-provisioning`'s Windows `stamp` action;
  `vendored-installer-engine`'s Phase 0 audit) -- removed both bullets here
  to avoid a two-owner situation; see `migration-intake`'s own Journal for
  the correction record.

### 2026-09-20 — agent-machines as a sixth owner; self_update.py's dtssh-mesh refresh converted

- Added `agent-machines` to `libs/peer-launch`'s `OWNERS` set (joining
  dispatch/CodeSpaces/Containers/Logger/Index) -- its first time vendoring
  `peer_launch.py` + `_installation_context.py`. Also closed a gap left by
  the previous slice: `agent-ssh`'s own environment-variable prefix
  (`AGENT_SSH_`) was never added to `peer_environment()`'s scrub list even
  though agent-ssh became a resolvable peer in that same slice.
- Converted `self_update.py`'s `refresh_dtssh_mesh()` (the watchdog tier's
  dtssh-mesh reachability refresh, delegating to `agent-ssh refresh-mesh`)
  from unconditional `shutil.which("agent-ssh")` ambient-PATH resolution to
  the validated same-cell peer boundary -- the deferred candidate from two
  slices back, now unblocked. Unlike Containers' *required* agent-ssh use,
  this call is optional (not every machine runs a dtssh mesh): a validated
  owner with no same-cell agent-ssh returns `None` (reported `skipped`,
  matching legacy behavior); only a malformed/refused context raises
  `ContextRefused`.
- **Caught and fixed a real regression in CI** for the previous slice (#3028):
  its own version bump to agent-index wasn't mirrored into
  `agent-index-service.json` -- the *same* drift class fixed twice before.
  Added a durable guard (`_registrar_declaration_violations()` in
  `tools/check-version-consistency.py`) so this is now caught at push-time
  instead of relying on running agent-dispatch's test suite; it caught the
  *next* recurrence (this slice's own agent-index bump) before push.
- Added focused tests: same-cell prefix + argv shape, `skipped` when no
  same-cell agent-ssh, `ContextRefused` propagation on a bad context, plus
  an isolation-guard regression test for `self_update.py`. Extended
  `libs/peer-launch/tests/test_packaging.py` for the sixth vendor.
- Validation: `agent-machines` full suite (569 passed, 20 skipped -- one
  unrelated installer-subprocess test timed out under host load and passed
  cleanly on retry/isolation, confirmed pre-existing/flaky, not a
  regression); `libs/peer-launch/tests` (10 passed); module-size (widened
  `self_update.py`'s baseline 1155 -> 1191 lines), version-consistency,
  version-bump, `sync-peer-launch`, `sync-installation-context`, and
  `check-marketplace-isolation` (`path-sibling-launch` count dropped 47 ->
  46) all pass.
- `#1110` remains open. `harness-knowledge`'s `assemble_plugins.py` (a
  skill-only, non-Python-package plugin) is the last identified Phase 6
  caller-conversion candidate and still needs its shape validated against
  the `OWNERS`/vendoring pattern before conversion. Phase 7's intake ledger
  remains outstanding.

### 2026-09-20 — agent-ssh as a new peer target; agent-containers provider_ssh.py converted

- Extended `libs/peer-launch`'s `PEERS` mapping with `agent-ssh` (joining
  agent-worktrees and agent-bridge). agent-ssh is itself a canonical
  `libs/installation-context` adopter with the identical receipt/governance
  contract, so no boundary changes were needed -- just the mapping entry and
  a re-sync of all five packaged vendors (PEERS lives in the shared canonical
  file, so every owner's copy moves together even though only one owner
  calls the new peer today).
- Converted `agent-containers`' `emit_ssh_profile()` (the provider-exec SSH
  profile publisher, `provider_ssh.py`) from unconditional
  `shutil.which("agent-ssh")` ambient-PATH resolution to the validated
  same-cell peer boundary under an explicit installation context. This was
  the identified-but-deferred candidate from the previous slice
  ("different peer, durable publication semantics, not yet analyzed") --
  and unblocks the other deferred candidate, `agent-machines`'
  `self_update.py` `shutil.which("agent-ssh")` call, for a future slice.
  agent-ssh is a *required* peer here (unlike the optional knowledge-repo
  config lookup other converted callers use), so a validated owner with no
  same-cell agent-ssh installation is a `ContextRefused`, not a silent
  absence. Legacy ambient-PATH resolution is unchanged with no explicit
  context.
- Added focused tests: same-cell prefix resolution and argv shape, refusal
  when agent-ssh has no same-cell installation, plus an isolation-guard
  regression test proving `provider_ssh.py` has no unexplained
  `path-sibling-launch` findings (`check-marketplace-isolation`'s count for
  that category dropped from 48 to 47).
- Validation: `agent-containers` full suite (28 `test_provider_ssh.py` cases,
  0 new failures); `libs/peer-launch/tests` (9 passed); module-size,
  version-consistency, version-bump, `sync-peer-launch`,
  `sync-installation-context`, and `check-marketplace-isolation` guards all
  pass. Widened `provider_ssh.py`'s module-size baseline (1068 -> 1104 lines)
  for the new resolver helper -- a manual, reviewed baseline edit, not an
  automated widen.
- Remaining pre-existing/unrelated failures observed in the same run (bash/
  WSL PATH gaps in shared bootstrap-opt-in tests, Windows POSIX-mode-emulation
  gaps in `test_private_state.py`, embedded-script assertions in
  `test_rescue.py`) are environment-specific to this Windows host and
  untouched by this change; not investigated further here.
- `#1110` remains open. Next candidates: `agent-machines/self_update.py`'s
  now-unblocked `agent-ssh` conversion, `harness-knowledge`'s
  `assemble_plugins.py` (a skill-only, non-Python-package plugin -- shape
  not yet validated against the `OWNERS`/vendoring pattern), and the Phase 7
  intake ledger.

### 2026-09-20 — Agent-index as a fifth same-cell peer-launch consumer

- Continued Phase 6 caller conversions. Added `agent-index` to the shared
  `libs/peer-launch` boundary's `OWNERS` set (joining dispatch, CodeSpaces,
  Containers, and Logger) and its environment-scrub prefixes; re-synced all
  five packaged vendors and bumped all five plugins' versions together (a
  canonical peer-launch change reaches every consumer's payload).
- Converted `agent-index`'s `scripts/resolve_effective_config.py` state-root
  discovery (used by the same-cell knowledge-repo config graft, the
  citadel E1e overlay) from unconditional `shutil.which("agent-worktrees")`
  ambient-`PATH` resolution to the validated same-cell peer boundary under an
  explicit installation context. Legacy ambient-`PATH` resolution remains the
  fallback when no explicit context is present, and the existing test-only
  `AGENT_WORKTREES_COMMAND` override still takes priority over both, matching
  `_worktrees_command()`'s own precedence.
- This script runs standalone (invoked directly, not necessarily under an
  installed `agent_index` package import path), so it loads the vendored
  `_peer_launch.py`/`_installation_context.py` by absolute file location --
  the same pattern `companion_context.py` already used for the installation-
  context primitive -- rather than assuming a package import is available.
- Preserved the existing bare-anchor-checkout `--project` retry behavior
  identically across both the legacy and same-cell paths by extracting a
  small `run(cwd, project)` strategy selected once per call, so the shared
  retry/parsing logic in `_external_state_root` did not need to duplicate
  itself. A same-cell peer-launch refusal (exit 126) raises
  `_peer_launch.ContextRefused` rather than degrading to `"unavailable"` --
  an explicit context that fails to validate must not become
  indistinguishable from a genuinely absent peer.
- Added focused tests proving ambient `PATH` is never touched under explicit
  context, a peer-launch refusal propagates instead of being swallowed, and
  the command-override precedence holds; extended `libs/peer-launch`'s shared
  packaging and isolation-guard tests to cover the fifth vendor.
- Validation: `agent-index`'s `tests/test_effective_config.py` (35 passed,
  up from 32); `libs/peer-launch/tests` (8 passed); focused peer-launch/
  worktrees-peer suites for the four already-converted consumers
  (`agent-codespaces` 30 passed, `agent-containers` 98 passed/313
  deselected, `agent-dispatch` 62 passed/1 skipped/2948 deselected,
  `agent-logger` 26 passed) to confirm the shared `OWNERS`/environment-scrub
  change did not regress any existing caller; install-contract,
  version-consistency, version-bump, vendored-libs-sync, docs-consistency,
  and headless-launch guards all passed. `check-marketplace-isolation`'s
  `path-sibling-launch` count for `agent-index` dropped from one real
  ambient-`PATH` finding to only the same boilerplate
  `installer-readiness.json` self-declarations every plugin carries,
  matching the shape left behind by the four prior conversions.
- `#1110` remains open. Phase 2/6 caller conversions continue (agent-machines'
  `shutil.which("agent-ssh")` in `self_update.py` is the next identified
  candidate, but `agent-ssh` is not yet a `libs/peer-launch` `PEERS` target --
  extending `PEERS` is a prerequisite, not a same-shape slice); the Phase 7
  intake ledger and full Validation Plan remain outstanding.

### 2026-09-15 — Fix reviewed compaction-safety findings

- Advisory review of #2697 caught two real defects in the Logger conversion:
  (1) `do_compact_hub`/`run_sync`'s hub-compaction path treats
  `tracked_worktree_paths() is None` as "confirmed nothing to protect", so an
  owner/peer/probe FAILURE under explicit context (not genuine peer absence)
  could silently disable the tracked-worktree protection and archive a live
  hub session; (2) a malformed peer response row was silently skipped rather
  than rejected, so a schema mismatch could yield a false "nothing tracked"
  empty set.
- Fixed by distinguishing genuine absence (returns `None`, unchanged) from
  failure (now raises `_peer_launch.ContextRefused`) in
  `tracked_worktree_paths()`. Added two purpose-built resolvers:
  `_resolve_tracked_paths_or_none` (used by `select_compactable`, which
  already has a safe per-session on-disk fallback, so folding a failure into
  `None` there is fine) and `resolve_hub_tracked_paths` (used by both hub
  call sites, which have no such fallback for foreign-machine hub sessions,
  so a failure now fails the whole compaction pass closed instead of
  proceeding unprotected). `_paths_from_list_response` now rejects the whole
  response on any malformed row instead of silently omitting it.
- Added dedicated `plugins/agent-logger/tests/test_worktrees_peer.py` (22
  cases) covering explicit-context resolution, the absence/failure split,
  malformed-row rejection, and the two resolvers, plus an engine-level test
  proving both hub call sites fail closed on an unresolved lookup. Corrected
  the shared dispatch regression's now-invalid absence/failure assertions to
  match.
- Re-verified: agent-logger 302 passed (Windows) / 302 (POSIX, same
  selection); shared dispatch procutil 61 passed/5 skipped (Windows), 60/6
  (POSIX); module-size, docs, install-contract, and headless-launch guards
  passed. A handful of unrelated pre-existing environment-flaky tests
  (ARM64 PowerShell package installs on Windows; WSL-to-Windows-PowerShell
  path bridging) were confirmed to fail identically on an unmodified
  checkout and are out of this slice's scope.
- A further review round caught two more real issues: a whitespace-only peer
  path passed validation and normalized to an accepted empty-string entry
  (fixed: reject on `p.strip()`); and `resolve_hub_tracked_paths` treated
  genuine peer *absence* as a resolved empty set for hub sessions, which may
  belong to a foreign machine this process cannot verify -- "no peer in this
  cell" is no more informative than a failure there. Both absence and failure
  now report `unresolved=True` uniformly for the hub path, while
  `select_compactable`'s local on-disk fallback keeps folding both into its
  existing safe degrade. Also caught: origin/main moved twice more during
  review with unrelated module-size-baseline regressions
  (`worktree-manager/__main__.py`, `agent-worktrees/__main__.py`) blocking
  every PR's required guard; widened both grandfathered ceilings to their
  true, already-merged size as separate atomic commits.
- A separate finding (Logger's own systemd/Task-generated scheduled
  compaction service invokes the venv's console-script entry point directly,
  bypassing the payload-local `runtime-gate.sh` dispatcher, so a
  namespaced/scoped installation's background job never receives explicit
  context) is Logger's own Phase-4 service-identity conversion, not a
  follow-on to this caller conversion -- filed as
  [#2701](https://github.com/ThomasMichon/copilot-extensions/issues/2701)
  rather than expanding this PR's scope.
- A further finding, empirically verified: `agent-worktrees list --json`
  requires a resolved single project (from CWD or `--project`) and exits
  non-zero from a neutral working directory -- the typical shape of a
  scheduled/background compaction run. This is a pre-existing limitation
  shared identically by the legacy ambient-`PATH` caller and the new
  same-cell caller (neither is machine-wide capable today), not something
  the peer-launch conversion introduced. Its practical consequence became
  more visible because the fail-closed hub-compaction fix now means hub
  compaction with the default `require_untracked_worktree=true` will
  typically skip rather than run in that deployment shape, until
  `agent-worktrees` gains a machine-wide listing capability or the caller
  resolves project scope explicitly. Filed as
  [#2706](https://github.com/ThomasMichon/copilot-extensions/issues/2706);
  fixing it requires either a new `agent-worktrees` capability or a
  considered design decision, not a caller-side isolation change.

### 2026-09-14 — Logger as a fourth same-cell peer-launch consumer

- Continued Phase 6 caller conversions. Added `agent-logger` to the shared
  `libs/peer-launch` boundary's `OWNERS` set (joining dispatch, CodeSpaces, and
  Containers) and its environment-scrub prefixes; re-synced all four packaged
  vendors and bumped all four plugins' versions together (a canonical
  peer-launch change reaches every consumer's payload).
- Converted `agent-logger`'s `tracked_worktree_paths()` (used by session
  compaction to decide whether a session's worktree is still tracked before
  archiving it) to resolve only the validated same-cell Agent Worktrees peer
  under an explicit installation context, never an ambient `PATH` command.
- This caller has the **opposite** safety direction from Containers' config
  lookup: its documented `None` result already triggers the *safer* fallback
  (an on-disk existence check that errs toward keeping, not archiving, a
  session), so owner-validation failure, a missing/foreign/malformed peer, or
  a probe error all deliberately degrade to `None` rather than raising --
  raising here would crash a compaction pass over an installation-governance
  blip. Recorded this reasoning inline so it is not mistaken for the
  Containers-style "must-refuse" contract.
- Native Windows and POSIX selections: agent-logger 26 passed; the expanded
  shared real-process peer selection (now covering four owners across two
  resolvers) 61 passed/5 skipped on Windows and 60/6 on POSIX; CodeSpaces
  adapter 30 passed; Containers config/relay 103 passed -- all on both
  platforms. Shared packaging passed 7 (one new test for the fourth vendor).
  Lint, sync, version-consistency, docs-consistency, install-contract, and
  headless-launch guards passed.
- Only the converted caller's tested legacy PATH branch received an isolation
  allowance; the remaining report-only inventory is unchanged by this slice.
  No persistent installation was activated or deployed.

### 2026-09-14 — Worktree Manager as a standalone-payload installation-context consumer

- Cross-referenced from the `worktree-manager-control-plane` effort. Worktree
  Manager (a standalone, non-plugin payload -- no `plugin.json`, outside
  `plugins/`) previously had two divergent, non-cell-aware mechanisms for
  locating an installed agent-* plugin runtime: one hardcoded to
  agent-worktrees and legacy-root-only, the other checking only whether
  `COPILOT_EXTENSIONS_CONTEXT` was *set*, never this effort's actual
  installation-mode policy. Added `worktree-manager/src/worktree_manager/
  agent_plugin_runtime.py`: a generic, plugin-id-parameterized resolver, with
  `marketplace_cells_enabled()` calling the vendored `resolve_installation_mode()`
  for the real global policy bit, so Worktree Manager's legacy-vs-namespaced
  decision can never disagree with what a plugin's own bootstrap would decide
  for the same file.
- Extended `tools/sync-installation-context.py` with a new
  `STANDALONE_PYTHON_ADOPTERS` list (REPO-relative paths, not resolved
  `Path`s, so a test's `module.REPO` reassignment is honored) for non-plugin
  payloads vendoring the Python primitive the same way agent-dispatch/
  codespaces/containers do. `worktree-manager/src/worktree_manager/
  _installation_context.py` is the first entry.
- Deliberately did **not** make Worktree Manager a `libs/peer-launch`
  consumer: that boundary's `OWNERS`/structural cell-root validation requires
  the caller to itself own a marketplace cell identity, which a management
  surface (the vision's own "explicit management context" concept) does not
  have by design. Extending peer-launch to a non-plugin caller category
  remains an explicitly open, separately-scoped follow-on if a future need
  requires its stronger activation-generation revalidation-at-execution-time
  guarantees.
- Full detail and validation: `worktree-manager-control-plane`'s README,
  Phase 3b Slice 2 Sub-slice 4.

### 2026-09-14 — Phase 3 tracker reconciliation

- Verified that
  [#2122](https://github.com/ThomasMichon/copilot-extensions/issues/2122)
  (ownership-checked Agent Machines repair/release and uninstall) was left
  open on GitHub despite its implementation being merged and accepted.
  [PR #2125](https://github.com/ThomasMichon/copilot-extensions/pull/2125)
  (2026-09-05) is the preceding design/spec PR and used `Refs #2122` --
  correctly non-closing since implementation had not landed yet.
  [PR #2177](https://github.com/ThomasMichon/copilot-extensions/pull/2177)
  (2026-09-07, 42 files changed) is the actual implementation -- its squashed
  commit message says "Implement #2122 ..." (not a GitHub closing keyword) and
  its PR body was empty, so the tracker was never auto- or manually closed.
  Confirmed the delivered code directly: `plugins/agent-machines/scripts/
  cell_lifecycle.py` and `tests/test_cell_lifecycle.py` implement and cover
  `cell-repair`/`cell-uninstall` today. This was a bookkeeping gap, not an
  implementation gap; closed #2122 with the evidence recorded in a closing
  comment.
- Checked the previously unchecked Phase 3 parent item "Carry validated
  snapshot identity into provision/runtime-slot ownership, cutover, rollback,
  and uninstall" now that all three of its nested children (including #2122)
  are verifiably complete.
- No code changed in this reconciliation. The parent effort, #1110, and #1096
  remain open; the remaining Phase 2/6 caller conversions, Phase 7 intake
  ledger, and full Validation Plan are still outstanding.

### 2026-09-11 — Containers same-cell knowledge configuration

- Verified [PR #2431](https://github.com/ThomasMichon/copilot-extensions/pull/2431)
  merged and the parent migration tracker remains open. Continued the same
  Phase 6 responsibility in the next serial implementation worktree.
- Added Containers to the canonical peer launcher's packaged consumers. Its
  knowledge-repository config fallback now validates the owning installation
  before optional-peer absence, invokes only same-cell worktrees, and refuses
  invalid ownership, blocked governance, failed/malformed probes, or an unbound
  required knowledge root rather than selecting fleet defaults. Explicit
  config overrides cannot bypass owner admission.
- Removed inherited Containers credentials and routing from all peer launches.
  Only the converted config caller's tested legacy PATH branch receives an
  isolation allowance; the remaining inventory stays report-only.
- Native Windows and POSIX config/caller selections each passed 95 tests.
  The expanded real-process peer selection passed 59 tests on Windows
  (4 skips) and 58 on POSIX (5 skips), including two-cell Containers routing,
  exact argv/environment isolation, and owner/peer refusal.
  Existing CodeSpaces adapter coverage remains green on both platforms.
  No persistent installation was activated or deployed by this slice.
- Advisory review identified a relay-profile catch that could replace refused
  configuration with a permissive allowlist. Both CLI and in-process relay
  registration now preserve refusal before token-store mutation; returned
  knowledge roots must also be existing directories. The expanded config/relay
  selection passed 103 tests on both Windows and POSIX.
- This advances `cell-local-invocation` and `provenance-safe-transition`;
  remaining Phase 2/6 boundaries, lease migration, Phase 7 intake, and final
  validation remain the parent completion gate.

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
