# First-party source versions and dependency-first observer acceptance

Parent: [PR Review Workspaces](README.md), coordinated under #9535.
**Agent-recommended source plan, proposed before policy implementation.**
The existing #11084 observer implementation and its open dependency-upgrade
finding remain separate. This proposal does not resolve that finding.

## Outcome, evidence and existing owners

Require a truthful compatible Procutil distribution in application metadata,
without making development tests ignore that minimum or pretend frozen source
metadata satisfies it. Preserve reproducible source identity, actual API
qualification and the normal release allocation owner.

The current source-test owner is `tools/plugin_test_cache.py`:
`source_resolution_arguments()` emits a canonical local-source override and a
**separate retained constraint**. Its existing `released_graph` fixture and
real UV control live in `tools/test_plugin_test_cache.py`.
A contained diagnostic used that actual owner with a synthetic package at
frozen `0.1.0-dev7` and a diagnostic minimum of `0.1.57-dev1`. One offline,
no-build UV invocation rejected it as unsatisfiable, rather than allowing the
override to erase the minimum. The outer diagnostic assertion expected a more
literal version spelling than UV emitted and failed; it was not reported as a
passing test or rerun. The saved real resolver rejection is the evidence.
Neither version is selected as the borrowed-stdio-compatible release floor.

`tools/promote_release.py` owns Main seeding and pending-changefile history;
`tools/accumulate_bumps.py` owns version computation and admitted destinations;
`tools/materialize_main.py` owns canonical/physical source materialization.
Their existing `consume_pending_changes()` path seeds from pinned Main before
allocation. It also consumes scratch changefiles and materializes other data:
do **not** call the complete promotion path merely to prepare tests.
The existing source-test snapshot, cache/fingerprint, admission lease and
cleanup owners retain their responsibilities.

This closes the existing CLI-through-one-service-contract intent and preserves
the [version-skew contract](../../../docs/patterns/service-contract-evolution.md).
It does not add a release allocator, cache, journal, transport or runtime owner.
The [release/versioning rules](../../../docs/pipelines.md) remain authoritative.

## Options and selected source design

1. **Keep the raw frozen source override and add the real minimum:** rejected
   by the observed resolver control. Library-first release alone does not fix
   the development source identity.
2. **Drop constraints, guess a version, or stamp the requested minimum into
   fixture metadata:** rejected. This hides the original incompatibility.
3. **Test only a released wheel:** necessary for the later installed-upgrade
   control, but insufficient for testing current, possibly modified source.
4. **Reuse release-owned version preparation in the existing source-test
   snapshot:** selected for implementation after this plan clears review.

Factor the existing seed/compute/admitted-apply portion into a narrow,
release-owned preparation seam, shared by promotion and source-test composition.
Do not duplicate version arithmetic or create a parallel promotion command.
It may write generated version-bearing metadata only inside the already owned,
contained source-test snapshot. The checkout's frozen manifests, consumed-state
history, Main, tags, release assets and installed environments stay untouched.
This is canonical generated candidate metadata, not manual version stamping.

### Input and output contract

Inputs are a pinned candidate commit/tree, pinned actual Main commit and its
last-promotion Dev boundary, exact newly applicable changefiles, the required
first-party dependency closure, and the existing trusted allocation tooling.
Resolve them through existing repository/source readers. Missing history,
unsafe paths, ambiguous sources or unavailable allocation inputs refuse
preparation; there is no remote fetch, guessed seed or constraint-free fallback
inside a test request.

The release owner computes versions independently of consumer minimum values.
The consumer cannot request a version that makes its bound pass. Reuse normal
canonical/physical-copy rules, transitive consumer coverage and admission.
Bound work to the selected dependency/build-resource closure; do not generate
unrelated applications, projections, archives or pipeline control state.
If the existing allocation/materialization code cannot separate that work,
refactor its shared seam first under this same reviewed contract.

Output is an immutable prepared-source mapping and its generated metadata.
The existing test preparation/fingerprint record must bind candidate/Main
identities, allocation-tool inputs, applicable changefiles, original and
generated versions, exact source/build-resource hashes and selected paths.
Do not invent a second receipt store. Validate that only authorized generated
metadata changed; source bodies and declared consumer requirements must match
the pinned inputs. Cached reuse requires all those identities to match.

The resolver receives the actual prepared package version, canonical source
mapping, **unchanged consumer bounds** and normal marker/extra evaluation.
An unsatisfied bound still refuses. A version match establishes dependency
resolution, **not ABI/API compatibility**: the borrowed-stream and compact
closure consumer controls below must independently pass on that exact source.
Candidate versions are identified as test candidates, never as published
compatible distributions or proof that an installed upgrade occurred.

### Resource and compatibility boundaries

Preparation runs inside the existing source-test admission/lease and aggregate
preparation deadline, without a new worker pool, persistent cache or daemon.
No budget is enlarged. Partial preparation is not reusable; cancellation,
failed validation or uncertain cleanup retains the existing physical owner and
its failure evidence. Cleanup uses that owner, not a new age-based sweep.
Normal tests whose requirements already resolve retain their behavior.
No production installer is taught to ignore or reinterpret package minima.

Keep the existing wire protocol, strict legacy discovery, single-frame replies,
fresh-challenge authentication, generation scope and absolute CLI lifetime.
CLI 60 seconds; Gateway body/frame/response 32 KiB/64 KiB/8 MiB, control burst/rate
32/16, request burst/rate 128/32, deadline 60 seconds; SDK 180/600 seconds remain.

## Dependency-ordered implementation and acceptance

- [ ] Obtain independent assessment and merge this plan under the **current**
  repository review/CI/merge rules, then adopt it before policy code.
  This proposal does not govern or weaken its own acceptance.
- [ ] Implement and qualify only the shared release-owned preparation seam
  and existing source-test composition/fingerprint integration.
- [ ] Prove the original frozen-source rejection remains a negative control;
  prove a normal owner-allocated candidate can resolve a genuinely satisfied
  minimum without changing the source checkout or the requirement.
- [ ] Publish the smallest dependency-layer source slice: Procutil borrowed
  stdin/stdout descriptors, refusal/lifetime tests, necessary Core optional
  helper export closure and mixed-layout controls, canonical derived artifacts
  and all affected-consumer/library changefiles. Do not copy the observer,
  profile or bulk implementation into a competing application PR.
- [ ] Observe the existing normal `validate-and-promote` owner through actual
  publication. Never manually dispatch promotion, push Main, select a future
  release version or bypass a release failure.
- [ ] Read the actual compatible promoted distribution/version and its API
  evidence. Only then add that truthful application dependency minimum to
  existing #11084. A package version alone is not API evidence.
- [ ] In an isolated fixture, install the preceding real dependency
  distribution, upgrade **only the application** through real metadata and
  resolution, verify selection of the compatible dependency, then invoke the
  actual CLI/borrowed-stdio path. Do not substitute `--no-deps`, mocked calls,
  a local-source override or this diagnostic fixture for the upgrade.
- [ ] Safely adopt the dependency source merge in #11084, preserve original
  commits/backups and authored work, refresh exact-head source/type/CI/review
  evidence, and resolve the High finding only after actual compatibility proof.
  Merge only after all normal gates pass.

Implementation may be split into small source PRs with actual dependencies:
preparation policy first, dependency API/closure next, existing application last.
Each uses normal programmatic source isolation and repository delivery; no
additional agent-hosting session or infrastructure is required.

## Validation and rollback

- [ ] Compare promotion and test preparation on the same pinned inputs: same
  allocation, consumed-changefile boundary and canonical/physical versions.
- [ ] A consumer minimum above the owner's computed candidate version refuses;
  changing the minimum must not influence the allocated version.
- [ ] A same-version source-body change, different Main boundary/tooling,
  edited or already-consumed changefile, target marker/extra change, or altered
  build resource invalidates reuse or produces the existing explicit refusal.
- [ ] Missing Main/history, conflicting/symlinked source roots, unsafe metadata
  destinations, incomplete preparation and cancellation fail closed without
  checkout writes, partial-cache success or hidden process/resource release.
- [ ] Prove the generated metadata delta is version-owner-only and every
  source/requirement/provenance binding matches; a forged API-compatible version
  or missing `_native_types`/borrowed-stream implementation fails real consumer
  qualification even if numeric resolution succeeds.
- [ ] Keep old-caller/new-Procutil behavior; cover old/new optional compact
  helpers, new application/old dependency rejection, compatible upgrade and
  supported rollback with real serialized CLI delivery and buffered-state
  refusal. Preserve finite process/Job cleanup ownership.
- [ ] Rollback of the preparation integration restores the explicit original
  unsatisfied-source result when the floor cannot be met, never ignored bounds.
  Expand the library API before migrating the application; do not remove it
  during the supported rollback window. Reverting the application migration
  must leave older callers working with the expanded library.

Documentation follows the source change: update the existing testing and
release/versioning guides with candidate-versus-released identity and refusal
semantics; do not describe this proposal as already implemented.
Source acceptance remains distinct from installation, runtime/service changes,
profile adoption, credential/access changes, live provider qualification,
desktop/capture experiments and rollout. Those effects are not selected here.
