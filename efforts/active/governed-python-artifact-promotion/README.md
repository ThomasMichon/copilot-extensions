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
