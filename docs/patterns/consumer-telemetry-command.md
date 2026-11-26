# Consumer telemetry command contract — v1

**Status: v1 contract with opt-in Bridge and Dispatch source implementations.**
The [Bridge reference](../../plugins/agent-bridge/docs/consumer-telemetry-command.md)
defines its trusted admission and selected-runtime metadata location.
The [Dispatch reference](../../plugins/agent-dispatch/docs/consumer-telemetry-command.md)
defines its independent coordinator-owned admission and closed source projection.
Source support does not establish a promoted release or installed adoption.
Track realization in
[consumer telemetry sink adoption](../../efforts/active/consumer-telemetry-sink-adoption/README.md)
(#13545), starting with U1. Do not configure a live receiver on the strength
of this document or a package version alone.

The bounded native performance, compatibility and loss packet is documented in
[consumer telemetry qualification](consumer-telemetry-qualification.md).
Its source measurements do not establish installed adoption or zero-loss delivery.

This closes the U0 design slice of
[Portable Telemetry](../../visions/telemetry/README.md), specifically
`harness-owned-sink-selection`, `continuous-executable-sink` and
`daemon-free-command-adoption`. It applies
[service contract evolution](service-contract-evolution.md), à-la-carte
independence and content-free, bounded emission. HTTP, endpoint commands,
SDK installation and backend selection are not prerequisites or v1 features.

## 1. Owner and current-source inventory

The implementation home is **`libs/process-telemetry`**, distributed through
its existing consumers. Extend its existing `SinkSlot` / `BufferedSink`
owner; do not add a bus, registry, SDK or independent reporter service.
One shared configuration parser and command adapter serve plugin adapters.
`process_telemetry.command_config` and `process_telemetry.command` own that
selection and delivery; plugin adapters do not duplicate them.

The inventory below describes checked-out source, not installed adoption.
Source links name the actual record owner, not a catalog of desired hooks.

| Existing source/consumer | Actual seam and meaning | Command-route scope |
|---|---|---|
| [Bridge telemetry](../../plugins/agent-bridge/src/agent_bridge/telemetry.py) | `session_lifecycle_event` and `SessionTraceReducer`: session health, session/conversation/tool state transitions, rebuild markers; original source cursors and log epochs where available | U1 first consumer: structural lifecycle projection below, through its normal owner initialization and real transition |
| [Bridge inbox delivery](../../plugins/agent-bridge/src/agent_bridge/delivery_telemetry.py) | Already offers reduced `inbox_delivery` observations to the existing owned buffer, not directly to every callable sink | Not admitted in v1; U2 must account for these dropped/uncovered records, not claim all Bridge events |
| [Bridge diagnostics](../../plugins/agent-bridge/docs/diagnostic-logs.md) and watchdog | Retained application diagnostics and watchdog-owned bounded observations are distinct owners | No automatic enrollment; no raw application logs in this route |
| [Dispatch telemetry](../../plugins/agent-dispatch/src/agent_dispatch/telemetry.py) | `task_lifecycle_event` and `producer_fence_event`; factory, legacy spool and explicit retained-spool APIs | U2: `dispatch.lifecycle.v1` through the same command owner; trusted service policy binds stored task repositories and an explicit, separate service route |
| [Dispatch emission diagnostics](../../plugins/agent-dispatch/src/agent_dispatch/emission_trace.py) | An independently owned `BufferedSink` writes content-free stderr diagnostics | Explicitly inapplicable to lifecycle command v1; not enrolled by library import |
| [Dispatch hourly reporting](../../plugins/agent-dispatch/src/agent_dispatch/hourly_reporting.py) | Reads retained sources and reduces hourly reports | Explicitly inapplicable: reader/reducer, not a command producer |
| [Manager source observation](../../worktree-manager/docs/source-observation.md) | Existing frame-health writer, typed performance measurements and optional real OTLP exporters | Reserved under #7935/#5900; no command adoption in this slice |
| Machines health/service reports | Imports the shared measurement/report/hourly schemas and reducers, not a lifecycle `SinkSlot` registration | Out of command v1; preserve existing report collection |
| Bridge/Dispatch `diagnostic_logs.py`, `telemetry_cli.py` | Diagnostic-log ownership and retained-spool configuration/maintenance | Explicitly inapplicable: independent retained diagnostics and management APIs, not exported application logs |
| Bridge represented/restored `EventLog` owners | `live_representation.py` / `live_runtime.py` preserve reducer state and explicit missing admission | Under bound mode these lack an admitted initiating operation and suppress export; no inferred caller identity or promise of represented-session command coverage |
| `tools/performance/manager_native_probe.py` | Explicit native validation uses signal/OTLP APIs | Validation-only; remains with #7935/#5900, not a production command consumer |
| `tools/reproducer-lab/initial_case*.py` | Diagnostic-log type/fixture and local tests | Validation-only; not another production lifecycle producer |
| [QoS/Engagement](telemetry-qos-engagement.md) and [event catalog](../telemetry-event-catalog.md) | Proposed normalized semantics/catalog; not the existing lifecycle wire schema or evidence of installed hooks | Not a second payload schema to implement in U1 |
| [Finite-owner stage, PR and hook projections](../../libs/process-telemetry/src/process_telemetry/stage_projection.py) | Shared closed schemas, `finite_owner` admission and the `hook_journal` for short-lived tool owners | Registered for agent-worktrees, agent-codespaces and agent-containers; each provider adopts in its own PR and descriptor, so registration alone is not adoption |

Bridge and Dispatch currently load their convention-located `telemetry.json`
factory or spool selection and expose synchronous callable sinks. Their
startup owners try configuration before the environment factory fallback.
The [shared library reference](../../libs/process-telemetry/README.md)
owns those existing semantics, retained-spool custody and buffer bounds.

This reconciles the actual checked-out Python imports of `process_telemetry`
across runtime plugins, Manager and tools, excluding library-internal helpers
and test suites. Owners are the linked producer modules and the retained
#7935/#5900 campaign, not every `agent-*` plugin. Library dependency declarations
alone are not adoption. Direct queue mutations or SSE-only call sites with no
existing telemetry offer remain **unobserved**; this change adds no missing
instrumentation. Uninstrumented plugins, dynamic external factories and
installed generations have **unknown** coverage outside these source witnesses.
Those are open #13545/U3 qualification inputs, not zero losses or implicit
suite-wide adoption. Inbox, watchdog and represented-session expansion would
need separately reviewed source/admission work; no owner scope is transferred.

### Source reservation and admitted implementation boundary

U0 changes only this contract, its index and the effort record. Public
coordination is #13545; #7935/#5900 retain performance ownership. The source/
export slice released through #7991/#8002/#8035/#8057 does not release the
broader native, collector, installation or rollout gates. Later bounded
reservations on those trackers do not transfer their files to this effort.

U1 may propose additive configuration/command modules and focused tests in
`libs/process-telemetry`, plus Bridge's existing telemetry adapter and the
smallest necessary existing initialization/request-admission integration.
Preserve the public callable APIs, lifecycle reducer outputs and measurement
interfaces. Before changing an integration hunk reserved by another owner,
obtain that owner's public API/file-scope agreement on #13545 or its linked
reservation. A review of this document is not a transfer of those files.
Do not edit Manager, entry/launch instrumentation, measurement/export,
collector, native evidence or update-telemetry sources to implement this route.

## 2. Configuration and selection

Contract identity: **`consumer-telemetry-command`**, major **1**.
The initiating, already trusted harness root owns:

* `.agent-telemetry/config.json`: shared declaration.
* `.agent-telemetry/config.local.json`: optional, user-local, Git-ignored
  replacement; never committed or projected into shared configuration.

The shared parser reads only these exact paths at an admitted root.
There is no upward directory search, global default sink, implicit
knowledge-repository lookup or environment-variable command override.
The existing harness/request admission owner supplies the exact root; the
telemetry library does not adopt or trust a repository.

Example shared declaration (synthetic; not activation instructions):

```json
{
  "schema_version": 1,
  "mode": "command",
  "allow_local_override": false,
  "command": {
    "argv": ["python", "tools/receive_telemetry.py"]
  }
}
```

`schema_version` is integer `1`, not Boolean. `mode` is exactly `disabled`
or `command`. `allow_local_override` is Boolean, default `false`, and is
permitted only in the shared file. `command` is required only for command
mode and contains only `argv`. Disabled configuration contains no `command`.
Reject duplicate/unknown fields, nonfinite JSON values, unsupported versions,
invalid UTF-8 and files larger than **16,384 bytes**. Read each opened file
with a limit-plus-one check, not an earlier pathname-size check.

`argv` is 1–32 nonempty strings, each at most 512 Unicode characters,
with no NUL and at most **8,192 UTF-8 bytes** in total. The executable and
interpreter dependencies must already exist. No credentials, record fields,
session identifiers or dynamic template substitutions enter argv.

### Precedence (a selection, not an object merge)

| State at an explicitly bound root | Result |
|---|---|
| Shared and local files absent | `unconfigured`; no command export and no legacy fallback for this bound operation |
| Either selected file unreadable/invalid, including an invalid shared declaration | `invalid_config`; no receiver and no alternate destination |
| Valid local `{"schema_version":1,"mode":"disabled"}` | `disabled`, even if shared policy disallows local replacement |
| Shared mode disabled and no valid local disable | `disabled`; a local command cannot enable it |
| Shared command; local file absent | Use shared command |
| Shared command with `allow_local_override: true`; valid local command | Replace the whole command selection with the local declaration |
| Local command without a shared command or without override permission | `override_refused`; no command or legacy fallback |

A present malformed local file refuses the route; do not silently ignore it.
Validate the shared file before considering an override, except that a valid
local disable can always suppress export without executing anything.
There is no partial merge of argv or policy. This is a fixed portable default;
an adopter can impose stricter admission outside the parser, not weaken it
with arbitrary configuration keys.

Configuration is snapshotted at ordinary owner initialization/admission, off
the emission path. A changed declaration takes effect only at a new admitted
route, not by scanning files on every event. No file watcher is required.
Imports and disabled/unconfigured emission start no worker, subprocess, scan
or queue. An owner that already resolved an unconfigured state reuses it
until its normal explicit reconfiguration boundary.

## 3. Origin, trust and route identity

The owner supplies an immutable admitted binding:
**canonical harness root + effective selection + route generation**.
The canonical root is the receiver's working directory for its whole lifetime.
Resolve relative executable/script paths against that root, not the current
product checkout, library location or a daemon's startup directory. Resolve a
bare executable once through the owner's admitted OS `PATH`, off-path; retain
the selected executable for the route. No shell expansion, command-string
evaluation, path interpolation or implicit `.ps1`/batch association occurs.
Scripts name an explicit native interpreter in argv; arguments remain literal.

The shared admission owner, not telemetry, decides that a caller may select
this root and run its command. Reject a root received as an arbitrary
unauthenticated request field. Never copy a sender's executable path to a
remote host and execute it as configuration. An admitted remote operation uses
the receiving owner's explicitly approved local binding; absent agreement,
export is unavailable, not redirected.

An admitted selection receives a random UUID `route_generation`; a new
selection or explicit reconfiguration receives a new value. The ID is opaque
and contains no path, account or host. Configuration paths and argv remain local
diagnostic data, never record attributes. Queue items retain their original
route; reconfiguration cannot send an old item to the new receiver.

A shared daemon holds bindings per admitted operation, not in a last-caller
global variable. Deferred activity retains its admitted binding within that
operation's lifetime. Service-only activity needs an explicit service-owned
binding; missing context does not borrow a caller's session or harness.
Unbound legacy mode is an explicit owner state, not what a failed binding
lookup returns. Unknown/missing admission suppresses optional export.

Launch with inherited OS identity, no elevation, no terminal/window, stdin
piped, stdout/stderr discarded at the OS handle boundary, and no inherited
unrelated handles. No receiver output reaches normal product output.
The environment contains only the owner's OS launch prerequisites:
`PATH`, `SystemRoot`, `WINDIR`, `COMSPEC`, `PATHEXT`, `TEMP`, `TMP`, `HOME`,
`USERPROFILE`, `LANG` and `LC_ALL` when present. Do not copy tokens, arbitrary
environment, trace baggage or adopter credentials into it. Receiver-owned
credential/dependency discovery remains an explicitly trusted receiver concern.
No telemetry package installation, daemon start or dependency bootstrap occurs.

## 4. Framing and record schema

Transport **`command-jsonl-v1`** writes one compact UTF-8 JSON object plus
one LF byte per record to stdin, without BOM or pretty-printing. Partial pipe
writes are completed within the same record deadline or the route fails;
never interleave records. Receivers must buffer partial reads until LF.
EOF ends the stream; an incomplete last line is loss/unknown, not acceptance.
The stream is not OTLP. There is no greeting, reply, ACK channel or endpoint
discovery in v1.

Required envelope fields, with no others in v1:

| Field | Type and meaning |
|---|---|
| `contract` | Literal `consumer-telemetry-command` |
| `version` | Integer `1` |
| `source` | Registered producer name: `agent-bridge`, `agent-dispatch`, `agent-worktrees`, `agent-codespaces` or `agent-containers` |
| `source_schema` | Paired with that producer by the shared registry: `bridge.lifecycle.v1`; `dispatch.lifecycle.v1`; `worktrees.lifecycle.v1` or `hook.lifecycle.v1`; `codespaces.lifecycle.v1`; `containers.lifecycle.v1` |
| `source_instance` | Random UUID for this producer lifetime; not a native session, user or machine ID |
| `route_generation` | Admitted route UUID from section 3 |
| `sequence` | Positive integer, at most `2^53-1`, increasing per source instance before queue admission; gaps may indicate loss; stop this source on exhaustion |
| `observed_at` | UTC RFC3339 timestamp with milliseconds, sampled at the source offer, not receiver persistence time |
| `event` | Source-owned, privacy-projected JSON object below |

Example (synthetic identities):

```json
{"contract":"consumer-telemetry-command","version":1,"source":"agent-bridge","source_schema":"bridge.lifecycle.v1","source_instance":"00000000-0000-4000-8000-000000000001","route_generation":"00000000-0000-4000-8000-000000000002","sequence":1,"observed_at":"2026-01-01T00:00:00.000Z","event":{"kind":"state_transition","name":"session","event":"session_state_changed","from":"idle","to":"sending"}}
```

### First source projection: `bridge.lifecycle.v1`

This is a transport projection, **not a rewrite of the reducer or existing
factory/spool schema**. Preserve supplied primitive values for admitted fields;
do not stringify objects, run `repr`, or add missing source identities.

* Required strings: `kind` (`state_transition`, `event`, `error`), `name`
  (`session`, `session_health`, `conversation`, `tool_call`, `event_log`),
  and `event` from this closed list: `session_state_changed`, `connect_failed`,
  `error`, `context_warning`, `context_critical`, `user_message`,
  `agent_message`, `agent_thought`, `turn_complete`, `tool_call`,
  `tool_call_start`, `tool_call_update`, `tool_call_progress`, `log_rebuilt`.
* Optional strings: `from`, `to`, `trigger`, `tool_status`, `stop_reason`,
  `session_state`, `conversation_state`. State fields admit only `idle`,
  `sending`, `responding`, `running`, `end-turn`, `completed`, `cancelled`,
  `error`, `stopped`, `ended`, `failed`, `starting`, `connecting`, `pending`.
  `tool_status` admits only `completed`, `cancelled`, `error`.
  `trigger` admits `daemon_restart`, `handoff`, `interrupt`, `resume`,
  `resync`, `other`. `stop_reason` admits `end_turn`, `max_tokens`,
  `max_turn_requests`, `refusal`, `cancelled`, `canceled`, `interrupted`,
  `other`. For `event_log`, both `from` and `to` are log epochs, not states;
  omit both before validating the remaining fields.
* Optional nonnegative integer: `event_id`, `event_count`. Preserve original
  cursor values up to `2^53-1`; never use a timestamp as a duplicate key.
* Health fields: finite numeric `context_pct` and `threshold` in 0–100,
  Boolean `retryable`. No arbitrary health `stage_name` or tool-kind string.
* Omit native `session_id`, `acp_session_id`, `worktree_id`, `tool_call_id`,
  `log_epoch`, source routing targets and other identity-bearing fields.
  Their presence in a legacy reducer is not shared-export permission.
* Unknown record families, unknown enum values and invalid admitted values
  drop the record with accounting. Unknown fields in a known reducer record
  are omitted, not passed through. Missing or null optional fields stay absent;
  this includes empty-rebuild state summaries. Omission precedes type/enum
  validation; a non-null invalid admitted value still drops the record.

The reducer can currently forward other session status strings. This narrower
projection deliberately drops those records rather than export arbitrary text;
U1 must cover that distinction in focused adapter tests. Additional source families, richer identity and QoS mapping need their own reviewed
source-schema capability; they cannot hide under an extensible `attributes`
bag in this contract. Shared attribution requires the vision's separately
admitted keyed/privacy boundary before serialization. No raw fallback is
permitted when that optional capability is absent.

### Second source projection: `dispatch.lifecycle.v1`

This is an independent, closed projection of Dispatch's existing
`task_lifecycle_event` and `producer_fence_event`, not a rewrite of their
factory/spool records. It uses source `agent-dispatch`; a provider or receiver
supporting only `bridge.lifecycle.v1` does not thereby support this schema.

All records require `kind: "state_transition"`, `name` equal to `task` or
`producer_fence`, and one event from the corresponding closed list:

* `task`: `task.proposed`, `task.created`, `task.approved`, `task.claimed`,
  `task.started`, `task.yielded`, `task.suspended`, `task.resumed`,
  `task.released`, `task.held`, `task.unheld`, `task.unexcluded`, `task.reset`,
  `task.submitted`, `task.completed`, `task.reopened`, `task.abandoned`,
  `task.detached`, `task.recovered`, `task.result_recorded`, `task.rejected`,
  `task.owner_session_bound`,
  `task.progress`, `task.card`, `task.steer`, `task.card_draft`,
  `task.card_draft_cleared`, `task.steer_taken`.
* `producer_fence`: `task.create_rejected`, `producer.claim_rejected`,
  `producer_scope.transitioned`, `producer_scope.transition_rejected`.

Optional task fields are `to` and `attempts`. `to` admits only `proposed`,
`queued`, `claimed`, `started`, `suspended`, `submitted`, `completed`,
`abandoned`, `dead_letter` (the last is retained-read compatibility).
`attempts` is an actual source count, not a fabricated attempt ID.
Task card/progress/steering observations retain their existing reducer's
structural event meaning; the projection contains no card or message content.
Periodic heartbeats and activity updates are not lifecycle observations and
remain excluded by the existing producer.

Optional fence fields are nonnegative integers `requested_generation`,
`current_generation`, `from_generation`, `to_generation`, `blocking_task_count`;
and Booleans `retryable`, `replayed`, `blocking_ids_truncated`.
All integers admit at most `2^53-1`; Booleans are not integers.
`replayed` is the producer's actual fence replay flag, not a sink retry.

No other fields are exported. Omit null/absent optional fields, native task,
request, producer, owner, worker, machine and session IDs, repo/source/routing
strings, raw reason/code text, labels, fingerprints, lists of blocked task IDs,
titles, prompts, payloads, result/card/steering content and arbitrary context.
Unknown fields are omitted **before** bounded snapshotting. Unknown record
families/events and invalid admitted primitive values drop the record with
accounting. No trace/span identity or missing operation key is synthesized.
There is no cross-record deduplication: source instance/sequence identifies an
observation; explicit replay and attempt values keep their original meaning.

Ordinary HTTP/MCP mutations and the existing relevance/
verification publishers retain one coordinator-owned admission. Stored task
repository selects its approved initiating root; taskless/rejected fence events
use only the explicit service root. Missing admission never borrows a last
caller or falls back to the legacy sink. The Dispatch reference owns the
bounded local service-policy file and lifecycle wiring. Telemetry does not
require an API bearer token; existing API authentication, task mutation
authorization and network-bind policy remain unchanged.

The envelope identifies a transport observation, not an operation attempt.
`source_instance` + `sequence` remains unchanged if a receiver deliberately
replays a captured line. A new source observation has a new sequence; supplied
source cursors retain their meaning but need not uniquely identify multiple
reducer transitions from one native event. No automatic replay/retry occurs.
There is no invented trace/span/parent context. A future real tracing schema
must preserve actual trace context, not derive it from these IDs.

### Finite-owner stage projections

`process_telemetry.stage_projection` owns three closed **stage** schemas for
short-lived tool owners: `worktrees.lifecycle.v1` (source `agent-worktrees`),
`codespaces.lifecycle.v1` (`agent-codespaces`) and `containers.lifecycle.v1`
(`agent-containers`). The shared registry pairs each schema with exactly one
source; a route configured for one never accepts another's records. Each
provider adopts its schema in its own reviewed PR with its own descriptor.

A stage record requires `kind: "stage"`, `stage`, `outcome` (`success`,
`failure`, `timeout`, `retry`) and `reason`. `reason` is `none` exactly when
the outcome is `success`, except that `force_claimed` may accompany a
successful claim. Optional bounded integers are `duration_ms` (monotonic,
`0..2^53-1`), `attempt` (`1..65536`, the source's actual attempt count) and
`limit_ms` (the configured limit a `timeout` was measured against).

| Schema | Admitted `stage` values |
|---|---|
| `worktrees.lifecycle.v1` | `account_resolution`, `claim`, `lease`, `worktree_create`, `session_bind` |
| `codespaces.lifecycle.v1` | `launch`, `seed_validation`, `plugin_preparation`, `credential_relay`, `bridge_payload`, `remote_embody`, `claim`, `account_resolution`, `bridge_registration`, `codespace_lookup`, `codespace_connect`, `ssh` |
| `containers.lifecycle.v1` | the CodeSpace venue stages except the last three, plus `container_lookup`, `container_start`, `container_connect`, `exec` |

Reasons common to every stage schema are `none`, `interrupted`, `other`,
`claim_busy`, `lease_conflict`, `force_claimed`, `snapshot_timeout` and
`account_unresolved`. The venue schemas add `seed_too_long`, `seed_invalid`,
`marketplace_add_failed`, `plugin_install_failed`, `clone_auth_failed`,
`relay_not_listening`, `credential_fill_failed`, `payload_install_failed`,
`installer_unavailable`, `bootstrap_python_unavailable`, `listing_failed`,
`not_found`, `connect_failed`, `transport_retry`, `transport_failed`,
`session_unregistered` and `session_unrepresented`; containers additionally
admit `engine_unavailable`, `image_unavailable`, `not_running` and
`start_failed`. A lookup reports the listing failure itself
(`listing_failed`), never a masking `not_found`.

`worktrees.lifecycle.v1` also admits PR-activity records: `kind: "pr"`,
`action` (`create`, `push`, `label`, `merge`, `abandon`), `outcome`
(no `retry`), `reason` (`none`, `interrupted`, `other`, `auth_failed`,
`provider_rejected`, `conflict`, `checks_pending`, `review_required`,
`gate_refused`, `nothing_to_publish`, `unavailable`), optional
`label_result` (`applied`, `already_present`, `repaired`, `not_requested`,
`unsupported`, `failed`) and `duration_ms`.

No stage or PR record carries names, paths, repositories, PR numbers, URLs,
branches, accounts, hosts, command lines, seeds or error text. Unknown fields
are omitted before snapshotting; unknown enum values, inconsistent
outcome/reason pairs and out-of-range integers drop the record with accounting.

### Hook projection: `hook.lifecycle.v1`

`hook.lifecycle.v1` describes `sessionStart` hook entries. A record requires
`kind: "hook"`, `plugin` (`agent-worktrees`, `budget-guidance`,
`agent-vault`, `context-handoff`, `agent-pull-requests`), `entry`
(`sessionStart`), `outcome` (`success`, `failure`, `timeout`, `unfinished`)
and `failure_category` (`none`, `nonzero_exit`, `timeout`, `exception`,
`unavailable_runtime`, `unfinished_prior_run`, `other`). `none` pairs exactly
with `success` and `unfinished_prior_run` exactly with `unfinished`.
Optional fields are `exit_code` (int32), `duration_ms`, `run_sequence`
(a best-effort, per-plugin local counter starting at 1, not a unique ID) and
`started_at` (UTC RFC3339 with milliseconds).

Hooks are latency-sensitive callbacks and never launch a receiver (section 5).
`process_telemetry.hook_journal` writes a start marker before any product
work and replaces it with a completion record on return. The runtime may stop
a timed-out hook before it reports, so the next run converts a marker older
than its stale threshold into one `unfinished` record. Every transition --
the start's capacity check and marker publish, completion, recovery and
drain -- holds one advisory journal lock. A run commits by writing its final
record into its own marker before appending it, then removes the marker; a
holder stopped before the append leaves a committed marker that the next start
reports with its real outcome, and one stopped after the append leaves a
marker that recovery or the next drain matches to the already-pending record,
so each run is recorded at most once. Delivery is at most once too: `drain`
removes the records it returns, so a drainer that then fails to offer them
loses them rather than risk a duplicate. Pending records, live markers, abandoned scratch files
and per-start recovery work are all bounded; a start that would exceed a
bound goes unobserved rather than grow storage. Bounds apply to the whole
shared journal: markers are capped across every plugin and entry, only the
admitted plugin/entry vocabulary creates files, any start recovers every
admitted entry's stalled markers, and each start and drain reclaims scratch
(written only under the lock), so total occupancy fits the one complete scan
each start and drain performs. The journal is content-free;
a separate finite owner of the registered
source later drains it into its own admitted route. Only the executing
provider's adopted hooks write the journal; listing a plugin in the enum is
not evidence that its hook is instrumented.

### Finite-owner admission

A finite tool owner (`process_telemetry.finite_owner`) reads exactly one
owner-local policy file, `consumer-telemetry.json` beside the installed
runtime: `{"schema_version":1,"root":"<absolute path>"}`. Its absence keeps
legacy behavior; any other shape refuses the optional route. The admitted root
then selects its command through section 2 unchanged. The owner never infers a
root from the working directory, environment or a repository argument, and
closes within the section 5 shutdown budget before the command exits.

## 5. Process, queue and time bounds

These are v1 **hard upper limits**, not assertions about today's library or
configurable promises to deliver every record. Adopters cannot increase them.
U1 must reuse/extend the existing bounded writer, not silently change legacy
synchronous sinks or use an unbounded blocking stdin writer.

| Resource/stage | Bound |
|---|---|
| Source offer | Existing bounded primitive snapshot and nonblocking offer only; no JSON encoding, filesystem, process launch, network, blocking lock, pipe I/O or join |
| Input snapshot | Existing copier: depth 3, 64 dictionary entries, 32 list entries, 512 characters/string, finite primitive values, 4,096-byte conservative budget |
| Serialized envelope | At most 8,192 bytes including LF; oversized records drop, never truncate into invalid JSON |
| Pending storage per producer | One existing `BufferedSink`: 128 queued records plus at most one 32-record drain batch; no second transport queue or unbounded encoded backlog |
| Concurrent routes per producer | At most 4 admitted route generations, including generations still draining/closing |
| Receiver lifetime | At most one child per admitted route generation, reused until source/route close; no child or worker per record |
| Receiver startup | 2,000 ms from off-path launch attempt to owned process/pipe establishment; not a readiness or persistence claim |
| Record write | 1,000 ms aggregate monotonic deadline including partial writes; timeout fails the route, not just that line |
| Retry/restart | Zero automatic retries or replacements for a failed generation; a new explicit admission may try a new generation only after prior owned cleanup |
| Source shutdown | Stop offers immediately; at most 250 ms drain, then stdin close, 250 ms graceful exit, then forced owned-tree termination/verification within a further 1,500 ms; total 2,000 ms |

Encode/write on the existing off-path writer. Reuse its maintenance iteration
for deadline/child checks. The OS process owner supplies cancelable pipe and
spawn ownership; do not emulate a deadline by abandoning a blocked thread and
starting another. Bounds cover startup races and source cancellation too.
On shutdown the aggregate deadline takes precedence over startup/write
deadlines and the remainder of a drain batch; do not finish 32 serial write
timeouts before closing. Do not change `BufferedSink.close()`'s existing
nonflushing contract: any optional command drain is an explicit off-path owner
operation before close, with the same aggregate deadline.
One failing route must not grow work for the other routes; its lost records
are accounted and never retargeted.

Use the existing native process containment owner: exact child handles and
Windows Job custody, or POSIX owned descendant scope and parent-death cleanup.
The receiver cannot detach or leave background descendants. Parent loss must
close/terminate that scope without a new telemetry reaper service. If the
platform cannot establish that ownership, refuse the optional route before
writing data. Do not kill processes by name or by a stale/reused PID.
Unconfirmed termination retains its exact custody and prevents replacement;
ordinary product exit/status must not wait indefinitely for it.

Routes reuse a receiver lazily on the existing worker's first record; short
hooks do not bootstrap it. Owner initialization occurs only in a normal
long-lived producer or finite tool owner with the shutdown budget above.
Do not add receiver launch to a latency-sensitive callback. Producer wall/
CPU overhead remains measured qualification, not inferred from these bounds.

## 6. Failure, outcomes and legacy compatibility

Product success, exit code, ordinary stdout/stderr and operational authority
are independent of telemetry. Fail open for optional export, **not** by
selecting another sink. Provide bounded local status through the existing
owner diagnostics, not a new telemetry-recursive logger or status service.
Never include argv, paths, record contents or exception text in default status.

Selection states are `unconfigured`, `disabled`, `active`, `invalid_config`,
`override_refused`, `unsupported_contract`, `origin_unavailable`,
`route_limit`, `start_failed`, `write_failed`, `deadline_exceeded`, `closed`
and `cleanup_unconfirmed`. Cache state per generation. Count offered,
enqueued, dropped-invalid, dropped-full/contention, dropped-route,
serialization failures, complete pipe writes, write failures and abandoned
records separately. Snapshots need not be transactionally consistent.
Expose saturation/unavailable counters honestly; do not report unknown as zero.

`BufferedSink.delivered` continues to mean **callback returned**, not receiver
read, disk persistence or backend acceptance. A complete pipe write means only
bytes entered the pipe. Receiver readback proves receipt; receiver-owned local
readback can prove its chosen persistence boundary; a backend acknowledgement
is a different outcome. V1 reports neither persistence nor backend success.

| Consumer state | Required behavior |
|---|---|
| Explicitly unbound legacy caller, old or new runtime | Existing factory/config/environment/spool selection and public callable APIs keep their meaning |
| Bound caller, absent/disabled v1 selection | No export for that operation; do not inherit a daemon-global legacy sink |
| Bound caller, invalid/unsupported selected v1 policy | Suppress optional export, name the state locally; no factory/spool/HTTP fallback |
| Legacy factory or spool during provider expansion | No config rewrite, automatic fan-out, automatic buffering, retention or destination change |
| Explicit retained spool | Preserve its existing namespace/custody and synchronous-vs-buffered choice; command selection does not convert it |

The existing `SinkSlot` is process-scoped, not already an origin router.
U1 must integrate binding-aware selection behind that seam and distinguish
bound/legacy records internally before enabling command adoption. It must not
reconfigure a global sink on each request. Preserve the explicitly unbound
legacy route alongside bound routes without leaking either stream to the
other. Existing library tests are required controls, not evidence that this
new routing behavior already exists.

## 7. Capability and supported skew

Provider support is distinct from config schema, source schema and package
version. U1 introduces an additive, read-only descriptor named
`consumer-telemetry-contract.json` in the **selected immutable runtime's
metadata root**:

```json
{
  "schema_version": 1,
  "contract": "consumer-telemetry-command",
  "versions": [1],
  "transports": ["command-jsonl-v1"],
  "source_schemas": ["bridge.lifecycle.v1"]
}
```

The existing runtime selector supplies that exact root; do not search `PATH`,
inspect a newer payload or execute a new capability verb on an old provider.
Read at most 4,096 bytes, reject duplicate fields/invalid types or unsupported
schema major, and ignore additive descriptor fields only. Absence means
unknown support, not latest support. Cache only for that selected generation.
Ship the descriptor only when that provider's actual entry/admission path
implements its advertised source schema; library presence alone is not enough.

A resident Bridge or Dispatch must additionally advertise its own optional object in
its existing running discovery/handshake response. Installed metadata is not
proof that a resident old daemon is capable. U1 must use a backward-compatible
existing response seam; if its old-reader contract forbids this addition,
expand that provider seam before migrating callers. Do not send new effectful
syntax to discover support and then replay an old invocation.

Provider-first phases, with support for legacy callers throughout v1:

1. **Expand (U1):** implement parser, route ownership, Bridge adapter and
   descriptor/live advertisement together; retain legacy behavior.
   U2 independently expands Dispatch with only `dispatch.lifecycle.v1` in its
   own descriptor and `/health` object. A shared-library adapter registry is
   not a union of every provider's supported schemas.
2. **Migrate:** enable bound selection only when the executing provider
   advertises v1 and the requested source schema. Unknown/incapable provider
   yields `unsupported_contract` locally; ordinary work still runs. Do not
   forward a bound operation to an old global export sink. If an old provider
   cannot isolate its optional legacy export, suppress that optional export
   through its supported owner path or refuse only telemetry enrollment.
3. **Rollback:** preserve config without rewriting it. A downgraded provider
   is explicitly unsupported for selected command export. Older callers
   still use the unchanged legacy baseline against new providers.
4. **Contract:** no legacy API/config removal in U0–U4. Any later retirement
   needs a separately reviewed upgrade **and provider-rollback** window.

No receiver capability subprocess is needed: the trusted declaration opts
into stdin v1. A receiver must reject unsupported contract/version/source
schema and invalid records rather than guessing their meaning. New envelope
fields or changed meanings require a new negotiated contract major; an
additional source schema is opt-in in the provider descriptor.

## 8. Finite developer acceptance contract

U0 is documentation only. Runtime, native, fresh-machine, receiver delivery
and release acceptance remain **unexecuted**, not implicit passes. U1 cannot
exit on direct helper calls or a mock sink alone.

| Lane/owner | Required finite witnesses |
|---|---|
| U0 documentation checks | Resolve changed local links, parse synthetic JSON examples, check configuration/framing/budget/compatibility consistency, preserve reserved source diff; normal current-head review and required CI |
| U1 focused shared/Bridge unit packet | Table-driven precedence, invalid/duplicate/oversized config, literal argv, snapshot bounds, framing/Unicode/partial writes, populated/empty rebuild projection, missing origin, disabled no-op work counts, old factory/spool controls, route/queue cap and loss accounting |
| U1 real consumer boundary | In an isolated fixture root, use normal Bridge owner config/admission and an actual existing lifecycle transition; independent stdlib-only receiver reads stdin and writes fixture-owned evidence; read it back and match schema, cwd, source and sequence; importing an adopter package into Bridge is forbidden |
| U2 coverage | Dispatch through the same parser/runner; two interleaved admitted origins, explicit service-only binding, deferred work and unbound legacy controls; inventory every actual shared-seam consumer as adopted, inapplicable or tracked |
| U3 selected native Windows/Linux | Literal paths with spaces, exact environment/handle isolation, one reused child, crash/nonreader/large-output controls, full queue, partial/broken pipe, timeout/cancel/parent-loss, descendant cleanup and finite exit drain |
| U3 skew/rollback | New caller/old provider, old caller/new provider, new/new, missing/malformed descriptor, old running daemon/new installed slot and rollback; no wrong-harness or legacy fallback |
| U3 fresh-repository qualification | No telemetry daemon, adopter package, previous config cache or receiver process; ordinary configured command dependencies only; record readback, losses, workload and actual overhead |
| U4 release handoff | Merged reviewed source, required changefiles for every touched/vendoring owner, consumable release with actual capability; installation/rollout belongs to a separately authorized owner |

Keep required CI change-scoped and unit-only with its current coverage policy.
Real subprocess/native/fresh-repository witnesses use explicitly selected,
contained integration lanes under [TESTING.md](../../TESTING.md), not an
unrestricted model-agent evaluation or a live install. Fixtures use synthetic
records, private process custody and owned roots; no real backend, credentials
or shared service cleanup. Measure cold/warm source-offer and end-to-receiver
cost for the actual workload; do not replace unavailable measurements with
zero or claim that a deadline is a performance result.
