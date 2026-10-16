# Remote ACP client: pinned experimental design

Parent: [Remote ACP Client Transport](README.md). Coordination: #6908.
This is a proposal, not a shipped configuration or implementation claim.
The selected option A is made concrete in [the qualified wire contract](contract.md).

## Immutable protocol reference

Selected family: **experimental Streamable HTTP + WebSocket transport RFD**.
Pinned source:

- Repository: `agentclientprotocol/agent-client-protocol`.
- Commit: `ccff4e7d2e431880225804a8c136c2ccfcb313d0`.
- File: `docs/rfds/streamable-http-websocket-transport.mdx`.
- [Immutable RFD](https://github.com/agentclientprotocol/agent-client-protocol/blob/ccff4e7d2e431880225804a8c136c2ccfcb313d0/docs/rfds/streamable-http-websocket-transport.mdx).

The draft is not a finalized remote ACP standard. Both profiles are required
client capabilities. Mode selection must be explicit, never a silent fallback:

| Operation | Streamable HTTP | WebSocket |
|-----------|-----------------|-----------|
| Connect | POST initialize; HTTP 200 JSON and `Acp-Connection-Id` | GET upgrade; HTTP 101 and `Acp-Connection-Id`, then initialize as first JSON-RPC message |
| Receive | Connection SSE GET plus one SSE GET per session | JSON-RPC text frames on the upgraded connection |
| Send after initialize | POST; HTTP 202 empty body; result arrives on appropriate GET stream | JSON-RPC text frame |
| Session identity | Both connection/session headers on session POST and GET; `sessionId` in ACP payloads | ACP payload identity |
| New session result | Connection SSE stream | Same bidirectional channel |
| Prompt result and callbacks | Session SSE stream; responses correlated by JSON-RPC ID | Same bidirectional channel |
| Cancel | Session POST notification; prompt settles separately | ACP cancel notification; prompt settles separately |
| Close | DELETE connection; HTTP 202 | Close client socket |
| Requirements | HTTP/2, JSON content type, SSE Accept, cookies | Cookies, text frames; ignore binary per pinned draft |

Connection/session IDs are not authentication. Store affinity cookies only in
the endpoint/identity-qualified in-memory connection context; retain them as
needed for explicit reconnect, never merge jars across targets.
The RFD has no v1 replay IDs or `Last-Event-ID` recovery. Bridge event-log
replay must not imply the remote wire can redeliver missing events.

## Decision D1 — lifecycle clarification

Three statements in the pin cannot be implemented as one unconditional promise:

1. **Durability and reliability expectations / v1:** "Sessions survive
   disconnects." A session persists independently of a connection and can be
   loaded after reconnect.
2. **WebSocket Request Flow / Bidirectional Messaging:** "On disconnect, the
   server cleans up the connection and any associated sessions."
3. **Unified Endpoint Routing / DELETE:** "Terminate connection and all
   associated sessions, return 202."

The RFD's Resume Session Flow also opens the session GET **before** sending
`session/load`. Content Negotiation and Validation returns 404 for a session
unknown to that connection. Whether persisted-session lookup counts as known
before attachment is not defined. Load history routing must therefore be
qualified rather than guessed.

### Option A — explicitly qualified experimental profile (selected)

Distinguish connection-local attachments/agent tasks from durable persisted
agent-session state. Disconnect and DELETE clean up attachments and may cancel
in-flight work, but do not delete persisted history. Resumption requires
advertised `loadSession` and actual server persistence. No claim that the
server keeps working while disconnected is implied.

Use the public SDK's documented HTTP load routing: history and load response
arrive on the connection stream, then open the session stream after successful
load. This avoids pre-load unknown-session GETs. Explicit client close releases
the connection, not the remote server process. No remote session deletion is
invented.

This option is explicitly accepted as a **qualification of the pinned draft**,
not an assertion that its contradictory paragraphs are already identical.
The distinct profile is `ce-remote-acp-http-ws-2026-10-15-a1`.

### Option B — await clarified upstream intent

Keep #6908 blocked without runtime changes until the RFD or its maintainers
resolve persistence, disconnect/DELETE and load ordering, then pin that public
revision and review any changed intent. Do not narrow to WebSocket-only,
substitute MCP Streamable HTTP, or implement an application-specific POST chat.

Neither option weakens exact-action approval validation or permits prompt
replay. Option A was explicitly selected after the plan review, not inferred
from that review.

## Public SDK prior art

The current Python SDK has both experimental web transports; do not reimplement
them on the assumption only stdio exists:

- Inspected immutable commit:
  `agentclientprotocol/python-sdk@bdb9ac7054fb2df76a922b60b356c3c65f8a8a64`.
- [Web transport documentation](https://github.com/agentclientprotocol/python-sdk/blob/bdb9ac7054fb2df76a922b60b356c3c65f8a8a64/docs/web-transport.md).
- [HTTP client](https://github.com/agentclientprotocol/python-sdk/blob/bdb9ac7054fb2df76a922b60b356c3c65f8a8a64/src/acp/http/client.py).
- `create_http_stream` / `create_websocket_stream` produce the existing
  message-level `Transport` for `connect_to_agent`.
- The inspected project declares version `0.12.1` and an optional `http`
  extra using `httpx2[http2]`, `websockets` and `starlette`. A source version
  declaration does not prove release age or governed-feed availability.

Reusability must be qualified before dependency changes: HTTP inbox is
unbounded; the default disables timeouts; HTTP/2 negotiation is enabled but
the client code must also be checked for actual protocol enforcement;
session-stream EOF does not fail the connection; close DELETEs the connection.
Check callback response session headers as well: JSON-RPC responses lack a
method/params session ID, so header routing must retain the original callback
request's session mapping. A raw SDK call is not proof of these contracts.

Prefer a small SDK transport adapter with public extension seams. If safe bounds,
auth refresh, callback routing or lifecycle separation require private SDK
monkey-patches, stop and make that dependency decision explicit instead.
No SDK source is vendored and no dependency is installed by this proposal.
All new manifests/resolutions must obey seven-day seasoning and the governed
artifact-feed contract before implementation or test provisioning.

## Decision D2 — SDK transport ownership

The SDK pin remains current public `main` on reinspection. These are source
findings, not claimed runtime failures:

| Required contract | Public SDK source | Why an outer wrapper is insufficient |
|-------------------|-------------------|--------------------------------------|
| Bounded HTTP receive queue | `http/client.py`: `_HttpStreamTransport.__init__` creates `asyncio.Queue()` without `maxsize`; `_handle_incoming` uses `put_nowait` | Factory accepts only URL, client and headers; `Transport.receive()` consumes after the private producer has already queued |
| Bounded SSE frame accumulation | `_sse.py`: `buffer += decoder.decode(chunk)` and `data_lines.append(value)` without a size limit | Limiting a response read chunk does not bound a frame split across arbitrarily many chunks |
| Explicit WS redirect and resource policy | `ws/client.py`: `create_websocket_stream(url, headers, cookie_store)` calls `ws_connect` directly | Factory exposes no connector, redirect policy, max-frame or queue configuration; post-connect wrappers cannot govern the handshake |
| Correlated HTTP callback-response headers | `http/client.py` and `http/protocol.py`: session header selected from method/params, but a JSON-RPC response has neither | The factory offers no callback-routing hook; a response wrapper cannot add HTTP headers inside its POST owner |
| Session stream failure visibility | `http/client.py`: session EOF only removes the stream from a set | Silent loss is not safe uncertainty reporting for pending approvals/prompts |

Public `client=` injection can help HTTP auth/timeouts and response validation,
but does not solve the producer-queue or WS factory boundaries. Selecting a
fixed-size outer queue or setting private attributes would falsely claim
boundedness or depend on private implementation behavior.

**Alternative 1 (agent-recommended):** authorize a small bridge-owned bounded
HTTP/SSE and WS wire transport implementing the SDK's public `Transport`
protocol (`send`, `receive`, `close`) and using public `connect_to_agent`.
Keep all ACP JSON-RPC dispatch, schema, sessions and callbacks in the SDK;
do not fork the protocol implementation or import its private transport
classes/parser. Reuse seasoned governed HTTP/WS dependencies and shared
credential owners. This is more owned wire code than the preferred factory
adapter, so it needs an explicit choice rather than silent expansion.

**Alternative 2:** contribute public queue/frame/connector/routing hooks to the
upstream SDK, wait for a seasoned governed release and consume that. Keep this
effort blocked until those seams exist. No SDK issue or PR is opened as a side
effect of this decision record.

Do not lower the safety requirements, monkey-patch `_HttpStreamTransport`,
replace `ws_connect` globals, or vendor SDK internals to avoid this choice.

## Existing owners and proposed extension seams

- `plugins/agent-bridge/src/agent_bridge/acp_client.py`: share initialization,
  new/load, prompt, cancel, event handling and callbacks. Add a typed transport
  entry point, separating process ownership from client-connection ownership.
  Do not model a remote endpoint as an SSH command or namespace workspace.
- `libs/credential-relay`: `CredentialSource`, `RelayBuilder`,
  `RelayPolicy`, source expiry and request-scoped authorization own acquisition
  and scope. Reuse these sources; do not duplicate host CLI token retrieval.
- `plugins/agent-mcp/src/agent_mcp/auth`: existing `AuthInjector.headers` /
  `invalidate` and token injection illustrate the HTTP owner seam. Promote
  only the reusable injection contract/adaptation into the shared credential
  library, then keep agent-mcp's public API as a compatibility facade.
  Do not import the sibling runtime into bridge, copy its acquisition code,
  or give bridge a separate persistent credential cache.
- Bridge permission/elicitation callbacks and existing answer/control paths
  own operator delivery. Validate the remote session ID before dispatching a
  callback to the owning client; never drop it at a single-session shortcut.

Shared-owner extraction is a proposed implementation choice, not a new
credential backend. The real existing consumers must retain focused tests.

## Proposed configured target

The exact proposed configured target is recorded in [contract.md](contract.md).
Only synthetic values appear there. This earlier shape is superseded by that
contract:

```yaml
remote_acp:
  example-agent:
    endpoint: https://example.com/acp
    profile: ce-remote-acp-http-ws-2026-10-15-a1
    draft_commit: ccff4e7d2e431880225804a8c136c2ccfcb313d0
    transport: streamable-http
    auth:
      source: git-credential
      identity: example-identity
    permissions: manual
```

`transport: websocket` selects the alternate mode explicitly. The configured
endpoint is the complete ACP endpoint, not a host to probe, a workspace to
allocate or an executable to spawn. Reject URL userinfo, fragments and secret
query parameters. HTTPS/WSS and certificate verification are mandatory outside
explicitly enabled loopback synthetic tests; no arbitrary cleartext opt-out.
The illustrative `identity` selects an existing source identity, not a literal
credential. Shared auth configuration must fail on unsupported combinations,
not silently reuse ambient credentials.

Proposed injection API shape: an async `headers(context)` and
`invalidate(context)` where context binds the normalized endpoint origin,
selected identity, resource/scope and connection generation. The shared owner
resolves source credentials under its existing policy; bridge receives only
the request headers and never records their values. No custom auth handshake,
query-secret transport or credential-store API is added.

Disable redirects for POST, SSE, DELETE and WebSocket handshakes. Reacquire at
most once on an explicit pre-admission authentication refusal. Never retry a
consequential prompt after timeout, disconnect, ambiguous acceptance or a
server error. Auth-refresh policy must distinguish rejected admission from an
unknown outcome rather than applying a blanket "retry once" to all requests.
Cookie and token cache identity includes endpoint and selected identity.

Remote durable session records retain endpoint identity, selected profile and
mode, remote ACP session ID, negotiated capabilities and outcome uncertainty.
No tokens, cookies, auth headers, credential commands' output or remote
response bodies enter config, metadata, logs or sanitized exceptions.
An unexpected endpoint/profile/identity change requires a new explicit attach.

## Headless approval contract

Every manual permission/application approval exposes the live request ID,
offered option IDs, remote session identity and connection generation through
the existing bridge attention and answer paths. Structured elicitation retains
the requested schema and action values. Explicit agent answers and
operator-relayed answers both resolve exactly that pending request.

There are separate identity domains: the transient JSON-RPC request ID
correlates the wire response; bridge's live answer ID fences the current
pending interaction; a durable application decision ID and tool-call ID are
opaque server-owned values. Preserve them without substituting one for
another, deriving meaning from their syntax, or changing structured input.
The client transports explicit decisions but does not invent server business
validation, reconstruct action payloads from display text, or override
server-side exact-action checks.

Advertise only the callback/form/permission capabilities the bridge can
actually answer through its existing protocol negotiation. A server requiring
an unsupported approval capability must fail explicitly before a write,
not proceed under tool autoapproval or force a remote web UI. The exact
application-required capability declaration is now fixed in [contract.md](contract.md)
under permitted namespaced `_meta`; it is not a standard ACP capability name
or a second handshake. Ordinary ACP callback support alone must not be
misreported as proof of arbitrary application approval semantics.

Reject unknown options, wrong sessions/generations, duplicates, restored-history
requests and stale answers. Cancellation/withdrawal/disconnect invalidates live
answer authority; reconnect does not resurrect it. A plain "yes" message is not
a structured answer. Existing trusted generic tool autoapproval may remain
available only under its configured authority; application approvals and
elicitations must not inherit broad `allow-all` behavior. Server-side
exact-action validation remains authoritative and unchanged.

## Validation matrix and completion evidence

Use existing contained runners in `TESTING.md` with supported Python; stage
new source/test files before snapshotting. Keep the fast required smoke lane
focused; place expensive exhaustive matrices in the existing manual lane.

| Contract | Required synthetic evidence |
|----------|-----------------------------|
| Transport | Independent remote process; real negotiated HTTP/2/SSE and real WS upgrade; correct 200/202 handling, both header identities, cookies and response correlation |
| Lifecycle | Initialize capabilities; new/load, streamed updates, prompt, cancel; connection close and server process survival; reconnect/load durable identity with no prompt replay |
| Multiplexing | Two sessions with overlapping numeric RPC IDs across connections; callbacks/responses cannot cross target/session/generation |
| Permissions | Explicit authorized-agent answer, human relay, offered rejection, opaque durable decision/tool-call IDs distinct from RPC IDs, stale/replayed/wrong-session answer refusal, cancellation and disconnect |
| Elicitation | Form schema/action/input fidelity, explicit accept/decline/cancel, withdrawal, missing required capability refuses without a write, no automatic application approval or UI dependency |
| Authentication | Actual constructed headers; scope/identity/origin isolation; expiry, refusal and bounded refresh; no redirect credential leakage or secret-bearing logs/errors |
| Bounds/failures | Frame and queue overflow, malformed SSE/JSON, stalled POST/GET/WS, session-stream loss, HTTP/1.1 refusal, invalid TLS, unknown remote outcome |
| Regression | Existing subprocess, Session Host, SSH/provider, local tool policy and shared auth consumers |
| Publication | Touched lint, install contract, schema/link checks, required CI and real review before merge |

The independent synthetic server must retain persisted session state separately
from connection tasks, exercise D1's chosen contract, and keep running after
client disconnect/close. It must validate exact offered actions, not autoaccept
any answer to manufacture a passing roundtrip.

No runtime tests have run at plan creation. Real HTTP/2, WS, approval and
disconnect-survival evidence remain open, as do clean-room and any available
external-venue validation. This effort does not authorize a live deployment.
