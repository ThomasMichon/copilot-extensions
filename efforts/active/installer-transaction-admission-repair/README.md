# Installer Transaction Admission Repair

- **Slug:** `installer-transaction-admission-repair`
- **Repo:** copilot-extensions
- **Branch(es):** one corrective PR against `dev`
- **Created:** 2026-10-13
- **Status:** Active
- **Vision:** below-altitude correction to the existing exclusive install-transaction contract
- **Umbrella issue:** #6463
- **Parent effort:** [CI Reliability & Flakiness Telemetry](../ci-flakiness-telemetry-and-reliability/README.md), Phase 3a

## Guiding Intent

Repair the POSIX PID-fallback admission race in the four matching agent-mcp,
agent-containers, agent-vault and agent-ssh installers. Their transactions must
remain exclusive during fresh admission and stale-owner recovery; empty owner
reads never authorize deletion of another holder's lock.

This effort owns only that repair, its regression evidence, related contract
documentation, patch changefiles and review/landing/released-source proof.
Telemetry campaigns, native continuity acceptance, other lock families and
host deployment are separate objectives.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Correctness worker | Local repair, targeted evidence and documentation | Dedicated corrective worktree; no publication authority |
| Integration coordinator | Independent review, publication, required CI and release proof | Normal repository PR and promotion flow |

## Coordination

- **Topology:** one bounded corrective worktree/PR; #6463 is the public claim.
- **Host (owns PRs):** Integration coordinator.
- **Delegates:** Correctness worker owns local changes; no child agents.
- **Handoff:** validated local changes return to the coordinator before commit
  or publication. Reviewer-owned code remains unchanged during this binding-only
  correction.
- **Binding replacement:** explicitly replace the worker's broad Phase 3a
  binding with this canonical effort, participant `Correctness worker`, slice
  `Repair and validation`. The parent remains Active and retains all telemetry
  work; replacing the binding neither completes nor abandons the parent.
- **Completion gate:** all Plan and Validation Plan items must be resolved,
  including clean current-head review, actual required CI, merge and proof that
  the released source contains all four repairs. A completed local phase or an
  unrelated green run is not completion.
- **Rollout boundary:** no host installation, runtime update or deployment is
  authorized by this slice. Any adopter rollout remains with its standing,
  explicitly authorized maintenance owner/workflow; it is not silently
  undertaken or claimed complete here.

## Context

#6483 introduced the matching install-transaction blocks. Required CI exposed
the stale-recovery overlap; a controlled actual-script regression reproduced
`start start end end` after successful subprocess exits. A reaper-only mutex
does not prevent an acquirer from replacing an absent link between an empty
comparison and unlink.

The existing `.reap` primitive now guards both admission and stale cleanup.
Flock, positive timeout configuration, owner-checked EXIT cleanup and explicit
repair of a dead admission mutex are preserved. The normative behavior is in
[`docs/install-contract.md`](../../../docs/install-contract.md).

## Request

Public-safe continuing objective under the repository's required-CI repair
policy: fix the reproduced admission race across all four matching installers,
then independently review, publish, merge and prove the released source.
Keep this bounded repair separate from the broader telemetry objective and
native continuity acceptance; neither parent is completed by this repair.

The objective-binding correction changed documentation only while the
independent reviewer inspected unchanged code. Integration now owns commit,
review and publication. Host rollout remains outside this authorized slice.

The named-residual follow-up requires exclusive creation at the exact pathname,
without following destination directories, for both owner and admission paths.
Directory-shaped nodes must remain untouched. Check-then-create is insufficient
when the shared namespace can change; the primitive must preserve the documented
portable contract rather than silently requiring GNU-only flags.

The corrective worker's implementation scope is the same four protocols,
their tests, directly related docs and patch change intent. Preserve the earlier
overlap/stress evidence, dead-mutex fail-closed behavior, timeouts, traps and
flock. Real consumers must refuse directory-shaped nodes at both shared paths,
including a controlled late directory insertion, without altering malformed
nodes or outside sentinels. No worker Git publication, child agents, paid trials
or host changes are authorized; integration retains final review and publication.

## Plan

### Repair and validation

- [x] Reproduce the exact empty-owner overlap before changing the installer.
- [x] Repair all four matching POSIX admission blocks without widening installation policy.
- [x] Preserve flock, ordinary/stale admission, timeout, EXIT ownership and dead-mutex behavior.
- [x] Add deterministic and adverse regressions; run isolated real-script stress and fresh transactions.
- [x] Update the install contract and generate patch change intent for all four plugins.
- [x] Correct directory-shaped owner/admission nodes with atomic exact-path creation
  and validate the same four consumers without widening the task.

### Integration and released-source proof

- [x] Independently verify the empty-read correction and exact-path/type-safety residual.
- [ ] Obtain independent clean current-head review and resolve any blocking findings.
- [ ] Publish through the repository flow; confirm actual required CI passes for the repair head.
- [ ] Merge the repair into `dev` and record the merged result.
- [ ] Confirm promotion/release validation and compare the released four installer bodies
  with the reviewed repair, recording the release identity and source fingerprints.
- [ ] Resolve every Plan/Validation Plan item and mark this bounded effort Done;
  keep the broad parent Active for its remaining telemetry objectives.

## Validation Plan

- [x] Before-fix actual-script regression fails with overlapping starts and successful children.
- [x] After-fix suites cover deterministic overlap, fresh/stale contention, malformed owners,
  dead admission mutexes, timeouts, release failures and EXIT ownership.
- [x] Four final targeted suites: 128 passed; 12 unchanged PowerShell behavior tests skipped
  because the isolated Linux image has no PowerShell.
- [x] Real-script stress: 24 six-child fresh/stale schedules, 144 serialized transactions.
- [x] Ruff F/E9, four shell syntax checks, install contract (13 plugins), module guard
  and diff whitespace checks pass; no module baseline widened.
- [x] Named residual: directories, directory symlinks and numeric directory symlinks
  at both paths are refused, preserved and never populated with child locks.
- [x] Controlled creation-race and typed-receipt snapshot/FIFO tests pass; prior
  overlap, ordinary/stale/flock, timeout, exit-ownership and stress evidence is revalidated.
- [ ] Independent coordinator validation and required CI confirm the final reviewed repair head.
- [ ] Released-source proof confirms every repaired installer, not merely an unrelated green run.

## Proposal

This records an already-authorized bounded correctness slice, not a new
architecture or policy campaign. Review and publication remain coordinator-owned.
The documentation/binding correction itself leaves code, tests and change intent
unchanged.

## Journal

### 2026-10-13 - Independent local verification closed the named residual

- The coordinator reran all four final transaction-lock suites against unchanged
  source in a hardened, network-disabled Linux container: 128 passed and 12
  unchanged PowerShell skips. Install-contract and effort-structure gates pass.
- Independent actual consumers closed the directory-shaped creation residual
  with nine targeted checks: exact-path directory/symlink refusal, preserved
  nodes, late-directory and FIFO races, typed regular receipt lifecycle, legacy
  dangling-link compatibility, foreign-owner cleanup and dead-mutex refusal.
  The reviewer removed its owned container and found no residual in this scope.
- Provider review, required CI, merge and released-source proof remain open.
  This local verification neither completes the parent telemetry effort nor
  authorizes native continuity trials or host rollout.

### 2026-10-13 - Named directory-shaped admission residual corrected locally

- Before correction, all eight focused agent-mcp shape/race cases failed.
  Directory-shaped owner cases logged `start start end end` after both real
  Bash consumers returned zero; directory-shaped admission paths also acquired
  incorrectly or received unowned child links. A late directory inserted at
  the actual creation boundary reproduced the same class.
- All four matching blocks now publish typed regular PID receipts with the
  standard exact-path `link(1)` utility and privately staged source files.
  There is no shared-path `ln -s`, GNU-only `-T`, new runtime library or
  interpreter. Regular source receipts avoid macOS/BSD/Linux differences in
  linking a symbolic-link source. Legacy dangling PID symlinks remain readable;
  directory symlinks and malformed records are refused without deletion.
- Owner-file reads take private hard-link snapshots and inspect the captured
  node before opening it. The raced-FIFO regression returns promptly and leaves
  the malformed source untouched.
- The four complete targeted modules now report **128 passed, 12 unchanged
  PowerShell skips**. This includes two real consumers for each of the three
  directory shapes at both shared paths, exact-boundary insertion tests, and
  the prior empty-read overlap, ordinary/stale/flock, timeout, trap ownership,
  dead-mutex and release-failure contracts. Node identity, directory contents
  and outside sentinels remain unchanged in every malformed-path case.
- Repeated real-script stress on the corrected bodies passed again:
  **24 fresh/stale schedules and 144 serialized transactions**, with the
  transaction roots empty afterward, including temporary snapshot cleanup.
- Ruff F/E9, shell syntax, the 13-plugin install-contract check, module guard
  and diff whitespace checks pass. The existing four-plugin patch change
  intent and normative install-contract documentation describe the correction.
  Tests ran in bounded, network-disabled, read-only-source Linux containers;
  no host, image or deployment change occurred. Native macOS/BSD execution
  was unavailable and is not claimed; its documented POSIX primitive and
  regular-receipt requirements are recorded in the contract.
- The bounded effort remains **Active**. Independent named-residual verification,
  publication, actual required CI, merge and released-source proof are still
  integration-owned and unresolved. The broad telemetry parent was not changed
  or declared complete.

### 2026-10-13 - Local repair validated; bounded completion contract established

- The deterministic `test_posix_pid_fallback_empty_owner_interval_never_overlaps`
  failed before the source repair with `start start end end`, after its
  subprocess zero-exit checks. It passes for all four repaired scripts.
- Targeted pytest used real Bash in disposable, network-disabled Linux
  containers with read-only source/root and bounded process/memory/time
  resources. The four complete transaction-lock modules produced 72 passes and
  12 PowerShell skips. Independent stress reused `_sh_contender` on three fresh
  and three stale roots per plugin: 24 schedules and 144 successful transactions.
- **Unit/static:** targeted regressions, structural contracts and source gates.
  **Simple integration:** actual Bash children, controlled scheduling, held-lock
  stamp refusal and exit cleanup. **Clean-room:** fresh temporary transaction
  roots prove first-touch admission, not a complete runtime installation.
  Full provisioning was skipped because dependency/service lifecycle behavior
  is unchanged and outside this correction. **Live external venue:** no remote
  host or CodeSpace was authorized; the real Linux kernel lane was exercised
  locally. No host activation or image change occurred.
- Detailed evidence was moved from the parent's Phase 3a journal to this
  canonical bounded objective; the parent retains a truthful summary and link.
  The replacement is explicit and does not declare the parent Done.
- Local changes remain uncommitted/unpublished. Review, actual required CI,
  landing and released-source proof are outstanding. Host rollout remains
  unauthorized here and outside this effort's completion claim.
