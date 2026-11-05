# Repository Write Guidance

- **Slug:** `repository-write-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed plan, implementation and completion PRs
- **Created:** 2026-11-05
- **Status:** Draft
- **Vision:** [Cooperative write guards](../../../visions/plugins/agent-worktrees/README.md#write-guards-are-hygiene-nudges-named-for-their-plain-mundane-reason)
- **Umbrella issue:** #10778
- **Sub-issues:** none

## Guiding Intent

Deliver repository rules of engagement at the first relevant write attempt:
read-only access, an isolated worktree, required elevation, or the repository's
own agent. Reject that attempt with actionable guidance and the existing
time-bounded break-glass route. This is a cooperative workflow nudge, not
comprehensive interception of arbitrary process writes or adversarial security.
Globally installed Worktrees must remain inert for unmanaged sessions.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driver | Plan, source changes, validation, PR stewardship and completion | Externally controlled isolated source worktree |

## Coordination

- **Topology:** sequential PRs owned by one driver.
- **Host (owns PRs):** Driver.
- **Delegates:** none.
- **Handoff:** the canonical README retains the current slice and remaining gates.
- Existing managed-session, guard-map, session-projection, installer and
  performance owners remain authoritative; this effort does not replace them.

## Context

The current native command hook supplies `toolCalls[]`; `apply_patch` arguments
are a freeform patch rather than a JSON object with a path. The command-policy
entry and write-target classifiers miss these common forms. A real managed
denial control therefore failed despite positive admission and a complete
native-produced guarded-root map. An older path-shaped event still denied.

The [circumstantial hook contract](../../../docs/patterns/circumstantial-hook-contracts.md)
separates reusable facts from event-time decisions. The
[prepared guard map](../../../plugins/agent-worktrees/docs/guarded-anchor-map.md)
already owns caller-scoped topology acquisition outside the hot path.
The native session sidecar represents binding and control separately; its
mere existence is not necessarily current-workspace membership.

## Request

The operator requested: "Correct. Drive an effort to build this out to merge and resolution".
The complete premise and subsequent session-marker suggestion are preserved
verbatim in [inception-transcript.md](inception-transcript.md).

## Scope Approvals

The quoted request authorizes this generic source campaign and its normal
review/merge flow. It does not authorize unrelated source changes, elevation,
new access, destructive production actions, fleet deployment, or guard
disablement. Any installed rollout uses its deployment owner's independently
applicable authorization. Disposable tests retain exact ownership and cleanup.
The previous failed live control remains failed; no replay or normalized input
will be relabeled as native acceptance.

## Plan

### Phase 1 - Reviewed contract
- [x] Capture the premise and marker suggestion; reconcile to the existing vision.
- [ ] Land this proposal through the repository's required review and CI gates.

### Phase 2 - Common native write forms
- [ ] Normalize legacy single-call and current batched command-hook inputs in
  the existing policy owner, preserving one composed deadline and all decisions.
- [ ] Extract native freeform patch targets, including add/update/delete/move,
  and common structured edit targets through the existing guard family.
- [ ] Consume prepared caller-scoped repository posture facts for targeted
  worktree/read-only/elevation/delegation reminders; reuse existing policy owners.
- [ ] Use bounded root matching only on plausible write arguments as a fallback
  where necessary; preserve read and quoted-data behavior.
- [ ] Retain first-attempt rejection, actionable workaround and existing live
  scoped break-glass expiry/revocation, without caching a tool permission.

### Phase 3 - Cheap admission and actual consumers
- [ ] Reuse the native session association as the cheapest available admission
  fast path when it establishes the current session/workspace binding.
  Controller-only, unrelated, stale or incomplete markers must not activate
  management. Preserve the passive owner fallback for compatible legacy starts.
- [ ] Keep admission, command policy and resident policy aligned without new
  per-event topology discovery, subprocess-per-repository work or daemons.
- [ ] Prove actual native managed denial, unmanaged allowance, and scoped
  break-glass behavior against disposable repositories; qualify the exercised
  driver/hook lane rather than trusting flags or descriptors alone.

### Phase 4 - Merge and resolution
- [ ] Complete documentation, contract and performance-impact assessments;
  land the implementation after review and required CI.
- [ ] Observe release and any authorized installed adoption needed for the
  acceptance claim; retain uncompleted deployment gates explicitly.
- [ ] Resolve the coordination issue only after its complete acceptance gate,
  journal outcomes, mark Done, land the archive, and finalize owned worktrees.

## Validation Plan

These implementation choices and checks are **agent-recommended** realizations
of the captured operator intent, not additional operator grants.

- [ ] Focused regressions for legacy/batched hooks, mixed read/write batches,
  all native patch target directives, common edits and boundary-safe matching.
- [ ] Reads and quoted path data remain allowed; malformed/unknown input retains
  the existing actionable fail-open contract without inventing evaluation.
- [ ] Admission covers no marker, controller-only marker, matching bound
  workspace, changed CWD, stale/revoked inputs and registry-only repositories.
- [ ] First write rejects with the correct posture and recovery; scoped
  break-glass permits the intended retry, not another repo, and expires/revokes.
- [ ] Unit/lint/install-contract gates pass against the final source; preserve
  the required unit-only CI portfolio and explicit native integration lanes.
- [ ] An isolated subprocess check exercises the shipped command-hook entry,
  not just a pure classifier; a fresh fixture exercises first-touch behavior.
- [ ] A live native A/B comparison proves real tool outcomes and actual
  hook/driver participation; preserve failed attempts and exact cleanup.
- [ ] Measure cold/warm composed admission and policy under a representative
  root inventory against the existing two-second hook target. Report sample
  counts, wall time, added work and limitations; do not claim tail qualification
  from a small sample. Broader telemetry ownership remains #7935.
- [ ] Record an explicit reason for every unavailable platform/external tier;
  do not turn missing evidence into a pass or authorize an alternative venue.

## Proposal

Pending review. This campaign closes the existing cooperative-guard vision
gap, with no new sandbox, process interception, service, or security guarantee.

## Journal

### 2026-11-05 - Kickoff
- The operator confirmed guidance-first behavior and requested delivery through
  merge and resolution. The native marker fast-path suggestion was captured.
- Claimed #10778 after scoped issue/PR overlap searches. Implementation waits
  for the reviewed plan; prior failed native controls remain diagnostic input.
