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
  [Phase 2 immutable-pin resolver follow-up -- #3132](https://github.com/ThomasMichon/copilot-extensions/issues/3132),
  [Phase 7 -- #4674](https://github.com/ThomasMichon/copilot-extensions/issues/4674).

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
      recompute-against-a-pinned-artifact verification described above.
      **2026-09-21: partially closed.** `scan_plugin_sources.
      resolve_pinned_commits()` is a real, honestly-partial resolver: it
      resolves an immutable commit SHA (`git rev-parse HEAD`) for a
      self-hosted/directory-marketplace source's own payload root, and is
      simply absent from the map for anything it cannot resolve (today: any
      plain installed-plugins payload copy -- the common case for an
      externally-installed marketplace plugin, which has no local git
      history to read). The consent schema gained an opt-in
      `requireImmutablePin` (default `false`) so enforcing the pin conjunct
      never silently disables the bypass path for the common
      externally-installed case; a repo that only syncs self-hosted plugins
      can opt in today. **What remains open, tracked in #3132:** an
      externally-installed marketplace plugin can still never be pinned --
      that needs either an install-time provenance record or a
      marketplace-release-API lookup, deliberately not guessed at here.
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

### Phase 7 -- Worktree-scoped dynamic guidance for projected instructions
Closes a gap found while a downstream consumer repo drove Phase 5 to a live
deployment and proof (tracked in that repo's own private tracking issue, not
cited here per this repo's identifier-neutrality rule): the checked-in
projection alone leaves an ordinary contributor without push rights unable
to self-correct sync-lag, and a fully hookless/headless launch path never
gets fresher content than that same checked-in floor even when a hooked path
could. See
[ThomasMichon/copilot-extensions#4674](https://github.com/ThomasMichon/copilot-extensions/issues/4674)
and the new sibling pattern doc,
`docs/patterns/worktree-scoped-dynamic-guidance.md`, for the full design.

- [x] `.gitignore` convention: `.github/instructions/**/*.local.instructions.md`
      -- document as part of the `setting-up-instruction-sync-worker` skill's
      scaffolding output (every adopting repo needs this rule, not just this
      one). **Landed**: a scaffolding section (not gated by this skill's own
      `projection-reflect.json` consent file, though the ignore-rule commit
      itself still needs the adopting repo's own ownership/contribution-flow
      authority) plus `references/templates/gitignore-rule.md` in that
      skill, and `instruction_projections._iter_projection_files` hardened
      so a `*.local.instructions.md` file is excluded from the checked-in
      orphan scan unless a best-effort git check reports it tracked or
      inconclusive (a plain directory with no `.git` at all still excludes
      it, unchanged -- tracking is simply inapplicable there) -- a tracked
      (or inconclusive) one in a real git working tree is still scanned and
      reported as `projection-orphan-file` rather than silently hidden.
      `_resolve_git_tracked_paths` itself also hardened to distinguish a
      genuinely missing `git` binary from a real repo's `rev-parse` probe
      merely failing to complete -- the latter is now `inconclusive` too,
      never silently treated as "not a repository." See the Journal.
- [x] Extend the projection template (`customizing-copilot:reviewing-
      customizations`) with the per-file "prefer the local sibling if
      present" preamble, prepended ahead of the existing rendered body.
      **Landed**: `render_projection` now builds this preamble inline
      (naming the destination's own `local_sibling_destination()` basename)
      and inserts it between the provenance marker and the template's own
      body for every rendered projection, repo-wide -- no per-template
      opt-in. A new `include_prefer_local` keyword (default `True`) lets
      `render_local_cache` render the same spec a second time with it
      suppressed, since the local cache file is itself the fresher content
      and must never carry a preamble pointing at its own sibling. Trimmed
      three shipped templates that were within the new preamble's width of
      `MAX_PROJECTION_BYTES` (`agent-worktrees`'s `head-claim-fallback` and
      `context-handoff`'s `handoff-fallback`/`awareness`) so the budget test
      still passes with the preamble included; see the Journal.
- [x] Add a render-only entry point to `projection_sync_worker.py` (or a
      sibling script) that performs `sync`'s render step against currently
      installed payloads **without** the git/PR half -- no branch, no commit,
      no push, purely a local file write to each destination's
      `.local.instructions.md` sibling. This is the shared function both
      `agent-worktrees` (Phase 7a below) and any adopting repo's own
      `sessionStart` hook call into. **Landed as
      `instruction_projections.render_local_cache()` /
      `local_sibling_destination()`** (not a `projection_sync_worker.py`
      addition -- it sits beside `render_projection`/`sync_repository` in
      the same module those already live in, since it shares their spec-
      loading and atomic-write primitives directly rather than composing
      through the sync/scan/decide pass, which this path deliberately
      skips). Consent-free, lock-free, git-free by construction; see the
      Journal for the negative-proof tests covering this.
- [x] A single repo-wide catch-all static projection (its own
      `instruction-projections.json` entry, applying to every repo that
      adopts this pattern) directing the agent to scan for and read any
      `**/*.local.instructions.md` files present, per the pattern doc's §3.
      **Landed**: `customizing-copilot` -- the mechanism's own home, and
      the one plugin guaranteed present wherever it's adopted -- ships a
      single `local-cache-catchall` source (`applyTo: "**"`), verbatim from
      the pattern doc's §3 template. Covered by the generic
      `test_shipped_projection_budgets.py` (auto-discovers any plugin's
      `instruction-projections.json`) plus a dedicated validity test.
- [x] `agent-worktrees`: wire the render-only entry point into worktree
      **create and resume**, and repeat it from the plugin's own
      `sessionStart` hook as a backup for drift accrued since. Requires the
      target repo to already be a trusted folder (same prerequisite
      `session-scoped-dynamic-guidance.md` §3 already documents for
      repository-level hooks). **Landed**: a new
      `agent_worktrees.local_cache_refresh` module resolves customizing-
      copilot's declared, versioned `render-local-cache` CLI operation
      (`manage-instruction-projections.py`) and invokes it as a
      bounded-timeout subprocess -- never importing that plugin's Python
      package directly, per `a-la-carte-independence.md`'s "no
      cross-plugin reach-around" rule -- fully best-effort (never raises,
      never gates the caller). Wired into `worktree_creation.
      _create_worktree_core` (after trust/permission approval, every
      `kind`), `resolve_launch_cli._resolve_resume_context` (after the
      fast-forward, skipped on `--dry-run`), and `__main__.
      _run_session_lifecycle` (the real `sessionStart` handler, via
      `local_cache_refresh.sessionstart_diagnostic()` as a silent,
      synchronous, deadline-bounded backup). See the Journal.
- [x] Guard test: a synthetic plugin with a fresh installed payload produces
      a `.local.instructions.md` sibling whose content byte-matches a fresh
      `sync`-equivalent `render_projection` call, and the checked-in file's
      own content is left untouched (no git write from this path, ever).
      **Landed**:
      `test_render_local_cache_writes_sibling_without_touching_checked_in_or_lock`
      in `tests/test_instruction_projections.py`.
- [ ] Negative-proof test: a write-incapable target directory (simulated
      read-only) leaves the checked-in floor as the only available content,
      with no error raised to the caller and no partial/corrupt
      `.local.instructions.md` file left behind. **Not yet covered** --
      `test_render_local_cache_reports_root_error_without_raising` proves a
      missing repo root reports a blocking finding without raising, but a
      write-*denied* (as opposed to nonexistent) destination remains open.

## Validation Plan

- [x] Phase 1's guard test fails on a synthetic plugin with a declared-but-
      unindexed category, and passes once indexed (a real negative-proof
      test, not just a passing positive one).
- [ ] Phase 2's recompute verification rejects a PR whose diff does not
      byte-match the recomputed lock entries, and separately rejects one
      whose lock entries match but whose actual generated file content does
      not (the lock-hash-vs-real-file split case), and a PR with a managed
      path present in one but absent from the other (three distinct
      negative-proof tests, not one). **Scoped-closed 2026-09-21, not
      dropped:** `bypass_decision`'s pin conjunct is built and proven (see
      the Journal), and `resolve_pinned_commits()` (PR #3159) is the
      complete, final resolver for this effort's actual scope --
      self-hosted/directory-marketplace sources only, per the operator's
      explicit decision not to extend trust/pinning to externally-installed
      marketplace plugins generally (instruction-projection is a narrow,
      bespoke workaround, not a general provenance system). The deep
      byte-exact clone-and-recompute automation this item originally
      envisioned remains unbuilt -- it is optional further hardening for
      the self-hosted case (technically buildable with only local git
      operations, no network dependency), not currently pursued given that
      framing. This is a closed decision, not an open gap awaiting a
      resolver.
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
- [x] Phase 7's render-only entry point never performs a network fetch,
      git write, commit, or push under any code path -- proven by a test
      that runs it against a repo with no configured git remote/credentials
      at all and confirms it still succeeds. **Landed**:
      `test_render_local_cache_never_touches_git_and_is_idempotent` (runs
      against a `tmp_path` repo with no `.git` directory at all).
- [ ] Phase 7's guard and negative-proof tests (see Plan) pass; the per-file
      preamble and the repo-wide catch-all are each independently provable
      to drive an agent to the fresher content in a clean-room-style test
      matching the methodology `session-scoped-dynamic-guidance.md`'s own
      evidence section used. **Partially covered:** every Plan item's own
      guard/negative-proof unit and integration tests pass (preamble
      placement/naming, local-cache self-reference exclusion, the
      `skipLocalCache` opt-out and its schema validation, the aggregate-
      budget fit, and the `agent-worktrees` create/resume/`sessionStart`
      wiring, each with dedicated tests -- see the Journal for every
      slice). The specific **clean-room, agent-driven proof** this item
      asks for (an isolated sub-agent, given only a system-prompt snapshot
      carrying both a checked-in projection and a divergent
      `.local.instructions.md` sibling, demonstrably prefers the sibling's
      content) has **not** been built -- it is a distinct, heavier
      validation exercise (the navigability audit's own 3-sub-agent,
      skill/tool-forbidden methodology), not yet attempted. This is the
      one remaining item before Phase 7 can be marked Done.

## Proposal

_Pending._

## Journal

### 2026-10-01 (cont.) -- Phase 7 slice 5: `agent-worktrees` wiring (last Plan item)
- Picked up the last unstarted Plan item: wired `customizing-copilot`'s
  `render_local_cache()` into `agent-worktrees`' own lifecycle boundaries.
  Explicit operator goal for this slice: roughly eliminate the need for a
  manual force-sync, in favor of an occasional housekeeping sync -- this
  wiring is what actually makes the mechanism built across slices 1-4
  *automatic* rather than something an operator has to remember to trigger.
- New `agent_worktrees.local_cache_refresh` module: resolves customizing-
  copilot's installed `skills/reviewing-customizations/scripts/
  instruction_projections.py` at runtime (marketplace layout first, falling
  back to a `_direct` install matched by `plugin.json` name -- generalizing
  `update_stage.discover_plugin_dir`'s own agent-worktrees-specific
  resolution to a named sibling plugin, since no prior cross-plugin runtime
  import existed anywhere in this codebase), then calls its
  `render_local_cache()` against `discover_enabled_sources(repo_root,
  require_trust=False)` -- the same `require_trust=False` the mechanism's
  own tests already use for a context where trust is already established.
  Fully best-effort by construction: customizing-copilot not being
  installed, the repo not yet trusted, or any render failure are all
  absorbed inside `refresh_local_cache()` itself, never gating the caller.
- Three call sites, exactly matching the pattern doc's §4:
  - `worktree_creation._create_worktree_core`, right after trust +
    extension-permission approval (every `kind`, including `system`) --
    before the paired-knowledge carve and before Copilot ever launches.
  - `resolve_launch_cli._resolve_resume_context`, right after the
    fast-forward (which may have just changed the installed payload this
    worktree sees), guarded by `not args.dry_run` -- a preview must never
    have this side effect.
  - `__main__._run_session_lifecycle` (the real handler
    `hook_client.py`'s thin client subprocess-dispatches to for
    `sessionStart` -- the hook itself never contained the real logic),
    alongside the existing anchor-hygiene/provisioning diagnostics, as a
    silent backup (no diagnostics string, since the refresh is itself
    silent by design and this call is pure backup, not a user-facing
    event).
- Tests: a new `tests/test_local_cache_refresh.py` (candidate-root
  resolution incl. marketplace + `_direct`-install-by-manifest-name
  fallback, graceful absence, internal-failure absorption, successful
  delegation to a stub module; direct wiring assertions for create and
  resume including a dry-run-never-refreshes case) plus two new tests in
  `tests/test_hook_ipc.py` for the `sessionStart` backup (calls through,
  and absorbs an internal failure without surfacing it). Patched every
  pre-existing test that exercises `_create_worktree_core`/
  `_resolve_resume(_context)` unmocked (`test_paired_carve.py`,
  `test_launch_project_scoping.py`) to stub the new call, so those tests
  stay hermetic rather than implicitly depending on whatever customizing-
  copilot install happens to be present on the machine running them.
  `tools/run-plugin-tests.py agent-worktrees`: 396 passed (plus the
  pre-existing, unrelated `test_posix_binstub_self_provisions_from_a_
  direct_install_layout` POSIX-shell failure on this Windows environment,
  confirmed via `git stash` to predate this change) across the suite's
  first three sub-suites before the test-supervisor's own wall-clock cap
  on this large suite; a broader targeted run across every file touched or
  exercising the new wiring (323 tests) passed clean.
- **Not done in this slice:** the clean-room, agent-driven proof the
  Validation Plan item above calls for. Flagged there rather than silently
  dropped -- it's the one remaining item before Phase 7 is Done.
- Phase 7's Plan is now fully landed (slices 1-5); the effort stays Active
  pending that one Validation Plan item.
- **Review round 1 findings (PR #4809), both addressed in the same PR:**
  - **Real, severe bug:** `local_cache_refresh._load_instruction_
    projections` registered the freshly-loaded module in `sys.modules`
    *after* `exec_module` ran. The real shipped `instruction_
    projections.py` declares postponed-annotation (`from __future__ import
    annotations`) dataclasses, whose decorator machinery looks up
    `sys.modules[cls.__module__]` while the class body executes --
    registering only after execution left that lookup unresolved, raising
    `AttributeError` *every single time* the real module was loaded. The
    broad `except Exception: continue` swallowed it silently, so every
    create/resume/sessionStart refresh since this landed would have been a
    **permanent, invisible no-op** -- confirmed by reproducing the exact
    failure directly against the real file before fixing (`exec_module`
    before registering fails with `AttributeError("'NoneType' object has
    no attribute '__dict__'")`; swapping the order fixes it). Fixed by
    registering in `sys.modules` before `exec_module`, and popping the
    partial entry back out on failure so a broken module is never left
    cached as if it had loaded. Added two regression tests: one using the
    exact dataclass-with-postponed-annotations shape that reproduces the
    real bug, and one proving a failed load leaves nothing cached.
  - The pattern doc's new landing note claimed "Phase 7 is complete,"
    contradicting the effort README's own still-open Validation Plan item
    one paragraph away. Reworded to "Phase 7's **Plan** is complete,"
    explicitly naming the open Validation Plan item -- matching the PR
    description's own narrower framing, which was already correct.
- **Review round 2 findings (PR #4809), both addressed in the same PR:**
  - **Real concurrency race, introduced by round 1's own fix:** the
    sessionStart resident server handles concurrent hook requests on its
    own threads, so two callers can reach `_load_instruction_projections`
    at once. Pre-registering in `sys.modules` before `exec_module` (round
    1's fix, required for the dataclass self-lookup) opened a window where
    a racing second caller's `cached = sys.modules.get(...)` early-return
    could observe the *first* caller's module mid-`exec_module` --
    partially initialized, `render_local_cache` not yet defined on it --
    and silently skip its own refresh. Fixed by serializing the whole
    check-and-load critical section behind a module-level
    `threading.Lock`. Added a 4-thread concurrent-load regression test
    (an artificial `time.sleep` inside the loaded module widens the race
    window) asserting every thread gets back a fully-executed module.
  - `discover_enabled_sources` was called without `agent_worktrees_command`,
    so a repo whose marketplace declares an `agent-worktrees-repo` source
    falls back to ambient `PATH` -- which could resolve a different
    installation cell's `agent-worktrees` (or none), silently omitting that
    source from the cache refresh. `reviewing-customizations/SKILL.md`'s
    own contract requires passing the current cell's catalog-resolved
    command. Added `_resolve_own_agent_worktrees_command()`, which resolves
    this exact cell's own deployed binstub via `installer.bin_dir()` (the
    same global-binstub location `installer.py`'s own deploy path writes
    to) rather than shelling out or trusting ambient `PATH`; `None` when
    this cell has no deployed binstub (unchanged fallback behavior).
    Threaded through `refresh_local_cache`. Added three dedicated tests
    (binstub present, absent, and `installer.bin_dir()` itself failing).
  - Also caught and fixed in the same push: the new regression test's
    docstring, and a code comment in `local_cache_refresh.py` itself, both
    named the review history ("as this loader originally did") rather than
    describing only the timeless invariant -- reworded both.
- **Review round 3 finding (PR #4809):** round 2's own `agent_worktrees_
  command` fix was itself wrong -- `installer.bin_dir()` resolves
  `install_dir()/"bin"` (the runtime-helper directory holding
  `resolve-runtime.*`/hook scripts), not any deployed binstub; the actual
  global shim lives in `installer.local_bin()` (`~/.local/bin`), which
  isn't reliably cell-pinned either, since it can be shared/overwritten
  across installs. The genuinely cell-pinned command is the owning
  payload's own `bin/payload/agent-worktrees[.cmd]` -- the exact path
  every project binstub `installer._project_binstub_specs` generates execs
  into -- resolved via `installer._payload_root()`. Fixed
  `_resolve_own_agent_worktrees_command` to use that instead. Replaced the
  three unit tests (which had exercised the wrong function) and added a
  fourth against a realistic deployed-plugin directory layout
  (`plugins/agent-worktrees/{plugin.json,src/agent_worktrees/,
  bin/payload/agent-worktrees}`), per the reviewer's explicit ask for a
  real-layout regression test rather than only a monkeypatched stand-in.
- **Review round 4 findings (PR #4809), both addressed in the same PR:**
  - **Real timing bug:** the `sessionStart` backup ran synchronously inside
    `_run_session_lifecycle`, but the resident hook server's own decision
    deadline is far shorter than `refresh_local_cache`'s worst-case cost
    (an `agent-worktrees-repo` source's own resolution budget, plus the
    renderer's git probes) -- a slow refresh could make the *entire*
    lifecycle response miss its deadline and get discarded, exactly the
    kind of harm a "pure backup" must never cause. Fixed by dispatching
    `refresh_local_cache` to a daemon thread instead of awaiting it inline
    -- never blocks this function's return; if the hosting process exits
    before the thread finishes, the refresh simply doesn't complete that
    round, no worse than skipping it. Updated the two existing `sessionStart`
    tests (a `_SynchronousThread` stand-in runs the dispatched target
    inline so the assertions don't race the real background thread).
  - **Real gap in round 2's own concurrency fix:** the lock only protected
    `_load_instruction_projections`'s own module load, not the nested,
    separately-unsynchronized lazy load `instruction_projections.py`'s own
    `discover_enabled_sources` performs internally (its own `scan-
    customizations.py` support module) during the render call -- a
    concurrent caller could still race on *that* nested load even with
    round 2's fix in place. Widened the lock (now an `RLock`, since
    `refresh_local_cache` holds it for its *entire* body and
    `_load_instruction_projections` also acquires it internally) to cover
    the whole refresh, transitively serializing any nested load the
    render call performs, without touching customizing-copilot's own
    source. Added a dedicated concurrent-`refresh_local_cache` regression
    test (not just the outer module load) with a fake nested-module lazy
    loader reproducing the shape of the real one.
- **Review round 5 findings (PR #4809), all addressed with a redesign in
  the same PR:**
  - **Cross-plugin reach-around (the deepest finding):** every fix through
    round 4 still had `local_cache_refresh` directly `importlib`-loading
    customizing-copilot's own private `instruction_projections.py` module
    and calling its internals in-process. This violates
    `docs/patterns/a-la-carte-independence.md` and
    `docs/patterns/runtime-agent-plugin.md`'s "no cross-plugin
    reach-around" rule -- a plugin may only talk to a sibling through its
    own declared, versioned surface, never by importing/poking its
    internal files, mirroring the precedent `claim_providers.py` already
    set (resolve only to a sibling's own payload-local binstub, invoke as
    a subprocess). Fixed with a real redesign: customizing-copilot's
    `manage-instruction-projections.py` CLI gained a third operation,
    `render-local-cache`, as the new declared surface; `local_cache_
    refresh.py` was rewritten from scratch to resolve that script's path
    and invoke it via a bounded-timeout `subprocess.run()` instead of any
    Python import. This incidentally eliminated the `sys.modules`/
    threading/lock machinery rounds 1-4 had been patching entirely --
    each subprocess is a fresh interpreter with no shared state to race.
  - **Round 4's daemon-thread fix was itself wrong:** dispatching the
    `sessionStart` backup to a background thread solved the deadline-
    blocking problem but broke correctness -- `docs/patterns/worktree-
    scoped-dynamic-guidance.md` §4's guarantee that the catch-all
    instruction's first-turn tool read is safe *because* `sessionStart`
    has already finished only holds if the render completes
    synchronously before the hook returns; an async dispatch races that
    read instead of guaranteeing it. Reverted to synchronous execution,
    but bounded: the backup now computes a timeout from the hook's own
    remaining decision-deadline budget (capped at a new
    `SESSIONSTART_MAX_TIMEOUT_S = 5.0`), skipping the call outright once
    under 2s of budget remains, so a slow refresh can again never cause
    the whole lifecycle response to miss its deadline -- without ever
    risking the first-turn-read race a thread would have introduced.
  - **Thread/lock accumulation:** every `sessionStart` call would have
    spawned a new daemon thread, all serialized behind one process-global
    lock -- unbounded queueing, violating
    `docs/patterns/work-coalescing-singleton.md`. Moot under the
    subprocess redesign: there is no shared lock or thread pool left to
    accumulate against.
  - **Missing Graceful cutover impact statement** (`CONTRIBUTING.md:609-
    628`, required for any change touching the resident `sessionStart`
    hook server): added to the PR description -- the redesign makes this
    a non-issue by construction, since the backup is a synchronous,
    timeout-bounded subprocess call inside the same request-handling
    thread, with no background daemon thread and no state held across a
    drain/cutover.
  - Also moved `_refresh_local_cache_diagnostic` (renamed
    `sessionstart_diagnostic`) out of the already-near-its-ceiling
    `__main__.py` into `local_cache_refresh.py` itself, alongside the
    function it wraps, shrinking `__main__.py` well clear of its 7083-line
    module-size cap instead of requiring a deliberate baseline widen.
  - `test_local_cache_refresh.py` rewritten for the subprocess-based
    design (including a real CLI round-trip test against the shipped
    `manage-instruction-projections.py`, not a stub); `test_hook_ipc.py`'s
    sessionStart coverage updated to match (removed the `_SynchronousThread`
    stand-in the round-4 fix had needed). Full targeted suite: 105 passed,
    4 skipped; customizing-copilot's own suite (for the new CLI operation):
    307 passed, 8 skipped. `tools/check-module-size.py` and `tools/check-
    docs-consistency.py` both clean with no baseline changes needed.
- **Review round 6 findings (PR #4809), all addressed in the same PR:**
  - **Real descendant-leak bug in round 5's own redesign:** the new
    `subprocess.run(argv, timeout=timeout)` call only terminates its
    direct child, not any descendant the CLI itself spawns (an
    `agent-worktrees repos find` lookup, git subprocesses --
    `scan_plugin_sources.py`) -- exactly the limitation `push_timeout.py`
    already documents and exists to close. Fixed by routing the call
    through `push_timeout.run_bounded()` instead (already reused for a
    non-push purpose by `git_ops.py`, so this isn't a new cross-purpose
    use), which kills the whole process tree on a stall. Added a real,
    non-mocked descendant-timeout regression test mirroring
    `test_git_ops.py`'s own `TestPushTimeoutTreeKill`: a stand-in CLI
    script spawns a grandchild and hangs, proving the grandchild does not
    survive `refresh_local_cache`'s own timeout.
  - **Two stale-design mentions the round-5 redesign missed:** a Plan-
    section landing note (separate from the Journal) and the PR
    description itself still described the superseded in-process
    `importlib`/`render_local_cache()` design. Updated both to describe
    the actual subprocess/CLI-boundary design.
  - **Graceful cutover impact statement** had been added to the PR
    description in round 5's own fix, but the round-6 review ran against
    an earlier fetch of that description (before the edit propagated) and
    flagged it as missing again -- reconfirmed present, no further action
    needed.
- **Review round 7 findings (PR #4809), all addressed in the same PR:**
  - **Real identity-spoofing gap in the `_direct`-install fallback:**
    `_resolve_cli_script`'s `_direct` candidate trusted a directory merely
    because it contained a `plugin.json` self-declaring the expected
    `"customizing-copilot"` name, with sorted-path order deciding which
    candidate wins when more than one claims it -- a stale or unrelated
    direct install could impersonate the real plugin, a direct violation
    of `docs/patterns/marketplace-installation-cells.md`'s "plugin name
    alone never selects a runtime" invariant. Fixed by resolving the
    sibling's root through `plugin_activation.resolve_active_plugins()`
    instead -- the same identity-verified active-plugin evidence
    `claim_providers.py` uses for its own sibling callbacks -- filtering
    its `.active` plugins by declared name and taking the reported live
    root. No active-plugin match (or an unreachable resolver) now fails
    closed to `None`: the refresh is silently skipped for that round
    rather than falling back to any unverified directory scan. This
    retired the home-grown `_candidate_plugin_roots` glob/manifest-name
    matcher entirely.
  - **Real deadline-budget bug in round 6's own fix:** `sessionstart_
    diagnostic`'s budget calculation reserved only a flat 1-second margin
    before the hook's own decision deadline, but `push_timeout.run_
    bounded`'s timeout path can itself spend up to 5 more seconds past its
    own `timeout` draining a killed process's pipes (`proc.communicate
    (timeout=5)` after the tree-kill) -- so a timed-out refresh could
    still overrun the hook's deadline despite round 4/6's bounding,
    silently breaking the synchronous-completion guarantee this whole
    backup exists to provide. Fixed by also reserving a new
    `_RUN_BOUNDED_CLEANUP_GRACE_S` (5.0s) in the budget math before
    deciding how much of it to hand to `refresh_local_cache` as its own
    `timeout`; when there's no deadline at all the grace reservation
    doesn't apply (nothing external to overrun), so the no-deadline case
    is unchanged.
  - **Code-style nit:** a new test's docstring named the review round
    that produced it instead of describing the invariant timelessly;
    fixed (review-round narrative belongs only in this Journal).
  - `_resolve_cli_script`'s and `sessionstart_diagnostic`'s own tests
    rewritten for the identity-verified resolution and the corrected
    budget math (including a reserved-cleanup-grace regression distinct
    from the plain too-tight-budget case); the `TestRefreshLocalCache`
    tests that used to create real files at the old naive path convention
    now monkeypatch `_resolve_cli_script` directly, decoupling them from
    the resolution mechanism. Targeted suite (`test_local_cache_refresh.py`,
    `test_hook_ipc.py`, `test_paired_carve.py`, `test_launch_project_
    scoping.py`, `test_git_ops.py`): 219 passed, 4 skipped. `tools/check-
    module-size.py` and `tools/check-docs-consistency.py` both clean.

### 2026-10-01 -- Phase 7 slice 4: the repo-wide catch-all static projection
- Picked up the next unstarted Plan item (slice 3's steer): `customizing-
  copilot` -- which already owns the whole projection mechanism
  (`instruction_projections.py`, `render_local_cache`,
  `local_sibling_destination`) and is therefore the one plugin guaranteed
  present in every repo that has adopted this pattern at all -- now ships
  its own `instruction-projections.json` declaring a single
  `local-cache-catchall` source, `applyTo: "**"`, rendered verbatim from
  `docs/patterns/worktree-scoped-dynamic-guidance.md` §3's template. This
  is the one thing every launch path loads unconditionally regardless of
  hooks, and the only piece that can help a source that has **never**
  synced in yet (slice 3's per-file preamble needs an existing checked-in
  file to carry it; this doesn't).
- New `plugins/customizing-copilot/instructions/` directory (the plugin
  had none before -- it previously shipped skills only, no projected
  instructions of its own).
- Renders to 1078 bytes, comfortably inside `MAX_PROJECTION_BYTES` --
  `test_shipped_projection_budgets.py` auto-discovers any plugin's
  `instruction-projections.json`, so no manual registration was needed
  there; added a dedicated
  `test_customizing_copilot_ships_the_repo_wide_local_cache_catchall` for
  the specific content/applyTo/byte-budget contract.
- `tools/run-plugin-tests.py customizing-copilot`: 303 passed, 8 skipped.
  `check-docs-consistency`, `check-version-consistency`, and
  `check-module-size` all clean (as declared and tested at that point in
  the PR -- the review round below went on to touch
  `instruction_projections.py` after all).
- **Review round 1 findings (PR #4798), all addressed in the same PR:** the
  catch-all is a declared source like any other, so `render_local_cache`
  would also render it into its own `local-cache-catchall.local.
  instructions.md` -- which the catch-all's own glob
  (`**/*.local.instructions.md`) then matches, reading back the identical
  "check for local siblings" directive a second time for no benefit.
  Added a new, optional, schema-validated `skipLocalCache` declaration
  field (default `false`) rather than special-casing this one source by
  name; `_render_local_cache_locked` now skips any source that sets it,
  and the stale-cleanup pass already removes a previously-generated
  sibling for a source that newly opts out (no special-casing needed
  there either -- it just falls out of the existing "not in this call's
  accepted set" path). Set `skipLocalCache: true` on the shipped
  `local-cache-catchall` declaration. Added
  `test_render_local_cache_honors_skip_local_cache` (generic, against a
  synthetic plugin) and `test_skip_local_cache_must_be_boolean` (schema
  validation), plus asserted `skip_local_cache is True` on the real shipped
  spec. A second, small module-size baseline widen (2439 -> 2457) for this
  real fix, same documented-policy reasoning as slice 3's two widens.
- **Review round 2 findings (PR #4798), all addressed in the same PR:**
  - **Real bug in round 1's own fix:** the key-set guard used
    `set(entry) - _required_keys > _optional_keys` (a *strict superset*
    comparison) -- an unrelated extra key such as `{"someUnrelatedKey"}` is
    not a superset of `{"skipLocalCache"}`, so the comparison evaluated
    `False` and the entry silently passed validation, quietly weakening
    the exact-key schema contract this guard exists to enforce. Fixed to
    `not set(entry) - _required_keys <= _optional_keys` (extras must be a
    *subset* of the optional keys to pass). Added
    `test_unrelated_extra_key_is_rejected`.
  - The new changefile wrongly used `minor`; `CONTRIBUTING.md`'s own
    Release & Versioning section defaults to `patch` absent an explicit
    maintainer request for `minor`/`major`. Corrected.
  - This Journal's own "no production module touched this slice" claim
    (written before round 1's fix touched `instruction_projections.py`)
    was stale; reworded above rather than left contradicting the final
    diff.
  - `docs/patterns/worktree-scoped-dynamic-guidance.md` still said *every*
    checked-in projection gets a local sibling (§1) and that the catch-all
    "remains open" (its own landing note) -- both now stale once
    `skipLocalCache` shipped. Updated §1 to document the opt-out exception
    (naming the catch-all as the one shipped example) and the landing note
    to reflect slices 3 and 4 both having landed, leaving only the
    `agent-worktrees` wiring open. The PR's Documentation impact statement
    is corrected accordingly -- this *did* need a doc update, unlike
    slice 4's first round.
- **Review round 3 finding (PR #4798):** `test_unrelated_extra_key_is_
  rejected`'s own docstring recorded the superseded strict-superset
  comparison and its intermediate failure -- a review-process narrative
  baked into test prose, against `CONTRIBUTING.md`'s own Code Style
  guidance (code/docstrings describe current, timeless state; the review
  history belongs only in this Journal, where it already is). Reworded to
  describe only the invariant under test.
- **Review round 4 finding (PR #4798), a genuine aggregate-budget
  overflow:** this repository's own checked-in projection lock
  (`.github/copilot/context-projections.json`) already totaled 12,162
  bytes against the default 12,288-byte `MAX_AGGREGATE_BYTES` ceiling --
  126 bytes of headroom, less than the new catch-all's own ~1,078
  checked-in bytes. The next `sync_repository` run against this repo
  would have hit a blocking aggregate-budget finding instead of
  publishing the projection. Added a reviewed
  `.github/copilot/instruction-projections.config.json` (`maxAggregateBytes:
  16384`) -- the mechanism's own documented override path
  (`_load_aggregate_budget`), rather than shrinking the already-landed
  stack. Added `test_repository_enabled_stack_fits_the_aggregate_budget`:
  reads this repo's own `.github/copilot/settings.json` `enabledPlugins`
  (not the full plugin catalog -- most of what this repo ships is not
  self-enabled) and asserts the real enabled stack's rendered total fits
  the effective budget, closing the gap the per-plugin budget tests above
  don't cover.
- Phase 7 Plan items remaining: `agent-worktrees`' own create/resume/
  `sessionStart` wiring -- the actual consumer of everything built across
  slices 1-4 -- is the last unstarted item.

### 2026-09-30 (cont.) -- Phase 7 slice 3: the per-file "prefer local" preamble
- Picked up the next unstarted Plan item (previous entry's steer): extended
  `render_projection` (`customizing-copilot:reviewing-customizations`'s
  `instruction_projections.py`) to build, inline, a short blockquote
  inserted between the provenance marker and the template's own rendered
  body on **every** projection, repo-wide -- no per-template opt-in, so the
  common case (a source that has synced in at least once) self-directs to
  its own gitignored `*.local.instructions.md` sibling with no further
  lookup, per
  `docs/patterns/worktree-scoped-dynamic-guidance.md` §2. The preamble
  names the sibling via the destination's own `local_sibling_destination()`
  basename rather than assuming `<sourceId>.instructions.md` naming (the
  pattern doc's illustrative wording) -- `source_id` and the declared
  destination filename are independent in the schema, even though every
  shipped projection today happens to name them identically.
- **Wide blast radius, confirmed:** this widens every rendered projection's
  byte count repo-wide, exactly as flagged when this item was carved out of
  slice 2. Two shipped templates were within the new preamble's width of
  `MAX_PROJECTION_BYTES` (4096) and tipped over:
  `agent-worktrees`'s `head-claim-fallback.instructions.md` (was already
  within ~150 bytes of the ceiling) and `context-handoff`'s
  `handoff-fallback.instructions.md` / `awareness.instructions.md`. Rather
  than raising the shared budget (a separate, larger decision affecting
  every other projection's own headroom), trimmed genuinely redundant
  content in those three files -- a recap `## Rules` section in
  `head-claim-fallback` that only restated points already made earlier in
  the same file, a duplicate "no auto-pickup" sentence plus its own
  trailing `## Rules` recap in `handoff-fallback` (the same message is
  already carried by `context-handoff`'s own always-loaded
  `awareness.instructions.md` and by the `efforts` completion-gate
  instruction), and several over-verbose command-table cells in
  `awareness.instructions.md` -- until `test_shipped_projections_fit_the_
  budgets` passed again for all three. No meaning was dropped, only
  restated or over-elaborated phrasing.
- Audited every other shipped projection across the repo (a direct script
  measuring `render_projection` byte counts for every `_PLUGINS` entry in
  `test_shipped_projection_budgets.py`): only those same three were within
  reach of the ceiling; the preamble's added ~175-180 bytes is otherwise
  comfortably absorbed everywhere else.
- `tools/run-plugin-tests.py customizing-copilot`: full suite green,
  including `test_shipped_projection_budgets.py` (17/17 passed). Also ran
  `agent-worktrees`'s and `context-handoff`'s own suites to confirm the
  trimmed instruction files didn't break anything there --
  `agent-worktrees`'s own suite is large enough to hit the test-supervisor's
  wall-clock cap on an unrelated later sub-suite (two full sub-suites
  passed first, 536 + 715 tests); `context-handoff`'s `test_emit_guidance.py`
  fails in this environment because `bash.EXE` isn't a real POSIX shell
  here (confirmed pre-existing via `git stash`) -- neither is caused by
  this change.
- `instruction_projections.py` was already at its grandfathered
  `tools/module-size-baseline.json` ceiling (2418 lines, 1 line of slack)
  before this change. Inlined the new preamble directly into
  `render_projection` (no separate helper function) to minimize the net
  addition, then made the remaining growth (2418 -> 2429, 11 lines) a
  **deliberate, reviewed baseline widen** per `check-module-size.py`'s own
  documented policy for this exact case -- real new capability, not
  unreviewed drift. Splitting this module further is out of scope for this
  slice; it already shares `render_projection`'s/`local_sibling_
  destination`'s spec-loading and atomic-write primitives directly, per the
  earlier render-only-entry-point Plan note. Review round 1's own
  self-reference fix (below) needed a second, smaller widen (2429 -> 2439)
  for the same documented-policy reason -- a real bug fix, not bloat.
- Phase 7 Plan items remaining: the repo-wide catch-all static projection
  and the `agent-worktrees` wiring (the actual consumer of everything
  built so far) -- each its own separately-reviewed PR per the standing
  steer.
- **Review round 1 findings (PR #4793), all addressed in the same PR:**
  - **Medium, genuine pre-merge bug:** `render_local_cache` re-renders the
    same checked-in-destination `spec` to produce the local cache file's
    own content (`render_projection(spec)`, written straight to the
    `*.local.instructions.md` path) -- so the just-landed preamble made
    every local cache file self-referential, naming and preferring its own
    path. Fixed by giving `render_projection` an `include_prefer_local`
    keyword (default `True`); `render_local_cache` now passes `False`,
    since the local cache file already *is* the fresher content and must
    never point at itself. Added
    `test_render_projection_omits_preamble_for_local_cache_rendering` and
    updated the existing byte-equality/marker-reuse tests that previously
    compared against a bare `render_projection(spec)` call.
  - Corrected this Journal and the Plan item above, which wrongly named a
    `_local_cache_preamble` helper -- the logic is inline in
    `render_projection`, not a separate function (the module-size slack
    this slice had to work within ruled a separate helper out; see above).
  - Restored `handoff-fallback.instructions.md`'s "no auto-pickup, claim
    tracking, or supersession" warning on the manual hand-write-the-file
    path, which the prior trim had dropped entirely rather than just its
    redundant `## Rules` recap -- the only other copies live in
    `awareness.instructions.md` and the `efforts` completion-gate
    instruction, neither of which is guaranteed to load in *this* file's
    own failure mode (no `context-handoff` extension at all). Found a few
    more bytes of genuinely redundant phrasing elsewhere in the same file
    to restore it within `MAX_PROJECTION_BYTES`.
  - Added this PR's required **Documentation impact** statement in the PR
    description; `docs/patterns/worktree-scoped-dynamic-guidance.md`
    already described this exact preamble design and needed no change.

### 2026-09-30 -- PR #4683 diagnosis and merge
- After 7 consecutive `COMMENTED` review passes on PR #4683, diagnosed the
  actual root cause rather than continuing to fix indefinitely: all 7
  passes ran against the **same unchanged commit** (no intervening push),
  ~20-40 minutes apart -- a re-review-loop artifact, not fresh regressions.
  Checked every distinct concern raised across all 7 passes against current
  HEAD: the lock/stale-source race, git isolated-env + literal-pathspecs,
  tri-state tracked/inconclusive handling, discovery-exception handling,
  the SKILL.md lock-contradiction wording, and a docstring review-history
  reference were **all already resolved** in the pushed code (rounds 1-7's
  fixes held) -- the reviewer was simply restating stale findings with no
  memory of prior verdicts, because nothing new had been pushed to
  re-scope its audit.
- One concern across the 7 passes was genuinely still open: the checked-in
  orphan scanner (`_iter_projection_files`) excludes
  `*.local.instructions.md` by suffix unconditionally, without verifying
  the adopting repo has actually added the `.gitignore` rule yet -- a
  stray committed file with that suffix (pre-adoption, or by accident)
  would be silently skipped by the scan forever. Real but low-severity
  (WARNING-class, reachable only if the ignore rule is skipped); logged
  here rather than folded into this already-large PR -- it belongs in the
  next slice (the `.gitignore` convention item below), keeping each PR
  reviewably small per the operator's explicit steer this session.
- Per this repo's ruleset (zero required approving reviews) and the
  `commented-review-verdict` fallback policy, a `COMMENTED` verdict is
  non-blocking by design -- posted a comment closing each stale thread
  with the above findings, then self-merged (`pr-merge 4683 --now`,
  Maintainer bypass) rather than continuing to wait for a clean pass that
  was never the actual merge gate.
- **Going forward:** subsequent Phase 7 slices land as separate, smaller
  PRs rather than growing `render_local_cache` further in one PR -- this
  slice's own 8-round history is the concrete case for that.

### 2026-09-30 (cont.) -- Phase 7 slice 2: `.gitignore` convention + orphan-scan hardening
- Picked up the gap PR #4683 deliberately deferred (previous entry): the
  checked-in orphan scanner's `*.local.instructions.md` suffix exclusion
  unconditionally trusted a repo having adopted the ignore rule, with no
  verification. Hardened `_iter_projection_files` to batch-query git
  tracked status (reusing `_resolve_git_tracked_paths`, the same primitive
  `render_local_cache` already uses for its own tracked-destination guard)
  for every `*.local.instructions.md` candidate found under
  `.github/instructions/`: a file is excluded from the scan unless
  `_resolve_git_tracked_paths` reports it tracked or inconclusive --
  including the plain-directory (no-`.git`) case, where tracking is simply
  inapplicable and the file is excluded without git confirming anything, so
  the long-standing no-git test still passes unchanged. In a real git
  working tree, a tracked file (or an inconclusive check -- git present but
  the query itself failed) is now scanned exactly like any other
  projection file and reported as `projection-orphan-file` if its
  provenance marker doesn't match the lock -- never silently hidden on the
  strength of a suffix alone.
- Added three tests mirroring `render_local_cache`'s own tracked/
  untracked/inconclusive coverage, but for the orphan-scan side:
  `test_orphan_scan_still_excludes_local_cache_file_in_an_untracked_git_repo`,
  `test_orphan_scan_flags_a_git_tracked_local_cache_file_instead_of_hiding_it`,
  and `test_orphan_scan_treats_inconclusive_git_check_as_not_excludable`.
  The existing no-git-at-all case
  (`test_render_local_cache_files_excluded_from_checked_in_orphan_scan`)
  still passes unchanged.
- Documented the `.gitignore` convention itself in the
  `setting-up-instruction-sync-worker` skill's scaffolding output (the
  other half of this Plan item): a new section not gated by the
  `projection-reflect.json` consent file, since `render_local_cache()`
  itself is permissionless by design (local, gitignored, no commit) --
  though its own `agent-worktrees` create/resume/`sessionStart` call sites
  are still a planned, unchecked Plan item below, not yet wired -- plus a
  `references/templates/gitignore-rule.md` template, so a repo gets the
  ignore rule scaffolded regardless of whether it also adopts the
  consent-gated scheduler/bypass pieces this skill otherwise scaffolds.
- `tools/run-plugin-tests.py customizing-copilot`: 297 passed, 8 skipped.
- Phase 7 Plan items remaining: the per-file "prefer local" preamble, the
  repo-wide catch-all static projection, and the `agent-worktrees` wiring
  (the actual consumer of everything built so far) -- each its own
  separately-reviewed PR per the prior entry's steer.
- **Review round 2 findings, addressed in the same PR (#4773) rather than
  a follow-up, since they were still cheap to fold in:**
  - Round 1 re-flagged 6 already-fixed findings as stale restatements
    against an unchanged commit (the same PR #4683 pattern) -- confirmed
    each was already addressed (manual version bump reverted + changefile
    added; Journal/docstring/SKILL.md/template wording corrected to stop
    implying the orphan scan's git-tracked check depends on the
    `.gitignore` rule, when `git ls-files` already excludes untracked
    files regardless of ignore matching; PR description given its
    required Documentation impact statement).
  - Round 2 found one genuine, pre-existing bug the new orphan-scan call
    site exposed: `_resolve_git_tracked_paths`'s initial `rev-parse` probe
    conflated "confirmed not a git repo" with "the probe itself failed to
    complete" (both returned `inconclusive=False`), so a transient
    probe failure inside a *real* git repo could make a tracked
    `*.local.instructions.md` file silently excluded from the orphan scan
    -- the exact "fail open" `render_local_cache`'s own tracked-destination
    guard was built to avoid. Fixed by distinguishing `FileNotFoundError`
    (genuinely no `git` binary -> still `inconclusive=False`, preserving
    the plain-directory case) from every other probe failure (now
    `inconclusive=True`, treated conservatively like the existing
    tracked-files-query failure case). Added
    `test_orphan_scan_distinguishes_a_failed_probe_from_a_confirmed_non_repo`
    and `test_resolve_git_tracked_paths_treats_missing_git_binary_as_not_applicable`.
  - Round 2 also found the `.gitignore` section's "permissionless" framing
    conflated two different things: `render_local_cache()`'s local,
    gitignored write is genuinely permissionless, but *committing* a
    `.gitignore` rule is an ordinary repo mutation
    (`docs/patterns/install-vs-adopt-boundary.md`) that still needs the
    adopting repo's own ownership/contribution-flow authority -- it just
    doesn't need *this skill's specific* `projection-reflect.json` consent
    file. Reworded the SKILL.md section and the template accordingly.
  - Round 2 also flagged a test docstring citing PR #4683 by number
    instead of describing the timeless invariant under test -- reworded.

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
  orchestration half is complete, tested (35 tests across
  `test_projection_reflect.py` + `test_projection_sync_worker.py`), and
  merged. Only the deep byte-exact recompute-against-a-pinned-artifact
  verification remains open, gated on the still-missing marketplace-source
  commit resolver (#3132) -- unchanged from the prior entry's assessment.

### 2026-09-21 (cont.) -- Partial immutable-pin resolver (issue #3132, half closed)

Took on the still-open half of the immutable-pin gap directly, but scoped
honestly rather than guessing at the full answer.

- Confirmed via `scan_plugin_sources.py`'s existing code (not a new
  investigation -- this was already documented) that the two candidate
  sources genuinely differ: a self-hosted/directory-marketplace plugin's
  `payload_root` lives inside this repo's (or an `agent-worktrees-repo`'s)
  real git working tree, while an externally-installed marketplace
  plugin's `installed_root` footprint is a plain file copy with no local
  git history at all.
- Added `_plugin_commit(footprint)`: resolves `git rev-parse HEAD` inside
  `footprint` (non-blocking, 5s timeout, validates the 40-hex SHA shape),
  returning `""` -- never raising -- for every case with no trustworthy
  commit (git unavailable, not a git working tree, command failure).
  Wired into `PluginSource.commit` at both `assemble_enabled_plugins`
  construction sites and `_sources_from_raw_dir`.
- Added `resolve_pinned_commits(sources) -> dict[str, str]`: builds the
  `"<plugin>@<marketplace>" -> commit SHA` map `projection_reflect.
  bypass_decision`'s `pinned_commits` parameter already accepted (landed
  2026-09-20) -- a source with no resolved commit is simply absent, so the
  existing fail-closed conjunct treats it as unpinned rather than this
  resolver inventing an answer for it.
- **Deliberately did not make pin enforcement unconditional.** Wiring
  `resolve_pinned_commits()`'s output into every real CLI run by default
  would have silently made *every* externally-installed-marketplace sync
  permanently review-only (since that source can never be pinned today) --
  a major, undiscussed behavior regression for the exact adopter case this
  whole effort's audit was motivated by. Instead extended
  `projection_reflect_consent.Consent` with an optional
  `requireImmutablePin` (default `false` when absent, strictly validated
  as a real boolean when present -- a non-bool value fails the whole
  consent file closed rather than guessing an interpretation): the CLI
  only computes and enforces pins when a repo's own committed consent
  explicitly opts in.
- 13 new tests: `_plugin_commit` resolves inside a real git checkout /
  empty outside one / empty when git is unavailable;
  `resolve_pinned_commits` includes only resolved sources (in
  `test_scan_customizations.py`); `Consent.require_immutable_pin` defaults
  false / honors an explicit `true` / rejects a non-bool value (in
  `test_projection_reflect_consent.py`); and three CLI-level tests proving
  the wiring (default ignores the requirement, `true` blocks an unpinned
  source, `true` permits a pinned one, in `test_projection_sync_worker.py`).
  Updated `reviewing-customizations/SKILL.md` and
  `setting-up-instruction-sync-worker/SKILL.md` (including its consent-
  schema example and scheduler-config description, which still described
  hand-rolling `sync`/`scan`/`bypass_decision` rather than calling
  `run_sync_pass()` -- fixed alongside). Two review rounds on PR #3159
  caught real gaps in the resolver itself, fixed in the same PR: a dirty
  or untracked payload could still receive a valid-looking pin (added
  `_payload_is_clean`, extended to `--ignored` files in round 2 since
  plain `--porcelain` omits them and the projection scanner does not
  consult `.gitignore`); the git subprocess inherited the ambient
  environment, so a caller-set `GIT_DIR`/`GIT_WORK_TREE` could redirect
  the probe at an unrelated repository (added `_git_isolated_env`); an
  installed-plugins footprint (a copied external payload) could still get
  pinned if it happened to sit under an unrelated enclosing git checkout
  (scoped `_plugin_commit` calls to the directory-marketplace branch only,
  in both `assemble_enabled_plugins` and `_sources_from_raw_dir`); and the
  scaffolder template called `run_sync_pass()` without ever resolving or
  passing `pinned_commits`, so a scaffolded scheduler would silently drop
  pin enforcement even with `requireImmutablePin: true` set (wired
  `resolve_pinned_commits()` through, gated on the same consent flag).
  A **third** review round caught a genuine TOCTOU race across all of the
  above: the clean check and `rev-parse HEAD` were two separate
  subprocesses with no shared lock (fixed by reading `HEAD` before *and*
  after the clean check and requiring agreement), `git status --porcelain`
  even with `--ignored` still respects a local `status.showUntrackedFiles`
  config that could hide a new file (fixed with
  `--untracked-files=all`), and -- most substantively -- pins were resolved
  *before* `run_sync_pass`'s own `refresh`/lock, in both the CLI and the
  scheduler template, so a refresh or concurrent update could change a
  payload after its commit was captured while the stale-but-well-formed SHA
  still passed the pin conjunct. Closed by changing `run_sync_pass()`'s
  contract: it now takes a `resolve_pins` callback (in addition to the
  simpler precomputed `pinned_commits` for tests/resolver-free callers) and
  calls it itself, after `refresh` and inside its own held lock, immediately
  before the locked sync -- both the CLI and the scheduler template now
  pass `resolve_pinned_commits` as that callback rather than precomputing
  a map. **A fourth review round found this still incomplete**: calling
  the callback after refresh didn't help if the callback itself
  (`resolve_pinned_commits`) just read each source's already-populated
  `commit` field -- that field was captured at *discovery* time, before
  `refresh` even ran, so a directory-marketplace checkout that advanced
  during refresh still passed its stale SHA. Fixed by making
  `resolve_pinned_commits` **re-probe fresh at call time** instead of
  trusting the field: added `PluginSource.is_local_checkout` (set only for
  a genuine directory-marketplace footprint, never an installed-plugins
  copy) so the resolver knows which sources are even eligible to
  re-probe, and re-runs `_plugin_commit()` against each one's
  `payload_root` live. A **fifth** review round found `is_local_checkout`
  itself too permissive (`footprint is not None` alone -- a plain
  `directory` marketplace source is an arbitrary local path with no
  provenance guarantee, and could point at a payload copied into a
  subdirectory of some entirely unrelated git repository) and a
  fail-open gap (a custom `resolve_pins` returning `None` was
  indistinguishable from "no `pinned_commits` ever supplied", silently
  disabling the conjunct for a caller that had explicitly opted in).
  Fixed: `is_local_checkout` now requires `controlled` (this repo's own
  tree) or resolution via the trusted `agent-worktrees-repo` source kind;
  `run_sync_pass()` normalizes a `None` `resolve_pins` result to an empty
  map (fail-closed) rather than treating it as opt-out. Also fixed a
  broken markdown code span split across a newline in the scheduler-
  config recipe. `customizing-copilot`'s full suite: 235 passed, 8
  skipped (36 new tests total across all five rounds). `check-module-
  size`/`check-version-bump`/`check-version-consistency`/`check-docs-
  consistency` all pass. Bumped to `0.1.0-dev92`.
- **Issue #3132 resolved as scoped, not left open.** The operator made an
  explicit scope decision: copilot-extensions trusts *itself*
  (self-hosted/directory-marketplace sources) for this instruction-sync
  mechanism, but does not want that trust extended to externally-installed
  marketplace plugins generally -- instruction-projection is a narrow,
  bespoke workaround for the harness's own static-fallback problem, not a
  general-purpose provenance system worth the added network/auth/trust
  surface a marketplace-release-API lookup would require. The
  self-hosted/directory-marketplace resolver already ships
  (`resolve_pinned_commits()`, PR #3159) and is the complete, final answer
  for this effort's actual scope -- there is no remaining resolver work to
  build. #3132 closed accordingly; see its own closing comment for the
  operator's exact framing.

### 2026-09-21 (cont.) -- Closed out: sub-issues closed, scope decision on #3132

- Closed sub-issues #3071 (Phase 1), #3082 (Phase 2), and #3120 (Phase 3),
  each with a comment citing the PRs that resolved them -- all three were
  done and merged but had been left open. Posted a status-update comment
  on the umbrella #3033 (left open: it's the tracking parent, and the
  downstream private companion effort's Phase 0/4/5 are still pending
  there, out of this repo's scope).
- Asked the operator how to proceed on #3132's remaining externally-
  installed-marketplace half (a network-dependent marketplace-release-API
  lookup, or an install-time provenance record outside this repo's
  control) rather than unilaterally designing a network/auth surface after
  the six-round review cycle the self-hosted half already went through.
  **Operator's answer:** copilot-extensions is trusted for its own
  self-hosted sync, but that trust should not extend generally to external
  marketplaces -- instruction-projection is a bespoke workaround, not
  worth a general provenance system. Closed #3132 as scoped-complete
  rather than leaving it open pending further design.
- **Effort status:** every repo-scoped Plan/Validation Plan item this
  effort can carry is now resolved, transferred (downstream Phase 0/4/5),
  or explicitly scoped-closed (the externally-installed pin half). Left
  `Status: Active` rather than `Done` pending the downstream companion
  effort and the umbrella issue's own closure -- not a false completion
  claim; see the umbrella issue for the durable pointer to what remains.

### 2026-09-29 -- Carved Phase 7 from a downstream deployment's live findings

- Prompted by a private downstream consumer repository driving Phase 5
  (`projection-reflect` instantiation) to a real live deployment and
  forced-drift proof, tracked in that repo's own private tracking issue
  (not cited here, per this repo's identifier-neutrality rule): an
  ordinary contributor without push rights hit an error from a
  session-start mechanism attempting the sync on their behalf, and a
  separate architectural discussion (this repo has no visibility into that
  private conversation; only the resulting design is carried here)
  concluded that headless/cloud/sandboxed launch paths with no hook and no
  session-state folder need the checked-in floor to remain authoritative
  regardless, while every other launch path could reasonably do better.
- Re-read `docs/patterns/session-scoped-dynamic-guidance.md` before
  proposing anything new, per this repo's own root `AGENTS.md` orientation
  discipline -- confirmed its §2a rule already excludes large/stable
  projected content from the session-scoped file by design, so the right
  move is a sibling pattern at worktree granularity, not a literal reuse or
  a modification of the existing one.
- Filed [#4674](https://github.com/ThomasMichon/copilot-extensions/issues/4674)
  and added Phase 7 to this effort's Plan/Validation Plan (above): the
  `.gitignore`/`*.local.instructions.md` convention, the per-file "prefer
  local" preamble, the repo-wide catch-all for not-yet-synced sources, the
  shared render-only entry point (no git/PR side effects, ever), and the
  `agent-worktrees` create/resume + `sessionStart` wiring. Authored the new
  sibling pattern doc, `docs/patterns/worktree-scoped-dynamic-guidance.md`,
  and cross-linked it from `session-scoped-dynamic-guidance.md`, the
  `docs/patterns/README.md` index, and this vision's Reality docs.
- Not yet built: this entry records the design and its tracking issue only.
  The actual render-only entry point, the projection-template preamble
  change, the catch-all projection, and the `agent-worktrees` wiring remain
  open Plan items above, each its own worktree/PR per this repo's phase
  convention.

### 2026-09-29 (cont.) -- Phase 7 slice 1: the render-only entry point

- Landed `instruction_projections.render_local_cache()` and
  `local_sibling_destination()`, in the same module as
  `render_projection()`/`sync_repository` rather than a
  `projection_sync_worker.py` addition -- it reuses their spec-loading
  (`_load_specs`) and atomic-write (`_atomic_write`/
  `_prepare_destination_parent`) primitives directly, and deliberately does
  not compose through the sync/scan/decide pass (which exists to make a
  *checked-in, lock-verified* decision this consent-free, lock-free path
  has no need of).
- `render_local_cache(repo_root, sources)` loads specs for every enabled
  source, calls the existing `render_projection()` unchanged (no format
  change, no version bump -- this slice does not touch what a checked-in
  file looks like at all), and writes the identical rendered bytes to each
  source's `local_sibling_destination()` instead of its checked-in
  destination. No lock file, no git operation, no consent check, anywhere
  in this path.
- 4 new tests in `tests/test_instruction_projections.py`: the naming
  helper's happy path and both its error cases; a full render proving the
  sibling's content byte-matches `render_projection()`'s own output while
  the checked-in destination and the lock file are both left absent;
  running the whole thing against a `tmp_path` repo with **no `.git`
  directory at all**, twice, to prove no git dependency and idempotence;
  and a missing-repo-root case reporting a blocking finding without
  raising. `tools/run-plugin-tests.py customizing-copilot` -- 271 passed,
  8 skipped, no regressions.
- Documented the new function in `reviewing-customizations/SKILL.md` (a new
  "Worktree-scoped local cache" subsection, sibling to the existing
  `run_sync_pass()` writeup) and in
  `docs/patterns/worktree-scoped-dynamic-guidance.md`'s Exemplars section.
- **Deliberately scoped narrow.** This slice does not touch
  `render_projection()`'s own output (the per-file "prefer local" preamble
  is a separate, wider-blast-radius change -- it would add a line to every
  consuming repo's next sync PR across the whole marketplace -- left for
  its own reviewed slice), does not add the repo-wide catch-all projection
  (still needs a home: most likely scaffolded by
  `setting-up-instruction-sync-worker` alongside its other four scaffolded
  artifacts, or shipped via `agent-worktrees`' own existing projection
  declaration, since that plugin is the one actually calling this entry
  point -- not yet decided), and does not touch `agent-worktrees` at all.
  Still open Plan items: the `.gitignore` convention/documentation, the
  per-file preamble, the catch-all projection, and all of the
  `agent-worktrees` create/resume + `sessionStart` wiring.
- **Copilot review on PR #4683 caught five real gaps** in the first cut,
  all fixed in the same PR before merge:
  1. A source disabled/removed/unloadable between two calls left its old
     `.local.instructions.md` on disk forever, silently overriding the
     checked-in fallback indefinitely once the planned catch-all/preamble
     prefer it. Fixed with a reconciliation pass that removes any existing
     local-cache file no longer backed by a currently-valid spec --
     **but only if it still carries `render_projection()`'s own provenance
     marker**, so an unrelated file that happens to match the naming
     convention is never touched.
  2. Two sourceIds within the same plugin declaring the identical checked-in
     destination (a copy-paste duplicate -- `_load_specs()` doesn't dedupe
     that, only `_find_spec_conflicts()` does, and this path never called
     it) converted to the same local-cache sibling and would have been
     resolved by silent last-writer-wins. Fixed by grouping specs by their
     portable local-cache path before writing anything; an ambiguous group
     is reported (`projection-local-cache-ambiguous`) and skipped entirely,
     while every unambiguous source still renders.
  3. Every call rewrote and fsynced every destination even when the bytes
     were already identical, churning mtimes/watchers and reporting a
     no-op as `changed`. Fixed by comparing on-disk bytes first and
     recording an identical destination in `unchanged` (mirroring
     `sync_repository_locked`'s own convention) instead of rewriting it.
  4. **The most serious one:** the local-cache sibling still ends in
     `.instructions.md` and still carries the full provenance marker (since
     it reuses `render_projection()`'s bytes verbatim), so the *checked-in*
     scan's own orphan detector (`_scan_orphan_files`, via
     `_iter_projection_files`) found it, flagged it
     `projection-orphan-file`, and `projection_reflect.classify_findings()`
     routes any check name outside its narrow `PLAIN_DRIFT_CHECKS`
     allowlist to conflict-dispatch by default -- meaning the mere
     *presence* of a local cache would have forced every future ordinary
     sync pass into false conflict-dispatch for any repo that adopted this
     mechanism. Fixed by excluding `*.local.instructions.md` from
     `_iter_projection_files` outright (its only caller), with a regression
     test proving a healthy synced repo plus a rendered local cache scans
     clean.
  5. The render-only path skipped both budget checks `sync_repository`
     enforces (`MAX_PROJECTION_BYTES` per file, the aggregate ceiling),
     so an installed template the checked-in sync would itself refuse
     could still become preferred local guidance. Fixed by checking both
     before writing -- an over-budget source is reported
     (`projection-local-cache-budget`) and skipped, never silently
     promoted, while unrelated valid renders still proceed.

  6 new tests added for these (10 total for this function); full suite
  still 277 passed, 8 skipped. Widened `instruction_projections.py`'s
  module-size baseline a second time (1871 -> 2007) to cover the fixes --
  splitting the module was reconsidered and rejected again for the same
  reason as the first widening (shared private helpers, no existing
  cross-module-private-import precedent in this skill's script set).

  **Round 2 (same PR, re-review after pushing round 1's fixes) found six
  more real gaps**, all fixed before merge:
  1. The write path replaced an *existing* file at the local-cache path
     without checking whether it was this renderer's own prior output --
     an unmarked file a user happened to leave at that exact path would
     have been silently overwritten, contradicting the stale-cleanup path's
     own promise never to touch an unmarked file. Fixed with a shared
     `_owns_local_cache_file()` helper both the write path and the cleanup
     path now call: true only when a marker parses *and* its own recorded
     checked-in `destination` converts (via `local_sibling_destination()`)
     to exactly the path in question. An existing-but-foreign file is now
     reported (`projection-local-cache-foreign`) and left alone, other
     sources still render.
  2. That same marker-destination check closes a subtler cleanup gap too:
     a marker-bearing file *copied* to the wrong `*.local.instructions.md`
     path (identical bytes, valid marker, wrong location) would previously
     have been deleted as "stale" even though this renderer never put it
     there. Now the marker's own destination must match the file's actual
     path, not just parse successfully.
  3. Duplicate `source_key`s declaring *different* destinations weren't
     caught at all (only same-destination collisions were) --
     `_load_specs()` already reports `projection-duplicate-source` for this
     shape but doesn't drop the specs. Fixed with an explicit pre-pass that
     rejects every spec sharing a repeated source key before the
     destination-collision grouping even runs.
  4. A malformed aggregate-budget config (`_load_aggregate_budget` itself
     reporting a blocking `projection-config` finding) still let this path
     proceed and publish caches under the silent fallback default --
     `sync_repository` refuses to write anything in that state. Fixed: a
     budget-config failure now stops the whole call before any write.
  5. The write loop's exception handler caught only `OSError`, but
     `_prepare_destination_parent()` raises `ValueError` for a
     symlink/reparse-point destination -- one unsafe destination would have
     crashed the entire call instead of reporting a finding and letting
     unrelated sources still render. Fixed by catching both.
  6. A symlinked/junctioned `.github` itself (not just `.github/instructions`,
     which was already checked) could let the walk -- and the stale-cleanup
     unlink -- reach files outside the repository. Fixed by rejecting
     `.github` indirection too, before ever descending into it.

  4 more tests added (14 total for this function, including a
  copied-marker-at-the-wrong-path regression for #2 and a
  foreign-file-on-write regression for #1). Full suite: 281 passed, 8
  skipped. Widened the module-size baseline a third time (2007 -> 2092).
  **Flagging for a future cleanup pass, not blocking this PR:** three
  widenings in one PR (1806 -> 2092, +286 lines / ~16%) is exactly the kind
  of organic growth `harness-guidance`'s `bounded-source-modules` behavior
  warns about even when each individual widening was reviewed and
  justified in the moment; this module is a reasonable candidate for an
  actual split once the code stabilizes, rather than continuing to widen
  its ceiling indefinitely.

  **Round 3 (same PR, re-review after round 2's fixes) found five more real
  gaps**, all fixed before merge:
  1. The malformed-budget-config early return also skipped stale-cache
     reconciliation, reintroducing the exact "stale sibling silently
     preferred forever" defect round 1 had already fixed, for this one
     edge case. Fixed: a budget-config failure now empties the *accepted*
     (write) set only -- reconciliation still runs, so a source that's
     genuinely gone still gets its cache removed even under a broken
     config.
  2. The existing-file read for the idempotence/foreign-file check used
     `_current_regular_bytes()` (an unbounded `path.read_bytes()`) -- a
     large foreign or damaged file at a local-cache path could be loaded in
     full every refresh despite fresh renders being capped at
     `MAX_PROJECTION_BYTES`. Fixed with a new bounded reader
     (`_read_existing_local_cache()`) that raises rather than fully
     reading an oversized/unsafe file, reported and skipped like any other
     write failure.
  3. No test actually exercised a real write failure (only the
     never-reaches-the-write-loop missing-root case was covered). Added a
     monkeypatched `_atomic_write` failure on one of two sources, proving
     the other source still renders and no partial file is left behind.
  4. `_owns_local_cache_file()` (path-based ownership) was also being used,
     unchanged, to gate the *write* path's overwrite decision -- but it
     never compared the marker's own `plugin`/`sourceId` against the
     *current* spec trying to publish there, so a sourceId rename sharing
     the same destination would have silently inherited the previous
     source's marker identity. Split the concern: renamed the path-only
     check to `_parse_owned_local_cache_marker()` (still what stale
     cleanup uses, deliberately unchanged there), and the write path now
     separately compares `plugin`+`sourceId` against the current spec --
     a mismatch is a legitimate handoff (the destination-collision
     grouping already guarantees at most one *current* spec claims any
     given destination), so it still supersedes the old content rather
     than refusing forever, but now reports it
     (`projection-local-cache-handoff`) instead of doing it silently.
  5. (Folded into #2's fix rather than a separate change.)

  5 more tests added (18 total for this function, plus one covering the
  reconciliation-survives-a-broken-budget-config case together with #1).
  Full suite: 285 passed, 8 skipped. Widened the module-size baseline a
  fourth time (2092 -> 2132) -- the split-candidate flag from round 2
  stands, unchanged.

  **Round 4 (same PR, re-review after round 3's fixes) found three more
  real gaps**, all fixed before merge:
  1. `valid_destinations` was computed *before* the write attempt, from
     every accepted spec -- so a destination whose refresh failed
     mid-write was still marked "valid" and the reconciliation pass left
     its stale prior content in place, looking current. Fixed: build
     `valid_destinations` incrementally, adding a destination only on
     confirmed success (unchanged, or an actual successful write) -- a
     failed refresh is no longer marked valid, so the existing
     reconciliation pass (unchanged otherwise) now removes the owned,
     now-stale prior sibling instead of leaving it to masquerade as
     current, and still reports if that removal itself fails.
  2. `_load_specs()` never rejected a checked-in *declared* destination
     literally named `foo.local.instructions.md` (only the general
     `.instructions.md` suffix was checked), but the checked-in orphan
     scan's `*.local.instructions.md` exclusion (round 1's fix) would have
     hidden such a file from orphan detection even though it's a real,
     locked, checked-in projection. Fixed at the source: declaration
     validation now rejects the reserved suffix outright, so the orphan
     scan's exclusion is exhaustively safe by construction rather than
     merely "safe in practice."
  3. The module's own top docstring claimed it "never removes repository
     files," no longer accurate now that stale-cache reconciliation exists.
     Clarified: checked-in projections and the lock are never removed;
     only an owned, gitignored, worktree-local cache sibling can be
     reconciled away.

  2 more tests added (20 total for this function). Full suite: 287
  passed, 8 skipped. Widened the module-size baseline a fifth time (2132
  -> 2161).

  **Round 5 (same PR, re-review after round 4's fixes) found one more real
  gap, and it was the one that mattered most for correctness under real
  concurrency:** nothing serialized two overlapping calls to
  `render_local_cache()` itself. The planned callers (worktree
  create/resume, `sessionStart`) can genuinely race each other for the same
  repository -- an older call could finish its render/write after a newer
  call already published fresher content, silently clobbering it and both
  reporting success; the stale-cleanup pass could likewise unlink a
  sibling another call had just refreshed. Fixed by splitting the function
  the same way `sync_repository`/`sync_repository_locked` already split
  (thin public wrapper resolves the root and acquires a lock; a private
  `_render_local_cache_locked()` does the actual work) -- but with a
  **dedicated lock** (`_local_cache_lock()`), deliberately separate from
  the checked-in sync's own, so this permissionless path never contends
  with or blocks behind the privileged checked-in worker. A concurrent
  attempt fails closed with a `projection-local-cache-lock` finding and
  touches nothing, mirroring the exact test shape
  `test_run_sync_pass_reports_conflict_when_lock_already_held` already
  established for the checked-in sync's own lock.

  1 more test added (21 total for this function). Full suite: 288 passed,
  8 skipped. Widened the module-size baseline a sixth time (2161 -> 2219).

  **Round 6 (same PR, re-review after round 5's fixes) found four more real
  gaps**, all fixed before merge:
  1. **The deepest one:** the round-5 lock serialized file *operations*,
     but not which call's *source set* was authoritative -- an older call
     holding a precomputed/snapshotted source list (e.g. captured before
     some plugin was enabled) could still acquire the lock *after* a newer
     call already published that plugin's cache and released it, then
     delete the newer cache as "stale" using its own outdated view, both
     calls reporting success. Moving `list(sources)` inside the lock would
     not have fixed this -- the staleness is in what the *caller* computed
     before ever calling in. Fixed by changing the second parameter from a
     precomputed `sources: Iterable[object]` to a
     `discover_sources: Callable[[], Iterable[object]]` resolver, invoked
     only *after* the lock is acquired -- no call can now act on a source
     set a more recent call has already superseded. All 21 existing test
     call sites updated (`[source]` -> `lambda: [source]`); 2 new tests
     prove the resolver is never invoked at all when the lock is
     contended, and is invoked exactly once when it isn't.
  2. The checked-in orphan scan's `*.local.instructions.md` exclusion
     (round 1) assumes the `.gitignore` rule this whole mechanism depends
     on is actually in place -- but that rule is still its own, separate,
     not-yet-built Plan item. A repo that hasn't adopted it yet (or a file
     committed before it was) could end up with a *tracked*
     `.local.instructions.md` file permanently invisible to orphan
     detection. Fixed by verifying, not assuming: `render_local_cache()`
     now checks (best-effort, via `git ls-files --error-unmatch`, fail-open
     when git is unavailable so the no-`.git`-dependency guarantee holds)
     that a destination is not git-tracked before writing *or* deleting it
     -- a tracked destination is reported (`projection-local-cache-tracked`)
     and left completely alone either way.
  3. `SKILL.md` claimed "no lock file" and "no lock needed," contradicting
     round 5's own `_local_cache_lock()`. Corrected to describe the
     dedicated lock, its contention behavior, and that callers must handle
     a locked-out result rather than assume every call refreshes the cache.
  4. The docstring's "each earned through review" sentence pointed at the
     Journal for chronology that doesn't belong in lasting API
     documentation (`REVIEW.md`'s timeless-documentation rule). Removed;
     the docstring now states only current guarantees.

  4 more tests added (25 total for this function). Full suite: 292 passed,
  8 skipped. Widened the module-size baseline a seventh time (2219 ->
  2301).

  **Round 7 (same PR, re-review after round 6's fixes) found five more
  real gaps, refining round 6's own git-tracked check**, all fixed before
  merge:
  1. The tracked-status probe used `-C root` alone, which an inherited
     `GIT_DIR`/`GIT_INDEX_FILE` can override, and a plain `ls-files` treats
     a filename containing pathspec magic characters (`[`, `]`, `*`, ...)
     as a glob rather than a literal name -- either could report a truly
     tracked file as untracked. Fixed with a git-isolated environment
     (`_git_isolated_env()`, mirroring `scan_plugin_sources.py`'s own) and
     `--literal-pathspecs`.
  2. **Fail-open was backwards for this specific check.** Treating "can't
     tell" the same as "confirmed untracked" (round 6's own shape) lets
     this renderer overwrite or delete a tracked file the moment the probe
     itself merely fails (a timeout, a corrupted index) inside a real git
     working tree. Redesigned as tri-state: `_resolve_git_tracked_paths()`
     now distinguishes "not a git working tree at all" (proceed -- the
     documented plain-directory case) from "a git working tree whose query
     failed" (refuse, exactly like confirmed-tracked) from "confirmed
     tracked"/"confirmed untracked".
  3. `discover_sources()` can itself raise (e.g.
     `discover_enabled_sources` -> `validate_committed_settings` on
     malformed repository settings) -- that exception was escaping this
     best-effort entry point uncaught. Fixed: caught under the lock,
     reported as a blocking `projection-local-cache-discovery` finding,
     existing cache files left untouched for that call.
  4. One `git` subprocess pair per candidate path (a write check plus a
     separate cleanup check) meant a large plugin stack paid repeated
     process-startup cost, and a slow probe could accumulate multiple 5s
     timeouts in one call. Fixed by resolving tracked status **once per
     call**, batched over every candidate path (both the accepted writes
     and the existing local-cache files found for cleanup) in a single
     `git ls-files` invocation.
  5. The docstring's "never touching... git in any way" claim was already
     inaccurate the moment round 6 added read-only tracking queries, and
     the `*.local.instructions.md` suffix is not actually gitignored until
     an adopting repo has adopted that still-separate, not-yet-built
     convention. Corrected to describe the real boundary: no checked-in or
     git-*mutating* action, read-only tracking queries when available.

  3 more tests added (28 total for this function): an inconclusive-check
  refusal (a monkeypatched timeout on the second of two git calls), a
  discovery-exception report, and reuse of the existing tracked/foreign
  regressions to prove the batched design didn't regress them. Full suite:
  294 passed, 8 skipped. Widened the module-size baseline an eighth time
  (2301 -> 2418). **Seven review rounds, 29 total findings, all fixed in
  this one PR** -- the split-candidate flag from round 2 stands, more
  strongly than ever.


