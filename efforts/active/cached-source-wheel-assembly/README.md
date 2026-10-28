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

Optimize released installation for prepacked first-party artifacts and fast
assembly from cache. Reuse the promotion pipeline's existing first-party wheel
bundles rather than inventing another artifact format or publisher. Source
remains available for development and governed fallback; raw-source delivery
does not require rebuilding that code on every device. When a local build is
necessary, cache it by its complete construction inputs. An empty target
environment is not a reason to rebuild unchanged code.

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

The clarified immediate priority is prepackaging through the existing release
artifacts and hardlinking from cache. Promotion may optimize the first-party
code representation for download and device assembly. Moving all installation
ownership to Worktree Manager and leaving plugins as discovery/usage guidance
is the broader direction, not a prerequisite for this performance slice.

The mechanisms and ordering below are agent-recommended implementation design,
not additional operator requirements or authority to bypass admission.

**Released-installer qualification priority, verbatim:**
> Great. Priority is going to be getting the updated installer tested on various machines. Any issue we encounter, we should track, and any performance issue should be analyzed, too. We want <1min for update, and even that feels slow.

The complete ordinary update through health is the sub-minute acceptance
target, not just wheel or venv assembly. The existing deployment owner retains
the serialized test lane. The per-target measurement breakdown, issue
deduplication and analysis below are agent-recommended execution detail.
This does not waive admission, integrity, dependency or service lifecycle gates.

**Proof and rollout sequencing, verbatim:**
> Once we get a proof-of-concept shipped in at least once service, we'll want to go to each machine and run an update. Then, we'll probably want to continue our install-focused detour here and just take on the work to align all service installers to reuse vendored code.

The first gate is an actual service consuming the prepacked inputs and using
hardlinked cache assembly, not merely a helper or disposable construction proof.
The addressed rollout follows release availability and existing per-machine
deployment authority. Portfolio installer convergence follows that wave; the
broader Manager/plugin ownership split remains outside this immediate slice.
Reuse the existing governed-artifact and
[core bundle](../runtime-bundle/README.md) work instead of creating another
builder, publisher or composition plan.

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

- [x] Implement and qualify the first exact-input reuse slice in the existing
  offline source composer. #9296 merged; this does not enable ordinary
  constructors or qualify the portfolio.
- [x] Identify the complete existing admitted construction identity: source
  contents and local dependency closure, selected extras/profile, interpreter
  compatibility, exact build backend/dependencies and packaging configuration.
  If a required input is not exact or attributable, retain an explicit miss
  rather than inventing cache equivalence.
- [x] Reuse complete locally built wheel artifacts for that exact identity,
  under existing installer-owned storage/custody. Do not edit source checkouts,
  live payloads, UV cache internals or another installation's state.
- [x] Build a missing first-party artifact once from the already admitted
  staged source, then publish its complete cache entry atomically. Preserve
  failure, interruption and concurrent-builder settlement.
- [x] Assemble a new venv from the selected cached dependency and local wheel
  artifacts without explicit directory targets forcing unnecessary rebuilds.
  Preserve exact dependency selection and configured-source policy.
  #9340 closes these constructor items for the explicitly supported static
  source graph; unsupported contracts retain the original isolated path.

### Phase 3 - Proportionate completion and adoption

- [ ] Qualify exact-input reuse for static graphs with different declared
  build requirements, retaining independently admitted tooling per requirement
  set rather than merging their resolution environments.
- [ ] Prioritize qualification of the released ordinary installer across the
  existing admitted machine scope. Preserve exact selected source, request,
  installed/serving identity, complete wall time and health in the deployment
  owner's ledger; distinguish changed-runtime, warm/no-op and cold paths.
- [ ] Require under sixty seconds for a complete update. Track each encountered
  issue, deduplicate against its existing owner, and analyze every performance
  miss by actual phase/child costs and repeated work. Do not exclude admission,
  transport or health costs silently or relax a correctness gate to meet time.
- [ ] Prefer the existing promotion-built first-party wheel bundles on the
  released installation path. Prove that the actual consumer skips local
  build-tool provisioning and first-party build hooks; producing a bundle
  without consuming it is not acceptance.
- [x] Use hardlinks from the extracted artifact cache for immutable candidate
  assembly. Keep generated or mutable files private and prove that preparation,
  update and cache retirement cannot change another generation's code.
  #9340 qualifies the ordinary Python constructor and private provenance writes,
  not release-artifact consumer adoption or every service lifecycle.
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
  record promotion and installed rollout separately.
- [ ] Prove the fast path in at least one released service before the
  operator-directed machine update wave. Record actual installed/serving identity
  through the owning deployment flow; do not infer rollout from a source merge.
- [ ] After that update wave, continue the proposed portfolio work to align
  service installers with shared vendored implementation, preserving each
  service's declared lifecycle and qualification boundary.

## Validation Plan

- [x] The actual UV reproduction distinguishes positional directories from
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

### 2026-10-28 - Ordinary graphs exposed a conservative cache miss

- The released-installer canary reports Core168.9 seconds and a separate
  payload-refresh snapshot of319.082 seconds. Core explicitly selected
  `distinct-build-requirements` and retained isolated source builds. These
  are partial observations, not the complete update equation or a health pass.
  The performance evidence is tracked on #9290.
- A read-only source-closure inspection finds two declaration groups in the
  update driver, Worktrees and Bridge: `setuptools>=83.0.0` and
  `setuptools>=68.0`. Manager additionally declares `setuptools>=68`.
  The whole-graph eligibility guard therefore excludes these ordinary graphs.
  This explains path selection, not all elapsed time or payload-refresh cost.
- The bounded follow-up gives each exact declaration set its own privately
  provisioned, policy-admitted tool environment. It never unions requirements.
  Exact installed tool bytes, target, interpreter, UV, builder and source still
  bind the existing cache key, with all tool environments rechecked.
  The contained Windows packet passes27 controls with one POSIX-only skip,
  including distinct lower bounds, conflicting pins and fresh-slot reuse
  without rebuilding source wheels. The pinned real-backend lane also passes:
  Linux26 controls/two synthetic-only skips, Windows25/three skips, in
  run40666514713 at `d7b2277e381e25990c5fe7d5770a1f272ba731f3`.
  The first required CI run refused the changed helper's old unit-admission
  digest; that single source digest is updated without changing unit selection,
  effects policy or coverage. Required CI and review remain delivery gates.
- Source work resumes here from the completed native-startup source slice.
  That effort's remaining POSIX/full-mux and installed gates remain under
  #9364, with no rollout or retained-session restart.

### 2026-10-28 - Released-installer timing qualification first

- Captured the operator's multi-machine test priority, issue tracking and
  sub-minute whole-update target. The existing admitted deployment owner and
  serialization remain; no second updater or unrelated rollout is introduced.
- Actual results and performance explanations are still required. The earlier
  narrow cached-wheel timing is not whole-update evidence, and source release
  is not proof of installation, serving health or prepacked real-service use.

### 2026-10-28,07:16 UTC - Constructor cache and hardlinks reached Main

- The current release snapshot is Main
  `29b265566e30a6ebd732fc3e891fac20ad3e9013`, actually merged by promotion
  #9373 at 07:16:10 UTC from qualified Dev
  `d77783f0a10b067a249dd39cee09f48f9dc6024b`.
- The #9340 constructor/cache/hardlink merge
  `1603cbbc0ac1f5a8838154d724e90eb4fa9f2724` is contained in that promoted
  Dev snapshot. The promotion identifies its source-artifact release and
  producing qualification run. This checks the source snapshot, not Git
  ancestry into the wholesale-regenerated Main tree.
- These faster ordinary-install changes are released, pending authorized
  rollout. No machine was updated by this observation. The distinct prepacked
  first-party artifact consumer, real-service proof, later serialized machine
  wave and installer portfolio acceptance remain open.

### 2026-10-28,05:55 UTC - Ordinary constructor slice merged; service proof remains open

#9340 actually merged as `1603cbbc0ac1f5a8838154d724e90eb4fa9f2724`
at05:55:51 UTC. Final head `4696344356c48690844be39ff8dea19b7fc605f9`
has current-head approval5678407122, no active issues, both resolved review
threads and all required checks passing. Native qualification40640273964 passes
the real-Setuptools cache/hardlink packets on Linux and Windows. The managed
Maintainer merge used the repository's normal additional-review exception;
no required CI or blocking review finding was bypassed.

The completed Plan items above credit the supported ordinary source constructor
and immutable hardlinks only. The effort stays Active. Source publication into
Dev is not release promotion, installed rollout or the required real-service
prepacked-artifact proof. The next slice composes with the existing #4876/#9032
toolkit prerequisite and normal Worktrees lifecycle rather than duplicating it.
Artifact consumption, one released service, serialized machine updates,
portfolio adoption, broader timing and the retained #8949 boundary remain open.

### 2026-10-28 - Keep shared build identities compatible

The latest review summary correctly identified that valid hardlinked
executables cannot pass the cache's stricter snapshot hashing. Eligibility now
retains the original isolated-source transaction before tooling/cache effects
when the selected UV, interpreter or helper identity has multiple links. This
preserves ordinary installation compatibility without changing the existing
bounded-reader ownership or weakening wheel/source input checks. Four native
filesystem controls pass, covering all three shared identity cases and normal
canonical-setup fresh/reuse behavior. Runtime-wheel hardlinks remain enabled.
All nine helper mirrors and their admitted source hash are synchronized.

### 2026-10-28 - Keep backend-reported requirements on the isolated path

The latest review summary identified older Setuptools contracts whose
`get_requires_for_build_wheel` adds `wheel` beyond the declared bootstrap
requirements. A cache miss now asks the admitted backend before its offline
non-isolated build and explicitly retains the original held isolated-source
transaction if any additional requirement is reported. It does not invent or
resolve that dependency itself. Exact cache hits do not execute the hook again.
Ten affected controls pass, including the actual synthetic backend reporting
the legacy dependency, refusal before a wheel/complete-marker is published and
fresh unchanged per-project reuse. That synthetic legacy control is explicitly
not the hosted real-modern-Setuptools lane; it skips there rather than
misrepresenting the modern backend's contract. All nine mirrors and the unit
source binding are synchronized.

### 2026-10-28 - Preserve caller interpreter and native cache context

The review's remaining summary concern was concrete even without an inline
finding: sanitizing the offline build environment removed the selected cache,
and entering retained child directories before resolving a relative interpreter
could change its meaning. The helper now resolves the caller's interpreter and
asks native `uv cache dir` for the held transaction's effective cache, retaining
that exact directory in offline builds without restoring feed configuration.
Ten affected controls pass, including actual fresh construction with a
caller-relative interpreter and both environment- and configuration-selected
cache directories. All nine helper mirrors and their admitted source hash are
synchronized. This is not a cache clear, feed change or timeout increase.

### 2026-10-28 - Keep lifecycle origin verification consistent

The subsequent review found the same canonical-name boundary in Worktrees'
installed-origin verifier, used by rollback and dev release. That owner now
normalizes metadata names only for source-wheel evidence, matching the engine
while preserving legacy directory-origin keys. All ten direct origin controls
pass in the contained plugin runner, including mixed-case/underscore wheel and
directory receipts and changed-origin refusals. The latest exact engine/helper
bytes pass the real-Setuptools cache/hardlink jobs on Linux and Windows in
run40633105108; this final lifecycle-verifier correction is separately covered
by its direct tests. Ordinary prepacked-artifact consumption remains open.

### 2026-10-28 - Normalize source-wheel names and preserve script layouts

The next review identified two further boundary cases. `script-files` projects
now retain the original isolated-source transaction before any tools are
provisioned, since the cache's supported wheel layout excludes `.data/scripts`.
Source-wheel metadata and completed-generation dependency comparisons normalize
distribution names; legacy directory-origin receipts retain the original
spelling. Native fresh/reuse cases cover ordinary and `Example_App` names in
both source-wheel and legacy directory modes. The full focused packet initially
reported nineteen passes, one POSIX-only skip and a failing mixed-case
wheel-metadata comparison; correcting that same normalization class made all
twelve affected eligibility/reuse controls pass. Canonical and nine consumer
copies are synchronized. Exact owned sandbox cleanup completed after transient
Windows executable locks, without host-state changes.

### 2026-10-28 - Preserve artifact mappings and isolated build requirements

Review of #9340 identified two unsupported graph shapes that must retain the
original constructor transaction. Removing local requirements manifests would
drop Manager's bundled `dbus-next` wheel mapping; pooling unlike build
requirements could create an unsatisfiable tools environment from individually
valid projects. Eligibility now explicitly misses before effects for either
shape. The unchanged held transaction retains the source mapping and separate
isolated build environments. The canonical helper and all nine mirrors are
synchronized. Twenty-six focused controls passed, including the exact held
Manager mapping, conflicting Setuptools pins and native offline bundled-wheel
installation. Test process containment completed, but immediate sandbox cleanup
encountered a transient Windows executable lock. A later exact-path cleanup
removed the owned sandbox without changing permissions or host state.

### 2026-10-28 - Speed-first direction and actual hardlink reuse

The immediate scope is release-artifact prepackaging and hardlink assembly,
not the broader Manager/plugin ownership migration. The existing promotion
builder already emits an app's first-party wheel closure in an attested bundle.
The ordinary Worktrees source lifecycle does not consume that artifact path;
another packaging format alone would not eliminate device-side builds.
That consumer adoption remains an explicit open gate, alongside the existing
release-lock-input and source-staging owners.

The ordinary immutable source constructor now selects a stable content-bound
archive URL and requests UV hardlinks. Native controls prove unchanged fresh
venvs share the same installed file identity, not merely equal contents, and
that changing generated Worktrees provenance leaves a hardlinked cached peer
unchanged. Thirty-three focused controls passed with one POSIX-only skip.
The mutable development-refresh and private build-tool paths retain copies.
The full disposable Windows lifecycle then passed with the original bounds:
cold construction 185.556s, a fresh venv reusing sixteen local wheels 58.211s,
and completed-generation reuse 10.063s. PowerShell 5, PowerShell 7 and Bash
entrypoints returned in 27.026s, 22.601s and 25.658s respectively. These are
individual samples, not tail qualification or the final speed target.
The three-project control also proves that unlinking its exact private UV
cache file leaves both hardlinked installed generations readable.

### 2026-10-27 - Real normal-path reuse measured; redundant copying removed

The disposable Worktrees lifecycle and native PowerShell/Bash entrypoints
passed with the existing timeouts unchanged. A second fresh immutable venv
reused all sixteen local project wheels without a build hook. Before the
copy reduction, cold construction took 211.707s, fresh-venv reuse 127.602s,
and completed-generation reuse 8.711s. Local wheel preparation accounted for
145.749s cold and 64.054s warm; the latter included 8.636s admitting and
fingerprinting the private build tools. These are one app's measured paths,
not thirteen-app update totals or a claim that the fast-path objective is met.

The warm path was still copying every project before checking for its wheel.
It now derives the same project selection directly from retained source,
copies only on a miss, and checks that copy against the binding before the
build hook and again after it. Manifest normalization removes volatile local
UV graph remapping, not packaging configuration or source modes.
Fourteen focused native controls passed, including a warm-hit prohibition on
source copying and refusal when source changes between keying and copying;
one executable-mode control remains POSIX-only. Actual before/after timing
and hosted real-backend qualification remain open.

### 2026-10-27 - Ordinary constructor integration implemented; qualification open

The canonical Python engine now retains a normal-source cache context after
the existing retained-input admission, under its existing construction
descriptor. The new helper admits a private build environment through the same
native UV configuration and seasoning policy, binds its actual installed
tool bytes, and builds supported static Setuptools projects offline without
build isolation. Unsupported contracts explicitly keep the original path.
The previously merged cache storage is now a canonical vendored helper rather
than a repository-only tool. No new feed or installation authority is created.

The normal native constructor control built three local projects, reused all
three across a fresh retention workspace, then rebuilt only the edited library
and installed its changed module. A contained cache/source/closed-wheel packet
passed53 controls with one POSIX-only skip. Missing installed content and
changed retained archives refuse before completion; backend mutation rejects
cache publication and permits a clean retry. Broader lifecycle qualification
is still running and its failures must be resolved before publication.

The exact #8949 overlap was inspected and a disjoint boundary recorded in its
thread: its bounded retained-content reader block is unchanged. No response
from that owner is claimed. The current synchronized engine consumers are
the Python adopters declared by `tools/installer_engine_ref.py`;
this is not a claim that all thirteen runtime installers execute this path.
An actual-backend hosted partition is added for Linux and Windows. Local
upstream-wheel retrieval failed TLS negotiation through two clients, so that
real-backend lane is not yet qualified. Ordinary adoption, latency accounting,
remaining installer implementations and proportionate validation remain open.

The actual Worktrees lifecycle initially exposed a real eligibility miss:
the process library carries the ordinary two-statement Setuptools `setup()`
entrypoint. That path completed its existing native lifecycle/reuse control,
but did not exercise the cache and therefore was not credited as adoption.
Eligibility now admits only that exact AST shape, with an optional docstring;
additional setup code remains a miss. A proposed mixed source/archive variant
was rejected after native UV reported conflicting local source URLs. No new
source-table override or altered backend-resolution policy was introduced to
paper over that conflict. The actual lifecycle control now requires source-wheel
evidence explicitly. Its new run and hosted real-backend qualification remain
open. The updated isolated normal packet passed12 controls with its POSIX-only
case skipped on Windows.

The cache-enabled actual lifecycle subsequently found a retained-byte
verification clock scoped to the build stage rather than the individual
verification operation. The reader now uses a fresh bounded I/O budget without
changing the lifecycle's child or return timeouts; its focused clock control
passes. The next actual run reached healthy candidate verification and runtime
selection, but still missed the existing300-second construct return bound.
That is an unresolved qualification failure, not a completed native install.
Trace the post-selection operations and account for their elapsed work before
publication; do not extend the bound or infer a pass from the healthy candidate.

### 2026-10-27 - Exact-input composition slice merged; normal adoption is next

#9296 merged at21:12:07 UTC as `5ed92969a9072907c724dbc630f1fe13c059d292`.
Current-head Copilot review5674269356 found no open issues; both earlier
high-priority findings were corrected and resolved. Qualification run40582712317
completed successfully across24 Linux/Windows jobs. Its Linux profiles packet
passed32 controls without skips, including actual permission-only invalidation
and rejected backend content/mode mutation followed by retry. Required CI also
passed after the final documentation edit; no CI gate was waived.

The source worktree is synchronized onto the merged Dev commit. The remaining
Phase2/3 and portfolio validation items stay open. Next, reconcile the normal
constructor boundary with #8949, supply exact build-input admission for that
path, and integrate self-contained caching with actual normal consumers.
Independent unchanged-library hits and proportionate completion work remain
separate measured requirements. The existing tooling-only composer is not an
ordinary installer, a production latency result or an installed rollout.

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
