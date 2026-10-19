# Built-in publication checks

`pr.publication_checks` opts a repository into local self-review receipts and
scope checks. Omission preserves existing publication behavior. These are fixed
built-ins, not an executable/plugin registry; they never write native reviews,
votes, thread status, approvals or provider policy.

```yaml
pr:
  publication_checks:
    features: { self_review: true, scope_report: true }
    review_lenses_path: .checks/review-skills
    allowed_paths: null
    guidance: Review purpose as well as correctness before publishing.
```

The same strict block is supported in related-repository PR declarations.
Unknown keys, non-boolean features, unsafe paths and executable configuration
are refused. `review_lenses_path` is optional and has no organization-specific
default. When set, it must contain immediate `<lens>/SKILL.md` files; their
content participates in the effective-policy fingerprint. Without a configured
corpus, name the review guidance actually used. `allowed_paths: null` (or absent)
is report-only; `[]` allows no changed files. Globs match path segments: `*`
does not cross `/`, `**` spans zero or more segments, and `folder/` includes
descendants. A task allow-list only narrows the repository allow-list.

Use the attributed central command, never a free-floating repository script:

```text
agent-worktrees pr-watch checks example/project lenses --workspace <worktree> --json
agent-worktrees pr-watch checks example/project record-self-review --workspace <worktree> --lenses runtime,tests --summary "Reviewed changed paths" --fixed "Added missing case" --json
agent-worktrees pr-watch checks example/project verify-self-review --workspace <worktree> --json
agent-worktrees pr-watch checks example/project scope-report --workspace <worktree> --since <previous-head> --allowed-path "src/**" --json
```

`--base` defaults to the resolved repository's remote/default branch. Receipts
bind the exact worktree/Git directory, base ref, effective configuration/lens
content and branch diff. Use the publication target's base spelling when
recording: a receipt for a different base does not satisfy publication.
`--expected-head` fences record/verification against a caller's observed HEAD.
Records are versioned JSON under that worktree's Git directory, never source
files. The recorded head and merge base are checked against the stored diff;
an unrelated base merge may preserve review only when the exact diff is
unchanged. Whitespace changes invalidate it. Malformed records, dirty tracked
files, missing/ambiguous merge bases and changed policy fail closed.
Diff fingerprints retain Git output as bytes and remove only `index ` lines;
invalid UTF-8 source bytes cannot collapse through replacement decoding.
Lens contents are likewise hashed as bytes. Receipts from an older decoding
implementation require a fresh review rather than an automatic upgrade.

The scope report includes merge base, each file's +/- counts, new-file status
and first branch commit/subject. `--since` reports joined/left/changed files,
including binary changes, without confusing unrelated base changes with branch
scope. JSON includes a `PROGRESS scope=...` line for milestone consumers.
An allow-list checks paths, not whether a change is a good idea.

## Mandatory publication path

Configured checks run before local `create-pr` and `push-changes` mutation and
again at the PR transport boundary against the candidate being published.
The checked candidate SHA is pinned into the push refspec. Automatic transport
recovery may retry that same candidate as a fast-forward, but does not silently
rewrite/rebase it after review; reconcile and run the checks again instead.
Direct `push-changes` also rechecks its resulting candidate before each push.
Foreign `create-pr --from-branch` without the source workspace refuses when
checks are configured; the caller's unrelated checkout cannot satisfy a target
repository's local receipt.

The checks do not replace validation hooks, provider review/merge gates,
repository authority or native approval freshness. A receipt is auditable
self-review evidence, not proof that a human approved the change.
