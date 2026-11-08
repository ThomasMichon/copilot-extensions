# Optional tracked comment and review publication

Back to [workspace tools](workspace-tools.md) and the
[reviewed consumer contract](../../../efforts/active/durable-pr-watch-delegation/tracked-comment-consumers.md).

`agent_pull_requests.tracked_publication.TrackedPublication` composes the
existing Worktrees `pr-comment` / `pr-review` parser and handlers with
`WorkspaceTools`. It is an optional **source embedding**, not ordinary CLI,
installed-tool or normal-host adoption. It creates no owner, ledger, credential
loader, provider endpoint or background work.

## Binding and input

An authenticated host already holding the actual workspace client constructs:

```python
publication = TrackedPublication(
    workspace_tools,
    phase="preview",
    worktree_id=original_worktree_id,
    target=tracked_target,
    native_scope=admitted_native_scope,
    request=selected_request,
)
```

`tracked_target` has exactly `provider`, `api_base`, `repo` and `number`.
The host supplies the exact mapping from that tracked repository alias to
native provider/endpoint/project/repository/principal/PR scope. A repository
name is not a native GUID; an account label or matching number is not an
authenticated principal. The adapter checks this mapping, and the actual
workspace receiver retains its own scope, storage, policy and writer checks.
Endpoint comparison uses the native scope's trailing-slash normalization;
equivalent configured forms do not retarget an operation.
Do not derive trusted mapping or credentials from the selected request.

The host supplies the binding explicitly to the existing
`agent_worktrees.__main__.main(argv, publication=publication)`, the
`pr_state_cli.cmd_pr_comment` / `cmd_pr_review` handler, or
`pr_reviewer_ops.pr_comment` / `pr_review` operation. This is the in-process
`agent-worktrees.selected-publication/v1` interface. It uses the original
parser; no new flags, request-file owner loader or process invocation is added.
An older embedding without this Python interface must report unsupported,
not attempt a protected write and retry through an ordinary command.

Only the selected comment/review invocation accepts this binding, with an
explicit original worktree ID. Preparation/submission reads local tracking
and configuration under the normal CWD/`--project` invocation resolution,
without live reconciliation or credential acquisition. Recovery parses that
existing flag but does not resolve or load the current project.
Changed or terminal associations refuse rather than retargeting the operation.
The host's one-shot client lifetime must already permit synchronous CLI use;
an existing running event loop is not taken over.

## Preview, submission and recovery

| Phase | Existing workspace operation and input | Result |
|---|---|---|
| `preview` | `allocate`: number, v1 intent, original source_attachment, budget_seconds | Same owner's committed final v2 preview; no native effects |
| `submit` | `submit`: number, exact approved v2 intent, original source_attachment, budget_seconds | Original ordered action outcomes, receipts and unknown fences |
| `recover` | `inspect`: number, original operation_id | Original retained intent and outcomes, including prepared/unadmitted selections |

Preview requires the actual receiver's selection/prepared-review contracts and
allocation support, not a writer. Submission requires prepared-review, original
selection inspection and actual write support; valid prepared bytes alone do not
replace a committed preview. Missing/withdrawn support refuses the selected path, never falls
back to a legacy provider write or adopts storage. Recovery needs prepared
inspection, not a currently available preparer or writer. It
addresses the original workspace owner directly: current tracking, configuration,
profile and CLI body do not reconstruct or redirect its original bytes.

The invocation's body and verdict must match the selected actions. After
preview, explicitly approve and submit the exact prepared body and source
selection; the original draft text does not approve its decorated replacement.
A general comment is one unanchored comment. ADO review text and vote are
separate ordered actions; comment-only reviews have no invented vote and
bodyless votes have no filler comment or GUID. Empty comment-only composition
refuses. GitHub/Gitea protected publishing, inline comments and owned edits are
unsupported by this finite adapter.

Both JSON and non-JSON selected output retain the structured workspace response.
Preview/recovery never print ordinary "posted" messages. An incomplete
submission exits nonzero while preserving every action outcome, including a
confirmed text action followed by an unattempted/unknown vote. Recover/retry the
same operation; never resend confirmed text or treat unknown votes as no-send.
Without an explicit binding, old syntax, output and provider behavior are unchanged.

`tests/test_tracked_publication.py` exercises the actual tracked CLI, native and
Gateway clients, real-file selection owner and synthetic protected ADO transport.
This does not qualify live providers, actual producer grants, deployment or
automatic host enrollment. Other publication surfaces remain separately owned.
