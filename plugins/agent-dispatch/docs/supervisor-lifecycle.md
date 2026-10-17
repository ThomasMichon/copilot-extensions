# Supervisor lifecycle evidence

The existing installer, runtime handoff, and retirement seams append structured
`supervisor-*` records to the runtime root's `lifecycle.log`, using the shared
zdd lifecycle writer and its bounded rotation. This is observability, not a
watchdog or a claim that an earlier outage's requester has been identified.

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
