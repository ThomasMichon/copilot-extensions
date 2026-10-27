# Cached Source Wheel Assembly

- **Slug:** `cached-source-wheel-assembly`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed proposal and implementation slices
- **Created:** 2026-10-27
- **Status:** Active
- **Vision:** `visions/plugin-services/runtime-assembly` features
  `one-verified-acquisition-path` and `complete-or-rejected-generations`
- **Umbrella issue:** #9290
- **Sub-issues:** existing #8873/#8949, #5851 and #6937/#6941 retain their scopes

## Guiding Intent

Keep the self-contained raw-source payload model. An app and its vendored
libraries can be delivered together without publishing prebuilt first-party
wheels. Routine installation should reuse admitted dependency artifacts and
locally built wheels when their complete construction inputs are unchanged.
An empty target environment is not a reason to rebuild unchanged local code.

The fast path must not install stale source or confuse a partial candidate
with a complete generation. Confirm the selected installed contents and stamp
completion, without making broad diagnostics or repeated source reconstruction
part of every routine assembly. Preserve the existing installation authority,
dependency policy, immutable generation and service activation boundaries.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| `buzzing-flywheel` | Proposal, isolated reproduction and non-overlapping implementation/validation | #9290 and the proposal PR |
| Existing installer owners | Retained-input verification, source staging and release-lock input coordination | #8873/#8949, #5851, #6937/#6941 |

## Coordination

- **Topology:** independent slices; a reviewed proposal precedes constructor edits.
- **Host:** `buzzing-flywheel` owns this proposal and its accepted implementation slice.
- **Delegates:** none. Existing owners are not replaced or assigned new work.
- **Overlap:** reconcile changes to the common engine and dependency helpers with
  #8949 before editing them. Its bounded content readers are not a cache and this
  effort does not supersede that PR. Keep #5851's source-staging/cleanup ownership
  and #6941's release-lock input work intact.
- **Handoff:** preserve reproduction results, input identity, outstanding review
  and native gates. A cache speedup or merged component is not portfolio adoption.

## Context

UV documents that local directories passed explicitly to `uv pip install`
are always rebuilt and reinstalled:
<https://docs.astral.sh/uv/concepts/cache/>.
The common source-backed constructor supplies explicit local project targets.

Requirements-file direct references avoid that special case, but UV's default
local-source freshness heuristic does not cover every source-body edit.
A negative control that changes a module without changing project metadata
can reuse the previous cached wheel. Therefore replacing positional directories
with a requirements file alone is not an acceptable fix.

The existing constructor already freezes admitted source content and retains
resolver/build inputs. Reuse that identity and custody rather than introducing
a second provenance authority or relying on a version, path or timestamp alone.
Raw-source delivery is not evidence that third-party dependency caches missed.

## Request

Public-safe scoped capture of the portable request:

> We need to ensure that for most updates/installs, we can quickly assemble the venv from cache. We need to cut down on pre- or post-validation, except to simply confirm we allocated everything form the wheel, and then stamp the venv complete.

The clarified delivery requirement is a self-contained raw-source payload per
app with its shared libraries vendored. This effort does not require changing
that distribution model to prebuilt first-party releases.

The mechanisms and ordering below are agent-recommended implementation design,
not additional operator requirements or authority to bypass admission.

## Plan

### Phase 1 - Review the bounded contract

- [x] Review and merge this proposal before changing the constructor.
  #9291 merged after current-head Copilot approval and required checks.
- [ ] Reconcile the exact common-helper edit boundary with #8949's owner.
- [x] Preserve the explicit-directory rebuild and stale requirements-cache
  negative controls in an isolated, synthetic, source-independent reproduction.
  `tools/reproduce_source_wheel_cache.py` exercises actual UV offline with an
  in-tree stdlib-only PEP517 backend and a private empty cache. Native Windows
  execution observed two explicit builds, then cached requirements reuse and
  stale source-body reuse. This is characterization, not cache acceptance.

### Phase 2 - Content-bound construction reuse

- [ ] Identify the complete existing admitted construction identity: source
  contents and local dependency closure, selected extras/profile, interpreter
  compatibility, exact build backend/dependencies and packaging configuration.
  If a required input is not exact or attributable, retain an explicit miss
  rather than inventing cache equivalence.
- [ ] Reuse complete locally built wheel artifacts for that exact identity,
  under existing installer-owned storage/custody. Do not edit source checkouts,
  live payloads, UV cache internals or another installation's state.
- [ ] Build a missing first-party artifact once from the already admitted
  staged source, then publish its complete cache entry atomically. Preserve
  failure, interruption and concurrent-builder settlement.
- [ ] Assemble a new venv from the selected cached dependency and local wheel
  artifacts without explicit directory targets forcing unnecessary rebuilds.
  Preserve exact dependency selection and configured-source policy.

### Phase 3 - Proportionate completion and adoption

- [ ] Separate necessary input admission and installed-content confirmation
  from repeated full-tree scans and optional broad diagnostics. Reuse existing
  held-input evidence where its actual custody makes that valid; do not omit
  checks merely because a cache or receipt exists.
- [ ] Stamp a candidate complete only after its installed set realizes the
  admitted construction. Keep existing service activation, health and rollback
  responsibility with their lifecycle owner; reconcile any contract changes
  explicitly before moving checks across that boundary.
- [ ] Integrate through the canonical engine and synchronized consumers, with
  a measured adopter ledger. Shared helper presence is not executed-path proof.
- [ ] Publish source changes through normal review, CI and release gates;
  record promotion and installed rollout separately. No production rollout is
  authorized by this proposal.

## Validation Plan

- [ ] The actual UV reproduction distinguishes positional directories from
  requirements references and detects stale source-body reuse.
- [ ] Unchanged admitted inputs reach a fresh venv without first-party build
  subprocesses or unnecessary dependency resolution; measure acquisition,
  build, copy/install, confirmation and stamp time separately.
- [ ] Changing only a source module, a vendored dependency, build inputs,
  interpreter compatibility or selected profile invalidates reuse even when
  the app version and project metadata are unchanged.
- [ ] Interrupted construction, incomplete/corrupt cache entries and concurrent
  builders never publish a complete marker or disturb the prior generation.
- [ ] Exact installed distribution/content confirmation succeeds for the
  selected inputs and refuses missing, extra or mismatched content.
- [ ] Warm/no-index and cold-cache controls preserve approved acquisition,
  dependency seasoning and actual source limits; no cache clearing or policy
  waiver is used to claim success.
- [ ] Focused units, contained native UV/clean-room controls, install-contract
  checks, synchronization and applicable cross-platform qualification pass.
  Keep unavailable platform/venue evidence explicit rather than substituting it.
- [ ] The adopter ledger proves actual construction paths and measured work.
  Every Plan/Validation item is resolved or transferred before this effort is Done.

## Proposal

Reviewed in #9291, merged as `582cd6effe2327e9abfb18726e225ad14e361be0`
on 2026-10-27 at19:56:52 UTC. Copilot review5673424495 approved the exact
candidate head and required checks passed. This accepts the plan, not an
implemented cache, adopter qualification or rollout.

The standalone reproduction requires explicit absolute UV/interpreter paths
and an existing private scratch parent. It creates only disposable source,
cache and venv directories and preserves raw output in the returned task folder:

```text
python tools/reproduce_source_wheel_cache.py --uv <absolute-uv> --python <absolute-python> --scratch-parent <private-scratch-parent>
```

It uses no package feed, external build backend, production installer or
service operation. Its four cases report actual backend build counts and
whether changed source reached the installed module. The stale-source outcome
is evidence against the naive argument substitution, not a passing correctness
claim for that substitution. Remove only the returned task folder after its
evidence is no longer needed.

## Journal

### 2026-10-27 - Corrected review-discovered cache invalidation gaps

The first review identified permission-only source changes and publication
before the compact backend source was rechecked. The binding now includes the
authenticated Git tree, and fresh cache publication checks compact source
content and modes before publishing. Actual backend-mutation/retry and POSIX
mode-only wheel-permission controls were added. The updated targeted Windows
packet passed19 controls, with its two POSIX-only cases skipped; updated
cross-platform qualification and review remain required.

### 2026-10-27 - Implemented the exact-input composition slice locally

The ordinary retained UV preflight supplies configuration and source custody,
not an exact resolved backend artifact lock. The existing repository-only
source-runtime composer already supplies inspected, hashed build-tool wheels
and offline owned backend execution. This slice adds content-bound local wheel
reuse there without editing #8949's canonical helpers or enabling a new normal
consumer.

The first contained native Windows control built two local projects, reused both
without another backend build across a fresh retention transaction, and rebuilt
after a module-only source edit. Actual wheel contents contained the edit.
Input identity uses authenticated source assembly content rather than
temporary-path-dependent retained bytes. The whole source graph is currently
one conservative invalidation unit; unchanged libraries in a changed graph do
not yet get independent hits. The contained native Windows cache/source-build/
source-wheel packet passed 61 controls; its POSIX-only FIFO control was skipped.
Touched Python lint and the 13-plugin installation-contract check passed.
Cross-platform qualification remains in progress. This is not completion,
ordinary installer adoption, a production speed measurement or rollout.

### 2026-10-27 - Proposal accepted; implementation boundary remains explicit

#9291 merged after current-head approval and required checks; the source
worktree synchronized onto that reviewed commit. The generic reproduction
confirmed that requirements references can reuse a wheel while missing a
source-body-only change, so the naive argument substitution remains rejected.
A coordination comment on #8949 preserves the overlapping verification owner;
no common-helper code has been edited. Continue the admitted-input identity
and cache implementation slice without counting the proposal as completion.

### 2026-10-27 - Claimed the cache-safe source assembly slice

Filed #9290 after checking existing installer and cache work. The initial local
controls establish an explicit-directory rebuild mechanism and a stale-source
negative control for the tempting requirements-only workaround. Implementation
waits for proposal review and the overlapping common-helper owner boundary.
