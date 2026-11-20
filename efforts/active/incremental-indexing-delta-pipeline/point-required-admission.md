# Required point-service admission

This **agent-recommended, receiver-first refinement** belongs to the existing
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386) slice.
It follows the [running-service declaration](azure-devops-point-targets.md#running-service-declaration-and-provider-rollback)
and [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md).
Review this contract before implementing it; its merge does not migrate a
caller, activate work or qualify a rollout.

## Gap and narrow expansion

A pre-declaration provider already accepts the old v2 hinted request as retained
work. Positive health followed by that old POST can therefore lose a caller's
required point semantics after rollback; a 404 is not the only possible outcome.
Carry the requirement on the existing `POST /reindex/hints` operation itself.
Do not add a registry, prerequisite command, endpoint or second identity system.

Add optional `required_point_service_version` to the strict hinted-request DTO.
Its absence preserves all existing v1/v2 intake behavior, including paused
retention. Presence requires the exact integer `1`; reject booleans, null,
strings, floats and unsupported versions. It requires typed v2 hints,
`full: false` and the complete matching selected point scope. Do not silently
drop the property, convert it into ordinary retention or redirect to the legacy
queue. Prior providers' existing unknown-field rejection refuses this new form
before queue effects, even though those providers accept the old v2 form.

Extend the existing optional `agent-index.point-service` v1 health object with
`required_admission_versions: [1]`. This additive capability declares the new
request/receipt semantics; existing v1 point-service metadata alone does not.
The capability says which requirement the receiver understands, not that its
current lifecycle admits it. Older declaration readers may ignore the field.
A future opted-in caller must require this capability as well as the existing
paired, scope, representation, coverage and execution-admission checks.
Do not change the legacy `indexing_contract` or imply general v2 execution,
HTTP component reads or complete-discussion coverage.

## Effect-side owner and admission

For this opt-in path only, require the existing installation/instance request
headers to select the same peer observed through discovery. Require a nonempty
instance token, even where an installation ID is legitimately empty, and reuse
the current control identity authorizer for equality checks. Do not weaken that
authorizer, mutate shared client identity or require lifecycle transaction
authority for indexing. These selectors prevent stale/wrong-peer requests;
they are not new authentication or cryptographic attestation.

The actual receiving application's installed production executor/reader pair
must match the complete typed-hint scope and admit execution at the queue
admission boundary. Factory presence, a custom callable pair, executor-only
selection, a cached health result or matching package versions do not suffice.
Missing, partial, unselected, paused, failed, draining, stopped or changed-scope
selections refuse the requirement before enqueue or notification. Return a
specific unsupported-contract/admission outcome and supported recovery, not an
empty success or automatic activation.

Use the existing service/lifecycle and settled-intake owners to order admission
against pause, drain, stop and cancellation. An accepted operation precedes
admission closure and remains joined through its queue write/notification;
otherwise it refuses without those effects. A positive check before an
unprotected asynchronous handoff is insufficient. Capture the selected peer,
scope and accepted requirement at that admission; do not reconstruct an
acceptance claim from a later health observation. No new source lock, store
preparation, provider request or model-readiness work belongs in admission.
Existing effect-time governance, resource limits and retained-state checks remain.

## Receipt and unknown outcomes

On successful required admission, retain the existing `accepted` and `task`
fields and add `point_service_admission` with
`id: "agent-index.point-service"`, exact integer `version: 1` and the existing
complete `scope_fingerprint`. Include the existing `installationId` and
`instanceToken` identity fields for this path. These values confirm the checked
requirement at admission, not execution completion or continued activation.
The accepted task must retain typed v2 hints whose union contains every
submitted target/correlation, with the same source, scope and incremental mode.
Do not decode that receipt through the v1-only `ChangeHints` parser.

A future caller validates that explicit receipt, peer, scope and typed union.
Missing, malformed, contradictory or lost receipts after submission mean
unknown acceptance, not proof of refusal. Never retry under old syntax, replay
against another generation, acknowledge completion or discard a retained claim
from such an outcome. Preserve the existing queue/pending recovery owners.
An accepted claim may remain queued if execution later pauses or fails; recovery
is explicit, and the admission receipt is not an applied-content certificate.

## Sequence and focused qualification

First implement and qualify this receiver expansion, leaving ordinary clients
unchanged. Then review a separate opt-in caller migration requiring the new
capability and identity-bound request/receipt path. Keep the existing v1 client
API and its receipt parser unchanged in the receiver step. No old form is
retired, and optional feature incompatibility must not block runtime replacement.
Rollback preserves pending parents, correlations and uncertain outcomes;
restoring a capable provider starts paused under the existing recovery owner.

Qualify these cases through the real request parser and HTTP serialization seam:

| Control | Required assertion |
|---------|--------------------|
| Old caller/new receiver | Absent requirement retains current v1/v2 behavior, including paused retention and unrelated legacy queue progress |
| New required form/prior receiver | Prior strict DTO rejects the property without queue writes or notification; ordinary v2 remains retainable |
| New/new admitted pair | Exact identity/scope selects only the isolated point queue and confirms the required receipt with the complete typed union |
| Malformed requirement | Null, boolean, string, float, unsupported version, v1 hints and full/source/scope mismatches refuse before effects |
| Stale positive discovery | Changed peer, absent capability, custom/executor-only selection and admission closure cannot silently downgrade the request |
| Admission race/cancellation | Pause/drain/stop before admission refuses; an already admitted enqueue is joined and truthfully receipted or retained as unknown |
| Receipt failure/rollback | Missing or invalid confirmation and lost returns do not cause old-form replay, claim loss or false completion |
| Retained reopen | V2 claims/correlations survive supported rollback; old executors cannot consume them, and capable reconstruction remains paused |

Put small pure parser/receipt/selection contracts in required unit CI. Reuse the
bounded opt-in HTTP/native service/reopen lane for actual queue and lifecycle
ordering, without repeating unrelated-corpus scale or adding live-provider work.
Keep every independent parent Plan/Validation gate open; this contract grants no
new caller, wire read, automatic recovery, source activation, repair or rollout.

Parent: [Incremental Indexing Delta Pipeline](README.md).
