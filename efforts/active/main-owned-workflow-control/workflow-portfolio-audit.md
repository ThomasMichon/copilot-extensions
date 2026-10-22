# Workflow portfolio audit and consolidation roadmap

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Standing intent: [workflow orchestration vision](../../../visions/main-owned-workflow-control/README.md),
[test portfolio](../../../visions/test-portfolio/README.md) and
[release pipeline](../../../visions/release-pipeline/README.md).

## Authority and evidence boundary

This is the shared #6237 audit and execution map, not a competing effort.
The operator appointed its campaign driver to take point. Existing #3336
release/controller, #7122 runner integration, #1303 test portfolio and
#6142/remediation owners retain their implementation and admission scopes.
The proposed waves below are **agent-recommended**. They require ordinary
review, accepted slice ownership and their stated proof before activation.

This amendment changes no workflow, required check, label, variable, credential,
release pause or runner configuration. It neither preempts an open contributor
PR nor licenses a new publisher, bypass, controller or capacity allocation.
The existing Maintainer stewardship proposal and release Phase 9 retain their
separate gates; they are not made active by this coordination plan.

## Pinned inventory

Captured October 18, 2026:

- Dev: `3bc02bce69f98bd7aee4234d9aa076f59c6b22b1`.
- Main: `ff6773de1b539f5227b98634be13fcaaa75adac7`.
- Both trees contain **21 workflow YAMLs**, plus the agentic workflow Markdown
  source: **22 physical declarations**, not 22 executable workflows.
- Dev has **84 literal job definitions**; Main has **81**. These are not matrix
  cells, allocated jobs, check records or execution counts.
- Only `base-branch-reminder.yml`, `ci.yml`, `validate-and-promote.yml` and
  `workflow-lockdown-guard.yml` differ between these pinned workflow trees.
  Dev source must not be described as the active preserved Main controller.

The table covers every YAML in `.github/workflows/`. Event abbreviations are
PR = `pull_request`, PRT = `pull_request_target`, WR = `workflow_run`,
RD = `repository_dispatch`, manual = `workflow_dispatch`.

| Workflow | Events | Literal jobs Dev/Main | Contract / retained owner |
|---|---|---:|---|
| `agent-dispatch-pwsh-race-exhaustive.yml` | manual, weekly | 2/2 | Linux and Windows exhaustive race coverage; Dispatch/component owner |
| `base-branch-reminder.yml` | PRT to Main | 3/3 | Main admission, configured identifier scan and feedback; #6237 |
| `ci-failure-fix-attempt.lock.yml` | issues, manual | 7/7 | Generated bounded repair workflow; remediation owner |
| `ci.yml` | PR except Main, Dev/Main push, manual | 28/26 | Selected contributor gate, source/platform/artifact validation; CI/controller and portfolio owners |
| `context-handoff-exhaustive.yml` | manual, weekly | 1/1 | Exhaustive continuity contracts; component owner |
| `copilot-code-review.yml` | manual setup definition | 1/1 | Native hosted review setup, not the managed review service itself; #7122/#6237 |
| `crash-diagnostics-stress.yml` | manual, daily | 2/2 | Linux/Windows crash and restart diagnostics; component owner |
| `installation-context-acceptance.yml` | manual | 2/2 | Partitioned native POSIX/Windows acceptance; installation owner |
| `installation-context-full.yml` | manual, daily | 1/1 | Exhaustive installation-context reference/adapter matrix; installation owner |
| `module-health-watchdog.yml` | manual, daily | 1/1 | Detection and issue filing, not validation or repair authority; health owner |
| `payload-cwd-contract.yml` | PR, manual, weekly | 2/2 | Linux/Windows full payload-CWD contracts; component owner |
| `pr-rebase-full.yml` | manual | 2/2 | Linux/Windows replay and recovery suite; Worktrees owner |
| `promote-trigger.yml` | WR for completed CI | 3/3 | Dev advancement, baseline refresh and trusted PR identifier reporting; release/#6237 |
| `purge-consumed-changefiles.yml` | RD, daily | 1/1 | Consumed-only housekeeping through its guarded PR path; release owner |
| `source-attribution-departures.yml` | PRT synchronize/closed/edited, all bases | 1/1 | Refresh other PRs affected by departing/shared heads; attribution owner |
| `stale-branch-sweep.yml` | manual, weekly | 1/1 | Guarded branch/issue maintenance; maintenance owner |
| `trusted-ci.yml` | PRT, path-filtered | 2/2 | Opt-in trusted authorization and legacy bridge runner route; #7122 |
| `validate-and-promote.yml` | RD, manual | 16/15 | Full source/candidate evidence, composition, publication and merge; #3336 controller |
| `windows-coverage-rotation.yml` | manual, daily | 3/3 | Selection, native Windows breadth and failure reporting; portfolio/component owners |
| `workflow-lockdown-guard.yml` | Dev PRT, Main push, manual | 4/4 | Trusted admission, review request, source attribution and refresh; #6237 |
| `zdd-singleton-stress.yml` | manual, daily | 1/1 | Singleton/process-lifecycle stress; component owner |

`ci-failure-fix-attempt.md` is the additional source declaration. Its generated
`.lock.yml` is not a second independently maintained implementation: changes
must use the source and its compiler.

There are ten scheduled workflow declarations: seven test/deep/platform lanes
and three health/maintenance lanes. Aligning cron times alone removes no
invocations and may worsen bursts. Native acceptance, exhaustive installation,
race, crash, singleton and Windows rotation contracts are not interchangeable.

The live workflow registry separately returned 26 records: 25 active and one
manually disabled. Three are dynamic Copilot/Dependabot services, not checked-in
workflow YAML. Two retained Darwin-custody paths are absent from pinned Main;
registry presence alone does not prove a currently executable trigger or
authorize deleting history.

### Reproduction and source limits

Enumerate pinned trees with `git ls-tree -r <sha> -- .github/workflows`, compare
their blob identities, and parse top-level event/job declarations rather than
counting a matrix as one execution. The audit used three disjoint read-only
evidence tracks, then mechanically checked the complete roster, event filters,
Main/Dev differences and job totals.

Review/admission sources include the Main reminder's admission/scan boundary,
Dev lockdown's separate authorization and source-attribution checks, and
`trusted-ci.yml`'s same-repository author/sender permission checks. Release
sources include CI discovery/aggregate selection and the controller's separate
source and materialized-candidate jobs. `TESTING.md` documents the distinct
native/deep/installation contracts.

Helper/action/recipe closure is **not complete**. Similar command names,
repeated ancestry checks or two `full` jobs are candidates for analysis, not
proof of interchangeable assurance. Receiver-side ancestry validation remains
necessary even if a sender already checked it. Package attestation is not
test-suite attestation.

## Operating baseline, not claimed savings

The Actions runs API was fully paginated for
`2026-10-18T00:00:00Z..2026-10-18T23:40:20Z`. Deduplicated run IDs matched the
reported **942** total, below the provider's 1,000-result query cap.

| Workflow cohort | Runs | Important qualification |
|---|---:|---|
| Promote trigger | 196 | 149 success, 47 skipped; owns more than Dev dispatch |
| CI, including one separately titled promotion invocation | 186 | Different events, source/composition and terminal states |
| Refresh departed PR source heads | 169 | 129 success, 40 skipped; shared-head and departure semantics |
| Workflow lockdown guard | 146 | Dev PR events plus eight Main pushes |
| Managed Code Review | 108 | Dynamic service runs, not setup definitions or runner assignments |
| Trusted self-hosted CI | 75 | All 75 skipped; not 75 self-hosted executions |
| Validate and promote | 40 | 34 success, three failure, three cancelled; not 40 completed releases |
| Base branch reminder | 10 | Main admission/scan/feedback, not ordinary Dev CI |
| Other cohorts | 12 | Distinct manual, scheduled and setup invocations |

There were 682 successful, 58 failed, 36 cancelled, 162 skipped, two
action-required and two nonterminal runs at capture. These are workflow-run
conclusions, not unique defects or counts of required check failures.

This window follows earlier reductions; it is not their pre-change baseline.
Creation-window CI counts and completion-trigger WR counts are not identical
event cohorts. No job count, allocated runner time, review latency improvement,
service quota relief or before/after saving is inferred from these totals.

### October 21 current-flow recheck

The operator returned the campaign to whole-flow optimization: queue analysis,
conditional execution and removal of repeated work, not further capacity setup
or individual installer-test repairs. This is the same reviewed #6237
objective. The implementation choices below are **agent-recommended**.

The fixed creation window is `2026-10-21T06:00:00Z..2026-10-21T18:00:00Z`.
Nine API pages returned **888 distinct run IDs**, equal to the reported total.
This is a complete run population for that window, not a complete job-timing
population or an event-normalized before/after experiment.

| Workflow cohort | Runs |
|---|---:|
| CI | 203: 143 PR events and 60 pushes |
| Promote trigger | 202 |
| Workflow lockdown guard, excluding separately titled promotion invocations | 154 |
| Departed-source refresh | 107 |
| Managed Code Review | 102 |
| Validate and promote | 39 |
| Main base reminder | 37 |

There were 821 successful, 47 skipped, 16 failed, two action-required and two
cancelled runs. Repeated workflow/event/head identities are not by themselves
redundant executions: attempts, separate PRs and metadata epochs matter.

Source inspection pins Dev `632f93d4bce28bd84ed9c4dc019c836b3629180d` and
Main `bcb55e0899f22dbd89a6dd09cf7fbad586a47582`; the source reduction starts
from Dev `a45012e38b5476f56d8f6e965d2529f065abb949`.
Representative job observations were checked against their literal timestamps:

| Boundary | Observation and interpretation |
|---|---|
| [PR source CI, run 39646930297](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39646930297), attempt 1 | The four ordinary jobs have created-to-start intervals of 2-4 seconds. These are runner-pickup/dispatch intervals, not evidence of a capacity backlog. |
| Same run, [guards job 119384842925](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39646930297/job/119384842925) | Execution takes 153 seconds. Sibling tracking-import analysis takes 38 seconds, report-only marketplace inventory 36, headless-launch analysis 25 and asyncio timeout analysis 16. |
| Same run, impact job 119384843587 | The separate planning runner executes for 12 seconds, then units start in another job after a dependency handoff. This does not require another test execution. |
| [Promotion 39634436538](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39634436538), attempt 1 | Run creation precedes first job creation by 3m27s. This is an admission/concurrency/platform interval, **not** a pure runner-queue measurement. The gate then starts in two seconds. |
| Same promotion | Actual execution is 2m58s for composition, 3m33s for premerge publication/readback and 1m12s for merge. Units and artifact construction initially run in parallel. These distinct producer/custody gates are not interchangeable test reruns. |

The bounded wider job sample similarly showed pickup intervals of a few
seconds. It does not establish a whole-population queue percentile. The
five-minute review-request residence has an explicit native/manual-request
grace contract; deleting it blindly would change request semantics, not merely
remove a sleep. Likewise the promotion trigger also owns PR identifier
scanning, baseline maintenance and recovery, so a blanket Dev-branch filter
would delete required consumers.

The selected source reduction preserves guards/units parallelism while
combining impact admission and positive unit execution in one existing hosted
job. The fixed-name aggregate requires both jobs to pass and checks the real
test-step outcome against the selection. This removes one allocated runner
for selected-unit PRs and a duplicate checkout; narrative-only PRs retain an
explicit skipped test step rather than fabricated unit success. Dev pushes
still do not duplicate promotion's independent release-unit pass.

The report-only marketplace inventory moves off PR updates while remaining on
admitted push/manual source CI. It is not a strict gate or
unit suite. In the observed PR, removing its repeated execution takes a
36-second step off the guards critical path; **actual after-change wall time
still needs the resulting run**, not subtraction asserted as a benchmark.

All previously admitted unit identities and production targets remain.
New conditional/gate negatives extend the same packet and unchanged
300-second ceiling. This is not a claim that the packet is the complete unit
census, that every periodic family passed, or that queues/review throughput
are now solved. Required provider contexts remain the PR aggregate, lockdown
and source attribution; the removed planning job is not independently required.

### October 22 comparable-event follow-up

The run-creation windows below are each twelve hours. Enumerate each in four
disjoint three-hour partitions, paginate every partition at 100 records per
page, and require unique run IDs to match each API-reported total. This avoids
the provider's 1,000-result query cap rather than silently truncating the
larger population.

| Creation window (UTC, inclusive) | Partition populations | Pages | Distinct runs |
|---|---|---:|---:|
| October 21, 06:00:00–17:59:59 | 315 / 249 / 131 / 193 | 11 | 888 |
| October 22, 09:50:00–21:49:59 | 354 / 154 / 257 / 357 | 13 | 1,122 |

The earlier window matches the preceding run-ID population; current conclusions
can change after capture or a rerun, so do not overwrite its dated outcomes.
The later window contains 272 CI runs (199 PR, 73 push), 271 promote-trigger
runs, 211 ordinary lockdown runs, 130 departure refreshes, 141 managed-review
runs, 35 promotions and 27 base reminders, plus separately titled promotion
checks and distinct maintenance/native lanes. **Total invocations increased**;
these unequal activity populations do not demonstrate fewer workflow runs.

For job comparison, take the first twenty chronological successful
`pull_request` CI runs in each window, ordered by creation time then run ID,
and read all jobs from each run's recorded attempt. These are bounded,
event-matched samples, not random samples or full-population queue percentiles.
Each sample contains eighteen actual selected-unit executions and two
narrative omissions.

| Observed CI boundary | Earlier sample | Later sample |
|---|---:|---:|
| Allocated runner jobs, twenty PR runs | 78 | 60 |
| Selected-unit PR allocation, eighteen runs | 4 each | 3 each |
| Narrative PR allocation, two runs | 3 each | 3 each |
| Allocated-job created-to-start median | 3 seconds, 78 jobs | 3 seconds, 60 jobs |
| Allocated-job created-to-start range | 2–8 seconds | 2–4 seconds |
| Guards execution median, twenty jobs | 160.5 seconds | 63.5 seconds |
| Unit-job execution median | 20.5 seconds, eighteen jobs | 32 seconds, twenty jobs |

This demonstrates eighteen fewer allocations per twenty observed PR events:
the separate planner disappears for selected-unit PRs, while narrative
selection moves into the remaining unit job. The new unit job includes
selection, and the packet/source changed between windows; its duration is not
an equivalent-work regression or speedup measurement. Guard reductions overlap
several reviewed changes and varying inputs, so their observed wall times are
not a controlled attribution to any one patch. The first/last sampled CI runs
are `39552923955`/`39560024329` and `39759654174`/`39765561570`.

Run creation precedes first allocated-job creation by as much as 132 seconds
in the earlier sample and 62 in the later sample. Keep that admission,
concurrency or platform interval separate from the two-to-eight-second runner
pickup interval. A long run-level wait does not by itself justify more runner
capacity.

Separately select the first ten chronological **completed**
`Running Copilot Code Review` runs in each window, using their actual dynamic
workflow identity rather than the checked-in setup workflow. All ten in each
sample report success. Hosted review pickup medians are **four seconds in both
samples** (ranges 4–5 and 4–6); run-to-job-creation medians are 1.5 and two
seconds. Execution medians are 235.5 and 246.5 seconds. First/last sampled
review runs are `39553009791`/`39558168499` and
`39760202452`/`39765387242`.

Neither these samples nor the 102/141 review runs created in the windows prove
improved review throughput, request-to-run latency, reduced inference
throttling or service quotas. The request/grace interval is not measured here.
The supported conclusion is narrower: fewer source-CI allocations per
selected PR with retained actual units, but no observed median runner-queue
improvement and no demonstrated reduction in total workflow invocations.

### October 22 promotion critical-path sample

Select the last three successful `repository_dispatch` promotions from the
already captured later creation-window population. Read attempt one for each;
every jobs page returns eleven jobs: ten allocated executions and one skipped
failure-report job. This is a bounded successful-run sample, not a random
sample, a failure census or before/after evidence.

| Attempt-one run | First-job admission | Run to final job completion | After first-job creation | Job pickup median |
|---|---:|---:|---:|---:|
| `39852103270` | 216 seconds | 918 seconds | 702 seconds | 3 seconds |
| `39857109817` | 1 second | 755 seconds | 754 seconds | 3 seconds |
| `39859212307` | 0 seconds | 738 seconds | 738 seconds | 3 seconds |

Compute admission from run creation to the first allocated job's creation;
compute pickup separately from each job's creation to start. Parse both as
UTC, not mixed local/UTC timestamps. The 216-second admission interval does
not establish a runner-capacity problem or its specific cause. Summed pickup
over ten jobs is 35/32/33 seconds; this sum includes parallel branches and is
not time to subtract from the critical path.

Artifact build and source units run in parallel. The actual source-unit logs
bind Dev `258a4dd71b4f667bb24f6abb921ea276b633d32f`,
`930e8e10648da2777f1fda2e905469725b9af8da` and
`fc5ad89d65b05f9c091ce660b3dffa982fe8c5dd`, respectively. Each executes all
877 currently admitted cases once, with coverage, in 9.527/18.269/17.601
seconds. These are not the full historical unit census. The artifact-build
jobs take 62/85/76 seconds and outlast the source-unit jobs' 26/36/36 seconds.

The subsequent promote jobs take 179/201/180 seconds, including exact-candidate
publication/readback steps of 92/103/97 seconds and pinned-signer verification
steps of 45/52/49 seconds. The later source-publication jobs take 228/239/249
seconds; their combined qualification, verification and publication command
takes 177/183/194 seconds. Merge jobs then take 73/72/71 seconds. These separate
trust boundaries are not interchangeable checks. Buffered helper output cannot
attribute the combined command's cost to individual functions.

The last sampled job explicitly selects reviewed helper
`ce77da58017442f83bf3d82a5f80b111b63b9737`. That immutable helper's attestation
loop is serial; it does not establish adoption of the bounded source changes
in #7679/#7732. Existing release/control ownership retains pin selection,
qualification and natural timing acceptance; this audit does not change a
trusted pin or start another promotion.

Across all allocated jobs, checkout steps sum to 124/133/127 seconds; again,
parallel work makes this a work total, not a critical-path saving. One bounded
source reduction uses a depth-one checkout of the gate-pinned Dev SHA in
`source-units` only. Its version/library guards and admitted packet require
current source bytes, not ancestry. Every history-dependent release checkout
remains unchanged. The packet, coverage, identities and deadline remain intact.
This removes unnecessary history transfer from that job, not an allocation,
workflow invocation or demonstrated promotion critical-path delay. Source
review and actual trusted-control adoption remain distinct gates.

### Enforcement snapshot

At the October 18 capture, the Dev ruleset checks were `workflow-lockdown-guard` and
`source-attribution`. Classic Dev protection additionally required
`PR gate (required check)`, bound to app ID 15368. Main's ruleset required
`main source gate` with strict freshness; its classic required-status endpoint
returned 404. Inspect both protection mechanisms, not just one check list.

Environment names `copilot` and `main-promotion` were observed. Secret values,
environment-review settings, current credential custody, complete in-flight
release obligations and all bypass applicability were not audited by that
name-only read. Refresh authoritative policy and every affected caller before
any cutover; this dated snapshot is not standing merge authority.

## Prioritized candidate ledger

Every row is a proposed investigation or slice, not permission to delete.

| Priority | Candidate | Evidence / open question | Replacement prerequisite and rollback |
|---|---|---|---|
| 1 | Coalesce compatible trusted PR metadata events | 169 departure refresh runs overlap synchronize/edited entrypoints; closed events and all-base retargeting differ from Dev-only lockdown | Preserve shared-head departure refresh, closed/retarget/base-edit epochs, contexts and fork-safe inert-data processing. Restore original event path on any missing refresh or context. |
| 2 | Retire duplicate legacy trusted-CI entry when integrated replacement is proven | 75 skipped invocations; #7122 already owns a pure existing-row backend selector, not CI activation | Confirm actual opt-in/admission state and accepted replacement coverage/aggregate semantics. Hosted remains default for unknown/unready/fork inputs. Restore the legacy path/hosted selection if admitted work or failure reporting is lost. |
| 3 | Narrow promotion completion entrypoints after preserving PR scanning | 196 WR invocations; 47 skipped. The same workflow also owns PR identifier reporting and Dev baseline maintenance | Do not simply filter to Dev and lose PR scans. Reuse an existing trusted handler only after exact-head scanning, Dev freshness and reporting contracts pass. Restore original caller before disabling a broken replacement. |
| 4 | Put qualified narrow Linux capacity to useful work | A pinned-source, pinned-image structural guard can be qualified independently of broad native CI | #7122/CI owner approves exact job, label, trust, tooling, resource and aggregate contracts. Actual Actions assignment and complete result required; image feasibility is not activation or capacity relief. Restore hosted execution for the affected row on failure. |
| 5 | Reuse identical source qualification across CI/release | Source `full` invocations appear in multiple gates; generated consumers and artifact custody are distinct | Prove the complete exact-input tuple and required authoritative evidence. Keep native/platform/generated-output checks. Retain old gates until replacement is required and failure/rollback proof passes. |
| 6 | Share deep-lane setup or a bounded scheduler | Ten schedules include seven distinct test lanes; setup/scheduler overlap does not prove coverage overlap | Preserve each selected test/marker/platform, cadence/fairness, containment limit and failure owner; avoid burst fanout. Restore individual entrypoints if any lane is missing or delayed beyond its accepted contract. |
| 7 | Reconcile repair and housekeeping ownership | Watchdog, generated repair, purge and stale-branch operations are not validation suites | Record detection, repair claimant, release steward and escalation when automation is disabled. Do not activate a repair worker or widen credentials merely to consolidate. |

## Ordered execution plan

These waves refine existing parent phases rather than replacing their gates.
No implementation starts against this amendment before its normal review.

| Wave | Deliverable and owner seam | Acceptance / next step |
|---|---|---|
| A - Close the audit contract | Campaign driver, with component/portfolio owners: bind each candidate's helper closure, exact event/input tuple, contexts, call graph and repair owner | Record accepted non-overlapping scope and before/after cohort; unresolved equivalence remains a blocker, not a removal recommendation. |
| B - First safe event reduction | #6237 driver proposes compatible PR metadata coalescing; CI/release controller approves overlapping integration/control seams | Unit and real event negatives pass; fewer invocations/check records for the affected comparable cohort, without missing refresh/scan/admission. Land through the correct Dev/control lane. |
| C - Useful bounded Linux lane | Existing #7122 selector owner and CI/controller own exact guard/workflow/aggregate admission; capacity operator owns image/node qualification | Reviewed selector delivered to actual control, inactive/unknown/fork paths remain hosted, one real admitted Actions job reports complete scope and actual runner assignment. No second copy of equivalent production work. |
| C2 - Owner-only local-provider review rescue | Existing #7122/#3336 owners accept the [rescue contract](bounded-review-rescue.md), control/scheduling seam and explicit repository-owner delegation; campaign driver coordinates | Hosted default; provider-neutral backup runner set, enforced owner/repository/workload admission, periodic positive runner-fault classification, checked-in switch, bounded native requeue, actual backup review and verified hosted restoration. Qualify independent admission for total hosted outage or hold/escalate; no live enablement from plan approval. |
| D - Source/release evidence reuse | #3336 controller and portfolio owner define authoritative source/recipe/coverage/platform/dependency/run/attempt identity | Negative stale/missing/skipped/failed/wrong-input cases block reuse; generated output and artifact publication retain independent proof. Preserve the single active promotion through verified merge. |
| E - Deep/maintenance consolidation | Component, portfolio and remediation owners accept each separately bounded row | Full native/installation/stress selectors and failure escalation remain executed and attributable; scheduler sharing saves actual invocations without uncontrolled fanout. |
| F - Close the loop | Campaign driver owns the measured report and current parent Plan/Validation Plan | Observe two release cycles plus comparable contribution events, rejection and rollback; refresh policy and as-is docs. Resolve or explicitly transfer every outstanding item before completion. |

Wave A's accepted bounded metadata/helper/event closure is recorded in
[metadata-event-closure.md](metadata-event-closure.md). Current-head enforcement,
old-head peer recovery and Main promotion refresh are distinct obligations,
not duplicate policy checks. The first proposed production seam shares only
the Dev synchronize entrypoint; exact CI/controller agreement and prospective
event equivalence remain open before Wave B edits.

The already accepted #3336 Phase 9 single-flight promotion work and parent
Phase 5a Maintainer stewardship continue with their current owners. This plan
does not duplicate their writer, permission, consent or rebase implementation.
Ordinary source, helper, test and documentation changes target Dev. Narrow
Main control changes require their separate current-base authorization.

### Runner and review admission

Keep native Code Review on its supported hosted primary while qualifying useful
additional capacity. GitHub's runner contract chooses a hosted label or ARC
scale set; it does not document ordered hosted-to-ARC overflow. Multiple labels
are cumulative constraints, not a fallback list. A setup success or online pool
does not prove a full review, workload compatibility or automatically shared
cloud capacity.

The operator subsequently requested a periodic, time-boxed rescue loop rather
than transparent overflow. The [bounded rescue contract](bounded-review-rescue.md)
is a general proposal for owner-delegated local providers to supply a backup
runner set during congestion, not a machine-specific deployment plan.
It preserves ordinary control/review authority and names owner-only admission,
native re-request and restoration proof. It is proposed work, not an active
fallback. Its own hosted control-gate dependency must be resolved or reported
as an explicit full-outage blocker.

The first structural candidate's source contract is the complete unmaterialized
tree at the pinned Dev commit, executed as
`python3 -I -S -B tools/check-install-contract.py`, with exit zero and
`Install contract OK (13 plugins).` required. Its proposed narrow selector is
not approved by this audit. No Node action, broad test toolchain, privileged
release job or foreign PR is admitted by that bounded structural contract.
Provider-specific deployment, credentials and capacity inventory stay downstream.
First-class managed runner lifecycle remains separately tracked by #7192.

## Validation and measurement contract

For each affected event, record source/control/recipe commits, event/action and
base, selected suites and matrix cells, platform/interpreter/toolchain identity,
run/attempt/job IDs, runner assignment, terminal results and required contexts.
Compare workflow invocations, allocated jobs, check records, runtime and queue
latency separately; include review request/start/completion and observed throttle
signals without assuming Actions and Copilot share one quota.

Required negative cases include forks/unknown permission, draft and retargeted
PRs, shared-head departure, stale source/control base, supersession/cancellation,
missing or wrong-attempt evidence, skipped replacement jobs and genuine failures.
Coverage-equivalence tests must preserve native Windows/macOS, PowerShell,
installation copies/modes and deep/stress selectors; Linux success cannot
discharge those contracts.

Each slice records unit, lightweight consumer, fresh-install and live Actions
tiers, with a concrete reason for an unavailable tier. Removal needs an
executed replacement and current authoritative required-check evidence, not
merely green setup or a shorter YAML file.

Success is a measured decrease in redundant work for comparable affected
cohorts with unchanged assurance and timely feedback. Legitimate new coverage
and one-time qualification costs are separated, not hidden in a savings claim.
Any missing coverage/context, unauthorized runner exposure, dual writer or
broken recovery aborts the slice; restore the affected old path before disabling
the replacement. Parent completion remains open until its whole gate resolves.
