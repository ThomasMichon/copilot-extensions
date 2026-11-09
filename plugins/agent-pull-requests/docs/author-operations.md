# Native author operations

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

On capable peers, `author-capabilities`, `create`, `update`, `ready`, `nudge`,
`resolve-thread`, `reopen-thread` and `push` accept
`--config <delivered-worker-policy>`. The frontend checks the actual peer
response before the dependent author operation, and forwards the same explicit
file. Missing files, unsupported old peers and mismatched worker targets refuse
without effects or an ambient-policy fallback. Old callers without this option
retain their existing behavior; provider rollback requires that old supported
route or a truthful unsupported-contract result, never replay after an uncertain
write.
An explicit file containing the reserved `pr-worker` binding must identify it
with `repo_name: pr-worker`. Missing or different markers refuse during
capability reporting and author operations rather than loading ambient policy.

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
Author exports preserve the target's configured automated `reviewer` binding;
they do not grant a worker arbitrary reviewer selection.

PATs are not required. Without an explicitly selected or already inherited
compatibility credential, ADO operations reuse the existing Azure CLI/Entra
authentication path. This workflow does not acquire a PAT, change credentials
or require new token configuration.

## Local source reconciliation is not finalization

```text
<PR command> complete --repo owner/repository --expected-branch <local-branch> --expected-head <full-commit> --worktrees-command <Worktrees command> --json
```

Run this optional native operation from the exact Git top level of the managed
source worktree to reconcile. The selected receiver must advertise
`operations.local-source-complete: true` in its version-1 author capabilities.
The current source owner's configuration must resolve the explicit target to
that same repository. No arbitrary workspace, positional worktree, PR number
or delivered `--config` is accepted. The owner checks the existing record,
repository, clean source and exact branch/full lowercase commit ID under the
existing publication/metadata locks. A worktree-local completion lock excludes
overlapping completions; publication and metadata admission locks are released
before fetches and Git hooks, then reacquired in that order for source rechecks.
Provider-aware repository comparison accepts equivalent Azure HTTPS/SSH URLs
without accepting another repository. Request-scoped Git isolation prevents
inherited repository/index selectors from redirecting nested Git helpers
without changing the caller's environment.

The adapter invokes Worktrees' existing `pr-complete` algorithm. It preserves
post-merge work, proof-checked squash boundaries and original-tip recovery refs;
it does not push, mark a PR merged, settle claims or finalize a worktree/effort.
Legacy `pr-complete [worktree]` retains its existing result and behavior when
none of the new guarded fields is supplied.

`--dry-run` returns a `preview`: fetching may update remote-tracking refs, but
the source branch/index/files are not reconciled and no completion backup is
created. A successful non-preview result is `confirmed`. Neither is evidence
of a forge merge: the existing owner can also preserve unmerged work through
rebase. `not-attempted` means admission refused before reconciliation;
`unknown` means completion/source state is unconfirmed. Errors after invoking
the Git owner retain its complete original result and any recovery ref.

The version-1 `agent-worktrees.pr-source-complete` envelope preserves the
selected repo, admitted source, expected branch/head, mode and original owner
result. JSON mode preserves valid owner evidence even on failure; nonzero exit
never becomes client success. Lost, malformed, mismatched or oversized results
remain unconfirmed. Inspect the same source and retained recovery refs before
another intentional attempt; there is no replay, raw Git/forge fallback or
automatic runtime update. Unsupported/rolled-back receivers refuse before
the new effectful syntax. Source support is not installed/platform acceptance.

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
exit 0; `not-attempted`, `failed` and `unknown` exit nonzero. Synthesized failures retain that same schema/version and the provider
when already resolved; pre-dispatch refusal may have `provider: null`.
A lost native response preserves the selected target as unconfirmed rather than falling
back or replaying. Reconcile this same PR through observation before another
intentional attempt. Provider readback is not atomic compare-and-swap and
cannot prevent a later participant change. Unsupported or rolled-back peers
refuse only this optional operation; existing supported commands remain usable.

## Reviewer requests are not verdicts

```text
<PR command> nudge --repo owner/repository --number 42 --worktrees-command <Worktrees command> --json
```

Native `nudge` requires `operations.reviewer-request: true` from the actual
selected Worktrees runtime. The initial explicit-target owner supports enabled
GitHub bindings and invokes `pr-author-nudge`, not tracked `pr-nudge`.
The latter retains its original tracked selection and normal unsupported/no-op
behavior. Unsupported explicit targets refuse only this optional feature.

The target's configured automated reviewer is the only selection. An
unbound/unmapped reviewer is a normal no-op without credential acquisition.
For a mapped reviewer the provider checks authoritative target identity,
active lifecycle and non-draft state, then sends one `requested_reviewers`
request through its existing transport. No comment, coding-agent mention,
hold, consent, vote, source, merge or claim mutation is performed.

The `agent-worktrees.pr-review-request` version-1 result retains `repo`,
`number`, `provider`, `supported`, `requested` and
`actions.review_request.status`. Only `accepted` and `no-op` have `ok: true`
and exit 0. Only `accepted` has `requested: true`; it means API acceptance,
not a new review, a pinned review head or merge permission. `not-attempted`,
`failed` and `unknown` exit nonzero. A generic subprocess error or lost or
malformed response after dispatch stays unknown, not a known rejection.
The frontend validates the versioned identity and outcomes and never replays
or falls back. Observe the same PR before deciding on another intentional
request; no fresh verdict does not prove that the earlier request failed.

## Selected native thread resolution

```text
<PR command> resolve-thread --repo project/repository --number 42 --thread 9 --expected-candidate sha256:<64-hex-digits> --expected-thread sha256:<64-hex-digits> --operation-id fix:thread-9 --worktrees-command <Worktrees command> --json
```

Requires the selected runtime's optional `operations.thread-resolve: true`,
initially enabled ADO bindings only, and the invoking claimant worktree.
Worktrees' owner is `pr-thread-resolve` with the same target, native thread,
expectations and operation ID. Neither command accepts bulk selection,
tracked selectors or provider/account overrides. GitHub/Gitea are unsupported;
their legacy bulk resolvers are never an effectful fallback.

Obtain expectations from a fresh native `threads` report. Its additive
`resolution_candidate` has `available`, `changed`, `candidate` and `error`.
Only `available: true` supplies `candidate.fingerprint`. Version 1 hashes
the canonical endpoint, target project/repository/PR, source project/repository,
both refs, head/base/common commits and current source iteration.
An unavailable result may retain `observed_candidate` separately.
Missing native count/nested-comment evidence, ambiguous IDs or candidate movement
cannot supply an expectation, even when the baseline report is complete or exits 0.
Use the selected native thread's existing `fingerprint` for `--expected-thread`;
a display index is not a native identity.

The owner requires actual authenticated principal and effective native read/PR
contribution permissions at repository and target branch. It brackets complete
candidate/thread reads and rechecks before dispatch. Only a live active discussion
with native iteration association qualifies; closed, pending, deleted and
system/vote-only threads refuse. Comment ownership is not a substitute for native
action authority. Capability reporting grants no operator authorization.

The sole intended effect is `{"status":"closed"}` on that thread, through the
existing remediation owner and consumed-before-write ledger. No reply, marker
comment, sibling resolution, vote, consent, hold, merge, source or claim is changed.
`agent-worktrees.pr-thread-resolve`, version 1, retains exact requested identity,
operation ID, provider/principal, expected/observed evidence, `reason`, `error`
and `actions.resolution.status`. Only `confirmed` has `ok: true` and exit 0.
`not-attempted`, authoritative `failed` and `unknown` exit nonzero.

This is **preflight plus readback, not atomic compare-and-swap**. Confirmation
requires the authoritative same-thread response, unchanged candidate and closed
readback with no content/identity change except status and native thread-update
timestamp. Comment authors, bodies and timestamps may not change.
Lost, malformed, timed-out, late or changed readback stays unknown, even if a later
read sees a closed thread. Keep the same operation ID: consumed keys reconcile
read-only, and conflicting actor/candidate/thread bindings refuse. There is no
automatic second PATCH, synthetic attribution comment or alternate transport.
Valid owner envelopes, additive fields and nonzero exits are preserved; lost
frontend output is unknown, not a retry instruction. Isolated source controls
do not qualify native Windows, installed/account/HTTP or three-provider lifecycle
acceptance.

## Selected native thread reopening

```text
<PR command> reopen-thread --repo project/repository --number 42 --thread 9 --expected-candidate sha256:<64-hex-digits> --expected-thread sha256:<64-hex-digits> --operation-id reopen:thread-9 --worktrees-command <Worktrees command> --json
```

Requires the selected runtime's optional `operations.thread-reopen: true`,
initially enabled ADO bindings only. Worktrees' `pr-thread-reopen` takes the
same selection and expectations. Reuse the fresh report, full candidate,
native principal/permission and iteration requirements described for
[resolution](#selected-native-thread-resolution); neither capability nor native
permission supplies operator effect authority.

Only a live discussion in native `fixed`, `wontFix`, `closed` or `byDesign`
state qualifies. Active, pending, unknown, deleted, system/vote-only,
incomplete or stale evidence refuses. The sole intended effect is
`{"status":"active"}` on that thread; it does not reopen the PR, reply,
edit comments, vote, release holds, grant consent, merge or change siblings.
Legacy resolution commands and status choices keep their meanings.

`agent-worktrees.pr-thread-reopen`, version 1, retains the same selected
identity/evidence fields, with `actions.reopening.status`. Only `confirmed`
has `ok: true` and exit 0. Preflight plus authoritative response/readback
requires the original candidate and unchanged thread content except status
and native thread-update timestamp; it is not atomic CAS or exclusive causal proof.

Use a new intentional operation ID. The existing action-scoped ledger consumes
reopening before dispatch, independently of prior resolution operations; this
is not global cross-command or cross-installation idempotency. Conflicting keys
refuse, and interrupted/unknown keys reconcile read-only without another PATCH.
Later observing an active thread cannot upgrade an original unknown outcome.
A stored confirmed result describes the original operation, not a promise the
thread remained active; independent reconciliation observations can show reclosure.
The conditional client preserves additive fields and uncertainty, with no raw,
bulk or effectful compatibility fallback. Source controls do not establish
native Windows, installed/account/HTTP or full lifecycle qualification.

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
