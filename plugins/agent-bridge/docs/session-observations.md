# Known-session semantic observations

The running Bridge advertises `bridge-session-observations-v1` and
`bridge-session-observation-history-v1`, each version 1, in its existing
`/health` capabilities. These are separate from raw event streaming and delivery
cursors. A client must negotiate the specific operation before using its
dedicated route. Missing or older capability support is unsupported, not a
reason to try a history query on a raw SSE endpoint.

The owning payload CLI exposes:

```text
agent-bridge --project example observe-session SESSION \
  --worktree-id FULL_WORKTREE_ID --contract-version 1 --json [--follow] [--after POSITION]
agent-bridge --project example session-observation-history SESSION \
  --worktree-id FULL_WORKTREE_ID --contract-version 1 --json [--before POSITION] [--limit 64]
```

Invoke the selected Bridge command from its attributable command catalog; these
examples do not introduce another global command. Both commands are passive:
they use existing credentials and an already-running daemon, without service
startup, SDK connection, session resurrection or delivery acknowledgement.
JSON output is one compact semantic body per line. Source gaps/errors exit 3.

The attributed POSIX and PowerShell payload gates classify these reads before
first-use publication or installation. They use the existing no-self-provision
refusal boundary and read-only runtime-resolution mode without changing the
caller's no-self-provision environment binding. A missing runtime
fails without consulting publication or invoking an installer; an existing
runtime receives the original arguments unchanged.
Classification consumes leading `--json`, `--project VALUE`, `--project=VALUE`,
`-p VALUE` and attached `-pVALUE` forms without treating a project value as a
verb. It also retains the owning root parser's accepted long-option
abbreviations: `--p` through `--project` (with separate or `=VALUE` values),
and `--j` through `--json`. Regression cases first parse those arguments through
the production parser, then invoke the attributed gate with inert missing-runtime
spies. No parser-wide abbreviation policy is changed.
A redundant leading `agent-bridge` is conservatively fenced against
provisioning but is not stripped or made a new Python CLI alias. The observation
verbs are root commands; there is no Worktrees-style namespace alias.
Other commands retain their existing first-use policy.

## Admission and identities

Both routes use existing Bridge HTTP authentication. A caller label, position
or session handle is not authentication. Reads additionally require an exact
already-known session ID and its stored full worktree ID. ACP aliases, worktree
suffixes, agent names and latest-session resolution are not accepted.

For owned sessions, the optional project selector must exactly match the stored
target project, including absence. Represented registrations do not carry a
qualified project identity: omit `--project`; their source reports `null`.
This is an explicit weaker binding, not a project inferred from the repository
name. A missing registration/log is unavailable. Represented history remains
read-only, memory-only and partial; registration expiry/absence does not prove
session retirement or relationship release.

Canonical Worktrees head is explicitly unknown. A supplied
`--head-session-id` refuses with `head-unavailable`: neither a Bridge hosting
record nor a represented registration establishes that authority. Consumers
must retain their independently admitted worktree/head selection fence.

Source identity combines the actual hosting generation, exact session,
project/worktree binding, registry, transport and existing EventLog continuity.
The durable session identity is reported only when known. Empty logs have no
journal incarnation or usable position. First-event continuity comes from the
existing producer, not a random observer ID. Resync/rebuild or hosting replacement
invalidates old positions rather than reusing numbered events as new evidence.
Represented log merges/replacement require a fresh snapshot; this interface
does not silently retarget positions.

Locally hosted remote ACP/SSH/provider sessions can expose the log this Bridge
already holds. Their transport is retained in source identity. This does not
establish remote execution/custody or full remote history. An arbitrary
federated handle is unavailable locally: this slice does not acquire a remote
carrier, discover owner membership or start an absent remote host.

## Read protocol

The authenticated GET routes are:

```text
/api/v1/session-observations/SESSION
/api/v1/session-observation-history/SESSION
```

Queries require `contract_version=1` and `worktree_id`; optional `project`,
`head_session_id` and `limit` apply to both. The observation route alone accepts
`follow=true|false` and `after`; history alone accepts `before`. Duplicate,
foreign and malformed parameters are rejected. Limits are 1–64 entries,
32 KiB encoded JSON per body and 35 KiB per SSE frame.

Both routes use semantic SSE framing. History is **exactly one finite
`history-page`, `gap` or `error` frame**, never an indefinite stream.
Without follow, observation likewise returns one frame. The CLI validates the
frame/body bounds before decoding and emits the body as JSONL.

The HTTP reader requests only the remaining frame budget plus one detection
byte from `readline`; it never consumes the raw response/SSE iterator.
The existing 15-second I/O silence timeout remains in force. Frame overflow,
transport timeout or caller interruption closes only that captured HTTP
response. Server-side cancellation releases that observer's waiter and
idle-watch registration, not another observer or the session actor.
EOF before a finite response, within a frame, or during follow without an explicit
`going-away` is a structured `source-unavailable` failure, not empty success.
Already-delivered follow observations remain usable; the final failure reports
lost continuity without replaying or controlling the underlying session.

- `snapshot` contains a bounded latest tail, atomic retained floor/head and
  source incarnation, and a replay position.
- `delta` contains the required base position and a contiguous bounded prefix
  after it. `has_more` permits draining additional live pages.
- `history-page` contains ascending identified entries older than `before`,
  with `next_before` when older retained entries remain.
- `gap` explicitly invalidates a replaced/out-of-range position or a native
  event gap. Reconcile with a new snapshot; paging never advances delivery state.
- `heartbeat` proves only this connection. `going-away` names hosting shutdown.
  `error` preserves an unavailable source instead of presenting empty history.

Positions are opaque and source-qualified. Entries preserve native event IDs,
exact native kinds, content hashes and a bounded semantic detail projection.
Duplicate identities with conflicting projected content are invalid.
`payload_omitted` is explicit: raw transcripts/tool input are not replicated.
The payload reference identifies the source record; existing raw interfaces
retain their own authentication, access and retention contracts.

Coverage is partial retained-event evidence, never lifetime or canonical mutation
coverage. Visibility can precede the EventLog's queued SQLite write; neither
snapshot nor live notification claims a durable barrier. Hosting generation
changes require reconciliation even when durable history is reloaded.

Request/resolution event occurrences retain exact correlation fields where
supplied. `current_requests` and `answerability` remain unknown: historical input
or permission evidence never grants an answering action or proves it is still
pending. Unknown native kinds remain `source-event`, without prose inference.
Reported lifecycle/tool/subagent events do not allocate, settle or release
canonical resources.

The internal `acp_current_requests.py` helper separately qualifies actual local
manual permission/form waiters, with bounded view-only identities and explicit
connection/session invalidation. It is not connected to this wire contract:
there is no current-state query, CLI flag or advertised optional capability yet.
The [implementation contract](../../../efforts/active/mux-companion-manual-cutover-diagnostics/sidecar-current-requests.md)
retains provider/parser negotiation and consumer validation as separate work.

Actual translated compaction starts carry `bridge-compaction-evidence-v1`
records, including optional direct total usage, attempt, model, execution,
trigger and target-window fields. Active pre-window capacity remains unknown.
Target capacity, adjacent usage and absent switch metadata do not qualify it.
No viewer threshold, answer action, head mutation or runtime policy is applied.

## Lifetime and validation

A held owned-session observer participates in existing subscriber accounting,
so it can postpone idle reaping while connected. Exit/cancellation releases
only that subscriber and its EventLog waiter; it does not stop the actor.
Represented observers do not acquire an owned-session idle-watch registration.
Shutdown and registration/log replacement are checked with bounded source waits;
there is no fleet poller or additional host.

Focused source selectors are `tests/test_session_observations.py` and
`tests/test_session_observation_transport.py`. They cover retained SQLite reload,
actual SDK translation, identity/page/gap boundaries, strict parser/authentication,
old peers, bounded frames and observer cancellation. Source support is not
installed/native qualification. Owner membership, federated semantic forwarding,
canonical head qualification and definitive pre-compaction measurement remain
separate work.
