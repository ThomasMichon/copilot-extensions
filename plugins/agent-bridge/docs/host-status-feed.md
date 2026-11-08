# Host status feed and coalesced subscriptions

The optional `host_status_feed: 1` capability on the **serving daemon's**
`GET /health` enables an authenticated, metadata-only retained host feed.
It is not a transcript, a command channel, permission authority or a fresh
attention snapshot. It closes the Bridge vision's selected-agent attention
subscription boundary; delivery receipts retain their separate meaning.

## Complete local flow

1. `PUT /api/v1/status/subscriptions/<id>` creates an idempotent subscription:

   ```json
   {
     "watcher": "exact-native-watcher",
     "agents": [{"host": "local", "session_id": "selected-agent"}],
     "events": ["question_pending", "permission_pending", "idle",
                "message_consumed", "stalled", "error"],
     "include_nested": false
   }
   ```

   The watcher is an exact registered local interactive native session with
   an existing inbox, not an alias or a newly created agent. Selection starts
   at the current feed head. Reusing an ID with a different selection refuses;
   delete explicitly before replacing it.
2. Authoritative owned events, represented native events, current native
   question snapshots and correlated root message consumption append status
   transitions. Replayed representation backfill does not create attention
   transitions. Nested transitions are explicitly marked and excluded by
   default. Payload text, prompts, tool arguments, question choices and
   permission correlation IDs are not copied.
3. The first matching unread transition admits **one** `notify`/`steer` message
   through the existing inbox. Its body points to
   `/api/v1/status/subscriptions/<id>/feed`. More transitions only extend the
   feed. The normal exact-native or retained-inbox consumer delivers the notice;
   this API adds no second model injection channel.
4. Read that feed with `after`, `epoch` and `limit`. Reads never move the
   subscription cursor. `through` is the scanned range, including unselected
   events; advance subsequent pages to it even when `events` is empty.
5. `POST .../<id>/ack` with `{"epoch":"<returned-epoch>","through":123}`
   explicitly acknowledges that range and rearms notification. Events
   concurrently committed above that range remain unread and receive a new
   coalesced notice. Repeated/non-forward acknowledgements are idempotent.
   Partial forward acknowledgement also rearms for the remaining unread range.
   Consuming or acknowledging the **inbox notice** is never feed acknowledgement.
   Service-generated notice consumption retains its inbox receipt but does not
   generate another status transition, preventing self/cross-watcher feedback.
6. `DELETE .../<id>` stops the selection. It does not withdraw an already
   admitted inbox message. Recreating the ID receives a new notice generation.

`BridgeClient.status_subscribe`, `status_feed`, `status_acknowledge` and
`status_unsubscribe` implement this flow with fresh capability discovery on
the selected endpoint. They pin each operation to that endpoint, disable
automatic effect retries, and refuse unsupported providers/rollback before
subscription or acknowledgement effects. Older callers retain existing APIs
and defaults. No package-version equality or new global HTTP version is needed.

## Retention and evidence

The existing Bridge SQLite database stores the feed, subscription cursors,
unread epochs and notice receipts. Feed reads survive daemon restart without
reading transcripts. Schema 44 adds independent tables without rewriting
inbox/session facts; writable prior readers preserve the additive tables.
A prior passive reader requiring its exact schema refuses explicitly rather
than adopting an incompatible candidate. Returning to the new provider
recovers retained status state.

The feed retains 4,096 transitions. Native event identity deduplication is
guaranteed within that retained range; historical backfill is excluded.
An evicted cursor, replaced epoch or future cursor returns
`409 status_replay_gap`, including the retained floor/head and reconciliation
action. Nothing silently resets or acknowledges that cursor. Reconcile with
the owning session detail APIs, then explicitly select/acknowledge a retained
range. Missing historical events are not reconstructed or claimed observed.

Historical question/permission/idle transitions are not assertions of current
attention. Native question detail remains in `/questions`, with its existing
freshness/terminal rules. `stalled` is only a fresh-registration silence
observation using the existing 90-second turn threshold, not permission to
cancel, resend or recreate a session. Stale/unavailable watchers retain unread
state with `notice_error`; no replacement watcher is selected.

## Shared remote source composition

Set an agent's `host` to an exact declared SSH environment. The local daemon
canonicalizes aliases and shares **one host feed carrier subscription per
remote daemon**, independent of the selected-agent/watcher count. The existing
authenticated persistent carrier and its bounded buffers own transport.
No SSH sweep, target poll subprocess, new daemon or transcript tail is added.

The additive read-only carrier operation `host.status` checks the serving
host's `host_status_feed: 1` before opening `/api/v1/status/events`.
Unknown carrier operations and old/rolled-back daemon capabilities refuse
explicitly; they never fall back to per-session polling or replay effects.
Remote support is established before creating a local remote selection.
New host selection starts at the remote head; restarts/reconnects resume the
durable host epoch/cursor. Import commits before advancing that source cursor;
redelivery uses retained event identity. Only the remote host's **local**
transitions are imported, never another host's imports.

`GET /api/v1/status/hosts` reports each selected source's availability and
last observation; a formerly available source without progress for 45 seconds
is `stale`, not healthy/idle. Backpressure or disconnect closes/releases the
subscription and reconnects with capped backoff. A replay gap, unsupported
generation or invalid stream remains explicit and does not silently skip
events. After detail reconciliation, explicitly
`POST /api/v1/status/hosts/<canonical-host>/reconcile` with
`{"accept_lost_history":true}` accepts the lost range and reconnects from the
current provider head. This operation is not a claim that the gap was recovered.

## Bounds, lifecycle and qualification

Limits are 128 subscriptions, 256 agents per selection, 1,024 selected-agent
entries in total, 16 remote hosts, six event kinds, 256 records per read and
4,096 retained transitions. At most 32 incoming host SSE readers page retained storage instead of
accumulating per-agent queues. The daemon's single status task retries pending
notices and observes selected local registrations every five seconds, with
event-driven wakeups for explicit acknowledgements and remote arrivals.
There is no per-event process/network launch. Insertion checks at most 128
bounded selections; stale remote progress is not turned into liveness.

Ordinary and restored/candidate serving paths use the same task and stop it
before database close. The explicit runtime-transfer path loads static route
definitions before its timed grants; ordinary client/lineage imports remain lazy.
Preparation starts an idle task
outside the routing fence; activation only wakes it. Passive candidates do
not start subscriptions or notices. Existing runtime/inbox admission still
fences retained effects across drain/cutover; schema preparation remains the
existing lifecycle owner's responsibility. Interrupted streams reconnect;
durable unread state is not a process-lifetime tail.

Synthetic first-touch database, HTTP/inbox and carrier/SSE parser tests qualify
source behavior. They do not prove an installed native extension, remote SSH
host, service cutover, release promotion or consumer adoption. Those require
separately authorized runtime evidence.
