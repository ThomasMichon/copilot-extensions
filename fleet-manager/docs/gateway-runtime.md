# NEW Gateway: first resident boundary (#6443)

This package now has an opt-in authenticated resident **new** Gateway and SDK.
It does not modify or replace an adopter's existing Gateway. The old planner,
release inspection, operation/allocation journals and client diagnostics keep
their existing behavior.

## Actual distribution and authority

The default wheel remains dependency-free and thin. Importing the client/wire
modules performs no discovery, installation, credential acquisition, process
spawn or host mutation. Authenticated SDK operations require the `auth` extra;
hosting requires `server`. Those extras use the shared joserfc-based identity
verifier plus maintained PyJWT, cryptography and jwcrypto for receipts and
encryption, not handwritten JWT verification or encryption.

```text
fleet-manager serve --role gateway --config explicit-gateway.json
```

This is foreground serving, not another installer or supervisor. The configured
service/deployment owner must manage process lifetime. General `node` hosting is
not implemented. The separately selected
[SSH client role](ssh-machine.md) provides only foreground SSH-directory/local
writer convergence; it is not a complete machine connector or setup installer.
The controller is a Python SDK role (`Controller.serve(stop)`),
hosted in the external custodian's own process; there is deliberately no CLI
that imports an arbitrary provider or fabricates production credentials.
The SDK does not host services, acquire installation cells or change #6424.
An explicit named-host HTTPS call owns one short-lived, isolated system-resolver
subprocess, killed and reaped at the call's absolute deadline. Numeric IPv4/IPv6
endpoints bypass it; IPv6 zone identifiers are unsupported. Resolved numeric
addresses are used only for socket connection: certificate verification and SNI
retain the configured hostname. No resolver daemon or blocked resolver-thread
pool persists between calls.
Resolved IPv6/IPv4 candidates are interleaved and nonblocking connection attempts
are staggered by 250 ms within the same absolute deadline. A slow or refused
first address cannot consume the other family's entire connection opportunity.
The winning socket alone continues to TLS; every pending loser is closed on
success, failure or deadline. This does not extend the request's budget, disable
IPv6 or substitute a numeric address for certificate hostname verification.
A connection winner uses one fresh monotonic sample to enforce expiry and set
its remaining socket timeout. Reusing the loop's pre-connect or pre-select
sample would extend an already elapsed deadline; a late-ready socket is closed
rather than admitted after expiry.

All listener connections are TLS, including loopback. All operation routes require
the shared `jwt-pop-v1` verifier plus current enrollment and policy.
The protected `/v1/challenge` route returns only the owner's proof nonce and
profile, never inventory or authority. Defaults permit
only loopback origins. A remote listener/backend/discovery requires explicit
`remote: true`, a configured TLS certificate/key and client CA trust. There is
no HTTP fallback, anonymous inventory endpoint, ambient proxy, redirect,
raw URL/method/header forwarding, marketplace dependency, Docker or index model.
Certificate verification is never disabled.

## Explicit non-secret configuration/discovery

Foreground hosting opens the same lexical absolute configuration selection
whose parent is checked by instance admission. Parent components are normalized
once; surviving filesystem indirection is not hidden by resolving an alias.
Shutdown keeps that admission until accepted HTTP handlers have retired.
Repeated Ctrl+C cannot interrupt their dedicated close/join worker or release
the disposable lifetime early; an uncertain close start is held, not replayed.
Close failures are reported only after the captured request threads retire.

`GatewayConfig.load()` consumes strict bounded JSON:

- `schema: fleet-manager.gateway`, `version: 1`, `role: gateway`;
- `fleet`, stable Gateway `installation`, receipt `issuer` and identity `audience`;
- `identity: {profile: "jwt-pop-v1", issuer, jwks, trust_valid_until}`:
  independently authenticated HTTPS access-token issuer, public ES256/P-256
  signing-key snapshot and explicit current-trust expiry. This is distinct
  from the Gateway's Ed25519 receipt keys and release trust. Issuer/key
  acquisition, renewal and provider-specific revocation freshness remain
  external obligations, not request-driven discovery;
- `jwks: {keys: [...]}`: public Ed25519 signature keys with unique `kid`,
  `alg: EdDSA`, `use: sig`; never private key bytes or network JWK discovery;
- `revoked`: denied signing key IDs, principal IDs and access/receipt JTIs;
- `signing_key`: external PEM **path reference**, `signing_kid`: a key in
  that trust set. CLI startup verifies the private/public key match.
  The key file is custodian-managed, not embedded in source configuration;
- `enrollments`: at most 128 current principals. Each supplies `principal`,
  `role` (`query`, `index-worker`, `pr-worker`, `credential-controller`), `service`,
  `installation`, positive `generation`, `expires_at`, nonempty exact lists
  `sources`, `corpora`, `accounts`, `audiences`, `scopes`, `jobs`,
  public P-256 `proof_jwk`, and optional public RSA `recipient_jwk`.
  A `pr-worker` must use an empty `corpora` list; a credential controller
  may also use an empty list. PR credential consumption does not invent an
  index corpus or confer index-query authority.
  Credential workers must have a recipient RSA key (2048–4096 bits).
  Controllers additionally supply an independently pinned Ed25519 `receipt_jwk`
  for sealed-credential receipts, not their DPoP key;
- `backends`: at most 16 approved offers, each with stable `identity`,
  `service`, `installation`, `generation`, `expires_at`, `corpus`, exact
  canonical `sources`, trusted HTTPS `endpoint`, `ca`, `issuer`, public `jwks`
  and `contract`. Only `fleet-index-scoped-v1` can execute;
- `credential_rules`: at most 128 unambiguous exact worker/source/account/
  audience/scopes/action selections bound to a configured `controller`.
  PR-worker rules additionally require the exact `forge` target below;
- `pending_limit` (1–256), `lease_seconds` (2–60), boolean `remote`;
- `tls: {host, port, certificate, key}`: external certificate/key path
  references. Port zero permits an explicitly owned embedding/test listener;
  production discovery must record the actual bound port. Remote serving also
  requires an explicit HTTPS `tls.origin`. Local-only listeners may derive
  their origin from the bound loopback address/port. Requests cannot select
  it through Host, forwarded headers or body fields. Proxy termination and
  shared-epoch multi-replica serving are unsupported.

Trust/enrollment policy is supplied at startup, not silently fetched or inferred.
Revoked backend identities are excluded from advertised support and refused
before execution-grant signing or any backend request. Receipt-key revocation
remains independently checked at admission and result verification.
Rotation can list multiple public signing keys; removal/revocation requires a
configured restart, which invalidates the entire ephemeral session/queue.
Online policy refresh and dynamic enrollment are not implemented.

An installer/controller owner supplies an explicit local endpoint file:
`schema: fleet-manager.gateway-endpoint`, `version: 1`, HTTPS origin `endpoint`,
`ca`, `issuer`, access-token `audience`, stable `gateway` subject, public
`jwks` containing **only the trusted Gateway signing keys** (not the general
access-token issuer key set), and boolean `remote`. `Discovery.load(Path(...))` validates it.
No search across ambient files, accounts or old Gateways occurs. A selected
endpoint fails explicitly on stale discovery, trust or transport failure.

`GatewayClient(discovery, principal, proof_key, access_token_callback)` calls
`connect()` before other operations. The callback is external token custody;
there is no token CLI argument, source-config token or SDK token cache.
Never serialize an SDK object/key or log a request body.

## Admission and wire contract

Access JWTs use the shared verifier's exact `at+jwt`, ES256/P-256,
issuer/subject/audience/time and `cnf` requirements. The adapter retains its
immutable request evidence and maps the verified issuer/subject and proof key
to current configured enrollment. Token role/source/generation claims never
override that policy. Revoked issuer key IDs, principal IDs, optional access
JTIs, proof keys and recipient/receipt keys refuse. Provider-permitted token
lifetimes are not shortened by a universal Gateway session-age limit.

Clients obtain the owner's nonce over verified TLS, then send RFC 9449
`dpop+jwt` proofs with actual POST/HTTPS target, access-token hash, timestamp,
unique proof ID and nonce. The shared verifier owns atomic replay admission
and strict trust/token/proof time checks. Restart creates a new nonce epoch;
reconnect requests a fresh challenge, with no automatic operation redelivery.
DPoP does not sign arbitrary request bodies: TLS protects the request, and
Gateway admission computes canonical content binding for receipts/delegation.
No PID, current process/source, native
`instanceToken` or ambient relay bearer establishes identity.

Each POST is `{access, proof, body: {epoch, payload}}`; `/v1/session` admits
without an epoch, returning the new random session epoch. Every subsequent
request must bind that epoch. Responses are signed Gateway receipts bound to
principal, route, epoch and request-content digest over verified TLS. Restart
does not reuse old epoch authority. `/v1/session` truthfully lists only the
principal's compatible configured operations; it exposes no global inventory.

Wire JSON is bounded to 64 KiB, depth 12, bounded strings/collections, with
duplicate-key/nonfinite/unknown-field rejection. Signing/encryption overhead
counts against that bound; oversized results fail, never clip.
Server requests have an absolute five-second monotonic deadline spanning TLS
handshake, HTTP headers and body, in addition to socket inactivity timeouts.
Slow-drip peers are disconnected and release their admission slot; orderly
shutdown waits for those bounded workers. Fixed backend calls have five-second
timeouts capped by the incoming request's remaining absolute deadline across
both admission and query. Slow response headers or bodies interrupt the owned
upstream socket too, so closing an incoming peer does not leave a worker held
by an outgoing read. Direct SDK discovery normalizes a trailing origin slash
before constructing request paths and DPoP targets. The accept
loop admits raw sockets into at most 16 request workers before those workers
perform TLS handshakes; a half-open TLS peer cannot stall acceptance of other
connections. Overflow connections close without starting another handshake.
Global admission/challenge budgets permit a burst of 256 and refill 128 per
second before credential verification. Verified enrolled principals have
independent bursts of 32, refilling 16 per second; no untrusted claimed
principal allocates such a bucket. Exhaustion explicitly refuses, never admits
unverified requests or extends credential validity. The socket backlog is 16.
IPv6 literal listeners select `AF_INET6` before bind;
`::1` is supported when the host supplies IPv6 loopback and the configured
certificate covers that address.

## Later index consumer: mandatory pre-retrieval contract

Request payload:
`{request_id, service_id, installation_id, generation, operation, parameters}`.
This reuses canonical offer/request/receipt identities and generation/expiry/
content-digest conventions. `index.search` parameters are
`{q, limit, corpus, source?, language?, repo?}`: 4 KiB query, 1–100 hits.
`source` is a selector, never a permission. Omission means the nonempty
intersection of authorized canonical sources and the configured corpus offer.
There is no prefix/alias inference. The source owner must enumerate canonical
stored families, including permitted commit-source descendants, explicitly.

The Gateway first sends the configured backend a fixed `/v1/index/admit`
POST containing `{request, grant}`. The grant is signed by the trusted Gateway,
audience bound to stable backend identity, fleet/corpus/source scope and exact
request correlation, with at most 30 seconds validity. The backend must verify
this **before any retrieval** and return a signature-verified `ADMITTED`
receipt with the exact scope/correlation and a one-use admission reference.
Only then does the Gateway POST `/v1/index/query` with that reference.
Backend identity/issuer/signing key are independently configured, not taken from
public health state. Configured revocations apply to both backend admission and
result receipt verification, including signing key IDs, subjects and receipt IDs.

The supplied `ScopedIndexBackend` seam implements bounded one-use admission,
grant verification at admission **and execution**, current offer fencing and
signed replies. Configure its `gateway_keys` with **only Gateway signing keys**,
not a general-purpose access-token issuer key set.
It calls `retrieve(ExecutionScope, parameters)` only after those
checks. The later index consumer MUST apply `ExecutionScope.sources` and
`corpus` to vector/hybrid candidate selection and hydration **before searching**.
Its callback must never do unrestricted retrieval followed by filtering.
`verify_execution()` is also available for a separately hosted equivalent.
The callback is not an adapter for the legacy native index; deployment owners
must explicitly attest and test their implementation of this execution contract.
Signed receipts cannot by themselves prove a backend's retrieval implementation.

The backend's signed query receipt binds `contract`, `scope`, `correlation`,
and bounded `result: {hits: [...]}`. Each hit preserves native data plus exact
`source`/`corpus` provenance. Gateway consistency validation rejects a contract
violation; it never repairs an unscoped backend by stripping forbidden hits.

Legacy `fleet-index-adapter`/`index-v1` is explicitly **unsupported**, not passed
through. Similarity (including reference-chunk authorization), clusters, scoped
status/histograms, lifecycle/reindex/drain/deploy and global diagnostics are
unsupported under #6443/#6383. They must acquire their own pre-retrieval scoped
contracts before the CLI can advertise them. Fixed index ingress and source
ingestion are consumer integrations; **actual index CLI Gateway routing/offload
is not delivered**.

## Subscribed credential control and consumption

`credential.get` uses the same request identity fields. Its parameters are
`{job, source, account, audience, scopes, action, recipient}` for index workers.
PR workers additionally require `forge`, matching their exact configured rule:

```json
{
  "provider": "azure-devops",
  "api_base": "https://dev.azure.test/example",
  "repository": "project/repository",
  "permission": "observe",
  "authentication": "bearer"
}
```

`ForgeScope` accepts `github` or `azure-devops`, an explicit HTTPS API base
without credentials/query/fragment/trailing slash, an exact two-component
repository and only the `observe` permission. Authentication is `bearer`,
or `basic` for an ADO PAT. This preserves the distinction between ADO access
tokens and PATs; the consumer must not infer an auth scheme from token bytes.
The endpoint/repository/account selection is trusted configuration, not ambient
CLI settings or a worker-selected alternate route. Index workers cannot send
`forge`; PR workers cannot omit it or acquire index credentials.

The authenticated worker's service/install/job/source/account/audience/scopes/
recipient/generation must match current enrollment and exact provider policy.
Query identities cannot acquire worker credentials. Only closed existing
relay actions `get-azure-token`, `get-github-token`, `get-access-token` exist;
there are no arbitrary CLI, git-helper, store/erase or HTTP verbs.

`Controller.start()/step()/close()/serve(stop)` uses authenticated
`/v1/controllers/{subscribe,poll,reply,disconnect}`. A controller owns one
unexpired subscription; renewal must present that subscription ID. The Gateway
chooses the controller by policy, not a worker-selected issuer. Work orders
are signed and bind Gateway epoch, both generations, exact request digest,
recipient public-key thumbprint, subscription and deadline.

`CredentialProvider.acquire(request, timeout=...)` is the external custodian
interface. `RelayCredentialProvider` structurally reuses an existing
`credential_relay.sources.CredentialSource` through closed static `RelayPolicy`
entries and a **mandatory external provider-result verifier**. The verifier must
honor actual audience/permissions, expiry and revocation or return `None`.
There is no default verifier, fake production provider or unverified JWT expiry
decoder. A broad token is not magically downscoped by a source label. Existing
provider acquisition remains outside Gateway custody/coordinator lifetime.
For PR consumption, `RelayPolicy.forge` participates in exact policy selection,
and the external verifier must validate the actual provider, endpoint,
repository, read-only permissions, authentication type, expiry and revocation.
It returns `CredentialMaterial.forge: ForgeScope` only for that verified
target. Missing, mismatched or expired material is `UNSUPPORTED`, never
relabelled to satisfy the request. Custom `CredentialProvider` implementations
have the same actual-provider-verification obligation.
Ordinary provider failures, including subprocess diagnostics, become a signed
`UNAVAILABLE` outcome without rendering exception text or ending the resident
responder. Collection is limited to that provider call; cancellation and other
process-control exceptions still propagate instead of becoming availability
responses.

Credential material is sealed to the enrolled RSA recipient with jwcrypto JWE
(`RSA-OAEP-256`, `A256GCM`), containing exact request binding and effective
expiry. The controller signs the ciphertext digest/binding/recipient/state.
For PR workers the sealed plaintext also carries the verified `forge` scope;
the recipient independently compares it to the original request before ACK.
Gateway validates that outer signature and correlates delivery without
decrypting, persisting or logging plaintext. Only the worker decrypts.
The effective delivery expiry is capped by the request deadline even if the
provider's independently verified credential lives longer.

`request_credential()` returns `PENDING`; `credential_result()` exposes the
bounded state or a one-time sealed receipt. `consume_credential()` verifies
controller/recipient/generation/correlation/audience/scope/expiry, decrypts,
and acknowledges **before returning** a `CredentialMaterial` whose token is
excluded from repr. Ack loss raises `ack-uncertain` and does not return a token.
Credential expiry is checked again after acknowledgement before material is
returned. Consumers acquire/refresh at their HTTP/Git boundaries through the explicit
worker policy below. No ambient, anonymous or stale-token fallback.
The PR worker's typed SDK exchange is available through
`CredentialWorker.acquire()` and the explicit configured consumer below.
Native Worktrees reads can renew this material at each HTTP/CLI boundary;
production provider verification and observation-service activation remain
separate work.

State: `PENDING -> DISPATCHED -> READY -> DELIVERED -> ACKED`, or explicit
`UNSUPPORTED/UNAVAILABLE/UNKNOWN`. Dispatch and delivery are each one-shot;
there is no blind automatic retry/redelivery. Duplicate/conflicting requests,
unsolicited/out-of-order/expired/wrong-recipient replies and reused acks deny.
Leases fence a crashed/disconnected controller. A dispatched uncertain request
becomes `UNKNOWN`; an undispatched unavailable request becomes `UNAVAILABLE`.
Provider timeout normalization explicitly handles Python 3.10's distinct
`asyncio.TimeoutError` as well as built-in timeouts. If the dispatch deadline
has elapsed, the controller reports `UNKNOWN` instead of attempting an expired
reply or claiming delivery; an earlier provider timeout can return `UNAVAILABLE`.
Exhausting the actual acquisition timer returns `UNKNOWN` without signing or
sending a failure receipt, even if the wall clock still reads before expiry.
Successful acquisition and reply/envelope expiry are also capped by the earlier
of signed dispatch expiry and request deadline. A provider's own earlier
failure remains `UNAVAILABLE` while that authority is valid; no subscription or
dispatch is renewed to deliver a late result.
The controller samples wall time again after provider acquisition, rather than
using its pre-acquisition timestamp to authorize a later result. Fractional
seconds bound the asynchronous wait; integer epoch seconds bound signed JWTs.
The receipt verifier already requires integer `exp` values, and the controller
also requires a bounded integer custom `deadline` before provider acquisition.
Missing, fractional, boolean or string deadlines are refused, never rounded
or coerced into a longer authority window.
The fresh post-acquisition expiry check and absolute signing cap are deliberate,
not an attempt to make both phases share one stale clock sample.
The pending limit bounds active requests; at most 1024 request identities are
retained per Gateway epoch, then admission refuses new work. The in-memory
broker is not a durable queue/task ledger. Restart means `UNKNOWN`, not success.
Existing OpLedger command/effect/allocation APIs are untouched; credentials and
ciphertext are not journaled into them.

## Evidence and honest remaining gates

### Worker-local source consumption

`CredentialWorker.acquire(parameters, timeout=30)` performs one correlated
request, bounded polling, recipient/controller verification and acknowledgement
before returning a credential. One absolute acquisition deadline caps every SDK
transport: challenge, session connection, submission, polling and acknowledgement.
The worker also checks the deadline before submitting provider work; an expired
connection attempt cannot start a late credential request. External access-token
custodians remain responsible for bounding their own callback acquisition.
Terminal uncertainty never triggers redelivery
or reuse. Expiry, stale Gateway epoch, disconnection and acknowledgement failure
raise a static reason code; no source credential is cached.

`gateway_worker.token_provider(Path(...), source=..., account=...)` loads strict
JSON with `schema: fleet-manager.credential-worker`, `version: 1` and:

- Absolute `discovery`, private P-256 `proof_key`, private RSA `recipient_key`
  and `access_token_file` **path references**, not embedded secret material.
- Exact `principal`, `service`, `installation`, positive `generation`,
  pinned `controller`, positive `controller_generation` and public
  `controller_keys` JWKS.
- `bindings`: one unambiguous closed credential-parameter object per
  source/account, each containing `job`, `source`, `account`, `audience`,
  `scopes`, `action` and the enrolled `recipient`.

This string-only loader remains an index consumer. It refuses `forge`
bindings with `typed-forge-consumer-required` rather than discarding their
target/authentication metadata.
`gateway_worker.credential_provider(Path(...), source=..., account=...,
forge=ForgeScope(...), timeout=30)` selects an exact typed binding from the
same configuration format and returns fresh `CredentialMaterial` on each call.
Every binding in that configuration must be typed; source/account/forge
duplicates are refused. Selection is checked before reading key/access-token
references. There is no implicit index-worker identity.

For a separately installed native consumer, the public process adapter is:

```text
fleet-manager credential --config <absolute-worker-policy.json> --source <source> --account <account> --forge <ForgeScope-JSON>
```

This acquires once through the same `CredentialWorker`, verifies and ACKs the
recipient delivery, and emits one bounded UTF-8 JSON object on **captured
stdout only**. Interactive terminal output is refused. The exact version-1
envelope is `{schema: "fleet-manager.forge-credential", version: 1, token,
expires_at, forge}`; `forge` is the complete scope above, not an auth-scheme
guess. Stdout contains a secret: consume it directly from the pipe, never
persist, print, forward to a logger or include it in diagnostics. Arguments
contain only trusted labels, scope and configuration path references, never
tokens or private keys. `--timeout` defaults to 15 seconds and must be finite
and positive, at most 60 seconds; the invoking native consumer may impose a
shorter absolute process deadline. Failure emits no credential and a static
stderr diagnostic, with no retry or ambient-auth fallback.

Worktrees' [snapshot command adapter](../../plugins/agent-worktrees/docs/pr-observation.md)
executes an explicit attributable command prefix for each native request and
independently verifies scope/expiry before using its result. No Fleet SDK is
imported into Worktrees, and no worker/controller/service is started by loading
this configuration. The deployment owner supplies an already-enrolled worker,
provider verifier, active controller, endpoint/trust and credential references.

The issuer/custodian renews the external access-token reference independently;
the SDK reads it before every signed request. Neither this file loader nor the
Gateway is an issuer, key installer, logged-in provider or service supervisor.
Source/account selection must match an exact binding before key or credential
consumption. Existing provider verification still owns real audience,
permissions, expiry and revocation; a label cannot downscope a broad token.

The opt-in index source consumer retains this policy reference in detached
workers and obtains credentials at paced HTTP request/retry and authenticated
Git fetch boundaries. Authenticated fetch prunes only its explicit owned
remote-tracking refspecs, so a deleted upstream branch cannot resolve to stale
local content. Renewable source connectors default to the owned `origin`
remote, ignoring ambient remote selection while honoring an explicitly passed
remote; native connectors retain their existing environment behavior.
Renewable mode also ignores ambient Git ref/fetch settings in favor of the
explicit selection or fetched remote HEAD. Remote-tracking refs are verified
by their exact fully qualified name, then pinned by commit ID; a colliding tag
cannot select stale content or resurrect a pruned branch.
Renewable GitHub connectors likewise ignore `AGENT_INDEX_GITHUB_API` and default
to the selected public GitHub API origin unless `api_base` is explicitly
supplied. Native connectors retain their ambient API selection. Acquiring a
fresh credential never authorizes an ambient alternate destination.
First-touch Git materialization creates only a dedicated
bare source checkout; it does not copy an incumbent database or edit an adopter
checkout. Unselected native source behavior remains unchanged.

Focused tests use real maintained cryptographic signatures/JWE, an explicit
synthetic test issuer/CA, real verified TLS and separately owned OS processes.
They cover two source families/two corpus grants, denied role/fleet/service/
source/generation/expiry/PoP, correlation/recipient/replay, outage and restart.
Synthetic providers exist **only in test fixtures**, with no host credential,
remote, deployed-copy or durable-queue access.

This is not production enrollment, full CLI compatibility, a managed
Gateway/node lifecycle, the source-core image proof, Linux container routing,
native corpus transfer or actual token ingestion. Those closure gates remain
coordinator/index/provider/deployment-owned under #6443/#6383/#6240/#6424.
The bounded runtime's local end-to-end evidence does not substitute for the
coordinator's live external-venue/source-image/native-canary proofs.
