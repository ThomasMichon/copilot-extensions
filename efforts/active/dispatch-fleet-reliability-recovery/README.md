# Dispatch fleet reliability and recovery

- **Slug:** `dispatch-fleet-reliability-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** Independent implementation worktrees; sequential merges.
- **Created:** 2026-10-12
- **Status:** Active
- **Vision:** agent-dispatch durable results, task/session attachment history and
  recoverable goals; plugin-services bounded work and single-owner lifecycle;
  registry-resolved configuration.
- **Umbrella issue:** #6466
- **Sub-issues:** #6467, #6470, #6471, #6793; coordinate #6185 and #6407.

## Guiding Intent

Keep dispatch work navigable without discarding audit evidence, bound supervision
at its real blocking boundaries, and recover terminal housekeeping separately
from completed business work. Repository-owned declarations identify registered
repositories and relative resources rather than duplicating machine-local roots.
Preserve the owning ground-layer, installer and configuration authorities.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Own the plan, integration, merge order and completion gate | Plan and integration worktree |
| Category implementer | Task metadata, producers and category-specific views | Dedicated implementation worktree for #6466 |
| Supervisor implementer | Bounded receipt reconciliation and progress recovery | Dedicated implementation worktree for #6467 |
| Registry implementer | Named repository/resource bindings and validation | Dedicated implementation worktree for #6470 |
| Source safety implementer | Trusted ownership snapshots and optional scoped GitHub authentication | `jittery-latch`, #6793 |
| Existing scalar-repair owner | Preserve ownership of #6407 and its existing #6421 PR | Existing PR and its current source worktree |

## Coordination

- **Topology:** Independent per-slice worktrees and PRs; merge serially and
  reconcile actual content conflicts before publication.
- **Host (owns PRs):** Coordinator; implementation participants may prepare
  their own slice PRs, but do not merge concurrently.
- **Delegates:** The three implementation slices above. No shared-checkout Git
  operations and no nested delegation without explicit authorization.
- **Handoff:** Return source changes, exact validation results, PR state and
  unresolved acceptance criteria. The coordinator retains the parent objective.
- **Existing ownership:** Do not supersede #6421. Feed relevant evidence to its
  owner and compose its released repair capability.
- **Lifecycle owners:** Compose #5301 and #5655; do not add a competing watchdog,
  cleanup daemon, reservation store or repository registry.

## Context

Recurring maintenance occurrences are ordinary tasks today. Newer rows can fill
query limits before ordinary work is rendered. Results, events and attachment
history are valuable and must remain intact; presentation separation is not a
retention policy.

The master supervisor resolves declarations and companion receipts before
creating its shared reconcile deadline. Receipt withdrawal can synchronously
validate and hash a runtime tree while holding a root-wide lock. Process-alive
checks therefore do not establish loop progress. This is distinct from the
inner-lane conclusion timeout in #6185.

Completed tasks can retain held terminal cleanup after capped retries.
Correcting the underlying serialization or timeout does not itself resume that
cleanup. Historical retirement flags must not be confused with active exclusive
fences or proof that a process remains alive.

Named repository references currently work inconsistently across declarations.
Evaluator argv has no explicit execution root, and several path fields interpret
repository names as relative directories. Merely replacing an absolute argument
with a relative string is not a safe portability fix.

Relevant existing plans are `supervised-lane-process-discipline`,
`versioned-singleton-manager`, `agent-dispatch-monitor-and-confirmed-state` and
`agent-dispatch-tasks-pane-ux-overhaul`. This effort owns the bounded repairs and
category delta, not their broader uncompleted campaigns.

## Request

Public-safe summary of the requested behavior:

- Separate recurring maintenance tasks into a category that does not clutter
  ordinary work, while retaining statistics, logs, results and agentic
  task/worktree/session audit associations. Defer pruning.
- Identify and fix supervision stalls with observable, bounded progress and
  lifecycle-owned recovery that preserves durable ownership.
- Repair malformed worktree serialization through its existing owner and harden
  terminal cleanup without restarting completed work or relaxing deletion gates.
- Make repository-owned registrations use registered repository names and
  relative resources. The global repository registry is the concrete checkout
  root authority; do not hardcode another machine's root.

Operational task recovery and deployment placement remain adopter-owned.
No private host, checkout, task or session identifiers are needed upstream.

### Bounded source-safety amendment

Public-safe summary of the subsequently authorized repair:

- Preserve ownership recorded by a trusted comment that the adapter updates in
  place. An absent predecessor comment or task on another coordinator is not
  evidence that the issue is unclaimed.
- Allow a source to explicitly select existing repository-scoped GitHub
  authentication, without switching global accounts, persisting credentials or
  exporting one account into the shared supervisor environment.

## Plan

### Phase 1 - Review intent and retain existing owners

- [x] Deduplicate category, supervisor and registered-resource scopes and claim
  #6466, #6467, #6470 and #6471.
- [x] Identify #6421 as the existing scalar writer/selected-repair implementation.
- [x] Review and merge this proposal before runtime implementation.
- [x] Reconcile category-specific presentation to the existing dispatch vision;
  land any needed standing-intent extension before realizing it.

### Phase 2 - Separate task categories without changing retention

- [x] Add an orthogonal category with compatible ordinary-work defaults and
  explicit all-category API/CLI retrieval.
- [x] Carry explicit category through task creation and schedule templates without
  altering labels, routing, sources, deduplication or managed-create replay.
- [x] Filter before SQL limits across list/search/sweep and delegated presentation.
- [x] Make ordinary Tasks request work and provide a separate Maintenance
  contribution with failures, progress, results and attachment access.
- [x] Provide preview-first, exact-provenance, audited historical classification;
  leave ambiguous rows unchanged and never infer maintenance from worker kind.
- [x] Preserve IDs, timestamps, statuses, results, logs, events and associations.

### Phase 3 - Bound supervision and preserve recoverable operations

- [ ] Establish the budget before declaration/companion resolution and isolate
  expensive receipt integrity work from the master reconcile thread.
- [ ] Keep one tracked operation per receipt and revalidate exact selection and
  generation under the owning lock before changing state.
- [ ] Reduce root-wide lock occupancy without weakening integrity validation.
- [ ] Expose cycle/phase/operation progress separately from process existence.
- [ ] Integrate an independent progress watchdog with the existing lifecycle owner;
  unknown outcomes retain authority and never authorize duplicate embodiment.
- [ ] Bound post-timeout pipe draining as well as initial subprocess execution.
- [ ] Compose #6185's conclusion boundary with terminal-cleanup recovery rather
  than replaying completed task work.

### Phase 4 - Resolve repository resources from one registry

- [ ] Define an explicit registered-repository binding for script resources,
  execution cwd and effort state-root identity.
- [ ] Resolve roots through the owning global registry at use time; reject missing,
  ambiguous or incompatible registrations without PATH/ancestor guessing.
- [ ] Separate local repository identity from forge/task-lane coordinates.
- [ ] Avoid persisting a second authoritative checkout root in repo discovery
  pointers or repository-owned declarations.
- [ ] Preserve standalone queue use and stage adopter migration deliberately;
  old peers must fail explicitly when required resolution support is unavailable.

### Phase 5 - Recover held cleanup through existing ground-layer gates

- [ ] Cooperate with #6421 through release; validate its selected repair interface
  without implementing a competing schema repair.
- [ ] Add preview-first, audited held-cleanup recovery fenced to the exact
  reservation/generation and current allocation ownership.
- [ ] Preserve prior failures, task completion, attempts, results and attachment
  history; reset only eligible housekeeping progress.
- [ ] Treat explicitly incomplete paired cleanup as retryable, not complete merely
  because an intermediate action is primed.
- [ ] Keep live, dirty, unlanded, open-PR, unfinished, held and ownership-ambiguous
  work protected by existing conclusion and cleanup authorities.

### Phase 6 - Preserve source ownership and scoped authentication

- [x] Review this bounded amendment before runtime implementation of #6793.
- [x] Accept the adapter's verified, standalone claimed snapshot after an
  in-place edit; preserve author, schema, occurrence and release-conflict
  validation. Do not infer relinquished ownership from a missing local task.
- [x] _(Agent-recommended implementation detail)_ Add explicit GitHub-only
  `forge.auth: repository`, retaining omitted/`ambient` standalone behavior.
  Resolve the optional agent-worktrees sibling through its existing invocation
  helper, select the repository account and obtain credentials through its
  existing command boundary, then run GitHub operations with a child-only
  environment. Refuse missing siblings, mismatches and credential fallback
  before mutation. Never log or persist token output.
- [x] Renew scoped credentials on demand; preserve immediate pre-mutation
  identity verification and avoid process-global environment changes.
- [x] Document the opt-in and unavailable-integration failures; add a changefile.
- [ ] Land through normal review/CI, and retain release/adoption as separate gates.
- [x] Leave task transfer (#6189), PR-ground-truth reconciliation (#2798),
  broader account-aware operations, and unrelated recovery owners unchanged.

## Validation Plan

- [ ] Request read-back preserves category-only scope, no pruning, audit retention,
  registry authority and existing schema-fix ownership.
- [ ] Old databases and omitted categories remain compatible; filters precede
  limits; list/search/sweep/local/remote views agree.
- [ ] Managed-create replay, results, events, attachment history and reservations
  survive category introduction and repeatable historical classification.
- [ ] Fault-inject slow hashing, root-lock contention and retained pipe handles;
  bound cycle latency and prove unrelated units continue progressing.
- [ ] Restart/lost-response tests preserve one operation and exact generation
  ownership; watchdog recovery does not duplicate active or cold workers.
- [ ] The same named-repository declaration resolves under two distinct registered
  roots; missing and ambiguous registrations fail explicitly.
- [ ] Scalar repair remains with its existing PR and is exercised after release.
- [ ] Held-cleanup retry never restarts business work or bypasses newer ownership.
- [ ] Partial pair races and interrupted progress remain resumable under exact
  pairing evidence rather than guessed ownership.
- [ ] Run targeted unit and actual CLI/RPC first-touch integration checks. Record
  clean-room/live-venue evidence or a concrete unavailability reason per slice.
- [ ] Add required changefiles, lint/install-contract gates and documentation-impact
  review; drive implementation PRs through merge and promotion.
- [ ] Authorized rollout has running-version and real consumer evidence; merge
  alone is not deployment.
- [x] Round-trip an actual provider comment edit through fresh discovery and
  selection, including a second empty coordinator; a trusted claimed snapshot
  remains excluded and is not released merely because its task is absent.
- [x] Keep malformed/untrusted markers rejected and conflicting releases from
  clearing a claim. Preserve ordinary reserved/claimed/released histories.
- [x] Exercise scoped account selection, renewal, unavailable sibling, mismatched
  account, failed/degraded token retrieval and child-environment isolation
  without exposing credential bytes. Include a real subprocess contract.
- [x] Run the same declaration's ambient mode without the optional sibling.
  Record native Windows results and available Linux CI separately from live
  adopter proof; a mock or source pass is not installed-consumer evidence.
- [ ] Resolve or explicitly transfer every plan/validation item before Done.

## Proposal

The initial proposal changes no runtime or deployment configuration. Category is
orthogonal to execution kind and routing. Registered resources resolve through an
optional adopted repository integration rather than introducing an unconditional
dependency for standalone dispatch. Housekeeping recovery cannot change business
completion or weaken allocation ownership.

_(Agent-recommended)_ Add explicit capability reporting for category filtering and
named-resource resolution so an older peer cannot silently accept but ignore a
requested contract.

## Journal

### 2026-10-16 - Bounded complete-suite CI sharding

- The approved source-safety repair's hosted dispatch job completed seven
  passing batches totaling 730.53 seconds of pytest time before the unchanged
  12-minute job deadline cancelled the remaining work. Its changed test files
  were in the completed sixth batch; no credential-test stall was established.
- The authorized fix-forward partitions the runner's authoritative sorted
  test-file selection deterministically across four CI smoke matrix jobs for
  dispatch only. Existing file validation, isolated fixtures, environment
  admission, process-tree containment and per-test/per-group limits remain.
  Empty shards never fall back to full-directory collection.
- Required `PR gate` still depends on the complete fail-fast-disabled smoke
  matrix; either shard's failure or cancellation remains blocking. Other
  plugins are single-job, while manual full and promotion full/coverage
  validation remain unsharded. This closes an execution-distribution gap,
  without changing standing intent or introducing selective coverage.
- Native runner/workflow/release-policy contracts pass 209 tests with one
  platform skip; the final focused shard portfolio passes 35, including a real
  contained pytest fixture and CLI forwarding. A real source manifest proves
  202 files partition into 101 + 101, with no omission or duplicate membership.
  Fatal lint, module ceilings and whitespace guards pass.
- No complete hosted shard run, new exact-head review, publication, merge,
  promotion or adoption is claimed by this local evidence. The new full-shard
  commands and supported manifest inspection are documented in TESTING.md.
- The initial two-shard native full run did not complete: shard 1 exhausted
  the unchanged 720-second aggregate budget during its third subsuite after
  two passing subsuites, with no observed assertion failure. This is not a
  passing full-suite result. The authorized measured refinement is four
  dispatch smoke shards, preserving the deadline and aggregate gate. The
  current 202-file source set partitions into 51, 51, 50 and 50 files;
  independent complete execution of all four remains required.
- Four-shard refinement validation: 32 count/completeness/matrix-gate contracts
  pass in a separate tooling interpreter; actual manifests have no omissions or
  duplicate memberships. A broader focused check passed 54 contracts but could
  not execute the unchanged real-fixture test because that interpreter lacks
  pytest-timeout. No dependency was installed and the managed dispatch venv
  was left unused for independent complete-shard validation.
- A targeted native diagnostic then isolated an assertion in the cutover
  fixture, reproducible with both candidate and pristine HEAD runners. Its
  deliberately aged passive-reap record was not current transition authority.
  The fixture now derives positive promotion proof from each actual published
  endpoint and verified process pair, checks increasing routing generations,
  and separately proves stale evidence is refused without changing the active
  route or process identities. Production ZDD and identity safeguards are
  unchanged. Both real native lifetime modes plus existing promoted-launcher
  and fixture PID/token guards pass: five tests in 100.85 seconds with the
  candidate runner and 124.09 seconds with the pristine HEAD runner.
- Separate bounded per-file timing, without a partition rerun, measured
  `test_supervisor.py` at 139.87 seconds (270 passed),
  `test_install_ps1_build_snapshot.py` at 29.77 seconds (14 passed, two skipped),
  `test_installer_engine_adoption.py` at 100.33 seconds (28 passed, nine skipped),
  and `test_task_worker_cli.py` at 99.83 seconds (55 passed). The latter's real
  HTTP-process case alone took 62.58 seconds. These observed costs total 369.80
  seconds before the separately measured native cutovers and remaining files;
  they establish accumulated work, not a claim that one repaired assertion
  explains the earlier 600-second limit. Complete-shard validation remains open.
- Post-repair complete native four-shard validation stopped at its first limit,
  without rerunning successful tests. Shard 1 covered all 51 files: 1,204 passed,
  six existing skips, and 295.26 seconds total wall time. Its three pytest
  subsuites reported 196.97, 55.01 and 12.23 seconds.
- Shard 2 reached the unchanged 600-second subsuite bound in its first group,
  returning runner exit 124 (outer exit 1), 610.17 seconds total wall time.
  The last completed case was the managed-companion real readiness/rollback/stop
  case; the next emitted case was `test_mcp_server.py::test_create_find_show`,
  which had no outcome before termination. No assertion failure was observed.
  Twenty-two files had been traversed, with 982 passing and 12 skipped reports,
  but there was no completed pytest subsuite summary. The remaining 29 files
  in that shard and both later 50-file shards remain unvalidated.
- Retained verbose timestamped logs show report-span lower bounds of 210.059
  seconds for managed companion, 132.810 for coordinator, and 100.225 for
  installer-engine adoption. These are not complete pytest file durations:
  interruption prevented the pending subsuite's duration summary. Exact
  completed-file lists, available duration sums and all remaining manifests
  are retained with the local validation logs. Native Windows-only lifetime
  cases remain legitimately inapplicable to hosted Linux; neither that platform
  distinction nor prior focused successes turn this native run into a pass.
  Hosted shard count remains four, and no budget, skip, production guard,
  publication, retry or rollout changed.
- Authorized native functional-coverage continuation reused the completed
  51-file first shard and derived disjoint positive `--test-file` batches for
  the second shard: managed companion (52 passed, one skipped, 190.65 seconds
  pytest / 198.38 wall), coordinator (139 passed, 122.44 / 129.18 seconds),
  installer-engine adoption (28 passed, nine skipped, 90.59 / 97.43 seconds),
  and the other 48 files. Their planned union is exactly the original 51-file
  shard, without overlap or omission.
- The remainder stopped on an actual assertion failure in its first subsuite:
  `test_companion.py::test_gated_launch_runs_only_after_receipt_and_stops_tree`
  found no `started.txt` after its existing five-second poll; the receipt
  existed. The completed failing subsuite reported one failed, 1,052 passed
  and 11 skipped in 389.32 seconds (399.90 seconds invocation wall time).
  The second subsuite and original shards 3 and 4 were not run. No assertion
  rewrite, retry, budget increase or further partition change was attempted.
- Only the reused first shard and three successful single-file batches count
  as green complete functional receipts: 54 of 202 files, 1,423 passed and
  16 existing skips. Earlier partial passing reports and the failed remainder
  do not establish complete native coverage. The original four-shard timing
  receipt remains failed/incomplete; fresh hosted Linux four-shard CI is still
  required separately. Logs, positive manifests, exact argv, failure trace and
  the full fixture patch are retained as local validation artifacts.
- The exact companion assertion received one focused diagnostic run per runner,
  with the original five-second observation and all existing containment limits.
  Candidate passed in 2.04 seconds and pristine HEAD under the same in-memory
  instrumentation passed in 1.42 seconds. Recorded receipts existed before
  gate release and matched the live creation token; the gate leader was alive
  before stop and exited afterward, and its receipt was removed. Captured child
  stdout/stderr were empty in both runs.
- These isolated passes do not explain or waive the earlier failed batch.
  The original failure retained no child receipt/stderr snapshot. The diagnostic
  marker probe mistakenly used the separate process cwd, not the fixture's
  plugin root, so its marker fields were excluded from conclusions; no valid
  marker-creation timestamp was captured. No extra diagnostic run or speculative
  production/test fix followed. No exact accepted-flake exception is known.
  Further fixture-versus-runtime action needs failure-context observability
  with the actual marker path and unchanged timing, rather than a timeout raise,
  expectation relaxation or retry-until-pass. Native coverage remains incomplete.
- The authorized diagnostic-only fixture now receives the actual marker object
  from the test and preserves the original five-second polling/assertion body.
  It captures receipt/gate events atomically, owned child stdout/stderr, PID
  creation tokens, descendant metadata and exceptions before teardown. It is
  explicitly opt-in; production companion code, containment, budgets and
  expectations are unchanged.
- Exactly one original 48-file remainder run under those diagnostics passed:
  1,053 passed and 11 skipped in 348.98 seconds, then 880 passed and one skipped
  in 351.04 seconds; 716.42 seconds invocation wall time under the unchanged
  720-second plugin bound. Reported files match all 48 positive manifest entries.
  The focused case itself took 3.97 seconds. No reproduction retry or later
  shard run followed.
- Valid evidence matches the marker parent to the receipt's declared plugin
  root, records the receipt before gate release, and observes the marker
  1.2475 seconds after release with content `started`. Receipt and live leader
  creation tokens match. Stop retired the leader and removed the receipt;
  child streams and exception lists are empty. Four descendant identities
  were recorded; one birth token remained queryable immediately after stop,
  which is not treated as a liveness/leak verdict without an exit-handle sample.
- The earlier contextual failure is still unexplained, not fixed or accepted
  as a flake. The passing diagnostic run adds observed functional execution but
  does not turn the earlier native four-shard timing failure into a pass or
  authorize merge. Shards 3 and 4 and fresh hosted Linux four-shard CI remain
  separate outstanding gates.
- Bounded static tracing confirms the fixture marker is absolute, independent
  of the separate process cwd. This unmanaged declaration has no provider,
  managed snapshot or health probe: launch synchronously records identity,
  atomically publishes the receipt, releases the byte pipe and performs one
  startup poll. The real gate/service interpreter bootstrap remains asynchronous
  relative to the fixture's five-second observation; no managed monitor is
  involved. No concrete unrestored mutation was found in the searched immediate
  test predecessors, which does not exclude every possible order-dependent effect.
- One specifically recorded stdio hypothesis received the sole controlled
  experiment: remove only the diagnostic file-stream overrides at the real
  Popen, restoring original inherited native stdout/stderr. The exact focused
  case passed in 6.20 seconds (5.97 call), with marker creation 2.4239 seconds
  after release and the original observation unchanged. This disproves a
  deterministic requirement for redirected streams, not an intermittent
  historical fault. CPython also explicitly preserves valid stdio handles
  alongside the gate handle whitelist. The unused dedicated stream files in
  this control are not evidence of empty child output.
- Read-only, birth-verified zero-wait handle sampling classified the current
  gate leader as matched-and-exited and its four recorded descendants as gone.
  No diagnostic process signal was sent. The earlier run's descendant birth
  tokens lacked exit-handle samples, so their historical liveness remains
  unknown; queryable creation identity is not a leak verdict.
- No causal fix is justified by these later passes. The earlier failure still
  cannot be classified as delayed bootstrap versus early child exit without
  failure-context execution/error evidence. One remaining decision is whether
  to preserve original-inherited-stdio diagnostics during the next required
  validation, not another speculative standalone retry or an exception. No
  portfolio run, budget/expectation change, publication or rollout followed.
- The next authorized diagnostics revision preserves original child
  stdin/stdout/stderr, argv, cwd and handle inheritance. It removes stream
  redirection and process inventory, records only the borrowed owned process
  handle's receipt-bound creation identity and zero-wait exit state, and adds
  an opt-in first-statement bootstrap breadcrumb without replacing the original
  marker or five-second assertion. It remains measurement, not a readiness or
  import workaround. Syntax, fatal lint and whitespace checks pass.
- Required original native shard 3 then passed all 50 files: 446 passed/nine
  skipped in 333.08 seconds and 665 passed/four skipped in 206.26 seconds,
  566.52 seconds total wall time. Original shard 4's first 25 files passed
  468 tests with ten skips in 154.93 seconds.
- Validation stopped at the first failure in shard 4's second subsuite:
  `test_repository_issue_source_safety.py::test_repository_probe_timeout_reaps_real_stalled_descendant[account]`
  asserted that the readiness file existed, but it was absent under the existing
  0.8-second probe timeout. The readiness prerequisite failed before any
  surviving-descendant assertion. The trace does not establish why readiness
  was absent and is not called a production leak or accepted flake. That
  subsuite reported one failed and 240 passed in 48.87 seconds; shard 4 returned
  exit 1 at 230.67 seconds wall time. No retry, budget or count change followed.
- The conditional original 48-file inherited-stdio context was not reached,
  so the revised companion diagnostics were not exercised in that case and
  the prior companion failure remains unexplained. All-202 native coverage,
  exact-four timing and fresh hosted Linux validation remain incomplete.
  The required-validation logs, remaining manifests and diagnostic review diff
  are retained locally; no publication or exception authority is claimed.
- Bounded stalled-probe analysis distinguishes policy from fixture input:
  production authentication commands retain 30-second timeouts; the regression
  imposes 0.8 seconds and then assumes the descendant became ready inside that
  interval. The real capture helper correctly charges spawn and identity-query
  work against its deadline. The old child stderr was deliberately discarded,
  so missing readiness alone cannot identify startup delay versus early exit.
- One causal experiment used the exact actor script and argv, observed real
  preparation separately, then handed the actual Popen object once to the
  unchanged capture/reaping path with timeout 0.8. Readiness was observed at
  0.734 seconds; identity-module import took 0.063 seconds separately. The
  sample did not cross 0.8 and does not demonstrate the historical failure's
  cause. Real timed capture returned no result, retired the known root, and
  stopped the real descendant heartbeat. No communicate/cleanup or identity
  was fabricated. This controlled in-memory fixture experiment is not a pass
  of the unmodified regression.
- No versioned probe fix followed an unproven cause. A remaining design decision
  is whether to explicitly separate real readiness/custody preparation from
  timed communication in the fixture, preserving 0.8 while pinning matching
  child identity and proving unknown/foreign owners cannot be signaled. That
  would be a fixture-contract correction, not a claimed production timeout fix;
  otherwise failure-context evidence is needed during a separately authorized
  necessary validation. No additional standalone attempt, full shard or retry
  occurred. Companion failure remains unresolved and its original-stdio
  48-file context remains unrun.
- The operator explicitly authorized fixture readiness/timed-capture separation,
  without accepting a historical-cause claim. A controlled old-fixture attempt
  with a real one-second child bootstrap delay failed its original readiness
  assertion with the actual 0.8-second argument. The child had not entered the
  delay before expiration in that attempt, so it is recorded only as the old
  structure's failure, not proof of why the historical failure occurred.
- The corrected test-only fixture prepares the real interpreter tree using
  existing suspended-spawn/Job custody on Windows or the existing POSIX session
  contract, waits for actual atomic descendant readiness, and pins native
  identity/membership before the timed call. A guarded spawn adapter hands
  over the same actual Popen object; real communicate and tree retirement are
  not replaced. Pinned child exit and heartbeat cessation are asserted before
  fixture custody closes and before the actor's finite watchdog can expire.
  Unknown/foreign identity and mismatched argv/cwd/environment requests refuse
  without releasing the timed phase or signaling foreign processes.
- The cold-bootstrap regression records an actual delay exceeding 0.8 before
  real readiness and timed communication. Account, credential, identity and
  forge-operation cases remain. Production authentication timeout stays 30s;
  only the intentionally stalled capture receives 0.8. Fixture preparation uses
  the existing ten-second readiness pattern inside the unchanged 30-second
  item default; an initial five-second preparation iteration failed and is
  retained, not called green. Actor active-watchdog, group 600s and plugin 720s
  limits remain unchanged. No skip or production implementation change was made.
- Further fixture validation exposed a mismatched pre-arm watchdog: preparation
  could wait ten seconds while the actor still ended preparation after five.
  Both now use the same ten-second readiness constant; active watchdog stays
  five. Parent-only dispatch/ZDD harness imports were also moved out of actor
  startup; actors use the existing lightweight native identity/publication
  helpers. Intermediate readiness/ownership failures are retained, not waived.
  Success is recorded only after error-free fixture teardown, and the regression
  explicitly observes the real communicate TimeoutExpired, not merely no output.
- Final native validation of the entire 43-test source-safety file plus directly
  coupled capture/identity guards passes with both runners: candidate 48 passed,
  two existing POSIX skips in 39.16s; pristine HEAD runner executed in memory
  48 passed, two skips in 23.80s. Fatal lint, module ceilings and whitespace
  checks pass. Complete fixture patch and all RED/iteration/final logs are
  retained locally. No portfolio/shard rerun followed; companion failure is
  unresolved, original-stdio 48-file context is unrun, fresh hosted Linux four-
  shard CI/review and publication remain separate gates, and no exception or
  historical root-cause authority is claimed.
- Independent full-file validation is real counterevidence to those earlier
  focused successes: 42 passed and one failed in 53.19 seconds, identity
  parameter, because the pinned child's zero-wait result was nonzero before
  fixture custody closed. That run did not retain the exact native return,
  LastError or exit code, so it is not classified as a live escape, async
  termination race or invalid handle, and no flake exception is accepted.
- Failure-preserving fixture diagnostics now retain exact wait/error,
  handle PID/birth match, exit code, heartbeat, stop/custody state and remaining
  active-watchdog margin before the strict exit assertion. The same recorded
  zero-wait value must still be zero; no confirmation-wait fix or production
  change has been made.
- The one authorized focused identity experiment passed one test in 13.13
  seconds. Taskkill returned zero; matching pinned-child waits at taskkill
  return, assertion entry and after the original assertion were all zero,
  exit code one. Custody remained open and stop was absent. The first observed
  exit was 1.937 seconds after arm, 3.063 seconds before the earliest natural
  watchdog, with stable heartbeat metadata. No diagnostic signal or bounded
  confirmation wait was used. This establishes a valid exit in that sample,
  not an explanation or waiver of the independent failure.
- The independent failure remains blocking and its native state remains
  unclassified. Exact diagnostics are staged for a next operator-authorized
  necessary validation; this slice made no additional standalone attempt,
  portfolio run, timeout/expectation relaxation or publication. Companion
  failure stays unresolved and original-stdio 48-file context stays unrun.
- The operator supplied an independent observation-contract basis, not a
  historical-cause claim: Microsoft's TerminateProcess contract says external
  termination is asynchronous and callers requiring completed exit must wait
  on the process handle. Source verification confirms the Windows capture
  cleanup issues taskkill and waits only for its root Popen; it neither owns
  nor promises a waited descendant kernel object. POSIX explicitly waits for
  group disappearance and remains unchanged.
  Reference: https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess
- The authorized fixture-only correction permits one wait only after a known
  matching WAIT_TIMEOUT, on the same pinned handle, capped at 1,000ms and by
  the existing five-second active window with one full second reserved for
  heartbeat/diagnostics. WAIT_FAILED, unknown exit/identity, changed custody or
  requested stop remain fatal; a second pending result is fatal, with no loop.
  Confirmed identity/exit, open custody, absent stop and stable heartbeat are
  rechecked, and the final clock check remains strictly before the natural
  watchdog and before fixture Job close. No signal, PID lookup substitution,
  production change or deadline extension was added.
- Observation-policy tests first failed 14 cases before implementation; all 15
  final policy cases pass, including timeout-to-signaled, failed/unknown refusal,
  persistent pending/error, identity/custody changes, insufficient margin and
  rejection of an exit observed after the watchdog. These are pure policy
  states without PID/creation claims; the real root/descendant capture cases
  remain separate integration evidence.
- The complete 58-test source-safety file and coupled capture guards pass once
  per runner: candidate 63 passed/two existing POSIX skips in 34.99s, pristine
  HEAD runner in memory 63 passed/two skips in 33.02s. Each produced five actual
  preclose native receipts with matching identity, confirmed exit, open custody
  and no stop. After heartbeat/diagnostics the minimum natural-watchdog margin
  was 3.063s and 3.25s respectively. Native pending-confirmation was not observed
  in those successful samples; that branch has focused policy regression
  coverage, not a fabricated historical reproduction.
- This corrects the unsupported synchronous-observation assumption independently
  of the earlier failure, whose actual nonzero return remains unclassified.
  No exception authority, portfolio run or publication follows from these
  successes. Companion failure remains unresolved, original-stdio 48-file
  context remains unrun, and parent-owned complete validation/review/CI gates
  remain open.
- Independent native source-safety validation then passed all 58 tests in
  32.48s. TESTING.md now explicitly separates real readiness/custody from the
  forced 0.8-second timed capture and documents bounded same-handle exit
  confirmation before the existing watchdog; production authentication remains
  30 seconds. The four-shard aggregate wording now says any shard.
- The next required native shard 4 completed all 50 files: 1,069 passed and ten
  existing skips, with subsuites of 179.73s and 268.83s and 480.07s invocation
  wall time. This includes the corrected complete source-safety file. Original
  shard 1, shard 3 and the three heavy shard-2 single-file receipts were reused,
  not rerun.
- The exact original remaining 48-file selection then ran once with original
  child stdio/cwd/argv/gate semantics and failure-preserving diagnostics. Its
  first contained 25-file subsuite completed 1,053 passed and 11 existing skips
  in 480.26s; the original five-second gated-companion case passed there.
  The second subsuite reached the unchanged aggregate plugin deadline with
  234.594s remaining at admission, during the first worker-CLI case, after 732
  observed passes and no observed assertion failure. Inner exit was 124,
  wrapper exit was 1, and invocation wall time was 728.65s including cleanup.
  Execution stopped at that first cutoff; there was no retry.
- The authoritative planned union remains 202 files exactly once with no
  overlap or omission. Completed invocations cover 154 files; the additional
  completed 25-file subsuite makes 179 green files and 4,656 passed/50 skipped
  across valid completed summaries. The final 23-file subsuite is incomplete;
  its partial reports are not credited as green coverage. First-subsuite
  per-file duration sums include procutil 250.44s, queue 41.81s and the installer
  build snapshot 31.13s. Retained manifests, exact argv, timestamped logs,
  complete-file duration tables and preclose diagnostics preserve this failure.
- Native functional coverage is therefore still incomplete, not a native FOUR
  performance pass. The historical companion failure remains unexplained:
  this successful original-stdio observation is not a demonstrated fix or an
  accepted exception. Native Windows cutover/Job coverage is platform-skipped
  on Linux, so these timings cannot establish hosted Linux geometry. Hosted
  FOUR remains unchanged and still needs fresh CI and review before publication
  or merge. This fixture/documentation slice changes no production bootstrap;
  no new clean-room installer or external Linux proof is claimed. No budgets,
  skips, production guards, publication or rollout were changed.
- The authorized remaining 23-file positive batch was checked against the
  current 202-file discovery: it is disjoint from the 179 green files and
  completes the exact-once planned union. All changed fixture inputs, including
  the new real-probe helper, were verified tracked/staged without working-copy
  drift and their hashes retained with the invocation.
- Existing complete evidence for this same 23-file subsuite was 880 passed/one
  skip in 351.04s, with 343.07s summed case durations. That measured evidence
  supported one batch inside the unchanged 600-second subsuite bound; no new
  shard count, grouping, test exclusion or budget was introduced.
- The single new attempt nevertheless hit that 600-second subsuite cutoff:
  811 partial passes, no observed assertion failure, inner exit 124/wrapper
  exit 1, and 605.72s invocation wall time. The contained native cutover case
  passed; its production-lifetime parameter was active at the cutoff. The final
  worker-instance and session-guidance files had no completed case reports.
  The 811 partial passes are not credited, and there was no retry.
- The valid green receipt remains 179 files and 4,656 passed/50 skipped; the
  23-file batch remains incomplete. Exact argv, unchanged 720/600/default-30
  limits, snapshot-input hashes, full timestamped output, partial case states
  and the complete remaining manifest were retained. Native full coverage and
  exact FOUR performance are not passing. Historical companion cause remains
  unknown with no exception authority; source remains unpublished, and fresh
  hosted FOUR CI/review remain integration-owner gates. Only this journal was
  changed after the observation; no further test, implementation, publication
  or rollout was performed.
- A subsequent independent focused check reproduced the original companion
  failure with original stdio and the unchanged five-second assertion: receipt
  publication and release completed with matching root birth, but neither the
  script-entry breadcrumb nor started marker existed before stop. This is valid
  entry-unconfirmed failure evidence, not an accepted flake or a wrong-path
  diagnostic; absence does not exclude blocking in the first file open/write.
- One authorized opt-in test-only phase observation also failed: one failed/
  25 deselected in 6.64s. It preserved argv/cwd/stdio, handle options, gate
  protocol, containment and the five-second assertion. A private sitecustomize
  observer recorded child import/gate/spawn phases and a non-signaling stack
  dump; this intentionally adds diagnostic import/profile overhead, so its
  measured durations do not purport to be uninstrumented historical timings.
- In that experiment, package initialization took 4.078s, including the eager
  queue import closure. The four-second stack was inside package version
  resolution importing importlib.metadata/resources. The gate then read the
  released byte immediately, 4.500s after parent release, without a captured
  gate wait. Service Python reached its startup observer at 4.984s; script
  bootstrap appeared only after the failed five-second observation. Existing
  Job-member handles were sampled before stop with actual creation tokens and
  membership; pending zero-wait results were recorded as exit-unconfirmed,
  never inferred to be leaks or used to signal foreign processes.
- Specific upstream comparison at dev be1ab20e995845c78454b49b73b21de62d782867
  found the shared procutil changes add direct-Python selection and move daemon
  implementation behind a lazy boundary; the current gated launch does not use
  those new paths. No gate/package-bootstrap repair is present there.
- Crucially, existing open PR #6487 already contains this exact root repair:
  its _launch_gated uses the same stdlib-only companion_gate.py by absolute
  script path instead of `-m agent_dispatch.companion_gate`, avoiding package
  initialization without replacing the gate protocol. Its native gate test
  poisons package initialization and checks the command remains gated, runs
  after release and exits correctly on both platforms. That live owner owns
  this launch/native-gate slice; no production fix was copied or prepatched
  here. Integration coordination and constructive repro feedback remain with
  the primary coordinator.
- Complete phase logs, stack evidence, identity observations, exact invocation,
  the full diagnostic-only delta and the specific upstream/PR patches were
  retained. Fatal lint and embedded-observer syntax checks passed. No second
  diagnostic run, candidate fix validation, portfolio retry, publication or
  rollout followed. The 179-file green receipt remains historical baseline
  evidence; the 23-file batch remains incomplete. Integration of the existing
  repair/new native test requires fresh discovery and validation, not a claim
  that this baseline's 202 files prove the future integrated snapshot.
- Precision retained after that observation: service site initialization was
  recorded before the deadline, but the observer did not profile the service's
  first file open/write. Its absent breadcrumb therefore cannot distinguish
  not-yet-entered script from a pending first write. The captured gate read
  completed within timestamp resolution and the import overhead is measured,
  but neither proves every historical cause or that the existing owner's
  repair completely eliminates this five-second failure. The one experiment
  is consumed; no second run or production change was made to erase that
  uncertainty. The primary coordinator posted repro feedback on the existing
  PR, and the exact owner hunk/regression were verified at its published head.
- Authorized isolated validation then pinned owner PR #6487 head
  b0ebd1e93af5529771aadac12e034feeea715f31 and applied only its verified
  direct-script launch delta to an ignored disposable source snapshot. The
  delta's SHA256 is
  3c7f117ccdbec63b6566a050d5f3f5389498650021192e9f58761111936a815b.
  Its dependency-free native poison-package test was reconstructed from the
  pinned API patch and verified against Git blob
  400afc2f524f35cb77e23f7f9cddde04c2089912. No other owner production changes or
  unvalidated capture/reconciliation dependencies were imported.
- The existing runner's in-memory plugin-root/prepared-venv seams reused its
  original venv admission lease and unchanged real run_contained boundary.
  Snapshot-first import paths, module __file__/hash assertions and observed
  actual Popen argv proved execution used the patched snapshot, not a warmed
  original module. Only companion.py differed among production snapshot
  sources; tracked production hashes were unchanged before/after.
- The original focused companion case and the owner's native poison-package
  regression each ran once: two passed/25 deselected in 5.59s, 20.234s runner
  wall time. Original stdio/cwd, headless/handle options, real gate protocol
  and the five-second marker assertion were retained. No child sitecustomize/
  profile hook was enabled. Receipt and matching root birth preceded release;
  bootstrap/marker were absent before release, and marker completion was
  observed 2.432s after it.
- Additional read-only proof reused the existing native fixture helpers to
  pin five actual same-Job process handles with matching creation tokens.
  Real controller.stop ran unchanged; every pinned object signaled exit in
  0.078s within the existing 15-second stop budget and before outer runner
  teardown, not through natural actor expiry or a fixture cleanup signal.
  The owner's poisoned initializer never ran, and its contributed command
  remained gated until release. Plugin/subsuite/default-item limits remained
  720/600/30 seconds.
- This is Windows validation of the isolated owner entrypoint delta only,
  not full PR #6487/current-dev validation, Linux evidence or proof of every
  historical cause. Tracked production remains unrepaired/unpublished; the
  existing owner and primary coordinator retain landing/integration authority.
  The earlier native 179-green/23-incomplete baseline and failed outer-context
  receipts remain unchanged. Exact code/provenance/hashes, source assertions,
  actual argv, stop proof and logs were retained. The operator's subsequent
  lifecycle-priority freeze preserves the ignored snapshot and all unpublished
  work without cleanup/deletion. The bounded check is complete; source writes
  and test commands are quiesced for the primary coordinator. No portfolio,
  retry, production patch, publication or rollout followed.

### 2026-10-15 - Phase 6 review repairs

- PR #6800's two Medium findings reproduced before correction: a conflicting
  release discovered before a live claim hid that claim, and the scoped
  subprocess timeout left a real descendant running. A third regression
  confirmed the actual scoped forge operation also bypassed tree containment.
- Same-occurrence conflicting releases no longer defeat a claim in either
  comment order, including retained snapshots with a previous label.
  The fresh review's differing-label variant and a matching-task/different-label
  variant each failed before the follow-up correction. Only matching-task,
  matching-label releases retain precedence in both orders;
  author, schema, issue, label and occurrence admission remain unchanged.
- Scoped production commands now reuse the existing captured process-tree
  runner for account selection, token retrieval, identity verification and
  forge operations. Injected runner seams and ambient mode remain compatible.
  Child environments stay isolated and failures do not echo credential output.
- The four targeted native Windows consumer suites pass 320 tests, including
  real stalled descendants at each of those four command boundaries. Fatal
  lint, module ceilings, headless-launch, install-contract and whitespace gates
  pass. This repairs the reviewed Phase 6 behavior, not standing intent.
- Validation tiers: unit/adapter coverage and simple real-process integration
  passed. No install/bootstrap change requires a fresh-machine scenario.
  Hosted Linux evidence awaits this exact push; no separate live external
  venue or rollout is authorized for this source-only repair. macOS is not
  claimed as natively exercised. Review, merge, promotion and adoption remain
  separate pending gates.

### 2026-10-14 - Source-safety implementation and native contracts

- Proposal #6794 merged as `1fbc4ce34effc3a5d72f7534a97461cc24e42401`
  after exact-head Copilot approval with zero findings and passing CI.
- The marker reducer now accepts the trusted in-place snapshot, preserves
  newer occurrences and rejects conflicting same-occurrence releases.
  It moved into the existing marker-helper module to respect the shrink-only
  module ceiling; the prior import remains compatible.
- GitHub-only `forge.auth: repository` composes the existing optional sibling
  invocation/account boundary. Each operation captures a fresh credential only
  in memory, verifies the resulting child identity and refuses degraded
  retrieval before mutation. Ambient mode is unchanged.
- The focused native Windows portfolio passes 307 tests, including the real
  provider edit/read/selection round-trip against an empty coordinator and a
  real subprocess child-environment check. Fatal lint, module-size, headless
  launch and install-contract gates pass.
- Authorized read-only integration exercised the candidate provider against
  the installed optional credential owner without changing parent credentials.
  No source was copied into an installed runtime. Actual production mutation,
  worker outcomes and activation remain adopter-owned acceptance gates.
- No installation/bootstrap path changes, so a separate first-install clean-room
  run is not applicable; fresh missing-sibling and synthetic credential cases
  exercise the new opt-in boundary. Native Linux execution remains with
  required CI. Review, source merge, normal promotion and authorized deployment
  are still outstanding, as are the broader effort's unrelated objectives.

### 2026-10-14 - Bounded source-safety proposal

- #6793 records two demonstrated source blockers. In-place marker editing
  removes the predecessor that the current reducer requires for a claimed
  snapshot; the GitHub adapter also has no source-scoped authentication opt-in.
- This amendment closes existing dispatch ownership and agent-fabric
  account-aware-operation intent. It adds no coordinator, credential store,
  supervisor, transfer mechanism or mandatory sibling dependency.
- Implementation remains gated on this amendment's review and merge. Existing
  recovery, release and broader identity-contract ownership is unchanged.

### 2026-10-14 - Native category contract budget composition

- The native category fixture put four serial cold CLI calls, each permitted
  20 seconds, inside one 30-second item plus real HTTP/setup/teardown. That is
  a contract-composition mismatch, not evidence of the original timeout cause.
- A measured normal run recorded CLI durations of 3.81, 4.39, 4.23 and 4.20
  seconds and an 18.28-second item interval. A contained nine-second cold-start
  delay left completed CLI calls within 20 seconds but exhausted the old
  aggregate item during the third call. The real modules and HTTP remained
  unmocked; neither timeout budget changed.
- Separate function-scoped view items preserve every selector, pivot, result,
  progress, statistics and attachment/session/worktree assertion. Under the
  same controlled delay all four real CLI calls passed in 12.27, 18.77, 14.06
  and 14.74 seconds, with each item below 30 seconds.
- Original failed-integration process/socket evidence is absent and the cause
  remains unproven. Its tracking and diagnostic disposition remain open with
  the coordinator; this fix does not declare the unknown hang resolved or
  accept it as a flake. No production lifecycle, pool, schema or rollout change
  is part of this test-contract correction.

### 2026-10-13 - Category source completion and inherited cwd admission

- #6543 merged to dev as `e45220dc726b22c5aad147a23094796b582fe4b3`
  after clean exact-head review, required CI and complete thread resolution.
  Its standing-intent extension #6482 had already merged before realization;
  the Phase 1 checkbox was stale. Phase 2 implementation items are resolved,
  not their release or installed-consumer obligations.
- Shape-only expansion introduced a coupled provenance gap: an inherited
  relative script cwd could pass admission when its command was named, then
  lose its defining-repository context in the generated held-tick declaration.
  Six validator, expansion and real recipe-document regressions failed first.
- The existing relative-cwd refusal now runs before the shape-only return,
  without registry or execution-file lookup. Positive coverage preserves named
  inherited cwd and directly declared leaf-relative cwd.
- The affected resource, script-forge, issue-loop and registrar-context suites
  pass 296 tests. This pure admission correction changes no installer, runtime
  selection or provisioning contract. Fresh temporary recipe files exercise
  the document boundary; native CI remains required before merge.
- No historical classification, adopter installation, native watchdog custody
  or scalar repair is claimed complete.

### 2026-10-13 - Category standing-intent prerequisite

- Proposal #6472 and category vision revision #6482 are merged. The dispatch
  vision now states orthogonal category metadata, compatible defaults,
  category-selection-before-bounds and evidence-based historical classification;
  its Tasks-pane child names independent full-fidelity Maintenance presentation.
- Category implementation proceeds in its isolated slice. Serial merge, release,
  integration and the parent completion gate remain with the coordinator.

### 2026-10-12 - Generic proposal and ownership boundaries

- Read-only evidence identified category/filtering gaps, a whole-cycle deadline
  gap, synchronous retention validation and capped held-cleanup recovery gaps.
- Existing #6407/#6421 already owns multiline scalar preservation and selected
  legacy repair; cooperation replaces a competing implementation.
- Opened the distinct coordination issues above. No runtime implementation,
  retention change, service update or worktree removal is part of this proposal.

### 2026-10-13 - Reviewed proposal and implementation ownership

- Proposal #6472 merged after an actual owner-authored clean `COMMENTED`
  Copilot review and passing required CI. The full agentic review lane reported
  a runner-start timeout; the available Lite review reported zero findings.
- Began isolated category-intent reconciliation, bounded-supervisor and
  registered-resource slices. Each implementation uses its own source worktree;
  the coordinator retains serial merge and deployment obligations.
- Existing #6421 scalar-repair ownership and the lifecycle owners remain intact.
  Category realization remains gated on standing-intent reconciliation.

### 2026-10-13 - Registered-resource review corrections

- #6531's first implementation review identified separate root lookups across
  validation fields, pre-lease emitter resolution and recipe identity/document
  reads. Five failing regressions reproduced those boundaries under a moving
  registry before the fixes.
- One transient resolution context now binds each validation/invocation and
  recipe chain to cached roots and a shared lookup budget. A standby emitter
  performs no lookup before acquiring its lease. Preserved public recipe and
  declaration seams while extracting focused modules, without raising size
  ceilings.
- Finite metadata probes now hold native process-tree custody through captured
  pipe EOF. A real Windows test exercises a leader that exits while its
  descendant retains the pipes; timeout closes the non-breakaway Job and proves
  the descendant has exited. This is finite-command custody, not independent
  master-watchdog delivery.
- Validation includes 421 dispatch compatibility tests, 19 repository-owner/
  clone tests and 42 resource/capture tests after the finite-probe correction.
  Native headless, module-size and fatal-lint gates pass. Review, promotion,
  authorized rollout and actual adopter migration remain outstanding.

### 2026-10-13 - Registered-resource provenance and custody follow-through

- The next #6531 review exposed five remaining boundaries: derived recipe cwd
  lost its repository class, reference effort state was treated as execution,
  builtin loading/standby validation still resolved resources, the field
  provenance walk used different roots, and POSIX retirement could signal a
  reaped leader's reused PID.
- Added failing regression coverage before correction. Named recipe directories
  now survive relative nesting and delayed placeholders as named resources.
  The field walk and merge share roots; cached class metadata is checked per
  read/execution use. Builtin spec loading and standby ticks validate shape,
  deferring identity and filesystem resolution to the held tick.
- POSIX captured-pipe handling retains the original leader unreaped until group
  retirement. Kernel child custody fences signaling without an external identity
  query; reaped or unknown custody refuses cleanup. Pipe EOF alone does not
  prematurely terminate a still-running leader.
- The affected consumer suites pass 418 tests on Windows; three native POSIX
  tests await the existing Linux CI lane. Native Windows containment remains
  exercised. Fatal CI lint, module-size, headless and install-contract checks
  pass. This is still source validation, not release, adoption or the separate
  master-watchdog completion gate.

### 2026-10-13 - Same-root implicit owner preservation

- Review of #6531 identified implicit checkout-derived owners changing during
  identity-only pointer migration when the checkout and registered names differ.
  The new regression failed before correction; migration now materializes the
  existing effective owner without changing explicit owner overrides.
- Registrar/resource consumer coverage passes 137 tests on Windows with three
  native POSIX skips; fatal lint and the install contract pass. The preceding
  revision's native Linux agent-dispatch CI job passed its complete suite.
- Required CI remains blocked by the independently tracked Windows launch
  failure (#6266, owned by #6274) and a distinct MCP routing-publication failure.
  Existing owners have been contacted before any overlapping implementation.
  No merge, release, deployment, or recovery-record mutation is claimed.

### 2026-10-13 - MCP cutover CI readiness prerequisite

- Traced #6548 to the integration fixture accepting `active.json` existence.
  Routing intentionally writes conservative custody history before the new
  active route, so file existence does not prove control-endpoint publication.
- The fixture now waits within its original startup deadline for the exact
  spawned daemon's routing endpoint. Real cutover, predecessor exit, handle
  flip, and data-plane assertions remain unchanged; production routing and
  the separate #6533 orphan-watchdog investigation remain untouched.
- Windows cutover coverage passes 27 tests, with eight native POSIX skips;
  fatal lint passes. Native Linux CI must exercise the changed real-process
  test before merge. No installation behavior changes, so a fresh-machine
  installer scenario is not applicable. No admitted external Linux venue is
  available; the existing native Linux CI lane is the integration witness.
- The non-blocking platform documentation finding now distinguishes native
  Windows and Linux evidence from unverified macOS custody. Missing unreaped
  child primitives refuse before spawning; no unsafe fallback is promised.

### 2026-10-13 - Discovery class and strict metadata boundaries

- A registered read-only pointer lost its class when discovery expanded relative
  executable fields against a bare path. Discovery now checks the registered
  active-source class before expansion; reference and knowledge registrations
  remain usable for recipe/state reads, never execution. This chooses explicit
  source refusal rather than inventing path-based provenance after expansion.
- Strict owner resolution now rejects non-string remotes before returning the
  ready response. Empty or omitted string remotes and legacy non-strict readers
  retain their prior behavior.
- Three dispatch and three metadata regressions failed before the source fixes.
  Final consumer suites pass 144 dispatch tests and 20 owner tests, including
  cached read-only checks, inherited recipe/state reads and exact negative CLI
  responses with unchanged registry bytes. Fatal lint, module ceilings and the
  install contract pass.
- Corrected MCP native Linux CI and the Windows launch check passed on the
  preceding head. No unconditional retry or bypass was used; the tracked
  independent Windows launch investigation remains owned separately.

### 2026-10-13 - Registration validation remains pre-admission

- Review identified eager callers of emitter validation at registration and
  declaration loading. Nine validator/registration/declaration regressions
  reproduced registry probes before any schedule lease.
- Emitter validation now defaults to shape-only; held builtin ticks retain their
  execution-time validation and resource resolution. Context forwarding covers
  both shape-only and explicit resolution without changing cwd provenance.
- The affected registration, declaration, resource and emitter contracts pass
  273 tests; fatal lint and whitespace checks pass.

### 2026-10-13 - Direct resource grammar admission

- Twenty emitter/evaluator loader, validator, registry and declaration cases
  reproduced accepting invalid named-resource grammar before execution.
- A shared syntax-only helper now covers direct emitter argv/cwd/side-load,
  evaluator registry argv/cwd, inline evaluator registration and existing
  script-forge shape validation. No registry or filesystem lookup is added.
  Positive admission coverage preserves valid not-yet-local named resources.
- Final script/resource/registration consumer coverage passes 346 tests,
  including positive not-yet-local admission. Fatal lint, module ceilings,
  install-contract and whitespace checks pass.

### 2026-10-13 - High-level expansion defers execution resources

- Actual repository/effort loop expansion still called execution validation
  before lease admission. Nine regressions reproduced the eager probe through
  direct expansion, decoded-document expansion and trusted registrar discovery.
- Both expansion callers now use shape-only validation. Generated specs retain
  the original loop declaration, including named execution/state resources,
  script-forge context and deferred worker identity. Held execution remains the
  resource-resolution boundary.
- Adjacent loop, resource, declaration and registrar contracts pass 350 tests.
  This is source evidence, not release or deployment; adoption remains gated on
  the reviewed promoted contract and actual consumer readiness.

### 2026-10-16 - Focus PR6800 on publication and its own gates

- Preserved the complete bounded investigation in a local commit and backup
  before current-base integration. The publishable follow-up excludes the
  exploratory companion observers; the original companion assertions and
  five-second window remain unchanged.
- Retain complete-set CI sharding and the verified real-process fixture
  contract corrections. The prior native record remains 179 green files with
  23 incomplete; no aggregate timeout, partial pass, or later isolated pass
  becomes a full-suite success.
- The existing PR6487 owns the direct gate-entry repair. Its pinned entrypoint
  delta passed two isolated snapshot checks, not full-PR or current-dev
  validation. No production change from that PR was copied here, and its
  required checks and review remain independent gates.
- Continue with supported current-dev integration, the runner and affected
  source contracts, then this PR's fresh hosted CI and review. No merge
  bypass or runtime rollout is authorized by this publication work.

### 2026-10-16 - Current-dev integration and runner contract proof

- Rebased the owned PR onto current dev with recovery refs and local backups
  retained. Conflict resolution preserves upstream documentation and its
  plugin-relative, unlinked-file selector validation alongside the complete
  file-sharding contract.
- The current runner contracts pass 111 tests with two platform skips. The
  scoped-source, issue-loop and default GitHub-adapter contracts pass 292
  tests through the contained native runner.
- The real nested-pytest regression exposed an undeclared timeout-plugin
  dependency. Both CI contract jobs now install `pytest-timeout` explicitly,
  with a regression checking that declaration; no timeout option was removed.
- These are targeted current-base results, not a complete native portfolio
  claim. The rewritten PR still requires fresh hosted CI and a current review
  before merge.

### 2026-10-16 - Repair the fresh review and CI consumers

- Review4300767243 exposed discarded matching-release evidence in a
  three-snapshot fold. Reduction now retains both claims and releases by full
  ownership identity within the selected occurrence. A matching release closes
  only its owner, never another unreleased claim.
- The 138 added permutation cases cover unrelated, missing and null task IDs,
  two claims with one release, all matching releases, differing labels and
  older occurrences. The coupled selection passes 475 tests; independent full
  source-safety verification passes 196.
- Hosted runner contracts exposed an outdated exact dependency-command guard.
  Its expectation now includes the required timeout plugin without dropping
  the other installer requirements.
- Promotion qualification now derives every sharded smoke row from the runner's
  single literal policy without executing candidate code. Unknown policies or
  recipes fail closed, each required shard must have succeeded, and legacy
  unsharded inventory remains supported. The qualification suite passes 64
  tests; the combined affected CI contract bundle passes 294 with five platform
  skips and 22 exhaustive-lane deselections.
- These repairs require a new published head and fresh CI/review. No cancelled
  duplicate run, partial report, stale approval or baseline observation is a
  merge waiver; runtime rollout remains separately authorized.

### 2026-10-16 - Address current-head Medium review findings

- Each public GitHub provider operation now resolves one fresh scoped child
  environment and explicitly passes it through its subcommands. No provider or
  global token cache is introduced; nested operations cannot replace the
  caller's credentials, and each mutation retains live user/repository checks.
- Scoped command errors expose only an allowlisted command group and numeric
  exit code, never arguments, query/body content, stdout, stderr or credentials.
  Fifteen regressions failed before these changes and pass afterward; the
  complete source-safety/provider selection passes 404 tests through the
  contained runner. Independent targeted verification passes all 15 cases.
- Qualification locates its literal shard default only within the unique
  `_ci_matrix` function. Unrelated policy readers no longer break inventory,
  while missing or ambiguous matrix/default declarations still refuse
  qualification. All 69 qualification tests pass.
- The Low CLI-validation observation does not identify duplicate facts: early
  validation checks the explicit argument mode, while later validation checks
  the resolved target set after exclusions. Three added regressions prove that
  excluding the only target refuses file, manifest and shard requests before
  preparation. The relevant CLI selection passes 18 tests.
- Fatal lint and whitespace checks pass. These repairs still require a new
  published head, fresh review and hosted CI; no merge or rollout is claimed.

### 2026-10-16 - Close the late command-adapter review

- The scoped command adapter now explicitly forwards working directory as well
  as environment and timeout. Compatibility options must match the contained
  runner's fixed capture/headless contract; unknown or conflicting options
  fail before execution rather than being silently discarded.
- A real child verifies the forwarded working directory. Five rejection cases
  cover incompatible capture, text, check, process flags and input options.
  Complete contained source-safety/provider coverage passes 410 tests without
  changing timeouts or process-tree custody.
- CI matrix help now refers to runner policy instead of hard-coding a shard
  count. The actual CLI help and 15 relevant matrix/selection tests pass;
  affected Python lint and whitespace checks pass.
- The previous published head's hosted CI succeeded. That result does not
  validate these later edits: the next published head still needs fresh CI and
  a passing review before merge.
