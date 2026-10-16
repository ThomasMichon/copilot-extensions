# Qualified experimental remote ACP contract

Parent: [Remote ACP Client Transport](README.md).
Design decisions and SDK evidence: [design.md](design.md).
This is an interoperability contract for implementation, not a claim that the
client has shipped or passed integration tests.

## Profile identity and qualification

- Identifier: **`acp-http-ws-durable-v1alpha1`**.
- RFD source: `agentclientprotocol/agent-client-protocol` commit
  **`ccff4e7d2e431880225804a8c136c2ccfcb313d0`**,
  `docs/rfds/streamable-http-websocket-transport.mdx`.
- ACP JSON-RPC lifecycle version: **1**, negotiated by `initialize`.
- Both explicitly selected `streamable-http` and `websocket` modes must be
  implemented; no automatic fallback between them.
- This is a **qualified experimental profile**, not finalized ACP HTTP and not
  unqualified conformance to the internally conflicting RFD.

**Qualification A:** the server may clean up connection-local agent tasks,
attachments and streams on disconnect or close. Durable conversation history
belongs to the server independently. Recovery uses only advertised
`agentCapabilities.loadSession: true` and `session/load`; it is not a promise
of continuing live inference, exactly-once writes, remote message replay or
successful recovery from a server that does not persist sessions. The client
does not replay prompts automatically.

**Qualification A/load:** open the connection SSE stream first, POST
`session/load` with both identity headers and the known session ID, consume
replay and the load response on the connection stream, then open the session
SSE stream after successful load. The response need not repeat `sessionId`;
correlate it to the original load request. No session GET before load.
Historical events cannot create live approval authority.

## HTTP and WebSocket wire behavior

All operations use the configured complete endpoint, illustrated by
`https://example.com/acp`; WebSocket mode uses its `wss` equivalent with the
same authority and path. No redirects or cross-origin auth forwarding.

1. **HTTP initialize:** POST JSON, HTTP/2 required and verified. Expect HTTP
   200 JSON with `Acp-Connection-Id`. Send no prior connection/session header.
   Open connection SSE GET with `Accept: text/event-stream`.
2. **Other HTTP sends:** POST JSON, expect HTTP 202 with no RPC result body.
   Replies arrive on GET streams correlated by JSON-RPC ID. No batching.
3. **New session:** POST with connection header; result on connection SSE.
   Open session SSE with both `Acp-Connection-Id` and `Acp-Session-Id`.
4. **Session methods and session-scoped callback responses:** POST with both identity headers.
   For responses without `sessionId` in their body, retain the originating
   server request's exact session mapping; never infer from the current tab,
   sole outstanding request, title, RPC-ID string conversion or global state.
   A request-scoped form with no associated remote session (for example, while
   `session/new` is pending) is delivered on the **connection SSE stream**;
   its response POST carries **only `Acp-Connection-Id`**, not an invented
   `Acp-Session-Id`. When its originating request has a known session, retain
   that session context and include both headers on the response.
5. **WS initialize:** GET Upgrade, expect HTTP 101 and connection ID header;
   initialize is the first JSON-RPC text message. All subsequent messages use
   that bidirectional socket. Ignore bounded binary frames per the RFD.
6. **Cancel:** ACP `session/cancel`, not connection/server termination.
7. **Explicit close:** HTTP DELETE with connection header, expect 202; WS
   normal socket close. Clean up only client/connection resources. Never
   terminate the independently hosted server or delete persisted history.
8. **Cookies:** accept/send endpoint-qualified cookies on HTTP and WS
   handshakes; keep affinity across an explicit reconnect for the same target
   identity. No cookie sharing across identities or origins, no persistence
   of cookie values in bridge metadata.

Use bounded connect/send/close deadlines, bounded frame accumulation and
bounded per-connection/per-session queues. Stream failure ends the live
connection generation and pending answer authority; report consequential
request outcome as unknown rather than success or a retry instruction.
No `Last-Event-ID` wire replay is offered by this profile.

## Experimental negotiation

Use ACP's permitted `_meta` fields on capability objects. Namespace:

**`remote-acp/explicit-approvals`**

This neutral namespace and the following fields define an **experimental
extension**, not standard ACP fields or an officially registered namespace.
It is reusable by arbitrary client/server implementations and names no author,
operator or implementation project. No additional top-level protocol fields,
out-of-band handshake or private POST-chat protocol is introduced.

Client `initialize.params.clientCapabilities` includes its ordinary truthful
ACP callback capabilities and:

```json
{
  "_meta": {
    "remote-acp/explicit-approvals": {
      "profile": "acp-http-ws-durable-v1alpha1",
      "applicationApprovals": {
        "version": 1,
        "explicitAnswers": true
      }
    }
  }
}
```

Server `initialize.result.agentCapabilities` includes its truthful standard
capabilities (`loadSession`, for example) and:

```json
{
  "_meta": {
    "remote-acp/explicit-approvals": {
      "profile": "acp-http-ws-durable-v1alpha1",
      "applicationApprovals": {
        "version": 1,
        "required": true
      }
    }
  }
}
```

Require exact profile and extension version agreement before `session/new`,
load or prompt. A server with `required: true` checks the client declarations
before performing any application write; unsupported/missing/malformed
capability is an explicit refusal with no write and no UI fallback. Likewise a
client configured with required application approvals refuses a server that
does not acknowledge them. Initialization itself must not perform application
writes. Merely supporting ACP callbacks is not an agreement to this extension.

### Why an extension remains necessary

Use native ACP tool-call IDs, option IDs, permission outcomes and structured
elicitation schema/content unchanged. Native form support is advertised through
the ordinary `clientCapabilities.elicitation.form`, not duplicated custom flags.
ACP does not negotiate whether a permission client will apply blanket tool
autoapproval or require an explicit answer. The sole additional policy promise
is therefore versioned `explicitAnswers`; `required` lets the server refuse
before any write rather than discovering the gap at a later approval boundary.
No custom methods, prompt dialect, business validation or approval UI protocol
are added. Agent answers and human relay use the same existing callbacks.

Native request-scoped forms retain the original `requestId` and its JSON type.
It must identify an outstanding request on this bridge-owned connection and,
when present, its remote session. No tool-call ID is invented for that variant:
the bridge answer uses its live token with an empty `tool_call_id`, and exposes
the original correlation separately as `native_request_id`. An application
approval without a native durable tool-call ID supplies a separate opaque
`decisionId`. A completed, replayed or unknown originating request confers no
answer authority.

## Application approvals and identity

Carry application decisions over existing `session/request_permission` or
`elicitation/create` callbacks, using only the callback types actually
advertised. On an application approval request, the callback's permitted
`_meta` contains:

```json
{
  "remote-acp/explicit-approvals": {
    "applicationApproval": {
      "version": 1,
      "decisionId": "opaque-example-decision"
    }
  }
}
```

When the native tool-call ID already is the server's durable decision identity,
use it unchanged and omit `decisionId`. Only when the application owns a
separate durable identity is `decisionId` needed. It is an arbitrary nonempty
opaque server-owned string; it is not
derived from the RPC ID, bridge ID, option text or tool-call ID. Preserve native
tool-call IDs, offered option IDs, requested form schema and structured input
exactly within supported JSON types. The response echoes the same extension
metadata and durable decision ID along with the ordinary ACP outcome/action
and structured content. The server retains all business validation and
exact-action/input checks; the bridge neither invents nor bypasses them.

The bridge presents a distinct live answer token bound to the endpoint,
identity, remote session, connection generation and pending callback. An
authorized agent's explicit structured answer or a relayed human answer must
name that live interaction and select an offered option or supply the
requested form content. Preserve RPC ID type as well as value for the
underlying response; integer `7` and string `"7"` are not the same identity.

Application approval always bypasses ordinary filesystem/tool `allow-all`
autoanswer logic. Textual chat agreement, restored history, reconnect, a
duplicate durable decision ID or a previously accepted answer is never
authority for a new answer. Cancellation, withdrawal, disconnect and explicit
close invalidate the live token; stale, replayed, unknown-option and
cross-session answers refuse. A reissued decision after reconnect requires a
new live callback and a fresh explicit answer.

## Target configuration and shared auth injection

Machine-local configuration proposal:

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

`transport: websocket` selects the alternate explicitly. Names resolve only
configured endpoints; they do not allocate worktrees, create directories,
invoke a command or own a process. Remote `cwd`, if required by ACP new/load,
is an opaque server path, not a host path to validate or create.

Auth adapter contract: async `headers(context) -> Mapping[str, str]` and
`invalidate(context) -> None`. Immutable context identifies normalized endpoint
origin, configured identity, optional source resource/scope and connection
generation. Credential-relay sources and existing scope authorization own
acquisition, expiry and policy; shared injection adaptation owns formatting.
Bridge is only the request consumer. Configuration carries no token, cookie,
literal auth header, secret query parameter or acquisition output. Missing or
unsupported identity/source/scope fails closed, never ambient-auth fallback.

Any cache is owned by the existing credential layer and qualified by endpoint,
identity and resource/scope; invalidate only that context. At most one refresh
on an explicit pre-admission authentication refusal. Never refresh-and-retry
an ambiguously accepted consequential request. Reject redirects, TLS
verification failures and non-loopback cleartext before sending credentials.
Loopback cleartext is limited to explicitly selected synthetic tests.

This shared API is an owner extraction contract, not an instruction to import
`agent_mcp` from bridge or add a second credential backend/cache.
