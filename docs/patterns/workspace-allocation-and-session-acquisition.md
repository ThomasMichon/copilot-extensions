# Workspace allocation and session acquisition

This pattern realizes the worktree vision's
[replay-safe-workspace-allocation](../../visions/plugins/agent-worktrees/README.md#replay-safe-workspace-allocation),
the bridge vision's
[workspace allocation and session embodiment](../../visions/plugins/agent-bridge/README.md#workspace-allocation-and-session-embodiment),
and dispatch's
[execution workspace continuity](../../visions/plugins/agent-dispatch/README.md#execution-workspace-continuity).
The implementation campaign is
[workspace-acquisition-continuity](../../efforts/2026/10/13%20workspace-acquisition-continuity/README.md).

## One workspace, distinct owners

`agent-worktrees create` allocates space without starting a session.
For a worktree-capable repository, `agent-bridge create` composes that same
allocation boundary with session embodiment. An explicitly supplied existing
sessionless worktree is an ordinary input to embodiment, not a forced takeover.
Existing or uncertain conversation history retains the normal head, holder,
retirement, and task-affinity checks.

Dispatch chooses the execution target once. Allocation returns a
repository-qualified workspace identity, including the worktree and its
resolved path. Acquisition, recovery, and cleanup retain those owner-returned
facts. They never independently rederive the execution repository from the
task's queue lane or code target.

Three repository roles can legitimately differ:

- **Queue lane:** the producing task namespace and its claim authority.
- **Execution project:** the registered repository owning the worker worktree.
- **Code target:** an optional repository the worker is asked to change.

Only the execution project qualifies allocation and session embodiment.
A mismatch between requested and owner-returned workspace facts refuses before
launch; it is not a reason to search another registry or create a replacement.

## Creation identity is not delivery identity

A logical creation request may carry an opaque caller-selected key. Qualify it
by the owning installation and canonical target repository; bind the key to the
effective creation facts and original result. Do not use it as the worktree ID.
An exact concurrent or lost-response replay returns that same result, or an
explicit pending/recovery outcome if creation is not yet proven.

Both front doors use the same allocation key and allocation facts. Bridge
binds its additional embodiment outcome to that allocation without redefining
the workspace request. A completed space-only result does not pretend a session
was created; completing embodiment does not allocate the space a second time.

Record intent before allocation effects and completion before returning the
result. Recovery uses the recorded identity and the owner's actual effects;
it never repeats creation simply because the client lost its response.
Changed target facts under the same key are a conflict. A removed or retired
result remains terminal history, not permission to allocate again. A caller
chooses a new logical revision for deliberate replacement.

Dispatch derives creation identity from the task's deduplication identity
(or durable task identity when it has no deduplication key) and an explicit
allocation revision. A reservation attempt number, transport retry, or ordinary
task claim generation must not silently become a new allocation revision.
Per-attempt acquisition/delivery IDs remain separate: they fence prompt
admission without redefining the workspace or permitting a duplicate turn.

Ground creation accepts optional `--creation-key`; local worktree-backed
bridge creation accepts the same option through protocol 34's distinct
workspace-creation endpoint. Explicit dispatch allocation revisions likewise
use a non-fallback revision endpoint. Older or unsupported owners refuse
instead of ignoring a safety-affecting option. Existing unkeyed standalone
creation keeps its deliberate fresh-create behavior; remote/fleet keyed
embodiment remains explicitly unsupported.

## Refusal and unknown outcome are different

Only a positively proven pre-effect failure may report `not_started`.
A deterministic invalid execution target follows dispatch's bounded failure
path. Unknown or interrupted effects retain the same workspace and consult the
owner's durable acquisition history; a deferral does not release allocation
identity or grant another create.

No timestamp, missing response, empty directory, unset head, or absent local
receipt alone proves that a previous conversation was never launched.
Authoritative sessionless creation history permits a first conversation;
existing native identity or ambiguous effects require continuity/reconciliation.
Missing optional capabilities fail explicitly and do not change execution mode.

## Connected validation

Exercise the public allocation/acquisition boundary, not only mocked factories:
different queue and execution repositories must still produce one target
workspace; replay and concurrent callers must allocate once; interrupted
responses must recover that result; conflicts and terminal keys must refuse;
and a first conversation in a positively sessionless existing worktree must
retain the same project/path. Preserve refusal for foreign/live holders,
missing legacy history, and ambiguous launch effects.
