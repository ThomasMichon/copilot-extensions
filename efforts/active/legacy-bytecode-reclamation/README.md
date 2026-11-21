# Legacy Bytecode Reclamation

## Goal

Public-safe operator request, verbatim:

> safe automatic reclaim of legacy unsealed and retired installed bytecode prefixes on Windows (bytecode-only, regenerable, not referenced by live processes or selected/retained runtimes), budgets sized to drain a backlog in a bounded time, and loud surfacing in update/status/doctor with the exact command.

- **Slug:** `legacy-bytecode-reclamation`
- **Repo:** copilot-extensions
- **Branch(es):** dependency-ordered PRs against `dev`
- **Created:** 2026-11-20
- **Status:** Draft
- **Vision:** [regenerable bytecode reclamation](../../../visions/installer/README.md#regenerable-bytecode-reclamation) and [authoritative replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #12886
- **Parent:** [Derived Disk Hygiene](../derived-disk-hygiene/README.md), bytecode follow-on

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
- [ ] Reconcile and land this proposal through Copilot review and required CI.

### Phase 2 - Qualify whole-prefix custody

- [ ] Implement native Windows cache-reference observation for the installation
  owner, including environment and interpreter-argument references, stable
  process identity, opaque-process outcomes and bounded native operations.
- [ ] Combine reference proof with existing construction/selection/retention
  custody; preserve selected, rollback, retained and live runtime consumers.
- [ ] Admit only recognized, old, bytecode-only, non-linked namespace trees.
  Keep an explicit hold when any required proof is missing.
- [ ] Prove the race boundary and restart-safe progress with actual Windows
  disposable consumers before enabling automatic whole-prefix effects.

### Phase 3 - Deliver drain work and reporting

- [ ] Reuse one planner for explicit dry run/apply and automatic maintenance,
  with installed-prefix support rather than a second destructive implementation.
- [ ] Add bounded, resumable catch-up work and an aggregate drain deadline.
  Use the existing native process/maintenance owner, not an unowned shell or
  new permanent reaper service.
- [ ] Surface measured, eligible, held and unmeasured bytes, reasons, progress,
  observation age and the exact command in update/status/doctor.
- [ ] Keep ordinary status reads cheap and update selection independent of
  maintenance failures, pending work or a large backlog.

### Phase 4 - Validate and deliver

- [ ] Pass targeted unit, native Windows, collector/installer integration,
  type/lint, generation and install-contract gates.
- [ ] Repeat read-only real-host assessment with the qualified implementation;
  report actual eligible bytes separately from age-only or unmeasured estimates.
- [ ] Steward implementation PRs through current-head Copilot review, green
  required checks and merge; report release and rollout separately.
- [ ] Resolve every phase/validation item, reconcile the parent and archive
  before releasing the effort and finalizing its source workspace.

## Validation Plan

- [ ] Dry run never mutates cache contents; summary counts held bytes by reason.
- [ ] Selected/retained/rollback/construction and live-prefix references refuse.
- [ ] Unknown owner, process access failure, PID reuse, new references, changed
  namespace identity and concurrent writers retain work without false success.
- [ ] Reparse points, symlinks, hard links and non-bytecode evidence survive.
- [ ] Unsealed and retired installed prefixes can actually empty in native
  Windows fixtures; proof is not limited to mocked source-qualified leaves.
- [ ] Large-volume fixtures cross multiple byte/time/entry budgets and restart
  boundaries with fair progress and a measured aggregate drain rate.
- [ ] Catch-up ownership survives the admitted caller boundary, or records a
  truthful interrupted result; cancellation and unknown effects are not replayed.
- [ ] Metadata/status work does not recursively rescan a multi-million-file
  cache on normal reads; incomplete measurements never become zero-byte claims.
- [ ] Existing new-cache selection, POSIX protection, automatic-update policy,
  incoming installation readiness and service/session custody remain intact.
- [ ] No real cache deletion, installed rollout or relief on a remote host is
  inferred from source tests, merge or release.

## Proposal

See [design.md](design.md). Its gates deliberately do not promise eligible
bytes until the installed ownership and live-reference proof passes.

## Journal

### 2026-11-20 - Baseline and proposal

- Claimed #12886 and captured the installed dry run without `--apply` or
  `--allow-age-only`. Remote measurement remains read-only.
- Existing whole-prefix refusal is deliberate. Replace the missing custody
  and drain strategy rather than deleting that refusal or raising one constant.
