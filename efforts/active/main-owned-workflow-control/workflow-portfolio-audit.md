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

### Enforcement snapshot

At capture, the Dev ruleset checks were `workflow-lockdown-guard` and
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
| C2 - Bounded native review rescue | Existing #7122/#3336 owners accept the [rescue contract](bounded-review-rescue.md), control/scheduling seam and scoped authority; campaign driver coordinates | Hosted default; periodic positive runner-fault classification, checked-in switch, bounded native requeue, actual ARC review and verified hosted restoration. Qualify independent admission for total hosted outage or hold/escalate; no live enablement from plan approval. |
| D - Source/release evidence reuse | #3336 controller and portfolio owner define authoritative source/recipe/coverage/platform/dependency/run/attempt identity | Negative stale/missing/skipped/failed/wrong-input cases block reuse; generated output and artifact publication retain independent proof. Preserve the single active promotion through verified merge. |
| E - Deep/maintenance consolidation | Component, portfolio and remediation owners accept each separately bounded row | Full native/installation/stress selectors and failure escalation remain executed and attributable; scheduler sharing saves actual invocations without uncontrolled fanout. |
| F - Close the loop | Campaign driver owns the measured report and current parent Plan/Validation Plan | Observe two release cycles plus comparable contribution events, rejection and rollback; refresh policy and as-is docs. Resolve or explicitly transfer every outstanding item before completion. |

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
uses existing literal hosted/ARC modes, preserves ordinary control/review
authority, and names native re-request and restoration proof. It is proposed
work, not an active fallback. Its own hosted control-gate dependency must be
resolved or reported as an explicit full-outage blocker.

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
