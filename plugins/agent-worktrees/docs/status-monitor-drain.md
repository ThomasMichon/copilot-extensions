# Status-monitor drain flow and diagnostics

This audits the existing installer-to-monitor handover. It does not change
admission, ownership, timeout, force or rollback policy. Related architecture:
[version cutover](architecture.md#version-cutover-reap-and-live-launcher-protection).
Ownership-transfer and serving-convergence follow-ups remain tracked by
[#5453](https://github.com/ThomasMichon/copilot-extensions/issues/5453) and
[#6389](https://github.com/ThomasMichon/copilot-extensions/issues/6389).

## What actually happens

1. The installer builds, marks complete and selects the new immutable runtime.
   Monitor handover occurs before final deployment-manifest publication.
   A stale or absent legacy monitor lock does not suppress handover when the
   active route names a live owner: the helper verifies native process identity,
   authenticated control health and an unchanged route before using the normal
   cutover. An unverified live route refuses convergence rather than reporting
   `no-live-monitor` success. This identity check does not certify roster health.
   Before activation, the installer captures that routed identity and retains
   its authenticated runtime slot even when the legacy lock is absent or stale.
   Custody and manifest publication wait for actual predecessor exit, not just
   a shutdown acknowledgement. Identity checks use the native Windows, Linux
   or Darwin process-birth token; existing route tokens remain comparable.
   Corrupt routing state or malformed active/previous rows refuse convergence.
   A live previous endpoint behind a dead or absent active owner likewise
   requires owner-led route recovery; it is not reported as monitor absence.
   Native helper-publication callbacks use the plan owner's base interpreter,
   not an independently selected bootstrap Python. Windows file-device identity
   representations differ across Python versions; the exact copy-inventory
   identity check remains required.
2. The cutover helper starts a passive monitor, waits for its bound endpoint,
   then publishes the successor route. Bound readiness is not elected serving
   readiness.
3. The orchestrator logs `drain` begin. Before sending any predecessor drain
   RPC, `ControlClient.drain` synchronously **promotes the successor** when the
   active route has changed. Promotion waits for the successor's shared
   `state_lock`, cleans retired handlers, activates its current-roster publisher
   and request surfaces, then publishes its monitor lock.
4. Only after successful promotion does the client send the predecessor RPC.
   The predecessor first waits for its own `state_lock`. Sweeps and resident
   hook decisions, including accepted session-start lifecycle work, share this
   lock. This wait is not evidence that any particular category is wedged.
5. Admission closure disables new mutation requests, closes the worktrees feed,
   and stops new managed-mux observations. Stateless worktree-status reads stay
   available. Previously accepted handlers/computations are not discarded.
6. The drain timeout starts **after** admission closure. Each poll cleans retired
   request surfaces under the shared lock, checks the busy oracle, and sleeps
   at least 50 milliseconds. Cleanup can itself wait for that lock.
7. An empty busy oracle returns `drained=true`, `clean=true`, `forced=false`.
   At the deadline, busy reasons are returned; `force=false` does not certify
   drain. The caller decides retirement or rollback. Undrain again waits for
   the state lock and attempts publisher/surface activation before lock
   publication; a permanently closed publisher can refuse it.

The busy oracle has six categories:

| Category | Work that prevents clean drain |
|---|---|
| `sweep` | The resident sweep is active. |
| `hook` | Current or retired hook-server handlers remain active. |
| `classify` | Current or retired classifier handlers remain active. |
| `tracking-write` | Current/retired write handlers or an in-flight write computation remain. |
| `worktrees-feed` | Feed handlers have not exited. |
| `worktrees-read` | Current-roster control reads have not finished. |

The separate `lifecycle` count provides session-start context, **not a new gate**.
`tracking-write` observations count handlers; `tracking-write-compute` separately
reports whether a computation is in flight. These may describe overlapping
work, so do not add them as a total. Counts are independently sampled diagnostic
observations; `busy_reasons` records the actual oracle decision.
Handler counts cover their server's tracked lifetime, not only successful
mutations: a socket being read, a rejected/fallback request or a response being
written can still be an active handler. Do not interpret them as task counts.
The control server excludes the drain RPC itself from its current-roster-read
count; it must not wait for its own handler to finish.

The nonstreaming control handler does not cancel its callback when a client
socket deadline expires. The client can report a timeout while the daemon is
still waiting for its lock or polling busy work. A response write after the
client disconnects can then fail. Consequently, client failure, daemon drain
completion and caller rollback are separate observations; one does not prove
the others happened.

## Reading the logs

The existing `<runtime-root>/status-monitor-routing/lifecycle.log` gains
`monitor-drain` JSON records alongside the orchestrator's lifecycle records.
Each trace has a generated `trace_id`, process PID, control port, `role`
(`client` or `daemon`), `control_action`, phase and monotonic `elapsed_ms`.
Records contain only fixed structural fields and category counts, never
request bodies, prompts, worktree/session identifiers, credentials or exception
messages. Existing lifecycle-log rotation remains in effect.

Client phases distinguish successor selection/promotion from the actual
predecessor request. Daemon promotion/undrain phases distinguish state-lock
waiting, retired-handler cleanup, publisher activation and lock publication.
A daemon drain records state-lock waiting **before** acquisition, admission
closure, periodic retired-cleanup/busy-check samples, and completion, timeout or
an aborted call. Progress samples are emitted at most once per five seconds
(two records per sample), not for every poll. Phase boundaries and terminal
outcomes are always recorded. An unwritable log attempts one sanitized warning
per trace; a closed or unwritable warning stream cannot change the control
response or replace an original lifecycle exception.

`timeout_s` is the requested daemon drain budget; `rpc_timeout_s` is the
client's distinct socket deadline. They are not interchangeable. Terminal
records retain the exact `drained`, `clean` and `forced` booleans, including a
forced-but-not-clean response. Noninteger or negative diagnostic counts are
omitted with their fixed field names listed in `invalid_count_fields`, never
serialized as arbitrary content.

`remaining_ms` is null before the local drain clock starts. It is not the
installer's remaining watchdog budget. Correlate phase timestamps with the
installer's own watchdog record. External process termination cannot emit a
terminal event; the last persisted begin/progress record identifies the stage
it reached.

In particular, an orchestrator `drain` begin alone proves neither that the
predecessor received the request nor that its busy oracle was reached. A
`promote-successor` begin without completion instead directs investigation to
the successor's promotion trace. Generic daemon `ready` also does not prove
current-roster readiness or agreement among selected slot, manifest, route and
monitor lock.
