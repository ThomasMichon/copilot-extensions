# Bounded native GitHub snapshot batches

Back to [Durable PR-Watch Delegation](README.md).

**Status:** Native provider and optional caller merged; serving qualification remains open.
**Tracking:** [#14306](https://github.com/ThomasMichon/copilot-extensions/issues/14306),
part of [#6007](https://github.com/ThomasMichon/copilot-extensions/issues/6007).
The following implementation and validation slices are agent-recommended.

## Purpose and existing owners

Reduce repeated native process, credential and REST collection work when the
existing PR observer refreshes an admitted GitHub population. Do not increase
its selected population, fairness quotas, native concurrency, cycle lifetime
or provider allowance to compensate for per-PR overhead.

This closes the PR-service vision's
[efficient and fair observation](../../../visions/plugins/agent-pull-requests/README.md#efficient-and-fair-observation)
intent. It follows
[service contract evolution](../../../docs/patterns/service-contract-evolution.md)
and composes the existing Worktrees provider, renewable observation credentials,
native process/capture owner and PR-service projection/replay. It adds no
observer, credential issuer, daemon or private forge implementation.

## Native provider contract

The proposed optional `agent-worktrees.pr-snapshot-batch/v1` operation accepts
one explicitly configured GitHub.com repository and one to ten distinct
positive PR numbers. Select a fixed GraphQL query from source and pass only
validated repository, PR and collection-cursor variables. There is no raw-query,
fragment, endpoint or mutation input. The transport performs POST queries to
the selected API's `/graphql` endpoint; scalar REST behavior remains unchanged.
GitHub Enterprise and other providers are explicitly unsupported by this first
optional profile, without disabling their existing scalar operations.

Use the existing typed observation credential source before every HTTP send.
Retain its caller-selected acquisition, expiry, process cleanup and capture
limits. Keep the existing direct transport's HTTPS, HTTP/1.1, no-proxy,
no-redirect and no-retry properties. Do not expose credentials in argv,
diagnostics or response data.

The fixed query requests candidate/lifecycle, labels, numeric review identity
and provenance, and observed check/status metadata. It does not request bodies,
threads, diffs, source or logs. Bound each connection page to 100 and the
template's worst-case fanout to 8,192 nodes per query. The bounded connection
count predicts the documented minimum primary cost of one point per query;
require valid cost/remaining/reset evidence and refuse further operation
reads on unexpected cost, exhausted quota, throttling or missing quota data.
HTTP request accounting and GraphQL primary points, secondary limits and
node limits remain distinct.

Return typed per-requested-PR results and bounded quota/backoff metadata.
Preserve exact selected provider/endpoint/repository/PR identity and head/base
evidence. Missing or inaccessible PRs, GraphQL errors, repeated cursors/IDs,
candidate changes or incomplete required collections remain unavailable,
not empty complete populations. Unscoped errors invalidate the batch.
Pagination stays inside the caller's original whole-operation budget;
do not restart the budget for a new PR or collection.

Qualify GraphQL normalization against the existing REST contract: numeric
review IDs and timestamps, dismissed/older reviews, unknown mergeability,
legacy statuses and check-runs, bounded failure diagnostics, incomplete checks,
and merged/closed PRs. Matching field names alone do not prove equivalence.
Keep the existing 1-MiB capture boundary and explicit oversize refusal.

## Caller and durable backoff contract

Declare native support through the existing selected-runtime contract before
an optional caller uses the new operation. Omitted batch configuration retains
the scalar call shape and supported legacy behavior. Explicit selection on a
missing/incompatible provider refuses before acquisition; do not probe by
calling the new operation and then replaying scalar requests.

The existing observer may group only its already-selected targets of the same
native binding. Different providers, accounts, repositories, credentials or
bindings cannot share a batch. Preserve its fair member/departure selection,
writer lease, per-PR projection/replay and actual native join/drain ownership.
Missing or partial results cannot close watches or members, erase retained
facts, revive withdrawn interest or become synthetic cleanup receipts.

Selected unregistered cache members may pack across intervening scalar cache
reconciliation reads while preserving selected membership, member order, scalar
reconciliation order and both fair cursors. Different-binding active work,
watched/retained/non-cache targets and batch reconciliation remain barriers.
Partial tails retain their original positions for the next eligible group;
ordinary waiting-target and scalar-rollback calls omit the membership hint.
This is internal transport grouping, not a new command, storage revision,
budget or selection policy.

The observing/accounting owner must persist the validated next-eligible query
time in bounded metadata before another attempt. Restart, another PR number
or an update cannot clear provider backoff. REST may continue across a GraphQL
primary-limit reset when independently available, but not bypass a shared
secondary limit. This governs the selected owner's requests, not every other
application using the same account. Do not introduce a global quota service
or another polling loop.

## Serial plan

- [x] Deliver the native provider, typed batch codec, strict CLI and additive
  capability with focused source controls; leave existing callers unchanged.
- [x] Deliver optional batch acquisition in the existing observer and standalone
  configuration owners, including durable backoff and actual serialization/
  projection/replay controls. Keep scalar defaults and old/new refusal honest.
- [ ] Qualify normal release and explicitly authorized consumer adoption
  separately from source completion. Measure serving cost, timing, current
  coverage and unaffected provider behavior before claiming operational gains.

## Validation plan

- [x] Exercise fixed query/variable construction and the actual transport/
  credential boundary: no raw query or mutation input, wrong-target access,
  credential disclosure, automatic retry or budget reset.
- [x] Cover multi-PR results, missing/duplicate/foreign PRs, scoped/unscoped
  GraphQL errors, pagination and candidate changes, numeric review provenance,
  REST check/review equivalence, capture limits and expiry.
- [x] Exercise cost/quota/backoff decoding and owner persistence across
  restart, another PR, interrupted reads and clock/reset evidence; retain
  independent primary-resource versus shared-secondary-limit semantics.
- [x] Cover new/new, old-caller/new-provider and selected-new-caller/old-provider
  parsing, omission/default behavior, supported rollback and explicit refusal.
- [x] Use actual existing observer selection/projection/replay controls for
  mixed bindings, partial results, unchanged observations, withdrawal, fair
  cursor progress, stop/deadline and joined cleanup.
- [x] Compare scalar and batch work counts on synthetic populations. Keep
  required unit CI focused; native cold/warm timing and provider/serving proof
  belong to separately selected bounded qualification.

No source result closes the full parent effort or grants runtime adoption,
repository mutation or another owner's resource authority.

## Selected-prefix local refusal refinement

A selected native prefix may enforce an independent local HTTP allowance before
starting the native batch query. Its known local denial is not provider
throttling or an unknown launched outcome. The same version-1 envelope can
report `owner-prelaunch-refused` only with every member unavailable, absent
quota/backoff and zero acquisition/attempt/point counts. Contradictions refuse;
ordinary failures with zero counters remain unresolved.

The executing reader advertises the separately selected
`agent-worktrees.pr-snapshot-batch-refusal/v1` API. A prefix producer qualifies
that receiver before emitting the extension; older native/scalar producers and
the durable state layout remain unchanged. The existing accounting owner keeps
its charge, natural eligibility and uncertain history. No local allowance is
relabeled as a provider secondary limit, and no new ledger or poller is added.

## Proposed explicit unresolved-query recovery

**Agent-recommended, review-first contract; not implemented.** An interrupted
native observation can leave query admission fenced after the original result
or cleanup receipt is lost. Preserve that uncertainty rather than infer an
unsent or completed operation from elapsed time, missing metrics or a later
process snapshot. The existing default remains fenced, including after restart
and scalar rollback.

An embedding owner may explicitly authorize future bounded observations despite
retained legacy uncertainty. That is a deliberate recovery-policy selection,
not proof that the original query completed, a timer, or authority a new API
grants itself. The proposed source-only recovery operation composes the existing
replay transaction and standalone writer lease:

- Require actual graceful withdrawal/join of the current foreground and its
  owned native scopes, followed by the existing exclusive service-writer lease
  and finite lifecycle/current-authority checkpoints. These establish current
  quiescence, never a retroactive receipt for the legacy query. An unsettled
  current owner refuses; no force cleanup or replacement controller is added.
- Bind a stable recovery identity to the original account/API scope and
  expected pending query identity. Recheck that fence inside the mutation
  transaction. A different pending operation, backward clock, unknown input or
  unselected policy refuses instead of clearing unrelated work.
- Retain the original pending identity, observation time and backoff fields in
  bounded recovery metadata with an explicit **outcome unknown** disposition.
  Remove only its active admission fence through that supported transition.
  Never manufacture an original completion, no-send or cleanup join. External
  accounting records and conservative charges remain with their original owner,
  unchanged and unrefunded.
- Keep fresh/default provisioning on the existing version-1 batch layout.
  An explicit recovery transition atomically adopts a compatible version-2
  batch layout and records its disposition in the same ReplayStore envelope,
  not a second journal or cache. Bound retained recovery records and refuse at
  capacity; do not prune unresolved history to admit more recovery.
- A new reader supports both layouts; an older reader refuses adopted
  version-2 metadata without rewriting it. Declare actual executing recovery
  support separately from package versions before an optional adopter invokes
  it. Existing scalar/batch invocation grammars and default call shapes remain
  unchanged; compatible rollback retains the new metadata reader.
- Read back an accepted original recovery identity after a lost response,
  without another transition or provider call. A newer pending query is an
  independent fence and must not be cleared by replaying that earlier recovery
  selection.
- The transition acquires no credential and sends no provider request. Later
  normal observations obtain new admission identities through the existing
  query-only native owner and retain every original population, fairness,
  concurrency, deadline, request/capture and backoff constraint. A new unknown
  result fences again; there is no automatic clear-and-retry loop.

### Recovery delivery and validation

Review this contract before implementation. Then deliver the optional core
operation and existing standalone lifecycle composition before migrating an
explicit adopter. Review, release, actual executing support, owner-policy
selection, graceful transition and serving qualification remain distinct.
Neither source acceptance nor recovery alone closes #14306 or #6007.

Focused controls must cover unchanged version-1 defaults; strict old/new reader
behavior; preservation of original facts, replay and uncertainty; wrong scope
or changed pending identity; active-writer exclusion; interrupted/expired
transitions; lost committed responses and idempotent readback; backward clocks;
bounded metadata capacity; and subsequent valid, throttled or unknown query
outcomes. Exercise the actual existing writer-lease/drain seam with isolated
fixtures. Do not substitute an arbitrary minimum query duration or fabricated
old receipt for the missing evidence. Native/installed/provider qualification
stays outside the focused unit gate.

## Journal

### 2026-11-23 - Contract accepted

The canonical plan merged through #14307 as `53a6706b7b` after current-head
Copilot approval and required checks. Implementation begins with native
provider/codec/CLI support; optional observer migration and serving acceptance
remain separate, and #14306/#6007 remain open.

### 2026-11-23 - Native provider expansion

The native `snapshot-batch` command and optional inventory-contract declaration
add the fixed query, typed collection/quota decoder and existing bounded
credential/capture owner. Scalar defaults and all observing consumers remain
unchanged. REST and query check evidence share the same pure normalizer.
Focused controls cover multiple PRs, independent pagination, changing/missing
evidence, numeric review provenance, lifecycle, sanitized transport failures,
primary/secondary backoff, strict neutral-directory parsing and actual
credential/native child boundaries. Cleanup failures retain their canonical
exception rather than becoming successful observations.

The [native operation contract](../../../plugins/agent-worktrees/docs/pr-snapshot-batches.md)
records the result schema and required selected-runtime/binary qualification.
This stateless provider does not implement observer selection, durable backoff,
release qualification or serving adoption. Those Plan/Validation items and both
tracking issues remain open; no operational savings are claimed.

### 2026-11-23 - Native provider accepted

The provider expansion merged through #14320 as `dff0e2176c` after current-head
Copilot approval and required green checks. The final source packet passes
244 focused tests and whole-file strict typing on the Linux and Windows
analysis targets. Review corrections preserve latest native check-run ordering,
elapsed throttle-date hints and rejection of non-JSON constants.

The next slice is optional acquisition in the existing observer and standalone
configuration owners, with durable backoff and actual per-PR projection/replay
controls. Scalar defaults remain unchanged. Both #14306 and #6007 remain open;
source acceptance is not release, installed support or serving qualification.

### 2026-11-23 - Optional caller expansion

The existing observer now has explicitly selected batch acquisition and
account/API admission metadata in ReplayStore's existing atomic envelope.
An unresolved query never becomes an elapsed lease. Validated primary and
shared-secondary holds survive restart and retain distinct REST behavior.
Fresh provisioning, additive writer-excluded adoption and compatible scalar
rollback preserve the ledger; older/unselected readers refuse it.

The [caller contract](../../../plugins/agent-pull-requests/docs/snapshot-batches.md)
records strict executing-core/native support checks, unchanged selection and
per-PR publication, actual joined cleanup and the remaining binary/serving gates.
Synthetic controls use the real scalar serializer, batch decoder, projection
and replay, including fair rotation, partial loss and cancelled registration
replacement. They do not establish installed qualification or operational gains.
The full parent effort and both tracking issues remain open.

The shared native source file retains its GitHub.com web-origin form for
inventory/scalar compatibility. The batch descriptor explicitly declares that
origin-to-fixed-API mapping; an earlier batch provider without it refuses before
acquisition. The standalone host can retain the ledger while rolling individual
repositories back to scalar reads. No adopted hold is removed by that rollback.

### 2026-11-23 - Optional caller accepted

The observer/standalone caller merged through #14338 as `a2bc70892d` after
current-head Copilot approval and required green checks. The final focused
packet passes 357 tests with five existing native-CLI platform/tier skips;
whole-file strict typing passes for twenty-one files on the Python 3.10 Linux
and Windows analysis targets.

Review corrections retain unknown outcomes even in valid failure envelopes,
admit only GitHub.com's declared web/API identity aliases without changing
cache keys, preserve old-core omission, and check the native cutoff through
actual replay-lock/commit contention. Only matching proven-prelaunch
reservations are compensated; opaque SDK timeouts remain unresolved.

Both tracking issues and the full parent effort remain open. The next slice
is normal release, executing-runtime/binary qualification and explicitly
authorized consumer adoption, followed by measured serving coverage and cost.
No source result establishes those facts or enables a provider automatically.
