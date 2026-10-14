# Session Intelligence and Accounting

- **Slug:** `session-intelligence-and-accounting`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees and serial PRs targeting dev
- **Created:** 2026-10-07
- **Status:** Active
- **Vision:** [agent-logger session intelligence](../../../visions/plugins/agent-logger/session-intelligence/README.md)
- **Umbrella issue:** #5665
- **Sub-issues:** #5676 (evidence preservation) · #5677 (catalog and accounting) · #5678 (aggregation and adoption) · #6439 (session-local usage evidence and historical estimation)

## Guiding Intent

Make agent-logger the reusable backend for complete session digests, durable
session catalogs, cost-attributable statistics, and machine-assigned daily fleet
aggregation. Existing applications consume this backend while retaining their
UX and domain-specific workflow. A standalone adopter can reproduce supported
usage charts and use the digests for later cross-machine retrieval.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary contributor | Architecture, integration, PR ownership, completion | Isolated per-phase worktrees |
| Bounded evidence workers | Logger, accounting, catalog, and rescue evidence | Read-only task delegation |
| Copilot reviewer | Proposal and implementation review | Repository PRs |
| Downstream compatibility steward | Consumer contracts and adoption evidence | Public-safe compatibility results |

## Coordination

- **Topology:** Serial independently reviewable phase PRs; shared contracts stay
  coordinator-owned, and implementation delegates receive disjoint scopes.
- **Host (owns PRs):** Primary contributor.
- **Handoff:** This canonical effort and phase contracts carry the public relay.
  Downstream-specific adoption has one-way private elaborations, never public
  links to private state.
- **Existing work:** Compose with the aggregate compiler and #1817; do not
  reimplement its admission, ownership, or precedence policy. Dependencies on
  unfinished enforcement must be explicit, not bypassed.

## Context

Existing agent-logger provides archive-aware session references, collation,
segment manifests, sync change tracking, chronicle reservations, and an
aggregate configuration compiler. Reuse those building blocks. Consumer
implementations provide proven event extraction, continuation watermarks,
SQLite catalog migrations, attribution joins, and elapsed-rate materialization.
Extract their general-purpose contracts without publishing private corpus data.

Process logs contain provider accounting that session-state alone cannot
reconstruct. Preserving those logs is part of this campaign, not an assumed
precondition. CodeSpace namespaces and verified container rescue snapshots are
first-class source evidence, not new billable sessions.

Detailed scope and proof obligations: [implementation contract](implementation-contract.md).

## Request

Public-safe capture of operator intent; bracketed substitutions remove private
consumer identifiers only. The unabridged private request stays with its driver.

> I would like to enhance copilot-extensions' agent-logger plugin. Presently, we
> clone over session-state folders into a target location of the user's
> configuration, we carefully excise large detritus from those folder, and we
> zip-archive session-state folders older than 30 days or so, to avoid space waste.
> We have a tool that can process session-state folders into "session-digests",
> which make the content more machine-ingestiable. And we have a separate
> [downstream usage application], that can extract the stats, such as token costs,
> model run rates, etc. I'm interested in assigning the agent-logger service with
> the duty to produce session-digests from all sessions, as well as to extract
> compilable cost-attributable stats from every session. Then, I want agent-logger
> to have a machine-assignable role to perform aggregations of those session-stats,
> to produce daily aggregations across all sessions for a given day, and deposit
> those alongside the "sessions" folder root, similar to what [the downstream
> applications] do. Basically, take the DB from [the downstream session catalog],
> and make it agent-logger's responsibility, and do the same with [the downstream
> usage application], so the copilot-extensions system provides all capabilities
> except the UX.
>
> Similar to how to vendor in agent-index into [a downstream search application],
> we'll do the same with agent-logger into [the downstream catalog and usage
> applications], so our [downstream] UX and containerized apps continue to operate
> as they do. We'll just now be able to share the capabilities outside [that
> deployment].
>
> My goal is that anyone using agent-logger can be able to produce the charts we
> currently produce (including the model run-rates per minute, breakdown by
> sub-agent and dispatch task type, bridge or not, etc), and to at least set the
> stage to be able to index and summarize session-digests across usage from all
> machines.
>
> agent-codespaces and agent-containers each have "logs rescue" systems which
> either merge session-states back amongst those of their host machines, or store
> them separate in a namespaced sub-folder (e.g. `.codespaces/<machine>` as opposed
> to `<machine>`). We'll want to make sure we don't miss those. See
> agent-codespaces' session recovery system for details.
>
> Enhance the overall visions for copilot-extensions, build out an effort, and
> drive this to completion.

Follow-up: effort slug confirmed as `session-intelligence-and-accounting`.

> Oh, in that case we'll need to ensure agent-logger also preserves and
> zip-archives logs on a schedule. I don't think it does that right now, as my
> work machines don't show logs/ under the [configured tracking folder].

Follow-up, 2026-10-12:

> We can update agent-logger's session-sync daemon to pull and store the additional
> information we need, for example selections from the session_store db associated
> with the sessions as we sync them. This will ensure we always have it; it's
> possible for the session_store.db to get corrupted and rebuilt, leaving only the
> session-state folders.
> We'll need a way to estimate tokens from events.jsonl transcripts, which I think
> are really the only durable artifacts we can count in for every session. There
> are probably "back-of-the-envelope" relationships we can dervice from the newer
> per-session metrics and their associated transcripts. That should give us the
> ability to produce a historical estimate.

Editorial note: the entire blockquote preserves the operator's wording verbatim,
including its original typos and nonstandard phrasing; only line wrapping changes.

Historical-estimate policy selected by the operator: `best-effort-with-uncertainty`
(report best-effort estimates with measured error ranges and explicit
extrapolation warnings, rather than require a fixed held-out accuracy threshold).

## Plan

### Phase 0 - Reviewed intent and contracts
- [x] Merge the vision revision and effort proposal before implementation.
- [ ] Carve phase issues and finalize versioned public-safe source, digest,
  accounting, catalog, daily-output, and consumer compatibility contracts.

### Phase 1 - Complete evidence preservation
- [ ] Preserve bounded, content-free session-local accounting observations before
  sync and compaction; retain prior evidence after store rebuild or corruption
  (#6439; [slice contract](usage-evidence-and-estimation.md)).
- [ ] Extend configured sync to preserve process logs alongside session-state.
- [x] Add canonical local target identity admission and kernel-owned publication
  claims under a lock spanning payload writes (#5808).
- [x] Integrate the reserved `session-sync push --source-identity-file` interface
  with the merged canonical source-publication helper (#6027).
- [ ] Add scheduled settled-log zip compaction, archive-transparent reads, and
  safe handling of active, rotated, resumed, or truncated files.
- [ ] Unify discovery of machine roots, CodeSpace namespaces, host-merged rescue
  sessions, verified container captures, and existing cold session archives.

### Phase 2 - All-session derivation and durable catalog
- [ ] Produce versioned digests and evidence-backed per-session statistics
  independently of chronicle readiness, rendering, and already-journaled status.
- [ ] Extract and host generic catalog, source watermarks, continuation identity,
  migration, revision, and rebuild primitives in agent-logger.
- [ ] Preserve source-qualified observations and resolve duplicate evidence
  without hiding genuine identity conflicts or missing telemetry.

### Phase 3 - Accounting and chart-complete query backend
- [ ] Add separately labeled transcript-only historical token estimates calibrated
  against measured sessions, with held-out error, applicability and explicit
  abstention when the available evidence does not support an estimate
  (#6439; [slice contract](usage-evidence-and-estimation.md)).
- [ ] Extract reusable event parsing, exact accounting, token composition,
  lifecycle, elapsed-rate, subagent, dispatch, and bridge/provenance joins.
- [ ] Offer versioned query/export and vendorable library contracts reproducing
  chart data and preserving unknowns, coverage, filters, and rate bases.
- [ ] Keep consumer-specific pricing policy injectable and transcript contents
  out of accounting stores and exports.

### Phase 4 - Assignable fleet daily aggregation
- [ ] Add independently configurable local-derivation and elected aggregation
  roles through the existing service/scheduled execution surfaces.
- [ ] Publish daily products adjacent to the configured sessions root with
  explicit timezone, source coverage, versions, and atomic revision updates.
- [ ] Support catch-up, late evidence, incremental invalidation, and clean rebuild
  without inventing a parallel queue, lease, or mandatory sibling dependency.

### Phase 5 - Adoption and completion
- [ ] Ship the supported pinned-vendor contract and validate both standalone and
  containerized consumers with unchanged query/presentation contracts.
- [ ] Record public-safe downstream parity and catalog migration evidence;
  consumer-specific changes remain in their own repositories.
- [ ] Update architecture, config, operational, and adoption documentation and
  land changefiles and reviewed implementation PRs.
- [ ] Verify released runtime adoption and complete every validation obligation
  before marking Done and archiving this effort.

## Validation Plan

- [ ] Session-local usage evidence and transcript estimates meet the bounded
  preservation, privacy, replay, archive, calibration and uncertainty checks in
  the [slice contract](usage-evidence-and-estimation.md); unsupported historical
  cohorts remain visibly unestimated.

- [ ] Source preservation: synchronized live and zipped process logs retain
  provider-metered telemetry and survive active append, rotation, resume, partial
  copy, crash/retry, and archive transitions without loss or double counting.
- [ ] Corpus coverage: ordinary roots, nested CodeSpace roots, merged rescues,
  verified/partial container captures, live/archive duplicates, conflicting UUIDs,
  and resumed old sessions are represented with correct identity and coverage.
- [ ] Digest/catalog parity: all admitted sessions, including empty and ongoing,
  get explicit products; source hashes, continuation boundaries, existing terminal
  workflow state, and incremental-versus-rebuild equivalence are preserved.
- [ ] Accounting parity: a fixed synthetic corpus matches existing event IDs,
  integer token/nano-credit quantities, model intervals, weighted rate variance,
  attribution, unknowns, filters, pagination, and coverage; floats use an explicit
  documented tolerance.
- [ ] Daily parity: totals match event-ledger queries across machines, midnight,
  daylight-saving transitions, late ingestion, corrected/resumed sessions, and
  idempotent backfill; partial/stale days never claim complete coverage.
- [ ] Role/config safety: unassigned/passive machines do not aggregate; explicit
  authorization and aggregate collisions gate effects; absent optional siblings
  do not block deterministic local accounting.
- [ ] Consumer adoption: pinned vendoring and additive migration preserve current
  APIs, chart series, DB state, and domain workflow in both downstream applications.
- [ ] Privacy and boundedness: accounting exports exclude transcript content and
  secrets; archive processing and pagination are bounded on large inputs.
- [ ] Linux and Windows scheduled collection/compaction and query behavior have
  equivalent contracts; platform-specific execution evidence is recorded honestly.
- [ ] Focused plugin/consumer tests, install-contract and changed-plugin guards,
  and a practical fresh-install/role scenario pass through bounded test paths.
- [ ] Reviewed PRs merge; released code is adopted and behavior verified, not
  merely committed or primed for deployment.

## Proposal

Reviewed and merged in #5671. Implementation is authorized against this plan.

## Journal

### 2026-10-13 - Process-log source containment reviewed and merged
- #6156 merged at `2026-10-14T06:35:39Z` as
  `f79c873ee4c03022216d5d1a0ff3d8a79f9e4fc1`, from final head
  `f66434f9bb7e78574766479e5d7c3e95af53040f`. The current-head Balanced
  Copilot review had zero open findings. Complete successful same-head CI
  `38515571854` and fresh required-check run `38518755723` both finished
  before merge; historical watcher rollups were not used as gate evidence.
- Process-log enumeration, direct reads and filesystem transfer sources
  reuse canonical ancestor pinning. Intentional ancestor aliases, configured
  reference identity, incremental POSIX enumeration, native handle release,
  root-change diagnostics and per-entry copy deferral retain their documented
  contracts. Only source-opener `ELOOP` is treated as a skipped candidate;
  other source-open and all destination-publication failures remain visible.
- Final-head Linux CI executed the agent-logger suite in two contained groups:
  476 passed / 29 skipped and 823 passed / 32 skipped. The publisher's native
  rebased-head selection passed 192 tests with 87 documented platform skips
  and verified cleanup. The preceding 264-pass Linux receipt is historical,
  not substituted for validation of the final rebased head.
- Re-read the effort's Plan and Validation Plan after actual merge. This
  closes the process-log source-read containment slice only. #5727 remains
  open for destination containment; reclamation, complete transports,
  source/session discovery, usage-evidence preservation, catalog/accounting,
  daily products, fixed-corpus consumer parity and released adoption remain
  active. No original evidence was retired or runtime rollout performed.
- The next preservation stretch remains coordinated with the existing
  publisher and the separate usage-evidence slice. This receipt does not
  transfer source publication ownership or authorize a second concurrent
  driver of those shared modules.

### 2026-10-13 - Usage-evidence amendment reviewed and merged
- #6442 merged after the current-head Copilot review had no open Medium/High
  findings and the configured required CI contexts passed. The worktree
  reconciled through the managed PR completion path.
- The amendment authorizes additive session-local accounting capture and
  session-held-out transcript estimation with explicit uncertainty. It does not
  claim either runtime capability is implemented.
- A non-required reviewer infrastructure check separately failed to acquire a
  runner; the watcher exposed only a failure rollup. #6495 owns the bounded
  diagnostic correction before this contributor resumes the accounting slice.
- The effort remains Active; preservation, calibration, consumer adoption and
  the parent validation obligations remain open.

### 2026-10-12 - Native process-log source boundary validation
- Windows enumeration collects every ZIP member under the listing's pinned
  ancestor boundary and releases file/directory handles before any public
  reference yield. Native regressions attempt ancestor rename at the ZIP open
  boundary and rename both files and ancestors while enumeration is suspended.
- Descriptor-relative reads reuse the shared regular-file opener rather than
  importing private flag construction. Root opening errors are normalized
  without relabelling consumer failures; unsupported Windows directory-fd reads
  refuse explicitly. Sorted entry/member observations and path-bearing invalid
  source diagnostics retain their public contracts.
- Bounded native Windows selection passed **173 tests, 73 skipped** with
  supervisor cleanup verified. The skips include POSIX races and unsupported
  native publication cases, not substitute platform successes. Fresh Linux
  execution and exact-head CI remain required; the earlier Linux result is
  historical evidence, not validation of these changes. Install-contract,
  touched-file F/E9 lint and the unchanged module ceilings pass.
- The source-read slice remains pending reviewed merge. The effort and #5727
  remain open for their other containment, preservation and adoption obligations;
  no source retirement, runtime deployment or configuration change was performed.

### 2026-10-10 - Process-log source ancestor containment implementation
- Resumed the source-read portion of #5727 under the canonical preservation
  allocation. Enumeration, direct raw/gzip/ZIP reads and filesystem transfer
  sources now reuse the canonical ancestor directory boundary from #6117;
  intentionally relocated ancestors remain supported. Missing required POSIX
  safety flags fail explicitly, and generator body errors retain their cause.
- Bounded network-disconnected Linux selection covering process logs, ZIP
  preservation, source roots and independent transfer-leg health passed
  **231 tests, 5 skipped**. Native rename-denial regressions for enumeration,
  direct reads and sync are included in the existing `source_roots` Windows
  selection; native execution and exact-head full CI remain pre-merge gates,
  not claims inferred from Linux. Install-contract and touched-file lint pass.
- Destination publication containment remains open in #5727. Reclamation,
  catalog/accounting/daily products, pinned consumer parity, release and
  approved adoption remain active. No original evidence was retired and no
  live source, target, configuration or deployed runtime changed.

### 2026-10-10 - Additive ZIP preservation landed
- Source PR #6117 merged as `0f13c88d52d645ae22c90a836604fbd35608178d`
  after exact-head Copilot approval and complete successful CI run
  `38108854234` on `0eeed158d04b4944f16d66924238b32f5b4928ce`.
  Required checks were complete before merge; the earlier non-required
  review-request throttle did not substitute for a review.
- The final source explicitly refuses unsupported POSIX safety flags with
  `ENOTSUP`, rather than dropping no-follow/directory protections. The two
  carried-over Medium findings were revalidated after replies anchored them
  to the existing fix; no additional speculative rewrite was needed.
- Final bounded preservation tests passed 438 Linux cases (21 skips) and
  190 native Windows cases (21 skips), including absent-root and handle
  lifetime cases. The isolated native test checkout was finalized only after
  the tested source landed. The retained source checkout reconciled through
  its normal PR completion command.
- Phase 1's compaction and complete transport/discovery checkboxes remain
  open: this is verified additive preservation, not source reclamation,
  normalized-storage parity, full ancestor containment, release or adoption.
  Next source slices remain reclamation and #5727 containment, followed by
  catalog/accounting/daily products and pinned consumer parity. No live
  publication, evidence retirement or system setting change was enabled.

### 2026-10-10 - Review-request recovery and absent-root correction
- A confirmed GitHub REST quota exhaustion prevented the review-request
  workflow from reaching the request. After the reported reset and a cooldown,
  an explicit API re-request produced a new whole-PR review at the published
  head. No workflow was rerun or merge gate bypassed.
- That review confirmed the earlier decoder fix and found an initially absent
  optional process-log root incorrectly failed snapshot commands. The fix
  treats only initial absence as an empty pass, without creating the root;
  permission, unsafe-root and disappearance-during-opening failures remain
  explicit. Existing transfer-health semantics remain unchanged.
- Code `9d3cac763` passed 435 bounded Linux sync/source/process-log tests
  (21 skips), including the new absent-root and refusal regressions. Native
  Windows evidence still belongs to the earlier source until rerun.
- Exact updated-head review, complete required CI, merge, release and adoption
  remain separate gates.

### 2026-10-10 - ZIP review corrections validated
- The first source review found an uncaught ZIP runtime failure and stale setup
  guidance. Snapshot verification now rejects unsupported codecs explicitly
  and records encrypted/corrupt-deflate runtime errors per file, preserving
  later candidates and the scheduled raw-log transfer. A parameterized synthetic
  regression exercises all three real decoder failure shapes, not a mocked
  archive failure.
- Updated the setup skill's scheduled/manual snapshot guidance, canonical config
  example and discoverable command triggers. The customization scan reports
  zero blocking findings and none on the changed setup skill.
- Exact code `758971aa6838a423c7a8c5216b157f32a599c2b4` passed 426 Linux
  network-disconnected sync/source cases (21 skips) and 183 bounded native
  Windows ZIP/health/source cases (16 skips). Linux admission initially found a
  live test-slot holder; a bounded wait admitted the run without stopping it.
- Source re-review, exact-final-head CI, merge, release and adoption remain
  separate outstanding gates. No original evidence was rewritten or retired.

### 2026-10-10 - Native ZIP validation corrected and passed
- Native Windows execution found three ZIP snapshot failures after the earlier
  admission deferral. A synthetic metadata probe confirmed that `lstat` and
  `fstat` can report different `ctime` meanings for one unchanged file.
  The source fix uses stable Windows birth time, protects snapshot/verification
  reads against writes and rename, and verifies a newly published ZIP through
  its original kernel-owned handle rather than conflicting with its own
  protected write handle.
- Exact source `0bcc86120a68d8e40816182dfc898d07fe0bb218` passed the bounded
  native Windows ZIP/process-log/combined-health/source-publication selection:
  180 passed, 16 deliberate platform-fixture skips. The native directory and
  file handle-lifetime tests executed. Matching Linux network-disconnected
  sync/source selection passed 423 tests with 21 skips; both platform type
  checks and install-contract passed.
- This supplies native execution evidence for the new ZIP and inherited health
  regressions, not release or consumer adoption. The source remains local,
  unreviewed WIP; no public implementation PR or live publication was performed.
  Source reclamation and all remaining parent Plan/Validation Plan items stay
  open.

### 2026-10-10 - Final health CI verified; ZIP preservation staged locally
- Verified run `38098437985` completed successfully on exact health source
  `155336c74ac2a41fad9e0550c7e821bec6e79980`. This resolves the outstanding
  final-head CI obligation, not the historical timing correction: checks were
  still pending when #6067 merged. The corrected journal #6084 merged as
  `215364921396009df16f08e8c6d67de83c6ed555`.
- The Windows archive-source lane selected only sessions, session ZIPs, source
  roots/publication and chronicle contracts (435 passed, 14 skipped). It is not
  native execution evidence for the new health regressions.
- Staged a source-only, non-retiring ZIP preservation slice: explicit
  local-producer opt-in, active-idle PID protection, content-addressed revisions,
  bounded streaming and full member-hash verification, kernel-owned no-replace
  publication, and pinned directory traversal. Reused the admission claim-file
  primitive rather than retaining a named-temporary cleanup implementation.
  Process-log configuration and command reporting have separate components
  so the core config/engine stay within their enforced module ceilings.
- Final focused Linux network-disconnected selection passed 423 tests with
  20 skips; Linux/Windows type checks, lint, install-contract and docs-consistency
  passed. A native Windows validation attempt was deferred by the host test
  supervisor because its validation slot was unavailable; no native test
  execution or acceptance is claimed.
- This source is local WIP, not a reviewed/merged/released capability. Native
  acceptance, public review/publication, and the remaining compaction/storage
  gates are still owed. All originals and existing archives remain untouched;
  reclamation, full sync/reader ancestor containment (#5727), catalog/accounting,
  daily products, pinned consumer parity and approved rollout remain open.

### 2026-10-10 - Independently recoverable transfer health merged
- #6067 merged as `cb1f9772ac40b41829920622f67228f3f9ef8989` after exact-head
  approval with zero open findings. Final-head CI run `38098437985` was still
  pending when the merge occurred; the earlier completed successful run was
  on `a39aba135`, two source commits earlier. The watcher's aggregate success
  field was not exact-head CI proof. Final-head completion remains owed.
  Session-state
  and process-log legs retain independent status, partial streaks, freshness,
  bounded failure reasons and filename-only deferred samples. Retry success
  clears only its own leg; sustained log failures reach the existing health
  threshold even across successful session transfers.
- All metadata writers and classification share complete leg/aggregate
  validation. Legacy heartbeat migration preserves the original attempt
  timestamp, exact deferred count and streak. Null/malformed leg metadata is
  preserved with explicit update warnings rather than silently repaired.
- Process-log root identity comes from the validating stat, matches the opened
  POSIX descriptor before enumeration, and is rechecked after copying. Missing
  or replaced roots remain partial; empty existing roots remain successful.
  The broader ancestor containment obligation is not closed.
- Final bounded, network-disconnected Linux selection passed 88 tests.
  Mypy of health/metadata, Ruff, install-contract and changed-module guards
  passed. Earlier-head Windows archive-source CI passed, but final-head CI
  remained pending at merge and native Windows
  execution of the new health regressions is not claimed.
- #5727 remains open for ancestor containment. Scheduled settled-log compaction,
  complete transports/admission, catalog/accounting extraction, daily products,
  pinned consumer parity and released runtime adoption remain unresolved.
  Both the complete preservation and overall validation gates stay open;
  no live publication, corpus rewrite, source retirement or rollout occurred.

### 2026-10-10 - Explicit identity-file CLI integration merged
- #6027 merged as `58789f46905b350d8c600b37265f34ab0d8fd366` after current-head
  approval with no unresolved Medium/High findings and green required CI, including native Windows
  archive-source contracts. The optional flag reuses bounded canonical metadata
  loading and validates its canonical publication key before target creation;
  the actual identity reaches the reviewed target admission transaction.
- The final approval retained one Low documentation-impact finding; the PR body
  was updated with that assessment before merge, but the review still listed it.
- Legacy no-identity pushes remain unchanged. Identified unsupported transports
  fail explicitly; a corrected OneDrive test pins its real temporary root and
  checks the specific admission rejection, not an ambient-root discovery error.
- Released caller-only recovery/live-mirror and capture-publisher integration
  to the source-unification owner via #5665. This releases source ownership,
  not live publication authorization. Broader preservation, compaction,
  containment/health (#5727), catalog/accounting and adoption gates stay open.

### 2026-10-07 - Kickoff
- Confirmed target effort adoption and public coordination issue #5665.
- Completed four read-only evidence tracks before planning the shared boundaries.
- Classified this campaign as vision-extending and reused existing digest,
  archive, compiler, and accounting building blocks.
- Captured the follow-up requirement to preserve and zip-archive process logs.
- Proposal remains behind the repository review gate.

### 2026-10-07 - Proposal approved
- Merged the vision and canonical proposal in #5671 after a clean approving
  review and passing required checks.
- Reconciled the execution worktree onto the merged proposal. The umbrella
  remains open; no implementation or adoption completion is claimed.
- Next slice: complete process-log evidence preservation and compressed-input
  compatibility before extracting accounting consumers.
- Carved implementation trackers #5676, #5677, and #5678; they preserve the full
  parent completion gate rather than reducing the campaign to its first slice.
- Started #5676 with an archive-transparent process-log reader, focused synthetic
  tests, and an explicit library contract. This is local WIP only: it has not
  run the bounded plugin tests or install/changed-plugin gates, and no
  implementation PR is open. Validate and integrate it before publication.
- The reader is an input primitive, not completed log sync or compression.
  Scheduled preservation, all-session derivation/catalog/accounting, daily role
  aggregation, consumer vendoring, and release-backed adoption remain outstanding.

### 2026-10-07 - Evidence reader validated and published
- Validated the process-log evidence reader: full `agent-logger` suite (752
  passed, 25 skipped) and `ruff` pass inside the test-isolation devcontainer;
  install-contract, docs-consistency, runbook-references, version-consistency,
  module-size, and large-files guards all pass.
- Rebased cleanly onto `origin/dev` and opened PR #5690 for review. This closes
  only the reader slice of #5676; scheduled sync/compression and accounting
  ingestion remain open next slices.
- Copilot review ran five rounds over PR #5690, each COMMENTED (non-blocking)
  until the final APPROVED verdict. Real findings addressed in order: (1) a
  ZIP archive/fd held open for the whole enumeration generator's lifetime,
  closed before yielding; (2) a High-severity root path-swap race (traversal
  through a symlinked `log_root` bypassing the final-component-only
  `O_NOFOLLOW` guard), fixed by pinning directory listing and per-entry ZIP
  reads to one verified `O_NOFOLLOW`-opened directory handle on POSIX; (3) a
  second High-severity finding that deferred `iter_lines()` reads still
  reopened by path, fixed by carrying a `verified_root` on `ProcessLogRef`
  and reopening it with `O_NOFOLLOW` immediately before each read; (4) an
  overly broad `except OSError` that relabeled permission/I-O/descriptor
  failures as "not a directory", narrowed to the two expected non-directory
  signals; (5) unrelated `.log` ZIP members (e.g. `notes.log`) incorrectly
  rejected as non-flat, fixed to check the leaf name against the process-log
  pattern before rejecting; (6) a relative `log_root` resolving against
  whatever directory was current at read time, fixed by pinning it absolute
  at enumeration. Windows keeps the previous, weaker path-based guarantee for
  all of these POSIX-only protections -- a documented, not hidden, platform
  gap. Final suite: 486 passed, 25 skipped; all repo-wide guards pass.
- Merged PR #5690 (squash) after the APPROVED verdict; `pr-complete`
  reconciled this worktree onto `origin/dev` post-merge. This closes the
  evidence-reader slice of #5676 only -- scheduled process-log sync/
  compression, all-session derivation/catalog/accounting (#5677), fleet daily
  aggregation, consumer vendoring, and release-backed adoption (#5678) remain
  fully outstanding. The umbrella #5665 stays open.

### 2026-10-08 - Process-log sync publication (filesystem targets) merged
- Wired `iter_process_log_refs`'s sibling root-pinning primitives into
  `session-sync`: `sync.process_logs.enabled` (opt-in) now publishes
  `<sync_source>/logs/` alongside session-state, for `local`/`onedrive`
  targets only, and only on an unfiltered sync pass (a process log isn't
  scoped per-repo the way session-state is, so a repo-scoped pass skips
  publication entirely rather than attempting to filter it -- admission
  fencing for that case is an explicit, named follow-up, not silently
  approximated).
- Opened PR #5710. Copilot review posted seven verdicts (not six, as an
  earlier draft of this entry incorrectly stated), COMMENTED throughout
  (verdict tier softened from "Changes recommended" to "Needs a closer
  look" only on the seventh and final round). The seventh round also
  introduced its own new finding rather than merely softening tier on an
  old one: "Repeated process-log failures cannot advance the persisted
  partial streak to the fleet-health alert threshold" -- `_mark_sync_meta_partial`
  only ever writes a flat `status: partial` with the latest failure
  reason, with no persisted counter/timestamp of how long a machine has
  been stuck there, so nothing downstream can distinguish a fresh
  degradation from one unresolved for weeks. This is now tracked as item
  3 of #5727 alongside the other two declines below, rather than treated
  as closed by the tier softening alone. The first six rounds closed a
  real progression of findings:
  POSIX root-pinning for the process-log directory walk (reusing
  `process_logs.py`'s own primitives, now promoted to public:
  `supports_dir_fd`/`open_root_dir`/`open_regular_at`); rotation/deletion
  tolerance (a vanished or rotated file is a skipped candidate, never a
  push failure); a fd-based metadata capture to stop a truncate-in-place
  rotation from landing stale bytes under the replacement's fresh mtime;
  rename-based rotation detection (the fd-only check above can't see a
  rename, since the descriptor still refers to the original inode); a
  genuine data-loss bug the rename-detection fix itself introduced --
  caught by the *same* review process one round later and fixed before
  merge (revalidation now runs before the atomic commit, never after, so
  a rejection can never destroy a previously-synced, still-good
  destination copy); a containment-escape hardening of the now-public
  `open_regular_at` (reject any non-bare-filename argument itself, not
  relying on callers to prefilter); and persisted `sync-meta.json` health
  reconciliation (a process-log failure or deferral now downgrades the
  session-state leg's already-written `ok` status to `partial`, preserving
  its other recorded fields rather than resetting them).
- Two HIGH findings were explicitly declined, with reasoning recorded in
  both the PR description and the affected functions' own docstrings:
  neither `open_root_dir` (source-read side) nor the destination-write
  side pins every *ancestor* path component via dir_fd -- both protect
  only the directly named, configured path itself, matching this
  already-merged module's existing, pre-PR posture everywhere else
  (`_existing_real_directory`'s own validation has the identical
  property). Fully closing either needs a broader dir_fd-anchored rework
  of this module's whole directory-validation approach, shared by code
  this PR doesn't otherwise touch -- out of scope for this bounded slice,
  and would also reject a legitimate system with an intentionally
  symlinked ancestor (e.g. a relocated `$HOME`). A related Medium
  (composing one clearable status across both transfer legs, rather than
  requiring explicit operator investigation to clear a `partial`) was
  also declined as a bounded follow-up. **All three of these declines
  (the two HIGH findings above, this Medium, and the seventh-round
  streak-escalation finding noted above) were originally recorded only
  as docstring/PR-description prose, with no tracking issue -- a
  documented limitation is not remediation.** They are now tracked as
  [#5727](https://github.com/ThomasMichon/copilot-extensions/issues/5727)
  so the deferral doesn't silently stay undone.
- **This did not actually meet CONTRIBUTING.md's documented review bar.**
  `CONTRIBUTING.md:124-130` requires zero *open* Medium/High findings to
  merge on a `Comment` verdict -- not merely zero *new* ones per round --
  and the seventh (final) verdict still carried the two declined HIGH
  findings and the declined Medium finding as open. An earlier draft of
  this entry incorrectly framed the merge as meeting that bar because no
  *new* Medium/High finding appeared after round six; that conflated "no
  new finding" with "zero open findings," which are not the same test.
  Separately, and correcting a second misstatement in an earlier draft
  of this entry: `dev` *does* require an approving review (ruleset "dev
  branch policy: review required (maintainer bypass)", see
  [docs/pipelines.md](../../../docs/pipelines.md)) -- the repo owner who
  merged #5710 is a named `bypass_actors` entry on that ruleset, so this
  genuinely was the documented **Maintainer bypass**, not an absence of
  any review rule. Recording all three facts plainly: the merge used the
  documented Maintainer bypass (mechanically permitted), and it still
  carried known-open Medium/High findings that were declined, not
  resolved -- now tracked as #5727 instead of silently treated as
  accepted. `pr-complete` reconciled this worktree onto `origin/dev`.
- This closes only the "extend configured sync" bullet's filesystem-
  targets/unfiltered-passes slice. SSH/ingest target support, repo-scoped
  admission fencing, scheduled settled-log ZIP compaction, and unified
  source-root/CodeSpace/rescue discovery (explicitly owned by a
  coordinating peer per the private effort's journal) all remain
  outstanding. The umbrella #5665 stays open.

### 2026-10-09 - Canonical target admission merged
- Merged #5808 as `32582587eb76a4ab7f4be0f4670ff6d00064060f` after a current-head
  zero-finding review and passing required checks. Local targets admit canonical
  schema-v1 identities, preserve declared aliases, reject unowned content, and
  hold the destination lock through payload, index, and health publication.
  Linux uses anonymous kernel-owned claims; Windows uses original-handle
  publication and cancellable deletion disposition. Unsupported identified
  OneDrive/SSH/ingest publication fails closed.
- Integrated reviewed installer repair #5885. A stale-base exact-ancestor replay
  defect was fixed in #5983; the release-blocking deadline fixture was fixed in
  #5989. Normal promotion #5995 released both prerequisites before publication
  used the verifier and exact expected-head lease. No replay metadata was
  fabricated and no rewrite protection was bypassed.
- The integrated Linux logger suite passed 1,110 tests with 41 skips. Exact
  published head `b909ca617` passed native Windows publication/failing-case
  selection (73 passed, three skips) and the exact archive-source CI selector
  (426 passed, 14 skips). CI initially reported a denied replacement-directory
  rename in one rescue test; its cause remains unproven and tracked in #6004.
  One authorized same-head failed-job rerun passed before merge. A recurrence
  requires live lock-holder evidence rather than repeated retries or deleting
  retained recovery state.
- This closes the bounded target-admission slice only. The identity-file
  engine/CLI integration is next; source callers remain gated until its separate
  reviewed receipt exists. #5727 containment/health work, complete scheduled
  preservation/compaction, catalog/accounting, daily roles, and release-backed
  consumer adoption remain open. The effort stays Active.
