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
| `identity.runtime_generation` | Expected immutable-slot 32-hex build identity |
| `creation_nonce` | Original workspace incarnation, checked against exact owner reads |

This object rejects unknown fields. It accepts no caller command, interpreter,
environment or successful-qualification assertion. Other request versions
cannot carry it. CLI mode with this requirement refuses before owner/native
effects; ordinary unpinned CLI acquisition remains supported.

## Selection and recovery

The provider resolves its own existing pinned Worktrees selection and runs
`resource-relationship capabilities` through that exact command. The response
must identify the expected executing serializer and its retained-relationship
and creation-nonce preservation contract, plus `retained_session_writes: 1`.
Selected-root metadata alone does not qualify it.

The original request and provider-selected command commit together in the
existing delivery-keyed acquisition journal before launch. The command remains
part of that journal's existing effect history through launch and delivery.
A new frontend or concurrent retry recovers it without selecting a replacement.
Later same-task acquisition retains the same requirement and command; an
unqualified historical acquisition cannot be retrofitted. Missing or conflicting
recovery facts refuse with the existing custody intact.

Acquisition reads, delegated registration and binding use the retained owner.
The native target stores only a pointer to the original acquisition journal.
Later native resume and handoff recover and requalify that same command, check
the original workspace, and use it for registration, succession and lineage
reads. Required lineage errors are not silently treated as legacy best-effort
success. Unpinned operations keep their existing fail-open behavior.

The historical `acquisition:` caller-affinity prefix is also used by keyed
workspace creation and singleton initialization. Without an explicit writer
pointer or a matching acquisition attempt, it does not select this journal:
those continuations retain their existing lifecycle owner. An explicit writer
pointer still requires its original journal, and a matching attempt with
missing or malformed effects refuses rather than becoming an unpinned call.

Registration, binding and succession carry the original requirement through
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

The merged pinned launcher owns retention, current installation governance and
pre-import checks of the original completion/build identity. Selector movement
does not replace this writer. Removed or recreated slots refuse; this feature
does not veto an authorized installer replacement or preserve a second registry.

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
incarnation, requirement and provider-selected pinned writer before native
launch. Required replay requalifies that command and exact workspace without
reselecting. A different key cannot capture the same retained workspace;
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
`retained_native_resume: 1` and enforces the original workspace incarnation
and native head revision inside its process-creation fence. An older owner
does not silently receive an ordinary unfenced resume.

The same journal retains completed and uncertain operation IDs. A same-ID
request reads that outcome, never relaunches. Pending/unknown recovery blocks
other resumes; transport mutation retries are disabled. The read-only
`GET /api/v1/sessions/{exact-id}/retained-recovery/{operation-id}` route, or
`status <exact-id> --recovery-operation <id> --json`, reconciles the original
operation without a new launch. A lost reply after committed acceptance
returns the retained completion. Unknown native execution remains held;
live status by itself cannot invent acceptance of that operation.
Preparation is retained before native qualification. A refused preparation
or an automatic admission that never entered native launch records
`not-started`; a preparing request or uncertain native entry remains unknown.
Read-only reconciliation can settle that exact no-effect outcome without
reusing its ID. A non-complete same-ID CLI replay reports
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
reconnects. The retained-recovery CLI does not start a missing daemon.
Preparing, invoking, unknown or malformed recovery records protect their
original session handles from startup collection, periodic garbage collection
and transactional deletion, so the original outcome remains inspectable.
Explicit teardown shares recovery's original executor fence and cannot remove
an unresolved handle, even with `force`. Persisted SQLite guards also reject
legacy deletion of a handle retained by a recovery journal or launch gate.
The retention binding follows the keyed journal through succession; it does not
assume the journal's initial session is still the exact current handle.

`retained_workspace_creation_writer_inspection: 1` adds the optional
`BridgeClient.get_session(exact_id, worktree_writer=True)` form on the existing
`GET /api/v1/sessions/{exact_id}?worktree_writer=true`. The negotiated query
requalifies the original serializer and workspace, bracketed by unchanged
journal, target, native identity and active Bridge readiness/instance checks.
It returns `worktree_writer_observation` schema
`copilot-extensions.creation-worktree-writer`, version 1, with the creation key
digest, exact session/native/workspace, original requirement, serializer
contract and Bridge instance. It exposes no command or environment. An
unqualified exact owner returns 409; an older reader ignoring the query is
unconfirmed, not successful evidence. Ordinary session reads remain unchanged.

A retained terminal creation journal remains inspectable for its exact managed
native session and still-existing workspace. Inspection does not reopen the
journal, resume a session or select a replacement writer. Changed identities,
journal movement, finalizing/terminal workspaces and unavailable original
writers retain their refusals; terminal history alone cannot prove liveness.

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

## Qualification boundary

The optional `retained_acquisition_writer_inspection: 1` health capability
adds `acquisition-inspect <delivery-id> --worktree-writer`, using the existing
`GET /api/v1/acquisitions/{delivery_id}?worktree_writer=true`. The CLI
negotiates that capability on each transport attempt. Without the option,
inspection retains its previous response and performs no writer probes.

The required form reopens only that delivery's original journal binding.
It never selects a replacement command, retrofits a legacy acquisition,
acquires a native, delivers a prompt or repairs a record. It requalifies the
retained executing serializer and reads the original workspace through that
same command. Changed project, path or creation nonce refuses. The journal
and active Bridge instance must still match after those reads; unknown,
passive, draining or unready owners do not yield successful evidence.

The additive `worktree_writer_observation` contains schema
`copilot-extensions.acquisition-worktree-writer`, version 1, the original
delivery/reservation/task/workspace fields, the retained `worktree_writer`
requirement, live serializer contract and active Bridge instance
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
writes reach that resident; required writes never do. Changed identity, nonce,
project and path refuse at all three receiving commands. Its declared
build identity and native ACP transport are controlled fixtures; it is not
installed-runtime, native Copilot or closed-cohort acceptance.
