---
visions:
  - visions/harness-guidance
---

# Ambient Guidance Navigability

- **Slug:** `ambient-guidance-navigability`
- **Repo:** copilot-extensions (primary, mechanism + per-plugin content);
  aperture-labs (consumer-side sync fix + AGENTS.md index; companion phase)
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-20
- **Status:** Active
- **Vision:** `visions/harness-guidance` -- vision-closing, not extending.
  Behavior `task-detail-on-demand` and Feature `navigable-on-demand-grounding`
  already state the target; reality currently violates both.
- **Umbrella issue:** [ThomasMichon/copilot-extensions#3033](https://github.com/ThomasMichon/copilot-extensions/issues/3033)
- **Sub-issues:** filed per-phase below as each phase starts.

## Guiding Intent

Skills are pull-only: they load only when their trigger phrases match what an
agent already decided to *do*. An agent that does not know a concept exists
(a claim ledger, a resource obligation, a blocked dispatch task, a stuck
handoff cutover) has no phrase to match on -- so the procedure, even when it
already exists as a skill or reference doc, is functionally undiscoverable.
`AGENTS.md` and the ambient `.github/instructions/*.instructions.md` files are
supposed to be the pre-skill map an agent walks *before* deciding what to do;
they are not fulfilling that role for operational failure-triage content
today. This effort closes that delta: every operationally important "what do
I do when X happens" category should be reachable by walking the ambient map
alone, down to naming the skill/doc/command that holds the actual procedure --
never requiring the agent to already know the skill exists.

## Request

> "So, I am starting to notice other agents losing fidelity on understanding
> worktree management, claims, session management, and more. We need to be
> careful about what gets gated behind a skill. Skills only trigger when the
> agent decides it wants to *do* a particular thing; they can't be the
> holders of 'oh, you might need to know this' type information. Instead, we
> need to rely on our `instructions.md` files to provide a quick mapping of
> all relevant concepts, with links to 'where to find that information'. The
> sum of AGENTS.md and the upfront `*.instructions.md` files should provide
> all the starting points for a 'tree walk' [...] Agents *should* be able to
> walk to the answer *without* invoking skills. When they get to the action
> they need to take, *then* they should consider invoking the skill to be
> told the exact procedure. [...] We need to reconcile the
> `reviewing-customizations` system in copilot-extensions with the ability
> to enforce this flow."
>
> (Operator instruction, relayed via handoff; paraphrased only to drop
> verbatim question examples already captured in the audit table above.)

## Context

### The audit (evidence, not assumption)

A frozen-snapshot navigability audit -- 3 independent, nearly-tool-free
`explore` sub-agents, each given only a captured system-prompt snapshot
(aperture-labs AGENTS.md + all currently-synced copilot-extensions static
`instructions.md` content) and explicitly forbidden from invoking skills or
touching the live repo -- tested 12 realistic "what do I do" questions:

| # | Question | Verdict |
|---|---|---|
| Source of `launch-command.ps1` (actually copilot-extensions-owned) | **False positive** -- pointed at aperture-labs' own `docs/tools.md`, which only indexes that repo's scripts |
| PR got COMMENTED verdict, no actionable feedback | PARTIAL |
| Handoff requested, `context-handoff` tools unavailable | PARTIAL |
| `agent-worktrees` not on PATH | PARTIAL |
| `repos gh` GraphQL error | PARTIAL |
| Detect concurrent/head session | PARTIAL |
| Intelligence Dampener queue stuck | NAVIGABLE (-> `unjam-intelligence-dampener`) |
| Which account for a `gh`-family command | NAVIGABLE (-> `repos gh` wrapper) |
| `agent-dispatch` source location | NAVIGABLE (only because of `cross-repo-debug-tracking`, #3010, landed same session) |
| Worktree "unsettled resource obligation" blocks finalize | **DEAD END** |
| Outbound claim ledger / release a claim | **DEAD END** |
| Dispatch task live-but-structurally-blocked | **DEAD END** |

4/12 navigable (one only because of a fix landed in this same session), 5/12
partial, 3/12 dead end, 1 **false positive** -- confidently wrong is worse than
a dead end, since it produces misdirected work instead of a "look further"
signal.

### Root cause is partly mechanical, not just missing content

`plugins/agent-worktrees/instructions/head-claim-fallback.instructions.md`
is a genuinely good exemplar of the target pattern already: it force-syncs
"if you don't seem to be this worktree's head session" / "if a handoff/cutover
trigger appears to have failed" as ambient, always-loaded guidance, naming the
exact diagnostic commands (`bind-session`, `handoffs-check`). **It is declared
in `agent-worktrees/instruction-projections.json` but was never synced into
aperture-labs** -- absent from `.github/instructions/agent-worktrees/` and
from `.github/copilot/context-projections.json`, whose locked plugin versions
are stale across the board (e.g. `copilot-extensions-harness` recorded at
`0.1.0-dev38`, several versions behind the `dev44` landed in #3010 this same
session). Authoring good ambient content is necessary but not sufficient if
consuming repos never resync it -- this is the same class of drift
`sessionstart-static-dynamic-conformance` catches for hook-emitted content,
but nothing currently audits *projection sync staleness* across consumer
repos.

### Related, non-duplicate prior art (checked before carving)

- **`agents-md-vs-instructions-split`** (Done) -- audited whether content was
  in the *right file* (audience: universal visitor vs. harness-self-config).
  Orthogonal axis: this effort audits whether content is *discoverable and
  complete*, not whether it's correctly placed. No overlap in findings; that
  effort found "no misplacement" in every repo it audited, which is
  consistent with this effort's findings (the problem isn't wrong-file
  placement, it's missing/unsynced/pull-only content).
- **`sessionstart-static-dynamic-conformance`** (Active) -- audits whether
  `sessionStart` hook output is genuinely dynamic (vs. static content that
  should have been a checked-in file instead). Orthogonal axis: static vs.
  dynamic *classification*, not coverage or sync-freshness. Its own findings
  (e.g. `worktree-conduct.md`/`account-conduct.md` being 100% static content
  delivered dynamically) are a related but distinct defect this effort does
  not re-litigate.
- `docs/patterns/agents-md-vs-instructions-split.md` and
  `docs/patterns/session-scoped-dynamic-guidance.md` already describe the
  delivery mechanism this effort reuses (no new mechanism needed for
  delivery); neither currently states a *completeness* or *sync-freshness*
  obligation, which is the gap Phase 1/2 below close.

## Plan

### Phase 0 -- Fix the immediate sync-drift (mechanical, low-risk)
- [ ] Resync aperture-labs' `.github/instructions/` and
      `.github/copilot/context-projections.json` against every plugin's
      currently-declared `instruction-projections.json` (picks up
      `head-claim-fallback` and the `cross-repo-debug-tracking` fix from
      #3010 immediately; reconciles stale plugin-version metadata repo-wide).
- [ ] Spot-check at least one other adopting control-plane repo for the same
      drift class (time-boxed; file a follow-up issue rather than a full
      audit if more are found).

### Phase 1 -- Registry + guard mechanism (`customizing-copilot:reviewing-customizations`)
- [ ] Design a small per-plugin `troubleshooting-index.json` (or an extension
      of `instruction-projections.json`) declaring the failure-mode
      categories that plugin owns (e.g. `agent-worktrees`:
      `claims-ledger`, `resource-obligations`, `head-session` [already
      covered by `head-claim-fallback`]; `agent-dispatch`:
      `blocked-task-recovery`; `agent-bridge`: `path-not-found`,
      `service-not-responding` [already partly in `diagnosing-...`]).
- [ ] Add a guard test (parallel to
      `test_dynamic_pointer_projections_have_exact_session_writers`) that
      scans each plugin's static projections and asserts every declared
      category has an ambient pointer row -- fails closed if a category is
      claimed but not indexed.
- [ ] Add a **sync-freshness** guard (new): compare a consumer repo's locked
      `context-projections.json` plugin versions against the currently
      installed plugin versions and flag drift beyond a reasonable
      threshold, closing the Phase 0 root cause mechanically going forward.
- [ ] Extend `docs/patterns/agents-md-vs-instructions-split.md`'s audit
      heuristic with a third question: "Is this a known failure symptom an
      agent can't phrase-match its way into? If yes, it needs an ambient
      index row, not just a skill trigger."

### Phase 2 -- Populate the concrete content gaps found by the audit
- [ ] `agent-worktrees`: claims-ledger index row (-> `claims` command /
      `tracing-claimant-graphs` skill), resource-obligations index row (->
      `worktree/references/obligations.md` / `finalize` failure meaning).
- [ ] `agent-dispatch`: blocked-task-recovery index row (live-but-
      structurally-blocked task -> inspect/suspend/release/escalate path).
- [ ] `agent-worktrees` or a shared location: `repos gh` GraphQL-error
      triage row; command-not-found/PATH triage row.
- [ ] `copilot-extensions-harness` or the reviewing agent's own guidance:
      COMMENTED-verdict-with-no-actionable-feedback handling row.
- [ ] `context-handoff`: tools-unavailable fallback row (what a session does
      when the skill/tools it's told to invoke aren't present this session
      -- the exact gap this session hit and worked around ad hoc).
- [ ] Ownership-boundary disambiguation: a rule (likely in the root
      `AGENTS.md` template guidance, or `working-cross-repo`'s own ambient
      half) that says *check which repo actually owns this path/script
      before trusting a local tool index* -- closing the `launch-command.ps1`
      false-positive class.

### Phase 3 -- Terse AGENTS.md category index (aperture-labs)
- [ ] Add a compact "Troubleshooting & Where To Look" section to
      aperture-labs' `AGENTS.md`: one line per category, pointing at either
      a direct command or the owning plugin's ambient index / skill --
      sized to respect this repo's own `agent-context-ingestion-cost`
      budget conventions (report-only category-aware context-budget rule
      pack).
- [ ] Document the pattern (in `docs/patterns/` here or in aperture-labs'
      own `docs/`) so another consumer repo can replicate the same terse
      index without re-deriving the model.

### Phase 4 -- Validate
- [ ] Re-run the same 12-question navigability audit (fresh frozen snapshot,
      same nearly-tool-free method) against the fixed state; record the
      before/after verdict table in the Journal.
- [ ] Confirm the `launch-command.ps1` question specifically now produces a
      correct "this belongs to copilot-extensions, resolve via `related
      resolve`" answer rather than a false positive.

## Validation Plan

- [ ] Phase 0's resync is verified by diffing aperture-labs'
      `context-projections.json` before/after and confirming
      `head-claim-fallback` + the `cross-repo-debug-tracking` projection are
      present with current plugin versions.
- [ ] Phase 1's guard test fails on a synthetic plugin with a declared-but-
      unindexed category, and passes once indexed (a real negative-proof
      test, not just a passing positive one).
- [ ] Phase 4's re-audit shows a materially higher navigable/false-positive
      ratio than the baseline table above, with the specific false-positive
      corrected.
- [ ] `tools/run-plugin-tests.py customizing-copilot` and any touched
      plugin's own suite pass; `check-version-bump` / `check-version-
      consistency` / `check-docs-consistency` clean on every PR.

## Proposal

_Pending._

## Journal

### 2026-09-20 -- Kickoff
- Carved from an operator observation (other agents losing fidelity on
  worktree/claim/session-management concepts) mid-session, immediately
  after landing `ThomasMichon/copilot-extensions#3010`
  (`cross-repo-debug-tracking`), which turned out to be a live worked
  example of exactly this effort's target pattern.
- Ran the frozen-snapshot navigability audit (3 background `explore` agents,
  12 questions, nearly-tool-free) -- see Context above for the full table.
  Operator caught a methodology-relevant correction mid-review: the
  `launch-command.ps1` question was marked NAVIGABLE by an audit agent that
  didn't check ownership first (the file is copilot-extensions-owned, not
  aperture-labs'); reclassified as a false positive, a new and arguably more
  important failure category than a plain dead end.
- Checked prior art before carving: `agents-md-vs-instructions-split` (Done)
  and `sessionstart-static-dynamic-conformance` (Active) are both real but
  orthogonal -- placement-correctness and static/dynamic-classification,
  not coverage/sync-freshness. No duplication found.
- Found the `head-claim-fallback.instructions.md` exemplar already exists
  and already proves the content pattern works -- the immediate blocker is
  sync-drift (Phase 0), not invention of a new delivery mechanism.
- Filed umbrella issue `ThomasMichon/copilot-extensions#3033`.
- Not yet started: Phase 0.
