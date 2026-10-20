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

## Delivering policy to a worker

`--workspace` selects the Git checkout, not the configuration source. Installing
the central command or supplying policy prose does not give a fresh worker the
host's resolved related-repository configuration. Export that effective policy
before dispatch:

```text
agent-worktrees pr-watch checks example/project export-config --workspace <absolute-worker-worktree> --base origin/main --output <private-dispatch-directory>/pr-worker.json --json
```

The workspace is a target-platform absolute path; export does not look it up on
the host. `--base` must use the selected repository's configured remote. It may
be omitted only when that binding already supplies a default branch. The new
output file contains the selected provider/endpoint, publication checks,
observation requirements, hold labels, WIP title prefixes and guidance, bound
to that worker workspace/base.
The resolved GitHub authority is pinned even when the host selected it through
`GH_HOST`, so a different worker environment cannot redirect the policy.
It does not copy host checkout/runtime configuration, machine identity, token commands, credentials or
publication/merge grants. Missing publication policy, an ambiguous workspace,
an endpoint containing credential/query data, or an existing output file
refuses export.

Deliver the file using the selected venue's supported input-file transport,
outside the product checkout. Retain the returned SHA-256 in the task input
inventory and require the worker to verify the delivered bytes before use.
Do not transcribe the policy, commit the artifact into the product repository,
or silently substitute defaults. Regenerate it when the selected host policy
or target workspace/base changes. Authentication remains the worker's own
configured responsibility.

Every worker check then passes `--config <delivered-pr-worker.json>` in
addition to `--workspace`. This explicit local check context works without a
host project registry or an adopted worker project; it does not admit
unmanaged stateful waits or subscriptions. All ordinary receipt, remote and
candidate checks still apply. Target resolution uses the explicit
file's declared `pr.repository` binding before consulting ambient registry
targets; a same-slug registered project cannot replace that delivered policy.
One-shot snapshot/thread reads use the same precedence with explicit config.
Without an explicit file, ordinary ambient resolution is unchanged.
Publication target matching uses the configured
remote's push URL, so an optimized fetch mirror cannot replace the actual
publication destination. Matching includes the provider endpoint (including
the Azure organization), not just the repository slug, and preserves equivalent
secure SSH/HTTPS destinations, including Azure's legacy organization URLs.
Azure path components are normalized individually, preserving encoded path
separators as component data. Public GitHub also accepts its secure
`ssh://git@ssh.github.com:443/...` clone alias; this does not apply to an
enterprise authority, another host/port or an HTTPS URL using the SSH hostname.
Multiple push URLs refuse instead of validating only
one destination of a multi-destination push.
For a Gitea SSH clone whose host, port or repository path differs from the
HTTP endpoint, configure that repository's canonical clone destination as
`pr.publication_checks.ssh_url` (for example,
`git@forge.invalid:example/project.git`). It is an explicit endpoint-specific
binding, preserved by export, not a guessed removal of the HTTP subpath.
Absent that declaration, an SSH path that does not match the full endpoint
refuses. Only secure SSH URLs without password/query data are accepted.
An absent/empty SSH binding preserves existing receipt fingerprints; declaring
or changing a binding invalidates evidence for the previous effective policy.
Missing, malformed or empty explicit configuration, including a non-mapping
`repos` block or repository entry, refuses the read/check
instead of falling back to ambient policy.

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
