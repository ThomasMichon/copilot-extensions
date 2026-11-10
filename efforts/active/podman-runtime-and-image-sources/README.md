# Podman Runtime and Image Sources

- **Slug:** `podman-runtime-and-image-sources`
- **Repo:** copilot-extensions
- **Branch(es):** Serial independently reviewed functional slices targeting `dev`
- **Created:** 2026-11-07
- **Status:** Active
- **Vision:** [agent-containers](../../../visions/plugins/agent-containers/README.md#pluggable-container-runtime), `external-runtime-drivers`, `configured-image-sources`
- **Umbrella issue:** #11386

## Guiding Intent

Make Podman a usable trusted-fleet substrate through the existing container
provider, including image acquisition from configured sources. Preserve one
answer for fleet identity, leases, readiness and dispatch, rather than adding a
second allocator or requiring callers to bypass `container:` routing.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Podman driver | Proposal, source implementation and consumer qualification | #11386 and managed source worktree |
| Existing external-driver integrations | Retain their runtime/tool-specific implementation | Their existing issues and PRs |

## Coordination

- **Topology:** One owner, dependency-ordered functional slices.
- **Host (owns PRs):** Podman driver.
- **Delegates:** None.
- **Handoff:** Preserve this canonical effort, remaining gates and existing PR ownership.

The first flow must include the real provider consumer, not merely a standalone
driver command. Independent existing external-driver integrations retain their
ownership; this effort does not replace or retire them. Do not fork the fleet,
lease, resolver, credential or lifecycle owners.

## Context

The version-1 [runtime-driver protocol](../../../plugins/agent-containers/docs/runtime-drivers.md)
already separates runtime operations from provider-owned identity, leases,
readiness, trust and addressing. External drivers own image/workspace setup;
Docker-only fleet keys are rejected instead of silently ignored.

The default runtime remains Docker. A registered external driver can supply
another trusted runtime, but this repository does not yet ship a Podman driver.
The existing protocol already uses Podman-based tools as an example.

Two consumer boundaries must not be hidden by a successful Podman CLI call:
Windows readiness currently refuses `ssh_transport` drivers, and direct-exec
requires the driver to own the credential-relay contract or the fleet to
explicitly disable it. Capability and transport changes therefore require
receiver-first review and actual consumer proof, not an unconditional flag.
Restricted containment remains on the inspected Docker hardening owner.

## Request

Public-safe verbatim source direction:

> Honestly we should just add Podman support to agent-containers, and add support for pulling from pre-configured container package sources when available.

This captures the portable requested behavior. It does not publish private
integration context or make that integration this effort's responsibility.
The realization and sequencing below are agent recommendations, not additional
operator requirements or deployment authority.

## Scope Approvals

The source charter covers investigation, reviewed source changes, bounded
caller-contained validation and normal issue/PR publication. It does not grant
engine installation, Podman machine initialization/start, registry login,
credential inspection/export, image pulls, real venue creation or mutation,
shared service changes, forced ownership or rollout.

Isolated source fixtures may use synthetic credentials and runtime adapters.
Native engine/image/venue tests need separately applicable effect authority and
exact fixture ownership. A reviewed plan or successful source merge is not that
authority.

## Plan

### Phase 1 - Review composition and consumer contract
- [x] Review the existing-driver realization and configured-image standing intent before implementation. (#11388)
- [x] Trace provisioning through the real provider/driver consumer, including relay, policy, selected incarnation and retained-work handling; preserve existing driver integration owners. ([Consumer trace and realization](phase1-contract.md))
- [x] Specify supported POSIX rootless and Windows selected-machine lanes, the selected Podman executable/connection, and explicit unavailable outcomes without automatic init/start/login or Docker fallback. ([Realization](phase1-contract.md); Windows transport remains an open implementation/acceptance requirement)
- [x] Resolve image references from existing composed configuration and the selected runtime's configured sources; define deterministic non-interactive ambiguity, authentication, absence and pull-failure behavior without creating a competing credential or package-source store. ([Realization](phase1-contract.md); proposed semantics, not runtime proof)
- [ ] Name any actual protocol/capability extension and its provider-first compatibility sequence before code; prefer the existing version-1 operations when they suffice. _(Initial Podman driver uses version 1 unchanged; the Windows broker extension must be specified and reviewed before its implementation.)_

### Phase 2 - Deliver the first complete trusted Podman flow
- [ ] Ship Podman support through the existing driver discovery/invocation boundary, preserving Docker default selection and independently registered drivers.
- [x] Implement image-backed provision, stopped/running inventory, exact-incarnation inspect/start/exec/copy-in and return through the existing provider; do not broaden cleanup or discard retained work. _(Source and native lifecycle evidence; release/installed selection remains open.)_
- [ ] Prove configured-source image selection and failure before member publication; retain partial/unknown effects with the existing lifecycle owner instead of replaying creation.
- [x] Connect and prove the real authenticated provider dispatch consumer under an accurately advertised supported transport; do not silently disable required relay or hosting policy. _(Native Linux SSH fixture through the provider; required relay selection remains enabled. Real credential-relay delivery, Bridge and Windows SSH remain independent open gates.)_
- [ ] Publish the first complete supported flow after focused gates and steward it through actual merge; do not promote scaffolding or driver-only success as the functional result.

### Phase 3 - Qualify platform parity and release
- [ ] Prove each claimed POSIX rootless and Windows selected-machine flow through its actual engine/transport lane, or transfer an explicitly unsupported lane to a named tracked objective without claiming parity.
- [ ] Preserve mixed-version refusal, rollback, native argv/exit/timeout behavior and existing consumer contracts for any changed independently deployed boundary.
- [ ] Update authoritative configuration/runtime-driver documentation, focused tests and changefiles; assess deployment/discovery changes against authoritative runtime replacement before changing installer code.
- [ ] Verify actual Main source inclusion and separately authorized selected installed/live consumer acceptance.
- [ ] Resolve every Plan and Validation Plan gate, including named tracked transfers, before marking this effort Done.

## Validation Plan

- [x] Focused driver/parser and configuration tests fail before the corresponding implementation and prove explicit engine/connection selection without Docker fallback. _(Unpublished source controls; native/installed acceptance remains open.)_
- [ ] Configured image resolution covers explicit/available sources, absent/ambiguous references, unavailable authentication and pull failure without prompting, exposing credentials or publishing a ready member.
- [ ] Real bounded child argv/process controls preserve literal arguments, environment ownership, output limits, exit codes, deadlines and caller-contained cleanup on supported host platforms.
- [ ] A first-touch isolated provider consumer proves provision, inventory, readiness, one authenticated dispatch and return with the same selected incarnation; no sibling package import or fake success past the driver boundary.
- [ ] Refusal and interrupted-operation controls preserve active/unknown leases, retained files, foreign members and successor incarnations; stopped members remain observable.
- [ ] Existing Docker and independent external-driver behavior remain unchanged; restricted fleets cannot acquire unqualified Podman containment.
- [ ] Windows selected-machine paths and POSIX rootless paths have native evidence before either is advertised; source-only or mocked controls are labeled as such.
- [ ] Run focused suites, touched lint, install/module/documentation/typing gates applicable to changed code, review and exact-head required CI; no manual version edits.
- [ ] Where provisioning/installer behavior changes, exercise a fresh clean-room consumer when practical and record unavailable-lane reasons; installed/live tests retain separate effect authority.
- [ ] Verify release and selected-consumer evidence independently, and resolve or explicitly transfer every remaining acceptance gate before completion.

## Proposal

Use the existing external-runtime-driver seam for first-party Podman support
instead of changing every Docker-shaped call site or inventing a second fleet
manager. Reuse native configured registry/source behavior and the composed
fleet configuration; no hardcoded feed or new credential cache.

This is the recommended composition, not a decision to treat Docker and Podman
as byte-compatible. The Phase 1 trace must resolve the Windows transport and
credential-relay gaps before implementation advertises capabilities. Any needed
boundary extension gets its own compatible receiver-first change under the
existing service-contract evolution pattern. Do not pre-emptively implement a
second transport or alter restricted containment.

Installer changes, if required for driver discovery, must evaluate
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and all selected consumers define success independently of
incumbent retirement. Preserve state, session keepers, automatic/manual
classification and the then-current generation. This proposal itself changes
no installer or running system.

## Journal

### 2026-11-07 - Proposal

- Deduplicated open Podman issues/PRs and active Podman-specific efforts; #11386 claims this portable source scope.
- Confirmed the version-1 driver seam and its distinct Windows/relay limitations instead of assuming Docker command compatibility proves dispatch.
- Captured the portable request verbatim and marked the realization as agent-recommended. Source implementation waits for review of this proposal.

### 2026-11-07 - Reviewed plan and real consumer trace

- #11388 merged as `e5d8e86dfd9d43fd2e46a8487a671733a372b6e8` after exact-head approval `5815175913` with no findings and green required CI. Only the provider-confirmed missing-CODEOWNER exception was used; no CI bypass.
- Managed synchronization reached the actual merge. Re-read Phase 1 and the complete validation gates before selecting the next source work.
- The [consumer trace](phase1-contract.md) records the existing ownership path and the concrete Windows broker boundary. No runtime, broker, engine, machine, image, credential or venue effect has occurred.

### 2026-11-07 - Unpublished first driver source

- Implemented the bundled version-1 Podman fallback without changing Docker defaults or independent driver ownership. Explicit Podman registration failures do not select a different implementation.
- Image acquisition uses the configured native client/connection, noninteractive pull policy and returned immutable ID. Creation has no replace/replay cleanup; per-member effects select the inspected full native incarnation.
- Literal pre-implementation controls failed for missing driver discovery and its absent module. The combined existing-driver/new-driver packet then passed 112 controls with 10 platform skips; the final focused packet passed 23 with 2 skips before additional pull-failure controls. Lint, install contract, enforced admitted typing, module size and documentation checks passed.
- A harmless real-process fixture crossed provider provisioning, the bundled CLI and a Python engine stand-in, then inventory/exec/copy/stop/start/remove. It retained provider relay selection and refused stale identities. This is not a native engine, authenticated SSH, relay or Bridge qualification.
- The native opt-in recipe has no default image or registry and requires an already-installed POSIX Podman engine plus separately authorized fixture/image effects. That lane is unavailable on the current source-validation host; no installation, image pull, real member, shared service or host update was attempted.
- Windows SSH and streaming remain explicitly unsupported. The concrete receiver-first broker extension still needs review and implementation. Source remains unpublished until the first complete functional consumer gate is resolved; this effort and every remaining native/release/installed gate stay Active.

### 2026-11-08 - Native engine and provider lifecycle slice

- Separately authorized native setup supplied a configured rootless machine and an immutable, credential-free fixture image. The native client and guest engine were recorded independently by their deployment owner; this public source journal contains no private host, connection or artifact identities.
- The opt-in lifecycle fixture reached the real provider, bundled command and native engine for provision, inventory, literal exec, copy-in, stop/start and exact-member cleanup. It verified the selected image has no baked-in host keys. The final native packet passed one lifecycle test with Windows authenticated SSH explicitly skipped.
- The isolated test home needed Podman's supported existing connection-file selection; no credential contents were inspected, copied or exported. A missing-workspace image failure and an insufficient default test-item budget preserved their native members. The fixture owner reconciled each exact incarnation and stopped state before cleanup or a corrected fresh attempt; neither operation was replayed blindly.
- The native multi-process fixture uses a bounded 120-second item budget. Final source controls passed 25 tests with three platform/native skips; touched lint passed. Native lifecycle is not SSH authentication, relay, Session Host, Bridge, released driver selection or Windows parity. The parent functional-publication gate remains open.

### 2026-11-08 - Supported authenticated source consumer

- The native Linux fixture ran the ordinary source-bound test preparer, provider provisioning, bundled driver process and real Podman engine. A fresh fixture key/profile authenticated a command through the driver's SSH ProxyCommand, then stopped/restarted and removed only the observed member incarnation. Required relay selection was not disabled, and the driver did not claim to own it.
- Missing test tooling was restored only in a private, unprivileged test environment. An initially selected tool artifact failed the seven-day age requirement; the canonical registry-only installer prepared a separate seasoned tool environment, and the source test owner forced fresh dependency resolution before the accepted repeat.
- The accepted named authenticated fixture passed in 83.45 seconds; preparation/execution/cleanup took 184.77 seconds. Source and native effects retained their existing process/ownership bounds; this is not a claim that the original default 30-second aggregate item budget passed.
- Native Linux authenticated SSH and Windows client lifecycle now supply the first supported source flow's evidence. No macOS native lane was exercised. Actual credential-relay delivery, Session Host/Bridge, Windows SSH, release and installed-consumer qualification remain open. Publication can now proceed for this bounded initial driver; the whole effort remains Active.
- Self-review found a case-variant malformed manifest could evade the explicit Podman refusal. The new `PODMAN.json` control failed before the correction; discovery now preserves that configuration failure without changing valid manifest/PATH precedence or another driver's selection.
- The final focused packet at default item limits passed 30 controls with four platform/native skips and 93 deliberate deselections. The install-contract check passed; touched lint, documentation consistency and scoped module-size checks passed. All 40 admitted strict typing projects passed for Linux and Windows; this does not promote pending container typing into admitted coverage.
- Publication preparation rebased onto current Dev after an index-only conflict, preserving the other effort owners' new rows. Updated Windows package/binding code prompted fresh native lifecycle preparation: one pass with Windows SSH skipped. The rebased default-limit source packet passed 30 controls with four skips and 93 deselections. Existing admitted typing passed; the newly changed `service-pr` tool environment was prepared and its Linux/Windows checks also passed, completing the current admitted project set. A historical execution count was de-counted rather than changed to imply the current roster.

### 2026-11-08 - Review correction: discovery and fixture selection

- #11554 opened with the initial supported driver and remaining platform/release limits retained. Review found that a case-sensitive `*.json` scan omits `.JSON` Podman filenames, so the refusal predicate never sees them. The native Linux four-filename control produced two failures before the scan correction; all four now refuse. Other drivers retain their existing suffix selection and valid registration precedence.
- A redirected driver directory alone cannot constrain ambient PATH selection. The engine-free process fixture failed with a safe unapproved-driver trap before explicit registration. It and both native fixtures now register the bundled interpreter/module command before provider resolution, preserving the authorized native executable/connection. Parent Docker stubs are not relied upon to constrain another process's effects.
- Corrected source controls passed 32 tests with four platform/native skips and 93 deselections. Native Windows lifecycle passed with the PATH trap; the native Linux authenticated fixture also passed with explicit selection and exact cleanup. These fixes do not qualify Windows SSH, real relay delivery, Bridge, release or installed selection.
- A later review identified that literal suffix matching narrowed Windows discovery for other drivers. The native Windows non-Podman `.JSON` regression failed before correction; standard `fnmatch` now preserves the former platform behavior while Podman's additional case-variant refusal remains. The focused packet passed 33 tests with four skips; the Linux filename/platform selection packet passed five controls. Bundled-driver module registration and CLI reporting share one mapping, and the newly added manifest write explicitly uses UTF-8.
- Directory-level enumeration failures now also block Podman fallback: an explicit registration may exist but be unreadable, so selecting PATH or the bundle would change ownership under uncertainty. The directory-failure control failed before the guard, then passed with no PATH lookup. The final focused packet passed 34 controls with four skips; admitted typing remained green.
- The next review identified that paused members satisfy the protocol's startability contract but require native `unpause`, not `start`. The paused-command and failure/no-replay controls failed before correction. Lifecycle now selects `unpause` only for the inspected paused incarnation; created/exited members retain `start`, and failed effects are not replayed.
- The focused driver/discovery packet passed 38 controls with four platform/native skips and 93 deselections at default item limits. The real provider-to-driver process fixture now exercises paused/resumed inventory. The existing authorized Windows-client/native-engine lifecycle fixture also passed pause/resume, stopped/restarted state and exact cleanup in 45.67 seconds under its 120-second item bound; independent native inventory confirmed no driver fixture remained.
- Touched lint, install contract, documentation consistency and scoped module-size checks passed. These corrections implement the existing version-1 contract, not a new protocol or platform capability; Windows SSH, real relay/Bridge, release and installed-consumer gates remain open.
