# Existing-owner workspace service/client integration

Parent: [PR Review Workspaces](README.md). This source-only expansion closes the
existing architecture's service/client integration decision. Contract admission
precedes realization. It creates no second provider, listener, credential issuer,
watcher, queue, ledger or lifecycle owner.

## Source owners and bounded scope

The executing source owner is `WatchService` -> `WatchGateway` -> `ReplayStore`.
Its connector, request lifetime, task admission/drain and writer exclusion remain
the only service lifecycle. `GenerationCache` remains its independently
rebuildable child; review intents/receipts remain in the existing v2 owner store.
Fleet's existing `ServiceCaller` supplies the authenticated transport.

An optional trusted workspace operation binding extends that existing gateway.
The service embedding owner supplies authorized read bindings and actual pinned
write-context factories through its current pipeline. Remote request labels,
stored manifests, callbacks' type names and capability metadata are not grants.
No arbitrary callback/credential configuration is added to the standalone
`services/pr` launcher. Its current deployment recipe therefore does not advertise
workspace support merely because a newer library is installed.

The first integration supports finite normalized load, section-aware comparison
after an explicit full refresh, immutable generation lookup/materialization,
local source attachment and explicit selected submission/reconciliation.
Provider conditional/delta optimization is not invented. Existing provider
evidence preserves partial sections and all observation/revision clocks.

## Wire expansion and discovery

Retain watch v1/v2 endpoints and meanings unchanged. Extend the existing scoped
watch `capabilities` response additively with optional `workspace` metadata.
Absence means unsupported workspace operations, not loss of ordinary watches.
The executing gateway advertises only operations whose trusted owner callbacks,
cache and store support are actually supplied; package version is not support.

The workspace protocol is `agent-pull-requests.workspace/v1`, carried by new
`/v2/review/<target>/<operation>` routes on the same handler/connector.
Every request selects the existing `ReplayBinding` and a bounded operation
payload. The authenticated caller principal/target selects the fixed existing
watch scope; binding equality remains a consistency check inside admission.
The workspace resolver additionally selects the authorized PR number/native
scope. Body account/provider/endpoint labels cannot substitute for that owner.

Operations:

- `load`: acquire through the current protected read owner, validate normalized
  scope and bounded evidence, publish using an explicit expected-current CAS.
- `sync`: refresh through that same owner and compare to the exact retained
  previous generation; expose changed sections/invalidations and publication
  conflict or uncertainty. A short/partial backend response is never empty success.
- `lookup`: return an immutable generation descriptor; missing/evicted data is
  unavailable, not an implicit re-observation.
- `chunk`: materialize only the named immutable generation, with byte offset,
  exact total and content hash. Never substitute the current pointer.
- `inspect`: read the original selected operation's retained intent/action states
  without attempting a backend effect.
- `submit`: load the exact retained generation and invoke the already merged
  `execute_review` over the same ReplayStore and actual owner factory.

The Fleet SDK has a 32 KiB physical body limit. Do not widen it. Immutable
generation reads use bounded base64 chunks of at most 16 KiB decoded bytes,
with exact offsets/total and full encoded-content SHA256. Clients cap total
bytes/chunks before assembling, verify every descriptor/offset/scope and the
complete generation hash/schema. Interrupted retrieval returns unavailable;
explicit lookup of that same ID can resume without backend effects.
CAS publication and generation validation remain at the cache owner; chunking
is transport framing, not another store or operation journal.

Submission is bounded by the existing physical request frame. The client refuses
an oversized intent/source envelope before sending. This first slice does not
claim large attachment upload support, invent a request-staging store or erase
fields to fit. Native library calls retain their own actual payload capacities.
Remote response overflow/transport loss after submission is an unknown outcome;
retain the original operation ID and inspect/reconcile explicitly, never replay
under a new ID.

## Native and local source materialization

The native client invokes these same PR-domain operations directly under its
existing owner, without Fleet or a new subprocess/HTTP client. Both clients
return the same normalized immutable generation and comparison semantics.
Capabilities report real supported protocol/operations and physical limits,
not identical package versions or proof a live service was upgraded.

Source stays in the reviewer venue. Client-side `attach_source` consumes the
materialized generation and its already selected `LocalReviewSource`; it never
fetches, creates a checkout, builds or acquires credentials. The separate immutable
attachment envelope must match the submitted retained generation/range. It is
consistency evidence, not remote resource or review authority.

For submission the service resolves its actual write context under the same
authorized native scope and refreshes principal/head/iteration/discussions and
caller-owned review decision inputs through the merged executor. Review is not
coupled to unconditional CI/merge readiness. Completion presence/values and other
reviewers are never written. Native IDs and confirmed/unattempted/unknown partial
results stay in the existing durable store. Equal current vote/content never
confirms an unknown effect.

## Lifetime and migration

Workspace work is admitted/tracked/drained by the existing gateway task owner.
Provider IO is outside cache/store transactions. Request disconnect/owner loss
withdraws later effects, but already-admitted work remains retained through drain.
No task cancellation or transport timeout releases writer custody early.
Current caller and service checkpoints govern publication, mutation and response.

Expand optional source receiver/discovery first. Migrate clients only after the
executing peer advertises this protocol and required operations. No legacy watch
endpoint or provider fallback is equivalent to workspace operations. Keep old
watch readers and native commands unchanged; no old form is contracted here.
V2 store conversion remains an explicit existing lifecycle-owner operation after
actual compatible-reader transition/drain, never an HTTP request or startup fix.

## Source acceptance

- [ ] Actual cache/store/native-client and same-handler synthetic wire consumers
  prove initial load, unchanged sync, discussion reply/edit/deletion, head/target
  movement and partial evidence without changing observation clocks.
- [ ] Immutable chunk materialization rejects truncation, wrong ID/scope/hash,
  unsupported peers and disconnect; concurrent CAS cannot publish half-current
  data or replace the generation selected by an in-flight reader.
- [ ] Existing watch behavior remains intact with absent and present optional
  workspace support; drain owns admitted source work.
- [ ] Actual merged protected executor/store prove bounded explicit submit,
  durable pre-send fencing, partial/unknown receipt retention and no blind replay.
- [ ] Oversized submit refuses locally; missing current read/write owners are
  explicit unavailable states, not fabricated callbacks or credentials.
- [ ] Focused contained tests, source lint/install/changefile gates and actual
  current-head review/required CI/managed merge pass.

No live backend effects, source acquisition, installation, serving activation or
rollout are authorized by this source slice. Real owner factory/property support,
running v2 reader compatibility, production frame/latency suitability and eligible
ADO end-to-end qualification remain with their current owners and the parent
effort. Synthetic consumers cannot establish those operational facts.
