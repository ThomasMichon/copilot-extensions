# Test Validation Burndown

Parent contract: [Main-Owned Workflow Control](README.md).

## Operator direction - October 19

> I think at this point it's all-hands-on-deck to sweep the promotion pipeline and PR CI pipeline history and identify flaky and overly-long tests, and assist the main agent in reducing the validation test to a reasonable number of tests and total time limit. We want the validation flow to run a set of reliable, parallel test suites, and collect coverage baselines for each, which will allow CI to skips tests that changes don't impact. But we still want to follow the rule that every main release shall have run every unit-level test. For integration and multi-proc tests, we'll need scheduled test runs (against dev), with an agent on the hook to initiate repair when they fail. When we complete the initiative to support dev-backed local deployments, we can consider reducing main-promotion frequency, or even gating it on the integration tests. But right now we need stable, reliable, speedy promotions and CI flows.

## Required outcomes

- Reliable parallel unit suites and a measured, reasonable validation budget.
- Per-suite coverage baselines supporting impact-based PR CI selection.
- Every Main release runs every unit-level test; a baseline or a previous
  release's success is not a substitute for that release's unit execution.
- Integration and multiprocess tests remain in scheduled runs against Dev,
  with an agent accountable for initiating repair on failure.
- Stable, speedy promotions now. Do not reduce promotion frequency or add an
  integration promotion gate as part of this immediate change; revisit those
  options after dev-backed local deployment support.

## Evidence and implementation sequence

The following decomposition is **agent-recommended**, not additional operator
policy or a grant of release authority.

1. Sweep recent promotion and PR CI history independently. Retain run, attempt,
   job, source identity, test node and outcome evidence. Measure queue and
   execution separately; a cancelled run or a single test failure does not
   establish flakiness.
2. Reconcile actual test behavior with the existing tier and collection
   contracts. Mark unknown or mixed classifications for owner resolution;
   subprocess tests do not become unit tests merely by changing a marker.
3. Deliver ranked bottlenecks and reproducible failure signatures to the
   existing release and test-portfolio owners. Accept a disjoint implementation
   boundary before editing their runner, workflow or coverage surfaces.
4. Build deterministic unit-suite inventory and independent parallel shards.
   Prove complete release collection and execution, including parametrized
   cases and newly added tests. Bound resources as well as elapsed time.
5. Bind coverage baselines to their suite, source and instrumentation identity.
   Missing, stale or incompatible baselines, unknown changed inputs and
   untraced generated/configuration dependencies select full affected suites,
   not an empty success-shaped selection.
6. Preserve integration/multiprocess inventory in scheduled Dev execution.
   Record exact input, outcome, failure routing, repair ownership and escalation.
   Do not mark a test moved until its destination and owner are proven.
7. Compare equivalent before/after events and release cycles. Report actual
   critical-path time and remaining failures; reviewed source or green narrow
   CI is not proof of live activation, full coverage or reliability.

## Ownership and acceptance

The existing campaign driver owns the history sweep and synthesis. The existing
release controller retains CI integration, promotion, pause, qualification and
Main activation. Existing test, coverage and remediation owners retain their
reserved implementation scopes. There is no second release controller.

An existing complete-corpus proposal must be reconciled to the new unit-only
critical path before activation. Retained code and evidence may be assigned to
the unit gate or scheduled integration portfolio; neither a new policy nor a
test reclassification waives a known correctness failure.

All implementation and activation follow the parent contract's review and
authority gates. This amendment records requested behavior; it does not claim
that coverage selection, schedules or repair supervision are already live.

## Initial measured evidence

The requested contract and this bounded audit merged through #7275 at
`2026-10-19T22:20:54Z` as `3c8aeb12428f8b8f2c4f37b2242ee915132d0c2a`.
This closes the initial evidence/plan publication, not unit classification,
coverage selection, integration scheduling, repair supervision or live timing
acceptance. See the [delivery journal](journal/2026/10.19-test-burndown.md).

The read-only promotion sample contains 60 newest runs from October 18-19,
not the entire requested seven-day population; the API reported 294 matching
runs. Twelve job inventories and four failure logs were examined. Source
workflow/control identity is not the same as admitted Dev or generated-candidate
identity, and a paused gate-only success is not a release.

Two exact job records were independently checked before integration:

| Observed job | Execution | Concrete bottleneck |
|-------------|-----------|---------------------|
| [Worktrees source job, run 39206595414](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39206595414/job/117910807731) | 26m45s | The separate coverage step takes 13m58s after normal execution. It is not free baseline metadata collection. |
| [Candidate qualification, same run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39206595414/job/117929607299) | 51m56s | The canonical-CI qualification step takes 51m27s; its failure reports downstream cancellation, not a demonstrated flaky unit. |

Both API job records name workflow/control head
`a325f5063e7cc0a2b3276be81be3e4959f8f4651`. The qualification log separately
identifies admitted Dev `dd12fff539b9bb8bf2b2ae8dd255c6035fd3e32d` and candidate
`a9fb46b6717c9785fc366cbe2503f80d41dacf2f`.

These measurements support the existing owner's single instrumented unit-pass
direction and removal of redundant execution only after equivalent evidence is
proved. They do not authorize deleting an integrity gate, counting integration
as unit coverage, reusing stale qualification or increasing a timeout.

Across four completed nine-plugin matrices, 36 retained step-duration pairs
sum to 10,729 normal-suite runner-seconds and 11,763 separate coverage
runner-seconds: coverage is 52.30% of that combined runner work. This is not a
prediction of elapsed-time savings. The aggregate was reconstructed from
returned API output; exact per-step timestamps were not retained. The two job
records above were independently refreshed, and the 36-row sums recomputed,
but the entire historical population was not re-fetched.

### PR CI findings

The newest 60 PR CI runs cover only October 19, 18:25-21:16 UTC. Twelve
representative job inventories and four readable failure logs were inspected;
one requested failure log returned HTTP 404. Successful-job timing observations
are a bounded convenience sample, not population percentiles.

| Finding | Evidence | Classification and owner action |
|---------|----------|---------------------------------|
| Native purge child intermittently exceeds its original 20-second bound | [Failure job](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39316565953/job/118286793618) and [passing job](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39316278947/job/118278644536) both check out merge `49ae4b4453a4bab58fe5b339b8166a3b7207ff72`; 193 pass/one fail versus 194 pass in 12.05s | Independently verified source-controlled intermittency, not diagnosed root cause. Added evidence to existing #7020; its #6487 diagnostic owner remains. |
| Windows snapshot fixture compilation exceeds 20 seconds | [Failure job](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39311131712/job/118260899765) versus [same-head success](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39312054399/job/118278077928) | `agent-vault` fixture `Add-Type` fails before two test bodies. Actual Windows merge identity is unverified; recurring setup instability, not controlled flake proof. |
| Long serial guards execution | Eight successful sampled jobs: median 14m09s, nearest-rank p95 20m36s | Execution alone exceeds the previously recorded ordinary-PR target; classify actual unit/integration effects and partition without deleting assurance. |
| Waiting is independently material | [Run 39318725031](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39318725031): guards wait 8m10s, then execute 14m09s; another run has 29m54s before first job creation | Keep admission/concurrency delay, runner queue and execution separate. Test optimization alone cannot guarantee whole-flow latency. |

The purge node is
`tools/test_check_install_contract.py::test_posix_powershell_purge_compares_physical_home_identity`;
its actual fixture implementation is in `tools/test_installer_finalizers.py`.
The Windows nodes are
`plugins/agent-vault/tests/test_install_snapshot_ps1.py::test_stamp_supports_first_use_provision_from_snapshot_only_ps1`
and `::test_snapshot_compatibility_launcher_does_not_self_provision`.
Real compiler/process effects require explicit classification, not automatic
unit enrollment from a file name or marker.

Ranked evidence was delivered to the retained implementation owner on #7249.
Its former complete-corpus proposal is explicitly superseded and held as a
draft; it is not the current activation policy. This audit does not take over
its runner, coverage, candidate-controller or workflow files.

## Current unit census and periodic acceptance - October 21

This is a bounded source-and-execution audit, not an enrollment change or a
claim that the parent acceptance criteria are complete. The pinned source is
Dev `1538d76d8dbbdf0470344118d2878eafce21f477`; the independently resolved Main
control snapshot is `dd4284a4c7caf7cfb7689f44e6f412734d92ee6c`.

### Exact packet completeness is not a complete unit census

Both snapshots declare **236 parameterized nodes in nine test modules** under
`tools/promotion_unit_tests/`, with 28 declared source files, 18 production
coverage targets and a 300-second whole-packet budget. All 18 targets are
repository tools; no plugin or shared-library production module is listed.
The collector, admission contract and packet verifier have identical Git blobs
in these two snapshots.

The contract correctly requires exact admitted collection, passing setup/call/
teardown, no skips or prohibited effects, unchanged admitted source bytes and
production call coverage. Retain those controls. They prove the declared
packet, not a bijection between every historical unit and a release-executed
node. The manifest has no full-corpus classification or original-node mapping.

The raw trees contain 2,111 and 2,940 Python `test_*.py` paths respectively.
These are different snapshots/layouts, including materialized copies, not
deduplicated unit populations. They exclude other languages and do not account
for parameterization or dynamic collection. Do not turn them into unit counts
or coverage percentages.

One concrete out-of-packet review candidate is
`libs/lazy-cli-dispatch/tests/test_dispatch.py`: fourteen unchanged cases cover
the actual dispatcher, `self_override` and `core_helper`, using fake modules,
callbacks and captured parser output. Neither these identities nor the
production module is admitted by the current packet. The inspected test and
production bytes match the pinned source.

This is a **candidate**, not an eligibility certificate. A supplemental
contained Windows run with the unchanged `UnitRun` observer was rejected
before collection: pytest's `colorama` startup attempted `ctypes.dlopen`.
It supplied no passing test or unit-eligibility result. No preload, observer
exception, test move, manifest change or CI enrollment was used to manufacture
a positive result.

The remaining unit gate is an owner-reviewed, deduplicated classification and
identity map: every unit must map to release execution, with unknown and
unavailable closure explicit. Review the candidate cohort's actual fixture/
import/effect closure before admitting it. A nine-module packet or a
filename/tier label cannot stand in for that census.

### Scheduled execution is real; repair initiation is not yet established

The current Dev and Main snapshots carry byte-identical periodic workflow and
residual inventory declarations. They select nine default-full Linux plugin
invocations through the hardened container route. Windows remains literally
disabled and supplies no native-platform qualification. Independent libraries,
root tools, standalone Manager/chat, explicit-tier/native stress, network
first-touch and unprovided filesystem tiers remain explicit residuals.

The actual scheduled [run 39439577296](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39439577296)
used control `f348e5a0cc596f69d462b454c7c60e9ff22cd30b` and separately recorded
tested Dev `9ad401dd8c4827a44e2ca5ed1e3bcbd13f94eca8`. Six Linux jobs succeeded,
three failed, the Windows job was skipped and the detection/report job
succeeded. This is not a same-source flaky-test comparison.

For all three failed jobs, the preserved execution record reports the actual
wrapper invocation and tested source. The downloaded container profile and
inventory bytes independently match that immutable Dev tree and their
recorded SHA-256 values. Logs reached real test failures, not only a missing
Docker/devcontainer prerequisite.

| Failed job | Observed failure family | Execution boundary |
|------------|-------------------------|--------------------|
| [Containers](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39439577296/job/118685704039) | Two `test_launch_storage.py` cleanup/refusal cases reached the test's forbidden credential/deletion callbacks | Stopped in group 1/3; that group reported 2 failed, 518 passed, 7 skipped |
| [Logger](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39439577296/job/118685703726) | Three `test_source_runtime_reselection.py` helper-copy failure/rollback cases reported state inequality | Reached group 3/3; the failing group reported 3 failed, 566 passed, 8 skipped |
| [Worktrees](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39439577296/job/118685703935) | Three POSIX first-install/namespace-health/watchdog cases failed | Reached group 5/22; the failing group reported 3 failed, 751 passed, 13 skipped |

These are observations, not root-cause or platform-availability diagnoses.
Failure-group counts are not complete-plugin pass counts; skipped cases and
unreached groups do not become passing evidence.

The report created [#7377](https://github.com/ThomasMichon/copilot-extensions/issues/7377).
At this audit's readback it had no assignee, comment or visible agent claim;
neither checked local repository lane contained its canonical issue key.
That does not exclude work on another coordinator. It does mean that the
visible detection issue and generic portfolio-owner role do not establish the
required accepted agent repair, initiation or escalation.

The direct #1303/portfolio-owner follow-through is to identify the actual
repair task/agent and route each exact failure family without duplicating
claimed work. Keep source, outcome, repair initiation and eventual resolution
linked. Until that evidence exists, retain the parent scheduled-repair item
as open. This audit changes no workflow, runner, selection, deadline, capacity
or release gate.
