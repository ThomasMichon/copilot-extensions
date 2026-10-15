# Required dev PR Attribution

Parent: [Private PR Source Attribution](README.md). Tracking: #6295.

## Request

> "we should make copilot-extensions's CI pipelines *enforce* that PRs to dev
> *must* carry the attribution marker. The CI pipeline must check for a marker,
> and explain the format it's looking for."

> "As also should give the agents explicit instructions on adding the
> attribution manually, both upfront in CONTRIBUTING.MD (or some dynamic thing
> in pr-workflow.md) and in the output of the CI status check when it fails."

> "We should consider adding a special label that could bypass no no-attribution
> requirement; but agents shouldn't apply it, nor should the CI pipeline remark
> on its existence or not, except to allow the PR through when it is present"

Operator decisions:

- All dev PRs are covered; automation must carry workflow-origin attribution.
- The operator enabled `attribution-exempt` as a silent exception. Agents must
  never apply or remove it. CI diagnostics must not mention its presence,
  absence, name, or use as a repair path.
- After plan review, the operator authorized a distinct required
  `source-attribution` context and extension of the existing no-bypass ruleset
  only after deployment/verification. This prevents already-open PRs from
  relying on old green lockdown checks that never examined source metadata.

## Plan

- [x] Clear this proposal's automated review gate before implementing it (#6296).
- [x] Require a structurally valid source marker in the current PR body for
  every PR targeting `dev`, regardless of author, branch, or changed paths.
  Ordinary managed sources use `<!-- agent-worktrees:source
  codename=<assigned-public-handle> -->`, with optional `root` and `enc`.
  Repository automation uses the same comment envelope with a public
  `workflow=<workflow-file-slug>` origin and optional run ID. gh-aw's existing
  compiler-controlled `gh-aw-workflow-id` comment is an accepted native
  workflow origin, avoiding a second model-generated marker or artifact rewrite.
- [x] _(agent-recommended)_ Reuse the existing independently required trusted
  `workflow-lockdown-guard` workflow path; publish the separately required
  source context from that same trusted workflow and activate its no-bypass
  rule only after live verification. Do not let PR-head changes disable their
  own metadata validation.
  Never check out or execute PR-head content in this trusted metadata job.
- [x] _(agent-recommended)_ Read current PR metadata through the GitHub API and
  reevaluate on opening, pushing, reopening, body/base edits, and label changes.
  A body-only repair must clear the gate without a dummy commit.
- [x] Silently skip only the attribution requirement when the operator's
  exception is present; protected-path authorization remains independent.
  This is operator policy, not a claim that GitHub can distinguish an agent
  from a human sharing the same account or label permissions.
- [x] Give missing/malformed-marker failures concise format examples and
  concrete preparation/manual-attachment steps. Do not echo untrusted PR
  prose, private source identifiers, or ciphertext into CI diagnostics.
- [x] Put mandatory attribution and manual repair guidance near the front of
  CONTRIBUTING.md and in the reusable PR workflow reference. Explain that
  agents repair metadata, not bypass CI.
- [x] Update every repository-owned automation producer targeting `dev` to
  supply its workflow-origin marker, preserving existing PR body content.
  Promotion/rollback PRs targeting `main` remain outside this dev-only rule.
- [x] Record deployment honestly: trusted workflow changes become effective
  only after their reviewed dev changes are promoted to main. Do not call the
  gate live merely because the implementation PR merged.

This is a repository-specific requirement. It does not remove the generic
agent-worktrees attribution opt-out from other repositories. Presence/format
validation establishes neither cryptographic provenance nor actual ownership;
the optional encrypted identity remains an owner's reverse-lookup mechanism.

### Enforcement trust boundary

This requirement inherits the repository's already accepted required-check
context-name limitation, documented in `docs/pipelines.md` and the
`workflow-lockdown-guard.yml` header. GitHub binds the check by context name
under the GitHub Actions publisher; a malicious trusted Write contributor can
attempt to forge the same context from an untrusted workflow. This plan accepts
that existing residual risk for attribution too: it enforces normal submissions
and accidental/manual-tool omissions, not a cryptographically hard boundary
against a collaborator deliberately forging CI.

A stronger adversarial guarantee would require a separately trusted publisher
pinned by `integration_id`; it is not silently claimed or introduced by this
change. Live validation must cite the actual trusted workflow's run and
definition, not infer successful deployment from a same-named check alone.

## Validation

- [x] Missing, malformed, duplicate/conflicting fields, private/raw fields and
  unsafe marker values fail with actionable, public-safe repair guidance.
- [x] Valid codename and workflow-origin forms pass; optional root/encrypted
  fields follow the existing marker contract in the worktree skill's
  [PR attribution reference](../../../plugins/agent-worktrees/skills/worktree/references/pr-attribution.md),
  backed by `providers/attribution.py` and `codename.is_valid_handle`.
  The implementation must publish the validator's exact accepted grammar in
  the contribution/repair guidance and test it. Encryption never becomes
  mandatory key setup.
- [x] Current-body retrieval and edited events prove that adding/removing a
  marker reevaluates the same PR head; non-dev PRs remain unaffected.
- [x] The operator-only exception passes silently, its removal restores normal
  enforcement, and it never disables the separate protected-path guard.
- [x] Every dev automation PR producer emits a valid workflow-origin marker.
- [x] Focused validator and workflow-wiring tests run locally. A disposable
  live dev PR proves missing-marker failure and body-only repair once the
  trusted workflow is actually deployed; close any validation-only PR after
  capturing evidence rather than merging it.
- [x] Implementation passes current-head review/required CI, merges, promotes,
  and receives live required-check evidence before this phase is complete.

## Journal

- #6296 reviewed and merged before implementation. The rollout refinement
  above was explicitly authorized afterward; it preserves the same trusted
  workflow and accepted publisher boundary while covering existing PRs.
- #6356 merged after current-head approving review and CI
  [38388369063](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38388369063).
  Dev-push CI
  [38391631615](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38391631615)
  passed. The implementation includes Markdown/HTML-aware format validation,
  independently scoped protected-path checks, bounded current-body backfill,
  shared-head failure fences and peer-departure recovery.
- The authorized operator-only exception label was defined after confirming
  it was absent. It was not assigned to or removed from any PR. Its definition
  does not activate enforcement or waive independent checks.
- Main promotion remains blocked by unrelated full-suite fixture failures;
  #6593 owns the latest obligation-settlement correction. The existing
  no-bypass ruleset still requires only its original workflow-lockdown context.
  Source-attribution activation is deliberately pending actual deployment and
  trusted-workflow live negative/body-only repair proof, not merely a dev merge
  or a same-named check.

### 2026-10-14 — Live proof and activation

- Promotion #6631 merged the reviewed source workflows to main. The exact
  validator/helper/lock and both workflow blobs were compared at main merge
  `f5e4e30353a076aec74781e6ef5cfe5df474e939`, not inferred from dev ancestry.
- Meaningful documentation draft #6526 supplied the live proof. Head
  `46675aca876cbb09d89160e3d53b4c72153475f5` stayed unchanged while only its
  own source comment was removed and then restored. Trusted lockdown run
  [38519522904](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38519522904)
  failed with `Missing source attribution marker.` and actionable preparation
  guidance, while the independent protected-path job passed. Body-only repair
  run [38520056211](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38520056211)
  passed. An earlier transport-error run was explicitly not credited as a
  missing-marker proof.
- Validation-only draft #6638 used a distinct managed reference at the same
  head. Both descriptions had valid source comments; trusted run
  [38520992880](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38520992880)
  nevertheless refused the shared dev head. The validation-only peer was
  explicitly closed, not merged.
- Trusted departure run
  [38521961102](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38521961102)
  refreshed the remaining PR and published successful source check
  `115713632138`. Its body and head still matched the exact saved values:
  recovery required neither an unrelated edit nor a dummy commit.
- The exception's silent present/removed behavior and independence remain
  covered by the focused unit contracts; agents did not assign or remove the
  operator-only label for live testing.
- After deployment and live proof, the explicitly authorized update added only
  `source-attribution` to active no-bypass ruleset `24152679`. Exact API readback
  preserved every existing field, and effective dev branch rules include the
  new requirement. No other ruleset was modified.
- Post-merge artifact publication failed separately in the covering promotion
  run; that is not presented as successful installed rollout. Parent fleet
  adoption remains explicitly blocked, so this completed enforcement phase
  does not mark the whole effort Done.
