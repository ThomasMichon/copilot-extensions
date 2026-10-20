# agent-chat-service

Dependency-free conversation state, a callable RPC core and an explicit local
hosting role for
[agent-chat graduation](../../efforts/active/agent-chat-graduation/README.md).
The distribution provides durable channels, membership, events, independent
cursors and bounded notification metadata.

**Default imports do not start a listener, acquire credentials, discover a
foreground session, install a plugin or implement managed service lifecycle.**
The optional `service` role provides the foreground host below. Managed
installation/cutover, Gateway adaptation, the installed user-facing client,
native attachment and fleet-manager integration remain separate slices.

## Use

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
owner supplies discovery to clients; no PATH search or ambient endpoint fallback
is introduced. The held client slice's HTTP-only endpoint selection still needs
the explicit Unix-socket consumption seam before direct POSIX client adoption.

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

## RPC contract

`dispatch` accepts one strict operation object. Unknown fields, invalid types,
ambiguous JSON and violated bounds raise `ChatError`, carrying `code`,
`message`, a suggested HTTP `status` and `as_dict()`. The core itself is not an
HTTP server. `strict_json` bounds and validates untrusted serialized input;
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
scans. Artifact qualification builds a wheel, installs it into a fresh target,
removes the fixture source and exercises the installed core with an isolated
Python process. This proves source-independent package use, not a version-slot
installer, hosted service or live native SDK adoption.

- [Standing vision](../../visions/plugins/agent-chat/README.md)
- [Architecture proposal](../../efforts/active/agent-chat-graduation/architecture.md)
