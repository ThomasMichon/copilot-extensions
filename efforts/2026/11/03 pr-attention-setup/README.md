# PR Attention Setup

- **Slug:** `pr-attention-setup`
- **Repo:** copilot-extensions
- **Branch(es):** isolated source branch; managed PR publication
- **Created:** 2026-11-02
- **Status:** Done
- **Vision:** reviewer `declarative-reviewer-adoption`, `one-review-one-lineage`,
  `revision-driven-resume`, `cooperative-resolution`; PR
  `author-and-reviewer-service-consumers`, `honest-observation-and-partial-effects`
- **Umbrella issue:** #10205
- **Sub-issues:** None; #1734/#7051/#6007/#9535 retain their separate runtime scope.

## Guiding Intent

Make an organization-neutral sustained PR-attention setup understandable and
reviewable without requiring a copied reviewer engine or another service.
Compose existing task, PR, transport and resource owners; disclose missing
operational capabilities rather than turning examples into authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source author | Source, fixes, PR/worktree custody and owned finalization | This issue and isolated source branch |
| Coordinator | Design, source verification, watch and authorized normal merge stewardship | Existing author-coordinator communication |

## Coordination

- **Topology:** One isolated source contribution; no nested workers.
- **Host (owns PRs):** Source author.
- **Delegates:** None.
- **Handoff:** Coordinator verifies the prepared diff before author publication;
  actual ownership transfer requires acceptance and preserved obligations.
- **PR stewardship:** Coordinator observes the canonical watch and may use the
  existing authorized normal merge handler after actual review/check gates.
  The source author retains source/PR/worktree custody and this context for fixes;
  observation and merge stewardship do not transfer claims or fabricate consent.
- Existing reviewer-module, provider lifecycle, watch and workspace owners
  retain #1734/#7051/#6007/#9535. No runtime implementation or activation is
  transferred by this effort.
- **Completion boundary:** This records the delivered guidance-only source
  and proposes its archive. The existing #10205 post-effort source settlement
  objective retains archive-PR review/merge, issue closure, effort-binding
  release and actual owned-worktree finalization. This archive is not landed
  merely because its proposed content says Done.

## Context

The reviewer recipe already provides retained target review and author-owned
landing semantics. The PR plugin's source workspace interfaces distinguish
unbound support from adopted protected effects. The setup skill closes the
guidance portion of their visions, not their outstanding operational acceptance.

Relevant public intent: `visions/plugins/agent-dispatch/reviewer/README.md` and
`visions/plugins/agent-pull-requests/README.md`. Patterns require a-la-carte
composition, exact owning commands, honest uncertainty and right-sized payload
surfaces. No standing intent extension or runtime contract change is proposed.

## Request

Excerpt from the delegated source mandate:

> implement an organization-neutral upstream skill in
> ThomasMichon/copilot-extensions that helps an agent set up an equivalent
> sustained PR-attention flow: discover PRs, configurable number of independent
> review lanes, review/comment/follow-up/verdict handling, repo-specific
> automatic/agent/self/human merge policies and minor adaptations. Also
> explicitly include agent-to-agent communication protocols.

Public-safe summary of the approved source scope: one agent-dispatch skill,
three focused references, charter/ledger examples, README discovery link,
bounded documentation-contract tests, generated changefile and this canonical
effort. Preserve existing owners, original evidence, repository gates and
uncertain effects. No child agents, runtime changes, new services/schedulers,
local rollout or production activation. Prepare and validate source before
coordinator verification; publication and normal merge follow that gate.

## Scope Approvals

The operator approved this bounded guidance-only source preparation, including
documentation, synthetic assets and structural tests. Coordinator verification
precedes PR publication. This records the source mandate, not an adopter's
review/merge grant or runtime permission. Repository review/checks and actual
merge/finalize remain completion gates.

## Plan

### Phase 1 - Prepare and validate guidance
- [x] Resolve source, policy, identity, effort capability and public deduplication;
  claim the guidance-only issue without absorbing existing runtime owners.
- [x] Author concrete setup intake, adapter qualification, lane/context handling,
  complete evidence/receipts, peer protocols, merge mapping and truthful limits.
- [x] Add explicitly synthetic non-authoritative charter/ledger examples and
  discoverability; implement bounded documentation-contract negative controls.
- [x] Run targeted tests and skill/reference/documentation/effort guards; add
  the generated agent-dispatch changefile with no manual version edits.
- [x] Submit the actual prepared diff/results to the coordinator for verification.
- [x] Obtain coordinator confirmation of the archive-independent test correction.

### Phase 2 - Reviewed publication and source completion
- [x] Publish only after coordinator verification, through a normal PR to `dev`.
- [x] Address actual blocking findings/checks and verify the provider merge.
- [x] Record the delivered source outcome and prepare the completed-source
  archive proposal, retaining explicit post-effort settlement rather than
  circularly claiming finalization.
- [x] Deferred to `#10205`: Verify actual review/checks/merge of the archive
  proposal, close the guidance issue, release the completed effort binding and
  settle/finalize the owned source worktree through the existing managed gate.

## Validation Plan

- [x] Targeted structural tests cover skill frontmatter/discoverability, every
  link/example, setup gates and negative controls for grant/task/consent/fence/
  uncertain custody; no runtime or live-provider qualification is claimed.
- [x] Skill/reference/docs/effort guards pass; verify public content stays
  neutral, synthetic and free of private identifiers/operational state.
- [x] Coordinator verifies the prepared source, ancillary diff and public safety;
  independently repeat the supervised 25-case packet with verified cleanup.
- [x] Coordinator verifies the final removal of the contribution-effort dependency
  and repeats the targeted packet before authorizing publication.
- [x] Actual initial-source review/checks/merge are independently verified;
  managed source observation releases that terminal PR claim; no rollout is included.
- [x] After archival, repeat the 25-case structural packet and validate changed
  documentation/effort structure to prove no campaign-location dependency remains.
- [x] Deferred to `#10205`: Verify actual archive-PR review/checks/merge, issue
  closure and effort/resource/finalization settlement; no rollout is included.

## Proposal

The guidance-only source landed in #10217. This follow-up proposes its completed
record and archive; actual archive publication/merge and source settlement remain
with #10205. New adopter configuration, adapter implementation and activation
remain outside this effort. Source is merged/pending release; no promotion or
installed availability is asserted.

## Journal

### 2026-11-02 - Source preparation
- Verified scoped public identity, target effort adoption and self-merge profile.
- Created #10205 after targeted public deduplication and audited its live body.
- Prepared the approved guidance-only skill/references/examples; the JSON
  example is deliberately unobserved and grants no effects.
- The supervised source packet passed all 23 structural cases, including
  invalid lane choices, removed refusal controls and fabricated assertions
  in the deliberately unobserved example. Cleanup was verified by the supervisor.
- Skill validity, runbook references, docs consistency, effort structure and
  touched-test Ruff `F,E9` checks passed. The customization scanner reported
  zero blocking findings and zero findings on the new skill; its 474
  suite-wide advisories are not a runtime qualification claim.
- Initial validation caught a test wording mismatch and the README's now
  incomplete skill roster; both were corrected and revalidated. The packet's
  venv lacked Ruff, so the existing installed lint executable was used.
- Added the generated agent-dispatch patch changefile; no version was edited.
- Reproduction commands from the source root:
  `test-supervisor -- python tools/run-plugin-tests.py agent-dispatch --test-file tests/test_pr_attention_skill.py`;
  `ruff check --select F,E9 plugins/agent-dispatch/tests/test_pr_attention_skill.py`;
  `python tools/check-skills.py plugins/agent-dispatch/skills/setting-up-pr-attention-flow/SKILL.md`;
  `python tools/check-runbook-references.py plugins/agent-dispatch/skills/setting-up-pr-attention-flow/SKILL.md plugins/agent-dispatch/skills/setting-up-pr-attention-flow/references/setup-and-adapters.md plugins/agent-dispatch/skills/setting-up-pr-attention-flow/references/coordination-and-recovery.md plugins/agent-dispatch/skills/setting-up-pr-attention-flow/references/communication.md plugins/agent-dispatch/README.md`;
  `python tools/check-docs-consistency.py`;
  `python tools/check-effort-vision-structure.py efforts/active/pr-attention-setup/README.md`.
- Coordinator pre-publication verification, actual PR review/checks/merge and
  issue/effort/resource settlement remain open. No PR or rollout was started.
- Coordinator source review requested surgical clarification of the native
  Task communication route, provider effect IDs versus conversation UUID,
  before/after-call receipt timing and delegated-mandate attribution. Those
  clarifications passed the supervised 25-case structural packet with verified
  cleanup; skill/reference/docs/effort/Ruff guards also passed. Publication
  remains held for coordinator verification.

### 2026-11-03 - Independent source verification
- Coordinator reviewed the amended skill, references, assets and ancillary
  diff, and independently passed all 25 structural cases with verified cleanup.
- That review found a contribution-effort-specific active path in a plugin
  regression test. Removed the incidental effort read/assertions so archiving
  this campaign cannot break plugin tests; retained the native transport,
  conversation UUID and receipt-ordering regressions unchanged.
- Removed the sibling campaign-index assertion as well; plugin discoverability
  remains bound only to its manifest/README and repository-relative link bounds,
  not this contribution's active or archived planning location.
- Targeted revalidation passed all 25 cases with verified cleanup; skill,
  reference, docs, effort and Ruff guards passed. Coordinator confirmation of
  this surgical delta still precedes publication. No second changefile, PR or
  activation is introduced.
- Coordinator accepted final pre-publication verification after independently
  passing all 25 cases in 0.21 seconds with verified cleanup and confirming
  zero campaign references in the plugin test. Core/ancillary source and public
  neutrality were reviewed. These results qualify structural source only.
- Managed initial publication is now authorized. Coordinator stewards the
  existing watch and authorized normal merge backup; source author retains
  source, fixes and PR/worktree custody. Review/merge and effort closure remain
  open until independently verified terminal evidence.

### 2026-11-03 - Delivered source and archive proposal
- #10217 merged to `dev` at `8ac275d5920ac5f7eee93d982c74057d809adba7`
  on 2026-11-03 at 00:54:18 PST (08:54:18 UTC). The approved source head was
  `1bd34a8796845f036a53ea816d2b7f7987f0b783`.
- Coordinator independently confirmed current-head, non-dismissed Copilot
  approval, complete empty review/comment pages and successful required checks.
  Normal actor-authorized merge did not bypass blocking feedback or CI.
- Source author observed the terminal PR through managed `pr-status`, which
  released its own PR claim, then used managed `git sync` to build this
  follow-up on the real merged `dev` source.
- Delivered: the portable skill, three focused references, synthetic
  charter/ledger, README discovery and 25 structural regression cases.
  These qualify authored guidance only, not runtime, clean-room, live-provider,
  release promotion, rollout or installed availability.
- Existing #10205 explicitly retains post-effort source settlement under the
  source author's actual public codename, including this proposal's real
  review/merge and subsequent issue/binding/worktree closure. No new issue,
  ownership transfer or fabricated terminal result is introduced.
- The completed-source record and dated archive are proposed for a normal
  follow-up PR. Actual archive merge, issue closure and finalization have not
  occurred; no unrelated resource, service or installation is changed.
- After moving the effort to its dated archive, the supervised structural packet
  passed all 25 cases in 0.17 seconds with verified cleanup. Documentation
  consistency, effort structure and staged whitespace guards passed. No plugin
  test requires this effort's active or archived location.
