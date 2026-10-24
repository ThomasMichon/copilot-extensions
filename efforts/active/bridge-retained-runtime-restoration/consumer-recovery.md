# User-owned consumer enrollment and lossless recovery

Back to the [effort](README.md) and [custody contract](custody-contract.md).
**Agent-recommended prospective design, incorporating operator direction.**
Normal architecture review precedes implementation. No API, deployed proxy,
native qualification or change to installed authentication is claimed here.

## Scope and governing intent

Bridge and Dispatch stay inside the user's own execution/access boundary.
Do not build a per-agent identity service, consumer-token hierarchy or
independent cryptographic native attestation prerequisite for local cooperation.
The service model's
[user-owned coordination boundary](../../../visions/plugin-services/README.md#user-owned-coordination-boundary)
owns that intent; [service transport](../../../docs/patterns/service-transport.md)
owns endpoint selection and secured boundary crossings.

This slice specializes Bridge's existing installation, registration, inbox and
SDK owners. Dispatch shares the trust posture, but its task/execution and
adopter-effect acknowledgements remain separately owned. No second queue,
outbox, credential store, scheduler or operation ledger is introduced.

## Access is different from custody

| Boundary | Proposed treatment | What it does not establish |
|----------|--------------------|----------------------------|
| Same user's admitted local callers | Rely on the established user-scoped endpoint/access controls; no additional consumer token | Native send, consumption or settled work |
| Container network bridge | Authenticate through the existing network owner's proxy wrapper; keep the backend private and inaccessible through a bypass | Global trust for the container or new execution authority |
| Other host or trust domain | Retain its explicitly secured transport and owner-approved ingress | Permission from a caller label, forwarded header or mere reachability |
| Unknown or inadequately isolated exposure | Refuse qualified enrollment before returning retained message bodies | An excuse to fall back to unqualified delivery |

A loopback address is not by itself a user-specific access control. Existing
shared-token compatibility remains in place until an independently reviewed,
qualified user-scoped transport replaces its access function. This refinement
adds no new local token requirement and does not silently remove current
protection. Container authentication belongs at the network proxy, not in a
new Bridge/Dispatch identity hierarchy. The proxy owner must constrain backend
reachability and reject spoofed client-supplied authentication/identity headers.
No network or proxy configuration is changed by this proposal.

Within that admitted scope, enrollment accepts the actual consumer owner's
observations without a second cryptographic ceremony. The daemon derives its
installation/database association through its existing installation owner, not
from a client-selected installation or path. The extension binds its current
SDK session, extension process/incarnation and supported handoff contract.
Registration fields and hashes are correctness observations, not transferable
authorization credentials or proof of native execution.

Recheck that same binding at poll and the final SDK boundary. A changed
installation, SDK session, alias successor or consumer incarnation cannot
inherit an earlier send opportunity. Renegotiation never rewrites old handoffs.
Stale/missing observations refuse visibly while preserving original work.
An ordinary legacy consumer is not retroactively enrolled or restore-qualified.

## Delivery and recovery

The existing durable inbox row is the source of truth. Before reporting
admission, retain the original operation identity, complete payload and producer
association. Before exposing its body to a qualified poller, commit the original
handoff under the DB owner's durable transaction. Retries use that same
operation and input; a reused key with changed input refuses explicitly.

Use integrity hashes only where they serve a named recovery need: detecting
corrupted retained input, accidental identity reuse or a changed replay payload.
A matching hash does not authenticate a peer, prove SDK acceptance or certify
consumption. Corruption preserves the original evidence and blocks delivery
visibly; no repair path discards the row or substitutes an empty payload.

The consumer calls the existing one-shot admission owner immediately before
the SDK effect and does not cache a true result for another call. Closure after
that commit sees an outstanding window, even if the network disconnects before
the SDK call. False, timeout or a lost admission reply grants no send permission.
The DB commit alone is not SDK acceptance.

| Interruption boundary | Retained state and recovery |
|-----------------------|-----------------------------|
| Admission reply lost | Retry/reconcile the same keyed admission; never insert a replacement message |
| Poll reply lost | Re-observe the same durable row/handoff; do not mint another send opportunity |
| Final-admission reply lost, or consumer exits before send | Preserve the outstanding original handoff; reconcile through its owner, not inferred non-delivery |
| SDK send reply lost or throws after possible acceptance | Record/retain unknown acceptance and payload; do not acknowledge, drop or blindly resend |
| SDK accepted, consumption not yet observed | Retain distinct acceptance and outstanding consumption; no delivery-complete shortcut |
| Consumption observation or outcome-report reply lost | Reconcile attributed consumption and retry recording the same outcome, not the SDK effect |
| Registration expires, consumer restarts or aliases change | Preserve old payload/handoff/outcome; the successor does not inherit permission from its label |

Missing replies and timeouts never acknowledge or garbage-collect accepted
messages. Unknown remains visible, recoverable outstanding work, not a terminal
discard or a successful delivery. Cleanup, bounded in-memory caches and
represented-event queues cannot be the only copy of custody or consumption
evidence. Retry outcome reporting through the existing durable owner, not a
parallel outbox; do not prune the original row before its reconciliation policy
has retained the required evidence.

Before sending again, the actual effect owner must prove either nonacceptance
of the original attempt or an existing deduplication contract for that same
original operation. Do not invent an SDK idempotency key or infer absence from
an empty cache, a missing ACK, process exit or elapsed time. If the selected SDK
cannot reconcile the original send, remain visibly blocked with the message
intact. This is lossless retention with honest uncertainty, not a claim of
unconditional eventual delivery or universal exactly-once execution.

Interrupt handling must not reconstruct pending messages from the extension's
bounded in-memory list and resend them under new authority. Associated control
and interrupt effects remain unsupported until their actual owner has a
qualified fence and recovery path. Ordinary unassociated legacy behavior stays
separate and must not be selected as a downgrade for retained work.

## Source sequence and review boundaries

1. Land this trust/recovery refinement through normal architecture review.
2. Extend existing local registration and installation owners with negotiated
   enrollment; keep unsupported ingress explicit. No new credential ceremony.
3. Wire retained poll, one-shot final admission, SDK outcome reporting and
   attributed consumption through the actual extension helpers. Preserve
   non-blocking SDK event handlers and serialize each consumer's delivery.
4. Exercise interruption/recovery controls below, then qualify actual native
   boundaries separately. Keep unsupported SDK/platform cases explicit.

Cross-container proxy implementation and deployment stay with their existing
network owners; qualified cross-container enrollment remains unsupported until
that boundary is implemented and qualified. Local source progress does not
require operating a container or replacing a production runtime.

## Validation obligations

Use the [existing bounded portfolio](validation.md#bounded-execution-and-portfolio).
These are required implementation cases, not executed evidence.

| Case | Required assertion |
|------|--------------------|
| Local trust and registration | Existing admitted user access works without a new consumer token; foreign installation/current-session/incarnation mismatches refuse before SDK effects |
| Proxy boundary | A synthetic admitted container request reaches the intended private backend; missing authentication, spoofed headers, wrong scope and direct bypass fail before inbox exposure |
| Every tabled interruption point | Inject reply loss/disconnect/timeout/process loss; reopen the real DB and recover identical original payload, handoff and distinct outcomes, with no duplicate SDK invocation |
| Concurrent closure/final admission | Exactly one serial order; the post-admission window stays outstanding until actual outcome reconciliation |
| Acceptance versus consumption | Acceptance alone cannot settle delivery; root-session attributed evidence records consumption separately without certifying semantic completion |
| Lost consumption/report reply | Original outcome recording is idempotent and recoverable without another send; missing native evidence remains visible unknown |
| Stale context and bounded-cache eviction | Restart, alias rollover, registration cleanup and tracking eviction neither delete original work nor renew send authority |
| Payload integrity/key reuse | Corrupt or changed retained input refuses visibly with evidence preserved; matching content cannot bypass admission |
| Unsupported SDK and interrupts | No invented idempotency, legacy downgrade, hidden retry, control application or interrupt/resend side effect |

Controlled SDK tests verify the real consumer helper wiring but do not prove
native behavior. Independent-process tests retain real DB durability and crash
boundaries. Linux/Windows native results, release, installed adoption and full
restoration remain separate gates in the parent [validation plan](validation.md).
