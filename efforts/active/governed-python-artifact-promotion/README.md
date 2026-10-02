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
rollback safety, and bounded storage. No release or installer may silently
fall back to a public package index for third-party dependencies, and no
release bundles a complete virtual environment or a single packaged binary
as its primary distribution unit.

This effort fills a specific gap in an otherwise well-covered area: existing
work already defines how installer execution, installation-cell ownership,
and the agent-index client/server split work. None of it yet defines the
**supply-chain contract** for a promoted first-party Python artifact itself
— its identity, its governed-feed-only dependency closure, or the admission
gate that keeps a promoted release installable under real-world feed lag.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| _(unassigned)_ | Design, spike, and phased implementation | a dedicated `copilot-extensions` worktree |

## Coordination

- **Topology:** independent per-slice PRs (each phase below should leave
  `dev` green on its own; no shared feature branch needed unless a phase
  proves otherwise).
- **Host (owns PRs):** whichever participant opens each phase's PR.
- **Delegates:** none yet.
- **Handoff:** completed phases are journaled here with links to their
  merged PRs before the next phase starts.

## Context

- **Do not duplicate adjacent work.** Before touching installer execution,
  installation-cell ownership, or agent-index packaging, read:
  - `efforts/active/vendored-installer-engine/README.md` — owns the shared
    installer execution engine (venv build, package install, versioned-slot
    lifecycle). This effort's artifact-consumption step should call into
    that engine, not reimplement install mechanics.
  - `efforts/active/tiered-payload-provisioning/README.md` — owns
    session-start reconciliation and deferred/on-demand provisioning. This
    effort's artifacts are an *input* to that provisioning, not a
    replacement for it.
  - `visions/plugin-services/installation-cells/README.md` — owns
    marketplace-installation-cell identity, ownership, and the fact that
    "multiple immutable runtime versions ... are slots inside one
    installation cell." This effort places its artifacts into that existing
    slot model; it does not define a new one.
  - `efforts/active/agent-index-server-package-split/README.md` and
    `efforts/active/agent-index-engine-daemon/README.md` — the agent-index
    thin-client/heavy-server split is **already landed** (server package
    split implemented and locally live-validated; engine daemon phases
    landed with only rollback-path validation outstanding). Do not re-plan
    this split; if this effort's artifact format changes how agent-index's
    server/engine package is built or distributed, extend those efforts
    directly instead of re-deriving the split here.
- **Vendored first-party library closure.** A plugin's own promotion
  already **materializes** its `tool.uv.sources` path dependencies on
  shared `libs/<lib>` packages (`tools/materialize_main.py`): the
  referenced lib tree is physically copied into the plugin's own directory
  and its `pyproject.toml` entry rewritten from an external editable
  reference to a local, non-editable path dependency (e.g. agent-bridge
  depends on several `agent-*` libs this way — see
  `plugins/agent-bridge/pyproject.toml`). These vendored libs have **no
  governed-feed identity at all** — they are first-party, not third-party —
  so a first-party artifact for one plugin is not just that plugin's own
  wheel: it is the **complete first-party closure**, the plugin wheel plus
  a wheel for every vendored `libs/<lib>` path dependency it still declares
  after materialization. The governed-feed admission gate and the
  seasoned-version constraint apply only to the genuinely third-party
  closure; the first-party closure is built, verified, and consumed
  together as a set, never resolved against any package index.
- **What is genuinely missing** (this effort's actual scope): promotion-time
  first-party wheel/manifest generation keyed to payload hash + platform +
  architecture + Python ABI; a governed-feed dependency-closure admission
  gate that accounts for feed propagation lag instead of locking to
  not-yet-available versions; and verified artifact consumption with a
  correct source-build fallback when no matching artifact exists.
- **Feed model.** Promotion runs on GitHub-hosted `ubuntu-latest` runners
  (`.github/workflows/validate-and-promote.yml:445-446`), which can reach
  only a public reference index, not any particular consumer's private
  governed-feed mirror or its propagation state. A promotion-side check
  therefore cannot certify "this closure is installable from *your*
  governed feed right now" for every consumer. Promotion's own gate
  resolves and pins a reproducible dependency closure against a public
  reference index and records it in the manifest; it does **not** claim
  governed-feed installability for any specific consumer. The actual
  governed-feed-lag-aware admission check is a **consumer-side,
  install-time** responsibility (Phase 3) run against that machine's own
  governed feed, with a safe from-source fallback when that specific
  machine's feed doesn't yet carry the pinned closure.
- **Lag-tolerant closure selection.** Resolving against "newest compatible"
  on a public reference index would routinely pin versions a real governed
  feed (which lags public releases by about a week) doesn't carry yet —
  defeating the fast path in the common case, not just at the edges, and
  directly reproducing the excessive-version-lock problem the original
  request called out. Promotion's resolver is therefore constrained to
  **seasoned** versions only: candidates must already be at least a fixed
  minimum age (default 14 days, chosen conservatively above the known ~1
  week governed-feed propagation window; tune from Phase 1's measured feed
  lag rather than this default) at resolution time, never simply "newest
  compatible." This makes the pinned closure very likely already mirrored
  on a real governed feed by the time any consumer installs it; the
  install-time admission probe remains the authoritative confirmation and
  absorbs the residual cases (a feed temporarily further behind than the
  seasoning window, an unusually slow mirror, etc.).
- **Trust root.** The manifest's expected digest is **committed into
  promoted, branch-protected repository metadata** — specifically, written
  into the promoted plugin payload on `main` by the generated release
  commit (`validate-and-promote.yml:555-597`, `promote_release.py:15-18`
  write generated release metadata to `main` only, not `dev` — the trust
  root lives in the independently-installed `main` payload a plugin
  actually ships from, not in a `dev` snapshot), protected by the same
  branch-protection rules as source code — not merely a value sitting next
  to the artifact in the same, equally-mutable artifact store. An attacker
  who can write to the artifact store cannot also forge a branch-protected
  commit, so replacing both the wheel and its digest together is not
  possible without compromising repository review controls directly. This
  is the chosen trust root; a signed attestation against a separately
  pinned verifier key was considered and rejected for this effort's scope
  as unnecessary added key-management surface given the branch-protection
  guarantee already available.
- **Credential separation is a required, not-yet-designed precondition of
  the trust root above.** The stated separation only holds if the
  credential that can publish Release assets is independent of the
  credential that can land the promotion commit to `main`. As currently
  configured, the promotion PAT holds Contents + PR write, and the `main`
  gate admits any owner-authored `release/promote-*` branch by prefix and
  author (`.github/workflows/ci.yml:125-127`) — if that same credential
  also publishes Release assets, compromising the artifact writer is
  sufficient to land a forged digest too, collapsing the intended
  separation. Phase 2 must either (a) introduce an independently
  constrained publisher credential used only for Release-asset writes, with
  no path to satisfying the `main` gate, or (b) add a separately-keyed
  signature as defense in depth if credential separation alone cannot be
  achieved. This is an open precondition, not yet resolved by this effort;
  do not implement Phase 2's publication step until one option is chosen
  and the chosen option's actual isolation is verified against the real
  credential/workflow configuration (not assumed).
- **Hermetic build inputs are part of artifact identity.** Existing
  first-party packages use open-ended build requirements (e.g.
  `libs/agent-procutil/pyproject.toml` declares `setuptools>=83.0.0`), so
  the same payload/platform/ABI key could select a different build backend
  version on a later promotion and produce different wheel bytes for a
  nominally identical identity — and an isolated build would otherwise
  download that backend from a public index, contradicting the
  governed-only acquisition goal. The build-tool closure (the exact
  resolved versions of `build-system.requires`, e.g. `setuptools`) must
  itself be pinned to a governed-feed-resolved version and its digest
  folded into the artifact's identity, not left open-ended.
- **Artifact publication and durable access.** A built, verified artifact
  set is only useful if it has a durable, deterministic location a
  consumer can fetch it from without a separate discovery API. Promoted
  artifacts are published as **GitHub Release assets on this repository**,
  one release per promoted plugin-payload version, tagged deterministically
  from the plugin name and promoted version (e.g.
  `<plugin>-v<version>`) so an installer can construct the expected
  release/asset URL directly from the trust-rooted manifest committed on
  `main`, with no separate lookup/index service to stand up or keep
  available.
- **Fallback vs. fail-closed.** "No usable artifact" must not conflate two
  different states: an artifact that was never published for this tuple
  (safe to fall back to from-source) versus a published artifact whose
  digest/provenance verification **failed** (must fail closed with
  diagnostics — falling back there would silently mask corruption or
  tampering and defeat the whole admission signal). Phase 3's consumption
  logic and the Validation Plan treat these as distinct cases.
- Baseline measurements (directional, to be re-validated with this effort's
  own spike rather than assumed): representative Windows ZIP sizes around
  12–25 MiB for most plugins and roughly 320 MiB for agent-index's full
  package before the client/server split; a complete-venv-archive
  distribution strategy was estimated at roughly 400 MiB per OS/arch/ABI
  tuple, which is why this effort does not pursue that as the primary
  distribution unit.

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
      configuration (`uv.toml`/`UV_DEFAULT_INDEX`, inheriting the box's
      pip `index-url` rather than pinning a separate one — `uv` does not
      read `pip.conf`). Capture evidence that no public package index was
      contacted.
- [ ] Define the authoritative feed model given the CI-reachability
      constraint above: promotion resolves/pins against a public reference
      index only; design the consumer-side admission probe (Phase 3) that
      actually answers "is this pinned closure available from *this*
      machine's governed feed right now," distinguishing transient
      propagation lag from a genuinely absent package/version.
- [ ] Measure actual governed-feed propagation lag (how long after public
      publication a version typically becomes available) to set and
      validate the seasoned-version minimum age used by closure selection
      (see Context) instead of leaving it at the untested 14-day default.
- [ ] Compare a from-source install against a verified first-party-wheel
      install, cold governed cache vs. warm shared cache, recording wall
      time, network activity, and physical disk use (careful not to
      double-count nested or linked directories).
- [ ] Record findings here and revise later phases only from measured
      evidence, not the directional estimates above.

### Phase 2 — Promotion-built, content-addressed first-party artifacts

- [ ] Build first-party wheels and manifests during promotion, keyed by
      plugin payload hash, platform, architecture, and Python ABI, with
      artifact digests and build provenance. Keep first-party payload
      identity separate from third-party dependency-closure identity.
- [ ] Build the **complete first-party closure**, not just the top-level
      plugin wheel: after `materialize_main.py` vendors a plugin's
      `libs/<lib>` path dependencies, build and verify a wheel for each
      vendored lib alongside the plugin's own wheel, as one set covered by
      the same manifest. Never attempt to resolve a vendored lib through
      the governed feed or any package index.
- [ ] Pin the **build-tool closure** itself (e.g. the exact `setuptools`
      version satisfying `build-system.requires`) to a governed-feed-
      resolved version, and fold its digest into artifact identity so a
      nominally identical payload/platform/ABI key cannot silently produce
      different wheel bytes on a later promotion, and an isolated build
      never needs to reach a public index for its own build backend.
- [ ] Resolve and pin a reproducible third-party dependency closure against
      a public reference index at promotion time, **constrained to
      seasoned versions** (see the lag-tolerant closure selection decision
      above — never simply "newest compatible"); record it in the manifest
      as the closure a consumer must later validate against their own
      governed feed.
- [ ] **Before implementing publication:** resolve the credential-separation
      precondition in Context — either an independently constrained
      Release-asset-publishing credential with no path to satisfying the
      `main` promotion gate, or a separately-keyed signature as defense in
      depth. Verify the chosen option against the actual credential/workflow
      configuration, not an assumption.
- [ ] Specify and implement the manifest's trust root per the committed
      decision above: the manifest's expected digest is committed into the
      promoted `main` plugin payload via the generated release commit (not
      a `dev` snapshot). Implement the installer-side check that reads the
      digest from that `main`-committed metadata (not from the artifact
      store) before trusting any downloaded artifact/manifest pair.
- [ ] Publish the built artifact set as GitHub Release assets on this
      repository per the deterministic tag/locator scheme decided above
      (one release per promoted plugin-payload version), so Phase 3 has a
      durable, discoverable location to fetch from without a separate
      index service.

### Phase 3 — Verified consumption with correct fallback

- [ ] Implement the consumer-side governed-feed admission probe designed in
      Phase 1: before consuming a promoted artifact, confirm its pinned
      third-party closure is resolvable from *this* machine's own governed
      feed; treat feed propagation lag as distinct from a genuinely
      unavailable package/version.
- [ ] Teach installers (via the shared installer engine, not a parallel
      mechanism) to locate, verify, and consume a matching artifact **set**
      — the plugin wheel together with every vendored first-party lib
      wheel it requires. Distinguish, and handle separately: (a) **no
      artifact published** for this payload/platform/arch/ABI tuple — fall
      back safely to the existing from-source path; (b) **a published
      artifact whose digest/provenance verification fails** — fail closed
      with diagnostics; never silently fall back, since that would mask
      corruption or tampering. Preserve existing immutable-slot assembly,
      health gates, activation, and rollback.
- [ ] Validate across supported platform/architecture/Python-ABI
      combinations, including a clean-room first install (never having
      exercised the from-source path).

### Phase 4 — Retention, provenance, and documentation

- [ ] Define artifact retention/GC with physical-storage accounting and
      provenance/licensing review (no third-party package bytes
      redistributed inside a first-party artifact).
- [ ] Document the artifact contract, rollback behavior, and feed-lag
      diagnosis for operators.

## Validation Plan

- [ ] A credential-isolation test proves the credential that publishes
      Release assets **cannot** independently satisfy the `main` promotion
      gate (and vice versa) — the core guarantee the trust root depends on.
- [ ] An offline repeat-build test proves two promotions of the same
      payload/platform/ABI produce byte-identical (or digest-identical)
      wheels, with the build-tool closure itself resolved only from the
      governed feed and never from a public index during an isolated
      build.
- [ ] An end-to-end publication test proves a promoted artifact set is
      fetchable from its GitHub Release using only the deterministic
      tag/locator derived from the `main`-committed manifest — no manual
      discovery step.
- [ ] A clean-cache test proves third-party resolution uses only the
      governed feed; verify the effective `uv`/`pip` configuration
      independently and never log credentials.
- [ ] Feed-lag tests exercise the **consumer-side** admission probe (not a
      promotion-side check — see the feed model constraint in Context):
      show it blocks consumption of a pinned closure not yet available on
      *this machine's* governed feed, succeeds once the feed carries it,
      and never silently substitutes a public-index candidate.
- [ ] A seasoned-closure test proves promotion's resolver rejects a
      candidate version younger than the configured minimum age even when
      it is otherwise the newest compatible version, confirming the fast
      path is actually reachable on a representative lagging governed feed
      rather than only in a best-case same-day scenario.
- [ ] A representative vendored-lib install test builds and installs a
      plugin with at least one `tool.uv.sources` path dependency (e.g.
      agent-bridge's vendored `agent-*` libs) entirely from the promoted
      first-party artifact set, with no attempt to resolve any vendored lib
      against a package index.
- [ ] Manifest/trust-root tests reject a wrong payload/ABI/platform, an
      altered artifact digest, or a mismatched dependency closure before
      activation — and specifically prove that replacing *both* the
      artifact and its manifest at the artifact store (without also
      forging a branch-protected commit to the repository's trust-root
      metadata) is still rejected.
- [ ] A dedicated test distinguishes the two "no usable artifact" cases:
      **absence** (no artifact published for this tuple) results in a safe
      from-source fallback; **verification failure** (published artifact,
      failed digest/provenance check) results in a fail-closed error with
      diagnostics and never falls back silently.
- [ ] Cold/warm source-build and artifact paths report comparable wall
      time, network, and physical storage, measured against this effort's
      own Phase 1 baseline (not an assumed target).
- [ ] Windows and POSIX clean-room install/update tests prove path-correct
      slots, health-gated activation, rollback, and from-source fallback.

## Proposal

_Pending Phase 1 spike evidence._

## Journal

### 2026-10-01 — Kickoff

- Effort created after reconciling against existing work: the agent-index
  thin-client/heavy-server split is already landed elsewhere
  (`agent-index-server-package-split`, `agent-index-engine-daemon`);
  installer execution and installation-cell ownership are owned by
  `vendored-installer-engine` and `installation-cells` respectively. This
  effort scopes itself to the genuinely missing supply-chain layer:
  promotion-built content-addressed artifacts, governed-feed admission, and
  verified consumption.

### 2026-10-01 — Review findings incorporated before merge

- PR #4877's automated review (non-blocking `COMMENTED`) raised three
  design-level gaps in this planning-only effort, addressed directly in the
  Context/Plan/Validation Plan above rather than deferred:
  1. **Feed model mismatch** — promotion runs on public GitHub-hosted
     runners and cannot certify installability from any one consumer's
     private governed feed. Reframed: promotion pins an exact closure
     against a public reference index; the actual governed-feed-lag-aware
     admission check moved to a new **consumer-side, install-time** probe
     (Phase 1 design → Phase 3 implementation).
  2. **Trust-root gap** — a digest/provenance record in the same mutable
     artifact store as the wheel doesn't prevent co-replacement of both.
     Phase 2 now requires a concrete trust root (branch-protected committed
     digest, or a signed attestation against a pinned verifier key).
  3. **Fail-closed-on-tamper gap** — "no verified match" must distinguish
     genuine absence (safe from-source fallback) from a published artifact
     that fails verification (must fail closed with diagnostics, never
     fall back). Phase 3 and the Validation Plan now treat these as
     distinct, separately tested cases.

### 2026-10-01 — Second review pass: concrete decision + contract consistency

- A second review round on the first fixup found the prior pass incomplete:
  1. **Trust root left as an open choice, not a decision.** Committed to one
     concrete option: the manifest's expected digest is committed into
     promoted, branch-protected repository metadata (not a signed
     attestation against a separately pinned verifier key — rejected as
     unneeded key-management surface given branch protection already
     covers the same guarantee). Phase 2 and the Validation Plan now name
     this exact mechanism.
  2. **Umbrella issue contract mismatch.** The effort's refined admission
     model (promotion pins against a public reference index; the
     machine-specific governed-feed check moved to install time) silently
     diverged from umbrella issue #4876's original wording ("promotion
     fails before publishing ... when the closure is not governed-feed
     resolvable"). Updated #4876 to match the effort's single, now-
     authoritative contract.
  3. **Dated qualifiers in durable Context.** Removed "(review finding,
     2026-10-01)" labels from the Context section — that section describes
     the current system contract, not a review-history artifact; the
     review trail belongs only here in the Journal.

### 2026-10-01 — Third review pass: closure selection was not actually lag-tolerant

- A third review round found the deepest gap yet: resolving "newest
  compatible" against a public reference index — even with the install-time
  admission probe added — would routinely pin versions a real governed feed
  doesn't carry yet, since governed feeds lag public releases by about a
  week. The probe only *detects* that mismatch and falls back to the slow
  source path; it never actually made the fast path reachable in the common
  case, directly failing to satisfy the original request's explicit
  concern about excessive version lock. Fixed by adding a **seasoned-version
  constraint** to promotion's closure resolver (reject any candidate
  younger than a configurable minimum age, default 14 days; never simply
  "newest compatible"), so the pinned closure is very likely already
  mirrored on a real governed feed by the time of consumption. The
  install-time probe remains the authoritative confirmation for the
  residual cases. Phase 1 now measures real feed lag to tune the default;
  Phase 2 and the Validation Plan name the constraint explicitly.

### 2026-10-01 — Fourth review pass: vendored first-party lib closure was missing entirely

- A fourth review round found the plan never accounted for a plugin's own
  vendored first-party dependencies: `tools/materialize_main.py` copies a
  plugin's `tool.uv.sources` `libs/<lib>` path dependencies into its own
  tree during promotion (e.g. agent-bridge's several `agent-*` libs), and
  these have no governed-feed identity at all. A first-party artifact
  covering only the top-level plugin wheel would leave installers asking
  the governed feed for internal-only distributions and failing (or
  resolving an unintended same-named package). Fixed by scoping Phase 2/3
  to the **complete first-party closure** — a wheel for the plugin plus a
  wheel for every vendored lib it still declares after materialization,
  built, verified, and consumed together as one set, never resolved
  against any package index. Added a representative vendored-lib install
  test to the Validation Plan.

### 2026-10-01 — Fifth review pass: no publication channel; wrong branch for the trust root

- A fifth review round found two remaining gaps: (1) the plan never
  specified where a built artifact set actually gets published, so Phase 3
  "locate" logic had nothing durable to locate from — fixed by publishing
  promoted artifacts as GitHub Release assets on this repository, tagged
  deterministically from plugin name + promoted version; (2) the trust-root
  description incorrectly said the manifest is committed to `main`/`dev` —
  generated release metadata actually lands only on `main`
  (`validate-and-promote.yml:555-597`, `promote_release.py:15-18`), not
  `dev`, so the trust root is corrected to name `main` specifically (the
  payload an installed plugin actually ships from).

### 2026-10-01 — Sixth review pass: credential separation and build hermeticity

- A sixth review round found the trust root's separation claim was not
  actually established by the real credential configuration: the promotion
  PAT (Contents + PR write) can satisfy the `main` gate for any
  owner-authored `release/promote-*` branch, so if that same credential
  also publishes Release assets, compromising the artifact writer is
  sufficient to forge the digest it's supposed to be checked against —
  collapsing the intended separation. Recorded as an explicit, unresolved
  precondition: Phase 2 must introduce an independently constrained
  publisher credential (or a separately-keyed signature as defense in
  depth) and verify the actual isolation before implementing publication.
  A second, previously-missed finding: open-ended build requirements (e.g.
  `setuptools>=83.0.0`) mean the same payload/platform/ABI identity could
  select a different build backend and produce different bytes later, and
  an isolated build would otherwise reach a public index for that backend.
  Fixed by folding a governed-feed-resolved build-tool closure digest into
  artifact identity, with an offline repeat-build validation case.

