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
