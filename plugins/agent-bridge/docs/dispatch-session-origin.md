# Original Dispatch assignment on ACP sessions

The [session acquisition](session-acquisition.md) receiver accepts optional
`dispatch_origin` metadata for local ACP sessions. This is a provider expansion,
not automatic Dispatch enrollment or permission to retire a session.

## Contract and scope

The executing daemon advertises `capabilities.dispatch_acp_session_origin: 1`
through `/health`. An upgraded caller must observe that declaration on the
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

`POST /api/v1/acquisitions` accepts the field only for ACP requests and requires
the same task and reservation as the acquisition. Both the original serialized
request and its existing replay digest retain it before launch.
`POST /api/v1/sessions/workspace-creations` (and keyed creation through the
sessions endpoint) requires an agent, creation key and prepared worktree ID.
Provider-native, remote, elevated, explicit-directory and parity-fault forms
are not supported. Metadata-bearing named-target resolution does not activate
a provider before local admission.

Only a newly created ACP session receives the annotation. Session start
validates and detaches it before preparation, then persists it in the existing
first-save `sessions.config_json` alongside model/toolset options, before native
launch. There is no new ownership table, post-launch patch or schema migration.
The session list and detail views read this original config, never mutable
target metadata or current task/pool labels. Missing or unrecognized metadata
is exposed as `null`; its raw stored config remains unchanged.

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
retention. No automatic CLI or Dispatch caller sends this field yet. Such a
caller must negotiate support before migration, retain its original wire
choice for pending attempts and never replay an unknown outcome in another form.
Provider rollback leaves baseline unstamped operations available, not an
equivalent metadata-enforcing fallback. No baseline form is retired here.

`tests/test_dispatch_session_origin.py` exercises strict nested input,
original request persistence, HTTP keyed creation, changed/dropped replay facts,
restart, mutable targeting, reused legacy sessions and pre-effect venue refusal.
Run it with the existing contained plugin runner alongside
`test_acquisition_runtime.py` and `test_workspace_creation.py`.
These are synthetic native/owner source controls, not installed-runtime,
native Windows, CLI-native registration or production cohort qualification.
Required unit-packet nodes, permissions, targets and budgets are unchanged.
