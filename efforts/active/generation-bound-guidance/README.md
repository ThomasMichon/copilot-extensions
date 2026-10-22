# Generation-Bound Guidance

- **Slug:** `generation-bound-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Draft
- **Vision:** `visions/harness-guidance` — closes `simple-local-static-delivery`, `generated-output-is-disposable-source-is-not`, and `resume-stable-context`
- **Umbrella issue:** #7943

## Guiding Intent

Publish the final complete static instruction set before first-prompt discovery.
Reuse successful preparation across lifecycle boundaries while its actual inputs
remain unchanged. A late backup refresh is file delivery, never proof that a
running model received it.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Guidance driver | Plan, source implementation, tests and reviewed merge | Managed source `lopsided-switch`; #7943 |

## Coordination

- **Topology:** sequential plan and implementation PRs.
- **Host (owns PRs):** Guidance driver.
- **Delegates:** none.
- **Handoff:** #7943 and this effort retain the incomplete objective.
- #7694/#7788 retain native launch scripts and optional maintenance ownership.
- #7974 retains bootstrap context and worktree identity ownership.
- Manager loading/configuration/runner work remains outside this slice.
- Existing telemetry owners retain collection and SDK design; use existing
  launch progress and activity events, without a network dependency.
- Release promotion and authorized installed rollout remain separately owned.
  This source effort neither runs an updater nor restarts a shared service.

## Context

The current Worktrees publisher resolves two complete source sets under its
publication lock and repeats Git preflight at every invocation. Bridge's local
prelaunch seam independently invokes the compatibility renderer with a shared
five-second discovery/render budget. SessionStart already queues an honest
asynchronous backup, but its completion reaches only request state and logs.

The archived `local-cache-delivery-primacy` effort established delivery
precedence; it is not reopened. The active `coordinated-launch-maintenance`
effort owns optional runtime maintenance, not static generation reuse.
This bounded follow-up realizes the existing startup publication pattern,
without introducing a content-admission gate, alternate renderer, daemon,
collector or maintenance owner.

## Request

Public-safe excerpts from the authorized implementation request:

> Required behavior: static instructions/skills/hooks ready beforefirstpromptdiscovery; one generation-bound preparation reused acrosscreate/resume/Bridge/sessionStart; settings/payload/pair changes invalidate; laterbackupcompletion shows refreshedguidance/restart requirement, not claimmodeladmission. Preserve reviewed fallback,tracked/userfiles, exactsourceidentity andcross-processnewerpublication.

> This is not permission to skipprelaunchreadiness or disablewholehooks. Optional runtimeupdates belongexistingmaintenanceowner, notyours.

> No sharedruntime updates/restarts/deployment; isolated sourcevalidation allowed. No children.

The issue's published acceptance criteria are the readable contract. Static
plugin registration/composition remains ahead of the instruction publication;
this work does not replace skill/hook discovery with an instruction-only claim.

## Plan

### Phase 1 — Reviewed contract
- [ ] Land this plan through normal automated review before implementation.
- [ ] Record baseline consumer order, publication count and local timings.

### Phase 2 — Preparation and reuse

The following code mechanisms are **agent-recommended**.

- [ ] Extend `static_instructions.py` with one complete resolved preparation
  and a checkout-local, versioned successful-publication receipt. Bind it to
  canonical checkout/home, exact enabled source identities, effective settings,
  resolved payload roots/declarations/templates and pair-composition state.
  A partial/error/timeout result never creates reusable readiness.
- [ ] Retain one publication lock and revalidate captured inputs at its mutation
  boundary. Avoid a second complete desired-set reconstruction when captured
  input evidence is unchanged. Preserve current index/path/link checks,
  namespace-only cleanup and protection of tracked/staged removals.
  A concurrent newer source selection must never be replaced by an older one.
- [ ] Preserve per-source independence: an unavailable enabled payload reports
  partial readiness while safe independent sources remain deliverable. Incomplete
  discovery never authorizes stale cleanup for unresolved sources or a reusable
  complete-readiness receipt. Allocation may succeed with an honest degraded
  diagnostic; it must not imply all guidance was delivered or repair payloads.
- [ ] Reuse the receipt only while source, destination and Git-protection
  evidence still matches. Missing/corrupt evidence, changed outputs, settings,
  payload identity or paired composition forces safe preparation, not optimistic
  success. Do not rely on age, version text alone or an inherited environment
  flag as readiness authority.
- [ ] Keep `local_cache_refresh.prepare_for_launch` as the foreground owner,
  with explicit generation/reuse fields in its structured result. Preserve
  final composition ordering and operation-specific limits; do not impose a
  broad timeout that silently permits an unprepared launch.
- [ ] Route the Bridge prelaunch seam through the existing attributable
  Worktrees preparation command when available, consuming its structured result
  rather than rendering a competing namespace. Preserve standalone optional
  compatibility, same-cell identity and async cancellation/tree cleanup.
  A missing owner or failed preparation reports degraded fallback honestly.
- [ ] Make `sessionstart_diagnostic` skip background regeneration for a verified
  current preparation. Otherwise retain one bounded backup owner. Completion
  persists a visible refresh/restart-required status through the existing
  guidance/status surface, with explicit unknown model admission and no hook
  invented to originate a turn. Failure remains failure, not restart success.
- [ ] Clarify the compatibility renderer's skill advice: same-generation
  ordinary reads do not regenerate; managed prelaunch owns static readiness.
  Update the startup pattern and relevant plugin docs, with changefiles.

### Phase 3 — Review and completion
- [ ] Land coherent source and documentation through review and required CI.
- [ ] Report source merge, release promotion and installed verification
  separately. Transfer deployment acceptance to the existing authorized
  maintenance owner rather than starting a competing update.
- [ ] Resolve Plan/Validation items, journal results and archive this effort.

## Validation Plan

All checks below are **agent-recommended** mechanisms for #7943 acceptance.

- [ ] Failing regressions first: unchanged create/resume/Bridge/SessionStart
  chain publishes once; repeat reads neither render nor enqueue a worker.
- [ ] Settings enable/disable, payload bytes/root/identity, pairing transition,
  missing/corrupt receipts, modified output and tracked/staged deletion changes
  invalidate correctly; unsafe publication preserves user and reviewed files.
- [ ] Missing enabled payloads retain unresolved outputs and reviewed fallback,
  deliver safe independent sources, and expose partial rather than ready state.
- [ ] Real independent-process contention proves current-generation selection
  wins, including a delayed old preparation and failed/incomplete composition.
- [ ] Real consumer-order fixture records registration, publication, spawn and
  first read; first touch works with an empty synthetic home and no prior cache.
  Reused readiness is tested through the consumer, not only file existence.
- [ ] Backup completion exposes refreshed/restart-required state; unchanged
  backup is not falsely described as new model delivery. Timeout/failure,
  owner contention and cancellation retain existing safety semantics.
- [ ] Targeted contained Worktrees/Bridge suites, Python lint, install-contract
  and relevant source guards pass. Add source-only first-touch integration
  without live service or operator-home mutation.
- [ ] Record before/after publication count and elapsed timings separately
  from broader launch latency. Preserve platform-specific skips explicitly.
- [ ] Record whether native/live installed acceptance was run; it is not
  authorized by this source task and must not be implied by unit success.

## Proposal

Use generation-bound preparation at the existing owner boundary, not a new
global cache or service. The receipt records successful file publication only.
Revalidation must be cheaper than rebuilding and republishing the complete set,
but no optimization may infer safety from a stale or incomplete input census.
If the existing command/status contract cannot carry the required result,
coordinate the smallest additive owner API before changing another slice.

## Journal

### 2026-10-22 — Intake and ownership

- Claimed #7943 after checking its full thread and open overlapping PRs.
  Public claim uses only the assigned source codename.
- Read the existing publisher, backup worker, Bridge consumer and pattern.
  No source implementation or installed mutation preceded this proposal.
- Captured the requested readiness, invalidation, fallback and no-rollout
  boundaries; implementation choices and validation mechanisms are explicitly
  agent-recommended.
- Clarified the missing-payload control: preserve per-source independence and
  partial-readiness diagnostics without repairing operator marketplace state.
