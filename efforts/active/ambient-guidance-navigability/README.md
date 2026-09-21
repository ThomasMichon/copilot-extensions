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
- **Sub-issues:** [Phase 1 -- #3071](https://github.com/ThomasMichon/copilot-extensions/issues/3071),
  [Phase 2 -- #3082](https://github.com/ThomasMichon/copilot-extensions/issues/3082),
  [Phase 3 -- #3120](https://github.com/ThomasMichon/copilot-extensions/issues/3120),
  [Phase 2 immutable-pin resolver follow-up -- #3132](https://github.com/ThomasMichon/copilot-extensions/issues/3132).

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
      heuristic with a fourth question: "Is this a known failure symptom an
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
- [x] **Deterministic sync tool**: a script (extends
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

      **2026-09-20/21: the orchestration half of this item is built,
      tested, and merged** (PRs #3139, #3149 -- 3149 fixed a genuine
      lock-atomicity race the first PR's review caught: `sync`'s own lock
      is now held across sync + scan + both lock-entry reads via
      `repository_sync_lock()`/`sync_repository_locked()`, so a concurrent
      worker can never interleave and get misattributed to a pass's
      outcome). `scripts/projection_sync_worker.py`'s `run_sync_pass()`
      composes `sync`/`scan`/`projection_reflect` into exactly one
      deterministic pass per call (the correct actionable-change trigger,
      fail-closed classification, managed-file-conflict routing, and now
      cross-worker atomicity are all exercised by its test suite), returning
      a single `SyncOutcome` a caller never needs a second invocation of
      this tool to complete -- closing the "onerous multi-round-trip PR"
      failure mode this item warns against. The consent-gated CLI entry
      point derives its trusted-source allowlist from
      `projection_reflect_consent.load_consent()` only (no
      caller-suppliable override), and the
      `setting-up-instruction-sync-worker` scaffolder template was updated
      to call `run_sync_pass()` instead of hand-rolling the sequence.
      **What remains open** is solely the deep byte-exact
      recompute-against-a-pinned-artifact verification described above,
      which depends on the still-missing marketplace-source commit resolver
      (tracked in #3132) to actually populate `projection_reflect.py`'s
      `pinned_commits` map for a real run -- `run_sync_pass()` already wires
      that parameter through today, so wiring in a real resolver's output
      requires no further orchestration change here.
- [x] **Conflict-dispatch primitive**: a reusable helper (candidate home:
      `agent-dispatch`, since dispatch itself is a copilot-extensions
      plugin) generalizing `config-reflect`'s `conflict_dispatch.py` pattern
      -- domain-scoped dedup key, compact descriptor, async `agent-dispatch
      create` call -- parameterized so it isn't config-reflect-specific.
- [x] **`projection-reconciler` agent template**: modeled on
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
- [x] **`setting-up-instruction-sync-worker` skill** (or a section within
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
- [x] Reuse the **same** deterministic producer identity a repo already
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
- [x] `agent-worktrees`: claims-ledger index row (-> `claims` command /
      `tracing-claimant-graphs` skill), resource-obligations index row (->
      `worktree/references/obligations.md` / `finalize` failure meaning).
- [x] `agent-dispatch`: blocked-task-recovery index row (live-but-
      structurally-blocked task -> inspect/suspend/release/escalate path).
- [x] `agent-worktrees` or a shared location: `repos gh` GraphQL-error
      triage row; command-not-found/PATH triage row.
- [x] `copilot-extensions-harness` or the reviewing agent's own guidance:
      commented-verdict-with-no-actionable-feedback handling row.
- [x] `context-handoff`: tools-unavailable fallback row (what a session does
      when the skill/tools it's told to invoke aren't present this session).
- [x] Ownership-boundary disambiguation: a rule (likely in the root
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
- [x] Re-run the same 12-question navigability audit (fresh frozen snapshot,
      same nearly-tool-free method) against the fixed state; record the
      before/after verdict table in the Journal.
- [x] Confirm the launch-script-ownership question specifically now produces
      a correct "this belongs to copilot-extensions, resolve via `related
      resolve`" answer rather than a false positive.
- [ ] Phase 5's own acceptance (that its scheduled worker produces a clean
      auto-merged PR at least once, and that a deliberately-forced conflict
      correctly routes to its reconciler rather than silently overwriting or
      blocking) is **that downstream companion effort's own validation
      item**, not this repo's -- this repo cannot verify a private repo's
      runtime behavior, and this Plan does not gate on it.

## Validation Plan

- [x] Phase 1's guard test fails on a synthetic plugin with a declared-but-
      unindexed category, and passes once indexed (a real negative-proof
      test, not just a passing positive one).
- [ ] Phase 2's recompute verification rejects a PR whose diff does not
      byte-match the recomputed lock entries, and separately rejects one
      whose lock entries match but whose actual generated file content does
      not (the lock-hash-vs-real-file split case), and a PR with a managed
      path present in one but absent from the other (three distinct
      negative-proof tests, not one). **Transferred, not dropped:** this
      depends on the immutable-pin verification gap `projection_reflect.py`
      explicitly tracks as unsolved (today's lock schema has no commit/
      release reference to recompute against) -- cannot be validated until
      that gap closes in a follow-up slice. **Narrowed 2026-09-20:**
      `bypass_decision`'s pin conjunct is now built and proven (see the
      Journal) -- what remains is solely the marketplace-source commit
      resolver that would populate it for a real run; no further schema
      work is needed once that resolver exists.
- [ ] Phase 2's bypass safety boundary is proven with negative tests for
      *each* conjunct, not just recompute mismatch: a no-change run opens no
      PR; a disabled plugin's projection is never touched even if its
      installed payload changed; a source outside the trusted-source
      allowlist is never auto-merged even with a byte-exact recompute match;
      a PR missing the stamp label, touching a path outside the managed
      globs, or containing a non-regular-file diff shape is rejected by the
      bypass; and a routed conflict is proven to update the existing PR
      without ever self-merging. **Partially covered** (conflict-routing and
      trusted-source-allowlist conjuncts are proven in
      `test_projection_reflect.py`); the stamp-label/diff-shape/disabled-
      plugin conjuncts are properties of the actual scheduler/bypass-profile
      implementation, which is inherently per-adopting-repo work (per the
      `setting-up-instruction-sync-worker` skill's own scope) and not yet
      built anywhere to test against.
- [x] The setup skill refuses to scaffold the scheduler/bypass without the
      repo's explicit, committed opt-in signal present (a negative-proof
      test: no opt-in file present -> setup declines), **and** a live
      revocation test: opt-in present at setup, then removed -> both the
      scheduled worker and the bypass profile fail closed on the next run
      without requiring a second `setup` invocation.
- [x] Phase 6's re-audit shows a materially higher navigable/false-positive
      ratio than the baseline table above, with the specific false-positive
      corrected.
- [x] `tools/run-plugin-tests.py customizing-copilot` and any touched
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

### 2026-09-20 (cont.) -- Phase 2 slice 1: the deterministic worker's decision layer
- Read the private downstream `config-reflect` system's architecture (not
  reproduced here; see the Plan's own pointer) to ground the reusable-
  primitives mapping before writing any code.
- Landed `plugins/customizing-copilot/skills/reviewing-customizations/scripts/projection_reflect.py`:
  the **policy layer** the deterministic sync tool needs on top of the
  already-existing `sync_repository`/`scan_repository` mechanism -- the
  `changed or lock_updated` actionable-change trigger, fail-closed finding
  classification (only `projection-missing`/`projection-source-update` are
  plain drift; every other check name, known or not, conflict-routes), and
  a trusted-source allowlist keyed off a lock entry's own
  `plugin@marketplace` identity. Pure, dependency-free functions -- no
  rendering, writing, or pushing.
- 20 tests (`test_projection_reflect.py`, `pytest.mark.guard`): unit-level
  proofs for each function plus two integration tests against the real
  `sync_repository`/`scan_repository` engine (a clean sync/scan against a
  trusted source is bypass-eligible; a simulated hand-edit of a managed
  projection -- the actual conflict-dispatch trigger case -- correctly
  routes away from bypass).
- **Explicitly did not claim the "Deterministic sync tool" checklist item
  done** -- this slice is the decision layer only. Still open: the actual
  worker script that orchestrates refresh-payloads -> sync -> scan and
  opens the stamp-labeled PR; and the Plan's **immutable-pin verification**
  requirement (a commit SHA/release digest per changed source, not just
  today's version string) has no home yet in the lock schema or the
  marketplace-source resolver -- flagged as a tracked, unsolved gap in the
  module's own docstring rather than guessed at. A hand-edit conflict is
  proven to correctly conflict-route; wiring that finding through to an
  actual `conflict-dispatch` call is the next slice, alongside the
  conflict-dispatch primitive and `projection-reconciler` agent template
  items.
- `customizing-copilot`'s full suite (175 passed, 6 skipped),
  `check-version-bump`, `check-version-consistency`, and
  `check-docs-consistency` all green.

### 2026-09-20 (cont.) -- Phase 2 slice 2: the conflict-dispatch primitive
- Landed `plugins/agent-dispatch/src/agent_dispatch/conflict_dispatch.py`:
  the generalized "resolve the conflicts on this stuck PR" dispatch helper
  the Plan calls for, home in `agent-dispatch` as suggested. Rather than
  duplicating the private prior art's hand-authored goal text, it builds on
  top of this plugin's own existing `conflict-resolution` loop recipe
  (`agent_dispatch.recipes`) -- discovered mid-slice that the generic
  recipe abstraction already covers the reusable dedup/PR-driving contract;
  this primitive's real, non-duplicated contribution is layering the one
  thing that recipe is deliberately policy-agnostic about: which named
  reconciler sub-agent owns a domain's resolution *policy* (device-biased
  config resolution vs. "never force-overwrite a hand-edited managed
  projection", etc.).
- Domain-scoped dedup key (`label:domain`), a compact JSON descriptor
  (`kind`/`domain`/`repo`/`pr`/`branch`/`base`, optional `extra`), a
  `descriptor_line`/`parse_descriptor_line` pair for the same observable
  stdout-marker convention the prior art uses, and `build_dispatch` which
  renders the shared recipe then appends the reconciler-delegation
  instruction, returning both the descriptor and the exact
  `agent-dispatch create` argv (title/`--prompt`/`--goal`/`--done-criteria`
  matching `recipes kick`'s own field mapping, so a task built this way is
  indistinguishable from one kicked directly).
- 11 tests (`test_conflict_dispatch.py`): descriptor shape, marker
  roundtrip (including rejecting another producer's `kind` sharing a log
  stream), and `build_dispatch` proving it genuinely reuses the recipe's
  shared safety clauses (not a hand-authored duplicate) while adding the
  domain delegation.
- `agent-dispatch`'s full suite (3059 passed across its sharded runner),
  `check-version-bump`, `check-version-consistency`, `check-docs-consistency`
  all green.
- Still open in Phase 2: the worker script/scheduler wiring that actually
  calls `projection_reflect`'s decision layer and this dispatch primitive,
  the `projection-reconciler` agent template this primitive names but does
  not yet define, the `setting-up-instruction-sync-worker` skill, and the
  immutable-pin verification gap already flagged in slice 1.

### 2026-09-20 (cont.) -- Phase 2 slice 3: the `projection-reconciler` agent template
- Landed `plugins/customizing-copilot/skills/reviewing-customizations/references/projection-reconciler-agent-template.md`:
  a scaffolding template (not a live agent -- deliberately kept off any path
  the mechanical scan treats as a real agent) modeled on the private
  `config-reconciler` prior art, narrowed exactly as the Plan specifies:
  **report-only** on a hand-edited managed projection (never force-
  overwrites, files/comments a tracked finding, stops); resolves an
  ordinary git-level conflict only by re-deriving the canonical render
  fresh (never blends two candidate truths); never self-merges.
- Unlike the private prior art, ships with **no bespoke MCP servers** --
  PR/issue tooling is repo-specific (Gitea vs. GitHub, etc.), so a generic
  template cannot assume a fixed transport; the not-yet-built
  `setting-up-instruction-sync-worker` skill is documented as the intended
  installer that adapts it per adopting repo.
- Documented the template's role in `reviewing-customizations`' `SKILL.md`.
- Bumped `customizing-copilot`'s version and the marketplace metadata
  version (content changed).
- Still open in Phase 2: the worker script/scheduler wiring, the
  `setting-up-instruction-sync-worker` skill itself (which this template
  depends on for actual installation), and the immutable-pin verification
  gap.

### 2026-09-20 (cont.) -- Phase 2 slice 4: the `setting-up-instruction-sync-worker` skill
- Landed `plugins/customizing-copilot/skills/setting-up-instruction-sync-worker/`:
  the last Phase 2 checklist item with a concrete, landable scope this
  session. Ships:
  - `projection_reflect_consent.py` + `Consent`/`load_consent`: the
    fail-closed opt-in loader (`.github/copilot/projection-reflect.json`,
    schema `copilot-extensions.projection-reflect-consent`) both the
    (not-yet-built) scheduler and bypass profile must call fresh on every
    run/PR -- 13 tests including the two negative-proof cases the
    Validation Plan calls for: no opt-in file present -> refuses, and a
    live-revocation test (opt-in present, then deleted -> the very next
    call fails closed without a second `setup`).
  - `SKILL.md`: the consent-gate-first procedure, what to scaffold once
    consent exists (the reconciler agent from slice 3's template, a
    scheduler config, a bypass profile), and the same-identity-reuse /
    decline-if-no-identity requirement from the Plan's own next bullet --
    marked that Plan item done too, since the skill's content is exactly
    where that requirement had to live.
  - `references/templates/scheduler-config.md` and `references/templates/
    bypass-profile.md`: generic, mechanism-agnostic templates a repo adapts
    to its own scheduler/review-gate, wiring the already-landed
    `projection_reflect.py` decision layer and
    `agent_dispatch.conflict_dispatch` primitive.
- `customizing-copilot` now ships 11 skills (was 10) -- updated the
  plugin's own README skill table and both stale skill-count mentions
  found while editing it (a pre-existing "nine skills" / "Ten skills"
  inconsistency, fixed alongside).
- **What's still open, honestly:** the actual scheduler script and bypass
  profile wiring for a real adopting repo remain unbuilt -- this skill
  scaffolds/documents the contract and ships the reusable Python pieces,
  but does not itself stand up a running worker (that is inherently
  per-adopting-repo work, per the skill's own scope). The immutable-pin
  verification gap flagged in slice 1 is also still open. With this slice,
  every Phase 2 Plan checklist item this repo's own history can carry is
  now checked; the remainder is downstream Phase 5 instantiation work.
- `customizing-copilot`'s full suite (188 passed, 6 skipped), `check-skills`
  (0 errors, 71 skills), `check-marketplace-isolation` (0 new findings),
  `check-version-bump`, `check-version-consistency`, `check-docs-consistency`
  all green.

### 2026-09-20 (cont.) -- Phase 3: populating the concrete content gaps
- Landed every remaining Phase 3 checklist item in one pass, each backed by
  Phase 1's `troubleshooting-index.json` registry + guard test so the claim
  is fail-closed-proven, not just prose:
  - `agent-worktrees`: extended `head-claim-fallback.instructions.md` with
    an "outbound claim is blocking finalize" section (`claims show`/`sweep`,
    pointing at `worktree:references/obligations.md` and
    `tracing-claimant-graphs`); new `cli-fallback.instructions.md`
    (GraphQL-error triage by failure class -- wrong account/scope, rate
    limit, permission, schema drift -- and command-not-found/PATH triage,
    distinguishing "not on PATH this process" from "not installed"); new
    `ownership-boundary-fallback.instructions.md` (check plugin-owned vs.
    local-repo-owned before trusting a local tool index, closing the
    audit's false-positive class).
  - `agent-dispatch`: new `blocked-task-fallback.instructions.md` --
    inspect (`show`/`card show`/`events`) before acting, then match the
    actual state (`release` a stuck-suspended task, `yield` a stuck-held
    one with `--exclude-self`, `abandon --permit` a genuinely-done/
    duplicate one, or recognize a normal SUSPEND on external state).
  - `copilot-extensions-harness`: new `commented-review-verdict.instructions.md`
    -- a plain-comment verdict is this repo's normal non-blocking shape, not
    a stuck state; read advisory, land the change, don't loop chasing a
    clean pass.
  - `context-handoff`: no new content needed -- `handoff-fallback.instructions.md`
    already ships a thorough "CLI fallback (tools unavailable)" +
    "find it yourself, no tools required" pair; only a
    `troubleshooting-index.json` registering it was missing, now added.
  - Populated `troubleshooting-index.json` for all four plugins (10
    categories total); re-ran `manage-instruction-projections.py sync` on
    this repo's own enabled-plugin set so the new content is actually
    checked in here too, not just declared.
- Bumped all four plugins' versions (`plugin.json` + `pyproject.toml` where
  applicable) and their marketplace entries + metadata version.
- `customizing-copilot`'s full suite (188 passed), the Phase 1 guard test
  (10 passed against real plugin content -- the first time it's exercised
  against non-synthetic data), `agent-worktrees`/`agent-dispatch`/
  `copilot-extensions-harness` guard suites, `check-marketplace-isolation`
  (0 bare-agent-command findings), `check-docs-consistency`,
  `check-version-bump`, `check-version-consistency`, `check-skills` all
  green. `context-handoff`'s full (non-guard) suite has 3 pre-existing
  local-environment failures (a Windows box's `bash.EXE` resolving to a
  non-functional WSL stub, exit 127) -- confirmed unrelated to this change
  (only a JSON file was added there) and left to CI's real Linux/Windows
  runners to adjudicate rather than worked around locally.
- With this pass, **Phase 3 and all of this repo's own Phase 1/2 Plan items
  are now checked.** Remaining open work is entirely downstream (the
  private consumer repo's Phase 0/4/5) or explicitly deferred
  (immutable-pin verification) -- see Phase 6 for the re-audit that
  actually validates this effort's target outcome.

### 2026-09-20 (cont.) -- Phase 6: re-audit against the fixed state
- Built a frozen snapshot (not the live repo) proxying a fully-synced
  consumer: a generic `AGENTS.md` plus every static `.instructions.md` file
  from the four plugins this effort touched (`agent-worktrees`,
  `agent-dispatch`, `copilot-extensions-harness`, `context-handoff`), copied
  from their current source templates.
- Ran the same 12-question audit against it with 3 independent, nearly
  tool-free `explore` sub-agents (view/glob/grep scoped to the snapshot
  directory only; explicitly forbidden from invoking any skill, running any
  live command, or reading anything outside the snapshot) -- the identical
  method the baseline audit used.
- **Before -> after, per question** (all 3 runs agreed on every verdict):

  | # | Question | Baseline | Re-audit |
  |---|---|---|---|
  | 1 | Plugin-owned launch script mistaken for local | **False positive** | **NAVIGABLE** (`ownership-boundary-fallback.instructions.md`) |
  | 2 | Commented-verdict PR review, no actionable feedback | PARTIAL | **NAVIGABLE** (`commented-review-verdict.instructions.md`) |
  | 3 | Handoff requested, tools unavailable | PARTIAL | **NAVIGABLE** (pre-existing `handoff-fallback.instructions.md`, now indexed) |
  | 4 | Plugin's CLI not on PATH | PARTIAL | **NAVIGABLE** (`cli-fallback.instructions.md`) |
  | 5 | `gh`-family command failed with a GraphQL error | PARTIAL | **NAVIGABLE** (`cli-fallback.instructions.md`) |
  | 6 | Detect a concurrent/head session | PARTIAL | **NAVIGABLE** (pre-existing `head-claim-fallback.instructions.md`) |
  | 7 | Stuck review-queue symptom, where to look | NAVIGABLE (skill-only) | **PARTIAL** in this strict no-skill re-audit (unchanged in substance -- still resolved only via an existing skill, which this method deliberately can't reach; not a regression) |
  | 8 | Which account for a `gh`-family command | NAVIGABLE (wrapper) | **NAVIGABLE** (`cli-fallback.instructions.md`, same wrapper now also named in the ambient file) |
  | 9 | Plugin's writable source-checkout location | NAVIGABLE (`cross-repo-debug-tracking`) | **NAVIGABLE** (unchanged + reinforced by `ownership-boundary-fallback.instructions.md`) |
  | 10 | Unsettled resource obligation blocking finalize | **DEAD END** | **NAVIGABLE** (`head-claim-fallback.instructions.md`) |
  | 11 | Outbound claim ledger / release a claim | **DEAD END** | **NAVIGABLE** (`head-claim-fallback.instructions.md`) |
  | 12 | Dispatched task live but structurally blocked | **DEAD END** | **NAVIGABLE** (`blocked-task-fallback.instructions.md`) |

  **Aggregate: 3/12 navigable, 5/12 partial, 3/12 dead end, 1 false positive
  -> 10-11/12 navigable, 1/12 partial, 0/12 dead end, 0/12 false positive.**
  The one specific false positive (#1, launch-script ownership) is
  corrected to NAVIGABLE, satisfying that Plan item explicitly. The one
  remaining PARTIAL (#7) is unchanged in substance from baseline (both were
  "resolved only by a skill, not ambient content") and was never a Phase 3
  content-gap target -- it is a legitimate residual, not a miss.
- Marked the two directly-checkable Phase 6 Plan items and four of six
  Validation Plan items done; the remaining two Validation Plan items
  (Phase 2's full recompute-verification and full bypass-conjunct proofs)
  are explicitly **transferred**, not silently dropped -- they depend on
  the immutable-pin gap and the not-yet-built per-adopting-repo scheduler/
  bypass implementation, both already tracked in this Journal.
- **Effort status:** every Plan/Validation item this repo's own history can
  carry is now resolved; the remainder is either downstream (private
  consumer repo's Phase 0/4/5) or explicitly transferred to a tracked,
  named follow-up (immutable-pin verification). Left `Status: Active`
  rather than `Done`, since two Validation Plan items remain genuinely open
  pending that follow-up -- not a false completion claim.

### 2026-09-20 (cont.) -- Immutable-pin gap: narrowed, not closed

Investigated the two candidate designs the prior session's handoff left
open (a lock-schema extension vs. a marketplace-source commit resolver)
before touching any code, per the handoff's explicit caution against
guessing.

- **Rejected widening `_LOCK_ENTRY_KEYS`/the provenance marker.** That key
  set is exact-match and enforced on *every already-rendered projection's
  marker repo-wide* (`_parse_marker`'s `set(marker) != _MARKER_KEYS`
  check). Adding a required `sourceCommit` key there would force a
  disruptive full resync of every managed `.instructions.md` file in this
  repo in one PR, just to carry a field nothing can populate yet (no
  resolver exists) -- exactly the ad hoc, under-designed move the prior
  Journal entry warned against. Confirmed via `scan_plugin_sources.py`:
  `PluginSource`/`_plugin_version()` only ever carry a mutable version
  string; the installed-payload footprint used for external marketplace
  plugins is a plain file copy, not a git checkout, so there is nowhere to
  read a commit SHA from today without building an actual resolver
  (network calls to a marketplace's release API, or a install-time
  manifest change) -- correctly still out of scope for this slice.
- **Landed instead:** `projection_reflect.py` gained an **additive,
  optional pin map** kept entirely outside the lock/marker schema --
  `bypass_decision(..., pinned_commits: Mapping[str, str] | None = None)`.
  `None` (the default) preserves prior behavior exactly (verified: all
  pre-existing tests pass unchanged). When a caller supplies a
  `"<plugin>@<marketplace>" -> commit SHA` map -- from *any* future
  resolver, without this module caring which -- every changed source
  missing a well-formed 40-hex pin (`is_valid_commit_pin`) is rejected,
  closing the remaining verification gap the moment any resolver exists,
  with zero schema migration and zero blast radius on already-rendered
  content.
- Added 5 new unit tests (`test_bypass_decision_ignores_pins_when_none_
  provided`, `_requires_pin_for_changed_source_when_pins_supplied`,
  `_rejects_malformed_pin`, `_eligible_with_valid_pin_supplied`, and
  `test_is_valid_commit_pin_requires_full_hex_sha`) plus the two negative-
  proof cases: an empty pin map rejects a changed source outright, and a
  malformed pin value is never treated as valid. `customizing-copilot`'s
  full suite (193 passed, 8 skipped) and `check-version-bump`/
  `check-version-consistency`/`check-docs-consistency` all pass; bumped
  `customizing-copilot` to `0.1.0-dev81` (plugin.json + marketplace.json).
- **Still open, correctly transferred, not solved here:** building the
  actual marketplace-source commit resolver that would populate
  `pinned_commits` for a real sync worker. That remains its own follow-up
  slice -- this change only makes the eventual resolver's integration a
  parameter, not a schema migration.

### 2026-09-20 (cont.) -- Landed the deterministic sync tool (one PR per run)

Operator's explicit driving constraint for this slice: whatever landed next
must not be so onerous it forces multiple round-trip PRs just to finish
syncing a single upstream change -- it must be one-and-done per change.
That constraint pointed directly at Phase 2's one remaining unchecked Plan
item, the **deterministic sync tool** itself: `sync_repository`,
`scan_repository`, and `projection_reflect`'s policy layer existed, but
nothing composed them into a single callable a scheduler could invoke once
per run and trust to be finished.

- Added `scripts/projection_sync_worker.py`. `run_sync_pass()` runs `sync`
  (the only mutation -- writes/locks whatever it can safely resolve) then
  `scan` (validates the result, reports everything unresolved) **exactly
  once**, reads back the changed lock entries, and calls
  `projection_reflect.bypass_decision()` to produce a single `SyncOutcome`.
  A caller reads `needs_pr` / `bypass_eligible` / `needs_conflict_dispatch`
  directly off that one object -- there is no second call that could
  surface more work from the same run, which is precisely what makes this
  "one-and-done": a scheduler wired to it opens **at most one PR per
  invocation**, never a follow-up PR to finish what the first call missed.
- Deliberately still performs **no git/PR/dispatch operations** of its own
  (matches `projection_reflect.py`'s existing boundary) and takes an
  optional `refresh` callback for installed-payload refresh (host-specific,
  run once before the pass, never retried mid-pass mid-way through) --
  both are the calling scheduler's job, which remains per-adopting-repo
  Phase 5 work, correctly out of this repo's own scope.
- Wired `pinned_commits` straight through to `bypass_decision()`, so the
  still-open marketplace-source commit resolver (#3132) only has to
  produce a map -- no further change to this orchestration layer.
- Added 7 new tests in `test_projection_sync_worker.py`: no-op idempotency
  on a second run against an unchanged repo, first-sync bypass-eligibility,
  untrusted-marketplace conflict-routing, hand-edit conflict-routing,
  missing/valid pin behavior (reusing the pin conjunct landed in the prior
  entry), and refresh-callback invocation. Documented the tool in
  `reviewing-customizations/SKILL.md`. Full `customizing-copilot` suite:
  200 passed, 8 skipped. `check-version-bump`/`check-version-consistency`/
  `check-docs-consistency` all pass.
- Marked this Plan item's orchestration half done in place (inline note,
  not a bare checkbox flip) since the item's deeper byte-exact recompute-
  against-a-pinned-artifact sub-requirement is still gated on the same
  resolver as above -- see the inline annotation on the Plan item itself
  for the precise split.

### 2026-09-20/21 (cont.) -- Deterministic sync tool: merged, race fixed, item checked

PR #3139 (the entry above) merged **while its third review round was still
in flight** -- this repo's `pr-self-merge` flow treats the automated
Copilot review as advisory (checks are the real gate), and checks were
green at that point. That round's findings were genuinely still open:

- **A real lock-atomicity race**, correctly caught: `sync_repository()`
  released its own per-repository lock before returning, so
  `scan_repository()` and the before/after `load_lock_entries()` reads ran
  *unlocked* -- a second concurrent worker's own sync could interleave
  between this pass's sync and its scan, and get misattributed to this
  pass's `SyncOutcome`. Fixed via two small new public functions in
  `instruction_projections.py` -- `repository_sync_lock()` (a public
  accessor for the existing lock) and `sync_repository_locked()`
  (`sync_repository`'s body, assuming the caller already holds it, so
  re-acquiring the same lock twice in one process never self-deadlocks).
  `run_sync_pass()` now acquires the lock once and holds it across sync,
  scan, and both lock-entry reads. Proven with a test that goes further
  than "acquisition fails when the lock is already held" (which would have
  passed even against the old, buggy code): a spied `scan_repository`,
  still nested under the held lock, itself attempts a second independent
  acquisition and must fail -- proving no other worker could interleave
  during the scan step specifically.
- Also fixed on the same PR: an unsafe pre/post-sync lock read (bypassed
  `_load_lock`'s own symlink/size-bound safety -- replaced with a new
  public `instruction_projections.load_lock_entries()` wrapper), a
  lock-only-update exemption gap (`_policy_relevant_destinations()` now
  diffs the lock's entries before/after the pass, not just `sync`'s
  content-diff `changed` list), dropped sync-side findings (a failed sync
  no longer looks like a clean scan), `needs_conflict_dispatch` incorrectly
  covering every bypass refusal (narrowed to real
  `classify_findings(...).conflict` findings only -- an untrusted-source or
  missing-pin refusal is review-only, not reconciler-dispatchable), a
  `--installed-root` CLI override that could substitute an unverified
  payload tree onto the same bypass-eligible surface (removed entirely),
  and JSON/text error-output parity across every CLI failure branch.
- The PR being already-merged left these fixes as unpushed local commits
  on a now-closed branch/PR -- recovered by extracting the diff and
  reapplying it as a fresh commit in a new worktree, landed as **PR #3149**
  (6 review rounds; one raised finding -- "exclude user/local settings from
  source discovery" -- was a genuine false positive rebutted with the
  existing test proving `discover_enabled_sources()` already hardcodes
  `include_user=False`/`include_local=False` internally, documented at the
  call site so a future review pass doesn't have to re-derive it). Also
  updated the `setting-up-instruction-sync-worker` scaffolder template
  (`scheduler-config.md`) to call `run_sync_pass()` instead of hand-rolling
  the sync/scan/decide sequence it was written to replace. Final review
  verdict: "Approval recommended -- no unresolved blocking issues remain."
  Merged; `customizing-copilot` at `0.1.0-dev86`.
- **Phase 2's "Deterministic sync tool" Plan item is now checked** -- the
  orchestration half is complete, tested (28 tests across
  `test_projection_reflect.py` + `test_projection_sync_worker.py`), and
  merged. Only the deep byte-exact recompute-against-a-pinned-artifact
  verification remains open, gated on the still-missing marketplace-source
  commit resolver (#3132) -- unchanged from the prior entry's assessment.

