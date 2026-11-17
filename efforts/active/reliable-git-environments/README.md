# Reliable Git and Daemon Environments

## Goal

Make Git-dependent consumers reliable when a long-running process has a stale
environment: use one shared explicit-aware absolute executable resolver and
fresh per-child daemon environments. Preserve the canonical Git/Copilot
mapping model without turning the repair into another copied identity or
repository-policy qualification layer.

- **Slug:** `reliable-git-environments`
- **Repo:** copilot-extensions
- **Branch(es):** managed source worktree; sequential plan and implementation PRs
- **Created:** 2026-11-16
- **Status:** Draft
- **Vision:** [Worktree identity and native mapping](../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity);
  [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #12939
- **Related owners:** #12427 / #12433 and #10212

## Guiding Intent

An installed Git executable must not become unusable merely because a resident
inherited an older PATH. Explicit selection and portable/nondefault installs
remain first-class. Fix the real dependency and launch context, not a local
ProgramFiles guess, permanent absence cache or a requirement to restart
Copilot. Executable discovery must not replay a possibly started operation.

Git owns checkout/worktree membership; Copilot owns sessions and workspace CWDs.
The proactive operational map is derived from those facts, while durable
workflow state and serialized mutation remain intact.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner, `hinged-chisel` | Shared Git/environment implementation, tests and PR stewardship | #12939 and this effort |
| Existing registration-policy contributor | Retains #12427 / #12433 | Existing issue and PR |
| Existing checkout-membership owner | Retains #10212 | Existing issue |

## Coordination

- **Topology:** Sequential plan and implementation PRs in one managed source worktree.
- **Host (owns PRs):** Source owner.
- **Delegates:** None.
- **Handoff:** Existing overlapping contributions retain custody; cooperate on
  their actual PR rather than replacing it. Public source delivery and any
  separately authorized installation remain different completion facts.

## Context

#12939 records the generic defect and claim. The current Worktrees Git wrapper
starts bare `git`; plugin activation independently caches Git lookup, including
absence. The current daemon environment starts from inherited process state
and then scrubs selected credentials/session fields.

#12433 changes registration-policy handling, and #10212 addresses Git-backed
checkout membership. This effort must not compete with either. Their
policy/mapping frontier is retained explicitly; refreshing PATH alone does not
declare the broader registration model corrected.

## Request

Public-safe request summary: provide the immediate repair through the existing
installation owner and deliver a durable source fix for shared Git resolution
and fresh daemon environments. Honor explicit Git selection, process PATH,
fresh Windows user/system PATH and installation-registry discovery; cache
successful resolution and rediscover confirmed executable disappearance.
Preserve native mapping reconstruction, write serialization and unknown effects.

This source effort contains only the generic implementation and qualification
scope. It does not publish private operational context or claim a deployment
grant. The immediate operational repair and any adoption evidence have their
own effect owners.

## Scope Approvals

Implement, test and land the generic source correction through the normal
dev PR, review, CI and changefile flow. Synthetic native lifecycle evidence
uses an isolated fixture and its normal lease/cleanup owner. No production
daemon restart, global PATH/account/access change, runtime-portfolio rollout,
installed-code patch or unknown-operation replay follows from this plan.

## Plan

### Phase 1 - Reviewed intent

- [ ] Land this source plan and the clarified native-mapping vision before runtime realization.
- [ ] Coordinate the mapping/policy boundary with the existing #12433 contributor, preserving that PR's ownership.

### Phase 2 - Durable source delivery

- [ ] Select one cohesive shared discovery owner and migrate the addressed real Git consumers to resolved absolute executables.
- [ ] Preserve explicit selection and portable PATH semantics; add Windows fresh OS PATH and installation-registry fallbacks without per-app installation guesses.
- [ ] Cache successful resolution; do not retain absence forever. Rediscover only confirmed executable disappearance before launch, not missing CWD, timeout or ambiguous creation.
- [ ] Build fresh per-child daemon environments with intended runtime overrides and existing credential/session scrubbing, without global environment mutation.
- [ ] Keep registration-model correction coordinated with its existing owner; do not extend Git-backed policy reconstruction to solve executable lookup or claim that PATH repair alone implements the native-mapping model.
- [ ] Land focused implementation through required source checks, changefiles, review and CI; distinguish merge from promotion and installation.

### Phase 3 - Source closeout

- [ ] Record source/test results and remaining independently owned mapping/adoption gates, then archive this finite source effort through the normal flow.

## Validation Plan

- [ ] Focused unit controls cover explicit selection, stale/process/portable PATH, Windows fresh PATH and nondefault registry discovery, missing-then-installed Git and positive-cache disappearance.
- [ ] No retry follows a possibly started command, timeout or a missing CWD; concurrent consumers do not mutate global environment.
- [ ] A real child consumer exercises absolute Git invocation under a stale inherited PATH rather than testing only a resolver return value.
- [ ] An isolated native service fixture proves fresh environment plus preserved selectors and credential/session scrubbing, using its existing lifecycle/lease cleanup owner.
- [ ] Mapping acceptance distinguishes Git/Copilot reconstruction from copied machine/platform policy and preserves journals, claims and unknown operations; coordinate the existing registration tests rather than weakening them silently.
- [ ] Record focused source gates and required PR CI separately from optional integration/native evidence, with honest platform limitations.
- [ ] Confirm merged source and final source-worktree/claim settlement; report promotion, installation and live adoption separately.

## Proposal

Prefer an existing suitable dependency-discovery owner. If none is cohesive,
use a shared process/dependency helper rather than application-local Git
installation lists. Windows registry metadata is a fallback, not the sole
portable-install path. Fail explicit invalid selection honestly instead of
silently switching to another executable.

Use the existing daemon lifecycle and cutover contracts. This changes neither
endpoint/protocol semantics nor cancellation/retirement authority. Evaluate
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
by incoming readiness and all current selections, preserving sessions/data
and treating outgoing retirement independently. A synthetic fixture is not a
production rollout.

## Journal

### 2026-11-16 - Source plan

Captured the generic dependency/environment defect, clarified the native
mapping intent and separated overlapping registration-policy ownership.
No runtime source implementation is included in this planning change.
