# agent-chat-service

Dependency-free conversation state and callable RPC core for
[agent-chat graduation](../../efforts/active/agent-chat-graduation/README.md).
This first distribution provides durable channels, membership, events,
independent cursors and bounded notification metadata.

**It does not start a listener or daemon, acquire credentials, discover a
foreground session, install a plugin or implement managed service lifecycle.**
HTTP/Gateway hosting, the installed user-facing client, native attachment and
fleet-manager integration remain separate implementation slices. A working
core is not evidence of those capabilities.

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
| `members` | `channel` | none |
| `resolve` | `codename` | none |

Actor metadata includes `codename` and optional `machine`, `environment`,
`repo`, `worktree_id`, `session_id` and `bridge_handle`. Names and recorded
handles are cooperative metadata, not authentication or current liveness.
A transport must enforce its real access boundary before dispatching a request.
Credentials do not belong in the store. A workspace-aware client must establish
and settle real native membership obligations; this library does not simulate
those claims.

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
