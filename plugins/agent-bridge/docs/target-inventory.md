# Target inventory and metadata hydration

Back to [local session acquisition](session-acquisition.md).

The optional `target_inventory: 1` health capability adds
`GET /api/v1/targets` and `BridgeClient.list_targets(refresh=False)`.
The version-1 response contains `targets` and independent `sources` observations.
It projects the union of active managed sessions, registered Session Hosts,
extension registrations and every Worktrees-listed workspace. The legacy
`/api/v1/sessions` managed-session response remains unchanged.

Each target has its actual `kind`, `handle` and source names. Managed targets
carry `session`; Host and extension targets carry `registration`; workspace
targets carry `owner`, `project` and the existing `worktree` view. A workspace
handle is not a fabricated managed session ID. A workspace without a native
head or managed row remains visible. Different owner/project/machine/path
observations are retained, not collapsed by a friendly name or native ID.
Consistent Host and managed observations of the same control record share one
entry without converting a live extension transport into an ACP controller.

Listing starts no native process, sends no prompt and hydrates no metadata.
Workspace discovery uses the existing app-owned cache and cold-crawl behavior;
`refresh=true` joins its bounded refresh. Stale or incomplete workspace evidence
is retained verbatim. Missing or invalid Host inventory is explicitly unavailable,
not an empty source. Local Host liveness is observed; remote liveness remains
unverified until its owning transport qualifies it.

## Metadata only

The separately negotiated `worktree_metadata_hydration: 1` capability adds
`POST /api/v1/worktrees/{id}/metadata` and
`BridgeClient.hydrate_worktree(id, request_timeout=None)`.
It reads the exact workspace's authoritative native head and incarnation,
then imports dormant metadata through the existing native owner. An original
keyed journal instead uses its
[retained metadata-adoption owner](retained-acquisition-writer.md#metadata-loss-without-native-conversation-loss).
The original native conversation, keyed exclusions and explicit history gap
remain intact.

The response contains the real SessionInfo plus
`metadata_hydration: {version: 1, native_execution: false}`. A new control record
is stopped, with automatic recovery disabled. The operation never loads native
execution, drains a queue, sends a prompt or accepts a task. Repeated calls and
lost replies discover the committed control. Another frontend with stale
in-memory ownership refuses rather than duplicating that persisted record.

Changed or ambiguous heads/workspaces, pending handoffs, live transport custody,
incomplete Host inventory and launch holds retain explicit refusals. A listed
workspace without a usable head is not permission to create another conversation.
Both Python operations qualify the actual receiver on each transport attempt.
Metadata POSTs are not automatically replayed after an unconfirmed response;
unsupported older receivers refuse before mutation. CLI target discovery,
normal read/send selection and Dispatch caller migration remain separate.
