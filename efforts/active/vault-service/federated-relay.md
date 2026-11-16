# Federated-relay service slice

Parent and full completion authority: [Vault Service](README.md).
Coordination: #8729. This document proposes the implementation/qualification
sequence before code; it does not claim installed readiness or activate a host.

## Selected direction and intent reconciliation

The current source assignment selects the existing `agent-vault` service as
the federated auth-relay owner. It connects through a deployment Controller's
local exposure of the protected Gateway endpoint and announces readiness to
accept auth requests. The Controller, not Vault, owns the tunnel.

This closes the reviewed Vault vision's
[explicitly trusted federation](../../../visions/vault/README.md#explicitly-trusted-federation)
and [independent local service](../../../visions/vault/README.md#central-fleet-and-standalone-local)
intent. It specializes the initial proposal's source placement: reuse
`plugins/agent-vault`, not a second `services/vault` daemon for this slice.
It preserves credential lifecycle/provider custody, explicit interaction and
the remaining durable-transaction obligations rather than declaring them done.

## Delivery

- [x] **R1 - Real federated service flow.** Add explicit opt-in federated-relay
  configuration to the existing independently installed Vault runtime. Its
  service lifetime owns connection, bounded reconnect and shutdown. Consume
  the Controller's local Gateway conduit without creating a tunnel, proxy
  daemon or second local Vault listener. Through the existing authenticated
  Controller-service channel, expose an explicitly selected account-source
  handler backed by the existing local credential custodian. Prove actual
  Gateway-routed consumer demand and an authorized confidential response.
- [ ] **R2 - Selection and availability.** Compose existing Gateway source
  inventory/dispatch and cache-first selection rather than adding consumer-side
  Controller retries. Bind provider/account/consumer/source grants independently
  of Gateway admission. Preserve source-lease meanings separately from actual
  credential expiry and native execution authority. Unsupported provider
  semantics remain explicit; do not relabel an opaque acquisition as silent.
  The first implementation uses normal bounded provider retrieval, including
  its ordinary refresh, rather than blocking on perfect cache-only support.
  Credential expiry may be explicitly unknown; it must not be synthesized from
  a source lease. Preserve the stricter cache-only availability contract as a
  separately qualified capability.
- [ ] **R3 - User action and retained work.** Integrate auth-needed outcomes
  with Vault-owned durable transaction/interaction authority from the parent
  plan. Cache discovery and silent renewal never start a browser/native flow.
  Explicit attended action, current attempt/nonce, bounded completion and
  first authorized fulfilment remain distinct from channel or UI lifetime.
  Preserve pending/unknown outcomes through disconnect and replacement.
- [ ] **R4 - Independent distribution and adoption.** Package the optional
  relay dependencies and service construction through existing native installer
  and immutable-runtime owners. Preserve standalone/local defaults, existing
  credential consumers and incoming-ready explicit replacement. Qualify actual
  installed service readiness and adopter use separately from source/release.

R1 is the first complete functional flow, not parent completion. After it passes
its required publication checks, publish promptly and steward through merge,
then continue R2-R4 and the retained parent gates. Do not stop at scaffolding,
an announced offer or a successful transport handshake.

## Conduit and identity contract

Use the existing Gateway service SDK and its explicit running-peer support
selection. A Controller-owned local endpoint changes the TCP route, not the
canonical HTTPS origin, HTTP Host, TLS server name, request-proof target or
application/source permissions. The connector must support that route explicitly
and qualify it against a real TLS peer; localhost alone is not permission and
hostname verification must not be disabled.

A versioned non-secret conduit observation identifies the canonical Gateway
origin, loopback connect address/port, Controller connection generation and
ready/unavailable transport state. It contains no access token, private key or
source capability. Missing, stale, unavailable or unsupported selection cannot
silently become a direct-network route. Vault independently authenticates,
negotiates the existing channel profile and enforces exact source/caller grants.

Use code-registered providers and explicitly provisioned key/policy references,
not arbitrary configured import strings, commands or remote backend URLs.
Bootstrap Gateway admission must not depend circularly on obtaining the token
over the not-yet-established Vault channel. Existing login-scoped credential
providers retain their issuer/account/native-session authority.

Readiness distinguishes a running Vault, ready conduit, authenticated attached
channel, admitted service offer, provider availability and consumer readiness.
An offer means readiness to receive its declared requests, not proof that every
credential is cached or that a consumer can use it. Withdraw or invalidate offers
when their serving generation is lost; reconnect never replays requests from the
old channel or promotes an uncertain credential operation into known failure.

## Lifecycle and compatibility

Reuse Vault's current listeners, native supervision, generation and shutdown
owners; do not attach renewal responsibility to a CLI, plugin hook or UX.
Optional relay construction must not turn a missing Gateway, SDK profile or
credential provider into failure of an otherwise valid standalone Vault.
Explicitly selected but invalid relay configuration fails visibly, not as a
silent standalone fallback.

Add support provider-first and identify contracts separately from package
versions. Old local clients retain existing syntax and behavior. New relay
selection against old Gateway/Controller contracts refuses before credential
disclosure or admission; no trial-effect/downgrade replay is permitted.
Use incoming readiness and complete current selection for explicit runtime
replacement, preserving state and unrelated session keepers. Service start and
accepted work must not die with the updater/client cleanup boundary.

## Validation

- [x] Actual isolated TLS Gateway + Controller connector + running Vault
  service + real account custodian + bound consumer flow with synthetic
  credentials; this must cover the service entry/lifetime, not only a library.
- [x] Canonical-origin/Host/SNI/proof preservation over a distinct localhost
  TCP route, wrong identity/CA refusal, unavailable/stale conduit and no
  implicit direct-network or environment-proxy fallback.
- [x] Exact independent source/account/recipient/host/action/capability grants,
  denied caller/source, installation replacement, expiry, provider miss/error,
  bounded request counts/deadlines and no secret-bearing logs/status.
- [x] Offer publication/withdrawal, tunnel/channel loss, reconnect, service
  shutdown, cancellation and uncertain accepted replies without automatic replay.
- [ ] Local-only old/new clients, disabled mode, unsupported active peers,
  optional dependency absence and current standalone/native service tests.
- [ ] Fresh installed optional profile, repeat setup, update, failed-candidate
  retention, actual source-independent service lifetime and exact cleanup
  through the existing platform test owners; report unexercised lanes honestly.
- [ ] Source-loss/failover/all-unavailable and explicit attended-auth transaction
  controls, plus actual consumer adoption/renewal in an authorized deployment.

No live credential, account login, unlock, provider/browser interaction, host
installation, tunnel creation or shared-service rollout is authorized by this
plan. Keep those adopter decisions and native custody with their current owners.

## R1 source acceptance

PR #12709 merged to `dev` at `2026-11-15T15:13:53Z` as
`a16a6ef0f4ea405fad6fdd38118d77192e5f533d`, from exact head
`732cc405bde17d4b0d5952989d5f7808faca655e`. Required CI
`43726497310` passed; the fresh owner-authored Comment review
`5911423447` recorded no open findings. The managed merge used the
repository-authorized exception to the additional code-owner vote, not a CI
exception or a claim of human security review.

The independent Windows service/lifetime/relay packet passed 143 tests with
one platform skip; the real TLS Gateway/route/source packet passed 134.
Machine authentication uses the validated key representation required by its
actual signer. Native controls retain the original cleanup owner through
repeated cancellation, confirm CLI/descendant exit, preserve a predecessor
after invalid selected trust, and keep later CA failures inside bounded
reconnect without taking local Vault consumers down. All 25 changed Python
files passed strict Linux/Windows analysis with the Python 3.10 target retained.
The exact admitted Linux unit packet passed with coverage; source and generated
archive/producer guards passed without weakening their inventory or policies.

This accepts R1's isolated service/provider/consumer source flow only. It does
not qualify installed optional profiles, every mixed-version platform lane,
live credentials, release/adoption, R2/R3, or the full Vault parent. The remaining
validation items and R2-R4 stay open; publication is not runtime readiness.

## Selected cache and provider direction

Extend the existing [Vault vision](../../../visions/vault/README.md#independent-cache-and-standard-auth-providers)
in place: Vault owns an independent protected cache and may act as a centralized
broker; KeePassXC is one backing store, with code-registered `gh`, `az`, GCM and
other standard-source drivers. This is source architecture acceptance, not an
installation, credential-export, enrollment or interactive-login grant. Reuse
the existing Vault and Gateway owners rather than introducing a parallel
service for the initial flow.

## Documentation and performance impact

Implementation updates the actual Vault configuration/service operator docs,
Gateway connector contract and focused test commands; the initial architecture
remains historical except where explicitly superseded here. Existing local
setup must not accidentally acquire a Fleet/Gateway dependency.

Keep reconnect and provider work out of ordinary ping/hook/render callbacks.
No new per-request process, filesystem tree scan, telemetry service or polling
daemon is needed. Measure the composed source path and retain bounded work,
provider timing and outstanding-request custody; a timeout alone is not latency
qualification. Native/cold/warm/live results remain separate evidence tiers.
