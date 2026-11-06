# Maintainer Vision Adherence Review

- **Slug:** `maintainer-vision-adherence-review`
- **Repo:** copilot-extensions
- **Branch(es):** Independent source PRs per owning repository
- **Created:** 2026-11-06
- **Status:** Active
- **Vision:** [Harness guidance: authoritative ownership](../../../visions/harness-guidance/README.md#authoritative-ownership)
- **Umbrella issue:** #10864

## Guiding Intent

Make conceptual acceptance criteria public, stable and understandable before
contributors write code. Apply the existing vision/pattern reconciliation
model rather than inventing a second architectural authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source contributor | Repository lens, discovery and validation | #10864 |
| Generic review-engine owner | Optional trusted subcharter composition | Owning repository's separate PR |

## Coordination

- **Topology:** Independent per-owner source PRs.
- **Host (owns PRs):** Source contributor.
- **Delegates:** None required.
- **Handoff:** Publish the canonical public reference before adopting it into
  a downstream review assignment. Generic engine integration remains separately
  reviewed; this repository neither owns nor installs that engine.

## Context

`AGENTS.md`, `CONTRIBUTING.md` and design principle 0 already establish
vision/pattern reconciliation. The harness plugin owns portable guidance for
working on this repository. #2847 retains customization-conformance ownership;
the existing repository-dispatch implementation reviewer stays unchanged.

## Request

Publish a Maintainer vision-adherence lens and paired skill. Encourage careful,
proportionate planning, welcome architecture-neutral fixes, and make future
portable review steering visible at its proper public owner.

## Scope Approvals

Source guidance and normal review-gated publication only. No recurring worker,
service, runtime installation, deployment, merge-policy change or private
operating-state publication is included.

## Plan

### Phase 1 - Publish the repository lens
- [x] Reconcile the change to existing guidance ownership and design principle 0.
- [x] Draft the public charter, paired skill and discovery links.
- [ ] Validate and merge the source PR under normal repository gates.

### Phase 2 - Compose and adopt
- [ ] Validate separately owned generic-reviewer composition.
- [ ] Reference the accepted public lens from an adopter without copying private
  instructions into this repository or treating source merge as runtime rollout.

## Validation Plan

- [x] Validate skill frontmatter, portable packaging and local Markdown links.
- [x] Run applicable existing guidance/source checks.
- [x] Check no-lens, trusted-lens, missing-lens and proposed-rule-change guidance
  contracts in the generic engine's focused static tests; model behavior is not
  qualified by those source assertions.
- [ ] Record actual review/CI/merge outcomes; no runtime qualification is claimed.

## Proposal

The harness reference is the repository-specific conceptual lens. The paired
skill applies it within an existing authorized review workflow. It adds no
generic review engine, alternate conformance gate or ambient hook.

## Journal

### 2026-11-06 - Public guidance slice
- Created #10864 after checking the distinct #2847 scope and narrow duplicates.
- Drafted the lens against existing accepted vision, pattern and contribution
  sources; publication and downstream adoption remain pending.
- Skill, effort structure, documentation consistency, reference resolution and
  scoped customization scans passed. The separately owned generic source passed
  fifteen scope/authorization tests and its guidance/packaging checks.
- This is declarative review guidance, not an execution-path change. No runtime,
  clean-room, external venue or model-behavior qualification is claimed.
