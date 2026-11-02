# Narrow canonical lock admission gate

Parent: [Prompt Receipt Observation](README.md).
**Retained exact proposal, subsequently admitted by the coordinating owner.**
The resident design is the merged #8193 contract. Implementation/checkpoint
status is in the parent README; this admission does not expand other owners'
producer or control scope.

## Exact proposed helper change

File: `plugins/agent-worktrees/src/agent_worktrees/handoff_trace.py`.
Only `_append_lock` and required imports are proposed:

```python
@contextmanager
def _append_lock(
    lock_path: Path, *, deadline: float | None = None,
) -> Iterator[None]:
    ...
```

`deadline` is an absolute `time.monotonic()` instant, not a relative timeout.
`None` preserves the current implementation's native blocking acquisition
(`msvcrt.LK_LOCK` / `fcntl.LOCK_EX`), lock path, sentinel initialization after
acquisition, context-manager yield and release behavior. No existing caller is
migrated implicitly. No lock file is deleted, replaced or re-owned.

For a finite supplied deadline:

1. Reject boolean, NaN and infinite values; an already-expired deadline raises
   `TimeoutError` before admission.
2. Attempt the same canonical lock nonblocking, using `msvcrt.LK_NBLCK` or
   `fcntl.LOCK_EX | fcntl.LOCK_NB`.
3. Retry only recognized contention errors in the caller's existing thread,
   sleeping at most `min(10 ms, remaining budget)` between attempts. Propagate
   unrelated filesystem/locking errors.
4. Check expiry before each attempt and after acquisition before yielding.
   If expiry is observed after acquisition, release it and raise `TimeoutError`.
   Never run the protected body after failed admission or unlock a lock not held.
5. Preserve initialization only after acquisition and unconditional release on
   body completion/exception. The deadline bounds admission, not protected fsync.

There are **zero helper processes, timer threads, polling workers or background
tasks**. A timed-out caller leaves no lock waiter. Synchronous OS file opening
or filesystem I/O cannot be forcibly interrupted by this API: the caller/server
retains ownership while such a call settles. The Node deadline may report pending,
but cannot claim that server work stopped or admit another uncertain mutation.

## Bounds accepted by both H2 and the delivery/control owner

The delivery/control owner explicitly accepted these values:

| Boundary | Exact limit |
|---|---|
| Association admission | Existing record guard first, nonblocking; zero queued receipt requests |
| Trace-lock wait | 100 ms, additionally capped by the supplied absolute deadline |
| Total consulted history | 1,048,576 bytes and 4,096 records across all consulted history, not per file |
| Individual record | 65,536 bytes |
| Total scan elapsed | 200 ms, additionally capped by the operation deadline |
| Pre-effect backend elapsed | 1,000 ms maximum and never beyond the caller's absolute deadline |
| Receipt server admission | Four in-flight operations per resident; zero queued requests |
| Mutation identity | At most one unresolved mutation per full immutable identity |
| Node whole observation | 2,000 ms including catalog/route/token/health/admission/response |
| Request and response frame | 8 KiB each |

Record guard precedes trace lock. The adapter supplies a context manager yielding
`revalidate()`, called after trace admission and immediately before append.
The scoped record guard stays held through append/fsync; no broad daemon state
lock is held during filesystem waits. Fsync may outlast the client deadline:
server accounting remains in flight until settlement. An exact query while a
prior record may still commit returns unknown, not absent or retry permission.
These are proposed limits, not measured qualification.

Matching legacy rows without an attributable creation nonce remain unavailable;
no ownership backfill or replacement append is inferred. Record/query identity
preserves installation, project, worktree, creation nonce, session and locator.

## Existing direct callers to preserve

All currently omit `deadline` and must retain current behavior:

- `handoff_trace.append_event` (same file, currently line 149).
- `claim_history.record_event` (`claim_history.py`, line 216).
- `claim_history_mirror._ledger_id`, `_ledger_id_readonly`, `_snapshot_ledger`,
  `local_identities_for_ref` (lines 598, 620, 638, 676).
- `handoff_diagnostics.stamp_session_state_handoff` (line 150).
- `pr_ado_remediation.Ledger.locked` (direct imported helper, line 35).
- H2's current `handoff_prompt_receipt.prompt_receipt` (line 67); only the
  separately admitted resident path will opt into bounded admission.

## Proposed test scope after admission

- Existing `tests/test_handoff_trace.py`: sentinel-after-acquire behavior,
  append semantics, thread concurrency and real cross-process append controls.
- New focused `tests/test_handoff_trace_lock_deadline.py`: expired/invalid
  deadlines, one nonblocking attempt, contention/release within budget,
  contention expiry without entering the body, unexpected-error propagation,
  unlock/handle cleanup and default-call compatibility.
- Real owned contender/holder controls on Windows and POSIX; the test harness
  may start its own processes, while the lock path itself starts none.
- Existing direct-consumer coverage: `test_claim_history.py`,
  `test_claim_history_mirror.py`, `test_ado_pr_remediation.py`,
  `test_session_worktree_binding.py` and the diagnostics coverage already in
  `test_handoff_trace.py`, selected proportionately.
- H2 resident tests separately prove total scan bounds, revision revalidation,
  commit uncertainty and server in-flight settlement. A lock unit test alone
  cannot qualify those contracts.

## Separate subject-delivery gate

The control owner reports no creation nonce in the current session projection.
The exact existing `handoff_diagnostics.stamp_session_state_worktree_binding`
writer was read: `worktree-binding.json` contains session ID, worktree ID/path,
machine and registration time, **not project or creation nonce**. It cannot be
silently treated as the required incarnation-qualified subject projection.
No nonce is inferred from paths or a new head; producer expansion needs its own
narrow owner admission. This is separate from the lock-helper decision.

## Mechanical rebase disposition

The retained branch was rebased normally onto current `origin/dev`.
Both conflicts were index context only. Every other owner's current row was
preserved; the verified index diff changes only Prompt Receipt Observation.
No reset, stash, manual force push or owner-source change was used.
