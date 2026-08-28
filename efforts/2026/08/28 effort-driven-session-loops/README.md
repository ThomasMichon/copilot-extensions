# Effort-Driven Session Loops

- **Slug:** `effort-driven-session-loops`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice pull requests from one managed worktree
- **Created:** 2026-08-27
- **Status:** Done
- **Vision:** **vision-closing** against
  [`visions/plugins/efforts`](https://github.com/ThomasMichon/copilot-extensions/blob/main/visions/plugins/efforts/README.md),
  authored with this effort to state the previously missing standing intent.
  Ambient delivery also advances
  [`visions/harness-guidance`](https://github.com/ThomasMichon/copilot-extensions/blob/main/visions/harness-guidance/README.md).
- **Umbrella issue:** [#1255](https://github.com/ThomasMichon/copilot-extensions/issues/1255)
- **Sub-issues:**
  [#1259](https://github.com/ThomasMichon/copilot-extensions/issues/1259)
  (repository enforcement and ambient policy),
  [#1261](https://github.com/ThomasMichon/copilot-extensions/issues/1261)
  (active effort binding),
  [#1258](https://github.com/ThomasMichon/copilot-extensions/issues/1258)
  (compact handoffs),
  [#1260](https://github.com/ThomasMichon/copilot-extensions/issues/1260)
  (cross-repository ownership), and
  [#1262](https://github.com/ThomasMichon/copilot-extensions/issues/1262)
  (integrated acceptance)

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

- [x] Land the new `visions/plugins/efforts` vision, this effort, and their index
      entries through the repository's review-gated pull-request flow.
- [x] Reconcile review feedback, merge the plan, and sync this worktree forward
      before beginning implementation.
- [x] Carve accepted implementation phases into GitHub sub-issues that cite the
      exact vision features and behaviors they close.

### Phase 1 - Repository adoption and ambient effort policy

- [x] Deferred to `GitHub issue 1234`: [#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234)
      owns shared session-start aggregation before another independent
      marketplace hook can be enabled, through the tracked runtime fix or an
      equivalent deterministic aggregation path. Direct producer tests landed
      here, while end-to-end coexistence remains owned by that issue.
- [x] Define `.copilot-extensions/efforts/config.json` using the new committed
      repository-config convention. Version 1 accepts only the exact semantic
      `{"version":1,"enforcement":"required"}`: a valid file declares both
      support and required use; absent or malformed configuration means the
      repository has not adopted efforts. Advisory adoption is out of scope for
      version 1 rather than being implied by softer wording.
- [x] Extend `efforts-setup` to scaffold the adoption marker and direct
      idempotent reconciliation of a minimal, stable, plugin-owned static
      fallback for launch paths that do not execute extension hooks. The
      fallback carries only the irreducible false-completion guard and
      effort-discovery pointer, not the full ambient kernel.
- [x] Add equivalent Bash and PowerShell `sessionStart` producers. When cwd is
      inside an adopting repository, emit a concise owner-marked kernel covering:
      use efforts instead of new plan docs for substantial work; review before
      execution; execute in waves; treat the active effort as the completion
      gate; let only the rightful head select the next authorized slice; pause
      only at real uncertainty, prerequisite, safety, or administrative gates.
- [x] Deferred to `GitHub issue 1234`: [#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234)
      owns registration of the efforts hook after the shared runtime fix or an
      equivalent deterministic aggregation path is complete. Registration
      remains intentionally omitted so efforts cannot displace another
      plugin's context.
- [x] Document the configuration, staged producer, and fallback contract; bump
      the efforts plugin version; and add focused schema, containment, symlink,
      gating, fail-open, parity, and byte-budget tests. The base kernel remains
      below 1,024 UTF-8 bytes. Dynamic orientation belongs to agent-worktrees'
      existing bounded digest/conduct path, not a second efforts producer.

### Phase 2 - Active-effort focus and bounded session orientation

- [x] Define one optional, structured active-effort pointer on the
      agent-worktrees-owned worktree record. The pointer must be
      repository-relative, contained, and independently valid for concurrent
      worktrees; do not create a repository-global current effort. Multiple
      worktrees may bind the same effort only when its coordination section
      declares distinct slices; changing participants does not duplicate a
      slice safely.
- [x] Derive the existing status core from the effort binding rather than
      creating a second responsibility signal: binding an open effort asserts
      `follow_up=true` and an effort-derived summary; only an explicitly
      completed or transferred effort can clear that cleanup gate.
- [x] Teach the planning flow to bind an effort when it is created or resumed and
      clear the binding only when the effort is complete, archived, or explicitly
      replaced.
- [x] When the optional worktree capability is present, enrich session-start
      guidance with a bounded pointer to the active effort. Consume #84, #907,
      #910, and #912 for the previous session, record-local recovery digest,
      pending handoff, and rightful-head role rather than reimplementing those
      signals.
- [x] Validate binding and completed release against the authoritative Git root;
      re-inspect dynamic reads under the recorded worktree path. Missing tools,
      stale records, malformed paths, and unavailable worktrees omit the hint
      without blocking startup, while non-cwd resumed sessions may recover it
      through the existing session/worktree binding.
- [x] Add concurrent-worktree, stale-pointer, unavailable-worktree,
      resumed-session, existing Bash/PowerShell conduct-parity, and
      context-budget coverage; update and bump each owning plugin touched by the
      final design.

### Phase 3 - Effort-aware handoff, ramp-up, and completion semantics

- [x] Make `planning-efforts`, `context-handoff`, and worktree lifecycle guidance
      agree that the active effort - not a session, handoff task, phase, or pull
      request - owns the objective and completion gate.
- [x] Define the release rule used by both humans and agent-eval: responsibility
      remains open until the effort is explicitly `Done`, every Plan and
      Validation Plan checkbox is resolved, and any deferred or blocked item is
      transferred to a named tracked objective.
- [x] Define a compact effort-backed handoff shape containing the canonical
      effort, next slice, material blockers or decisions, and any required
      confirmations. It should link durable state rather than duplicating the
      plan and journal.
- [x] Make the ramp-up path consume that pointer and recover only the predecessor
      session's immediate activity when needed, preserving a bounded takeover
      briefing.
- [x] Preserve a full standalone handoff for repositories that have not adopted
      efforts or for objectives that legitimately lack an active effort.
- [x] Add agent-eval or equivalent harness cases proving that an effort-backed
      rightful head continues after a completed slice, a superseded session
      assists rather than racing, no session declares completion while the
      release rule remains unsatisfied, and the head still stops at required
      review, approval, or safety boundaries.

### Phase 4 - Capability-aware cross-repository efforts

- [x] Expose a read-only capability check that can determine whether a target
      repository has valid effort adoption configuration without executing target
      code or trusting repository names. Version 1 checks an authoritative local
      checkout/worktree; a remote-only target remains host-owned until it is
      fetched and inspected.
- [x] Update the planning and cross-repository guidance: keep orchestration in
      the host when the target has not adopted efforts; when it has, allow a
      target-owned sub-effort linked one-way from the host effort.
- [x] Define ownership rules for multiple hosts collaborating on one compatible
      target: the target sub-effort is canonical for target-local scope, while
      each host retains only its own orchestration context and a reference.
- [x] Reject cyclic references, drifting peer copies, and target configuration
      that attempts to weaken plugin-owned policy.
- [x] Cover host-only, compatible-target, malformed-target, and concurrent-host
      placement cases in skills/docs tests or clean-room scenarios.

### Phase 5 - Integrated validation and release

- [x] Exercise configured and unconfigured repositories in normal cwd-backed
      launches, resumes, ACP launches with persisted trust, and the documented
      bare-resume/static-fallback case.
- [x] Run the relevant plugin suites, hook process-hygiene checks, version
      consistency guards, context-budget inventory, and clean-room harness
      scenarios for every changed plugin combination.
- [x] Confirm the public docs explain adoption, effort focus, compact handoffs,
      cross-repository ownership, optional capability degradation, and the
      difference between completing a slice and completing an effort.
- [x] Mark every plan and validation item complete, record merged pull requests,
      set the effort to Done, and archive it through a final reviewed delta.

## Validation Plan

- [x] A repository with valid enforcement configuration receives the exact
      concise efforts producer output; an unconfigured repository receives `{}`.
      End-to-end coexistence with other plugin contexts is not considered proven
      until #1234 or its equivalent aggregation path is resolved.
- [x] Unknown keys, wrong versions/types, malformed JSON, out-of-repository
      paths, and symlink escapes fail open and never activate policy.
- [x] Bash and PowerShell producers emit equivalent guidance bytes and one final
      JSON object across the supported validity, containment, and failure matrix.
- [x] The static fallback remains minimal, plugin-attributable, idempotently
      reconciled, and sufficient to preserve effort discovery and the completion
      gate when hooks do not run.
- [x] Two worktrees in one repository can bind different active efforts without
      collision; two declared participants can bind distinct slices of the same
      effort; and a stale or archived effort pointer is never presented as active.
- [x] An open effort binding keeps the existing `follow_up` cleanup gate asserted,
      and completing or explicitly transferring the effort clears it without a
      second independent responsibility flag.
- [x] Session-start orientation is bounded and contains pointers only: no
      transcript, full effort body, private identifier, or unbounded history.
- [x] Missing agent-worktrees, session history, handoff state, or ramp-up support
      degrades to the base efforts kernel without startup failure.
- [x] An effort-backed handoff plus the durable effort is sufficient for a fresh
      successor to select the next slice; predecessor ramp-up adds immediate
      context without replacing the effort.
- [x] Acceptance cases prove that agents continue across phase and session
      boundaries until the explicit release rule is met; only the rightful head
      drives, and required review, genuine uncertainty, and destructive or
      administrative confirmation remain legitimate pause gates.
- [x] Cross-repository placement uses validated target capability: host-owned
      when absent, one-way target-owned sub-effort when present, and no cyclic or
      duplicate canonical effort.
- [x] All changed plugin versions and marketplace entries remain consistent, and
      every changed plugin passes its focused test and clean-room matrix.

## Proposal

The first implementation slice stages everything the efforts plugin can own
safely while #1234 remains open: establish the repository adoption marker,
setup reconciliation, static fallback, concise ambient kernel, cross-platform
producer parity, tests, docs, and version bump. The producer stays unregistered
until the runtime or a suite-owned path deterministically joins context from
independently enabled plugins. This preserves immediate fallback value and a
reviewable implementation without creating another timing-dependent competitor.

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
  campaign.

### 2026-08-27 - Plan merged and implementation trackers carved

- Merged reviewed plan PR
  [#1256](https://github.com/ThomasMichon/copilot-extensions/pull/1256) after
  reconciling Copilot review feedback, then synchronized the implementation
  worktree to the merged baseline.
- Filed phase issues #1259, #1261, #1258, #1260, and #1262 so each implementation
  slice remains traceable to the accepted vision and effort.

### 2026-08-27 - Phase 1 foundation staged behind the aggregation gate

- Diagnosed #1234 against live Copilot CLI sessions: every configured hook ran
  and emitted valid JSON, but only one non-empty `additionalContext` result
  reached the model. The surviving producer varied with completion timing, and
  the combined emitted context was below the documented cap. Recorded the
  sanitized evidence on
  [#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234#issuecomment-5448456690).
- Rejected a plugin-suite aggregator as an interim remedy because independently
  installable plugins and repository-local hooks would still race unless every
  producer moved in one synchronized flag day. The primary fix remains runtime
  aggregation.
- Added strict version 1 adoption config, an owner-marked static completion
  fallback, and Bash/Python plus native PowerShell policy producers without
  registering another `sessionStart` hook.
- Added focused direct-producer coverage for valid and invalid adoption,
  containment, symlink/reparse rejection, bounded input, contaminated Git
  environments, manifest-owned attribution, byte limits, stdout hygiene, and
  cross-platform guidance parity. The policy kernel remains below 1,024 UTF-8
  bytes.
- Two adversarial review passes exposed and closed cross-platform gaps in Git
  environment cleanup, canonical path handling, interpreter failure, bounded
  stdin, timeout behavior, version anchoring, and PowerShell's permissive JSON
  parsing. Both producers now reject nonstandard JSON, array-wrapped objects,
  and duplicate/case-conflicting keys before applying semantic validation.

### 2026-08-28 - Phase 1 merged and active-effort focus implemented

- Merged Phase 1 foundation PR
  [#1263](https://github.com/ThomasMichon/copilot-extensions/pull/1263) and
  synchronized the implementation worktree to that reviewed baseline.
- Added one optional `active_effort` value to the agent-worktrees record:
  contained repository-relative README path plus declared participant/slice.
  Bind/show/release operations validate the authoritative worktree checkout,
  effort shape/status, reparse safety, and duplicate slice ownership under the
  existing record locks.
- An open binding now derives the existing `follow_up` cleanup gate and concise
  summary. Manual resolution cannot hide an open effort; completion and named
  transfer are explicit release paths.
- Enriched the existing record-first history/session-conduct path with a bounded
  effort pointer, preserving the current succession and handoff owners instead
  of adding another hook or recovery store.
- Updated `planning-efforts` to bind once the effort's participant/slice
  declarations are planned (and on resume when needed), and to release only
  after verified completion or a named transfer, while preserving standalone
  behavior when agent-worktrees is unavailable.
- Adversarial review tightened slice ownership to one normalized slice per
  repository, replaced substring declaration checks with exact declared table
  cells/headings, constrained flat/by-repo active and archive layouts, made
  completed release require `Status: Done` plus resolved Plan/Validation Plan
  task markers, and kept named transfer available when the checkout is gone.
- Added monotonic effort revisions so stale full-record writers cannot erase a
  newer bind/release transition, blocked `status --resolved` while any binding
  remains, and made POSIX reads descriptor-relative with Windows final-handle
  containment verification.
- Repaired five stale runtime-resolver tests exposed by the full suite in a
  separate versioned commit, then validated 3,497 agent-worktrees tests (6
  skipped), 45 efforts tests, and the repository install/version/docs/payload
  contract guards. Bumped agent-worktrees to `1.5.3-dev653`, efforts to
  `0.1.0-dev15`, and marketplace metadata to `1.7.5-dev672`.

### 2026-08-28 - Phase 2 active-effort focus merged

- Merged reviewed Phase 2 PR
  [#1271](https://github.com/ThomasMichon/copilot-extensions/pull/1271);
  its `Fixes #1261` linkage closed the active-effort binding issue.
- The merged slice bound one revisioned, repository-contained effort slice to
  each managed worktree, derived the existing follow-up disposition and bounded
  session orientation, and required explicit replacement, verified completion,
  or named transfer before releasing responsibility.

### 2026-08-28 - Phase 3 effort-backed continuity implemented

- Merged reviewed Phase 3 PR
  [#1273](https://github.com/ThomasMichon/copilot-extensions/pull/1273);
  its `Closes #1258` linkage closed the compact-handoff implementation issue.
- Added a compact context-handoff shape for worktrees whose
  `active_effort.active` value is true. The baton links the canonical effort,
  bound participant/current slice, next slice, immediate session delta, and
  required confirmations instead of copying the durable Request, Plan,
  Validation Plan, or Journal.
- Preserved the full standalone handoff for repositories without effort
  adoption, stale/closed/unavailable bindings, and objectives that legitimately
  have no active effort. Updated the runtime handoff prompts so the compact
  shape is not overridden by older full-template instructions.
- Made ramp-up effort-first for local worktrees: read the effort as the durable
  objective/completion gate, then inspect only immediate predecessor activity
  that the effort journal and git state do not explain. Remote worktrees fail
  back to standalone reconstruction unless an exact remote catalog command is
  explicitly supplied.
- Aligned context-handoff, planning-efforts, and worktree guidance on rightful
  succession and release. A superseded predecessor assists rather than edits;
  deferred/blocked tasks use machine-checked transfer syntax naming the tracked
  objective receiving them; `effort-focus release --completed` rejects a
  checked transfer without that target. Review, approval, administrative, and
  safety gates remain legitimate stops.
- Added focused guidance tests and the generic Tier-E
  `effort-handoff-eval` clean-room scenario. Its fixture creates a real managed
  worktree and active effort binding, then requires a fresh successor to
  continue after a completed phase, select review submission as the next slice,
  reject competing predecessor edits, refuse premature effort completion, and
  pause for required approval before implementation.
- Bumped context-handoff to `0.1.0-dev47`, agent-logger to `0.1.1-dev72`,
  agent-worktrees to `1.5.3-dev655`, efforts to `0.1.0-dev16`, and marketplace
  metadata to `1.7.5-dev674`.

### 2026-08-28 - Phase 4 capability-aware ownership implemented

- Added a read-only adoption probe to the existing Bash/Python and native
  PowerShell efforts producers. Only an exact committed version 1
  `required` config plus a valid checkout copy emits the stable compatible
  capability response; absent, malformed, uncommitted, locally weakened,
  remote-only, non-repository, symlink/reparse, special-file, replacement-ref,
  and missing-object cases fail closed.
- The probe resolves an authoritative local Git root with contaminated Git
  variables removed, lazy fetching and replacement objects disabled, a literal
  regular-file tree entry required, and bounded blob reads by object ID. It does
  not execute target code or infer support from repository names.
- Made host-owned orchestration the unambiguous default. Compatible adoption
  permits, but does not automatically select, one canonical target-local effort;
  hosts retain only their own context and one-way references, and later hosts
  join the same target effort rather than creating cycles or drifting copies.
- Extended planning, setup, plugin, testing, and cross-repository guidance plus
  64 deterministic parity and placement tests. Repeated adversarial review
  tightened committed-state authority, path indirection, Git object safety,
  special-file handling, exact argument parsing, and ownership wording.
- Bumped agent-worktrees to `1.5.3-dev657`, efforts to `0.1.0-dev17`, and
  marketplace metadata to `1.7.5-dev677`.

### 2026-08-28 - Phase 4 merged and integrated acceptance began

- Merged reviewed Phase 4 PR
  [#1275](https://github.com/ThomasMichon/copilot-extensions/pull/1275);
  its `Closes #1260` linkage closed the cross-repository ownership issue.
- Re-ran the four continuity surfaces: 3,503 agent-worktrees tests (6 skipped),
  197 agent-logger tests (1 skipped), 64 efforts tests, 42 context-handoff Node
  tests, 34 installer-readiness tests, and the headless-launch, install,
  payload-generation, documentation, skill, and version guards.
- The Tier-E `effort-handoff-eval` correctly refused to spend credits when its
  Tier-P starting-state gate failed. A clean source snapshot proved plugin
  installation and worktree creation, then exposed the fixture defect: the
  setup driver checked the installed binstub through a login shell but invoked
  the bind step from its non-login shell. The scenario now resolves that
  binstub once and uses its exact path for the binding. Its Tier-P manifest
  precondition also enters the managed worktree before querying the
  project-scoped effort binding.
- The first judged Tier-E run produced the four correct continuity decisions
  but was a literal-mode false pass: published guidance invoked
  `session-role --json`, while the JSON-only command rejected that conventional
  flag and the agent improvised past the blocker. Added a compatibility alias
  with parser coverage so existing guidance remains executable. Bumped
  agent-worktrees to `1.5.3-dev658` and marketplace metadata to
  `1.7.5-dev678`.
- The fresh rerun reached the role check but reported `head-elect` and
  `registered=false`: the generic clean-room provider launched ACP from the
  container home rather than the fixture's managed worktree. Added a generic
  `eval.acp_cwd` manifest field to both runners and set this scenario to launch
  from its worktree.
- A third fresh run proved the child process cwd alone was insufficient:
  tool execution used the worktree, but the ACP `session/new` request still
  supplied `/`, so the sessionStart registration hook could not resolve the
  managed worktree and the literal-mode judge again rejected the semantically
  correct answer. The clean-room namespace provider now exposes `eval.acp_cwd`
  as its authoritative `workspace_folder`, making agent-bridge send the same
  path in `session/new` that the launched shell uses.
- A fourth run delivered the symlink alias in both places, and the exact
  registration probe confirmed agent-worktrees correctly declines that alias
  while registering from the generated physical worktree path. Added
  `eval.acp_cwd_file` so setup-created scenarios can publish one authoritative
  runtime path after setup; the effort fixture now uses its physical managed
  worktree path instead of asking registration to trust an indirection.
- The next physical-cwd run registered the driven session as the rightful head,
  but Copilot CLI retained the registration context instead of the separate
  agent-worktrees command catalog. The agent guessed the non-executable source
  shim, received exit 126, and again violated literal mode by continuing.
  Registration now narrowly coalesces agent-worktrees' own payload-local
  command catalog with its binding context; this does not aggregate independent
  plugin contexts or claim to resolve #1234. That follow-up bumped
  agent-worktrees to `1.5.3-dev659` and marketplace metadata to
  `1.7.5-dev679`.
- The next fresh run passed strict literal-mode judging. The successor received
  the payload-local command catalog and worktree binding together, inspected the
  open effort successfully, rejected premature completion and predecessor
  edits, selected review submission, preserved the approval pause, and left the
  fixture unchanged.
- Final implementation validation passed 3,506 agent-worktrees tests (6
  skipped), 197 agent-logger tests (1 skipped), 64 efforts tests, 42
  context-handoff Node tests, 34 installer-readiness tests, the clean-room
  script/manifest checks, and the version, install, runtime-resolution,
  payload-generation, process-hygiene, skill, docs, runbook, headless-launch,
  timeout-handler, lint, and suite-discovery guards.

### 2026-08-28 - Phase 5 merged and effort completed

- Merged reviewed Phase 5 PR
  [#1279](https://github.com/ThomasMichon/copilot-extensions/pull/1279)
  after strict literal-mode acceptance passed and repeated review rounds closed
  every reported hook, provider, concurrency, portability, and lifecycle
  finding.
- The rebased final head passed 39 focused agent-worktrees session binding/hook
  tests, 16 clean-room provider/quoting tests, Bash and PowerShell syntax and
  lint checks, version consistency, public-diff sanitization, and an actual
  Bash 3.2 parse check. Copilot's final review recommended approval with no
  findings.
- The two intentionally gated Phase 1 registration items are explicitly
  transferred to [#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234);
  every remaining Plan and Validation Plan item is complete. The effort
  completion gate is satisfied and this record is archived as the final
  reviewed delta.
