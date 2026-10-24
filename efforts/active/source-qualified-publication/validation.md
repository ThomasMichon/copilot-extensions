# Validation contract

Back to [the effort](README.md) and [implementation contract](implementation.md).
Tests below are implementation obligations, not claims that new tests exist
or have passed. New test basenames are proposed explicitly.

## Fixtures and assertions

| Case | Exact home / boundary | Required assertions |
|---|---|---|
| Locked 10 → point unlocked 11 → 20 ticks | New `worktree-manager/tests/production_picker/test_point_refresh_publication.py`; actual `PickerScreen`, `LiveLoader`, Inbox and `_tick` | Gate feed at 10; apply real point response at 11; drain UI Inbox then tick 20 times. Screen and loader retain unlocked 11. Do not mock `_apply_loader_records` away. |
| Source collision | Same new screen test; existing `test_data_ssh_sources.py` and `test_current_roster_reads.py` | Local and remote sources both contain ID `tree`; remote adapter updates only its key. Local point provider is a fail-on-call spy. Verify correct source/project command, metadata and failed/unsupported remote result. |
| Ordering and reset | New `test_source_publications.py`; existing `test_current_worktrees_feed.py` and `test_current_worktrees_feed_deadlines.py` | Point 11 then feed 10, 12 and delayed point 11; simultaneous refreshes; incomplete feeds; reload epoch cancellation; same-generation stale reconnect; new generation revision 1 only after complete reset. Old generation never resurrects deleted rows. Equal-version conflicting content is diagnosed. |
| UI threading and lifetime | New point/store tests; existing `test_inbox.py` | Worker never writes screen data/debug or committed store. Mutations happen during UI drain. Picker cancellation rejects late events and terminates only owned children; no indefinite point overlay survives source retirement. |
| Gated reclaim | New `plugins/agent-worktrees/tests/test_worktrees_observe.py`; existing `test_worktrees_runtime.py`, `test_worktrees_client.py`; new Manager `test_maintenance_publication_receipts.py` | Reclaim effect succeeds once; block owner commit. Receipt/UI remain pending; unblock actual owner publication and converge on the same connected source without reload or full scan. |
| Correlation and time bounds | Same owner tests | An unrelated larger project cursor cannot satisfy the request. Receipt proves exact identity and covered facts sampled after admission/effect. Server max wait/ID limits enforced with fake clock/events; handler never waits holding the owner state lock. Queue saturation/expiry gives explicit result. |
| Contention and rollback | Existing `test_worktrees_projection.py`, `test_worktrees_recovery.py`; new observe tests | Record-stamp contention does not mean published. SQLite contention/failure leaves old head/rows/observations visible; no receipt or feed commit precedes durability. Owner election loss produces superseded, retains #7933 rollback behavior. |
| Partial bulk/new owner | New observe and maintenance receipt tests | Mixed success/failure/pending items retain individual outcomes. A new live owner appearing after reclaim is never cleared by killed-PID evidence; publication may truthfully remain locked. Missing response never retries destructive effect. |
| 10 × 1,000 unchanged real observations | Extend `test_worktrees_producer.py`, `test_worktrees_current_rows.py`, `test_worktrees_projection.py`, `test_worktrees_feed.py` | Run actual `materialize` provenance through real projection + negotiated v2 feed. After initial baseline, ten publications yield zero semantic table insert/update/delete and zero semantic `worktree` frames. Observation writes/frames are counted separately, not concealed. |
| One meaningful change | Same producer chain, title and state variants | Changing one title yields exactly one semantic row update and one semantic row frame; no selection index rebuild for title-only change. State/index changes update only the required index work. No 1,000-row normalization disguised as a “delta”. |
| Freshness | Same chain plus Manager feed/store tests | Advance fake observation clock with real observation; freshness advances independently of semantic version. Heartbeat alone advances last-contact only. Partial observation renews only covered groups. Cached/unavailable data never gains fresh timestamp. |
| Reconnect/delete/order | Existing producer/feed tests and `test_current_worktrees_feed.py` | Full reconnect matches current durable state; reorder/front insertion/empty roster exact; deletion only at commit; abort before commit keeps old view; persisted order survives advisory reopen; generation reset with revision 1 is atomic. |
| Compatibility | Existing `test_worktrees_client.py`, `test_current_roster_reads.py`, `test_current_worktrees_feed.py`; new observe tests | v1↔v1 unchanged; new consumer/old producer does not send unknown fields or claim receipt; old consumer/new producer receives truthful v1. v2 unsupported/malformed/degraded is explicit; no silent fleet fallback. |

## Command-level validation

Run from an isolated source worktree, stage new tests before snapshot runners.
Use Python 3.11 or newer and runtime-local tooling. On Linux with
Docker/devcontainers available, prefer the documented devcontainer wrapper.
On Windows use the contained source runners, not an installed runtime suite.
Set runner fixture/scratch roots inside the owned checkout when the execution
environment requires it. No tests may touch live tracking/process/monitor state.

Proposed targeted commands using existing runner interfaces:

```powershell
python tools\check-effort-vision-structure.py efforts\active\source-qualified-publication\README.md
git diff --check

# F1/F2: actual listed test modules and new observe module.
python tools\run-plugin-tests.py agent-worktrees -k "worktrees_current_rows or worktrees_projection or worktrees_producer or worktrees_feed or worktrees_runtime or worktrees_client or worktrees_recovery or worktrees_observe"

# F3/F4: the standalone containment runner has no -k passthrough.
# Its supported PYTEST_ADDOPTS environment selects one combined parser lane.
$env:PYTEST_ADDOPTS='-k "worktree_observations or current_worktrees_feed or current_roster_reads"'
python tools\run-standalone-tests.py worktree-manager
Remove-Item Env:PYTEST_ADDOPTS

python tools\check-install-contract.py
```

Before running a selection, verify the runner's `--help` and select native/
integration tiers explicitly as required by current `TESTING.md`. Existing
held-feed tests include real child stdout tests; do not report a unit-only
selection as having exercised those subprocess controls. Run `ruff check
--select F,E9` with the test/runtime environment interpreter over the exact
changed Python paths. Add one changefile for each changed shipped product;
the plan-only effort has no runtime changefile.

### Simple end-to-end (required for implementation)

Add an explicitly marked contained native test that launches an owned fixture
owner endpoint and invokes the actual source CLI for
`list --json --current --worktree-id tree --refresh`. Feed that response
through the real Manager parser/loader/Inbox, not a mocked parser result.
Stub destructive reclaim process resolution at the fixture boundary; verify
one effect invocation, real JSON receipt transmission, gated publication and
bounded wait convergence. Include a synthetic remote transport argv fixture
with local provider forbidden; no real remote agent is needed.

Keep these in a new `test_worktrees_observe_native.py` and the Manager point
refresh module's explicit native lane, using repository containment/marks.
Record full command, selected/deselected counts and actual source revision.

### Fresh-environment and live qualification

F1's durable schema evolution requires a fresh fixture database, v1 database
migration, rollback/old-reader refusal, and restart/advisory read controls.
These are clean-state integration tests, not a host installer rollout.
No installer/bootstrap change is proposed, so a full machine clean-room run
is not required for the plan-only PR; re-evaluate if implementation broadens.

Live destructive reclaim, remote dispatch, shared install/update and
pre-existing agent interaction are expressly outside authorization. Record
live external validation as **not run: authorization boundary**, not as
passed or impossible. A later authorized adopter can qualify real local and
remote transports without changing the source contract or moving ownership.

## Plan-only evidence

Validate structure, links and privacy; review code citations against the
inspected source baseline. Unit/native/clean-room/live implementation tests
are intentionally not run before there is implementation. Source PR review
and required narrative CI remain mandatory. Do not claim the three issues
fixed, close them, or mark the effort Done on a plan-only merge.

## F1 execution evidence

- The plan merged in #8021. F1 code is locally implemented, not yet source-merged.
- The contained Worktrees selection passes 145 tests with the separately
  failing native CLI startup test explicitly deselected. This is **not** an
  all-green source qualification.
- Actual producer/materializer, durable SQLite triggers and v2 feed receiver
  prove 1,000 rows × ten observation-only publications yield zero semantic
  writes/deltas; one title change yields exactly one update/delta. The new
  deliberately high-volume fixture has a 180-second containment timeout, not
  a runtime latency allowance.
- Native authenticated socket controls pass for both v1 and v2. The existing
  subprocess CLI full-roster test repeatedly fails its unchanged two-second
  initial deadline, including when run alone: zero frames, zero control
  requests, child still running, empty stderr. This blocks source completion;
  it is not waived as “pre-existing” or covered by the passing socket test.
- Manager's contained parser/feed selection passes 83 tests (3,482 deselected).
  An initial native startup-budget control failed during concurrent dependency
  preparation/testing; the complete selection subsequently passed twice with
  no test or deadline relaxation. This does not erase the independent,
  reproducible Worktrees CLI failure above.
- `ruff check --select F,E9`, the targeted effort-structure check and the
  install-contract guard (13 plugins) pass.
- No CLI startup, Manager startup, or deadline expansion was made. A broader
  startup diagnosis needs separate admission. F2/F3/F4 and live rollout remain
  outside this implementation slice.

### Bounded startup diagnosis (2026-10-23)

The separately admitted diagnosis compared the exact same native case,
1,100-row payload, contained environment, prepared CPython 3.12 test
interpreter and original two-second verdict. The command remained
`python -m agent_worktrees --project project list --json --current --stream
--follow --include-other-platforms --all`. Only failing-test diagnostics
continued collecting phase events for up to ten seconds; missing two seconds
still failed, unconditionally.

| Source | First health request | Feed request | stdout begin | stdout done |
|---|---:|---:|---:|---:|
| F1 `ee0baacc4` | 8.640 s | 8.656 s | 8.844 s | 8.859 s |
| Exact pre-F1 source `e53d8d24e` | 6.110 s | 6.110 s | 6.297 s | 6.328 s |
| F1, separate child-only import-profile observation | 5.828 s | 5.859 s | 6.078 s | 6.094 s |

For the baseline comparison, only F1-changed Worktrees source files were
temporarily restored from the committed parent in the same owned worktree;
the identical diagnostic test and interpreter were retained, and all F1 source
bytes were restored afterward. No reset, shared runtime or other worktree was
used. Parent publication happens before the child-start clock, so that
materialization is not the measured pre-control interval.

The standard Windows total-processor counter sampled 100% before and after
both unprofiled observations. These are **uncontrolled loaded-host samples**,
not a worst-case bound or evidence assigning the between-run delta to F1.
Both source versions fail before any owner request; actual owner/feed transfer
then completes in roughly 0.2–0.3 seconds in these samples.

Child-only import profiling (`WORKTREES_FEED_IMPORT_PROFILE=1` on the contained
test) attributes cumulative import costs to `agent_worktrees.activity`
(1.056 s), `agent_procutil` (0.889 s, including `asyncio` at 0.825 s),
`agent_worktrees.config` (0.646 s) and `worktrees_client` (0.560 s).
Cumulative timings overlap and must not be summed. F1's added
`worktrees_observation_contract` is 0.072 s cumulative and 0.0035 s self;
its dependency work is also needed by projection/index admission. This does
not support blaming F1 materialization or claiming that lazifying its tiny
self cost would repair the multi-second pre-control failure.

The CLI entry's eager import/dispatch boundary is
`plugins/agent-worktrees/src/agent_worktrees/__main__.py`. Live forge file
inventories show both #8045 and #8031 modifying that exact file; #7788 also
owns its eagerly imported `update_stage.py`. Those claims are preserved.
No functional entry/import-chain change was made and no existing agent was
contacted. This is a concrete baseline plus ownership blocker, not a deadline
exception; F1 publication remains held for an admitted owner-respecting
startup solution or reviewed gate disposition.

### Admitted entry repair qualification

The entry amendment merged as #8139 (`e8108d45955506268282881c0097dcfba8fed959`)
before functional work. Original F1/evidence commits remain on
`backup/f1-before-entry-plan-1ab`; their rebased candidates remain on the
implementation branch. The source plan was reviewed on a documentation-only
branch in the same owned worktree, not by publishing the blocked code.

Targeted selector/context/client checks ran before the native case. The final
combined selection reported **62 passed, 1 failed, 72 deselected**; the failure
was the actual console's unchanged two-second deadline. Earlier context
fixtures named “outside repo” inherited the enclosing source checkout because
this execution environment requires scratch beneath the checkout. An owned
invalid Git marker at the scratch boundary prevented ancestor discovery;
the affected context selection then passed without changing production code
or protected test files. Selector-effect test instrumentation was also fixed
to restore patched builtins before pytest reporting. These are test-fixture
corrections, not runtime deadline exceptions.

| Entry into the same 1,100-row case | First health | Feed request | stdout begin | stdout done |
|---|---:|---:|---:|---:|
| `python -m agent_worktrees` | 4.844 s | 4.891 s | 5.110 s | 5.141 s |
| Guarded `-m` (rejects broad command/recovery imports) | 3.828 s | 3.843 s | 4.062 s | 4.078 s |
| Actual console executable from the source-owned test environment | 3.953 s | 3.953 s | 4.141 s | 4.187 s |

Each verdict remains **FAIL at two seconds**, even though bounded failure-only
diagnostics capture eventual completion. The console test verifies the actual
installed declaration before invoking its generated executable, not merely a
Python function. The guarded child refuses `activity`, `front_door_cli`,
`list_cli`, `status_monitor_runtime` and a second dispatcher import. Its empty
stderr and completed snapshot prove those forbidden imports were not required
by the repaired healthy path. This does not qualify the latency gate or prove
full post-initial-feed cancellation under that failing case.

Processor samples around the initial repaired native group were again 100%.
No controlled-load/worst-case conclusion is drawn, and neither the threshold
nor the owner/server/shared-library scope was expanded. The earlier 145-test
Worktrees, 83-test Manager, native-socket and 1,000-row F1 packet is retained,
not claimed rerun against the latest rebase.

Install-contract validation still passes for 13 plugins. Touched-file
`ruff 0.11.13 check --select F,E9` reports two F811 `procs` shadow findings in `__main__.py`
outside the admitted early hunk. A stdin check of the exact pre-entry `HEAD`
reports the same two findings (baseline lines 4818/4965; candidate +10).
No suppression or unauthorized modification was made; these findings and the
native failure remain explicit source-publication blockers.

### Integration with merged #8045

After confirming merge `419a12d054193e3253f91a8395444608116fa1e3`, the
candidate rebased cleanly to `60f2e37ee`; its only `__main__.py` delta versus
that base remains the ten-line early selector. The open #8031 head was
rechecked as `a3f34952706fe1020558c7181daacd0555d50f67`, with disjoint
import/launch/sweep hunks; #7788 remained open at
`afb2d47f7ab156c3e6ab9ba6d31624c46a91eda9`.

Only the original module case was rerun, not the full suite or a new
benchmark: **1 failed, 35 deselected**. It recorded health/feed at 2.703 s,
stdout begin at 2.875 s and done at 2.907 s. Zero frames arrived within the
unchanged two-second verdict. A preceding total-processor sample was again
100%; this is not a controlled-load or worst-case claim. The console/guarded
and broader F1 packets remain the explicitly earlier evidence above.
No further import/library/owner change was made after this failing gate.

### Bounded inert controls and original evidence retention

Source candidate: `f45fe9696d89b5f4abf77b84514ff5f175ba646e`.
Exactly three serial children ran `python -c
"print('F1-INERT-READY', flush=True)"`, with no explicit imports and normal
implicit site/pth startup. This is an **inert workload, not the Worktrees CLI**.
The prepared interpreter was CPython 3.12.14, using the same source-owned test
environment and canonical `run-plugin-tests.py` containment/conftest
construction. All three inherited the same environment fingerprint.
Fixture paths were newly allocated; the old full-roster raw environment was
not retained, so no byte-for-byte equality with that old environment is
claimed. No roster producer/control server ran for the inert children.

The measurement starts at parent `time.monotonic()` immediately before
`Popen`, with the same captured-pipe/DEVNULL/UTF-8/CREATE_NO_WINDOW boundary
as the native case. Each control had a ten-second deadline. Raw floating-point
timestamps and outputs remain in the bundle; this table rounds seconds:

| Control | Spawn returned | First stdout | Process exited | Exit / stderr |
|---|---:|---:|---:|---|
| 1 | 0.015 | 0.328 | 0.359 | 0 / empty |
| 2 | 0.015 | 0.343 | 0.359 | 0 / empty |
| 3 | 0.000 | 0.234 | 0.250 | 0 / empty |

All readers joined; no errors or timeouts occurred. The diagnostic pytest
case passed once. It was not rerun and no actual full-roster case was retried.
Fresh preceding `Get-Counter '\Processor(_Total)\% Processor Time'`
samples at one-second cadence were 99.6133542160198%, 100.0% and
99.52040752984144%, each status 0. They precede the controls by roughly nine
seconds and are not instantaneous per-child utilization or a worst-case proof.

These samples do **not** support an unavoidable two-second interpreter/host
floor. They also do not isolate application cost from scheduling variation
across the longer CLI workload. Do not subtract these values from another
run or add cumulative import spans to fabricate an application budget.

The source graph still retains `config`, canonical registry/CWD handling and
authenticated control-client imports. In particular,
`status_monitor_cutover.py:20` imports `agent_procutil`; its shared
`libs/agent-procutil/src/agent_procutil/__init__.py:52` eagerly imports
`asyncio`. The synchronous `no_window_flags`/`no_window_kwargs` functions at
625/638 do not use it; the executable asyncio uses in that module are in
`spawn_in_kill_on_close_job` at 427/434 (plus its annotation at 411).
This is a concrete owner dependency, not measured proof that moving it will
pass the CLI deadline. A shared-library owner may investigate lazy async
loading while preserving async APIs, annotations/introspection and process
semantics; F1 must not copy the primitive or edit that excluded owner surface.
Otherwise qualification needs an explicitly coordinated quiet lane. Neither
path is an automatic retry or authorization to weaken the gate.

**Raw receipt:** twelve original artifacts, 20,534-byte JSON bundle,
SHA-256 `a70331db3c056ac8b6989e65839f2fd7aa8188c36d058d3fd7290fff889aa145`.
Each artifact retains its original bytes and individual digest. Samples and
metadata were exported outside the runner's cleanup root before that cleanup;
the diagnostic source was copied before execution. The full compressed capsule
is stored on the owning task's evidence card, and decompression/readback
verified the exact bundle hash before the temporary diagnostic source was
removed. The original local bundle is also retained; it is not a public-source
artifact because it contains environment/interpreter paths.

**Lint clarification:** raw Ruff 0.11.13 candidate and baseline logs are in
the same bundle. Candidate `__main__.py:4830` is the `procs` parameter of
`select_orphan_launcher_shells`; line 4977 is the `procs` local in
`_enumerate_launcher_shells_windows`. Both F811 findings refer to the imported
compatibility re-export at line 110. Baseline
`240c97586b80e8a3d8ed3d41e0aae5a751c08c83` reports the corresponding
4820/4967 sites and import 100. All are outside the admitted early selector
at 51–60. No rename, removed re-export or suppression was made.
Required CI uses a seasoned, unpinned Ruff installation (`ci.yml:41`) and
`ruff check --select F,E9 --output-format=github .` (`ci.yml:158`).
These local older-tool findings are **not an observed failing required-CI
result for this unpublished candidate**; canonical-tool disposition remains
unverified. The independent native deadline still blocks qualification.

### Integration of L/E/G3 and retained complete native output

Dependencies are actual merged ancestors: #8190
`72be3f01d934901d4dc891baf46f6866fc484879`, #8045
`419a12d054193e3253f91a8395444608116fa1e3`, and #8163
`80b1b0415c765dd440c2c05f5e7fa3e8e6fccaad`.
The G3 conflict was reconciled, not discarded: the exact `doctor`
recovery-report recognition now precedes configuration import in
`invocation_project.resolve_invocation_project`; the front-door symbol
re-exports that same implementation. Targeted tests prove no project lookup,
active-project mutation or CWD change, including G3's existing actual
front-door parameter matrix.

Before the permitted Ruff fix, the full open-PR file inventory was read with
pagination completed for every truncated result. The main-file claims in
#6009, #7611 and #8031 had disjoint hunks. Only the unused import binding
changed (`procs as _procs_module`, followed by `procs = _procs_module`);
no function logic, public module name or `procs` parameter changed.
The same Ruff 0.11.13, now with explicit canonical plugin configuration and
`--select F,E9`, passes. A regression asserts module identity and the public
selector's parameter name. No suppression, gate change or active owner-hunk
modification was used.

At source `a870063da3abd3222974dbdd0dd52948817adc92`:

- Targeted parity/client and actual release-materializer import controls:
  **100 passed, 48 deselected**. The materializer copied the complete
  canonical package, retained byte-identical `_async.py`, and the isolated
  materialized child proved synchronous imports do not load asyncio while
  the private async export remains available. This is a contract control,
  not a latency benchmark; L's separate wheel/full packet is retained,
  not claimed rerun.
- The **one** unchanged actual module 1,100-row case:
  **1 passed, 35 deselected**, including initial snapshot, one-row update,
  cutover, zero exit and joined readers. First health 1.157 s, feed 1.172 s,
  begin 1.328 s, done 1.360 s, subsequent commit 1.594 s.
- Pre-invocation Get-Counter samples were 57.0289088731416%,
  71.59050634114806%, 69.40754776458643% at one-second intervals.
  This differs from earlier loaded-host observations: no causal speedup
  or worst-case latency conclusion is inferred.

Only test-counter export changed afterward, at
`f60649ebc705908c7f48aa4f17d5a5d5bbf578b5`; production source is identical.
Combined F1 selectors excluded the already-run module case:
**272 passed, 1 failed, 1 deselected**.

| Entry | Health | Feed | Begin | Done | Outcome |
|---|---:|---:|---:|---:|---|
| Actual generated console executable | 1.531 s | 1.531 s | 1.703 s | 1.734 s | Pass; later commit 2.031 s, cutover/exit/join passed |
| Instrumented guarded-module harness | 1.938 s | 1.953 s | 2.156 s | 2.188 s | Fail at unchanged initial two-second gate |

The guarded case emitted no import-refusal error. Its extra `-c` harness
imports `importlib.abc`/`runpy` before installing the finder; that is not the
plain `-m` workload. A possible next *test-only* investigation is whether
the finder needs its ABC import at all (Python's finder protocol uses
`find_spec`). It was **not changed or retried** after this failure.
No instrumentation-overhead attribution or deadline exception is asserted.
Latest Manager-wide parser selection was not repeated after the failing
combined gate; the earlier 83-case packet remains explicitly earlier evidence.

The real producer counter export records ten publications of 1,000 rows:
every observation-only publication has zero semantic frames and zero
semantic writes; the one title change records exactly
`UPDATE tree-0017`. The current combined packet also retains compatibility,
atomic publication, recovery and v1/v2 control tests.

**New raw receipt:** 37 original artifacts, 2,885,776-byte bundle,
SHA-256 `eb64d3a0ac0c8fe3482e776194b1194b4616b0e6e774b4f3525d739dc00f744c`.
It contains full native stdout/stderr, phase/outcome records, interpreter and
source identities, load samples, semantic counters, materialized import output,
test-source copies, original runner logs and fresh claim/lint evidence.
Exports occur outside automatic fixture cleanup, in test `finally` paths for
native results. The parity JUnit file was subsequently located at a mangled
drive-relative path: backslashes in the ambient pytest option had been
interpreted during argument splitting. It records 100 tests with zero
errors/failures/skips. Its original bytes were copied and stored as a separate
22,112-byte raw supplement, SHA-256
`9910e85f482b7726a6fe2b302cb0602278ea77c8a6251b6bb720f2d04747dee2`,
on the owning task card; decompressed readback was verified before the
misplaced generated file was removed. The earlier claim that XML was not
emitted was incorrect; the main 37-artifact bundle remains immutable and the
supplement adds the recovered original rather than rewriting that bundle.
The bundle includes the prior task-card capsule and is stored in a proposed,
**non-runnable evidence-only carrier**, with complete payload readback and
decompressed hash verification. The owning F1 task points to that receipt;
it is not marked complete. Original local artifacts remain retained.

### Plain finder protocol: guard-only qualification

The [Python meta-path protocol](https://docs.python.org/3/reference/import.html#the-meta-path)
requires an object implementing `find_spec(name, path, target=None)` and
propagates exceptions to abort import. No ABC behavior was used by this
guard. At `e8a597bb47f1bd849d83fc619fbbcc1de35ea57a`, only the test changes:
the child no longer imports/inherits `importlib.abc.MetaPathFinder`.
The guard remains a plain class with the same method, counter and all
forbidden-module predicates. No proxy framework or product change was added.

An AST proof compares the original script/native function at `764e90601`:
after normalizing only the removed ABC dependency, both script ASTs and the
entire native function AST are identical. Observer initialization still runs
inside the timed child. Invocation, 1,100-row workload, `Popen` timing
boundary, two-second assertion and output/error checks did not move or weaken.

- **6 passed, 36 deselected:** real import attempts for each of the four
  forbidden modules and the second dispatcher lookup are blocked before a
  following finder's effect; the allowed-import control succeeds.
- **1 passed, 41 deselected:** the actual guarded native case, using that
  same class, completes health/feed at 1.266 s, begin at 1.438 s, initial
  done at 1.469 s and update commit at 1.734 s. Cutover, zero exit and
  reader cleanup also pass.
- **39 passed, 3 deselected:** the affected feed contracts, excluding the
  three already-measured actual CLI variants. No broad blind rerun.
- Targeted Ruff and the 13-plugin install-contract check pass.

Fresh preceding total-processor samples were 96.05507957853825%,
97.05381662420625%, 99.34442998043427%. These are not controlled equal-load
comparisons or worst-case qualification. The prior actual module 1.360 s and
console 1.734 s passes remain their own finite evidence; full GUI/installed
and remaining campaign acceptance stays open.

**Passing raw receipt:** 17 original artifacts, 812,457-byte bundle,
SHA-256 `ba1be0186c7ee5aea71092b00141d9dbe7b77cede5a0da1ab86c2d01dbe343ee`.
It includes the exact guard source/diff/AST proof, raw control logs, native
stdout/stderr/phases/interpreter/source/outcome, load samples and guards.
It is retained in a proposed **EVIDENCEONLY** carrier with exact decompressed
readback verification. The prior carrier and prototype remain preserved and
non-runnable; no queue/spawn/execute action was used.

### Required source publication gate

The published candidate is `9bd44cff26fae9821b16444eadcad55f34acf94e` against
base `c1e4e48867c7242e0a7f62b35930f01eb27a1376`. Managed publication rebased
and squashed the retained commits; earlier prototypes remain backed up.
The rebase's additional runtime files belong to already-landed upstream work,
not new F1 edits. Local all-changed-file Ruff passes on that stable published
head using its merge-base diff.

An earlier lint invocation mistakenly used a two-dot comparison against an
advancing shared `origin/dev`, which selected upstream-only missing files and
reported E902. The corrected merge-base/ACMR selection checked 22 actual
changed Python files and passed; no code finding was suppressed.

Required CI run `39954062531` reports successful eligible units, workflow
lockdown and source attribution, but its module-size guard fails:

```text
plugins/agent-worktrees/src/agent_worktrees/__main__.py:
6773 lines, grandfathered shrink-only ceiling 6761
```

The required aggregate therefore fails too. This is not a flaky test, an
installation issue or a permitted gate exception. The twelve-line increase
comes from the early entry and preserved public process-module binding.
Raising the baseline or trimming unrelated comments merely to satisfy the
counter is not the proposed remedy.

A production-code extraction would be needed, outside the current test-only
admission: keep a small real-`__main__` call to an entry-owned dispatch helper,
and retain the public `__main__.console_entry` name/signature while delegating
its existing `_shutdown_exit.run_and_exit(main)` composition to the same
entry module. This can reduce the monolith without changing command,
shutdown, authorization or timing semantics. No such change was made.
Source review remains pending and the PR is not merged/finalized.

### Size-compliant extraction and wire-admission corrections

The exact [entry/shutdown extraction](entry-size-extraction.md) is committed
locally in `5a946fd6f8d4362e3d079c4fc799c0f9fba1eff1`. Root
`console_entry` still resolves `main` at call time and delegates through the
unchanged exit policy. The actual module-size check passes at 6,760 lines
against 6,761; the entry module is 111 lines. No baseline or packaging change
accompanies that extraction.

The latest native case at that commit retains the same 1,100 rows, timer
before `Popen`, observer initialization inside the child, two-second initial
deadline, output/error checks and cleanup:

| Entry | First health | Feed | Begin | Done | Verdict |
|---|---:|---:|---:|---:|---|
| Module | 4.922 s | 4.938 s | 5.110 s | 5.141 s | Failed |
| Console | 4.282 s | 4.297 s | 4.485 s | 4.516 s | Failed |
| Guarded module | 4.234 s | 4.265 s | 4.421 s | 4.453 s | Failed |

Each ran once. All report `qualified: false`, empty stderr, cleanup return
code 1 and joined readers. Pre-run CPU samples were approximately 100%.
These are loaded-host failures, not causal performance attribution or a
deadline waiver. Earlier native passes are historical, not current
post-extraction qualification. No new native case or JUnit output was produced
during the subsequent wire corrections.

Both outstanding Medium findings on #8217 were independently reproduced:

- Actual `WorktreesRuntime.handle()` failures, serialized through JSON, retain
  contract version 1 for v2 requests. Before correction, invalid selectors
  lose `ProjectionError` and unready/superseded replies lose their real
  diagnostics to a version mismatch (3 failed, 11 passed, 22 deselected).
  The client accepts a shared v1 failure only with explicit `ok: false` and
  nonempty string code/message. Success still requires the negotiated
  version; unknown versions, malformed failures and unnegotiated v2 failures
  are rejected.
- Valid rows of exactly 65,536 compact UTF-8 bytes fail after locally added
  provenance is counted at `done` (6 failed, 5 passed before correction).
  After correcting that snapshot seam alone, the distinct observation-only
  update still fails at `commit` (2 failed, 3,988 deselected). Both barriers
  now remove only `_projection`, `_availability`, `_observations` and
  `_observation_derivations` from previously admitted rows before complete
  revalidation and reconstruction. Raw ingress still counts every field.
  ASCII/multibyte near-limit snapshots and observation-only updates pass;
  oversize raw rows, including each reserved metadata name, fail at both
  ingress paths without replacing committed rows/head.

The contained Windows source commands (prepared Python 3.12 environment;
runner scratch rooted inside the owned checkout) were:

```powershell
python tools\run-plugin-tests.py agent-worktrees `
  --test-file tests/test_worktrees_client.py `
  --test-file tests/test_worktrees_feed.py `
  --test-file tests/test_worktrees_observations.py `
  --test-file tests/test_shutdown_exit.py `
  --test-file tests/test_current_roster_entry.py `
  -k "not (actual_cli or real_transport or cross_real_authenticated_control_socket or real_producer)"

$env:PYTEST_ADDOPTS='-k "(worktree_observations or current_worktrees_feed or current_roster_reads) and not (actual_loader_subprocess or reconnect_resolves_new_generation or terminal_errors_stop_actual_child_respawn)"'
python tools\run-standalone-tests.py worktree-manager
Remove-Item Env:PYTEST_ADDOPTS

python tools\run-plugin-tests.py agent-worktrees `
  --test-file tests/test_worktrees_client.py -k unnegotiated_v2_failure
```

Results: **134 passed, 11 deselected** in Worktrees; **92 passed,
3,903 deselected** in Manager; **1 passed, 45 deselected** for the last added
envelope control. The latter adds a test only; production bytes are unchanged
from the 134-case pass. Manager initially could not collect an unrelated module
because its prepared environment lacked declared `process_telemetry`; the
supported `--prepare` restored that dependency before the reproductions.
Eight unknown-marker collection warnings remain visible, not suppressed.
Ruff with each product's configuration and `--select F,E9`, actual scoped
module-size validation, `git diff --check` and the 13-plugin install-contract
guard pass.

Documentation impact: these repairs restore the already-specified wire and
atomic-publication contracts; no public interface or architecture is added.
The entry status and this evidence are updated. Existing changefiles cover
both shipped products. The earlier 1,000-row/ten-publication semantic-write
counter packet is retained, not claimed rerun by this narrower selection.
No loader/UI, effect, installer, installed runtime, remote venue, shared process
library, durable schema or writer-incarnation change is included.

The next native selection remains `test_worktrees_feed.py -k actual_cli`
with explicit native-tier admission through the contained plugin runner.
Allow approximately one minute for its three serial owned variants, plus
preparation/cleanup margin (request a two-minute quiet window). This is an
estimate, not a runtime-budget increase. Run only after quiet-lane admission.
Until it passes unchanged, source publication, review-thread resolution,
fresh review, required green CI and actual merge of #8217 remain open.
The effort and parent trackers are not complete.

### Complete v2 wire-row budget

Draft #8217 reached `b5ab565a` with required green CI. Fresh review
`5635819089` confirmed the two earlier corrections and identified a distinct
complete-wire-row budget mismatch. The admitted source fix keeps
`_encode_row`'s legacy field/type checks and existing semantic-version checks.
A small v2-specific check encodes the complete projected row as compact JSON,
`ensure_ascii=False`, UTF-8, including the actual `semantic_revision`.
It runs before the atomic transaction, at v2 read construction, and at both
engine admission paths. Manager's existing raw budget and its post-admission
receiver-metadata exclusion remain unchanged.

The unchanged baseline reproduced **one failed test**: a real
`WorktreesRuntime`/SQLite publication accepted 65,536 stored bytes, emitted
65,558 v2 bytes at revision 1 and was rejected by the actual Manager parser.
After correction, the same regression rejects the wave before any durable
head/row/observation/order change.

Actual new cases, verified from the emitted JUnit:

| Case | Coverage / result |
|---|---|
| Exact 65,536 / 65,537 wire bytes | Accept / reject, each with ASCII and multibyte text at semantic revisions 1, 9, 10 and 9,223,372,036,854,775,807: 16 cases |
| Real producer-to-Manager path | Actual runtime/SQLite publisher, one-shot engine validation, real serialized publication frames, engine feed receiver, Manager parser/held-feed receiver and durable advisory reread; no mocked publication or Manager result |
| Rejected wave | First publication and partial replacement batches leave all durable projection tables and the previous in-memory publication unchanged; another project remains isolated |
| Revision digit growth | A row exactly at 65,536 bytes at semantic 9 becomes 65,537 at semantic 10 and is rejected atomically |
| Semantic no-op | The next observation-only wave may advance head 9 → 10 while row semantic revision stays 9: zero semantic writes and zero semantic row frames |
| One valid change | Reducing content by the actual added revision digit admits exactly 65,536 bytes at semantic 11: one semantic update and one row frame |
| Both engine admissions | Oversize raw rows rejected at revisions 1, 10 and maximum signed-SQLite revision |
| Receiver metadata | Accepted Manager rows exceed the wire bound after decoration but still commit through their existing metadata-stripping publication barriers |
| Legacy capacity | A full 65,536-byte v1 row with observation fields remains usable by the new publisher; an actual compatible older-writer SQL publication of a full-capacity row remains readable as v1 and fails explicitly only when its projected v2 encoding is oversized |

The maximum revision fixture seeds a valid committed head immediately before
that revision rather than executing billions of waves. Revisions 9/10 are
reached through real preceding publications. The new boundary matrix has
23 test cases, not a native performance benchmark.

The existing scoped Worktrees selector was extended only with the directly
affected `test_worktrees_projection.py` module and the new cases in
`test_worktrees_observations.py`: **192 passed, 11 deselected**. It retains
the exclusions `actual_cli`, `real_transport`,
`cross_real_authenticated_control_socket` and `real_producer`. Manager's
same parser/feed selector reports **92 passed, 3,903 deselected**, with
the eight previously recorded marker warnings still visible. Both actual
JUnit files were located and parsed: 192/92 cases, zero failures/errors,
including all 23 new boundary cases. No missing XML is inferred as success.

Ruff F/E9, the actual scoped module-size guard, install contract and diff
checks pass. No baseline, schema, election, writer-incarnation, receiver/UI,
entrypoint or installed-runtime change is included. Existing product
changefiles cover this correction.

Documentation impact: the engine/Picker contract now specifies complete v2
wire-byte accounting and unchanged legacy capacity; this effort records its
validation and retained native gate. Performance impact: one bounded compact
encoding per candidate row in the existing publication loop, and per v2
admitted/emitted row; no new process, network/history scan, projection writer
or semantic replacement is introduced. No launch-latency improvement or
post-fix native qualification is claimed.

The correction still requires ordinary draft publication, resolution of its
specific thread only after the real fix is published, fresh accepted review
and required current-head CI. The coordinator retains the independent
unchanged two-second native hold; no ready/merge/rollout action is authorized.

### Closure observation removal and the actual Manager selector

The complete-wire correction `6dce27dc` was actually published on draft #8217.
Required CI run `40050507451` passed against that exact head. Fresh Copilot
review `5636246760` confirms the wire finding resolved, but its overview
contains a substantive previously missed Medium despite saying “No open
issues”: copying retained raw payload can restore a removed optional
`closure.computed_at` in live/advisory v1 reads. That is not a passing clean
review and was not dismissed because it lacked a separate inline thread.

The removal regression failed first; the timestamp-refresh control passed.
The one-line correction starts v1 reconstruction from `semantic_row(row)`
before overlaying current observations. Two regression cases cover live and
advisory output, actual v1/v2 feed reconstruction through Manager, retained
stored payload, unchanged semantic revision, and zero semantic writes/deltas.
The existing scoped Worktrees packet now reports **194 passed, 11 deselected**.

The same existing Manager selector now reports **87 passed, 5 failed,
3,903 deselected**, with its eight visible marker warnings. This selector
matches `test_current_worktrees_feed_deadlines.py` as well as the parser/feed
modules, so it includes real subprocess startup/deadline controls. Earlier
“pure”/“unit-only” descriptions of the 92-case selection were inaccurate;
the earlier 92-pass executions remain historical results, not evidence that
the selection had no native effects.

The failed cases are:

- `test_feed_process_startup_uses_source_budget_not_publication_budget`,
  both local/source variants.
- `test_feed_transient_deadline_recovers_without_partial_or_empty_overwrite`,
  startup, initial-publication and partial-frame variants.

No causal attribution or accepted-flake claim is made. The failure logs and
actual JUnit are retained. No unchanged retry, threshold relaxation, skip or
out-of-scope loader/test repair was performed. This is an additional explicit
qualification blocker, not the shared CI HTTP47 issue and not a waiver from
the separate unchanged Worktrees two-second native gate.

The correction remains in the admitted review-fix scope, with no new durable
schema, writer, entrypoint or installed behavior. Documentation now states
that v1 output is reconstructed from semantic fields plus current observation
metadata. Draft review/required-CI publication may proceed under the explicit
qualification holds; source completion, readiness, merge and rollout may not.

### Reusable input dictionaries

After normal backed refresh, `81dedbf3` included merged #8392's repair as an
ancestor with identical relevant HTTP47 registry/fixture bytes. A fresh scoped
read confirmed that this exact head was actually published despite the push
client's 30-second timeout; required CI `40053692459` passed. No push retry,
CI retry or increased transport deadline was used to establish that result.

Fresh review `5636432797` found a High at
`worktrees_projection.py:317`: the encoder captures separate payloads, but
retained raw input references can alias the final object yielded by a reusable
dictionary generator. The reproduced cases fail with v1 observation times
`[12, 12]` instead of `[11, 12]`, and `KeyError` when the valid generator
clears its dictionary after the last yield.

The narrow correction decodes the already-validated payload before retaining
the row and computing its other captured fields. It snapshots nested values,
not only the outer dictionary. Two regression cases cover reuse and final
clearing across two real publication waves, live/advisory v1/v2 output,
observation IDs/timestamps, serialized engine/Manager feed admission and zero
semantic writes/deltas on unchanged semantic rows.

The focused source selector is:

```powershell
python tools\run-plugin-tests.py agent-worktrees `
  --test-file tests/test_worktrees_observations.py `
  -k "complete_wire_budget or closure_observation_reconstruction or publication_snapshots_reusable"
```

It reports **27 passed, 12 deselected**, with actual JUnit retained.
The unchanged native/Manager timing controls were not rerun. Their prior
failures remain qualification blockers, not assumed fixed by upstream refresh
or by a green pure-unit CI gate.

Documentation impact: the engine contract specifies per-row snapshot ownership.
The additional cost is one bounded decode of each already-encoded candidate;
no process, history scan, second publication writer or schema change is added.
Fresh publication, review and required CI remain necessary for this correction,
while readiness, merge, native retry and rollout remain unauthorized.

### Explicitly authorized contended-host tier

The operator subsequently authorized a bounded test-only contended-host tier
after profiling the loaded host: begin at 2x and permit one 3x follow-up only
for declared timing/fixture-admission expiry, never to mask semantic,
restart-count, ownership or cleanup findings. No quiet-machine prerequisite
was imposed for this tier. This changed test-run authorization, not production
defaults or the original nominal performance goals.

Timed executions used test-only commit
`5152829024475591cec147467bfea7229ec5c035`, on production ancestor
`19f58003`. Production source/configuration diffs are empty. Two local
test-module helpers accept only factors 1/2/3, default 1; normal CI/default
behavior stays at factor 1.

#### Budget classes remain distinct

- Worktrees observer goal: nominal 2s, contended acceptance 4s/6s.
  Step waits 2s → 4s/6s, reader/feed-close waits 1s → 2s/3s,
  failure-only diagnostic cap 10s → 20s/30s.
- Manager selected fixture source timeout 2s → 4s/6s, observer wait
  5s → 10s/15s, cleanup join 3s → 6s/9s.
- Artificial Manager publication/idle overrides .15s/.4s scale with
  synthetic delays: startup .4s, negative stall 10s and healthy heartbeat
  .1s all multiply by the same factor. Thus .15f < .4f < 2f and
  heartbeat period remains below idle expiry. Startup-only controls do not
  override the unused production idle default.
- Outer watchdogs are separate: Worktrees item/group/plugin
  30/300/900 seconds become 60/600/1800 at 2x and 90/900/2700 at 3x.
  Manager's 600-second contained-run ceiling becomes 1200/1800.
  Resource/process caps do not change.
- Real Worktrees client/read/idle and Manager production deadlines remain
  unchanged. Per-test monkeypatches and synthetic `Source.timeout` values
  are not committed runtime settings.

Static AST comparison preserves all original 74 Worktrees and 26 Manager
assertions (normalizing only the explicitly scaled test wait names), the
exact `Popen` call, its preceding real monotonic start and the guarded child
observer script. Additional checks strengthen report/cleanup validation.
No test uses clock retiming.

#### Actual sequential results

| Selected case | Tier / acceptance budget | Actual result |
|---|---|---|
| Manager startup local/source variants | 2x | Both pass; exactly one child each |
| Manager recovery startup / initial-publication / partial-frame | 2x | All pass; exactly two children each, no partial/empty overwrite, complete owned cleanup |
| Worktrees module | 2x / 4s | Full case pass, initial completion 3.843s |
| Worktrees console | 2x / 4s | Fail: observer expiry; diagnostic snapshot done at 4.406s |
| Worktrees guarded-module | 2x / 4s | Fail: observer expiry; diagnostic snapshot done at 4.297s |
| Worktrees console | One 3x / 6s follow-up | Full case pass, initial completion 2.047s |
| Worktrees guarded-module | Same single 3x invocation / 6s | Full case pass, initial completion 3.985s |

Manager ran first, then Worktrees. Manager JUnit records 5/5 passes.
Worktrees 2x JUnit records one pass/two failures; its raw failures are retained.
Before escalation, both late snapshots independently passed engine/Manager
admission: 1,100 exact identities, declared counts, correct fields, no error
frame or premature visible publication, joined readers and owned termination.
Update/cutover were not reached in those failed 2x runs, so they were not
claimed as passed. The single 3x invocation selected only console/guarded,
and JUnit records 2/2 passes including all update/cutover/exit/cleanup checks.
No fourth factor, extra timed retry or passing-module rerun occurred.

Aggregate CPU samples immediately before Manager were 99.04/100/100%;
before Worktrees 2x, 98.86/97.93/93.92%; before 3x,
99.52/98.27/94.67%. These finite loaded-host observations do not establish
causal attribution, a worst-case bound or a speedup from changing the budget.
The separate daemon/tool profile remains with its owner; no raw locals or
session content were profiled here and no service was changed.

All contended reports explicitly set `nominal_run_exercised: false` and
`nominal_qualified: false`. Actual Worktrees elapsed times and comparison with
the original two-second threshold are separate fields; a scaled pass never
becomes a nominal pass. Original nominal failures remain preserved.

The report helper's initial generic delay/idle labels included inactive
startup-control values. Raw files were not rewritten: an explicit annotation
records active publication/source budgets, unchanged unused idle default,
and scenario-specific delays. Future reporting reads actual patched module
values; two in-process reporting tests cover this correction. Parser/ratio
validation passed 11 Worktrees and 11 Manager cases; the corrected Manager
reporting selector passed 13 cases without any timed rerun.

The operator subsequently accepted the exact contended-tier evidence for F1
source completion and released its source approval hold. Ordinary current-head
review, required CI and managed merge remain required. Nominal performance work
stays tracked by #7935/#7936/#7946; acceptance neither changes production
SLAs/configuration nor closes those goals. A scaled intent or pass alone was
not the authorization: independent exact-result acceptance was.

### Accepted source disposition

On 2026-10-24 the operator independently verified candidate `0ef1505b`,
required green CI `40064027710`, owner-authored clean Comment `5636917775`,
all resolved code threads, the complete test-only diff, no production/src/libs/
configuration change, and byte-identical copies of all 74 evidence members.
The exact contended results are accepted for this source slice.

The F1 source approval hold is therefore released. The original nominal
two-second goal is **not** a prerequisite to this source PR's completion under
that accepted disposition, and it is **not** reported as passed. Parent
performance #7935/#7936/#7946, F2/F3/F4, GUI and installed qualification remain
open under their existing owners.

Apply the repository's normal owner-authored verdict policy: an accepted
current-head Comment with no Medium/High findings is sufficient; an Approve
label or another operator approval is not an additional gate. Current required
CI, exact head/provenance, resolved threads and the configured role/profile
still apply. Use normal readiness and managed merge, with the configured
CODEOWNER exception only; never bypass a new finding or required check.
No rollout is authorized by source completion.
