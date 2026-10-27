# Consolidated Core Runtime Bundle

- **Slug:** `runtime-bundle`
- **Repo:** copilot-extensions
- **Branch(es):** independent, bounded per-slice PRs
- **Created:** 2026-10-26
- **Status:** Active
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
The existing public artifact publisher produces first-party artifacts; that
implementation boundary is not a legal prohibition on third-party bundling.
Aggregate bundles may include third-party components when their actual licenses
and existing source/distribution authority permit it, preserving required
notices, matching source access and replacement rights where applicable.
Nothing here authorizes republishing a qualified private-source exception or
treating a public download/SPDX label as redistribution approval. Keep this to
actual obligations, not a separate licensing approval framework.

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

Comparison request (verbatim):
> btw - can we test a build install vs a wheel install of one of these plugins / extensions? 9159 seems approved also. Do we need a wheel per OS and arch type - aka this should work for linux, mac, windows etc

## Plan

### Slice A - Reviewed composition contract
- [x] Clear review of this proposal before changing production composition: #9159 merged on 2026-10-26 as `f77a7c075b` after current-head approval and required green CI.
- [ ] Derive core roots from canonical inventories, retaining selected extras,
  target/Python conditions and the separation of optional heavy environments.
- [ ] Define the minimal core composition/selection binding using existing
  first-party manifests and exact-profile schemas. A candidate is not approval.
- [ ] Record one explicit initial native target; refuse unsupported sources,
  conflicting distribution identities or incomplete closure rather than flattening
  them. Preserve every selected dependency and its admitted origin.

### Slice B - First isolated artifact/CLI proof
- [x] Prefer authenticated published first-party wheels; use the shared
  builder/toolchain when a build is needed, producing one wheel per selected
  canonical distribution. Do not resolve runtime dependencies during repeated
  per-package builds or select bytes by name/version alone.
- [x] Compare one real plugin's source-build and verified-wheel installations
  using the same source/version, interpreter and exact runtime dependency
  closure in fresh roots. Record cold/warm conditions and installed CLI health;
  distinguish first-party build savings from runtime dependency acquisition.
- [x] Establish the initial disposable target's exact permitted closure through existing release/profile/feed
  owners, or the same installation authority's independently verified source
  mode and explicit exact installation lock. Never invent an approval field.
- [x] Retain complete byte inspection and input custody through the existing
  numbered constructor and installed health, using a fresh disposable root.
- [x] Run a real selected CLI with isolated Python imports and compare installed
  module/distribution identity with the selected inputs.
- [x] Record actual source, artifact, target, closure and native constructor
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
- [ ] Read actual wheel tags: reuse universal first-party wheels where valid,
  while qualifying native dependencies per OS/architecture/Python target.
  Packaging compatibility is not functional cross-platform acceptance.
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

### 2026-10-26 - Reviewed proposal and first authenticated member
- Proposal #9159 merged after approval `5660349849`, required green CI and no
  unresolved feedback. Implementation remains bounded by this Plan/Validation.
- Reused the existing source-index, attestation and bundle validators to
  authenticate the released Worktrees artifact set from development source
  `42134b2b95042b842462366c38365afce66f0912`, bound through protected Main
  `ebca5a60e21b7e9b0ba851c71b9abf64b2e7aaa4`. All sixteen first-party wheel
  members, including the plugin, declare `py3-none-any`.
- This proves artifact identity/bytes, not consumer-profile approval, runtime
  dependency admission, construction or cross-platform execution. No new
  provenance record, acquisition exception, runtime or live setting was created.

### 2026-10-26 - Measured member comparison and complete core candidate inspection
- Four fresh Windows Python 3.11 environments pass with identical final
  twenty-one distribution versions and real installed CLI execution. Worktrees
  plus fifteen library wheels take 9.041s cold / 9.469s warm versus source
  38.044s / 31.185s. First-party installation alone takes 2.794s / 0.720s versus
  31.396s / 23.228s; warm source still builds all sixteen distributions.
- These are one-sample packaging measurements, excluding source export, lock
  preparation and published-artifact acquisition/build. Build tooling differs:
  released wheels record setuptools 84.2.0; the governed source path selects
  84.0.0. Pinning the former on the consumer feed fails the unchanged seven-day
  cutoff. No seasoning exception was used. The existing CLI banner has
  placeholder build fields; installed distribution metadata supplies the actual
  package version, not a runtime attestation.
- Authenticated and source-bound all fifteen published component bundles from
  the same protected source. They cover fifteen of nineteen core roots; Chat,
  Dispatch service, SSH service and Worktree Manager lack root artifacts there.
  Eighteen shared distributions have conflicting wheel identities across those
  component sets. Name/version equality is not permission to choose one.
- On Windows, complete source validation needs native long-path traversal for
  the deeply exported tree. The unprefixed diagnostic omitted deep files and
  produced a false hash mismatch; the same validator passes every manifest
  with native path spelling and unchanged bytes. No hash was weakened.
- The candidate-input owner's existing root selection yields forty-two
  canonical first-party distributions for the initial native target, preserving
  applicable extras and recording legacy source edges explicitly. This is a
  candidate selection, not approval. An offline-only shared-toolchain attempt
  cannot resolve setuptools metadata from the isolated comparison cache.
  Complete core acquisition, exact-lock authority and numbered construction
  remain open; no service, installation policy or default selector changed.

### 2026-10-27 - Complete isolated native core and unchanged-generation reuse
- Built forty-two canonical first-party distributions once through the existing
  shared toolchain, including the registered standalone resource/setup adapters.
  The existing native compiler selects seventy-four exact third-party wheels
  for Windows/Python 3.11. The source experiment uses explicitly admitted
  transport/origin policy and unchanged seasoning, not a production profile.
- Both fresh numbered environments contain the same 116 exact distribution
  versions. Installed Worktrees imports originate inside the selected
  environment; Worktrees, Worktree Manager and dependency checks pass. Existing
  byte inspection and retained-input contexts remain held through constructor
  and installed health. No serving registration or default selector changes.
- Cold resolve/acquire/construct/CLI health takes 248.968s and 241.063s in two
  samples. Governed acquisition dominates at 186.687s / 179.796s; construction is
  32.188s / 32.438s. These totals exclude first-party source export/build and are
  not full-update or service-cutover throughput.
- Same-policy unchanged core reuse takes 4.531s including independent current
  source/profile validation, native policy recapture, real constructor reuse,
  both CLIs and dependency health. Reuse itself is 1.141s. All 116 original
  constructor-snapshot wheel paths are unavailable, and reconstruction is
  instrumented to refuse; no resolver, acquisition or construction executes.
  The completed marker and default selector remain unchanged.
- The integration adds only opt-in recorded-target file matching to the
  existing lock reader and independently selected exact local exclusions to the
  existing feed capture owner. Strict defaults, mandatory SHA-256, tied-file
  refusal and all other constraint/routing refusals remain. Native proof also
  exercises corrected file/directory ACL grants and extended Windows snapshot
  paths with canonical file URIs through their existing shared helpers.
- Validation tiers: failing-first pure controls cover the new admission and
  URI boundaries; actual native closed-wheel and full-core construction supply
  the simple end-to-end lane. Fresh isolated homes/processes exercise first
  key creation and immutable installation, not complete clean-room host
  provisioning. Full admitted packet and available hosted native lanes remain
  publication gates; Linux/macOS, production feed/closure qualification and
  ordinary installer enabling are not inferred from this target.
- An older experiment's receipt refuses policy recapture while its feed
  identity still matches; its changed policy component is not proven because
  that cold run did not retain component witnesses. The newer witnessed
  cold/reuse pair passes without forcing an old identity. Preserve this
  invalidation boundary and qualify the older refusal before ordinary adoption.
  Changed-dependency updates, cache retention, publication and multi-platform
  service migration remain open under the existing owners.
