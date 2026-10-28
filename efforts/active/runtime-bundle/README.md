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

### Slice C - Consolidated first-party release binding
- [ ] Derive the canonical core project closure from the existing inventories
  and selected root/extras declarations. Preserve target conditions and source
  indirections as data; do not resolve third-party dependencies or remove the
  qualified-origin workaround. Share this selection with the existing input
  owner rather than maintaining another roster.
- [ ] Extend the existing Python artifact manifest with a registered core
  composition, using the marketplace's existing version authority. Build each
  canonical first-party distribution once through the common locked builder;
  include the existing standalone build resources and source admission.
  Composition selection participates in the existing content identity.
- [ ] Re-derive and verify the complete composition against candidate source
  through the existing promotion validator, then carry it in the existing
  artifact index and immutable first-party publisher. Keep signing and source
  admission with their current owners. No complete-environment archive, dummy
  metapackage, second resolver or parallel provenance channel.
- [ ] Prove the emitted first-party artifact and release-profile binding with
  real native inputs and failing-first identity/closure/resource controls.
  Record absent native lanes honestly. The first-party artifact does not
  publish third-party runtime bytes or enable ordinary installations.

### Later slices - Explicitly retained, not completed by the first proof
- [x] Retain an independently selected aggregate transport through the existing
  wheel snapshot owner, then exercise the same inspected-profile and numbered
  constructor/reuse path with original transport inputs unavailable. #9319
  merged as `89e9529c865dd21dbc57baefc42f4a9a21b9b1ef`; this completes custody,
  not ordinary selection or publication.
- [x] Replace experiment-specific wheel transport assembly with the repeatable
  exact-profile producer in #9413. Its candidate bytes do not establish profile
  approval, complete source-material distribution or publication authority.
  #9417 merged as `326a72d1332afe351e7f5e8ac0841ecba56a8230`.
- [x] Produce and read back a reproducible candidate delivery envelope binding
  the exact profile, its existing wheel transport and explicitly selected
  accompanying source/notice materials. Bind each material to affected wheel
  digests and preserve its bytes; selection/qualification of required materials
  remains with the caller. Reuse profile and transport identities rather than
  creating release approval, signing, feed authority or a second constructor.
  Keep the public first-party publisher unchanged; this slice neither chooses
  an aggregate channel nor uploads third-party content. #9424 merged as
  `832f0e66065582d55b152b7dd0fa9f994bc173fe`.
- [ ] Expand core plus optional heavy environments and the required platform/ABI
  matrix through the same admission contract.
- [ ] Integrate install/update/no-op/rollback with the existing governor and
  lifecycle owners; prove reuse rather than rebuilding an unchanged closure.
- [ ] Publish through the existing artifact/promotion path only after its
  authority, provenance, feed and distribution rules are satisfied.
- [ ] Adopt services one at a time only after the actual serialization,
  generation, cutover and installed-state gates are met.

## Validation Plan

- [x] Candidate delivery controls reject changed/omitted/unexpected material
  bytes, unrelated wheel bindings, unsafe paths, incomplete wheel transports,
  exhausted budgets and output replacement. Real readback must retain the
  selected materials and reach the existing wheel consumer without the original
  inputs. Record unavailable release/native lanes without claiming qualification.
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

### 2026-10-28 - Complete candidate delivery producer
- Coordinated a non-overlapping packaging slice with #4876. The existing
  toolkit and one-service consumer owners retain their scope; the public
  first-party publisher is unchanged.
- Candidate delivery now binds the canonical selected profile, the existing
  wheel transport and explicitly selected source/notice files. Material records
  bind affected wheel digests, exact bytes and kind, without creating approval,
  licensing clearance or feed/publication authority.
- Two API builds and the actual command emit the same 38,934,900-byte envelope
  for the 116-wheel native candidate and all eight accompanying files. Reversed
  material selection is byte-identical. The original wheel consumer and material
  readback succeed with copied inputs absent; original qualified files are
  unchanged. This is packaging evidence, not release or live-update acceptance.
- Review of #9424 reproduced a compressed-manifest budget gap: two controls
  show 8,194 expanded bytes reaching JSON decoding under a 4,096-byte archive
  or snapshot budget. Manifest and aggregate expanded-size checks now precede
  decoding. The real complete candidate remains byte-identical.
- Focused delivery/transport/release controls pass 105 cases with one
  platform-specific skip. The PR documents its install-contract/effort impact;
  #9424 merged as `832f0e66065582d55b152b7dd0fa9f994bc173fe` after exact-head
  approval, successful actual CI and all feedback resolved. The parent stays
  Active for the remaining release, consumer and lifecycle gates.

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

### 2026-10-27 - Aggregate transport custody adapter
- The below-altitude adapter advances Runtime Assembly's
  `one-verified-acquisition-path` without another resolver, provenance record,
  approval service or constructor. A caller-selected ZIP must contain exactly
  the selected wheels, pass its independent aggregate digest, and preserve all
  member hashes under explicit inclusive budgets and one absolute deadline.
- The result is the existing `WheelSnapshot`; the normal inspected-profile
  adapter and numbered constructor remain unchanged. Actual native construction
  and reuse cover both loose files and a ZIP with originals unavailable.
- This is byte custody, not acquisition policy, license/source-material
  qualification, release publication, ordinary installer enabling or service
  migration. Those retained parent gates remain open.

### 2026-10-28 - Repeatable selected-profile transport producer
- #9413 implements the producer matching the existing aggregate consumer:
  profile validation, original snapshot custody, complete wheel/metadata
  inspection, deterministic stored ZIP members, canonical consumer readback and
  atomic non-replacing output. There is no resolver, download, admission receipt,
  runtime installation, publisher or channel update.
- Focused producer and existing snapshot controls pass 58 cases on native
  Windows, including actual CLI invocation, output races, corrupt candidates,
  source independence, inclusive aggregate storage and failure cleanup. A
  coupled long-path fixture now creates its input using native spelling rather
  than depending on the host's LongPathsEnabled setting.
- The real selected 116-wheel profile produces byte-identical transports from
  two function calls and the actual CLI. Original transport removal precedes
  canonical readback; existing source-material assets remain unchanged. This
  qualifies the producer/consumer byte seam, not a new installed run or complete
  distribution envelope. Native Linux/macOS and pipeline activation remain
  unqualified. The parent stays Active.
- The first review exposed two real transaction defects. Five failing-first
  controls measure header/closing-directory storage overshoot and output left
  visible after retained-input exit verification fails. All ZIP writes are now
  pre-bounded, and publication follows successful retained-input exit plus a
  final private-candidate check. The real selected profile and CLI still produce
  the identical transport bytes after these corrections.
- #9417 merged on 2026-10-28 after approval of the corrected head, successful
  required CI and resolution of both findings. Only this producer source slice
  is complete. Enclosing release/material bindings, production publication and
  ordinary consumer integration remain the next retained work; no rollout or
  complete-distribution claim follows from the merge.

### 2026-10-28 - Consolidated core promotion candidate
- The existing builder, manifest/index, source verifier and immutable
  publisher now have an explicit `core` composition candidate. Its version
  comes from marketplace metadata, not a second version clock or metapackage.
  Canonical project discovery and runtime-root/extras selection share
  `python_project_inventory`; the pending lock-input owner must reuse that
  selector before it lands.
- Native Windows/CPython 3.11 builds 42 canonical first-party wheels with one
  governed toolchain and independently verifies the complete source/resource
  closure through the ordinary promotion verifier. The API archive is
  10,563,113 bytes, SHA-256
  `8eef8e788b543a3b7f3056f6ca2d3da413cbdf2afcb5a7c691b4d9a7e5dc2f9c`.
  The measured 398.172 seconds includes toolchain resolution, wheel builds
  and independent readback, not source export, installation or service cutover.
- The actual CLI rebuild uses the same source, composition and retained
  toolchain. All 42 wheels have identical member payloads; their ZIP timestamps
  differ, so rebuilt wheel/archive content identities are not claimed equal.
  The existing identity correctly includes the actual wheel bytes.
- Focused controls cover complete release-profile binding, omitted shared
  libraries, source/resource/version/selection changes, duplicate canonical
  identities, malformed data, selected extras and optional-environment
  boundaries. The omitted-library control fails with legacy optional-library
  behavior restored and passes with complete-core binding. Existing legacy
  artifact, publication and reuse contracts remain covered.
- Validation tiers: unit controls and real API/CLI builds plus source-bound
  archive readback exercise the simple end-to-end lane. Fresh exported source
  and a new locked build environment cover first-touch artifact construction,
  not clean-room host provisioning. Native Linux/macOS, public promotion
  publication, full third-party release qualification and ordinary updater
  adoption remain open. No aggregate channel is selected or updated, no
  third-party bytes are uploaded, and no service/default runtime changes.
- After rebasing onto current development source, the exact candidate's native
  API build and independent source binding pass again: 42 wheels,
  10,563,113 bytes, 372.937 seconds, archive SHA-256
  `7f04619645f1a5ea7e197ca93ce3fbd53096b32fcad3c8102462e18f86c9ca8e`.
- Review found two portability defects: an eager core import imposed ambient
  `packaging` on legacy help/builds, and default specifier membership depended
  on the parser's prerelease defaults. Five controls fail before correction
  and pass afterward. Core CLI selection now runs in the resolved toolchain
  and explicitly admits compatible development versions without weakening
  version floors. A genuinely package-free caller passes legacy help and the
  real 42-wheel core CLI; independent source-bound readback passes in 246.422
  seconds with the retained toolchain. This rebuild has archive SHA-256
  `efc450c3b78da2fea085aede68506476e444658ce72c333fdf3d7336e4f8b1b9`;
  member payloads match the API build, while ZIP timestamps remain distinct.
- Eighteen bounded core controls join the existing exact-input unit packet,
  with only changed/new source bindings and the two new helper coverage targets.
  The existing five-minute budget and effect/coverage rules are unchanged.
  The native Windows full-packet attempt stops at an unchanged long-path
  source-publication fixture after 1,743 passing cases and eight platform
  skips; it is not passing packet evidence. Hosted Linux CI remains required.
- A second review catches omitted-selector interpreter drift during CLI
  re-entry. The new control reproduces uv's default differing from the calling
  interpreter; re-entry now forwards the interpreter already recorded in the
  lock. Explicit selection remains unchanged. Both controls and the focused
  CLI/core decisions pass 20 cases after the correction.
- Final selection review reproduces two previously missed cases: a local
  dependency absent from the canonical inventory was treated as external, and
  equivalent underscore/mixed-case extra spellings were not normalized.
  Selection now refuses any direct or conditional local path mapping without a
  canonical source and canonicalizes queued extras. Seven controls cover both
  failures and retain external wheel URL mappings as data; all 25 core packet
  cases pass. Four controls fail before the corrections. The qualified packet
  gains those seven nodes and only the two affected source digests.
- Independent validation with the exact pinned `packaging==25.0` verifies all
  42 native wheel records and the unchanged selected payload identity in
  32.391 seconds. The parser tooling used the existing governed source route;
  neither live runtime state nor dependency source authority changed.
- The new missing-canonical refusal also exposed Manager's existing qualified
  local `dbus_next` wheel exception. Two controls using its actual manifest and
  wheel bytes reproduce the regression; the closure now defers that exact
  decision to `_bundled_dbus_next`, with contained paths. Altered bytes,
  distribution, project and filename remain refused. No general local-wheel
  exemption or third-party artifact inclusion is added. Two bounded owner
  delegation controls join the packet (27 core cases total); the combined
  core/profile controls pass 100 cases with one platform skip. Real repository
  closure selection again produces the same 42 canonical projects and payload
  identity, including on the Windows host where the dependency's Linux marker
  is false.
- Review's final portability assessment identifies an undefined Python 3.10
  child path: the locked toolchain intentionally does not carry a TOML
  backport. The new core construction command now explicitly requires a
  Python 3.11+ locked build interpreter, checking that interpreter's marker
  environment before selection or re-entry. Two failing-first controls cover
  caller re-entry and already-locked invocation. This does not change legacy
  builders, emitted wheel Python ranges, installer target qualification or
  the shared toolchain's package set; broader native acceptance remains open.
