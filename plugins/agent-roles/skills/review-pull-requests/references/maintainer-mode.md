# Declared Maintainer mode

Return to [the setup procedure](../SKILL.md).

This is an optional adoption contract for a Maintainer-owned sustained role,
not a permission mapping or a new runtime configuration format. Select it in
[the charter](../assets/CHARTER.md) through the repository's normal policy
owner. Generic reviewers do not inherit it. Record the authoritative role
source, assigned CODEOWNER responsibilities, peer-verdict policy, current-review
requirements and existing completion adapter. Missing policy stays unknown;
the word Maintainer alone grants no provider effect or administrative bypass.

## Complete population, qualified coverage

Every sweep enumerates **all open PRs targeting the selected bases**, across
all pages, excluding drafts and the acting reviewer's own PRs. Include other
Maintainer authors. Do not blanket-exclude all Maintainers, PRs this actor
reviewed before, or PRs with a bot review. Partial census and unknown roles
remain unknown, not exemptions or evidence of an empty actionable queue.

For each active PR, refresh head/base, discussion, official non-dismissed
reviews, required checks and actual review blocks. Resolve author/reviewer
roles from authoritative provider grants and repository policy, not an
author-association label or a globally assumed permission-to-role mapping.
Determine required qualified Maintainer acceptance, CODEOWNER coverage,
latest-push restrictions and any applicable review-rule exception separately.
Retain the evidence, scope, revision validity and unresolved gaps, not just
counts of open PRs or prior votes.

Where assigned CODEOWNER responsibility requires review, this role supplies
the missing review unless another eligible Maintainer already provides valid
coverage under actual policy. A bot's COMMENTED review is not qualified
Maintainer approval. Even an APPROVED review counts only if its actor,
ownership scope and current or policy-valid retained authority qualify.
Another Maintainer's valid coverage can avoid a duplicate vote, but not erase
an explicit request or responsibility that repository policy still assigns.

Unclear foreign or bot coverage is not a reason to defer this role's otherwise
authorized, warranted independent review. Resolve what is available, retain
unknowns, and provide an honest own assessment; another approval is permissible
when the review need is real, not a demand to force a positive verdict or repeat
an identical own vote on an unchanged head. An actual missing authority, required
input, provider safeguard or hold still blocks the affected action.

Qualify each opinion by actor, scope, head and discussion freshness under the
target's actual policy; keep source assessment, individual native opinion and
aggregate `reviewDecision` distinct. Neither aggregate null nor a success footer
answers whether a required review exists.

| Observed native state | Decision under actual target policy |
|---|---|
| COMMENTED | Assess the findings. It may be a nonblocking or accepted verdict where policy permits, but is not an approving CODEOWNER vote where one is required. |
| APPROVED | Count only qualified, non-dismissed, current or policy-valid retained coverage; unresolved findings and independent gates still matter. |
| CHANGES_REQUESTED | Revalidate the actual blocking findings and their owner; neither another approval nor a passing aggregate silently clears them. Preserve the adopted advisory peer posture for this role's own verdict. |
| DISMISSED | Retain the historical source assessment and effect, but do not count the dismissed native vote as live coverage. |
| null, missing or conflicting state | Resolve individual reviews, discussion and policy; unknown is neither approval nor a veto on an otherwise authorized independent review. Do not infer completion or replay an uncertain write. |

The coordinator owns this census and coverage decision. The selected rubric
owns one PR's judgment: complement native bot functional/line review with the
adopted vision, architecture and governance lens. Verify whether existing
findings were addressed; do not repost identical bot or own findings merely
to demonstrate activity.

## Peer posture without false approval

For another Maintainer's PR, guidance is advisory and nonblocking by default:
recommend improvements rather than issue formal CHANGE_REQUESTS or demands.
An explicit repository/operator policy may select a blocking posture instead;
record that choice and its source. This default is specific to adopted
Maintainer mode, not a universal rule for reviewers.

Advisory posture never means fabricated approval or waived CI, unresolved
threads, security requirements, binding holds or other actual merge gates.
If approval cannot honestly be given, explain the concern and route the actual
gate to its owner. Whether a COMMENT verdict satisfies a particular obligation
is the repository's decision, not a courtesy inferred from the author's role.

## Authorized completion for authors without merge permission

The Maintainer coordinator also owns normal authorized landing for authors
without contributor/merge permissions, classified using the repository's
actual role vocabulary. Identify the existing authorized consumer/completion
adapter and responsible actor; do not assume a generic role label authorizes
effects. Static reviewer lanes never become mergers, fix authors or resource
owners. The original author retains its branch and fixes.

Before completion, refresh current head, required qualified reviews, CI,
threads, mergeability, draft/hold/security state and author consent where
applicable. Use the existing repository completion adapter within its actual
profile. Submission alone is not consent where additional consent is required;
where ordinary policy accepts a ready PR submission, do not invent a universal
extra approval or label. Preserve self-merge profiles and human-owned gates.
An adapter refusal is not permission for arbitrary administrative bypass.
Observe actual provider merge separately and settle only owned obligations.

## Current authority after publication

Every sweep reevaluates whether actual review authority is **current**, even
after a successful POST and GET readback: an author can push immediately after
either. Keep immutable original POST responses and GET commit bindings. Never
manually rebind a review or rewrite its receipt to the newer head.

Refresh current head/discussion/check state and the native non-dismissed
official opinion plus qualified coverage. On movement, keep the effect
historical, assess the justified delta through the retained reviewer and supply
missing current review when actual gates require it. Do not blindly retry a
write. Existing readback/preflight safeguards still apply; record the
non-atomic race where the provider has no server freshness fence. A successful
historical post is not lasting proof of current coverage.

## Clean rebase: retain assessment, satisfy the real gate

Prove lineage and patch/tree equivalence, accounting for the changed base,
dependencies, policy and discussion. A SHA change alone is not changed intent.
If these inputs are unaffected, retain the valid source assessment instead of
performing a full rerun or reposting identical findings. Uncertain lineage,
equivalence or affected dependencies require the justified delta/full gate.

Separately honor actual dismissal, latest-push, CODEOWNER and native-head
requirements, and explicit author re-review requests. A first official review
on a requested newer head may be needed even when its finding is unchanged.
“New material judgment only” is not a blanket reason to leave a legitimate
current-review gate or request unfulfilled. Explain retained assessment versus
new official head coverage; do not claim a fresh full analysis.

Never repeat an identical own vote on an unchanged current head without
qualifying new input. A mechanical rebase does not reset round, time or failure
caps. At a cap, report the unmet gate/request and next owner rather than
fabricating coverage or silently exempting the PR.
