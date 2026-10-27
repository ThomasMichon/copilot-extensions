# Declarative Komodo substrate lifecycle

Back to [Linux role fleet](README.md). Tracked by #8860 under #6030;
product lifecycle #6240, client-only lifecycle #6424 and operation ownership
#6241 retain their existing scopes.

## Request and completion boundary

Public-safe summary of the execution direction: fleet-manager must reproducibly
install and manage a selected Komodo deployment from versioned adopter-owned
declarative configuration. An existing official Core/Periphery/database
installation must be adoptable without changing its identities, credentials,
database, persistent storage or incumbent workload bindings. Installation and
recovery must work when Core is unavailable; the manager cannot be a Stack
hosted by that same Core.

This closes the existing `repeatable-role-fleet-adoption` and
`bounded-desired-state-control` intent in
[machine-fleet](../../../visions/machine-fleet/README.md). It does not replace
the ordinary planner, role provider or credential/release/SSH authorities.
The current `KomodoRoleProvider` is explicitly a pure Stack/bootstrap-reference
projection, not this executor.

**The delivery gate is real execution, not another proposal-only API.** A
reviewed plan, typed configuration, callback protocol or synthetic receipt is
not installation. Source implementation must reach a real native deployment
owner; an adopter composition must then exercise that path with actual official
images. Deployment permission remains separate from source review.

## Coordination and source appointment

The #8860 participant owns this bounded substrate slice in a separate source
worktree. The campaign owner retains integration and final acceptance.
Implementation is serial: this plan clears review before runtime edits.
No other participant's open PR is patched or superseded.

Reserved source is additive `fleet-manager/src/fleet_manager/komodo_*`, focused
tests under `fleet-manager/tests/`, and the corresponding product documentation.
The small shared operation/admission/node-journal additions needed for one
fixed manager command must be coordinated through #6241 before editing those
owners. No client installer, client release format, Gateway authentication,
SSH onboarding, credential producer or ARC pod owner is transferred here.

Agent-recommended decomposition and interface details follow. The requirement
above is requested; these implementation choices remain reviewable.

## Implementation contract

### 1. Selected declaration and independent prerequisites

- Extend the existing optional role-binding namespace with a bounded,
  explicitly selected manager-substrate declaration. Preserve unselected
  declarations and unrelated configuration. Keep the ordinary planner inert.
- Bind the canonical provider instance and physical host/daemon identity,
  project, supported official Core/Periphery/database image digests and
  architecture, deployment/configuration revision, resource limits, persistent
  storage identities, independent runtime root, exposure and credential-provider
  references. No raw executable, shell, arbitrary Compose/environment, inline
  credential or config-selected Python module.
- Resolve those references through code-registered existing owners. Current
  release/configuration/credential/host custody comes from those owners, not
  from a self-asserted digest or a caller-supplied installation label.
- Require an admitted Linux Docker/Compose host, independently runnable
  installer tooling and selected immutable images. Report absent image/tool/
  route/provider evidence as a prerequisite, never silently install Docker,
  provision a host, change a feed, open a tunnel or fetch mutable artifacts.
- Keep the thin workstation client free of Docker, Komodo state and manager
  installation. The admitted host-side owner executes the selected declaration;
  normal install/update converge it through the existing controller composition.
  No new resident daemon or per-agent scheduler is introduced.

### 2. Actual native installation and preserving adoption

- Provide a fixed official Komodo substrate profile with independently owned
  Core, Periphery and supported Mongo services. Render only reviewed, typed
  settings; use fixed native Docker/Compose commands through
  `agent_procutil.owned_process.run_caller_owned`, finite deadlines and bounded
  non-secret output. Reuse shared installation/private-path primitives where
  their contract applies rather than building another generic installer.
- Persist the manager's native database, key material, Periphery state,
  credential materialization and backup storage outside disposable code slots.
  Never install into a repository or make a service depend on its source checkout.
- Compose convergence uses selected image digests with no build, mutable pull,
  orphan removal, volume deletion, force takeover or unrelated resource cleanup.
  Inspect exact live resource identity, ownership and selected configuration
  before changing anything. Preserve foreign, conflicting and ambiguous state.
- First install uses native initial-admin configuration and official
  Core/Periphery onboarding. Credential generation/materialization remains
  with the selected credential owner, with values absent from source, argv,
  logs, receipts and reports. Never weaken private file permissions for an
  image's service UID; use that owner's narrowly scoped materialization path.
- Adoption requires an explicit existing-owner assertion and exact resource/
  storage/native identity evidence. A matching project name is insufficient.
  An already-matching installation is observed and recorded without replacing
  containers, rotating keys, reinitializing admin/database, issuing another
  onboarding grant or changing an incumbent Stack.
- Extend the pure provider's bootstrap requirement with actual effect-owner
  evidence only after execution; do not rewrite `installed: false` into true
  merely because configuration or a constructor validated.

### 3. Journaled convergence, health and recovery

- Reuse the existing `OperationIntent`, `AdmissionAuthority`, `NodeJournal`
  and named external resource custody, with a single reference-only fixed
  manager convergence operation. Bind exact selected deployment/configuration
  content, target, generation, actor and instance at admission and again at
  the native effect boundary. Review is necessary but is not an effect grant.
- Use the physical manager/daemon/project identity as the journal resource.
  Serialize install, adoption and update against that identity. An accepted or
  UNKNOWN attempt retains custody across process exit; no TTL, new operation
  ID, changed alias or higher generation silently takes it over.
- Persist UNKNOWN before a native mutation. Timeout, lost reply or unsettled
  child cleanup is not rollback and never automatically replays. Reconcile
  by inspecting exact resources and the original accepted operation. Changed
  credentials, foreign replacement, uncertain native state or data-version
  compatibility refuse without deleting evidence.
- Stage immutable deployment slots and retain the previous selected-good
  manifest. Publish successful selection only after bounded authenticated
  Core version/identity and registered Periphery connection checks. Container
  existence, process exit zero or an old `readiness.json` is not readiness.
- Failed-candidate retention must include the database compatibility decision.
  Updating images may modify data before health failure: retaining old files
  alone is not rollback. Refuse unsupported database major changes, require
  the native database owner's backup/migration procedure where applicable,
  and recover only a currently supported compatible selection.
- The same config-directed install/update path can restore missing owned
  Core/Periphery containers from retained configuration/storage without a
  working Core API. Native re-creation precedes authenticated final health;
  absent final health remains explicit, not a requirement to call Core before
  it can be started. Do not wipe credentials/database to recover availability.
- Status reports desired, accepted, installed, currently healthy and converged
  separately, plus drift, retained-good selection and unresolved effects.
  Explicit receipt-owned removal stops/removes only proved owned containers/
  code; data/credentials/volumes and workloads remain unless separately retired
  through their rightful owners.

### 4. Adopter realization and workload handoff

- The adopter repository declares the selected instance, host, artifacts,
  configuration, resource budgets, storage and provider references in its
  existing Fleet namespace. Host-specific resolution and source publication/
  acquisition use existing owners; generic Fleet code contains no adopter names.
- Replace the manual bootstrap dependency by composing its existing native
  configuration/credential authority with the installed Fleet lifecycle.
  Do not leave two executable reconcilers owning the same project.
- Before live adoption, compare the proposed declaration and installed native
  state, retain the exact expected incumbent identities and obtain the normal
  deployment owner's authorization. Pure inspection and disposable tests do
  not authorize an update of an incumbent.
- Komodo remains the sole writer for selected workload Stacks. The independent
  substrate installer never becomes a general workload Docker executor.
  Connect the existing role projection to its selected workload adapter only
  after current manager readiness and its own exact-content admission.

## Plan

- [x] Review this source appointment and lifecycle contract (#8872).
- [x] Implement strict declaration/resolution, fixed native substrate owner,
  operation custody and installed-package entry seam, with focused tests.
- [ ] Bind the actual adopter install/update composition and versioned desired
  configuration; retire the manual-only dependency without a parallel writer.
- [ ] Qualify fresh deployment, preserving adoption, unchanged repeat, update,
  candidate failure and Core-unavailable recovery through the real owner.
- [ ] Publish operating docs and exact evidence; resolve remaining source/
  rollout gates through their existing owners. Keep the parent campaign open
  until its independent goals are actually met.

## Validation Plan

- [x] Unit tests cover strict input, immutable images, malformed/secret-shaped
  declarations, exact instance/host/storage binding, foreign resources,
  credential drift, supported database transitions and no raw command surface.
- [x] Component tests use actual contained subprocesses and existing journals
  for exact repeat, competing owners, crash before/after mutation, lost replies,
  cleanup uncertainty and restart reconciliation without duplicate effects.
- [x] Installed-package tests execute the public owner seam outside its source
  tree. Default client/planner commands perform no Docker or credential effects.
- [ ] A fresh isolated Linux host/project with official immutable images reaches
  authenticated Core and native Periphery readiness without a web/manual step.
- [ ] A separately bootstrapped installation is explicitly adopted with the
  same native keys, database, credential/storage identities and workload bindings.
  Unchanged repeat performs no unintended native write.
- [ ] A selected update passes current health; an unhealthy candidate retains
  supported recovery and never claims database rollback from saved code alone.
- [ ] With Core stopped/unreachable, independent install/update restores the
  exact owned substrate and only then performs authenticated health checks.
- [ ] Real adopter desired state reproduces the selected deployment through
  Fleet-manager; missing prerequisites and drift stay visible. Preserve every
  incumbent service and separately owned workload.

## Progress

The bounded source owner merged in #9274 as
`d7edf4a7a04c1c4a62c82ccec73b741c8bae74ad`. The host-toolchain contract
supports stable Compose 5 alongside Compose 2 without installing or replacing
a host plugin. Its contained native/owner packet passes 164 cases, including
every owner scenario with both Compose 2.39.4 and 5.1.4 version responses,
plus early refusal of unsupported versions. These are native-protocol fixtures,
not actual Compose or official-image deployment qualification.
Stable 5.x semantic-version syntax requires numeric components without leading
zeroes and nonempty build identifiers.
All 54 native boundary cases pass, including the
malformed examples and valid zero/build-identifier controls.
Actual installed adopter composition and official-image
acceptance remain the following delivery gates, not consequences of this merge.

The reviewed source appointment landed in #8872. The
[explicit host SDK owner](../../../fleet-manager/docs/komodo-owner.md) implements
the bounded declaration, native effect boundary, same-journal admission,
status/withdrawal and installed-package entry seam. Component qualification
uses synthetic native subprocess and authenticated HTTP fixtures; it does not
qualify official images or activate the ordinary installed controller.
The merged source's complete contained Fleet qualification passed 3184 tests with nine explicit
platform/prerequisite skips and seven passing subtests, partitioned exhaustively
into 139 Komodo cases and their 3045-case complement under the existing runner's
admission, sandbox and process owner. The required no-actor unit packet passes
all 2443 admitted cases without skips. Review corrections include mixed-update
failure settlement and health-blocking missing-key status; retained mixed native
state never authorizes credentialed probes. The native client is private and
inert, each native mutation revalidates current selection/admission, and status
retains the journal's physical-resource uncertainty even without attempt metadata.
Shared post-health observations reject stops, restarts or key drift before
publishing or reusing success; original key evidence survives uncertain retries.
These are source/component qualifications.
Actual adopter construction, native official-image acceptance and production
delivery remain open; no deployment or incumbent adoption is claimed here.

## Journal

### 2026-10-27 - Bounded source owner and Compose compatibility

- #9274 merged after current-head Copilot assessment with no open findings and
  required green checks. A failed public package-metadata transport check passed
  on one normal failed-job retry; no dependency-age or review gate was bypassed.
- The Compose compatibility review identified malformed version acceptance.
  The correction requires numeric components without leading zeroes and
  nonempty dot-separated build identifiers; the 54-case native boundary packet
  includes the reported malformed examples and valid controls.
- The follow-up documentation review identified chronology in Progress.
  Review history is retained here; current support and qualification boundaries
  remain in Progress. Actual adopter and official-image acceptance stay open.
