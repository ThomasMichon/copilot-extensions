# Unpublished creation cancellation

Back to [native provider commands](../README.md#explicit-native-provider-commands).

```text
agent-pull-requests cancel-creation --worktree <exact-full-worktree-id> --creation <immutable-pr-id> --expected-revision <n> --reason <text> --confirm --worktrees-command <absolute-command> --json
```

Use only after actual operator approval for this exact unpublished attempt, in
the owning project's admitted configuration context. Without `--confirm`, the
frontend refuses before reading Worktrees. Retain or reconcile useful/uncertain
work instead of abandoning it because of a conflict, rebase or delay.
Repository/PR-number selection, worker configuration and abbreviated flags are
not accepted. An abbreviated worktree ID cannot satisfy the exact status match.

The selected Worktrees receiver must declare
`agent-worktrees.pr-creation-cancellation.v1` through
`pr-status <worktree> --all --no-live --json`, including one exact matching
creation. The frontend then calls the existing `pr-abandon --creation` owner
once with the original revision and reason. Older/rolled-back receivers refuse
this optional command before dispatch; other native and legacy verbs remain
supported. Discovery is not proof of eligibility: the owner rechecks revision,
publication/absence and locking at cancellation.

Output retains the owner payload and adds `frontend.cancellation_status`:
`not-attempted` before dispatch, `confirmed` only for an exact valid v1 receipt,
or `unknown` after a lost, invalid or failed dispatch result. Only `confirmed`
exits zero. Owner diagnostics survive; `frontend.diagnostic` explains a refusal
or uncertain result. An unknown outcome may include a committed cancellation;
there is no automatic retry/fallback. Reconcile the original attempt before
intentional receipt recovery using its original revision and reason.

This cancels a qualified local reservation only: it never closes a remote PR,
posts a reason comment, changes source or releases claims/finalization gates.
Remote abandonment remains a separate operation. Source/process qualification
and native-platform/installed acceptance are tracked separately in the
[reviewed contract](../../../efforts/active/durable-pr-watch-delegation/native-creation-cancellation.md).
