# Acknowledged Execution Handoff

- **Slug:** `acknowledged-execution-handoff`
- **Repo:** copilot-extensions
- **Branch(es):** independent, sequential PRs against `dev`
- **Created:** 2026-10-19
- **Status:** Blocked
- **Vision:** [Dispatch execution handoff](../../../visions/plugins/agent-dispatch/README.md#acknowledged-execution-handoff), preserving [reviewer lineage](../../../visions/plugins/agent-dispatch/reviewer/README.md#one-review-one-lineage)
- **Umbrella issue:** [#7268](https://github.com/ThomasMichon/copilot-extensions/issues/7268)

## Guiding Intent

Let an authorized adopter transfer execution of a bounded Dispatch scope
without overlapping writers, losing existing tasks or interrupting unrelated
work. An accepted request is not an acknowledged handoff. Dispatch owns task
admission and execution accounting; the adopter owns domain effects and policy.

**Current authorization is design-only.** A reviewed plan does not authorize
implementation, deployment, process termination or any live handoff.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Proposal owner | Generic contract, proposal PR and issue coordination | Assigned owner of #7268 |
| Independent reviewer | Review the proposal through the normal repository gate | Proposal PR review |
| Implementation owner | Future runtime and validation slices, only after authorization | Not assigned |

## Coordination

- **Topology:** one proposal PR, then separately authorized implementation PRs.
- **Host (owns PRs):** Proposal owner.
- **Delegates:** Independent reviewer owns review only; no implementation lane.
- **Handoff:** Record decisions and blockers here and in #7268. Preserve existing
  lifecycle, remote-driver and task-session-resume owners; do not take their work.
- This public effort is canonical for the generic contract. Adopter-specific
  policy, operational permission and effect integration remain adopter-owned.

## Context

Existing task holds and producer-generation fences are useful primitives, but
production fencing is not proof that existing executions and their external
effects are quiescent. Reuse those authorities rather than adding another task
queue, scheduler or process registry.

This is a **vision extension**: the parent Dispatch vision gains an explicit
optional execution-handoff guarantee. The design serves its atomic-claim and
visible-recovery intent and preserves the reviewer vision's
`one-review-one-lineage` and `reuse-before-replacement` guarantees.
It follows [patterns](../../../docs/patterns/README.md): primitives below,
orchestration above; attributable installation ownership; cross-platform parity;
and proportionate safeguards against concrete stale-writer races.

Adjacent work is distinct: #6677 concerns manual task-session exit/resume;
#3208 concerns atomic Bridge end-and-reclaim; #5930 concerns remote-driver
mutation ownership. None is permission to replace an active owner.

## Request

Accepted planning request, quoted as a public-safe excerpt with the private
status preamble omitted:

> May I search for and claim—or create—a public upstream issue and submit a
> design-only effort/contract PR there? It would contain no private references or
> facility details and would not implement, deploy, or activate takeover controls.

The operator confirmed the slug `acknowledged-execution-handoff`.
The [contract proposal](contract.md), phased implementation decomposition and
validation matrix below are **agent-recommended**, not additional operational
grants.

## Plan

### Phase 1 - Review the contract
- [x] Land the vision extension, generic [contract](contract.md) and this effort
      through normal proposal review.
- [x] Resolve review findings and identify the existing authorities to extend;
      do not implement a parallel lifecycle or effect store.
- [x] Record proposal landing and the next authorization boundary.

### Phase 2 - Separately authorize implementation
- [ ] Obtain explicit implementation scope and assign an implementation owner.
- [ ] Extend the existing coordinator/supervisor admission and drain accounting,
      including complete existing-assignment enumeration and legacy refusal.
- [ ] Define the adopter effect-fence adapter and implement generation-bound
      mutation admission without conflating effect authority with task ownership.
- [ ] Implement durable recovery, observation and explicit handback using the
      same tasks, workspaces and recorded session lineage.

### Phase 3 - Validate and qualify adoption
- [ ] Exercise the [validation matrix](contract.md#validation-matrix) with focused
      unit coverage and isolated periodic process/effect rehearsals.
- [ ] Complete platform and mixed-version capability checks; retain explicit
      unsupported results rather than success-shaped fallback.
- [ ] Obtain separate release/adoption permission, then prove the running
      supported cohort and an authorized handoff/handback before claiming live.

## Validation Plan

- [x] Proposal review confirms scope, owner boundaries, final-effect ordering,
      conservative legacy handling and a validation obligation for each race.
- [ ] Deterministic unit tests prove admission, generations, stale
      acknowledgements, replay, timeout, crash recovery and handback.
- [ ] Concurrent scope enumeration cannot omit queued, claimed, starting,
      running, suspended or pending-wake assignments, including work in flight
      when admission closes; unrelated scopes continue normally.
- [ ] A stale writer cannot begin an effect after revocation acknowledgement;
      already accepted or ambiguous effects block acknowledgement until resolved.
- [ ] Isolated actual-process and fake-external-effect rehearsals prove both
      transition directions, interruption and delayed responses without killing
      unowned processes or executing real adopter effects.
- [ ] Native task/workspace/history identity and author consent survive the
      transition; explicit session transfer is never reported as same-native reuse.
- [ ] Required PR/promotion validation remains focused unit coverage; exhaustive
      process, native and external-effect tests use scheduled/manual containers.
- [ ] Source merge, release, installation and running adoption are reported
      separately; every Plan item is resolved or transferred before Done.

## Proposal

Read [contract.md](contract.md). It is prospective design, not a supported
command or an instruction to operate a running system. The design landed in
[#7270](https://github.com/ThomasMichon/copilot-extensions/pull/7270).
The next action is explicit implementation authorization and owner assignment
under #7268. Until then, Phase 2 is blocked; review approval is not that grant.

## Journal

### 2026-10-19 - Proposal landed; implementation gated
- #7270 merged to `dev` as `cb27a321a1f6066fe910176e647e3b72f860091a`
  after current-head Copilot approval and the required PR gate passed.
- Phase 1 and proposal-review validation are resolved. Runtime implementation,
  effect integration, release and adoption remain unperformed and separately
  gated. #7268 remains open; this effort is Blocked, not Done.

### 2026-10-19 - Design-only scope
- Claimed #7268 after checking scoped execution/handoff issues and the active
  effort index. The adjacent pause, reclaim and driver contracts remain owned
  separately.
- Confirmed the effort slug. Captured the public-safe request and separated the
  authorized proposal from future implementation and adoption.
