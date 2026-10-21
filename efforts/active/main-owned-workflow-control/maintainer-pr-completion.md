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

October 20 deployment acceptance direction, verbatim:

> Just deploy whenever necessary

> All that needs filing and fixing

This authorizes the normal deployment and repair work needed for this slice,
not ownership transfers, policy exceptions, credential expansion or controller
activation. The installed-update failures are tracked separately: #6925/#6928
retain phase-aware watchdog ownership, #7305 with #7420/#7446 retain Manager
construction and recovery, #6820 retains historical-command reconciliation,
and #7494/#7495 track the new native dependency and staged-refresh regressions.
The maintenance-axis gap is tracked by #7510; #7511 owns the narrow repeated
validator-code import repair exposed by native profile validation.
Repository editing class, agent exposure and Manager project membership are
independent; a related worktree-class repository is not automatically a full
Manager project. Concrete downstream classifications remain downstream.

The first #7510 repair distinguishes an omitted repository class from an
explicit reclassification, including `reference`, while retaining independent
agent preferences and project membership. Its isolated registry, CLI and
retained-reference unregister regressions cover persisted class changes,
metadata retention and invalid-input rejection. The separate related-worktree
unadoption path, historical launcher disposition and installed acceptance remain
open; a class-only repair does not authorize those live transitions.

The #7441 per-review-layer authority fix is qualified by promotion #7448 and
present in the selected, importable Worktrees 1.24.106-dev1 runtime. That is
caller delivery evidence, not whole-update acceptance: Bridge cutover, Dispatch
construction, command ownership and the first real Manager/profile invocation
remain open. The CODEOWNER split stays rolled back and the controller stays off.

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
Promotion #7391 and producing run `39469486744`, attempt 1, bind the qualified
Dev snapshot `5c6ddda800a520ad8989e501529d9f458c970c40` to Main
`917425026a811c641087b402e6d031f8edde5870`. Exact source-unit reuse,
artifact attestation, publication and full raw readback passed before the
candidate PR opened. Installed-runtime and visible Manager acceptance remain
separate; refreshing a payload does not prove its runtime was activated.
Read-only checks of six execution-user legacy project configurations found
the expected required GitHub direct-submit profile and no local PR override.
Other users and separately provisioned installation cells are not thereby
audited. The existing Windows consumer failures in #6947 and #6564 still block
installed acceptance; source merges and a refreshed payload manifest are not
proof that the fixes are running.

### Completion-token permissions and authority acceptance

The owner confirmed the following permission additions and approval of the
updated existing installation on October 20. This is an operator receipt,
not independent App-token verification or controller activation. Completion
tokens must request only this subset, even when another approved use retains
broader App-level permissions:

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

### CODEOWNER-only exception: preserve the independent approval rule

The observed Dev review ruleset combines one required approving review with
required CODEOWNER review. Giving the completion App a bypass on that combined
ruleset would exempt both requirements. That is broader than the requested
CODEOWNER-only exception.

The owner accepted this narrower split on October 20, subject to review of this
amendment before live application:

| Layer | Required behavior | Completion App bypass |
|-------|-------------------|-----------------------|
| Existing review ruleset | Retain one required approval and every other setting; move only the CODEOWNER requirement into the new layer | None |
| New Dev CODEOWNER-only ruleset | Require CODEOWNER approval, with zero additional required approvals | PR-only |
| Existing PR-required, CI and workflow/source-attribution layers | Unchanged | None |

Preserve the existing human bypass actors and modes in both review layers.
Keep their other review parameters unchanged, including unattributed-change
handling. The App is not added to the general approval rule, required-check
rules, classic protection or Main policy.

**Current live state: rolled back.** The initial October 20 application passed
configuration readback but caused the managed merge helper to reject the
independent approval layer before checking actor-scoped authority. The original
combined rule was restored and verified first; the added layer was then disabled,
not deleted. Existing human bypasses, required CI and Main remained unchanged.
Do not reapply the topology until the affected callers are qualified.

Compatibility must distinguish a human already authorized on both layers from
an App authorized only on CODEOWNERS. Every nontrivial review layer considered
for a managed exception needs its own positive provider authorization evidence.
An actor lacking bypass authority on the independent approval layer cannot use
that exception to waive its requirement. Reviewer vetoes, unknown requirements,
extra approvals, required CI and head/base freshness remain separate gates.

[GitHub rule layering](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets#about-rule-layering)
aggregates applicable protections using the most restrictive requirements.
The [REST rules schema](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset)
allows a zero approving-review count independently of the CODEOWNER flag.
That establishes a supported proposal shape, not proof that a live App canary
has enforced the intended interaction.

Apply create-first: capture the current before-images, create and verify the
active CODEOWNER-only layer while the combined rule still protects Dev, then
change only the existing rule's CODEOWNER flag. Recheck the before-images
immediately before each write; concurrent drift or an ambiguous response
requires reconciliation, not a stale overwrite or duplicate rule. Verify the
resulting layers and unchanged CI/classic/Main boundaries afterward.

Rollback restores and verifies the original combined review rule first, then
disables the added CODEOWNER-only rule. It does not delete a rule, reset other
policy, revoke unrelated App permissions or restart a controller.
An accepted retry must reconcile the exact retained disabled layer and its
ownership rather than create a duplicate or reactivate it from name alone.

The separately authorized canary must prove current-head approval admission,
missing-approval rejection and a persistent reviewer veto using the actual App
identity. Controller-side checks remain mandatory even with provider layering.
Credential provisioning, report-only execution and any merge/rebase canary
remain separate acceptance gates. Do not change non-strict Dev CI policy merely
to manufacture a rebase demonstration.

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
| CODEOWNER exception | Create-first/restore-first policy transactions; unchanged human actors and CI/Main protections; the App cannot bypass the independent approval layer; actual approval/veto interaction is proven separately. |

The slice is complete only when implementation and documentation are reviewed
and merged, the admitted release/adoption and canary evidence is recorded, and
every parent Phase 5a and slice validation item is resolved or transferred to a
named tracked objective with an explicit owner. A planning PR, label, green
optional check, issue comment or retired worktree does not complete the slice.
The broader parent effort remains open until its own unrelated phases resolve.
