# Status-monitor drain flow and diagnostics

This audits the legacy synchronous installer-to-monitor handover. The
incoming-start-first Worktrees installer is described in the
[install contract](../../../docs/install-contract.md); it does not wait for
this predecessor drain. This audit does not change
admission, ownership, timeout, force or rollback policy. Related architecture:
[version cutover](architecture.md#version-cutover-reap-and-live-launcher-protection).
Ownership-transfer and serving-convergence follow-ups remain tracked by
[#5453](https://github.com/ThomasMichon/copilot-extensions/issues/5453) and
[#6389](https://github.com/ThomasMichon/copilot-extensions/issues/6389).

The ordinary asynchronous installer retains native predecessor custody even
when the old peer lacks its retirement/recovery capabilities or its control
request times out. The incoming resident attempts one bounded `successor-handoff`
notice before promotion, selected through its running peer's
`status-monitor-successor-handoff-v1` health capability. Health lookup and notice
share one five-second budget; missing support skips only the notice. A compatible old resident observes it without treating
it as readiness or closing admission. Independent ready-route observation still
triggers old drain. Once the incoming monitor passes actual serving readiness,
it arms a durable five-minute deadline in `status-monitor-retirement-grace/`,
without changing the legacy retirement-record schema. Installation returns
without waiting for that grace or predecessor exit. The selected resident's
existing retirement loop then attempts bounded termination of that exact PID/birth
and guarded release of its previous endpoint after expiry. A failed cleanup is
reported without rolling back the healthy incoming monitor. Runtime-reference and
dev-claim settlement remains with the existing retirement lifecycle, not a new
reaper. Unknown native identity, changed routes and unsupported incoming
capabilities still refuse their respective operation.
Deferral requires `status-monitor-retirement-grace-v1` in the incoming
preparation health. Restoring an older, pre-grace runtime retains the existing
bounded native cleanup after successor publication instead of assigning that
runtime an unsupported deadline. Canonical custody settlement, including direct
installer callers, first completes any matching grace-route cleanup; failure
retains the original record and runtime references for retry.

Mux status distinguishes `Git observation busy` from `cleanup pending`.
The former means a matching observation is still running; the latter means its
owner retained unsettled native cleanup. `observation retiring` identifies closed
admission without a matching cleanup ticket. These labels do not release custody,
refresh cached Git facts, or change drain readiness.
An already completed, uninvalidated mux segment within its existing freshness
window can still be read during ordinary observation and mutation-lock
contention. This read-only snapshot neither renews its age nor acquires another
observation. Cleanup custody, closed admission, expiry and invalidation still
prevent reuse; an unavailable segment retains the explicit UNKNOWN reason.

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
   identity check remains required. On Windows, a PowerShell Core callback uses
   an ordinary `-File` spelling while retaining native long-path inputs for
   Python and the authenticated copy inventory. Execution policy is unchanged.
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

The busy oracle has eight categories:

| Category | Work that prevents clean drain |
|---|---|
| `sweep` | The resident sweep is active. |
| `hook` | Current or retired hook-server handlers remain active. |
| `classify` | Current or retired classifier handlers remain active. |
| `tracking-write` | Current/retired write handlers or an in-flight write computation remain. |
| `worktrees-feed` | Feed handlers have not exited. |
| `worktrees-read` | Current-roster control reads have not finished. |
| `repos-map-read` | Registered-repository map control reads have not finished. |
| `repos-map-producer` | The owned repository-map producer is refreshing. |

The separate `lifecycle` count provides session-start context, **not a new gate**.
`tracking-write` observations count handlers; `tracking-write-compute` separately
reports whether a computation is in flight. These may describe overlapping
work, so do not add them as a total. Counts are independently sampled diagnostic
observations; `busy_reasons` records the actual oracle decision.
Handler counts cover their server's tracked lifetime, not only successful
mutations: a socket being read, a rejected/fallback request or a response being
written can still be an active handler. Do not interpret them as task counts.
The control server excludes the drain RPC itself from its inventory-read
count; it must not wait for its own handler to finish.

## Local-status shutdown settlement

The 120-second retirement decision and bounded local-status `shutdown()` are
not worker-completion proofs. At final teardown the monitor uses
`InProcessRuntime.shutdown_and_wait()`: close local-status admission, perform
the existing bounded stop, then retain host daemon admission until the owned
sweep target has finished and its thread is joined, and the concrete status
server's full accepted-handler count reaches zero. Teardown retains those
owners even if runtime fields are cleared. Partial-start cleanup uses the
same settlement path; repeated interrupts cannot release custody early.

Final teardown attempts each owned surface even when an earlier cleanup raises.
It retries failed intake/authority cleanup without treating an exception as a
successful zero count, and preserves the original monitor error (or first
cleanup error) until settlement finishes. Local-status stop failures retain
their concrete server/sweep/cache owners: intake must actually close before a
zero handler count authorizes release. A permanently failing close retains
custody rather than claiming retirement.

This host-only wait runs outside cache/server locks, not in a status request
handler. A closed cache fences late SQLite access but does not mean assembly
has retired. The wait does not change election, drain deadlines, supersession
authority, caller-owned feed subscribers or independent replacement children.

The nonstreaming control handler does not cancel its callback when a client
socket deadline expires. The client can report a timeout while the daemon is
still waiting for its lock or polling busy work. A response write after the
client disconnects can then fail. Consequently, client failure, daemon drain
completion and caller rollback are separate observations; one does not prove
the others happened.

## Managed-mux handler custody

The separate `mux_link.InProcessRuntime` intrinsically retains a reference to
the existing host daemon admission before constructing its cache or listener.
Its unchanged `shutdown()` closes native intake and fences the cache, but a
runtime-owned retirement worker retains the concrete server, cache and admission
until the native accept/reaper threads and every accepted handler have finished.
A closed cache, a cleared runtime field or an unknown handler count is not
settlement. No handler joins itself, and no new caller method is required.

Shutdown attempts the cache fence even if native close raises. Transient native
cleanup failures are retried before the original error is propagated; repeated
interrupts do not replace that first error. If native closure remains unavailable
beyond the bounded caller observation, the concrete retirement owner continues
holding admission rather than certifying a zero count. Production custody has
no expiry. Routing, election, drain ACKs and replacement authority are unchanged.
The monitor's existing final cleanup must still reach `managed_mux_runtime.shutdown()`
when an earlier surface fails; this intrinsic custody does not execute skipped calls.

## Reading the logs

Ordinary monitor exit also appends `monitor-exit` records to the same
`lifecycle.log`: `cleanup-begin`, then `cleanup-complete` or `cleanup-failed`.
The fixed `reason` distinguishes `idle-no-demand`, `shutdown-requested`,
`superseded-runtime`, `superseded-route`, `interrupted` and `loop-error`.
Completion means the existing teardown returned, not that a replacement is
ready or that the process has been independently observed to exit.
Committed idle retirement retains its idle reason while accepted work drains;
the shared retiring latch alone does not establish a superseding route.
A successful `undrain` or `promote` clears that pending idle cause after
discovery publication succeeds, so a later retirement records its own cause.

`last_idle_eligibility` contains only the checks actually evaluated by the
most recent idle predicate, in their original order. `true` means that check
allowed idle retirement; `false` stopped evaluation. Omitted checks were not
evaluated, and an empty object means no idle evaluation was recorded. This is
a last observation, not a simultaneous demand snapshot or proof of the cause
of a separately requested shutdown. It includes recent completed tool-hook
demand after in-flight work, without changing its grace interval or admission.
The monitor records no raw request, project, session or exception data, performs
no extra demand probes, and does not write these diagnostics during ordinary
sweeps. Log failure remains best-effort and does not change teardown.
Diagnostic interruptions cannot bypass mandatory cleanup or replace its original
error, including when a log append raises `KeyboardInterrupt`.
These records do not start a missing monitor or qualify live hook recovery.

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

## Read-only identity observation

`runtime_monitor_identity.candidate_serving` and `routed_identity` read an
existing control token from the captured route's runtime root. They never
create, replace or change permissions on that token. Missing or blank tokens
make candidate readiness false and ordinary routed identity unavailable.
Other token-read errors propagate without repair. Explicit service startup
and mutation paths retain `load_or_create_control_token`; the default
`ControlClient` constructor is not a read-only API.

Asynchronous-retirement identity may still use the existing native PID/birth,
unchanged route and matching lock-prefix fallback when authentication is
unavailable. That fallback is not readiness or an authenticated capability
claim: `require_capabilities=True` still requires authenticated health.

The provider module advertises `READ_ONLY_OBSERVATION_CAPABILITY` with value
`worktrees-monitor-observation/read-only-v1` for these two operations only.
An independently deployed caller that requires non-mutating observation must
check this capability on its selected provider before invoking either
operation, and refuse an older provider without it. This is a local provider
capability, not a new wire request or daemon-health capability. It does not
certify other probes, authorize replacement, or complete the serving-convergence
work tracked by [#6389](https://github.com/ThomasMichon/copilot-extensions/issues/6389).
