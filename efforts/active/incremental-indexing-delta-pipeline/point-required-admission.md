# Required point-service admission

This **agent-recommended, receiver-first refinement** belongs to the existing
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386) slice.
It follows the [running-service declaration](azure-devops-point-targets.md#running-service-declaration-and-provider-rollback)
and [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md).
Reviewed in PR #13712 before receiver implementation. Its merge does not
migrate a caller, activate work or qualify a rollout. The
[receiver documentation](../../../plugins/agent-index/docs/component-ledger.md#required-hinted-intake-admission)
records the implemented boundary.

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

## Explicit caller migration proposal

This **agent-recommended, review-first caller step** builds on the receiver
merged in #13715. This proposal changes no runtime behavior. Implement it only
after its separate review merges; caller implementation still needs its own
focused validation and review. It closes the existing incremental-ingestion
vision through the same operation and contract-evolution pattern, not a new
discovery service or a general v2 client conversion.

### API and ownership

Add `AgentIndexClient.reindex_points(change_hints: PointChangeHints)` returning
the existing dictionary-shaped response. The typed envelope supplies the exact
source and complete scope; the method always submits incremental work.
Leave `reindex(...)`, its v1 `ChangeHints` parser, ordinary CLI/MCP callers and
all absent-requirement behavior unchanged. No caller is automatically migrated.
Reuse `_request` and its existing identity headers and read-client restrictions.
Do not introduce a command, endpoint, transport, registry or lifecycle operation.

Keep the method as orchestration in `client.py`; put its small capability and
receipt checks beside the existing `require_point_service` and
`point_scope_fingerprint` helpers in `indexing/point_service_contract.py`.
Reuse the actual `PointChangeHints` decoder and union, not a parallel DTO.
These checks inspect values only: no store open, source acquisition, model
initialization, activation or repair.

### Selected-peer discovery and submission

Require a `PointChangeHints` value and the client's already-selected
installation/instance identity before submission. An empty installation ID is
valid where the existing owner permits it; the instance token must be a nonempty
string. Do not copy identity from health into the client or guess it from package
versions. Compare both `installationId` and `instanceToken` from that selected
peer's health response to the bound client identity.

For each explicit call, use the existing health operation once and require
`require_point_service(..., operation="execute")` for the submitted complete
scope. In addition require a list-valued `required_admission_versions` containing
exact integer `1`, not boolean `true`. Existing point-service v1 metadata alone
is insufficient. Missing/malformed support, another peer, wrong scope or inactive
execution refuses this optional feature before POST with a specific capability
or admission explanation and the existing explicit-selection/recovery guidance.
It must not disable the ordinary v1 API or start a service to make the check pass.

Submit exactly one `POST /reindex/hints` using the existing bound headers,
`source: change_hints.source`, `full: false`, the typed v2 `change_hints` body
and `required_point_service_version: 1`. Health is an observation, not an
admission reservation: the receiver's effect-side peer, pair, scope and
lifecycle checks remain authoritative. No extra preflight or lifecycle
transaction is introduced. One call costs one health request and at most one
POST, independent of corpus size; it does not enumerate or fetch source history.

### Receipt and failure classification

Require an exact boolean `accepted`. For `true`, validate both selected-peer
identity fields and the receiver's explicit `point_service_admission` contract
ID, exact integer version `1` and complete scope fingerprint. Decode
`task.change_hints` as `PointChangeHints`; require the submitted source,
`task.full is False`, the same complete scope and
`stored.union(submitted) == stored`, including every target and correlation.
Return the response only after these checks. This confirms queue admission,
not applied content, completed acquisition or continued activation.

A valid `accepted: false` is refusal, not authorization to retry or downgrade.
A missing, malformed, contradictory or lost post-submission result is unknown
acceptance. Preserve that distinction in the caller's error rather than
presenting it as a safe pre-submission capability refusal. Transport/status
exceptions after submission must not imply that no effect occurred.
Never retry automatically, send an alternate old body, switch generations,
acknowledge work or discard a retained claim. Existing queue/pending recovery
owners retain the uncertain operation. A capability failure before POST has
submitted no indexing effect; it likewise does not authorize automatic upgrade,
activation or fallback.

### Focused caller qualification and sequence

Receiver expansion is complete at the source boundary; it is not proof of an
installed running version. After this caller proposal merges, implement and
qualify the explicit API conditionally against the actual selected peer.
Retain all old forms without deprecation in this slice. Provider rollback either
refuses discovery or rejects the new required property through the prior strict
DTO; it must never cause old-form replay. Runtime replacement remains independent
of optional feature availability and preserved pending state.

| Control | Caller assertion |
|---------|------------------|
| Old API/new receiver | Existing v1 method/parser and paused-retention behavior remain unchanged |
| Explicit caller/current receiver | Real HTTP serialization carries bound headers and required v2 body; accepted receipt confirms peer, complete scope and full typed union |
| Missing or declaration-only capability | No POST; exact-version checks reject malformed lists and boolean-only support without disabling ordinary calls |
| Stale discovery | Changed peer, wrong scope or closed admission refuses the required request without legacy enqueue |
| Rollback after positive health | A prior strict DTO that accepts ordinary v2 rejects the new property without queue writes/notification; no alternate body follows |
| False, missing, malformed or lost receipt | Refusal or unknown acceptance remains truthful; at most one POST, no generation switch, claim discard or false completion |
| Identity and read-only clients | Empty installation/nonempty instance follows the existing identity owner; missing instance, mismatched peer and read-only write attempts cannot bypass it |

Use small pure helper and actual-method tests with inert request owners in
required unit CI. Include malformed acceptance, identity, admission version,
scope and missing target/correlation controls without a combinatorial matrix.
Reuse the existing bounded opt-in HTTP/native lane for actual request headers,
the real strict prior DTO and queue-effect assertions. A test helper that
bypasses `_request` headers does not qualify identity-bound HTTP behavior.
No new full-corpus/native required-CI lane, live-provider experiment, HTTP
component read, automatic caller activation or rollout belongs in this step.

Parent: [Incremental Indexing Delta Pipeline](README.md).
