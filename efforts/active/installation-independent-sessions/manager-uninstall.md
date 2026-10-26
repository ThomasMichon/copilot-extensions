# Supported Manager Uninstall Prerequisite

Back to the [canonical effort](README.md). Tracking issue: #8864.

**Status:** reviewed contract; implementation review corrections under validation.
The earlier candidate qualified actual Linux and Windows removal; the revised
candidate still requires changed-head native qualification and passing review.
No release or host operation is asserted by this document.

## Selected intent

The operator selected `supported-uninstaller-first`:

> Add a supported product uninstaller first (larger scope)

Provide that product capability before resuming the actual two-product
installation-survival experiment. Test-only deletion is not the selected path.
This prerequisite does not replace the parent effort's copied wrapper, current
boundary handlers, same-process interactive survival or release acceptance.

## Bounded contract

The following implementation and validation refinements are
**agent-recommended**, not additional verbatim operator requirements.

- Provide a documented, supported Manager uninstall operation through its
  existing installer ownership. Prefer extending that owning operation over
  introducing an unrelated prerequisite command or another installer.
- Remove only attributable Manager runtime, generated frontdoors and owned
  integration. Preserve durable configuration, project registrations and state
  by default; do not add an implicit data purge.
- Resolve the installation through existing Manager root/source authority.
  Validate ownership and selected generations before mutation. Unknown,
  foreign, changed, leased or locked targets must produce a truthful refusal
  or unresolved outcome, never broader deletion or a success banner.
- Reuse the established construction, retention, publication and transaction
  locks in their existing order. Preserve their lock/control artifacts until
  the owning transaction is settled; do not create another selection ledger.
- Stop only attributable Manager infrastructure through its existing lifecycle
  owner when necessary. Never terminate Copilot, its mux, another installation
  or an unrelated process to make removal succeed.
- The uninstall execution must not depend on rebuilding the target runtime.
  An installed caller holding its own Windows interpreter cannot truthfully
  report removal of that locked runtime. Use an existing safe external/staged
  boundary, or refuse before effects with the supported invocation path.
  Any helper is bounded, owned and settled, not a new persistent waiter.
- Preview, repeated removal, unsupported bindings and failure reporting must
  be non-provisioning and preserve unknown effects. Partial removal is not
  successful complete uninstall.

## Existing owners

Manager's `scripts/runtime_install.py`, `self_install.py`, bootstrap and
standalone assembly remain the product boundary. Canonical installer-engine
and versioned-runtime helpers retain shared lock, publication, ownership and
retention semantics; do not patch generated or deployed copies.

Preserve the source-managed installation owner (#7028), Windows updater
handoff (#8312), wait-only launch-depth programme (#5579) and retained-runtime
restoration (#8063). This slice does not take over those programmes.

The contract closes the parent
[session-hosting intent](../../../visions/session-hosting/README.md) through
the existing [install contract](../../../docs/install-contract.md), rather than
introducing a service or a machine-global selector.

## Independently updated boundary

Apply [service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
The eventual implementation must declare its uninstall contract/capability
separately from package versions, against the actual selected installer/runtime.
This document does not claim that declaration already exists.

The proposed identity is `worktree-manager.uninstall`, generation 1, initially
supporting generation-1 callers/providers. Older providers have no established
uninstall baseline; preserve their existing install/update operations without
treating arbitrary deletion as equivalent uninstall support. A new provider may
operate on an older target only when the existing installer can validate that
target's Manager-owned layout and artifacts. Unknown target layouts refuse
before mutation; package-version equality is not an ownership or support test.

Expand the provider first without changing existing install/update semantics.
Migrate any convenience caller only after support is established through an
existing safe discovery seam. Missing legacy metadata must not trigger an
effectful probe, permissive unknown-argument handling or a silent fallback.
Document supported legacy targets and rollback/refusal behavior.

## Plan

1. [x] Read the current Manager/standalone installer entrypoints, owned artifact
   inventory, root authority, locks, retention and service lifecycle. Select
   the smallest compatible operation and record its actual interface.
2. Implement safe preview/refusal and supported uninstall through those owners,
   including standalone packaging and user-facing documentation.
3. Add focused unit and real subprocess controls, plus a changefile. Preserve
   old install/update callers and truthful unsupported uninstall behavior.
4. Exercise actual installed removal/reinstall in disposable Linux and native
   Windows roots; do not substitute prototype deletion or host rollout.
5. Land through the normal review/CI flow, then return to the parent effort's
   real interactive Copilot/mux baseline and copied-wrapper migration.

## Validation

- Preview and absent/already-removed targets cause no construction or deletion.
- Owned runtime/frontdoors are actually removed while durable-state bytes and
  foreign artifacts remain intact.
- Changed selection, malformed ownership, links/reparse points, active leases,
  concurrent update/removal and Windows file locks refuse or reconcile through
  existing owners without force-clearing custody.
- The supported installed/external caller boundary settles its own resources
  and reports actual completion; no locked runtime or orphan helper is hidden.
- Focused old/new, new/old, new/new, rollback and missing-capability cases
  exercise the real parser/process seam. Preserve strict CLI errors and existing
  install/update semantics; keep exhaustive native work out of required unit CI.
- Actual Linux and Windows install/uninstall/reinstall runs retain source,
  interpreter and ownership identity. No process-survival, version-changing
  handler adoption or operator-host claim follows from these controls alone.

## Completion boundary

Completing #8864 only supplies the missing supported removal operation.
The parent effort remains open until its own entire Plan and Validation Plan
are resolved. No host uninstall, shared-service intervention, live-session
termination, credential change or installed rollout is authorized by this plan.

## Implementation journal

- The contract merged in #8871. The provider candidate extends the existing
  installer with external-base-Python `--uninstall`, explicit apply and JSON;
  default preview/absence do not construct. Generation-1 capability and
  construction-owned inventories are separate from package versions.
- Integration review reproduced and fixed ambiguous shared-frontdoor removal
  when the provider registration was missing. Current matching registration
  is required before shared commands can be withdrawn.
- Settled daemon routing history remains preserved. Removal takes the existing
  daemon spawn lease and canonical runtime custody, rather than treating every
  routing directory as a live daemon or deleting control evidence.
- The complete affected ordinary installer/removal packet passed 115 tests
  with two platform-gated skips. Existing daemon-lease/removal controls and
  native portfolio-admission checks also pass.
- The first native installed-removal flow has not passed: actual first
  construction exceeded the local preparation limit before preview, removal or
  reinstall. Bounded diagnostic stacks showed source fingerprinting, retained
  UV preparation and then real dependency construction; they did not establish
  an uninstall failure or a successful runtime removal.
- Real installed preparation and removal/reinstall now use separate existing
  300-second native groups. Ordinary item and production deadlines remain
  unchanged. Reconcile current construction-owner changes and finish actual
  Windows/Linux removal qualification before returning to the parent's
  interactive baseline.
- After current-base reconciliation, frozen candidate
  `35dc0507cf3b59f7854255d9c415cfc7950c1161` passed the complete Linux
  uninstall unit file: 33 tests, no skips. The unchanged native operation
  completed in 74.48 seconds, including actual installed preview, installed-self
  refusal, removal, repeated absence and reinstall. Source/payload/installed
  hashes and durable-state bytes matched; the owned container was removed.
- The local Windows diagnostic reached real UV construction: venv creation
  completed in 2.047 seconds and dependency resolution in 906 milliseconds,
  followed by first-party/helper wheel building. A deliberately shortened
  diagnostic child deadline produced a normal failure receipt; this is not
  acceptance or a production deadline change. All diagnostic instrumentation
  was removed. A manual fresh-Windows lane now exercises the unchanged native
  operation before this prerequisite can be considered complete.
- Draft #8955 at `18c7e5ea77ec6e142b933b06e3374a47d646a194` passed the
  fresh Windows native removal/reinstall lane. The frozen Linux shipping-head
  native flow also passed in 60.88 seconds without skips. Required CI then
  identified a one-line growth of the grandfathered SDK facade and missing
  positive unit admission for the changed lease component.
- The correction keeps batch custody in its existing SDK component rather
  than growing the facade, and admits pure invalid-input controls with exact
  source/test hashes. It does not widen the module-size baseline, add actors
  to unit CI or waive either required check. Final-head validation and normal
  review remain required.
- Corrected head `af3aa6add10468f0e81de26e9f3afbc611a56cc1` passed required CI
  and actual Windows/Linux removal/reinstall qualification. Its first review
  identified three remaining gaps: unreliable platform process enumeration,
  permissive-umask inventory creation, and daemon-child bytecode writes into
  the sealed tree. All three are being corrected with refusal/mode controls and
  an actual installed daemon start/health/shutdown before removal. The prior
  native proof does not credit these new controls; review and changed-head
  qualification remain open.
- Current-Dev reconciliation preserves both the pre-existing native portfolios
  and the new uninstall lane, including source/engine-source contracts and
  daemon root tests. No other portfolio job changed.
- The review corrections refuse unreliable process enumeration, create
  inventories with exclusive `0600` permissions and disable ordinary daemon
  child bytecode writes. Native removal qualification now includes actual
  installed daemon start, health and graceful shutdown before removal.
- A Windows interpreter control had spent its unchanged 30-second budget
  assembling an unused standalone payload. Its synthetic-program fixture now
  copies the real shell boundary and complete first-party module closure
  directly into the isolated prefix. It still executes the production
  retention bootstrap and verifies the child PID, interpreter prefix and
  installed program/server module origins; real standalone assembly remains
  covered by the independent native acceptance owner.
- The four directly affected Windows test files passed 94 controls with four
  explicit platform-gated skips under their unchanged item/group limits.
  Canonical SDK copies and exact admitted source/test hashes also match.
  Revised-head native removal/reinstall and a fresh review remain open.
- At `a8c6496071ea1b0b7feb700a41768b25225d6804`, the fresh Windows lane
  `40281114456` passed actual installed daemon start/health/graceful shutdown,
  sealed-tree preservation and removal/reinstall. The admitted unit packet
  also passed; the size guard found three excess lines in the existing
  grandfathered installer module. Consolidating its duplicate activation
  calls restores the unchanged ceiling without widening policy.
- The next review found a home-relative provider-directory mismatch.
  Removal now applies the installer's existing `expanduser()` semantics.
  Two real preview/removal controls cover ordinary and spaced tilde paths.
  The affected installer/removal packet passed 99 Windows tests with four
  platform skips under unchanged limits.
- The new Linux run stopped before any test because building the current
  `agent-procutil` dependency could not find `cc`. The exact owned container
  was removed; no compiler was installed or result credited. Diagnose the
  canonical toolchain prerequisite before rerunning the new native head.
- A distinct fresh NoToken Linux run then declared and provisioned the
  documented `gcc`/`libc6-dev` source-build prerequisite inside only its owned
  container. At `8bcc3fb516afb54692d5855368126c74c5326e75`, uninstall controls
  passed 39 tests with no skips, interpreter controls passed 18 with one
  explicit Windows-only skip, and actual installed daemon/removal/reinstall
  passed in 57.55 seconds. Source/installed hashes and durable-state bytes
  matched. The exact container was removed and the shared image/tag unchanged.
  This does not turn the earlier missing-compiler run into a pass.
- The next review found additional empty-environment compatibility cases and
  another Manager self-reexecution path that could write bytecode. Removal now
  retains empty-as-unset directory/provider semantics while refusing a selected
  foreign provider. Installed Manager self-reexecution paths suppress bytecode;
  the native flow also executes the actual installed configuration list command
  and checks its sealed tree before removal.
- The five directly affected Windows files passed 104 tests with eleven
  explicit platform/tier skips under unchanged limits. New-head native proof
  and review are required for these additional corrections.
- A later review identified two configuration-fixture prefix slices left at
  their pre-`-B` offsets and a mismatch between lexical `..` roots and canonical
  installation records. Both fixture commands retain `-B` with the corrected
  prefix; removal validates no-indirection before resolving its target root.
  Real relative/absolute-parent CLI controls and a POSIX symlink refusal
  preserve both usable canonical roots and strict ownership.
- Once that configuration test reached its actual update step, Windows
  `CREATE_NO_WINDOW` exposed lost implicit grandchild stdout/stderr. A real
  bounded process control reproduced the loss and confirmed explicit streams
  preserve receipts. Headless engine passthrough now retains those streams
  without changing its window flags, stdin or ordinary interactive branch.
  The test verifies the decoded complete receipt rather than comparing an
  unescaped Windows path against raw JSON.
- The complete affected engine, inventory, configuration and removal packet
  passed 203 Windows tests with three explicit POSIX skips. Changed-head
  native qualification and review remain required.
