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

Use `agent-bridge create remote-acp:example-agent` or
`agent-bridge create https://example.com/acp`, or authenticated bridge
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

Named and URL starts with `caller_id` reuse that caller's conversation only
after resolving the selected project's current canonical target and policy.
Reuse requires the same project, server-side cwd and complete binding snapshot;
a stopped conversation loads its retained remote ID. Another project's binding
never reuses or takes over that session. A changed binding refuses reuse until
an explicit new attachment (`force_new: true`); unowned anonymous starts always create
a new conversation. Concurrent unowned starts share the reserved starting session,
including an explicit native ID whose load has not yet completed. Stopped
caller reuse serializes with explicit resume/stop through the session's
lifecycle lock and revalidates state and policy after acquiring it.

An explicit URL must match exactly one bound target policy in the requested
project context before credentials,
network access or session creation. HTTPS uses the policy's selected transport;
WSS additionally requires `transport: websocket`. Host case and default ports
are canonicalized, but paths remain distinct. URL text never supplies auth
policy; unmatched or ambiguous URLs refuse without ambient-auth fallback.
Endpoint targeting/contributions are advertised by HTTP generation 41
(`REMOTE_ACP_TARGETS_PROTOCOL_VERSION`). The client refuses a direct URL before
sending it to an older daemon; named remote ACP retains its generation-38 floor.
For named targets, generation-38 through generation-40 daemons retain legacy
CLI caller reuse; binding-aware daemon reuse applies from generation 41.

### Durable host-worktree responsibility

HTTP generation 43 adds retained host ownership, independently of generation 42's
attention boundaries. The ordinary CLI forwards its
qualified host owner; `BridgeClient.start_session` and the session API accept
`caller_owner_ref`, such as `local/example/host`. A session-qualified reference
normalizes to its worktree: a successor caller session retains the same owner.
Caller affinity, caller-session provenance, the Bridge handle and the
target-scoped native conversation ID remain separate identities.
An owner declaration in `AGENT_WORKTREES_OWNER_REF` is not authority by itself:
if the CLI cannot resolve its qualified owner, it refuses rather than silently
starting unowned, even when its caller-ID probe also failed.

Managed requests require the compatible Worktrees
[`resource-relationship` authority](../../agent-worktrees/docs/resource-relationship.md)
in the same resolved installation, including its explicit read-only reservation
absence capability. Missing/incompatible authority, unresolved or frozen owners
and conflicting ownership refuse before remote creation. There is no
legacy-claim, private-store or unowned fallback. A managed CLI request also
refuses if its qualified owner cannot be obtained. Clients refuse managed
remote requests to older daemons before the CLI's legacy caller-reuse path.
The finite authority command must establish native custody before receiving
transaction stdin: a Windows Job or Linux identity-checked process group.
Timeout/cancellation retires that scope and bounds pipe collection; unconfirmed
cleanup stays an explicitly unknown outcome and cancellation still propagates.
macOS daemon-host authority is not qualified without that identity backend;
ordinary clients and standalone unowned requests are unchanged.
The production startup path defers ACP SDK/client construction until an ACP
operation needs it. The public `session_manager.AcpClient` export still resolves
to the exact client class, not a substitute. Passive candidate preparation also
avoids constructing ordinary serving routes and starting cache machinery;
those remain owned by the existing serving and readiness boundaries.

Bridge atomically persists its pending acquisition and session, then confirms a
protected claim before connecting to the peer. The stable relationship ID is the
reservation's replay key. Lost claim responses reconcile that same key, not a
second reservation. If read-back is also unavailable, the original commit remains
unknown rather than becoming a definite refusal. An unknown `session/new`
outcome retains an explicitly
uncertain obligation and is never replayed automatically. A known native ID is
saved before binding; recovery reconciles that claim and loads the known
conversation instead of creating another. Matching includes the journal's
requested and accepted native IDs after a failed load or restart. If interruption
occurs after the session row records an accepted ID but before journal binding,
recovery reconciles that persisted ID before admitting another attachment.
If startup is cancelled after the SDK knows its accepted native ID, Bridge
persists that ID before awaiting transport cleanup. Repeated cancellation
cannot turn a known conversation into an unidentifiable remote outcome.
The approved binding appears once in claim recovery; bound identity adds a
fixed-size SHA-256 fingerprint of the opaque native ID. The full ID remains in
the Bridge journal and inspection. This keeps the exact subject within the
Worktrees callback limit without duplicating a potentially long binding or ID.
After reservation, Bridge checks capacity for the eventual bound subject before
connecting to the peer. Insufficient capacity retains the pending claim for
explicit local retirement; it never creates an unusably large remote attachment.

By default, managed starts reuse the owner's conversation in the same canonical
binding and server-side cwd, even without a caller ID. `force_new` explicitly
requests another conversation, but cannot fork an already-known owned native
ID. A different worktree cannot take over an existing owned conversation, and
an existing unowned record is not implicitly adopted.

Stop, connection loss, daemon restart and same-worktree succession preserve the
claim. Active or uncertain claims block worktree finalization. Explicit end
closes local admission, drains local operations and durably retires the local
relationship before settling the claim through the registered
`bridge-relationship` provider. The provider verifies the exact current subject;
peer absence is neither required nor asserted. Disconnected idle relationships
can retire locally. This does not terminate the peer service, delete remote
history, or establish that uncertain remote work was cancelled.

Inspection is available in `remote_relationship` on ordinary start/list/detail
responses, and after local retirement through:

```text
agent-bridge relationship-inspect <bridge-session-id>
GET /api/v1/sessions/<bridge-session-id>/relationship
```

These report the owner, relationship ID, claim ref, native ID and uncertainty.
Settlement failure retains the retired journal and Bridge handle for an explicit
end retry; unknown remote outcomes remain in the terminal audit. Provider status
reads never ensure a daemon or self-provision a runtime.
Failed local shutdown retains the managed client until all its local transport
tasks quiesce. Stop does not discard that teardown reference, resume cannot
replace it prematurely, and end/provider verification cannot claim quiescence
merely because the session is marked stopped.
Turn admission rechecks daemon drain after claim inspection, before counting or
persisting a turn. An inspection released after a clean drain cannot start new
work across the cutover boundary.
Queued follow-ups remain durably queued, with their original ID, FIFO position
and caller attribution, throughout claim inspection. Queue consumption and
turn creation commit in one transaction. Cancellation, admission failure or
daemon exit before that commit leaves the original queue row recoverable;
an interruption afterward does not enqueue a duplicate. Cancellation still
propagates to the stopping task.
Nonclosed relationships retain their original session handles through ordinary
garbage collection and startup cleanup, regardless of age. Closed and unowned
sessions keep the normal retention policy; terminal relationship audit remains
independent of session pruning.
Direct retirement also recovers an accepted native ID persisted in the session
row before journal binding, so terminal audit does not depend on resuming first.
For an unconfirmed reservation, end performs exact read-only reconciliation,
not another reserve. Only authoritative absence, with no accepted native ID or
uncertain remote attempt, permits an unreserved local close; its receipt remains
in the terminal journal and inspection reports `reservation_absent: true`.
Unknown, malformed or unavailable absence evidence retains the pending handle.

Relationship tables require database schema 27. Passive preparation and
migration-free writable adoption refuse a preceding schema-26 database rather
than promoting it without those tables. The ordinary writable database startup
prepares the additive schema and authority identity before passive adoption is
possible; adoption itself never creates tables, migrates or starts a writer.
An upgrade across this schema boundary therefore needs separately authorized
schema preparation through ordinary startup, not an implicit candidate-side
migration or restart.

Acknowledged cross-worktree transfer is not implemented by this slice.
Worktrees continues advertising `handoff: false` and refusing retained offers
and acceptances. Do not substitute generic claim release or disposable resource
deletion. Before activating retained relationships, qualify every participating
ledger writer; a source merge or one compatible command is not a rollout grant.
Standalone programmatic requests with no supplied owner remain explicitly
unowned and do not require Worktrees.

### Enabled-plugin target requirements

An enabled plugin may declare these optional fields in its `plugin.json`:

```json
{
  "bridgeConfig": ".agent-bridge/config.yaml",
  "bridgeProviders": ".agent-bridge/providers.d"
}
```

Paths are relative to the identity-verified payload root. Traversal,
cross-platform absolute paths and symlink/reparse indirection are refused.
Installed does not mean enabled: existing plugin-activation resolution owns
global/project selection and indeterminate state.

The config file supplies **inert requirements**, not private instance values:

```yaml
remote_acp:
  example-agent:
    transport: streamable-http
    auth:
      source: injected
      scheme: Example
    description: An already-running example agent
```

The pinned profile, draft, required application approvals and manual permissions
retain their existing defaults. Endpoint, identity, arbitrary headers, commands
and credential approvals are not accepted in this contribution schema.

The optional `bridgeProviders` directory contains native remote descriptors,
not executable providers. Each JSON file has one target:

```json
{
  "schema_version": 1,
  "kind": "remote-acp",
  "name": "example-agent",
  "target": "example-agent"
}
```

A string `target` references that plugin's config requirements. Alternatively,
`target` may contain a requirements object, allowing a descriptor-only plugin.
Both surfaces are optional; identical definitions from the same owner coalesce.
Conflicting definitions or owners refuse the affected name. Existing
command-based namespace providers and their machine-local `providers.d`
registration remain unchanged.

The adopter explicitly binds the instance in service config:

```yaml
remote_acp_bindings:
  example-agent:
    plugin: example-harness@example-marketplace
    endpoint: https://example.com/acp
    identity: example-client
    credential_source: builtin/git-credential
```

The `plugin` must match the contribution's canonical owner. Registered-project
bindings can also live in the existing repository bridge config: canonical
`.copilot-extensions/agent-bridge/config.yaml`, legacy `.agent-bridge/config.yaml`
fallback, then the selected marketplace overlay. Explicit service bindings win
over repository bindings. Repository config cannot approve a credential source.
An optional binding `project` narrows applicability; project-only activation
requires matching `sender_repo` at attachment.
CLI creation passes its selected project for named and URL remote targets:
`agent-bridge --project demo create remote-acp:example-agent`. The authenticated
session API supplies the same context through `sender_repo`.

Ordinary roster/detail includes `remote-acp:<name>` with owner, applicable
projects and a non-secret binding diagnostic. Unbound targets remain visible
as `requires-binding` and non-spawnable. `state: bound` means configuration and
source registration/approval are complete, **not** that credentials exist, the
server is reachable, or a conversation works. Discovery does not contact the
endpoint or retrieve credentials. `/api/v1/agents?project=<name>` composes remote
bindings in that project before filtering contributions, including cached
rosters. Detail accepts the same `project` query. Plain and streaming CLI rosters
and remote detail pass their selected project; ordinary static/provider rows
retain their behavior.
Cached and uncached asynchronous roster paths compose repository-backed rows
off the event loop. Cached project composition occurs before final namespace
assembly and completeness checks, with no later composition await. Discovery
publication retains its generation guard; namespace membership/freshness and
the remote report identity are rechecked after composition. Superseded remote
rows are omitted and reported incomplete rather than published as current.
Uncached fallback routes also compose project-aware rows within one resolver
boundary and recheck that identity after their final namespace await; they do
not add a second project-composition await after reconciliation.
Disabled contributions withdraw; uncertain activation cannot retain runnable
authority from a previous scan. Source-level discovery failures are reported,
not treated as authoritative emptiness.

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

### Approved registered sources in the ordinary daemon

For a declared `injected` source, `auth.credential_source` (or the instance
binding's `credential_source`) names a provider-qualified relay source.
The ordinary daemon supplies `builtin/git-credential` without introducing a
credential store; this source still requires the explicit endpoint/username
credential to exist in the host's existing helper. A plugin declaration does
not provision or mint that credential.

Service-only approval binds that reference to the exact non-secret context:

```yaml
remote_acp_auth_sources:
  builtin/git-credential:
    origin: https://example.com
    identity: example-client
    scheme: Example
```

Direct service `remote_acp` entries use
`auth: {source: injected, credential_source: builtin/git-credential, identity:
example-client, scheme: Example}` for the same path. Legacy `git-credential`
entries remain compatible, with the default token scheme unchanged.

Origin, identity, resource, scope and token scheme must match the approval;
generation binds the actual transport instance. The scheme accepts a
constrained ASCII HTTP token, never an arbitrary header name/template.
Missing approval, unknown/ambiguous source, mismatched context or unsupported
resource/scope refuses before attachment and never falls back to another
source.

Existing in-process relay providers can register a source with
`RelayBuilder.add_http_source("provider@marketplace/source", source,
scoped=True)`. Registration alone is not approval. The source must honor the
exact forwarded resource/scope fields and its own expiry/revocation policy;
`scoped=True` is an explicit source capability, not a target's override.
Duplicate references are unusable without disabling unrelated sources.
Provider registration reuses the existing relay owner; config does not import
adapter code or execute provider commands during discovery.

The production startup/relay-adoption path also registers **native command
providers**. An enabled, identity-verified schema-v1 `providers.d` manifest opts
in with `"http_credentials_version": 1`. Its existing absolute `command` is
used as-is; anonymous legacy providers cannot supply HTTP credentials.
`<command...> relay-profile` advertises non-secret source capabilities:

```json
{"http_sources": [{"id": "grant", "scoped": true}]}
```

The reference is derived as `<manifest.plugin>/grant`, never supplied by the
profile. Duplicate references are ambiguous; a malformed profile refuses that
provider without disabling healthy providers. Empty source lists are valid.
After changing registration, use the ordinary authorized daemon lifecycle to
refresh it; agent list/detail do not execute a credential provider.

Only after exact service approval and attachment does the bridge invoke
`<command...> http-credential grant get`. The endpoint/identity/resource/scope
fields are sent on stdin as newline-separated `key=value` fields terminated
by a blank line. The existing credential-source response uses `username` and
`token` or `password` fields on stdout; nonzero exit, timeout or malformed
encoding refuses without exposing provider output. Commands are finite,
headless, deadline-bounded, and use the provider's existing runtime rather
than importing its package. The provider owns actual credential acquisition,
expiry and revocation; no credential is passed in argv or stored by the bridge.
Startup reads capabilities, not secrets, and HTTP-only source registration
does not start a TCP relay merely for these attachments.
Disabling `enable_credential_relay` disables the shared TCP listener, not the
process-local HTTP credential registry. Listener-disabled/elevated daemons
still register approved native sources without starting or adopting that
listener; credential approval and request-time acquisition remain unchanged.

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
Control POST/DELETE requests and each long-lived connection/session SSE reader
own separate HTTP/2 client pools. A quiet reader cannot retain a shared network
read lock while another stream has a completed response waiting to be consumed.
These physical connections retain one logical ACP connection and a shared,
in-memory scoped cookie jar; authentication, TLS verification, redirect refusal,
complete POST-body validation and all deadlines are unchanged.
Low-level HTTP transport tests/adapters inject `client_factory`, which must
create a fresh client and independent transport pool on every call, instead of
injecting one shared client. The factory carries custom trust configuration to
every connection without copying private HTTP-client configuration.
Failure also starts idempotent connection cleanup automatically: sibling
consumers are cancelled and wire resources close without a later operator stop.
Consumer-drain and wire-close phases each use the configured ordinary timeout.
Closed-state checks also stop SSE reads when cancellation races an already
buffered chunk; shutdown never waits for an extra application chunk to arrive.
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
