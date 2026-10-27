# Cancelling an unpublished local PR attempt

`pr-abandon --creation` cancels one local creation reservation, not a provider PR.
It requires explicit operator approval for that exact attempt and a concrete
reason. It never resets Git, deletes source or refs, closes a provider PR,
settles resource claims or removes a checkout.

## Selected-runtime contract

The executing runtime's tracked `pr-status --no-live --json` response declares
`creation_cancellation_contract: agent-worktrees.pr-creation-cancellation.v1`.
It also reports each association's immutable `pr_id`, current `pr_revision`
and retained `creation` evidence. `--all` includes interleaved attempts.
This identifies operation support, not eligibility or operator authorization.
Do not infer support from a payload's package version or another installation.

Older runtimes retain their existing numbered-PR behavior and strictly reject
the new arguments. Missing contract metadata means unknown cancellation support.
There is no equivalent legacy fallback: retain the reservation and select a
capable runtime through the ordinary update process when authorized. A rollback
can lose the additive receipt fields and therefore loses cancellation proof;
it must not be used to manufacture a terminal or merged record.

## Eligibility and confirmation

Only a newly allocated, owner-stamped `creating` reservation qualifies. The
receipt binds the worktree incarnation, immutable PR ID, target, attribution,
remote destination and provider endpoint. Entering publication durably
invalidates cancellation **before** the first push, including a failed or
timed-out push. A legacy record is not upgraded merely because its number,
URL and head fields are empty.

Version 1 supports same-repository GitHub heads. It requires confirmed absence
of both local and remote PR-head refs and an explicitly empty, successful
all-state provider search. A fork, changed destination, existing PR (including
closed/merged), unavailable read, unsupported provider or uncertain attempt
refuses without cancellation. Requiring local-head absence also excludes the
legacy feature-branch publication path; missing refs alone are never proof.

Read `pr-status --all --no-live --json`, then select one ID explicitly:

```text
agent-worktrees pr-abandon <worktree-id> --creation <pr-id> --reason "<specific reason>"
```

The unconfirmed call returns the alternatives-first warning and performs no
record/provider read or mutation. Only after actual approval for that attempt:

```text
agent-worktrees pr-abandon <worktree-id> --creation <pr-id> --expected-revision <revision> --reason "<specific reason>" --confirm --json
```

`--creation` cannot combine with numbered-PR selection or a target override.
Changed revisions require renewed confirmation, not an automatic retry with a
new token. An identical confirmed retry returns the original receipt unchanged.

## Persistence and ordinary finalization

The supported owner uses the existing publication authority, destination and
record fences. It preserves unrelated attempts, resources and frozen attribution.
The entry remains in history as `state: abandoned` with a versioned **local
creation cancellation** receipt containing the reason, actor, time and confirmed
revision. `creation_cancelled: true`, `provider_closed: false` and
`claim_released: false` make the limited outcome explicit. No synthetic PR number,
provider closure or merge is recorded.

Ordinary finalization recognizes a valid cancellation as **not merged**, then
still checks source alignment, every other PR/ref, dirty content, obligations,
ownership and action-time safety. Cancellation is not permission to discard
unmerged commits or finalize a worktree with unresolved responsibility.

The bounded development regressions are
`tests/test_pr_creation_cancel.py`, alongside the existing provisional retry,
publication-state and numbered-abandonment controls. Their disposable Git
fixtures and read-only provider adapters do not qualify a production runtime or
authorize cancellation of any real record.

See [CLI reference](cli-reference.md) and [publication architecture](architecture.md).
