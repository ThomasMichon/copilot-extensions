# Installer Upgrade Recovery

- **Slug:** `installer-upgrade-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** sequential bounded contribution PRs targeting `dev`
- **Created:** 2026-11-03
- **Status:** Draft
- **Umbrella issue:** #10377
- **Related issues:** #9489, #7743, #9448, #7758, #10180, #6820, #7028, #7947
- **Vision:** [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)

## Guiding Intent

Drive high-impact installer/update compatibility failures to a verified usable
incoming installation through the latest supported explicit install/update
flow. Fresh-install success, a prepared candidate or a merged bug fix does not
prove that an affected older installation can recover.

This workstream closes the installer vision's existing authoritative replacement
intent; it does not extend it. Incoming readiness and every applicable current
selection define success. Old recovery protocols, provenance, stale installer
transactions and queue fences cannot veto explicit replacement. Actual incoming
admission, build, durable-state migration, permission, I/O and health failures
remain failures. Preserve user state and protected sessions; outgoing central
retirement remains independent of incoming completion.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Workstream driver | Generic issue reconciliation, selected source fixes, acceptance and publication | Managed source handle `dusty-contraption`; #10377 |
| Existing entry and sequencing owners | Retain fresh-entry, command migration and update sequencing implementation | #7743 and #6820 |
| Existing construction and execution owners | Retain canonical construction, source-managed installation and installation-request execution | #9489, #7028 and #7758 |
| Existing recovery and lifecycle owners | Retain their scoped queue, historical cleanup, durable-state and central lifecycle work | #9448, #10180 and #7947 |

These are coordination dependencies, not new assignments or ownership transfers.

## Coordination

- **Topology:** serial bounded source units, with normal independent PRs where
  their edit and acceptance scopes do not overlap.
- **Host:** Workstream driver owns this effort, its PRs and completion gate.
- **Delegates:** none selected. A future bounded delegate does not inherit the
  parent objective merely by completing a source slice.
- **Claims:** recheck the issue, current source/release and open PRs before
  implementation. Reuse an existing issue rather than duplicate it. Preserve
  contributor expressions and active claims; coordinate an overlap before
  changing its implementation.
- **Boundary:** this is a defect and upgrade-acceptance workstream, not a second
  installer, reset engine, artifact resolver, daemon, scheduler, provenance
  authority or repository-wide merge queue.
- **Handoff:** this README, [acceptance matrix](acceptance.md) and #10377 retain
  selected cases, owners, outcomes and the next source unit.
- **Effects:** source development, contained tests and normal source publication
  only. Shared installations, credentials, services, queues, sessions and
  deployment are not changed by establishing this plan.

## Context

Existing issues already describe unsupported UV options, an update entry behind
installed Python/Manager dependencies, first-foundation upgrades with existing
requests, and attributable historical cleanup. Some source corrections have
merged. An open issue does not prove that the original code defect survives,
and a source merge does not prove recovery through the released ordinary entry.

The workstream therefore starts from reproducible generic cases and actual
consumer contracts, not another broad rewrite or a copy of historical refusal
tests. The initial candidate families and their existing owners are in the
[acceptance matrix](acceptance.md). A candidate is not an implementation claim.

The authoritative references are the
[install contract](../../../docs/install-contract.md#authoritative-replacement-and-completion),
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
and current architecture adoption boundaries. Existing #6820 sequencing,
#7028 installation and #7947 state work remain canonical for their mechanisms.
New caller syntax must not assume that an arbitrary installed older runtime
already supports a capability. A reviewed architecture cutover uses its
documented fresh migration entry rather than pretending old protocols work.

## Request

Public-safe requested outcome, verbatim:

> So, we need to take the high-pri issues and drive fixes for them. We also need to ensure that for any update incompatibilities that we ensure our latest install/update flow will be able to recover the user into a good flow once applied

The requested coordination destination is:

> Yes, hoist relevant issues to copilot-extensions and drive a workstream up there

Implementation decomposition below is agent-recommended. The request does not
authorize production updates, recovery mutations or new access.

## Plan

### Phase 1 - Reviewed scope and issue ownership

- [x] Establish #10377 and reconcile adjacent public workstreams before claiming
  their existing source units.
- [ ] Land this canonical plan and acceptance matrix through repository review
  before implementation.
- [ ] Classify each selected candidate as a surviving source failure, released
  fix needing consumer proof, owned dependency or unsupported-contract migration.
  Hoist only additional generic gaps that do not already have an upstream issue.

### Phase 2 - Priority fixes and latest-flow recovery

- [ ] Select the first bounded, unclaimed reproducible source gap, or obtain
  explicit coordination with its existing owner. Record its old-footprint case
  before changing production code.
- [ ] Fix the owning operation and directly dependent callers; add focused
  red-on-old coverage and actual parser/process or installer consumers.
- [ ] Prove recovery through the latest supported normal install/update entry,
  including incoming readiness, complete current selection and preserved user
  state. Internal helper success is not whole-flow acceptance.
- [ ] Repeat for the selected priority cases, preserving existing construction,
  admission, durable-state and lifecycle owners instead of reimplementing them.

### Phase 3 - Release and completion

- [ ] Drive each source PR through review, required CI, merge and containing
  release observation. Use changefiles for affected plugins; do not hand-bump
  versions or equate `dev` merge with release.
- [ ] Record source, release and contained-consumer outcomes separately.
  Installed/affected-host qualification remains a separately authorized lane,
  not an inferred result or an implicit deployment obligation.
- [ ] Resolve every selected Plan/Validation item or transfer it explicitly to
  a named tracked objective, update affected reality documentation, and archive
  only when this parent gate is satisfied.

## Validation Plan

- [ ] For each selected incompatibility, start from representative broken or
  incompatible prior state and exercise the real latest entry, not only a
  pristine installation or a mocked successful recovery.
- [ ] Prove required incoming runtime/service readiness and all applicable
  selectors, routes, launchers and registrations. Distinguish package identity
  from running generation and whole-set completion from partial progress.
- [ ] Preserve configuration, credentials, repositories, worktrees, sessions,
  databases and accepted/uncertain effect history; repeat without manual cleanup.
- [ ] Retain actual incoming acquisition/build/migration/I/O/permission/health
  failures as failures. Preserve useful errors and confirmed child cleanup.
- [ ] Exercise healthy-current reuse, repeat convergence, source-worktree
  independence, automatic-only inhibition and protected-session/central-only
  retirement controls from [acceptance.md](acceptance.md).
- [ ] For changed peer boundaries, qualify current/prior callers and providers,
  supported rollback, missing metadata and unsupported requirements. Do not
  blindly replay an effectful operation with older syntax.
- [ ] Use focused unit checks, real subprocess/native fixtures and practical
  clean-room lanes. Keep broad native matrices opt-in, not added to required
  unit-only CI. State exact reasons for unavailable lanes.
- [ ] Audit every issue, effort, test, example, PR and commit for public-safe
  scope. Do not attach private reports, raw logs or deployment identifiers.

## Proposal

Start with update reachability and explicit/manual classification, followed by
the Windows construction/legacy-state cases that still reproduce. Existing
source corrections receive ordinary-entry recovery proof before another patch
is proposed. Select each concrete unit only after current owner and source
reconciliation; the matrix is not a blanket reservation of those components.

## Journal

### 2026-11-03 - Workstream established

Opened #10377 after checking existing installer/recovery issues and adjacent
efforts. The existing UV compatibility source PR #9501 is merged; #9489 still
needs its remaining evidence reconciled rather than a duplicate source fix.
Fresh-entry and first-foundation cases have existing owners and retained
qualification boundaries. This plan proposes canonical coordination and
acceptance, not completed implementation, release or installed recovery.
