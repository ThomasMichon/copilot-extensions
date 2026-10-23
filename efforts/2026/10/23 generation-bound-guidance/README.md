# Generation-Bound Guidance

- **Slug:** `generation-bound-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Done
- **Vision:** `visions/harness-guidance` — closes `simple-local-static-delivery`, `generated-output-is-disposable-source-is-not`, and `resume-stable-context`
- **Umbrella issue:** #7943

## Guiding Intent

Publish the final complete static instruction set before first-prompt discovery.
Reuse successful preparation across lifecycle boundaries while its actual inputs
remain unchanged. A late backup refresh is file delivery, never proof that a
running model received it.

**Source-slice closeout:** #8045 merged at
`419a12d054193e3253f91a8395444608116fa1e3` after current-head approval and green
required CI. This closes the bounded source-delivery responsibility, not the
parent acceptance gate. Native first-prompt/process qualification, cold-bootstrap
repair, release observation and installed acceptance remain open under #7943
with the coordinating campaign and existing maintenance owners.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Guidance driver | Plan, source implementation, tests and reviewed merge | Managed source `lopsided-switch`; #7943 |

## Coordination

- **Topology:** sequential plan and implementation PRs.
- **Host (owns PRs):** Guidance driver.
- **Delegates:** none.
- Do not contact, read, subscribe to or reuse pre-existing Bridge agents.
  No agent-chat or remote worker dispatch. Detect collisions through read-only
  forge/task state and escalate them to the coordinating owner.
- **Handoff:** #7943 retains the incomplete native/installed objective; this
  archived effort and its validation report retain source evidence.
- #7694/#7788 retain native launch scripts and optional maintenance ownership.
- #7974 retains bootstrap context and worktree identity ownership.
- Manager loading/configuration/runner work remains outside this slice.
- Existing telemetry owners retain collection and SDK design; use existing
  launch progress and activity events, without a network dependency.
- Release promotion and authorized installed rollout remain separately owned.
  This source effort neither runs an updater nor restarts a shared service.

## Context

The pre-change Worktrees publisher resolved two complete source sets under its
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
- [x] Land this plan through normal automated review before implementation.
- [x] Record baseline consumer order, publication count and local timings.

### Phase 2 — Preparation and reuse

The following code mechanisms are **agent-recommended**.

- [x] Extend `static_instructions.py` with one complete resolved preparation
  and a checkout-local, versioned successful-publication receipt. Bind it to
  canonical checkout/home, exact enabled source identities, effective settings,
  resolved payload roots/declarations/templates and pair-composition state.
  A partial/error/timeout result never creates reusable readiness.
- [x] Retain one publication lock and revalidate captured inputs at its mutation
  boundary. Avoid a second complete desired-set reconstruction when captured
  input evidence is unchanged. Preserve current index/path/link checks,
  namespace-only cleanup and protection of tracked/staged removals.
  A concurrent newer source selection must never be replaced by an older one.
- [x] Preserve per-source independence: an unavailable enabled payload reports
  partial readiness while safe independent sources remain deliverable. Incomplete
  discovery never authorizes stale cleanup for unresolved sources or a reusable
  complete-readiness receipt. Allocation may succeed with an honest degraded
  diagnostic; it must not imply all guidance was delivered or repair payloads.
- [x] Reuse the receipt only while source, destination and Git-protection
  evidence still matches. Missing/corrupt evidence, changed outputs, settings,
  payload identity or paired composition forces safe preparation, not optimistic
  success. Do not rely on age, version text alone or an inherited environment
  flag as readiness authority.
- [x] Keep `local_cache_refresh.prepare_for_launch` as the foreground owner,
  with explicit generation/reuse fields in its structured result. Preserve
  final composition ordering and operation-specific limits; do not impose a
  broad timeout that silently permits an unprepared launch.
- [x] Route the Bridge prelaunch seam through the existing attributable
  Worktrees preparation command when available, consuming its structured result
  rather than rendering a competing namespace. Preserve standalone optional
  compatibility, same-cell identity and async cancellation/tree cleanup.
  A missing owner or failed preparation reports degraded fallback honestly.
- [x] Make `sessionstart_diagnostic` skip background regeneration for a verified
  current preparation. Otherwise retain one bounded backup owner. Completion
  persists a visible refresh/restart-required status through the existing
  guidance/status surface, with explicit unknown model admission and no hook
  invented to originate a turn. Failure remains failure, not restart success.
- [x] Clarify the compatibility renderer's skill advice: same-generation
  ordinary reads do not regenerate; managed prelaunch owns static readiness.
  Update the startup pattern and relevant plugin docs, with changefiles.

### Phase 3 — Review and completion
- [x] Land coherent source and documentation through review and required CI.
- [x] Report source merge, release promotion and installed verification
  separately. Transfer deployment acceptance to the existing authorized
  maintenance owner rather than starting a competing update.
- [x] Resolve Plan/Validation items, journal results and archive this effort.

## Validation Plan

All checks below are **agent-recommended** mechanisms for #7943 acceptance.

- [x] Deferred to `ThomasMichon/copilot-extensions#7943`: Full native
  create/resume/Bridge/SessionStart first-prompt chain qualification. Source
  failing-first and unchanged-generation controls pass without regeneration
  or backup enqueue; that is not installed model-admission proof.
- [x] Settings enable/disable, payload bytes/root/identity, pairing transition,
  missing/corrupt receipts, modified output and tracked/staged deletion changes
  invalidate correctly; unsafe publication preserves user and reviewed files.
- [x] Missing enabled payloads retain unresolved outputs and reviewed fallback,
  deliver safe independent sources, and expose partial rather than ready state.
- [x] Real independent-process contention proves current-generation selection
  wins, including a delayed old preparation and failed/incomplete composition.
- [x] Deferred to `ThomasMichon/copilot-extensions#7943`: Full native
  registration/publication/spawn/first-read acceptance. Source consumer-order
  controls and empty-home first-touch/cross-process reuse passed; the matched
  Windows cold-bootstrap case still failed independently of this source slice.
- [x] Blocked; transferred to `ThomasMichon/copilot-extensions#7943`: Native
  Linux/macOS descendant retirement and installed backup-notice qualification.
  Source controls cover late completion, no admission claim, failure, contention,
  cancellation, short-lived parents and inherited output pipes; unavailable
  native lanes and transport-finalizer warnings remain explicit.
- [x] Targeted contained Worktrees/Bridge suites, Python lint, install-contract
  and relevant source guards pass. Add source-only first-touch integration
  without live service or operator-home mutation.
- [x] Record before/after publication count and elapsed timings separately
  from broader launch latency. Preserve platform-specific skips explicitly.
- [x] Record whether native/live installed acceptance was run; it is not
  authorized by this source task and must not be implied by unit success.

## Proposal

Use generation-bound preparation at the existing owner boundary, not a new
global cache or service. Only ready records permit reuse; partial records retain conservative ownership history.
Revalidation must be cheaper than rebuilding and republishing the complete set,
but no optimization may infer safety from a stale or incomplete input census.
If the existing command/status contract cannot carry the required result,
coordinate the smallest additive owner API before changing another slice.

## Journal

### 2026-10-23 — Source merged; parent gates retained

- #8045 actually merged at `419a12d054193e3253f91a8395444608116fa1e3`.
  Copilot approved the exact final head with no blocking source-scope findings;
  all required CI passed. No CI bypass or deployment was used.
- Re-ran only the two independent-process reuse/freshness controls against the
  merged version-2 source: 2 passed. No broad suite or contended entry benchmark
  was repeated.
- Release was separately inspected: Main
  `2a1929032764cadccb08022222cebb80874b03aa` did not contain the new preparation
  module at this observation. Installed state was neither changed nor verified.
- Explicitly transferred full native entry, remaining process qualification,
  cold-bootstrap and installed acceptance to #7943 and the coordinating owners.
  Archived this source effort; #7943 remains open.

### 2026-10-22 — Review corrections

- Corrected the implementation-only Bridge contract fingerprint after its
  required source guard reported stale registry evidence; required CI then
  passed without bypass.
- Addressed all five automated-review findings with failing controls first:
  probe-timeout backup admission, concurrent publication notices, linked source
  inputs, ignore-file invalidation and preflight-bound Git evidence.
  Focused post-fix validation passed 19 cases without broad suite reruns or
  deadline increases. Source review and native acceptance remain distinct.
- A later review identified deadline propagation, abnormal-exit notification
  and nested Git custody gaps. The source corrections add real receipt-hash
  expiry and post-tool reaper controls, capture the watch timestamp before the
  probe, and retain the Git subtree under Bridge's existing owned-group cleanup.
  Focused results: Worktrees 11 passed/2 native Linux skips; Bridge 13 passed.
  Those skipped native controls remain an explicit #7943 acceptance obligation.
- Addressed the macOS cleanup finding without widening ownership into shared
  libraries: Bridge's already-required psutil backend supplies native birth
  identity, with a pre-spawn capability check and mismatch/access refusal.
  Five focused controls passed; native Darwin descendant scenarios remain
  explicitly unexecuted on this Windows worker and retained under #7943.
- Preserved mutation evidence before output effects so post-copy verification
  failure or abnormal exit cannot suppress the repair/restart notice. Failure
  counts are marked unavailable rather than displayed as zero delivered files.
  The failing real-output control now passes; focused results were Worktrees
  13 passed and Bridge 4 passed.
- Corrected the review's dangling-link revalidation defect without weakening
  healthy linked-input hashing: failed providers retain metadata-only evidence,
  while safe independent sources publish. Two failing controls became green
  in a seven-case broken/healthy source-link selection.
- Closed the later-publication and short-lived queue-parent notification gaps:
  the consumer reads the current publication revision and can diagnose a
  provably exited owner without the parent's reaper. Six focused integration/
  liveness controls passed, including a real parent/worker exit sequence and
  access-denial preservation. No process termination or request takeover is
  performed by this advisory reader.
- The requested clarification supplied a concrete cyclic-link case. Added
  failing fresh/cached controls, handled resolution `RuntimeError` per provider
  and as invalid cached evidence, and retained explicit deadline propagation.
  Ten focused cyclic/missing-source/deadline controls passed.
- Reproduced and corrected owner-exit cleanup waiting on descendant-held output
  pipes. The owned observer now detects process exit independently of EOF and
  is joined/cancelled within the finite command scope. Five focused controls
  passed; native qualification limitations remain in the validation report.
- Addressed the early-exit/missing-birth-evidence case by explicit preparation
  refusal before communication, not a weaker group signal or a broad startup
  timeout. The regression failed before the correction; the focused selection
  passed five cases with three platform skips. Cleanup uncertainty remains
  visible rather than being called a successful retirement.
- Corrected cache-only metadata being mistaken for active worker ownership.
  Foreground preparation without a request is now inactive; queued workers
  and unconfirmed admission locks remain protected. Three focused controls
  passed. This does not claim or change the cold full-roster entry path.
- Added byte-bound output ownership history for partial publication. Healthy
  providers cannot overwrite unresolved/unknown slots, but their proven slots
  remain updateable across partial passes. Three failing collision/legacy
  controls became green in a 13-case selection. Metadata version 2 explicitly
  separates partial history from reusable readiness.

### 2026-10-22 — Matched baseline and source-only continuation

- Collected all outstanding validation processes; they completed and no owned
  validation shell remained. Preserved implementation in `0f43df6a4`.
- The exact cold-hook case fails against unchanged pre-E and candidate source
  using the same canonical staged payload/input/deadlines. Bootstrap input
  hashes match; both reach the unchanged 30-second test limit while waiting for
  the direct stamp, whose declared lifecycle deadline remains 10 seconds.
- The scoped [validation report](validation.md) records commands, phase,
  identities, counts and matched publication measurements. Native/bootstrap
  root-cause repair and installed acceptance remain open under #7943; source
  publication is permitted only through passing review and required CI.

### 2026-10-22 — Reviewed plan and baseline

- #7994 merged with current-head approval and successful required checks.
- Baseline focused Worktrees suites: 107 passed, 4 platform skips in 110.54s
  (121.00s including preparation/cleanup). An initial overly long Windows test
  root hit Git's path limit; rerunning the four affected migration controls
  under a shorter owned root passed, followed by the full focused green run.
- Baseline foreground publication resolves the complete desired set twice;
  Bridge separately renders before spawn; SessionStart always queues backup.
  No installed readiness or model-admission claim follows from these tests.

### 2026-10-22 — Source implementation and bounded validation

- Implemented generation reuse, safe partial delivery, the existing Bridge
  owner-command seam and one-time post-tool late-completion diagnostics.
- Five focused Worktrees groups completed: 169 passed, 10 explicit platform/tier
  skips; Bridge completed 65 passed, 3 platform skips. Install-contract,
  headless-launch, module-size and touched-Python lint checks passed.
- Expanded cold-hook validation exposed a separate native-bootstrap question:
  the raw-source fixture omitted the vendored process helper. With the existing
  release materializer, the real cold stamp instead reached its unchanged
  lifecycle deadline. No production bootstrap change or deadline increase is
  authorized. A same-input pre-change comparison is required before assigning
  causality or attempting source-only publication.
- A single-provider source-publication probe measured one first touch at
  1.50 seconds (one desired-set construction, four Git calls), followed by two
  unchanged calls at 141 and 78 milliseconds (zero constructions/Git calls).
  This is not an end-to-end entry measurement and lacks a matched old-publisher
  timing baseline; neither broad launch improvement nor installed qualification
  is established.
- Source partial delivery may proceed through normal review and green required
  checks if the matched baseline establishes an independent native defect.
  The parent #7943 native/campaign acceptance gate remains explicitly open;
  no red required check may be bypassed and source merge is not full acceptance.

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
