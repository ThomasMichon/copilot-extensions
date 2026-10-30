# Knowledge Projection Preparation

- **Slug:** `knowledge-projection-preparation`
- **Repo:** copilot-extensions
- **Branch(es):** Independent proposal, implementation, and completion PRs to `dev`
- **Created:** 2026-10-30
- **Status:** Draft
- **Vision:** `harness-guidance`: `simple-local-static-delivery` and `generated-output-is-disposable-source-is-not`
- **Umbrella issue:** #9712
- **Sub-issues:** None initially

## Guiding Intent

Preparing a paired knowledge worktree should establish the local Git-ignore
prerequisite for disposable generated instructions. Operators should not need
to discover a skipped projection and append an exclude rule manually.
Reuse the native publisher's ownership and safety checks rather than creating
a competing ignore writer or changing committed repository policy.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Proposal, implementation, validation, and PR stewardship | Managed source worktree; public updates on #9712 |

## Coordination

- **Topology:** Independent per-phase PRs.
- **Host (owns PRs):** Source driver.
- **Delegates:** None.
- **Handoff:** Journal evidence and any unfinished acceptance here; retain the
  issue until all source-delivery gates are resolved.

## Context

The native publisher in
`plugins/agent-worktrees/src/agent_worktrees/static_instructions.py` already
resolves the repository's real Git exclude file, preserves its contents, adds
the reserved generated namespace, and verifies effective ignore coverage.
`worktree_creation.py` carves the companion and `knowledge_plugins.py` composes
the harness overlay, but companion preparation does not establish that
prerequisite. The compatibility renderer consequently warns
`projection-local-cache-ignore` and skips files in a fresh companion checkout
without checked-in ignore coverage.

The relevant existing contract is
`docs/patterns/worktree-scoped-dynamic-guidance.md`: generated copies are
disposable, Git-ignored output; checked-in projections and reviewed fallbacks
remain independent. This effort closes that existing intent rather than
extending policy. Existing source-resolution and startup-delivery efforts retain
their broader ownership; this is only paired companion ignore preparation.

## Request

The operator clarified:

> I mean, do we need to modify agent-worktrees code that preps the knowledge repo pairing to perform that work?

Then requested:

> Make an effort to drive that

The operator confirmed the slug `knowledge-projection-preparation`.

The referenced work is automatic local Git-ignore setup for generated
instructions during paired knowledge-worktree preparation. No plugin retirement,
message sending, runtime deployment, global registration cleanup, or new
standalone knowledge session is included.

## Plan

### Phase 1 - Review the bounded proposal

- [ ] Confirm existing issue/effort ownership and trace the native publisher,
  companion creation, composition, and supported repair/resume entry points.
- [ ] Land this proposal through the repository's required review flow before
  implementing the change.

### Phase 2 - Prepare the companion through the existing owner

- [ ] Reuse or narrowly extract the publisher-owned local-ignore operation and
  call it at the validated paired knowledge-worktree preparation boundary.
- [ ] Preserve existing exclude contents and effective ignore rules, linked
  worktree/common-Git-directory semantics, tracked output protection, and
  symlink/reparse and bounded-file safeguards.
- [ ] Keep failures visible and preparation retryable without changing
  repository-tracked files, plugin activation, trust, or pair/session custody.
- [ ] _(agent-recommended)_ Document the prerequisite at the existing owner and
  include the mandatory plugin changefile; avoid a second policy/config surface.

### Phase 3 - Qualify and close source delivery

- [ ] Complete the Validation Plan and land the reviewed implementation with
  required CI green.
- [ ] Record release status accurately without installing or activating a
  runtime; any operational rollout requires separate authorization.
- [ ] Resolve all plan items, archive this effort as Done, close the coordination
  issue, and finalize its controlled source worktree.

## Validation Plan

The following specific test coverage is _(agent-recommended)_ to demonstrate
the requested automatic preparation rather than merely testing a helper.

- [ ] Regression: a fresh paired knowledge worktree without committed ignore
  coverage gets the reserved local exclude through its real preparation path,
  then produces its declared projection instead of skipping it.
- [ ] Repeat preparation without duplicate rules, lost existing exclusions, or
  tracked working-tree changes; exercise an actual linked/bare-backed worktree.
- [ ] Cover existing ignore coverage, invalid/unwritable or indirect exclude
  paths, tracked generated destinations, and a visible retryable preparation
  failure without weakening native safety checks.
- [ ] Run focused owner/lifecycle tests, Python lint, install-contract checks,
  and required repository CI for the final candidate.
- [ ] Run a disposable first-touch consumer check using real Git and publication;
  record platform limits and the reason for any unavailable external lane.
- [ ] Review final docs and public artifacts for accurate ownership, generic
  examples, and absence of private identifiers or operational rollout claims.

## Proposal

Prefer the smallest integration at the existing validated paired preparation
boundary. The native publisher remains the authority for local generated-output
handling. Do not solve this by invoking the checked-in projection sync,
duplicating a renderer, or unconditionally publishing the companion's plugins
into the harness.

## Journal

### 2026-10-30 - Kickoff

- Confirmed target effort adoption and the operator-selected slug.
- Searched open issues and PRs; no matching companion-ignore repair was found.
- Opened #9712 and reserved this bounded work.
- Source inspection found that the native publisher already owns exclude
  preparation; the companion lifecycle lacks the prerequisite.
- Proposal only. Implementation awaits review and merge.
