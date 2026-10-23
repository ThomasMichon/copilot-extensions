# S3a/b implementation dependency after the authority amendment

[Episode](s3ab.md) · [Merged contract](selected-authority-contract.md)

The user decision is reviewed and merged in #8278:
`c6c117c03cc75790023c7db1ffa15e2d59a589ea`.
Implementation is still blocked on protected source ownership, not on that
semantic decision. #8143 remains draft; no performance gate has been cleared.

## Exact protected seam

`plugins/agent-codespaces/src/agent_codespaces/account_candidates.py` owns
`candidates()`: account ordering/deduplication, source precedence, callbacks and
namespace-discovery overlap. #5761's active effort and issue comment reserve
that work to `rattling-circuit`:
https://github.com/ThomasMichon/copilot-extensions/issues/5761#issuecomment-6349766750

Fresh task/forge checks found no replacement/release or bounded shared-seam
agreement. Empty canonical task searches and no conflicting open PR do not
release this explicit owner. The source changes cannot be taken unilaterally.

## Narrow agreement requested — proposed, not implemented

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

The owner must agree how to expose the already-read values while preserving:

- existing `candidates()` return type, order and precedence;
- ordinary serial callers and namespace opt-in concurrency/callback timing;
- actual partial/error propagation and the existing foreground deadlines;
- no duplicate candidate-selection implementation in the observer.

S3 then uses fresh selected-source reads at true effects and full fresh reads
at bounded publication under #8278. Existing credential provider precedence
and failures remain authoritative. A new helper in any other shared owner is
another explicit proposal/coordination point, not implied by this request.

## Completion boundary

A narrow owner agreement or explicit source-owner release is the next input.
Until then, no shared candidate/account/config API is edited. All prior raw
frames remain retained, and the total-work non-regression gate still requires
complete cold/warm/not-due 0/1/10 CLI/keyring and bridge/no-bridge evidence.
The semantic amendment does not make earlier subwork counters a performance pass.
