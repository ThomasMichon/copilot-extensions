# Effort-Driven Session Loops

- **Slug:** `effort-driven-session-loops`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice pull requests from one managed worktree
- **Created:** 2026-08-27
- **Status:** Draft
- **Vision:** **vision-closing** against
  [`visions/plugins/efforts`](../../../visions/plugins/efforts/README.md),
  authored with this effort to state the previously missing standing intent.
  Ambient delivery also advances
  [`visions/harness-guidance`](../../../visions/harness-guidance/README.md).
- **Umbrella issue:** [#1255](https://github.com/ThomasMichon/copilot-extensions/issues/1255)
- **Sub-issues:** pending plan review

## Guiding Intent

Make effort adoption an explicit repository capability and turn that capability
into a durable execution loop. In an adopting repository, meaningful multi-step
work should normally become an effort rather than a standalone plan: the goal,
plan, validation, coordination, and progress live in one reviewable record;
implementation proceeds in waves; and each new session continues the effort
until the effort itself is complete.

Continuity should consume less context, not more. The active effort carries the
durable objective and remaining work. A handoff should normally need only the
effort pointer and next slice, with bounded session ramp-up available for the
predecessor's immediate activity. Session-start guidance may surface those
pointers when reliable repository and worktree identity exist, while remaining
safe and useful when they do not.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driver | Owns the canonical effort, serial implementation slices, and pull requests | managed copilot-extensions worktree |
| Copilot review | Reviews each plan and implementation pull request | repository ruleset |

## Coordination

- **Topology:** one managed worktree with serial pull requests; each cross-plugin
  slice lands before its consumers.
- **Host (owns PRs):** the driver of #1255.
- **Delegates:** Copilot review owns the automated review pass; focused
  implementation or review agents may be used within a slice without taking
  ownership of the effort.
- **Handoff:** name `effort-driven-session-loops` and the next incomplete
  phase/slice. Load this effort first; use session ramp-up only for the prior
  session's immediate activity.
- **Completion gate:** the worktree remains responsible until every Plan and
  Validation Plan item is complete, all required pull requests are merged, and
  the effort is archived.

## Context

The efforts plugin currently ships the `efforts-setup` and `planning-efforts`
skills, but it has no ambient hook and no authoritative repository capability
marker. A session must already know to invoke those skills, and the presence of
an `efforts/` tree is not enough to establish that the repository expects the
effort lifecycle.

The reusable delivery architecture already exists:

- [`docs/patterns/context-injection.md`](../../../docs/patterns/context-injection.md)
  defines strict repository configuration, cwd gating, plugin-owned
  `sessionStart` kernels, Bash/PowerShell parity, fail-open behavior, and the
  shared context budget.
- Issue [#860](https://github.com/ThomasMichon/copilot-extensions/issues/860)
  established config-backed ambient guidance and provides existing hook
  exemplars.
- Issue [#910](https://github.com/ThomasMichon/copilot-extensions/issues/910)
  tracks mirroring a handoff pointer into the worktree record so recovery does
  not depend on a second service remaining reachable.
- Issues [#84](https://github.com/ThomasMichon/copilot-extensions/issues/84),
  [#907](https://github.com/ThomasMichon/copilot-extensions/issues/907), and
  [#912](https://github.com/ThomasMichon/copilot-extensions/issues/912) own the
  single-head session chain, record-local recovery digest, and drive-versus-
  assist session role. This effort consumes those owners and adds only
  effort-specific identity and policy.
- Issue [#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234)
  tracks the current loss of all but one `sessionStart` `additionalContext`
  result. The efforts hook must not be treated as reliably coexistent until
  that shared aggregation defect or an equivalent suite-owned aggregator lands.
- `agent-logger:ramp-up-session` already turns one predecessor session into a
  bounded takeover briefing.

The missing layer is effort-specific policy and identity: explicit adoption, one
active effort per worktree objective, effort-aware session orientation, compact
handoffs, and target-capability-aware cross-repository placement. The new
[`visions/plugins/efforts`](../../../visions/plugins/efforts/README.md) vision
states that standing intent; this effort closes its delta against the current
skills-only implementation.

## Request

Public-safe transcription of the operator request:

> Add a config-backed hook that can assert whether efforts are enforced for a
> repository and inject a concise explanation of what enforcement changes.
> With efforts enabled, agents should generally create efforts instead of
> standalone plan documents, define their goals durably, build and review the
> plan, implement it in waves, and use handoffs to continue the next part of the
> effort.
>
> Effort-backed sessions should be loop-driven until the effort is done. Agents
> should ask for steering only when uncertainty or required safety confirmation
> demands it, and they must not declare completion before the effort's completion
> gate.
>
> Session start should best-effort surface the active effort, previous session,
> and potential handoff when reliable cwd-backed worktree context exists. Bare
> resume is a known temporary gap.
>
> Cross-repository placement should follow target capability. If the target
> repository has effort configuration, it may own a referenced sub-effort;
> otherwise the host repository should own the effort on the target's behalf.

## Plan

### Phase 0 - Reviewed intent and campaign

- [ ] Land the new `visions/plugins/efforts` vision, this effort, and their index
      entries through the repository's review-gated pull-request flow.
- [ ] Reconcile review feedback, merge the plan, and sync this worktree forward
      before beginning implementation.
- [ ] Carve accepted implementation phases into GitHub sub-issues that cite the
      exact vision features and behaviors they close.

### Phase 1 - Repository adoption and ambient effort policy

- [ ] Resolve #1234 before enabling another independent marketplace hook: land
      the shared aggregation fix tracked there, consume an upstream runtime fix,
      or route the efforts producer through the suite's single deterministic
      aggregator. Direct producer tests may proceed earlier, but end-to-end
      coexistence is gated.
- [ ] Define `.copilot-extensions/efforts/config.json` using the new committed
      repository-config convention. Version 1 accepts only the exact semantic
      `{"version":1,"enforcement":"required"}`: a valid file declares both
      support and required use; absent or malformed configuration means the
      repository has not adopted efforts. Advisory adoption is out of scope for
      version 1 rather than being implied by softer wording.
- [ ] Extend `efforts-setup` to scaffold and reconcile the adoption marker plus a
      minimal, stable, plugin-owned static fallback for launch paths that do not
      execute extension hooks. The fallback carries only the irreducible
      false-completion guard and effort-discovery pointer, not the full ambient
      kernel.
- [ ] Add equivalent Bash and PowerShell `sessionStart` producers. When cwd is
      inside an adopting repository, emit a concise owner-marked kernel covering:
      use efforts instead of new plan docs for substantial work; review before
      execution; execute in waves; treat the active effort as the completion
      gate; let only the rightful head select the next authorized slice; pause
      only at real
      uncertainty, prerequisite, safety, or administrative gates.
- [ ] Register the hook, document the configuration and fallback contract, bump
      the efforts plugin version, and add focused schema, containment, symlink,
      gating, fail-open, parity, and byte-budget tests. The base kernel must
      remain below 1,024 UTF-8 bytes and the complete efforts contribution,
      including dynamic orientation, below 1,536 bytes.

### Phase 2 - Active-effort focus and bounded session orientation

- [ ] Define one optional, structured active-effort pointer on the
      agent-worktrees-owned worktree record. The pointer must be
      repository-relative, contained, and independently valid for concurrent
      worktrees; do not create a repository-global current effort. Multiple
      worktrees may bind the same effort only when its coordination section
      declares distinct participants or slices.
- [ ] Derive the existing status core from the effort binding rather than
      creating a second responsibility signal: binding an open effort asserts
      `follow_up=true` and an effort-derived summary; only an explicitly
      completed or transferred effort can clear that cleanup gate.
- [ ] Teach the planning flow to bind an effort when it is created or resumed and
      clear the binding only when the effort is complete, archived, or explicitly
      replaced.
- [ ] When the optional worktree capability is present, enrich session-start
      guidance with a bounded pointer to the active effort. Consume #84, #907,
      #910, and #912 for the previous session, record-local recovery digest,
      pending handoff, and rightful-head role rather than reimplementing those
      signals.
- [ ] Validate every dynamic pointer against the authoritative cwd and repository
      root. Missing tools, stale records, malformed paths, unavailable services,
      and non-cwd launch modes must omit the dynamic hint without blocking startup.
- [ ] Add concurrent-worktree, stale-pointer, no-service, resumed-session,
      Bash/PowerShell, and context-budget coverage; update and bump each owning
      plugin touched by the final design.

### Phase 3 - Effort-aware handoff, ramp-up, and completion semantics

- [ ] Make `planning-efforts`, `context-handoff`, and worktree lifecycle guidance
      agree that the active effort - not a session, handoff task, phase, or pull
      request - owns the objective and completion gate.
- [ ] Define the release rule used by both humans and agent-eval: responsibility
      remains open until the effort is explicitly `Done`, every Plan and
      Validation Plan checkbox is resolved, and any deferred or blocked item is
      transferred to a named tracked objective.
- [ ] Define a compact effort-backed handoff shape containing the canonical
      effort, next slice, material blockers or decisions, and any required
      confirmations. It should link durable state rather than duplicating the
      plan and journal.
- [ ] Make the ramp-up path consume that pointer and recover only the predecessor
      session's immediate activity when needed, preserving a bounded takeover
      briefing.
- [ ] Preserve a full standalone handoff for repositories that have not adopted
      efforts or for objectives that legitimately lack an active effort.
- [ ] Add agent-eval or equivalent harness cases proving that an effort-backed
      rightful head continues after a completed slice, a superseded session
      assists rather than racing, no session declares completion while the
      release rule remains unsatisfied, and the head still stops at required
      review, approval, or safety boundaries.

### Phase 4 - Capability-aware cross-repository efforts

- [ ] Expose a read-only capability check that can determine whether a target
      repository has valid effort adoption configuration without executing target
      code or trusting repository names. Version 1 checks an authoritative local
      checkout/worktree; a remote-only target remains host-owned until it is
      fetched and inspected.
- [ ] Update the planning and cross-repository guidance: keep orchestration in
      the host when the target has not adopted efforts; when it has, allow a
      target-owned sub-effort linked one-way from the host effort.
- [ ] Define ownership rules for multiple hosts collaborating on one compatible
      target: the target sub-effort is canonical for target-local scope, while
      each host retains only its own orchestration context and a reference.
- [ ] Reject cyclic references, drifting peer copies, and target configuration
      that attempts to weaken plugin-owned policy.
- [ ] Cover host-only, compatible-target, malformed-target, and concurrent-host
      placement cases in skills/docs tests or clean-room scenarios.

### Phase 5 - Integrated validation and release

- [ ] Exercise configured and unconfigured repositories in normal cwd-backed
      launches, resumes, ACP launches with persisted trust, and the documented
      bare-resume/static-fallback case.
- [ ] Run the relevant plugin suites, hook process-hygiene checks, version
      consistency guards, context-budget inventory, and clean-room harness
      scenarios for every changed plugin combination.
- [ ] Confirm the public docs explain adoption, effort focus, compact handoffs,
      cross-repository ownership, optional capability degradation, and the
      difference between completing a slice and completing an effort.
- [ ] Mark every plan and validation item complete, record merged pull requests,
      set the effort to Done, and archive it through a final reviewed delta.

## Validation Plan

- [ ] A repository with valid enforcement configuration receives the exact
      concise efforts producer output; an unconfigured repository receives `{}`.
      End-to-end coexistence with other plugin contexts is not considered proven
      until #1234 or its equivalent aggregation path is resolved.
- [ ] Unknown keys, wrong versions/types, malformed JSON, out-of-repository
      paths, and symlink escapes fail open and never activate policy.
- [ ] Bash and PowerShell producers emit equivalent guidance bytes and one final
      JSON object for every input class.
- [ ] The static fallback remains minimal, plugin-attributable, idempotently
      reconciled, and sufficient to preserve effort discovery and the completion
      gate when hooks do not run.
- [ ] Two worktrees in one repository can bind different active efforts without
      collision; two declared participants can bind distinct slices of the same
      effort; and a stale or archived effort pointer is never presented as active.
- [ ] An open effort binding keeps the existing `follow_up` cleanup gate asserted,
      and completing or explicitly transferring the effort clears it without a
      second independent responsibility flag.
- [ ] Session-start orientation is bounded and contains pointers only: no
      transcript, full effort body, private identifier, or unbounded history.
- [ ] Missing agent-worktrees, session history, handoff state, or ramp-up support
      degrades to the base efforts kernel without startup failure.
- [ ] An effort-backed handoff plus the durable effort is sufficient for a fresh
      successor to select the next slice; predecessor ramp-up adds immediate
      context without replacing the effort.
- [ ] Acceptance cases prove that agents continue across phase and session
      boundaries until the explicit release rule is met; only the rightful head
      drives, and required review, genuine uncertainty, and destructive or
      administrative confirmation remain legitimate pause gates.
- [ ] Cross-repository placement uses validated target capability: host-owned
      when absent, one-way target-owned sub-effort when present, and no cyclic or
      duplicate canonical effort.
- [ ] All changed plugin versions and marketplace entries remain consistent, and
      every changed plugin passes its focused test and clean-room matrix.

## Proposal

The first implementation work after this plan merges should resolve the shared
`sessionStart` aggregation precondition in #1234 if it remains open, then keep
the efforts change itself plugin-only: establish the repository adoption marker,
setup reconciliation, concise ambient kernel, cross-platform producer parity,
tests, docs, and version bump. Hook registration becomes a shippable capability
only through a deterministic aggregation path. This slice provides immediate
value without inventing dynamic worktree state.

The second slice should add active-effort identity through the existing
worktree-record owner, consuming #910's record-local handoff pointer rather than
creating a parallel store. Handoff and ramp-up behavior should land only after
that durable identity exists. Cross-repository placement follows last because it
depends on the adoption marker and canonical-effort semantics established by the
earlier slices.

## Journal

### 2026-08-27 - Kickoff

- Searched open and closed GitHub issues for efforts, session-start guidance,
  handoff, and ramp-up work. Issue #860 supplies the general ambient-guidance
  pattern; #84, #907, #910, and #912 own session succession and record-first
  recovery; #1234 is the hook-aggregation prerequisite. None covers
  effort-scoped relentless execution or cross-repository effort capability.
- Filed umbrella issue #1255 after finding no duplicate.
- Confirmed that copilot-extensions has adopted in-repository efforts, so this
  public effort is canonical and no duplicate downstream effort is required.
- Authored the `visions/plugins/efforts` standing intent and this five-slice
  campaign. No implementation has started; the vision and plan must clear review
  and merge before Phase 1 begins.
