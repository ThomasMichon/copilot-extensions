# agent-chat-service

Dependency-free conversation state and a callable RPC core, with explicit
installed client and local hosting roles for
[agent-chat graduation](../../efforts/active/agent-chat-graduation/README.md).
The distribution provides durable channels, membership, events, independent
cursors and bounded notification metadata. The optional `client` role adds
the canonical process helper for owned credential/stdio provider execution;
the optional `service` role supplies the foreground local host.

**Default imports do not start a listener, acquire credentials, discover a
foreground session, install a plugin or implement managed service lifecycle.**
Credentials are obtained only by an explicitly invoked client RPC through its
configured provider. Managed installation/cutover, Gateway adaptation, native
attachment and fleet-manager integration remain separate slices. A working
client or foreground host is not evidence of those capabilities.

## Callable core

Install the distribution with the chosen package manager. Runtime dependencies
are only Python's standard library; Copilot and a source checkout are not needed.
The caller supplies its owned data directory and nonsecret installation identity.
Use one owning store instance per directory, and close it during owned cleanup.

```python
from agent_chat_service import ChatStore, json_bytes, strict_json

store = ChatStore("/path/to/owned/data", "example-installation")
try:
    joined = store.dispatch({
        "op": "join", "channel": "room",
        "actor": {"codename": "agent-a", "machine": "example-host"},
    })
    store.dispatch({
        "op": "post", "channel": "room", "codename": "agent-a",
        "request_id": "example-request-1", "text": "A bounded conversation.",
    })
    page = store.dispatch(strict_json(
        b'{"op":"read","channel":"room","codename":"agent-a","limit":10}'
    ))
    print(json_bytes(page).decode("utf-8"), flush=True)
    store.dispatch({
        "op": "cursor", "channel": "room", "codename": "agent-a",
        "expected_cursor": page["cursor"], "next_cursor": page["next_cursor"],
    })
finally:
    store.close()
```

The path is illustrative: resolve an actual owned directory through the
adopter's normal configuration. The library does not select a user home or
create another ownership registry.

## Explicit local host artifact

The distribution's `service` extra adds only the canonical
`agent-single-instance-lease` and `agent-endpoint-rendezvous` libraries. It
requires neither Copilot, another service, a source checkout, a sibling runtime,
Docker nor a process-provider helper. Installing the core without that extra
does not acquire those dependencies; attempting to host reports
`host_service_dependencies_missing` without creating data or provisioning them.

The deployment owner installs that role through its selected artifact flow.
Its **internal foreground entry point** is:

```text
<selected-interpreter> -I -m agent_chat_service.hosting --config <absolute-host.json>
```

The installed `agent-chat-service` console is equivalent. This is the executable
an existing service lifecycle owner embeds or supervises, **not a replacement
install/update/start/stop interface**. Package-manager installation and a
running foreground process do not establish managed installation, survival of
an installer's cleanup boundary or health-gated activation. The existing
standard lifecycle and Worktree Manager owners retain those contracts;
fleet-manager integration enhances them rather than becoming mandatory for
standalone use. There is no new installer, scheduler, self-updater or HTTP
administration route in this artifact.

Configuration is strict bounded nonsecret JSON:

```json
{
  "schema": "agent-chat.host",
  "version": 1,
  "data_dir": "/path/to/owned/chat-data",
  "installation_id": "example-installation",
  "token_file": "/path/to/owned/private-access-key"
}
```

Supply actual absolute paths for the host platform. The deployment's existing
credential owner supplies a private regular file containing 32-1024 printable
ASCII bytes, with an optional surrounding newline. The host never generates,
prints, acquires, persists or rotates a credential. POSIX key permissions must
exclude group/other access; Windows requires the deployment owner's private
file ACL. Treat key possession as access to this one cooperative conversation
domain, not participant identity or permission to control native sessions.
Actor metadata remains metadata. A cross-host Gateway adapter must retain its
own real authentication/authorization; this local key is not a substitute for it.

Local endpoints follow the existing transport ladder:

- **POSIX:** HTTP over a Unix socket in an owner-private `data_dir/run`.
  No network port is opened. The owned socket uses a fresh name, avoiding
  deletion of a preexisting socket after a crash.
- **Windows:** the temporary standard-library HTTP fallback binds
  `127.0.0.1:0` only. A reusable named-pipe/HTTP bridge is not supplied by this
  slice; the native pipe remains the target. No fixed port, remote bind, tunnel,
  public listener or firewall change is supported.

Both endpoints require the external key on **every** request, including health.
The host publishes its actually bound endpoint through the canonical
`endpoint-rendezvous` file at `data_dir/run/endpoint.json`, and emits one
nonsecret `agent-chat.host-ready` descriptor on stdout. The existing lifecycle
owner supplies this explicit run directory to clients; no PATH search or ambient
endpoint fallback is introduced. The client can consume that canonical record
through the explicit `endpoint_dir` mode below. Source support is not native
platform qualification or a selected managed installation.

Only `POST /api/v1/chat` and authenticated `GET /health` exist. Busy health
probes return the core's structured HTTP 503, not a disconnected connection.
There are at most 32 live
request workers, including partial headers and bodies; excess connections close
before creating another worker. Aggregate request line/headers are bounded to
16 KiB and bodies to 64 KiB, with no chunking, ambiguous length or keep-alive.
An absolute five-second ingress deadline closes slow-drip headers/bodies;
socket inactivity alone is not the deadline. Output has its own five-second
deadline and one-MiB cap. The store's existing 16-waiter pool and 300-second
long-poll maximum remain unchanged. Held waits do not monopolize health or
ordinary RPC admission. Request logs never reflect credentials or bodies.

`HostedChat(config)` acquires the canonical single-instance lease **before**
opening the store. Its `serve(stop_event)` has no child process or detached
helper. The embedding owner must stop that serving loop before closing the
host. `drain()` rejects new RPCs, releases held waits through the existing store
generation fence and reports health as nonaccepting. `close()` drains and closes
storage at the committed-transaction boundary, joins bounded request workers,
removes only the owned endpoint/socket and then releases custody. A timed drain
failure still waits for storage retirement and worker cleanup before releasing
the lease, then surfaces that original failure. Other incomplete cleanup retains
custody; it is not a successful close. The foreground entry handles normal
SIGINT/SIGTERM this way where delivered by the platform. Forced Windows process termination is a
crash, not graceful drain; the installed qualification checks durable recovery
separately. No reader cursor, pending client request, data or membership is
erased during stop/restart.

This is one active writer, not overlapping active/passive data ownership.
Passive candidate admission, compatible storage selection, stable launcher,
health-gated route commit, rollback and post-caller health remain the existing
managed-lifecycle integration under #6424/#6240. Do not infer those capabilities
from the host's lease, health response or endpoint descriptor.

## Installed client role

Install the selected distribution's `client` extra through the adopter's normal
verified artifact/role flow. It adds the standalone `agent-chat` console command
and the canonical `agent-procutil` and dependency-free
`agent-endpoint-rendezvous` dependencies; it does not provide an installer
or change an existing selected client. No Copilot, source checkout or sibling
runtime is required. An older helper without `owned_process.run_caller_owned` reports
an explicit unsupported capability rather than falling back to unowned execution.

Provider and HTTP-worker execution uses the published caller-owned utility on
Windows and supported native Linux. Unsupported OS/custody prerequisites refuse
visibly; no legacy or unowned fallback is used. Windows evidence alone is not
positive Linux client qualification. Offline catalog/status/capability operations
and the callable core do not invoke that utility.

Separate streaming raw-byte limits apply before decoding: RPC stdout is 1 MiB,
HTTP-worker stdout is 1 MiB plus its 512-byte control allowance, credential
stdout is 32 KiB plus a possible CRLF, and each stderr is 64 KiB. Overflow
returns no partial result and never retries. The canonical utility confirms
owned-tree cleanup before reporting an output limit. An `OwnedCleanupError`
instead propagates unchanged with its exact process/Job custody; it is not
translated into an ordinary recoverable provider failure or permission to replay.

On POSIX, new reader namespaces and pending-post directories are created with
owner-only access. Atomic pending-body files are created exclusively with
mode `0600` before writing, including under a permissive umask; replacement
retains that mode. Existing directory permissions are not silently changed.
Use an owned state directory with appropriate account ACLs on Windows.

All network operations require an explicit nonsecret configuration:

```json
{
  "schema": "agent-chat.client",
  "version": 1,
  "endpoint": "https://conversation.example",
  "state_dir": "/path/to/owned/client-state",
  "actor": {"codename": "agent-a"},
  "token_command": ["/path/to/owned/credential-provider", "get-token"]
}
```

Paths are illustrative; supply actual absolute owned paths appropriate for the
platform. Provider argv must name a complete native executable/interpreter
invocation, not a relative command or implicit shell/script launcher.

For the independently hosted local service, replace `endpoint` with
`"endpoint_dir": "/path/to/owned/chat-data/run"` and retain the explicit
`token_command`. Exactly one origin or local run directory is required; a
fixed-route `request_command` cannot be combined with local discovery.
The existing credential owner still supplies the local access key through that
provider. The client does not read the host's key file, mint a credential,
install or start the host, or acquire a different authentication identity.

Each local RPC reads the canonical rendezvous record inside the same finite,
isolated HTTP worker used for ordinary HTTP. Reading is capped at 16 KiB;
linked/reparse paths, nonregular records, malformed or unsupported records and
nonlocal TCP destinations refuse explicitly. The canonical stale-publisher
check runs on the original advertisement before selecting a primary or
alternate transport; a known-dead publisher refuses without dialing.
TCP records require their advertised port and never default to port 80.
Native POSIX uses the advertised
Unix socket directly beneath the owned run directory; Windows uses the host's
advertised dynamic loopback endpoint. A named pipe without an explicitly
advertised supported alternate is unsupported. There is no fixed-port,
environment, source-checkout or network-discovery fallback, retry, or endpoint
liveness claim from file existence alone.

The logical local client namespace binds the explicit run directory, not its
current port/socket name, so endpoint rebinds preserve reader serialization and
uncertain-post custody. Pending requests remain held until an explicit retry
under their original ID; reconnecting does not automatically send them.
Changing an existing origin-based configuration to this local mode is **not**
an implicit migration of that origin's pending namespace. Retain and reconcile
the old configuration's uncertain requests before an explicitly owned change.

`token_command` returns an ASCII credential on stdout, within its deadline;
stderr is not exposed as protocol output. Credentials are not written to state,
messages or diagnostics. The client uses HTTPS or an existing owned loopback
route, keeps TLS verification enabled, and follows neither redirects nor ambient
HTTP proxy variables. The fixed RPC path is `/api/v1/chat`.
An on-demand, canonically owned worker bounds the entire HTTP operation,
including DNS, connect, TLS, headers and body, to 15 seconds for ordinary RPCs
or the requested long-poll duration plus 15 seconds. Continuing trickle traffic
cannot extend that total deadline. Credentials and requests reach this finite
worker only over bounded stdin, never argv, environment or credential files.
The worker is not a resident service, and deadline failure does not retry.
Its Python invocation explicitly preserves isolated mode, excluding workspace
and `PYTHONPATH` package substitutions even when the parent is already isolated.

An explicit `request_command` instead of `token_command` delegates that fixed
loopback route to an existing owned stdio provider, using the version-1
`agent-chat.gateway-request` / `agent-chat.gateway-response` envelopes. The two
provider modes cannot be mixed. Provider processes use the canonical native
owned execution helper, never a daemon or sibling-runtime import. Cross-OS
provider execution is unsupported in this slice; direct Windows executables
on POSIX and `wsl.exe` refuse before launch. Native caller ownership does not
establish cross-kernel descendant cleanup; that remains separate utility work.

```text
agent-chat --config client.json join room
agent-chat --config client.json post room - --request-id example-request-1
agent-chat --config client.json read room --limit 10
agent-chat --config client.json wait room --timeout 300 --batch-ms 250
agent-chat --config client.json members room --limit 100
agent-chat --config client.json members room --limit 100 --after last-codename
agent-chat --config client.json status
agent-chat --config client.json leave room
agent-chat scan --describe
```

`post ... -` accepts bounded UTF-8 text on stdin. Each explicit post needs its
original stable request ID. Before transport, the client writes and flushes that
request. Lost, mismatched or uncertain responses retain it; no automatic retry
or fresh-ID replay occurs. Explicit owner recovery can retry the same original
ID/content under the server's deduplication contract. Changed retained content
refuses locally. A later refusal does not erase a previously uncertain request.
At most 128 pending records are admitted per transport/viewer namespace;
capacity refusal preserves them. This is not a total filesystem-usage guarantee.

`read` serializes local viewer operations with a crash-released OS lock. It
validates the complete page, writes and flushes bounded UTF-8 JSON, then sends
cursor CAS. Failed output never advances progress; an unconfirmed CAS reports
that the printed batch may replay. Independent viewers have independent progress.
`scan --request-file -` accepts bounded version-1 controls, validates body-free
returned-only positions and never calls semantic read or cursor advancement.
`members` and `resolve` return recorded metadata, not verified liveness.
`members --limit` / `--after`, or `Client.members(channel, limit=..., after=...)`,
explicitly opt into the bounded pages described in the RPC contract below.
The client validates page size, ordering and continuation without reading or
advancing any semantic cursor. Calls without page controls retain the ordinary
legacy response shape; no automatic enumeration loop is introduced.

`status`, `scan --describe` and `--version` do not invoke providers or contact
an endpoint. Status reports local pending IDs, not bodies or remote readiness.
There is no implicit home, endpoint discovery, automatic join, wait/rearm loop
or subscription activation. Actor metadata is not authentication or a native
claim: a connection/native owner must establish real obligations before join
and confirm remote leave before settling them. This client creates no replacement
claim database and cannot certify native listening or finalization readiness.

### Offline installed invocation catalog

Invoke `catalog` through the adopter's explicitly chosen installed console or
interpreter, not a command found by searching `PATH`:

```text
<chosen-interpreter> -I -m agent_chat_service.cli catalog
```

The version-1 `agent-chat.client-catalog` returns the current distribution
identity, SHA-256 wheel RECORD evidence for all eight packaged Python modules
(including `hosting.py` and `workspace.py`) and one exact
`agent-chat` argv. That argv preserves the current interpreter, including a
virtual environment's interpreter spelling, and uses isolated module invocation.
Source-only, editable, user-site-only, mismatched, redirected or incomplete
installations refuse instead of advertising an unusable isolated invocation.
The command does not read client configuration, run a provider, contact a
service or write installation state.
Verifying the host module's bytes does not import its optional dependencies,
start a listener or establish that the service role is installed or ready.

This is attributable **local installed-resource evidence**, not authenticated
release selection, an installer receipt or proof that executable dependencies,
the remote service or native subscriptions are ready. The caller still chooses
and trusts the initial installation through its existing lifecycle owner.
RECORD checks are not a security boundary against a writer that can alter the
installation and its metadata together. Ordinary explicit wheel/package-manager
setup remains available; receipt-owned managed setup/selection is still the
existing fleet lifecycle dependency under #6424/#6240. A connection plugin must
not turn this catalog into a competing installer or silently change selection.

### Optional trusted workspace context

The optional client API
`agent_chat_service.workspace.resolve_workspace(worktrees_command=..., session_id=..., cwd=...)`
consumes a host's trusted initialization session ID and absolute working
directory. The host captures those inputs once. The resolver neither reads an
environment session ID nor repeatedly compares foreground metadata, imports
Worktrees source, searches PATH/catalog files, installs anything or changes
the caller's working directory.

For an installed command boundary, invoke the catalog's exact client argv with
`workspace --request-file -` and send one bounded JSON object on stdin:

```json
{
  "schema": "agent-chat.workspace-request",
  "version": 1,
  "session_id": "example-startup-session",
  "cwd": "/absolute/host-initialization-directory",
  "worktrees_command": ["/absolute/selected-interpreter", "-I", "-m", "agent_worktrees"]
}
```

Use real platform-native absolute paths and the complete executable/interpreter
argv supplied by the adopter's existing command-selection owner. A shell script
path alone is not executable argv; its selected interpreter must be explicit.
The client uses its canonical caller-owned process helper, isolated finite
children, one 60-second overall deadline, 64-KiB stdout and separate bounded
stderr. Provider failures, timeouts, inconsistent identity, untracked worktrees,
invalid paths and unsupported owner contracts are explicit errors, not an
anchor/standalone fallback. No owner output or stderr is reflected in errors.

The version-1 `agent-chat.workspace-context` reports a participant tuple
`{machine, repo, kind, id}`. For a real tracked worktree, `id` is the worktree
ID, stable across startup sessions; `display_name` is the separately observed
friendly codename, or null when absent. The selected Worktrees command must
support `record --cwd <absolute-directory> --json`, with the version-1
`agent-worktrees.workspace-context` contract. This is one bounded owner read:
the owner checks the actual registered project and existing tracking record,
then projects identity and asserted head from the same strict record read.
The client validates project, path, execution tuple, label and head without
calling legacy enrolling inference, resident-backed `list`, or the global
`head-session` lookup. Versioned execution-space metadata must be complete
and platform-consistent; physical-host naming never replaces its execution owner.
For an anchor checkout, `kind` is `anchor` and `id` is the startup session ID;
the owner's included versioned configured execution-identity contract supplies
machine/repository authority. There is no synthetic managed-worktree owner or
head lookup. An owner without that anchor capability refuses explicitly;
ordinary tracked worktrees do not require it.

For worktrees, `head` reports the existing owner's session ID, revision,
initializing-session match and pending-handoff observation. It never asserts
or changes a head, reconciles succession, or grants a later mutation. The
observation is not a lease and may become stale; native admission remains its
owner's responsibility. `owner_ref` is null for anchors.

This is **workspace resolution only**: `assignment.status` remains
`unavailable` with reason `chat-assignment-binding-required`. The result
deliberately contains no Chat actor, inbox or eligible-source assignment.
It does not join, rename or migrate retained membership, history, cursors or
uncertain effects, and both `mutations_performed` and
`native_listening_verified` are false. The existing local service/Bridge owner
still owes authoritative assignment and shared notification intake.
Ordinary explicit-actor commands and offline catalog/status/capability paths
remain independent of Worktrees, Copilot and this optional operation.

## RPC contract

`dispatch` accepts one strict operation object. Unknown fields, invalid types,
ambiguous JSON and violated bounds raise `ChatError`, carrying `code`,
`message`, a suggested HTTP `status` and `as_dict()`. The core itself is not an
HTTP server. `strict_json` bounds and validates untrusted serialized input;
its default remains 64 KiB, with an explicit bound up to 1 MiB for client replies.
`json_bytes` emits finite UTF-8 JSON.

| Operation | Required fields besides `op` | Optional fields |
|-----------|-----------------------------|-----------------|
| `join` | `channel`, `actor` | none |
| `leave` | `channel`, `codename` | expected `actor` |
| `post` | `channel`, `codename`, `text`, `request_id` | none |
| `read` | `channel`, `codename` | `limit` |
| `cursor` | `channel`, `codename`, `expected_cursor`, `next_cursor` | none |
| `wait` | `channel`, `codename` | `timeout`, `batch_ms` |
| `scan` | `codename`, `epoch`, `after` | `limit`, `timeout`, `batch_ms` |
| `members` | `channel` | `limit`, `after` |
| `resolve` | `codename` | none |

Actor metadata includes `codename` and optional `machine`, `environment`,
`repo`, `worktree_id`, `session_id` and `bridge_handle`. Names and recorded
handles are cooperative metadata, not authentication or current liveness.
A transport must enforce its real access boundary before dispatching a request.
Credentials do not belong in the store. A workspace-aware client must establish
and settle real native membership obligations; this library does not simulate
those claims.

An ordinary `members` call retains the original `{"channel", "members"}` full
enumeration in the callable core. For wire-bounded enumeration, explicitly
provide `limit` (1-100) and/or `after` (the last returned codename). A paged
response adds boolean `more` and nullable `next_after`; continue only with
that returned codename until `more` is false. The default paged limit is 100.
These pages bound even all 256 channel members with 4096-byte actor metadata
inside the one-MiB HTTP budget. An oversized unpaged HTTP reply refuses with
`capacity`/409 and directs the caller to pagination; it never silently truncates
or returns a generic 500. Normal-sized legacy replies are unchanged.
Membership pages are ordered current metadata, not a snapshot, semantic viewer
cursor, read receipt or native authority. Concurrent membership changes may
require a fresh enumeration. Existing clients need explicit page controls and
the paged response shape before consuming that optional contract.

Posts deduplicate by codename/request ID, binding the channel and text.
Identical replay returns the original immutable event with `duplicate: true`;
changed content refuses. Committed requests remain reconcilable after leave
without reactivating membership; new posts still require an active membership.
Reading returns a bounded page **without consuming
it**. An actual client writes and flushes output before the separate cursor
compare-and-set. Leave/rejoin preserves progress, and an expected-owner leave
refuses a conflicting source identity.

`wait` returns ID/count hints, not bodies or cursor advancement. `scan` returns
body-free metadata under `agent-chat.scan` version 1, independently of semantic
reading. Discovery uses `epoch: null` and zero positions; continuation supplies
the returned epoch and explicit per-channel event positions. Each position is
`{"channel": "room", "event_id": 0}`. Only returned events advance that channel's
scan position. Epoch mismatch and unavailable positions require explicit
recovery. A native adapter persists pending hints before scan progress; it
does not call semantic read to prepare a notification.

## Bounds and persistence

Serialized requests are limited to 64 KiB; message text to 8 KiB UTF-8;
request IDs to 128 bytes; actor metadata to 4 KiB. Pages contain at most
100 events and remain within 1 MiB even with supported JSON escaping.
Scans accept at most 16 distinct channels. Waits/scans share a finite waiter
admission pool, support at most 300 seconds and a 5-second batch window, and
release the store transaction while waiting.

Held waits and scans are fenced by instance-local membership generations,
not wall-clock timestamps. A committed leave invalidates the generation even
if rejoin records the same `joined_at`; failed leave transactions and idempotent
active joins do not. Held requests cannot survive store restart, so this fence
adds no persistent schema or migration and does not support multiple owning
store instances for one directory.

Constructor defaults limit events, text bytes, channels, actors and memberships.
Text-byte quota is not a SQLite file-size or total disk-usage guarantee.
Inactive memberships still consume capacity. Capacity refusal does not prune
unread history or prevent leave/cursor settlement. The database uses full
synchronous WAL transactions, preserves installation identity and schema, and
adds a durable log epoch to compatible existing state. Storage migration and
deployment admission remain the lifecycle owner's responsibilities.

`health()` observes the store's local open state under its lock. A closed store
refuses instead of reporting a healthy service. Health is not authentication,
network reachability, storage capacity, future write readiness, process
ownership or native listening proof.

## Hosting-owner drain boundary

The callable `ChatStore.drain()` / `undrain()` methods supply the conversation
domain's safe boundary to an existing service/lifecycle owner. They are not RPC
operations, management endpoints, a listener, an installer or another supervisor.
The embedding host must authenticate any management surface it exposes.

`drain()` serializes behind the store's current SQLite transaction, then refuses
every new valid RPC with `ChatError("draining", ...)` and suggested HTTP status `503`.
It wakes and invalidates held scans/waits without advancing semantic cursors,
changing memberships or discarding committed post/request identities.
Its result reports `state` (`draining` or `drained`), `active_waiters`,
`storage_schema` and `installation_id`. Repeat calls preserve the admission
freeze and report the current waiter count; `drained` means the store has no
admitted wait/scan still unwinding and its prior transactions have committed
or rolled back. It does not attest that an embedding HTTP handler has flushed
its response or that a remote peer received it. The host still owns transport
draining and lost-response reconciliation.

`undrain()` reopens admission for the current instance, but never revives a
wait from a drained generation, even during a rapid drain/undrain rollback.
The existing `health()` remains an open-store/liveness observation, not serving
readiness. Draining is instance-local; reopening the preserved store starts
active and retains its durable epoch, events, stable requests and viewer state.
An authorized lifecycle owner must therefore retain its own activation policy.
Use one owning store instance per directory: these methods do not make
overlapping writers or schema migration safe. A passive candidate must not open
the active store to manufacture a health result.

## Validation

Use the repository's contained standalone runner:

```text
python tools/run-standalone-tests.py services/chat --prepare
```

Portable contracts cover real SQLite state, independent progress, atomic
deduplication, failure rollback, bounded pages, waiter cleanup and metadata
scans. Client contracts exercise real credential-provider/HTTP/core/SQLite
round trips, output/flush/CAS, retained uncertainty and actual CLI death with
lock release observed before releasing the held response or outer fixture
cleanup. Artifact qualification builds
the distribution and its declared canonical helper, installs the real `client`
extra offline into a fresh environment, removes both fixture sources, then
uses the actual installed console for join/post/read/leave and forced console
termination/recovery. The default core is
also exercised without the helper dependency. This proves source-independent
package use, not a version-slot installer, hosted service or live native SDK
adoption. Test credentials and local authorization fixtures are synthetic,
not live JWT or production evidence.

- [Standing vision](../../visions/plugins/agent-chat/README.md)
- [Architecture proposal](../../efforts/active/agent-chat-graduation/architecture.md)
