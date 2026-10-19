# Wave A: trusted metadata event and helper closure

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Portfolio: [shared roadmap](workflow-portfolio-audit.md).
As-is behavior: [required Dev source metadata](../../../docs/pipelines.md#required-dev-source-metadata).

## Accepted scope and limits

The existing Main-control participant accepted this campaign driver's Wave A
read-only workflow/helper/event audit and separately reviewed negative-case
tests/docs, reporting no active writer on the two named metadata workflows or
their refresh helpers. This is not blanket production write clearance:
unpublished CI/release-controller integration custody remains to be reconciled.
Public open-PR overlap checks cannot establish that unpublished custody.

The pinned Wave A mapping below is not generic helper equivalence for the whole
portfolio. The source-only synchronize implementation and its required
regressions are described separately below, under the Campaign driver's
single-driver scope. No refresh helper implementation, scheduler, credential,
runner route or live event canary changes. Main activation, live event
equivalence and rollback remain separate gates. CI/promotion, #7199, #7122,
Maintainer stewardship, installation attribution and remediation scopes remain
with their existing owners.

## Pinned Wave A source boundary

The Wave A mapping uses Dev `94f32c6f19dc00f6719f89fb8c38dca2f78bc2b8` and Main
`ff6773de1b539f5227b98634be13fcaaa75adac7`. These are explicit Git snapshots,
not a claim that a later live default-control revision has been audited.

| Path | Dev blob | Main relationship |
|---|---|---|
| `.github/workflows/source-attribution-departures.yml` | `a47936653f3c39992f8725a0538df48263614924` | Identical |
| `.github/workflows/workflow-lockdown-guard.yml` | `dde736c03448172a9b3001beffc5b2cf4fb3434f` | Main blob `3bd1bbac8f6ab8abc74e61f045114892ae450ad4`; review-nudge implementation differs |
| `.github/actions/required-pr-attribution.cjs` | `e09c666e99978fc1e05f16106aac9c744b7547e4` | Identical |
| `.github/actions/source-refresh-budget.cjs` | `915d478cb1a9bb1ac8cdad73ec1cd1e39031def0` | Identical |
| `.github/actions/package-lock.json` | `aa345fce038e4bf4c4480c2624a521f5e304467c` | Identical |
| `worktree-manager/scripts/picker-snapshot/install.py` | `a0b751094a42d5409bd4724699719ee9f28d9854` | Identical |

Dev's review nudge first reads eligibility so a settled request need not wait
out the grace interval; pinned Main waits before that first read. Do not treat
the Dev implementation as active Main behavior or bundle a review-nudge
rollout into metadata event consolidation.

## Event partition: preserve decisions, share only compatible entrypoints

`workflow-lockdown-guard.yml` admits Dev-targeted `pull_request_target`
events: opened, reopened, synchronize, edited, ready-for-review, labeled and
unlabeled. Its protected-path and native source-attribution checks run for
every such admitted PR event; they cannot be changed to skipped checks merely
because an edit appears irrelevant.

`source-attribution-departures.yml` admits synchronize, closed and edited
without a base-branch filter. Its non-required refresh job then selects actual
Dev departures. A workflow invocation with that job skipped is still a workflow
invocation, not eliminated work.

| Event and current base | Current-head obligation | Departed-head obligation | Review nudge |
|---|---|---|---|
| Open/reopen/ready-for-review to Dev | Protected-path check and native `.enforce` | None | Eligible nondraft Maintainer only; fresh runtime checks still apply |
| Synchronize on Dev | Same two checks on the new event head | `.departed` refreshes remaining current Dev peers on event `before` | Eligible nondraft Maintainer only |
| Body/title edit on Dev, no base change | Both required checks still run | None; departure workflow's job skips | None |
| Label/unlabel on Dev | Both required checks still run, including exemption changes | No departure trigger | None |
| Retarget into Dev | Both required checks on the current head | None | Eligible nondraft Maintainer only |
| Retarget away from Dev to any base | No Dev-targeted native invocation | `.departed` refreshes peers on the departed PR head | None |
| Close/merge while targeting Dev | No closed event in the Dev handler | `.departed` refreshes peers on the closed PR head | None |
| Non-Dev synchronize/close/edit without leaving Dev | No Dev-targeted native invocation | No refresh job; existing all-base invocation may still occur | None |
| Main push or trusted Main manual dispatch | No PR-native check jobs | Full `.backfill` over current open Dev PRs | None |

The duplicated Dev synchronize entrypoints do **not** perform duplicate policy
work. Native enforcement checks the moving PR's new head; departure refresh
repairs other PRs on the old head without requiring edits to their descriptions.
Both results remain necessary even if a later implementation shares one
workflow invocation.

Do not simply narrow all events to Dev: GitHub filters on the new base, so
retarget-away would disappear. Do not simply broaden the existing required-job
workflow and skip its required-named jobs outside Dev: commit-scoped skipped
checks could interfere with a remaining Dev PR sharing that same head.

## Direct helper, data and publication closure

Native PR enforcement and departure refresh check out `github.workflow_sha`,
with credentials not persisted, and install the same locked parsers without
lifecycle scripts. Main push and input-free manual backfill also use that
control snapshot. Promotion-dispatched backfill is the deliberate exception:
after trusted control verifies release/control ancestry, it checks out
`inputs.promotion_sha || github.workflow_sha`. With the verifier's promotion
inputs present, helpers and parser lock come from the merged release snapshot,
which can differ from the newer workflow/control snapshot.

None of these paths executes the PR head. The installer admits registry release
age and integrity before installation and publishes dependencies only if
manifests, lock and configuration remain unchanged. Preserve the distinction
between trusted control identity and selected helper/lock identity, rather than
assuming every metadata invocation uses the control tree's helper bytes.

The parser lock closes over CommonMark `0.31.2`, parse5 `7.2.1`, entities
`3.0.1` and `4.5.0` in their respective dependency locations, mdurl `1.0.1`
and minimist `1.2.8`. Metadata validation excludes rendered code/quote examples,
validates one public source origin and does not treat syntax as ownership proof.

The runtime call graph is:

```text
Dev PR -> .enforce -> headPolicy -> decision/validate + snapshot -> required result
departure -> .departed -> .backfill(head=before-or-departed-head)
Main push/manual -> .backfill(all current open Dev PRs)
.backfill -> refreshPR -> headPolicy -> pending check -> fresh snapshot -> completion
                                      -> unstable/unverifiable: superseding failure
all bounded refresh reads/writes/pages -> source-refresh-budget.cjs
```

`headPolicy` paginates associated PRs and falls back to a fresh full Dev listing
when association membership is absent. Multiple current open Dev PRs on one
head fail independently of their individual marker validity or exemption.
Snapshots bind validator/dependency definition, PR identity, body, head, base,
state, labels and same-head membership. Retain pre/post-publication freshness
reads, paced writes and deduplication; an event payload is not a current-policy
snapshot.

Refresh has one 950-request budget, including pagination and bounded policy
retries. It reserves cleanup requests to supersede an unverifiable success
and close an unfinished check. The per-PR policy loop is at most three
attempts; failure processing continues to other PRs except when the overall
request budget is exhausted. Transport retries remain zero. Do not replace
this with a new unbounded scan or independent publisher.

Native `source-attribution` and `workflow-lockdown-guard` check names are
required-policy interfaces. Departure refresh has a distinct non-required job
name but publishes `source-attribution` checks for current Dev peers. Batch
refresh alone has `checks: write`; ordinary metadata jobs retain their narrower
permissions. Review requests use their existing separate licensed authority
and per-PR concurrency; consolidation does not grant it to refresh jobs.

## A second live consumer: promotion verification

`tools/verify_promotion_workflows.py` explicitly dispatches
`workflow-lockdown-guard.yml` on Main after an Actions-token promotion merge
may suppress normal push consumers. It requires the exact successful job
`refresh-source-attribution`, not merely a green workflow conclusion.

It correlates promotion run-attempt, creation-time window, release SHA/tag and
trusted Main control ancestry, rejects ambiguous matching runs, reads all
bounded job pages, and rejects a missing/skipped required refresh job. The
controller obtains this verifier from its trusted workflow revision. The
verifier supplies `promotion_sha`, so the refresh executes the release's
helpers/lock even when its workflow definition runs from a newer Main control
revision; the inline ancestry guard is evaluated before that checkout.

Consequently Main push/manual backfill, workflow identity, promotion inputs,
run title and required job name cannot be deleted or renamed as incidental
cleanup. Any deliberate interface change requires the existing controller
owner to migrate and prove that caller first. No controller code is changed
by this audit.

## Evidence and remaining equivalence obligations

The focused existing contracts executed for this source mapping:

- Node metadata-helper and review-gate suites: 66 passed, one skipped. The
  skipped pinned github-script bundle opt-in is not native bundled-action proof.
- Existing metadata workflow wiring, protected-path trust and promotion
  verification suites: 86 passed. These use local fixtures, not GitHub mutation.
- Missing local parser modules were restored through the existing seasoned,
  lock-preserving installer; no dependency declaration or version changed.

Reproduction uses the repository's existing bounded pytest environment:

```text
node --test .github/actions/required-pr-attribution.test.cjs tools/test_copilot_review_gate.cjs
python -m pytest -q tools/test_required_source_attribution_workflow.py tools/test_trusted_pr_policy.py tools/test_verify_promotion_workflows.py
```

The helper negatives already exercise shared heads, fresh association fallback,
departures, superseded heads, concurrent peer membership changes, changed
metadata during publication, exhausted budgets and failure fencing. Wiring and
promotion negatives retain trusted checkout, all-base retarget visibility,
required contexts and missing/skipped/wrong-identity consumer rejection.
This audit adds no parallel suite merely to restate those passing cases.

**Source-only Wave B slice:** the Campaign driver owns the synchronize
consolidation through ordinary Dev review, following the operator's
single-driver direction. The source declarations share the Dev synchronize
entrypoint while retaining both current-head enforcement and old-head refresh.
All-base closed/edited departure handling and Main push/manual backfill remain.
The existing release/controller owner retains publication, qualification,
pause and Main activation. This is not a competing controller or an assertion
that a message receipt supplied peer acceptance.

Source delivery and separate live acceptance must prove:

- Exact before/after event admission for Dev sync, close/merge and retarget in
  and away from Dev, plus body/label edits and unrelated bases.
- Both old-head peer recovery and new-head required checks survive duplicate,
  reordered and superseded events; no skipped required-context collision.
- Job-level permissions, trusted snapshot/imports, concurrency, request budget,
  failure owner and promotion-verifier interface remain compatible.
- One accepted live event canary, comparable invocation/job/check measurements
  and rollback to the original affected entrypoint, under current Main control.

The declared Dev synchronize workflow admissions decrease from two to one;
unrelated-base synchronize admissions decrease from one skipped invocation
to none. Allocated metadata jobs do not decrease. The new isolated,
non-required job can add skipped check records on other trusted-entrypoint
events, so no overall check-count, compute or review-headroom saving is
claimed without comparable live measurements.

## Source regression and selected Main admission contracts

`tools/test_metadata_consolidation_preflight.py` reads the actual source
workflow declarations under test, rather than generating a proposed router.
Its cases are collected by the existing required
`tools/test_required_source_attribution_workflow.py` entrypoint; no CI workflow,
selector, extra job or test runner is added.

The tests reuse the bounded condition evaluator and real-Git Main-gate fixture.
They check 72 action/current-base/previous-base combinations, unchanged
required/promotion jobs and departure-job permissions/concurrency, and the
separately specified workflow-admission counts. The actual selected gate body
then evaluates synthetic Git commits carrying the source workflow declarations
or rejected helper-mixed, outsider and unchanged shapes. These negatives assert
the intended admission diagnostic, not just a nonzero exit that could hide a
broken test harness.

The full gate executes from a real LF step file, matching Actions rather than
transporting a multiline heredoc through Windows Bash arguments.

Run it through the repository's existing bounded pytest environment:

```text
python -m pytest -q tools/test_required_source_attribution_workflow.py
```

Source routing is read from the checkout under test. The separate trusted
admission snapshot defaults to immutable local `HEAD`; set
`METADATA_PREFLIGHT_SHA` to an available full 40-hex Main commit to qualify that
retained control, then unset it afterward. An unavailable or malformed selected
commit fails rather than silently substituting another revision. Git blob
contents retain trailing newlines so literal workflow bodies compare faithfully.

The source required-metadata entrypoint passed 88 tests. Explicit retained
Main `a325f5063e7cc0a2b3276be81be3e4959f8f4651` passed all 82 partition and
admission cases against the source declarations. This proves the tested source
partition and workflow-only bootstrap gate shape, not full GitHub admission,
qualified Main activation, live concurrency or observed savings. The fixture
does not change the preservation manifest.

Lightweight local Git/Bash consumers ran. Fresh-environment provisioning is
inapplicable because no installer/runtime changes; the separately accepted live
event cutover, rollback and measurements remain open. Source merge must not be
reported as active trusted-default routing.
