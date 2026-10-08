# Session Intelligence and Accounting

- **Slug:** `session-intelligence-and-accounting`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees and serial PRs targeting dev
- **Created:** 2026-10-07
- **Status:** Active
- **Vision:** [agent-logger session intelligence](../../../visions/plugins/agent-logger/session-intelligence/README.md)
- **Umbrella issue:** #5665
- **Sub-issues:** #5676 (evidence preservation) · #5677 (catalog and accounting) · #5678 (aggregation and adoption)

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

## Plan

### Phase 0 - Reviewed intent and contracts
- [x] Merge the vision revision and effort proposal before implementation.
- [ ] Carve phase issues and finalize versioned public-safe source, digest,
  accounting, catalog, daily-output, and consumer compatibility contracts.

### Phase 1 - Complete evidence preservation
- [ ] Extend configured sync to preserve process logs alongside session-state.
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
