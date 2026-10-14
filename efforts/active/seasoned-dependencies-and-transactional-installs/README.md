# Seasoned Dependencies and Transactional Installs

- **Slug:** `seasoned-dependencies-and-transactional-installs`
- **Repo:** copilot-extensions
- **Branch(es):** One managed coordinator branch; sequential proposal and implementation PRs
- **Created:** 2026-10-13
- **Status:** Draft
- **Vision:** [plugin-services](../../../visions/plugin-services/README.md): seasoned external dependencies and immutable, failure-safe runtime construction
- **Umbrella issue:** #6476
- **Related issues:** #1181 (incomplete-slot recovery); #5472 (same-version immutability)

## Guiding Intent

External dependencies must have been released for at least seven days before
being declared or selected for installation. Installers respect the adopter's
configured feed rather than bypassing it. A failed dependency installation is a
failed candidate build, never a partially successful runtime publication.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Proposal, shared policy, integration, PRs and rollout | Managed contribution branch |
| Declaration evidence | Manifest and configured-feed eligibility audit | Bounded read-only evidence task |
| Windows evidence | Windows installer failure and publication seams | Bounded read-only evidence task |
| POSIX evidence | POSIX installer failure and publication seams | Bounded read-only evidence task |

## Coordination

- **Topology:** One coordinator-owned implementation branch and sequential PRs.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Evidence only; implementation ownership is assigned explicitly.
- **Handoff:** Compact cited evidence returns to the coordinator; delegates do not
  mutate Git, installed payloads, running services or adopter configuration.
- Existing owners of #1181 and #5472 retain their distinct objectives. This
  effort closes only the overlapping requirements its own implementation proves.

## Context

A recent dependency-minimum update (#6362) requires a package version absent
from a configured feed. Public release availability alone does not establish
feed eligibility: a proxy's artifact upload timestamp may be later than the
public release timestamp, and a real seven-day resolver cutoff can reject an
otherwise available version.

Existing implementation owners are
[`libs/installer-engine`](../../../libs/installer-engine/),
[`libs/versioned-runtime`](../../../libs/versioned-runtime/) and
[`libs/installation-context`](../../../libs/installation-context/).
The [install contract](../../../docs/install-contract.md) already requires
completion-gated selection, immutable completed slots and provenance-aware
cleanup. Their existence does not prove every shell installer has enrolled its
construction lifetime.

## Request

Operator input, in sequence:

> We need to ensure that if any dependencies fail to install, we don't install
> the slot; don't leave it in a bad state. Help make the fixes to package versions
> so everything can install cleanly.

> We need to adjust our package version dependencies so be compatible with the
> [configured package feed].

The bracketed substitution removes private deployment context without changing
the feed-compatibility requirement.

> Essentially, copilot-extensions needs a rule that any declared dependency
> needs to have been released for at least a week.

The operator selected **declarations-and-installation** when asked whether the
seven-day rule also applies to versions selected by runtime installers, rather
than only to declared minima. The operator confirmed this effort's slug.

## Plan

### Phase 1 - Reviewed policy and scope

- [ ] Land this proposal and the corresponding standing vision intent through
  the repository's review gate before implementation.
- [ ] Establish a seven-day rule for external build, runtime, development and
  selected optional dependencies; retain an explicit exemption for attributable
  repository-owned source dependencies, not their external dependency closure.
- [ ] Identify all package-manager and installer entry points that need the
  policy, including the repository's npm tooling. Unknown release metadata must
  not count as eligible.

### Phase 2 - Declarations and resolved installation

- [ ] Adjust incompatible version floors to allow week-old artifacts from the
  configured feed, preserving Python/platform support and existing upper bounds.
- [ ] Add declaration-age enforcement and dependency-update cooldowns without
  hardcoding any adopter's feed or identity. Weekly scheduling alone is not a
  release-age gate.
- [ ] Apply a bounded, fail-closed seven-day cutoff to actual dependency
  resolution, including transitive and isolated build dependencies. Honor a
  stricter cutoff; refuse weaker or unsupported bypass paths.
- [ ] Cover UV, any retained pip paths and npm installation consistently.
  Unsupported age enforcement must fail explicitly rather than silently fall
  back to an unrestricted resolver.

### Phase 3 - Failed construction never publishes

- [ ] Wire failure cleanup through canonical shared primitives and synchronize
  every affected independently shipped installer on Windows and POSIX.
- [ ] Fail the build if any selected dependency fails, including a selected
  extra; do not convert that failure into success by retrying a reduced
  capability in the same partially populated environment.
- [ ] Publish completion, active selection, manifests and service cutover only
  after the selected dependency closure and health gates succeed.
- [ ] Remove only the failed build's owned, incomplete candidate. Preserve
  completed same-version slots, the prior healthy runtime, unrelated builders,
  selector/retention ownership and installation-cell receipts. Never rename a
  built venv as a shortcut that leaves embedded paths invalid.
- [ ] Surface cleanup errors and keep any unreclaimed candidate nonselectable;
  process interruption must not turn an incomplete directory into a runtime.

### Phase 4 - Release, rollout and conclusion

- [ ] Update authoritative operating documentation and add changefiles for
  every affected plugin.
- [ ] Land implementation through real review and required CI, then verify the
  promoted release's exact contents rather than relying on commit ancestry.
- [ ] Run the authorized unified updater and prove clean dependency resolution,
  completed-slot selection and healthy retained/cut-over services.
- [ ] Resolve every Plan and Validation Plan item, journal the outcome, archive
  this effort, release its completed binding and finalize the managed worktree.

## Validation Plan

- [ ] Unit-test the exact 604800-second boundary, future/unknown timestamps,
  yanked or incompatible artifacts, local-source exemptions, stricter cutoffs
  and forbidden package-manager bypasses.
- [ ] Exercise the real resolver against a controlled index with old, new and
  missing-timestamp artifacts, including a transitive and isolated build
  dependency. Exercise npm's actual admission path with controlled metadata.
- [ ] Run native contained Windows and POSIX installer failures after partial
  dependency writes; prove nonzero exit, no completed/reusable failed slot,
  unchanged prior runtime/selectors and no service cutover.
- [ ] Prove same-version retry succeeds from a clean candidate and that failure,
  contention, interruption and cleanup denial cannot alter a completed or
  differently owned slot.
- [ ] Exercise fresh installation without prior runtime state, plus update with
  a live retained healthy runtime. Record clean-room evidence or an explicit
  unavailable-lane reason; do not substitute mocked helpers for real lifecycle
  boundaries.
- [ ] Run targeted tests, touched-code lint, install-contract/fan-out guards and
  required CI. Keep the required lane focused and put exhaustive matrices in
  the existing full portfolio.
- [ ] Audit declared minima and prove actual installation through the configured
  feed with the seven-day cutoff, without modifying or bypassing that feed.
- [ ] Verify the deployed candidate's completion and service health, and persist
  rollout evidence before declaring the objective complete.

## Proposal

Extend the plugin-services vision with a dependency-seasoning floor and make
its existing immutable-runtime guarantee explicit for failed construction.
Implement through the existing shared installer, resolver and ownership seams;
do not introduce a new service, installer control plane or private feed policy.
This is a proposal, not a claim that those mechanisms are already deployed.

## Journal

### 2026-10-13 - Proposal

- Captured the operator's dependency, seven-day installation and failed-slot
  requirements; the configured-feed substitution is the only redaction.
- Claimed #6476 and completed bounded declaration, feed-timestamp, Windows and
  POSIX evidence passes. A real cutoff probe showed why public package release
  dates alone cannot establish configured-feed installation eligibility.
- Implementation has not started; this proposal must clear review first.
