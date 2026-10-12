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

## Plan

- [ ] Clear this proposal's automated review gate before implementing it.
- [ ] Require a structurally valid source marker in the current PR body for
  every PR targeting `dev`, regardless of author, branch, or changed paths.
  Ordinary managed sources use `<!-- agent-worktrees:source
  codename=<assigned-public-handle> -->`, with optional `root` and `enc`.
  Repository automation uses the same comment envelope with a public
  `workflow=<workflow-file-slug>` origin and optional run ID.
- [ ] _(agent-recommended)_ Reuse the existing independently required trusted
  `workflow-lockdown-guard` path rather than adding an initially unenforced
  check or letting PR-head changes disable their own metadata validation.
  Never check out or execute PR-head content in this trusted metadata job.
- [ ] _(agent-recommended)_ Read current PR metadata through the GitHub API and
  reevaluate on opening, pushing, reopening, body/base edits, and label changes.
  A body-only repair must clear the gate without a dummy commit.
- [ ] Silently skip only the attribution requirement when the operator's
  exception is present; protected-path authorization remains independent.
  This is operator policy, not a claim that GitHub can distinguish an agent
  from a human sharing the same account or label permissions.
- [ ] Give missing/malformed-marker failures concise format examples and
  concrete preparation/manual-attachment steps. Do not echo untrusted PR
  prose, private source identifiers, or ciphertext into CI diagnostics.
- [ ] Put mandatory attribution and manual repair guidance near the front of
  CONTRIBUTING.md and in the reusable PR workflow reference. Explain that
  agents repair metadata, not bypass CI.
- [ ] Update every repository-owned automation producer targeting `dev` to
  supply its workflow-origin marker, preserving existing PR body content.
  Promotion/rollback PRs targeting `main` remain outside this dev-only rule.
- [ ] Record deployment honestly: trusted workflow changes become effective
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

- [ ] Missing, malformed, duplicate/conflicting fields, private/raw fields and
  unsafe marker values fail with actionable, public-safe repair guidance.
- [ ] Valid codename and workflow-origin forms pass; optional root/encrypted
  fields follow the existing marker contract in the worktree skill's
  [PR attribution reference](../../../plugins/agent-worktrees/skills/worktree/references/pr-attribution.md),
  backed by `providers/attribution.py` and `codename.is_valid_handle`.
  The implementation must publish the validator's exact accepted grammar in
  the contribution/repair guidance and test it. Encryption never becomes
  mandatory key setup.
- [ ] Current-body retrieval and edited events prove that adding/removing a
  marker reevaluates the same PR head; non-dev PRs remain unaffected.
- [ ] The operator-only exception passes silently, its removal restores normal
  enforcement, and it never disables the separate protected-path guard.
- [ ] Every dev automation PR producer emits a valid workflow-origin marker.
- [ ] Focused validator and workflow-wiring tests run locally. A disposable
  live dev PR proves missing-marker failure and body-only repair once the
  trusted workflow is actually deployed; close any validation-only PR after
  capturing evidence rather than merging it.
- [ ] Implementation passes current-head review/required CI, merges, promotes,
  and receives live required-check evidence before this phase is complete.
