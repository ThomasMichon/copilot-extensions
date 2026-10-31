# Receipt process-custody boundary

Parent: [Prompt Receipt Observation](README.md). This is a blocking qualification
packet, not completion or permission to change another owner's implementation.
The later merged #8193 zero-helper resident contract supersedes the proposed
helper integration below; retain this packet as the rejected-path evidence.

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

The unchanged strict negative assertions are retained in
`evidence/legacy-prompt-receipt-custody.repro.mjs`, beside the rejected helper
implementation. Run that historical counterexample explicitly with `node --test`;
`RECEIPT_TEST_PYTHON` may name an existing test interpreter. It is expected to
fail and cleans its owned children before asserting. The supported resident
transport has separate zero-helper tests; this evidence does not qualify it.

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

## Bounded ready-descriptor discovery

Read-only source inspection was refreshed against `origin/dev`
`667e6f5606d38587748ea8da388d2994868a472f`. No candidate runtime resolver,
readiness command or installer was executed to synthesize a descriptor.
No installed-runtime scan, benchmark or live-agent contact was used.
The relevant catalog, binding and readiness contracts are unchanged between
that source revision and the candidate.

**No supported, already-selected executable invocation is published by the
inspected startup/owner interfaces:**

| Existing owner surface | What it actually supplies | Why it is insufficient here |
|---|---|---|
| `plugins/agent-worktrees/scripts/emit-command-catalog.ps1`, generated from `libs/payload-invocation/templates/catalog-powershell.tmpl` | Version-1 `commands[].argv` names the payload shim. `availability: ready` tests that the shim and installer exist. | This is not readiness of an installed Python runtime. Invoking the shim would resolve and potentially provision it. |
| `libs/payload-invocation/command_binding.py::resolve_command_binding` and the vendored `_command_binding.py` | Source fingerprint, marketplace/plugin identity, selected `runtimeRoot`, and installation/activation receipt generations. | Selection validates ownership; no interpreter argv or effective environment is returned. |
| `plugins/agent-worktrees/installer-readiness.json` and `installer_readiness.py::evaluate` | A declaration to execute `agent-worktrees installer-readiness`; its result says the runtime has already loaded. | Neither declaration nor result contains a ready invocation. Calling it would require the very resolver this lane must avoid. |
| `libs/versioned-runtime` markers and installation-context runtime-slot completion receipts | Slot completion, root/version, source/build provenance and receipt identities. | Completion is not active selection or an executable descriptor. Reconstructing current/last-known-good/newest selection and interpreter layout in Node would be another resolver, not consumption of an existing descriptor. |
| `plugins/agent-worktrees/src/agent_worktrees/launch_environment.py::_build_env` | Instruction directory, hook enablement, qualified owner reference, and caller/profile environment overrides. | No canonical ready interpreter/owned-execution descriptor is handed to the extension. Arbitrary profile variables are not readiness/provenance evidence. |

The shell's `AGENT_RT_PY` and PowerShell's `$AgentRtPy` are outputs of executing
the resolver; they are not an attributable startup descriptor. The resolver's
`ready` mode controls trace emission, not a persisted command-ready handoff.
No consumer code was changed to guess those values or derive executable paths.

### Smallest missing owner handoff

The existing **payload-invocation/agent-worktrees launch owner** would need to
publish one optional, source-qualified ready invocation in its existing catalog
or explicitly addressed session receipt, after its own normal readiness work:

- exact non-provisioning physical interpreter/entry argv and required isolated
  environment, bound to plugin/source, selected installation and runtime
  generation; do not expose a venv trampoline as if it were a physical entry;
- explicit unavailable/blocked state and invalidation identity, so this consumer
  can retain its pending receipt/fence without starting another observation;
- an owner-qualified helper-launch lifetime contract, not only custody of the
  command later launched by that helper.

Only after that handoff exists should this consumer propose a thin fixed-purpose
adapter to `agent_procutil.owned_process.run_caller_owned`. The adapter must not
move the leak up one level: admission/startup, cancellation before native custody
is established, helper death and unknown cleanup all remain in the qualification
matrix. Unknown cleanup retains the fence; it is not completion or retry admission.
The descriptor alone would not qualify this helper boundary.

The existing catalog binding source last changed in merged #7598; the generic
installer-readiness declaration came from merged #1245. No active PR claiming
this missing descriptor interface was identified by the scoped forge search.
#7833 (custody) and #7788 (launch maintenance) are still OPEN and were not changed.
Assign the missing handoff through its existing owner rather than adding a second
broker here. #8026 remains draft and unmerged.
