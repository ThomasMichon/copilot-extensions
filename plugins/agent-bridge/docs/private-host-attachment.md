# Opt-in native Session Host attachment

`session_host.private_attachment.PrivateAttachment` is a host-owned component
for transferring a surviving child's frontend connection without using ordinary
reattach, index claims, PID liveness probes or process reaping. It is **not wired
into production launchers, automatic repair or full ADOPT**. Private runtime
snapshots can explicitly include the separately verified host observations and
session metadata described below; that does not grant attachment authority.
Existing Session Hosts retain their ordinary nonce-authenticated TCP behavior.

## Authority and lifecycle

An owning launcher explicitly constructs the component on the host event loop,
before **any** frontend has attached. A host's retained attachment epoch refuses
retrofitting a formerly attached legacy host, even after its socket disconnects.
The construction supplies the installation's routing root, opaque configuration
binding and session identity. The actual host process birth, principal, instance,
loop and retained loopback TCP server/socket graph bind the component.

The launcher independently registers each caller's native `PeerIdentity`, full
frontend `Endpoint`, operation and original `Deadline`. These are owner inputs,
not fields taken from a request. A request contains only a one-use grant.
Execution requires the exact native Unix/Windows channel type, same principal,
the channel's original live process evidence, and byte/type-exact agreement with
the installation's current active frontend publication under its routing fence.
Unknown peers, graph drift, expired budgets and replay refuse explicitly.

The existing `zdd` Unix socket and owner-only Windows named-pipe transports carry
the bounded private frames. `handle(channel, deadline)` serves one registered
request, using off-loop framing I/O and an owner-loop synchronous mutation.
It returns fixed, redacted refusal reasons. This component creates no native
listener and does not discover or authenticate a caller by TCP port or nonce.
The integrating launcher still owns endpoint creation, native accept/connect,
independent peer registration and complete channel cleanup.

## Separate grants

`RESERVE` creates a bounded staging ticket, not an attached frontend. Initial
reservation is allowed only for a fresh opted-in host with no previous owner.
It duplicates the native caller's original process reference while the caller
is alive. Later reservation requires a previously captured snapshot, a strictly
newer routed frontend generation, and **exit through the retained predecessor
reference**. Closing its control channel or copying its TCP socket is not exit.

The reservation also privately returns a `recovery_snapshot` and planned
`recovery_generation` before activation. That token is **not yet a snapshot or
permission to attach**. A successful activation binds it to the resulting host
attachment generation and the host's commit-time cursor. A coordinator that
retained the reservation receipt can therefore recover an owner that exits
after commit but before its next CAPTURE, including a lost activation reply.
Recovery still requires a fresh native grant, current routed frontend authority,
a strictly newer frontend generation and exit of the exact committed owner.
Uncommitted/discarded stages never make the token valid; a later successful
CAPTURE replaces it with the new capture's token.

`INSPECT_COMMIT` consumes a separate fresh native/current-route grant to query
that retained recovery token without issuing another CAPTURE or replacing it.
It returns a receipt only after the exact reservation actually committed and
while that routed native owner and its stream remain current and live. A
prepared, discarded, superseded or progressed token refuses. In wire-idle mode,
any ACP progress also refuses. A lost ACTIVATE reply remains uncertain until
this query succeeds; timeout or refusal is never translated into a commit.
`verify_host_commit` retains fresh native host custody and compares the result
with the original host birth/instance/session/context/endpoint, exact next
attachment generation, receiver publication, reservation token and unchanged
ACP stamp/cursors. This observation grants no SDK stream or database mutation.
CAPTURE and INSPECT_COMMIT additionally report the current frontend TCP peer
endpoint as `frontend_data_endpoint`. Opaque older receipts may omit it; an
idle receiver's activation cannot omit this exact connected-stream binding.

The additive TCP `ATTACH_PRIVATE` (`P`) message contains only the 64-byte ticket.
The opted-in host reads a fixed-size handshake within the reservation deadline,
then stages the stream without HELLO, ACP forwarding, ACK processing, timer
cancellation or displacement of the current owner. Ordinary ATTACH and unknown
tickets are refused in this opted-in mode; ordinary hosts remain unchanged.

A fresh `ACTIVATE` grant rechecks native peer, full active publication, original
reservation budget, staged transport, captured attachment generation and exact
predecessor exit. Only then does it publish the staged frontend and cancel
host-owned disconnect timers. Buffered child output keeps its existing sequence
identity; replay starts from the **host's current acknowledged cursor plus one**,
not a stale capture or a caller-supplied cursor. Stale frontend control frames
cannot mutate the newly adopted connection or arm its reap policy.

`CAPTURE` is separately granted to the current, live native owner. It returns
the host's native identity, instance and attachment generation, configuration
and session bindings, actual TCP endpoint/version, and acknowledged/high-water
cursors. The child PID is observational metadata, never signal authority.
Repeated capture invalidates the previous snapshot token. This preserves the
existing ACK semantics; it does not claim ACK means an independent database
commit. Host progress may continue after capture, and activation uses the
surviving host's later authoritative cursor.

Reservation expiry and host shutdown release only owned staged transports and
native reference duplicates. They do not terminate or restart the child. Failed
or cancelled preparations do not attach a frontend. Captured snapshots and
tickets remain private, in memory and on authenticated native channels.

## Evidence and remaining boundary

### Opt-in ACP wire quiescence

`PrivateAttachment(..., acp_wire_boundary=True)` additionally installs one
host-owned NDJSON/RPC observer before the host's reader starts or any frontend
attaches. No ordinary launcher enables it. The same CAPTURE, RESERVE and
ACTIVATE grants remain in use; there is no second control endpoint or capture
alias. Split and coalesced frontend writes are parsed without changing their
bytes. Requests in both directions retain separate integer/string ID spaces;
responses settle only the corresponding opposite-direction request.

In this mode, CAPTURE refuses outstanding prompt/configuration/permission/
elicitation or other RPC requests, partial messages, unmatched responses,
unsupported JSON and uncertain child-stdin delivery. Outstanding requests are
bounded to 128 per direction, IDs/methods to 512 UTF-8 bytes, and each message
or retained partial parser buffer to the existing host message limit. Unsupported
wire state permanently refuses this boundary with a fixed, redacted diagnostic;
ordinary byte-exact forwarding still continues. Fresh hosts are required:
the observer cannot be retrofitted over already-read traffic.

A wire-idle capture includes an optional typed `acp_wire` observation with
byte/message progress and the next observed integer client request ID, bound
to the same native receipt. Ordinary opaque host receipts omit this field.
RESERVE and ACTIVATE
recheck that exact stamp: completing a new RPC, or delivering another update,
after capture does not make the older capture safe again. A fresh successful
CAPTURE is required. Failed capture leaves the previous token intact but cannot
make it pass the unchanged-progress check. Initial reservation's recovery token
does not establish an ACP boundary; a subsequent successful CAPTURE does.

This is deliberately **wire-idle-only**, not running-turn continuation. The
SDK owns its pending Futures, handler tasks, request counter and parser in the
frontend, while the manager owns turn completion and persistence. The existing
adapter ACK follows a stream write/drain, **before** SDK parsing, reducer
completion or a database commit. Equal host ACK/high-water cursors therefore
prove only delivered wire bytes. This host stamp neither transfers SDK state nor
attests reducer/DB completion; the captured-client owner must still prove that
boundary and restore the actual session/preferences without initialize, load,
resume or reaping before nonempty foreground activation can be enabled.

### Explicit idle frontend staging

`AcpClient.start_streams(private_continuation=True, continuation_events=...)`
opts a new frontend into an owned NDJSON transport **before initialization**.
It uses the SDK's public message-transport seam with the same outgoing framing;
ordinary clients keep the SDK's existing byte-stream implementation. Retrofitting
an existing client is refused. The owner explicitly binds the session EventLog.

`capture_idle_continuation` requires a separately `VerifiedHostReceipt` with an
ACP wire stamp. Exact received/sent byte and message totals must agree with that
stamp, as must the SDK's next request ID. Buffered parser bytes, undrained writes,
pending requests, scheduled/running SDK handlers or observers, session-update
callbacks, outer client operations, running prompts, permission/elicitation
Futures, load/replay and out-of-turn timers all refuse. The SDK task census is
an explicit compatibility fence: missing or unsupported internals refuse rather
than assuming quiescence. A delivered notification is not a completed callback.
Preferences require actual advertised/read-back selections, not fresh settings.

The bounded private snapshot includes the existing ACP identity, effective
preference selections and advertised options, response/thought/tool reducers,
background/subagent attribution, completion markers and the EventLog's next ID.
No SDK Future, task, subscriber, stream or native handle is serialized.

`prepare_idle_continuation` compares that snapshot with retained native receipt
custody and the receiving EventLog's identity/cursor, restores fresh SDK objects
with the captured request counter, and leaves them **inert**. It sends no
initialize/new/load/resume call and starts no receive loop. Its transport rejects
both sending and receiving; `is_running` remains false. Closing it releases only
its own frontend streams, never the surviving child.

Staging alone deliberately provides **no activation or manager admission**.
The separate explicit owner below composes native commit reconciliation and
independent committed-byte verification at an idle boundary.
The event-ID agreement here is an observation, not persistence proof, and it
does not transfer a manager's unfinished prompt task. Running/pending RPC
continuation remains unsupported. `test_acp_idle_continuation.py` uses real SDK
traffic to cover idle reducer/preference/counter staging, delayed callbacks,
pending task/Future refusal, malformed state and inert no-RPC restoration.
These alone are not an end-to-end frontend cutover proof.

### Independent idle event and turn readback

`acp_durability.capture_idle_durability` checks a settled IDLE manager session
and the actual idle client snapshot against a separate `Database(read_only=True)`
owner. It reads one SQLite transaction through `mode=ro`/`query_only`, including
committed event IDs, types, payloads and timestamps, ACP session identity/status,
and every completed turn's prompt, response, thought, tools and completion
metadata. The latest turn must agree with the captured reducer. It never flushes
the source event queue: events delivered in memory but not yet committed refuse.
Pending manager prompt work, queued prompts, incomplete/error turns, active
background tasks, gaps, mismatched contents and unsupported shapes also refuse.

The bounded private observation supports complete contiguous histories of at
most 2048 events and 2048 turns, within the private frame limit. Truncated tails
or larger sessions refuse rather than silently weakening coverage. No contents
are written to a snapshot file. `verify_idle_durability` uses another independent
read transaction in the receiver, rechecks the prepared database file identity,
and compares all captured contents and the actual receiving EventLog, not merely
equal maximum IDs. It leaves the database read-only, starts no writer, migrates
nothing, and does not claim a host index or touch delivery cursors.

This proves visibility of SQLite-committed bytes at the checked boundary, not
power-loss durability: the existing database uses WAL with `synchronous=NORMAL`.
Neither the typed observation nor a previously successful read licenses later
mutation. Nonempty ADOPT and automatic retirement remain disabled.

### Explicit native-bound idle activation

`acp_activation.NativeIdleActivation.prepare` owns one actual private TCP
connection and a socketpair with no relay tasks. It sends only the reservation's
ATTACH_PRIVATE ticket and restores the inert SDK client. A caller-supplied stream
pair, ticket, ACTIVATE success flag or decoded receipt cannot open this owner.
The original deadline, loop/thread/self birth, original retained host evidence,
immutable snapshot data and original stream/transport/socket graph are rechecked.

`activate` first obtains a freshly registered native INSPECT_COMMIT through the
integrating coordinator's query callback. The native receipt must match the
original host and exact committed reservation, as well as the raw connection's
actual local/remote four-tuple. Only then does the owner consume the host's
HELLO, requiring its exact cursor and child PID. No ACP receive loop or relay
runs during these checks. An independent event/turn read transaction follows;
a second fresh native query then checks that the host did not progress during
readback. A lost activation reply is reconciled through these queries, never
assumed successful or failed.

Under a short current-receiver routing fence, the owner checks self birth and
the exact active publication, prepared reducer/preferences/request counter,
stream graph and buffers, native host custody, and independently reads the
committed event/turn contents again. It acquires the existing migration-free
writable database resource and binds the EventLog before starting the same
byte-exact adapter relays and the precisely checked SDK TaskSupervisor receive
seam. Unknown SDK state or a custom/eager task factory refuses. There is no
initialize/new/load/resume, index claim, event reset, child restart or reap.
Cleanup releases only the component's frontend transports, tasks, writable
resource and retained host duplicate; it does not terminate the child.

`test_acp_activation_native.py` uses an actual persistent ACP child and separate
old/new frontend processes, not a mocked SDK connection. It proves exact old
owner exit, a lost ACTIVATE reply, continued ACP identity and integer request
namespace, post-transfer prompt completion and independent contiguous event/turn
readback. It also exercises precommit refusal, mismatched TCP peer observations,
swapped-stream refusal without closing the unowned stream, committed-content
drift before and after asynchronous readback, and publication drift. These are
opt-in component proofs, not daemon ADOPT, production launcher wiring,
ongoing manager admission or running-RPC continuation. Full foreground resource,
cache/token/maintenance authority and final successor attestation remain
separate gates.

### Borrowed pending-runtime idle session ownership

`PendingRuntime.prepare_idle_session` stages a `PendingIdleSession` only after
the native receiver has granted the existing writable resource graph. It binds
the actual restored manager session and EventLog to that one database, checking
captured metadata, ACP identity, turn count, preferences, native host receipt
and the candidate's birth/publication. Locked or pending sessions, active
background work, unsupported manager resources and graph drift refuse.
No host index is claimed or rediscovered.

Passing this typed binding to `NativeIdleActivation.prepare` retains the
standalone owner's native custody and stream checks, but borrows the runtime's
already-granted database. Independent durability still reads through the
original read-only resource and a separate coherent SQLite transaction; it
accepts the EventLog's explicit borrowed database association only when the
database file identity matches and no writes are pending. The native activation
fence rechecks the exact manager/log/database graph and callback, then installs
the continued client and actual host transport on the existing session before
the SDK or relay tasks can run. It uses the same event/progress/usage reducer
as ordinary start, resume and reattach.

The pending runtime owns the idle writer and cleanup. Failed activation or
client detachment never closes or rebinds the shared database. Candidate
lifespan detaches registered idle owners before closing its retained writable
resource, even if the app's database pointer drifted. The native fixture proves
a subsequent turn through the actual manager, continuous ACP/request identity,
turn-count advancement, shared-database preservation and initial/late manager,
database and callback drift refusals.

This is still an explicit **passive component**: the candidate remains
unready and its session routes remain unreachable. `CandidateServing` continues
to refuse nonempty captured sessions. A complete live-cache/token/maintenance
owner and final successor attestation are required before nonempty serving or
ADOPT can be enabled; running/pending RPC transfer remains unsupported.

### Completed worktree-cache observations

The current refresh owner is captured only when no refresh, queued refresh,
warming callback or classification backfill remains active. A completed warming
holder is an observation, not an active task. Its source generations, absolute observation times,
diagnostics and last-refresh evidence remain detached observations; restoration
does not reset their age or authorize warming. Explicit live refresh refuses.
Unsupported task-holder graphs refuse rather than being serialized or discarded.
Remote ACP target policy is not covered by this native local-host contract:
capture refuses configured or manager-retained remote targets.

`ServingRuntimeCapture(include_worktrees=True, ...)` adds an explicit worktree
coverage variant to the session-metadata snapshot. It reads the cache retained
by the serving application's lifespan, requiring its actual resolver,
governance instance, owner thread and event loop. A crawl or fresh-start lock,
unfinished classify backfill or owner/existence probe, cancelled periodic
owner, missing periodic task, or graph drift refuses. Capture never performs
discovery to populate an empty cache. Entries, opaque closure descriptors,
classify-unsupported verdicts and the effective interval remain private and
bounded to 128 agents, 4096 entries and 256 KiB.

`worktree_runtime.FrozenWorktrees` reconstructs only completed observations.
It preserves a genuinely empty captured cache and returns isolated entry
copies. Refresh, configuration, periodic startup and uncached provider probes
explicitly refuse; no subprocess, task, governance helper or maintenance owner
is restored. Ordinary worktree routes and their owner/resume probes select the
application's retained cache instead of bypassing it through the legacy
process-global accessor. Detached legacy applications retain that accessor;
an explicitly present but invalid application cache is not a fallback.

The prepared application and `PendingRuntime` retain and recheck this exact
cache graph and its private contents. Native idle-client activation therefore
refuses a replaced cache or entries changed after asynchronous native proof.
These cache observations alone do not preserve represented live-event tails,
verify all authentication consumers, transfer maintenance callbacks or enable
nonempty foreground serving. Those and final successor attestation remain
separate gates.

### Quiescent represented-event histories

`ServingRuntimeCapture(include_live_events=True, ...)` explicitly adds
represented-event coverage after worktree/session coverage. `LiveEventStore`
tracks each complete ingestion, alias, creation and drop operation, including
the gaps between SDK deduplication, event append, SDK-map recording and alias
copy completion. `live_runtime.capture_live_events` takes nonblocking operation,
store and log custody. Active operations, merges, waiters, persistent logs,
unsupported graph/reducer shapes, invalid FIFO sharing or cursor indexes refuse;
capture never waits for an incomplete operation or substitutes an empty store.

The private snapshot is bounded to 256 registration/history keys, 4096 total
represented events and 256 KiB, with bounded JSON depth and node count. It
preserves registration order, shared log/set/FIFO/SDK-map identities, all event
bytes and next IDs, telemetry identity/epoch/reducer/tool ordering, open-tool
references, and transitive merge history with exact `MergedIds` cursor indexes.
Restoration silently replays reducers into isolated ordinary memory logs and
checks their complete captured state. No telemetry sink, waiter, lock, Future,
stream, subscriber or task is transferred or emitted.

The passive application retains the reconstructed store. `PendingRuntime`
requires its exact pointer and private contents, so native idle-client
activation refuses initial replacement and late dedup/cursor-state drift.
Candidate preparation must not discard it for a freshly configured blank store.
These are passive continuity components: no represented ingestion route is
exposed, nonempty `CandidateServing` still refuses, and full ADOPT/automatic
production wiring remain disabled. Authentication continuity has its own
opt-in coverage below; maintenance ownership and final successor attestation
are still separate gates.

### Quiescent authentication consumers and UI login codes

`configure_serving_app` owns complete HTTP responses and WebSocket lifetimes
through `AuthCustodyMiddleware`. Direct UI code operations and ACP WebSocket
endpoint execution share that custody, including awaited request-body reads.
Capture refuses active work rather than transferring locks, requests or tasks.

`ServingRuntimeCapture(include_authentication=True)` requires represented-event
coverage and compares the actual constructed HTTP authentication middleware,
its declaration and custody with the token read by WebSocket and UI consumers.
An unbuilt stack, token disagreement or replaced owner refuses. Library-owned
middleware wrappers must match the actual canonical FastAPI stack.

The private snapshot preserves absent, null and present code storage distinctly,
with at most 256 codes and 64 KiB of code data. Absolute monotonic expiry times
are never renewed; consumed codes stay absent and expired codes remain expired.
The same-host successor owns detached code storage and fresh operation custody.
Passive preparation and native idle-client activation require the exact owner,
code-storage pointer and private contents, including during final route checks.
Ordinary position, event and span result tokens retain their existing format
and resolve through the reconstructed represented history and durable aliases.

These checks grant neither nonempty serving nor full ADOPT. Maintenance,
governance and final successor attestation remain independent gates.

### Explicit session metadata preparation

`VerifiedHostReceipt` validates the complete typed host response against the
actual native control channel and separately supplied host birth, frontend
publication, session identity and stable host launch context. It retains that
channel's original native process reference and refuses after host exit.
`decode_host_receipt` alone produces only a wire observation, never custody.
No PID reopen, host-index lookup or TCP nonce substitutes for this verification.

The receipt's existing `config_identity` is a **stable host launch context**,
not the composite runtime snapshot identity: changing an in-memory model,
effort, MCP definition or event tail changes the composite binding without
creating a circular dependency on the host's earlier context.

`ServingRuntimeCapture(include_sessions=True, ...)` requires completed roster
coverage, an original deadline and a separately verified receipt for every
indexed surviving host or attached ACP client. It captures actual session
metadata, target, MCP definitions, model/effort overrides, ACP identity, progress,
stop/handoff state, in-memory event tail and event-ID cursor. Task/lock/subscriber
presence is recorded explicitly as an **observation**, not serialized execution.
Remote forwards, relay supervisors, provider locks and active manager-owned
handoff/reap tasks still refuse this incomplete transfer.

Restoration uses the captured session constructor path: no disk host-index
loading, SQLite metadata/event rediscovery, target-instance migration, startup
GC, host claim, ACP replay or background recovery runs. It reconstructs event
telemetry from the captured tail without writes or emitted restart events;
this does not establish full reducer/client continuation or independent database
durability. Readers, waiters, Tasks, Futures, ACP connections and native handles
are not copied. Observed state remains on the prepared manager for a future
continuation owner to reconcile. Nonempty captured sessions explicitly block
the staged foreground-serving component until that owner exists.

`test_private_host_attachment.py` covers portable grant, route, graph, cursor,
replay, deadline, transport and owned-cleanup policies.
`test_private_host_native.py` exercises Linux and native Windows with an actual
Session Host, a real continuously surviving child and three real frontend
processes. It also verifies the typed receipt in the receiving frontend through
its actual native channel. It proves live-predecessor refusal, continued old-owner traffic,
exact predecessor exit, closed staging, a fresh native activation grant, later
host-cursor replay, byte-exact new-owner traffic and owned cleanup.

These are component proofs. `test_session_snapshot.py` covers explicit private
metadata staging, refusal of unverified hosts, preserved overrides and event
cursors, no rediscovery and no foreground activation. The main graph still
needs production idle ACP admission, running-RPC continuation and daemon-owned
commit-forward reservation custody; production host launch and initial
attachment need this private registration path. Remote
boundary adoption, live cache/token and maintenance authority, a persistent
restricted successor launcher, full conditional cutover and final attestation
also remain separate gates. None is inferred from this host receipt, and full
ADOPT and automatic retirement remain unavailable.
The native host proof includes committed-owner exit without another CAPTURE,
refusal while that owner is live, fresh recovery staging and current-cursor
replay to the third frontend with the same child. This is host-component
post-commit recovery, not a claim that the daemon adapter already retains
reservation receipts, reconciles uncertain replies or resumes ACP turns.

## See Also

- [Agent Bridge](../README.md)
- [Shared private runtime channels](../../../libs/zdd/README.md)
