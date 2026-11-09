# Native Azure DevOps author operations

The native frontend delegates to the selected Worktrees runtime. It does not
keep another PR-policy, credential or source-custody store. Substitute the exact
attributable commands from the session command catalogs below; an executable
on `PATH` or a package version is not a capability response.

```text
<PR command> author-capabilities --repo project/repository --worktrees-command <Worktrees command> --json
<PR command> create --repo project/repository --head feature/fix --title "Fix" --body-file <description-file> --draft --source-workspace <absolute-source-root> --expected-head <full-commit> --worktrees-command <Worktrees command> --json
<PR command> update --repo project/repository --number 42 --body-file <description-file> --reviewer <identity-id> --work-item other-project/123 --worktrees-command <Worktrees command> --json
<PR command> ready --repo project/repository --number 42 --worktrees-command <Worktrees command> --json
<PR command> push --repo project/repository --workspace <absolute-source-root> --head feature/fix --expected-head <full-commit> --worktrees-command <Worktrees command> --json
<PR command> status --repo project/repository --number 42 --worktrees-command <Worktrees command> --json
<PR command> wait --repo project/repository --number 42 --timeout 600 --worktrees-command <Worktrees command> --json
```

The claimant remains the invoking managed worktree. The explicit registered
target selects provider, endpoint, credentials, configured base and publication
policy; it never inherits the caller's unrelated merge rights. New author
operations are advertised individually by `pr-author-capabilities` schema 1.
The frontend checks that actual response before the dependent operation.
Missing or older peers refuse: no raw forge fallback, automatic update,
credential workaround or silent switch to GitHub.

## Explicit worker policy

After separately authorizing author work, use the existing
[worker policy export](../../agent-worktrees/docs/pr-publication-checks.md#delivering-policy-to-a-worker)
with `--include-author`. Ordinary exports remain read/check-only.
The selected Worktrees runtime advertises `operations.worker-policy-export`
and `operations.worker-policy-config`; this frontend separately advertises
`frontend.worker-policy-config` in its `author-capabilities` response.
These additive schema-1 capabilities are independent of package versions.
Do not infer frontend support from the Worktrees peer's declaration alone.

On capable peers, `author-capabilities`, `create`, `update`, `ready` and `push` accept
`--config <delivered-worker-policy>`. The frontend checks the actual peer
response before the dependent author operation, and forwards the same explicit
file. Missing files, unsupported old peers and mismatched worker targets refuse
without effects or an ambient-policy fallback. Old callers without this option
retain their existing behavior; provider rollback requires that old supported
route or a truthful unsupported-contract result, never replay after an uncertain
write.

The reserved exported `pr-worker` binding is target-scoped in memory. Author
operations use its declared PR policy without inheriting a same-slug registry
entry or checkout policy. Caller custody and metadata locking still use the
invoking project's configuration, separately from that target policy. The
worker binding does not become the claimant worktree: source and PR claims
still belong to the invoking managed caller. Publication-origin selection uses
the delivered target declaration independently of the caller's origin policy,
and does not activate itself merely because the file contains a declaration.
Exported policy carries
no credentials, self-approval, self-merge role or bypass grant, and does not
authorize dispatch, publication, auto-completion or a human-only action.
Status/wait and subscription routing remain unchanged by this author-only
configuration option.

PATs are not required. Without an explicitly selected or already inherited
compatibility credential, ADO operations reuse the existing Azure CLI/Entra
authentication path. This workflow does not acquire a PAT, change credentials
or require new token configuration.

## Draft readiness is not merge consent

Native `ready` requires the selected Worktrees runtime's optional
`operations.draft-ready: true` capability. The initial explicit-target owner
supports enabled Azure DevOps bindings. It invokes Worktrees'
`pr-author-ready`, not tracked `pr-ready`; the latter retains its original
tracked selection and legacy hold-release behavior.

The explicit author operation reads the selected PR's actual repository,
number, active lifecycle and boolean native draft state before a single
draft-only update, then verifies authoritative same-target readback. An
already-ready PR refuses without a write. No hold label, consent,
auto-completion, vote, reviewer, title, source or claim is changed.

Its `agent-worktrees.pr-ready` version-1 result retains `repo`, `number`,
`provider` and `actions.draft.status`. Only `confirmed` has `ok: true` and
exit 0; `not-attempted`, `failed` and `unknown` exit nonzero. A lost native
response preserves the selected target as unconfirmed rather than falling
back or replaying. Reconcile this same PR through observation before another
intentional attempt. Provider readback is not atomic compare-and-swap and
cannot prevent a later participant change. Unsupported or rolled-back peers
refuse only this optional operation; existing supported commands remain usable.

## Candidate checks and source custody

Creation from an already-pushed branch with configured checks requires the
explicit reachable delegated source root and full expected commit. Worktrees
verifies its exact Git root, branch, canonical push destination, current base,
existing self-review/scope evidence and authoritative forge refs. Source or
target movement during preflight refuses before creating anything.

An admitted source workspace is not an unrelated managed caller checkout.
Publication retains the user's branch, Git hooks and ordinary fast-forward
protections. It does not rename the branch, rewrite published history or copy
commits from the caller. A refusal caused by remote work requires source-owner
reconciliation under the target's merge-forward/rebase policy, not a force push.
Read the source operation's returned disposition before retrying.

The initial adapter is `reachable-workspace`. An exported worker configuration
is policy, not source adoption or credentials. Remote-only receipt transport is
not advertised: self-supplied hashes cannot qualify an unseen source. Keep a
consumer's existing supported route until its actual installed replacement has
passed that consumer's acceptance.

ADO cannot atomically fence PR creation to both observed refs. Post-create
verification therefore reports `verified-after-create`, `changed-after-create`
or `unknown-after-create` without discarding the created PR identity or caller
claim. A successful response is not a promise that a branch can never move.

## Metadata repair and uncertain effects

`update` addresses an existing PR. Optional title/body values are exact
replacements; omitting either leaves it untouched. A body file is UTF-8 and
preserves multiline, blank-line, quote and Unicode content through provider
readback. Native create/update forward a body-file path to the owner rather
than expanding its contents into another process's command line; use files for
long descriptions and Windows argument-limit safety. Reviewer requests and
work-item links are additive, not replacement
of unrelated metadata. Work items are explicitly project-qualified. The
provider retains revision guards and verifies both relation endpoints.
Publication-origin labels compose with the existing additive label operation.

Inspect the returned PR identity and every action outcome, including on a
nonzero exit. Confirmed creation is not undone by a failed description,
relation, label or claim step. Reconcile unknown effects against that same
PR before retrying; metadata-only repair never repeats create or push.
Neither author checks nor reviewer requests cast votes, attest human presence,
approve a PR or authorize completion.

Observation is unchanged: native snapshots retain votes, approvals, policies,
threads and human gates independently. Bounded wait timeout is not terminal
completion. Durable shared subscriptions, thread-fenced replies and existing
evaluation requeue remain their existing owners' operations, not a second
author-side watcher or per-task polling service.
