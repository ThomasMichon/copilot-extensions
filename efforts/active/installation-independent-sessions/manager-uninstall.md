# Supported Manager Uninstall Prerequisite

Back to the [canonical effort](README.md). Tracking issue: #8864.

**Status:** proposed contract; no runtime support or host operation is asserted.

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

1. Read the current Manager/standalone installer entrypoints, owned artifact
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
