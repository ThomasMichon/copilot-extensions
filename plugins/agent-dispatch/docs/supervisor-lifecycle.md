# Supervisor lifecycle evidence

The existing installer, runtime handoff, and retirement seams append structured
`supervisor-*` records to the runtime root's `lifecycle.log`, using the shared
zdd lifecycle writer and its bounded rotation. This is observability, not a
watchdog or a claim that an earlier outage's requester has been identified.

## Bounded reconciliation and progress

The master establishes its shared wait budget before declaration discovery,
registration reads and companion provider resolution. Pending reads retain the
last confirmed desired state; unfinished providers cannot supply new launch
authority. Receipt integrity runs in a separate two-worker pool, so a contended
root lock or slow tree hash cannot occupy every health/runtime worker.
Declaration conversion runs inside the discovery future. Coordinator mirroring
uses one tracked publication batch on a separate single-worker pool: a slow
register/remove cannot hold the master, and the batch checks its deadline
between requests. Uncertain metadata writes are read back before an idempotent
retry of that exact target; withdrawals stay suppressed until absence is
confirmed. A changed declaration cannot submit a concurrent publication batch.
Publication is metadata mirroring, not authority to replay task execution.

Each withdrawn receipt has one durable `*.reconcile-operation.json` record tied
to its exact process receipt and selected launch generation. `pending`, `timeout`
and `unknown` retain authority. Immutable state milestones in `reconcile-history`
preserve prior operation generations; this slice does not prune that history.
A new supervisor reads back the same generation;
it never repeats an unconfirmed retirement or withdrawal. Completion requires
absent receipts **and** confirmed retirement of the recorded process generation.
Matching durable root selection pins must also be absent, including matching
pins from another process-identity domain. Pin readback is non-mutating metadata
inspection under the root lock and its deadline; a surviving pin keeps the
operation unknown rather than authorizing cleanup or a duplicate launch.
Restart identity readback and initial owner-token sampling use the receipt
executor, never synchronous per-receipt process probes on the master. Owner
sampling waits only for the remaining shared deadline; unfinished identity work
stays in shutdown accounting. Late readback does not settle authority. A receipt
deletion failure remains unknown and cannot schedule another destructive retirement.
Completed owner probes are discarded on every outcome, so a transient probe
error or unusable token can retry in the same daemon. Receipt futures enter
shutdown accounting even when a later record aborts the controller's pass;
partial scheduling cannot be reported as a clean drain.
Shutdown retires bounded executors and clears their active references even when
work misses the grace window. A failed successor spawn can reclaim the singleton
and create usable replacement pools; original futures and publication/receipt
operations remain tracked rather than resubmitted. This is not native custody proof.
Changed receipts are superseded rather than retired. The existing crash-safe OS
lock primitive serializes the exact receipt operation. No new reservation store,
force-release policy or automatic recovery scheduler is introduced.

Selection integrity is verified while its durable root pin remains published,
outside the root-wide bookkeeping lock. Before withdrawal the same scope and
metadata are revalidated under that lock. Full content checks remain mandatory,
including runtime caches; an expired integrity deadline does not accept a
partial digest. Unknown outcomes are not authorization to delete caches or pins.
Captured command execution and post-timeout pipe draining both have deadlines.

Runtime status records the master's PID/start token, cycle, phase and receipt
operation states. `supervisor_health.supervisor_progress_observation` is a
read-only seam for an **independently invoked existing lifecycle owner**; stale
progress and process existence are different observations. Its results never
authorize replacement, restart or withdrawal. Optional progress read/publication
failures cannot abort desired-state reconciliation or hide completed mutations:
they report fixed, sanitized reason/error categories through standard logging,
limited to one warning per category per 30 seconds. Unavailable receipt progress
is marked unknown (`null` with an observation error), not a confirmed empty list.
Data/state failures in the final summary follow the same classified best-effort
rules. The observer uses only finite, non-future progress timestamps from the
current cycle and matching generation; a fresh phase can distinguish an advancing
slow cycle from a wedge. A superseded writer cannot overwrite another generation's
progress. These observations still confer no recovery authority.
Independent watchdog invocation,
custody/recovery and native platform proof remain integration gates of the
existing #5301/#5655 owners. This slice neither implements a second watchdog nor
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

## Enablement and ownership limits

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
