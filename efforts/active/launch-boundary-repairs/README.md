# Launch Boundary Repairs

- **Slug:** `launch-boundary-repairs`
- **Repo:** copilot-extensions
- **Branch(es):** independent repair PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Draft
- **Vision:** existing plugin-services progress/timeout and explicit-failure contracts
- **Umbrella issue:** #9520
- **Sub-issues:** existing #5512 and #5761 retain their original scope

## Guiding Intent

Make confirmed launch failures observable and actionable without guessing their
cause, weakening repository admission, replaying prompts or changing an installed
service as a side effect of source work.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Repair coordinator | Source integration, validation and PR stewardship | Source-attributed repair PRs |
| Evidence reviewers | Read-only, disjoint source inspection | Coordinator-provided evidence packets |

## Coordination

- **Topology:** independent per-slice PRs; serialize changes sharing an owner.
- **Host (owns PRs):** Repair coordinator.
- **Delegates:** Evidence reviewers own no source edits or live resources.
- **Handoff:** Each source result includes exact validation and retained blockers.
- Existing #5512 owns exclusive monitor replacement and actual serving readiness;
  this effort does not replace that design with another restart protocol.
- Existing #5761 owns namespace discovery latency and result distinctions.
  Contribute through that owner rather than creating a second discovery engine.
- Open #9322 owns Windows Bridge PATH refresh; do not supersede its author.
  Reconcile any further Git executable requirement against that PR first.

## Context

#9520 records a bounded repair intake. Current source still admits a guest
repository through an already-installed Worktrees receiver before plugin
preparation; relay readiness makes SSH calls with a five-second timeout; failed
readiness observations have no retained diagnostic outcome; the shared
owned-delivery parser and detached-launch refusal consumer omit some refusal
details; monitor restart prints spawn failure while returning zero. The normal
typed/deferred/unconfirmed/lost pending-seed helper is not a repair target.

Installer activation already has launch-health gates. Those gates do not prove
that a guest preparation path validates a command it finds. Likewise, a
CodeSpace reaching `Available` does not prove its hosting-policy receiver exists.
Validate these actual consumer boundaries rather than adjacent installer checks.

Intentional seed-size limits, retired wrappers and optional browser capabilities
are not defects to remove. A readiness timeout is an incomplete observation,
not proof that a plugin is missing. Historical failures without sufficient
evidence remain unclassified.

## Request

The operator requested: "Fix it".

Scope summary: repair the confirmed infrastructure gaps discussed in the
preceding audit, validate them and steward their source contributions. The
implementation sequence below is agent-recommended. Source changes do not
authorize a shared runtime rollout, service restart or live venue retry.

## Plan

### Phase 1 - Review and ownership

- [ ] Land this proposal after the repository's required review and CI.
- [ ] Check current source, claims and open PRs for every repair; record already
  fixed, intentional and evidence-dependent items without speculative edits.

### Phase 2 - Explicit failure and diagnostic preservation

- [ ] Make monitor restart report a real spawn failure through its exit status;
  preserve disabled/already-current behavior and existing installer advisory
  handling. Do not equate spawning with acquired ownership or serving readiness.
- [ ] Preserve structured refusal details through the shared owned-delivery
  parser and detached-launch refusal consumer, without granting resend or
  replacement authority. Preserve the normal pending-seed helper's existing
  typed/deferred/unconfirmed/lost behavior.
- [ ] Retain bounded, sanitized failed readiness observations through ordinary
  control cleanup, reusing the existing storage owner and retention machinery.
  Preserve control cancellation and successful cleanup semantics.

### Phase 3 - Preparation and deadline correctness

- [ ] Repair fresh-guest preflight ordering through the canonical hosting-policy
  owner, preserving denial before launch effects and avoiding a second evaluator.
- [ ] Verify and repair present-but-unhealthy guest preparation using the owning
  health/repair contract; do not merely add another command-existence check.
- [ ] Reconcile relay SSH and namespace discovery deadlines with their enclosing
  budgets and current progress contract. Prove slow healthy and failed cases;
  do not inflate constants or shorten an existing healthy allowance blindly.
- [ ] Address remaining Git/PATH requirements cooperatively with #9322 and other
  active owners; do not open a competing implementation.
- [ ] Account for monitor ownership/readiness and drain admission-lock timing
  against #5512 and the documented drain contract, not a local replacement design.

### Phase 4 - Delivery and disposition

- [ ] Land each eligible source repair with mandatory changefiles, required CI
  and real current-head reviews; finalize its dedicated source worktree.
- [ ] Record the disposition of every intake area, including exact external
  owners or missing evidence for items not safely implementable here.
- [ ] Close this source effort only when every implementation and validation
  item is resolved or explicitly transferred to a named tracked objective.

## Validation Plan

- [ ] Add focused red/green regressions for each changed behavior.
- [ ] Exercise the real affected consumer boundary in an isolated integration
  control, including refusal propagation and unchanged success behavior.
- [ ] Prove refusal details survive the owned-delivery parser and detached-launch
  consumer; keep typed/deferred/unconfirmed/lost pending-seed reporting unchanged,
  and prove refusal or uncertain acknowledgement does not authorize replay.
- [ ] Exercise fresh-guest and present-but-unhealthy preparation in disposable
  fixtures where practical; never use a shared guest as an implicit test target.
- [ ] Run changed-plugin tests through the canonical contained runner, focused
  Ruff checks and applicable install-contract/generated-asset checks.
- [ ] Account explicitly for native Windows/POSIX and clean-room qualification;
  a mocked result is not live or installed acceptance evidence.
- [ ] Record reasons for any skipped external lane. Live service mutation,
  installed rollout and venue retries remain outside this source-only scope.
- [ ] Verify provider merge state and managed finalization for every opened PR.

## Proposal

The checklisted sequence is agent-recommended. Prefer existing producers,
owners, result shapes and retention policies over new services, configurable
frameworks or heuristic error-text scrubbing. No historical root-cause claim
depends on the mere existence of one of these source defects.

## Journal

### 2026-10-29 - Intake

- Claimed #9520 after searching current issues and open PRs.
- Read-only evidence identified a misleading installer/admission comparison;
  checked the actual guest hosting-policy consumer before classifying it.
- Existing PATH and monitor/discovery owners are preserved explicitly.
- No source repair, installed update, service restart or live venue retry has
  occurred as part of this proposal.

### 2026-10-29 - Plan clarification

- Applied the supplemental guidance on #9521: Context, Phase 2 and focused
  validation now name the owned-delivery parser and detached-launch refusal
  consumer, explicitly preserving the unrelated pending-seed helper.
- The guidance is not Maintainer acceptance. Implementation still awaits the
  proposal's required acceptance, current-head review and CI.
