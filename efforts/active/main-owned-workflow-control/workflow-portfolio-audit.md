# Workflow portfolio audit and consolidation roadmap

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Standing intent: [workflow orchestration vision](../../../visions/main-owned-workflow-control/README.md),
[test portfolio](../../../visions/test-portfolio/README.md) and
[release pipeline](../../../visions/release-pipeline/README.md).

## November 17 source-PR edit: repeated execution, not a reuse hit

The merge-first order is complete with #12013; resume the Root's event/helper
closure without starting another installed/native capture. A body-only
evidence update at `09:38:17Z` is followed four seconds later by CI
`44057400685`, while the existing CI `44056500810` is still running.
Both are successful attempt-one `pull_request` executions. The run API does
not expose the activity subtype; the body update is the observed initiating
operation, not a reconstructed event payload.

The actual unit checkout in both logs is merge `ab3fe4d475`, from head
`e47e5f6f44f6b18695e6a2197f1a4bdb701ee1d8` and base
`6ba99aefb7efab66e35a8eb571eab129cfa02245`. Every published context-fingerprint
category matches: source/recipe/manifest identity, sealed environment,
runtime/interpreter and installed distribution bytes. Today's empty
closed-run PR associations are not substituted for event-time association
proof.

| Second-run job | Actual interval, UTC | Allocated duration |
|---|---|---|
| Guards and lint | 09:43:32-09:48:50 | 318 seconds |
| Eligible units with coverage | 09:43:32-09:46:52 | 200 seconds |
| Required PR aggregate | 09:48:53-09:48:57 | 4 seconds |

This adds 522 seconds, or 8 minutes 42 seconds, of job allocation. It is not
controlled billing, review-throughput or throttling evidence. The first
execution produces a context-qualified fresh artifact. The second explicitly
reports `mode=executed`, `reason=provider-lookup-budget-exhausted`, then uploads
fresh unqualified evidence. It does not reuse the first execution. The
particular lookup/verification stage that exhausted the original eight-second
deadline remains unknown; do not extend that deadline or weaken proof from
this observation.

Source `ci.yml` admits `pull_request.edited`; the source guard and unit
selectors inspect the base/head diff, not event `changes`. The concurrency
rule cancels only synchronize, so an edit can queue another execution.
The trusted metadata handler remains a distinct, legitimate body/source-
attribution consumer. Do not retire its metadata checks as source duplicates.

The existing CI owner has a
[bounded overlap/interface request on #3336](https://github.com/ThomasMichon/copilot-extensions/issues/3336#issuecomment-6945270081).
No shared workflow/helper edit, rerun, dispatch, budget change or release
action accompanies this finding. Removing `edited` outright would lose
base-retarget validation. Merely skipping jobs would preserve unnecessary
workflow invocations. A true invocation reduction must first retain that
base-change coverage through an accepted existing event consumer.
Until that seam is agreed, source evidence-description updates belong in
issue comments rather than repeated PR-body edits.

After adopting Dev `f353f7758c`, the source-CI workflow still admits every
`edited` event; its source/unit selectors still use the base/head diff.
The existing trusted metadata workflow has no retarget-to-source-CI dispatch
interface. A producer-side no-op hypothesis is separately ruled out:
Worktrees' GitHub metadata provider compares the freshly read field with the
desired value and confirms unchanged fields without PATCHing them. Its
existing composed-description retry control also requires no additional writes.
Do not rewrite that already-idempotent provider or infer that the observed
changed evidence body was an unchanged-body write.

The [November 20 attribution slice](journal/2026/11.20-unit-lookup-attribution.md)
adds bounded phase diagnostics to the existing reuse helper, not a workflow
change or claimed execution saving. Run/artifact/job inventory, producer
qualification, archive download and packet verification retain the same
eight-second aggregate and request sequence. The historical exhausted stage
remains unknown; real diagnostic delivery and subsequent comparable evidence
remain separate from synthetic controls. The edited/retarget ownership seam
and its existing acceptance requirement are unchanged.

## October 23 repeated PR qualification: exact-input reuse boundary

Query the already captured October 22-23 population, not another rolling
cohort. Its 322 PR CI runs contain 49 repeated head SHAs and 53 additional
run records at those heads. **Those are candidates, not 53 proven redundant
validations:** a shared head alone says nothing about base, synthetic merge,
recipe, toolchain or metadata epoch. The captured run records have empty PR
arrays, so do not infer PR identity from that missing association.

Close one concrete pair separately. #8120's CI runs `39913005748` and
`39913119523`, both attempt one, execute unit jobs `120280077818` and
`120280483775`. Their actual checkout and impact logs agree on:

| Input | Observed value in both executions |
|-------|-----------------------------------|
| PR head | `f5c98b622ea39630b10e52e04e335725e4654713` |
| Event base | `4e0a1e0e355c8b6c3682d4b7f60e02bae43151eb` |
| Checked-out synthetic merge | `8b053e70095f3a86e7362f979fd43245cf5c7f78` |
| Hosted image | `ubuntu-24.04`, `20261012.334.1` |
| Interpreter / uv | CPython `3.12.15` / `0.12.6` |
| Installed unit tooling | coverage `7.16.2`, pytest `9.1.1`, packaging `26.3`, PyYAML `6.0.3`, iniconfig `2.3.1`, pluggy `1.7.0`, Pygments `2.21.0` |
| Actual unit outcome | The same 902 cases pass in 18.043 and 15.435 seconds |

The first run is created at `06:41:07Z`; the second at `06:42:14Z`.
Their actual required aggregates finish at `06:42:22Z` and `06:43:36Z`.
Do not use later attached metadata-check refreshes as runner execution.
The precise triggering edit/action is not established by those logs.

Repeated **source PR unit qualification** is therefore a better next
equivalence candidate than an unproved checkout speedup. Any reuse must bind
the exact source/merge/base, workflow recipe, complete manifest/source closure,
platform/interpreter/dependencies, original provider run/attempt and verified
three-file packet. Never infer a pass from a matching SHA or artifact name
alone. Missing, incompatible, expired, pending or failed evidence must retain
fresh execution, not become a skipped-success result.

This is not permission to omit current metadata/guard checks, retarget
validation, the required aggregate, or independent unit execution for every
Main release. The current packet has no explicit complete toolchain binding;
its unchanged schema cannot by itself prove that reuse tuple. No reuse
implementation, token-permission change, source-CI omission or Main activation
had been made at that measurement boundary.

The operator subsequently authorizes strict source-PR reuse and read-only
Actions-artifact access. The source implementation binds the actual interpreter
and installed distribution bytes, original provider PR/base/head association,
exact attempt and fresh upload, immutable archive and canonical coverage
packet. The credential/event-free child environment is explicit and hashed;
only fresh execution uploads. Current-checkout contradictions fail rather
than relabelling proof. Backend or old-proof incompatibility retains fresh
units. Source #8311 subsequently merges with actual current-head approval,
required CI and real provider reuse. The publication packet preserves the
independent peer additions: 1,129 current nodes, 106 closed sources and 48
production coverage targets, with the unchanged 300-second bound.

Cold CI `40039039714`, attempt one, executes those 1,129 units in 20.140
seconds at head `0f360dab09626f7e18c9ea6ca3911aceeee220a8` and merge
`eb048c3724be7eb621ac543a4d53f1fb5cc07117`. An evidence-description update
triggers ordinary CI `40041585967` at the same head and merge. Every published
context-fingerprint category matches. It verifies the original successful
run/attempt/job, immutable artifact/archive and canonical three-file packet;
no unit process runs again and fresh-only upload is skipped. Current guards
and required aggregation pass. Original packet digest is
`c8cccf8f03a037becd4d6cd4c375c391d6b43b1b51a4ae499ce09364334e3dd2`.

Provider timestamps give 27/8 seconds for qualification and 49/27 seconds
for the allocated unit job in that cold/reused pair. Overall CI takes 62/69
seconds and remains guard-bound. This is one observational pair, not a
controlled speedup, runner-queue saving or fewer workflow invocations.
An earlier prior-head miss remains unclassified; no input/dependency
binding is weakened to obtain the hit. Qualified release adoption, full
unit-census, every-release and accountable periodic-repair gates remain open.

## October 23 historical-blob checkout candidate: not selected

Plain shallow checkout remains rejected because PR retargets and earlier-head
diffs need endpoints outside the synthetic merge's immediate parents. A
separate candidate keeps `fetch-depth: 0` and the complete current checkout,
but uses `filter: blob:none` to omit historical blobs. The actual checkout
action fetches current blobs before removing its temporary credential; the
unit selector needs trees, not historical file bodies. A real-Git fixture
preserves all commits, tags, current files and the production selector's
merge-parent/fork-head, earlier-head, retarget and historical-base results.
Unknown endpoints still fail closed with the consumer transport unavailable.
This closure applies only to the source unit consumer, not other checkouts.

Measure actual public-origin fetch plus checkout at source
`41011498bb8cce638a7daae6cca6d11d5a9da929` before editing CI. Each
cold full/filtered pair uses one immutable advertised-ref snapshot, anonymous
Git with system/global configuration and credential helpers disabled, and
identical source, refs, all commits, index and every current file hash. The
first pair covers 2,398 advertised refs/11,873 commits; the reversed-order pair
covers 2,399/11,875. Both cover all 8,853 current files and disable the remote
before the equivalence proof. Disposable stores are removed normally.

| Pair order | Full fetch + checkout | Filtered fetch + checkout | Result |
|------------|-----------------------|---------------------------|--------|
| Full, filtered | 25.406 + 12.344 = 37.750 s | 16.625 + 17.515 = 34.140 s | 3.610 s faster |
| Filtered, full | 25.641 + 11.843 = 37.484 s | 20.485 + 19.015 = 39.500 s | 2.016 s slower |

The final Git object store falls from approximately 124.35 MB to 59.58 MB,
but fetching current blobs makes checkout slower in both pairs and the total
effect reverses direction. Object-store bytes are not measured wire bytes.
This is native public-transport evidence, not controlled Actions latency.
**Do not select or publish this optimization on those results.** Keep CI
checkout, full history, event handling, credentials and release gates unchanged;
do not substitute fixture byte savings for a reliable consumer-cost reduction.

## October 23 remaining source-CI recipe closure

Actual source CI `39943737570` spends ten seconds in the unit checkout and
22 seconds in the three-scanner AST group. Those are execution steps, not
runner pickup or promotion admission. Do not remove `pull_request.edited`
blindly: base retargets still require validation. A fresh real-Git fixture
rejects replacing the unit checkout with plain depth two: a detached
merge-plus-parents checkout preserves the usual base/head selection, including
a fork head with no local branch, but lacks a retargeted event's base that
full history contains. Missing endpoints fail closed rather than producing
false evidence, but the resulting failure is still a behavior regression.
Manual/unknown events do not read a diff; push units remain unallocated.

The next bounded candidate uses facts gathered by the headless guard's
existing first AST visitor. An unsuppressed spawn needs a recognized
module attribute or import; a detached launch needs both a `Popen` reference
and a recognized detach-helper reference. Omit a later visitor only when
those necessary AST facts are absent. This uses parsed, normalized Python
identifiers rather than source-text keywords. Every file is still read and
parsed, syntax failures still diagnose, and admitted visitor/scoping logic,
all six rules and allowlists remain unchanged.

The unchanged native corpus preserves 2,369 reads, 2,310 parses, all 99 raw
hits in 81 nonempty finder results, and the complete ordered diagnostic list.
Actual visitor passes decrease from 6,263 to 2,649. Three alternating whole
guard runs per version have median **38.909 seconds before** and **22.715
seconds after**. The 113 focused cases include forced-unfiltered differential
checks, import aliases, Unicode-normalized identifiers, syntax errors and
changed-input refresh. These local measurements are not controlled Actions
latency, fewer workflow invocations or review-throughput evidence.

A preceding shared-child-edge cache candidate also preserved semantics but
regressed paired whole-guard median from 37.190 to 42.254 seconds. Discard
that implementation rather than mistaking fewer edge enumerations for a
real improvement.

#8213 merges at `2026-10-23T12:29:36Z` as
`c1e4e48867c7242e0a7f62b35930f01eb27a1376`. The isolated publication tree
passes all 113 cases and exact whole-guard equivalence across 2,372 reads,
2,313 parses and 99 raw hits, retaining independent intervening source additions.
Actual final-head CI `39952588491` runs all three scanners successfully in
twelve seconds and all 1,012 admitted units in 16.114 seconds with coverage.
The exact source passes approval, complete-feedback and required-check gates.
These are natural source observations, not controlled hosted speedup or
qualified release adoption.

## October 23 tracking-alias recipe admission

The remaining sibling tracking-write guard always reads and parses every
Python file in every non-owning plugin. Its alias fixed point and reflective
access checks can only recognize a root introduced by a static import of the
package/protected module, or by an exact literal dynamic-import target.
Inspect those necessary roots in the already parsed, breadth-first node tuple
before entering the unchanged alias analysis. Literal targets use Python's
decoded AST values, including escape sequences and adjacent string literals;
import identifiers retain Python's Unicode normalization. This is not a
source-text prefilter or a broader dynamic-expression analyzer.

An in-memory prototype on the unchanged native 2,859-file corpus preserves
every read, parsed AST digest and ordered diagnostic. Three alternating
whole-guard pairs have median **40.715 seconds before** and **33.683 seconds
after**. One pair reverses direction, so retain the visible native variance;
these measurements are not controlled hosted or whole-flow speedup.

The candidate's 92 focused cases pass, including forced-unfiltered
differentials, encoded/adjacent targets, normalized imports, wildcard imports,
lazy helpers, queue/reflection access, read/parse errors, changed-input refresh
and absence of downstream alias work for impossible trees. Existing scope,
denylist, alias propagation and diagnostic ordering remain unchanged.

Committed source qualification passes in hardened, network-disconnected Linux
with the existing four-CPU/memory/process limits: all 92 cases, exact
unfiltered/admitted equality across 2,859 reads and original parsed AST digests,
and the real guard CLI. Only 28 trees enter alias analysis; 2,831 omit
impossible passes. An initial ad hoc recipe combined this contract check with
six additional benchmark traversals and exhausted its 300-second bound.
Remove those redundant benchmark traversals, not any contract assertion or
test, and retain the unchanged bound. Both disposable containers are removed.
Ordinary source review/required CI and release adoption remain outstanding.
No workflow event, runner, controller pin, service or release-unit policy changes.

#8227 merges at `2026-10-23T13:33:14Z` as
`41011498bb8cce638a7daae6cca6d11d5a9da929`. Its actual publication tree
retains independent intervening additions and passes all 92 focused cases,
exact equality across 2,867 reads and original AST digests, and the real CLI;
2,839 impossible analyses omit while 28 remain admitted. Current-head Copilot
approval, complete feedback/closing-reference reads and the actual-source
seven-read required-check verifier pass before managed merge.

Actual final-head source CI `39961246652` retains all three scanner success
messages, completes their group in fifteen seconds, and runs all 1,012
admitted units in 19.969 seconds with coverage. The guards and required
aggregate pass. This natural sample is not controlled hosted speedup; no
workflow invocation or release gate disappears. The publication child
finalizes normally after canonical merged-PR reconciliation. Qualified release
adoption and the whole-parent measurement/portfolio gates remain open.

## October 23 documentation-count recipe reduction

The documentation and version guards enforce different policies; neither is a
duplicate to remove. Attribute the documentation count check before changing
its scope: all 1,009 Markdown files contain 22,248,244 normalized characters.
The eight existing regular expressions spend most of their time rejecting
arbitrary initial tokens, including patterns with no matches in this corpus.

Split each unchanged full expression into its original prefix and a necessary
suffix. Search that suffix with the same case-insensitive regex semantics
before running the full expression. Derive both from shared fragments rather
than maintaining a separate keyword roster. Every file remains read and
normalized; a missing suffix skips only an impossible full-pattern scan.
No changed-path selector, cached result, count grammar or diagnostic ordering
changes. Unicode case-insensitivity and whitespace continue to use `re.I`
and the original regex syntax rather than an ASCII lowercase approximation.

On the same native corpus, three alternating full count-check runs per version
have median **15.415 seconds before** and **4.735 seconds after**. Every
original regex string, match span, capture, file and ordered diagnostic agrees.
The complete documentation guard and 122 focused cases pass; these include
all count phrase forms, numeric/word/unknown tokens, emphasis, multiline
whitespace, Unicode case variants, ordered failures, impossible-scan work
counts and read-error propagation. These are local recipe measurements, not
controlled Actions/whole-flow latency or review-throughput claims.

Required source CI still invokes the whole documentation guard. #8199 merges
at `2026-10-23T11:11:37Z` as `c7fce94080ab7f2bfda775b8e5ffcd2a9ca88c1d`.
Actual final-head CI `39943737570` executes its complete documentation step
in two seconds and all 1,010 admitted units in 19.780 seconds with coverage.
The first CI attempt correctly refused stale source-admission hashes; both
are repaired and the 93 new original assertions are enrolled through their
unchanged wrapper, not bypassed. The merged packet preserves a concurrent
peer's two new nodes and passes all 1,012 cases/3,036 phases/45 targets in
44.658 seconds in isolated Linux with the same 100-source closure and no
prohibited effects. No deadline or effect boundary changes.

Source merge, actual required-CI observation and release adoption remain
distinct. No Main controller, workflow trigger, runner or release-unit
requirement is changed by this optimization. These observations are not a
controlled Actions speedup or completion of the whole portfolio.

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

### Owner reminder no-op admission

The current Dev workflow inventory (October 23, source `80fb803c093e7f881d4b53a4a279efa42451511f`)
contains 22 YAML files, 66 literal jobs and ten scheduled workflows. This is a
source inventory, not executed coverage: some native jobs are explicitly
unenrolled and their scheduled status output is not a test result. The older
October 18 trigger table above remains historical; notably, current departure
refreshes admit only closed/edited events and trusted-CI is manual-only.

Three already completed Main reminder runs, `39852362163`, `39853993747` and
`39858588255`, each contain three allocated jobs plus a synthetic identifier
check. Their `remind-to-target-dev` jobs are `120081608557`, `120087299109` and
`120103090149`: pickup takes three seconds each and execution takes two/four/two
seconds. Timestamped logs show equal PR author/repository owner and the explicit
legitimate-automated-PR skip. These are successful no-op allocations, not missing
admission or scanning.

The bounded Dev change adds owner inequality to that job's existing synchronize
exclusion. The JavaScript, automated signatures, current-PR reread, trusted-bot
marker deduplication, Main admission, identifier reporting, workflow triggers,
concurrency and credentials stay unchanged. This can remove one no-op runner
allocation per affected owner event after qualified control adoption; it removes
no workflow invocation and establishes no hosted critical-path saving. Source
contracts resolve the recorded Dev workflow separately from preserved Main, whose
legacy admission remains accepted by the actual-controller compatibility test.
Existing release/control ownership retains adoption and live-result acceptance;
this slice starts no Main writer or manual validation run.

### Periodic execution and cleanup publication - October 23 UTC

The complete seven-day scheduled population, October 16 00:00 through
October 23 01:30 UTC, has 46 runs across ten families: 25 failed, one cancelled
and twenty successful. Every run is bound to its immutable cron recipe.
Creation minus the latest nominal cron slot ranges from 11,165 to 26,385
seconds, median 17,316.5 seconds. This is a schedule-admission observation,
not a provider-reported intended time, internal queue measurement or SLA.
Five complete first-attempt job sets separate ordinary three-second pickup
from a cleanup job's 401-second pickup and 385-second execution. That run
spends 360 seconds waiting for required checks; it is not proof of a failed
test or permission to enlarge the wait.

The full-suite rotation's seven failing Linux groups remain explicit
escalations on #7377 to the existing #1303/#6142 repair owners, not accepted
transfers or parent completion. Its successful status-only Windows selector
does not constitute native execution. Installation-context's cancelled
90-minute run and crash-diagnostics' cleanup failures retain separate repair
obligations; their schedule declarations are not coverage.

Cleanup run serialization ends when a run times out, but its generated PR
can remain open. Complete file pages for #7603 and #7895 show 91 shared
changefile-deletion paths, with the older PR's entire path set included in
the newer one. Both are still existing-owner obligations, not permission to
merge, close or replace either PR. The bounded source guard reads all open
Dev PR metadata once, with paginated/slurped output and a 45-second provider
deadline, after actual consumed deletions and the local path fence but before
commit or push. Matching owner, repository, generated branch and workflow
origin refuse another publication visibly, including drafts and holds.
Provider/metadata errors also fail closed. The decision stays in trusted
workflow YAML rather than mutable Dev code running with the release token.

This addresses `minimal-redundant-invocations` and accountable maintenance
without changing run cadence, credential custody, consumed-file selection,
existing required checks, expected-head merge or the six-minute wait. It
reduces duplicate publications, not scheduled invocations or coverage.
Dev source delivery does not activate the independently owned Main recipe;
natural prevention and operating-cost comparison remain open after adoption.

### October 23 rolling whole-flow follow-up

Fully enumerate the fixed creation window
`2026-10-22T20:00:00Z..2026-10-23T07:59:59Z` in four disjoint three-hour
partitions. Their reported populations are 406/566/388/565; all 21 pages
return **1,925 distinct run IDs**, exactly matching those totals. This rolling
window overlaps the earlier October 22 window by one hour fifty minutes.
It is not an independent causal before/after experiment.

| Workflow population | Runs |
|---|---:|
| CI | 451: 322 PR and 129 push |
| Promote trigger | 460 |
| Ordinary workflow lockdown | 373 |
| Departed-source refresh | 194 |
| Dynamic Copilot review | 277 |
| Validate and promote | 75 |
| Base reminder | 29 |
| Native acceptance / full installation / Windows rotation | 17 / 18 / 3 |
| Separately titled promotion lockdown / Dependabot | 27 / 1 |

Conclusions are 1,720 success, 84 skipped, 78 failure, 34 cancelled and nine
action-required. The population includes 29 failed promotion runs. These are
run conclusions, not 29 independent defects, confirmed flaky tests or evidence
that a failed run published a release. Increased activity and unequal event
populations mean that the larger total is not a consolidation speedup or
regression measurement.

Select the first twenty chronological successful PR CI runs, the first ten
chronological completed dynamic review runs, and the last three successful
`repository_dispatch` promotion runs. Read all jobs from each recorded attempt;
these are bounded, nonrandom samples rather than population queue percentiles.
All twenty CI samples execute selected units; all ten review samples succeed.
The first/last CI runs are `39845479261`/`39854813950`, and review runs are
`39845027410`/`39850480820`.

| Boundary | CI sample | Review sample |
|---|---:|---:|
| Allocated jobs | 60: three per run | 10: one per run |
| Run to first allocated-job creation | Median 1s, range 0-32s | Median 2s, range 1-4s |
| Job creation to runner start | Median 3s, range 2-7s | Median 5s, range 4-5s |
| Job execution | Guards median 73.5s; units 38.5s; aggregate 3s | Median 361s, range 115-579s |
| Run to last allocated-job completion | Median 85.5s, range 68-110s | Median 366.5s, range 124-586s |

Successful CI AST groups take a median 26 seconds; install-contract checks
five seconds; documentation consistency 6.5 seconds. Eleven selected dependency
checks have a ten-second median. Inputs and tool/source versions vary; these
observations do not attribute a controlled saving to any one change.
The later natural packet-only #8155 observation separately proves that
#8111 omits the AST group without omitting units: CI `39921653075` runs all
917 cases in 16.821 seconds and completes in 50 seconds.

**Do not use every record returned by the jobs endpoint as the CI runner
tail.** It also returns non-allocated `identifier leak guard` and
`source-attribution` check records, sometimes refreshed much later. For example,
CI `39854301989` is created at `21:04:54Z`, while an attached source-attribution
record completes at `21:38:04Z`. That is not 33 minutes of runner queue or CI
execution. The table uses only actually allocated jobs; downstream check
freshness remains a separate real consumer contract, not permission to delete
those records.

The three natural promotion attempts each contain ten allocated jobs and a
skipped failure-report job:

| Attempt-one run | First-job admission | To last allocated-job completion | After admission |
|---|---:|---:|---:|
| `39918930957` | 244s | 842s | 598s |
| `39920111180` | 222s | 810s | 588s |
| `39921197334` | 230s | 857s | 627s |

Their thirty actual pickup intervals have median three seconds and range
2-10 seconds. The 222-244-second pre-job intervals are admission/concurrency/
platform observations, not proof of a runner-capacity fault or of its cause.
Unit jobs take 41/39/42 seconds, parallel with the longer 77/61/81-second build
branch. The source-publication command takes 143/122/138 seconds; the merge
command takes 70/68/71 seconds. Buffered multi-operation publication output
does not isolate individual helper costs, prove rollout of a specific helper
or authorize changing its trusted pin.

For failure attribution, additionally inspect only the latest three failed
promotion attempts in this captured population. Jobs `120270810434`
(run `39909441499`) and `120273868185` (run `39910277300`) fail source
publication with `manifest source closure differs from candidate`.
Job `120274907975` (run `39911319173`) refuses artifact construction because
the frozen source/composition for Dev
`4e0a1e0e355c8b6c3682d4b7f60e02bae43151eb` differs from the candidate.
A workflow's control head is not necessarily that Dev input. These signatures
are identity-bound refusals, not demonstrated flakes, and do not classify the
other 26 failures. The exact observations are escalated to the existing #3336
release owner without starting another publisher, rerun or repair worker.
Successful later samples do not alone close those failure families.

**Current investigation priority:** trace semantic duplication across CI
completion, trusted metadata reporting and deferred-promotion entrypoints
before proposing another trigger removal. The source bodies show distinct PR
identifier reporting, Dev baseline maintenance, genuine-ancestry admission and
active-promotion recovery consumers. Preserve each exact-head/metadata epoch
and required context; repeated names or one shared head do not prove
redundancy. Keep the original candidate ledger below as a historical
investigation map, not a claim that its already-delivered reductions remain
unimplemented. Capacity expansion, another unit cohort and individual native
installer repairs are not substitutes for this whole-flow investigation.

**Bounded completion-admission follow-up:** the source trace distinguishes
identifier reporting, new-head attribution, departed-head peer repair and
promotion-completion recovery; none is a duplicate merely because its name or
commit overlaps another. The retained population has one successful Main-push
CI, `39911280826`, followed by `39911295855`. Its allocated ancestry job
`120274675022` runs for 13 seconds and explicitly rejects source
`8d905243c80f6e867ca6deff875dc3eb52a3c2dd` as not a Dev ancestor;
dispatch skips. The source job condition merged in #8189 omits only successful CI
push completions on Main, retaining all manual refs, Dev ancestry/freshness,
promotion failure/cancellation recovery, closed-Main wakes and other jobs.
This removes a demonstrated no-op allocation, not an invocation, required
validation or measured review bottleneck. Main control adoption and natural
no-runner acceptance remain separately owned.

### November 20 identifier-validation contract closure

The two identifier-labelled validations observed on #13603 share scanner
implementation and secret-name inputs, but are **not interchangeable checks**.
The safe outcome of this trace is to reject direct deletion, not to count
another reduction.

The source snapshot `0ada40974b994a50d1bdb48b9b75bad6da8ab081`, qualified
historical Main `b954ebcb6cd329858341fd28ed1206ade6981bb9`, and subsequently
observed Main `80816e7a336eb9ba8b143b6d4311c2f0e1a8b9eb` contain identical
blobs for the two workflows, composite action, reporter and scanner below.
This is byte-level contract continuity, not a claim that a controller commit
is the triggering PR input or that later Main qualifies older native work.

| Contract | Required publication validation | Trusted follow-up reporting |
|---|---|---|
| Producer | `workflow-lockdown-guard.yml`, `workflow-lockdown-guard` job, on Dev-targeting `pull_request_target` | `promote-trigger.yml`, `scan-and-report`, following non-cancelled PR CI with a PR association |
| Trusted tooling | Checkout `github.workflow_sha`; fetch event base/head only as inert objects | Checkout `github.workflow_sha`; local `identifier-leak-guard` action fetches the event head as inert data |
| Path selection | `--base BASE --git-ref HEAD`: three-dot Git diff, with full tracked-head fallback for unavailable/unrelated base | Paginated PR-files API, excluding removed paths, then `--paths-jsonl --git-ref`; no equivalent full-tree fallback |
| Denylist | Same `FORBIDDEN_IDS_FACILITY` and `FORBIDDEN_IDS_WORK` names joined into the CI input; explicitly disables live sweep | Same secret names, joined by the composite action; does not explicitly disable live sweep |
| Failure/reporting | Scanner failure fails the required job; the same step also enforces final-diff size | Reporter creates `identifier leak guard`, supplies detailed findings and idempotent per-head PR feedback, and reports infrastructure failures |
| Missing configuration | Scanner returns zero when no identifiers are configured | Reporter returns neutral only when configuration is absent and prerequisites succeeded |
| Epoch/refusal | Event base/head and required-job outcome; protected-path author check fails closed at the PR-files API cap | Rechecks current head before scanning/reporting and refuses stale feedback; metadata/fetch/setup/report failures cannot become reliable success |

Both use Python 3.12 and the same scanner's inert-blob reader. Neither executes
the PR tree in its privileged job. These commonalities do not establish equal
path sets, event-time base/denylist identity, refusal behavior or feedback.
No secret values were read.

Actual old-head evidence is bounded: job `136075750551` in run `44672272599`
passes the required identifier/final-diff publication step for
`3cb189b8bf3497e515c03875ac2740ed01c25985`; custom Check Run `136077299280`
for that same head reports success and nine scanned files. This demonstrates
both paths were present, not that the required scanner's exact input set or
denylist bytes were identical to the later reporting scan. A direct job-log
read was refused because the response contained terminal escapes; no log
filter or escape override is substituted for missing execution evidence.

The November 20 live policy read confirms Dev ruleset `24152679` requires
`workflow-lockdown-guard` and `source-attribution`. Classic protection
separately requires app-15368 `PR gate (required check)`; `ci.yml`'s aggregate
depends only on its source guards and units. Neither inspected mechanism
requires the custom `identifier leak guard` context. This does **not** make its
findings/feedback disposable: the required scanner's own CI failure message
directs contributors to that custom check for details.

Main-targeting PRs also have a distinct accepted-owner path in
`base-branch-reminder.yml`: its scanner depends on successful Main admission
and reuses the same composite action. Do not remove that path while narrowing
Dev follow-up reporting. The completion workflow additionally retains Dev
baseline maintenance and genuine-ancestry dispatch; retiring one scan job
would not alone retire its workflow invocation.

**Disposition:** retain both existing paths. A possible future integration
would produce required enforcement and the existing per-head detailed report
from one trusted scan, preserving final-diff size, fork isolation, missing/
malformed-input behavior, head-movement refusal and Main admission. It needs
an accepted CI/release-owner seam before shared workflow/action edits. Controls
must prove those contracts before retiring the old Dev follow-up job, and an
actual comparable event must establish any allocation/check reduction.
No workflow, provider context, trigger, secret, native profile or Main control
is changed by this read-only closure; no new run is requested.

The subsequent job/check metadata read bounds cost without treating the custom
check as a runner. Required job `136075750551` is created at `04:07:24Z`,
starts at `04:07:26Z` and completes at `04:07:50Z`: two seconds to pickup and
24 seconds of job allocation. Checkout occupies 19 reported seconds; the
combined identifier/final-diff publication step has identical start/end
seconds. That timestamp resolution does not establish zero scanner cost.
Custom check `136077299280` likewise starts and ends at `04:11:34Z`, 224
seconds after the required job completes; that gap is not execution duration
or proven queue delay.

Two completion-workflow runs overlap that custom-check timestamp:
`44672686941` / scan job `136077100285` and `44672692649` / scan job
`136077118612`. Each has a three-second created-to-start interval; their
allocated scan/report jobs last 33 and 32 seconds, with composite-action
steps lasting 15 and 13 seconds. Their other two jobs skip with zero steps.
Both returned run records have empty PR associations, and both composite
steps encompass `04:11:34Z`; metadata alone cannot assign this particular
head's custom check to one of them. Do not count both as duplicate work for
that head, subtract their combined allocation as a saving, or call the
composite duration scanner-only time. The existing job-log refusal remains;
no escape override, reconstructed event payload, new run or broader cohort
query is substituted for missing association evidence.

### November 20 scheduled-entrypoint census and stress-pair trace

The operator retains the existing CI/release-owner boundary for the proposed
identifier integration and directs continuation of the remaining audit.
No shared workflow implementation or owner reassignment follows that choice.

The pinned Source roster declares ten scheduled workflow files and eleven cron
expressions; none of those expressions is repeated. All ten workflow blobs
match the previously observed Main snapshot
`80816e7a336eb9ba8b143b6d4311c2f0e1a8b9eb`. Job counts below are declarations,
not observed allocations: conditions and matrices can change actual execution.

| Workflow | Declared UTC cron | Declared jobs | Observed scheduled invocations |
|---|---|---:|---:|
| Dispatch PowerShell race exhaustive | `41 11 * * 2` | 2 | 1 |
| Context Handoff exhaustive | `0 6 * * 1` | 1 | 1 |
| Crash diagnostics stress | `37 10 * * *` | 2 | 8 |
| Installation context full | `23 10 * * *` | 81 | 8 |
| Module health watchdog | `17 8 * * *` | 1 | 8 |
| Payload CWD contract | `27 11 * * 3` | 3 | 1 |
| Purge consumed changefiles | `0 9 * * *` | 1 | 8 |
| Stale branch sweep | `30 9 * * 1`; `17 * * * *` | 2 | 27 |
| Windows coverage rotation | `41 9 * * *` | 4 | 8 |
| ZDD singleton stress | `52 10 * * *` | 1 | 8 |

The observation query is the paginated Actions run list with
`event=schedule&created=2026-11-13..2026-11-20`, an inclusive calendar-date
filter, not seven complete rolling days. Its 78 returned workflow invocations
contain 39 successes, 31 failures, seven cancellations and one incomplete run.
All report attempt one. Counts do not establish expected-cadence completeness,
runner allocation/billing, failure causes or review throughput.

The failure distribution is crash diagnostics eight, installation context
five, purge eight, stale sweep one, Windows rotation one and ZDD eight.
The seven cancellations belong to Windows rotation; the incomplete run belongs
to installation context. These are retained observations, not qualified
platform results, accepted flakes, permission to stop testing or evidence that
a failing lane is redundant.

The first bounded helper trace follows the two daily stress callers. Both
declare a 15-minute job bound, but execute different obligations:

- `crash-diagnostics-stress.yml` runs Node 20 with
  `CONTEXT_HANDOFF_CRASH_DIAGNOSTICS_STRESS=1` and the complete
  `plugins/context-handoff/tests/crash-diagnostics.test.mjs` selection,
  separately on Linux and Windows.
- `zdd-singleton-stress.yml` installs the seasoned pytest dependency under
  Python 3.12 and selects `libs/zdd/tests`, exposing that package on
  `PYTHONPATH`, on Linux only. It does not set the PR smoke-only flag.

For actual scheduled head `bfb679c3061abf434f577ddd7705c121afd826f3`,
the two workflow blobs match the traced Source definitions. Run `44795927319`
has a successful Linux job `136495315296` and a failing Windows job
`136495316140`; run `44796810328` fails its Linux job `136498363752`.
In both failures, metadata identifies the suite-execution step, not setup,
as failed. Annotations provide exit code one and an action-runtime deprecation
warning, not a test assertion or causal explanation. Do not equate that
warning with the failure or generalize Linux success to Windows.

**Disposition:** the stress pair is not duplicate validation. Keep both
platform selections, full-suite flags and bounds. Combining their files while
retaining both daily cron expressions would still invoke a workflow twice;
coalescing the timestamps or serializing their jobs changes timing, contention
or isolation and needs a separately accepted contract. Neither is currently a
proven invocation reduction.

Repair ownership remains an open campaign item. The inspected #6142 is closed
for portable repair construction and explicitly leaves live activation behind
separate episode/authentication gates; it is not acceptance of these failures
by an active repair worker. Keep the observed failures and unresolved signatures
under #6237 and its existing remediation coordination rather than inventing a
new repair scheduler, rerunning stress/native work or marking the coverage
qualified. The other eight scheduled workflows still need their own bounded
helper/consumer closure; this roster and two-caller trace do not complete that
work.

The subsequent full-context **admission** trace resolves the largest declared
job-count ambiguity without executing a portfolio. In
`installation-context-full.yml`, 80 job conditions explicitly require
`workflow_dispatch`; only `exhaustive` admits the scheduled event. That job
sets `INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1` and runs the
`libs/installation-context/tests` selection under Python 3.12 with two xdist
workers and a 90-minute job bound. The other selector interfaces, including
the native Manager portfolios, remain manual and unchanged.

Actual run `44794812448` at head
`bfb679c3061abf434f577ddd7705c121afd826f3` has the same workflow blob
`bb92b61a730da4a63a0c26af1a376e0839fd6a09` as the traced Source. Its
job inventory contains 80 skipped jobs and one executing `exhaustive adapters`
job, `136491478749`, still in progress at the read. All 80 skipped records
have nonempty, distinct Check Run URLs; example `136491477228` is
`Bridge selected snapshots (Windows)`.

This identifies check-record overhead, **not** 80 runner allocations or
80 redundant test executions. A dispatch-preserving layout change might
remove those scheduled skipped records, but a simple file split could break
the current workflow filename/portfolio input, selected job names or receipt
consumers. A replacement must preserve those manual contracts and the
scheduled exhaustive selection before removing anything. No throughput gain,
workflow-invocation reduction, native result, owner transfer or authorization
to redesign the held selectors follows from this observation. Full test/helper
closure for this portfolio remains open.

Following that scheduled flag into Source distinguishes the daily execution
from the admitted unit packet, without collecting or running any test.
`test_installation_context_posix.py`, `test_installation_mode_governance.py`
and `test_snapshot_provenance.py` use
`INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS=1` to expand available adapter/
reference-runner matrices; the provenance module also enables its exhaustive
runner rows. Actual host availability still controls which adapters exist.
These declarations are not proof that every host or matrix row executed.

At Source `00e323313b8a7c49b7b5f7d5c04c61a129c6bdc3`, the
`tools/promotion-unit-manifest.json` profile remains
`copilot-extensions.promotion-units/v1`, with 5,710 nodes and the original
300-second bound. It contains zero nodes under
`libs/installation-context/tests/` and no source entries for that test
directory. Therefore the scheduled full-context selection cannot be retired
as a repeat of that declared unit packet. This is a bounded Source manifest
comparison, not an audit of every other CI consumer or a live qualification of
the later scheduled head.

### November 20 periodic Linux rotation caller/report closure

The census's `Windows coverage rotation` label is a workflow title, not a
Windows result. The traced `windows-coverage-rotation.yml` explicitly disables
its Windows `test` job with a literal false condition. Its active default
scheduled portfolio selects nine Linux plugins through
`tools/run_tests_in_devcontainer.py`. Native Windows container capacity remains
unenrolled; no capacity probe, enrollment or Windows execution is performed.

The `select` job resolves scheduled input to Dev, rejects newline-bearing ref
inputs, checks out once and emits an immutable commit SHA. Every Linux matrix
job consumes that SHA, so removing the selector job would lose the shared input
identity even though part of its Windows-only work is currently unused.
Manual focused scopes are mutually exclusive and separate from the default
portfolio. Each Linux job has a 30-minute bound, invokes the declared hardened
container wrapper without a test expression for ordinary scheduled runs, and
retains execution logs, outcome, scope, profile/inventory hashes and owner
metadata even on failure. Default full invocation is not evidence that
explicit-tier, network, filesystem or native-platform prerequisites were
provided.

`tools/periodic-integration-inventory.json` names portfolio #1303 and
plugin/platform specialists as the repair roles, retains the unenrolled
residual families, and declares no promotion or required-PR dependency. The
report job routes genuine Dev selection or Linux execution failure to the
existing `periodic-linux-integration-failure` tracker. Manual non-Dev or focused
inspection cannot silently file the shared default-portfolio failure.

The one failed rotation in the bounded census is run `43472145295`, whose
workflow head is `22cb3487722e637f17d6f6b78b6c5b4973b6c8c1`. Its workflow
blob `fea91bb7cb383da7e1185f21f9c5bdaf592e8600` equals the traced Source.
Its actual job inventory contains successful selection, nine Linux plugin
jobs, one skipped Windows record and successful reporting. Agent Containers
succeeds; the other eight Linux plugin jobs fail in the container-invocation
step. This does not distinguish preparation failures from test assertions or
qualify unavailable tiers.

All nine retained artifact names identify Dev
`4af7895e9f14c1e7d196ef59a3a5c52db7dfaf8c`, not the workflow's Main head.
Open #7377 comment `6860150286` independently names that Dev input and exact
run, proving the existing failure-report consumer received it. Artifact
contents and individual test outcomes were not inspected, so neither the
names nor the report substitute for packet/content validation or a repaired
portfolio. No new failure issue, repair worker, rerun or #1303 Bash dependency
is introduced.

**Narrow unused-work candidate:** `tools/select_windows_rotation.py` computes
two Tier A picks and one Tier B pick. The workflow's `plugins` output has only
one consumer: the matrix of the literally disabled Windows job. The active
Linux matrix instead uses its explicit nine-plugin/default or focused input
list. Thus the current Windows-only calculation and its Python setup do not
select executing scheduled Linux work. Retiring that preparation would still
need to preserve the retained manual date/selector and future enrollment
contracts. It would not remove the necessary SHA-selection job or a workflow
invocation; any runner-time saving is unmeasured. Rank it below
the proven repeated-Source-CI event seam rather than presenting it as the Root
consolidation outcome.

**Disposition:** retain the Linux default-full portfolio, its immutable Dev
identity, artifact/reporting contract, explicit unavailable Windows state and
existing #7377/#1303 repair path. Complete the canonical wrapper's actual
selection/containment closure before proposing any coverage overlap removal;
workflow naming, one Linux success, failed suites and skipped Windows records
are not equivalence proof. Shared workflow ownership and every prior hold are
unchanged.

### November 20 periodic wrapper selection and containment closure

Continue the preceding caller trace at source
`130c8550beb4f477f3e3b2856f915f10b1217d2e`. The default scheduled call names
one plugin, not `--changed`, an exact-node selection, a functional profile or
an explicit-tier/host-state opt-in. The canonical wrapper and inner runner are
`tools/run_tests_in_devcontainer.py` (blob
`0178c78b0e25ca51b863b9caa57ddd7419aab945`) and
`tools/run-plugin-tests.py` (blob
`0d68750aa9d9b6b84a3798224c22e73e1ccfef13`).

| Boundary | Actual source contract to preserve |
|---|---|
| Materialization | Copy current bytes of tracked paths into a fresh per-invocation bounded volume, never bind the live checkout. Untracked files require an explicit opt-in. Build a credential-free Git object closure and reject ancestor-path escapes. |
| Outer isolation | The pinned test-isolation spec drops capabilities, forbids privilege escalation, uses a read-only root and bounded writable surfaces, and exposes no Docker socket. This is a Linux boundary, not Windows/native acceptance. |
| Preparation | Invoke the same runner with `--prepare-only` while networking is available. It prepares dependencies without invoking pytest or importing test modules/conftests. Test-file/profile/JUnit options do not reach this pass. A failure prevents the test pass. |
| Network transition | Inspect live network attachments, refuse host/shared namespaces or unknown shapes, and disconnect every attached network before pytest. Strip `--reinstall` after preparation so the offline pass does not deliberately rebuild the environment. |
| Default selection | Recursively expand `test_*.py` while honoring each suite's pytest `norecursedirs`. Preserve special sequential groups and partitions. The portfolio plugin skips T3/T4 without explicit admission; default-full is therefore not an exhaustive/native claim. |
| Inner containment | Use private mutable state roots, remove ordinary credential/host-profile inheritance, take the environment lease, and run source-command preflight plus process-tree/resource/deadline containment. Default mode does not select native-application or absolute-native qualification. |
| Outcome and teardown | Propagate preparation/test failure; a teardown failure makes an otherwise successful run fail. Preserve a primary failure while reporting secondary teardown errors. Remove only this invocation's container and volume; debug retention is explicit. |

The inner run retains item, group and plugin budgets plus process/memory/temp
caps; plugin-specific defaults are not one universal timeout. The workflow's
outer step deadline remains independent. No deadline, profile or admission
change follows from this audit.

The normal test pass calls dependency preparation again, but the existing
source/dependency-fingerprint cache decides whether anything must be rebuilt.
Two runner invocations are not two pytest executions: the online pass returns
before pytest, whereas actual collection/execution occurs offline. Deleting
that split or substituting `--collect-only` would change the network/import
contract, not remove equivalent validation.

The runner's optional JUnit mode requires a fresh plugin report directory and
a report from each successful group. The wrapper does not export those files
from its disposable volume; its default invocation returns status and streamed
output. The preceding workflow/report/artifact observations therefore remain
the evidence boundary. No retained artifact content, full collected-node
inventory, cause of the eight failed Linux steps, or repair is qualified here.

Existing source controls were read, not executed:
`tools/test_run_tests_in_devcontainer.py` covers prepare/disconnect/run order,
preparation failure, network refusals, offline-only options and teardown
precedence; `tools/test_run_plugin_tests.py` covers directory-aware discovery,
preparation without pytest, source-command refusal and budget accounting.
These controls explain intended regressions; their presence is not a new
test pass or live isolation proof.

**Disposition:** the default Linux integration portfolio is not interchangeable
with the positively admitted unit packet, explicit tiers, native Windows
acceptance or its own dependency-preparation pass. Retain the existing
#7377/#1303 failure ownership and every unenrolled residual. This closes the
bounded wrapper/default-selection source trace, not portfolio-wide equivalence
or a run/check reduction. No Docker/native test, workflow edit, rerun,
installation, shared repair or cleanup was performed.

### November 20 weekly test callers and inventory-only reduction candidate

The remaining three weekly test-labelled entrypoints do not execute equivalent
work. Their workflow blobs match the actual scheduled definitions observed
within the preceding fixed census:

| Workflow / observed run | Source contract and observation |
|---|---|
| Payload CWD / `44380974436`, head `3ff041fc383b182c2d00394db21cc8ac8022f4d9` | Blob `0eba3c9c9881b9b15a71502a31ffab8bb3ef550d`. Only routing-inventory job `135072791961` executes, from `19:28:57Z` to `19:29:00Z` on November 18. Its sole work step echoes that integration is unenrolled under #1303. Both Linux and Windows test jobs are literally disabled and skipped. |
| Handoff exhaustive / `43895708345`, head `793a56b6c93638c7cd1cedf47df55a68d2580e9d` | Blob `b6aa63268b9e7385ade07196cfe2ce20eb675c5f`. Linux Node 20 selects `plugins/context-handoff/tests/exhaustive/*.test.mjs` under ten minutes. Job `133398568876` and its suite step succeed. |
| Dispatch PowerShell race / `44164434445`, head `3d925e38178b01d23a3c38074c2306c254e631ee` | Blob `ef832923de8e925db56984fc5ca66badab869831`. Linux and Windows use the contained runner, set `AGENT_DISPATCH_RUN_PWSH_RACE_TESTS=1` and select the snapshot/activation modules under fifteen-minute job bounds. Jobs `134326600854` and `134326601239`, including both suite steps, succeed. |

These are provider job/step observations, not independently inspected test
reports, per-case skip counts or proof that all current Source cases ran.
Neither Handoff nor Dispatch pins a Dev ref in checkout. Scheduled default-
branch execution must not be relabelled the independently required scheduled
Dev-integration acceptance.

The Handoff source expands one exhaustive file. Its assertions include actual
temporary Git repositories, concurrent stale-lock contenders, foreign-token
preservation, PID/start-time distinctions, rebase/dirty-tree refusal and
settlement polling. It imports the production Handoff implementation rather
than implementing a Python unit substitute. This is a different selection
from the daily crash-diagnostics file and the nine Linux Python plugin suites.
Its old comment describing required JavaScript CI is not evidence that such a
current required job exists; the inspected current `ci.yml` has no Handoff
exhaustive selection.

Dispatch's selected modules skip when `pwsh` is unavailable. Three real
multi-process cases additionally require the workflow's opt-in: simultaneous
snapshot publishers, reuse during a held build lock and cross-version
activation ordering. Ordinary default-full Linux invocation does not set that
opt-in, so selecting the same plugin is not equivalent coverage. Preserve
real process boundaries, both platforms, availability/skip reporting and
existing item/group/plugin limits. No native test was run by this audit.

**Concrete invocation candidate:** Payload CWD's weekly schedule currently
allocates a runner only to repeat a static unenrolled-routing notice. Removing
that scheduled trigger, while retaining manual inventory and the disabled
native recipes plus the explicit #1303 gap, would target an invocation rather
than miscount skipped tests. The observed one-run job allocation is three
seconds; setup/queue/billing and future cadence savings are not established.
The inventory/notice consumer and portfolio owner must accept the replacement
before a shared workflow change. This is not permission to enroll missing
Linux/native work, erase its residual or make it required PR/release CI.
The [bounded interface request](https://github.com/ThomasMichon/copilot-extensions/issues/1303#issuecomment-7048613971)
is posted on the existing portfolio tracker; posting is not acceptance.

The retained `tools/test_payload_cwd_ci_scope.py` still describes removed
`test-runner-linux`/`test-runner-windows` CI jobs and an older standalone PR
shape. It is not current coverage proof or an executable recipe to restore
by habit. Its stale-source assertions and the unexecuted root-tools/native
contracts remain with the existing #1303 owner; no integration execution,
test rewrite or parallel repair assignment follows this finding.

### November 20 maintenance helpers, publication refusal and label backstop

The three remaining scheduled files perform distinct mutations or detection,
not substitute test validation. Source helper inspection is pinned to
`130c8550beb4f477f3e3b2856f915f10b1217d2e`; no maintenance helper was invoked,
including in dry-run mode.

| Workflow | Helper/consumer contract and observation |
|---|---|
| Module health watchdog | Main-only detection loads `check-module-size.py`, ranks tracked non-test Python by margin to its baseline/cap, and files at most the worst candidate within the threshold. Existing labelled issues deduplicate filing; lookup failure aborts filing without failing the script. It does not repair/decompose code or replace the changed-file size gate. Run `44778612400`, job `136435016352`, and `Detect and file` succeed; success alone proves neither a new issue nor a healthy whole corpus. |
| Purge consumed changefiles | Trusted default-branch YAML uses the release environment/token and checks out Dev. It reads Main's persisted promotion boundary and removes only changefile names present there. Promotion's independent `changefile_existed_at` exclusion already prevents reapplying those bumps, so housekeeping is not release qualification. Publication retains staged/final path scope, required-check waiting, final head pinning and the existing owner-controlled merge path. |
| Stale branch / label maintenance | Weekly branch deletion and hourly/event-driven exact label-policy reconciliation are separate jobs and concurrency groups. Main/manual/cron admission prevents the hourly label backstop from becoming an hourly branch-deletion sweep. The labels job checks out `github.workflow_sha`; its policy values remain private and are not read by this audit. |

The corresponding workflow blobs match the observed run definitions:
watchdog `59ec210af26769077a6b53a45040ebe1310f35ae` at run head
`27a9ed1388a4ce57a743f2032fca6e753cbb3dcd`; purge
`68363288fe33a0ef2b31616173f6765398db3058` at
`81d6874fce4eda5c0b1abcd4325c93935315615c`; and stale/labels
`3ee065ae9762650168ecd94b9bf73ee2828de099` at both
`793a56b6c93638c7cd1cedf47df55a68d2580e9d` and
`ddb05d27a2c171a70592d4ed6029f01290f85ef1`.
Explicit mutable Main/Dev checkouts in those definitions are not proof that
the helper bytes or tested source equal the workflow-run head. No checkout-log
or helper-byte qualification is inferred from this workflow-blob match.

Latest observed purge `44783630325`, job `136452574532`, fails at the purge
step. Its actual failure annotation names outstanding generated cleanup
PR #13117 and explicitly refuses another publication until the existing
cleanup owner resolves it without changing merge gates. This is a concrete
refusal signature, not proof all eight census failures have that cause,
current PR-state readback, authority to take over the PR, or permission to
disable required checks. Keep release/cleanup custody with #3336.

The census's failed stale run `43942833225` executes both jobs: labels
`133564576376` succeeds while branch sweep `133564576932` fails. Available
annotations give exit one and platform/action notices, not the underlying
branch-operation failure. Conversely, run `44739812373` executes only labels
`136300318063`; branch sweep `136300320187` skips. The latter is not a
successful branch-deletion run. Neither a green labels job nor an empty
policy-supported no-op reveals current private policy/configuration or proves
that any label was deleted.

The branch helper refuses incomplete PR/branch inventories, protects the
default branch and Dev, uses the latest same-repository PR record and expected
head OID, rechecks open PRs, and performs compare-and-delete with an expected-
OID lease. It only flags no-PR branches for human triage. The label helper
uses casefolded exact names, validates the full paginated inventory before
deleting, and redacts policy/backend details. These are separate ownership
and mutation contracts; consolidating their names or permissions would not
preserve them by itself.

Existing source controls were read without execution: watchdog lookup/ranking
controls, branch history/lease/error controls, the trusted pending-cleanup PR
guard, and label-policy/workflow-scope controls. No issue status, branch, label,
changefile, credential, protection, release or retained-resource state was
modified. Read-only run/annotation lookup and the bounded portfolio interface
request above are the only remote operations in this audit continuation.

**Disposition:** retain daily detection, the consumed-only cleanup refusal and
the weekly-branch/hourly-label distinction under their existing owners.
Repair acceptance for the branch failure and outstanding cleanup remains open;
no new remediation worker or cleanup execution is assigned. Bounded source
selection/helper traces now cover all ten scheduled declarations, but complete
per-case coverage, live mutation effects, repair acceptance, comparable
before/after measurements and cross-lane equivalence remain unresolved.
The inventory-only Payload CWD trigger is a concrete candidate awaiting
acceptance, not an implemented saving. Shared scheduling would require explicit
cadence, privilege, isolation and failure-owner preservation rather than merely
coalescing cron strings.

### November 20 managed review setup and retired trusted-CI entrypoint

The checked-in `copilot-code-review.yml` is a manual setup declaration with
the official `copilot-setup-steps` identity, empty permissions, literal hosted
`ubuntu-latest` and one bounded echo step. It has no checkout, dependency
installation or validation suite. Its blob
`e4e9b375d839f3b791c128739f59da3c9d6051a4` matches the observed Main snapshot
`80816e7a336eb9ba8b143b6d4311c2f0e1a8b9eb`. The native review service remains
a separate provider-owned execution path, not another name for source CI.

The bounded November 20 head query for the already-qualified #13603 head
`ec75a9572219489fb8c32640ab3309612e786b4e` returns four runs: source CI,
trusted lockdown, departed-source refresh and native review `44673903042`.
The latter reports event `dynamic` and path
`dynamic/agents/copilot-pull-request-reviewer`, not the checked-in setup
workflow path. It contains one successful job `136081056365` with label
`ubuntu-latest`. Its actual steps include the configured
`Complete minimal review setup` inside the managed job, between the provider's
user-configured-setup boundary steps. This is not a second setup-workflow run
in that head/date cohort.

The managed job is created at `04:21:56Z`, starts at `04:22:02Z` and completes
at `04:24:22Z`. `Processing Request (Linux)` spans `04:22:18Z..04:24:18Z`.
These give a six-second created-to-start interval and 140-second allocation
for one successful review. They do not measure initial review-request latency,
inference-only duration, provider throttling, quota availability or population
throughput. No review request, canary, runner-route change or capacity probe
was initiated by this lookup.

The subsequent source-CI metadata read closes another part of this same head's
ordering, not a new experiment. Run `44673870881`, attempt one, is created at
`04:21:38Z` for `ec75a9572219489fb8c32640ab3309612e786b4e`.
Units job `136080946976` and guards job `136080947236` both start at
`04:21:41Z`, three seconds after creation. They complete at `04:24:32Z`
and `04:25:20Z`, allocating 171 and 219 seconds respectively. Required
aggregate job `136082289661` is created at `04:25:20Z`, starts three seconds
later and completes at `04:25:25Z`. The three source-CI jobs therefore
allocate 392 reported seconds in parallel/sequential composition; run
creation to aggregate completion is 227 seconds, not their sum.

The guard job's admitted Python type-contract step spans
`04:22:14Z..04:24:04Z` (110 seconds); the whole-file strict ratchet takes
seven reported seconds, and the unchanged full source AST guard group spans
`04:24:12Z..04:25:00Z` (48 seconds). These step spans locate observed work,
not proof that its checks or scope are redundant. The unit qualification step
spans 145 seconds including its surrounding consumer work; the separately
recorded 5,710-case execution takes 128.663 seconds. Do not equate that
step/job span with test-only CPU time or infer a new reuse hit.

Fresh approval `5987470930` targets this head at `04:24:23Z`, 62 seconds
before the source-CI aggregate completes. Thus native review is not the last
of these two observed gates in this sample. Merge at `04:28:00Z` is another
155 seconds later, but that interval is not attributed to runner execution,
review throttling or a human wait. This does not inventory every independent
metadata/merge predicate or establish a controlled before/after improvement.
A bounded source-only investigation of repeated work inside admitted guard
consumers can proceed independently of the held workflow interfaces; no
typing/AST omission, new cache/admission rule, deadline change or workflow
edit follows from this timing observation. It is not a new prerequisite for
the higher-ranked echo-only trigger reduction or accepted-owner one-scan work.

The adjacent `trusted-ci.yml` now declares only `workflow_dispatch`, an
empty-permission hosted routing-notice job and no PR/schedule subscription.
Blob `9ae37ea076c2772e8f2768852a75a9d2f3d37a28` also matches that observed
Main snapshot. Thus the original ledger's proposed retirement of its legacy
automatic PR entrypoint is historical at this boundary, not a remaining
automatic-invocation reduction to implement again. This source/adoption
observation does not settle periodic integration's unenrolled residuals,
earlier failed captures or actual self-hosted execution.

The trusted-CI guard and its existing source controls retain the exact minimal
review setup/runner exception and reject unrelated self-hosted routes.
Controls were read, not rerun. Keep hosted review selection and the existing
#7122/#3336 activation boundaries. Deleting a manual declaration or changing
the review runner is not evidence of fewer redundant validations, additive
capacity or improved review throughput.

### November 21 admitted-type and changed-code boundary

The source-only follow-up binds the inspected checker to the qualified
`ec75a9572219489fb8c32640ab3309612e786b4e` input, rather than attributing its
110-second step to a newer recipe. The current source and that head have
identical blobs for `ci.yml`, `typecheck.py`, its project manifest, program
partitioner, changed-code selector and source/bootstrap/catalog view modules.
No checker, environment preparation, native fixture or performance probe was
executed by this trace.

The manifest declares 43 admitted strict projects and 33 pending projects.
Forty admitted projects target Python 3.10 and three target Python 3.11; each
declares Linux and Win32 analysis. Those are 86 declared project/target pairs
before any materialized-program expansion, not 86 observed executions or
interchangeable platform checks. The six declared requirements paths are not
a count of installed environments: `environment_path` already shares by
captured lock bytes, host interpreter version and host platform. `prepare`
uses a lease and prepared marker, and returns without reinstalling a matching
existing environment. Repeating its command is not evidence of repeating
dependency installation.

The two CI selectors also retain different admission. `--enforced` selects
non-pending projects; `--new-code` selects changed physical files, including
pending owners, and imposes whole-file strict checking plus existing complete
program expansions. The recorded base `8ed2adb46b77a5072150d19e0ca8cea9e8eebd06`
is the merge base of its pinned pair with `ec75a957...`. That static comparison
contains Python edits to `tools/ci_pr_unit_reuse.py` and
`tools/promotion_unit_tests/test_ci_pr_unit_lookup_timings.py`, both owned by
pending `repository-tools`. The admitted cohort does not replace their strict
ratchet. This source comparison is not a reconstructed event payload or a new
runtime observation of the exact selected MyPy targets.

Each checked program stages its own targets/configuration and preserves source,
directory, output-membership and import-provider checks around execution.
Independent/materialized consumers retain physical custody across program and
target boundaries. Ordinary MyPy cache paths are shared per environment,
analysis platform and target version; imported-source, guard-alias and payload
overlays explicitly disable that cache. Their source-view/custody distinction
must survive any proposed optimization, not become a cache-based omission.

**Disposition:** retain the admitted pass and changed-code ratchet, original
platform/version matrix, pending-owner accounting and freshness/refusal checks.
The 110/7-second step observations do not prove duplicate qualification or
identify per-project CPU cost. No persistent evidence cache, merged namespace,
parallel checker, admission change or workflow edit is selected. This closes
the proposed direct-deletion interpretation while leaving bounded source-only
repeated-work analysis available under the existing typing owner and normal
review; it does not add a prerequisite to the higher-priority trigger reductions.

### November 21 fieldless-visitor dispatch candidate

The full-source wrapper currently runs four independent guards concurrently;
the observed 48-second group span cannot be assigned to any one worker.
A bounded, read-only headless-guard profile locates most instrumented work in
visitor dispatch/traversal. Repeated enclosing-source extraction accounts for
only 69 calls and 0.01042 cumulative seconds, so a text cache is not selected.
The previously rejected shared-child-edge cache is not revived.

The source candidate specializes only an existing default generic binding for
AST classes with empty fields. Each visitor still owns its existing dispatch
table; there is no cross-file or persistent cache. Explicit leaf visitors,
custom generic handlers and a replaced standard handler retain their original
dispatch. Instance-specific children and malformed field metadata continue
through the captured generic fallback. Every file is still read and parsed;
the six detection rules, scope, allowances and ordered diagnostics are unchanged.
No subprocess, network, installation, tree or history work is added by the
production change.

One bounded ABBA prototype comparison preserves all 4,220 text reads, 4,122
parse inputs, 232 raw hits and ordered diagnostics. Individual whole-guard
samples are 51.598/49.374/33.236/34.819 seconds (original/candidate/candidate/
original); medians are 43.208 and 41.305 seconds, a local 4.4% reduction with
substantial visible host variance. This is not controlled Actions latency,
tail qualification, fewer pipeline runs or review-throughput evidence.

After pulling forward, the actual implementation preserves all 4,285 current
text reads, 4,185 parse inputs, 232 raw hits and zero ordered diagnostics against
the unchanged Dev guard. That single equivalence pair takes 48.866/37.755
seconds; its order/cache effects are not used as another speedup claim.
The real guard CLI passes with thirteen adopting plugins. All 118 focused
guard regressions, fatal Ruff checks and whole-file strict checks for both
declared analysis platforms pass. The new performance regression also rejects
the exact unmodified guard, rather than only testing the optimized implementation.

Five pure regression cases and the guard's source/coverage target are positively
admitted to the existing unit packet: 5,906 nodes, 967 sources and 411 coverage
targets, preserving every prior entry/digest and the 300-second ceiling.
The separate 113-case development suite remains unchanged. Local observations
alone do not satisfy the separate current-head source-review and complete
required Linux CI gates.
Clean-room provisioning and external native lifecycle execution are not changed
by this AST-only candidate and are not represented by these unit observations.
The separately tracked Windows packet gap (#13598) and all native/candidate/cleanup ownership
remain separate; no grant, waiver, Main activation or runtime rollout follows.

Source #13787 merges at `2026-11-21T01:56:40Z` as
`d72add0240b7cdd4929cb10c26eee4cac29bda50`. Approval `6001939540` targets the
exact published head `131b6edfc3c38fb73523492d7d67a54cc1fc661f`; complete
review/thread reads report no open findings. Required source CI `44852019392`
passes guards, the unit consumer and its required aggregate. Unit job
`136685335927` succeeds and uploads freshly executed evidence rather than
claiming a reuse hit. Trusted lockdown and source attribution pass independently.
The source merge-time verifier reads live policy/results and returns that exact
head before the managed, role-authorized merge; no failing CI is bypassed.
Closing references are empty and parent #6237 remains open.

The canonical parent is reread after merge and only this finite source item is
resolved. Release adoption, controlled whole-flow measurements, native
qualification and retained workflow/repair/cleanup ownership remain open.
The next source-only stretch is the existing unit-census/eligibility work, not
another guard benchmark or an unaccepted workflow change.

### November 21 original guard-unit admission

The original guard suite has 59 test functions and 113 expanded cases. Its
synthetic repository fixture redirects production roots but leaves the public
headless allowlist bound to the checkout. A contrary matching allowance supplied
only in memory makes an original spawn assertion fail. This is a fixture-input
dependency, not a production policy defect or a prohibited unit-auditor read:
the existing effect guard permits read-only public non-code inputs.

The source correction binds that fixture's allowlist to its own temporary
repository. One regression supplies the contrary checkout allowance in memory,
invokes the original spawn assertion and requires zero checkout-allowlist reads.
Removing only the fixture correction in memory makes that regression fail.
No actual checkout allowance is edited. Whole-file strict typing preserves
every original assertion and parameterization; its one dynamic class becomes
a typed factory with the same actual runtime base and constructor, not a
test-only substitute.

The packet adapter exports the exact original test function and fixture
objects, without copying assertions. Standalone and adapter execution each pass
all 114 cases. A separate development observation uses the real `UnitRun`
auditor, process/thread/sleep restrictions, temporary-only writes and coverage
contexts: all 114 cases pass, report no prohibited effect, and each executes
the production guard in its own call phase. The only observed repository code
reads are the guard, original suite and adapter.

Positive admission adds 114 exact nodes and two hash-bound source inputs:
6,020 nodes, 969 sources and the same 411 production targets. Every prior node
and source digest remains; profile and the 300-second budget are unchanged.
The existing release-helper contract still requires all 88 decision targets.
The focused observation is development evidence, not a complete promotion
packet or a helper-coverage waiver. Normal current-head Linux CI and source
review are still required. The Windows collection gap (#13598), release
adoption, native/candidate acceptance and all workflow/cleanup owners remain
separate. No workflow trigger, native grant, runtime or Main activation changes.

Source #13798 merges at `2026-11-21T02:49:58Z` as
`83a3973a982ed38f0791f133f5b9389e4a8c2434`. Copilot approval `6002353929`
targets exact published head `2f4064bf9277e284f2a3af6a959e7b0945c3c3cc`;
complete review/thread reads report no active findings. Required source CI
`44857441803` passes guards, the full unit consumer and the required aggregate;
unit job `136702833875` freshly qualifies the exact-input packet and uploads its
evidence. Trusted lockdown and source attribution succeed independently.
The source merge-time verifier reads live policy/results and returns that exact
head before the managed, role-authorized merge; no failing CI is bypassed.
Closing references are empty and parent #6237 remains open.

The full canonical parent is reread after merge; only this finite source
admission item is resolved. Continue the retained unit census and separately
accepted workflow-owner reductions. Every-release observation, comparable
whole-flow measurements, native/candidate acceptance and retained custody
remain open; source publication does not silently dispose of any of them.

### November 21 sibling guard-unit admission

The original tracking-write suite collects 92 cases. Ninety-one operate on
synthetic temporary Python files through the actual scanner; the remaining
`test_real_repo_checkout_is_clean` launches the real checkout CLI. The asyncio
suite collects 21 cases: twelve in-process analysis/listing/error controls and
nine CLI subprocess controls. These are execution boundaries, not classifications
inferred from subprocess words inside synthetic source text.

Two packet adapters export the original eligible test objects and asyncio's
original `guard` fixture, retaining all assertions and parameter identities.
Both original suites and both production scanners remain byte-identical.
The complete original-to-adapter collection map accounts for all 113 cases:
103 mapped and ten explicit native residuals left unchanged outside the packet.
No subprocess witness is stubbed, weakened or represented as a unit pass.

All 103 exported cases pass focused execution and a separate development
observation with the actual `UnitRun` auditor, process/thread/sleep restrictions,
temporary-only writes and coverage contexts. Every selected case executes its
own production scanner during the call phase, with no prohibited effect.
The observed repository Python reads are exactly the two scanners, two original
suites and two adapters. Both new adapters pass whole-file strict typing on the
configured Linux and Windows targets and fatal Ruff checks.

Positive admission adds 103 nodes, six hash-bound inputs and two production
targets: 6,123 nodes, 975 sources and 413 targets. All preceding 6,020 nodes,
969 source digests and 411 targets remain unchanged; profile, 300-second
budget and all 88 release-helper decision targets remain. Focused observation
is not full-packet qualification or native execution. Complete current-head
Linux CI and source review remain required. Every-release observation,
full-census acceptance, #13598 and the existing workflow/native/custody owners
remain separate; this source item changes no workflow or operational authority.

Source #13801 merges at `2026-11-21T03:09:14Z` as
`5ab627967b0a6a6555c9fbd680cc3b16c01292fc`. Copilot approval `6002454308`
targets exact published head `b14b22ad254223f0c9f662188c01d71e1736436d`;
complete review/thread reads report no active findings. Required source CI
`44859052187` passes guards, the full unit consumer and the required aggregate.
Unit job `136707943801` freshly qualifies the exact-input packet and uploads its
evidence; trusted lockdown, attribution and identifier enforcement pass
independently. The source merge-time verifier reads live policy/results and
returns the exact head before the managed, role-authorized merge. No failing
CI is bypassed. Closing references are empty and parent #6237 remains open.

The full canonical parent is reread after merge; only this bounded source
census item is resolved. Continue separately accepted workflow reductions and
the retained census. Native subprocess residuals remain unexecuted by this
packet; release observation, whole-flow measurements, native/candidate
acceptance and all retained custody remain open.

### November 21 install-contract guard-unit admission

The complete `tools/test_install_contract_guard.py` suite contains nine test
functions and 42 cases. They exercise lexical masking and escaping, persistent-
environment diagnostic ordering and line numbers, canonical-adapter boundaries,
and generated-versus-source scan paths. PowerShell and registry expressions are
synthetic input text, not executed commands. The production helper's unrelated
runtime-retention function is not invoked or represented as qualified.

The packet adapter exports the exact original test objects with their existing
parameterization and assertions. The original suite and production helper are
unchanged. Collection checks original-object identity for all 42 cases. A focused
development observation runs them with the actual `UnitRun` effect auditor and
coverage contexts: all pass, no prohibited effects occur, and every case reaches
the production helper in its call phase. Repository Python reads are exactly
the adapter, original suite and helper.

Positive admission adds 42 nodes, three hash-bound sources and one coverage
target: 6,165 nodes, 978 sources and 414 targets. All preceding 6,123 nodes,
975 source digests and 413 targets remain unchanged, as do the profile,
300-second packet budget and all 88 release-helper decision targets. The
adapter passes whole-file strict checks on Linux and Windows targets.
Focused development evidence is not complete-packet qualification; normal
current-head Linux CI and review remain required. This is a bounded census
increment, not fewer workflow invocations, broader install-contract integration,
native execution, every-release observation or full-census acceptance.
The ten previously retained sibling-scanner native cases, #13598, workflow-owner
decisions and all native/candidate/custody holds remain separate and unchanged.

Source #13808 merges at `2026-11-21T03:38:58Z` as
`cc8196e947b856f7ccb5150974054ae8edc59776`. Copilot approval `6002598629`
targets exact published head `5242e36f0530b679c293b08c6292fa6eab970372`;
complete review/thread reads contain no active findings. Required source CI
`44861624869` passes, including unit job `136716078749` freshly qualifying the
exact-input packet from `03:32:41Z` to `03:35:25Z` and uploading its evidence.
Trusted lockdown, attribution and identifier enforcement pass separately.
The source merge-time verifier performs seven live scoped reads and returns
the exact head before the managed, role-authorized merge; no failing CI is
bypassed. Closing references are empty and parent #6237 remains open.
After rereading the full canonical parent, only this bounded source census
item is resolved. Continue the retained census and separately accepted workflow
reductions; every-release, broader integration, native/candidate and whole-flow
acceptance remain open.

### November 21 configuration guard-unit admission

Six complete original suites collect 92 cases. Exact-original adapters map
89: feed neutrality 20, runtime resolution 19, action pins twelve, compiler
version ten, package-tree exclusion seven and version consistency 21.
The two workflow-directory fixtures are exported unchanged. Collection compares
every original parameter identity with its adapter and checks original test/
fixture object identity.

Three original cases remain outside the packet: the feed scanner's fixture
that writes a synthetic `.git` path, its actual whole-checkout scan, and the
package-tree guard's real CLI subprocess smoke. The actual auditor refuses the
Git-path fixture; its input and assertions are preserved, not renamed or
weakened to admit it. No positive-admission rule is changed. The earlier ten
sibling-scanner native subprocess residuals remain unchanged as well.

The other cases operate on synthetic text or temporary fixture files through
the real production guards. All 89 pass the actual `UnitRun` effect auditor,
with zero prohibited effects and each case reaching its own guard during its
call phase. Repository Python reads are the six adapters, six original suites,
six production guards, and the already hash-bound standalone-consumer helper
and Chat initializer. The latter is read as fixture data, not imported or
executed. All original suites and production files remain unchanged.

Positive admission adds 89 nodes, eighteen source hashes and six targets:
6,254 nodes, 998 sources and 420 targets. All 6,165 current-base nodes, 980 source
digests and 414 targets remain, with the same profile, 300-second budget and
all 88 release-helper decision targets. All six adapters pass whole-file strict
typing on Linux and Windows targets and fatal Ruff checks. Focused audited
development execution is not complete-packet qualification; normal current-head
source review and full Linux CI remain required. This is a bounded census
increment, not fewer workflow invocations, broader/native execution,
every-release observation or full-census acceptance. #13598 and the existing
workflow, native/candidate and retained-custody owners remain separate.

The first hosted head `71d0bc765ea9e4bfd58ebc838b8740dfde06404e` passes
full unit qualification, but both source and trusted guards correctly reject
the four-space-indented manifest at 1,049,359 bytes against the unchanged
1,048,576-byte cap. Two-space serialization reduces it to 1,018,663 bytes.
Direct comparison proves identical decoded contents and canonical manifest
digest; every node, source hash, target, budget and helper obligation remains.
This fixes the caused guard failure without a size exemption, reduced coverage
or altered eligibility. The changed head still requires fresh review and
complete required CI.

Concurrent Dev changes subsequently advance three existing admitted source
hashes: Worktrees resource relationships, the native process-runtime archive
and the native versioned-runtime script. The safe backed replay retains every
current Dev hash and all 89 owned additions, with no changed original guard
test or helper. The only replay conflict is the manifest's indentation change;
its resolution uses the current-base admission plus the owned additions, then
two-space serialization. Counts, limits and file size remain unchanged.
Prior-head review and CI do not qualify these new complete packet inputs;
fresh exact-head review and full required CI remain mandatory.

Another concurrent Dev change updates seven existing source hashes, including
the imported standalone-consumer helper, and adds two Fleet source inputs
without changing the original node/target inventories or limits. The second
backed replay preserves those inputs and all 89 additions. The batch is
re-observed against that actual dependency: all 89 cases pass with no prohibited
effects and own-guard call coverage, reading the same twenty repository files.
No prior complete-packet result is reused as current qualification.
To avoid repeating source-map whitespace conflicts, final compaction changes
only the node-list indentation; source-map and other field layout remain as
on Dev. The manifest is 1,024,599 bytes with identical decoded admission to
the current-base union. The cap, all 88 helper decisions and the 300-second
budget remain enforced. This is not a new schema or eligibility exception.

Source #13814 merges at `2026-11-21T04:31:32Z` as
`cfd97a8ba7ca632ed771164d92f18275ee5da010`. Final approval `6002791220`
targets exact head `4de6887b736345a6e7864c8f1babf692355b6849`;
complete review/thread reads contain no active findings. Complete required
source CI `44865853693` and trusted metadata CI `44865854246` pass after the
description's source marker is repaired through `attribution-prepare`.
That repair changes no source head. Unit job `136729216136` freshly qualifies
the packet from `04:24:12Z` to `04:27:26Z`, then uploads its evidence. A
provider-lookup-budget warning correctly falls back to fresh execution; it is
not credited as a reuse hit or as attribution of an earlier lookup failure.
Seven live scoped reads through the source merge-time verifier precede the
normal managed Maintainer merge. No failing CI is bypassed.

Closing references are empty and parent #6237 remains open. The upstream
merge retains all 89 owned nodes, eighteen added source digests, six exact
adapter blobs and every coverage target; all prior replay heads remain in
recovery refs. The full canonical parent is reread after merge and only this
bounded admission item is resolved. Continue the retained source census and
separately accepted workflow reductions; broader integration, native,
every-release, whole-flow and retained-custody acceptance remain open.

### November 21 Picker inbox guard-unit admission

The original Picker inbox-discipline suite collects 32 cases in 31 test
functions. Its adapter exports the exact original functions and `repo`
fixture; collection compares every parameter identity and function/fixture
object. All original assertions, fixture behavior and production code remain
unchanged. The inputs are synthetic Python text in temporary fixture trees.
The guard performs real AST analysis of aliases, closures, branch joins,
tuple/walrus bindings, suppression comments, syntax errors and file listing.
The two argument-handling cases call the actual entry point in-process;
thread and Picker expressions in fixture text are not executed.

All 32 pass the actual `UnitRun` effect auditor with zero prohibited effects,
and every case records call-phase coverage in the real guard. The only
repository Python reads are the adapter, original test module and production
guard. Focused development execution takes 2.844 seconds, not a canonical
complete-packet timing. The adapter passes whole-file strict typing on Linux
and Windows targets and fatal Ruff checks.

Positive admission adds 32 nodes, three source hashes and one target:
6,286 nodes, 1,001 sources and 421 targets. Every current-base node, digest and
target remains; profile, 300-second budget and all 88 release-helper
decisions are unchanged. Existing node-only compaction and source-map layout
are preserved under the unchanged file-size cap. Normal current-head review
and full Linux source CI remain required. This is a bounded census increment,
not fewer workflow invocations, native Picker behavior, every-release
observation or full-census acceptance. #13598 and the workflow, native/
candidate and retained-custody owners remain separate and unchanged.

Source #13826 merges at `2026-11-21T04:54:21Z` as
`082cc04be63bc7d255a39eff3cd6e4428acfffa2`. Copilot approval `6002925775`
targets exact published head `e2b6b6718ff017db34afea5c381f65a2cc17db84`;
complete review/thread reads contain no active findings. Complete required
source CI `44867950529` and trusted metadata CI `44867950786` pass.
Unit job `136735766588` freshly qualifies the complete packet from
`04:49:55Z` to `04:51:53Z`, then uploads its evidence. Seven live scoped reads
through the source merge-time verifier precede the normal managed Maintainer
merge; no failing CI is bypassed. Closing references are empty and parent
#6237 remains open. After rereading the full canonical parent, only this
bounded admission item is resolved. Continue the retained census and separately
accepted workflow reductions; broader integration, native, every-release,
whole-flow and retained-custody acceptance remain open.

### November 21 temporary-document guard-unit admission

The effort/vision structure and marketplace-isolation suites collect 26
original cases. Two thin adapters export 24 exact original functions and
parameter identities: seven structure cases and seventeen isolation cases.
The actual rewritten-history Git test and whole-checkout capability inventory
remain unchanged outside the packet. No original assertion, fixture or
production guard changes.

Selected cases create temporary fixture trees and exercise actual document
schema checks, lexical/AST detection, path selection and in-process argument
handling. Shell, PowerShell and JavaScript snippets are parsed as data, not
launched. All 24 pass the real `UnitRun` effect auditor with zero prohibited
effects and actual call-phase coverage in each case's own production guard.
Each selection opens only its adapter, original suite and guard. Focused
development observations take 1.032 and 1.641 seconds, not canonical packet
or native timing.

Positive admission adds 24 nodes, six source hashes and two coverage targets:
6,310 nodes, 1,007 sources and 423 targets. Every preceding node, source digest
and target remains. Profile, 300-second budget, all 88 release-helper
decisions, node-only compaction and the file-size cap are unchanged. The
manifest is 1,033,116 bytes. Both adapters pass whole-file strict typing on
Linux and Windows targets and fatal Ruff checks. Complete current-head
review and Linux source CI remain required. This is a bounded census
increment, not fewer workflow invocations, a full-checkout inventory pass,
native execution, every-release or whole-campaign acceptance. Existing
workflow, native/candidate, #13598 and retained-custody gates remain open.

Source #13829 merges at `2026-11-21T05:06:21Z` as
`2fcfa5c63009b5cf6ec5472716cb6ea051878fdc`. Copilot approval `6002966108`
targets exact published head `36f44287a827ed7b4aca822e5331011a651a097f`;
complete review/thread reads contain no active findings. Complete required
source CI `44868828120` and trusted metadata CI `44868828264` pass.
Unit job `136738514320` freshly qualifies the complete packet from
`05:01:25Z` to `05:03:24Z`, then uploads its evidence. Seven live scoped reads
through the source merge-time verifier precede the normal managed Maintainer
merge; no failing CI is bypassed. Closing references are empty and parent
#6237 remains open. The full canonical parent is reread after merge and only
this finite admission item is resolved. Continue the retained census and
separately accepted workflow reductions; broader integration, native,
every-release, whole-flow and retained-custody acceptance remain open.

### November 21 vendored-library guard-unit admission

The original vendored-library synchronization suite collects nineteen cases.
A thin adapter exports eighteen exact original cases and their `fake_repo`
fixture. Collection verifies every parameter identity and test/fixture object.
The native symlink case remains unchanged outside the packet; neither its
fixture nor its refusal assertions are replaced by an in-memory imitation.
All original tests and production helpers remain unchanged.

Selected cases create temporary library/consumer trees and exercise actual
TOML parsing, editable-reference inventory, artifact-only exclusion, source-byte
and version comparisons, pointer peers, same-consumer collisions and in-process
list/CLI diagnostics. No install, materialization, source rewrite or native
process executes. All eighteen pass the actual `UnitRun` effect auditor with
zero prohibited effects and their own production-guard call-phase coverage.
Repository Python reads are the adapter, original test module, guard and the
already hash-bound editable-reference, artifact-inventory and standalone-
consumer helpers. Focused audited development execution takes 2.531 seconds;
this is not a canonical complete-packet timing.

Positive admission adds eighteen nodes, three source hashes and one target:
6,328 nodes, 1,010 sources and 424 targets. Every preceding node, source digest
and target remains. Profile, 300-second budget, all 88 release-helper
decisions, node-only compaction and the file-size cap are unchanged. The
manifest is 1,036,181 bytes. The adapter passes whole-file strict typing on
Linux and Windows targets and fatal Ruff checks. Complete current-head review
and Linux source CI remain required. This is a bounded census increment, not
fewer workflow invocations, native symlink/installation qualification,
every-release or full-census acceptance. Existing workflow, native/candidate,
#13598 and retained-custody gates remain open.

The separately inspected trusted-CI suite reads original repository YAML
fixtures and live workflow declarations. Those are not admitted Python or
declared build-data inputs under the current unit source contract. It remains
unchanged and outside this admission; no fixture rewrite, new data exception
or inferred eligibility is used to export it.

### November 20 path-gated audit-guard native boundary

The newer `unit-effect-guard-native.yml` is not the ordinary unit packet or a
scheduled exhaustive lane. Its Dev-PR trigger lists exactly its workflow,
`promotion_unit_effects.py`, the pure `test_effects.py` controls and
`test_promotion_unit_effects_native.py`; it also supports manual dispatch.
Two fixed hosted platform jobs check out the explicit PR head/workflow SHA
without persisted credentials. Each six-minute job runs the two control files
inside a caller-owned `Limits(wall_seconds=120)` boundary, parses JUnit to
require the named interleaving case with no error/failure/skip, and rejects
retained or still-present sandbox state after cleanup. This is not permission
to widen any other native lane or alter required CI.

The production guard stores its relative-open path stack in `threading.local`.
The native test deliberately interleaves an outside and inside open with the
same spelling but different directory descriptors, nests another open and
asserts that only the outside write is refused and both thread-local stacks
settle. Ordinary `run-promotion-unit-tests.py` explicitly refuses both
`threading.Thread.start` and `_thread.start_new_thread`; its current manifest
contains 26 pure effect-control nodes and no native thread-control node.
That is a static packet-membership difference, not another packet execution.
Do not delete the real interleaving simply because both lanes mention units
or share the pure effect-control file.

Workflow blob `0775dd686a43ce7c51359beb2fa08ba6d7f1d6f6` matches both the
observed Main snapshot `80816e7a336eb9ba8b143b6d4311c2f0e1a8b9eb` and actual
PR run `44313200971` at head
`495b840f2c93e4a9c756a52fac3759c372b8560d`. Its Linux and Windows jobs
`134832393516` and `134832393221` report successful qualification steps.
The bounded run-list query reports twelve PR invocations in the same inclusive
November 13-20 date filter, returning the latest five; no complete outcome
distribution, repeated-input equivalence or measured saving is inferred.
The read current Source helpers are not independently compared with that
earlier run's helper bytes, so this is not fresh native qualification of
current Source or any held Manager/installer profile.

**Disposition:** retain the four-path event scope, actual interleaving and
cleanup/no-skip assertions, both platform routes and original limits.
The shared pure controls are not alone justification to remove the unique
native obligation or broaden the actor-free unit tier. No run, rerun,
dependency installation, profile change or implementation followed this
read-only trace.

### November 20 manual PR-replay selection

`pr-rebase-full.yml` remains manual-only in Source and the observed Main
snapshot; both use blob `795dd46b2cdb1d2cea207092c583a389b609eecf`.
It declares separate Linux/Windows jobs, each bounded to 35 minutes. The
contained Worktrees runner receives the literal owned-replay/recovery/rewrite/
publication `-k` expression, `--allow-explicit-tiers`, both exhaustive-rebase
and native-headless environment opt-ins, and explicit 1,800-second group and
plugin bounds. Preserve that selection rather than inferring every test in
the repository from the word `full`.

The selected source includes real bare-Git publication/lease/replay controls,
foreign-head and shared-default refusal, protected history/backups and stale
or concurrent publication rejection. The Windows-only headless control
separately requires two consoleless-parent observation cycles with actual
descendants, no new/owned visible windows or foreground transitions, and no
observer errors. Ordinary default-full selection lacks these opt-ins.
Reading that source is not authorization to run its native helper, rebase a
live PR, touch any retained history or repeat a held capture.

The bounded workflow-run query for November 13-20 returns zero records.
This proves only that query's observed absence, not that the lane never ran,
is disposable, is currently qualified or has an accepted replacement. A
manual-only definition with no run in that cohort supplies no demonstrated
automatic invocation to remove. No replay, native observation or dispatch
was performed.

### November 20 current declaration delta

The original October 18 pinned inventory remains a dated baseline, not the
current roster. At Source `1480e24ab31a77c0f62821b7fd4c5670b74f9ffd`, Git lists
33 workflow YAMLs plus the separate generated-workflow Markdown source,
versus the baseline's 21 YAMLs. The twelve added declarations have five
path-gated PR entrypoints, five path-gated `pr/**` push entrypoints and two
manual-only controllers; each also supports manual dispatch. This is a
source trigger inventory, not Main adoption, job expansion, actual execution
or assurance/equivalence proof. The added native guard is traced above;
the other eleven have the bounded first-hop map below, not complete product
implementation or live qualification.
No new scheduled declaration is inferred, and none of these counts is a
claimed reduction or license to change another owner's lane.

An independent directory/blob metadata read of observed Main
`80816e7a336eb9ba8b143b6d4311c2f0e1a8b9eb` also lists 33 YAMLs and the same
Markdown source. All twelve additions have matching workflow blobs there.
Across the complete roster, only `base-branch-reminder.yml`, `ci.yml` and
`validate-and-promote.yml` differ from this Source. This establishes declaration
presence/identity at that dated Main snapshot, not current live branch state,
helper/candidate compatibility, workflow execution or installed rollout.
Do not silently substitute Source control bytes for those three different
Main-owned definitions.

### November 20 added-workflow first-hop map

The bounded evidence pass reads the remaining eleven added workflow bodies at
Source `1480e24ab31a77c0f62821b7fd4c5670b74f9ffd`, then at most one helper
interface hop. The earlier parent traces own the ordinary contained runner.
The table cites workflow-relative line ranges for the selectors and trust/
evidence boundary. It reports declarations, not observed allocated jobs.

| Workflow under `.github/workflows/` | Admission, platform and first-hop contract |
|---|---|
| `bootstrap-update-native-acceptance.yml` (53-319) | Path-gated Dev PR or manual; seven literal Linux/Windows jobs, including a Windows packet matrix and opt-in full-tree promotion copies. Explicit Manager bootstrap/update/input/provider/native files and prepared-Python handoff controls use the contained runner. Five-minute job bounds and source-byte assertions remain; inline copy qualification selects `push=False`, not a release publication request. |
| `chat-container-qualification.yml` (23-65) | Path-gated PR or manual immutable candidate/base input; one Ubuntu job, twenty minutes. `run-standalone-tests.py services/chat --prepare --chat-suite acceptance --timeout 180` precedes `qualify_chat_container.py --python-base ...`. Digest-pinned base input and checkout validation are source contracts, not actual image/readiness proof. |
| `historical-cleanup-native-acceptance.yml` (26-113) | Path-gated `pr/**` push or manual; three Linux/Windows jobs, twenty minutes. Explicit Worktrees historical-daemon/native/lazy-dispatch files, Manager historical command/runtime/provider/retirement/cleanup files and `tests/native/test_historical_index_registration_native.py` are selected. These are fixture/native qualification declarations, never permission to clean this host or retained Index state. |
| `legacy-monitor-native-acceptance.yml` (37-96) | Path-gated `pr/**` push or manual; two Linux/Windows jobs, twenty minutes. Explicit anchor, installer-return, monitor retirement/preparation/grace/completion/handover selections retain file filters and a Windows-only native cutover step. Source-native monitor tests do not authorize a live monitor replacement. |
| `maintainer-pr-completion.yml` (21-48) | Manual-only, one literally disabled `reconcile` job, five minutes. Trusted workflow-SHA checkout and read-only contents/PR/issues/check permissions; helper selects `maintainer_pr_completion.py --report-only`, optionally `--pr`. A helper's separate `--apply` capability is not selected or activated. |
| `mcp-owner-exec.yml` (82-352) | Path-gated `pr/**` push or manual; Linux/Windows jobs, fifteen/ten minutes. Explicit Remote Driver headed/session-host, process suspended-handoff, ZDD private-Windows and MCP execution/custody/generation/native tests use the contained runner. Client preparation pins download hashes; JUnit/named artifacts retain seven-day evidence. Names containing `owner` describe execution custody, not an accepted repair claimant. |
| `owned-budget-native-acceptance.yml` (16-71) | Path-gated `pr/**` push or manual; Linux/macOS jobs, fifteen minutes. Inline containment selects `libs/agent-procutil/tests/test_owned_budget_native.py`; Linux additionally selects the nested repair-probe timeout/reaping control. This is the `Borrowed budget native` job family, not a new grant to rerun an earlier held failure. |
| `prepared-membership-native.yml` (32-184) | Path-gated `pr/**` push or manual; three literal jobs, including a Windows packet matrix. Explicit managed-session/pre-policy/shell-arguments/post-tool/IPC/status/transport selections qualify prepared membership, fallback, patch/write-policy and selected-cell production under ten-minute job bounds. Similar policy names do not prove packet overlap or authorize policy/runtime repair here. |
| `python-selection-native.yml` (19-77) | Path-gated Dev PR or manual; one Windows job, five minutes, with Python 3.9/3.12 tooling. The contained runner selects installer admission and daemon Python-selection files with `-k real_python39`; XML requires exactly two admission and four daemon cases without errors/failures/skips. It does not qualify every older runtime or change their admission policy. |
| `windows-qualification.yml` (18-144) | Typed manual request; Ubuntu admission and Windows execution, with separate immutable controller/candidate checkouts. Actor and triggering actor must match the repository owner on protected default-branch control. `windows_qualification_worker.py` handles `admit`, `execute`, `sanitize`; request-specific packet expansion is a later contract frontier, not resolved by one helper-interface read. Seven-day sanitized artifacts and the private index URL's name are visible; no URL value, request execution or capacity is obtained. |
| `worktrees-roster-native.yml` (26-82) | Path-gated PR or manual; Linux/Windows jobs, twenty minutes. Explicit Worktrees client/feed/runtime/recovery files and Manager current-roster/resident-transport files select the cold-roster boundary. The workflow adds no separate retained artifact/report step; a source selection is not a current successful roster result. |

Most test checkouts use the event/PR head SHA without persisted credentials;
the report-only controller pins the workflow SHA, and the typed Windows
controller separately admits its candidate. Literal conditions, matrix
selectors, environment opt-ins, limits and result checks remain part of the
contract; a shared runner/helper path is not equivalent validation.
In particular, bootstrap's seven literal jobs expand to at most nine with its
three-packet Windows matrix, including the gated promotion-copy job.
Prepared membership declares thirteen Windows packets plus two non-matrix
jobs, at most fifteen expanded jobs. These are maximum configuration shapes,
not observed check records, allocations, test executions or current coverage.

None of these eleven inspected workflow bodies names a failure-repair claimant
or tracker. This is a source-declaration frontier, not proof no domain owner
exists and not a transfer away from the existing component, portfolio, release
or native owners. Repository-owner admission for a manual controller is not
accepted failure custody. Correlate actual failures and accepted tracked
ownership before closing the repair obligation; do not spawn a parallel repair
worker or edit the workflow merely to manufacture an owner field.

The inspected display names/job IDs do not match `PR gate (required check)`,
`workflow-lockdown-guard` or `source-attribution` from the independent live Dev
context readback below. This does not make the checks dispensable, waive
contribution policy, or prove that existing source CI never invokes related
actor-free controls. No test, collection, native/helper execution, dispatch,
rerun, image build, installation or cleanup was performed by this source map.

### November 20 required-context readback

The read-only Dev classic-protection response requires `PR gate (required
check)`, bound to app 15368, with non-strict status freshness. The active
Dev workflow-lockdown ruleset `24152679` separately requires
`workflow-lockdown-guard` and `source-attribution`, also non-strict.
Other active Dev PR/review rulesets retain the separate approval/CODEOWNER
contract; `24069919` specifies one approval and CODEOWNER review. The proposed
separate CODEOWNER/completion-App layer `25505838` remains disabled. Classic
review fields were null, not evidence that the separate review rule vanished.

The default-branch ruleset `18553911` independently requires strict
`main source gate` freshness. The branch-creation rule is separate again.
This read mapped context and ref applicability, not bypass-actor eligibility,
the complete author/reviewer contract or permission to merge/activate. A clean
unit or native qualification result cannot substitute for these independent
contexts, source review, accepted feedback or the resolved role. No rule,
approval, environment, token or merge state was changed.

### November 20 environment and credential-binding metadata

The repository-scoped read-only API capture lists exactly two environments.
`copilot` has an empty `protection_rules` list and null deployment-branch
policy. `main-promotion` has only a `branch_policy` protection rule, with
`protected_branches: true` and `custom_branch_policies: false`. Neither lists
a required-reviewer or wait-timer rule. Both report `can_admins_bypass: true`;
this field does not identify an eligible actor or grant this campaign bypass
authority. Protected-branch admission is not a literal Main-only allowlist:
the environment name alone cannot establish that narrower boundary. Keep
workflow event/ref/trust checks and branch rules as separate contracts.

The secret-name endpoints return six repository entries and one environment
entry. No secret value was requested or returned. The `copilot` environment
has no listed secrets; `main-promotion` lists only `APERTURE_RELEASE_TOKEN`.
Repository and both environment variable-name lists are empty; no variable
values were printed.

| Listed binding | Declared workflow consumer frontier |
|---|---|
| Environment `APERTURE_RELEASE_TOKEN` | `promote-trigger.yml` / `refresh-module-baseline`; `purge-consumed-changefiles.yml` / `purge`; `validate-and-promote.yml` / `promote`, `publish-python-artifacts`, `record-promotion`. All five jobs explicitly bind `main-promotion`. |
| Repository `COPILOT_GITHUB_TOKEN` | Generated `ci-failure-fix-attempt.lock.yml` activation, agent and detection declarations. |
| Repository `MAINTAINER_REVIEW_PAT` | `workflow-lockdown-guard.yml` / `request-review`. |
| Repository `FORBIDDEN_IDS_FACILITY`, `FORBIDDEN_IDS_WORK` | Identifier-scan declarations in `base-branch-reminder.yml`, `promote-trigger.yml` and `workflow-lockdown-guard.yml`. Values remain deliberately unread. |
| Repository `FORBIDDEN_LABELS` | `stale-branch-sweep.yml` / `labels`; private policy content remains unread. |
| Repository `WINDOWS_QUALIFICATION_INDEX_URL` | `windows-qualification.yml` / `windows`; presence does not qualify the index, platform or native result. |

This is a parsed declaration/reference map over the current 33 YAML files,
not an execution or proof of effective credential delivery. No YAML job
declares the `copilot` environment. Optional generated declarations also
reference `GH_AW_DEFAULT_OTLP_ENDPOINT`, `GH_AW_DEFAULT_OTLP_HEADERS`,
`GH_AW_GITHUB_TOKEN`, `GH_AW_GITHUB_MCP_SERVER_TOKEN` and
`GH_AW_CI_TRIGGER_TOKEN`; none appears in the queried repository/environment
secret-name inventories. That observation does not prove a missing runtime
credential or a failing path: conditional use, fallback and external/runtime
configuration remain distinct. The automatic `GITHUB_TOKEN` reference is not
a missing user-configured secret.

The earlier dated Main snapshot `80816e7a336eb9ba8b143b6d4311c2f0e1a8b9eb`
already matches the other workflow blobs. For the three source/Main blob
differences, an additional read-only comparison confirms equal parsed
workflow-permission, environment and secret-reference signatures:
`base-branch-reminder.yml`, `ci.yml` and `validate-and-promote.yml`.
This closes that binding-metadata discrepancy only, not behavioral equivalence
of those differing workflow bodies or a claim about a newer moving Main.

The repository Actions policy reports Actions enabled, `allowed_actions: all`,
`sha_pinning_required: false`, default workflow permission `read` and
`can_approve_pull_request_reviews: true`. Explicit workflow/job permissions
remain separate: the metadata/maintenance/generated repair jobs declare
selected write scopes, and `publish-python-artifacts` declares
`attestations: write` and `id-token: write`. In particular, a read-scoped
`GITHUB_TOKEN` declaration does not bound the independently supplied release
token or review PAT. Secret names and update timestamps do not prove their
actual principal, token kind, scopes, validity, custodian, rotation owner or
authorized use. Those facts remain unknown where the existing owner/evidence
does not establish them; no credential probing or repair was performed.

No protection, permission, token, workflow, runtime or deployment was changed.
This dated readback completes the bounded environment/name-binding audit, not
Phase 0's complete in-flight release snapshot, failure-owner acceptance,
native qualification or permission for Main activation or shared integration.

### November 20 in-flight release-control snapshot

The read-only repository-wide active-state inventory ran at
20:03:17-20:03:39 UTC, followed by direct workflow inventories at
20:04:05-20:05:14 UTC and job/artifact metadata at 20:05:34 UTC. It requested
`queued`, `in_progress`, `waiting`, `pending` and `requested`, with page size
100 and pagination, for `validate-and-promote.yml`, `promote-trigger.yml`
and `purge-consumed-changefiles.yml`. These sequential reads are not an atomic
provider snapshot and do not claim later settlement.

| Observed release-control identity | State at capture |
|---|---|
| Promotion run `44806469374`, attempt 1 | `repository_dispatch`, Main controller SHA `42b96cbf8dbfd9fcdf14d3477bd6cd8009d3928a`; in progress, created 19:54:24 UTC. This controller SHA is not proof of the frozen Dev/candidate identity. |
| Trigger runs `37642403020` and `37642383755`, attempt 1 | Reported queued since October 7 at 15:12:08 and 15:09:04 UTC, both Main SHA `b9fed24456b79d8c5236ebc7022404c5858bba53`. Age alone does not establish whether these are executable work, stale provider records or safe cancellation candidates. |
| Direct purge workflow responses | Zero returned runs and zero totals for all five requested active states. This does not resolve its earlier failed publication or existing cleanup-PR custody. |

The promotion's six returned jobs match its job total: admission job
`136535518179` and candidate-freeze job `136536036272` succeeded; source units
`136536609478`, Windows Resume `136536609494`, unsigned artifact construction
`136536609511` and installed Manager TTY `136536609516` were in progress.
The two listed artifact records are `14664122217` /
`frozen-promotion-candidate` and `14662549743` /
`promotion-admission-45e0996fd6f638a840120c1205c2d4c91146408e-22e299a9d84ceb8800b02f9996e0c21e35d9663b`.
Names/existence are not verified candidate contents or result provenance;
neither artifact was downloaded. The paginated open-PR query targeting Main
returned an empty list at that capture, not a guarantee no publication PR can
be created afterward.

Completeness remains explicitly qualified. Repository-wide queued and
in-progress responses returned 8/10 and 6/92 rows/totals respectively. The
direct trigger in-progress response also returned zero rows with total 1,
although its queued response returned both rows with total 2. Other direct
workflow/state responses had matching row counts and totals. No cause is
assigned to the mismatches: lifecycle races, filtering or provider behavior
have not been distinguished. Do not equate missing rows with zero obligations
or count the unmatched totals as known executable release jobs. This bounded
inventory is evidence for the observed identities, not a global custody
clearance or an exhaustive settled-release claim.

Existing release/CI owners retain the controller, candidate and publication
seams. No run was dispatched, retried, rerun, cancelled or watched to completion;
no credentials, native grant, policy or owner changed. The natural active
release is not a new campaign-authorized native attempt or proof that any
earlier retained native/lifecycle failure has been resolved.

### Enforcement snapshot

At the October 18 capture, the Dev ruleset checks were `workflow-lockdown-guard` and
`source-attribution`. Classic Dev protection additionally required
`PR gate (required check)`, bound to app ID 15368. Main's ruleset required
`main source gate` with strict freshness; its classic required-status endpoint
returned 404. Inspect both protection mechanisms, not just one check list.

Environment names `copilot` and `main-promotion` were observed. Secret values,
environment-review settings, current credential custody, complete in-flight
release obligations and all bypass applicability were not audited by that
name-only read. The November 20 section above adds environment protection and
credential-name binding metadata without claiming actual credential custody.
Refresh authoritative policy and every affected caller before any cutover;
these dated snapshots are not standing merge authority.

## Prioritized candidate ledger

The November 20 ranking below incorporates the bounded closure above. The
October inventory counts remain dated observations, not current savings
forecasts. A row is an investigation or accepted-owner interface, never
permission to delete, retry native work or activate control. These priorities
do not make broad Main ownership migration or new capacity a prerequisite for
a safe reduction under existing gates.

| Priority / state | Candidate | Evidence / open question | Existing prerequisite and rollback |
|---|---|---|---|
| 1 - owner acceptance open | Remove only Payload CWD's weekly routing-notice trigger | One observed scheduled run allocates one three-second echo-only job; both native recipes are disabled. The specific #1303 request is posted, not accepted. | Existing portfolio/notice owner accepts retaining manual inventory, disabled native recipes and the explicit unenrolled gap. Restore only the schedule if an accepted notice consumer is lost; do not enroll or erase missing coverage. |
| 2 - integration seam retained | Produce required identifier enforcement and detailed per-head feedback from one trusted scan | Shared scanner code is not equal input/admission/reporting semantics; direct deletion is refuted. The custom detailed check is not a named required context, but required-job diagnostics depend on it. | Existing CI/release owner accepts the one-scan interface. Preserve final-diff size, fork isolation, complete path/error semantics, head movement, Main admission and detailed feedback before retiring the old Dev follow-up job. Restore that job if any contract is lost. |
| 3 - remaining event closure | Coalesce compatible edited/retarget metadata work beyond delivered synchronize consolidation | #7236 delivers the source synchronize reduction; independently qualified Main activation remains open. Closed events and all-base retargeting still differ from Dev-only lockdown. | Existing CI/release owner retains edited/retarget and control integration. Preserve metadata epochs, departed-source refresh, contexts and inert-data processing; do not infer acceptance from the earlier synchronize seam. Restore the affected original event path on missing refresh or context. |
| 4 - broader reuse unproved | Reuse source/release evidence only across a fully compatible consumer boundary | Strict repeated source-PR unit reuse already landed in #8311 with one actual reuse hit. That does not justify omitting independent every-Main units, native consumers, generated output or artifact-custody checks. | #3336/portfolio owner retains candidate/result interfaces. Prove complete source/recipe/platform/dependency/run/attempt identity and all receiving refusals before any broader omission. Missing or incompatible evidence retains fresh execution. |
| 5 - selectors remain distinct | Share deep-lane setup or a bounded scheduler without dropping coverage | All ten scheduled entrypoints have bounded traces; Handoff exhaustive, Dispatch races, crash diagnostics, periodic plugin suites and Windows/native lanes are not equivalent selections. | Existing component/portfolio/remediation owners accept each row, its cadence, containment and failure routing. Avoid burst fanout; restore individual entrypoints if a lane is missing or delayed beyond its accepted contract. |
| Historical automatic entry retired | Do not implement trusted-CI PR retirement a second time | Current Source and the dated Main snapshot make `trusted-ci.yml` manual-only. Native Code Review remains a separate provider-owned execution, not a duplicate setup workflow. | Keep hosted review and its minimal setup contract. Manual declaration deletion or runner-route changes do not demonstrate another validation reduction. |
| Separate held capacity track | Put qualified narrow Linux capacity to useful work only under its existing admission | A structural guard can be qualified independently, but feasibility is not an assigned Actions job, additive review capacity or current pool readiness. | Existing #7122/CI/controller and capacity owners retain job, label, trust, tooling, resource and aggregate authority. No new capacity/native grant follows this audit. Preserve hosted fallback and require real assignment/result before claiming offload. |
| Existing repair custody | Reconcile repair and housekeeping ownership without taking over publication | Watchdog, generated repair, purge and stale-branch operations are not validation suites. The observed purge refusal already names an outstanding cleanup PR; no replacement publication is assigned. | Existing repair/release/cleanup owners retain their resources and merge gates. Do not activate a worker, widen credentials, cancel old queued records or pre-empt that PR merely to consolidate. |

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
not duplicate policy checks. The subsequently accepted source synchronize
seam lands in #7236; its separate Main activation and live event/rollback
acceptance remain open. That delivery does not accept edited/retarget or
one-scan integration. Those interfaces remain with the existing CI/release
owner, not a newly appointed campaign writer.

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

## Source-attribution server failure - #7906

Direct parent: [A7906-P and A7906-G](README.md). Preserve the existing
source-attribution, promotion and #6909/#7900 owners; no implementation or
runtime ownership is reassigned.

- [ ] **P7906:** Only association HTTP 500/502/503/504 may select a complete
  paginated open-Dev census. Authentication, rate, unclassified, incomplete,
  duplicate-head and moving-snapshot failures remain closed. Preserve the
  existing request budgets and final stable-snapshot fence. Attribute #7907's
  source37/Main-helper delivery without substituting it for the actual trusted
  affected-current-head failure-path result.
- [ ] **V7906:** At the original consumer tier, retain the exact
  affected-current-head result for the eligible server failures and all
  fail-closed classes, pagination, request limits and final snapshot fence.
  Until the existing owner completes that qualification, it remains
  technically unverified. This text authorizes no invocation, CI rerun,
  injected failure, probe, renewed attempt, release change or owner transfer.

This planned receiver can preserve unfinished work without declaring a source
fix. The source has no separately identified explicit issue-OPEN, exclusive
claim-transfer or ACK condition; technical incompletion alone is not a veto
on later requirement-preserving tracking consolidation. Normal reviewed
delivery and complete current receiver/source readback still precede any
separate source judgment. No source disposition occurs in this proposal.
