# Native tool-hook diagnostics

This component-owned reference expands the native Windows hook packet in the
[repository testing guide](../../../TESTING.md#select-the-evidence-first).
It describes source-test evidence, not installed-runtime acceptance. Selection,
fixture ownership and the original deadlines remain unchanged.

Native write failures retain their already-parsed launch birth, QPC and CPU
frame, caller start and elapsed time alongside the original stdout/stderr and
phases. That context is attached only after child return; it does not add
work inside the child or RPC budget, and a notification error still fails the
original success assertion.
The minimal launch/birth control also prints bounded parsed launch/phase
context when its unchanged 10-second subprocess limit expires, then re-raises
the same timeout. It parses at most 16 KiB of captured stderr, retains at most
1024 phase characters, marks truncation and unavailable stderr explicitly,
and does not print unrelated diagnostic text. Actor-free controls exercise
partial clocks, unavailable output and oversized output after the original
native cases; they do not retry the child or qualify the startup failure.
Timed RPC fixture phases buffer wall/QPC clock pairs in a fixed 64-row
capture, including nested JSON markers. They do not query process CPU or write
stderr during the live RPC interval. The original socket/response cleanup
precedes reporting; CPU data is not relabeled from a later sample. Legacy CPU
phase records remain readable. Clock rows include positive `qpc` and
`frequency` fields, with separate socket-created, connect-start and
connect-returned boundaries. Optional detail clocks distinguish a successful
connect-task creation from the subsequent bounded completion wait. Historical
catch/unwrapping phase IDs remain readable, but the successful connect path no
longer deliberately throws through PowerShell. These envelopes do not isolate
native execution from PowerShell binding, scheduling or telemetry costs. The
original common phases retain their order; native controls also accept retained
historical success-or-catch detail sequences.
Encoding-start and socket-start clocks immediately precede the original
request-encoding and socket-constructor statements. They separate broad local
pre-connect gaps without changing either statement or the 64-row capture;
bookkeeping still perturbs the measured path and is not accepted overhead.
The post-exit policy-composition variant also records ten fixed coarse
bootstrap boundaries in that same region: capture readiness, copied-script
entry after parsing, directory detachment, stdin completion, admission loading,
membership entry/return, event decoding, native-proof fallthrough and post-tool
source loading. The existing map initializes before the unchanged shell marker;
historical IDs remain fixed and the original RPC emitter map stays unchanged.
Only the copied pre-tool script gains markers, and its parent-derived digest
identifies that variant. Production, normal-mode and report-only script bytes
remain unchanged. The original cases, calls, assertions, timers and deadlines
remain; the additional diagnostic work is not accepted overhead or warm-up
qualification. Missing/partial phases do not identify a native instruction.
Pure controls retain default command bytes and reject unknown source, while
both native engines prove registered policy delivery, all ten boundaries and
post-close reporting. Separate early-script-error controls prove closure
without delaying or retrying an original child.
The pure emitter reads the real pre-tool script as an explicitly admitted
source-data witness; neighboring or arbitrary PowerShell scripts remain
unadmitted, and no unit actor permission is added.
Truncation and reporter errors fail the original
success check explicitly. Outside opt-in retained diagnostics, a child killed
before flushing loses its buffered internal phases; earlier entry/dispatch
markers and independent owner samples remain separate evidence, not a
reconstruction.
Clock bookkeeping and deferred output still perturb the fixture; this is not
an accepted overhead or cold-start bound.
The full non-report-only profile can separately opt into
`notification_diagnostics` for the original send-only read and mutation test
families. It instruments only their fixture-owned copied sender, not production
or normal-mode scripts, and excludes the separate prepared-cell routing test
whose original helper freezes its clock. The sender retains ten fixed scalar
fields, capped at 4 KiB: phase, return reason, four copies of the original
remaining-budget values, fixed exception class, numeric HRESULT/socket status
and write-completion boolean. It adds no clock read or timer reset. Allocation,
connect, write, waits and disposal keep their original order and 100 ms budget.
Terminal serialization occurs only after the original entry elapsed value;
a killed child may therefore have no sender summary.

Sender reasons distinguish discovery/capability (2), port (3), IPv4 (4),
size (5), allocation budget (6), connect wait/budget (7), pre-write budget (8),
write-setup budget (9), write completion (10), incomplete write (11) and
exception (12). Zero is the initial state, not successful delivery.
The existing budget values distinguish exhaustion from a pending connect
without an extra task or socket query. Phase IDs identify validation (0),
request construction (1), encoding (2), allocation (3), the four original
remaining-budget assignments (4-7), and returned write wait (8).

The corresponding real receiver adapter forwards the original bytes, limits,
exceptions and dispatch arguments. Its additional 4 KiB report retains at most
64 classified frames plus saturated counters and explicit truncation. It never
retains raw frames: only size, framing, JSON shape, version, authentication
match and a fixed kind classification are recorded. Sender output contains
neither payload nor endpoint/token/path/exception prose. Neither write
completion nor callback entry proves policy admission. These adapters add
unaccepted diagnostic overhead and leave the original arrival assertions,
receiver lock, RPC and child/test/runner/job deadlines unchanged. A passing
instrumented case is not normal qualification or a historical diagnosis.
The `accepted` count records the original owner's accept callback; the
separate `handler_started` count records handler entry. Zero observations
do not prove that a socket was never accepted by the operating system.
The pre-tool resident request reuses the complete event already decoded for
native checks; raw-only transport callers retain their existing decoder.
`test_native_pretool_reuses_decoded_event` rejects a second decode at that
seam and requires the original registered-entry denial and wire-event equality
for both Windows engines and legacy/cell inputs. Raw-only roundtrip, metadata
compatibility and response-refusal controls remain in `test_post_tool_shell.py`.
These checks prove removed duplicate work, not a cause or disposition of
historical timing failures. The 500 ms transport clock still begins before
endpoint validation and is neither moved nor reset.
`test_phase_free_registered_write_control` adds eight counterparts for
personal-state and write-name-real-policy events on both engines and
legacy/cell fixtures, plus both legacy protected-patch input counterparts.
It reuses the original registered-write body and every
membership, wire, denial and fallback assertion without child phase, launch or
CPU writers. The existing interpreter-lookup sentinel remains; this is not an
unmodified installed environment. Source-byte checks and fingerprints run only
after the measured child exits, with the original outer clock and 10/30/300
limits. A final step on the existing light alias-policy worker selects these
cases after its original packet, keeping added work out of the near-limit
cell worker without raising job limits. A passing counterpart is not
subtractive overhead evidence or a causal disposition of an earlier failure.
The same step checks its actual workflow routing as a file-backed source
contract outside the unit-only packet, like the existing native source checks.
The natural-policy composition fixture shares its passive resident wrapper
with four native forwarding controls. Both engines preserve bound decoded-event
identity as well as raw-only calls, including the original kind/raw/membership
values. The wrapper forwards production's optional decoded argument rather than
silently restoring raw decoding inside the RPC clock. These controls run after
the original light-worker packet; existing composition cases and limits stay
unchanged. Parameter parity is not a diagnosis of a retained native timeout.
Only opt-in `report-only` and `post-exit` diagnostics extend the existing buffered RPC phase
helper to natural-policy composition. Buffered stderr flushes after RPC cleanup;
a structured numeric summary is printed after the child returns, including on
an original output mismatch. Normal composition keeps its uninstrumented
transport. This still perturbs the child. The helper adds no process queries or CPU
samples and keeps the original 64-row buffer, cleanup and 500 ms clock.
Two native loopback controls check actual response/phase preservation on both
engines, after the original light-worker packet. They initialize JSON for the
helper-mechanics check and are not first-touch or latency qualification.
Pure opt-in controls remain separate from the real-source native inputs.
All diagnostic results remain nonacceptance, including any passing cases.
Report-only and post-exit registered-write and natural-policy fixtures retain those same
numeric records in one parent-owned 4 KiB mapping per existing child instead
of a second child-local array. A fixed version/count header and per-row commit
word publish at most 64 existing phase IDs with their original clocks and
frequency. The parent decodes only committed rows after child exit or the
unchanged timeout, then closes its mapping. Partial rows are not returned;
incompatible or invalid records are explicitly unavailable. No process memory,
arguments, environment, tool data, process-query rights or extra sampler is
involved. Native controls kill both engines at the original 10-second limit,
check committed-row retention and partial/invalid refusal, and verify that the
named mapping no longer exists after owned handles close. Pure decoding and
disabled-path controls use no native actors. Retained loopback controls also
preserve the actual response and phase transcript on both engines. Parent
decoding and reporting follow the original elapsed-time observation.
Natural-policy invocations supply a post-close reporter for those same numeric
records outside pytest capture. Existing oracle/warning capture resets therefore
cannot discard an earlier region's decoding status or closure. Default helper
consumers keep the original stderr report; callback failure falls back to that
path rather than replacing the original test exception with the callback error.
Pure controls cover successive regions, capture resets and original exception
identity; real-engine controls exercise the reporter path on both hosts.
Registered-entry controls also load the copied admission helper before the
real policy RPC, requiring nonempty committed phases on both engines. The
diagnostic admission projection uses the same shared writer, so loading it
cannot replace that writer with the normal child-local array implementation.
Existing secondary prior-reader and real-policy allow/deny launches open their
own sequential region when retention is enabled; no child reuses a closed
primary region. Both-engine real-policy controls verify all three region
lifetimes and the original allow/deny results. Fallback elapsed time is saved
before parent decoding/reporting, just like the primary observation.
Normal fixture output and buffers,
production code, clocks, assertions, order and termination remain unchanged.
The existing Windows lane runs slow-marker, overflow and LF/CRLF source
contracts only after the original packets, with optional observers disabled.
Its late RPC controls run on the light alias-policy worker in normal and full
diagnostic profiles; full diagnostics place timeout-cleanup controls there too.
Sender-only diagnostics retain those controls on the cell worker because that
profile does not start the write matrix. The existing routing guard verifies
one effective placement per profile, identical control commands/environment
and unchanged worker and 10/30/300-second runner and 10-minute job limits.
Those real native controls stay outside required unit-only CI. Pure numeric
clock decoding has nineteen positively admitted cases; the real PowerShell source
is a native-contract input, not an exemption from unit-source admission.

## Bounded code-only native attribution

The manual full profile's `native_observation_mode=code-stack` preserves the
post-exit metadata and existing retained phase/bootstrap variant. It selects
only original registered-read, registered-write, natural-policy and selected-owner
post-tool PowerShell calls, not intentional timeout or observer controls.
The existing original-call boundary requires a PowerShell list argv with
`-NoProfile`, `-NonInteractive` and the original ten-second timeout. Python
stamping and other setup calls remain unwrapped and cannot consume the
code-sample claim. This target-class selection does not replace the backend's
native process/thread identity checks.
The original
fifteen-job topology, selectors, calls, order and deadlines stay unchanged.
An atomic runner-owned directory claim is shared across each Windows job's
pytest steps. Only an eligible child can take it, after the eight-second delay;
at most one original child per worker receives one attempt. Missing claim
configuration fails before observation; existing claims never authorize a retry.

The source-only backend retains the original creation-owned process handle,
verifies PID/birth and AMD64 architecture, and reuses the bounded PSS thread
enumeration. Each of at most sixteen threads is pinned to its original owner
and birth before a brief suspend/context/read/resume operation. It walks at
most sixteen frames per thread using a restricted PE/AMD64 version-one unwind
interpreter. Supported non-frame partial prologs and epilog suffixes are interpreted.
Frame-register partial prologs report `partial-frame-register-prolog` before
any stack-slot read until the declared prolog is complete. Completed frame
prologs still use the fixed frame base when RSP includes a dynamic allocation.
This conservative refusal avoids using a caller's frame register before
`UWOP_SET_FPREG` establishes the current frame;
unsupported versions, chained records, machine frames, dynamic code, ranges
and operations stop explicitly rather than scanning for plausible addresses.
Absent static entries are accepted as leaf frames only in the named Windows
system modules. This is not a general managed-runtime or JIT stack walker.

Reads validate committed, accessible regions: stack reads stay in the captured
stack allocation, and image/unwind reads stay in the verified image allocation.
All target reads, including PE headers and epilog instructions needed to unwind,
share a 64 KiB attempt cap. The cooperative 250 ms budget begins at backend
construction and is clamped to the original communicate deadline. Context
buffers are aligned and scrubbed; every suspension is paired with resume, or
the retained thread handle must confirm termination. Failed resume/close
retains cleanup uncertainty. No target write, injection, privilege change,
symbol download or private symbol service is used.

The optional wrapper receives stdlib's already-established absolute
communication endtime. It neither resets that deadline nor joins a collector
before stdlib's original kill/drain/context cleanup. Reporting remains deferred.
The additional `native_code_observation` summary is capped at 32 KiB per worker,
separate from existing metadata and RPC row allowances. It exports verified
process identity/status and normalized module/code-offset frames, never raw
memory or registers, arguments, locals, environment, tool content or paths.
At most 32 module signatures retain only basename, PE timestamp and image size
from headers already read for unwinding, for later public-module identification;
this adds no live symbol requests or target reads.
Byte, frame, output, deadline and unsupported-unwind stops remain explicit.
Nonselected and mode-off behavior retain their original paths.

Focused controls cover identity refusal, context alignment/scrubbing,
unwind operations, partial prologs/epilogs, range/byte/output caps, shared
selection and actual Windows capture/closure. A blocked synthetic collector
checks the exact original deadline and proves child exit before joining.
These controls and all instrumented portfolios remain diagnostic only: a
passing sample does not explain an earlier failure, accept observation
perturbation, or qualify original hook latency or throughput.

## Owned runner and full-portfolio observation

The manual `prepared-membership-native.yml` `runner_owner_diagnostics` input
adds source-only observation at each existing Windows containment Job owner.
It does not load a pytest observer or wrap a test's process, receiver or policy
calls. The existing resource-poll loop checks its remaining cycle and a 20 ms
observation cutoff between member reads while querying only
that Job's membership and birth-pinned member handles. It records numeric
creation, CPU and exit counters, with monotonic/UTC origins for correlation.
Member counters precede the liveness query. A confirmed exit refreshes the
final time/counters from that same pinned birth before reporting or release;
unavailable final identity/time stays unavailable rather than becoming a live
sample or an unchanged-CPU interval through exit.
Each observation pass samples retained process counters first, then discovers
members and samples waits. An unfinished pass resumes at its next operation on
the following resource poll, rather than restarting on a slow first member.
The same 20 ms cutoff is checked between operations; a slow or unavailable
counter does not indefinitely starve later members, discovery or wait work.
The selected observer explicitly requests
`PROCESS_QUERY_INFORMATION | SYNCHRONIZE`; thread-only snapshot capture
returned access denied with query-limited handles in the fresh fixture.
There is no memory-read/all-access request, privilege change or access fallback.
After counters, only live members retained for at least eight monotonic seconds
are eligible for snapshot/WCT work. This conservative first-seen age does not
infer age from a wall clock or a claimed creation time; process counters remain
eligible immediately. Alternate query starts prefer an eligible member not yet
selected for a wait query; the remaining starts use the existing member rotation.
The first-selection state belongs to the birth-pinned member, not a reused PID.
Pending requests are polled before any further selection, without adding query
slots or guaranteeing capture before exit. Each member's thread cursor advances
through at most 16 process-scoped snapshot entries. Only `PSS_CAPTURE_THREADS` is used:
no memory, thread context, handle names or VA clone.

At most one asynchronous wait-chain request and pinned thread are retained.
The thread's owner, birth and live state are verified before querying WCT with
flags zero, without foreign-process traversal. Polls never sleep or wait for
an event. Returned foreign-thread nodes are omitted; at most 16 nodes contribute
numeric type/status histograms, never names, addresses or thread IDs.
Callbacks and their buffers remain alive until native session closure settles.
Pending requests, cancellation, numeric API errors and thread truncation are
explicit; access denial is unavailable evidence, not a reason to enable debug
privileges. Native error stages are: 1 process state, 2 snapshot, 3 walk marker,
4 thread walk, 5 thread open, 6 thread times, 7 thread owner/birth, 8 thread
state, 9 asynchronous session and 10 wait-chain query.

No host process scan, command line, payload, environment value, exception text,
stack or memory capture is performed. Rows are buffered until original
teardown. The unchanged 4096-total-row budget is split between a most-recent
1024-row process/wait timeline, at most 2048 process summaries and at most
1024 retained wait-summary rows, all keyed by verified PID and birth.
A process summary retains the first observed time/CPU and
latest time/CPU/exit state plus sample count after the member handle closes;
PID reuse never combines identities. Earlier summaries are not evicted to
admit later births. Summary-cap exhaustion reports dropped sample count, not
an inferred count of missing processes. This trades a shorter detailed timeline
for retention of early sampled identities; it neither recovers evicted historical
rows nor proves that a short-lived child was sampled. `[CI-JOB-PROCESS-SUMMARY]`
records contain only the same bounded numeric identity/counter data. Five fixed
numeric-or-null fields also retain the current unchanged-counter start and the
longest observed unchanged CPU-counter interval with its kernel/user counters.
An interval requires consecutive verified-live samples with equal counters;
counter changes reset the current interval. First sighting and delayed exit
reporting never extend it. Counter quantization can hide work, and gaps between
samples do not identify a blocked thread, its wait cause, continuous zero CPU
or a scheduling fault. These fields use existing samples only: no extra native
call, retained handle or row is added.
`[CI-JOB-WAIT-SUMMARY]` records preserve the first and latest already-collected
wait row for at most 512 identities after timeline churn. A single sample emits
one row marked both first and latest; later samples retain their count and two
rows. Unavailable, budget-exhausted and cancelled observations remain explicit,
not inferred successful waits. Earlier identities are never evicted to admit
later ones; capacity loss reports dropped sample count. This further shortens
the detailed timeline to preserve bounded early wait evidence without adding
queries, rights, handles or time. First/latest samples cannot reconstruct
intervening waits or establish a failure's cause.
The version-5 observation summary includes all lane capacities, retained
counts, timeline discard and both summary-loss accounts, and the unchanged
wait-selection minimum age in milliseconds.
Wait `sample_at_ms` and completion `at_ms` use the process records' origin.
Only the original concurrent process
limit plus its controller are retained. Exhausted budgets, incomplete samples,
truncation and unclosed observation handles remain explicit. Individual Win32
calls are not preempted; this mode can perturb execution and is not an overhead
bound. Short-lived or breakaway children
can be absent; this is not a complete process history or a scheduling-cause
diagnosis. The original owner still performs all termination and settlement.
Only the selected disposable hosted Windows owner admits this mode; the switch
is removed from child environments. Successful instrumented packets return
diagnostic failure, while existing nonzero exit categories are preserved.
The original packets, ordering, assertions and child/test/runner/job deadlines
remain unchanged, and later original packets still execute after a diagnostic
failure. Default push runs remain uninstrumented; a diagnostic result never
qualifies them.

The explicit process-only preparation includes the observer's pure module,
selected-only process/wait adapters and Windows registry reader in its source closure. The pure
`test_required_tool_closure_contains_actual_owner_startup_imports` control
checks the transitive declared imports. A focused non-unit check,
`python -m pytest tools/test_plugin_test_job_import.py`, copies the actual
declared sources and starts a fresh `-I/-S/-B` interpreter to import containment
without a checkout or site-package fallback. It does not activate observation,
load a DLL, run a qualification receiver, or claim native timing acceptance.
The separate Windows-only `tools/test_plugin_test_job_waits_native.py` control
uses the original owner to create a fresh disposable Job and native sleeping
child, then checks late real thread capture and settled handles. Its 8.75-second
fixture and unchanged ten-second owner bound are feasibility evidence, not a
reproduction or a qualification of any original native failure.

The manual `prepared-membership-native.yml` `full_native_diagnostics` input
keeps the original fifteen-job topology, selectors and test identities.
Normal and full-diagnostic lanes partition the original 161-case cell packet
by complete file groups: `test_managed_session.py` and
`test_pre_tool_bounded_reads.py` stay on the cell worker, while the selected
`test_post_tool_shell.py` group runs on the existing `write-name-policy` worker
after its original packet and before later controls. Each group retains its
original case order, setup, assertions, iterations and deadlines. The moved
group inherits the same full-observation job environment and runs after an
intentional diagnostic failure unless cancelled. No worker or deadline is
added; the sender-only diagnostic packet retains its original three-file order.
The workflow-placement regression guards this split, inherited environment
and unchanged 30-second test, 300-second runner and ten-minute job bounds.
An explicit `PYTEST_PLUGINS` entry loads the source-only
diagnostic bootstrap on Windows; runtime packages and
`conftest.py` are unchanged. The bootstrap removes its own environment entry
before test children start, retaining unrelated entries.
The existing Linux producer runs unchanged.
The `late`, `post-exit` and `code-stack` modes use `_native_portfolio_pytest.py` to
observe real ten-second PowerShell calls throughout the original
selected tests, including legacy families without inline process markers.
It leaves subprocess doubles and other commands untouched and rechecks the
native seam at each call. It reuses the same
creation-owned observer and receiver hook, buffers native metadata until the
original call report and includes the original test outcome. An intentional
timeout control can therefore pass while reporting a timed-out child.
The original no-process guard control is left unwrapped. An explicit `Popen`
patch in another observed test still reaches the real subprocess module, so
observation cannot isolate a process prohibition from the code it guards.
Full mode's default `late` setting waits eight seconds before its first native query, leaving the
normal child's startup and execution unsampled. Completion cancels that wait;
an empty sample list is therefore expected, not evidence that the observer
failed to start. Each row declares its mode and live-sampling delay. A child still
running after arming receives the same bounded metadata queries within its
original ten-second deadline. Focused observation remains immediate.
This reduces one source of observation perturbation; it does not prove that
the observer caused a prior nonreproduction or that late sampling is inert.
The full profile's `native_observation_mode=post-exit` alternative starts no
collector thread and makes no live-child metadata queries. Only after stdlib's
original timeout kill/drain/context cleanup does it start one bounded query
against the retained created-child handle. It can recover total CPU counters
and exact PID/birth/exit FILETIME values from an exited process, not its earlier
live wait state. Identity must match the original handle's already-read birth;
missing, inconsistent or non-exited metadata remains unavailable, with no PID
lookup fallback. Each post-exit row also exports `observer_started_monotonic`,
the existing parent timestamp taken after `Popen` returns and before original
communication starts. It is not a process-creation timestamp or a new clock
sample. Correlate it only with the same parent's monotonic clock, not the
child's QPC or wall clock. Returned children
remain unsampled. Cleanup uncertainty is explicit, and the original kill is
never delayed for this query. Each row declares its sampling mode; the default
remains `late`, and this setting does not activate diagnostics on its own.
Failed post-exit calls also reuse the bounded numeric failure-context exporter
described below, through the existing report hook rather than another registered
pytest plugin. Passing calls add no failure context. The same parent-owned
4 KiB/64-phase retention remains active in this mode, without moving the original
communication deadline. The copied policy bootstrap additionally retains the ten
fixed phase points described above.
In these observed modes, original policy-composition cases also observe their fixture-owned resident
dispatcher and guard calls. Fixed rows retain entry/exit deadline remainder,
elapsed time, decision category and reason length, never payloads, reasons,
paths or exception text. The original functions receive the original arguments,
results and exceptions are forwarded unchanged, and the 500 ms transport is
not extended. At most 64 dispatch rows and 16 guard timings per dispatch are
retained, with explicit truncation. These probes are diagnostic overhead,
not latency acceptance; a hard process abort can prevent buffered reports.
At most 64 child rows are retained per test, with explicit truncation or
report-error markers; no original outcome is changed by reporting.
The full profile changes a successful pytest exit into diagnostic failure,
but preserves an existing nonzero exit category. Remaining original packets
continue after that deliberate exit, while default failure/skip behavior
remains unchanged. The forced collector-cleanup control runs after all
original membership packets with the diagnostic plugin disabled.
Full observation takes precedence over the focused `sender_diagnostics`
input. It is not native acceptance or a latency distribution, and it does not
clear earlier failures or authorize a runtime change.

The explicit `code-stack` variant adds bounded code-only attribution to the
post-exit profile. Its target, privacy, unwind, timing and cleanup limits live in
[bounded code-only attribution](#bounded-code-only-native-attribution).

The full profile's `native_observation_mode=report-only` instead loads the
independent source-only `_native_failure_report_pytest.py`. It imports no
original fixtures and installs no subprocess, receiver, dispatcher or call
wrapper. After a failed call report, it reads fixed numeric timing/launch
locals and list lengths from that owned synthetic test's traceback only.
It visits at most 64 frames and retains at most eight matching contexts,
with explicit unavailable, truncation or report-error markers. For an original
`TimeoutExpired` with a matching owned frame, it also scans at most 16 KiB of
retained stderr after bounded UTF-8 conversion. It exports only availability,
truncation and presence flags for the existing natural-shell build and policy
start/complete markers; a cap-split final line cannot become a marker.
For an unchained exact `AssertionError`, the same scanner can read an exact
`CompletedProcess` retained as `result` in a matching owned `invoke` helper.
Its five booleans appear as that context's optional `completed_stderr` field;
the existing `timeout_stderr` field is unchanged. Outer/non-helper results,
arbitrary holders and exceptions with a cause or context are not admitted,
so a stale completed result cannot stand in for a timeout's captured output.
It never exports captured text or reads stdout, list contents, command arguments,
environment values or exception text, and makes no native process query.
Marker absence describes retained data only, not proof a phase never executed.
Unavailable or truncated data remains explicit. Metadata reflects report time, not
proof that a request completed within budget. Successful calls produce no
context; an abort can prevent reporting. Its end marker retains the original
exit category and declares zero intercepted calls; a successful session still
ends in diagnostic failure. Default and late behavior is unchanged.
Focused controls live in `tests/test_native_failure_report.py` and the actor-free
`tools/promotion_unit_tests/test_native_rpc_fixture.py`; the opt-in
`AGENT_WORKTREES_REPORT_ONLY_CONTROL=1` case deliberately fails through the
real pytest report path and is not acceptance.
