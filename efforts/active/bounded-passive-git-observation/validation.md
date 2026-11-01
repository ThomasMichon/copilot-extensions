# Validation and stop criteria

Back to [the effort](README.md). Commands below are run from the owned source
worktree root with a working Python 3 selected by normal repository tooling.
Historical planned native files below remain proposals except for the exact
Windows R1a and guarded-recovery fixtures named in the current receipt below.
Native fixture/process-survival qualification is now recorded; source
acceptance, installed qualification and nominal performance remain open.

## Windows-first caller-custody checkpoint

### Independent atomic creation and local facade

Foundation v1 `0bb18607` has a reproduced genuine-reader-start defect and is
superseded, not silently blessed by its earlier green selection. The v2
fail-first run captured exactly `[False, False, False]` after native thread
creation raised before a worker existed; the pending-launch and after-launch
controls passed. Evidence:
`.venv-tools/r1a-foundation-v2-reader-failfirst-20261024.log`.

The narrow correction distinguishes a synchronous launch failure from an
uncertain or actually started launch using the owned thread's pending
registration and identity/start state under a nonblocking registry lock.
It preserves interruption custody and retries an unavailable proof without
waiting or renewing D. Actual CPython 3.11.9/3.12.14 ordering is captured in
`.venv-tools/r1a-foundation-v2-thread-start-contract-20261024.log`.

Updated affected selection: **143 passed, 99 deselected**; shared selection:
**37 passed, 1 native case deselected**. These include the no-created-worker,
pending-start, after-start RuntimeError, pre-start interruption and busy-lock
controls. Raw receipt:
`.venv-tools/r1a-foundation-v2-reader-green-20261024.log`.
No native, receiver/authority or integration qualification is inferred.

Final affected selection: **138 passed, 99 deselected**; canonical shared
atomic/Job selection: **37 passed, 1 native case deselected**. Raw receipt:
`.venv-tools/r1a-atomic-final-units-20261024.log` (15.53 seconds plugin pytest /
32.09 seconds including preparation; 1.73 seconds shared pytest). These are
diagnostic durations, not native latency qualification.

Captured fail-first shared tests exposed ignored invalid/expired deadlines;
the post-create interruption escaped rather than retaining owned cleanup.
The local facade's four initial contracts failed because it did not exist.
Raw output is retained in `.venv-tools/r1a-atomic-failfirst-20261024.log`.

The new shared tests inject the real private adapter's Windows API boundary:
actual attribute backing arrays/order, no CreateProcess on capability failure,
Unicode/empty environment and cwd, before-resume interruption at D, exact
retained native root/Job/open parent streams, remaining budget and unchanged
default cleanup. Consumer composition tests run the actual R1a observation
helper, atomic adapter and local facade together, not a mock past that helper.
They also preserve cleanup-phase cancellation through zero-wait settlement.
No native process is launched by these tests.

Facade tests use actual bounded reader threads with injected roots and byte
streams: decoding/replacement/newline behavior, partial startup, interrupted
join, no release before root/readers/Job completion, and zero-wait settlement
without clock renewal or starting unattempted readers. Readers are joined;
native root close follows verified settlement. No generic factory,
`owned_process.py`, POSIX, service or installed behavior was changed.

The independent receiving contract remains proposal-only. Both intentionally
failing recovery-boundary probes at `01c80dcbb` remain in the source; they are
not included in the atomic green selection or waived. Any resulting full PR
CI failure must be reported as that unresolved gate, not bypassed.

Future native caller-death proof must keep its supervisor's outer Job alive,
kill only the exact owned caller, and avoid retaining a duplicate inner Job
handle that would prevent kill-on-close. No such native run is admitted here.

### Contained review corrections after source refresh

After the mechanical index-only rebase, the focused consumer selection below
passes **133 tests, 99 deselected** (14.77 seconds pytest / 36.44 seconds total).
This covers retained snapshot sibling/unknown hints before discovery,
interrupted multi-ticket settlement, blocked-project sampling fairness,
successful undrain reopening, the actual server close-before-force barrier,
and two updater generations contending through predecessor cleanup/release.
No production deadline or observation budget was changed.

Raw files were opened before their commands and retained under `.venv-tools`:
`r1a-review-contained-failfirst-20261024.log`,
`r1a-review-lifecycle-failfirst-20261024.log`,
`r1a-review-undrain-failfirst-20261024.log`, and
`r1a-review-contained-green-20261024.log`. The first two runs also exposed
incomplete injected fixture arguments/methods; corrected runs captured the
actual updater and undrain failures before their source fixes. Do not count
those fixture errors as causal product regressions.

The original 124/22 logs remain unchanged. No new shared Job code was changed
or rerun at this contained checkpoint. Atomic creation/facade implementation
and client ordering remain pending the owner-qualified caller-recovery stop
in the linked proposal; none of those missing tests, current-head server CI,
source review or native/platform/installed qualification is represented by
this local pure/injected pass.

The amended implementation passes **124 tests, 99 deselected** through the
normal isolated consumer wrapper:

```powershell
python tools\run-plugin-tests.py agent-worktrees --test-file tests\test_windows_observation_custody.py --test-file tests\test_git_observation_deadline.py --test-file tests\test_git_ops.py --test-file tests\test_status_segment.py -k "(windows_observation_custody or git_observation_deadline or Classify or TestGitWrapper or TestPushTimeout or status_segment or upstream or default_branch) and not TestPushTimeoutTreeKill"
```

Canonical `libs/agent-procutil/tests/test_job_teardown.py -k "not real_child"`
passes **22 tests, 1 deselected**, using the isolated consumer interpreter,
canonical shared-library import path and contained-test marker. The native
child/grandchild case was explicitly excluded, not passed or waived.

Local ignored receipts are `.venv-tools/r1a-win-first-unit-checkpoint.log`
and `.venv-tools/r1a-win-first-job-checkpoint.log`. The unit wrapper reports
11.26 seconds pytest / 27.94 seconds including preparation and cleanup.
These durations are diagnostic, not production/native deadline evidence.

Coverage includes actual snapshot, classify, raw bundle, request/background,
served/warm roster and updater/monitor shutdown functions with injected
backends; independent B, stale bytes/ages, no direct fallback, alias/race,
invalidation, late close/transfer, zero-wait settlement, lease/token/gone/
supersession/cancellation and ordinary POSIX work counts. Fail-first cases
include lost served-path hints and POSIX network-timeout fallback. Negative
controls prove the actual updater/monitor tests detect discarded custody or
premature release; they do not claim every new test ran against an old binary.

No native fixture, live production monitor/session, installed runtime,
caller-death/zero-survivor receipt or latency qualification was exercised.
No native GO exists for this checkpoint. Keep the PR draft and implementation
ownership active pending independent source admission and a native window.
POSIX #7944, R1b admission-lock concurrency, R2 fsmonitor and the broader
#2619/#6086 acceptance gates remain open. Historical receipts below describe
earlier source checkpoints, not superseding platform parity claims.

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

### R1a first source checkpoint

The admitted local deadline slice has 83 passing targeted contracts (23 new
deadline/runner cases plus 60 existing classifier/segment/legacy cases), with
99 unrelated/native cases deselected:

```powershell
python tools\run-plugin-tests.py agent-worktrees --test-file tests\test_git_observation_deadline.py --test-file tests\test_git_ops.py --test-file tests\test_status_segment.py -k "(git_observation_deadline or Classify or TestGitWrapper or TestPushTimeout or status_segment or upstream or default_branch) and not TestPushTimeoutTreeKill"
```

Focused `ruff check --select F,E9`, module-size ceilings, install-contract,
effort-structure, docs-consistency and whitespace checks passed. The module
ceiling was retained, not widened. Unit clocks/process doubles are not native
consumer/shim or descendant-survival evidence. Those tiers below remain open;
no native window, live fixture or timing retry was used for this checkpoint.

### Three High source-review corrections

Seven pure fail-first cases reproduced missing full POSIX capability preflight
(unsupported/access/OS failures before spawn), invalidated failed-Job custody,
and swallowed cleanup failures in default fallback and both segment surfaces.
After the admitted corrections, the same combined selection passed **90 tests**
with 99 unrelated/native cases deselected. The explicit retained-handle retry
settles a failed cleanup obligation; it is not a renewed observation deadline.
Existing legacy binary/input, inherited-group and 180-plus-five behavior remains
covered without changing those paths.

The Medium per-probe census finding remains blocking and is not covered by
these High fixes. [Its scoped proposal](posix-confirmation-proposal.md) is not
an implemented API. No shared procutil change, native execution, production
deadline growth or expanded qualification is claimed.

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

## 2026-10-26 scoped native receipt

Explicit admission: `AGENT_WORKTREES_NATIVE_RECOVERY=1`. Run the following
files through `tools/run-plugin-tests.py agent-worktrees --test-file ...`,
with the existing contained state/Job owner; item/group/plugin test budgets
are 90/900/2700 seconds. These runner budgets do not change production D.

- `test_status_monitor_guarded_recovery_native.py`: 14 passed, real native
  PID/start, control transport, strict parser, capability and route/lock races.
- `test_status_monitor_guarded_cutover_native.py`: 6 passed, actual installer
  helper and shared orchestrator, typed/lost/untyped receipt, uncertain visible
  publication and successor-exit refusal. No production monitor is launched.
- `test_git_observation_native.py`: 6 passed, actual Git alias plus inherited
  stdout/stderr descendants; held native process handles prove exact exits.
  The outer fixture Job remains open during caller-death checks, including
  death before the first resume. The unchanged D is 15 seconds, with a
  five-second cleanup reserve and verified Job/root/reader settlement.
- `test_windows_observation_custody.py`: 44 passed, including actual monitor
  guarded dispatch/legacy callback and mandatory retained-custody retirement.
- `test_status_monitor.py`: 184 passed after correcting the eight broader
  packet failures. Windows identity lookup is no longer imposed on POSIX
  cache callers; cache controls cover both branches.
- Canonical shared procutil/ZDD packet: 290 passed, 3 Linux-only skips
  (directory fsync, flock and fork), executed with `plugin_test_containment.run_contained`, isolated state
  and canonical source on `PYTHONPATH`, not raw host pytest.

All fixture descendants are contained and settled by their owning Jobs. No
name-based kill, global Git rewrite, installed update, live session/mux change
or service restart occurred. Native fresh temporary roots exercise first-touch
publication/control and process creation. Full fresh installation is not
claimed: this slice changes the opt-in execution/recovery contract, not first-
provisioning policy. Linux/container qualification is unavailable on the
Windows worker; ordinary POSIX unit controls remain separate compatibility
proof, not a POSIX observation-containment implementation.

Final combined current-source packet: **498 passed, zero skipped**, 149.75
seconds inside the contained runner (156.89 seconds including preparation and
cleanup). This includes all 26 explicitly admitted native cases and the pure
R1a/compatibility/core-handler/lifecycle regressions. Current-head review/CI,
original draft publication/merge, release and separately authorized installed
proof remain open. None of these timings is a full-harness or process-economy
pass.

### Subsequent last-round consumer corrections

The 498-case receipt above predates six still-open last-round review defects;
it is not the final candidate's acceptance. `test_observation_review_regressions.py`
reproduced 11 failures with one required-replacement control already passing.
After the scoped cache/snapshot/handoff/updater corrections, all 92 affected
cases passed, followed by **546 passed, zero skipped** in the combined 14-file
packet (146.60 seconds; 153.70 including preparation/cleanup). The same native
26-case packet remained included. A separate final wire-file run then passed
15 cases, adding actual old-client/new-provider compatibility without changing
the guarded capability refusal or fallback policy. No native case was waived.

The updater now revalidates eligibility only after lease contention; ordinary
non-contended work does not acquire extra per-tick IPC. No new cache, service,
process census, production deadline or mutation/publication lock design was
introduced. Linux and macOS ordinary behavior remains legacy; their new
observation-containment work is still explicitly open under #7944.


### 2026-10-27 second-round lifecycle and cancellation receipt

The affected 13-file packet ran through `tools/run-plugin-tests.py` with native
recovery opt-in: **548 passed, zero skipped**, 162.71 seconds pytest and 170.55
seconds including the existing runner's preparation/cleanup. It includes the
original deadline/held-pipe/caller-death cases and the new stored-cancellation,
secondary-cancellation/native-lease and deferred-invalidation regressions.

The native guarded cutover file alone passed **12 cases**, including a complete
successful handoff. The real old process remains alive behind a mandatory
cleanup barrier after accepting shutdown; the real adapter cannot finish until
its retained native handle signals exit. Successor published/roster/repository
readiness is observed before shutdown. Refused or unready promotion rolls back
without old retirement. A live previous-only owner with a closed listener is
refused before restart or cleanup effects for either prior-liveness hint.

The canonical seven-file procutil/ZDD packet ran through `TestSandbox`,
`isolated_environment` and `run_contained`: **359 passed, 15 skipped**, 33.72
seconds. Files: procutil `test_windows_observation_spawn.py`,
`test_job_teardown.py`, `test_caller_owned.py`; ZDD `test_cutover.py`,
`test_cutover_refusal.py`, `test_cutover_pre_adoption.py`,
`test_conditional_cutover.py`. Skips are native Linux pidfd/group/fork/fsync/flock
and Darwin/POSIX preparation paths, unavailable on this Windows worker. No
Linux container lane is available. The Windows injected creation fixture
substitutes CRT handles, binary/inheritance constants and Win32 errors so its
useful pure cases remain portable rather than being skipped on other hosts.

These receipts do not substitute for the exact required promotion unit packet,
current-head review/CI, a promoted release or separately authorized installed
validation. No production service or original interactive session was a fixture.


The exact required promotion unit packet subsequently completed at committed
source `5c353233e87fd1d8151a3652da250e3266c43986`: **2,407 passed**, no skipped
admitted nodes or forbidden effects, **153.812 seconds** against its unchanged
300-second whole-run cap. All 129 production coverage targets and the existing
source/helper closure were enforced; `unit-proof.json`, `unit-manifest.json`
and `coverage.json` were retained by the executing owner. Ten out-of-packet
nodes were deselected by the fixed manifest, not waived or newly admitted.

Execution used `TestSandbox`, `isolated_environment` and `run_contained` with a
fresh tooling venv outside the repository. The first chosen venv lacked
`coverage`; after restoring the declared dependency, its repo-local
site-packages correctly failed the source-read admission fence. Moving only
the test tooling to the isolated temporary root resolved that environment
mismatch; no guard, source closure, node list, effect prohibition or deadline
was weakened. Tooling matched the declared CI inputs through `seasoned_uv.py`.


### 2026-10-27 third-round regression and standalone-library evidence

Before correction, two library-native setup-budget regressions and eight initial
resident hook/cache/sibling-layout regressions failed for the reported causal
reasons. The changed six-file hook/cache packet then passed 295 tests in 50.98
seconds (79.55 seconds including managed preparation/cleanup). Its explicit
`test_observation_publication_fences.py` controls cover claim-first publication,
mutation-first publication/expiry barriers, real SQLite preservation, metadata-
only hook selection and actual `get()` population in the default sibling layout.

The documented standalone command was actually run, not replaced by a paired
PYTHONPATH environment:

```powershell
python tools/run-plugin-tests.py agent-procutil --test-timeout 90 --timeout 900 --plugin-timeout 2700
```

The two independently prepared sub-suites passed **505 + 155 = 660 cases** in
148.52 seconds including preparation and cleanup. **128 + 36 = 164** existing
platform/native opt-in cases were skipped. Both actual consumer-pairing test
functions now live in `test_atomic_observation_process.py`; the standalone
library tests neither import nor depend on `agent_worktrees`. The new command-
cutoff tests run as pure, injected native cases on this lane.

The wider 16-file native selection passed 756 tests and exposed one remaining
old untyped project-scope fixture. After selecting its legacy branch explicitly
and using the real carrier, its complete `test_status_monitor.py` file passed
184 cases. This is not a claim that the wider current-base packet, required
promotion packet, review/CI or installed qualification is complete. Linux Docker
is unavailable; native opt-in tests retain their existing contained fixture Jobs.

### 2026-10-31 current-base qualification failure

Candidate: `204f9241cb6edf0120769a6ebe9818a1e13ee7ce`, rebased onto current `dev`.
The five third-round fixes remain committed, but the current-base packet failed:
**732 passed, 14 failed, zero skipped**, 417.79 seconds pytest and 510.42 seconds
including managed preparation/cleanup. Retained local receipt:
`.venv-tools/r1a-currentbase-20261031.log`.

The normal runner selected these 15 files with
`AGENT_WORKTREES_NATIVE_RECOVERY=1`, `--test-timeout 90`, `--timeout 900` and
`--plugin-timeout 2700`:

- `test_atomic_observation_process.py`
- `test_git_observation_deadline.py`
- `test_git_ops.py`
- `test_status_segment.py`
- `test_windows_observation_custody.py`
- `test_observation_publication_fences.py`
- `test_observation_review_regressions.py`
- `test_hook_ipc.py`
- `test_status_monitor.py`
- `test_status_monitor_cutover_helper.py`
- `test_status_monitor_guarded_recovery.py`
- `test_status_monitor_observation_retirement.py`
- `test_status_monitor_guarded_recovery_native.py`
- `test_status_monitor_guarded_cutover_native.py`
- `test_git_observation_native.py`

Six atomic-consumer cases failed because the canonical fixture's Job constructor
does not accept the current SDK's `name` argument. Three warm-roster cases lack
the current `observe_records` adapter argument, and guarded monitor dispatch
receives no runtime generation. Actual native `stall`, `before-resume` and
`cancel` controls failed at their bounded marker gates. The real first-install
hook bootstrap stamp exceeded its lifecycle deadline. These require diagnosis
and repair; fixture mismatches do not waive actual native or bootstrap failures.

The first runner attempt used the cached test interpreter while preparation
tried to replace that interpreter, producing an access error. Re-running from
the independent Python 3.12.14 base interpreter reached the completed packet
above. No tooling or product dependency manifest changed.

Install contract, docs consistency, effort structure and whitespace checks pass;
module-size qualification fails at canonical procutil 1,028/1,000 lines,
worktrees main 6,763/6,761 and monitor CLI 1,017/1,000. Two existing procutil
manifest hashes differ from the final source. The current manifest contains
3,174 exact nodes and 176 coverage targets, rather than the historical 2,407/129.
The exact required packet was not run: its selected interpreter lacks coverage,
and canonical source binding still requires reconciliation. No earlier receipt
is represented as final-source proof.

Shared SDK source edits remain outside this continuation's admission; obtain
the existing owner's scoped decision for the procutil size/fixture seam before
continuing. No publication, fresh accepted review, required green CI, merge,
release, installed proof or broader-effort completion is claimed. The retained
source and canonical effort remain open and resumable.

### 2026-10-31 owned repairs and unchanged native-control rerun

The same complete 15-file packet now passes **746 tests, zero failed/skipped**,
232.08 seconds pytest and 268.56 including managed preparation/cleanup.
Receipt: `.venv-tools/r1a-owned-currentbase-20261031.log`.
The preceding three-file pairing/custody/native packet passed 66 cases;
the actual cold-hook stamp separately passed one case with 158 deselected.

The ten fixture mismatches were repaired only in worktrees tests: current
named-Job construction, `observe_records`, and the health module's bound runtime
generation. Actual native observation controls and the real first-install hook
were rerun unchanged with an existing supported Python selected on the child
PATH. All six native observation controls pass without extending any native
wait or production deadline. The complete rerun preserves the original causal
failure receipt rather than labeling it a successful skip or weakening its
assertions.

Existing live classification and retirement helpers now carry the duplicate
map and drain diagnostic/busy predicates. Both worktrees module-size guards
pass without raising a baseline; focused Ruff F/E9 and whitespace also pass.
The canonical SDK cap, standalone library/current-base qualification, final
promotion packet, source review and required CI remain gates. The existing
#9578 owner exclusively holds the coupled SDK correction; no SDK rewrite,
runtime rollout or source merge is inferred from this local consumer pass.
