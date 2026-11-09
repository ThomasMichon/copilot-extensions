# Opt-in bounded report responses

Back to the [existing-owner report API](watch-gateway.md#optional-existing-owner-report-reads).

The source API additionally supports `/v2/read/<target>/capabilities|threads|diff`,
`agent-pull-requests.report-read/v2` and `owner_executed_report/v2`.
V1 and default hosting retain their 32 KiB response limit. Ordinary native
`threads`/`diff` commands retain their separate 1 MiB capture and validation
ceiling; they do not require v2 to read reports above 32 KiB.
This is not installed enrollment, live-provider qualification or a rollout.

## Trusted selection

An embedding selects the same finite `response_limit` on its `ReportReader`
and `WatchGateway.handler_binding(response_limit=...)`, then passes that actual
binding to the existing SDK `ServiceConnector`. The limit must be an integer
above `BODY_BYTES` and at most the SDK's existing `RESPONSE_BYTES` ceiling.
The executing SDK supplies that budget on its process-local `ServiceRequest`;
caller JSON cannot select it. Missing APIs, old/default handlers, mismatched
reader limits and the separately buffered local-handler profile refuse v2
before owner resolution or advertisement. There is no identity-less backend.

For v2 the reader invokes its trusted resolver as
`resolve_binding(remaining, response_limit=selected_limit)`. The returned
`ReportBinding` must identify that same `response_limit`; its collector must
apply it to the actual provider capture, not merely the final serializer.
The supplied `NativeGitHubReport.resolve` implements this composition under
its original selected account, fixed credential, execution/cleanup budget
and live request. V1 calls still resolve and capture with `BODY_BYTES`,
including when they share a v2-capable host.

## Negotiation and complete exchange

Construct `ReportClient(..., response_limit=maximum)` explicitly to select v2.
This is the client's collection ceiling, not a server grant. `capabilities()`
validates the exact report/replay scope and the server's actual finite
`max_response_bytes`, which must fit that ceiling. `read()` accepts the same
`number`/`budget_seconds` input as v1 and copies the negotiated ceiling into
the wire-only `expected_response_bytes` field. The reader rejects missing,
boolean or mismatched expectations before owner resolution. Unknown fields
remain invalid. A selected declaration changing between requests requires
fresh negotiation, never an automatic retry or v1/native fallback.

V2 read exchanges additionally include `max_response_bytes`. The client checks
it against the original negotiation and validates all existing exchange/native
fields. The Gateway counts the complete encoded response, including replay
binding, metadata, escaping and additive fields, before constructing the SDK's
opt-in `BoundedServiceResponse`. Capture, encoding and client collection each
enforce their actual bounds. Over-budget results are unavailable, never
truncated, stripped of fields or replaced with a successful empty report.
Valid native provider errors remain whole reports with their original exit.

The client uses one enclosing v2 budget, at most the SDK request maximum,
covering authentication, acquisition, collection and final validation. Reads
also bound that exchange by their requested `budget_seconds`. The receiver
retains its own original finite work deadline and expiry, not a synchronized
cross-host clock. Reader replacement, changed limits, scope withdrawal,
disconnect and expiry are rechecked through publication. Chunk/ACK delivery
and delayed native cleanup retain their original SDK/request owners; partial
EOF is not success and drain cannot forget unsettled cleanup.

Reports are not written into replay storage, receipts, caches or telemetry.
No new transport, listener, watcher, queue, concurrency allocation, credential
issuer or physical frame/chunk/total-response ceiling is introduced. Isolated
source fixtures exercise TLS, native children and the injected CLI facade;
installed packaging/hosting, live providers, native Windows and the broader
CLI/HTTP/Dispatch migration remain separate acceptance.
