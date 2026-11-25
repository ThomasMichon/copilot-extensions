# Retained Worktrees writers

Back to [local session acquisition](session-acquisition.md).

The optional `retained_acquisition_worktree_writer: 1` health capability
supports **ACP acquisition request version 3**. It is an independently
negotiated extension within HTTP generation 52, not a new global HTTP floor.
The client uses the existing per-attempt protocol/capability fence before
posting, including after daemon cutover. Request versions 1 and 2 retain
their existing syntax and behavior; an older provider cannot accept version
3 while silently ignoring its required fields.

Each fenced attempt pins its health check and mutation to the same endpoint,
even when the startup event observer concurrently follows a replacement daemon.
A safely retryable refusal starts a new attempt with a fresh qualification;
an ambiguously delivered mutation is not replayed.

Version 3 requires the existing exact `project` and `worktree_path`, plus
`worktree_writer`:

| Field | Meaning |
|---|---|
| `version` | Requirement version 1 |
| `identity.installation_root` | Expected selected Worktrees installation |
| `identity.runtime_generation` | Expected birth writer's 32-hex build identity, retained afterward as provenance |
| `creation_nonce` | Original workspace incarnation, checked against exact owner reads |

This object rejects unknown fields. It accepts no caller command, interpreter,
environment or successful-qualification assertion. Other request versions
cannot carry it. CLI mode with this requirement refuses before owner/native
effects; ordinary unpinned CLI acquisition remains supported.

## Selection and recovery

The provider resolves its own existing Worktrees selection and runs
`resource-relationship capabilities` through that exact command. The response
must identify a compatible executing serializer and its retained-relationship
and creation-nonce preservation contract, plus `retained_session_writes: 1`.
Selected-root metadata alone does not qualify it. The version-1 adapter requires
`copilot-extensions.worktree-record-writer` version 1, retained relationships
version 1 and creation-nonce version 1, together with executing-process identity
and expected-identity enforcement. A newer runtime generation in the original
installation can satisfy that contract; another installation cannot.
An initial request without a retained binding still requires its observed birth
identity before committing the new journal. Compatible adoption applies only
after a qualified original binding exists; it cannot invent the guarantees of
an unqualified earlier writer.

The original request and provider-selected command commit together in the
existing delivery-keyed acquisition journal before launch. The command remains
part of that journal's existing effect history through launch and delivery.
A new frontend or concurrent retry preserves those original facts, but selects
and qualifies the installation's current compatible executor. The historical
command is validated as journal data, never executed as a recovery fallback.
Later same-task acquisition retains the same original requirement; an
unqualified historical acquisition cannot be retrofitted. Missing or conflicting
recovery facts refuse with the existing custody intact.
Nested continuation keeps the outer operation's qualified executor even when
it reads an earlier delivery's request. Each delivery retains the inherited
birth command, not the newer executor, so a later delivery does not silently
rewrite provenance or strand a prior reader after rollback.

Acquisition reads, delegated registration and binding use the retained owner.
Controller qualification uses that same command for a fixed cached inventory
read, independently of the execution target's write scope. It retains the
controller's owner, session, project, machine and canonical-path checks; missing
or conflicting evidence still refuses. This observation does not release or
broaden the retained target scope: registration, binding, succession and other
target operations remain confined to the original workspace.
The native target stores only a pointer to the original acquisition journal.
Later native resume and handoff recover the original contract, qualify a current
compatible executor, check
the original workspace, and use it for registration, succession and lineage
reads. Required lineage errors are not silently treated as legacy best-effort
success. Unpinned operations keep their existing fail-open behavior.

The historical `acquisition:` caller-affinity prefix is also used by keyed
workspace creation and singleton initialization. Without an explicit writer
pointer or a matching acquisition attempt, it does not select this journal:
those continuations retain their existing lifecycle owner. An explicit writer
pointer still requires its original journal, and a matching attempt with
missing or malformed effects refuses rather than becoming an unpinned call.

Registration, binding and succession carry the original workspace incarnation
and this invocation's qualified executing identity through
Worktrees' [qualified executing-session option](../../agent-worktrees/docs/resource-relationship.md#qualified-executing-session-writes).
Those commands invoke their existing registered handlers in the qualified CLI,
not a resident selected only by ordinary verb versions. The actual handler
checks the executing build and locked workspace incarnation before mutation.
This operation-scoped path neither disables nor qualifies the resident globally.
An older owner without the declaration refuses before native effects; the
requirement is never dropped on retry.

Provider refresh treats that pointer as session-owned rather than provider
metadata and preserves it across stopped-session recovery. The refreshed target
must still be local and match the original workspace before it is persisted
or launched. Required resume never falls back to creating another conversation,
even when a caller requests recreation.

Required handoffs retain the original successor, native identity, opening seed and progress in
the same acquisition journal before launch. Post-spawn lineage failures retry
against that successor, not a new conversation. An unconfirmed start with no
recoverable holder, changed inputs/native identity or unknown seed outcome
refuses with the retained handoff intact. A completed retry returns the same
successor without reseeding.

A registration failure after native creation leaves that same conversation
stopped and recoverable. Resume retries registration before reporting success
or linking a successor, including when it reattaches to a surviving host.
While registration is pending, background recovery remains durably disabled.
Failure detaches and clears the frontend client without reaping its host/native,
so a normal send cannot bypass guarded registration recovery. Startup leaves
that dormant session stopped; successful guarded registration re-enables its
ordinary background recovery.

## Build-bound record adapter

The strict version-1 requirement and saved command remain immutable provenance
for supported acquisition and keyed-creation records. They no longer require
the original executable to remain available. The adapter uses the existing
installed-owner resolver, never the saved interpreter or ambient `PATH`, and
negotiates the declared version-1 semantics above before effects. The selected
command and executing identity are operation-scoped; delegated session writes
still check that actual identity and the locked original workspace incarnation.
An unsupported contract, foreign installation or changed incarnation refuses;
build inequality alone does not.

No persisted adoption rewrites request digests, creation facts, handoff history
or original operations. This keeps concurrent retries and prior Bridge readers
on the same original journal. A prior Bridge implementation can still interpret
the unchanged record under its own stricter execution rules; an unavailable old
slot refuses rather than substituting a new conversation or resending a prompt.
This operation-level limitation is not an installer replacement veto. A writer
rollback must still advertise the required semantics before new code uses it.

Read-only acquisition and keyed-creation writer observations keep the original
`worktree_writer` and separately report `executing_writer_identity`. Observing
either identity neither adopts native execution nor settles an unknown send.
Dispatch's ordinary admission validates birth provenance and the qualified
executing identity separately. Both must belong to the expected installation
and preserve the original incarnation, with the same executing-process and
versioned preservation guarantees. An older observation without the additive
executing identity retains its original exact-identity requirement; missing
new evidence cannot imply compatible adoption.

## Ordinary keyed creation

The optional `retained_workspace_creation_writer: 1` capability accepts the
same strict `worktree_writer` requirement on the existing
`POST /api/v1/sessions/workspace-creations` request. It requires a creation key
and an already-prepared explicit local ACP worktree; provider-instance,
remote, CLI and allocate-new forms refuse before effects. The client
negotiates the named capability on every transport attempt within HTTP 52.
Unpinned requests retain their existing behavior.

The ordinary CLI carries this requirement with
`create <target> --creation-key <key> --worktree-id <id> --worktree-writer-file <path>`.
The UTF-8 file contains the strict requirement object above; `-` reads it from
stdin. Requirement stdin cannot also supply the prompt. Invalid JSON, unsupported
creation forms and conflicting stdin refuse before connecting to the daemon.
The existing client negotiates the capability on each attempt and never retries
without the requirement. Omitting the option preserves ordinary unpinned behavior.
This transport does not independently admit the writer or close its cohort.
The executing CLI advertises `agent-bridge.create` version 1 with
`keyed-worktree-writer` through its existing `version --contracts-json` response.
Required callers select that support before using the option; absent or invalid
metadata does not establish support, including after a CLI rollback.

This route does **not** create an acquisition attempt. Its original
`workspace_embodiments` row atomically retains the creation facts, workspace
incarnation, requirement and provider-selected writer provenance before native
launch. Required replay uses the same contract adapter and exact workspace.
A different key cannot capture the same retained workspace;
changed inputs, dropped requirements and retrofitting an unpinned row refuse.
Schema 41 prepares the nullable columns and immutable original-row/native
fences through the ordinary writable migration. Passive and existing-only
adoption validate rather than prepare them.

The session carries a pointer to that original creation row. Registration,
binding, strict native resume, provider refresh and succession recover that
owner instead of interpreting its historical `acquisition:` caller prefix as
an acquisition journal. The native identity is recorded before registration;
a registration failure retains that native stopped and dormant until guarded
recovery succeeds. The same row retains each handoff's original successor,
native and seed progress. An unconfirmed seed is not resent and no retry
creates a replacement conversation.

Exact-session resume can additionally carry that same `worktree_writer` in an
optional body on `POST /api/v1/sessions/{id}/resume`. The client requires
`retained_workspace_creation_writer_resume: 1` before **every** attempt,
including after a refused connection or draining-provider cutover. The older
creation capability alone cannot establish enforcement of this new body.
The receiver matches the exact owned session and original keyed journal,
requirement, workspace and retained native before resuming with that writer;
aliases, missing journals and changed requirements refuse before launch.
Unqualified body-less resume keeps its existing behavior.

The CLI carries it through
`resume <exact-session-id> --strict --worktree-writer-file <path>` (`-` reads
stdin). CLI mode, reclaim and `--worktree-id` combinations refuse before
discovery. A missing exact session never falls back to a worktree or singleton,
and an unproven response is not replayed. The existing
`agent-bridge.resume` version-1 executing-CLI descriptor advertises
`keyed-worktree-writer`; daemon support remains a separate per-attempt check.

### Once-only retained native recovery

The optional `retained_native_session_recovery: 1` capability extends that
existing exact-session resume body with `retained_recovery`. It contains
version `1`, an opaque `operation_id`, the original 64-hex `journal_key`,
`native_session_id`, positive integer `head_revision` and the original
`bridge_installation`. The original `worktree_writer` remains required.
`resume <exact-id> --strict --retained-recovery-file <path>` accepts that full
body; `-` reads one bounded stdin document. The executing resume/status CLI
contracts separately advertise `retained-native-recovery`.

The receiver requalifies its original keyed writer and installation, requires
a stopped local session and owner-confirmed native loss, then writes the
operation before one exact native load. Worktrees must advertise
`retained_native_resume: 1` and `retained_hosted_activation: 1` from the actual
selected executor. The native-resume fence enforces the original workspace
incarnation and head revision. Missing hosted-activation support or an
unqualified local Linux/WSL execution epoch refuses before Host creation;
an older owner does not silently receive an ordinary unfenced resume.

The original RPC owner records `retained_native_load_accepted` in the same
operation-tagged event journal immediately after `session/load` acknowledges.
It binds the original recovery request and selected writer to the actual local
Host transport, process births and execution epoch, then flushes before
returning to preference application or registration. The association stores a
nonce digest, not the Host's attach credential. A timeout without an
acknowledgement never manufactures this event. A stop arriving during the RPC
does not erase an acknowledgement, grant permission or complete registration.
The additive configuration snapshot retains the load response's advertised
options and original preference intent, including resolved caller settings.
It is not proof that subsequent configuration succeeded.
The recovery row's request/state schema remains unchanged, so an older reader
preserves unknown custody and rejects accepted-load evidence as a no-load
failure.

After a successful load, registration rechecks that acknowledgement against
the current original operation, executing writer, Host transport, process
births and epoch. It sends the recorded child PID and explicit
`--retained-hosted-activation` through Worktrees' qualified registration
command. It neither creates nor requires a CLI hold for this hosted source.
A missing acknowledgement, changed association or unconfirmed write retains
the original recovery gate instead of reporting success. Ordinary CLI-native
registration still uses its existing hold witness.

Original-operation status reconciliation can finish a load interrupted before
registration. It requires the complete operation-tagged acknowledgement history,
the original local Host, unchanged process births/epoch and native head, a
compatible current writer and an unassociated stopped session. It claims the
Host through its existing generation owner and authenticates a current idle
child separately from the historical load acknowledgement. Reattachment only
initializes the transport and adopts that same conversation: no new Host,
native load, new conversation, prompt or queue drain.

For a durable acknowledgement, missing terminal progress or a later failed
configuration phase does not negate the accepted load. If configuration did
not finish, reconciliation requires its original response snapshot and applies
that original intent through the reattached client's existing configuration
owner. Target-settings mode forces fresh configuration readback rather than
treating old advertised values as current; missing required confirmation stays
held. Only configuration events advance the checked history during this step;
unrelated concurrent activity, stops and ownership changes refuse completion.
An older acknowledgement without the snapshot can finish only when its original
history affirmatively records completed configuration.

On the original frontend, a failed resume can still retain its ACP client.
Completion first qualifies that inactive RPC owner's client, Host socket,
acknowledgement and idle operation state under the existing ownership locks,
then detaches only that frontend before reattaching. An unassociated client
stays untouched. A configuration-timeout diagnostic is accepted only after the
same durable acknowledgement, never as independent load evidence.

An older writer's affirmative tagged load-finished history can be adapted only
when the retained Host's immutable state and birth evidence associate its launch
with that original event span. Missing or conflicting history, a foreign live
frontend, changed head/incarnation, a stop hold or unavailable transport stays
held. Reattachment failure never prunes or reaps this unknown original Host.
Registration response loss retries the same hosted activation idempotently,
not the load. Completion atomically records the original operation complete,
removes its launch gate and marks the session idle; task acceptance, permission
holds, policy clocks and pending prompts remain independent.

The same journal retains completed and uncertain operation IDs. A same-ID
request reads that outcome, never relaunches. Pending/unknown recovery blocks
other resumes; transport mutation retries are disabled. The original-operation
`GET /api/v1/sessions/{exact-id}/retained-recovery/{operation-id}` route, or
`status <exact-id> --recovery-operation <id> --json`, reconciles the original
operation without a new launch. A lost reply after committed acceptance
returns the retained completion. Unknown native execution remains held;
live status by itself cannot invent acceptance of that operation.
Preparation is retained before native qualification. A refused preparation
or an automatic admission that never entered native launch records
`not-started`; a preparing request or uncertain native entry remains unknown.
Inspection can settle that exact no-effect outcome without reusing its ID.
Recovery now durably tags its startup event span with the original operation
before entering native preparation. Status reconciliation requires a stopped
original owner, unchanged compatible writer/workspace/head,
and the retained local Host record with both birth-matched processes
confirmed exited. It commits `not-started` and removes that operation's
launch gate atomically; it does not remove the Host record, change the
request/history, clear a stop token, drain prompts or start native work.
An in-memory failed initialization transport can be detached only through its
inactive original recovery owner, before any native RPC and without pending
permission or elicitation state, after the same process-exit proof. Only that
owner's retained streams and socket are closed; no process is signaled and no
generic client shutdown runs. Startup pruning and ordinary dead-client
settlement preserve Host evidence while the original recovery is unresolved,
so inspection also works after frontend restart.
Completed handoff predecessors are retained history, not competing live
owners. Reconciliation excludes such a row only after following the completed
keyed handoff chain and verifying its stopped state, original native/workspace
binding, absence of an active in-memory transport or turn, and exited
birth-matched processes for any retained predecessor Host. Uncompleted,
foreign, live and ambiguous predecessor custody still blocks settlement;
the historical row itself is never changed.
For older writers without the explicit operation tag, only a complete fresh
metadata-adoption epoch containing its sole recovery operation and the
affirmative initialization/child-exit/terminal-resume failure sequence is
eligible. Missing or ambiguous history, a possible native load, live or
unknown Host custody, and changed ownership remain held. This is receiver-side
completion of the existing version-1 inspection contract, not a new caller
option or permission to replay an unknown operation.
A non-complete same-ID CLI replay reports
`verb: recovery-inspected`, not a successful resume.
The existing native launch gate commits atomically with the operation journal.
Its native-bound gate form also blocks legacy ordinary ACP resumes and fresh
starts after provider rollback; only this task's exact recovery owner may pass
it. Unknown outcomes retain the gate, and confirmed acceptance settles only
that operation's gate in the same commit.

Recovery preserves the original session, workspace, charter, native
conversation and queued prompts. It neither drains prompts nor sends a new
turn, runs the ordinary resume retry ladder, recreates a conversation or
enables background recovery. Native durable stop tokens remain in force
through automatic recovery admission;
this operation is not manual consent to clear them. Ordinary explicit resume
retains its separate behavior. Only ordinary keyed local sessions are supported;
admitted-runtime custody and separate cold-update recovery retain their
existing owners. Local Host registration records native host/child birth
identities. Recovery can settle that exact stale record only after both
original processes are confirmed exited under the Host-index lock. Missing
identity, live/unknown processes, a changed record or a remote boundary holds.
There is no process termination or forced claim release.

Automatic recovery does not inherit approval authority from a missing upstream
permission callback. The replacement client holds permission requests in the
existing manual-resolution path until an owner explicitly resolves them or
the request is canceled. The durable original recovery journal restores this
hold before ACP initialization after transport loss or daemon restart;
reattachment never grants auto-approval. The retained-recovery CLI does not
start a missing daemon.
Preparing, invoking, unknown or malformed recovery records protect their
original session handles from startup collection, periodic garbage collection
and transactional deletion, so the original outcome remains inspectable.
Explicit teardown shares recovery's original executor fence and cannot remove
an unresolved handle, even with `force`. Persisted SQLite guards also reject
legacy deletion of a handle retained by a recovery journal or launch gate.
The retention binding follows the keyed journal through succession; it does not
assume the journal's initial session is still the exact current handle.
Malformed terminal entries remain held too: a terminal state string alone is
not a validated recovery request. Schema 45 installs the recovery column and
additional deletion guards through writable migration of existing databases,
validating their earlier writer contract before migration without rewriting its
guards. Original-handle lookup is indexed and launch-gate membership is collected
once per query, rather than rescanned for every retained session.

`retained_workspace_creation_writer_inspection: 1` adds the optional
`BridgeClient.get_session(exact_id, worktree_writer=True)` form on the existing
`GET /api/v1/sessions/{exact_id}?worktree_writer=true`. The negotiated query
qualifies a compatible current serializer and the original workspace, bracketed by unchanged
journal, target, native identity and active Bridge readiness/instance checks.
It returns `worktree_writer_observation` schema
`copilot-extensions.creation-worktree-writer`, version 1, with the creation key
digest, exact session/native/workspace, original requirement, executing identity, serializer
contract and Bridge instance. It exposes no command or environment. An
unqualified exact owner returns 409; an older reader ignoring the query is
unconfirmed, not successful evidence. Ordinary session reads remain unchanged.

A retained terminal creation journal remains inspectable for its exact managed
native session and still-existing workspace. Inspection does not reopen the
journal or resume a session. Changed native/workspace/installation identities,
journal movement, finalizing/terminal workspaces and unavailable original
contracts retain their refusals; terminal history alone cannot prove liveness.

The existing full-session CLI exposes the same optional observation through
`status <exact-session-id> --full --worktree-writer`. Its
`agent-bridge.status` CLI contract separately advertises
`exact-session-worktree-writer`; daemon capability negotiation still governs the
actual read. See the [CLI reference](../skills/agent-bridge/references/cli-commands.md#service-control)
for executing-CLI discovery and passive health JSON. This does not add another
provider operation or change the HTTP generation.

This is read-only qualification of the creation's delegated writer, not a
ready permit, native-liveness assertion or admission of every other writer
of the shared workspace. The ordinary Dispatch writer remains independently
gated.

### Metadata loss without native conversation loss

For the union of registered sessions and workspace-only targets, and its
metadata-only head-hydration operation, see
[target inventory](target-inventory.md).

The optional `retained_native_metadata_adoption: 1` health capability adds
metadata preparation to the same original keyed-creation owner:

- `POST /api/v1/sessions/{predecessor-id}/retained-metadata` takes
  `{"recovery": <version-1 retained recovery request>, "worktree_writer":
  <original requirement>}`.
- `GET /api/v1/sessions/{predecessor-id}/retained-metadata/{journal-key}`
  inspects a committed adoption without native effects.

The separately negotiated `client_retained_metadata.prepare` and `inspect`
helpers use these routes within HTTP generation 52. This provider-first
expansion adds no CLI option or automatic caller migration. Each attempt
checks the actual receiving daemon's capability; older receivers return an
unsupported-contract result before mutation, not a generic resume fallback.
The client validates the returned version, original key/request, predecessor,
new control ID and history boundary. An unconfirmed reply is not replayable.

Preparation requires a complete original creation journal and unchanged native
head/workspace incarnation, a compatible current Worktrees writer, no old
managed record or conflicting holder, complete Host inventory and no unresolved
launch, recovery, handoff or stop custody. Original agent, model, effort, tools
and task origin come from immutable creation facts; unsupported or incomplete
configuration refuses. The native owner is observed before and after its
dormant-conversation admission preview. Preparation starts no native process,
sends no prompt, drains no queue and attaches no task.
Provider-backed targets reconstruct request-owned environment, argument and
controller-origin provenance with the original start-time merge rules. A later
provider refresh can change provider defaults without losing those original
request overrides.

When prior managed history is missing, the existing native-import transaction
commits one **new managed control ID** and its immutable adoption in the original
creation journal together. The original key, birth ID, native ID, writer,
handoffs and recovery operations remain unchanged. Version-1 outcomes contain
`request`, `predecessor_id`, `session_id`, `prior_operations` and
`history: {status: "unavailable", predecessor_id, event_epoch, turn_epoch}`.
The new event and turn epochs equal the new managed ID. No old events, turns,
cursors or delivery receipts are synthesized. The predecessor and native-ID
aliases do not address this new stream; use its explicit managed ID.

A lost response, restart or repeated identical preparation discovers that same
committed epoch. A missing adopted record remains held rather than allocating
another epoch. Absence of an observable adoption is not negative evidence about
an earlier uncertain request. Old-epoch recovery operation IDs cannot be
reinterpreted against the new record.

The adopted record stays stopped with background recovery disabled. Its first
native execution must use the existing qualified retained-recovery operation,
with a new operation ID and current head evidence. Generic resume/import cannot
drop this custody; permission requests remain held for explicit resolution.
After a new qualified native recovery completes, ordinary lifecycle closeout
can proceed. Historical completed operations alone do not release retention.
Ordinary worktree resume checks that recovery hold before either ACP or CLI
launch can claim a gate. After qualification, normal handoff admits its exact
journaled successor without allowing an unrelated import or reusing an excluded
control ID. The epoch marker remains internal ownership metadata during
provider classification and refresh; genuine provider targets still require
their original override provenance.
Only the adopted control's own marker denotes missing history. A handoff
successor's fresh native conversation retains normal native-ID resolution and
transcript resync, even when its target carries the predecessor's lineage marker.
This preparation does not prove a new turn, native acceptance, original-task
attachment or installed-provider qualification.

Writable migration adds nullable `creation_adoption_json` and the exclusion,
identity, pointer and configuration-retention guards plus an indexed adopted-ID
lookup at schema 49. Retention uses that index rather than scanning every creation
journal for each session. Passive
cutover validates the current schema before writes; a prior schema-48 receiver
refuses it. Supported rollback must retain these semantics or remain unable to
open this store: do not downgrade its schema stamp, erase adoption or restore
an empty old namespace. Arbitrary legacy writable bootstrap is not a qualified
rollback path. This operation-level compatibility limit is not an installer
replacement veto or a requirement to keep the original executable.

The actor-free promotion controls exercise birth/handoff/adopted lineage,
old-namespace and old-receipt exclusion, corrupt boundaries and qualified import
inputs. Selected plugin controls separately exercise actual HTTP negotiation
and malformed/lost replies, atomic database rollback/restart, independent-process
serialization, configuration retention and prior-schema passive refusal.
Native same-conversation execution, a new turn and installed rollback remain
independent qualification gates.

### Ordinary worktree resume after metadata loss

`retained_worktree_resume: 1` advertises composition through the existing
worktree ACP resume front door. It selects the workspace's unique original
keyed journal, verifies its discovered owner/project/path and authoritative
native head through a compatible writer, prepares the control epoch when
needed, then invokes retained native recovery under its existing launch owner.
It does not require the caller to reconstruct rows or supply writer facts.
The metadata and native operations retain their separate exclusion boundaries;
ordinary resume does not nest a second workspace executor inside preparation.

The response carries `retained_metadata_adoption`, `retained_recovery` and
`worktree_writer_observation` alongside the ordinary session fields. The
explicit adopted managed ID is current control identity; the original native
conversation and missing historical stream remain distinct. A confirmed ready
control is reusable without another native load or recovery operation; its
response has no newly executed `retained_recovery`. A known subsequent stopped
activation obtains current head evidence and a distinct operation ID, not a
replay of a historical completion or a task-policy reset.

Unknown operations, changed native heads, ambiguous readiness, unavailable
writers and incompatible workspace ownership hold before another native load.
Missing keyed metadata is not silently converted to CLI control. Existing
present birth records and qualified handoff successors retain their ordinary
paths. Source tests cover lost replies and concurrent retries against the real
resume front door without a second native load.

The executing CLI advertises `exact-session-absence` for full JSON status.
An exact session's 404 returns a typed `refused_unavailable` result naming
`native_session_unavailable` and the target, retaining exit status 1. Other
failures and compact status keep their existing behavior; transport failure is
not session absence.

`retained_keyed_native_admission: 1` advertises that a later keyed prompt
re-enters the original creation writer through its existing continuation owner.
The exact resumed workspace/head check and prompt admission share that compatible
writer context; they do not substitute the currently selected default writer.
Receipt-only observation and an already-retained receipt remain non-mutating
and do not need to reconstruct the writer. This capability admits no old key
into an adopted epoch and does not replace the caller's current task admission.

The additional continuation scope applies to fresh **resumed** keyed admissions,
not initial created-session admissions. Each such send selects/qualifies its
compatible writer once and verifies the exact workspace/head; it adds one
capability read and one workspace record/head pair to the existing resumed
admission checks. These are bounded owner commands, not repository/history
scans or per-token callbacks. A completed receipt and receipt-only observation
perform no writer selection or native load. Writer evidence is requalified for
each new mutation rather than cached across possible owner replacement.

Required actor-free work-count controls cover created versus resumed admission,
receipt misses, retained receipts and accepted-send replay. The selected
`tests/test_dispatch_retained_resume.py` composition additionally checks that
lost resume replies and failed pre-commit storage reuse the same native load,
and that receipt observation does not select a writer again. For reproducible
model-free cold/retry comparisons, run that file through the repository's
container runner with `--junit-dir <owned-output-directory>` and compare its
three named cases on the same source and runner configuration. Those case
durations include fixture setup and are not native-launch measurements.

Native cold/warm latency is unqualified here. In an authorized retained-native
pilot, time the ordinary exact-workspace resume command once from stopped state,
then repeat against the resulting ready control; compare wall time and the
existing startup diagnostic stages under the same runtime/workspace. Observe
the persisted current invocation's receipt separately, without resending or
stopping a live worker to manufacture a cold case. Preserve the pilot's original
deadline and cleanup owner; a pending/unknown operation is not another sample.

### Target inventory and metadata-only lookup

`agent-bridge targets --json` consumes `target_inventory: 1` and returns the
typed union of active managed records, live Session Host and extension
registrations, and every discovered workspace. It retains each source's
availability and discovery completeness rather than treating an unavailable
source as empty. `--refresh` requests the existing workspace refresh.
The ordinary `agent-bridge sessions` text view includes every source.
`agent-bridge --json sessions --all-sources` selects this version-1 typed
envelope (`targets` and `sources`). The default `--json sessions` preserves
the managed-record array, so independently installed older consumers continue
to work. `sessions --owned-only` also retains the managed-only text view.
`GET /api/v1/sessions` and the client `list_sessions()` method remain unchanged.
An explicit `sessions --status stopped`, `ended` or `failed` also includes the
requested historical managed records in the typed/text view, without adding
terminal history to the ordinary active catalog.

For a verified local endpoint, the CLI also reads the selected Worktrees owner's
passive inventories across all locally registered worktree/knowledge
repositories, including repositories with agents disabled. A forwarded,
explicit or unverifiable endpoint never receives machine-local completion.
Sources are independent: an unavailable owner does not hide another owner's
rows. Structurally valid stale or degraded workspaces remain visible with
`observation_valid: false` and unavailable source evidence; they do not become
current discovery, dispatch authority or live registrations. Declared source
freshness windows retain their original expiry rather than renewing on read.
Freshness is bounded by both publication age and reported refresh age, including
elapsed source-read time. Strict repository enumeration must acknowledge complete
inventory authority; an unsupported or unreadable registry is not an empty
successful catalog.
The CLI rechecks every locally read window after the final repository read:
an earlier observation that expired during a later read remains visible but
unavailable. Process-local deadlines are not serialized into the catalog.

Inventory-only discovery also participates in the service cache's ordinary
startup and private resolver/cache snapshots. These sources remain outside agent
resolution. Same-execution-space snapshots retain source expiry; deadline-free
legacy records keep their prior shape. New receivers accept omitted legacy
inventory metadata as empty, while older receivers reject unfamiliar source
state before applying it rather than silently dropping repositories.
Local inventory-only service reads select the attributable Worktrees installation
with explicit `--project` and `--passive` arguments, not a project-named PATH
executable. Missing owners are unavailable; selected-owner environment, finite
timeout and descendant cleanup remain part of that read.
On POSIX, the existing finite owned-child executor retains the unreaped root
until its original process group is retired and positively confirmed, including
when a descendant holds a captured pipe after root exit. Async cancellation joins that bounded owner
through settlement rather than abandoning its worker or signaling a reused
process-group ID. Windows retains the existing strict Job-owned async path.
Daemon startup uses the same strict, selected-owner repository reader, including
when no topology is configured. An unavailable or unqualified registry never
falls back to a provisioning shim or an empty successful enumeration. Known
independent workspace rows remain visible, while discovery reports incomplete
repository inventory. Private resolver/cache snapshots preserve that failure
status for forwarded readers; a successfully qualified empty registry remains
distinct from failure.
This status belongs only to workspace inventory, not fatal agent topology
diagnostics. Configured and remote agents retain ordinary listing, streaming and
initial-subscription behavior when the repository owner is unavailable.
Windows Job-assignment or suspended-resume failures from the selected launcher
also mark that inventory unavailable instead of aborting daemon readiness or
catalog rendering. Other repositories and independently registered targets
remain visible; no weaker launcher is retried.

Standalone local ACP Hosts are observed through their existing daemon-instance
registrations and birth-matched Host state. This does not attach a controller
or change ownership. An unbound Host uses its actual instance handle and a null
native session ID. Dead, reused, unknown or disposable test instances never
become live production Hosts; credentials and control endpoints are not exported.
Hosts publish a caller-selected state-file locator through their existing
process registration, including arbitrary filenames outside the runtime root.
The locator grants observation, not artifact cleanup. A verified running Host
with missing or invalid metadata remains an explicitly unavailable, unbound
observation rather than disappearing or acquiring an invented native ID.
Finished registrations and known stopped Host states are not live Hosts.
Repeated observations with unknown CWD coalesce only with the same daemon
instance and exact PID/birth identity. Known, differing workspace scopes remain
separate even when another identity field matches.
Extension heartbeat registration is armed even when Bridge credentials are not
available at extension startup. It retries the selected endpoint and adopts the
SDK's current native session identity on heartbeat, including an idle resume
that produces no event. Existing process-incarnation and delivery fences remain.

Ordinary workspace-handle `read`, `wait` and raw `send` select this inventory
before falling through to profile matching. A workspace without a managed
record uses `worktree_metadata_hydration: 1` to read its authoritative head
and persist metadata through the original keyed adoption or native-import owner.
Listing and hydration never load the native conversation, submit a prompt,
claim a launch gate or accept a task. A dormant managed target refuses raw
send with `resume_required`; explicitly resume the workspace first. Lost
hydration responses terminate the CLI, including enclosing handoff-follow
reconnect loops, with `workspace_hydration_unconfirmed`; they are not replayed.
Structured owner refusals retain their actual reason rather than becoming an
inferred missing target.

An unqualified workspace handle requires complete workspace discovery, including
on the metadata receiver. Once the projection identifies a qualified managed
record, the CLI reuses that record while it exists. Reading existing history
does not depend on a new head probe or the absence of a pending handoff.
Only a missing managed record enters metadata hydration.
Workspace projections prefer live qualified records over inactive predecessors.
Handoff following excludes its predecessor and can select another live managed
record in the same owner/project/path/transport scope without probing a head;
it never falls backward to older inactive history.

Conflicting workspace owners, unavailable discovery and unresolved registered
transports refuse instead of creating a replacement profile session. A known
workspace on an older receiver reports the missing inventory/hydration contract
before effects. Existing legacy managed-session reads remain supported.
Explicit provider and venue forms retain their own resolution/admission path
when an unrelated workspace source is incomplete, on current and older
receivers. A known unresolved registration still refuses rather than being
reinterpreted as a provider creation request.
Owner qualification compares the resolver's actual transport, including local
loopback and resolved SSH aliases, not the profile's raw host spelling. Cached
rendering projects that identity from topology without synchronous launch-plugin
or repository-settings discovery.

Dispatch control-identity migration, assignment delivery and affirmative worker
task acceptance remain separate consumers. Neither these source controls nor a
successful resume prove a new native turn, task `STARTED`, closeout or installed
recovery.

## Qualification boundary

The optional `retained_acquisition_writer_inspection: 1` health capability
adds `acquisition-inspect <delivery-id> --worktree-writer`, using the existing
`GET /api/v1/acquisitions/{delivery_id}?worktree_writer=true`. The CLI
negotiates that capability on each transport attempt. Without the option,
inspection retains its previous response and performs no writer probes.

The required form reopens only that delivery's original journal binding.
It selects a compatible current executor without retrofitting an unqualified
legacy acquisition, acquiring a native, delivering a prompt or repairing a
record. It qualifies that serializer and reads the original workspace through
the same invocation selection. Changed project, path or creation nonce refuses. The journal
and active Bridge instance must still match after those reads; unknown,
passive, draining or unready owners do not yield successful evidence.

The additive `worktree_writer_observation` contains schema
`copilot-extensions.acquisition-worktree-writer`, version 1, the original
delivery/reservation/task/workspace fields, the retained `worktree_writer`
requirement, separate executing identity, live serializer contract and active Bridge instance
(installation root, PID and routing generation). It exposes no command or
environment. Missing acquisitions retain their 404; an unqualified writer
inspection returns 409. This is a fresh read-only observation, not a durable
ready permit, native-liveness proof or successful-delivery assertion.

This capability qualifies Bridge's delegated writer route, **not** the resident
serializer or every other producer of the shared Worktrees record. Existing
relationship transactions retain their own effect-side expected-nonce/writer
checks. Independent original-workspace and complete participating-writer
admission remain required before ordinary governed assignment is assembled.
No ordinary Dispatch writer, allocation, rollout, cleanup or native-replacement
permission follows from the capability.

Focused source tests cover request negotiation, pre-native refusal, journal
recovery, concurrent replay and an HTTP round trip. A finite source-process
control executes the actual Worktrees parser/serializer for registration and
succession while retaining an existing protected claim and nonce, with a
reachable ordinary-verb-compatible but unqualified resident. Unpinned control
writes reach that resident; required writes never do. Compatible newer
generations can serialize continuation without changing birth provenance;
foreign installations, changed nonces, projects and paths still refuse.
Its declared
build identity and native ACP transport are controlled fixtures; it is not
installed-runtime, native Copilot or closed-cohort acceptance.
