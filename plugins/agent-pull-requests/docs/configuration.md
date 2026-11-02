# Pull Requests configuration

This plugin has **no independent preferences YAML or second PR-policy store**.
Its native CLI delegates provider, target identity, repository PR policy and
account selection to Worktrees' existing owner. See the complete
[Worktrees configuration reference](../../agent-worktrees/docs/config-reference.md)
and the canonical [native CLI adapter](../src/agent_pull_requests/native_cli.py).

## Files, fields and precedence

| Surface | Fields / types / defaults | Owner and scope |
|---|---|---|
| Target selection | `--repo owner/repository` string and PR number integer; explicit CLI target required where the command requires it | Invocation selector, not a configuration layer or a checkout basename. |
| Provider / PR policy | `pr` mapping, provider, base branch, strategy, fork, roles, merge/review, publication and source-attribution families | Exact field/default/required/alias tables are in the Worktrees reference's PR section. Native repository vetoes remain independent of ordinary preference order. |
| Repository/account identity | Registered remote, provider/account mapping, installation cell and qualified project identity | Worktrees registry and repository-policy owners, never inferred from a watch subscription. |
| Read/review request | Versioned request/envelope schema, target, evidence/source references, workspace and review-intent fields | [Owner-read API](../src/agent_pull_requests/owner_read.py), [review intent](../src/agent_pull_requests/review_intent.py), and [review workspace](../src/agent_pull_requests/review_workspace.py); not human preference files. |
| Watch subscription and replay | Subscription IDs, selection, cursor, lifetime/custody, withdrawal, replay and delivery records | [Watch contract](../src/agent_pull_requests/watch_contract.py); durable state, **not** preferences and never a knowledge graft. |

Ordinary Worktrees preferences use defaults → repository base + bound
knowledge → machine → exact repository+machine. The Pull Requests adapter
does not copy that configuration, discover another binding, or promote a
higher ordinary preference into merge/review/credential authority. A
checkout-free target retains the provider's registered target/policy reader.

## Validation, selectors and application

Command-specific required arguments and types come from
[`__main__.py`](../src/agent_pull_requests/__main__.py) and
[`native_command.py`](../src/agent_pull_requests/native_command.py).
CLI/environment selectors retain their existing meaning; a read request's
target is not the execution repository. Unknown request fields, malformed
identities, unsupported schemas and missing required target data follow their
native strict API validators. Native policy mappings retain recursive merge,
atomic ordinary lists/scalars/null, aliases and migration rules documented by
their owner; this plugin adds no alias or null interpretation.

Each command reacquires its native target/provider configuration. Running
watch state does not become a hot-reloaded preference source. Missing optional
layers preserve defaults; explicit binding/cell/account failures remain errors,
never ambient-account fallback. Configuration reads do not install, start a
watch, fetch a credential, mutate a provider or transfer custody.

Keep tokens, personal watch state, review evidence and private source links
outside shareable configuration examples. Installation manifests/receipts and
runtime selection are governed by the installation owner, not by PR preferences.
