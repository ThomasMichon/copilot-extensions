# Governed Python Artifact Promotion

- **Slug:** `governed-python-artifact-promotion`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice (see Coordination)
- **Created:** 2026-10-01
- **Status:** Active
- **Vision:** extends `visions/installer/README.md`, `visions/plugin-services/installation-cells/README.md`, `visions/plugin-services/runtime-assembly/README.md`, and the agent-index runtime vision; this effort does not replace any of them
- **Umbrella issue:** [#4876](https://github.com/ThomasMichon/copilot-extensions/issues/4876)
- **Sub-issues:** _pending_

## Guiding Intent

Make Python plugin updates substantially faster by producing reusable,
content-addressed first-party installation inputs during promotion, while
preserving package-feed governance, provenance, cross-platform correctness,
rollback safety, and bounded storage. No release may silently fall back to
a public package index for third-party dependencies, and no release bundles
a complete virtual environment or a single packaged binary as its primary
distribution unit.

Public promotion runners may explicitly source the build toolchain
(`setuptools`, `wheel`, `packaging`) from seasoned public releases. This
approved exception does not apply to managed-device runtime dependency
resolution, does not certify availability on any consumer's governed feed,
and does not permit third-party dependency bytes in published artifact sets.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| _(unassigned)_ | Design, spike, and phased implementation | a dedicated `copilot-extensions` worktree |
| Consumer coordinator | Wheel-byte validation and shared-installer consumer integration | Managed contribution worktree; #4876 |

## Coordination

- **Topology:** independent per-slice PRs (each phase should leave `dev`
  green on its own).
- **Host (owns PRs):** whichever participant opens each phase's PR.
- **Delegates:** none yet.
- **Handoff:** completed phases are journaled here with links to their
  merged PRs before the next phase starts.

## Context

- **Do not duplicate adjacent work.** Installer execution mechanics belong
  to `efforts/active/vendored-installer-engine/README.md`; installation-cell
  ownership belongs to `visions/plugin-services/installation-cells/README.md`;
  deferred on-demand provisioning belongs to
  `efforts/active/tiered-payload-provisioning/README.md`. The agent-index
  thin-client/heavy-server split is **already landed**
  (`efforts/active/agent-index-server-package-split/README.md`,
  `efforts/active/agent-index-engine-daemon/README.md`) - do not re-plan it.
- **What is genuinely missing** (this effort's actual scope): promotion-time
  first-party wheel/manifest generation keyed to payload hash + platform +
  architecture + Python ABI; a governed-feed-aware dependency-closure
  admission policy that accounts for feed propagation lag instead of
  locking to not-yet-available versions; and verified artifact consumption
  with a correct source-build fallback when no matching artifact exists.
- A plugin's own `materialize_main.py` promotion step already vendors its
  `tool.uv.sources` `libs/<lib>` path dependencies into its own tree; those
  vendored libs have no governed-feed identity and must be covered by this
  effort's artifact set too, not just the top-level plugin wheel.
- Baseline measurements (directional, to be re-validated with this effort's
  own spike rather than assumed): representative Windows ZIP sizes around
  12-25 MiB for most plugins; a complete-venv-archive distribution strategy
  was estimated at roughly 400 MiB per OS/arch/ABI tuple, which is why this
  effort does not pursue that as the primary distribution unit.

## Request

> Can promotion pre-build Python installation inputs for plugin packages, so
> that plugin installers can check for a version-matching, hash-verified
> release and download it directly instead of building a virtual
> environment from source on every update? Third-party dependencies must
> never be pulled through a public hosted package index - only through the
> machine's governed, seasoned feed configuration - and the design must
> avoid an excessive version lock that pins a dependency version newer than
> what the governed feed currently carries (governed feeds can lag public
> releases by about a week). A single packaged binary is not an acceptable
> distribution strategy.

**2026-10-10 clarification:** the original request's governed-feed-only
rule remains binding for managed-device dependencies. Public promotion
build tooling has the explicit exception described in Guiding Intent above;
the earlier wording must not be read as requiring public runners to reach a
private consumer feed. Consumer-side admission still checks the actual feed.

**2026-10-13 consumer-policy direction:**

> Whatever has the biggest chance of succeeding in most machine governance situations

The operator delegated the implementation choice, rather than prescribing a
particular resolver or authorizing a governance exception. The
agent-recommended first consumer lane below uses inspected governed-feed
wheels and offline assembly; it does not relax the original feed-only rule.

**2026-10-14 lock-authority clarification:**

> authority-locks

The operator selected **"Keep exact locks; design authority-approved
feed-compatible lock profiles"** after reviewing the conflict between local
re-resolution and the active
[Runtime Assembly vision](../../../visions/plugin-services/runtime-assembly/README.md).
This supersedes the agent's proposed host-side dependency re-resolution.
Profile approval and selection belong to the designated release/installation
authority; host admission realizes that exact selection. It does not relax a
pin to accommodate feed lag.

**Client fallback and release-availability clarification:**

> We do need to have a fallback client-side, if the artifacts don't resolve or their dependencies get blocked, though, to having the client assemble the venv locally
>
> It also looks like dev-to-main had artifact publication fail, so we've introduced a new point of failure

The client-side source-build path is required, not a future optional
enhancement. The normal artifact path retains its exact authority-owned
lock. When that path is unavailable, the same installation authority may
select the verified-source fallback and establish its own explicit exact
installation lock within the release's declared constraints and machine
policy. That is a different, attributable assembly decision, not silent
mutation of the artifact lock. Neither mode can bypass a package block,
seasoning requirement, source-integrity failure or governed-feed routing.
Artifact infrastructure must not become a prerequisite for an otherwise
permitted local source installation.

**Promotion ordering and interim client requirement:**

> Somehow agents are unable to take the latest main promotion due to "unavailable" or "non-matching" artifacts. Is that caused by the work done here? Artifact production needs to be done by the promotion flow, before it declares completion and merges the PR. And for now dependency on artifacts needs to be optional during install and update.

This supersedes the earlier proposal to let an artifact-production failure
pass source promotion. The promotion flow must produce, verify and establish
availability of the exact candidate's artifacts before merging its PR or
declaring completion. A post-merge publisher alone cannot satisfy that gate.
Install and update have a different contract: artifact use remains optional
for now, and their existing governed local-source path must not wait for
artifact rollout/publication proof. Missing or non-matching artifacts are
availability outcomes, not permission to consume invalid bytes or bypass
dependency governance. Preserve source-integrity and artifact-integrity
refusals and the installation authority's explicit path selection.

## Plan

### Phase 1 - Spike: governed-feed-only install and timing baseline

- [x] **Done (2026-10-02).** Proved a first-party wheel can be installed
      while every transitive third-party resolution uses only the
      machine's governed feed configuration. See Proposal for the evidence
      and method.
- [x] **Done (2026-10-02).** Measured actual governed-feed propagation lag
      across 3 real third-party dependencies - see Proposal. Lag is **not**
      a fixed constant; it varies per package from ~0 days to 65+ days,
      correcting this effort's original "~1 week" assumption.
- [x] **Done (2026-10-02).** Compared from-source vs. verified first-party
      wheel install, cold and warm cache, wall time and physical storage -
      see Proposal. Network-activity comparison (request counts/bytes) was
      not captured; only host/index identity was verified. If a future
      phase needs exact byte/request-count deltas, re-run with a packet
      capture or an HTTP proxy in front of both paths.
- [x] **Done (2026-10-02).** Findings recorded below; later phases should
      treat the lag-tolerant version-selection design as revised by this
      evidence (a dynamic, feed-queried admission check, not a static
      age window).

### Phase 2 - Promotion-built, content-addressed first-party artifacts

- [x] **Builder and promotion wiring implemented (2026-10-12).**
      `tools/build_python_artifacts.py` builds a plugin's own wheel plus
      every vendored `libs/<lib>` wheel it needs, discovering both the
      dev-branch live `editable = true` canonical-reference form and an
      ordinary in-tree vendored copy, recursively. The originally requested
      top-level plugin's own escaping references are validated as a whole
      via `uv_editable_ref.uv_editable_problems`; every reference
      discovered while recursing into an already-found vendored lib (an
      escaping `editable = true` entry, or a non-editable in-tree one) is
      instead validated per-entry with this tool's own structural
      validators, since a vendored lib can legitimately cross-reference a
      sibling vendored lib without `editable = true` -- a pattern
      `uv_editable_problems` was never designed to accept at the
      whole-consumer level. It writes a manifest
      recording the payload hash (a working-tree content hash - not `git
      HEAD`, since promotion's scratch tree is mutated by version bumps and
      materialization before the build runs), the wheel filenames' own
      python/abi/platform tags (with a hard failure on a genuinely
      conflicting tag across the set, never a silent first-match guess),
      each wheel's sha256, and the build-tool `Generator:` actually used
      (read from each built wheel's own `dist-info/WHEEL`, required -
      a wheel with no readable `Generator:` fails the build rather than
      recording an unknown toolchain) - all folded together into one
      `artifact_id`, including every wheel's own digest. **Build
      hermeticity resolved (2026-10-02, second slice):** every wheel is now
      built `uv build --wheel --no-build-isolation` against ONE pinned
      `setuptools`/`wheel` venv (`resolve_toolchain_lock`), resolved once
      and reusable -- unchanged -- across every plugin in a run by passing
      the same `--toolchain-venv` path to each invocation; each built
      wheel's own `Generator:` is verified to match the locked toolchain
      exactly, failing closed on any drift. The manifest's `build_toolchain`
      field is now a structured lock record (`{"packages": {...},
      "provenance": {...}, "lock_id": "sha256:..."}`, schema version 3) rather than schema
      version 1's ad hoc per-wheel generator list, and `lock_id` folds into
      `artifact_id`. Smoke-tested for real: building `agent-bridge` then
      `agent-worktrees` against the SAME `--toolchain-venv` produced
      identical `lock_id`s, confirming the shared-lock mechanism. **Not yet
      done:** live release-lane proof. The `build-python-artifacts` job now
      invokes `promote_release.py --build-python-artifacts-dir` to compose
      the real candidate and build every first-party set in a second,
      disposable checkout with one shared public-runner toolchain. The
      builder remains independently usable. Actual trusted-main signing
      and publication evidence is tracked explicitly in Validation Plan.
- [x] **Contract implemented (2026-10-12).** Native GitHub OIDC attestations
      authenticate both the transfer index and first-party bundles.
      Signing runs without release-write credentials. Verification constrains
      the pinned hosted workflow/source identity; the writer checks exact
      candidate composition before push and actual merge composition before
      immutable GitHub Release publication. Live signing/release proof remains
      a named Validation Plan gate, not an implementation gap.
- [ ] Design and publish authority-approved exact dependency-lock profiles
      for the third-party portion, bound to the selected first-party release
      and its existing provenance identity. Define who approves/selects a
      profile and which approved source supplies its feed-compatibility
      evidence before enabling it. Public CI cannot certify a private feed;
      this does not extend the public build-tooling exception to runtime
      dependencies, add another trust root or distribute third-party bytes.
      The bounded [exact-profile contract](exact-profile-contract.md)
      contributes the authority/selection and engine-consumer seam from #6234;
      its design is approved in #6795 and the inert record/selection slice
      merged in #7044. Authenticated approval adapters, real exact closures,
      publication and consumer admission remain open.
      The [zero-configuration amendment](../lean-runtime-dependencies/zero-configuration-defaults.md)
      requires a complete adopting-policy baseline: release profiles preferred,
      automatically selected governed source mode when permitted, and no
      personal approval file or per-dependency prompt as a normal prerequisite.



### Phase 3 - Verified consumption with correct fallback

- [x] Provide the bounded shared wheel-inspection component and complete-set
      actual-byte verification (#8419). Inspection facts do not authenticate
      approval, enable a consumer or replace retained-construction evidence.
- [x] Retain exact wheel-byte snapshots under the existing private input owner
      with explicit count, archive/closure/storage and deadline bounds (#8523).
      Custody evidence alone does not authenticate acquisition or approve reuse.
- [ ] Implement the reviewed [wheel-input admission slice](wheel-input-admission.md):
      compare actual wheel bytes with the exact selected record before retained
      construction, then integrate owned reuse evidence without creating approval.
- [ ] Teach installers (via the shared installer engine, not a parallel
      mechanism) to locate, verify, and consume a matching artifact set,
      falling back explicitly to the existing client-side source-build/venv
      path when artifact discovery/acquisition fails or its dependency set
      cannot be admitted. An artifact-integrity/provenance failure remains
      a security refusal, not an ordinary fallback trigger. Preserve existing
      immutable-slot assembly, health gates, activation, and rollback.
- [ ] Validate across supported platform/architecture/Python-ABI
      combinations, including a clean-room first install.

#### First consumer lane _(authority-lock direction; review before implementation)_

Prefer a portable, invocation-local acquisition boundary over a new proxy,
resident service, administrator-installed egress rule or unrestricted source
build. These additional machine facilities would reduce deployability on
locked-down hosts without themselves proving the package-source contract.

- [ ] Accept only the release/installation authority's selected exact lock
      profile, bound to the verified first-party release. A lag-tolerant
      choice among approved profiles happens at that authority, not inside
      artifact admission. If the artifact path is unavailable, report its
      reason and request the same authority's verified-source fallback;
      admission never invents a closure or silently chooses another release.
- [ ] Preserve a client-local source-build path independent of release-asset
      availability. Build from the independently verified selected source
      payload, using the existing shared engine's staging, health and rollback
      path. The installation authority may resolve an admissible fallback
      closure from the machine's governed feed within declared constraints,
      lock it before assembly, and record source-build mode and the trigger.
      Do not claim the resulting generation realizes the unavailable artifact
      lock. If every permitted dependency choice is blocked, defer explicitly
      rather than bypassing governance or reporting a successful install.
- [ ] Acquire the selected lock's exact wheels only through affirmatively
      approved sources. Preserve named-index authentication and OS trust
      configuration without exposing credentials in argv, logs or receipts.
      Bind download locations and redirect chains to explicit feed trust;
      a trusted index URL alone does not authorize arbitrary linked hosts.
      Preserve each file's feed admission metadata and exclude yanked or
      withdrawn files before exposing the local set to resolution, including
      exact pins. Copying a wheel locally never erases a feed's refusal.
- [ ] Inspect each selected wheel's actual metadata before assembly. Reject
      direct URL, VCS and external-path requirements before their targets are
      acquired, and require a closed dependency set matching the selected
      lock. Require intact distribution identity, wheel tags and file hashes;
      metadata-sidecar hints do not replace verification of actual bytes.
- [ ] Declare finite per-package and total acquisition counts, streamed
      metadata/wheel byte limits, total temporary-storage limits and one
      inclusive acquisition/assembly deadline before fetching. Bound
      concurrent work by those same budgets. Exhaustion fails
      closed without activation; success, failure and cancellation all clean
      up invocation-owned partial inputs. Select measured defaults before
      enabling the lane; admission does not enumerate feed version histories.
- [ ] Assemble solely from the inspected, exact locked wheel set, with
      network disabled for those operations and no ambient cache, index,
      override, dependency re-resolution or build-backend escape. Validate
      the selected lock's extras, markers and interpreter/tag compatibility,
      rather than resolving a substitute closure. Feed availability and any
      applicable machine seasoning policy remain admission conditions, not
      permission to replace a pin.
- [ ] Persist an assembly receipt recording the exact lock/profile identity,
      selected versions/wheel hashes, target and applicable policy.
      Artifact-mode receipts bind the signed artifact manifest digest;
      source-build receipts instead bind the independently validated source
      release identity/digest, exact fallback installation lock and trigger.
      Source-mode proof must remain available without an artifact manifest
      or successful artifact build/sign/publication. Both record the actual
      assembly mode and evidence of existing provenance, not a second trust
      root. Reuse admitted unchanged dependencies without
      requiring feed access, as the Runtime Assembly vision requires.
      Re-evaluate when explicit policy or supplied revocation evidence
      invalidates admission. An unadmitted cache entry is not reusable proof.
      Missing bytes or necessary admission evidence defer without activation.
- [ ] Distinguish artifact-path unavailability from source-build failure.
      A missing compatible artifact wheel or blocked artifact closure may
      invoke the authorized local fallback; source and build dependencies
      still require approved acquisition and applicable policy. Define and
      test build-backend isolation before admitting source-only third-party
      inputs. An unsafe or unavailable fallback leaves the prior generation
      intact with the actual cause, never a public-index escape.
- [ ] Integrate only through the existing shared installer engine after
      acquisition/admission contracts pass. Preserve source fallback for an
      unavailable artifact path and fail closed for a present invalid
      artifact. Native release verification and Windows/POSIX
      first-touch evidence remain prerequisites to enabling consumption.
- [ ] Coordinate with the existing workflow/publisher owners so promotion
      produces, verifies and establishes client-consumable availability of
      exact-candidate artifacts before merging its PR or declaring success.
      Artifact build/sign/publication failures leave that PR unmerged, with
      an explicit failed gate and exact-candidate retry; retain source
      validation, signer/source binding and immutable-asset checks. Any
      final post-merge identity binding must not be the first availability
      check or retroactively stand in for pre-merge production.
- [ ] Keep install/update artifact consumption optional for now. The
      authorized governed local-source path remains usable for an already
      selected release when artifacts are missing, non-matching or
      unavailable, without an agent-imposed publication/rollout-proof gate.
      Preserve dependency governance, immutable staging, health checks and
      integrity refusal; source fallback is not an authorization to install.

### Phase 4 - Retention, provenance, and documentation

- [ ] Define artifact retention/GC with physical-storage accounting and
      provenance/licensing review.
- [ ] Document the artifact contract, rollback behavior, and feed-lag
      diagnosis for operators.

## Open Design Questions and Resolved Promotion Decisions

Earlier review rounds on this effort surfaced several real design gaps
worth preserving as named open questions, deliberately **not** pre-solved in
this planning document. Promotion decisions belong to Phase 2; consumer
feed/admission decisions belong to Phase 3. Each requires a concrete,
verified answer against its actual repository or machine configuration:

- **Feed model:** promotion runs on public CI and cannot certify
  installability from any one consumer's private governed feed - the
  governed-feed check is necessarily a consumer-side, install-time concern,
  not a promotion-time one.
- **Lag-tolerant version selection:** a public version or age floor does not
  establish availability on a particular governed feed.
  **Phase 1 evidence (2026-10-02): lag
  is not a fixed ~1-week constant** - measured at 0 days (pydantic,
  uvicorn) to 65+ days (fastapi) across just 3 real dependencies of one
  plugin. The designated installation authority queries actual feed
  availability when choosing an approved exact profile or establishing an
  authorized source-fallback lock within declared constraints. Admission
  realizes that exact selection; it never silently relaxes an existing lock.
  Required seasoning remains an additional eligibility floor, not an
  estimate of feed propagation. See Proposal for the full measurement.
- **Trust root -- resolved (2026-10-01):** `.github/workflows/validate-and-promote.yml`'s
  `promote` job confirms "branch-protected committed metadata" does **not**
  hold as a trust root here: `main` carries a zero-bypass PR-required
  ruleset, but the candidate PR that lands on it is itself authored and
  squash-merged by `APERTURE_RELEASE_TOKEN` (a fine-grained PAT scoped
  Contents: Read/write + Pull requests: Read/write -- see that job's
  `env.GH_TOKEN` and its surrounding comment block). Any digest committed
  to `main` by this same pipeline is only as trustworthy as that one PAT --
  an attacker (or bug) that can push the artifact can push the matching
  digest too. The fix is to root trust in a signer this PAT does not
  control: GitHub's native Artifact Attestations
  (`actions/attest-build-provenance`, Sigstore-backed, keyless OIDC
  signing). The signing identity is GitHub's own short-lived OIDC token for
  that exact workflow run/job/ref (subject binds repo + workflow path +
  ref), independently verifiable via `gh attestation verify` against
  Sigstore's public transparency log -- not a value this repo's own commits
  or `APERTURE_RELEASE_TOKEN` can produce. Neither `id-token:` nor any
  `attest`/`sigstore`/`cosign` usage exists anywhere in `.github/workflows/`
  at design time (confirmed absent in both `ci.yml` and
  `validate-and-promote.yml`). Phase 2 now provides this through the
  credential-separated `python-artifacts` job; it does not reuse repo-write
  authority as an authentication root.
- **Credential separation -- resolved (2026-10-01):** confirmed against the
  actual configuration (`validate-and-promote.yml` lines ~104-108 and
  ~455-510): the `promote` job's `main-promotion` environment holds both
  repo-write authority (`APERTURE_RELEASE_TOKEN`) and would be the natural
  place to also build/publish artifacts -- that single job must **not**
  be the one requesting the attestation's `id-token: write`. Phase 2 must
  run artifact build + attestation signing in a job/step that does **not**
  have `APERTURE_RELEASE_TOKEN` in scope (e.g. a separate job keyed only to
  `needs.gate.outputs.sha`, with its own minimal `permissions: id-token:
  write` and no `contents: write`), so that compromising the PAT does not
  also grant the ability to forge a passing attestation, and vice versa.
  This is the concrete form of "an independently constrained publisher
  credential" named in the original finding.
- **Build hermeticity -- resolved (2026-10-02):** confirmed every
  `pyproject.toml` in `plugins/` and `libs/` (43 files surveyed) uses the
  same `setuptools.build_meta` backend with an open-floor `requires`
  (`setuptools>=68.0`, `>=83.0.0`, or `>=84.0.0` depending on the package --
  never an upper bound or exact pin). A single shared backend simplifies
  the fix: Phase 2 must not rely on pip/`uv`'s normal PEP 517 **isolated**
  build environment (which silently resolves "whatever satisfies the floor
  today," unrecorded and unreproducible run-to-run). Instead, promotion
  resolves one shared **build-toolchain lock** per promotion run (exact
  `setuptools`/`wheel` versions, resolved from a governed feed on managed
  machines, or seasoned public releases in the explicitly opted-in public
  promotion-runner mode),
  installs it into a controlled build venv, and builds every plugin/lib
  wheel in that run with `--no-build-isolation` against that one locked
  venv. The resolved toolchain lock's hash becomes a component of each
  artifact's identity key alongside payload hash + platform + architecture
  + Python ABI (not a replacement for those fields), and the exact pinned
  versions are recorded in the artifact manifest -- so two promotions that
  happen to use different setuptools versions are naturally different,
  auditable artifact identities instead of a silent same-identity byte
  mismatch.
- **Vendored first-party libs -- resolved (2026-10-01):** `tools/materialize_main.py`
  already enumerates exactly the set this effort needs. Its
  `materialize_uv_editable_ref_into()` walks each consumer's
  `[tool.uv.sources]` entries, materializes every referenced `libs/<lib>`
  (recursing into a materialized lib's own nested `[tool.uv.sources]`
  entries), and accumulates them in a `materialized_libs` set -- each
  materialized `libs/<lib>/` carries its own untouched `pyproject.toml` and
  is independently buildable as a normal wheel. Phase 2 should reuse this
  exact enumeration (not re-derive it) as the authoritative per-plugin
  artifact set: the plugin's own wheel plus one wheel per entry in
  `materialized_libs`, each keyed by the same payload-hash + platform +
  architecture + Python-ABI identity scheme.
- **Publication channel -- resolved (2026-10-01):** GitHub Release assets,
  confirmed consistent with an existing precedent already in this repo:
  `ci.yml` (~line 636) already consumes a third-party dependency (`psmux`)
  via a deterministic `.../releases/download/v<version>/<fixed-filename>`
  URL. Phase 2 adopts the same shape for first-party artifacts: a
  deterministic tag per plugin/version (`<plugin>-v<version>`).
  **Refined by the 2026-10-12 integration:** each asset is a ZIP named
  `<plugin>-<version>-<artifact-id-digest>.zip`, containing the schema-3
  manifest, plugin wheel and declared vendored-lib wheels as one verified
  set. The signed transfer index binds the complete candidate/project set
  and every bundle digest. Content-addressed filenames permit independent
  rebuilds without overwriting an existing asset. Consumers discover these
  bundles through the deterministic release tag, not fixed per-wheel assets;
  no separate discovery service is required.
- **Wheel-tag semantic compatibility:** an artifact set's
  python/abi/platform tag reconciliation
  (`build_python_artifacts.overall_identity_tags`) currently treats any two
  differing, non-universal tags in the same slot as a hard conflict
  (strict string equality or fail closed) -- deliberately conservative, but
  not semantically correct: e.g. an `abi3`-tagged wheel is genuinely
  compatible with any `cp3x`-tagged wheel for a newer interpreter (CPython's
  stable ABI), and manylinux platform tags have their own compatibility
  hierarchy, neither of which this reconciliation understands. **Not yet a
  real problem**: every plugin/lib in this repo is pure-Python
  (`py3-none-any`) today (confirmed by the Build hermeticity survey above),
  so this never fires in practice. Resolving it concretely (a real PEP
  425/600-aware compatibility resolver, or an explicit target-tag-set
  validation approach) is deferred to whichever future phase first needs to
  build a platform-specific artifact -- not solved speculatively here, per
  this effort's own established pattern of naming real gaps rather than
  pre-solving unneeded generality.
- **Public-runner toolchain resolution -- resolved (2026-10-10):**
  `validate-and-promote.yml` runs on public GitHub-hosted runners, which
  cannot reach the corporate governed feed `resolve_toolchain_lock`
  otherwise requires (it fails closed without one, by design, correct for
  every OTHER caller of this tool on a managed machine). Raised to the
  operator rather than silently decided. Resolution: the public runner
  resolves the build toolchain (`setuptools`/`wheel`/`packaging` -- build
  tooling, not a plugin's own distributed runtime dependency closure)
  through public PyPI directly, but pinned to a **seasoned** (minimum-age)
  version rather than unqualified "latest" -- approximating what a real
  governed feed would likely already carry by the time a managed device
  consumes a release built this way, not an exact substitute for querying
  one directly. See `tools/seasoned_public_feed.py` and this Journal's
  2026-10-10 entry. The API/CLI now expose an explicit `seasoned-public`
  build-tooling mode with policy/pins/interpreter-bound reuse and manifest
  provenance. The actual promotion job now invokes this mode explicitly.

## Validation Plan

- [ ] Prove the trusted default-branch promotion workflow builds, signs and
      publishes real artifact sets for the exact merged candidate. Record
      native signer identity, bundle/manifest digest agreement and immutable
      retry evidence; local mocks do not complete this release-lane gate.
- [ ] A clean-cache test proves third-party resolution uses only the
      governed feed; verify the effective `uv`/`pip` configuration
      independently and never log credentials.
- [ ] Feed-lag tests prove the consumer-side admission check blocks
      consumption when an exact selected artifact is unavailable on the local
      governed feed, names the missing artifact, and retains the previous
      generation until the authorized fallback succeeds. Authority tests
      select/lock an admissible source-build closure without mutating the
      artifact lock or using a public index; the consumer cannot invent or
      silently switch a profile. If both paths are blocked, no activation occurs.
- [ ] Feed-admission fixtures present a compatible newer yanked wheel,
      an exact pin to it, and a previously admitted cached wheel subsequently
      invalidated by supplied withdrawal evidence. New acquisition and
      explicitly invalidated reuse exclude refused files; they never select
      an older substitute locally. Unchanged admitted dependencies remain
      reusable without a feed request when no policy/evidence invalidates them.
- [ ] Controlled feed/redirect tests admit only explicitly trusted download
      chains and reject untrusted hosts or insecure redirects before their
      endpoints receive a request. Named-index authentication and OS trust
      settings work without credential exposure in argv, logs or receipts.
- [ ] Real wheel fixtures with direct URL, VCS and external-path requirements
      prove rejection before those targets are acquired or passed to the
      resolver. Altered metadata, mismatched distribution/tag identity and
      forged sidecar hints fail against the actual wheel bytes.
- [ ] Offline subprocess tests poison ambient indexes, caches, overrides and
      backend discovery, and observe zero network requests or source-backend
      execution during assembly. Exact locked closures with extras, markers
      and target-tag differences prove validation cannot invoke a resolver,
      fetch an unlisted dependency or substitute another version inside the
      selected artifact lock. Source-build tests separately prove permitted
      local assembly, governed build-dependency acquisition and refusal of
      unsafe source backends, with no public fallback.
- [ ] Receipt-reuse tests independently change the artifact manifest digest
      in artifact mode, source release identity/digest and fallback trigger
      in source mode, and assembly mode, wheel bytes, target
      interpreter/platform, lock/profile identity and effective policy in
      both modes. Each mismatch refuses reuse before slot activation.
      Matching source-mode reuse needs no artifact manifest; matching reuse
      preserves the selected pins and hashes without trusting ambient caches.
- [ ] Acquisition stress tests independently reach the per-package/total
      acquisition, metadata/wheel byte, temporary-storage and inclusive-time
      limits, including oversized/cyclic lock records and cancellation. Each
      refuses without activation and cleans up partial inputs; completed
      reuse and caller-owned inputs remain intact. Cold/warm measurements
      justify finite default budgets before consumer enablement.
- [ ] Manifest/trust-root tests reject a wrong payload/ABI/platform, an
      altered artifact digest, or a mismatched dependency closure before
      activation.
- [ ] A test distinguishes "no artifact published" (safe from-source
      fallback) from "published artifact fails verification" (fail closed
      with diagnostics, never fall back).
- [ ] Native client tests cover missing assets, discovery/download outage and
      blocked artifact dependencies while source inputs and an admissible
      governed closure remain available: each explicitly falls back, builds
      and health-checks a local venv, records the actual fallback lock/mode,
      independent source identity/digest and trigger without requiring an
      artifact manifest, and activates atomically. Tampered artifacts,
      blocked fallback choices and fallback build failures cannot activate
      or alter the prior slot.
- [ ] Client tests corrupt source payload bytes, source release identity and
      its expected digest independently, with no artifact manifest present.
      Each refuses before any build/backend execution or slot activation;
      source-mode receipts cannot legitimize unverified source inputs.
- [ ] Release tests inject artifact build/sign/upload failure or an
      unavailable/non-matching candidate asset while source validation
      passes: promotion remains failed/incomplete and its PR unmerged.
      A successful run proves exact-candidate production, verification and
      availability before merge; exact-candidate retry cannot overwrite
      immutable assets. Source-validation/provenance failure also blocks.
      Separately, install/update of an already selected verified source
      release succeeds through its governed local-build path with artifact
      acquisition unavailable; no artifact-publication proof is required.
- [ ] Cold/warm source-build and artifact paths report comparable wall
      time, network, and physical storage, measured against Phase 1's
      baseline.
- [ ] Windows and POSIX clean-room install/update tests prove path-correct
      slots, health-gated activation, rollback, and from-source fallback.

## Proposal

### Phase 1 spike evidence (2026-10-02)

**Method:** built real wheels for `agent-bridge` and all 9 of its
materialized `libs/<lib>` path dependencies (`ssh-manager`,
`credential-relay`, `zdd`, `single-instance-lease`, `config-migrate`,
`plugin-resolve`, `agent-procutil`, `dropin-registry`,
`plugin-activation`) via `uv build --wheel` from this `dev`-branch
checkout. Installed the `agent-bridge` wheel with `uv pip install
--find-links <local dist dir> <wheel>` into fresh venvs on a machine
already configured per the managed-machine governed-feed rules
(`uv.toml` default index = `https://packagefeedproxy.microsoft.io/pypi/simple/`).
Compared against installing the same plugin directly from its project
directory (today's real from-source path). All timings are single runs on
one Windows machine (augloop1) - directional, not statistically rigorous.

**1. Governed-feed-only resolution - proven.** The full verbose (`-v`)
install log was searched for every contacted host. Zero matches for
`pypi.org` or `files.pythonhosted.org`. Every third-party dependency
(`fastapi`, `uvicorn`, `starlette`, `h11`, `wsproto`, `pyyaml`, `pydantic`,
`pydantic-core`, `annotated-types`, `anyio`, `click`, `idna`,
`typing-extensions`, `typing-inspection`, `agent-client-protocol`) resolved
through `packagefeedproxy.microsoft.io` → its backing
`*.pkgs.visualstudio.com` Azure Artifacts feed → `*.vsblob.vsassets.io`
blob storage - the full governed chain, never a public index. The 9
first-party vendored-lib names (`agent-ssh-manager`, etc., which do not
exist on any public index) were also checked against the governed feed
first and fell back to the local `--find-links` wheels only once the feed
had no matching name - confirming the governed feed is consulted
uniformly for every name, with no special-cased bypass.

**2. Feed propagation lag - measured, and the "~1 week" assumption does not
hold uniformly.** Compared the governed-feed-resolved version of each
third-party dependency against PyPI's actual release history
(`pypi.org/rss/project/<name>/releases.xml`) as of 2026-10-02:

| Package  | Governed feed resolved | PyPI latest (as of 2026-10-02) | Lag |
|----------|------------------------|----------------------------------|-----|
| fastapi  | 0.141.1 (released 2026-07-29) | 0.142.2 (released 2026-09-30) | ~65 days, 2 minor versions behind |
| pydantic | 2.13.5 (released 2026-08-28)  | 2.13.5 is PyPI's latest stable (2.14.0 betas excluded) | ~0 days |
| uvicorn  | 0.54.0 (released 2026-09-25)  | 0.54.0 is PyPI's latest | ~0 days (7 days old, but current) |

Lag varies from 0 to 65+ days across just 3 real dependencies of one
plugin - it is not a fixed constant tunable with a single universal age
window. This revises the Lag-tolerant version selection Open Design
Question above: Phase 3's installation authority needs actual governed-feed
availability for profile selection or an authorized source-fallback lock;
an assumed age constant cannot substitute for that evidence. Admission
does not re-resolve the selected lock.

**3. Timing and storage - wheel-based install is faster, markedly so
warm.**

| Scenario | From source (today) | First-party wheel (proposed) | Delta |
|----------|---------------------|-------------------------------|-------|
| Cold (no `uv` cache) | 24.4 s | 20.6 s | ~16% faster |
| Warm (`uv` cache populated) | 15.2 s | 5.7 s | ~63% faster (2.7x) |

First-party artifact storage cost is small relative to third-party deps:
the 10 built wheels (`agent-bridge` + 9 libs) total ~0.89 MiB; the fully
installed venv (including all third-party packages) is ~14.8 MiB - so the
content-addressed first-party artifacts this effort proposes storing are a
small fraction of total install footprint, consistent with the "bounded
storage" goal. `agent-bridge`'s vendored libs are small pure-Python
packages, so the cold-case win is modest here; a plugin with heavier
build steps (e.g. anything invoking a native/compiled extension, or
`agent-index`'s much larger footprint per the Context section's baseline
measurements) would be expected to show a larger cold-case delta - not
yet measured directly.

**Not yet done:** exact network request-count/byte deltas (only host
identity was verified, not volume); a POSIX/Linux run (Windows only so
far); and a larger plugin than `agent-bridge` to test whether the cold-case
win grows with build complexity.

## Journal

### 2026-10-24 - Exact-set snapshot component merged; constructor remains open

- #8523 merged as `1c386200bd4297e0bb5321d43185e485f75777bd`.
  The shared helper retains ordinary unlinked inputs as read-only exact copies
  and streams under the existing private temporary-input owner. It shares one
  deadline and explicit count, per-archive, closure-byte and temporary-storage
  limits; snapshot verification composes with the complete-profile verifier.
- Thirty native snapshot controls cover inclusive limits, replacement, mutation
  across the set, growth/read/write failures, final-EOF deadline, cancellation,
  caller-input preservation and actual isolated `-I -S` bootstrap. The combined
  local snapshot/archive/profile/bootstrap/source-binding cohort passed 289
  cases, with one POSIX-only installer case skipped on Windows.
- Final-head review approved with no findings or threads, required CI passed,
  and all four Linux/Windows inspection and real-built/offline artifact jobs
  passed. The first CI refusal for incomplete shared-consumer release metadata
  was corrected with changefiles and a fresh green run, not bypassed.
- The next stretch integrates an explicit closed wheel mode through the existing
  canonical transaction, constructor, installed-origin receipt and health/
  completion owner. Ordinary source-policy checks remain intact; no arbitrary
  record/hash or callback becomes profile/feed approval. Authenticated adapters,
  acquisition, actual offline construction/reuse, source fallback, retention and
  rollout remain open. This effort stays Active and the worktree is retained.

### 2026-10-24 - Bounded inspection component merged; retained assembly next

- #8419 merged as `90663eab5b44d2d63032fa8a0ac95cf016ce8d6d`.
  Its shared stdlib archive verifier is synchronized across all five Python
  consumers. The existing complete-profile API retains exact complete sets,
  actual metadata, closure budgets, strong runtime RECORD hashes and strict
  first-party promotion policy; no production profile consumer was enabled.
- On the reviewed final head, required CI passed all 1,414 selected cases and
  all four Linux/Windows inspection and real-built/offline artifact jobs passed.
  The admitted coverage report is 16,547,830 bytes under the unchanged
  16,777,216-byte cap. Redundant test labels were reduced with all prior cases,
  bodies and assertions retained; the final-EOF regression adds one case.
- The next owned stretch retains the exact selected inputs under explicit
  count/total-byte/storage/deadline budgets, then composes closed offline UV
  assembly and mode/profile/policy/source/artifact receipts through the existing
  constructor and retention owner. Ordinary source-policy refusal of
  `--no-deps` must not be bypassed to obtain an offline transaction.
  Authenticated approval/acquisition, reuse/revocation, source fallback,
  native/fresh construction, retention and authorized rollout remain open.
  The effort stays Active; neither this component nor its merge is completion.

### 2026-10-24 - Shared-installer wheel-input continuation

- Resumed the retained consumer scope after the publication milestone.
  The shared constructor now supplies frozen source inputs, retained UV
  execution, candidate health and construction/retention custody; it does not
  yet realize the selected runtime profile from inspected wheel bytes.
- The [bounded consumer sub-plan](wheel-input-admission.md) precedes its
  implementation. It preserves existing authority/profile and candidate-input
  owners and keeps byte consistency distinct from permission, source fallback
  and service activation. No new runtime loader, global cache or rollout is
  enabled by this proposal. The parent effort remains Active.

### 2026-10-15 - Historical implementation, freshness and publication receipts

- #6697 merged at 01:26:21 UTC as
  `5f871e58301e79f395f76be67f030675efb1e22b`. Published head
  `8f615bc2b426d308f817050830868e683a903462` received review `5526821509`
  with zero findings; [CI run 38663542017](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38663542017)
  passed all 29 actual checks, including Index job `116191357279`.
- The authorized rollout changed only
  `strict_required_status_checks_policy` to `true` on existing main ruleset
  `18553911`. Writable-field readback preserved its other rules, conditions
  and Admin-role `always` repair bypass. This records the configuration
  observed then, not a new policy operation or current ruleset attestation.
- [Old-flow run 38664339697](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38664339697)
  passed build, attestation, promotion and publication using trusted
  workflow/signer source `f327cf44793c60321bc205eddadccaeda3d5ee43` and
  validated dev `4f7b62facbe44df61f159dcde9b1afb11d34b3ea`. #6789 merged
  to main `c33ccea3da5effc6f21b9e8c2ee726f8e4a85902`; the release-health
  observer recorded thirteen non-draft first-party releases with nonempty
  assets targeting that merge. This was old-flow publication, not evidence
  of #6697 inclusion or its pre-merge availability gate.
- [Containing run 38668602887](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38668602887)
  checked out the #6697 merge under trusted main `c33ccea3...`, but Vault
  job `116206228023` failed
  `test_posix_pid_fallback_waits_for_live_owner_and_reaps_dead_one`: the
  waited dead-child probe returned `refused`, not `acquired` (one failure,
  288 passes, ten skips). Artifact build, attestation and publication were
  skipped; the no-op promoter was not release proof. This dated failure
  predates the final merged #6551 repair.
- These historical receipts preserve the implementation/publication
  distinction. They do not establish any subsequent promotion, installed
  rollout or consumer acceptance, and do not complete the broader effort.

### 2026-10-14 - Non-admin merge authority and explicit post-merge verification

- The operator selected an isolated Actions-token squash merge, rather than
  a new application credential or removal of the administrator repair escape.
  The owner PAT still creates the exact candidate branch/PR and publishes
  artifacts; it must never authorize the routine merge. That merge runs in a
  fresh job with no checkout, setup action, downloaded artifact or repository
  code. Server-enforced strict base freshness complements the atomic expected
  head. Its rollout changes the existing main rule in place and preserves its
  administrator escape, rather than creating a blanket no-bypass ruleset.
- A main control repair may advance main beyond the latest published release.
  Publication verifies an exact release snapshot on main; it must not require
  that snapshot to remain the branch tip. Human repairs remain shape-validated
  workflow/control changes, not an exception admitting arbitrary source.
- An Actions-token merge suppresses automatic push workflows. Completion
  therefore owes explicit dispatch and successful immutable-snapshot evidence
  for both existing main-push consumers: CI and Workflow lockdown guard.
  Read-only CI uses the state-derived promotion tag; the check-publishing
  guard runs only from trusted main and verifies target ancestry before
  executing the exact merged implementation. Dispatches pin the expected merged SHA,
  and must fail on a moved tag, wrong run identity, skipped required refresh,
  failed job or deadline. This is a new event contract coordinated with #6237,
  not retrospective proof that its earlier automatic-push lane ran.
- This closes the exact-candidate ordering/identity intent in the existing
  promotion pattern. Consumer artifact use and governed verified-source
  fallback remain optional and independent of publication proof. No live
  policy change or completion claim is made by this design entry.
- Implementation isolates both write-capable orchestration jobs from all
  third-party checkout/setup actions. The dispatch verifier is fetched from
  the immutable trusted workflow commit, not the candidate or a mutable
  branch. Promotion dispatch preserves the complete main-push CI coverage
  and existing bounded tiers rather than selecting the unrelated manual
  exhaustive suites. Its aggregate requires every main-push job to succeed.
- Broad publication/source validation exposed and fixed five new
  contract/fixture failures; the related promotion batch then passed 257 cases and
  exposed only a native test launcher choosing the WindowsApps WSL shim.
  Switching that fixture to the existing native Git Bash selection passed
  all ten actual shell-guard cases. Final merge/dispatch/watchdog contracts
  passed 118 cases, including strict-policy absence, stale base, moved head
  and tag, exact workflow SHA, pagination, skipped coverage and idempotent
  dispatch. The final coherent merge/tag/dispatch/watchdog/fleet selection
  passed 159 cases in 92.56 seconds. Python lint, whitespace and full
  module-size guards passed.
  Hosted exact-head review/checks, containing release, new trusted-flow
  publication proof, strict-rule activation and authorized rollout remain
  open; Windows local evidence is not Linux-hosted evidence.
- Source-owned rebase onto current dev preserves the independently merged
  small/production retention tiers and manual exhaustive budgets. Promotion
  dispatch still selects the current push tiers and 120-second fleet bound,
  not the manual exhaustive lane. The integrated workflow contracts passed
  116 cases; real contained fleet admission/equivalence and both retention
  windows passed three cases without widening their bound.
- Native review found the relocated tag step had lost its former job's Git
  identity and authentication setup. The writer step now configures the bot
  identity and non-persisted GitHub CLI credential helper before the actual
  tagger. A fresh native Git-repository execution of that exact shell step
  creates the annotated remote tag and output receipt with no ambient Git
  identity and no persisted token. That contract and its coupled credential/
  CI-selector checks passed four cases; the Windows changed-path lane now
  selects it as well.
- Native review correctly rejected caller-supplied tag/SHA consistency as
  authority for privileged source-attribution execution. The revised guard
  keeps its definition main-only, independently verifies the exact release
  against the immutable main control snapshot before checkout, and executes
  only that verified release implementation. The coordinator records both
  control and target identities, rather than requiring control HEAD to equal
  the published release. This closes the trusted-admission and compatible
  execution intent in `visions/main-owned-workflow-control/README.md`; it
  does not introduce mutable contribution imports or a new credential.
  The focused trust/metadata/event-policy selection passed 99 cases,
  including executing the literal guard against self-consistent off-main
  targets and tag dispatches, and allowing a newer main control snapshot.
  Live GitHub comparison accepted the immutable main snapshot and refused
  the unmerged PR head with a no-common-ancestor error; no fetched code
  executed. Hosted review/publication proof and strict-rule activation are
  still outstanding.
- Review caught a real post-merge liveness defect: scanning all retained main
  dispatches would exhaust the bounded pagination limit even with the exact
  matching run on the first page. Both workflow lookups now use the owning
  promotion's immutable API `created_at` as an inclusive lower bound, never
  a retry's later start time. A 1,500-row unrelated-history regression proves
  exact-run reuse and duplicate-run refusal; malformed owning-run identities
  or timestamps fail before dispatch. The focused workflow/readiness/retry
  selection passed 119 cases in 75.92 seconds, and touched Python lint and
  whitespace checks passed. A read-only live GitHub query accepted the encoded
  timestamp qualifier and returned no out-of-scope rows. This is API-filter
  evidence, not an actual publication or Linux workflow execution.
  The operational reference now correctly distinguishes the merge job's
  non-admin write-capable Actions token from the owner release PAT.
- The next review had no Medium/High findings. Its useful Low-severity
  documentation correction clarifies that dispatch reuse is scoped to the
  same run-attempt identity. An Actions rerun increments the attempt and
  dispatches new consumers; it still uses the original run creation time
  as the inclusive discovery bound. This does not change the implementation
  or claim reuse of a prior attempt's consumer runs.
- Exact-head hosted CI run `38659619307` exposed a separate measured budget
  failure, not a test failure to bypass: `agent-index` job `116181814124`
  exceeded its eight-minute whole-job limit after every test and cleanup
  step succeeded. The split suites reported 375 passed/23 skipped in 305.64
  seconds and 99 passed in 2.52 seconds; the runner wrapper also reported
  success. Hosted setup and teardown brought the job to 8 minutes 2 seconds,
  and the platform cancellation correctly failed the aggregate gate.
  The index plugin now shares the existing twelve-minute dispatch bound;
  all other plugin job limits and every test/aggregate condition remain
  unchanged. This narrowly fixes the observed setup/test/cleanup allowance,
  rather than retrying an undocumented failure or skipping the required gate.
  A new hosted exact-head run must still prove the correction.

### 2026-10-14 - Candidate-scoped readiness before merge

- Reconciled current #6535/#6632 publisher repair, #6639 boundary-hardening,
  #6237 workflow consolidation and open PR custody before taking the missing
  ordering seam under #4876. The operator now authorizes relevant rollouts
  and requires continuing through an actual clean promotion with every
  artifact-publishing job green. Existing serialized updater and observer
  owners retain their work; no competing updater or release dispatch is
  introduced.
- Implementation retains squash promotion and actual-merge version-tag
  binding. A separate immutable candidate prerelease carries the same
  attested index and complete bundle set before the PR is created or merge
  is enabled. The gate verifies source composition, uploaded metadata and
  every downloaded byte using read-only authority; failure never arms
  merge. Failed candidates cannot poison stable version tags or authorize
  source installation. The final publisher independently rechecks that
  ready set without repairing it, then retains its actual-merge checks.
- Client consumption remains optional and no installer fast path is
  enabled by this slice. Source fallback, lock authority, dependency
  governance, integrity refusals and the open consumer/retention gates
  remain unchanged.
- Initial scoped validation passed 261 artifact/source/publication cases,
  then 34 readiness/merge-wait cases including actual binary subprocess
  transport and a real Git candidate/non-ancestor squash source-binding
  check. The exact-candidate retry addition passed its two native Git
  cases plus the existing protected-main candidate-push contract (three
  cases); touched Python lint and whitespace checks passed. A retry
  preserves the original candidate when only generated time/tag state
  changes and refuses a source change without moving either remote ref.
  Hosted publication and rollout remain open until a normal containing
  promotion proves every publishing job green. Installer clean-room
  validation is not claimed by this pipeline-only slice.
- Implementation PR #6697 is open against `dev`; it is not merged or
  released yet. Final scoped readiness/CLI/merge-wait/attestation and
  trusted-adapter validation passed 46 cases. Native review and required
  CI are pending. The owning session retains actual promotion/publication
  and authorized rollout follow-through rather than treating PR creation
  as completion.
- Its first current-head native review found two real omissions: enabling
  auto-merge did not durably bind a later merge to the verified head, and
  explicit CI selectors omitted the new readiness/retry suites. The fix
  removes auto-merge entirely: the waiter uses read-only authority while
  checking the exact PR head and required contexts, then performs one
  direct squash merge with an atomic expected-head condition. A head or
  policy race, expired deadline or missing write authority fails without
  leaving auto-merge armed. Existing PR lookup is exact and fail-closed.
- Changed-path required CI now includes the full new logic contracts on
  Linux and focused native Git/binary transport contracts on Windows,
  with selector regression coverage rather than an unconditional new
  matrix. The revised readiness/waiter/credential contracts passed 50
  cases; all three changed workflow shell bodies passed native Bash
  syntax validation. Fresh exact-head review and required CI remain open.
- The next native review identified a distinct identity gap: readiness
  selected the remote branch SHA rather than retaining the trusted
  promoter's returned commit. The promoter now appends its actual pushed
  SHA directly to the step output file; readiness rejects a moved or
  deleted ref before any publication and never reselects identity from
  that ref. PR lookup, merge and final publication retain the same
  producer identity. Final binding additionally compares the entire
  candidate and merge trees, including the state/coverage paths excluded
  from wheel identity.
- The resulting 75 scoped cases pass, including executing the actual
  readiness Bash body against moved/deleted refs, direct output-file
  receipt/no-op tests, real Git state-only substitution refusal and the
  existing immutable-publisher contracts. Updated the prescriptive
  promotion pattern and contributor release flow to match direct
  expected-head merging rather than recommending auto-merge.
- A third native review correctly rejected the remaining normalized
  retry adoption itself. The replacement pins the platform's immutable
  run-created UTC time and fixed workflow Git metadata, recomputes the
  entire candidate SHA and accepts only that exact remote SHA. No
  mutable branch contents establish authority. The existing state-derived
  idempotent tagger now names the final tag from the recorded stem,
  preserving the same frozen preparation identity.
- The complete promoter/readiness/retry selection passed 84 cases, with
  one new forged-state fixture checkout corrected and its refusal then
  passing independently. Merge/tag contracts passed; the ten real
  trusted-adapter contracts also passed after their copied trusted-tool
  fixture included the new helper. A counterfeit candidate-side helper
  remains an execution trap rather than a trusted fallback.
- Required Windows CI exposed a separate real 120-second fleet-suite
  limit and enclosing seven-minute cancellation, not supersession. The
  isolated fixture-only repair retains all previously selected tests and
  the 256-row/query/VM assertions, batches read-fixture population in one
  durable transaction, and checks equivalence with actual admission.
  Its owner reports two complete unchanged-budget runs: 598 passed,
  six platform skips and seven existing explicit-tier deselections in
  95.48/98.45 seconds, on local Python 3.14.7, not CI 3.12.10.
  Independent coordinator execution passed all three affected
  equivalence/read/retention contracts. Hosted CI remains the required
  proof of the integrated seven-minute lane; no budget is relaxed.

### 2026-10-14 - Reviewed ordering contract merged; source updater unblocked

- Intent PR #6621 merged at `52067d5e707f9bb48dc0ce496132b390360379db`
  after native Copilot approval of exact head
  `721e71f895a0bd90ad0f7ce3f53d7ba43b74cb8d`, no unresolved threads
  and all required CI contexts succeeding. Promotion-time artifact readiness
  is mandatory before merge; client artifact consumption remains optional.
  This lands reviewed intent, not the pipeline-ordering implementation.
- The sole already-authorized updater reports its normal unified
  source/venv update completed with exit 0 against released source
  `f327cf44793c60321bc205eddadccaeda3d5ee43`, with no first-party
  artifact identity selected and no package-policy override. It separately
  verified installed production file identities and the corrected runtime
  dependency floor. The update retained command-receipt refusal warnings,
  a supervisor-reconciliation warning and a missing optional local tool;
  this is not a universal service-health or clean-room PASS.
- The next implementation slice is the pre-merge readiness/final-binding
  seam with the existing workflow and publisher owners. Reconcile current
  claims and branches before editing; do not duplicate #6535/#6632/#6639
  publisher repairs or #6237 workflow work. Client artifact admission,
  authority-approved locks, fallback isolation, native clean-room proof,
  retention and the remaining validation obligations stay open.

### 2026-10-14 - Promotion must finish artifact production before merge

- The operator explicitly separated mandatory promotion-time artifact
  production from optional client consumption. The prior non-blocking
  source-promotion proposal is superseded before implementation.
- Normal run `38519804724` built and attested artifacts, then merged #6642
  to main `f327cf44793c60321bc205eddadccaeda3d5ee43`. Its post-merge
  publisher `115719534253` failed the same temporary-index removal using
  older trusted tooling at `f5e4e30353a076aec74781e6ef5cfe5df474e939`.
  The ordering allowed source to become current before asset publication.
  #6633 is contained in the new main, but later successful publication
  has not been established here. Existing publisher/workflow custody stays.
- A direct read of the new main's runtime plugins/shared libraries found no
  Python artifact-manifest/admission integration. The reported updater
  owner then confirmed the hold was its coordination prerequisite, not an
  executable artifact rejection: no updater had run against that new main
  and no first-party artifact identity had been selected. The owner removed
  that extra gate and retained one already-authorized update through the
  existing source/venv path; its actual installation result remains pending.
  Publication proof for this campaign is not an agent-only install/update
  prerequisite. This investigation started no second updater, rerun or
  deployed-copy edit.

### 2026-10-14 - Explicit local fallback; artifact publication remains incomplete

- The operator retained exact authority-owned locks, then explicitly required
  a client-side local-venv fallback for artifact/dependency unavailability.
  The proposal now separates the normal artifact lock from an authorized,
  independently recorded source-build lock and revises the Runtime Assembly
  intent before implementation. Neither path may bypass package policy.
- Promotion `38508930963` actually merged source through #6631 to main
  `f5e4e30353a076aec74781e6ef5cfe5df474e939`, then its asset publisher failed
  while filtering a temporary Git index against a different checkout.
  Canonical repair #6633 merged as
  `871dd56f430ee787ff60fa5395992729192fa383`; source-identity hardening
  #6628 is separately owned. No competing repair or manual retry was created.
- The earlier queued `38517616929` was cancelled without jobs. Subsequent
  normal promotions remain owned by the release steward; successful
  post-repair artifact publication and immutable retry are not yet verified.
  Source promotion, artifact publication and installed-runtime rollout are
  separate outcomes, not interchangeable completion claims.
- The plan now requires optional artifact failures to be reported/retried
  independently of validated source-release availability, without weakening
  source, artifact, attestation or immutable-publication checks. This is
  reviewed intent, not a deployed workflow or installer behavior change.

### 2026-10-13 - Native signing reached; publication and consumer admission remain open

- Genuine promotion `38479483177` passed the native/full lanes, first-party
  build and attestation, then failed promotion because the Bridge coverage
  baseline was absent. The already-owned sibling-fixture import repair #6597
  merged normally; no competing repair was published.
- Containing promotion `38484939290` passed all suites, enrolled baselines,
  artifact build and attestation, but its final consumer rejected
  `manifest payload hash differs from candidate`. Run `38487345996`
  repeated that refusal. These are native build/signing evidence, not
  successful release publication or immutable-retry proof.
- Canonical source correction #6604 and its dev workflow mirror #6611 are
  merged. Current issue #6535 separates the already-owned trusted-main
  bootstrap from a bounded source-identity durability follow-through.
  This effort retains broader #4876 custody and does not duplicate either
  repair, dispatch another promotion or authorize an updater.
- #6568's separate Windows landing timeout is now linked to the older
  open PR #5694, which already modifies the exact fixture and owns repeated
  normal-budget acceptance. Its evidence was attached there; the untouched
  investigation workspace finalized normally. The timeout issue remains
  open pending the canonical owner's full-file and complete-portfolio proof.
- The operator delegated consumer-policy selection to the approach most
  likely to work across machine governance settings. The proposed inspected
  wheel/offline lane above avoids requiring new host services or privileged
  network controls, while explicitly naming its source-only limitation.
  Implementation waits for plan review. No artifact consumer, dependency
  policy, installed runtime or shared service was enabled.

### 2026-10-13 - Journal published; separate obligation-fixture claimant retained

- Journal PR #6582 merged as
  `9cbf0441ebfc351de3b3bc24eb7a4a31579cad27` at
  `2026-10-13T22:37:17Z`. Its final head
  `e0b3c2d040a663b5a00dabe12210dd4e5fa668fb` received Copilot approval
  `5510820616` with zero open findings, the earlier status finding was
  resolved, and required CI `38477048301` passed. This publishes progress,
  not a completed artifact or installer gate; the effort remains Active.
- The four obligation-settlement signatures are tracked by #6587 and the
  automatic signature issues #6589 through #6592. A recent, non-indexed issue
  read exposed #6587's claim and its PR #6588 after earlier indexed searches
  had returned no match. Both precede the later umbrella reservation.
  That reservation was released before PR publication, and no competing
  repair was opened. The earlier claimant retains the four-fixture slice.
- Local complete-record/receiving-config fixtures passed the entire
  obligation-settlement and independent-execution-space portfolio: 122
  tests, with unchanged containment and timeout budgets. Ruff F/E9,
  installer-contract checks (13 plugins) and whitespace checks passed.
  The unsubmitted source is retained pending explicit disposition, not reset
  or marked landed. Its local result does not verify #6588's current head.
- Canonical repair #6588 subsequently merged as
  `117476aa6f005e498a69880352c39ea3c0aeed86` at
  `2026-10-13T22:41:34Z`. With the operator's explicit approval, the duplicate
  local commit was preserved on a backup reference before discarding its
  two unpublished files. The duplicate child then finalized normally and its
  parent obligation settled at rest; no competing implementation was landed.
  Automatic promotion `38479483177` is the next native-evidence candidate.
- Native signing/publication proof still awaits a subsequent genuine
  promotion after the canonical repair's merge. Consumer closure/admission,
  shared-engine consumption, cross-platform clean-room validation, measured
  network/storage behavior and retention remain unresolved. No installed
  runtime or shared service was changed.

### 2026-10-13 - Phase 2 integration released; native signing proof pending

- Slice 5 merged as #6332 (`2cbd4035bd55110ebcaaa237f7a77702263798a7`)
  after required CI passed and the current-head review reported no open
  findings. Its source worktree was finalized.
- Promotion run `38354692556` passed its full validation and merged release
  PR #6480, producing main commit
  `c7fa37e39a7d851390d71cb57afd29a25af2569a`. The workflow and publisher
  blobs at that commit match the final integration implementation.
- That run executed an older default-branch workflow, so its successful
  promotion does **not** prove the newly released native artifact lane.
  Dispatched real promotion run `38365819933` from the new trusted main
  commit. The workflow's single-pending concurrency queue cancelled it
  before any job ran when automatic run `38366908719`, using the same
  trusted workflow commit, replaced it. Follow that genuine promotion
  candidate rather than repeatedly dispatching competing runs. Native
  identity/digest/immutable-retry evidence remains open until the new lane
  produces and verifies its actual outputs.
- Run `38366908719` failed before artifact execution: both standalone index
  jobs rejected the old `agent-index-service` CLI name after the dev service
  move, and two Worktrees handoff test doubles lacked the newly required
  configuration keyword. The latter are already repaired by #6494.
  The index compatibility repair subsequently merged as #6522; #6508 repaired
  the separate packaged resolver diagnostic fixtures. Build/sign/publication
  jobs were skipped, so this run supplies no native artifact evidence.
- After #6522 superseded its index repair, the operator selected an additive
  remainder for #6506: preserve the merged parser/index behavior, add only the
  dispatch alias and retain native layered-dependency execution coverage.
  That narrowed PR merged normally as
  `562c0a8ffd35873b5735e2655ffafa3e535cc43d`, following exact-head approval
  with zero findings and green required CI. Hosted Windows and Linux runner
  contracts both passed. The native fixture explicitly proves an isolated
  parent loads pytest outside its interpreter scheme and executes both real
  aliases in canonical contained environments without installing packages.
- Promotion run `38450328906` passed both standalone index lanes, demonstrating
  recovery of the original alias blocker. Its full Worktrees lane failed
  before artifact execution; diagnosis of that current failure remains
  necessary before the native signing/publication gate can complete.
- The Worktrees failures (#6556/#6557) trace to
  `execution_spaces.registry_for_config` dropping its already resolved
  receiving configuration when loading registry overlays. The bounded repair
  forwards that object through the existing loader keyword without weakening
  scalar-load counts or identity fences. Pre-publication deduplication found
  the independently opened #6563 implementing the same repair; coordination
  now preserves that single canonical PR rather than publishing a competitor.
  Its owner completed exact-head approval and required CI, then merged it as
  `f19bcf09d589438c5297ddda3a664ba4e42232a8`; provider confirmation records
  `2026-10-13T20:56:45Z`. Its source worktree is finalized.
- Supplemental local evidence proves actual overlay parsing, malformed-registry
  refusal and a fresh-process machine getter that loads configuration once.
  Packaged wrapper previews retain the real manifest and canonical assets in
  a bounded fixture, checking every packaged wrapper's exact bytes; both
  platform cases pass without longer deadlines. Separating native explain
  and finalize cases preserves both real Git contracts within their item
  budgets. The rebased test-only follow-up is #6570, with no production repair
  or duplicate mock/fallback change. Its contained batch passed 18 tests,
  including the dedicated warning-adapter tests; the landing fixture isolates
  that unrelated installed adapter rather than invoking it from a synthetic
  worktree. Exact-head Copilot approval `5510141064` reported zero findings,
  there were no unresolved threads, and required CI `38469248189` succeeded.
  The follow-up merged as `13fa71a4c4262f1ae28e23fda8adb91bb6e023cb` at
  `2026-10-13T21:27:31Z`; its source worktree is finalized and its parent
  obligation is at rest.
  The full Windows portfolio remains **incomplete**: its first
  three groups passed, but a later attempt timed out in
  `test_a_commit_after_a_sync_still_counts`, now explicitly tracked as #6568.
  This is not a green portfolio
  claim, and no production authority check, release gate or deadline was
  relaxed. Merge sequencing is coordinated with #6563's owner; the original
  repair claims and separate native timeout remain owned.
- The next genuine automatic promotion is `38468423676`. Its completed
  Worktrees job `115541174026` exposes a different six-case fixture signature:
  launch preflight, project-scoping and local-cache fixtures lack the
  receiving configuration's `machine` field required by the execution-space
  fence. The literal job result is six failures, 588 passes and 11 skips;
  build/sign/publication proof is still blocked. The release owner received
  the exact nodes and traceback before any parallel repair was started.
- The six new signatures are #6571 through #6576. A final posting-race check
  found an earlier active consolidated claim on #6576
  (`6155707847`), published after the initial empty-thread reads. The later
  reservations were explicitly released on all six threads before any source
  edit; the duplicate local task was retired and the untouched source worktree
  finalized. The existing claimant retains this fix-forward slice. No second
  repair PR or relaxed release gate was created. Owner coordination and the
  native artifact proof remain open.
- Independent fixture repair #6578 subsequently merged as
  `ea1a1ccd6be92a6335fb544ec40d245875a48ecd` at
  `2026-10-13T21:48:17Z`. The earlier claimant's supported publication flow
  detected that substantive overlap and did not publish a competitor.
  Automatic promotion run `38474138013`, started at
  `2026-10-13T21:55:19Z`, is the next native-evidence candidate; it runs the
  trusted workflow at `9c4d6acade6468e8220692194a62009e1b5ef4e4`.
  Its initial gate and full-tree guards passed, but the completed Worktrees
  job `115562509623` failed at `2026-10-13T22:06:06Z`: four
  `test_obligation_settlement.py` fixtures omit `record.machine` before the
  execution-space mutation fence. That group reports four failures, 422
  passes and five skips. The earlier six-case group is no longer the reported
  blocker; these separate parent-settlement fixtures now block native
  signing/publication proof. Deduplication and ownership coordination precede
  any test-only fix-forward, with the production fence unchanged.
- Consumer entry-point review confirms that the shared installer engine
  currently supplies resilient uv/venv and deployment helpers, not a
  dependency-admission path. Existing governed-feed trust discovery and
  protected named-index configuration are reusable, but an online
  single-index resolution is insufficient evidence: transitive wheel metadata
  can still introduce a direct URL before a post-resolution check runs.
  First-party promotion metadata already rejects direct runtime references;
  the consumer's third-party closure needs an acquisition-time boundary,
  rather than claiming a post-compile URL check prevents those downloads.
  No new installer path or dependency policy was enabled by this review.
- This is released source, not a claim of local runtime rollout or installer
  artifact consumption. Governed-feed closure/admission, shared-installer
  consumption, platform/clean-room evidence and retention remain open.

### 2026-10-12 - Phase 2 slice 5: composed promotion artifact integration

- Slice 4 merged as #6318 (`1703cc42bf8c23182f6a9793d14bd4d56b6d4228`).
  Its final current-head review approved with no open or previously missed
  findings; all five threads were resolved and required CI passed.
- Added an explicit non-pushing preparation path that exports first-party
  bundles from the actual bumped/materialized candidate, using a second
  disposable checkout so backend residue cannot enter the promotion tree.
- The promotion workflow builds/signs separately from `main-promotion` and
  its release PAT. Both the transfer index and every bundle receive native
  GitHub provenance attestations. Verification constrains repository,
  workflow path/digest, source ref/digest and hosted-runner identity, using
  the read-only workflow token rather than requiring new PAT permissions.
- The release writer validates the same dev/main/content-tree and complete
  project set before candidate push, then rechecks the actual merged commit
  before publishing content-addressed assets under plugin/version releases.
  Existing assets are never clobbered. Failed transfer/signing/builds block
  promotion and enter the existing failure reporter.
- Local validation: 379 combined promotion/builder/resolver cases passed,
  including real Git composition and a real candidate subprocess exporting
  two first-party sets through one shared lock without source-tree pollution.
  Touched lint, headless-launch and module-size guards pass.
  Hosted fresh-toolchain/wheel-bundle smoke and actual attestation/release
  evidence remain to be recorded. Managed-device public installation was not
  run; consumer clean-room/install/rollback paths are unchanged and belong
  to Phase 3. Native OIDC signing can only be proved by the trusted
  default-branch promotion job after this workflow reaches that branch.
- Hosted Windows CI caught a fixture-only dependency on an ambient Git
  identity during `commit-tree`. The shared promotion fixture now sets its
  own repository-local identity, and the new contract suite disables
  global/system Git configuration to prove it is independently runnable.
  Production promotion already configures its own job-local identity.
- Hosted real-wheel smoke then caught a manifest-boundary mismatch:
  wheel digests include the builder's `sha256:` prefix, while bundle-file
  transfer digests are bare hex. The bundler now checks the actual wheel
  shape, and fixtures use that same shape with a negative bare-digest case.
- Review follow-through: mirrored the established `tomli` fallback and
  chunked SHA-256 pattern for the Python 3.10 floor. CI exercises real
  import/parsing/hash/export behavior on 3.10. The effort index now agrees
  with Active status; the plan distinguishes implemented pipeline/trust
  work from its still-open native-signing/release validation gate.
- Fast hosted composition exposed a real two-phase tag collision:
  preparation and validation can produce the same commit within one second.
  Preparation now creates no tag/ref; only the subsequent ordinary promotion
  creates its tag. The real subprocess regression checks this boundary.
  Dependency closure/admission, shared-installer consumption, the full
  consumer/platform matrix and durable retention remain open.
- The final integration review found two trust-boundary defects before merge.
  Candidate execution now runs with read-only contents permission in its own
  build job; a fresh default-branch-only signer never imports candidate code
  or runs build backends. Its trusted, non-extracting archive validator checks
  schema-3 identity, the exact declared first-party wheel member set, regular
  member types, bounded uncompressed sizes and every wheel digest before
  signing. The same validator runs before promotion and publication, so an
  attacker-controlled outer ZIP digest cannot conceal malformed contents.
  Focused regressions cover archive mutations with recomputed outer digests
  and a real validator subprocess that must not import candidate tooling.
- A subsequent review tightened this to the complete schema-3 contract:
  trusted validation requires payload hash, aggregate/per-wheel tags,
  first-party source paths, generator, exact toolchain pins and canonical
  source/interpreter provenance. It independently recomputes the lock and
  artifact IDs using existing builder helpers without resolving a toolchain
  or executing a candidate/backend. Fixtures now use complete manifests.
  The publication decision above now names content-addressed bundles rather
  than its superseded fixed per-wheel asset format.
- Source provenance is now independently bound, not merely well-shaped:
  the signer recomposes the exact dev/main candidate with trusted default-
  branch versioning/materialization/projection tools, never importing the
  candidate's own scripts. Trusted discovery checks the declared source
  closure and each distribution/version, and rehashes the composed payload.
  This uses the bumped/materialized candidate rather than raw dev bytes,
  which deliberately differ. Before release writes, publication repeats the
  check against the actual merged tree. Regressions poison candidate
  composition modules and reject changed sources or invented library labels.
- Required CI against the moving dev merge baseline exposed a guard-fixture
  leak: partial-publication dispatch mocked pushes but not the newer
  destination-snapshot Git reads, trying to run Git in the fictional
  `fixture` directory. The in-process guard now supplies bounded synthetic
  remote/config reads while preserving all four recovery-dispatch assertions;
  its isolated runner passes all 21 cases. No production publication logic
  was relaxed.
- The same review's previously-missed Medium finding identified nondeterministic
  ZIP headers under content-addressed filenames. Bundles now sort members and
  manifest wheel records, use fixed timestamps/permissions, and store already-
  compressed wheels without recompression. Identical input bytes remain
  identical despite changed clocks, mtimes, permissions or wheel-record order,
  avoiding a spurious immutable-asset collision on an unchanged rebuild.
- Wheel trust now inspects actual package bytes rather than only declared
  filenames/digests: each nested ZIP requires matching WHEEL generator/tags,
  METADATA distribution/version, safe members and a complete verified RECORD.
  Fixtures are real minimal wheels; opaque bytes, forged metadata, unsafe
  members and broken RECORDs fail before signing/publication.
- Release visibility and tag binding are explicit: draft releases fail,
  annotated/lightweight tags resolve through a bounded reader, and a new
  release's tag is rechecked before a separate asset upload. Existing stable
  plugin/version tags can remain at an older shipped ancestor only when
  their exact first-party source closure and payload still match. Requiring
  every unchanged version tag to point to each latest global promotion would
  contradict the chosen stable-tag/content-addressed-rebuild contract.
- The native Python 3.10 hosted lane caught a compatibility defect in nested
  ZIP verification: that version's SpooledTemporaryFile lacks the seekable
  interface zipfile requires. Wheel bytes now stream into a standard
  TemporaryFile, keeping memory bounded and preserving the real seekable
  file contract on the supported floor.
- The final payload-binding review separated two previously independent
  truths: a source hash and a valid wheel can both be genuine while the
  wheel's installed code is not from that source. Trusted signing/publication
  now compares every payload-file hash with candidate source, requires a
  complete Python module set and declared package data, and derives generated
  entry points from project declarations. The static mapper covers existing
  src packages and declared flat modules; unsupported generated/compiled
  outputs fail closed pending a verified mapping. A separate bounded test
  component covers exact/changed/injected/missing code/data, generated metadata,
  and a fully self-consistent forged wheel that must fail before any release
  API call. Linux, Windows and the native 3.10 CI selectors include it.
- Runtime metadata is source-bound too: trusted validation derives
  Requires-Dist, Requires-Python and normalized extras from static project
  declarations, including optional-dependency markers. Direct URLs and
  dynamic runtime declarations fail rather than bypassing consumer feed
  admission. A hash-pinned, long-seasoned packaging wheel supplies the
  standard PEP parser in isolated hosted validator venvs; provisioning runs
  without release authority and never changes managed-device feed policy.
  The final documentation-impact description now includes the isolated
  agent-worktrees guard-fixture repair and its mandatory changefile.
- The next review tightened operational failure boundaries: missing or
  unreadable wheel inputs report structured bundling errors, and the build
  CLI creates a missing output directory while refusing occupied/invalid
  destinations. Publication verifies the index attestation first, reuses the
  trusted archive validator to require its exact subject set, then verifies
  only those referenced bundles. Extra, missing or unsafe subjects fail
  before bundle verification. The complete artifact suite passes 159 tests;
  native OIDC/publication evidence remains an outstanding validation gate.

### 2026-10-12 - Phase 2 slice 4: explicit public build-toolchain integration

- The default API/CLI path remains governed-only and fails without an
  affirmatively trusted feed. Public promotion runners can explicitly select
  `seasoned-public`; no failure ever switches between policies.
- Public exact pins share one metadata clock and install as binaries from one
  sanitized explicit index. The resolver and existing build-requirement gate
  reject incompatible or insufficient pins without relaxing seasoning.
- Both policies use the existing staging/atomic publication and concurrent
  reuse machinery. Public markers and schema-3 manifests name the real source
  policy; policy, seasoning, pins, and resolved interpreter participate in
  reuse and artifact identity. Reused public pins are rechecked.
- Extracted the CLI/source-policy concerns instead of crossing the module-size
  cap. `docs/pipelines.md` describes the standalone contract. Promotion
  invocation, signing/publication, dependency-closure admission, and verified
  installer consumption remain open.
- Validation: existing builder/resolver contracts plus focused public-policy
  regressions; path-scoped hosted smoke exercises a fresh public toolchain and
  real wheel builds sharing one lock. Public-mode package installation is not
  run on a managed device. Full installer clean-room and external consumption
  matrices remain owned by later phases, which change those paths.
- Review follow-through: schema-3 records reject caller-supplied locks lacking
  valid source/interpreter provenance before wheel building, not merely after
  resolution. Both fixture locks and negative regressions exercise this rule.
  Governed records also reject noncanonical extra fields before serialization,
  including non-JSON values, with an explicit artifact-build error.
  Public hash arrays must be sorted and unique, preserving a single canonical
  identity for each eligible digest set.
  The API uses an omitted-seasoning sentinel, so even an explicit seven-day
  argument is rejected in governed mode rather than silently ignored.
- Age-floor race follow-through: snapshot-qualified file SHA-256 hashes travel
  with exact pins into `uv pip install --require-hashes`. A later upload to the
  same version cannot supply unseasoned bytes; unknown hashes and unpinned
  transitive dependencies fail before publication. File hashes also bind reuse
  and artifact provenance. A real offline uv regression proves rejection of a
  same-version wheel absent from the eligible hash snapshot.

### 2026-10-10 - Phase 2 slice 3: seasoned public-PyPI version resolution for public CI runners

- Identified and resolved a real infrastructure conflict before wiring
  `build_python_artifacts.py` into the actual promotion pipeline (the next
  item in Phase 2's checklist): `resolve_toolchain_lock` requires an
  affirmatively trusted GOVERNED feed and fails closed without one -- by
  design, and correct for every other caller of this tool (a managed
  machine, always governed-feed-reachable) -- but `validate-and-promote.yml`
  runs on **public GitHub-hosted runners**, which have no network path to
  a consumer's private governed feed.
  Raised this to the operator rather than silently picking a resolution
  myself. Decision: the public runner resolves the build toolchain
  (`setuptools`/`wheel`/`packaging` -- build tooling, not a plugin's own
  distributed runtime dependency closure) through **public PyPI directly**,
  but pinned to a **seasoned** version (at least a configurable minimum age
  old, never unqualified "latest") specifically to approximate what a real
  governed feed would likely already carry by the time a managed device
  actually consumes a release built this way -- never an exact substitute
  for querying a real governed feed directly (that stays Phase 3's
  consumer-side install-time design, which has its own real feed to query).
- Implemented `tools/seasoned_public_feed.py`:
  `resolve_seasoned_public_version(package, *, min_age_days=7, now=None,
  fetch=...)` queries PyPI's JSON API (`https://pypi.org/pypi/<package>/json`)
  and returns the newest release whose NEWEST uploaded file is at least
  `min_age_days` old, excluding any release PyPI reports as yanked or that
  parses as a pre/dev release. `min_age_days=7` is a deliberately modest
  default floor for the toolchain specifically -- not derived from Phase 1's
  own measured per-package lag (0 to 65+ days across just 3 dependencies of
  one plugin), which Phase 1 itself found too package-specific to
  generalize into one constant; this is a cheaper, narrower approximation
  for foundational packaging tooling only.
- Deliberately stdlib-only (`urllib.request`, no third-party `packaging`
  import): `build_toolchain_lock.py` already applies this exact constraint
  to itself (`_assert_toolchain_satisfies_build_requires`'s own
  `packaging`-dependent logic runs as an inline script executed THROUGH the
  locked toolchain venv's own interpreter, never imported into the host
  process, so this tool keeps working on a genuinely clean machine with no
  `packaging` installed anywhere yet) -- this module honors the same rule,
  via a narrow, self-contained `_parse_release_version` (recognizes only a
  clean numeric release segment with an optional PEP 440 epoch and/or
  stable `.postN` suffix; anything else, including every genuine pre/dev
  release, is excluded rather than guessed at).
- Age is measured from a release's NEWEST uploaded file, not its earliest
  -- this resolver returns a bare version, not a pinned exact file hash,
  so a later install could select whichever file ends up published for
  that version, including one added after the fact; every file must clear
  the age floor for the seasoning guarantee to actually hold. The real
  network fetch is isolated behind an injectable
  `fetch` parameter so tests exercise the actual selection logic against
  fixed, synthetic responses rather than real (slow, flaky, not
  reproducible) calls to pypi.org.
- Resolver regression coverage (`tools/test_seasoned_public_feed.py`): newest-seasoned
  selection (including a stable `.postN` release), yanked/pre-release
  exclusion (including when they'd otherwise be the newest-and-old-enough
  candidate), newest-file age measurement, the exact min-age-days
  boundary, empty/no-qualifying-release and malformed-JSON/wrong-root-
  shape/missing-`releases`-key/offset-less-timestamp/negative-min-age-days
  fail-closed paths, HTTP/URL error wrapping, a naive (non-timezone-aware)
  `now` rejection, the real `_default_fetch`'s request/timeout
  construction (stubbed at `urllib.request.urlopen`, never touching the
  real network), and `_parse_release_version`'s own parametrized
  accept/reject cases. `ruff check` and `tools/check-module-size.py` both
  pass; no changefile required (this diff touches no `plugins/<name>/`
  tree).
- **Review round found 5 real issues, all fixed before merge:** (1) the
  version parser rejected stable `.postN` releases along with genuine
  pre/dev ones, even though the resolver's own contract only excludes
  pre/dev -- extended the parser to accept an optional `.postN` suffix,
  ordered so a bare release sorts before its own post release. (2) a
  negative `min_age_days` silently disabled the seasoning control entirely
  (and could admit a future-dated upload) -- now rejected up front. (3) a
  non-object JSON root (e.g. a bare list) raised `AttributeError` instead
  of the documented `SeasonedVersionError` -- fixed by checking the root's
  type before calling `.get()` on it. (4) an offset-less upload timestamp
  parsed successfully via `datetime.fromisoformat` but then raised an
  uncaught `TypeError` when compared against the timezone-aware `now` --
  now treated as a malformed timestamp (any file with one disqualifies the
  whole version, never computed from only the files that happened to
  parse). (5) **the most significant catch:** age was being measured from
  a version's EARLIEST file, but this resolver returns a bare version
  string, not a pinned file hash -- a version could look seasoned while
  quietly carrying a brand-new file added after the fact, which a later
  install could select. Switched to measuring from the NEWEST file (every
  file for a version must clear the age floor), updated the docstrings,
  and inverted the test that had codified the old (incorrect) behavior.
- **Second review round found 1 more real issue:** release-segment
  comparison was done on RAW tuples without PEP 440's trailing-zero
  normalization ("1.0" == "1.0.0" == "1" -- a trailing `.0` is never
  significant), so e.g. seasoned `1.0.0` and `1.0.post1` would wrongly
  rank the base release above the post release (`(1, 0, 0) > (1, 0)` as
  plain tuples, even though a post release is newer than its own base
  release under PEP 440). Fixed by stripping trailing zero elements from
  `release_segments` inside `_parse_release_version` itself, so every
  caller gets canonical, directly-comparable tuples; added both a direct
  equivalence-class test and the exact mixed-spelling integration
  regression case the review named.
- **2026-10-12 review follow-through:** validate distribution names before
  any request and normalize them using PEP 503 name normalization, preventing
  path/query/fragment characters from changing the request target. Corrected
  the candidate-key type to include the post-release component. Response-read
  timeouts and malformed Unicode now raise `SeasonedVersionError` with their
  original cause. The existing trailing-zero normalization already addresses
  the carried-over sorting finding. All 49 collected resolver cases pass,
  including real response-read failure handling through the fetch helper;
  touched Python lint passes. Test-count descriptions now refer to coverage
  rather than a count that becomes stale with each review round.
- **2026-10-12 required-CI follow-through:** the artifact-builder path
  gate now includes both resolver files and runs their suite alongside the
  existing builder suite. Age settings must be non-negative integers;
  NaN, infinity, floats, strings and booleans are rejected before fetching.
  The combined lane exposed four Windows fixture failures: drive colons
  were being split as POSIX XDG separators, and root-relative paths then
  resolved on the checkout's drive rather than the temporary drive. The
  fixtures now select the temporary drive and omit drive prefixes from
  their simulated POSIX paths, without changing production feed discovery.
  All 255 combined cases pass locally (56 resolver cases); touched Python
  lint passes. Guiding Intent, the original Request's clarification,
  build-hermeticity policy and umbrella issue #4876 now consistently scope
  the approved public-feed exception to promotion build tooling only.
- **2026-10-12 response-contract follow-through:** truncated HTTP responses
  and connection-reset failures are wrapped with their original cause, alongside
  the existing timeout handling. A file is admitted only when its `yanked`
  field is exactly boolean `False`; missing and wrong-type metadata disqualify
  the whole version. Regression cases exercise response-read failures through
  the real fetch helper and mixed valid/malformed file lists. The earlier
  required-CI finding is already addressed by commit `7364f5232`: both resolver
  paths are in the artifact-builder gate and its test invocation includes the
  resolver suite.
- **2026-10-12 hosted-CI detour:** run `38225329693` exposed a
  Codespaces relay-discovery fixture race: the dead-port probe closed its socket
  before the live relay started, so that relay could inherit the same port.
  Discovery then correctly preferred the newer mapping, but the test mistook it
  for adopting a dead endpoint. The test now holds a bound, non-listening socket
  throughout discovery and asserts its port differs from the live relay.
  The real Bash/Node discovery regression passes under the contained Windows
  runner; a mandatory Codespaces patch changefile accompanies the fixture fix.
  No production relay behavior, assertions, timeouts or retry budgets changed.
- **2026-10-12 Lite-review refinements:** equivalent supported version
  spellings now use their original string as a deterministic secondary sort
  key, rather than depending on response order. `URLError`-wrapped timeouts
  preserve the dedicated timeout diagnostic and original exception cause.
  Clarified the relay fixture's actual allocation order and extracted the
  repeated POSIX-XDG test-path conversion. All 265 combined artifact/resolver
  cases pass locally; the latest resolver has 66 cases. No assertions or budgets
  were weakened.
- **2026-10-12 sibling-fixture follow-through:** moved non-listening-port
  reservation into one context-managed helper used by both Python and wrapper
  discovery, plus the direct dead-endpoint probe. All fixtures retain the
  reservation through probing; the Python discovery case asserts neither live
  relay owns the reserved port. All eight real relay-liveness cases pass under
  the contained Windows runner, with assertions and timeout bounds preserved.
- **Still open (this effort's own next slice):** actually wire this
  resolver into `resolve_toolchain_lock`/`build_python_artifacts.py`'s CLI
  (a caller-selectable "seasoned public" mode, distinct from and never
  silently substituting the existing governed-feed-only default), then wire
  THAT into a new `validate-and-promote.yml` job that builds, attests
  (`actions/attest-build-provenance`, in a job scoped with only
  `id-token: write`/`attestations: write`/`contents: read` -- never
  `APERTURE_RELEASE_TOKEN`, per the Trust root/Credential separation Open
  Design Questions below), and hands the result to `promote` for
  publication as a GitHub Release asset -- mirroring the existing
  coverage-baseline download/gate/publish pattern already in that
  workflow's `promote` job.

### 2026-10-02 - Phase 2 slice 2: shared build-toolchain lock

- Implemented the second Phase 2 slice: resolved the Build hermeticity
  Open Design Question's remaining gap -- every wheel `build_python_artifacts.py`
  builds is now built `uv build --wheel --no-build-isolation` against ONE
  pinned `setuptools`/`wheel` venv, resolved by the new
  `resolve_toolchain_lock(venv_dir, *, python=None)`, instead of `uv`'s
  normal per-wheel isolated PEP 517 build (which silently resolves
  "whatever satisfies the open-floor `requires` today," unrecorded and
  unreproducible run-to-run).
- `resolve_toolchain_lock` creates (or, if `venv_dir` already holds a venv
  from an earlier call, reuses) one venv, installs `setuptools`+`wheel`
  into it via `uv pip install` (governed-feed-only, like every other `uv`
  call in this script), and reads back the EXACT installed versions via
  the venv's own `importlib.metadata`. A caller shares one lock across an
  entire promotion run by passing the SAME `--toolchain-venv` path to every
  per-plugin CLI invocation in that run -- this tool still builds one
  plugin per process invocation; sharing happens by venv reuse, not by
  batching multiple plugins into one call.
- Added `ToolchainLock` (`generator`/`lock_id` properties) and verified,
  per wheel, that its actual `dist-info/WHEEL` `Generator:` line matches the
  locked toolchain's exactly -- a mismatch (meaning `--no-build-isolation`
  did not really use the pinned venv) fails the build closed rather than
  silently recording a drifted toolchain.
- **Manifest schema bumped to version 2:** `build_toolchain` is now a
  structured record (`{"packages": {"setuptools": "...", "wheel": "..."},
  "lock_id": "sha256:..."}`) describing the one lock every wheel in the set
  was built against, replacing schema version 1's ad hoc list of per-wheel
  `Generator:` strings gathered after the fact; `lock_id` (not the old
  generator-string list) now folds into `artifact_id`.
- `build_plugin_artifacts` gained a `toolchain: ToolchainLock | None`
  parameter: when omitted (today's CLI default without `--toolchain-venv`),
  it resolves its own disposable, single-invocation lock internally (still
  a real, pinned, non-isolated build -- just not shared with any other
  call) and tears down the disposable toolchain venv before returning.
- Also added `_governed_feed_configured`/`resolve_toolchain_lock`'s own
  governed-feed-only enforcement (an automated review finding on this
  slice's PR, refined across two rounds): without it, a runner with no
  configured package index at all would let `uv pip install setuptools
  wheel` silently resolve from public PyPI, violating the effort's
  explicit prohibition (umbrella issue #4876). The check specifically
  validates the EFFECTIVE DEFAULT index, not merely "some index is
  configured somewhere": `UV_INDEX` (plural) and a plain `[[index]]` table
  without `default = true` only add a SUPPLEMENTAL index (`uv` still falls
  back to public PyPI for the default), so neither satisfies the gate; and
  an explicit default that just points at `pypi.org`/`pypi.python.org`
  itself is rejected too. Only `UV_DEFAULT_INDEX`/`UV_INDEX_URL`, the
  legacy `index-url` key, or an `[[index]]` entry with `default = true`
  (and a genuinely non-public URL) satisfy it. Reads configuration only,
  never a hardcoded feed URL (this repo stays feed-neutral; see
  `tools/check-feed-neutrality.py`).
- A second review round also found that `resolve_toolchain_lock`'s venv-
  reuse check (`venv_python.is_file()`) treated interpreter EXISTENCE as a
  completion signal: if `uv venv` succeeded but `uv pip install` failed or
  was interrupted, the shared `--toolchain-venv` path would permanently
  look "already built" to every later retry, which would skip straight to
  the (forever-failing) version query -- poisoned until someone manually
  deleted it. Fixed by building into a sibling staging directory and
  publishing it into the real `venv_dir` via a single atomic rename ONLY
  after both `uv venv` and `uv pip install` succeed; a retry after any
  earlier failure finds no `venv_dir` at all and redoes both steps.
- 26 new unit tests (90 total): `ToolchainLock` properties,
  `resolve_toolchain_lock` (venv creation + install, venv reuse/skip,
  venv/install/query failures, malformed-JSON and missing-package fail-
  closed cases, the governed-feed-unconfigured refusal, and recovery from
  a prior interrupted setup), the governed-feed-detection helper itself
  (default-index env vars vs. supplemental-only `UV_INDEX`, `index-url`
  vs. supplemental-only `[[index]]` tables, explicit-public-PyPI rejection,
  both platforms, and a malformed-`uv.toml` fail-closed case),
  `build_wheel`'s `--no-build-isolation`/`--python` command construction
  when a toolchain is given (and that a stray `python=` argument is
  ignored in that case), the generator-mismatch fail-closed path, the
  disposable-lock-resolution-and-cleanup path, and that no staging
  directory is left behind after a successful resolve. All existing tests
  updated to pass an explicit fake `ToolchainLock` (or stub the governed-
  feed check) so no unit test invokes the real `resolve_toolchain_lock`
  (which shells out to `uv`).
- A fourth review round found 4 more real issues against this same slice:
  (1) the governed-feed check's user-level `uv.toml` fallback ignored
  `UV_CONFIG_FILE`'s EXCLUSIVITY in `uv`'s own config resolution -- when
  set, `uv` reads ONLY that exact file, skipping its normal discovery
  entirely (the same exclusivity `plugins/agent-worktrees/scripts/install.sh`
  already handles) -- so a governed user-level `uv.toml` that `uv` itself
  is NOT reading could still satisfy the gate; fixed by making
  `_effective_uv_toml_candidates` resolve to exactly `[UV_CONFIG_FILE]`
  when set, never additionally the user-level path. (2) the public-host
  blocklist omitted `test.pypi.org`, so `UV_DEFAULT_INDEX=https://test.pypi.org/simple`
  passed the gate despite being a public hosted index; fixed by adding it
  alongside `pypi.org`/`pypi.python.org`. (3) `resolve_toolchain_lock`'s
  "publish via rename" step unconditionally deleted any pre-existing
  `venv_dir` first -- two concurrent invocations sharing the same
  `--toolchain-venv` path could both pass the initial absence check, and
  the second would then delete the first's ALREADY-published, possibly
  in-use venv; fixed by never pre-deleting the destination and instead
  catching the `OSError` a rename onto an existing non-empty directory
  raises, discarding the losing publisher's own staging copy and
  deferring to whichever publisher's venv is already there. (4) installing
  bare `setuptools`/`wheel` names enforced no relationship to any specific
  source's own declared `[build-system].requires` floor -- a lagging
  governed feed could resolve a version below what a source itself
  declares it needs (e.g. `setuptools>=84.0.0`), and `--no-build-isolation`
  can never substitute a different version to compensate; fixed by
  `_assert_toolchain_satisfies_build_requires` (using `packaging.requirements`/
  `packaging.version`, already a dependency used elsewhere in `tools/`),
  checked in `build_wheel` before every build when a toolchain is given,
  evaluating applicable environment markers and failing closed on an
  unparseable requirement or an insufficient locked version. 10 more unit
  tests (100 total) covering all four fixes. Re-verified for real:
  `agent-bridge` still builds correctly end-to-end with the full set of
  checks active.
- A fifth review round found 2 more real issues: the governed-feed check
  was a DENYLIST (reject only `pypi.org`/`pypi.python.org`/`test.pypi.org`,
  accept anything else), which would silently treat an arbitrary untrusted
  index (e.g. a public mirror under a different hostname) as "governed"
  just because it isn't one of those three names -- fixed by replacing it
  with an ALLOWLIST: a new, machine-local-only
  `BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS` environment variable (a
  comma-separated hostname list) is now the sole source of trust --
  nothing is considered governed unless this machine's own environment
  affirmatively lists its host, even if the effective default index looks
  perfectly reasonable. **This is a breaking operational change**: any
  machine that was relying on this tool must now also set this variable
  to its own governed feed's hostname, or every `resolve_toolchain_lock`
  call fails closed. Also, `_assert_toolchain_satisfies_build_requires`
  evaluated PEP 508 markers via `Marker.evaluate()` with no `environment`
  argument, which evaluates against the Python process running this
  SCRIPT, not the interpreter actually locked into `toolchain.venv_python`
  -- if `--python` ever selects a different interpreter, a conditional
  build requirement could be skipped or enforced incorrectly. Fixed by
  adding `ToolchainLock.marker_environment` (cached, queried once via a
  tiny stdlib-only script run THROUGH `toolchain.venv_python` itself,
  returning the same keys `packaging.markers.default_environment()` would)
  and passing it to every `Marker.evaluate(environment=...)` call. 6 more
  unit tests (106 total). Re-verified for real (with the new trust-policy
  variable set to this machine's actual governed-feed host) that
  `agent-bridge` still builds correctly, and confirmed the tool now fails
  closed with a clear error when that variable is unset.
- A sixth review round found one more real issue: the marker-environment
  query script computed `implementation_version` from
  `platform.python_version()`, but PEP 508's `implementation_version` is
  properly `sys.implementation.version` (formatted the same way
  `packaging.markers`' own internal `format_full_version` helper does) --
  these coincide on CPython but diverge on alternative implementations
  (e.g. PyPy), where a marker keyed on `implementation_version` could
  silently take the wrong branch despite the fix claiming to evaluate
  against the actually-locked interpreter. Fixed by computing it from
  `sys.implementation.version`'s own `major.minor.micro` (+ a release-
  level/serial suffix for a non-final release) inside the query script
  itself. 1 more unit test (107 total), running the real query script
  end-to-end against the actual interpreter to prove the computed value
  matches `sys.implementation.version` rather than `platform.python_version()`.
- A seventh review round found `tools/build_python_artifacts.py` had grown
  to 1,406 lines across this slice's rounds, exceeding
  `tools/check-module-size.py`'s 1,000-line cap for new/unbaselined files
  -- a real CI gate that would have failed this PR. Split the build-
  toolchain-lock/governed-feed-trust logic (`ToolchainLock`,
  `resolve_toolchain_lock`, the governed-feed trust gate, the marker-
  environment query, and the build-requires enforcement helpers) into a
  new `tools/build_toolchain_lock.py` module (initially 485 lines, now
  609 lines after subsequent review-round fixes; both files have stayed
  under the cap throughout), re-imported and
  re-exported by `build_python_artifacts.py` (968 lines now) so its own
  public API and every existing test's `bpa.<name>` access pattern stayed
  unchanged. One real fix the split itself surfaced: `_governed_feed_configured`
  is now called from inside `build_toolchain_lock.py`'s own module, so a
  test that monkeypatched it via the `bpa` re-export no longer took effect
  there (Python resolves a free variable via the DEFINING module's own
  globals, not the importer's) -- updated the two affected tests to patch
  the real defining module instead. Confirmed `tools/check-module-size.py`
  now passes for this diff.
- An eighth review round found 5 more real issues: (1) the governed-feed
  gate only validated the EFFECTIVE DEFAULT index, but `uv pip install`
  can also consult SUPPLEMENTAL indexes (`UV_INDEX`, or a plain `[[index]]`
  entry with no `default = true`) -- a trusted default alongside an
  untrusted supplemental index passed the gate while the actual install
  could still source packages from the untrusted one. Fixed by pinning
  the install to EXACTLY the one validated URL (`uv pip install
  --no-config --index-url <validated-url>`, with `UV_INDEX`/
  `UV_INDEX_URL`/`UV_DEFAULT_INDEX`/`UV_CONFIG_FILE` stripped from its own
  subprocess environment) instead of merely confirming "some index looks
  fine" and trusting `uv`'s own ambient config to pick the real one used.
  (2) the staging directory name was unique only per PID, so two threads
  in the same process sharing a PID could collide and mutate each other's
  in-progress venv -- fixed with `tempfile.mkdtemp` under `venv_dir.parent`
  for genuine per-call uniqueness. (3) a rename failure during publish was
  unconditionally swallowed as if a concurrent winner always existed --
  a genuine permission/filesystem/invalid-destination error with no real
  winner present would fall through to a later crash launching a
  nonexistent interpreter; fixed to confirm the destination genuinely
  exists before treating the failure as benign, otherwise re-raising as
  `ArtifactBuildError` with the original cause. (4) a documentation-
  process finding: the prior Journal entry's arithmetic was wrong (100 + 8
  ? 106; corrected to 100 + 6 = 106, matching the real measured count).
  (5) the new module's own docstring baked in a PR/review-round reference
  -- corrected to describe only the timeless technical reason for the
  split, per `CONTRIBUTING.md`'s "describe current state, not review
  history" rule. 4 more unit tests (110 total). Re-verified for real:
  `agent-bridge` still builds correctly with the install now explicitly
  pinned to this machine's governed index.
- A ninth review round found 3 more real issues: (1) `_is_public_pypi_url`/
  `_url_host`/`_trusted_index_hosts` compared hostnames without
  normalizing a legally-trailing root dot (`pypi.org.` is DNS-equivalent
  to `pypi.org`, but a raw string comparison treated them as different),
  letting that spelling bypass the public-PyPI denylist -- fixed with a
  shared `_normalize_hostname` helper applied consistently everywhere a
  hostname is compared. (2) `--no-config` only disables config FILES --
  `uv` still honors `UV_EXTRA_INDEX_URL`/`UV_FIND_LINKS` from the
  environment (confirmed common on this repo's own clean-room runners),
  either of which could still supply `setuptools`/`wheel` from an
  unvalidated source despite the explicit `--index-url`; fixed by adding
  both to the stripped-variable list alongside the four already removed.
  (3) `_assert_toolchain_satisfies_build_requires` looked up
  `toolchain.packages.get(req.name)` directly, but PEP 508 names are
  case-insensitive while `Requirement.name` preserves the source's own
  literal spelling (`Setuptools>=90` would miss the lowercase `setuptools`
  lock entry); separately, a genuinely applicable requirement for a
  package this toolchain does NOT lock at all was silently treated as
  satisfied, even though `--no-build-isolation` means nothing installs it
  automatically. Fixed by canonicalizing both sides via
  `packaging.utils.canonicalize_name` before lookup, and failing closed
  (not skipping) when an applicable requirement names an unlocked
  package. 6 more unit tests (113 total); re-verified for real against
  `agent-bridge`.
- A tenth review round found 6 more real issues, several genuinely
  severe: (1) the default (no `--toolchain-venv`) CLI path passed
  `tempfile.TemporaryDirectory()`'s own path directly to
  `resolve_toolchain_lock`, but that directory already exists the moment
  `TemporaryDirectory()` is constructed -- `resolve_toolchain_lock`
  publishes by renaming a staging dir ONTO its given path, which POSIX may
  allow onto an empty directory but Windows `Path.rename()` always
  refuses outright. This meant the tool's own default, flag-less CLI usage
  was completely broken on Windows -- never caught because every real
  smoke test so far explicitly passed `--toolchain-venv`. Fixed by passing
  a non-existent child (`toolchain_root / "venv"`) instead. (2) the `uv
  venv` subprocess still inherited an ambient `UV_VENV_SEED`, which can
  make `uv venv` itself pre-install `setuptools`/`wheel`/`pip` from
  whatever ambient (unvalidated) source it would otherwise use -- the
  later bare `uv pip install setuptools wheel` could then see the
  unconstrained requirement already satisfied and leave that untouched,
  silently bypassing the validated index entirely. Fixed by computing one
  sanitized environment (now also stripping `UV_VENV_SEED`) and passing
  `--no-config` to BOTH the `uv venv` and `uv pip install` calls, not just
  install. (3) `_assert_toolchain_satisfies_build_requires` needs the
  third-party `packaging` library, which this repo declares nowhere as a
  real dependency (present today only transitively via `pytest`) -- a
  genuinely clean machine running this CLI directly would hit a bare
  `ModuleNotFoundError`. Resolved pragmatically for now: catches the
  import failure and fails closed with an actionable message; declaring/
  bootstrapping a real dependency (or performing this check inside the
  already-governed toolchain venv instead) is named as a deliberately
  deferred follow-up, not silently left unaddressed. (4)
  `_effective_uv_toml_candidates` only ever checked the user-level
  `uv.toml` path, missing system-level config entirely
  (`%PROGRAMDATA%\uv\uv.toml` on Windows; `/etc/uv/uv.toml` and
  `/etc/xdg/uv/uv.toml` on POSIX) -- a machine whose trusted governed
  default is configured at one of those levels (this repo's own
  `install.ps1`/`install.sh` already account for exactly these paths)
  would be wrongly rejected as unconfigured; fixed by checking all of
  them, in `uv`'s own precedence order. (5) this effort's own 113 trust/
  concurrency/cross-platform regression tests were never wired into any
  CI workflow, so they gated nothing; added a required
  `tools/test_build_python_artifacts.py` step to `ci.yml`. (6) a
  documentation-process finding: the Journal's own module-size record
  (485 lines) had gone stale as later rounds grew the file further;
  corrected to note both the original and current (609-line) sizes. Also
  discovered and fixed, independent of review feedback: this Journal's
  own directional-arrow corruption (flagged as already-resolved by
  several prior review rounds) had actually been silently REINTRODUCED
  by this session's own SSH-based file-fetch mechanism on at least one
  later round -- traced to a lossy console-encoding path in the fetch
  step, not the review tool's own staleness; repaired again and verified
  via direct remote byte inspection (not just a visual re-read) this
  time. 4 more unit tests (117 total); re-verified for real against
  `agent-bridge` with the new sanitized-environment/system-config-path
  fixes active. This slice has now gone through 10 automated review
  rounds, each finding genuine, progressively narrower issues --
  consistent with this effort's own documented review history on its
  prior slice.
- Real smoke test (not just mocked unit tests): built `agent-bridge` (10
  wheels) then `agent-worktrees` (its own wheel + vendored libs) against
  the SAME `--toolchain-venv` -- both manifests recorded the identical
  `lock_id`, confirming the shared-lock-across-a-run mechanism actually
  works, and every wheel's `Generator:` matched the locked
  `setuptools (84.0.0)` exactly. Full `tools/` suite re-run: zero new
  failures (the only failures are the same ~45 pre-existing, environment-
  specific clean-room/WSL-bash/symlink-sandboxing failures this effort's
  prior slice already documented as unrelated).
- **Not yet done** (named in the Phase 2 checklist): wiring this builder
  into the real `promote_release.py` pipeline; the third-party dependency-
  closure resolution/lag-tolerant selection; and the trust/publication
  contract (attestation, GitHub Release channel). **Next:** pick up one of
  those as the next Phase 2 slice.
- An eleventh review round found 6 more issues, closing out the
  deliberately-deferred `packaging`-dependency gap for real this time: (1)
  **HIGH:** the final `uv build --no-build-isolation` call (and, less
  severely, the toolchain version/marker-environment queries) inherited
  the caller's ambient `PYTHONPATH`/`PYTHONHOME` -- the locked venv
  interpreter still honors those variables, so an ambient module could
  shadow the locked `setuptools` even though the manifest claims the
  locked venv was authoritative. Fixed with a single shared
  `sanitize_subprocess_env()` helper, applied to every subprocess that
  invokes a specific interpreter directly (the build itself, both
  toolchain queries, and venv creation/install). (2) **HIGH:** a shared
  `--toolchain-venv`'s reuse check (`venv_python.is_file()`) proved only
  that an interpreter existed, not that it came from the currently
  validated governed index -- a stale venv built under a prior/different
  trust policy would be silently reused and trusted forever. Fixed by
  publishing a provenance marker (`.governed-feed-provenance.json`,
  recording the validated index URL) atomically alongside the venv at
  publish time; reuse now verifies an EXACT match, and quarantines
  (never deletes) any unmarked or mismatched venv aside before rebuilding
  from the currently-validated index, exactly as if it had never existed.
  (3) `--no-config` does not suppress `UV_CONSTRAINT`/`UV_OVERRIDE`
  (main install) or `UV_BUILD_CONSTRAINT` (source-distribution build
  dependencies) -- any could redirect the locked packages to a direct URL
  regardless of `--index-url`; added to the stripped-variable set. (4)
  closed the deliberately-deferred `packaging`-dependency gap named in
  round 10: rather than declaring/bootstrapping it as a real tool-level
  dependency, `packaging` is now itself one of this toolchain's own
  locked packages (`_LOCKED_TOOLCHAIN_PACKAGES` gained a third entry), and
  `_assert_toolchain_satisfies_build_requires`'s actual parsing/comparison
  now runs as a subprocess THROUGH the locked toolchain's own interpreter
  (a new `_BUILD_REQUIRES_CHECK_SCRIPT`) rather than importing `packaging`
  in the calling process at all -- a genuinely clean machine with just
  Python and `uv` installed now has no dependency gap, closed rather than
  pragmatically worked around. (5) the no-governed-feed-configured error
  message named only the user-level `uv.toml`, even though round 10 had
  already added system-level config-path checking -- updated to mention
  both. (6) a stray control character in `ci.yml` (introduced, ironically,
  by this session's own SSH-fetch-and-rewrite of that file while adding
  the round-10 CI step -- the same lossy-fetch issue already identified
  and worked around for this Journal) had silently replaced the intended
  section symbol (`CONTRIBUTING.md § Code Style`) in a code comment;
  repaired and verified via direct codepoint inspection. 7 more unit
  tests (124 total, all passing); real smoke tests re-verified against
  `agent-bridge`/`agent-worktrees`/`agent-vault`: the shared-`--toolchain-
  venv` reuse path now confirms matching provenance before reporting the
  identical `lock_id`, and the default no-`--toolchain-venv` CLI path
  (the round-10 Windows fix) still succeeds end-to-end with `packaging`
  now present in the recorded `build_toolchain.packages`.
- A twelfth review round found 3 more issues, closing the final credential-
  handling and transport-security gaps: (1) **MEDIUM:** the validated index
  URL may legally carry embedded `user:pass@` credentials (uv supports
  this); persisting the raw value into `.governed-feed-provenance.json`,
  and interpolating it into the quarantine-failure diagnostic, violated
  this effort's own "never log credentials" rule. Fixed with a shared
  `_credential_free_index_identity()` helper, stripping userinfo before
  the value is EVER persisted or displayed -- the raw, possibly-
  credentialed URL is still used for the actual authenticated `uv venv`/
  `uv pip install --index-url` calls themselves, just never stored or
  logged. (2) **MEDIUM:** the hostname allowlist accepted a plain
  `http://<trusted-host>/...` URL, and `UV_INSECURE_HOST` (which disables
  uv's TLS verification for a named host) was never stripped -- either
  path let a network attacker impersonate an allowlisted host while the
  resulting toolchain was still recorded as governed. Fixed by requiring
  an HTTPS scheme in `_validated_trusted_index_url` and adding
  `UV_INSECURE_HOST` to the stripped-variable set. (3) the race-losing
  publisher's rename-failure fallback treated ANY existing interpreter at
  the destination as proof a legitimate winner published there, without
  checking its provenance -- two callers validating DIFFERENT indexes
  could both observe an absent destination before either publishes, so
  the loser could silently trust a venv sourced from the other caller's
  different index. Fixed by requiring the same `_provenance_matches` check
  used by the normal reuse path here too. 8 more unit tests (132 total,
  all passing; zero `F`/`E` markers in the pytest dot stream, cross-
  checked against `--collect-only`'s own count); real smoke test
  re-verified against `agent-worktrees` (fresh build) then `agent-bridge`
  (reuse) sharing the same `--toolchain-venv` against the real governed
  feed -- both manifests recorded the identical `lock_id`, and the
  provenance marker now stores the (credential-free, since this feed has
  none embedded) validated index URL.
- A thirteenth review round found 2 more issues, resolving a genuine
  architectural hazard in the quarantine design itself: (1) **HIGH:** the
  quarantine rename (moving a mismatched venv aside before rebuilding)
  could move a shared toolchain out from under another process actively
  building against it right now -- two callers racing on the same shared
  path could each observe the other's provenance as mismatched and rename
  a live, in-use venv aside mid-build. (2) the reuse identity ignored the
  requested `--python`: a venv built for one interpreter could be
  silently returned for a later call that explicitly asked for a
  different one, running markers/builds against the wrong interpreter.
  **Both resolved by removing the quarantine mechanism entirely**, not
  patching around it: `resolve_toolchain_lock` now NEVER renames, deletes,
  or otherwise disturbs an occupied `--toolchain-venv` path. When the
  shared slot's provenance doesn't match this call's own validated index
  AND requested interpreter (now part of the provenance marker), it
  resolves into a deterministic, content-addressed sibling directory
  keyed on that exact (index, python) identity instead -- built fresh
  there if needed, reused there on a repeat call with the same differing
  identity, and never touching the original occupied path. A mismatch
  found AT that identity-keyed alternate path is a genuine anomaly (never
  an expected race) and fails closed rather than silently rebuilding over
  it. Net +2 unit tests (134 total: 2 provenance-mismatch tests rewritten
  for the new never-touch-the-shared-path behavior, 1 obsolete
  quarantine-specific test removed as no-longer-applicable, 3 new --
  repeat-mismatch reuse, anomalous-alternate-mismatch, and python-identity
  mismatch; all passing). Smoke-tested for real again: built
  `agent-worktrees` (fresh) then `agent-bridge` (reuse, same `lock_id`)
  against the live governed feed; then hand-simulated a provenance
  mismatch on the shared venv and confirmed the rebuild landed in a
  correctly-marked alternate sibling directory while the original
  (mismatched) marker at the shared path was left completely untouched.
- A fourteenth review round found 2 more issues: (1) `_credential_free_
  index_identity` only stripped URL userinfo -- a governed feed using a
  SIGNED URL (e.g. a `?token=...` query parameter) would still persist
  that credential to disk despite the "credential-free" contract. Fixed
  by also stripping the query string and fragment before the value is
  ever persisted or displayed, same as userinfo; the raw URL (including
  its original query/fragment) is still used for the actual authenticated
  `uv` calls. (2) every direct `python -c <script>` subprocess invocation
  (the marker-environment query, the toolchain version query, and the
  build-requires check script) was vulnerable to a current-directory
  `sys.path` injection: a local `json.py`/`platform.py` reachable from the
  subprocess's cwd could shadow the stdlib module the script imports and
  forge its result -- stripping `PYTHONPATH`/`PYTHONHOME` does not close
  this vector, since Python historically prepends an empty/cwd entry to
  `sys.path` for `-c` regardless. Fixed by adding `-I` (isolated mode) to
  all three invocations, which excludes the current directory from
  `sys.path`. Net +3 unit tests (137 total, all passing): one new test
  covering query/fragment stripping, two new tests asserting `-I` appears
  in the version-query and marker-environment-query command shapes (the
  build-requires-check script's `-I` usage is already asserted by the
  existing shared `_dispatch_toolchain_subprocess` test helper used across
  many tests). Smoke-tested for real again: built `agent-worktrees` fresh
  against the live governed feed with the isolated-mode queries active --
  identical `lock_id` as prior rounds, confirming no behavioral regression.
- A fifteenth review round found 2 more issues, the first a genuine flaw
  in round 14's own fix: (1) stripping the ENTIRE query string from the
  persisted/compared identity conflates distinct trusted index URLs that
  merely SHARE a host and path -- a query parameter can select a tenant
  or feed (e.g. `?feed=A` vs `?feed=B`), not just carry a credential, so a
  venv installed from one would be silently considered valid for the
  other. Fixed by replacing the stripped-string comparison with a new
  `_opaque_index_identity`: a one-way hash of the FULL, unredacted URL
  (userinfo, query, and fragment all included), persisted/compared
  instead of any human-readable redacted form -- preserves full
  distinguishing power (no two meaningfully different URLs collide)
  while remaining safe to persist or display, since a hash cannot be
  reversed to recover the original secret. `_credential_free_index_
  identity` (the round-12/14 stripped-string helper) is now reserved
  for human-readable DISPLAY only -- it is no longer used for any
  persistence or identity comparison. A side effect: two validated URLs
  differing only by embedded credentials (not query) are now ALSO
  treated as different identities -- a credential rotation routes to a
  fresh alternate slot rather than silently reusing the old one, which is
  the correct, more conservative direction for a fail-closed contract.
  (2) the occupied-shared-slot check tested only `venv_python.is_file()`,
  so an existing but EMPTY or partially-built directory at
  `--toolchain-venv` (e.g. manually pre-created by an operator, or
  residue from a crashed prior run) was not detected as occupied -- the
  code would then attempt to build directly into it, and the eventual
  publish rename would fail (Windows rejects renaming onto an
  already-existing destination, even an empty one), exactly the class of
  bug round 10 fixed for the *default* no-`--toolchain-venv` path but
  missed for an explicit, pre-existing `--toolchain-venv` path. Fixed with
  a new `_occupied_by_other_identity` check: ANY existing directory that
  is not a complete, provenance-matching venv for this exact identity
  (mismatched, stale/unmarked, OR simply empty/partial) is now routed to
  the alternate slot before staging ever begins, not discovered only when
  the publish rename itself fails. 6 more unit tests (140 total, all
  passing: 2 rewritten for the opaque-hash marker schema and the new
  harsher credential-rotation semantics, 1 new covering the query-string
  tenant-selector regression, 1 new covering the hash's one-way,
  non-reversible property, 1 new covering the pre-existing-empty-
  directory routing, plus removal of the no-longer-accurate "different
  credentials still match" assertion). Smoke-tested for real again: built
  `agent-worktrees` fresh against the live governed feed; then built a
  SECOND plugin against a MANUALLY pre-created, empty `--toolchain-venv`
  directory and confirmed the build correctly routed into a fresh
  alternate sibling directory instead of attempting (and failing) an
  in-place rename.
- A sixteenth review round found 3 more issues: (1) the user-/system-level
  uv.toml check never consulted PROJECT-level config (`uv.toml`/
  `pyproject.toml`'s `[tool.uv]`), which uv itself checks FIRST -- a
  project-only governed default was invisible, and a project-level
  override could be silently skipped in favor of a lower-precedence
  user-/system-level URL. Fixed with a new `_project_uv_toml_candidates`
  that walks upward from the current working directory (stopping at the
  first `uv.toml` or `pyproject.toml` found, exactly like `uv` itself),
  checked before user-/system-level config and skipped entirely when
  `UV_CONFIG_FILE` is set (that variable's own exclusivity). (2) the
  provenance marker recorded the `--python` SELECTOR TEXT, not the
  interpreter it actually resolves to -- reusing the same text (e.g.
  `python3`, or a symlink) after `PATH` reordering or a repointed symlink
  would silently match and reuse a venv built for a now-different
  interpreter. Fixed with a new `_resolve_interpreter_identity`: asks `uv`
  itself (`uv python find`, the same resolution `uv venv --python` uses)
  then resolves any symlink in the result, persisting/comparing THAT
  instead of the caller's raw text. (3) the build-requires verifier
  ignored two meaningful parts of a PEP 508 requirement: a direct URL
  reference (`name @ https://...`) has an EMPTY specifier and trivially
  "passed" against any installed version, and an extras clause
  (`name[extra]`) "passed" without the extra's own dependencies ever being
  installed -- both violate the fail-closed contract. Fixed by rejecting
  both outright in `_BUILD_REQUIRES_CHECK_SCRIPT`. 9 more unit tests (149
  total, all passing). The marker schema change required trimming other
  docstrings/comments to stay under the 1,000-line module cap (985 lines).
  Smoke-tested for real again: built `agent-worktrees` fresh against the
  live governed feed, confirming `uv python find` resolution works
  end-to-end and the marker now records the resolved interpreter path.
- A seventeenth review round found 1 more issue: the venv was published
  (provenance marker written, then renamed into place) BEFORE its
  installed versions were actually validated -- the `importlib.metadata`
  query ran only AFTER publish, outside the build branch. If that query
  failed (malformed output, a missing package), the venv was already
  published with a matching marker, so `_occupied_by_other_identity`
  would treat it as complete and every retry would reuse the same broken
  venv and fail again, rather than rebuilding -- the same class of
  poisoning hazard earlier rounds fixed for the old quarantine design,
  reintroduced via this different structural path. Fixed by extracting
  the query into `_query_toolchain_versions` and calling it against the
  STAGING venv BEFORE the marker is written or the rename attempted; a
  failure there now simply discards the (never-published) staging
  directory, exactly like a `uv venv`/`uv pip install` failure already
  did. A lost publish race still re-queries the actual WINNING venv
  rather than trusting the (now-discarded) staging copy. 1 more unit test
  (150 total, all passing). Trimmed several more docstrings/comments to
  stay under the 1,000-line module cap (993 lines -- this module is now
  within ~1% of that cap; any further substantive finding will likely
  require splitting a piece of it into a third module rather than
  trimming comments further). Smoke-tested for real again: built
  `agent-worktrees` fresh against the live governed feed with the
  reordered validate-then-publish sequence active.
- An eighteenth review round found 1 more issue: the project/user/system
  config-discovery loop (`_effective_default_index_url`) would silently
  `continue` past a higher-precedence candidate that EXISTED but failed
  to parse (malformed TOML, or an unreadable file), falling through to a
  lower-precedence candidate instead -- `uv` itself would not silently
  fall through to a different file either, so a malformed project-level
  `uv.toml` could wrongly let a DIFFERENT, lower-precedence (user-level)
  URL win. Fixed by returning `None` (fail closed) as soon as an existing
  candidate fails to parse, rather than continuing the loop -- a
  `pyproject.toml` simply lacking a `[tool.uv]` table remains a normal
  continue (not malformed, just carries no uv config). 1 more unit test
  (151 total, all passing). Trimmed further to stay under the module cap
  (997 lines -- within 0.3% of the cap; any further substantive finding
  will very likely require splitting a piece of this module out rather
  than trimming comments further). Smoke-tested for real again: built
  `agent-worktrees` fresh against the live governed feed.
- `tools/build_toolchain_lock.py` reached 997/1,000 lines with the
  eighteenth round's fix (no further headroom), and a nineteenth round's
  finding needed new code -- split its "what index does this machine
  trust" concern (project/user/system `uv.toml` discovery, the governed-
  feed allowlist gate, and the credential-free/opaque index-identity
  helpers) into a new `tools/governed_feed_trust.py`, re-imported and
  re-exported from `build_toolchain_lock.py` exactly the way
  `build_toolchain_lock.py` itself is re-imported/re-exported from
  `build_python_artifacts.py`. Every existing caller/test kept seeing the
  same names on `build_toolchain_lock.py`; no test changes were needed
  for the split itself. 997 -> 758 lines; new module 297 lines. Verified
  behavior-neutral: full test suite (151 tests) and
  `check-module-size.py` both passed unchanged before any new code was
  added on top.
- A nineteenth review round found 1 more issue, on top of the split: the
  validated index URL was passed directly in `uv pip install`'s own argv
  (`--index-url <url>`), which (a) makes a credentialed URL visible in
  process listings, and (b) silently discarded a NAMED `[[index]]`
  entry's own `name`, since `--no-config --index-url` supplies only an
  anonymous URL -- a `UV_INDEX_<NAME>_USERNAME`/`PASSWORD`-authenticated
  governed feed would lose its authentication entirely. Fixed by
  threading the index's own `name` through `_effective_default_index_url`/
  `_validated_trusted_index_url` (now returning `(url, name)` instead of
  a bare `url`), and replacing the install's `--no-config --index-url`
  with a minimal, sanitized temp `uv.toml`-shaped file (written next to
  the staging venv, cleaned up in the existing `finally:` block, success
  and failure paths alike) naming just that one validated `[[index]]`
  entry (with `name` when known), pointed to via `UV_CONFIG_FILE` --
  `uv`'s own documented exclusivity for that variable already gives the
  same "nothing else can supply a different index" guarantee
  `--no-config --index-url` was providing, without ever putting the
  (possibly credentialed) URL in argv. `UV_INDEX_<NAME>_USERNAME`/
  `PASSWORD`-shaped env vars are deliberately left unstripped so a named
  index keeps authenticating. 2 more unit tests (153 total, all passing):
  a named entry's `name` surviving through both lookup functions, the
  install argv no longer containing the raw URL for both a named and
  unnamed index, the temp config file's content and cleanup (including
  the install-failure path), and a credential env var surviving
  sanitization. `check-module-size.py` still passes (`build_toolchain_
  lock.py` 798 lines, `governed_feed_trust.py` 310 lines -- both with
  real headroom again). Smoke-tested for real again: built
  `agent-worktrees` fresh against the live governed feed, then
  `agent-bridge` reusing the same `--toolchain-venv` -- identical
  `lock_id` on reuse, confirming the `UV_CONFIG_FILE`-based install
  still resolves the exact same governed index as before, and no temp
  config file was left behind in either the venv or the working tree.
- A twentieth review round found 5 more issues. (1) `UV_NO_CONFIG` was
  left ambient -- `uv` treats it as an equivalent of `--no-config`, so it
  ignores round 19's own `UV_CONFIG_FILE` and falls back to its implicit,
  ungoverned default index; added to the install call's stripped-variable
  set. (2) the sanitized temp index-config file could carry the raw
  validated URL (including embedded credentials) at the process umask's
  default, broader permissions -- `Path.write_text()` creates then writes
  as two separate steps, leaving a window at non-restrictive permissions;
  fixed with a single atomic `os.open(..., 0o600)` + `os.fdopen()` so
  restrictive owner-only permissions apply from the moment the file
  exists. (3) a rename failure caused by a DIFFERENT identity winning the
  publish race (between this call's own occupancy check and its own
  publish attempt) was treated as a hard failure instead of what it
  actually is: a normal race this call's own already-validated staging
  venv can resolve by publishing to its OWN deterministic alternate slot
  instead -- fixed with a new `_publish_staging_venv` helper that
  redirects exactly once (a SECOND mismatch, found at the alternate slot
  itself, remains a genuine anomaly and still fails closed). (4) the
  opaque index-identity hash (`_opaque_index_identity`, since round 5)
  was a BARE `sha256(url)` -- most of a governed-feed URL is predictable,
  so the persisted digest was an offline-crackable verifier for a
  low-entropy embedded password/token even though it could not be
  reversed. Fixed with a new per-machine keyed digest (`hmac` keyed by
  `_provenance_key()`, a random 32-byte key generated once and persisted
  with restrictive permissions under the same per-user state directory
  convention as `tools/_admission_protocol.py`'s own `admission_dir()`)
  -- an attacker without that key file can no longer test credential
  guesses against the persisted digest at all. (5) the new test suite's
  always-on CI step was not path-gated, violating `TESTING.md`'s own
  "gate specialized suites by changed paths" invariant; fixed by adding a
  `git diff --name-only`-based detection step (mirroring this workflow's
  own existing `installation-context` pattern) that gates the step on
  changes to the builder/toolchain-lock/trust modules and their test
  file.

  3 more unit tests (156 total, all passing): a race against a different
  identity redirecting to the alternate slot (replacing the old test that
  asserted the now-superseded fail-closed behavior for that exact
  scenario), a second mismatch AT the alternate slot still failing
  closed, the keyed digest differing by key and never equaling the bare
  hash, and the per-machine key persisting/reusing across calls with
  restrictive permissions (POSIX). An autouse fixture now isolates every
  test in the file from this machine's own real, persisted key material.
  `check-module-size.py` still passes (`build_toolchain_lock.py` 870
  lines, `governed_feed_trust.py` 370 lines). Smoke-tested for real
  again: built `agent-vault` fresh against the live governed feed WITH
  `UV_NO_CONFIG=1` set ambiently in the environment, confirming the
  governed index is still used (not silently bypassed) and no temp
  config file or stray artifact was left behind; confirmed the
  per-machine provenance key file is created under the expected per-user
  state directory.
- A twenty-first review round found 5 more issues plus 2 "previously
  missed" findings in unchanged code. (1) the build BACKEND (which
  executes arbitrary code from the source tree) still saw an ambient
  credentialed index URL or named-index credential env var during the
  final `uv build --no-build-isolation` call, even though that build
  needs no index access at all; fixed by stripping the same package-
  source variables (plus credentials this time) from that subprocess's
  own env in `build_python_artifacts.py`. (2) `0o600` mode bits do not
  establish an owner-only ACL on Windows -- the sanitized index-config
  file could still inherit a broader ACL from its caller-selected parent
  directory; fixed with a new `_restrict_file_to_owner` (strips inherited
  permissions and grants Full Control to only the current user + SYSTEM
  via `icacls`, applied to the file while still EMPTY, before its secret-
  bearing content is written). (3) `_provenance_key`'s `O_CREAT|O_EXCL`
  open made the key file visible (0 bytes) to a concurrent reader before
  the 32 key bytes were written, and a crash in that window left a
  permanently-malformed file every later call kept reading back; fixed
  by writing to a uniquely-named temp file first, then publishing via
  `os.replace` so only a fully-written 32-byte file is ever visible at
  the final path. (4) "previously missed": `_resolve_interpreter_identity`
  probed `uv python find` with the CALLER's own ambient config while
  `uv venv` itself was created with `--no-config`, so the two could
  silently resolve different interpreters; fixed by computing the
  sanitized, `--no-config` environment ONCE (before identity resolution)
  and reusing it for both. (5) "previously missed": a failed probe fell
  back to the caller's raw selector text instead of failing closed --
  exactly the false-match hazard this function exists to prevent (two
  calls whose probe fails for genuinely different actual interpreters,
  but share the same selector text, would silently compare equal); fixed
  to raise instead. The shared package-source-variable strip list and
  credential-variable pattern were ALSO factored out of both
  `build_toolchain_lock.py` and `build_python_artifacts.py` into a new
  `governed_feed_trust.strip_package_source_env_vars` (an opportunistic
  dedup that also recovered headroom `build_python_artifacts.py` needed
  after fix (1) pushed it over the module cap).

  9 more unit tests (165 total, all passing). `check-module-size.py`
  still passes (`build_toolchain_lock.py` 934 lines, `governed_feed_
  trust.py` 409 lines, `build_python_artifacts.py` 995 lines -- the
  latter two now razor-thin on headroom; a further substantive finding
  touching either will very likely require another split). Smoke-tested
  for real again: built `agent-worktrees` fresh then `agent-bridge`
  reusing the same `--toolchain-venv` against the live governed feed --
  identical `lock_id`, confirming the reworked interpreter-identity
  resolution still reuses correctly; confirmed via `Get-Acl` that no
  stray `*.index-config.toml` file was left behind and the provenance
  marker/key files carry the expected owner-restricted ACLs on this
  Windows machine.
- A twenty-second review round found 3 more issues. (1) the HMAC
  provenance key itself was just as credential-bearing as the index-
  config file it protects, but was never hardened via
  `_restrict_file_to_owner` -- fixed by applying it there too, which
  required moving `_restrict_file_to_owner` (and `ArtifactBuildError`,
  which it raises) down into `governed_feed_trust.py` -- the lowest
  layer both the key file (defined there) and the index-config file
  (`build_toolchain_lock.py`, which now imports/re-exports both) need
  it from. (2) empirically, `icacls <path> /inheritance:r /grant:r
  <owner>:F SYSTEM:F` does NOT remove already-inherited ACEs on this
  repo's own machines as the Microsoft documentation for `/inheritance:
  r` implies -- it converts them to explicit entries instead, so
  Authenticated Users/BUILTIN\Users/BUILTIN\Administrators all survived
  the original fix. Fixed by explicitly stripping those well-known,
  locale-independent SIDs afterward, then VERIFYING the final `icacls`
  query output names only the owner and SYSTEM before trusting the file
  as hardened -- a credential file whose ACL cannot be confirmed
  restrictive must never be silently trusted as protected. (3) two
  concurrent first-run callers could both observe a missing provenance
  key and each publish their own via `os.replace`; whichever landed LAST
  silently became the real machine key while the other caller kept
  trusting its own (no-longer-persisted) in-memory bytes, making its own
  provenance computations disagree with every other caller. Fixed by
  re-reading and returning the file's ACTUAL final content after
  publishing, rather than the in-memory key a call itself generated.
  Also fixed the CI path-gate step (round 20) to include
  `tools/uv_editable_ref.py`, which `build_python_artifacts.py` imports
  but the gate's changed-path expression had omitted.

  8 more unit tests (167 total, all passing). Tests that exercise
  `_opaque_index_identity`/`_provenance_key` without otherwise mocking
  subprocess calls now get a file-level autouse default stub for
  `subprocess.run` (this machine's own real `icacls` behaves unreliably
  against paths inside pytest's own tmp tree, the same untrusted-mount-
  point quirk this file's own pytest-teardown workarounds already
  document elsewhere -- unrelated to this fix's actual correctness,
  separately verified via real smoke tests against normal paths).
  `check-module-size.py` still passes (`build_toolchain_lock.py` 883
  lines, `governed_feed_trust.py` 542 lines). Smoke-tested for real
  again with a freshly-generated key: `Get-Acl`/`icacls` now confirm
  only `NT AUTHORITY\SYSTEM` and the owning user have access to the
  provenance key file, with no `BUILTIN\Administrators`/`Authenticated
  Users`/`Users` entries surviving.
- A twenty-third review round found 1 more new issue, plus re-flagged
  round 22's key-publish-race fix as still insufficient. (1) the final
  ACL verification used SUBSTRING matching, so an inherited explicit ACE
  like `DOMAIN\svc-backup` was wrongly accepted when the real owner was
  `DOMAIN\svc` (a substring of it), and any principal merely containing
  the word "system" was accepted as `NT AUTHORITY\SYSTEM` -- silently
  certifying a credential-bearing file as owner-only while another
  principal could still read it. Fixed by parsing the EXACT principal
  name from each `icacls` ACE line (everything before `:(`) and
  comparing it, normalized, against the owner and the literal
  `"nt authority\system"` -- never a substring check. (2) round 22's
  "re-read the final persisted value" fix for the key-publish race was
  judged insufficient: two callers could still each generate and publish
  a genuinely DIFFERENT key, with whichever `os.replace` landed last
  silently becoming the real one -- the other caller had already
  returned (and could already be persisting provenance keyed on) a value
  no longer on disk by the time it would have re-read. Replaced with a
  real lockfile (`<key-path>.lock`, `O_CREAT | O_EXCL`) that serializes
  first-run creation: a caller re-checks for an existing key AFTER
  acquiring the lock (another caller may have published while this one
  waited), so AT MOST ONE caller per machine ever actually creates the
  key -- every other caller, racing or not, reads back that exact same
  one. Bounded by a generous, now test-overridable timeout so a crashed
  lock-holder cannot wedge every future caller forever.

  4 more unit tests (170 total, all passing): two tests proving the
  substring-match ACL-verification bug is closed (a similarly-named
  principal, and a principal merely containing the word "system"), a
  REAL-THREAD test with 4 concurrent callers converging on one identical
  key via the actual lockfile (not a mocked race), and a bounded-timeout
  test for a crashed/abandoned lock. `check-module-size.py` still passes
  (`governed_feed_trust.py` 591 lines). Smoke-tested for real again with
  a freshly-generated key: `icacls` still confirms only `NT
  AUTHORITY\SYSTEM` and the owning user have access.
- `tools/build_toolchain_lock.py` hit 997/1,000 lines with no headroom.
  Split its "what index does this machine trust" concern (project/user/
  system `uv.toml`/`pyproject.toml` discovery, the trust-policy
  allowlist check) into a new `tools/governed_feed_trust.py`, re-
  imported/re-exported from `build_toolchain_lock.py` exactly like that
  module is re-imported from `build_python_artifacts.py` -- a component
  boundary, not a reusability concern. Verified behavior-neutral (151
  tests) before adding anything new.
- A twenty-fourth review round found 1 new issue plus 3 previously
  missed. (1) `_restrict_file_to_owner`'s owner resolution used the
  `USERDOMAIN`/`USERNAME` environment variables -- ordinary, caller-
  controlled process environment, not an authenticated property of the
  process token; a modified environment could make the hardening grant/
  verify against a forged principal. Fixed with a new
  `_current_token_identity` helper resolving the REAL current process
  token's account name and SID via `whoami /user /fo csv /nh`, never
  `os.environ`; `icacls` now grants by the resolved SID, and the final
  verification compares `icacls`'s own resolved display name, sourced
  from that same OS-backed identity. (2) `_project_uv_toml_candidates`
  stopped its upward walk at ANY `pyproject.toml`, even one with no
  `[tool.uv]` table -- `uv` itself ignores such a file and keeps
  searching parents, so a child/leaf package's own plain
  `pyproject.toml` could shadow a real parent project's index. Fixed
  with a `_pyproject_declares_tool_uv` peek before stopping the walk.
  (3) the POSIX system-level config tier checked `/etc/uv/uv.toml`
  before `/etc/xdg/uv/uv.toml` unconditionally, never consulting
  `XDG_CONFIG_DIRS` at all -- now honors `XDG_CONFIG_DIRS` (colon-
  separated, in listed order; defaulting to `/etc/xdg` when unset/empty)
  before falling back to `/etc/uv/uv.toml`, matching uv's own
  `locate_system_config_xdg`/`system_config_file`. (4) two stale
  comments still referenced the removed round-13 quarantine design --
  reworded to state only the current invariant.

  11 more unit tests (181 total, all passing). `check-module-size.py`
  passes (`governed_feed_trust.py` 688 lines, `build_toolchain_lock.py`
  883 lines, `build_python_artifacts.py` 995 lines -- razor-thin, ~5
  lines of headroom). Smoke-tested for real again against the live
  governed feed, confirming via `Get-Acl`/`icacls` that the provenance
  key and index-config files carry genuinely owner-only ACLs.
- A twenty-fifth review round found 5 more genuine issues, plus
  confirmed 3 stale review threads already resolved (round 20's HMAC
  keying; the pre-existing CI gating for this test module) rather than
  re-fixing them. (1) a TOCTOU race: the occupied-by-other-identity
  check ran BEFORE interpreter-identity resolution (itself a real `uv
  python find` subprocess), leaving a window where a concurrent,
  different-identity publisher could populate `venv_dir` during that gap
  and get silently reused. Extracted into `_target_dir_for_identity`,
  now called as the LAST thing before the reuse/build decision. (2) the
  credential-bearing index-config temp file was a SIBLING of the
  staging venv directory in the caller-selected (possibly multi-
  principal-writable) `target_dir.parent`, reopened by pathname after
  hardening -- another local principal there could rename/replace/
  symlink its path before `uv` opened it via `UV_CONFIG_FILE`. Fixed by
  hardening the STAGING DIRECTORY itself right after creation and moving
  the config file inside it (removed explicitly before the directory is
  renamed to publish, or it would be published right along with it).
  (3) `whoami`/`icacls` were invoked by bare name -- resolved through the
  current directory/`PATH`, where a substituted executable could forge
  the identity or no-op the hardening. Both now resolve
  `%SystemRoot%\System32\<tool>.exe` via a new `_trusted_system32_tool`
  helper, failing closed if missing. (4) `_pyproject_declares_tool_uv`
  treated a malformed/unreadable `pyproject.toml` the same as one with
  no `[tool.uv]` table, silently certifying a DIFFERENT parent project's
  index -- `uv` itself errors on a malformed one instead; now
  distinguishes the two and fails closed. (5) 4 re-exported-only names
  tripped this repo's required `ruff check --select F,E9` (F401) --
  marked with `# noqa: F401`. Fixing (5) revealed this test module's own
  CI step had never actually been REACHED in 24+ prior rounds (the
  `guards + lint` job always failed earlier at this same lint step
  first); running it for the first time caught one further, pre-
  existing, genuinely cross-platform test bug (a Windows-literal-path
  assertion that never matched on Linux CI) -- fixed alongside. Live
  smoke testing (not the automated review) then surfaced two more real
  bugs in the new directory-level hardening itself: `OWNER RIGHTS` (a
  dynamic per-object principal directories inherit by default) needed
  adding to the broad-principal strip list, and combining
  `/inheritance:r` + `/grant:r` in one `icacls` call does not reliably
  produce an inheritable ACE for the granted principal on a directory --
  fixed by adding explicit `(OI)(CI)` inheritance flags to the grant.

  10 more unit tests (191 total, all passing). `check-module-size.py`
  and `ruff check --select F,E9` both pass. Smoke-tested for real again
  against the live governed feed: `resolve_toolchain_lock` succeeds, the
  index-config file leaves no leftovers anywhere, and the published
  venv's own ACL is hardened to only the resolved identity and `NT
  AUTHORITY\SYSTEM`.
- A twenty-seventh review round found 4 more issues (all 4 of round
  26's confirmed resolved). (1) `_trusted_system32_tool` resolved the
  system directory from `%SystemRoot%` -- itself caller-controlled
  process environment an attacker could repoint at a directory with
  substituted `whoami.exe`/`icacls.exe`, recreating the exact bypass
  this helper exists to close. Replaced with `_system_directory`,
  calling the `GetSystemDirectoryW` OS API directly. (2) the ACL
  verifier hardcoded the English display name `"NT AUTHORITY\SYSTEM"`,
  and the grant used the literal name `"SYSTEM"` -- both localized on a
  non-English Windows installation. Now grants by the well-known
  `S-1-5-18` SID directly and resolves ITS OS-reported display name via
  `_well_known_sid_display_name` (`LookupAccountSidW`/
  `ConvertStringSidToSidW`). (3) a crashed lock holder left
  `_provenance_key`'s own `O_EXCL` lockfile on disk forever -- every
  call after the original timeout would wait out the same timeout and
  fail closed, permanently. Added a PID-recording, liveness-checking
  stale-lock reclaim path. (4) reworded 7 comments narrating this
  effort's own prior review rounds/revisions instead of stating only the
  current invariant.

  5 more unit tests (200 total, all passing). Smoke-tested for real
  again against the live governed feed.
- A twenty-eighth review round found the round-27 stale-lock fix (3,
  above) was itself NOT atomic: two waiters could both observe the same
  dead PID, both unlink the stale lock, and the second could then also
  unlink the first's brand-new, genuinely live replacement purely by
  pathname -- a real two-thread reproduction against that design
  converged on two DIFFERENT published keys instead of one. Replaced the
  entire manual lockfile-plus-liveness-check scheme with a real OS-
  backed, genuinely exclusive lock (`_provenance_key_lock`: a named
  Windows mutex via `CreateMutexW`, or a POSIX `flock` advisory lock) --
  the OS itself owns exclusivity and releases it automatically the
  instant the holding process exits for ANY reason, including a crash,
  so there is no separate "is the old holder still alive" question to
  answer or race to get wrong; `_process_is_alive` and the PID-recording
  scheme were removed entirely as no longer needed. Also added a
  dedicated, path-gated `windows-python-artifact-builder` CI job
  (`.github/workflows/ci.yml`) running just the 3 genuinely Windows-OS-
  API-only tests at the time (selected via a new `windows_only` pytest
  marker; a 4th, the round-30 `LockFileEx` contention test, was added
  later -- see below) that the Ubuntu `guards + lint` job's own tests
  skip themselves out of -- those production paths had never actually
  executed in required CI at all before this. Reworded the remaining
  review-round labels (round 19 through round 27) throughout
  `tools/test_build_python_artifacts.py`'s
  own comments to timeless technical descriptions, keeping this Journal
  as the sole place that retains the historical round-by-round mapping.

  2 fewer, net, unit tests (198 total, all passing): removed
  `_process_is_alive`'s own 2 dedicated tests and the now-invalid
  stale-lock-reclaim test (replaced by a stale-lock-file-does-not-block
  regression and a genuinely-held-lock-times-out test, using a real
  background thread rather than a faked lockfile). `check-module-size.py`
  and `ruff check --select F,E9 --output-format=github .` both pass
  clean. Smoke-tested for real again against the live governed feed,
  including directly exercising the real OS mutex/flock acquisition.
- Round 28's push broke the required `guards + lint` Ubuntu job again:
  the new `windows_only`-marker split revealed that tests forcing
  `sys.platform == "win32"` to exercise Windows-shaped code on the real
  Linux runner now hit genuine `ctypes.windll.*` calls, and
  `ctypes.windll` does not exist as an attribute at all on non-Windows
  platforms (unlike `fcntl`, a cleanly-absent importable module). Fixed
  by branching `_provenance_key_lock` on `hasattr(ctypes, "windll")`
  (actual capability) instead of `sys.platform` (spoofable), and by
  mocking `_well_known_sid_display_name` directly at the handful of test
  sites that restore the real `_verify_restricted_acl` but run on Linux.
  198/198 tests pass; `guards + lint` finally went green END-TO-END for
  the first time in the PR's history (~12.5 min run, every step
  including the late `ruff` step actually reached and passed).
- A twenty-ninth review round (generated against the round-28 commit,
  predating the ctypes.windll fix above) found one more genuine issue:
  the named Windows mutex (`Local\...`) used by `_provenance_key_lock`
  is scoped to ONE Terminal Services session, so two processes for the
  same account in different sessions would each wait on a DIFFERENT
  mutex object, reopening the exact race this lock exists to close.
  Replaced the named mutex with a `LockFileEx` byte-range lock on the
  lock file itself -- visible host-wide regardless of session, with no
  separate kernel-namespace ACL to get right, and still released
  automatically by the OS the instant the holding handle closes for any
  reason. The round's other finding (a stale "Round 25 moved..." test
  comment) was already resolved by the ctypes.windll-fix commit above,
  which this review predates. 198/198 tests pass, ruff/module-size
  checks clean, and smoke-tested genuine cross-PROCESS exclusion
  directly: a parent process holds the lock, a real child process
  blocks until the parent releases, then acquires immediately.
- A thirtieth review round found two issues in the required
  `windows-python-artifact-builder` job itself. (1) It used a bare
  `pip install pytest` instead of `uv`, violating this repo's own
  dependency-tooling rule; replaced with `astral-sh/setup-uv` +
  `uv run --with pytest`, matching this file's other Windows jobs. (2)
  More substantively, that job did not actually gate the round-29
  `LockFileEx` fix at all: its only `windows_only` tests covered
  System32/SID lookup, while the lock's own concurrency tests are
  unmarked, same-process, cross-THREAD tests -- they only ever ran in
  the Ubuntu job (exercising the `fcntl` branch, never `LockFileEx`) and
  couldn't have exercised the cross-session gap even running on
  Windows, since two threads in one process share a session by
  definition. Added a new `windows_only` test spawning a genuine CHILD
  PROCESS to hold/contend for the lock, proving real OS-level
  `LockFileEx` exclusion end-to-end, and wired it into the required
  lane.

  1 more unit test (199 total, all passing; 3 still skipped on Ubuntu,
  as before -- the new test runs for real on Windows).
  `check-module-size.py` and `ruff check --select F,E9
  --output-format=github .` both pass clean. The exact `-m windows_only`
  pytest invocation (matching what `uv run --with pytest` ultimately
  executes) passes all 4 Windows-only tests on a real Windows machine;
  the `uv run` wrapper itself couldn't be locally verified due to an
  unrelated broken `uv` shim on this dev machine, so real CI is the
  authoritative check for that one step.
- A thirty-first review round found two issues. (1) A stale journal
  count (still said "3" Windows-only tests above, after round 30 added
  the `LockFileEx` contention test as a 4th) -- corrected. (2) More
  substantively, a genuine bug that an earlier round's fix had left
  half-done: `resolve_toolchain_lock` resolved `python_identity` via
  `_resolve_interpreter_identity`/`uv python find` for its PROVENANCE
  MARKER, but then created the venv with the caller's raw `python`
  selector text in a SEPARATE `uv` invocation -- if PATH was reordered
  or a selector symlink was repointed between the two calls, `uv venv`
  could create the environment under a DIFFERENT interpreter than the
  one just probed, while the marker still recorded the earlier-resolved
  identity, letting a later caller reuse a mismatched venv under false
  provenance. Fixed by always passing the already-resolved, absolute
  `python_identity` to `uv venv --python`, never the raw selector text
  (kept at the same `--no-config <dir> --python <value>` positional
  shape so the ~25 existing tests asserting the staging directory stays
  at a fixed argv position needed no restructuring, only the VALUE
  changed).

  199 tests total (unchanged -- no test added/removed this round, only
  a real bug fixed and existing assertions re-validated against the new
  value), all passing. `check-module-size.py` and `ruff check --select
  F,E9 --output-format=github .` both pass clean. Smoke-tested for real
  against the live governed feed: confirmed the actual `uv venv`
  subprocess invocation receives the fully-resolved absolute
  interpreter path via `--python`, not a bare selector, and that the
  resulting toolchain lock resolves correctly end-to-end.

### 2026-10-02 - Phase 2 slice 1: `tools/build_python_artifacts.py` (wheel + manifest build)

- Implemented the first real Phase 2 slice: a tool that builds a plugin's
  own wheel plus every vendored `libs/<lib>` wheel it needs (via `uv build
  --wheel`), enumerating the vendored set by reusing
  `uv_editable_ref.find_uv_editable_refs` recursively (the same primitive
  `materialize_main.py` already uses, so artifact coverage and the
  materialized tree can never disagree about which libs are in scope), and
  validating each discovered reference with `uv_editable_problems` -- the
  same acceptance check materialization itself applies -- so this tool can
  never build or describe a source materialization would have refused.
- Manifest fields: `payload_hash` (a working-tree content hash of the
  plugin dir + every vendored lib dir, order-independent -- deliberately
  NOT `git HEAD`, since promotion builds from a scratch tree already
  mutated by version bumps/materialization before the build runs), each
  wheel's `python_tag`/`abi_tag`/`platform_tag` (parsed from the wheel
  filename itself -- the canonical, self-describing source, never guessed
  from the running interpreter, with a hard failure on a genuinely
  conflicting tag across the wheel set), each wheel's sha256, and the
  build `Generator:` actually read from each wheel's own `dist-info/WHEEL`
  (required -- an unreadable/missing one fails the build). All of it,
  including every wheel's own digest, folds into one `artifact_id`.
- Automated PR review (`ThomasMichon/copilot-extensions#4961`) found 8 real
  issues in the first draft, all fixed before merge: vendored-reference
  discovery accepted what materialization would reject (fixed by reusing
  `uv_editable_problems`); the payload hash used `git HEAD` instead of the
  actual working tree the build reads (fixed with a direct content hash);
  the wheel-filename parser mis-parsed an optional PEP 427 build tag
  (rewritten as right-to-left tokenizing instead of a single backtracking
  regex); conflicting platform/ABI tags across a wheel set were silently
  resolved by whichever wheel came first (now a hard failure); a missing
  `Generator:` was silently recorded as unknown (now a hard failure); the
  `artifact_id` omitted the wheels' own digests (now included); and two
  documentation-process findings (keep the Phase 2 plan item open until
  pipeline-wired; add the required Documentation impact statement).
- 33 unit tests (`tools/test_build_python_artifacts.py`), all green;
  confirmed zero regressions against the rest of `tools/`'s suite (the only
  failures in a full `pytest tools/` run are 45 pre-existing,
  environment-specific `clean-room`/WSL-bash failures unrelated to this
  change). Smoke-tested for real against `agent-bridge` repeatedly across
  every review round: built all 10 wheels (the plugin + its 9 vendored
  libs), every one reporting `setuptools (84.0.0)` as its actual generator,
  manifest written correctly.
- A second review round found 2 more issues (1 real, 1 stale): `build_wheel`'s
  new-wheel detection diffed `out_dir`'s own filenames before/after, which
  would silently see "0 new wheels" on a second invocation rebuilding the
  exact same filename (an identical version rebuilt again, or a retry
  after a later manifest step left a same-named wheel behind) -- fixed by
  building into a fresh temporary staging directory every time and moving
  the single result into `out_dir` (overwriting deliberately), with a new
  regression test and a direct double-invocation smoke test against
  `agent-bridge` confirming it. The "add a Documentation impact statement"
  finding was already addressed in the PR description by the time of this
  round -- the review tooling diffs file content, not the PR body, so it
  could not see that fix; left as a reviewer-visible non-issue rather than
  a code change.
- A third review round found 3 more issues: the payload-hash serialization
  joined `"path:digest"` strings with a plain separator, which is
  ambiguous for pathological filenames (two different `(path, digest)` sets
  can serialize identically) -- fixed with a shared `_hash_fields` helper
  that length-prefixes every field before hashing, applied consistently to
  `directory_content_hash`, `compute_payload_hash`, and `artifact_id`
  itself (which also now folds in every wheel's filename+digest the same
  unambiguous way); `payload_hash` was computed AFTER every wheel had
  already been built, so a build backend leaving residue inside the source
  tree (e.g. setuptools' `build_meta` creating a `*.egg-info` directory
  alongside the sources) would be folded into the identity of the very
  input that produced it -- fixed by computing it before the first build,
  with a regression test simulating exactly that residue; and
  `read_wheel_generator` decoded `dist-info/WHEEL` with `errors="replace"`,
  which would silently accept corrupt metadata as a known toolchain --
  fixed to fail closed on invalid UTF-8. The stale "Documentation impact"
  finding and a stale test-count note recurred across rounds for the same
  PR-body-vs-file-diff reason noted above; both are now also reflected in
  this Journal entry's own diff.
  **Documentation impact:** this effort doc is the sole documentation
  surface for `tools/build_python_artifacts.py` (a new, standalone tool);
  no other repository documentation describes it, and `TESTING.md` does
  not enumerate individual `tools/test_*.py` files, so it remains accurate
  without changes.
- A fourth review round found 1 more real issue and reiterated 1 carried-
  over, non-blocking one: `*.egg-info`'s directory name varies per package
  (unlike the fixed names already ignored), so a build's residue left
  behind from one invocation was still being hashed as part of the NEXT
  invocation's "pre-build" tree -- fixed by ignoring any directory
  component ending in `.egg-info` (mirroring `uv_editable_ref._file_hashes`'s
  own identical exclusion), with a repeated-invocation regression test.
  The carried-over "validate wheel tags semantically, not by string
  equality" finding is real but out of proportion to this slice: resolving
  it needs genuine PEP 425/600 compatibility-class logic (`abi3` forward
  compatibility, manylinux platform-tag hierarchies), and no plugin/lib in
  this repo ships anything but a pure-Python wheel today. Recorded as a
  new, named Open Design Question above rather than solved speculatively,
  matching this effort's own established pattern for genuinely deferred
  work.
- A fifth review round found the most substantial gap yet, confirmed by a
  direct smoke test against `agent-worktrees` (not just `agent-bridge`):
  discovery only ever looked for the dev-branch LIVE, escaping,
  `editable = true` canonical-reference form -- so it found nothing at all
  for a plugin that vendors libs in-tree by design, AND would find nothing
  for ANY plugin once promotion's own `materialize_main.py` has rewritten
  every live reference into exactly that in-tree form (the actual state
  this tool runs against during real promotion). Fixed by classifying each
  `[tool.uv.sources]` entry by its `editable` marker rather than by whether
  it escapes the immediate consumer's own directory: an `editable = true`
  entry is the dev-branch live form (`find_uv_editable_refs`, validated
  against the real top-level plugin only); anything else whose path
  resolves into a `libs/<lib>` directory is an in-tree vendored copy
  (`find_in_tree_lib_sources`, validated with lighter, direction-neutral
  structural checks). The smoke test against `agent-worktrees` additionally
  surfaced a THIRD real shape neither form alone covered: a vendored lib
  cross-referencing a SIBLING vendored lib one level up without
  `editable = true` (`plugins/agent-worktrees/libs/plugin-activation`
  depending on `../dropin-registry`) -- resolved by applying
  `uv_editable_problems`'s strict escaping-form validation ONLY to the
  originally requested top-level plugin, never while recursing into an
  already-discovered vendored lib's own manifest, where a sibling in-tree
  cross-reference is legitimate. Two more real, lower-severity findings
  from this round fixed in the same pass: an out-of-tree `--out-dir` nested
  inside a hashed source directory would fold a prior run's own output
  into the NEXT run's payload hash (now rejected explicitly before
  building); and the manifest's `version` field used the wheel's PEP
  440-normalized spelling (`"0.4.1.dev3"`) instead of the raw declared one
  (`"0.4.1-dev3"`), which would never exactly match the `<plugin>-v<version>`
  release-tag identity this effort documents (now reads the raw version
  from `pyproject.toml` directly, with a sanity check that it still
  corresponds to the wheel's own normalized version). 49 unit tests now
  (`tools/test_build_python_artifacts.py`), including one derived directly
  from the real `agent-worktrees` cross-reference bug the smoke test found;
  re-verified `agent-bridge` and `agent-worktrees` both build correctly.
- A sixth review round confirmed the recursive-discovery fix and found 3
  more real issues against it: the in-tree lib validation only checked the
  lib directory itself for a symlink, missing one nested anywhere below it
  (fixed by reusing `uv_editable_ref._find_symlink`'s own recursive check,
  applied to both vendored libs and -- a second occurrence of the same
  gap -- the top-level plugin directory itself, which had no symlink
  protection at all); the in-tree discovery's `candidate.parent.name ==
  "libs"` check was too permissive, accepting a non-editable path that
  escaped to an UNRELATED plugin's `libs/` directory (fixed by constraining
  accepted locations to the consumer's own `libs/` or, when the consumer
  itself already lives directly under a directory named `libs`, that same
  parent `libs/` folder -- matching `materialize_nested_uv_editable_refs`'s
  own identical "expected sibling location" constraint); and the manifest
  only recorded the artifact SET's aggregate tags, not each wheel's own
  parsed `python_tag`/`abi_tag`/`platform_tag` as the effort's own
  documentation already claimed (fixed by adding them to every wheel
  entry). 52 unit tests now, 3 environment-conditional (skip when this
  particular sandboxed machine's own symlink-resolution restriction makes
  the scenario unexercisable, same limitation already noted for `uv`
  itself elsewhere in this effort's Journal); re-verified both plugins
  build correctly, with their manifests' wheel entries now carrying tags.
- A seventh review round found 3 more real issues: the per-entry symlink/
  structure/location validation applied only to the top-level plugin's
  escaping references (via `uv_editable_problems`, run once) -- a NESTED
  lib's own escaping, `editable = true` reference was queued and built
  completely unvalidated, since recursion deliberately stopped re-running
  the whole-consumer `uv_editable_problems` check (to allow the legitimate
  sibling cross-reference case from the prior round). Fixed by extracting
  a new, single-entry validator (`_validate_editable_canonical_ref`,
  reusing the exact same primitives `uv_editable_problems` itself calls)
  and applying it to every escaping `editable = true` entry at EVERY
  recursion depth, not only the top level; `plugin` (the CLI argument)
  was used directly as a path component (`PLUGINS_DIR / plugin` and the
  manifest filename) with no validation at all, letting an absolute value
  or a `../` traversal escape both -- fixed by reusing `is_safe_lib_name`
  on it the same way a vendored-lib name already is; and two different
  sources producing the identical wheel filename within ONE invocation
  would silently overwrite each other, leaving an earlier manifest entry's
  sha256 describing bytes no longer on disk -- fixed by tracking filenames
  already produced THIS invocation and failing closed on a real collision,
  while still preserving the intentional retry-overwrite behavior for a
  prior invocation's own leftover wheel. 56 unit tests now; re-verified
  both `agent-bridge` and `agent-worktrees` build correctly.
- An eighth review round reported 0 new open findings (its lighter "needs
  a closer look" banner, versus the prior seven rounds' "changes
  recommended") and confirmed both of round seven's HIGH findings
  resolved, but surfaced 3 more real, narrower edge cases from
  code already in place: an `editable = true` entry whose path does NOT
  escape its own consumer root fell through BOTH discovery functions
  entirely (neither `find_uv_editable_refs`, which only returns escaping
  entries, nor `find_in_tree_lib_sources`, which skips every
  `editable = true` entry) and would have been silently omitted from the
  manifest rather than built or explicitly rejected -- fixed with an
  explicit check that fails closed on this unsupported combination;
  deduplicating a vendored lib solely by its final directory name could
  silently drop one of two genuinely distinct sources sharing a name
  (e.g. two different `libs/.../libs/widget` trees) -- fixed by comparing
  the resolved canonical directory whenever a name repeats, failing closed
  on a real mismatch; and a malformed wheel with more than one
  `dist-info/WHEEL` entry would silently trust whichever ZIP member came
  first -- fixed to require exactly one. 59 unit tests now; re-verified
  both plugins build correctly.
- A ninth review round found the previous round's own first fix was
  itself incomplete: a non-editable escaping reference was assumed to
  always be a legitimate sibling in-tree cross-reference that
  `find_in_tree_lib_sources`'s own (deliberately tighter) scan would
  "always" pick up -- but if the target escapes to somewhere outside
  every allowed `libs/` location, that scan also skips it, so the
  `continue` silently dropped the dependency from the artifact set
  entirely (the build still "succeeded"), even though
  `materialize_nested_uv_editable_refs` would refuse the exact same
  reference. Fixed by cross-checking: every non-editable escaping
  reference must appear in that same consumer's own in-tree-discovered
  set, or the build now fails closed naming the exact unaccounted-for
  reference. 60 unit tests now; re-verified both plugins build correctly.
- A tenth review round confirmed that fix and found one more real,
  narrower issue plus 3 documentation-process/style corrections:
  `_read_sources_table` called `.get()` on `[tool]` and `[tool.uv]` before
  checking their own types, so a structurally valid-but-malformed TOML
  document (e.g. `tool = []`) raised an uncaught `AttributeError` instead
  of the documented `ArtifactBuildError` -- especially reachable while
  recursively inspecting a vendored lib, where the top-level
  `uv_editable_problems` guard never runs; fixed by validating each
  intermediate table explicitly, with 2 new regression cases. The 3
  style findings (review-round chronology embedded in non-Journal design
  documentation and in a test comment, per `CONTRIBUTING.md`'s "describe
  current state, not review history" rule) are corrected directly in this
  same diff -- this Phase 2 checklist item and the Wheel-tag semantic
  compatibility Open Design Question entry now describe only the enduring
  technical state, and the egg-info test comment states the invariant
  without naming a review round. 62 unit tests now; re-verified both
  plugins build correctly.
- An eleventh review round found one more instance of the same class of
  bug: `read_project_version` also called `.get()` on `[project]` before
  checking it was a table, raising an uncaught `AttributeError` on a
  malformed-but-TOML-valid manifest -- fixed identically to
  `_read_sources_table`'s own prior fix, with a regression test. 63 unit
  tests now; re-verified both plugins build correctly.
- A twelfth review round found `parse_wheel_filename` could still misparse
  a non-normalized wheel name: PEP 427 normalizes a distribution's own
  `-`/`_`/`.` runs to a single `_` specifically so the filename split is
  unambiguous, but the parser reconstructed `name` by rejoining multiple
  tokens with `-` whenever more than one remained, so a malformed,
  unnormalized name like `demo-pkg` in `demo-pkg-1.2.3-py3-none-any.whl`
  was silently accepted as `name="demo", version="pkg"`. Fixed by treating
  `name` as always exactly the first token (never rejoined) and requiring
  `version` to look like a real version (start with a digit, per PEP 440)
  -- both properties a well-formed wheel always has, closing the
  ambiguity rather than guessing. 64 unit tests now; re-verified both
  plugins build correctly. This slice has now gone through 12 automated
  review rounds, each finding genuine, progressively narrower issues -- a
  pattern consistent with this effort's own documented review history on
  its original design PR.
- **Not yet done** (explicitly out of scope for this slice, named in the
  Phase 2 checklist): wiring this into the real `promote_release.py`
  pipeline; a per-promotion-run shared build-toolchain lock (this slice
  builds each wheel via the normal isolated PEP 517 build rather than a
  pre-resolved, pinned one -- `build_toolchain` here records whatever the
  ambient build actually used, which is correct per-wheel but does not yet
  guarantee one shared value across a whole promotion run); the
  third-party dependency-closure resolution/lag-tolerant selection; and
  the trust/publication contract (attestation, GitHub Release channel).
  **Next:** pick up one of those as the next Phase 2 slice.

### 2026-10-02 - Phase 1 spike run: governed-feed resolution proven, lag measured, timing/storage baselined

- Built real wheels for `agent-bridge` + its 9 materialized vendored libs
  via `uv build --wheel`, then installed into fresh venvs on a
  governed-feed-configured machine (augloop1), comparing the wheel-based
  path against today's from-source install.
- Proved governed-feed-only resolution: searched the full verbose install
  log for every contacted host -- zero `pypi.org`/`files.pythonhosted.org`
  matches; every third-party dependency resolved through
  `packagefeedproxy.microsoft.io` and its backing Azure Artifacts/blob
  storage chain.
- Measured feed propagation lag against PyPI's real release history for 3
  dependencies: fastapi ~65 days behind (2 minor versions), pydantic and
  uvicorn ~0 days (feed had PyPI's actual latest). **This corrects the
  effort's original "~1 week" lag assumption** -- lag is per-package and
  variable, not a fixed constant; revised the Lag-tolerant version
  selection Open Design Question to recommend a dynamic, feed-queried
  admission check instead of a static age window.
- Measured timing: cold install 20.6s (wheel) vs 24.4s (source, ~16%
  faster); warm install 5.7s (wheel) vs 15.2s (source, ~63%/2.7x faster).
  First-party wheel storage (~0.89 MiB for 10 wheels) is a small fraction
  of total installed footprint (~14.8 MiB venv).
- Full method and tables recorded in Proposal. Checked off all 4 Phase 1
  plan items; noted two follow-ups not yet measured (network byte/request
  counts, and a larger/heavier plugin than agent-bridge for the cold-case
  comparison).
- **All 7 Open Design Questions now have a concrete, committed direction,
  and Phase 1's spike has produced real measured evidence** -- both
  alternative completion-gate conditions for this leg are satisfied.
  **Next:** Phase 2 implementation can begin (promotion-side wheel
  building, manifest/attestation, publication) informed by this evidence.

### 2026-10-02 - Build hermeticity resolved; all 6 of 7 questions now concrete

- Surveyed every `pyproject.toml` under `plugins/` and `libs/` (43 files):
  all use `setuptools.build_meta` with an open-floor `requires` (`>=68.0`,
  `>=83.0.0`, or `>=84.0.0`) -- confirmed single-backend, simplifying the
  fix to one shared build-toolchain lock rather than a per-backend scheme.
- Resolved **build hermeticity**: promotion resolves and records one
  governed-feed-sourced build-toolchain lock per run, builds every wheel
  `--no-build-isolation` against it, and folds the lock's hash into each
  artifact's identity alongside the existing payload/platform/arch/ABI
  fields. See the Open Design Questions entry for the full reasoning.
- **Remaining:** Phase 1's spike (governed-feed-only install + timing
  baseline) is the only item not yet started. All 7 Open Design Questions
  now have a concrete, committed direction; Phase 2 implementation can
  begin drafting the promotion-side changes once Phase 1 produces its
  baseline evidence.

### 2026-10-01 - Four Open Design Questions resolved concretely

- Read `.github/workflows/validate-and-promote.yml`'s `promote` job in full
  (the `main-promotion` environment, `APERTURE_RELEASE_TOKEN` scope and
  comments, and the `release/promote-<run id>` branch + squash-merge flow),
  `ci.yml`'s existing `psmux` GitHub Release consumption, and
  `tools/materialize_main.py`'s `materialize_uv_editable_ref_into()` /
  `materialized_libs` enumeration, per the Open Design Questions section's
  own instruction to verify against the real configuration rather than
  assume.
- Resolved **trust root** and **credential separation** together: the
  promotion PAT that can write `main` must not be the same credential that
  signs/attests the published artifact; GitHub Artifact Attestations
  (Sigstore/OIDC, `actions/attest-build-provenance`) is the concrete
  mechanism, run from a job scoped to `id-token: write` only, separate from
  the job holding `APERTURE_RELEASE_TOKEN`. Neither exists in this repo's
  workflows yet -- confirmed net-new for Phase 2.
- Resolved **vendored first-party libs**: reuse `materialize_main.py`'s
  existing `materialized_libs` enumeration directly rather than re-deriving
  which `libs/<lib>` trees need their own wheel.
- Resolved **publication channel**: GitHub Release assets, validated against
  the existing `psmux` download precedent in `ci.yml` rather than assumed
  from scratch.
- **Still open:** build hermeticity (next in queue -- needs each plugin's/
  lib's resolved build-tool closure folded into artifact identity). Phase 1
  spike (governed-feed-only install + timing baseline) has not started.

### 2026-10-01 - Kickoff and review

- Effort created after reconciling against existing work (agent-index
  client/server split already landed elsewhere; installer execution,
  installation-cell ownership, and deferred provisioning owned by adjacent
  efforts). Scoped to the genuinely missing supply-chain layer.
- Seven rounds of automated PR review surfaced real, substantive design
  gaps in the original fully-elaborated draft: a promotion-side feed check
  that can't actually certify per-consumer installability; a closure
  resolver that would routinely pin versions ahead of a lagging governed
  feed; a missing accounting for vendored first-party `libs/<lib>`
  dependencies; no artifact publication channel; an incorrect `main`/`dev`
  trust-root claim; an unresolved credential-separation gap in the trust
  root; and a build-hermeticity gap in artifact identity. Rather than
  continue elaborating each fix inline through further review rounds, this
  document was deliberately scaled back to a lean plan: the findings above
  are preserved as named **Open Design Questions** to resolve concretely
  during Phase 2 implementation, instead of being pre-solved (and
  re-litigated) at the planning stage.
