# Experimental remote ACP clients

Bridge can drive an **already-running** remote ACP endpoint without allocating a
worktree, creating its remote directory, spawning a process, or acquiring
authority to terminate the server. Existing bridge prompts, events, attention,
permission answers, structured form answers, stop and resume remain the control
surface. Incoming bridge `/acp` WebSocket exposure is a different capability.

## Qualified profile

The neutral profile is **`acp-http-ws-durable-v1alpha1`**, qualifying the
[Streamable HTTP + WebSocket RFD at immutable commit
`ccff4e7d2e431880225804a8c136c2ccfcb313d0`](https://github.com/agentclientprotocol/agent-client-protocol/blob/ccff4e7d2e431880225804a8c136c2ccfcb313d0/docs/rfds/streamable-http-websocket-transport.mdx).
It is experimental, not finalized ACP HTTP or an officially registered profile.

Both modes use ordinary ACP JSON-RPC and initialize version 1:

- **Streamable HTTP:** verified HTTP/2, initialize POST→200 JSON plus
  `Acp-Connection-Id`; connection SSE GET; subsequent POST→202, with RPC results
  on connection/per-session SSE streams. Session sends and callback responses
  carry both connection and session headers. Cookies support affinity.
- **WebSocket:** explicit GET upgrade, connection ID in the 101 response,
  initialize as the first JSON-RPC text frame; same bidirectional ACP lifecycle.
- **Qualification:** connection cleanup is distinct from persisted conversation
  history. Resume requires advertised `loadSession` and server persistence.
  Load replay/result uses the connection stream; the session stream opens after
  successful load. History replay never creates answer authority.

There is no automatic transport fallback, prompt replay, live-inference
continuity promise, remote wire event replay, or exactly-once application-write
claim. A dropped stream makes consequential work's result uncertain. Explicit
close DELETEs the HTTP connection or closes the WebSocket; it never kills the
server or deletes persisted history. Remote session IDs are scoped to their
target; use the returned bridge session ID to disambiguate identical IDs from
different endpoints.

The service adapter loads remote history with replay enabled and marks those
bridge events `history_replay: true`. Without remote replay IDs this may repeat
previously observed history; it never implies a fresh approval, progress claim
or usage charge. Programmatic `AcpClient.load_session` callers can explicitly
select `suppress_replay=False`. The process-oriented `resync` path refuses remote
targets rather than spawning a local agent or replacing the event log.

## Configuration and control

Add machine-local service configuration:

```yaml
remote_acp:
  example-agent:
    endpoint: https://example.com/acp
    profile: acp-http-ws-durable-v1alpha1
    draft_commit: ccff4e7d2e431880225804a8c136c2ccfcb313d0
    transport: streamable-http
    application_approvals: required
    auth:
      source: git-credential
      identity: example-identity
    permissions: manual
```

Set `transport: websocket` explicitly to select the alternate. `endpoint` is the
complete HTTPS endpoint; WebSocket mode derives WSS with the same authority/path.
URL userinfo, query parameters, fragments, redirects, disabled TLS and arbitrary
cleartext endpoints are refused. No token or header value belongs in config.

Use `agent-bridge create remote-acp:example-agent` or authenticated bridge
`POST /api/v1/sessions`:

```json
{
  "agent": "remote-acp:example-agent",
  "target_dir": "/workspace"
}
```

The directory is an opaque **server-side** ACP cwd, never a host filesystem
operation. To attach an existing remote conversation, include
`"remote_session_id": "opaque-server-session"` in that same request.
The response's bridge `session_id` works with existing send/read/status,
interrupt, stop, resume and end operations. Resuming a stopped remote bridge
session loads its existing remote ID; it never silently recreates a conversation.
Endpoint/profile/identity changes refuse resume and require a new explicit
attachment. A configured remote target rejects local launch flags, environment,
worktree creation, model flags and per-session MCP process definitions.

## Standard-first headless approvals

Native ACP permission callbacks retain tool-call IDs, option IDs and outcomes.
Native form elicitation retains its schema, action and structured content.
Both authorized-agent answers and human relay use these same existing paths.
There are no custom approval methods, application-write endpoints or UI protocol.

The only additional policy negotiation is the neutral experimental
`_meta["remote-acp/explicit-approvals"]` capability namespace. It is **not** an
official ACP registration:

```json
{
  "profile": "acp-http-ws-durable-v1alpha1",
  "applicationApprovals": {"version": 1, "explicitAnswers": true}
}
```

The client advertises this in `clientCapabilities._meta`, alongside truthful
standard callback/form capabilities. The server acknowledges the profile and
`{"version":1,"required":true}` in `agentCapabilities._meta` under the same key.
Missing/incompatible support refuses before session creation/load/prompt.
Servers must likewise refuse unsupported required approval support **before any
application write**, including during initialization; no UI fallback is implied.
ACP's native form support does not express whether a tool permission client
uses blanket autoapproval, so this explicit-answer policy promise is necessary.

Application callbacks may include under the same `_meta` key:

```json
{"applicationApproval": {"version": 1, "decisionId": "opaque-server-decision"}}
```

Use the native tool-call ID unchanged when it already is the durable decision
identity; omit `decisionId` then. Supply the separate opaque ID only when the
application owns one. The response echoes the metadata without interpreting the
ID or replacing server-side exact-action validation. RPC IDs, native tool-call
IDs, durable application IDs and bridge live-answer tokens are distinct domains.
Request-scoped native forms are supported only when their original `requestId`
identifies an outstanding request in the current bridge-owned ACP connection
(and its remote session, when one already exists). That ID
retains its JSON type and is exposed as `native_request_id`; no tool-call ID is
invented. Its answer uses the live token and an empty `tool_call_id`. An
application approval without a native durable tool-call ID supplies a separate
opaque `decisionId`. Unassociated or historical request scope has no live answer
authority.

Every remote permission is manual, even when ordinary local tools use
autoapproval. Its event retains the native `tool_call` payload, including
`rawInput`, content and locations, so an answer can inspect the exact action
even when the server supplied no separate tool update. Answer permissions
through the existing correlated permission
path with the offered `request_id` and `option_id`:

```text
agent-bridge answer <bridge-session> --request-id <live-token> --option-id <offered-option>
```

`BridgeClient.answer_permission` provides the same authenticated operation.
Rejecting a permission selects the server's offered reject option rather than
interpreting chat text or turning a form's decline flag into an approval.
Remote form events/status
also expose a fresh `request_id`. Human relay or an authorized agent can use:

```text
agent-bridge answer <bridge-session> --request-id <live-token> --json '{"choice":"explicit"}'
```

The live token selects the exact pending form, including a request-scoped form
with no native tool-call ID. An optional `--tool-call-id <opaque-tool-id>` must
match when supplied. Current attention and attention-wait references retain
the live token separately from native request and durable decision identities.

The authenticated `/api/v1/sessions/<id>/ask-user` body accepts `tool_call_id`,
`request_id`, `action` (`accept`, `decline`, `cancel`) and structured `content`.
Generic chat text such as "yes" is never an approval. Unknown options, mismatched
tokens, cross-session answers, repeated answers and stale generations refuse.
Cancellation, withdrawal, disconnect and close invalidate pending answer
authority. Reissued decisions require a new callback and fresh explicit answer.
Every approval wait has its own `rpc_timeout_seconds` deadline, including an
idle-session callback with no outstanding outbound RPC. Expiry closes the
connection and refuses later answers.
When an originating RPC completes, an unanswered request-scoped form is
withdrawn with terminal cancellation; its live token disappears while the
callback response retains the original wire routing.

## Authentication owner and limits

The configured `git-credential` source uses the shared credential-relay
`GitCredentialSource` with the explicit endpoint/username, noninteractive
acquisition and no new token cache/store. A missing credential refuses; it
never switches to ambient auth. A returned different username refuses.

For a custom host adapter, the public `AcpClient.start_remote(..., auth=...)`
accepts the shared `credential_relay.http_auth.HttpAuthInjector` interface:
async `headers(HttpAuthContext)` and `invalidate(HttpAuthContext)`.
Context binds origin, configured identity and generation, with optional source
resource/scope. Custom adapters retain their source's scope and expiry policy.
Such an adapter declares `auth.source: injected` and optional non-secret
`auth.resource` / `auth.scope` selectors; an unbound injected source refuses,
never falls back to host Git credentials. The ordinary daemon does not load
arbitrary adapter code from configuration.
The built-in Git adapter rejects unsupported resource/scope instead of widening
authority. Bridge never imports a sibling plugin runtime.
Header formatting is shared with agent-mcp; no acquisition logic is copied.

For application-form withdrawal, `elicitation/complete` can carry the original
application-approval metadata with its explicit `decisionId`. It must identify
one unique live form. Native elicitation IDs are preserved separately, never
guessed to equal a tool-call or decision ID. Unmapped/ambiguous notifications
do not select a request; explicit session cancellation invalidates all pending
answers.

Auth headers are acquired at the request boundary, not persisted. One refresh
is permitted after an explicit pre-admission 401. Timeouts, disconnects, server
errors and ambiguous acceptance are **not** retried. Redirects are disabled
before credentials can reach a different destination. Remote RPC error text is
sanitized, not incorporated into bridge errors/logs.
Provider exceptions during 401 invalidation are bounded and sanitized as well;
their messages and formatted traceback context are not exposed to callers.
Malformed successful SDK results are likewise replaced by a sanitized typed
failure before caller errors, event logs or turn-failure persistence.
Asynchronous SDK notification diagnostics are also redacted inside the remote
connection's context, using Python's public log-record factory API. Local SDK
connections and application logging retain their existing behavior; SDK code
and private handlers are not patched.
SDK source paths are canonicalized on both sides of the diagnostic boundary,
including packages imported through a directory symlink.

Defaults: 1 MiB JSON/SSE/WS frames, 64 queued messages/callbacks/sessions,
30-second connect/send/close deadlines, a 900-second RPC result/approval deadline,
and a 120-second SSE idle deadline. WebSocket liveness uses native ping/pong
with a 20-second ping interval and the ordinary timeout as its pong deadline;
quiet application traffic alone does not disconnect a healthy WebSocket.
`max_frame_bytes`, `max_queue_messages`, `timeout_seconds`, `idle_seconds`,
`rpc_timeout_seconds` expose
bounded validated adjustments. Overflow or stream loss invalidates the live
connection, not a quiet fallback. Server SSE keepalives must arrive within the
idle deadline. Compressed HTTP responses are refused to bound frame decoding.
SSE accepts LF, CR and CRLF delimiters, including CRLF split across reads.
Failure also starts idempotent connection cleanup automatically: sibling
consumers are cancelled and wire resources close without a later operator stop.
Consumer-drain and wire-close phases each use the configured ordinary timeout.
Historical tool output never establishes a live background-task inventory.
The shared progress reducer also ignores marked history during manager
rehydration: historical milestones cannot overwrite live progress or refresh
the current-output timestamp after a daemon restart.
New/load results are adopted by the client before session-stream events are
admitted; prompt sends wait for the session stream to be ready. A handshake
finishing after explicit teardown is refused and its acquired socket closed.
Startup continuations cannot overwrite an operator stop/end. If the server
returned a validated durable session ID but stream setup failed, the stopped
ledger retains that ID for explicit `session/load`, never silent recreation.
Explicit stop captures an already accepted ID before clearing the client, even
when its session SSE opener is still pending.
Remote connect/resume also participates in existing manager acquisition/drain
accounting before awaiting an old client. Drain cannot report clean while
attachment is pending; a drain that starts during attachment refuses further
connection adoption instead of allowing a later prompt through.
Attachment checks registry ownership and terminal state after teardown and
before adoption; a completed end cannot resurrect an untracked connection.

Affinity cookies stay in memory per target/identity and survive explicit resume
within the same daemon; they are not persisted across daemon restart. `source:
none` and cleartext are available only with `allow_loopback_test: true` for
explicit loopback synthetic fixtures; neither enables arbitrary cleartext.
Both transports retain cookies issued with a pre-admission 401 and include
the updated scoped cookie jar in the single authenticated retry.
Remote ACP connections are excluded from the process idle reaper and automatic
process handoff. In particular, a healthy agent without `session/load` support
is not automatically disconnected merely because its conversation is idle.

No runtime deployment or server-side business semantics are configured by this
feature. See [the canonical test guide](../../../TESTING.md) for contained
validation and the public effort's contract for the detailed interoperability
decision record.
