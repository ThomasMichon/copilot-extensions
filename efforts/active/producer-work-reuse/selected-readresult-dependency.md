# S3a/b implementation dependency after the authority amendment

[Episode](s3ab.md) · [Merged contract](selected-authority-contract.md)

**Historical workload checkpoint:** the later design direction replaces the
primary multi-mux workload with a representative two-account,
one-ACP-or-Mux-session-per-venue profile and proposes durable all-venue
inventory/push-assisted freshness. The 17-versus-13/10 calculation below is
retained historical analysis, not the new profile's acceptance ceiling.
Functional optimization is paused pending the successor's reviewed
inventory/auth/control-publication contract. See
[`evidence/representative-profile-manifest.json`](evidence/representative-profile-manifest.json)
for the new current-consumer characterization and its explicitly unavailable
ACP/cache/push cells.

The user decision is reviewed and merged in #8278:
`c6c117c03cc75790023c7db1ffa15e2d59a589ea`.
The additive read-result seam subsequently received explicit, narrow user
authorization. This is not an owner release: all other #5761 reservations
remain intact. #8143 remains draft; no performance gate has been cleared.

## Current checkpoint: additive seam, unresolved total-work acceptance

A fresh open-PR subject and file-overlap check found only #8143 on the exact
candidate/owner-wave files. That absence is not release evidence. The explicit
user grant authorizes only `CandidateRead`/`read_candidates`, with the existing
`candidates()` tuple projection preserved. The local implementation exposes the
actual routing, configuration and provider reports, bound-account/provider/
active observations, and ordered accounts from one invocation. It adds no
credentials, hashes, cache, global state, provider or credential API.

The owner now forms its immutable scope from those actual values rather than
loading effective configuration a second time. Indeterminate provider reports
fail closed even when the effective merged value is unchanged.

**The selected/publication algorithm is not implemented or qualified by this
checkpoint.** Transport, retry, cancellation, age and publication algorithms
remain the retained draft's stricter implementation. No additional shared
helper or source signature was introduced.

The complete matched synthetic consumer matrix is retained in
[`evidence/candidate-read-manifest.json`](evidence/candidate-read-manifest.json).
All 72 final frames contain the expected observations, including zero work for
zero venues and unchanged-not-due polls. It still measures the retained
regression, not a fix: ten CLI venues cost 596 versus 103 cold baseline requests
without bridge, and 926 versus 123 with bridge.

### Optimistic one-venue conflict under the amended semantics

Even ideal same-read reuse, with no redundant source/config reads, gives the
following lower bound for the retained three-account Windows-direct workload:

| Required work | CLI process requests |
|---|---:|
| Full candidate/credential read for acquisition | 2 discovery + 3 credentials |
| Three account listings, reusing acquisition credentials | 3 |
| Config fetch and direct SSH execution | 2 |
| Fresh selected credential before each of those two effects | 2 |
| Full live candidate/credential read before publication | 2 discovery + 3 credentials |
| **Optimistic minimum** | **17** |

The measured matched baseline is **13 cold / 10 warm**. With bridge the
optimistic minimum is **21**, versus **15 cold / 12 warm**. These are derived
lower bounds, not measurements of an amended implementation. They assume only
one publication batch, omit selected-source discovery, omit connection/retry
overhead, and already remove acquisition's redundant post-list sweep and
listing-token rereads. Opaque provider-keyring reads also cannot be waved away:
the corresponding minima are **8 versus 4** provider calls without bridge and
**10 versus 5** with bridge.

Consequently the unchanged per-cell total-work non-regression gate cannot
currently be claimed under this schedule. A cheaper selected-source reader
alone does not remove the one-venue lower bound. This needs campaign
architecture adjudication, not a unilateral cache, freshness relaxation,
deadline change, accepted-cost exception or new shared API. No such change is
authorized here. Source review/publication/merge remain blocked; the same task
and draft retain the work.

### What the 13/10 comparator does and does not guarantee

This is the pinned `SessionForwards` consumer from
`35872e7ad67e1c0ef182a1f4401a49af69c4afb6`, using the same current shared
candidate/config/auth/listing functions and matched synthetic external
boundaries as the draft. It is not a separately installed whole-repository
baseline or proof of an equivalent authority contract.

For one CLI venue without bridge, cold work is: one account-routing discovery,
one active-account discovery, three token reads, three account listings, one SSH
config fetch and four individual mux commands: **13**. The unchanged warm-due
poll repeats the same work except that the legacy credential cache eliminates
the three CLI token requests: **10**. The not-due poll does no observation work.

The comparator includes canonical candidate ordering/account routing,
configuration loading, selected bound-account resolution at open, the existing
credential-provider precedence and legacy cache behavior. Its probe requires a
listed Available row before opening; absent/failed listing and non-verdict
transport results are unknown. It retains bounded command retries and owned
manager disconnect. Publication renews successful tenants and releases only
confirmed negative tenants; release passes the observed tenant generation.

It does **not** establish full account/config/credential authority at every
effect or publication; fresh opaque selected credentials before each config,
connection, command and retry; a complete/unique cross-account inventory;
explicit bounded inventory-proof age; provider-report completeness despite an
unchanged retained merged value; or expected-generation fencing for heartbeat.
The pinned consumer calls heartbeat without the observed generation.
No mirror runs in the count fixture, and neither GH-internal HTTP nor native
process/CPU costs are measured. Thus **13/10 is a lower-safety historical
work comparator, not evidence that the stronger amended guarantees cost 13/10**.

The required tradeoff is whether a historical, lower-safety per-cell cost
ceiling remains the acceptance ceiling for the newly mandatory fresh-authority
guarantees. Only campaign adjudication may change an acceptance metric, approve
a differently qualified like-for-like comparator, or revise required safety
semantics. None is selected or implemented by this checkpoint; preserving both
unchanged leaves source integration/merge blocked.

## Exact protected seam

`plugins/agent-codespaces/src/agent_codespaces/account_candidates.py` owns
`candidates()`: account ordering/deduplication, source precedence, callbacks and
namespace-discovery overlap. #5761's active effort and issue comment reserve
that work to `rattling-circuit`:
https://github.com/ThomasMichon/copilot-extensions/issues/5761#issuecomment-6349766750

Fresh task/forge checks found no replacement/release or bounded shared-seam
agreement. Empty canonical task searches and no conflicting open PR do not
release this explicit owner. The source changes cannot be taken unilaterally.

## Original narrow agreement request — historical

An additive source-owned read operation, provisionally:

```text
read_candidates(*, on_account=None, concurrent=False) -> CandidateRead
candidates(...) -> the unchanged accounts projection
```

`CandidateRead` exposes only values that this one invocation already read:

- ordered candidate accounts;
- existing `gh_account.AccountInventory` routing result;
- effective configuration and the actual `ConfigProviderReports`;
- bound-account/provider-selection/active-account observations used to select
  the candidates, with available completeness/error provenance.

This is an invocation-local read result, not a currentness cache or revision
authority. No credentials, credential hashes, global state or new provider are
added to it. Consumers take their immutable wave projection without mutating
the owner's values.

The requested seam must expose the already-read values while preserving:

- existing `candidates()` return type, order and precedence;
- ordinary serial callers and namespace opt-in concurrency/callback timing;
- actual partial/error propagation and the existing foreground deadlines;
- no duplicate candidate-selection implementation in the observer.

S3 then uses fresh selected-source reads at true effects and full fresh reads
at bounded publication under #8278. Existing credential provider precedence
and failures remain authoritative. A new helper in any other shared owner is
another explicit proposal/coordination point, not implied by this request.

## Completion boundary

The narrow explicit grant resolves only the candidate read-result edit boundary.
All prior raw frames remain retained, and the total-work non-regression gate is
still unsatisfied. The semantic amendment and successful evidence recorder do
not make earlier subwork counters or the retained implementation a performance
pass. Native/release/installed qualification and normal source review/CI remain
separate gates.
