# Atomic creation and handoff review proposal

## Admission update and newly exposed recovery stop

The exact private-adapter deadline/stream-retention amendment, local
observation facade and client drain-before-promotion ordering are now admitted.
No generic factory, `owned_process.py`, POSIX or global-CWD change is admitted.
The original proposal below remains its design record, not a claim that those
changes have already been implemented.

The required caller-recovery trace exposed an additional **unadmitted**
lifecycle hunk. `status_monitor_cutover.activate_after_update` constructs
`CutoverOrchestrator` without `expected_old` or `refuse_old`. Its
`_predecessor_health_check` checks status/roster at a port, not the exact
PID/start-time identity. Consequently `zdd.cutover_rollback._rollback` uses
its unguarded branch: a live port can lead to route restoration and undrain
without the current-owner comparison. `_undrain` only requires the positive
`draining: false` receipt in the guarded configuration. A client promotion
failure after the proposed drain-first ordering would rely on this boundary.

**Stop the client ordering change here rather than auto-undraining.** The
stronger `runtime_monitor_recovery.reconcile_owned_transition` path exists,
but it is a retained installer recovery path, not the immediate unguarded
rollback invoked by this constructor. Its existence cannot qualify that
immediate route/undrain decision.

### Smallest additional exact owner proposal

Admit only `status_monitor_cutover.py:activate_after_update`'s pre-orchestrator
selection/constructor hunk, plus its existing cutover-helper tests:

1. While holding the existing cutover guard, capture and authenticate the
   current routed predecessor using existing endpoint/`routed_monitor_identity`
   mechanisms, including a nonempty process-start identity. Refuse inconsistent
   or missing proof; do not synthesize identity from port readiness.
2. Pass that existing `Endpoint` as ZDD's existing `expected_old`, with
   `refuse_old` fencing exact endpoint plus native start identity. The
   publication guard must do no network/Git work. Reuse the existing
   predecessor health callback through a local owner-bound wrapper that
   verifies the retained PID/start identity before/after the health response
   and its returned PID.
3. Use the existing guarded ZDD rollback, with **no ZDD implementation or
   recovery API change**: mark drain started before the client call, confirm
   successor termination, CAS the route only from the exact expected
   successor, restore the exact predecessor, and require a positive existing
   undrain receipt. Uncertain termination/publication or owner change keeps
   the existing reconciliation breadcrumb and returns failure.

Pure/injected tests must execute the actual helper/orchestrator pairing:
promotion failure after predecessor drain restores/undrains the same verified
owner only after successor exit; third-owner route change and PID/start reuse
produce zero blind undrains; failed undrain and unconfirmed successor exit
remain failed/reconcilable, never successful cutover. Preserve ordinary
client force behavior outside observation custody. This adds no wire field,
new controller, auto-undrain shortcut or service operation.

This lifecycle hunk has **not** been edited. The admitted atomic-creation
implementation is also still pending at this stop; no caller-death or native
qualification is claimed. Independently contained review corrections may
proceed within the existing carrier/caller/server/lease hunks.

Back to [the effort](README.md). **Proposal only; no new native creation API,
shared adapter change or client/lifecycle implementation is admitted here.**
The Windows-first source remains draft and unaccepted; native qualification
has no admission window. Review findings 4366859038 and 4366859043 remain
blocking. The earlier per-probe-census Medium is resolved, not these findings.

## Inspected source and refresh

The clean published checkpoint is `5dcc264e2fe4bb8caca44e8ac5528d4f993576a5`.
A backup preserves it. Normal fetch/rebase onto
`c5694fee01a1d7d736dfa39e4d32afd3d1d63895` encountered only the mechanical
`efforts/README.md` conflict; resolution retains the Native Anchor Custody
entry, all upstream entries and this effort's admitted R1a row. The remaining
four commits applied without conflict. No source conflict was hand-resolved.

## 4366859038: actual available primitive and missing contract

`libs/agent-procutil/src/agent_procutil/_windows_caller_owned.py:spawn` already
creates a strict, non-breakaway kill-on-close Job **before** `CreateProcessW`.
Its startup attribute list contains `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`
(`0x20002`) and `PROC_THREAD_ATTRIBUTE_JOB_LIST` (`0x2000D`). The process is
created with extended startup information, no console and suspended execution,
then resumed only after creation. Assignment is a creation-time kernel
condition, not a post-create PID lookup.

Fresh upstream source already has `_environment_block` and passes explicit
Unicode environment and `cwd` to `CreateProcessW`; the earlier inspected
source's missing cwd/env is not a current gap. Neither changing global CWD
nor rewriting `owned_process.py` is needed.

This is an existing **private** primitive, not a compatible drop-in for
`spawn_sync_in_kill_on_close_job`:

- It returns `_Process` with binary streams, `wait`, `poll`, `kill`, `close`;
  it has no Popen `communicate`/reader-join protocol.
- Its post-create failure handler currently calls
  `job.terminate_and_wait(2.0)` and `process.wait(2.0)`. Direct reuse would
  silently add cleanup allowance after D.
- Public `run_caller_owned` ultimately uses fixed cleanup allowances and
  separate output-limit semantics too. Passing it a timeout is not a proof
  of the R1a total-D/retained-reader contract.

### Smallest proposed owner amendment

1. **Shared private adapter owner:** admit only the
   `_windows_caller_owned.py:spawn` failure-cleanup hunk and its own tests.
   Add an opt-in absolute `cleanup_deadline` (same in-process D). With no
   opt-in, preserve all existing behavior. For the opt-in, use remaining
   budget for positive waits; when no wait budget remains, transfer exact
   process/Job custody via existing `OwnedCleanupError`, never close an
   unconfirmed Job or fall back to post-create assignment. Preserve the
   original interruption/launch failure as the cause. No generic factory,
   `owned_process.py`, `_confirm_group`, helper broker or POSIX change.
2. **R1a-owned `push_timeout.py`:** after explicit admission to consume that
   private adapter, use its atomic `spawn` with unchanged Git cwd/env and
   strict stdio. A local observation-only facade owns the native root, both
   binary streams and reader objects; provide the currently required
   deadline-aware communicate/wait behavior without changing the generic
   Popen factory. Initialize retention before starting readers; startup or
   join failure transfers every actual object. Decode only the captured
   bytes using existing replacement/newline behavior.
3. **Settlement:** verified empty Job, completed root and completed readers
   must all precede release. The native root handle additionally needs its
   existing `close()` after settlement. Zero-wait later settlement must
   not renew D. Convert adapter `OwnedCleanupError` to the existing
   observation cleanup error without replacing actual native custody.

This needs exact shared-owner approval for the private adapter cleanup hunk
and private consumption contract, not blanket permission over generic spawn.
An alternative public facade would be a larger new shared seam; it is not
necessary to propose a generic `atomic=True` rewrite.

### Platform floor and proof

Microsoft documents Job-list startup attributes on **Windows 10+ / Server
2016+**:
[UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute).
Capability/attribute setup failure must refuse **before CreateProcessW**.
Never downgrade to suspend-then-assign or infer support from a version label.
Keep contained-test suppression/headlessness and strict nested-Job behavior.

Pure fail-first tests must inspect the actual CreateProcess call/attribute
backing storage, strict Job setup before creation, exact cwd/env (including
Unicode/empty environment), no post-create assignment/PID opening, and zero
creation on unsupported attributes. Inject failure after successful creation
but before resume and exhausted D: exact handles/streams and interruption
cause remain retained, with no fixed 2-second allowance. Exercise partial
reader startup, held readers and late zero-wait settlement without clock
renewal. Ordinary factory and non-opt-in private-adapter tests stay unchanged.

The later isolated native caller-death test must kill only its fixture-owned
caller at a barrier immediately after CreateProcess returns and before
resume. Kernel Job-list membership already exists there, so the suspended
root and descendants must disappear with the caller. A second barrier after
resume covers descendants/held pipes. Injected API order is not this receipt.
No native test is run or claimed at this proposal checkpoint.

## 4366859043: server ordering is necessary, not the whole boundary

`status_monitor_cli.py` samples `force` before
`status_monitor_drain.py:drain_with_diagnostics` enters the state lock and
closes admission. A worker can acquire custody between the sample and close.
The generic helper later returns `drained=force` at timeout.

The admitted server hunk can close the carrier before sampling force under
the existing state lock. All starts use the carrier's lock, so a ticket
admitted before close is counted; later starts refuse. Cleanup transfer
remains allowed. No config/title effect moves out of its existing lock.
Barrier tests must insert the worker between the old sample and close and
prove both active and cleanup-pending custody veto forced success.

However, actual `status_monitor_cutover.py:ControlClient._drain` currently:

1. selects a successor;
2. **promotes it and enters transition demand**;
3. only then requests predecessor drain and reads `drained`.

Thus even a truthful server refusal does not undo earlier promotion.
**Minimal additional owner admission needed:** only this client `_drain`
ordering and its existing cutover tests. Select the successor without
promoting; request predecessor drain first; promote/retain successor demand
only after its existing `drained` receipt is positive. A custody-blocked
refusal returns unchanged without promotion/demand transfer. Preserve the
ordinary legacy force policy where no observation custody exists; do not
invent a new wire field, authority, service or parent-death workaround.
If predecessor refusal or missing response occurs, no handoff is inferred.
Promotion failure remains explicit and must not become a successful cutover.

Required pure client tests count promotion and transition-demand entries:
zero on custody refusal/transport failure, ordered strictly after a positive
drain, one on ordinary success. Pair them with actual server callback barrier
tests, not a server-only boolean assertion. Existing caller tests must
establish whether retaining the predecessor route/read surfaces through this
ordering needs another narrow amendment; stop and name that hunk if so.

No client/cutover or new shared native implementation is made by this proposal.
Other contained findings may be repaired within their already-admitted hunks,
but neither blocker is waived or transferred past the PR.
