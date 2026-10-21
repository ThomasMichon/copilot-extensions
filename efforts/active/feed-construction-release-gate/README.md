# Feed Construction Release Gate

- **Slug:** `feed-construction-release-gate`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed worktree; proposal and implementation target `dev`
- **Created:** 2026-10-20
- **Status:** Draft
- **Vision:** extends [Source-Owned Release Pipeline](../../../visions/release-pipeline/README.md#configured-feed-construction-and-first-launch); realizes governed runtime assembly and independent installer readiness
- **Umbrella issue:** #7529
- **Related owners:** #3336, #6237, #4876, #6476, #7420, #7446, #7514

## Guiding Intent

Do not publish a release whose declared Python environments cannot be constructed
and launched under the selected package-feed restrictions. Source CI and
promotion must exercise the real configured feed, complete environment inventory
and installed entry points rather than infer availability from public release
timestamps, a wheel build or a warm developer environment.

This deliberately amends the previous exclusion of all installation execution
from promotion. The exception is bounded construction and first launch, not the
general integration portfolio. Existing eligible-unit selection and its coverage
packet remain unchanged. The existing end-to-end release latency objective also
remains; parallelism, authoritative inventory and justified equivalent-input
reuse must not become silent omissions.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Gate driver | Own proposal, generic qualification mechanism, evidence and completion | #7529 and its managed source worktree |
| Inventory evidence worker | Read-only census of canonical environment owners and construction/health declarations | Gate driver; no edit or publication authority |
| Release/control owners | Accept the existing CI and trusted promotion integration seams | #3336 and #6237 |
| Existing repair owners | Retain Manager construction/update and mirror-age admission implementations | #7420, #7446 and #7514 |
| Repository owner and automated reviewer | Review the policy exception, implementation and activation evidence | Normal repository PR flow |

## Coordination

- **Topology:** proposal first, then bounded implementation PRs against `dev`.
- **Host (owns PRs):** Gate driver.
- **Delegates:** inventory evidence only; no shared-worktree writers.
- **Scope:** reuse canonical installer, dependency-policy, runtime-health and
  artifact mechanisms. No parallel installer or promotion writer.
- **Shared files:** coordinate CI, trusted-controller and publication edits with
  #3336/#6237 before changing them. Existing runtime and seasoning repairs are
  dependencies, not competing implementations.
- **Handoff:** retain this effort's full completion gate, exact current source,
  owned PRs and outstanding platform/feed/control obligations.

## Context

- The declaration audit checks canonical public release metadata. It does not
  prove availability or resolver behavior through a configured consumer feed.
- First-party artifact production does not construct every consumer environment;
  standalone runtimes and selected optional/profile environments require explicit
  coverage rather than inference from a top-level plugin wheel roster.
- The existing release vision expressly excluded installer and generated-consumer
  integration prerequisites. This proposal changes that intent explicitly before
  implementing the narrow exception.
- Actual feed reachability from a hosted CI worker is unqualified at proposal
  time. Success from another environment is not that proof.
- Feed coordinates and authentication remain external adopter configuration.
  The operator authorized masked Actions configuration; no real feed, private
  deployment identity or credential belongs in this public effort or source.

## Request

The operator's immediate request (verbatim):

> Right. We need this pronto to prevent an outage. We need to ensure that every venv is constructable under the feed resrictions, during CI and promotion, so that we don't release something that can't launch

The operator confirmed the effort slug and authorized externally configured,
masked Actions feed selection. Provider-specific coordinates are intentionally
not reproduced here. The inventory, execution and evidence design below is
**agent-recommended**; it must clear review before implementation.

## Plan

### Phase 1 - Review the policy and complete inventory

- [ ] Review and merge this release-vision exception and canonical effort.
- [ ] Reconcile the complete environment census against existing authoritative
  declarations: runtime plugins, standalone applications, bootstrap/build
  environments and selected optional/server/profile closures.
- [ ] Record canonical construction and installed-health entry points and the
  supported platform/profile matrix, required extras and actual interpreter
  architecture/ABI; reject unclassified owners rather than maintain a permissive
  hand-written skip list. Classify development-only fixtures explicitly rather
  than confuse them with shipped runtime owners.
- [ ] Obtain accepted non-overlapping CI/trusted-controller integration seams
  from the existing release/control owners.

### Phase 2 - Implement bounded, feed-preserving qualification

- [ ] Reuse canonical dependency admission and construction in disposable roots
  with fresh environments and no success supplied by a prepopulated cache.
- [ ] Preserve configured index order, authentication/TLS, age policy and selected
  profile constraints, including bootstrap and isolated-build dependencies.
- [ ] Prove installed package origin and supported entry-point first launch in
  fresh processes, without importing the source checkout or starting host-owned
  services. Identify the actual launch boundary each result proves.
- [ ] Emit complete, source/candidate-bound inventory results; missing, duplicate,
  failed, skipped, timed-out or unavailable-feed results refuse qualification.
- [ ] Keep the provider-neutral runner bootstrap independent of unrelated broken
  product graphs; missing required feed configuration is an explicit failure.
- [ ] Reuse the existing trusted CI admission boundary. Candidate construction
  receives no publication/signing authority, and an untrusted PR never gains
  privileged feed credentials through a trusted-trigger workaround.

### Phase 3 - Earn actual CI and candidate evidence

- [ ] Prove configured-feed access from an actual hosted Actions worker.
- [ ] Exercise every admitted native platform/profile row using real
  construction and installed launch, not cross-platform resolution alone.
- [ ] Bind proof to the immutable source and composed candidate plus inventory
  and package-policy inputs. Reuse only independently verified equivalent inputs,
  never a stale or another-platform result.
- [ ] Preserve the separate eligible-unit proof and whole-release latency
  objective; do not restore unrelated integration suites.

### Phase 4 - Activate and complete release protection

- [ ] Require complete qualification in contribution CI and before candidate
  publication/promotion through the existing trusted control owner.
- [ ] Qualify the exact active Main controller's compatibility and deliver any
  control change through its reviewed activation path.
- [ ] Demonstrate a negative construction/launch result actually prevents
  publication/merge, then a fully qualified candidate can complete normally.
- [ ] Update actual pipeline/install documentation, record the merged source and
  activation evidence, resolve every plan/validation item and mark this effort
  Done. Source merge alone is not activation or runtime rollout.

## Validation Plan

- [ ] Unit regressions reject unknown/missing/duplicate inventory rows, altered
  source/policy bindings and incomplete or forged qualification results.
- [ ] Deterministic policy fixtures cover exact age boundaries and a seasoned
  canonical artifact with a recently rewritten mirror timestamp, without
  weakening the existing admission owner's rules.
- [ ] Real configured-feed construction covers transitive, build and selected
  optional dependencies, with no public-index fallback.
- [ ] A deliberately unavailable dependency, failed installed import/entry point,
  unavailable feed and exhausted deadline each produce a visible failed gate.
- [ ] Installed-origin and fresh-process launch controls cannot pass from a
  source-tree import, an old environment, another interpreter architecture or a
  previously running service.
- [ ] Untrusted candidate execution cannot acquire signer/publisher credentials;
  missing authorization or feed configuration is visible, not a successful skip.
- [ ] Actual hosted-worker evidence covers the complete declared native matrix;
  unavailable capacity remains a blocker, not a skip or implied qualification.
- [ ] Real CI and promotion negative controls prove the gate is required, not
  merely an informational successful job.
- [ ] Record actual execution/queue duration and preserve the release budget.
- [ ] Existing installer/rollback, publication integrity, required review and
  eligible-unit proof contracts remain intact.

## Proposal

Pending automated review. No construction runner, CI controller, feed
configuration, runtime deployment or enforcement cutover is implemented by this
proposal.

## Journal

### 2026-10-20 - Urgent release-protection proposal

- Claimed #7529 after checking adjacent artifact, seasoning, runtime and
  promotion ownership.
- The operator confirmed the slug and masked Actions feed configuration.
- Requested coordination with #3336/#6237 before shared workflow edits.
- Started a read-only inventory census; implementation waits for reviewed intent.

### 2026-10-21 - Initial census and installed-launch boundary

- The initial read-only census identifies thirteen runtime plugins, standalone
  Manager and index-engine environments, and selected index server, engine and
  optional MCP closures. Shared libraries are construction inputs, not
  automatically separate environments.
- Bootstrap and isolated-build requirements belong in the qualification
  closure. The complete native platform/interpreter architecture/ABI and
  profile/extras matrix is still unclassified; this is not a completed
  inventory or permission to omit a row.
- A real installed launch exposed a separate headless Picker readiness defect
  tracked in #7638. A version probe or a successful environment construction is
  therefore not interchangeable with installed consumer first-launch evidence.
- Existing installation repairs remain separately owned. No construction runner,
  workflow enforcement or feed secret has been activated by this proposal.
- Reconciled the test-portfolio vision's blanket integration exclusions with
  the same bounded construction/launch exception; unit eligibility, coverage
  and the general periodic integration portfolio remain unchanged.
