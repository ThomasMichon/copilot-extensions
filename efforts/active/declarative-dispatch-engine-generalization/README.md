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

- [ ] Implement a `ForgeProvider` adapter for Azure DevOps work items
  (list/reserve/claim/release) alongside the existing GitHub implementation.
- [ ] Generalize `validate_config`'s hard-coded `"only 'github' is supported"`
  gate to dispatch on the adapter registry instead of a literal string.
- [ ] Prove one live Azure DevOps-backed declaration end-to-end (discovery,
  batching, reservation, settlement) alongside the existing GitHub declaration
  it must not regress.

### Phase 2 - Declarative worker identity

- [ ] Define the shape of a reusable, named worker identity (a sub-agent
  definition, in the mold of `proxy-code-review:proxy-reviewer`) that a
  declaration selects instead of inlining `worker_guidance` prose.
- [ ] Extract at least one existing declaration's inline prose (the
  `odsp-web-harness-backlog` loop is the live candidate) into such an
  identity, proving the declaration shrinks to policy/eligibility only.
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
