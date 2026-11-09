# Size-qualified HTTP report responses

Back to the [existing report contract](owner-report-read.md),
[selected native owner](native-report-owner.md) and [canonical effort](README.md).

**Agent-recommended contract reviewed in #11694 under #11692. The finite SDK
prerequisite merged in #11699; report-profile/native-budget source composition
merged in #11702. No installed or live-provider support is claimed.**
This closes part of
[one service contract](../../../visions/plugins/agent-pull-requests/README.md#cli-through-one-service-contract)
and follows
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
Existing caller, credential, provider, transport and lifecycle owners remain.

## Source findings and finite choice

The existing service channel already permits bounded 8 MiB responses in
acknowledged 16 KiB chunks within 64 KiB frames. `ServiceCaller.request`
already accepts a bounded `response_limit`. These are not unlimited streams.

The caller-aware `gateway_service_handler.ServiceResponse` constructor and
responder separately enforce 32 KiB. The PR report reader, Gateway serializer,
client and native capture use that same finite bound. Raising only the PR
client limit would not qualify the actual authenticated serving path.
An identity-less HTTP backend is not an alternative to the caller-aware handler.
See the actual [Gateway profile](../../../fleet-manager/docs/gateway-service-profile.md).

Select one provider-first, opt-in **bounded caller-aware response expansion**,
then a parallel size-qualified PR report profile over that existing channel.
Do not add another transport, listener, chunk store, credential issuer, watcher
or long-lived report owner. Preserve all physical frame/chunk, total-response,
queue, concurrency and deadline ceilings.

## Shared response owner first

The SDK supplies an explicitly selected response representation and handler
budget capable of a complete buffered response above 32 KiB, never above the
existing `RESPONSE_BYTES` ceiling. Selection belongs to trusted deployment code,
not an HTTP body, header, callback label or configuration-selected executable.

Ordinary `ServiceResponse` construction and an unselected handler retain their
current 32 KiB behavior. A larger response must be admitted by the actual
executing handler/binding and response constructor before headers are emitted.
No new package-version equality rule or caller-granted authority is introduced.
Missing or old APIs refuse the optional path rather than bypassing the check.
The actual selected response budget must reach the handler through the
executing SDK request path, process-local like its lifetime. It is not a new
serialized caller-context field or an offline permit. This lets a consumer
refuse a larger profile when its real handler or local embedding still has the
old limit, rather than trusting a separately supplied matching number.

Reuse the original authenticated caller context, request lifetime, connector
exchange and chunk/ACK emitter. Identity, policy, expiry and cancellation
checkpoints remain in force through delivery and EOF. A large response cannot
release request custody early, become a successful partial EOF, or make drain
forget an admitted handler or native cleanup.

The first scope is the existing outbound caller-aware `HandlerBinding` path.
The separately buffered in-process local-handler profile keeps its current
limit and must not advertise this larger path without its own qualification.
There is no new wire transport or identity-less proxy fallback.

## Parallel report profile

Keep `/v1/read/<target>/...`, `agent-pull-requests.report-read/v1`,
`owner_executed_report/v1` and their strict 32 KiB exchanges unchanged.

An explicitly selected source host may additionally supply:

| Selection | Reviewed identity |
|---|---|
| Routes | `/v2/read/<target>/capabilities`, `threads`, `diff` |
| Protocol | `agent-pull-requests.report-read/v2` |
| Profile | `owner_executed_report/v2` |
| Request ceiling | Existing `BODY_BYTES` |
| Response ceiling | Actual admitted handler/reader bound, above `BODY_BYTES` and at most `RESPONSE_BYTES` |

Support comes from the actual configured executing SDK handler and report owner,
not the latest payload or matching labels. A host whose selected transport
cannot admit the larger response refuses v2 before advertising it or acquiring
a provider report. Default watch-only hosting remains unchanged.

Capabilities describe the exact configured report scope, operations and actual
byte ceilings. V2 read requests retain the existing authenticated binding and
report scope, positive PR number and finite work budget. They additionally carry
`expected_response_bytes`, copied by the client from its negotiated declaration.
It is a required current-contract expectation, not permission to increase a
receiver's limit. Reject mismatch before owner resolution/body acquisition and
recheck selection through publication. Unknown fields and boolean/nonfinite
numeric inputs remain invalid.

Responses preserve the whole native report and original owner exit, scope,
operation/number, context and observation interval. They identify v2 and the
actual response ceiling so the client can validate the original negotiation.
These remain configured owner-executed reports, not observed-principal
generations, write eligibility or current-candidate guarantees.

The source CLI facade may use an explicitly injected v2 client; its default
host, v1 behavior and ordinary native commands remain unchanged. Caller request
JSON never selects an executable, configuration file, endpoint, account or
credential. Old/missing/rolled-back v2 receivers refuse without a v1, native,
other-forge or second-dispatch fallback.

## One finite exchange, complete evidence

Pass the admitted per-request byte budget through the actual owner seam before
provider capture. V1 calls retain their original capture bounds; configuring v2
must not silently increase an old caller's allocation. The selected native owner
still resolves actual policy, verifies the explicit account and keeps one fixed
credential under the original live request.

Count the complete encoded exchange, including binding/metadata, JSON escaping
and additive fields. Cap provider capture, server encoding and client collection;
reject over-budget or malformed exchanges as unavailable, never truncate a diff,
drop fields, invent partial completeness or substitute a successful error report.
Actual provider refusal/error reports remain whole valid exchanges with their
original nonzero owner outcome.

The enclosing caller deadline covers challenge/request work, acquisition,
delivery, full response collection and validation. Receiver work retains its
actual admitted budget, expiry and original disconnect signal; no per-stage
deadline reset, automatic retry or abandoned worker is allowed. Preserve the
SDK's separate delivery ownership and existing disconnect/uncertainty semantics,
without claiming synchronized cross-host clocks or atomic remote cancellation.

Provider cleanup and buffered/streamed delivery stay with their existing
request owners until settled. A cancelled client cannot print a valid-looking
partial report. No report body, chunk, credential or identity response enters
replay/conversation storage, durable receipts, telemetry or a persistent cache.
The reader still performs no subscription, replay/attention ACK, comment, vote,
merge or lifecycle mutation; transport ACK/backpressure remains with its owner.

## Serial delivery and validation

Review this contract first. Then deliver finite source leaves in order:

1. Qualify the SDK's opt-in handler response construction/emission while ordinary
   handler and local-profile limits remain unchanged (finite source merged in
   #11699; not installed support).
2. Qualify the explicitly selected v2 reader, Gateway, client/facade and actual
   native byte-budget composition over that SDK, without installed enrollment
   (finite source merged in #11702).
3. Return to the canonical Plan/Validation and record remaining acceptance.

New isolated fixtures, not operator credentials or retained hosts, must prove:

- [x] Actual Gateway/connector/SDK and source CLI delivery of complete reports
  above 32 KiB, preserving UTF-8/CRLF, nested/additive fields, unknown
  completeness, native provider errors and owner-exit disagreement.
- [x] Actual selected transport/handler/owner support before v2 advertisement;
  wrong scope, unsupported local handlers, old SDKs, replaced readers and
  changed byte declarations refuse before body acquisition or publication.
- [x] Exact encoded-envelope and capture/client bounds, including escaping and
  metadata overhead; larger-than-admitted reports remain unavailable without
  truncation, body persistence or a second acquisition.
- [x] The original budget/lifetime through slow authentication, acquisition,
  backpressure and collection; withdrawal, expiry, disconnect and cancellation
  cannot produce successful EOF or release handler/native cleanup custody early.
- [x] Old v1 client/new host, new v2 client/old or rolled-back host, v1/v2
  coexistence and actual new/new TLS transport. Missing support never selects
  an identity-less backend or native fallback.
- [x] Record source/platform/resource evidence separately from installed
  packaging/hosting, live providers, native Windows, larger-than-existing
  transport ceilings and full CLI/HTTP/Dispatch or parent completion.

The [source qualification journal](journal/2026/11.09%20Bounded%20report%20v2%20source.md)
records the final 259-case contained report packet, 4,665-case actor-free unit
packet and current-head approval/required-check merge in #11702. This resolves
only these finite source obligations. Explicit standalone configuration,
installed hosting, live providers and broader consumer acceptance remain open.

The host chooses an admitted finite ceiling; current concurrency is not raised.
Fixture success is not a production memory/throughput claim. This proposal
authorizes no live credential acquisition, service activation, rollout,
production effects or resource release.
