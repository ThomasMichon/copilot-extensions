# Existing-owner workspace service/client API

`WorkspaceOperations` composes the existing `OwnerReadHandler`,
`GenerationCache`, `ReplayStore` and protected `execute_review`. It performs no
provisioning/adoption, source fetching, installation or credential acquisition.
Construct it only inside the current owner pipeline with an authorized fixed
native repository/principal scope, read binding and actual write-context factory.
The factory accepts the selected PR number and returns the normal pinned context;
it is not reconstructed from caller JSON, cached data or a claimed role.

Pass optional `workspaces={(caller_principal, target): operations}` to the existing
`WatchService` or `WatchGateway`. Each key must already have a watch scope and the
same ReplayStore. The optional cache must share its root, namespace and persisted
service binding. Absent support preserves every existing watch response field.
The standalone launcher does not accept arbitrary callback configuration and
does not advertise workspace operations without actual adopter integration.

The existing handler serves `/v2/review/<target>/<operation>` over its normal
authenticated Fleet connector, request lifetime and task/drain owner. Requests
carry the existing `ReplayBinding` plus `request` data. That binding is checked
against the admitted caller; native provider identity remains separate from the
Gateway principal and store namespace. Request-side scope/credential overrides
are not supported.

## Finite operations

The source API is `WorkspaceOperations.execute(operation, payload,
expires_at=..., checkpoint=...)`; native callers use `NativeWorkspaceClient`.
Fleet clients use `WorkspaceClient` with the existing `ServiceCaller`, scoped
`ReplayBinding`, route prefix and expected native scope. This adds no transport,
auth client or operational command. Call its read-only `capabilities()` before
other remote operations; old handlers return unsupported rather than falling
back to ordinary watches or another provider.

| Operation | Required request fields |
|---|---|
| `capabilities` | none |
| `load` | `number`, `expected_current`, `budget_seconds` |
| `sync` | load fields plus exact retained `previous_id` |
| `lookup` | `number`; optional exact `generation_id` |
| `chunk` | `number`, exact `generation_id`, `offset` |
| `inspect` | `number`, original `operation_id` |
| `submit` | `number`, immutable `intent`, separate `source_attachment`, `budget_seconds` |

Load/sync use the current protected read owner and explicit expected-current
cache CAS. Sync is a full section refresh plus the existing normalized comparison,
not a fabricated provider delta. A partial section remains incomplete. Results
include publication status and observed revision even when CAS conflicts; that
does not make the observed generation current. The compact comparison retains
changed sections/invalidations and generation ID rather than embedding the full
generation in a physical frame.

`materialize(number, generation_id)` resolves a descriptor and reads only that
immutable ID. Each 8 KiB decoded chunk carries offset, exact total, native scope,
generation ID and full encoded-content hash. Both clients use the same bounded
schema/content validator. Old generations stay selectable when current advances;
eviction/disconnect/corruption never substitutes current or silently re-observes.
Fleet's existing 32 KiB physical frame is unchanged.
The executing owner memoizes validated immutable bytes within one bounded
16 MiB total read buffer. Each chunk rechecks the existing cache descriptor,
plain file identity and caller/owner lifetime, not the whole generation.
Changed content/evicted files refuse rather than borrowing another scope or current
ID; identical republication revalidates once and refreshes only the file stamp.
The buffer has no durable file, effect receipt or new lifecycle; final assembly
still verifies the complete hash/schema. This avoids quadratic disk/JSON work.
The client paces finite chunk requests at half the existing SDK challenge refill
rate by default, without another poller or retry loop. An existing transport owner
may supply async `read_admission` to share its actual IO pacing across consumers.
Each request still obtains its own normal challenge/token; none is cached or
reused. Inline pacing is cancellation-aware transport flow control, not a
delegation permission/read-count ceiling. Shared saturation still reports the
actual 429/unavailable outcome; it does not trigger automatic replay.

Client `attach_source(generation, source_owner, **limits)` uses the merged local
attachment API in the reviewer venue. Its actual selected source owner verifies
already prepared objects and retains immutable refs; it does not fetch, clone,
checkout or build. The separate attachment is passed to explicit submission,
not published into metadata or accepted as remote authority.

Submit reloads the retained generation and executes the merged protected owner
pipeline against the original intent/source. Fresh native principal, revision,
discussion/ownership and caller policy checks remain in that executor. Durable
intent/in-flight/partial receipts use the same ReplayStore; there is no upload
staging store or second operation ledger. A lost transport result is unknown,
not a failed effect. Retain the original operation ID, inspect/reconcile through
the same owner, and never invent a new identity or infer success from equal values.
The existing SDK's explicit pre-dispatch credential/challenge failures retain
their known-no-send classification. Only its `outcome-unknown` transport error
marks a possibly dispatched submit; ordinary post-dispatch HTTP/response failures
remain conservative. No new stage, credential issuer or speculative reconciliation
is invented for an SDK-confirmed unattempted request.

Oversized submit envelopes refuse locally before dispatch. Large immutable read
generations are framed, but this first wire does **not** upload arbitrarily large
intents/source manifests. An oversized result remains unavailable/unknown with
durable receipts retained; it is not truncated to claim success.

## Lifetime, compatibility and limits

Every cache/store mutation and result retains current caller/service checks.
Provider IO stays outside the storage locks. Cancellation closes request lifetime
but the existing gateway keeps its admitted worker through drain; no replacement
writer or untracked thread is created. Explicit v2 adoption still belongs to the
normal lifecycle owner after actual old-reader drain/transition, not startup or
an HTTP operation.
The existing read owner's optional request checkpoint is composed into its
remaining-work callback, retaining caller lifetime through physical-page reads
without changing legacy native callers or actual operation budgets.

Executing capability support does not prove backend availability, actual pinned
write-owner adoption or installed serving readiness. Missing owners return
unavailable. Existing watch clients keep their exact strict capability shape.
Neither package equality nor a new source library upgrades a running service.
Expand the optional receiver first; migrate clients only after that running
receiver advertises workspace/v1 and the requested operation. No watch form is
deprecated or removed by this expansion. Disabling/rolling back the optional
workspace binding makes new clients report unsupported and leaves watch requests
unchanged; clients clear negotiated support on a failed rediscovery.
Any binary rollback must retain running v2-compatible store readers or use the
existing lifecycle owner's drain/transition procedure. Downgrading the durable
envelope, discarding unknown receipts or guessing legacy reader compatibility is
never a supported rollback. This source change performs none of those operations.

Source tests exercise real atomic cache/store, unchanged strict watch clients,
actual handler/native client/Fleet-shaped framing, protected synthetic transport,
concurrent CAS and cancellation/drain. No test uses a live backend or installer.
Actual ADO end-to-end review, prepared-source acquisition, running v2 readers,
production latency/frame suitability and authorized release/rollout remain
unqualified and outside this source-only slice.
