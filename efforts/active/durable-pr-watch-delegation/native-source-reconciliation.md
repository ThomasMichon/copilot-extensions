# Native local source reconciliation

Back to the [CLI parity inventory](cli-parity-inventory.md) and
[canonical effort](README.md). Bounded coordination: #11753.

**Agent-recommended proposal; review precedes implementation.**
This source-only slice advances the PR-domain
[one-service-contract direction](../../../visions/plugins/agent-pull-requests/README.md#cli-through-one-service-contract)
and [preserved source custody](../../../visions/plugins/agent-pull-requests/README.md#delegated-source-with-preserved-custody).
It follows the existing
[version-skew pattern](../../../docs/patterns/service-contract-evolution.md).
It does not qualify HTTP source mutation, remote workspaces or legacy retirement.

## Existing owner and finite boundary

Worktrees `pr-complete [worktree]` already owns local history reconciliation.
`pr_complete.complete_worktree` fetches the configured upstream and chooses
no-op, fast-forward, proof-checked squash reconciliation or preservation of
new local commits through rebase. Its exact squash-tree proof, recorded
merged-PR boundary and branch-named recovery refs remain authoritative.
It can also preserve unmerged work while rebasing onto an advanced upstream;
the command's success is not itself evidence that a forge PR merged.

The dedicated PR CLI has no native completion adapter. The proposal composes
the existing owner, rather than copying its Git algorithm, importing source
from the calling control repository or mutating a remote checkout.

| Surface | Additive guarded form |
|---------|-----------------------|
| Worktrees owner | `pr-complete --repo <registered-target> --expected-branch <local-branch> --expected-head <full-object-id> [--dry-run] --json` |
| Dedicated native client | `complete --repo <registered-target> --expected-branch <local-branch> --expected-head <full-object-id> --worktrees-command <selected-runtime> [--dry-run] [--json]` |

These are local source operations: execute from the managed source worktree
that is to be reconciled, not an unrelated claimant or a target repository's
anchor. No arbitrary workspace path, foreign worktree selector, PR number,
provider/endpoint/credential override or delivered worker-policy file is
accepted in the new form. The existing legacy positional selector, optional
configuration, defaults and result remain unchanged when no guarded fields
are supplied. Supplying any guarded field requires all three and refuses
the legacy positional selector and explicit configuration; do not silently
drop a guard or fall back to the legacy path.

The current source owner's configuration must resolve the explicit target
to that source repository, not a same-named foreign entry or another
repository's delivered PR policy. Reuse the existing managed-source admission
and publication/metadata lock owners. Require the exact existing local record,
recorded Git top level and common repository, an eligible nonterminal source
record, the selected current branch and a clean index/worktree. The expected
head is a full lowercase Git object ID, not an abbreviated ref or revision
expression. Check the source binding and branch/head under the owning locks
before invoking the existing reconciliation algorithm; stale or wrong-source
requests refuse before fetch, backup creation or history mutation.
Recheck the admitted source after any preparation that could invalidate it.
This protects against ordinary stale requests and cooperating source writers;
it is not a claim of atomic protection against arbitrary external Git writes.

The optional operation is `local-source-complete` in the existing version-1,
target-qualified `agent-worktrees.pr-author-capabilities` operation map.
Its declaration describes receiver support, not current source admission or
forge merge evidence. The first implementation may advertise it for enabled,
resolved GitHub, Gitea and Azure DevOps bindings because the operation composes
the local Git owner, not a new provider write. Actual native-platform and
provider/installed qualification remain separate.

## Effects, preview and recovery

After admission, the existing owner may fetch its configured upstream,
advance the selected branch, create its existing recovery ref, and reconcile
history while preserving subsequent local work. No alternate remote or
source algorithm is introduced. Existing hooks and policy are not weakened.
Normal Git conflicts and unavailable upstreams retain the owner's diagnostic
and recovery information.

`--dry-run` preserves the owner's preview semantics: fetching can update
remote-tracking refs, but the selected branch, index and working files are
not reconciled and no completion backup is created. Do not describe this
as a network-free or entirely mutation-free observation.

Neither form creates, pushes, closes or merges a PR, publishes a comment,
requests review, grants consent, releases a hold, changes tracked PR state,
settles a claim, releases resources, marks an effort Done or finalizes the
worktree. Calling `complete` is not a new grant of destructive authority.
Existing effect approvals and independent source/resource/effort gates remain.

The guarded owner returns `agent-worktrees.pr-source-complete`, version 1,
with exact `repo`, `source` (`worktree_id`, `branch`, `expected_head`),
boolean `dry_run`, boolean `ok`, `status`, and the complete original owner
`result` when one exists. Unknown additive fields and nested owner evidence
remain intact. Statuses are:

| Status | Meaning |
|--------|---------|
| `not-attempted` | Guard, source admission, capability or configuration refusal before the reconciliation owner was invoked |
| `preview` | The owner successfully computed the requested dry-run result; no source reconciliation is claimed |
| `confirmed` | The owner returned a successful non-preview reconciliation/no-op result for the admitted source |
| `unknown` | Invocation began but completion or resulting source state is not confirmed, including an owner error |

Only a structurally valid owner success with `preview` or `confirmed` returns
`ok: true` and exit 0. Every other outcome exits nonzero and carries an
actionable diagnostic. An owner error is conservatively unconfirmed: a
failed Git command or abort is not a frontend promise that every ref/index
byte stayed unchanged. Retain recovery refs and the original result, even
on failure; a backup is recovery evidence, not permission to discard work.

The native client validates the exact requested target, expected branch/head,
strict boolean/integer fields, source identity and mode/status coherence.
It preserves a valid complete owner envelope on stdout in JSON mode,
including on failure; additional client diagnostics go to stderr.
Nonzero owner exit never becomes frontend success. Malformed, mismatched,
lost, late, oversized or unavailable output after dispatch stays `unknown`,
never a manufactured successful no-op. Missing source identity on a valid
pre-admission refusal is allowed; an attempted result must identify its
admitted source. No automatic replay, alternate executable, raw Git fallback,
forge fallback or runtime update follows uncertainty. Observe the same
source and retained Git/recovery state before another intentional attempt.
No new operation ledger, checkpoint signature or acknowledgement service
is needed for this finite local adapter.

## Compatibility, delivery and qualification

Expand Worktrees first while retaining every unguarded caller. The native
client selects the optional capability from the actual configured direct
or managed-peer runtime before sending the new guarded fields. Missing,
false, malformed or unsupported capability refuses before reconciliation.
An additive capability field does not disable other native operations.
An old or rolled-back runtime must not receive the new effectful syntax.
Package versions, installed payloads and successful forwarding mocks do
not establish support; no legacy removal window starts here.

- [ ] Qualify the actual guarded Worktrees parser/dispatcher and managed-source
  admission with exact, stale, abbreviated, dirty, detached, wrong-branch,
  foreign-record/repository and mixed legacy/guarded requests. Prove refusal
  precedes fetch, backup creation and reconciliation.
- [ ] Use isolated real Git repositories through the existing owner for
  no-op, fast-forward, squash merge, later overlapping upstream edits,
  post-merge commits, unmerged work, conflict, failed backup and preview.
  Prove original-tip recovery, preservation of subsequent work and unchanged
  PR/claim/resource/effort state; retain existing legacy owner controls.
- [ ] Exercise the real dedicated frontend-to-owner composition, including
  current/prior runtimes, unsupported/rolled-back receivers, strict invalid
  options, additive evidence and direct/managed-peer command selection.
- [ ] Inject owner errors, malformed/wrong-source output, nonzero exits,
  timeout and output loss/budget failure. Preserve exact owner evidence
  where valid, truthful preview/unknown outcomes and zero automatic replay.
- [ ] Record source and native-platform qualification separately from
  installed adoption, HTTP parity, live-provider evidence and the full
  parent Plan/Validation. Do not retire a legacy command or finalize either
  source or orchestration worktree on the strength of this leaf.
