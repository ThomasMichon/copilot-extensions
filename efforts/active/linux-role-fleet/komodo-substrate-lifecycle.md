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

### Original-attempt SDK repair amendment

Tracked by #9735 under the existing #8860 appointment and #6030 campaign.
This closes `bounded-desired-state-control` and the original-attempt recovery
requirements above; the version-skew boundary follows
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
The operator selected design, review and implementation of focused SDK fixes
and same-original recovery. The following mechanisms are **agent-recommended**
and remain subject to this plan's review before runtime edits.

#### Identity and native-observation corrections

- Compare documented PEM SubjectPublicKeyInfo and base64-DER public-key
  representations by their validated key payload, not raw presentation text.
  Malformed, missing, private-key-shaped or different key material must refuse.
  Preserve existing retained file/key fingerprints and secret custody; health
  normalization must not silently migrate stored identity evidence.
- Accept an empty per-server key override only for a currently authenticated
  connection whose live key matches the retained selected key and whose native
  fixed configuration proves the selected global trust. A populated override
  must match too. Preserve current Core/Periphery versions, selected server,
  connection state, Docker connection and post-health native/process checks.
  Never change enrollment, keys, trust settings or workloads to pass health.
- Treat Docker mount array ordering as incidental for paired native
  observations. Preserve every mount field, multiplicity and destination;
  duplicate/ambiguous mounts refuse. Container ID, image, configuration,
  storage/read-write bindings, network and process identity remain exact.
- Any new persisted native-fingerprint form must have an explicit format
  distinction. Retain legacy evidence and support exact legacy comparisons;
  never treat a historical opaque digest as a canonical mount-set proof.
  If semantic equivalence cannot be established from retained evidence, refuse
  explicitly rather than refreshing a baseline from the current resource.
  Paired-read normalization alone must not silently reinterpret old receipts.

#### Original candidate and current repair executor

- Add an opt-in installed SDK repair contract, separately discoverable from
  the existing `fleet-manager.komodo-substrate-owner.v1` baseline. Keep ordinary
  convergence, its strict current-deployment binding and existing Gateway
  routes unchanged. Older SDKs retain baseline behavior; unsupported repair
  refuses before effects, with no manual-installer or weaker-grant fallback.
- A code-registered current repair resolver authenticates two distinct subjects:
  the retained original candidate selection and the current released executor.
  The exact original request/deployment/generation remains immutable. The
  executor's newer release/runtime identity is separately retained, never
  substituted into that request or reported as the installed candidate.
  Configuration cannot select a callback, arbitrary module or historical code.
- Require a current exact-original-request permit and an explicit current
  repair-only transition appointment from the existing authority. Bind that
  appointment to the original request digest, original candidate, selected
  executor and same physical resource. Renew it through its owner at each
  admission/publication checkpoint; a self-supplied DTO or compatibility list
  is not authorization. The transition permits verification and journal
  settlement only, not convergence or another native apply.
- Resolve both subjects from existing configuration/release/runtime/host/
  credential/storage owners. Revalidate finite currentness, actor/account,
  fleet, installation, target and generation. The selected declaration,
  profile, native tooling, material paths/device/inodes, credentials,
  compatibility, adoption and physical owner must remain the original
  candidate's. Only an explicitly authenticated executor-code repair may
  differ. Do not execute an old runtime or label new code with its digest.
- Enter the original physical lock and same NodeJournal. Require the exact
  retained original UNKNOWN receipt, original profile and settled whole-process
  attempt evidence. Missing/corrupt evidence, another request, unresolved
  process custody, foreign replacement or changed material refuses. Never
  create another journal, claim, operation ID or generation, clear uncertainty,
  or invoke apply/stop/remove/enrollment from this repair path.
- Inspect using the retained original fixed profile, images, public keys,
  network and compatibility decisions. Repeat admission, native/process and
  material checks across current authenticated health and final publication.
  Publish the original succeeded receipt only when complete current readiness
  is established; supported explicit failed settlement retains the actual
  failed candidate and data-compatibility decision without rollback.
- Retain original history and executor/transition provenance in additive
  outcome evidence. Interrupted publication must resume the same original
  settlement idempotently or preserve UNKNOWN; no success-shaped fallback.
  Status must distinguish the original installed candidate, current desired
  selection and verifier release. Repair does not publish a new deployment
  as installed or automatically authorize a later update.

#### Repair plan and validation obligations

- [x] Review this amendment before identity, native-observation or repair edits
  (#9738).
- [x] Implement the focused corrections and installed, code-registered
  original-attempt repair seam; preserve normal convergence and prior readers.
- [x] Test PEM/DER equivalence, populated/global trust, malformed/private/
  different keys, unproved configuration and unsuccessful connection controls.
- [x] Test mount-order equality and actual field/multiplicity/config/process
  changes; test versioned new fingerprints, exact legacy readback and explicit
  refusal when legacy semantic equivalence is unprovable.
- [x] Exercise the actual same-journal owner with an older retained UNKNOWN
  candidate and newer authenticated executor: exact original settlement,
  no native write, current request/transition admission and distinct outcome
  identities. Include wrong request/resource/generation/release/material,
  expired/revoked appointments, unsettled process and changed native controls.
- [x] Interrupt before/after health and each publication boundary, then
  restart through the installed repair seam. Preserve original custody and
  show no apply replay, double terminal receipt or lost original evidence.
- [x] Exercise old caller/new SDK, new repair caller/unsupported SDK and
  supported provider rollback; discover support from the executing immutable
  SDK rather than a newer payload or package version alone.
- [ ] Publish current operating/support docs and qualify an authorized actual
  original attempt after normal release/acquisition. Synthetic and installed
  fixture tests do not close official-image/adopter acceptance or authorize
  production changes. Continue the existing parent Plan and Validation Plan.

### Substrate delivery

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

### 2026-10-31 - Original-attempt repair SDK source merged

- #9752 merged as `6512fce29329be80c93ed6c62343d63bf16aee37` after the
  actual current-head Copilot review reported no open findings and required
  CI passed. Managed completion used the provider-confirmed Maintainer
  exception to an additional CODEOWNER vote; no required check or negative
  feedback was bypassed. Canonical post-merge reconciliation preserved the
  pre-completion source history.
- Re-read both this sub-plan and the parent Plan and Validation Plan.
  The focused source and contained/installed-SDK obligations above close.
  Operating/support documentation is merged, but normal release/acquisition
  and qualification of an authorized actual original attempt remain open.
  No official-image or adopter acceptance item closes, and #9735/#8860/#6030
  retain their existing ownership.
- Validated public-key payloads preserve raw retained receipts; explicitly
  versioned mount evidence ignores only mount-array order while legacy hashes
  remain exact. The code-registered existing authority separately admits the
  original candidate/request and finite current executor/runtime transition.
  Interrupted settlement retains original immutable history and resumes the
  same decision without native replay or a second terminal receipt.
- Review-driven controls cover immutable/material and current-authority drift
  inside publication, complete retained bridge/attachment custody, conflicting
  pending metadata and late cleanup changes. Same-profile replacement-container
  controls first reproduced credentialed login before eventual refusal in
  pending success/failure and successful-terminal retries. The shared evidence
  path now checks retained native fingerprints before those health requests;
  failed-terminal retries retain their no-health behavior.
- The final rebuilt affected SDK qualification passes 211 cases across two
  sequential bounded packets: 197 existing cases and fourteen late-publication/
  pending-container/terminal-container controls. Real noneditable mixed-version
  and fresh-process interruption/restart controls are included. The separate
  ordinary-owner qualification remains 152 cases; repair-only follow-ups did
  not change that owner. An earlier over-combined development packet reached
  its aggregate limit and is not counted as a pass. Product and native-command
  deadlines were not extended.
- Next obtain the normal promoted release and authenticated closed artifact,
  then qualify an existing-authority embedding's actual current executor and
  exact-original repair transition. Never patch or execute historical code,
  relabel new code with an old digest, replay convergence, replace keys or
  clear the original journal. Source/component evidence is not production
  rollout, official-image readiness or completion of the broader fleet.

### 2026-10-30 - Original-attempt repair contract reviewed

- #9738 merged as `b6937bd5441ee5896ae539487269f4af94603594` after
  current-head Copilot approval, no inline findings and successful required
  checks. Normal source reconciliation retained the pre-completion history.
- Re-read the parent Plan and Validation Plan. Only amendment review closes;
  identity/native corrections, the installed repair seam, current transition
  authority, interrupted settlement and actual original-attempt acceptance
  remain open under #9735 and the existing substrate owners.
- Next implement and qualify the focused source corrections and the
  reconcile-only original-candidate/current-executor separation. No runtime
  was patched, native effect replayed, journal cleared or deployment accepted.

### 2026-10-27 - Official-image startup contract correction

- #9332 tracks the exact-tag startup findings. MongoDB 8.0.32 drops to its
  database UID before reading file credentials; the fixed profile must not
  make host-owner-private credential files broadly readable. Its fixed Bash
  handoff reads only the two selected mounts before re-entering the unchanged
  vendor entrypoint, preserving normal initialization and privilege dropping.
- Komodo 2.3.3 Periphery defaults its native key below `root_directory`.
  The profile explicitly selects the retained shared key path instead.
  The tag's singular environment aliases remain supported and unchanged.
- The contained profile/native/owner packet passes 175 cases, including real
  Bash handoff of literal credential bytes, missing-file refusal, Compose
  interpolation and native observation. These are source/protocol controls,
  not official-image startup, adoption or installed-adopter proof.
- The remaining Komodo declaration/health/Gateway/installed-SDK packet passes
  47 cases. The complementary Fleet packet passes 3,044 cases with nine
  explicit prerequisite skips and seven passing subtests. The selected engine
  fixture initially retained an older SDK; normal source preparation restored
  that test dependency and its exact-source comparison then passed.
- The Plan and Validation Plan remain open for actual adopter construction,
  immutable official images, native health, lifecycle and recovery. No
  production resource, credential, service or permission changed in this slice.

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
