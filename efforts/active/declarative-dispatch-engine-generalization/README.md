# Declarative Backlog & Review Engine Generalization

- **Slug:** `declarative-dispatch-engine-generalization`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-05
- **Status:** Draft
- **Vision:**
  [`visions/plugins/agent-dispatch/repository-issue-loop/`](../../../visions/plugins/agent-dispatch/repository-issue-loop/README.md)
  (declarative-turnkey-adoption, provider-neutral-backlog-capability,
  declarative-worker-identity) and
  [`visions/plugins/agent-dispatch/README.md`](../../../visions/plugins/agent-dispatch/README.md)
  (concise-event-then-charter-pull, preloaded-dispatch-supplement) and
  [`visions/plugins/agent-dispatch/reviewer/`](../../../visions/plugins/agent-dispatch/reviewer/README.md)
  (the open structural-guard question from its Phase 6 in
  `review-automation-reliability`)

## Guiding Intent

Make the declarative recipe engine (reviewer loops and repository-issue-loops
alike) as easy for a colleague on an unfamiliar team to adopt as it is for its
original author, provider-neutral rather than GitHub-only, driven by named
reusable worker identities instead of inlined prompt prose, and cheap to
embody per event instead of paying full instructional cost on every task.

## Context

Live use of the `odsp-web-harness-backlog` repository-issue-loop (validating
the #2056 terminal-reservation fix, see `review-automation-reliability`) and a
same-day conversation about extending the engine surfaced four related,
forward-looking gaps against the newly-extracted repository-issue-loop vision
and the parent agent-dispatch vision:

1. The `ForgeProvider` seam in `repository_issue_loops.py` is already
   provider-agnostic, but `validate_config` hard-gates
   `forge.provider != "github"` -- there is no second adapter, so pointing the
   engine at an Azure DevOps backlog is not yet possible.
2. Adopting a new loop today means authoring a private declaration whose
   `worker_guidance` is a long, hand-written prose blob (see
   `odsp-web-harness-issue-loop.json`, dotfiles) -- there is no library of
   reusable, named worker identities a new adopter can just select.
3. `embody.autopilot_worker_prompt` inlines a large, generic "how to behave as
   an agent-dispatch worker" instructional essay into **every** embodied
   worker's seed, regardless of the actual event that triggered embodiment
   (new work vs. a submitter update vs. a steer answer) or whether the worker
   already knows this material from a prior turn.
4. The reviewer vision's Phase-6 open question (below-altitude prose was
   already explicit and was still violated once) points at the same root
   cause: policy expressed only as prose in a per-repository declaration is
   weaker than policy expressed structurally in a reusable, named identity.

## Plan

### Phase 1 - Azure DevOps backlog provider

- [x] Implement a `ForgeProvider` adapter for Azure DevOps work items
  (list/reserve/claim/release) alongside the existing GitHub implementation.
  Landed `AzureDevOpsProvider` (via the `az` CLI's `devops`/`boards`
  subcommands): WIQL discovery + `az boards work-item show` for fields,
  `System.Tags` as the label equivalent, and reservation markers carried as
  work-item comments through the generic `az devops invoke` REST bridge --
  reusing the existing `_marker`/`_parse_marker`/`_latest_reservations`
  helpers unmodified across both providers.
- [x] Generalize `validate_config`'s hard-coded `"only 'github' is supported"`
  gate to dispatch on the adapter registry instead of a literal string.
  `_SUPPORTED_FORGE_PROVIDERS = {"github", "azure-devops"}`; a new
  `_forge_provider_for(config)` factory selects the adapter class, replacing
  `run_tick`'s hard-coded `GitHubProvider(...)` default.
- [ ] Prove one live Azure DevOps-backed declaration end-to-end (discovery,
  batching, reservation, settlement) alongside the existing GitHub declaration
  it must not regress. Still open -- no live Azure DevOps organization/project
  has exercised this adapter yet; today it is validated only by mocked-`az`
  unit tests (17 new tests: identity mismatch, tag-as-label round-trip,
  reservation-marker parity with GitHub, provider selection).

### Phase 2 - Declarative worker identity

- [x] Define the shape of a reusable, named worker identity (a sub-agent
  definition, in the mold of `proxy-code-review:proxy-reviewer`) that a
  declaration selects instead of inlining `worker_guidance` prose.
  Landed as `agent_dispatch.worker_identities.load_worker_identity`: an
  identity is a `<name>.identity.md` file in the same frontmatter (`name`,
  `description`) plus markdown-body (`rules`) shape as an in-session
  `*.agent.md` sub-agent, resolved repo-local-first then from the plugin's
  packaged `plugins/agent-dispatch/identities/`. A declaration's new
  `worker_identity` field is mutually exclusive with inline
  `worker_guidance`; `validate_config` resolves it at validation time.
- [x] Extract at least one existing declaration's inline prose (the
  `odsp-web-harness-backlog` loop is the live candidate) into such an
  identity, proving the declaration shrinks to policy/eligibility only.
  Extracted verbatim into the packaged built-in
  `plugins/agent-dispatch/identities/odsp-web-harness-backlog.identity.md`.
  **Not yet applied to the live `dotfiles` declaration** -- that requires the
  running agent-dispatch daemon to have this PR's code deployed first (an
  older daemon would reject `worker_identity` as an unknown key and break the
  live loop); see Gotchas.
- [ ] Assess whether a named identity's structural boundaries (permitted
  tools/mutations) can enforce the reviewer vision's never-supersede rule
  more robustly than prose alone -- closing the Phase-6 open question in
  `review-automation-reliability`.

### Phase 3 - Concise event-then-charter-pull prompts

- [ ] Classify the event shapes a recipe already knows about embodiment time
  (new work assigned, submitter update, steer answer, resumed-after-handoff)
  and design the short, per-event seed text for each.
- [ ] Add the "full charter" command/route a seed points at, replacing the
  inlined instructional essay in `embody.autopilot_worker_prompt` /
  `bridge.worker_prompt` with an on-demand fetch.
- [ ] Measure the token-cost delta per embodiment before/after, and confirm
  no loss of behavioral fidelity (claim/evaluate/complete mechanics, decline
  conventions) versus today's inlined prompt.

### Phase 4 - Preloaded dispatch supplement on the worker identity

- [ ] Attach the shared "how to behave as a dispatch worker" instruction
  supplement to the worker identity from Phase 2, by reference, so it loads
  once at identity-selection time rather than being rediscovered (or
  re-inlined) per task.
- [ ] Confirm a worker embodied under a named identity never spends a tool
  call or prompt tokens re-deriving this supplement from scratch.

### Phase 5 - Turnkey colleague adoption

- [ ] Write the adoption path for a colleague unfamiliar with the runtime:
  declaration schema reference, the library of available worker identities,
  and a worked example end-to-end (a new repository, its declaration, its
  selected identity).
- [ ] Identify and remove any remaining step in that path that requires
  reading engine source rather than the declaration schema and an identity's
  own documentation.

## Validation Plan

- [ ] A new Azure DevOps-backed declaration reaches the same discovery →
  batch → settle outcomes as the existing GitHub-backed one, provider
  differences fully behind the adapter.
- [ ] A declaration authored against a named worker identity contains no
  inlined behavioral policy prose, only eligibility/cadence/identity
  selection.
- [ ] Per-embodiment seed size and token cost drop materially for a
  known-event embodiment versus today's always-inlined prompt, with no
  behavioral regression in claim/evaluate/complete/decline flows.
- [ ] A colleague can stand up a new loop from the declaration schema and an
  existing worker identity alone, without reading engine source.

## Proposal

Sequence Phase 1 (ADO) and Phase 2 (worker identity) independently since
neither blocks the other; land Phase 3/4 (prompt shape) together since the
charter-pull command and the preloaded supplement are two halves of the same
seed redesign; do Phase 5 last so the adoption doc reflects the shape the
other phases actually land in.

## Journal

### 2026-09-05 - Kickoff

- Captured four forward-looking gaps discussed live while reconciling the
  #2056 backlog-loop fix against the newly-extracted repository-issue-loop
  vision: ADO provider support, declarative (named, sub-agent) worker
  identity, concise event-first prompts with on-demand charter pull, and a
  preloaded shared dispatch-behavior supplement on the identity. No
  implementation started; this effort tracks the plan only.

### 2026-09-06 - Reconciled with aperture-labs; started Phase 2

- Reconciled the "aperture-labs" reviewer-loop concern from the prior
  handoff: the copilot-extensions PR history for the reviewer module
  (`#1445`..`#2134`) is entirely merged under the one operator account, and
  already matches the vision docs this effort builds on. No separate
  unmerged branch or competing identity concept was found; the existing
  `*.agent.md` sub-agent shape (e.g.
  `copilot-extensions-reviewer.agent.md`) is the precedent Phase 2's worker
  identity borrows from.
- Landed Phase 2's identity shape and one extraction: new
  `agent_dispatch.worker_identities` module (`WorkerIdentity`,
  `load_worker_identity`), a new `worker_identity` declaration field on
  `repository-issue-loop` (validated, mutually exclusive with
  `worker_guidance`), and the `odsp-web-harness-backlog` identity extracted
  verbatim from the live dotfiles declaration into
  `plugins/agent-dispatch/identities/odsp-web-harness-backlog.identity.md`.
  10 new tests (`test_worker_identities.py` + 4 cases in
  `test_repository_issue_loops.py`); full existing suite (55 tests) passes
  unchanged.
- Did **not** switch the live `dotfiles` declaration to
  `worker_identity: odsp-web-harness-backlog` yet -- the running
  agent-dispatch daemon must have this PR's code deployed first, or it will
  reject the new field as an unknown key and break the live backlog loop.
  That switch is the very next slice once this PR lands and deploys.
- Phase 2's third bullet (structural enforcement of never-supersede) is
  still open -- the identity file today only carries prose rules, no
  enforced tool/mutation boundary; deferred to a follow-up slice.

### 2026-09-06 (cont.) - Landed Phase 1 (Azure DevOps provider)

- Also fixed a pre-existing, unrelated CI-blocking baseline bug found while
  landing Phase 2: `#2167` bumped `agent-index`'s own version without
  updating its shipped agent-dispatch registrar declaration fixture, failing
  the `agent-dispatch` suite's version-consistency assertion for any PR.
  Landed separately as `#2170` (merged) so it didn't get bundled with
  unrelated Phase 2 content.
- Implemented Phase 1's `AzureDevOpsProvider` (list/reserve/claim/release
  over Azure DevOps work items via the `az` CLI), generalized
  `validate_config`'s forge-provider gate to
  `_SUPPORTED_FORGE_PROVIDERS = {"github", "azure-devops"}`, and added a
  `_forge_provider_for(config)` factory replacing `run_tick`'s hard-coded
  `GitHubProvider(...)`. `repo` keeps the same `owner/name`-shaped format for
  both providers (`organization/project` for Azure DevOps satisfies the same
  regex), so no new top-level declaration field was needed. Reused
  `_marker`/`_parse_marker`/`_latest_reservations` unmodified -- Azure
  DevOps work-item comments carry the identical JSON marker convention as
  GitHub issue comments. 17 new tests (identity mismatch, tag round-trip,
  provider-factory selection, malformed-config message update); full
  existing suite passes unchanged.
- Phase 1's third bullet (a live Azure DevOps org/project proving the full
  discovery -> batch -> reserve -> settle path) is still open -- this
  session had no Azure DevOps organization available to validate against;
  today's coverage is mocked-`az` unit tests only. That live proof is the
  next slice for whoever picks this back up, alongside a first real
  `azure-devops`-backed declaration to adopt it.
