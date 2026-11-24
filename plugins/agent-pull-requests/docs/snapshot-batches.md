# Optional native GitHub observation batches

The existing unfiltered continuing repository observer can select
[`agent-worktrees.pr-snapshot-batch/v1`](../../agent-worktrees/docs/pr-snapshot-batches.md).
This changes acquisition only. The existing inventory/member/departure
selection, per-PR projection/replay, writer lease and joined drain remain the
owners. No other poller, credential issuer or quota service is introduced.

## Explicit selection

In the standalone [local profile](../../../services/pr/LOCAL.md), set the
top-level `github_snapshot_batches` to `true`. For each selected repository,
add `"snapshot_batch": "agent-worktrees.pr-snapshot-batch/v1"` to its existing
repository entry. That identity retains its existing GitHub.com web-origin or
API-base spelling (`https://github.com` or `https://api.github.com`), without
re-keying the cache. Retain the existing attributed prefix and typed
per-request `credential_command`. The shared native `source_config` keeps its
explicit GitHub web-origin binding, `api_base: https://github.com`, as required
by the existing inventory/scalar routes. The optional batch command maps that
declared origin to its fixed `https://api.github.com/graphql` destination.
Other providers and the filtered profile cannot select this operation.
Omitting both fields preserves scalar defaults and the old core call shape.
Selecting only the metadata owner is supported for scalar rollback.

The actual executing PR core must expose the binding and storage APIs. The
repository boundary recognizes only the declared GitHub.com web/API aliases
as the same backend; provider, repository, PR and all foreign-host checks remain.
Native envelopes retain their original endpoint spelling. This admits real
web-origin inventory/scalar results alongside fixed-API batch results without
rewriting raw evidence or changing the configured cache key.

The existing passive native inventory declaration must advertise the compatible
batch protocol, shared web-origin configuration support, fixed command/endpoint, query-only effects and bounded
members/requests/nodes/capture. An absent/incompatible declaration refuses
before inventory or query acquisition; no speculative command probe or scalar
fallback occurs. The native contract separately requires curl/TLS binary
qualification; source tests and a descriptor do not establish that proof.

Python embeddings select `ReplayStore(..., repository_cache=True,
repository_continuing=True, github_snapshot_batches=True)` and the corresponding
`NativeObservationBinding(..., snapshot_batch=...)`. They retain their
existing lifecycle and authority callbacks.

Only already-selected adjacent targets of the exact same immutable native
binding share a batch, at most ten PRs. Accounts, repositories, credentials,
providers and prefixes never mix. The existing native concurrency ceiling
bounds pending operations; operations for the same GitHub account/API serialize
so their throttle result is durable before another starts. This can lower
concurrency, never raise it. All groups retain the original cycle deadline;
pagination does not renew it.

## Durable backoff and interrupted reads

The existing ReplayStore envelope adds `github_snapshot_batches_version: 1`
and bounded account/API admission metadata under `github_snapshot_batches`.
Its existing kernel lock and atomic document commit retain pending operation
identity, validated primary/shared-secondary deadlines and last clock evidence.
At most 64 account/API rows are retained; capacity refuses rather than evicting
a hold when configuration changes.

A pending marker commits **before** native query invocation. Validated response
backoff commits before the marker clears and before another query can start.
Local PR-range and exhausted-budget refusal occurs before that reservation.
The existing absolute native execution budget includes settlement/publication
margin; time spent waiting for admission does not renew the child deadline.
GraphQL primary backoff blocks queries but permits independently available
scalar REST snapshots and inventory. Shared-secondary backoff blocks both.
Another PR, repository, credential reference, restart or compatible update does
not reset account/API backoff. A backward clock cannot make retained evidence
eligible.

An interrupted, expired, malformed or undecodable native read leaves the
original pending marker unresolved. A valid native failure envelope with an
unknown transport/provider outcome does too: a last earlier quota sample or
the absence of a backoff field cannot establish the latest send's outcome.
Completed independent members may still publish their already-valid facts.
An unresolved operation blocks further provider acquisition for
that account/API. It is not a timed lease: elapsed time is not proof of the
provider's unknown throttle result or native settlement. This source slice
provides no automatic pending-marker recovery or clearing command. Preserve
the original state and custody for explicit owner-led reconciliation; do not
strip the marker, select a different label/root, or retry scalar reads to
bypass it. Other accounts/providers remain independent.

## Provision, adoption and rollback

Fresh explicitly selected provisioning creates the extension. An existing
compatible local owner requires the additive transition:

```text
agent-pull-requests-service adopt-snapshot-batches --config /run/config/pr-cache.json
```

The command uses the existing service writer lease and refuses a live writer.
It does not stop a process, acquire provider credentials, replace the volume,
change replay identities or delete state. Actual service withdrawal and
replacement remain with their lifecycle owner and required effect authority.
Existing compatible activity metadata is preserved, not simultaneously
migrated.

For scalar rollback on a compatible new reader, omit each repository's
`snapshot_batch` field while retaining `github_snapshot_batches: true`.
The same ledger then still fences shared-secondary and unresolved admissions,
while REST may operate through a primary-only hold. Removing the metadata
selection or using an older reader refuses the adopted envelope without
rewriting it. Retain the compatible state/reader; no downgrade/reset is implied.

## Evidence and limits

The caller strictly binds every result to requested PR/provider/API/repository,
validates quota/backoff and collection completeness, and passes complete
members through the existing scalar snapshot decoder and per-PR publication.
Unavailable members retain prior facts and cannot manufacture terminal events,
replace a cancelled registration or revive withdrawn interest. Actual executor
join, not cancellation or a result flag, proves owned native work has returned.

Synthetic controls cross the actual native scalar serializer, batch decoder,
repository projection and watch replay. They compare 32 selected PRs with four
bounded native batch operations rather than 32 scalar operations, without
claiming a measured HTTP count, point saving, cold/warm timing or serving
coverage. Release, binary qualification and authorized installed adoption remain
separate gates.
