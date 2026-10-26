# Consolidated Core Runtime Bundle

- **Slug:** `runtime-bundle`
- **Repo:** copilot-extensions
- **Branch(es):** independent, bounded per-slice PRs
- **Created:** 2026-10-26
- **Status:** Draft
- **Vision:** advances Runtime Assembly's core/optional-environment release lock and one verified acquisition path
- **Composes with:** #6937, #4876, #6234 and #6707; their inventory, artifact, admission, construction and lifecycle owners remain authoritative

## Guiding Intent

Produce a visible consolidation result: one valid core runtime artifact set,
constructed in a disposable environment, with a real installed CLI executing
from it. Keep the existing install/update path serving while proving that result.
Independent construction does not wait for unrelated lifecycle work, but live
activation, migration and supervision retain their actual safety dependencies.

The first milestone is not an approved release lock, production installation or
complete multi-platform bundle. It establishes a narrow, reproducible composition
contract on one explicitly recorded native target before widening the matrix.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Core bundle submitter | Core composition, isolated proof and host-owned review/merge | Managed source lane `spry-hinge` |
| Existing artifact/admission owners | Existing first-party production, exact profiles, feed/source admission and immutable construction | #4876 and its retained efforts |

## Coordination

- **Topology:** bounded per-slice PRs, leaving the default branch green.
- **Host (owns PRs):** Core bundle submitter.
- **Delegates:** none for this initial slice.
- **Boundary:** compose existing owners; do not introduce a second resolver,
  provenance record, approval service, constructor, supervisor or source policy.
- **Handoff:** record reviewed/merged slices and remaining acceptance here.
  An isolated proof never implicitly authorizes rollout.

## Context

The current native constructor freezes one attributed local source graph. A
composition probe selects nineteen core roots but refuses two distinct
`agent-zdd` locations: canonical `libs/zdd` and the Worktrees vendored copy.
That is a real source-identity conflict, not permission to discard a path.
A shared UV workspace is therefore not the core assembly strategy.

The first-party builder already provides a shared locked toolchain and
`build_source_artifacts`; exact-profile wheel inspection and the numbered
constructor already exist. Reuse those seams. Build each selected first-party
distribution once, retain its canonical binding and verify the resulting
closed set before construction.

The candidate release-lock-input work remains a separate, bounded input slice.
It is not approval or a constructor. Its universal source projection currently
cannot represent the exact temporary Manager wheel origin. #7606 explicitly
retains that workaround through its date and native-feed acceptance gates.
Do not remove it, drop a mapping, choose a public replacement or weaken seasoning.
Its Linux marker is false on the initial native Windows target; that observation
is neither a universal-input result nor removal evidence.

This effort composes with the
[governed artifact effort](../governed-python-artifact-promotion/README.md)
and the
[Runtime Assembly vision](../../../visions/plugin-services/runtime-assembly/README.md).
Public distribution remains first-party-only; third-party dependencies come
from the installation's approved sources. Nothing here authorizes redistribution
of a qualified third-party exception through a new public bundle.

## Request

**Public-safe summary of the accepted delivery direction:** make bundle
construction the primary delivery lane, with existing reliability work proceeding
in parallel. The next visible result is a valid core artifact and a real CLI in a
disposable environment, not another generic setup subsystem.

Operator confirmation:
> okay - so let's do that?

The initial single-target milestone and reuse-first implementation sequence
below are the agent-recommended realization of that accepted direction.
They do not change the final cross-platform goal or any live-change authority.

## Plan

### Slice A - Reviewed composition contract
- [ ] Clear review of this proposal before changing production composition.
- [ ] Derive core roots from canonical inventories, retaining selected extras,
  target/Python conditions and the separation of optional heavy environments.
- [ ] Define the minimal core composition/selection binding using existing
  first-party manifests and exact-profile schemas. A candidate is not approval.
- [ ] Record one explicit initial native target; refuse unsupported sources,
  conflicting distribution identities or incomplete closure rather than flattening
  them. Preserve every selected dependency and its admitted origin.

### Slice B - First isolated artifact/CLI proof
- [ ] Use the shared builder/toolchain to produce one first-party wheel per
  selected canonical distribution; do not resolve runtime dependencies during
  repeated per-package builds or select bytes by name/version alone.
- [ ] Establish the exact permitted closure through existing release/profile/feed
  owners, or the same installation authority's independently verified source
  mode and explicit exact installation lock. Never invent an approval field.
- [ ] Retain complete byte inspection and input custody through the existing
  numbered constructor and installed health, using a fresh disposable root.
- [ ] Run a real selected CLI with isolated Python imports and compare installed
  module/distribution identity with the selected inputs.
- [ ] Record actual source, artifact, target, closure and native constructor
  identities, phase timings and explicit unavailable lanes. No serving
  registration or default-runtime pointer changes.

### Later slices - Explicitly retained, not completed by the first proof
- [ ] Expand core plus optional heavy environments and the required platform/ABI
  matrix through the same admission contract.
- [ ] Integrate install/update/no-op/rollback with the existing governor and
  lifecycle owners; prove reuse rather than rebuilding an unchanged closure.
- [ ] Publish through the existing artifact/promotion path only after its
  authority, provenance, feed and distribution rules are satisfied.
- [ ] Adopt services one at a time only after the actual serialization,
  generation, cutover and installed-state gates are met.

## Validation Plan

- [ ] Negative controls refuse duplicate/conflicting first-party source bindings,
  changed wheel bytes, missing dependencies and unadmitted origins.
- [ ] Preserve target markers, extras and Python ranges; the initial target's
  success must not qualify another target or the optional heavy environments.
- [ ] Verify first-party build reuse and package constraints against the actual
  wheel metadata, not a copied roster or synthetic success record.
- [ ] Exercise the complete retained-input -> numbered construction -> installed
  import/CLI seam, with originals unavailable as a control where feasible.
- [ ] Prove no live runtime, service registration, auth setting or default
  selector changed; faults leave the existing installation untouched.
- [ ] Record each validation tier actually executed and reasons for unavailable
  native lanes. Required CI/review stay nonwaivable.

## Proposal

This is below the approved Runtime Assembly vision's altitude: a core composition
binding and proof, not another acquisition authority. The first implementation
must identify the smallest missing seam after reusing existing builder,
profile/admission and constructor APIs. Source-tree conflicts are evidence to
choose the wheel path, not a reason to relax source identity.

## Journal

### 2026-10-26 - Reviewed-intent boundary
- Existing inventory and constructor research established the source-identity
  conflict and the wheel-first direction. No production composer or runtime
  change is implemented by this proposal.
- Workaround removal remains with #7606. The isolated first milestone neither
  removes it early nor claims native affected-feed qualification.
