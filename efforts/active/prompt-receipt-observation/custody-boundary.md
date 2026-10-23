# Receipt process-custody boundary

Parent: [Prompt Receipt Observation](README.md). This is a blocking qualification
packet, not completion or permission to change another owner's implementation.

## Source and ownership

- Current candidate: #8026, owner `hinged-socket`; canonical issue #7942.
- Consumer: `plugins/context-handoff/extensions/context-handoff/handoff-core.mjs`,
  `observeHandoffPromptReceived` and `runReceiptChild`.
- Scheduling/cancellation: sibling `prompt-receipt.mjs`,
  `registerPromptReceiptObservation`, `reconcile` and `dispose`.
- Qualified Python API inspected and exercised:
  `libs/agent-procutil/src/agent_procutil/owned_process.py::run_caller_owned`.
  Its `OwnedCleanupError` retains custody uncertainty explicitly.
- Python-free native API inspected and exercised:
  `libs/agent-procutil/src/agent_procutil/native_owned.ps1::Invoke-NativeOwnedProcess`.
  It atomically creates the target inside a strict Job, writes stdout/stderr to
  its caller-provided log, and exposes `NativeOwnedProcessException.CleanupConfirmed`.
- #7833 is OPEN and touches `owned_process.py`, `_windows_caller_owned.py` and
  related broker/qualification files. No changes were made to them.
- #7788 is OPEN and owns launch-maintenance integration, including
  `update_launch_cli.py`, `update_stage.py` and Manager `launch-session.*`.
  Those files were not touched. Its shared README/CLI-reference files were
  also left unchanged; receipt documentation uses its own focused page.

## Reproduced failures, with owned cleanup afterward

The original tests used Node parents and descendants. Their passing result
does not qualify arbitrary runtime descendants: Node's own child lifetime
behavior can conceal the missing custody. Repeating the probes with a real
Python parent spawning a sleeping Python descendant with inherited stdio gives:

| Case | Observation at 2,800 ms | Descendant |
|------|------------------------|------------|
| `runReceiptChild`, unavailable `taskkill` executable | Promise settled with `ETIMEDOUT` | Still alive |
| `runReceiptChild`, >64 KiB stdout | Promise settled with `ERR_CHILD_PROCESS_STDIO_MAXBUFFER` | Still alive |

The probe changed only its own environment, used absolute test-interpreter
argv, captured only its own child identities, then terminated and joined its
known test processes and removed fixtures. It did **not** reproduce an
indefinitely pending promise in these cases; the verified failure is leaked
descendants despite apparent settlement.

## Existing primitive reuse evidence

Calling the unmodified source `run_caller_owned` with a 64 KiB stdout limit
returned `OutputLimitExceeded` in **516 ms** and confirmed its observed
descendant gone. A **703 ms** timeout run returned `TimeoutExpired`, but its
target had not emitted the PID fixture: that run is not positive descendant
cleanup evidence.

Calling the unmodified Python-free `Invoke-NativeOwnedProcess` from fresh stock
Windows PowerShell twice, with a trivial captured Node target and a 1,000 ms
target deadline, completed successfully in **3,015 ms** and **2,297 ms**.
The whole cold invocation includes loading/compiling the native helper; its
target timeout does not bound that preparation. It currently exposes combined
log capture rather than the Python API's bounded separate stream result.

The source receipt suite also failed its **<2,000 ms** first-touch lifecycle
assertion against the current taskkill adapter (whole test-case duration
**2,663.75 ms**), and a deferred native success case exhausted its budget.
Those failures remain open. Earlier 1.827-second happy-path results are not a
bound on these failure paths.

The durable negative regression is
`plugins/context-handoff/tests/exhaustive/prompt-receipt-custody.test.mjs`.
Run it explicitly with `node --test`; `RECEIPT_TEST_PYTHON` may name an existing
test interpreter. The tests clean their owned children before asserting; they
are not disabled or satisfied by the earlier Node-child fixtures.

## Minimal integration contract requested from the owning lane

Prefer exposing an **already-attributed, ready runtime invocation** from the
existing launch/runtime owner so this consumer can call the existing
`run_caller_owned` primitive without first starting an uncustodied resolver.
Missing readiness must return unavailable without provisioning or scanning.
The consumer can then add a thin, fixed-purpose receipt adapter, not a new
generic broker or Job implementation.

If that ready invocation is unavailable, the existing Python-free native owner
would need a qualified captured-result entry with an end-to-end cold deadline,
bounded separate stdout/stderr, caller-death cancellation and explicit
`CleanupConfirmed`. Any preparation/caching needed to meet that deadline belongs
to that owner, outside this receipt lane. Do not add a per-event worker queue,
duplicate ledger or inferred process authority.

The minimal result contract is exit/result data plus explicit cleanup truth;
uncertain cleanup must retain a fence and must not admit another observation.
Tests must cover Python descendants retaining stdio, overflow, timeout,
termination failure, root exit, cancellation/caller death, missing readiness,
concurrent replay and at least two windowless cycles. Measure the entire
cold callback/adapter lifecycle, not only the target's configured timeout.
