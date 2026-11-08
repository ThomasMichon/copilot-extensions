# Podman Runtime and Image Sources

- **Slug:** `podman-runtime-and-image-sources`
- **Repo:** copilot-extensions
- **Branch(es):** Serial independently reviewed functional slices targeting `dev`
- **Created:** 2026-11-07
- **Status:** Draft
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
- [ ] Review the existing-driver realization and configured-image standing intent before implementation.
- [ ] Trace provisioning through the real provider/driver consumer, including relay, policy, selected incarnation and retained-work handling; preserve existing driver integration owners.
- [ ] Specify supported POSIX rootless and Windows selected-machine lanes, the selected Podman executable/connection, and explicit unavailable outcomes without automatic init/start/login or Docker fallback.
- [ ] Resolve image references from existing composed configuration and the selected runtime's configured sources; define deterministic non-interactive ambiguity, authentication, absence and pull-failure behavior without creating a competing credential or package-source store.
- [ ] Name any actual protocol/capability extension and its provider-first compatibility sequence before code; prefer the existing version-1 operations when they suffice.

### Phase 2 - Deliver the first complete trusted Podman flow
- [ ] Ship Podman support through the existing driver discovery/invocation boundary, preserving Docker default selection and independently registered drivers.
- [ ] Implement image-backed provision, stopped/running inventory, exact-incarnation inspect/start/exec/copy-in and return through the existing provider; do not broaden cleanup or discard retained work.
- [ ] Prove configured-source image selection and failure before member publication; retain partial/unknown effects with the existing lifecycle owner instead of replaying creation.
- [ ] Connect and prove the real authenticated provider dispatch consumer under an accurately advertised supported transport; do not silently disable required relay or hosting policy.
- [ ] Publish the first complete supported flow after focused gates and steward it through actual merge; do not promote scaffolding or driver-only success as the functional result.

### Phase 3 - Qualify platform parity and release
- [ ] Prove each claimed POSIX rootless and Windows selected-machine flow through its actual engine/transport lane, or transfer an explicitly unsupported lane to a named tracked objective without claiming parity.
- [ ] Preserve mixed-version refusal, rollback, native argv/exit/timeout behavior and existing consumer contracts for any changed independently deployed boundary.
- [ ] Update authoritative configuration/runtime-driver documentation, focused tests and changefiles; assess deployment/discovery changes against authoritative runtime replacement before changing installer code.
- [ ] Verify actual Main source inclusion and separately authorized selected installed/live consumer acceptance.
- [ ] Resolve every Plan and Validation Plan gate, including named tracked transfers, before marking this effort Done.

## Validation Plan

- [ ] Focused driver/parser and configuration tests fail before the corresponding implementation and prove explicit engine/connection selection without Docker fallback.
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
