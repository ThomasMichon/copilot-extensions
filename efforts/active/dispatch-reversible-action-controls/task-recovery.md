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

- [ ] Actual supported diagnostic reads include current and historical
      assignments, a queued/unowned task with an older retained attachment,
      missing reservation, optional-peer failure and changed owner/generation.
      Results are bounded, attributable and mutation-free; no token leakage.
- [ ] Distinguish target unpin, agent detach, Proposed reset, Queued reset,
      physical stop and existing hold/release/rearm semantics at their actual
      public boundaries. Cover defaults, invalid destinations and useful
      action-refusal output rather than merely testing helper names.
- [ ] Exercise current control authorization, restricted/cross-task token
      refusal, stale status/owner/generation, concurrent reclaim or attachment
      replacement, and exact idempotent replay versus conflicting retry.
- [ ] Detach/reset cannot orphan a live execution or enable a second claimant
      while a reservation, exclusive key or unresolved send retains custody.
      Preserve the old attachment/audit and all independent holds.
- [ ] Reconcile qualified original pending/unknown send identity with actual
      transport evidence, plus wrong identity, unsupported/unavailable
      transport, already-settled contradiction and transaction races.
      Legacy attempted/no-token and absent-event cases remain unknown.
      No test may replace the original wake, send a prompt or fabricate absence.
- [ ] Cover REST/client/CLI/MCP parity, strict parser validation and advertised
      live contract support. Exercise old caller/new provider, new caller/old
      provider and provider rollback without effectful fallback.
- [ ] Add changefile and owning docs; complete required review and CI, verify
      the containing release, and retain separately authorized installed/native
      qualification. Keep exhaustive/process matrices out of required unit CI.

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
