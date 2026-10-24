# Observation-scoped POSIX confirmation proposal

Back to [the effort](README.md) and [the contract](contract.md).
**Agent-recommended, not admitted or implemented.** This addresses the blocking
Medium finding `discussion_r4365960075` on #8458. The three High fixes do not
resolve it. Do not mark the PR ready or merge while it remains open.

## Established limitation

Current `push_timeout._run_observation` invokes `_confirm_group` for every
successful POSIX Git child. The Linux implementation performs at least two
complete `/proc` censuses with per-process group observations. Four probes
therefore require at least eight censuses, before any retry. This is new
recurring discovery work, not an acceptable hidden cleanup cost.

The inspected public `require_caller_owned_support()` is a capability
preflight, not narrow group-emptiness confirmation. Public `run_caller_owned`
still delegates an individual operation and its confirmation; using it once
per Git call does not amortize this cost or preserve the current absolute
deadline by itself. There is no inspected public Linux primitive that both
retains one observation's group identity and confirms that group just once.

Do not replace confirmation with root exit, EOF, `killpg(pgid, 0)`, an
assumption about Git descendants, or a reused unfenced host census.

## Recommended option: one existing Git root anchors one observation group

Use the **first actual Git command**, not a new helper/keeper process, as the
group anchor. Its native child custody remains unreaped until final group
confirmation, even when the command has already exited.

The first child is created in a new **process group in the caller's session**
(`process_group=0` before exec), not in a new session. Subsequent children
join that pinned PGID before exec. Retaining `start_new_session=True` and
then trying to join its group from the parent is invalid: POSIX forbids the
cross-session join. That shortcut is explicitly excluded.

These are required before-exec group semantics, not permission to raise the
supported Python floor. The owner must supply a safe adapter at the existing
floor (for example its existing native spawn boundary with POSIX setpgroup
semantics), or reject this proposal. Do not assume a newer `Popen` keyword,
use an unsafe threaded `preexec_fn`, or invent privilege/escape fallbacks.

The shared primitive exclusively owns the anchor's Popen/native custody and
exposes no caller `poll`, `wait` or `communicate` operation that could reap
it. WNOWAIT/native identity and retained handles pin the original anchor PID
and PGID. Other completed roots may be reaped only when their exit and both
pipe EOFs are observed and no later individual-PID action will be performed.
The anchor remains the signal/confirmation identity until the whole group is
confirmed empty. No signal is sent against a PGID after its anchor is reaped.

This is an opt-in descendant-custody primitive, not a cgroup, daemon, broker,
watcher, cache or new aggregate authority. It does not claim protection
against deliberate escape beyond the existing supported POSIX group contract.
If the owner cannot establish that contract for a caller/platform, refuse
before Git executes; do not guess escape privileges or add a fallback.

## Proposed API and exclusive ownership

**Requires explicit integration admission and agreement with the existing
agent-procutil owner before touching shared files.** R1a currently owns none
of these shared-API changes and will not contact existing agents to obtain it.

Proposed narrow API, in `agent_procutil.owned_process`:

```python
class PosixObservationGroup:
    def __init__(self, *, deadline: float): ...
    def spawn(self, argv, *, cwd, env) -> ChildObservation: ...
    def observed_exit(self, child: ChildObservation) -> int | None: ...
    def finish(self) -> None: ...
```

`ChildObservation` supplies pipe views and a stable child token, not ownership
of Popen. `observed_exit` uses unreaped native observation for the anchor.
`finish` owns group termination, confirmation, root reaping and pipe closure.
The scope stores the exact supplied monotonic `D`, never a fresh per-call
allowance. Any retained-cleanup exception owns the scope and its still-valid
custody/handles so the recipient can explicitly settle that obligation.

**Unresolved lifetime dependency:** an exited/unreaped Git anchor pins an
identity but is not a living cleanup executor and cannot watch a caller-lifetime
pipe. The inspected caller-owned implementation supplies that lifetime behavior
through an execution helper; silently adding another helper here is not
admitted. The procutil owner must establish an existing compatible outer
owner-lifetime boundary for the proposed group, including caller death, or
reject this option and return the architecture decision. Neither same-session
membership nor an open pidfd alone proves caller-death cleanup. This proposal
does not assert that the new primitive already exists or is qualification-ready.

Plugin integration is an explicit optional `observation_scope` keyword on the
already proposed R1a functions and `run_bounded`. The outer segment Git phase
or independent classifier/resolver creates one scope. Nested default,
untracked-branch and classifier calls receive that **same object and D**.
Mismatched scope/deadline reuse refuses. No thread-local/global current scope,
cross-observation reuse, persisted PGID or transmitted monotonic instant.

Windows need not change to amortize a Linux census: keep its existing
per-probe strict Job and zero host-census path. A Windows scope wrapper, if
useful for API parity, delegates only to those already admitted semantics.

## Admission of later probes and failures

Later probe B is allowed only after A's root exit status and both pipe EOFs
have been observed, the anchor's identity/custody remains valid, no failure is
latched, and command-wait budget remains before `D - 5s`. A genuine nonzero
Git result such as "ref absent" is not a cleanup failure; existing fallback
and offline classification semantics may continue under retained custody.

Unknown descendants are not declared gone between probes. They remain owned
by the same group; no successful per-probe result claims full cleanup.
Candidate classification facts stay local until `finish()` confirms the
entire scope. The public classifier/segment cannot return confirmed
classification or refresh evidence before that point.

Timeout, cancellation, incomplete pipe observation, failed group assignment
or lost identity latches failure and prevents every later Git probe. A known
cleanup failure is never converted to an ordinary missing ref or retried
observation. Retain the error's scope/custody; no fallback to another group.
An explicit later owner cleanup attempt does not renew D or turn the original
failed observation into a passing one.

Fresh review `discussion_r4366058208` also identified existing resident callers
that discard the error and begin again on the next tick:
`status_updater_cli.py:540-551` and `__main__.py:3171-3178` at the reviewed head.
Those caller changes are **not admitted** to this R1a proposal owner. Their
existing lifecycle owners must retain the cleanup object across ticks and
gate the affected observation pipeline until explicit settlement; logging or
rendering an empty row is not settlement. Unrelated independently owned work
must not be serialized behind a new global lock or global pending cache.
Shutdown/owner loss must settle or retain responsibility through that owner's
existing lifecycle, not silently discard native custody. The new High and the
Medium both remain blocking; this is a dependency, not a transfer/waiver.

## Deadline, capability and platform boundaries

- Keep the existing 15-second default total and five-second cleanup
  reservation **inside D**. The ten-second remote cap still applies.
- Finish/timeout/cancellation passes the same D through termination,
  confirmation, reaping and pipe closure. Add an opt-in absolute-deadline
  parameter to shared confirmation rather than silently adding its current
  two-second allowance after D. Preserve legacy callers' existing defaults.
- Insufficient budget starts no child. Cleanup not confirmed by D remains a
  failed, retained ownership obligation, never a timing waiver or fresh fact.
- Public capability preflight must cover native identity, readable membership
  evidence, before-exec group creation/join and the real caller's containment
  policy. Unsupported/access-denied cases refuse before execution.
- Preserve contained-test suppression. Do not silently create a new group in
  an inherited contained worker. Its outer owner must explicitly admit a
  compatible nested-group custody boundary; absent that, return unavailable.
- Require isolated native proof that an exited/unreaped anchor really keeps
  its group joinable on each supported POSIX backend. EPERM/ESRCH/access loss
  is a refusal/failure, not permission to detach or start an unowned child.
- No production fixture, cgroup/elevation assumption, service update or
  native test is authorized by this proposal.

## Exact proposed hunks and stop gate

| Owner | Proposed hunks, after separate admission |
|-------|-----------------------------------------|
| Shared procutil owner | `libs/agent-procutil/src/agent_procutil/owned_process.py`: narrow `PosixObservationGroup`/child-custody API, before-exec same-session group creation/join, retained anchor and terminal lifecycle |
| Shared procutil owner | `libs/agent-procutil/src/agent_procutil/_caller_owned.py`: opt-in absolute deadline on confirmation, retaining its membership/identity checks and legacy defaults; no observation cache |
| Shared procutil owner | Focused shared-library tests for unreaped anchor, group joins, refusal/access loss, failure custody, cleanup ordering and exact census counts |
| R1a owner | `push_timeout.py`: accept/use the explicit scope instead of confirming each POSIX probe; no new legacy behavior |
| R1a owner | `git_ops.py` and the two `status_bar_cli.py` Git prefixes: create/forward the scope explicitly, defer successful return until `finish`, preserve D and failure propagation |
| R1a owner | Focused scope/work-count tests and documentation/changefile; no monitor, producer, roster, receipt or fsmonitor hunk |
| Existing resident caller/lifecycle owners, separately admitted | The two identified callback catch boundaries: retain failed custody across ticks, settle before observing again, and test the actual caller loop; no lock split or new global cleanup framework |

Shared-owner agreement and the coordinator's API/containment decision are
prerequisites, not approvals supplied by this document. If this narrow primitive
is not admitted, keep the Medium finding and PR blocked. Do not ship a
per-probe census, skip descendant confirmation, or silently expand into R1b.

## Deterministic work counts and later native qualification

Before implementation admission, agree on these explicit fixtures:

- Healthy POSIX observations with 1, 4 and 20 Git probes each perform exactly
  **two global censuses at final settlement**, not 2, 8 and 40 respectively.
  Per-probe pipe/root/retained-identity checks perform zero global censuses.
- Zero probes/unsupported preflight produce zero global censuses and zero
  Git starts. One active census followed by two empty census observations
  produces exactly three, under the same D. Cleanup delay may require more
  observations; count them, never hide them behind the healthy-case number.
- Failure at probe k prevents probe k+1. Failed final settlement retains the
  anchor, identity and handles; no successful classification is returned.
- No extra keeper process. Account separately for Git starts, live roots,
  retained unreaped anchor, census calls and native exit observations.
- Scope A cannot reuse scope B's census, group or deadline. No cross-project
  ambient state is consulted while assigning children to the scope.

Pure mocks establish counts and ordering only. After explicit native admission,
exercise real same-session joins with the first root already exited/unreaped,
held pipes, delayed descendants, cancellation, caller death and loss of
membership access. Preserve identity until confirmation; reject a backend
that cannot establish it. Windows needs its own retained-Job regression, not
a Linux census claim. Native timing/resource acceptance, installed proof and
broader #2619/#6086 remain separately gated.
