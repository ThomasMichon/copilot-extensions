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
only original registered-write, natural-policy and selected-owner post-tool
PowerShell calls, not intentional timeout or observer controls. The original
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
interpreter. Supported partial prologs and epilog suffixes are interpreted;
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
