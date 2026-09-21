---
visions:
  - visions/harness-guidance
---

# Ambient Guidance Navigability

- **Slug:** `ambient-guidance-navigability`
- **Repo:** copilot-extensions (primary, mechanism + per-plugin content);
  a private downstream consumer repository has a companion effort for its
  own AGENTS.md index and one-time sync-drift fix
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-20
- **Status:** Active
- **Vision:** `visions/harness-guidance` -- vision-closing, not extending.
  Behavior `task-detail-on-demand` and Feature `navigable-on-demand-grounding`
  already state the target; reality currently violates both.
- **Umbrella issue:** [ThomasMichon/copilot-extensions#3033](https://github.com/ThomasMichon/copilot-extensions/issues/3033)
- **Sub-issues:** [Phase 1 -- #3071](https://github.com/ThomasMichon/copilot-extensions/issues/3071).

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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent (this repo) | Designs and lands the registry/guard mechanism (Phase 1), the `projection-reflect` generic recipe (Phase 2), and per-plugin content (Phase 3) here | independent per-phase worktree, this repo's own PR flow |
| Driving agent (downstream consumer) | Runs the one-time sync-drift fix, adds a repo-owned terse AGENTS.md category index, and instantiates `projection-reflect` (Phase 0 / Phase 4 / Phase 5) in its own private repo | that repo's own worktree/PR flow; not part of this repo's history |

## Coordination

- **Topology:** independent per-repo phases, not a shared branch. Each phase
  is its own worktree and its own PR, following that repo's own merge policy.
- **Host (owns PRs):** the driving agent in each repo, for that repo's own
  phases.
- **Delegates:** none beyond the split above; the downstream repo's Phase 0/4/5
  work is out of scope for this repo's own history and is tracked in that
  repo's own private effort/issue instead.
- **Handoff:** each phase closes with its own repo's validation green and its
  PR merged (or explicitly deferred) before the next phase starts; a fresh
  session may pick up at any phase boundary from this doc's Journal.

## Context

### The audit (evidence, not assumption)

A frozen-snapshot navigability audit -- 3 independent, nearly-tool-free
`explore` sub-agents, each given only a captured system-prompt snapshot (a
downstream consumer repo's root `AGENTS.md` plus every currently-synced
copilot-extensions static `instructions.md` file) and explicitly forbidden
from invoking skills or touching the live repo -- tested 12 realistic "what
do I do" questions:

| # | Question | Verdict |
|---|---|---|
| Source of a plugin-owned launch script mistaken for a local one | **False positive** -- pointed at the consumer repo's own local tool index, which only covers that repo's own scripts |
| PR got a "commented, no action needed" reviewer verdict with no actionable feedback | PARTIAL |
| Handoff requested, but the handoff skill/tools are unavailable this session | PARTIAL |
| A plugin's CLI is not on PATH | PARTIAL |
| A `gh`-family command failed with a GraphQL error | PARTIAL |
| Detect a concurrent/head session in the same worktree | PARTIAL |
| A stuck review-queue symptom, where to look | NAVIGABLE (an existing skill directly names it) |
| Which account to use for a given repo before a `gh`-family command | NAVIGABLE (an existing wrapper command directly names it) |
| A plugin's writable source-checkout location, when only its runtime is installed | NAVIGABLE (only because of `cross-repo-debug-tracking`, #3010, landed the same day) |
| A worktree's "unsettled resource obligation" blocking finalize | **DEAD END** |
| Outbound claim ledger / release a claim | **DEAD END** |
| A dispatched task that's live but structurally blocked | **DEAD END** |

3/12 navigable (one only because of a fix landed the same day this audit
ran), 5/12 partial, 3/12 dead end, 1 **false positive** -- confidently wrong
is worse than a dead end, since it produces misdirected work instead of a
"look further" signal.

### Root cause is partly mechanical, not just missing content

`plugins/agent-worktrees/instructions/head-claim-fallback.instructions.md`
is a genuinely good exemplar of the target pattern already: it force-syncs
"if you don't seem to be this worktree's head session" / "if a handoff/cutover
trigger appears to have failed" as ambient, always-loaded guidance, naming the
exact diagnostic commands (`bind-session`, `handoffs-check`). **It is declared
in `agent-worktrees/instruction-projections.json` but the audited consumer
repo had never synced it in** -- absent from that repo's own
`.github/instructions/agent-worktrees/` and from its locked
`.github/copilot/context-projections.json`, whose recorded plugin versions
were stale across the board (multiple plugins several dev-versions behind
what was already installed). Authoring good ambient content is necessary but
not sufficient if a consuming repo never resyncs it -- this is the same class
of drift `sessionstart-static-dynamic-conformance` catches for hook-emitted
content, but nothing currently audits *projection sync staleness* across
consumer repos.

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

## Request

> An operator observed other agents losing fidelity on understanding
> worktree management, claims, and session management, and asked that
> ambient guidance stop being gated behind skill invocation: skills only
> trigger when an agent already decided what it wants to *do*, so they
> cannot hold "you might need to know this exists" information. `AGENTS.md`
> plus the upfront `*.instructions.md` files should together give every
> starting point for a "tree walk" toward more specific information, so an
> agent can navigate to a known-category answer without invoking any skill,
> and only reach for a skill once it knows the exact action to take. The
> operator asked that this be reconciled with `reviewing-customizations`'
> ability to enforce that flow going forward.

## Plan

### Phase 0 (downstream, not tracked in this repo's history) -- Fix the immediate sync-drift
Runs entirely in the private downstream consumer repo's own worktree/PR flow:
resync its `.github/instructions/` and `.github/copilot/context-projections.json`
against its currently-*enabled* plugins' declared `instruction-projections.json`
-- the sync manager's contract is settings-scoped (discovers enabled
payloads, not every installed/marketplace plugin), so this deliberately does
not check in pointers for disabled capabilities (picks up `head-claim-fallback`
and the `cross-repo-debug-tracking` fix immediately; reconciles stale
plugin-version metadata for every enabled plugin). Recorded here only as
context for Phase 2's `projection-reflect` design; not a checklist item of
this
repo's own effort.

### Phase 1 -- Registry + coverage guard (`customizing-copilot:reviewing-customizations`)
- [x] Design a small per-plugin `troubleshooting-index.json` (or an extension
      of `instruction-projections.json`) declaring the failure-mode
      categories that plugin owns (e.g. `agent-worktrees`:
      `claims-ledger`, `resource-obligations`, `head-session` [already
      covered by `head-claim-fallback`]; `agent-dispatch`:
      `blocked-task-recovery`; `agent-bridge`: `path-not-found`,
      `service-not-responding` [already partly in `diagnosing-...`]).
- [x] Add a guard test (parallel to
      `test_dynamic_pointer_projections_have_exact_session_writers`) that
      scans each plugin's static projections and asserts every declared
      category has an ambient pointer row -- fails closed if a category is
      claimed but not indexed.
- [x] Extend `docs/patterns/agents-md-vs-instructions-split.md`'s audit
      heuristic with a third question: "Is this a known failure symptom an
      agent can't phrase-match its way into? If yes, it needs an ambient
      index row, not just a skill trigger."

### Phase 2 -- `projection-reflect`: the generic sync-automation recipe (this repo)
Closes the sync-freshness half of the audit's root cause (superseding a
plain "compare locked vs. installed versions" guard with an actively
self-healing flow), modeled directly on this facility's own proven
`config-reflect` system (reflect/reconcile split, fail-closed producer,
narrow PR-shape bypass, domain-deduped conflict dispatch, non-self-merging
reconciler -- see a downstream private effort's architecture summary for the
exact reusable-primitives mapping; not reproduced here since it cites
private paths).

- [x] **Immediate/proactive trigger** (landed, `#3053`): a force-synced
      ambient rule -- after merging an upstream PR here, immediately
      force-update installed plugins and re-run the projection sync in the
      current harness/consumer repo, before ending the turn. This is the
      cheap, always-on half; the items below are the scheduled backstop for
      when no session happens to be active in the consumer repo when a
      change lands.
- [ ] **Deterministic sync tool**: a script (extends
      `manage-instruction-projections.py` or a sibling) that, given a
      consumer repo, does: refresh installed plugin payloads for every
      enabled plugin, `sync`, `scan --from-settings` for drift. **The
      "did anything change" condition is `changed or lock_updated`, not
      `changed` alone**: `sync` can report an empty `changed` list with a
      lock-only update, and `scan --from-settings` can emit findings with no
      file change at all -- treating either as a no-op silently drops lock
      drift or reports success while hiding a real finding. **Deterministic
      finding classification** (by `scan`'s own check name, not ad hoc
      judgment): `projection-missing` (declared but not yet checked in) and
      `projection-source-update` (checked-in projection differs from current
      source) are plain drift -- `sync` resolves them and the worker
      proceeds to open a PR. `projection-ownership` (untracked or
      conflicting destination ownership -- the hand-edit/conflict case),
      `projection-budget` (aggregate size over budget), and
      `projection-orphan-lock` (a locked destination no plugin still
      declares, which `scan` explicitly never deletes and marks for manual
      review) are never auto-resolved or silently folded into a bypass-
      eligible PR -- each routes to conflict-dispatch below for a human
      decision. **This allowlist is not exhaustive against the manager's
      full result contract** (it can also emit, among others,
      `projection-local-modification`, `projection-marker`,
      `projection-lock`, `projection-source-unavailable`, and
      `projection-orphan-file`) -- the classification must default
      **fail-closed**: any check name not explicitly allowlisted as plain
      drift routes to conflict-dispatch by default, never treated as a
      no-op or silently included in a bypass-eligible PR. Only when there is
      truly nothing to report does the worker skip opening a PR. **Managed-file
      conflict routing**: `sync` already returns blocking findings (not a
      git-merge conflict) when it detects a locally hand-edited managed
      projection or an ownership/lock validation failure, leaving `changed`
      empty -- the worker must treat *that* outcome as a conflict too and
      route it to the conflict-dispatch primitive below (a hand-edited
      managed file is exactly the "flag it, don't silently overwrite" case
      the reconciler exists for), not silently stop or silently skip it.
      **Verification target**: reuse the existing
      `.github/copilot/context-projections.json` lock schema as the
      recompute-verification target (it already carries `template`,
      `pluginVersion`, `templateBytes`/`templateSha256`, and
      `renderedBytes`/`renderedSha256` per destination) -- no new manifest
      format is needed. **Reproducibility requires an immutable pinned
      source, not "whatever's currently installed"**: an installed payload
      on a worker/reviewer machine can itself have moved on since the PR was
      opened, so a version + hash alone does not guarantee the reviewer can
      recompute the *same* bytes later. The PR must carry (or the lock
      schema must gain) the exact immutable upstream reference each changed
      projection was rendered from (a commit SHA or release digest in the
      trusted source's own history, not just a mutable version string), and
      verification must fetch/recompute from *that exact pinned artifact* --
      never from "the reviewer's own current install state" -- requiring a
      byte-exact match against **both** the PR's lock-entry diff **and** the
      actual generated instruction files it declares (hash each managed
      destination file on disk and confirm it matches its own lock entry's
      `renderedSha256`, not just that the lock entries match each other) --
      a producer that left lock hashes untouched while altering a managed
      file's real content must fail this check, and any managed path
      present in the diff but absent from the lock (or vice versa) must
      fail it too. This is strictly stronger verification than
      `config-reflect` can offer (there is no live, unrepeatable device
      state here -- the source is already-reviewed, already-merged upstream
      content pinned to an immutable reference, so the render is 100%
      reproducible from that reference). **Byte-exact match proves
      reproducibility, not trust**: `discover_enabled_sources` resolves
      every repository-enabled marketplace, including third-party
      ones this repo did not author. The bypass must additionally restrict
      itself to an explicit **trusted-source allowlist** (defaulting to this
      repo's own marketplace only; any other marketplace/plugin source is
      review-only until explicitly added to the allowlist) -- an
      untrustworthy source can be perfectly reproducible and still unsafe to
      auto-merge.
- [ ] **Conflict-dispatch primitive**: a reusable helper (candidate home:
      `agent-dispatch`, since dispatch itself is a copilot-extensions
      plugin) generalizing `config-reflect`'s `conflict_dispatch.py` pattern
      -- domain-scoped dedup key, compact descriptor, async `agent-dispatch
      create` call -- parameterized so it isn't config-reflect-specific.
- [ ] **`projection-reconciler` agent template**: modeled on
      `config-reconciler`, but narrower -- the sync manager already refuses
      to overwrite a locally hand-edited managed projection outright (a
      deliberate existing safety property), so the reconciler must not try
      to force that overwrite by looping `sync` or silently discarding the
      local edit either. On a genuine hand-edit conflict it stays
      **report-only**: it records/preserves the local preimage, comments on
      (or files) a tracked finding describing exactly what blocked the sync
      and why, and stops -- it does not attempt an automatic repair of that
      case. It only resolves ordinary git-level conflicts (e.g. concurrent
      updates to the same lock-file region across unrelated plugins) by
      re-deriving the canonical render fresh; it never blends two candidate
      "truths" for a managed file. Never self-merges; updates the same PR
      and returns.
- [ ] **`setting-up-instruction-sync-worker` skill** (or a section within
      `authoring-harness-plugins`): scaffolds, for any harness repo that
      asks for it, the scheduler config template, a bypass-config-profile
      template (label / path-globs / diff-shape rule / recompute-verify
      callback / trusted-source allowlist -- adaptable to whatever review
      gate that repo uses), and the reconciler agent file. Per
      `docs/patterns/install-vs-adopt-boundary.md`: granting a scheduler
      repo-write authority and a review-bypass profile is repo mutation, not
      a machine-local install/update concern -- the skill must require an
      explicit, committed, in-repo opt-in (the repo's own config declaring
      it wants this) as its ownership signal before scaffolding anything,
      never merely "the operator asked for it in this session" or "the repo
      is PR-gated" (a repo you only contribute to is often PR-gated too).
      **Consent must be rechecked live, not only at setup time**: both the
      scheduled worker and the bypass profile re-read that same committed
      opt-in signal on every run/every PR, fail-closed (worker refuses to
      open a PR, bypass refuses to auto-merge) the moment it's missing or
      revoked -- an adopter withdrawing consent must disable the automation
      immediately, not only prevent a future `setup`.
- [ ] Reuse the **same** deterministic producer identity a repo already
      trusts for its own reflect-style automation (do not mint a new
      identity per feature) -- the safety boundary is the conjunction of
      identity + stamp label + path-scope + diff-shape + recompute-match +
      trusted-source allowlist, not identity alone (identity alone was
      already proven insufficient by `config-reflect`'s own hard-won
      lessons). **Onboarding a repo with no existing reflect-style
      identity**: the setup skill must not silently mint one. It either
      declines outright (report-only mode: it can still tell an adopter what
      drift exists, just never open an auto-mergeable PR for it) until a
      trusted deterministic identity has been provisioned and registered
      through that repo's own normal, reviewed account/credential process,
      or it walks the operator through that one-time registration step
      explicitly -- never as an automatic side effect of "set up the
      instruction sync worker."

### Phase 3 -- Populate the concrete content gaps found by the audit
- [ ] `agent-worktrees`: claims-ledger index row (-> `claims` command /
      `tracing-claimant-graphs` skill), resource-obligations index row (->
      `worktree/references/obligations.md` / `finalize` failure meaning).
- [ ] `agent-dispatch`: blocked-task-recovery index row (live-but-
      structurally-blocked task -> inspect/suspend/release/escalate path).
- [ ] `agent-worktrees` or a shared location: `repos gh` GraphQL-error
      triage row; command-not-found/PATH triage row.
- [ ] `copilot-extensions-harness` or the reviewing agent's own guidance:
      commented-verdict-with-no-actionable-feedback handling row.
- [ ] `context-handoff`: tools-unavailable fallback row (what a session does
      when the skill/tools it's told to invoke aren't present this session).
- [ ] Ownership-boundary disambiguation: a rule (likely in the root
      `AGENTS.md` template guidance, or `working-cross-repo`'s own ambient
      half) that says *check which repo actually owns this path/script
      before trusting a local tool index* -- closing the false-positive
      class found by the audit.

### Phase 4 (downstream, not tracked in this repo's history) -- Terse AGENTS.md category index
Runs entirely in the private downstream consumer repo's own worktree/PR flow:
add a compact "Troubleshooting & Where To Look" section to that repo's own
`AGENTS.md` -- one line per category, pointing at either a direct command or
the owning plugin's ambient index / skill, sized to respect that repo's own
context-budget conventions. This repo's part is limited to documenting the
generic pattern (Phase 1's coverage guard, Phase 2's `projection-reflect`
recipe, Phase 3's content-gap fixes, and this doc) so any consumer repo can
replicate it without re-deriving the model.

### Phase 5 (downstream, not tracked in this repo's history) -- Instantiate `projection-reflect`
Runs entirely in the private downstream consumer repo's own worktree/PR flow,
consuming Phase 2's generic recipe once it lands here: generalize that
repo's own review gate's existing reflect-style bypass config from a single
hardcoded profile into a small list of named profiles (so its existing
trusted deterministic identity can serve a second, distinctly-scoped reflect
kind without proliferating identities); add the `projection-reflect` profile
(that repo's own instructions-file paths + the lock file, diff-shape rule,
recompute-verify callback, and an explicit trusted-source allowlist scoped to
this repo's own marketplace); record that repo's explicit, committed opt-in
before enabling anything (the ownership signal Phase 2's setup skill
requires); stand up the scheduled worker (a thin timer-triggered wrapper
around Phase 2's sync tool, no device polling needed); add that repo's own
`projection-reconciler` agent file, wired to whatever dispatches on Phase 2's
conflict-dispatch label there.

### Phase 6 -- Validate
- [ ] Re-run the same 12-question navigability audit (fresh frozen snapshot,
      same nearly-tool-free method) against the fixed state; record the
      before/after verdict table in the Journal.
- [ ] Confirm the launch-script-ownership question specifically now produces
      a correct "this belongs to copilot-extensions, resolve via `related
      resolve`" answer rather than a false positive.
- [ ] Phase 5's own acceptance (that its scheduled worker produces a clean
      auto-merged PR at least once, and that a deliberately-forced conflict
      correctly routes to its reconciler rather than silently overwriting or
      blocking) is **that downstream companion effort's own validation
      item**, not this repo's -- this repo cannot verify a private repo's
      runtime behavior, and this Plan does not gate on it.

## Validation Plan

- [ ] Phase 1's guard test fails on a synthetic plugin with a declared-but-
      unindexed category, and passes once indexed (a real negative-proof
      test, not just a passing positive one).
- [ ] Phase 2's recompute verification rejects a PR whose diff does not
      byte-match the recomputed lock entries, and separately rejects one
      whose lock entries match but whose actual generated file content does
      not (the lock-hash-vs-real-file split case), and a PR with a managed
      path present in one but absent from the other (three distinct
      negative-proof tests, not one).
- [ ] Phase 2's bypass safety boundary is proven with negative tests for
      *each* conjunct, not just recompute mismatch: a no-change run opens no
      PR; a disabled plugin's projection is never touched even if its
      installed payload changed; a source outside the trusted-source
      allowlist is never auto-merged even with a byte-exact recompute match;
      a PR missing the stamp label, touching a path outside the managed
      globs, or containing a non-regular-file diff shape is rejected by the
      bypass; and a routed conflict is proven to update the existing PR
      without ever self-merging.
- [ ] The setup skill refuses to scaffold the scheduler/bypass without the
      repo's explicit, committed opt-in signal present (a negative-proof
      test: no opt-in file present -> setup declines), **and** a live
      revocation test: opt-in present at setup, then removed -> both the
      scheduled worker and the bypass profile fail closed on the next run
      without requiring a second `setup` invocation.
- [ ] Phase 6's re-audit shows a materially higher navigable/false-positive
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
  worktree/claim/session-management concepts), immediately after landing
  `ThomasMichon/copilot-extensions#3010` (`cross-repo-debug-tracking`),
  which turned out to be a live worked example of exactly this effort's
  target pattern.
- Ran the frozen-snapshot navigability audit (3 background `explore` agents,
  12 questions, nearly-tool-free) -- see Context above for the full table.
  A review caught a methodology-relevant correction: one question was
  initially marked NAVIGABLE by an audit agent that didn't check ownership
  first (the file in question is copilot-extensions-owned, not the
  downstream consumer repo's own); reclassified as a false positive, a new
  and arguably more important failure category than a plain dead end. A
  later review also caught an arithmetic slip in the summary (3/12
  navigable, not 4/12) -- corrected.
- Checked prior art before carving: `agents-md-vs-instructions-split` (Done)
  and `sessionstart-static-dynamic-conformance` (Active) are both real but
  orthogonal -- placement-correctness and static/dynamic-classification,
  not coverage/sync-freshness. No duplication found.
- Found the `head-claim-fallback.instructions.md` exemplar already exists
  and already proves the content pattern works -- the immediate blocker in
  the audited downstream repo was sync-drift, not invention of a new
  delivery mechanism; that one-time fix is tracked in that repo's own
  private effort, not here.
- Filed umbrella issue `ThomasMichon/copilot-extensions#3033`.
- Not yet started: Phase 1.

### 2026-09-20 (cont.) -- Designed `projection-reflect`, landed the immediate trigger
- Operator proposed modeling the sync-freshness fix on this facility's own
  proven live-config reflect/reconcile system (private, downstream) rather
  than inventing a new mechanism: a deterministic, non-agentic sync worker
  producing a narrowly-scoped, stamp-labeled PR a review gate can safely
  auto-accept, with a conflict-dispatch fallback to a non-self-merging
  reconciler agent when it can't cleanly land.
- A downstream research pass confirmed the mapping is sound and identified
  one improvement over the private prior art: because the source here is
  already-reviewed, already-merged upstream content (not live, unrepeatable
  device state), the bypass can require a byte-exact recompute-and-verify
  match, strictly stronger than what the prior system can offer.
- Operator confirmed: reuse the downstream repo's existing trusted
  deterministic identity for this new reflect kind too, rather than minting
  a new one -- the real safety boundary is the conjunction of identity +
  stamp label + path-scope + diff-shape + recompute-match, not identity
  alone (a lesson the prior system's own history had already established).
- Landed the cheap, always-on half immediately:
  `ThomasMichon/copilot-extensions#3053` (merged) adds the proactive
  resync trigger to the force-synced `cross-repo-debug-tracking`
  instructions -- after merging an upstream PR, immediately resync the
  current harness/consumer repo rather than waiting for a scheduled pass.
- Revised the Plan: Phase 1 is now narrowly the content-coverage registry
  and guard; Phase 2 is the new `projection-reflect` generic recipe (this
  repo); a new downstream Phase 5 instantiates it. Not yet started: Phase 1
  or Phase 2's remaining items (the deterministic sync tool, conflict-
  dispatch primitive, reconciler template, and setup skill).

### 2026-09-20 (cont.) -- Phase 1 landed
- Designed and shipped `troubleshooting-index.json` as an opt-in, per-plugin
  sidecar to `instruction-projections.json`: each declared category carries
  an `id`, `summary`, `pointer`, and `markers` (substrings the guard checks
  for in that plugin's own static projection templates).
- Added `plugins/customizing-copilot/skills/reviewing-customizations/scripts/troubleshooting_index.py`
  (schema validation + coverage check, independent of the larger
  `instruction_projections.py` render/sync machinery) and the guard test
  `plugins/customizing-copilot/tests/test_troubleshooting_index.py` --
  parallel to `test_dynamic_pointer_projections_have_exact_session_writers`,
  it proves both directions with synthetic plugins (uncovered category
  reported, covered category not reported, no-declaration and
  no-static-projections edge cases, five malformed-declaration fail-closed
  cases) plus a real repository-wide guard (`test_every_real_plugin_...`)
  that will start enforcing coverage the moment Phase 3 populates any
  plugin's registry.
- Extended `docs/patterns/agents-md-vs-instructions-split.md`'s audit
  heuristic with the fourth question this effort's audit motivated, and
  documented the new registry in `reviewing-customizations`' `SKILL.md`.
- No plugin populates `troubleshooting-index.json` yet -- that's Phase 3's
  content-gap work, deliberately decoupled from this phase's mechanism.
- Incidentally found and fixed an unrelated, pre-existing `marketplace.json`
  version-consistency drift for `agent-worktrees` (stale by one dev version
  from the just-merged `#3062`) as a separate atomic commit.
- `customizing-copilot`'s full suite (163 passed, 6 skipped),
  `check-version-bump`, `check-version-consistency`, and
  `check-docs-consistency` all green. Not yet started: Phase 2.
