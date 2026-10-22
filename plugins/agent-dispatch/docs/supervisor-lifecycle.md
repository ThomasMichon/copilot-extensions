# Supervisor lifecycle evidence

The existing installer, runtime handoff, and retirement seams append structured
`supervisor-*` records to the runtime root's `lifecycle.log`, using the shared
zdd lifecycle writer and its bounded rotation. This is observability, not a
watchdog or a claim that an earlier outage's requester has been identified.

## Bounded reconciliation and progress

The master establishes its shared wait budget before declaration discovery,
registration reads and companion provider resolution. Pending reads retain the
last confirmed desired state; unfinished providers cannot supply new launch
authority. Registry reads and their nested snapshot copy share one bounded
discovery operation; only the master accepts a completed snapshot. A slow copy
retains the previous confirmed state without sacrificing nested-spec isolation.
Before the first successful store snapshot or configured declaration
snapshot, a pending read is explicitly indeterminate, not a confirmed empty
registry: reconciliation defers
before stopping units or scheduling receipt withdrawal. Receipt integrity runs
in a separate two-worker pool, so a contended
root lock or slow tree hash cannot occupy every health/runtime worker.
Receipt inventory, including receipt/selection/operation metadata reads, runs on
its own single-worker executor. The master consumes only completed results and
keeps the latest adopted set authoritative while an inventory is in flight.
Pending inventory is tracked alongside the operations it schedules; shutdown
refreshes child futures after an inventory finishes within the same grace budget.
Completed parents are pruned before sampling child futures, so a parent finishing
during sampling cannot hide a still-running receipt operation from shutdown.
Removing or pausing a running managed companion stops its process first, then
updates the inventory's adopted set and queues its exact-generation withdrawal.
Integrity scanning never runs inline on that master removal path; selection pins
remain until confirmed withdrawal, including when the operation deadline expires.
Unknown orphan operations are fences, not newly adopted units.
Companion providers have a separate four-worker pool from authoritative
declaration/registration discovery, so four long-running valid providers cannot
hide unrelated additions or removals.
An unresolved unmanaged provider also retains its receipt fence while its
declaration remains present; lack of a confirmed launch is not withdrawal.
Withdrawal invalidates a provider's cached authority even if its running future
cannot be cancelled. That future remains tracked until it finishes; reintroducing
the same declaration must resolve it afresh rather than consume a withdrawn result.
A changed authority also invalidates its uncancellable observation immediately;
returning from authority A to B and then A cannot revive the first A's result.
Declaration conversion runs inside the discovery future. Coordinator mirroring
uses one tracked publication batch on a separate single-worker pool: a slow
register/remove cannot hold the master, and the batch checks its deadline
between requests. Uncertain metadata writes are read back before an idempotent
retry of that exact target; withdrawals stay suppressed until absence is
confirmed by a scoped registry read started after publication withdrawal was
confirmed. Earlier in-flight registry results are discarded, and cached registry
rows remain suppressed during that wait. A changed declaration cannot submit a
concurrent publication batch.
Completed readback rows that arrive after the batch deadline remain in its
detached per-ID ledger for a later budget, instead of triggering the same slow
read on every cycle. Cached observations are consumed before another read,
discarded when their captured target changes, and removed before any write.
An unknown write still settles its original target before a new declaration.
Only the master harvests completed batch ledgers. Workers return partial custody
with any unexpected failure; the master records it before surfacing the original
exception, so an uncertain earlier write cannot become unstarted work.
Missing and explicitly null declaration environments both use `default` for
publication and readback comparison.
Publication is metadata mirroring, not authority to replay task execution.
Publication readback is deliberately registry-wide: an uncertain write retains
its captured target even when its machine/environment differs from the next
declaration. A scoped absence must not replay that older write or mistake an
ID collision in another scope for a confirmed withdrawal. This read remains in
the single tracked publication worker, never on the master.

Each withdrawn receipt has one durable `*.reconcile-operation.json` record tied
to its exact process receipt and selected launch generation. `pending`, `timeout`
and `unknown` retain authority. Immutable state milestones in `reconcile-history`
preserve prior operation generations; this slice does not prune that history.
A new supervisor reads back the same generation;
it never repeats an unconfirmed retirement or withdrawal. Completion requires
absent receipts **and** confirmed retirement of the recorded process generation.
Unmanaged receipts require the same exit confirmation as managed receipts.
Completed operations are reaped before scheduling, and admission rotates through
receipt identities so two unknown readbacks cannot starve a later withdrawal.
Unsupported or missing process-receipt schema versions remain indeterminate and
cannot authorize retirement. Corrupt metadata is logged per path or identity and
retained, fencing that identity without aborting valid sibling scheduling or
progress collection. If any operation-progress record is unreadable or invalid,
the whole optional inventory observation is unavailable; valid sibling rows are
not presented as a complete inventory, and record bytes remain unchanged.
Provider metadata failures use the same indeterminate contract.
Local operation-metadata readers and atomic writers share bounded per-path locks,
so a Windows reader cannot reject a simultaneous local atomic replacement or
hold an unrelated identity's lock. Lock entries are released when their last
user leaves. Master-side availability observations use the existing provider
executor, with at most the remaining shared budget and a 50ms wait per check;
unfinished metadata reads remain tracked through shutdown. A successful
observation can serve nested checks in its consuming cycle, but expires before
the next cycle. Operation-write attempts invalidate it before I/O, including
committed writes whose response is lost; withdrawal invalidates unused results.
Malformed operation state types remain indeterminate rather than escaping the
per-identity corruption fence.
Generation receipt/selection values must likewise be objects or null, even
when their recorded digest matches. A digest does not validate their shape.
Canonical Windows JSON and metadata-hash readers use read/write/delete sharing,
and their writer uses native atomic rename with retained-reader semantics, not
multi-step replacement that can remove the destination on failure. A held
inventory reader cannot deny selection publication and retire a ready process;
the reader still rejects metadata that changes during its strict observation.
An executor submission failure cancels admission before marking an exact
unstarted operation superseded. A late queued callback then cannot execute.
If execution already started, its separate future stays tracked and its durable
outcome is retained, not relabelled as unstarted.
Accepted-but-cancelled queued mutations retain their captured generation until
the same unstarted settlement is recorded. A failed settlement write retains
that proof while this owner is alive; a committed response loss is read back
without rewriting it. A cancelled readback or changed generation cannot confer
unstarted-mutation authority. Abrupt owner loss still retains the durable
unknown fence rather than permitting replay.
Directory enumeration propagates access failures as indeterminate for both
progress and inventory scheduling. Only genuinely missing or empty directories
produce a confirmed empty observation; denied scans cannot silently hide records.
Matching durable root selection pins must also be absent, including matching
pins from another process-identity domain. Pin readback is non-mutating metadata
inspection under the root lock and its deadline; a surviving pin keeps the
operation unknown rather than authorizing cleanup or a duplicate launch.
An interrupted launch with a managed process receipt and root pin but no local
selection file withdraws that validated pin after confirmed retirement too.
Restart identity readback and initial owner-token sampling use the receipt
executor, never synchronous per-receipt process probes on the master. Owner
sampling waits only for the remaining shared deadline; unfinished identity work
stays in shutdown accounting. Late readback does not settle authority. A receipt
deletion failure remains unknown and cannot schedule another destructive retirement.
Completed owner probes are discarded on every outcome, so a transient probe
error or unusable token is logged and fenced, then can retry in the same daemon.
Receipt futures enter
shutdown accounting even when a later record fails in the controller's pass;
partial scheduling cannot be reported as a clean drain.
Completed legacy reconciliation futures are cleared on success or failure;
only genuinely pending work is retained. A worker-raised timeout is surfaced as
a failed operation, not mistaken for a still-running wait.
Optional progress-worker construction or submission failures cancel queued work
on the failed pool and clear it for a later retry. They emit the same classified,
throttled warning without aborting discovery or hiding completed unit mutations;
the latest progress and final-status frames remain coalesced until admission succeeds.
Receipt reconciliation invoked outside an active cycle uses a forward call
budget; an existing cycle deadline, including an expired one, is not extended.
Cycle deadline and admission-token references are cleared on both success and
exception exits. A failed cycle retains its last phase for diagnosis rather
than claiming completion; a later retry gets fresh cycle authority.
Cold managed-selection fallback uses the same bounded provider pool and
remaining cycle deadline as provider resolution. Pending reads remain tracked
and indeterminate; withdrawal or changed declaration authority invalidates
their eventual result. Corrupt selection metadata withholds that companion
without aborting unrelated units or changing its evidence.
Declaration-authority changes invalidate pending selection observations before
provider resolution, even when the changed provider is inactive or invalid.
Returning to an earlier authority cannot reuse a pre-change result; unfinished
reads remain in shutdown accounting until they actually finish.
Managed recovery consumes that accepted persisted snapshot rather than reading
the same selection again on the master. A later cold lookup also uses the
bounded observation path.
Shutdown retires bounded executors and clears their active references even when
work misses the grace window. A failed successor spawn can reclaim the singleton
and create usable replacement pools; original futures and publication/receipt
operations remain tracked rather than resubmitted. This is not native custody proof.
Changed receipts are superseded rather than retired. The existing crash-safe OS
lock primitive serializes the exact receipt operation. No new reservation store,
force-release policy or automatic recovery scheduler is introduced.
A generation with neither managed selection metadata nor a managed snapshot in
its process receipt does not scan unrelated transition groups before retirement.
Managed generations retain their selection/integrity validation, and every
retirement still checks matching root selection pins before reporting completion.
Selection-pin enumeration propagates access and I/O failures rather than
interpreting an unreadable directory as empty. Failed observation retains the
unknown operation and receipt-availability fence without deleting surviving pins;
only a genuinely missing directory or a completed scan can confirm absence.

Selection integrity is verified while its durable root pin remains published,
outside the root-wide bookkeeping lock. Before withdrawal the same scope and
metadata are revalidated under that lock. Full content checks remain mandatory,
including runtime caches; an expired integrity deadline does not accept a
partial digest. Unknown outcomes are not authorization to delete caches or pins.
Receipt retirement carries that verified snapshot into the final metadata-only
scope, presence and record check; it never rehashes a runtime after irreversible
retirement. Final pin withdrawal and absence verification share a separate
one-second bookkeeping budget, so retirement exhausting the execution budget
does not itself strand an otherwise valid selection. Metadata changes, lock
contention or write failures still retain unknown authority.
Captured command execution charges process-creation elapsed time against its
timeout; post-timeout pipe draining has its own one-second deadline. The child
is polled non-blockingly afterward to reap an exited direct child even
when a descendant still holds its pipes; the timeout remains indeterminate.
The OS process-creation call itself cannot be interrupted by this subprocess API.
The containment gate runs its pinned, standard-library-only script directly,
without initializing the coordinator package or resolving it through the
contributed command's working directory.

Runtime status records the master's PID/start token, cycle, phase and receipt
operation states. `supervisor_health.supervisor_progress_observation` is a
read-only seam for an **independently invoked existing lifecycle owner**; stale
progress and process existence are different observations. Its results never
authorize replacement, restart or withdrawal. Optional progress read/publication
failures cannot abort desired-state reconciliation or hide completed mutations:
they report fixed, sanitized reason/error categories through standard logging,
limited to one warning per category per 30 seconds. Unavailable receipt progress
is marked unknown (`null` with an observation error), not a confirmed empty list.
Status-file read errors expose fixed I/O, JSON and object-shape classifications,
not filesystem paths or raw exception text.
Receipt metadata failures likewise expose only a fixed unavailable category,
not the underlying filesystem exception or receipt path.
Captured command failures expose a fixed unavailable message. Declaration
discovery failures log only their error type, without exception text or
tracebacks; confirmed declarations remain retained and cold discovery stays
indeterminate.
The outer serve loop also logs expected indeterminate failures without their
exception chains. Process-receipt availability uses the same sanitized metadata
reader as selection and operation records, including when provider resolution
reports the failure.
Receipt progress, inventory, worker completion and scheduling logs expose only
the exception category, not its underlying path or provider message.
A controller without a callable progress observation reports
`receipt-progress-unsupported`; only a completed observation may report `[]`.
Data/state failures in the final summary follow the same classified best-effort
rules. The observer uses only finite, non-future progress timestamps from the
current cycle and matching generation; future cycle starts and invalid or future
completion timestamps (including booleans) are unknown, not healthy progress.
A fresh phase can distinguish an advancing
slow cycle from a wedge. A superseded writer cannot overwrite another generation's
progress: both its PID and confirmed native start token must match the stored
generation before publication. Missing receipt-operation observations remain
unknown, including a cycle-start record that has no inventory yet. These
observations still confer no recovery authority.
Collection and filesystem publication are also off the master: one tracked
progress worker coalesces the latest phase/cycle-start/final-status frames instead
of accumulating a backlog behind a blocked status volume. Final summaries use
completed cached observations or an explicit pending/unknown marker. Cycle-start
and final-status I/O share this worker, and completion timestamps are captured by
the master rather than refreshed by delayed publication.
Completed probe futures are removed from cycle accounting; unfinished futures
remain tracked through shutdown.
The idle-worker handoff is atomic with producer admission, so a frame arriving
as the worker finishes is not lost. A coalesced final summary from a superseded
cycle cannot be written under the newer cycle's start marker. An inline update
drains old-generation status I/O within the existing grace window before stopping
any managed unit. If publication remains unresolved, it retains both the singleton
and running units without arming spawn cooldown; it may hand off once publication
settles.
An unexpected exception escaping the optional progress worker is classified and
logged when its completed future is collected. It cannot abort the next master
phase; the newest frame starts a replacement worker without replaying a consumed
publication.
Independent watchdog invocation,
custody/recovery and native platform proof remain integration gates of the
tracked #5301/#5655 objectives. This slice neither implements a second watchdog nor
claims that same-loop status writing proves independent recovery. The inner-lane
conclusion boundary in #6185 is unchanged.

## Record contract

The outer record contains UTC `ts`, emitting `pid`, source hostname `node`, `service`, `version` when
known, `action`, and `outcome`. The detail schema is
`agent-dispatch.supervisor-lifecycle/v1`. It carries the owning source `origin`,
lifecycle `verb`, invocation `correlation`, initiating PID and parent PID, unit
identity, reason, observed native enabled/active states, and selected profile.
Linux action records include the sampled native MainPID when available;
retirement result records include selected and retired process IDs. Selection
facts are explicitly **audit-time observations**, not a claim that an env file
could not change between the branch decision and emission.
No command line, credentials, arbitrary environment fields, or env-file contents
are recorded.

Selection fields include normalized `mode`, boolean `label_opt_in` and
`configured_enabled`, and the configured machine selector. Installer
`scope_source` identifies a configured selector or unresolved runtime discovery;
it does not claim to know flags supplied through extra arguments. Runtime
records identify the actual resolved machine and environment. A spawned
successor's acceptance is not evidence of singleton acquisition or survival.
Each runtime reports its own acquired scope separately.

| Evidence | Meaning |
|---|---|
| `runtime-ready` / `import-health-gate` | The runtime import health gate passed; service reconciliation has not yet completed. The slot's `.install-complete.json` is a build receipt, not whole-service readiness. |
| `service-reconcile` / `configured-supervisor-pass` | The installer entered its supervisor reconciliation pass. |
| `service-reconcile` / `sequence-complete` | That pass returned; `fail` records a reported native action failure. This is not an assertion that every declared pool is healthy. |
| `installer-reconcile` / `invocation-returned` | The whole install/update service-reconciliation sequence returned normally, after its coordinator and supervisor steps. This is not an assertion of pool health or successful external actions that the installer only warned about. |
| Native action `begin`, followed by `ok` or `fail` | The owning seam attempted an action and recorded its command result. A lone `begin` has no inferred result. |
| `selection` / `prior-native-disabled` or `prior-native-masked` | The installer observed and preserved a native disable/mask. Its provenance is **unknown**, not attributed to a human. |
| `installer-inert` | This invocation selected an unlabeled legacy configuration and stopped/disabled it. |
| `explicit-no-supervisor`, `explicit-client-only`, or `client-only-policy` | The owning installer selected its explicit opt-out or client-only policy. This does not identify who invoked it. |
| `handoff` / `spawn-request-returned` | The self-update spawn call returned; no successor health/lease claim is made. |
| `handoff` / `native-restart-requested` | An identity-confirmed native supervisor committed exit 42; the service manager, not a detached successor, owns the replacement launch. |
| `retire` / `skipped` | The retirement CLI performed no POSIX process retirement. |

Records share a correlation within their owning invocation. The installer and
retirement CLI are separate processes and therefore have distinct correlations;
their initiating/parent process metadata provides the local linkage without
copying the environment or argv. Missing process metadata remains unknown.

An unavailable audit writer emits the repository-standard warning and does not
suppress the action it observes. Native command failures are also reported,
instead of being converted into successful audit outcomes. Audit loss is thus
visible, but this log is not a transactional service-manager audit.

The private Windows retirement subprocess returns one complete JSON object on
stdout, either compact or pretty-printed. Its stderr remains diagnostic output;
the installer retains the native stream records until parsing, so diagnostics
cannot be mistaken for JSON properties or braces. The result requires a boolean
`ok`, selected/retired PID arrays (retired identities must be selected), and a
string-array `errors`. Failed typed results retain their errors and process exit
code. Empty, malformed, multiple or non-object stdout results fail explicitly
with a diagnostic before the existing identity-scoped native fallback; partial
retirement failures do not authorize a broader second classifier.

## Enablement and ownership limits

The Windows interactive-logon fallback starts the installed supervisor launcher
through shared `agent_procutil.spawn_daemon`, retaining its output in
`supervise-launcher.log` under the installation root. Raw breakaway flags can
escape an inner installer Job while leaving the launcher inside an outer
updater Job; shared lifetime brokerage owns that boundary. A failed durable
launch propagates to the installer, without a success audit or a new logon
registration. This does not change serve-mode, label or native-disablement
selection, and a successful spawn is not proof that every pool is healthy.

`tests/test_supervisor_logon_lifetime_native.py` is the separate opt-in Windows
production-lifetime regression. With `AGENT_DISPATCH_TEST_LOGON_LIFETIME=1`,
outside the contained test runner, it exercises the extracted installer function
for two fresh launch/cleanup cycles: close an inner installer Job and the outer
caller Job, require the bounded control to exit and the fixture resident to
survive, then retire only pinned fixture processes. Contained runs skip this
lane rather than disabling test containment.

Fresh eligible supervisors retain the label/serve-mode opt-in gate. Existing
disabled or masked native supervisors are not automatically re-enabled.
Runtime-only masks (`masked-runtime`) are also preserved and recorded as such.
After a persistent mask is explicitly removed, rerun the installer/update to
restore its unit definition before enabling it.
An inert configuration that was installed disabled also requires an explicit
native enablement after its configuration changes; there is no safe inference
that a disabled unit is merely installer-generated rather than operator-owned.
A disabled or unconfirmed native master does not authorize retirement of its native profile
units; both native adapters require positive master activation. Pool overrides and
budget pauses are not modified. On Windows, enabled profiles stopped by the
generation-retirement pass are restarted when the master remains disabled;
disabled profiles are not started or re-enabled.

External `systemctl`/Task Scheduler actions do not pass through these seams.
They have no fabricated source-owned event. A source log cannot reconstruct
an earlier external D-Bus caller or prove when an unlogged disable occurred.

POSIX `start_new_session` does not escape a systemd service cgroup.
`KillMode=control-group` can terminate a self-update successor still owned by
its predecessor's unit. A runtime spawn log alone is insufficient lifetime
proof. For a native Linux supervisor, the generated launcher exports its main
PID and the unit supplies its exact identity through the launcher's
`--systemd-unit` argument, overriding any stale editable env metadata.
When both match the daemon and
its live cgroup, self-update drains its units, releases its singleton lease,
and returns exit 42 without spawning a detached successor.
`Restart=on-failure` owns that replacement, and the stable launcher reuses the
existing installed runtime resolver to select the current complete slot instead
of the installer's old pinned interpreter. Exit 42 is not a success status.
Neither restart policy nor resource-job containment is widened.

An isolated native resource-unit fixture confirmed that a detached successor
shares its predecessor's cgroup and does not survive exit 0 or exit 42 with
`KillMode=control-group`. That fixture had `Restart=no`; it is not an
end-to-end proof of the production unit's crash-restart cycle. Unit tests prove
the source-owned restart request emits 42 without a successor and that the
generated launcher resolves the newer complete slot. Full native restart-loop
and platform rollout validation remain distinct from those proofs.
`Restart=on-failure` still does not restart a deliberately stopped unit or a
successful exit.

The Linux coordinator and supervisor units limit starts to five within a
120-second window (`StartLimitIntervalSec=120`, `StartLimitBurst=5`). Their
five- and ten-second restart delays therefore cannot evade the circuit breaker
during a persistent failure. Installer activation clears the start-rate latch
with `reset-failed` before requesting a restart; supervisor recovery uses the
same audited native-action seam as enablement and restart. Disabled, masked,
and inert supervisors remain gated off. A coordinator unit refresh after a
successful graceful cutover (`--no-restart`) neither resets the latch nor
restarts the coordinator.

Windows and non-native launchers retain their existing spawned-successor
handoff. The native ownership gate is Linux-only and requires exact main-PID
and cgroup identity. Valid native-unit metadata with an unreadable or mismatched
main PID/cgroup defers before mutation instead of selecting detached spawn;
only a genuinely non-native launch uses the retained spawn path.

This closes the diagnosability portion of the
[plugin-services vision](../../../visions/plugin-services/README.md) under the
[service-lifecycle-supervision pattern](../../../docs/patterns/service-lifecycle-supervision.md).
The historical disable-requester investigation remains open in
[issue #6137](https://github.com/ThomasMichon/copilot-extensions/issues/6137).

See also: [agent-dispatch](../README.md) and
[spawn supervisor](spawn-supervisor.md).
