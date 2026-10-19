# Maintainer PR completion steward

**Status: staged and disabled.** The workflow has a literal false job guard,
the checked-in configuration is disabled, and no App binding is configured.
This is not the repository's active completion path. Authorized submitters
continue using the managed direct-submit flow in `CONTRIBUTING.md`.

The reviewed parent contract is
[Maintainer PR completion](../efforts/active/main-owned-workflow-control/maintainer-pr-completion.md).
This tool is repository-owned orchestration, not another installed PR service.

## Source and admission

`maintainer_pr_completion.py` runs trusted repository tooling. The staged
workflow checks out its immutable `github.workflow_sha`, never a PR head,
artifact or branch supplied by the event. It has read permissions only and
always requests report-only operation. Neither a repository variable nor a
manual input can enable it.
Network observations also require that Main workflow context, so this is not
an alternate local observer using an ambient account. Manual recovery is a
targeted workflow dispatch; a local disabled preview never contacts GitHub.

`.github/maintainer-pr-completion.json` owns the repository/base, consent,
hold and WIP bindings. The initial scope is an open, same-repository Dev PR
from an invited collaborator in the pinned base's root CODEOWNERS roster.
Drafts, forks, release candidates, missing consent and holds are excluded.
The live base ref must agree with the PR's observed base identity.

Review admission is stricter than the current direct-submit Comment policy:
the latest submitted Copilot review must actually be `APPROVED`, cover the
current head and follow the latest reopen or base-ref-change epoch.
Comments, dismissed approval and incomplete history are not approval.
The shared PR-domain reviewer-lifecycle policy preserves every outstanding
reviewer change request. Required CI uses the same source-aware, complete
current-policy verifier as the managed merge path.

The adapter reads full bounded review, epoch, rule and comment pages.
Malformed, partial or unavailable evidence is reported, never converted into
an empty history or affirmative authorization. Ordinary row/status projection
and cached observed-green checks are not mutation evidence.

## Bounded reconciliation

Each candidate is observed again before a write. A changed head, base,
consent, hold, membership, review or CI decision returns it to observation.
Native `REBASE` uses `expectedHeadOid` only when current policy requires an
up-to-date branch and the provider reports rebase capability. Base advancement
alone does not trigger a rebase. A successful update never carries the old
review or CI into a merge of the new head.

Merge delegates to the existing normal-first, head-pinned provider helper.
Only its independently proven missing-required-approval exception may use the
configured fallback; CI, reviewer negatives and other policy are not excepted.
Provider-confirmed merge completion and the configured App actor are checked
separately from request delivery.

Only positive provider conflict evidence returns resolution to the original
submitter. One App-owned comment is created or updated in place; duplicate
events do not create a stream of notices. Unreadable metadata, API failures
and ambiguous write responses are not labeled conflicts. An uncertain write
is reported with unknown application state, is not immediately retried, and
requires fresh reconciliation.

The staged workflow is manual-only as well as job-disabled: it creates no
automatic, skipped workflow runs while awaiting acceptance. The activation
proposal reuses completion of the existing CI and metadata workflows, with a
review-workflow hint and a 15-minute catch-up for missed ordering/associations.
That trigger wiring and the App credential step require the reviewed activation
change; they are not active subscriptions today.

Reconciliation is serialized without cancelling an active writer. There is no
runner waiting for a review or CI. A pass admits at most 100
consented candidates, at most ten 100-entry pages per collection, a
240-second API budget and a 45-second request cap. Overflow or deadline
exhaustion reports the need for targeted manual recovery rather than quietly
dropping the remaining work. One unreadable candidate does not hide the
outcomes of candidates already reconciled.

## Arguments and results

- Default or `--report-only`: observation only, subject to the configured mode.
- `--pr N`: bounded targeted recovery; it does not relax admission.
- `--apply`: requires enabled configuration, trusted Main workflow context and
  the explicitly approved, action-minted App/installation binding.

Disabled report-only operation reports that no action was applied. A disabled
`--apply` is a nonzero refusal. JSON distinguishes observation, pending request,
confirmed completion, unavailable evidence and unknown write outcome; request
acceptance never means the PR merged.

## Separate activation and acceptance

Live operation still requires owner acceptance of the exact single-repository
App, requested permissions, Main-only credential environment, review-only
ruleset exception, workflow/configuration change and rollback. The licensed
review-request credential is not a merge credential. No new identity,
credential, ruleset exception or environment is created by this implementation.

Head compare-and-swap is not an atomic review, label or base-version lock.
Fresh reads and provider-side checks narrow those races but do not eliminate
them. Activation must explicitly accept that boundary and qualify the actual
App identity, event ordering, conflict notice, rebase/new-review/new-CI/merge
canary and original-worktree reconciliation. Normal reviewed Main tooling is
part of the repository's existing trusted-source boundary.

The owning worktree retains unpublished work, backup, publication and
reconciliation obligations. Neither an auto-merge label nor a controller
result transfers those claims or finalizes the submitter's worktree.

Focused contracts live in the repository-harness test suite:
`plugins/copilot-extensions-harness/tests/test_maintainer_completion_*.py`.
That existing suite owns the regression execution; no additional CI job is
required.
