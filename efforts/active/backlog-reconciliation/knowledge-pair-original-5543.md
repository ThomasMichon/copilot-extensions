# Original issue 5543 — description and discussion

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)

<!-- ADMITTED_PACKET_BYTES_BEGIN -->
Source: https://github.com/ThomasMichon/copilot-extensions/issues/5543

Historical record copy of the admitted description and complete discussion. Qualification metadata is omitted; source control bytes other than tabs/newlines are displayed as literal \xNN escapes, not repaired commands. Historical OPEN, intake and receipt wording does not countermand a later explicit adoption or supply new authority. Original issue content and source/resource custody are unchanged.

## Original description

## What happened

On a harness repo with a bound knowledge repo (`stateless: true` + `requires_external_state_root: true`), every new harness worktree eagerly gets a paired `<id>-k` knowledge sibling at launch, even when the session never writes personal state.

The standard sign-off (`push-changes`/`create-pr` → merge → `finalize` from inside the session) finalizes **only the harness half**. The `-k` sibling stays clean and `unused` and is never finalized. Then `cleanup --clean` holds the harness worktree indefinitely:

```
⚠️  Skipping <id>: content is on the default branch · held until BOTH paired worktrees finalized (sibling not yet finalized)
```

Default `cleanup --clean` in the knowledge repo also preserves the sibling because it is `unused`. Neither side ever converges without operator intervention. I observed this with 5 stranded harness worktrees on one machine. They were recovered only by running `-p <knowledge> cleanup --clean --include-unused`, followed by a harness `cleanup --clean`.

## Root cause

`prune.default_paired_sibling_final` returns `sibling.status == "finalized"`. Nothing in the normal flow ever sets an untouched sibling to `finalized`, so the BOTH-gate (#957) cannot be satisfied for the common "harness-only change" case.

## Suggested fix (either or both)

1. **Cascade on finalize:** when `finalize` runs on one half of a pair, also finalize the sibling when it is provably idle and empty: no commits beyond its base, no uncommitted or untracked changes, no live claims or follow-ups, and no live session. Its folder and branch are then removed under the same idle rules.
2. **Relax the cleanup gate:** treat a sibling that is clean, `empty`/`unused`, and has no live session or claims as satisfying the gate. This also heals pairs that are already stranded.

The pairing gate should still protect a sibling with real content (commits, dirty state, claims, or a live session).

## Related

- #957: introduced the paired BOTH-gate
- #1646: paired removal leaves branch refs / unknown sibling state (adjacent symptom)


## Reconciled acceptance from historical trackers

*The following contribution was assisted using Copilot.*

### Paired-removal branch and unknown-state acceptance transferred from #1646

The original body is only a heading, so retain the title's two symptoms without claiming a reproduced cause. This adds evidence/cleanup acceptance to the existing paired-lifecycle objective; it does not accept the rejected #5546 implementation or decide the unresolved BOTH-gate/unused-retention policy.

- [ ] Inspect and report both repositories' exact worktree registrations, checkout paths, branch refs and reciprocal tracking/pair identities before selecting a cleanup action.
- [ ] Preserve branch refs and retained state until exact owned, content-safe removal is proven. Report leftover refs, partial removal and retryable failure explicitly; never silently claim both halves removed.
- [ ] Treat missing/unreadable/foreign sibling records, failed registry/mux queries and unknown pair state as holds. A missing checkout alone is not an idle or acknowledged-reaped proof.
- [ ] Preserve uncommitted/untracked/unlanded companion content, live sessions, claims, follow-ups and registered ownership. Finalized does not itself mean stopped.
- [ ] Revalidate both relevant lifecycle identities under the existing ordered safety/locking boundary so concurrent resume, registration, claim creation or sibling changes cannot race removal.
- [ ] Keep existing BOTH-finalized and explicit-unused policies until a separately reviewed authoritative revision changes them; report-first reconciliation is not permission for force removal or inferred sibling finalization.
- [ ] Cover already-stranded pairs, branch-ref cleanup failures, one-half-missing records, unknown remote observations, concurrent changes and idempotent retry. Preserve exact partial outcomes for repair rather than fabricate a sibling's state.


Existing implementation owners, claims, holds and activation gates remain unchanged. This is tracker consolidation, not a completion or reassignment of the transferred work. Coordination: #7556.

<!-- backlog-reconciliation:7556:transfer:1646:to:5543 -->

## Paired finalization and recoverable removal cohort

*The following contribution was assisted using Copilot.*

I am adopting #6584's complete atomic OR resumable removal contract into this
OPEN paired-finalization parent. Empty-companion convergence and failed
removal recovery remain distinct unfinished workstreams. The inherited #1646
and #4094 acceptance is retained, not counted as a new implementation.

### Direct Plan

- [ ] PF1 (#5543) - Make harness-only paired lifecycles converge through a
  separately owner-reviewed safe cascade OR cleanup-policy revision, including
  already-stranded pairs. Until that revision is accepted, preserve BOTH-finalized
  removal and explicit-unused retention. Do not infer a sibling finalized or
  trade away the gate merely because an alternative is simpler.
- [ ] PF2 (#5543, #1646) - Inventory exact reciprocal registrations, checkout
  paths, branches, refs and pair identities in both repositories. Protect
  dirty/untracked/unlanded content, live sessions, claims, follow-ups and
  registered ownership. Finalized is not idle; absent checkout, failed mux/
  registry query, unreadable/foreign record or unknown remote state is a hold,
  not proof of disposal. Preserve the later unlanded-companion warning/block
  requirement and credit #8314 only for its delivered bounded paired-claim
  safety slice.
- [ ] PF3 (#6584) - Make eligible physical removal atomic OR resumable.
  Complete fetch/landing/HEAD and other required safety checks before
  destructive teardown; retain the original needed-locks-first OR
  linkage-last alternatives without mandating either transaction design.
  Git-lock contention must not silently strand a partially removed tree
  without a supported recovery path.
- [ ] PF4 (#6584) - Preserve accurate finalizing/incomplete/retryable custody
  after failure, including when the Git pointer/admin data is already gone.
  Keep the original active-state report and the follow-up finalizing-state
  correction distinct. Repeated lock, fetch-timeout and uninspectable-HEAD
  failures must remain diagnosable and recoverable through supported idempotent
  finalize OR repair, not an instruction to delete the directory manually.
- [ ] PF5 (#4094 via #6584) - For Windows locked/inaccessible residuals,
  report the actual failing operation/path/probe with bounded retry OR an
  exact actionable hint. Separate structural emptiness, readable-content
  proof and lock/accessibility proof. An inaccessible descendant is UNKNOWN,
  never disposable cache noise; do not infer a lock or permission cause
  without evidence.
- [ ] PF6 (#1646, #6584, #4094) - Revalidate both relevant lifecycle/pair
  identities, ownership, content, liveness, claims, refs and accessibility
  under existing ordered locking at the action boundary. Preserve residual
  branches, partial outcomes and the last authoritative recovery record
  until exact owned, content-safe removal is proven. Concurrent resume,
  registration or claim creation must not race a stale idle decision.
- [ ] PF7 (#6584, #4094) - Distinguish tracking-only retirement, verified
  complete physical absence and INCOMPLETE removal in JSON and human results.
  Keep ok/exit/warnings/removed semantics coherent; do not claim physical
  success from a successful intermediate stage or confuse a deliberately
  retained unmanaged checkout with failed or fully deleted managed content.
- [ ] PF8 - Preserve all incumbent source/resource/implementation and
  detailed-effort owners, assignments, claims, holds and activation gates.
  #8346's live-cwd-holder deferral/no-teardown and #6145's report-only
  documentation are partial credit, not full recovery. #2567's executor
  design and #4101/#2649's objective/runtime scopes remain independent.
  Stale/empty lock age or an absence of an observed Git process grants no
  lock-deletion authority. No elevation, ACL reset, holder kill, force
  removal, manual directory cleanup or new tidier is authorized.

### Direct Validation

- [ ] PFV1 - Qualify the selected owner-reviewed empty-companion convergence
  policy against untouched/stranded pairs AND real dirty, untracked and
  unlanded sibling content. Include finalized-but-live sessions, follow-ups,
  concurrent registration/claims, missing/unreadable/foreign/remote-unknown
  records and exact pair/ref changes. Retain the current BOTH/unused policy
  unless that accepted revision explicitly changes it.
- [ ] PFV2 - Cover shared-clone lock contention, fetch failures, missing HEAD/
  linkage/admin data, interrupted/one-half removal, branch-ref failure and
  repeat resume/repair. Prove atomic refusal OR supported idempotent recovery
  with accurate finalizing/incomplete state and continuing custody.
- [ ] PFV3 - Through the existing separately authorized bounded lane, qualify
  the actual Windows handle/access/inaccessible-descendant and partial-removal
  cases with exact diagnostics, fresh action-time proof and actionable hint.
  A source warning, cwd-holder test or other-platform lock simulation alone
  does not satisfy this retained consumer case.
- [ ] PFV4 - Verify truthful structured/human outcomes for complete physical
  absence, residual tree/lost linkage, supported retry and tracking-only
  retirement. Keep unknown/holder/dirty/landing/pair/claim refusals; preserve
  exact partial outcomes rather than fabricating a sibling's state.
- [ ] PFV5 - Before administrative supersession, read back the FULL current
  OPEN parent with every original/refined clause and continuing custody.
  Product work stays unchecked; tracker intake is not implementation,
  native qualification, deployment or release of any physical resource.

The original parent body and complete transferred child body follow unchanged
except for a generic repository example in the newly copied child text. This
intake performs no physical cleanup, lock/process probe, retry, registry
mutation, inferred finalization or source implementation.


### Preserved original scope from #6584 (sanitized examples)

## What

`agent-worktrees finalize` is not atomic: under git lock contention in a shared clone it can fail midway and leave a worktree **half-removed** — the worktree directory still exists with most of its files, but its `.git` pointer file and its admin dir under `<anchor>/.git/worktrees/<id>` are already gone, while the agent-worktrees registry still reports the worktree as active.

## Where

Linux host, project `<repository>` (one shared clone + ~90 linked worktrees, many concurrent agents doing fetch / commit-graph writes). Observed on 2026-10-13 while sweeping ~25 clean, idle worktrees (each first `git checkout --detach origin/main`, then plain `finalize`). 4 of 18 attempts ended half-removed; an earlier finalize sweep produced 1 more. Several other attempts first failed on a transient `commit-graph-chain.lock` in the shared clone and succeeded on retry.

## Current behavior

- `finalize` removes the git linkage (`.git` file + `.git/worktrees/<id>`) and part of the tree, then fails (apparently on a concurrent fetch / lock in the shared clone).
- Result: directory with ~26 remaining entries, no git linkage, registry still `active`.
- There is no automatic rollback or resume; re-running `finalize` can't operate on it because it is no longer a git worktree. The documented safe path (never delete a managed worktree by hand) leaves the operator with a repair-only state.

## Expected behavior

- `finalize` should be atomic or resumable: either acquire the needed git locks up front (retry/back off on `*.lock` contention before touching anything), or order the steps so the git linkage and registry entry are removed **last**, after the tree is gone.
- If it fails midway, the registry should record a `finalize-incomplete` state and a re-run of `finalize` (or `repair`) should complete the removal idempotently.

## Related

- #4094 (orphaned empty directory on Windows when a file lock is held) — same class (non-atomic removal), different trigger and platform.
- #5404 / #4801 (false 'unmerged work' after squash merges) — the reason this sweep needed `checkout --detach origin/main` first: items squash-merged into an integration branch that was itself squash-merged to main can never be proven landed by any ancestry/cherry/squash/blob check.


*The following contribution was assisted using Copilot.*

## Reconciled acceptance from historical trackers

### Direct Windows lock/access diagnostics and partial-removal custody from #4094

Append this COMPLETE numbered unchecked case to the CURRENT OPEN #6584 BODY without replacing its original atomic OR resumable contract, finalizing-state correction, lock-contention evidence, comments, existing owners or holds. Preserve the independent #2567 executor-design and #4101 objective/2649 runtime work. This is tracking coalescence, not a fixed removal engine or deletion permission.

#### Plan

- [ ] P1: Preserve the original Windows failed-finalize/removal outcome: detect and report residual locked/inaccessible directory with bounded retry OR an exact actionable operator hint. Record the actual failing filesystem operation/path/probe and distinguish unknown cause from a proven lock or permission cause.
- [ ] P2: Keep structural emptiness, readable-content proof and lock/accessibility proof SEPARATE. An inaccessible descendant is UNKNOWN, never disposable cache noise. Repeat relevant access/lock and fresh content/liveness/ownership proof at the action boundary.
- [ ] P3: Preserve a source-attributed retryable residual record after partial removal, including a directory remaining after Git linkage/admin data disappears. Keep current authoritative recovery/custody visible; do not retire its last record or report physical success merely because one stage succeeded.
- [ ] P4: Distinguish tracking-only retirement, verified COMPLETE physical absence and INCOMPLETE removal in structured/human results. Keep ok, exit, warnings and removed semantics coherent; prove absence before claiming physical removal. A deliberately retained unmanaged checkout is not a failed or fully deleted managed checkout.
- [ ] P5: Preserve the parent's atomic OR resumable alternatives and idempotent recovery, rather than prescribe one obsolete transaction mechanism. Corrected finalizing state, shared Git-lock and fetch/HEAD failures remain distinct evidence, not one assumed OS cause.
- [ ] P6: Credit #8346's actual live-cwd-holder deferral/no-teardown and #6145's documentation/report-only behavior without calling this full Windows/partial-removal case implemented. Preserve all responsibility/pair/content/claim/liveness and process-owner fences.
- [ ] P7: Keep report/approval scope and safe unknowns: no elevation, ACL reset, holder kill, force deletion, age-only lock clearing or new tidier. Exact lock recovery/remedy stays with its verified owning operation and authority; an old/empty lock is not permission by itself.

#### Validation

- [ ] V1: Qualify the original actual Windows handle/access/inaccessible-descendant and partial-removal cases through existing separately authorized bounded lanes; verify exact diagnostics, action-time rechecks and operator hint. A source warning, cwd-holder test or another platform's lock simulation alone is not this retained consumer proof.
- [ ] V2: Cover managed physical success, still-present partial tree/lost linkage, supported retry/resume and tracking-only retirement with truthful JSON/human outcomes and durable retryable custody. Preserve current holder/unknown, dirty/landing/pair/claim refusals; no incidental process or directory remedy.
- [ ] V3: BEFORE #4094 administrative supersession, verify eligible OPEN/unlocked #6584, materialize EVERY numbered original/refined clause directly in its body and read back the FULL CURRENT body/continuing custody. A related link, old guidance merge, a comment or this proposal is not receipt. Do not require unrelated whole-parent/all-host completion for this finite tracker transfer.

#### Continuing Coordination

- [ ] C1: Continue under SAME atomic/resumable-removal, Windows diagnostics, shared-clone/landing/permission/pair and current source/resource owners through #6584 and existing open bounded-source coordination. #2567 and #4101 retain their own runtime/doc scopes; no claimant expires or is reassigned.
- [ ] C2: Record complete direct intake separately from later implementation/native proof. Preserve every incumbent parent clause and hold, keep #6584 OPEN, and retain #4094 if any unique scope/custody cannot survive. This intake performs no cleanup, lock/process probe, retry, registry write or physical action.

Existing implementation owners, claims, holds and activation gates remain unchanged. This is tracker consolidation, not a completion or reassignment of the transferred work. Coordination: #7556.

<!-- backlog-reconciliation:7556:transfer:4094:to:6584 -->

## Complete discussion

### Comment 6028557369

An attempted fix in #5546 (closed) let default cleanup collect an empty `knowledge` companion once its harness sibling was finalized. Review found that unsafe:

- `finalize` leaves the harness session running, so being `finalized` alone doesn't mean the sibling is idle.
- Removal-time revalidation locks only the companion's record, so a harness session can register between the idle check and the reap.
- A missing checkout, or a timed-out or failed mux or registry check, can be misread as idle.
- The docs (`worktree-lifecycle.md`, `worktree` SKILL, `cleanup-details.md`) say unused worktrees need an explicit opt-in.

A possibly simpler direction: let the harness half be reaped first, since that path already does full liveness revalidation under its own lock. Then collect the empty companion only once the harness record carries `reaped_at`, which positively proves it was removed. That trades away part of #957's hold-both-halves rule.

### Comment 6178273205

Related case (agent-worktrees 1.24.61-dev1): after finalizing ~140 harness worktrees, their paired knowledge siblings stayed in the Picker (`pushed`/`orphaned`/`active`). Bulk-finalizing them found **12 with one or two unlanded commits** (per-task log files the agent had committed but never pushed). Nothing warned about this when the harness side was finalized, so that state would have been lost if the siblings had been force-removed. Suggest: when finalizing a harness worktree, also finalize its paired sibling, and if the sibling has unlanded commits, block (or at least warn loudly) instead of leaving it stranded.


### Comment 6330916202

*The following contribution was assisted using Copilot.*

Retain the paired-lifecycle convergence and inherited removal-safety checklist. The empty-companion case must converge through a separately reviewed safe policy, not by interpreting finalized as idle or unknown/missing as removed. The attempted relaxation was closed without merge. Preserve the original safe-cascade OR cleanup-policy alternatives and the later unlanded-companion warning/block concern, while keeping current BOTH-finalized and explicit-unused policy until its owner accepts a revision. No pair, branch, claim or retained recovery state is released here.

Evidence references:
- Read full #5543 body, both substantive comments and complete timeline. The current body directly contains the previously transferred #1646 checklist; that inherited acceptance remains part of this canonical issue rather than being silently dropped or transferred again.
- Actual provider read confirms #5546 is CLOSED with mergeCommit null. Its proposed empty-knowledge cleanup relaxation and reported tests are not a merged implementation receipt. The collaborator's review identifies live-finalized sessions, one-record-only locking, unknown-state misclassification and explicit-unused policy as unresolved safety constraints.
- Current prune.cleanup_disposition:415-434 still holds the pair unless the injected sibling-final probe is exactly true. default_paired_sibling_final:496-524 returns false for a nonfinalized sibling and unknown for absent/error state; it does not infer an untouched sibling finalized.
- cleanup_reap.py:14-48 reloads the current record under its required lock, checks checkout/branch/pair identity and responsibility before removal. That is real action-time protection, not proof of an accepted two-half idle/cascade policy or complete branch-ref cleanup.
- Preserve all seven inherited clauses: inventory both repositories' registrations/paths/refs/reciprocal identities; retain exact owned refs/state until safe removal and report partial failures; unknown/missing/unreadable/foreign/failed-query sibling state holds; protect dirty/untracked/unlanded content, sessions, claims and follow-ups; revalidate both relevant identities under ordered locking against concurrent changes; retain BOTH/unused policy until reviewed revision; cover stranded/one-half-missing/remote-unknown/ref-failure/concurrent/idempotent-retry outcomes without fabricated sibling state.
- The later comment adds real unlanded knowledge commits after harness finalization and asks for block or loud warning instead of silent stranding. Empty-companion optimization must not absorb that case into disposable-unused classification.
- Related worktree-conclusion-tidier is report-first and preserves ground lifecycle authority; it is not permission to bypass this policy crossroads. No new universal native rollout requirement is added.
- Inspected paired prune/reaper source matches dev 16a2499a542f58f2af32b3041e040a35daaf2f89 and released main a99a17ba8ccf890cf45a87107b6cd2ed259150a8. No cleanup, finalize, pair repair, branch deletion, process probe or test was executed.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/4ceff87a3af9f925d3589f5d9210208ce165a073). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-45:issue-5543 -->

### Comment 6372509711

*The following contribution was assisted using Copilot.* Taking the bounded finalization-safety slice: refuse harness finalization while its paired knowledge worktree has unfinished content, unresolved responsibility, or unverifiable state. Preserve the BOTH-finalized removal gate and explicit unused-retention policy; no inferred sibling finalization, automatic deletion, or empty-companion cleanup relaxation. I will add regression coverage and normal-path documentation, then report the reviewed result here. The wider empty-companion convergence objective remains open.

### Comment 6379576688

Paired-claim enforcement slice completed in ThomasMichon/copilot-extensions#8314 (merged as 3061104010a4c06da512a3cf1dfd9f823680dd03).

The source now journals ordinary paired-worktree obligations and prevents settling, releasing, erasing or transferring an unfinished pair's responsibility. Exact reciprocal proof and bounded lifecycle fences cover first stamps, retargets, ledger writes and physical cleanup. Failed repeated finalization restores mutable active responsibility rather than stranding the owner. BOTH-finalized removal, explicit unused retention, producer/handoff custody and live-holder protections remain intact; no sibling is inferred finalized.

Focused claim/rollback, real CLI/concurrency and holder regressions passed; the exact published head received Copilot approval and all required CI passed. Normal-path documentation is updated and the source worktree is finalized. No runtime rollout is claimed. This completes only the bounded finalization-safety slice, not the wider empty-companion convergence objective; no issue closure is being requested.

<!-- ADMITTED_PACKET_BYTES_END -->

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)
