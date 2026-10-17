# Provider evidence inventory

Parent: [PR Lifecycle Conformance](README.md).

Source snapshot: `a7559772e5b147b2fe0bca6e27eed967c7d8f73f`, 2026-10-16.
This is a source/fixture inventory, not live-provider acceptance. **Supported**
means an implementation exists; **incomplete** names a contract gap;
**unsupported** means an explicit refusal or absent operation; **unproven**
means the required behavior lacks evidence. These are not interchangeable.

## Existing command and contract seams

The existing [provider protocol][protocol], [snapshot model][snapshot],
[discussion and transition contract][contract], and [reviewer operations][ops]
remain the extension points.

- `pr-diff`, `pr-comment`, and `pr-review` resolve an active tracked PR and
  enforce a configured-provider mismatch guard. They are worktree-addressed;
  explicit foreign addressing on `pr-watch`/`pr-merge` does not establish
  foreign addressing for every reviewer verb.
- `pr-status --threads` reads discussion; `--resolve-threads` requests
  resolution. Existing display identities and mutation return strings do not
  establish durable, individually addressable discussion.
- `PRSnapshot` carries head, base ref, draft, labels, reviews and mergeability,
  but not a complete provider/repository/source identity or base commit.
  `Review` has an integer ID and candidate commit; `Comment` has only author
  and body; `CommentThread` has an integer ID, status, path and comments.
- `ThreadsResult.supported` does not describe pagination completeness.
  A transport error is not a missing capability.
- `Baseline` is a review-transition/head/check cursor. It is not an
  incremental diff, comment-edit or discussion-history cursor.
- `pr_checks.checks_payload` explicitly describes **observed** checks.
  `checks_complete` applies to that observation, not required branch-policy
  contexts. Reuse the merge provider's required-check evidence; green observed
  checks alone do not prove the required merge gate exists or passed.
- Publication authority and recovery already protect tracked PR association,
  source publication and rewrites. Those guards are reusable boundaries, not
  evidence that remote comment/review writes are idempotent.

## Real-provider matrix

Provider bodies: [GitHub][github], [Gitea][gitea], [Azure DevOps][ado].
Method names below are the source anchors.

| Operation | GitHub | Gitea | Azure DevOps |
|-----------|--------|-------|--------------|
| Create/get | Supported: `create_pull`, `get_pull` | Supported: same methods | Supported: same methods |
| Snapshot/reviews | Supported point-in-time `get_snapshot`; history completeness is not uniformly represented | `get_snapshot` and paginated `_all_review_objs` | `get_snapshot` maps current reviewer votes, not ordered review history |
| Candidate observation | `observe_head` exists | `observe_head` exists | Explicitly unsupported provider-clock observation |
| Unified diff | `get_diff` supported | `get_diff` supported | `get_diff` explicitly unsupported |
| Thread reads | Incomplete: `_THREADS_QUERY` limits threads to 100 and comments to 50 without traversal | Incomplete: `get_comment_threads` uses an unpaged review list and skips failed per-review comment reads | Incomplete: `get_comment_threads` consumes one `value` response and drops comment/reply identity |
| Durable thread identity | Incomplete: opaque native IDs become display indexes | Synthetic review-comment grouping, not native resolution threads | Native numeric thread IDs retained; individual comment identity lost |
| General comment | `post_comment` supported | `post_comment` supported | `post_comment` creates a new thread |
| Reply/edit/reopen thread | No protocol/adapter operations | No protocol/adapter operations | No protocol/adapter operations |
| Resolve requested thread | Incomplete: `resolve_threads` re-fetches IDs and resolves all active threads regardless of requested IDs | Explicitly unsupported | Selected-ID resolution exists; partial failure and authoritative read-back are not structured |
| Review publication | `submit_review` supported, no expected candidate input | `submit_review` supported, no expected candidate input | `submit_review` casts a vote and optionally posts a comment; no expected candidate input |
| Direct merge | `merge_pull` passes expected head to the required-check helper | Explicitly unsupported | Explicitly unsupported |
| Merge consent | Label-based `request_auto_complete`; native `enable_auto_merge` also exists | Label-based `request_auto_complete`, not immediate merge | Native `request_auto_complete`, not observed completion |
| Close/abandon | `close_pull` supported | Explicitly unsupported | Explicitly unsupported despite native abandoned disposition |
| Operation identity/reconciliation | General comment/review recovery unproven | General comment/review recovery unproven; label retry/read verification is narrower | General thread/vote recovery unproven |

GitHub's separate [`github_bar` reader][bar] exhausts relevant merge-bar pages
and fails closed. That is reusable prior art; it does not make
`get_comment_threads` complete. Likewise, Gitea's paginated review, label and
head-lookup helpers do not make its separate discussion reader complete.

Expected-head merge support must not be generalized to verdict publication.
Accepting an argument on an unsupported adapter is not enforcement. A
provider's native review commit/iteration binding and conditional-write
capabilities still need transport-level proof before the stronger mutation
contract can be advertised.

Adapter transport choices stay internal. This inventory does not authorize
replacing an adopter's credential broker or approved service transport.

## Reusable tests and what they do not prove

[`test_providers.py`][tests] exercises real adapters with substituted
`run_cli`/REST responses. Its GitHub, Gitea and Azure reviewer-operation groups
are useful transport-boundary fixtures, not complete multi-round scenarios.

[`test_pr_provider_conformance.py`][conformance] checks protocol shape for all
registered providers and full lifecycle behavior for the in-memory mock.
Mock state belongs to one instance; it does not establish durable restart
recovery. Neither layer proves complete real-adapter lifecycle parity.

The existing provider and conformance files passed **291 cases on native
Windows** through `test-supervisor` and the repository plugin runner. This
baseline preserves current behavior; it does not pass any new acceptance
requirement merely because an unsupported result is expected by an old test.

## Next implementation boundaries

1. Add the [normalized contract](contract.md) and addressable, complete read
   results to existing provider/CLI seams, preserving compatibility.
2. Extend discussion and lifecycle operations with explicit capability and
   authority outcomes. Required unsupported steps remain acceptance gaps.
3. Prove candidate fencing and ambiguous-write recovery per transport before
   exposing stronger mutation guarantees.
4. Reuse existing blocking observation and durable dispatch wait/resume in the
   three provider-specific persistent-agent acceptance scenarios.

[protocol]: ../../../plugins/agent-worktrees/src/agent_worktrees/providers/base.py
[snapshot]: ../../../plugins/agent-worktrees/src/agent_worktrees/pr_snapshot.py
[contract]: ../../../plugins/agent-worktrees/src/agent_worktrees/pr_contract.py
[ops]: ../../../plugins/agent-worktrees/src/agent_worktrees/pr_reviewer_ops.py
[github]: ../../../plugins/agent-worktrees/src/agent_worktrees/providers/github.py
[gitea]: ../../../plugins/agent-worktrees/src/agent_worktrees/providers/gitea.py
[ado]: ../../../plugins/agent-worktrees/src/agent_worktrees/providers/azure_devops.py
[bar]: ../../../plugins/agent-worktrees/src/agent_worktrees/providers/github_bar.py
[tests]: ../../../plugins/agent-worktrees/tests/test_providers.py
[conformance]: ../../../plugins/agent-worktrees/tests/test_pr_provider_conformance.py
