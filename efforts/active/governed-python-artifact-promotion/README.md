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
  `efforts/active/agent-index-engine-daemon/README.md`) — do not re-plan it.
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
  12–25 MiB for most plugins; a complete-venv-archive distribution strategy
  was estimated at roughly 400 MiB per OS/arch/ABI tuple, which is why this
  effort does not pursue that as the primary distribution unit.

## Request

> Can promotion pre-build Python installation inputs for plugin packages, so
> that plugin installers can check for a version-matching, hash-verified
> release and download it directly instead of building a virtual
> environment from source on every update? Third-party dependencies must
> never be pulled through a public hosted package index — only through the
> machine's governed, seasoned feed configuration — and the design must
> avoid an excessive version lock that pins a dependency version newer than
> what the governed feed currently carries (governed feeds can lag public
> releases by about a week). A single packaged binary is not an acceptable
> distribution strategy.

## Plan

### Phase 1 — Spike: governed-feed-only install and timing baseline

- [ ] Prove a first-party wheel can be installed while every transitive
      third-party resolution uses only the machine's governed feed
      configuration (`uv.toml`/`UV_DEFAULT_INDEX` — `uv` does not read
      `pip.conf`). Capture evidence that no public package index was
      contacted.
- [ ] Measure actual governed-feed propagation lag to inform a lag-tolerant
      version-selection policy for later phases.
- [ ] Compare a from-source install against a verified first-party-wheel
      install, cold vs. warm cache, recording wall time, network activity,
      and physical disk use.
- [ ] Record findings and revise later phases only from measured evidence.

### Phase 2 — Promotion-built, content-addressed first-party artifacts

- [ ] Build first-party wheels and manifests during promotion, keyed by
      plugin payload hash, platform, architecture, and Python ABI, covering
      the plugin's own wheel and every vendored `libs/<lib>` wheel it
      requires after materialization.
- [ ] Resolve and pin a dependency closure for the third-party portion only,
      using a lag-tolerant selection policy informed by Phase 1, and record
      it in the manifest.
- [ ] Design and implement an artifact trust/publication contract (digest
      authentication, publication channel, credential model) — see Open
      Design Questions below; resolve these concretely during this phase,
      not deferred further.

### Phase 3 — Verified consumption with correct fallback

- [ ] Teach installers (via the shared installer engine, not a parallel
      mechanism) to locate, verify, and consume a matching artifact set,
      falling back safely to the existing from-source path when no
      verified match exists, and failing closed (never falling back) when
      a located artifact fails verification. Preserve existing
      immutable-slot assembly, health gates, activation, and rollback.
- [ ] Validate across supported platform/architecture/Python-ABI
      combinations, including a clean-room first install.

### Phase 4 — Retention, provenance, and documentation

- [ ] Define artifact retention/GC with physical-storage accounting and
      provenance/licensing review.
- [ ] Document the artifact contract, rollback behavior, and feed-lag
      diagnosis for operators.

## Open Design Questions (resolve concretely in Phase 2, not here)

Earlier review rounds on this effort surfaced several real design gaps
worth preserving as named open questions, deliberately **not** pre-solved in
this planning document — Phase 2 is where each gets a concrete, verified
answer against the actual repository/CI configuration:

- **Feed model:** promotion runs on public CI and cannot certify
  installability from any one consumer's private governed feed — the
  governed-feed check is necessarily a consumer-side, install-time concern,
  not a promotion-time one.
- **Lag-tolerant version selection:** resolving "newest compatible" would
  routinely pin versions a real governed feed doesn't carry yet; the
  resolver needs a seasoning/age constraint (or equivalent), tuned from
  Phase 1's measured lag.
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
- **Build hermeticity:** open-ended build-system requirements (e.g.
  `setuptools>=83.0.0`) can produce different bytes for a nominally
  identical artifact identity across promotions; identity needs to account
  for the resolved build-tool closure too. _(Not yet resolved -- next in
  queue.)_
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

_Pending Phase 1 spike evidence._

## Journal

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
