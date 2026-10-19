# Maintainer PR completion and ownership visibility

Parent and completion gate: [Main-Owned Workflow Control](README.md#phase-5a---opt-in-maintainer-merge-and-rebase-stewardship).
Standing intent: [workflow stewardship](../../../visions/main-owned-workflow-control/README.md),
[shared PR capability](../../../visions/plugins/agent-worktrees/pull-requests/README.md)
and [claims presentation](../../../visions/venue-pivots-ux/README.md).

## Status and ownership

The contract was reviewed and merged in #7219. Readiness/guidance source merged
in #7225 and #7232, claims presentation in #7228, and the disabled controller in
#7244. Qualified release, installed behavior, live authority and activation
remain gated. The **PR completion steward** owns this Phase 5a slice and its
readiness, guidance and completion visibility prerequisites. The parent
campaign remains Active.

**Identity direction (operator summary):** reuse the existing App installed only
for this repository, under sole repository-owner administration; do not create a
second App. This selects an identity, not new permissions or live activation.

This extends the existing plan from #7165, not a second automation campaign.
Existing #3336 release, #7122 capacity, #1303 coverage, source-attribution and
review-rescue owners retain their responsibilities. Independent implementation
and validation can proceed in parallel; actual overlapping edits and merge
actions are serialized. No new credential, ruleset exception, live workflow
enablement or deployment is authorized by this plan.

The existing durable PR-watch/installation-cell contributor retains its
in-flight `skills/worktree/references/pr-workflow.md` edits. Readiness work
must coordinate that file explicitly rather than overwrite unpublished
changes. Existing configuration/performance and coverage contributors retain
their active configuration, reconciliation and test-portfolio files.

## Operator direction

The parent Request already captures the consent-label, automatic rebase and
current-head Copilot approval proposal. Additional October 18 direction:

> Focus initially on ensuring the setting at least encourages maintainer-role agents to know they can drive their PRs through after getting Copilot review and green CI only

> Yeah we also need to make sure the copilot-extensions repo config is set right and no machines have an improper local override of it

> The auto-merger should only bypass the requirement that a CODEOWNER must *provide* review; it doesn't get to overcome a blocking review from anyone.

> The CLAIMS column needs to flatten the hierarchy and then sort claims, not just show only immediate claims of a worktree.

> Agree. Take ownership of and update that effort, incorporate your analysis, build out vision and effort docs, and drive this

These directions authorize the completion slice, not reassignment of other
participants' release or control responsibilities. The mechanisms and tests
below are **agent-recommended** ways to implement those requirements.

## Evidence and the delta

Evidence was read against Dev `37ecc15c8c`; refresh before implementation:

| Finding | Evidence / existing owner | Consequence |
|---------|---------------------------|-------------|
| Authority text contradicts a completed review | `pr_ops.self_merge_bypass_note` always says "No verdict yet"; #3638, #4234 | An authorized submitter can interpret ordinary review-rule handling as another operator approval gate. |
| Readiness mixes different operations | `pr_contract` consent eligibility and `pr_state_cli` verdict casing | A self-merge caller can receive consent-only reasons or an awaiting-review reminder after an actual approval. |
| Guidance describes obsolete behavior | Contribution/PR-workflow prose describes unconditional admin merge and an impossible owner approval shape | Text disagrees with real approvals and the provider's normal-first merge implementation. |
| Mutation protection is narrower than the desired contract | `github_required_checks` verifies required CI for admin fallback; `pr bar` separately checks reviewer vetoes | Clearer permission wording alone cannot establish that only the missing required approval is excepted. |
| Descendant projection exists but is not released | #7134 / `55f94f9776`; installed/released producer comparison | Do not implement a duplicate flattening fix or claim a source merge changed the visible Manager. |
| Partial claim results lack visible qualification | Producer emits counts/completeness; Manager forwards only summary/links | Truncated or unresolved descendants can look like a complete list. |

The public issue history provides historical observations, not current policy:
#3638's older zero-required-approval premise must not replace a fresh provider
read of required checks, rulesets and the acting identity's authority.

## Contract

### Permission is not readiness

One shared PR-domain decision distinguishes the following facts:

- The configured flow and the acting identity's live authorization.
- Copilot review evidence for the current revision and accepted review shape.
- Every still-active change request, regardless of another reviewer's approval.
- Required CI completeness, result, source identity and revision.
- Draft/hold/conflict state and the current source/base identity.
- Author consent where the selected flow requires it.

Unknown evidence is not permission. A live, repository-authorized exception to
an absent CODEOWNER approval is not an instruction to invent a raw bypass.
Conversely, that expected exception must not be presented as an unexplained
policy violation requiring another operator prompt.

For the current direct-submit profile, preserve the repository's documented
clean-comment acceptance where applicable; an actual approval is also valid.
The initial automation proposal retains its stricter current-head Copilot
`APPROVED` prerequisite. Do not silently make those policies interchangeable,
infer review cleanliness from the verdict alone, or relax contributor human
acceptance requirements.

### Reviewer vetoes survive automation and unrelated reviews

A later comment, pending review or another reviewer's approval does not clear
an outstanding change request. Resolve its disposition through the provider's
actual review lifecycle and applicable policy. Rebase and head movement do
not erase that negative. Both direct completion and automated completion
must preserve the veto, required CI, holds and revision checks.

### Consent delegates responsibility, not completion

An accepted consent label transfers bounded rebase/merge stewardship to the
repository automation while the submitting worktree retains its publication
and reconciliation obligations. Removing consent or adding a hold stops further
writes. A new head requires new evidence; no old-head approval or checks carry
forward as if they covered rebased content.

Only a positively identified conflict returns conflict resolution to the
submitter, with one deduplicated actionable notice. Unknown API outcomes,
rate limits and concurrent updates reconcile without being mislabeled as
conflicts. Rebase only when necessary for the configured merge policy; do not
manufacture endless review and CI rounds merely because the base advanced.

### Visible descendants do not become ancestor-owned claims

Flatten eligible descendant claims before the existing shared prominence
ranking, then deduplicate by canonical resource identity. Preserve the real
owner and enough provenance to reach it. The row stays bounded; show omitted
counts and incomplete/unresolved state, with a supported detail path instead
of an unbounded graph payload or SSH per render.

Cross-machine absence is not an empty or settled subtree. Use existing
authoritative publication/relationship boundaries; unavailable descendants
remain visibly unresolved. Projection must never mutate claim ownership or
weaken per-hop finalization.

## Ordered implementation slices

1. **Reviewed intent and reservation.** Claim #6237 Phase 5a, update its vision,
   this contract and the parent checklist; retain #3638/#4234 instead of
   duplicating their trackers. Publish the plan before implementation.
2. **Current direct-flow repair.** Normalize verdict handling and make
   status/watch/reminders operation-aware. Reuse provider/PR-domain policy
   rather than a new CLI-specific policy. Enforce negative-review and required
   CI gates at the mutation boundary and pin the head. Correct upstream
   contribution/config/skill wording; update adopter-specific prose only in
   its owning repository.
3. **Completion visibility.** Reuse #7134. Carry completeness and omitted-count
   information through Manager rows and detail navigation. Extend projection
   only for demonstrated missing cases; preserve source ownership and limits.
4. **Disabled consent steward.** Extend Phase 5a's existing provider-native
   expected-head rebase and trusted-default-branch automation proposal.
   Evaluate authoritative live state for each bounded action; no head-code
   execution, waiting runner, replacement PR service or competing observer.
   Reuse existing check/review evidence contracts where suitable.
   The repository-owned [staged implementation](../../../tools/maintainer_pr_completion.md)
   keeps both workflow execution and configuration disabled; its contracts run
   in the existing repository-harness suite, not a new CI lane.
5. **Authority acceptance and canary.** Present the exact proposed automation
   identity, permissions, review-only exception, configuration diff and
   rollback to the repository owner. Do not reuse the licensed review-request
   credential or assume human Maintainer exceptions authorize an App.
   After explicit acceptance, prove report-only behavior, conflict return,
   a rebase/new-review/new-CI/merge canary and local reconciliation.
6. **Adoption and completion.** Switch canonical consent binding only after
   the released controller and canary are proven. Audit effective configuration
   and machine-local overrides only within each driver's authorized venues.
   Record unreachable or externally owned cells as named follow-up gates,
   not clean audits. Observe real completion and finalization; source merge,
   qualified release, local installation and working behavior are distinct.

Steps 2 and 3 may use independent worktrees after plan review. Step 4 depends
on the readiness contract, not on every unrelated portfolio wave. Steps 5 and
6 retain explicit policy/credential/rollout approval gates.

The source-delivery boundary is now recorded, not an activation decision.
Read-only checks of six execution-user legacy project configurations found
the expected required GitHub direct-submit profile and no local PR override.
Other users and separately provisioned installation cells are not thereby
audited. The existing Windows consumer failures in #6947 and #6564 still block
installed acceptance; source merges and a refreshed payload manifest are not
proof that the fixes are running.

### Proposed completion-token permissions

The following is an **agent-recommended activation proposal**, not a report of
the existing App's live grants or approval to change them:

| Repository permission | Completion token | Purpose |
|-----------------------|------------------|---------|
| Contents | Write | Merge and update the opted-in branch; read pinned source policy |
| Pull requests | Write | Read review history, request native update, and maintain PR conflict notices |
| Checks | Read | Verify check-run results and integration identities |
| Commit statuses | Read | Verify required legacy status contexts |
| Administration | Read | Inspect classic branch protection and required-check policy |
| Metadata | Read | Inspect repository identity, rules and collaborator membership |

[GitHub's endpoint permission reference](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps)
defines these access levels. PR comments accept Pull requests write instead of
Issues write, so no separate Issues write is proposed. Completion needs no
Actions write, Workflows write, secret-management or organization permission.
Other approved uses of the same App may retain their own grants; completion
tokens request only this subset. Downscoping a token does not isolate App-key
holders from the App's overall authority.

The Dev review-only ruleset exception is a separate owner-approved policy
change, not another token permission or a CI bypass. Reuse still needs exact
App/installation evidence, the protected Main-only workflow credential boundary,
reviewer-veto enforcement, and the separately accepted live canary and rollback.

## Validation and completion gate

| Surface | Required positive and negative evidence |
|---------|----------------------------------------|
| Readiness | Uppercase approval and clean-comment handling; no false "No verdict yet"; no consent-only blocker in a direct flow; unknown actor permission remains unknown. |
| Reviewer lifecycle | Current-head Copilot approval plus a human negative; another reviewer's later approval/comment; same-reviewer disposition; pagination and unavailable history. |
| Mutation | Normal-first, head-pinned merge; permitted missing-approval fallback only; no bypass for pending/missing/failed/wrong-source required checks, vetoes, holds, draft state or head movement. |
| Rebase | Expected-head race, moved base, unsupported update method, actual conflict, ambiguous response, and fresh evidence required after success. |
| Claims | Parent/child/grandchild, shared descendant, canonical duplicate, missing/cyclic/cross-machine edge, bounded overflow and unchanged original ledgers; partial results remain visible in Manager. |
| Integration | Required-CI-first and review-first ordering, consent removal, duplicate events, catch-up, original-worktree unpublished work and backups, merged-PR reconciliation and claim settlement. |
| Adoption | Exact released implementation, effective canonical configuration, permitted local-override audit and explicit live-canary approval; no claim that Dev merge installed a fix. |

The slice is complete only when implementation and documentation are reviewed
and merged, the admitted release/adoption and canary evidence is recorded, and
every parent Phase 5a and slice validation item is resolved or transferred to a
named tracked objective with an explicit owner. A planning PR, label, green
optional check, issue comment or retired worktree does not complete the slice.
The broader parent effort remains open until its own unrelated phases resolve.
