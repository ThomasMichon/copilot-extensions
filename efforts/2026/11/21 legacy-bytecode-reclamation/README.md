# Legacy Bytecode Reclamation

## Goal

Public-safe operator request, verbatim:

> safe automatic reclaim of legacy unsealed and retired installed bytecode prefixes on Windows (bytecode-only, regenerable, not referenced by live processes or selected/retained runtimes), budgets sized to drain a backlog in a bounded time, and loud surfacing in update/status/doctor with the exact command.

- **Slug:** `legacy-bytecode-reclamation`
- **Repo:** copilot-extensions
- **Branch(es):** dependency-ordered PRs against `dev`
- **Created:** 2026-11-20
- **Status:** Done
- **Vision:** [regenerable bytecode reclamation](../../../../visions/installer/README.md#regenerable-bytecode-reclamation) and [authoritative replacement](../../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #12886
- **Parent:** [Derived Disk Hygiene](../../../active/derived-disk-hygiene/README.md), bytecode follow-on

## Guiding Intent

Stopping a producer does not retire its historical cache. Give recognized
bytecode a complete reclamation lifecycle without weakening live-use,
runtime-retention, content or native filesystem protection. A useful report
distinguishes measured backlog from proved reclaimable bytes.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| Cache-reclamation driver | Design, implementation, qualification and PR stewardship | #12886 |
| Runtime maintenance owners | Existing selection, construction, retention and process custody | Scoped source changes and existing native contracts |
| Derived Disk Hygiene coordinator | Parent scope and unrelated tracks | #10968 |

## Coordination

- **Topology:** one isolated source worktree; review the design before runtime changes.
- **Host (owns PRs):** Cache-reclamation driver, through actual merge.
- **Delegates:** none; existing runtime owners remain authorities, not bypasses.
- **Handoff:** carry this Goal, current phase, proof gaps, active commands,
  authorization limits, release state and next action.
- Preserve the accepted earlier lifecycle delivery. This is a new bounded
  follow-on, not a rewrite of its completed record or the parent's other tracks.

## Context

The installed dry run on a Windows host inventoried 2,971 legacy unsealed
prefixes, 1,494,616 files and 28,291,385,258 logical bytes. All prefixes were
held as `liveness-unproven`; the top-level eligible-byte summary was zero.
This proves a reporting and admission gap, not permission to delete those bytes.
The installed pruner does not inventory installed-prefix bytes.

The explicit pruner supports unsealed prefixes only. The shared automatic
collector recognizes unsealed and installed prefixes but intentionally removes
only source-qualified leaves under exact absent runtime slots. It refuses whole
prefixes as `legacy-whole-prefix-no-writer-custody`. Its 32 MiB pass budget and
ordinary update cadence are not a practical backlog-drain strategy.

The [proposal](design.md) separates proof, inventory, drain work and reporting.

## Request

The Goal records the public-safe functional request. Also required:

- Establish an installed read-only baseline before source implementation.
- Include a larger remote backlog in the read-only measurement report.
- Use a new source worktree, normal changefiles, review, CI and `dev` PR flow.
- Supply measured eligible/held bytes and the exact supported relief command.
- Do not delete real caches or use age-only admission as automatic liveness proof.

## Scope Approvals

Authorized: source design and implementation, normal repository publication,
read-only installed/fleet assessment, and destructive validation in owned
disposable fixtures. No host cleanup, candidate installation, process
termination, remote mutation or fleet rollout is authorized by this effort.
An approved design or dry run does not grant those effects.

## Plan

### Phase 1 - Review the lifecycle repair

- [x] Record the installed baseline and claim #12886.
- [x] Trace explicit pruning, shared legacy collection and runtime protection.
- [x] Reconcile and land this proposal through Copilot review and required CI.

### Phase 2 - Qualify whole-prefix custody

- [x] Implement native Windows cache-reference observation for the installation
  owner, including environment and interpreter-argument references, stable
  process identity, opaque-process outcomes and bounded native operations.
- [x] Combine reference proof with existing construction/selection/retention
  custody; preserve selected, rollback, retained and live runtime consumers.
- [x] Admit only recognized, old, bytecode-only, non-linked namespace trees.
  Keep an explicit hold when any required proof is missing.
- [x] Prove the race boundary and restart-safe progress with actual Windows
  disposable consumers before enabling automatic whole-prefix effects.

### Phase 3 - Deliver drain work and reporting

- [x] Reuse one planner for explicit dry run/apply and automatic maintenance,
  with installed-prefix support rather than a second destructive implementation.
- [x] Add bounded, resumable catch-up work and an aggregate drain deadline.
  Use the existing native process/maintenance owner, not an unowned shell or
  new permanent reaper service.
- [x] Surface measured, eligible, held and unmeasured bytes, reasons, progress,
  observation age and the exact command in update/status/doctor.
- [x] Keep ordinary status reads cheap and update selection independent of
  maintenance failures, pending work or a large backlog.

### Phase 4 - Validate and deliver

- [x] Pass targeted unit, native Windows, collector/installer integration,
  type/lint, generation and install-contract gates.
- [x] Repeat read-only real-host assessment with the qualified implementation;
  report actual eligible bytes separately from age-only or unmeasured estimates.
- [x] Steward implementation PRs through current-head Copilot review, green
  required checks and merge; report release and rollout separately.
- [x] Resolve every phase/validation item, reconcile the parent and archive
  before releasing the effort and finalizing its source workspace.

## Validation Plan

- [x] Dry run never mutates cache contents; summary counts held bytes by reason.
- [x] Selected/retained/rollback/construction and live-prefix references refuse.
- [x] Unknown owner, process access failure, PID reuse, new references, changed
  namespace identity and concurrent writers retain work without false success.
- [x] Reparse points, symlinks, hard links and non-bytecode evidence survive.
- [x] Unsealed and retired installed prefixes can actually empty in native
  Windows fixtures; proof is not limited to mocked source-qualified leaves.
- [x] Large-volume fixtures cross multiple byte/time/entry budgets and restart
  boundaries with fair progress and a measured aggregate drain rate.
- [x] Catch-up ownership survives the admitted caller boundary, or records a
  truthful interrupted result; cancellation and unknown effects are not replayed.
- [x] Metadata/status work does not recursively rescan a multi-million-file
  cache on normal reads; incomplete measurements never become zero-byte claims.
- [x] Existing new-cache selection, POSIX protection, automatic-update policy,
  incoming installation readiness and service/session custody remain intact.
- [x] No real cache deletion, installed rollout or relief on a remote host is
  inferred from source tests, merge or release.

## Proposal

See [design.md](design.md). Its gates deliberately do not promise eligible
bytes until the installed ownership and live-reference proof passes.

## Journal

### 2026-11-21 - Source completion and archive

- #13865 merged into `dev` as `190d12cfcfbe1a71bfc5bad52763e513370010e4`.
  Current-head Copilot review `6004739492` confirmed the three code findings
  resolved. Its remaining low-severity impact-assessment omission was corrected
  in the PR description and its thread resolved. No review finding remains open.
- Required CI passed on the exact source head, including the eligible-unit
  packet, guards/lint, source attribution and PR gate. The mixed-Python native
  lane passed. Run `44896048102` also passed both Linux and Windows-publication
  qualification, including the retained-client handover case that timed out on
  the preceding head. No required check was bypassed.
- The source and native fixture gates above complete this bounded effort.
  The local pinned-checker preparation and unrelated Windows doctor-fixture
  failures remain explicitly recorded below; they did not waive required CI
  or authorize unrelated source repairs. The ordinary provider checks passed.
- The qualified read-only assessment proved zero eligible bytes, not a safe
  host drain. Process-owner visibility remained incomplete. No real cache was
  deleted, no candidate installed and no process terminated. Release promotion,
  authorized installation and any host relief remain separate operations.
- Reconciled the parent follow-on and effort index, then moved this record and
  its reviewed design to the dated archive. The source worktree releases its
  effort binding and finalizes only after this closeout clears review and CI.
  The parent's other tracks and installed-observation gates remain open.

### 2026-11-21 - Review corrections

- The second review exposed mixed-case Windows references, missing recorded
  foreign-consumer observation and interrupted prefixes stranded by a stale
  admission cutoff. Regressions now cover each path through the production
  runtime owner, including opaque consumers, fresh grace-period admission and
  newly visible runtime slots. The shared packet passed 166 cases with eight
  platform/privilege skips; strict Windows and Linux type checks passed.
- The unchanged Gateway build-context admission test found five missing cache
  modules in the Docker context whitelist. Added the exact closure; its full
  77-case module passed. Generated copies, source-size ceilings, focused lint
  and the install contract passed. Updated only changed source digests;
  preserved the 6,378-node inventory, coverage targets, effect restrictions and
  execution budgets. Required current-head CI and a fresh review remain gates.
- Preserved empty runtime-association stubs during automatic Windows leaf
  maintenance. Existing retired-leaf relief under a live shared prefix remains
  active. A normal-GC-then-catch-up regression proves that the remaining base
  bytecode can still be reclaimed.
- Bound multi-pass coverage to the latest complete namespace inventory.
  An unavailable inventory now retains an unknown unmeasured count rather than
  reporting an empty complete scan. Both explicit CLI paths persist successful
  apply observations through the common runtime-owner API.
- The corrected packets passed 157 shared-cache cases and 78 Worktrees cases,
  with eight platform/privilege skips. A separate native backlog fixture removed
  8,456,704 logical bytes in eight entry-limited passes over 3.36 seconds
  (about 2.52 MB/s). Its process/runtime proof universe is a fixture, not a
  real-host eligibility or performance claim.
- Source CI identified additional transitive release consumers and stale
  admitted unit inputs. The release intents and source closure were updated;
  the 6,373-node inventory, coverage targets, effect restrictions and execution
  budgets remain unchanged.
- Optional Windows-native unit-packet qualification stopped before collection
  on the existing bytecode-attribution observer's Windows path handling.
  No observer or budget was weakened. The required ordinary CI packet remains
  the publication gate. No real cache cleanup or rollout occurred.

### 2026-11-20 - Design accepted

- #13828 merged with current-head Copilot approval and required CI green.
- Source implementation and native qualification remain open. No live cleanup,
  candidate installation or fleet rollout occurred.

### 2026-11-21 - Qualification and delivery gates

- The focused shared packet passed 148 tests with eight platform/privilege
  skips. The Worktrees cache/status/doctor packet passed 77 tests. The prefix
  packet then passed 19 tests, including two additional native link controls.
  The runtime packet includes a fresh disposable
  virtual environment that ran the retained CLI through the native finite-worker
  owner, bound to that runtime's own SDK, and persisted its terminal observation.
- A real disposable Windows interpreter's arguments and environment prevented
  prefix removal. After its confirmed exit, the fixture's native prefix birth
  and filesystem operations permitted reclamation. Its process universe and
  runtime admission were scoped fixture controls, not evidence that a real
  host's full backlog is safe.
- Restart, replacement, fresh-content grace, native hard-link/reparse-point
  preservation, held-prefix fairness and byte/entry/time budget controls passed. Cached
  observations do not recursively scan the cache and do not claim a cached
  in-progress worker is still alive. Install-contract, source module-size and
  focused Python lint checks passed.
- A bounded, non-elevated, read-only source assessment measured 71,206,733 bytes
  across a partial prefix inventory. It reported zero eligible or removed bytes
  and `process-principal-unavailable`; most prefixes remained unmeasured in
  that pass. No runtime custody capable of mutation was acquired.
- The canonical Worktrees type-check environment cannot currently install its
  pinned `types-commonmark==0.9.2.20250330`: the dependency policy reports a
  publication timestamp inside its seven-day quarantine. That policy was not
  bypassed. The source-only strict checker iteration is separate evidence, not
  a substitute for the canonical or provider gate.
- Broader Windows diagnosis tests exposed unchanged fixture failures:
  `test_no_drift_when_consistent` supplies only a Linux registration;
  two pair-integrity cases expect a legacy controller relation that the current
  diagnosis does not report. Their failures do not authorize unrelated repairs
  or a required-check bypass. Cache-specific and other selected tests passed.
- Source publication, current-head review, required CI, release and authorized
  installation remain separate gates. There is still no host cleanup or
  rollout grant.

### 2026-11-20 - Baseline and proposal

- Claimed #12886 and captured the installed dry run without `--apply` or
  `--allow-age-only`. Remote measurement remains read-only.
- Existing whole-prefix refusal is deliberate. Replace the missing custody
  and drain strategy rather than deleting that refusal or raising one constant.
