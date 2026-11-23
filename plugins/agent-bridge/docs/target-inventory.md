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
Workspace linkage requires the managed controller's owner, project, exact path
and local/SSH transport to match; a bare shared handle never supplies that proof.

Listing starts no native process, sends no prompt and hydrates no metadata.
Workspace discovery uses the existing app-owned cache and cold-crawl behavior;
`refresh=true` joins its bounded refresh. Stale or incomplete workspace evidence
is retained verbatim. Missing or invalid Host inventory is explicitly unavailable,
not an empty source. Local Host liveness is observed; remote liveness remains
unverified until its owning transport qualifies it.

On first startup, the process that creates the Host directory writes an empty
index under the Host index lock unless another writer has already created it.
This lets the first `create <project>` proceed when the other sources are also
complete and no target matches. If the directory already exists, startup does
not replace a missing or invalid index. Listing targets never repairs it.

## Conversation inventory

The optional `bridge_inventory: 1` health capability adds
`GET /api/v1/bridges` and `BridgeClient.list_bridges(...)`.
The new `list` command selects this version-1 response with `bridges`, `sources`
and `complete`. It requires the caller's project, explicit `--project REPO`, or
explicit `--all-projects`. `--machine`, `--namespace`, `--status` and `--live`
filter the same projection. It does not ensure the Bridge service.

The projection includes session-backed dormant worktrees, managed conversations,
extension registrations and standalone Hosts observed by the existing inventory.
Empty worktrees remain potential targets, not conversations. Dormant anchors
stay out of this default view. Assignment, liveness and control remain separate:
a running workspace can have `control: unavailable`, and an unavailable source
does not become a complete empty result. Conflicting native identities make
control unavailable rather than selecting a head by arrival order.
Title, disposition and creator/controller fields use cached owner metadata.
Listing never reads each conversation's transcript or imports native history.
An observed workspace supplies its owning project, even when the native
registration names a member repository. Explicitly conflicting owners remain
separate. Missing liveness, hosting and transport evidence remains unknown.

Select remote Bridge hosts explicitly with repeatable `--host`:

```text
agent-bridge --project example --json list --host host-a --host host-b
agent-bridge live-sessions list --all-sources --host host-a --host host-b --json
```

The latter selects only observed live conversations across all projects,
including managed ACP conversations and standalone Hosts. Existing
`live-sessions list` output and its registration-only JSON array are unchanged.
`--all` and `--supervisor` retain their registration-only meaning and cannot
be combined with the new source-union selection.

Host selection is bounded to 16 entries, with at most four concurrent reads and
a 15-second deadline per host. The remote `bridge.inventory` carrier operation
requests only that host's local contribution; it never recursively enumerates
the fleet. Every result has a host-qualified `route`; repeated observations of
the same scoped conversation retain alternate `routes`. The response preserves
each selected host's source evidence. An old carrier, an old daemon, a timeout
or unavailable metadata sets `complete: false` without dropping other hosts'
positive observations. No host names are inferred from personal conventions.

This is an additive discovery contract, not selector or lifecycle migration.
Existing `sessions`, `targets` and registration APIs retain their shapes and
effects. New clients require the executing daemon's `bridge_inventory: 1`
capability. Unsupported new reads refuse explicitly; rollback leaves existing
commands available. A supported carrier that lacks `bridge.inventory` reports
an unsupported operation for that host. There is no fallback to process
launch, receiver enrollment, service recovery or message submission.

### Exact conversation inspection

`show ID` and `BridgeClient.resolve_bridge(ID, ...)` select a single projected
conversation through the existing `bridge_inventory: 1` read. They accept an
exact workspace handle, native session ID, registration ID or current/retained
managed ID exposed on that row. They do not use substring, profile or
potential-placement matching. Project/CWD scope and explicit `--all-projects`,
`--machine`, `--namespace` and repeatable `--host` selectors have the same meaning
as `list`.

Fleet reconciliation retains `identity_aliases` for managed and registration
IDs whose observed native session matches the selected head. Routes retained
from an older head are not identity aliases.

The version-1 JSON response contains `bridge`, `sources` and `complete`.
Multiple matching conversations or conflicting observed native identities
refuse with `ambiguous_bridge_selector` (409). A missing match is
`bridge_not_found` (404) only for complete evidence; otherwise it is
`bridge_inventory_incomplete` (503). A positively observed single conversation
retains incomplete source evidence, unknown liveness or unavailable control.
This is inspection, not authority to execute its cached route. Effectful callers
must still use the owning operation's current identity and admission checks.

The provider expansion already supplies these identities. This first shared
selector consumer adds no prerequisite RPC or capability beyond the negotiated
inventory read. A provider without that capability refuses before inspection;
legacy commands remain available on rollback. `send`, `read`, `wait`, `resume`,
`stop` and `end` keep their existing selectors in this slice. No previous form
is deprecated or removed.

The [contained source runner](../../../TESTING.md) can select
`tests/test_bridge_inventory.py`, `tests/test_bridge_listing_cli.py` and
`tests/test_bridge_fleet_inventory.py` with `--allow-explicit-tiers`.
The packet uses synthetic five-host observations and private registry fixtures.
It exercises the actual CLI/client/API serialization and unsupported-capability
refusal without native execution. It does not certify installed fleet adoption.

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
New imports require the exact record's `execution_leg_live` to be explicitly
false. Active, unknown or omitted custody evidence refuses. The exact record's
validated `bound_agent`, not its cached discovery value, supplies the launch
profile. A member repository's name may differ from the owning project label;
the existing exact-record owner and path qualification retain that boundary.

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
