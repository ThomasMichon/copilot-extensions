# Task diagnostics and recovery amendment

Back to the [canonical effort](README.md). Coordination: #9636.

## Request

Public-safe requested behavior, with private specifics omitted:

> Add the full suite of diagnostic actions: detach agent, reset task to
> Proposed or Queued, and a supported reconciliation endpoint. First analyze
> exactly what state the agent and task are in using retained queue and native
> session history. Explain whether task identity tokens are assigned to every
> agent/worktree.

The precise downstream evidence remains outside this public plan. Its generic
design input is a task that can be unowned and queued while an older attachment,
reservation or attempted wake still retains execution responsibility. A stopped
session and an absent recorded prompt do not prove an attempted send had no
effect. Source authorization is not authorization to reset, detach, stop,
replay, replace or enable an actual worker.

## Reconciliation and ownership

This closes the Dispatch vision's durable attachment history, conservative
recovery and retained unknown-effect requirements. It preserves worktree-based
worker identity and does not introduce universal agent credentials. Follow
the single-writer queue, entity-relationship, service-contract-evolution and
acknowledged-execution-handoff patterns.

Reuse `inspect-task`, doctor, task lifecycle, reservation, attachment and wake
owners. #3556 retains its full current task-to-Bridge observational chain and
#3555 its resolver-CLI parity; this bounded task recovery view must compose
available owning evidence, not claim those broader outcomes complete.
#9532 retains native wake delivery and #8796 its original targeted-acquisition
scope. No second queue, resolver, identity registry, daemon or scheduler.

## Plan

The following implementation means are **agent-recommended**, not additional
operator requests.

1. **Review this amendment first.** Preserve every existing Phase 4 gate.
   Record the merged proposal before changing runtime behavior.
2. **Add a bounded task diagnostic snapshot.** Report task status and generation,
   current ownership, historical/open attachments, retained reservation identity,
   wake/send custody, independent holds and concrete action refusal reasons.
   Distinguish current from prior execution, target routing from allocation,
   absent from unavailable evidence, and unknown from positive death. A read
   does not activate optional infrastructure or repair any state.
3. **Keep recovery verbs distinct.** Existing `detach` remains target unpinning;
   add explicitly named agent-assignment detachment. It closes the exact
   matching attachment and clears current assignment only when safe, without
   stopping a process or releasing retained reservation/exclusive custody.
   Support `reset --to proposed|queued`, preserving existing Proposed defaults.
   Reset changes task lifecycle, not historical facts or send outcomes.
   Existing hold/unhold, suspend/release, rearm and physical force-stop remain
   independent operations; expose their availability instead of duplicating
   their implementations behind a new universal mutation endpoint.
4. **Fence effects through existing authority.** Use current coordinator
   control authorization, expected status and task/assignment incarnation
   checks, durable audit and idempotent retry behavior. Recheck under the queue
   transaction at mutation time. Refuse new claim/execution when retained
   live, attempted or unknown execution remains unresolved; merely changing
   task status or owner must not bypass that exclusion. Preserve progress,
   results, history, attempt budgets and independent routing/hold semantics
   except where an explicitly documented reset field already owns the change.
5. **Expose evidence-bound wake reconciliation.** Retain the original wake,
   delivery-token and send identity. Only qualified evidence from the owning
   transport can settle the supported outcome; a caller's labels, lease age,
   timeout, absent token or missing native event cannot manufacture evidence.
   Observation of acceptance is not execution completion. Legacy attempted
   wakes without qualified custody remain explicitly unresolved with an
   actionable refusal; do not mint retroactive receipts, silently clear them
   or introduce a force-clear escape hatch. Reconciliation itself sends no
   prompt and grants no automatic replay.
6. **Wire and document the public boundaries together.** REST models/routes,
   Python client, CLI and MCP expose consistent diagnostics and actions.
   Advertise additive support from the executing coordinator using the
   existing contract seam. New callers refuse unsupported new effects before
   mutation, preserve the old reset/default-detach meanings and keep ordinary
   old operations usable on supported older providers. Restricted task tokens
   cannot gain control/fleet recovery authority. Explain conditional scoped
   task credentials versus delivery-lease/send identity without exposing
   token values in diagnostics, events or logs.
7. **Land, release and qualify separately.** Update owning help/docs and
   changefile, pass focused contained regressions and ordinary review/CI,
   verify actual containing release content, then qualify an installation
   only under its owner's separate rollout and live-action authorization.
   No source or synthetic result closes the remaining native/consumer gates.

## Validation Plan

- [x] Actual supported diagnostic reads include current and historical
      assignments, a queued/unowned task with an older retained attachment,
      missing reservation, optional-peer failure and changed owner/generation.
      Results are bounded, attributable and mutation-free; no token leakage.
- [x] Distinguish target unpin, agent detach, Proposed reset, Queued reset,
      physical stop and existing hold/release/rearm semantics at their actual
      public boundaries. Cover defaults, invalid destinations and useful
      action-refusal output rather than merely testing helper names.
- [x] Exercise current control authorization, restricted/cross-task token
      refusal, stale status/owner/generation, concurrent reclaim or attachment
      replacement, and exact idempotent replay versus conflicting retry.
- [x] Detach/reset cannot orphan a live execution or enable a second claimant
      while a reservation, exclusive key or unresolved send retains custody.
      Preserve the old attachment/audit and all independent holds.
- [x] Reconcile qualified original pending/unknown send identity with actual
      transport evidence, plus wrong identity, unsupported/unavailable
      transport, already-settled contradiction and transaction races.
      Legacy attempted/no-token and absent-event cases remain unknown.
      No test may replace the original wake, send a prompt or fabricate absence.
- [x] Cover REST/client/CLI/MCP parity, strict parser validation and advertised
      live contract support. Exercise old caller/new provider, new caller/old
      provider and provider rollback without effectful fallback.
- [x] Add changefile and owning docs; complete required review and CI and verify
      the containing release. Keep exhaustive/process matrices out of required
      unit CI.
- [ ] Under separate installation-owner and live-action authority, qualify
      actual installed consumers and retain the independent native-continuity
      gates. A source receipt is not a running-account result.

The checked criteria qualify source behavior through contained queue,
authenticated ASGI, parser and actual default-factory tests. They do not
establish installed-account adoption or native execution. Separately authorized
installed/native qualification remains open.

## Journal

### 2026-10-30 - Expanded recovery proposal

- Claimed #9636 after searching existing recovery work and reading #3556 and
  #8796. Their observational and targeted-acquisition scopes remain intact.
- Existing source already has reset-to-Proposed and target unpinning. Neither
  is an agent-assignment detachment or a supported reset-to-Queued operation.
- The requested analysis preceded this proposal. Its generic outcome is the
  distinction between current task state, retained execution and qualified
  evidence; no private live state is copied into source fixtures.
- Proposal only. No runtime implementation, task mutation, process effect,
  installation update or reconciliation is claimed.

### 2026-10-30 - Amendment reviewed

- #9638 merged as `9fd66497ff` after current-head Copilot approval with no open
  findings and successful required checks. Managed reconciliation retained the
  prior source ref and aligned the existing worktree to the reviewed proposal.
- Re-read the canonical Plan and Validation Plan. Only proposal review is
  complete; implementation, release and every independent consumer gate remain.
- Begin bounded queue diagnostics and recovery regressions, with a separate
  receipt-only transport observation prerequisite. Missing transport history
  remains unknown; source changes authorize no actual recovery operation.

### 2026-10-30 - Recovery source implementation

- Added bounded queue evidence and action-refusal reporting, distinct
  revision-fenced assignment detachment and Proposed/Queued reset, atomic
  recovery receipts, control-only REST/MCP admission and compatible client/CLI
  surfaces. Existing reset also preserves unresolved execution custody.
- Original-wake reconciliation observes Bridge's new receipt-only native
  interface. A positive original receipt can settle acceptance; absence,
  unsupported targets and unqualified historical traffic remain unknown.
  No new prompt, timeout-to-absence conversion or universal credential exists.
- Actual isolated regression packets pass: 156 task-recovery cases, including
  both outboxes, restart replay, authority, truncation, retired attachment and
  the receipt-only command; 211 Bridge receipt/admission/capability cases.
  The earlier integrated recovery/adjacent lifecycle packet passed 294 cases
  before the final edge-case extensions. Static source, module-size,
  installation-contract and documentation guards pass.
- A suspended-assignment control exposed detachment retaining the suspended
  state; use the existing owned-state set when returning detached work to
  Queued. The fixture and implementation now agree. A module-size refusal
  moved the cohesive receipt CLI branch into its focused module rather than
  widening the general send router.
- Independent implementation review, containing release and separately
  authorized running/native qualification remain open. No live task,
  reservation, wake, installation or worker was operated.

### 2026-10-30 - Observation and retirement review corrections

- Independent review identified receipt-client construction ensuring a daemon
  before observation. Both ordinary and keyed receipt-only commands now use
  the real non-ensuring client factory; unavailable-provider controls forbid
  daemon ensure/start rather than mocking past that boundary.
- Reuse canonical driver-specific native classification and the existing
  terminal exact-absence release predicate. Terminal historical exclusive
  flags do not imply active custody. Supported release/retire regressions
  cover ordinary, restricted and CLI assignments without manually asserting
  the conversation marker.
- Diagnostics and settlement share one queue-only wake eligibility planner.
  Unsupported adapters, contradictory settled state, incomplete identity and
  truncated history report concrete refusal instead of advertised support.
- The corrected recovery/adjacent-driver packet passes 372 cases; ordinary
  and keyed receipt observation passes 183. Seven synthetic declaration-tool
  controls pass without expanding the required unit inventory. The existing
  3,052-unit packet passed after re-admitting the changed protocol constants.
- CI also exposed missing source-bound contract declarations/capture evidence.
  The existing HTTP-51 capture owner now prepares only committed semantic
  declarations and then requires actual receipt-only observations, including
  zero daemon ensure/start. Preparation preserves all existing fixture and
  provenance declarations; it is not itself a capture or a passing CI gate.
  Fresh committed capture, re-review, release and running qualification remain.

### 2026-10-30 - Actual capability evidence captured

- The existing HTTP-51 portfolio executed all four required source-bound
  probes, including both fixture installation modes. Actual ordinary and
  keyed receipt-only client factories recorded zero daemon ensure/start calls;
  unavailable-provider, missing-receipt and old-capability cases retained
  their conservative outcomes without a prompt.
- The capture owner published its current capability observations and complete
  source closure, then a separate no-write execution verified the same outputs.
  The exact PR-diff contract guard passes: two contracts and 884 fixtures.
  Earlier HTTP generations and ordered provenance remain retained.
- The first invocation lacked the capture process library in its interpreter
  and stopped before observations/publication. Preparing only the canonical
  test environment resolved that explicit missing dependency; no runtime
  installation, service repair or captured-result substitution was used.
- This closes the missing local capture evidence, not independent review,
  required hosted CI, containing release or installed/native qualification.

### 2026-10-30 - Diagnostic factory and acquisition-only review corrections

- The next independent review cleared the five prior findings and identified
  diagnostics using the default coordinator-ensuring factory. CLI and default
  stdio MCP diagnostics now use existing read-only discovery: no coordinator
  start, SSH failover or control-credential discovery. Actual default-factory
  controls cover both available and absent providers without mocking past
  the factory. Existing bounded read retries remain intact.
- Native identity is now resolved for every reservation, not only retired
  records or those with published agent/handle fields. Supported acquisition
  binding followed by spawn failure proves an acquisition-only native remains
  protected before spawn publication, across legacy reset and all strict actions.
- The complete corrected recovery, observation, authority and adjacent-driver
  packet passes 458 cases. Static and existing captured Bridge contract guards
  also pass; this Dispatch-only correction changes no captured Bridge bytes.
- Hosted CI passed the preceding revision but cannot waive these findings.
  The new revision still requires independent review and its own CI before merge.

### 2026-10-30 - Complete the frontend activation boundary

- The diagnostic/acquisition correction received exact-head independent
  approval and green CI. Before delivery, apply the same no-implicit-start
  class fix to the new strict recovery and receipt-reconciliation frontends:
  no coordinator ensure or SSH failover before admission, while keeping
  control-authority discovery and legacy Proposed reset behavior.
- Production-factory controls now cover every new CLI/stdio recovery path
  with available and absent providers. Preserve explicitly injected factories
  by testing `None`, not callback truthiness.
- The complete final recovery/authority/driver packet passes 471 cases.
  This meaningful final revision still needs its own review; the preceding
  approval is not carried forward.

### 2026-10-30 - Integrate the current target without erasing evidence

- The complete frontend revision received current-head independent approval
  and green CI, but a newly merged related-plugin change conflicted with
  current HTTP-51 artifacts. Preserve both source changes and both ordered
  provenance streams rather than choosing an entire registry side.
- Guarded synchronization retained the reviewed tip and stopped without
  modification. The subsequent private-branch rebase merged only declaration
  deltas and existing committed observations, preserving the target history
  plus the original capture records. No synthetic observation was introduced.
- Prepare and genuinely recapture the combined committed source through the
  existing owner, then repeat no-write verification. All four probes and the
  exact new-base guard pass; earlier generations remain retained. Dispatch
  implementation/test bytes are unchanged by the target integration.
- Integration exposed stale venue-test fixtures: a local case targeted this
  source-only repository, controller-only cases omitted their independent
  positive hosting prerequisite, and a minimal recovery double omitted the
  canonical timeout policy. Correct only those fixtures; production hosting
  admission and controller assertions remain unchanged. The actual hosting
  policy and venue packet passes 163 cases.
- Re-review of the integrated head, required CI, release and running/native
  qualification remain separate gates.

### 2026-10-30 - Reviewed source suite merged

- #9643 merged to `dev` as `6d0afa4a9759fb9386f4ec2e57342f7df53bf229`
  after exact-head approval on `ecc28f01fb`, no open review findings and
  successful required CI. The retained acquisition conversation was explicitly
  resolved after its supported binding/failure regression proved the fix.
- Re-read the canonical Plan and Validation Plan. Close source delivery and
  the contained boundary criteria only; retain installed consumers, actual
  native continuity and the final release/qualification criterion.
- The source worktree's terminal PR observation was stale after a cross-repo
  merge command. Refreshing its canonical `pr-status` established Merged;
  verified `pr-complete` then preserved the old tip and pulled forward past the
  real squash without replaying or discarding new work.
- Containing Dev CI passed. Follow the existing automatic containing promotion,
  verify the released bytes, then obtain installation-owner authority before
  runtime adoption or any live task action. The effort remains Active.

### 2026-10-30 - Containing release verified

- Containing Dev CI `41042324449` succeeded. Existing automatic promotion
  `41042643738` admitted exact source `6d0afa4a9759fb9386f4ec2e57342f7df53bf229`,
  completed qualification and source publication/readback, then merged its
  ready Main candidate. No duplicate dispatch or qualification retry was used.
- Selected Main is `bb7e82ec51e0ec57e4d10b27fbe1a4808be68ff2`, resolved from
  the annotated `promote-dev-<source>` tag. All 29 changed Bridge/Dispatch
  runtime modules are byte-identical to reviewed source. Released versions
  are Bridge `0.9.228-dev1` and Dispatch `0.18.163-dev1`.
- The earlier successful promoter admitted a predecessor, not this source,
  and was not credited. Package versions alone were not containment evidence.
- Close the source-release criterion, not installed or native qualification.
  No update, live reset/detach/stop, wake replay, replacement or worker-pool
  activation was performed. The effort remains Active.
