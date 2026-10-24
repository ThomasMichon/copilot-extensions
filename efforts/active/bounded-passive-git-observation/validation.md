# Validation and stop criteria

Back to [the effort](README.md). Commands below are run from the owned source
worktree root with a working Python 3 selected by normal repository tooling.
Proposed test files do not exist yet; their commands become executable only
after admitted implementation. No runtime tests are claimed by this plan.

## P0 — docs-only gate now

Stage this effort folder before checks. Run:

```powershell
python tools\check-effort-vision-structure.py efforts\active\bounded-passive-git-observation\README.md
python tools\check-docs-consistency.py
git diff --cached --check
```

Obtain the required fresh independent plan review, then use managed
`create-pr`, source Copilot review and all required CI before managed merge.
No plugin payload/runtime changes means no plugin changefile or speculative
full runtime suite. Inspect PR CI results rather than calling a local docs
pass a server-side check. The plan-only stop is the reviewed merged proposal
returned for independent integration admission, not implementation.

## R1 — deterministic units

Extend `test_git_ops.py` and `test_status_segment.py`:

- Stall the very first branch call; non-active returns UNKNOWN with no
  invented branch/drift/fetch attempt. Known-live remains ACTIVE with
  unavailable branch metadata. Preserve GONE/zombie with zero Git calls.
- Advance an injected monotonic clock across successful default, branch,
  status/count/log/containment calls. Every later timeout is nonincreasing;
  exhausted budget causes zero additional spawns, not another 15 seconds.
- Exercise both default-resolver local probes, both segment prefixes,
  config-hint/ref-absent/detached cases and optional remote query. Preserve
  offline default and precedence; remote call is capped by both 10 seconds
  and remaining budget.
- Timeout before fetch: `fetch_requested=False`. Timeout during/after fetch:
  requested/failed true; failed fetch never yields fresh removal evidence.
- Compare the existing ACTIVE/GONE/DIRTY/WIP/UNUSED/COMPLETED/ORPHAN and
  branch-drift, count-failure, squashed-content and finalized-display cases.
- Capture actual constructed subprocess argv/options: every bounded
  observation uses containment and remaining deadline. Verify deadline-aware
  cleanup waits/pipe closure and unchanged legacy push/helper behavior.

```powershell
python tools\run-plugin-tests.py agent-worktrees --test-file tests\test_git_ops.py --test-file tests\test_status_segment.py --test-file tests\test_status_monitor.py
```

Run the smallest combined selector first if only one sub-slice changed;
record the exact selection and expected/completed counts. These mocked
contract tests do not establish real descendant cleanup or concurrency.

## R1 — real-process contract and admission progress

Proposed opt-in `tests/native/test_git_observation_deadline.py`:

- Own isolated repo/HOME/tracking/config/endpoints and a test Git shim.
  Stall initial `rev-parse` or default `symbolic-ref` in turn. The shim starts
  an owned console descendant retaining stdout/stderr so root-only kill or
  serial pipe drain is observably insufficient.
- Invoke actual classification and both segment entry paths in subprocesses,
  not a mock of the deadline helper. Record start/end, child identities,
  pipe EOF and residuals; no helper/descendant survives teardown.
- Run a disposable actual monitor/admission boundary with two targets and a
  barrier proving target A entered the stall. While it remains blocked,
  complete one unrelated hook/mutation admission and one target-B
  publication through the existing owner path. Require completion **before
  releasing A** and before A's deadline, not merely after timeout.
- Deliberately hold the admission lock across A's observation in a negative
  control: the progress assertion must fail. A publication to only an
  in-memory fake dictionary does not satisfy this test.
- Change the target record/project/owner generation during observation;
  old results cannot publish over newer evidence or leak project context.
  Test served-segment and warm-roster paths separately.
- Exercise cancellation, startup containment refusal, root exit with held
  descendant pipes and a cleanup wait that would exceed `D`. Windows uses
  the existing owned Job boundary; Linux retains owned process-group rules.
  Unsupported isolation is a visible blocker, never a broadened kill.

```powershell
python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests\native\test_git_observation_deadline.py
```

Stop/fail for any residual process/pipe, deadline reset, concrete state from
incomplete Git evidence, cross-project write or unrelated-progress failure.
Do not repair a live monitor or weaken production admission to obtain proof.

## R2 — unit and supported-native restart cycles

Units assert exactly one status invocation per classification with the
invocation-local fsmonitor override. Keep existing Git classification
work-count assertions, adjusting argv expectations rather than adding
configuration/process probes. Existing session-catalog cooldown, fresh-mux
and real-live-session protection tests must remain green.

```powershell
python tools\run-plugin-tests.py agent-worktrees --test-file tests\test_git_ops.py --test-file tests\test_session_catalog.py
python tools\run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests\native\test_passive_fsmonitor_cycle.py
```

The proposed native test uses supported Windows Git and an isolated repo with
worktree config enabled. Preserve pre-test global policy and fixture config
bytes. Enable fsmonitor **only in this fixture** and establish support by an
explicit owned start/status probe before testing. Unsupported Git/platform
is a reported missing qualification, not a passing skip for issue closure.

Use three records: dark active-status, dark finalized-status, and a genuine
owned live Copilot/mux session with a real liveness receipt. A synthetic live
boolean is unit coverage only. No live production worktree is a fixture.
Perform at least three classify -> owner cleanup -> classify cycles each,
with a fresh mux observation and fixture clock allowing cleanup cooldown.
Inspect after each classification as well as each cleanup so a transient
restart followed by stop cannot hide. Count starts, stops and status reads
separately; status inspection must not itself start a daemon.

Expected: dark/finalized watchers are absent after cleanup **and** subsequent
passive reads; no additional passive watcher starts; live watcher's verified
identity remains running/unchanged and ordinary interactive Git retains its
policy. Actual owner cleanup must decline to stop that live watcher. Global
and worktree config bytes remain unchanged by the code under test. Cleanup
only exact fixture-owned resources; no process-name kills or config rewrites.
No genuine isolated session available means the live-control tier remains open.

## Qualification and broader gates

These are opt-in integration/native lanes, not additions to exhaustive
required PR CI. Retain normal fast changed-contract CI and place expensive
process matrices in the existing explicit portfolio after its owner's
admission. Stage new test files so the wrapper snapshots them.

Report source/interpreter/Git/OS identity, isolated workload cardinality,
expected/completed tests and cycles, subprocess starts versus peak-live,
CPU/cache conditions, individual elapsed/cleanup observations and errors.
No latency pass is presently available under reported 100% host CPU
contention; do not substitute a new benchmark, arbitrary slack or mocks for
native qualification. Functional deterministic work/ownership tests may
proceed separately without asserting latency.

#2619's native mux/tracking events, controller transitivity/direct-target
freshness and long lost-event backstop remain open with their existing owner.
#6086's representative quiet **and** concurrent workloads, cumulative
spawn-rate versus peak-live budgets and release/installed acceptance remain
with `process-spawn-reduction`. Completing this matrix does not complete
those campaigns. No remote venue or rollout is authorized for this slice;
source/native-fixture proof is not deployed-system proof.
