# Governed Python Artifact Promotion

- **Slug:** `governed-python-artifact-promotion`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice (see Coordination)
- **Created:** 2026-10-01
- **Status:** Draft
- **Vision:** extends `visions/installer/README.md`, `visions/plugin-services/installation-cells/README.md`, and the agent-index runtime vision; this effort does not replace any of them
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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| _(unassigned)_ | Design, spike, and phased implementation | a dedicated `copilot-extensions` worktree |

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

- [x] **Tool built (2026-10-02); pipeline wiring still open.**
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
      "lock_id": "sha256:..."}`, schema version 2) rather than schema
      version 1's ad hoc per-wheel generator list, and `lock_id` folds into
      `artifact_id`. Smoke-tested for real: building `agent-bridge` then
      `agent-worktrees` against the SAME `--toolchain-venv` produced
      identical `lock_id`s, confirming the shared-lock mechanism. **Not yet
      done:** wiring this into the real promotion pipeline
      (`promote_release.py`) - `build_python_artifacts.py` is still a
      standalone, independently usable tool today, not yet invoked anywhere
      in `validate-and-promote.yml`'s actual promotion flow. This checklist
      item stays open until promotion actually invokes the builder.
- [ ] Resolve and pin a dependency closure for the third-party portion only,
      using a lag-tolerant selection policy informed by Phase 1, and record
      it in the manifest.
- [ ] Design and implement an artifact trust/publication contract (digest
      authentication, publication channel, credential model) - see Open
      Design Questions below; resolve these concretely during this phase,
      not deferred further.



### Phase 3 - Verified consumption with correct fallback

- [ ] Teach installers (via the shared installer engine, not a parallel
      mechanism) to locate, verify, and consume a matching artifact set,
      falling back safely to the existing from-source path when no
      verified match exists, and failing closed (never falling back) when
      a located artifact fails verification. Preserve existing
      immutable-slot assembly, health gates, activation, and rollback.
- [ ] Validate across supported platform/architecture/Python-ABI
      combinations, including a clean-room first install.

### Phase 4 - Retention, provenance, and documentation

- [ ] Define artifact retention/GC with physical-storage accounting and
      provenance/licensing review.
- [ ] Document the artifact contract, rollback behavior, and feed-lag
      diagnosis for operators.

## Open Design Questions (resolve concretely in Phase 2, not here)

Earlier review rounds on this effort surfaced several real design gaps
worth preserving as named open questions, deliberately **not** pre-solved in
this planning document - Phase 2 is where each gets a concrete, verified
answer against the actual repository/CI configuration:

- **Feed model:** promotion runs on public CI and cannot certify
  installability from any one consumer's private governed feed - the
  governed-feed check is necessarily a consumer-side, install-time concern,
  not a promotion-time one.
- **Lag-tolerant version selection:** resolving "newest compatible" would
  routinely pin versions a real governed feed doesn't carry yet; the
  resolver needs a seasoning/age constraint (or equivalent), tuned from
  Phase 1's measured lag. **Phase 1 evidence (2026-10-02) revises this: lag
  is not a fixed ~1-week constant** - measured at 0 days (pydantic,
  uvicorn) to 65+ days (fastapi) across just 3 real dependencies of one
  plugin. A static per-package age window would be wrong in one direction
  or the other depending on the package. The consumer-side admission check
  (per the Feed model question above) should instead directly **query the
  governed feed's own currently-available versions at install time** and
  select the newest one the feed actually carries, rather than applying any
  assumed universal age constant. See Proposal for the full measurement.
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
  today (confirmed absent in both `ci.yml` and `validate-and-promote.yml`),
  so this is net-new infrastructure for Phase 2, not a reuse of an existing
  mechanism.
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
  `setuptools`/`wheel` versions, resolved from the governed feed like every
  other third-party dependency in this effort -- never a public index),
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
  deterministic tag per promotion (`<plugin>-v<version>`) with fixed,
  predictable asset filenames (one per platform/arch/ABI, plus one per
  vendored lib wheel), requiring no separate discovery service and matching
  a pattern this codebase already trusts.
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

## Validation Plan

- [ ] A clean-cache test proves third-party resolution uses only the
      governed feed; verify the effective `uv`/`pip` configuration
      independently and never log credentials.
- [ ] Feed-lag tests prove the consumer-side admission check blocks
      consumption of a closure not yet available on the local governed
      feed, and never silently substitutes a public-index candidate.
- [ ] Manifest/trust-root tests reject a wrong payload/ABI/platform, an
      altered artifact digest, or a mismatched dependency closure before
      activation.
- [ ] A test distinguishes "no artifact published" (safe from-source
      fallback) from "published artifact fails verification" (fail closed
      with diagnostics, never fall back).
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
through `packagefeedproxy.microsoft.io`  its backing
`*.pkgs.visualstudio.com` Azure Artifacts feed  `*.vsblob.vsassets.io`
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
Question above: Phase 2 should query the governed feed's own currently
available versions at install time, not apply an assumed age constant.

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
  and passing it to every `Marker.evaluate(environment=...)` call. 8 more
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
  new `tools/build_toolchain_lock.py` module (485 lines), re-imported and
  re-exported by `build_python_artifacts.py` (968 lines now) so its own
  public API and every existing test's `bpa.<name>` access pattern stayed
  unchanged. One real fix the split itself surfaced: `_governed_feed_configured`
  is now called from inside `build_toolchain_lock.py`'s own module, so a
  test that monkeypatched it via the `bpa` re-export no longer took effect
  there (Python resolves a free variable via the DEFINING module's own
  globals, not the importer's) -- updated the two affected tests to patch
  the real defining module instead. Confirmed `tools/check-module-size.py`
  now passes for this diff. This slice has now gone through 7 automated
  review rounds, each finding genuine, progressively narrower issues --
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
