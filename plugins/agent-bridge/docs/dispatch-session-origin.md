# Original Dispatch assignment on Bridge sessions

The [session acquisition](session-acquisition.md) receiver accepts optional
`dispatch_origin` metadata for local ACP and CLI-native sessions. This is a provider expansion,
not automatic Dispatch enrollment or permission to retire a session.

## Contract and scope

The executing daemon advertises `capabilities.dispatch_acp_session_origin: 1`
and independently `capabilities.dispatch_cli_session_origin: 1`
through `/health`. An upgraded caller must observe the declaration for its mode on the
daemon it will actually invoke before sending this field; a package version or
an installed CLI is not evidence that the daemon supports it.

The optional object contains exactly:

- `task_id`: the original session-creating task.
- `reservation_key`: that assignment's original reservation.
- `cohort`: the existing version-1
  [Dispatch assignment cohort](../../agent-dispatch/docs/task-obligations.md#original-assignment-cohort),
  with explicit nullable `emitter` and `pool` references.

References retain exact task, managed producer request, pool registration,
catalog revision, execution machine/environment and reservation values.
Unknown fields, blank/NUL-bearing or over-512-character references, boolean
counters, unsupported schema versions and cross-task/reservation references
are rejected. This caller-supplied annotation is not independent proof of
producer authority, native custody, current assignment or cleanup permission.

`POST /api/v1/acquisitions` accepts the field for either mode and requires
the same task and reservation as the acquisition. Both the original serialized
request and its existing replay digest retain it before launch.
`POST /api/v1/sessions/workspace-creations` (and keyed creation through the
sessions endpoint) requires an agent, creation key and prepared worktree ID.
Provider-native, remote, elevated, explicit-directory and parity-fault forms
are not supported. Metadata-bearing named-target resolution does not activate
a provider before local admission.
The resolver also refuses explicit `admin:` venues and bare or aliased
`requires_admin` targets before privileged relay startup, including repo-bound
forms. Ordinary requests retain the resolver's existing elevation behavior.

Only a newly created ACP session receives the annotation. Session start
validates and detaches it before preparation, then persists it in the existing
first-save `sessions.config_json` alongside model/toolset options, before native
launch. There is no new ownership table, post-launch patch or schema migration.
The session list and detail views read this original config, never mutable
target metadata or current task/pool labels. Missing or unrecognized metadata
is exposed as `null`; its raw stored config remains unchanged.

For CLI acquisitions, the original request, selected creation decision and held
launch gate supply the first-save input. Inside the existing registration
transaction, a new native row retains the snapshot in nullable
`live_sessions.dispatch_origin_json`, before reservation claim and alias folding
commit. Heartbeats never backfill or overwrite it. Mutable `driven_by`, venue,
worktree or current task labels are not birth evidence; the driver must match
the original pending acquisition and its retained request digest.
Both live-session list and detail expose the validated annotation, with `null`
for absent or opaque storage. A same-process rename carries the original raw
snapshot; a resume into an already recorded conversation preserves that
conversation's snapshot or legacy absence instead of inheriting its provisional
registration's stamp. A newly created CLI without the requested first-save
evidence remains a pending acquisition: no task prompt is admitted or replacement
process launched to hide that missing evidence.

A reused/resumed conversation keeps its original stamp, including honest
legacy absence. A new session in a reused workspace must name its own creating
assignment, not the workspace's older allocation cohort. A separately created
successor without explicit original-assignment input is unattributed; lineage
is not permission to guess a stamp. Existing task/attachment and workspace
owners remain authoritative for actual obligations and disposition.

## Upgrade, rollback and validation

Old callers preserve their request/replay meaning: absent acquisition metadata
is omitted from serialization, and absent creation metadata adds no option to
the keyed facts. Old receivers may ignore additive JSON; that is not successful
retention. Dispatch's [CLI acquisition caller](../../agent-dispatch/docs/task-obligations.md#conditional-cli-native-acquisition-caller)
conditionally selects this field before reservation; the
[managed ordinary caller](../../agent-dispatch/docs/task-obligations.md#conditional-managed-ordinary-creation-caller)
uses the corresponding ACP capability and retains historical baseline choices.
Callers must negotiate support before migration, retain their original wire
choice for pending attempts and never replay an unknown outcome in another form.
Provider rollback leaves baseline unstamped operations available, not an
equivalent metadata-enforcing fallback. Schema 46 adds nullable CLI storage
without backfilling existing rows. Read-only adoption requires supported writable
preparation; an already-current schema missing the column refuses rather than
silently repairing it. Older providers do not supply the CLI-origin contract;
they are not an equivalent path for metadata-bearing launch or rename. No
baseline form is retired here.

The existing acquisition request-file client checks the mode-specific named
capability on the selected daemon before each send attempt, composing with
retained-writer requirements when supplied. Missing or invalid capability
declarations refuse before POST instead of letting an older additive reader
silently launch without retaining the metadata. Ordinary requests without
origin metadata keep their prior negotiation path.

Ordinary keyed ACP creation accepts `--dispatch-origin-file <path>` on
`create`; `-` reads JSON from stdin and cannot share that stream with a prompt
or writer requirement. It requires a prepared local worktree and creation key.
Invalid JSON, scope and remote/provider/CLI forms refuse before connection.
The existing Python `start_session` client accepts `dispatch_origin` and
negotiates the ACP-origin capability before each selected-endpoint send,
composing with retained-writer requirements. Retry across an unsupported
daemon cutover refuses rather than dropping metadata or replaying a baseline
form. The receiver's keyed facts retain changed/dropped-origin refusal.

The executing CLI's existing `version --contracts-json` declares
`agent-bridge.create` capability `keyed-dispatch-origin` and
`agent-bridge.acquire` capabilities `dispatch-acp-session-origin` and
`dispatch-cli-session-origin`. A future caller must observe the corresponding
CLI declaration before invoking the new carrier, as well as running-daemon
support. Provider declarations do not establish installed cohort qualification
or retrofit metadata into existing pending invocations.

`tests/test_dispatch_session_origin.py` exercises strict nested input,
original request persistence, HTTP keyed creation, changed/dropped replay facts,
restart, mutable targeting, reused legacy sessions and pre-effect venue refusal.
Real-resolver controls exercise both HTTP creation and acquisition without
calling the privileged startup owner.
Run it with the existing contained plugin runner alongside
`test_acquisition_runtime.py` and `test_workspace_creation.py`.
`tests/test_dispatch_cli_origin.py` joins acquisition, native registration,
SQLite, aliases and HTTP views; it covers original replay, first-save refusal,
rollback, resume, opaque storage and schema preparation.
These are synthetic native/owner source controls, not installed-runtime,
native Windows or production cohort qualification.
`tests/test_dispatch_origin_cli.py` joins the actual parser, client, HTTP
receiver and first-save database without an acquisition. It also checks
unsupported peers, composed requirements, cutover and pre-connection refusal.
Required unit-packet nodes, permissions, targets and budgets are unchanged.
