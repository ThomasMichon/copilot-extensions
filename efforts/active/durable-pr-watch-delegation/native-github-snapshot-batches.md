# Bounded native GitHub snapshot batches

Back to [Durable PR-Watch Delegation](README.md).

**Status:** Proposed; source and serving support are not implemented.
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

The observing/accounting owner must persist the validated next-eligible query
time in bounded metadata before another attempt. Restart, another PR number
or an update cannot clear provider backoff. REST may continue across a GraphQL
primary-limit reset when independently available, but not bypass a shared
secondary limit. This governs the selected owner's requests, not every other
application using the same account. Do not introduce a global quota service
or another polling loop.

## Serial plan

- [ ] Deliver the native provider, typed batch codec, strict CLI and additive
  capability with focused source controls; leave existing callers unchanged.
- [ ] Deliver optional batch acquisition in the existing observer and standalone
  configuration owners, including durable backoff and actual serialization/
  projection/replay controls. Keep scalar defaults and old/new refusal honest.
- [ ] Qualify normal release and explicitly authorized consumer adoption
  separately from source completion. Measure serving cost, timing, current
  coverage and unaffected provider behavior before claiming operational gains.

## Validation plan

- [ ] Exercise fixed query/variable construction and the actual transport/
  credential boundary: no raw query or mutation input, wrong-target access,
  credential disclosure, automatic retry or budget reset.
- [ ] Cover multi-PR results, missing/duplicate/foreign PRs, scoped/unscoped
  GraphQL errors, pagination and candidate changes, numeric review provenance,
  REST check/review equivalence, capture limits and expiry.
- [ ] Exercise cost/quota/backoff decoding and owner persistence across
  restart, another PR, interrupted reads and clock/reset evidence; retain
  independent primary-resource versus shared-secondary-limit semantics.
- [ ] Cover new/new, old-caller/new-provider and selected-new-caller/old-provider
  parsing, omission/default behavior, supported rollback and explicit refusal.
- [ ] Use actual existing observer selection/projection/replay controls for
  mixed bindings, partial results, unchanged observations, withdrawal, fair
  cursor progress, stop/deadline and joined cleanup.
- [ ] Compare scalar and batch work counts on synthetic populations. Keep
  required unit CI focused; native cold/warm timing and provider/serving proof
  belong to separately selected bounded qualification.

No source result closes the full parent effort or grants runtime adoption,
repository mutation or another owner's resource authority.
