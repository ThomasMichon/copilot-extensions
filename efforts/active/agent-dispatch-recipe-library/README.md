# agent-dispatch recipe library (registrar `extends:` unification + named recipes)

- **Slug:** `agent-dispatch-recipe-library`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-30
- **Status:** Active (Phase 9 done; Phase 2 -- the Gitea backlog provider
  and reviewer adapter -- is now fully closed, merged via PR #5414
  2026-10-06; Phase 3's own tracked single-emitter-primitive follow-on
  slice remains, explicitly out of this effort's own scope. Of this
  effort's own Validation Plan: each Phase's own tests independently, the
  real consuming-repo migration, and the recipe README cross-check are all
  done (2026-10-06); a clean full-suite run and the live fixture
  exercising each of the 4 named recipes end-to-end both remain open)
- **Vision:** `visions/plugins/agent-dispatch/README.md` (§*The recipe*)
  advances *loop-recipes* from "four fixed archetypes, hand-declared per
  consumer" to "named, extendable templates a consumer instantiates with a
  handful of params"; `visions/plugins/agent-dispatch/repository-issue-loop/README.md`
  and `visions/plugins/agent-dispatch/reviewer/README.md` now reflect the
  realized `extends:` model, the four named recipe instantiations, the
  realized GitHub + Azure DevOps + Gitea backlog-provider surface, and the
  realized reviewer-side provider neutrality (GitHub + Azure DevOps + Gitea
  read-side observation all realized; webhook / direct-action parity
  intentionally narrower for every provider).
- **Umbrella issue:** #4691 (claimed and expanded by this effort — was an
  unplanned placeholder for the `extends:` model alone; this effort's scope
  also covers the provider-adapter gap and four new named recipes, all
  surfaced in the same design conversation)
- **Sub-issues:** _TBD, one per Plan phase once filed_
- **Full design context:** the `extends:`/recipe-taxonomy critique that
  seeded #4691 is Round 1 of
  [`efforts/active/task-verification-gate/inception-transcript.md`](../task-verification-gate/inception-transcript.md)
  — read it before starting design work here (per #4691's own instruction);
  this effort does not re-quote it in full.
- **Related:** [`agent-dispatch-recipe-composability`](../agent-dispatch-recipe-composability/README.md)
  (#4959) builds on this effort's `extends:` resolution mechanism to
  generalize it (any already-resolved declaration as a base, chaining,
  script-path-hook override values) — a distinct, later-starting effort,
  not a duplicate of this one's scope.

## Guiding Intent

Today, adopting agent-dispatch for a new kind of standing work (a reviewer
pool, a backlog triage loop, a one-off script sweep) means either hand-writing
a full `kind: reviewer-loop` / `kind: repository-issue-loop` JSON/YAML
declaration with every param spelled out, or — worse — a private, per-repo
script (a "worker_guidance" prose blob, a bespoke Python driver script) that
duplicates logic agent-dispatch should own generically. A consumer should
instead be able to write a handful of lines that **extend** a named, shared
recipe and fill in only what's genuinely repo-specific (target, venue, lane
count, filter criteria) — never re-derive or re-paste the loop shape, the
prompt, or the evaluator logic. This effort delivers that `extends:` model,
closes the GitHub-only provider gap the existing vision already declares as
should-be, and ships four new named recipes (backlog-triager,
issue-reproducer, effort-builder, effort-driver) that make the full spread of
common standing-work shapes — not just "review a PR" — turnkey-adoptable the
same way.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions (this repo) | `extends:` registrar model, provider adapters, four new named recipes, reviewer-recipe delta | worktree PRs against `dev` |
| A consuming repo's own registrar declarations (e.g. a private `dotfiles`/harness repo) | Adopts the new `extends:` model once it lands and is promoted; migrates off any private custom scripts | the consumer's own private effort, linked back here, not tracked in this repo |

## Coordination

- **Topology:** independent per-phase PRs against `dev`, each phase
  independently reviewable and mergeable where the dependency graph allows
  (provider adapters and the `extends:` model are prerequisites for the four
  named recipes; the reviewer-recipe delta is independent of all of them).
- **Host (owns this repo's PRs):** copilot-extensions worktree sessions.
- **Delegates:** none currently.
- **Handoff:** this effort's Journal records when each phase merges and
  promotes; a consumer's linked private effort (e.g. a harness/dotfiles
  repo's own tracking effort) starts adopting once it has.

## Context

- **`task-verification-gate`** (#4666, plan merged via PR #4692,
  implementation landing via PR #4709) is a **prerequisite this effort
  builds on, not something it re-implements**: every new recipe's evaluator
  uses `require_verification` + the `Complete`/`Abandon` decision vocabulary
  + the trusted script-evaluator kind that effort ships, rather than a
  bespoke per-recipe completion mechanism.
- **Four recipe archetypes already ship** (`visions/plugins/agent-dispatch/README.md`
  §*The recipe*, `plugins/agent-dispatch/README.md` §*Recipes (loop
  archetypes)*): **reviewer**, **conflict-resolution**, **goal-driven**, and
  **repository-issue-loop** (a goal-driven specialization for an entire
  backlog). `agent-dispatch recipes list/describe/render/kick/drive` is a
  working CLI today. This effort does **not** add a fifth archetype engine;
  it adds the `extends:` templating layer over these four, closes their
  GitHub-only provider gap, and ships four *named, canned instantiations*
  (see Plan) that parameterize `repository-issue-loop`/`goal-driven` rather
  than inventing new engines.
- **The registrar's current taxonomy conflates trigger mechanism with
  template-completeness** (#4691's own framing): `emitter` is a fully
  self-contained producer; `reviewer-loop`/`repository-issue-loop`/
  `plugin-companion` are "unfinished emitters" needing a repo-provided
  registrar to finish them out. The settled direction (Round 1 of
  `task-verification-gate`'s inception transcript): one producer primitive
  (`emitter`), with schedule/webhook/websocket as emitter *triggers*, and an
  `extends:`-based registrar template model (global/plugin/repo/cross-repo
  recipe references + param overrides) replacing the separate `kind`
  schemas. A consuming repo's declaration becomes, in the common case, pure
  YAML: a ref to the shared recipe, the target repo, a schedule, and pool
  criteria — no extra script.
- **Provider adapters are GitHub-only today**, despite
  `visions/plugins/agent-dispatch/repository-issue-loop/README.md`'s
  `provider-neutral-backlog-capability` and
  `visions/plugins/agent-dispatch/reviewer/README.md`'s
  `provider-neutral-review-capability` already declaring should-be
  provider-neutrality. No Azure DevOps or Gitea backlog/forge adapter exists
  in `plugins/agent-dispatch/src/agent_dispatch/` yet. Closing this gap is
  this effort's Phase 2, not a re-litigation of whether it should exist —
  the vision already settled that.
- **Motivating operational finding** (from a private consumer's own
  operational audit, generalized here): a machine running several
  hand-written `repository-issue-loop` declarations found each one carrying
  a multi-thousand-word inline `worker_guidance` prompt string duplicated
  near-verbatim across near-identical lanes, plus a private Python script
  (`ado_repro_loop.py`) standing in for an Azure DevOps backlog source no
  upstream adapter yet provides. That consumer's own tracking is a private
  effort linking back here; the general-purpose gap it exposed — no
  `extends:` model, no ADO adapter, no named triage/repro/effort recipes —
  is this effort's actual scope.
- Related, narrower effort: `review-automation-reliability` (#2357/#2423)
  and its completed ancestor `turnkey-reviewer-loops` already hardened the
  reviewer recipe's reliability; this effort's reviewer-facing phase (4) is
  scoped to the genuine remaining delta (provider adapters, a
  **configurable** stale-exit parameter, confirmed verification-gate
  wiring), not a repeat of that work.

## Request

Captured close to verbatim from the operator's own framing (generalized to
remove any private-consumer identifiers, per this repo's public/
organization-neutral contribution boundary):

> Most of the registrar code ends up written per-consumer-repo instead of
> generalized upstream. I want generalized "template" recipes upstream in
> agent-dispatch, specifically:
>
> a. **General task worker.** A task takes a title, prompt/instructions, and
>    evaluation criteria. An emitter simply provides those fields, pulling
>    them from some source; the default is a user or agent creating tasks
>    manually. Assigned agents drive the task until done, revalidating
>    against its objective and/or evaluation criteria, blocking via steering
>    cards (never a bare stopped turn) when the task description permits it.
>    The worker uses a sub-agent definition that applies the same guidelines
>    and safety rails as the main agent, substituting steering cards for
>    stop-and-ask. Sessions default to autopilot, headless, unless the task
>    says otherwise.
> b. **Reviewer worker.** Reviews a target PR to merge or abandonment by
>    applying Approval/Blocking verdicts each round, with a 7-day-since-
>    last-commit exit as a secondary criterion. A standard PR-feed emitter
>    should support GitHub, ADO, and Gitea drivers with filter criteria,
>    interval or webhook/websocket nudges, and track creation/commits/
>    comments/verdicts/conflicts/merge/abandonment. The evaluator completes
>    the task on merge/abandon/expiry, and — if the agent suspended —
>    confirms a verdict was actually posted, waking it if not.
> c. **Backlog triager.** Classifies an issue, confirms it's a legitimate
>    bug, assigns priority, etc. Completion criteria: the issue carries
>    appropriate triage labels/markers, is assigned to an effort, and is
>    resolved/closed as applicable. Emitters support filterable issue lists
>    from GitHub, ADO, or Gitea.
> d. **Issue reproducer.** Attempts reproduction via relevant strategies,
>    attaches evidence, and records repro state — reproducible keeps the bug
>    active and tagged; not reproducible issues a "strike" a later triager
>    can use as a close signal.
> e. **Effort builder.** Groups related triaged bugs, builds/checks in
>    efforts, and assigns the bugs to them — it does not drive the effort,
>    only creates/joins it. Completion requires every named bug assigned to
>    an effort and that effort in PR.
> f. **Effort driver.** Drives an assigned effort relentlessly to completion
>    — PRs as needed, resolving bugs/hurdles — until it can be archived via
>    its last PR. Completion requires the effort in archive state with
>    evidence the PRs were made and the bugs resolved.
>
> The bulk of all code for these should live in agent-dispatch's own
> deployment, not be repeated per consuming repo. Configuring a new lane in
> a consuming repo should be a handful of parameters — lanes/venue/target for
> a reviewer, source/query/tagging for a backlog worker — referencing a
> shared recipe, never a repo-local copy of the loop/prompt/evaluator logic.

**Follow-up correction (2026-09-30, same day, generalized per this repo's
identifier-neutrality rule — private consumer names replaced with
descriptive venue categories):**

> The review "staleness" needs to be a parameter for the reviewer loop. 30
> days for a slower-moving Azure DevOps-backed consumer, 7 days for a
> faster-moving GitHub-backed consumer (e.g. this repo), etc.

This replaces the fixed "7-day-since-last-commit" reading of item (b) above:
staleness is a **per-declaration parameter** (e.g. `stale_after_days`), not a
hardcoded engine constant — different consuming repos/venues need materially
different values (a slower-moving ADO backlog vs. a fast-moving GitHub repo).
Plan Phase 4 and the Validation Plan below are revised accordingly.

**Reconciliation with what already ships** (this effort's actual scope,
settled against the Context above): (a) is very likely already satisfied by
the existing manual/self-tracked-task path plus the `goal-driven` recipe —
Phase 1 confirms this and closes only a genuine documentation/naming gap, not
a new engine. (b) is mostly already shipped by the existing `reviewer`
recipe plus `task-verification-gate`; the genuine remainder is the ADO/Gitea
driver, a **configurable** stale-exit parameter (not a fixed 7 days — see the
follow-up correction above), and confirmed verification-gate wiring (Phase
4). (c)/(d)/(e)/(f) are genuinely new — named, canned parameterizations of
the existing `repository-issue-loop`/`goal-driven` archetypes (Phases 5-8),
not new engines either. The `extends:` model (Phase 3) and provider adapters
(Phase 2) are the true foundation all of the above compose on.

## Plan

### Phase 1 — General task-worker gap check (Request item a) ✅ no gap found
- [x] Confirm the existing manual/self-tracked-task path (`create`/`propose`
      with no emitter behind it — "tracked by its caller") plus the
      `goal-driven` recipe already satisfies: a title/prompt/
      evaluation-criteria-shaped task; an agent driving it to completion,
      revalidating against the stated goal; steering-card blocking (never a
      bare stopped turn) per the task's own allowance; autopilot-headless as
      the default launch mode. Confirmed — see Journal entry below for the
      four concrete citations.
- [x] Where a genuine gap exists (e.g. the default worker charter doesn't yet
      say "steering card, never stop-and-ask" explicitly, or
      autopilot-headless isn't yet the documented default), fix it as a
      documentation/charter change — not a new task-worker engine. No gap
      found; nothing to fix.
- [x] Tests: none anticipated beyond existing coverage, unless a real charter
      behavior gap is found and fixed. None found — no test changes.

### Phase 2 — Azure DevOps + Gitea provider adapters
- [x] Add an Azure DevOps backlog-provider adapter (list/reserve/claim/
      release) implementing the existing provider-neutral surface
      `repository_issue_loops.py` already defines for GitHub. **Already
      shipped, discovered during this effort** — `AzureDevOpsProvider` in
      `repository_issue_loops.py` (via the authenticated `az` CLI) fully
      implements `ForgeProvider`, and `"azure-devops"` is already in
      `_SUPPORTED_FORGE_PROVIDERS`. Landed by a different effort before this
      one reached Phase 2; no new code needed here beyond this finding.
- [x] Add a Gitea backlog-provider adapter, same surface. **Done
      2026-10-05, closing `ThomasMichon/copilot-extensions#4825`**: a real
      `GiteaProvider` (`gitea_provider.py`, replacing the structural stub)
      implements `ForgeProvider` via the Gitea REST API over `curl` (the
      same integration approach `agent-worktrees`' own Gitea PR provider
      already decided and validated), and `"gitea"` is back in
      `_SUPPORTED_FORGE_PROVIDERS` -- `validate_config` now accepts a
      well-formed `forge.provider: gitea` declaration (new
      `forge.api_base`/`forge.token_env` fields, validated by a new
      `gitea_connection.py`). Validated against a real Gitea instance (not
      unit tests alone, per this repo's "validate beyond unit tests"
      policy): list/reserve/claim/release all exercised end-to-end against
      a disposable scratch repo, catching and fixing nothing in this
      surface (two real bugs *were* caught in the sibling reviewer-side
      adapter below).
- [x] Add the equivalent forge adapter for the **reviewer** recipe's
      provider-neutral **read-side observation** capability for Azure
      DevOps. Landed here: `azure_devops_provider_adapter.py` mirrors the
      existing GitHub adapter's pure-classifier + thin-CLI-wrapper shape,
      reviewer payload refs now accept
      `azure-devops-pr:organization/project/repository#<id>`, and
      reviewer-loop stale-exit checks can resolve ADO-backed PR
      observations from the persisted provider cache. **Scope narrowed by
      code search and documented below**: engine-side verdict posting and
      merge/close actions are not implemented for GitHub either; those
      remain worker-direct tool actions, so Azure DevOps parity here is the
      read-side adapter/routing surface, not a new engine-side vote-casting
      API.
- [x] Add the equivalent forge adapter for the **reviewer** recipe's
      provider-neutral review capability for Gitea. **Done 2026-10-05**: a
      real `GiteaPRAdapter` (`gitea_pr_provider.py`, replacing the
      structural stub) mirrors the GitHub/Azure DevOps adapters' pure-
      classifier + thin-REST-wrapper shape. Decided the previously-open
      repo-addressing question (`review_target_refs.py`'s own "parsed
      structurally... but no live adapter exists yet" note): a Gitea
      reviewer ref is `gitea-pr:<key>/<owner>/<repo>#<number>`, where
      `<key>` is an opaque authority key (conventionally the instance
      hostname, but never resolved as one) looked up in a configured,
      non-empty `api_bases` mapping (`<key>` -> the instance's real,
      possibly path-hosted, base URL). The ref's `<key>` is never trusted
      directly as a credential authority or turned into a URL: an
      unconfigured key is refused before any request, and a path-hosted
      instance (e.g. `https://h/gitea`) is queried at its real configured
      base rather than a bare reconstructed origin. Validated against a
      real Gitea PR (not
      unit tests alone): approval-status aggregation (latest review per
      reviewer, `REQUEST_CHANGES` observed correctly), blocking-thread
      detection (an unresolved inline review comment), and mergeability
      all confirmed live. Live validation caught two real adapter bugs unit
      tests alone could not have (fixed before landing): Gitea's combined
      commit-status endpoint reports `"pending"` by default even with
      `total_count: 0` (no CI configured at all) -- unlike GitHub, which
      omits the rollup entirely in that case -- now treated as "no checks"
      (clean), not pending; and `git/commits/{sha}`'s author/committer
      dates are nested under an inner `commit` key the first pass read
      flatly, silently returning `last_commit_at: None` forever.
- [x] Tests: adapter contract tests mirroring the existing GitHub adapter's
      own test shape, for both backlog and reviewer surfaces.

### Phase 3 — Registrar `extends:` unification

**Sub-plan:** [`phase-3-extends-registrar.md`](phase-3-extends-registrar.md)
(where `extends:` plugs into the existing declaration pipeline, the
recipe-reference syntax and merge semantics, and why the `extends:` work is
sequenced independently of the single-emitter-primitive taxonomy refactor
below — read it before starting any Phase 3 work).

- [ ] Introduce the single `emitter` producer primitive, with schedule/
      webhook/websocket as emitter *triggers* rather than separate `kind`
      values. **Tracked as its own follow-on slice** (sub-plan §*Sub-PRs*,
      item 4) — not a blocking dependency for `extends:` itself.
- [x] Introduce `extends:` in a registrar declaration: a reference to a
      global (plugin-shipped), repo-local, or cross-repo recipe, plus a
      `emitter:`/`evaluator:` block of template-injected or direct param
      values. A global recipe provides the core scripts/prompts; a
      declaration fills in only its own variables (which may themselves be
      script references for the remaining gaps). **Resolution mechanism +
      template-injected param substitution landed**
      (`registrar_recipes.py`: `resolve_extends`/`resolve_recipe_ref`/
      `deep_merge`/`substitute_placeholders`, wired as a pre-expansion step
      ahead of `read_declaration_file_set`'s existing `kind`-dispatch —
      zero changes to that dispatch or any direct declaration's behavior).
      A resolved template's string fields are filled from the
      declaration's own scalar override values first (`{param}`-style,
      unresolved placeholders left intact), then the declaration's keys are
      deep-merged over the result. `global:` recipe refs resolve against an
      (currently empty) in-code registry — see the next item.
- [x] Ship the four already-existing archetypes (reviewer,
      conflict-resolution, goal-driven, repository-issue-loop) as
      `extends:`-able global recipes under this model, with no behavior
      change to their existing direct-declaration path (backward
      compatible). **Landed (Sub-PR 2):** `GLOBAL_RECIPES` now has four
      entries. `repository-issue-loop` is a thin pass-through (the common
      `exclude_labels` set + `pool.body.type: headless` — no charter
      default, since what the loop is *for* varies completely per
      adopter). `goal-driven` reuses the same engine with a new built-in
      `worker_identity: goal-driven` (`agent_dispatch/identities/
      goal-driven.identity.md`) — the standing-loop counterpart of this
      package's ad-hoc `goal-driven` CLI recipe. `reviewer`/
      `conflict-resolution` both resolve to `kind: reviewer-loop` (the
      actual standing-loop engine for PR review today — there is no
      separate `kind: reviewer`), differing only in a default
      `pool.body.charter` generalized from each archetype's own ad-hoc CLI
      charter (no `{repo}`/`{pr}` placeholders, since those vary per
      discovered PR and live in that PR's own task, not this static
      charter). The four shared standing-conduct clauses
      (`RESOLUTION_CLAUSE`/`SUSPEND_CLAUSE`/`EXTERNAL_AUTHOR_CLAUSE`/
      `STAGNATION_CLAUSE`) were exported as a public surface from
      `recipes/registry.py` so both the ad-hoc CLI recipes and these
      registrar charters share one source rather than duplicating prose.
      A consumer can still override any shipped default outright (ordinary
      deep-merge). Documented with a worked migration example in
      `plugins/agent-dispatch/README.md`.
- [x] Tests: an `extends:`-based declaration referencing each existing
      archetype behaves identically to today's direct `kind:` declaration
      with the same effective params; a repo-local and a cross-repo recipe
      reference both resolve correctly. **Repo-local/cross-repo path-ref
      coverage landed** (38 new tests: 31 unit in `test_registrar_recipes.py`
      for the mechanism itself including placeholder substitution (and its
      doubled-brace exclusion), the `params:` no-leak contract,
      `params:`-over-override substitution precedence, and cyclic-reference
      rejection (including a cycle routed through a tuple), 4 integration
      tests in `test_registrar_discovery.py` proving the pre-expansion
      wiring (including a full `ProfileDeclaration` equality check against
      a hand-written direct declaration), 1 CLI-level regression test in
      `test_cli.py` proving a reviewer-loop declaration's repo-root-relative
      `extends:` ref resolves correctly end-to-end, and 2 tests in
      `test_registrar_registry.py` proving a transient recipe-file I/O
      failure is classified indeterminate (not invalid) and that a
      plugin-contributed declaration's `extends:` ref resolves against the
      plugin root, not the registrar subdirectory). **Plus (Sub-PR 2): 7
      more tests** in `test_registrar_recipes.py` proving each of the four
      `global:` recipes resolves end-to-end through
      `read_declaration_file_set` (including full equality against a
      hand-written direct declaration for `repository-issue-loop`), that a
      declaration can still override a shipped default charter outright,
      and that the built-in `goal-driven` identity resolves. Full affected-
      file suite green (3743 passed, 23 skipped; the sole failure
      encountered mid-development, `test_idle_headless_fleet_nudge_
      includes_remote_host`, is a known pre-existing unrelated flake that
      fails even in isolation on a clean `dev` checkout -- confirmed, not
      introduced here). Three other apparent failures
      (`test_consume_baton_completes_on_pickup` and two siblings) turned
      out to be a test-environment artifact, not a real regression: running
      pytest from *inside* a live Copilot CLI session inherits
      `COPILOT_AGENT_SESSION_ID` into the test process, which an
      environment-conditional code path in `task_query_cli.py` reads
      directly -- clearing the variable before the run reproduces green
      every time, confirming an actual CI runner (which never sets it) is
      unaffected.
      Left **unchecked**: the "each existing archetype" half is blocked on
      the global-recipes item above and is not complete until that lands.

### Phase 4 — Reviewer-recipe delta (Request item b's remainder)
- [x] Add a **configurable stale-exit parameter** to the reviewer recipe
      (e.g. `stale_after_days`, measured since the target change's last
      commit) alongside merged/abandoned in its resolution logic. This is a
      **per-declaration param, not an engine constant** — different
      consuming repos/venues need materially different values (e.g. 30 days
      for a slower-moving Azure DevOps backlog vs. 7 days for a fast-moving
      GitHub repo like this one or a harness repo). No default bakes in a
      single "one true" cadence; a declaration that omits the param leaves
      staleness un-checked (never silently applies a guessed default).
- [x] Confirm (and extend if needed) the reviewer recipe's evaluator uses
      `task-verification-gate`'s `require_verification` +
      suspend-requires-verdict pattern: when a reviewer task suspends
      (`run`-hibernates) without having posted a verdict, the evaluator (or
      the `run`-outage recovery sweep, whichever owns this case) wakes it
      rather than leaving it silently parked.
- [x] Tests: a reviewer task that suspends without a verdict is woken, not
      left parked; two fixture declarations with different
      `stale_after_days` values (e.g. 7 and 30) each resolve via the
      stale-exit path only once *their own* configured threshold is crossed,
      confirming the parameter is genuinely per-declaration, not a shared
      constant; a declaration with no `stale_after_days` set never triggers
      a stale-exit.

### Phase 5 — Named recipe: backlog-triager (Request item c)
- [x] Ship a global `extends:`-able recipe parameterizing
      `repository-issue-loop`: prompt requests classification, legitimacy
      check, and priority assignment; evaluator (via the verification-gate
      mechanism) confirms the issue carries the required triage label/marker
      schema and effort assignment.
- [x] Use Phase 2's existing provider-neutral backlog emitter surface --
      GitHub + Azure DevOps adapters today, and the same generic hook the
      deferred Gitea adapter will eventually plug into -- with no
      recipe-specific listing code.
- [x] Tests: end-to-end against a fixture issue, GitHub adapter first;
      confirm the evaluator's schema/marker check.

### Phase 6 — Named recipe: issue-reproducer (Request item d)
- [x] Ship a global recipe: attempts reproduction via relevant strategies,
      attaches evidence + a comment, tags reproducible/not-reproducible
      (a "strike" marker on the not-reproducible path).
- [x] Reuse Phase 2's backlog-provider adapters (same filterable-issue-list
      shape as Phase 5).
- [x] Tests: a reproducible and a not-reproducible fixture outcome, each
      confirmed via the evaluator's evidence/tag check.

### Phase 7 — Named recipe: effort-builder (Request item e)
- [x] Ship a global recipe (a `goal-driven` specialization): takes a query or
      a named set of triaged issues, groups related ones, carves/joins a
      tracked effort, and assigns the constituent issues to it — does not
      drive the effort itself.
- [x] Evaluator requires every named issue assigned to an effort, and that
      effort in PR (this repo's own effort review-gate, or the consumer's
      equivalent).
- [x] Tests: a fixture set of related issues drives the recipe to a merged
      effort-creation PR with all issues assigned.

### Phase 8 — Named recipe: effort-driver (Request item f)
- [x] Ship a global recipe (a `goal-driven` specialization): takes an
      assigned effort and drives it — PRs as needed, resolving bugs/hurdles
      — until it reaches archive state via its last PR.
- [x] Emitter sources from the active efforts in the consumer's own bound
      state root (repo-local; no forge adapter needed here).
- [x] Evaluator requires the named effort in archive state, with evidence
      the PRs were made and the constituent issues resolved.
- [x] Tests: a fixture effort with open constituent issues drives to
      archive state with issues closed and PRs merged.

### Phase 9 — Docs
- [x] `plugins/agent-dispatch/README.md`: document the `extends:` model, the
      eight shipped global recipes (four base archetypes plus the four named
      additions from this effort), clearly calling out that
      `backlog-triager`/`issue-reproducer`/`effort-builder` are named
      instantiations of existing engines while `effort-driver` is the distinct
      repo-local active-effort loop, and document the ADO/Gitea adapter state.
- [x] Update `visions/plugins/agent-dispatch/README.md`,
      `.../repository-issue-loop/README.md`, and `.../reviewer/README.md` to
      add the `extends:` model + four named recipes as realized features, and
      document the provider-neutrality state precisely: backlog support
      realized on the shared GitHub + Azure DevOps surface (with Gitea still a
      deferred adopter), reviewer support still intentionally partial
      (GitHub realized; Azure DevOps/Gitea reviewer adapters deferred).
- [x] Publish a migration note for a consumer moving a hand-written
      `kind: repository-issue-loop`/`reviewer-loop` declaration (with inline
      custom scripts) onto the new `extends:`-based thin form.

## Validation Plan

- [ ] Full `agent-dispatch` plugin suite green
      (`test-supervisor -- python3 tools/run-plugin-tests.py agent-dispatch`).
      **Still open 2026-10-06** (see Journal): every recipe-library-specific
      test module (`test_registrar_recipes.py`, `test_registrar_discovery.py`,
      `test_repository_issue_loops.py`, `test_effort_driver_loops.py`,
      `test_cli.py`, `test_registrar_registry.py`) passed cleanly across 3
      consecutive full-suite runs, but each of those 3 runs had a failure
      elsewhere in the suite (none in a module this effort touches), so this
      criterion itself stays open until one complete run passes clean. 2
      distinct, unrelated timing-sensitive failures on an
      independently-confirmed heavily-loaded shared machine were observed
      and filed as their own issues rather than silently dismissed (#5582,
      #5584) -- neither touches this effort's own code; #5582 is a
      hypothesized race (confirmed transient: passes cleanly in isolation)
      and #5584 is a documented tight real-world subprocess-startup
      timeout -- but filing them is not the same as a
      green run.
- [x] Each Phase's own tests above pass independently. **Confirmed
      2026-10-06**: ran each recipe-library-specific test module directly
      and in isolation (not only as part of a combined full-suite run) --
      `test_registrar_recipes.py`, `test_registrar_discovery.py`,
      `test_repository_issue_loops.py`, `test_effort_driver_loops.py`,
      `test_cli.py`, `test_registrar_registry.py` together: 567 passed, 1
      skipped.
- [x] A real consuming repo's hand-written declaration (the motivating
      operational finding's own lanes, or an equivalent fixture) is migrated
      to an `extends:`-based thin declaration with zero custom script, and
      confirmed behavior-equivalent to the original. **Done 2026-10-06**
      (see Journal): a consuming repo's 4 live, hand-written
      `kind: repository-issue-loop` declarations migrated to
      `extends: global:repository-issue-loop`. Confirmed byte-for-byte
      identical `agent-dispatch registrar discover-repo` resolved output
      before/after for **all 4** declarations (reconstructed each file's
      pre-change content from git history and diffed the complete resolved
      output against the post-change tree, not just a single representative
      sample), before landing through that consumer's own PR flow.
- [x] `plugins/agent-dispatch/README.md` lists all eight shipped global
      recipes (the four base archetypes plus backlog-triager,
      issue-reproducer, effort-builder, and effort-driver) with their params
      documented and cross-checked against
      `plugins/agent-dispatch/src/agent_dispatch/registrar_recipes.py`'s
      `GLOBAL_RECIPES` dict. **Verified 2026-10-06**: the README's table and
      narrative match `GLOBAL_RECIPES`' actual fields exactly for all 8
      entries; no changes needed.
- [ ] A live fixture repo/issue/PR set exercises each of the four newly
      named recipes end-to-end per their own Phase's test item. **Still
      open** -- each Phase's own test item used a local/mocked `tmp_path`
      pytest fixture, not a genuinely live GitHub repo/issue/PR set; this
      item asks for the latter specifically and remains the one substantive
      piece of outstanding validation work (standing up a disposable scratch
      repo, real issues, and driving each recipe's loop to real completion
      against it).

## Proposal

_Pending review._

## Journal

### 2026-10-06 — PR #5414 merged: Gitea backlog + reviewer adapters, rounds 7-20 of automated review
PR #5414 (`pr/agent-dispatch-real-gitea-backlog-review-ee03`) carried the
real Gitea backlog (`gitea_provider.py`) and reviewer (`gitea_pr_provider.py`)
adapters the entry below describes through its first six review rounds.
This session drove it the rest of the way through 14 more automated review
rounds (7-20) to a clean merge -- **squash-merged into `dev`.** Every round's
finding was fixed, regression-tested, and live re-validated against a fresh
disposable Gitea scratch repo (created via an admin identity, a day-to-day
lower-privilege collaborator identity added for the actual adapter-under-
test calls, deleted after each round) before the next push, per this
repo's own validation policy. Highlights by round (full detail in session
history/PR diff):

- **7:** `reserve()` wrote its `reserved` marker before the label existed --
  reordered so the label is resolved first.
- **8:** `forge.api_base` (and `GiteaPRAdapter.api_bases`) only checked
  non-emptiness, accepting a non-URL/credentialed/query-string value;
  added `normalize_gitea_api_base()`. Also closed a stale-doc gap in
  `docs/repository-issue-loop-adoption.md`'s forge schema table.
- **9:** `reserve()`'s label-then-comment write had no rollback on a
  comment-write failure, and the GitHub-mirrored `mergeable: false`
  handling didn't account for Gitea's own draft-PR quirk.
- **10:** the round-9 rollback could clear another loop's own active
  reservation during the pre-election race window; added
  `_other_active_reservation`/later generalized to
  `_active_reservation_depends_on_label`.
- **11:** `fetch_pr()` grouped an entire review's inline comments as one
  thread, letting one resolved conversation mask another unresolved one
  in the same review; partitioned by `(path, position)`. Also fixed the
  same unpaginated-review-comments quirk `_all_comments` already worked
  around on the backlog side.
- **12:** `_label_id()` cached a missing-label result forever; evicted on
  miss. `_mergeability()` still risked CONFLICTED for pending/errored/WIP
  PRs; simplified. The round-9/10 rollback didn't protect against an
  indeterminate transport failure where this loop's own write actually
  landed.
- **13:** `_mergeability()` suppressed a real pending/failing check
  rollup behind an ambiguous `mergeable` value; reordered to check the
  rollup first. `release()`'s own check-then-delete was itself racy;
  added a post-delete re-scan/compensation.
- **14:** a CLEAN rollup alone shouldn't override an explicit
  `mergeable: false`. Both Gitea subprocess transports had no timeout
  (added 120s + sanitized `TimeoutExpired`). `release()`'s label DELETE
  didn't tolerate an already-404 label (idempotent retry).
- **15:** `reserve()`'s own label could be raced away by a concurrent
  release() between its label-add and marker-write; added a post-marker
  re-assert. Gitea's `warning` commit-status was mismapped to
  CHECKS_FAILED (should be pending, matching this repo's own production
  `agent-worktrees` Gitea adapter).
- **16:** the API token traveled as a literal curl argv element (visible
  to same-host `/proc` inspection) in both transports; switched to
  reading the Authorization header via stdin (`-H @-`).
- **17:** a re-requested reviewer's `REQUEST_REVIEW` row couldn't
  supersede that reviewer's own stale verdict, so a re-request after
  changes stayed reported as the old `CHANGES_REQUESTED`.
- **18:** the approval reducer ignored Gitea's own `official` field (an
  unofficial review could flip the aggregate) and dropped team-only
  pending review requests (no `user`, only `team`) to `NONE` instead of
  `PENDING`.
- **19:** the final canonical-state reduction checked
  `CHANGES_REQUESTED`/`APPROVED` but never `PENDING`, so an outstanding
  re-request on one reviewer lost to another reviewer's stale approval.
- **20:** clean -- no new findings; merged.

Phase 2 (both the Gitea backlog provider and the Gitea PR/reviewer
adapter) is now **fully closed**. Phase 3's own tracked single-emitter-
primitive slice remains explicitly out of this scope and stays open,
to be picked up or spun off separately.

### 2026-10-05 — Phase 2 close-out: real Gitea backlog + reviewer adapters (done)
Picked up `ThomasMichon/copilot-extensions#4825` and Phase 2's sibling
reviewer-side Gitea item -- this facility now has Gitea access and
expertise (its own self-hosted instance), closing exactly the gap the
stub-landing Journal entry below flagged as blocking.

- **Backlog adapter** (`gitea_provider.py`, replacing `gitea_provider_stub.py`):
  real `GiteaProvider` via the Gitea REST API over `curl` -- the same
  integration approach `agent-worktrees`' own Gitea PR provider already
  decided and validated, not a fresh decision. New `gitea_connection.py`
  validates `forge.api_base`/`forge.token_env` (gitea-only); `"gitea"` is
  back in `_SUPPORTED_FORGE_PROVIDERS`.
- **Reviewer adapter** (`gitea_pr_provider.py`, replacing
  `gitea_pr_provider_stub.py`): real `GiteaPRAdapter` mirroring
  `github_provider_adapter.py`/`azure_devops_provider_adapter.py`'s
  pure-classifier + thin-REST-wrapper split. Decided
  `review_target_refs.py`'s previously-open Gitea ref shape question:
  `gitea-pr:<key>/<owner>/<repo>#<number>`, where `<key>` resolves against
  a configured `api_bases` mapping to the instance's real (possibly
  path-hosted) base URL -- never trusted directly or turned into a URL.
- **Live-validated both** (not unit tests alone, per this repo's own
  policy) against a disposable scratch repo/PR on the facility's own Gitea
  instance: backlog list/reserve/claim/release round-tripped correctly
  (comment-marker edit-in-place, label add/remove); reviewer approval
  aggregation, blocking-thread detection, and mergeability all confirmed
  against a real `REQUEST_CHANGES` review with an unresolved inline
  comment. Live validation caught two real bugs no unit test (mocking
  plausible-looking fixtures) would have: Gitea's combined commit-status
  endpoint defaults to `"pending"` even with zero statuses configured
  (unlike GitHub, which omits the rollup entirely) -- now correctly read
  as "no checks" (clean); and `git/commits/{sha}`'s author/committer dates
  are nested under an inner `commit` key, not flat -- the first pass
  silently returned `last_commit_at: None` forever. Both fixed before
  landing, with regression tests added.
- Manually widened the module-size baseline for `repository_issue_loops.py`
  (1826 -> 1835, +9 lines: the two-line `gitea_connection` validate call +
  forge-dict wiring + the now-3-provider `_SUPPORTED_FORGE_PROVIDERS` set) --
  a deliberate, reviewed edit per that guard's own documented escape hatch.
- Replaced the four stub-era tests (`test_validate_config_rejects_gitea_
  provider_until_implemented`, `test_forge_provider_for_selects_gitea_stub`,
  `test_gitea_provider_is_an_explicit_stub`, `test_gitea_pr_adapter_is_an_
  explicit_stub`) with real-behavior coverage; added two new dedicated test
  files (`test_gitea_provider.py`, `test_gitea_pr_provider.py`).
- **Post-review hardening (same day, three automated review rounds):**
  this PR's own automated reviewer caught a real credential-authority gap
  the live validation above didn't exercise (the reviewer ref's key was
  trusted directly as a DNS-resolvable host, letting an untrusted ref
  redirect `GITEA_TOKEN` to an arbitrary server, and breaking path-hosted
  instances) -- fixed via the `api_bases` mapping design described above.
  Further rounds caught: dismissed/stale Gitea reviews still counted
  toward approval aggregation (now skipped); `/pulls/{n}/reviews` and each
  review's own `/comments` sub-list were both unpaginated (now bounded-
  scanned, mirroring the backlog side's `_all_comments`); Gitea's
  `skipped` commit-status state raised instead of mapping to clean; and
  two module docstrings embedded transient "this closes tracking-issue-X"
  prose that belongs in this Journal, not timeless source documentation.
  All fixed and re-validated live against a disposable Gitea PR before
  merge.

### 2026-09-30 — Kickoff
- Claimed and expanded #4691 (previously an unplanned placeholder for the
  `extends:` model alone) into this full effort, after a fresh-look
  investigation found the operator's six-recipe request significantly
  overlaps with already-shipped capability (`task-verification-gate`'s
  settled task lifecycle; the existing reviewer/conflict-resolution/
  goal-driven/repository-issue-loop archetypes; the working `recipes`
  CLI). Re-scoped to the genuine remaining gap: the `extends:` registrar
  model itself, GitHub-only provider adapters (ADO/Gitea are
  vision-declared-should-be but unbuilt), and four missing named recipe
  instantiations (backlog-triager, issue-reproducer, effort-builder,
  effort-driver) — explicitly *not* five new archetype engines.
- Request captured close to verbatim above, generalized to remove any
  private-consumer identifiers per this repo's contribution boundary;
  demarcated the "reconciliation with what already ships" analysis as this
  effort's own scoping work, not part of the operator's original ask.

### 2026-09-30 (same day) — Correction: staleness is a per-declaration parameter
- Operator follow-up: the reviewer recipe's stale-exit threshold must be a
  **configurable parameter**, not the fixed 7 days item (b) originally
  named — concretely, 30 days for a slower-moving Azure DevOps-backed
  consumer vs. 7 days for a faster-moving GitHub-backed consumer (e.g. this
  repo). Captured verbatim as a Request follow-up (generalized to drop
  private consumer names per this repo's identifier-neutrality rule,
  `AGENTS.md`) rather than silently editing the original quote.
- Revised Phase 4 (`stale_after_days`-shaped param, no baked-in default —
  an unset value leaves staleness unchecked rather than guessing a cadence)
  and its test item (two fixture declarations with different thresholds
  each resolve independently) to match. No other phase is affected.

### 2026-09-30 (same day) — Phase 1 closed: no gap found
- Investigated the general task-worker path (Request item a) against the
  four properties Phase 1 names, citing concrete source:
  1. **Title/prompt/evaluation-criteria shape** — `create`/`propose` share one
     argument surface (`create_cli.py`'s `_add_create_args`) including
     `--goal`/`--done-criteria`; the `goal-driven` recipe
     (`recipes/registry.py`) renders the identical shape from a single
     `goal` param.
  2. **Drive-to-completion, revalidating against the goal** — the autopilot
     worker charter's goal/progress loop (`worker_charter.py`
     `_AUTOPILOT_CHARTER`) explicitly resumes from the recorded progress log
     and re-checks done-criteria each pass, rather than restarting.
  3. **Steering-card blocking, never a bare stopped turn** — the universal
     operating-procedures charter (`worker_charter.py`
     `_OPERATING_PROCEDURES`, "Every turn ends terminal, steered, or
     waited") already states this as a hard contract, not an informal
     convention.
  4. **Autopilot-headless as the default launch mode** — confirmed at two
     layers: `create_cli.py`'s plain `create --spawn` defaults
     `spawn_backend` to `"bridge"` (headless ACP), and standing supervisor
     registrations default `--embody-backend` to `headless`
     (`supervise_registration_cli.py`). The one deliberate exception —
     `recipes kick --spawn` defaults to `"embody"` (interactive CLI
     autopilot) — is an intentional, documented choice for an ad-hoc,
     developer-kicked recipe instance, not a contradiction of the general
     task-worker default.
- No documentation/charter gap found; all four properties are already
  shipped and already documented. Checked off Phase 1 with no code/doc
  changes beyond this Journal entry and the checklist itself. Moving to
  Phase 2 (Azure DevOps + Gitea provider adapters).

### 2026-09-30 (same day) — Phase 2: ADO backlog adapter already shipped; Gitea blocked on a design decision
- Investigating Phase 2's first item (Azure DevOps backlog adapter) found it
  **already fully implemented**: `AzureDevOpsProvider` in
  `repository_issue_loops.py` implements the `ForgeProvider` protocol
  (`list_open_issues`/`reserve`/`claim`/`release`) via the authenticated
  `az` CLI, including identity verification, WIQL-based issue discovery,
  and the same comment-marker reservation convention the GitHub adapter
  uses. `"azure-devops"` is already registered in
  `_SUPPORTED_FORGE_PROVIDERS`. This landed via a different, independent
  effort/commit before this effort reached Phase 2 — checked off with a
  citation, no new code required.
- Confirmed the reviewer recipe's PR-feed/verdict-posting surface
  (`producers/github_pr_review_webhook.py`) is still GitHub-only; the ADO
  backlog adapter above does not cover it. That half of Phase 2's third
  item is genuinely still open.
- **Stopped before starting the Gitea backlog adapter** — this repo has no
  existing convention for talking to Gitea (no vendored CLI analogous to
  `gh`/`az devops`, no prior adapter code, no declared choice of REST API
  vs. a CLI tool such as `tea`), and no live Gitea instance is available in
  this session to validate against, per this repo's own "validate beyond
  unit tests before landing a fix" policy (`AGENTS.md`) — a Gitea adapter
  built and merged on unit tests alone, with no end-to-end check against a
  real Gitea instance, would repeat exactly the class of mistake that
  policy exists to prevent. This is a genuine design-crossroads blocker
  (not a "good stopping point"): the operator needs to decide the
  integration approach and name an available Gitea instance (or confirm
  none is available and the adapter should be built unit-tested-only with
  that limitation explicitly recorded) before this item can proceed.

### 2026-09-30 (same day) — Gitea backlog adapter: operator decision, stub landed
- Operator direction: leave the real Gitea integration to a future
  agent/session with Gitea access and expertise rather than deciding the
  approach now; no live Gitea instance is available in this environment.
- Landed a structural `GiteaProvider` stub (its own `gitea_provider_stub.py`
  module): implements the `ForgeProvider` protocol shape and
  `_forge_provider_for` can construct it directly, but every method
  (`list_open_issues`/`reserve`/`claim`/`release`) raises
  `NotImplementedError` naming the tracking issue. `validate_config` keeps
  rejecting `forge.provider: gitea` (unchanged — see the Phase 2 checklist
  above), so no real declaration can ever reach this stub; it only
  scaffolds direct provider selection for when `#4825` lands.
- Filed `ThomasMichon/copilot-extensions#4825` to track the real
  implementation (integration approach: Gitea REST API vs. the `tea` CLI;
  validation against a real instance).
- Full `agent-dispatch` suite run: the targeted `test_repository_issue_loops.py`
  suite passes clean (75 tests, including the new Gitea stub coverage). The
  broader suite surfaced 4 pre-existing failures in
  `test_procutil.py::test_namespaced_sibling_resolution_stays_in_active_marketplace_cell`
  (a Windows long-path/unicode temp-dir issue) — confirmed via `git stash`
  that these reproduce identically with none of this session's changes
  applied, so they are unrelated pre-existing environmental flakiness, not a
  regression from this change.
- `repository_issue_loops.py` was already at its grandfathered module-size
  ceiling (1810 lines); the stub pushed it over. Extracted `GiteaProvider`
  into its own `gitea_provider_stub.py` module (re-imported back for
  backward-compatible access) rather than widening the ceiling.

### 2026-09-30 (same day) — Review feedback: keep `gitea` rejected at validation; fix a provider-routing bug
- Automated PR review on the Gitea-stub PR caught two real issues before
  merge, both fixed:
  1. **High**: my first version accepted `forge.provider: gitea` in
     `validate_config`, which would let a declaration validate cleanly and
     then fail forever on its first tick (`NotImplementedError`), leaving a
     resident serve loop stuck retrying indefinitely with no way to tell
     that apart from a transient failure. Fixed: `"gitea"` stays **out** of
     `_SUPPORTED_FORGE_PROVIDERS` (declarations still reject it exactly as
     before this effort), while `_forge_provider_for` can still route to the
     stub directly (useful groundwork, and exercised by its own test) —
     re-add `"gitea"` to `_SUPPORTED_FORGE_PROVIDERS` once `#4825` ships a
     real adapter.
  2. **Medium**: `loop_commands.py`'s interactive `status`/`doctor`/
     `discover` paths hardcoded `GitHubProvider(...)` regardless of a
     declaration's configured provider — a **pre-existing bug also affecting
     Azure DevOps declarations today**, surfaced by this review because it
     would have let an (incorrectly-accepted) Gitea declaration silently
     query GitHub instead of hitting the stub. Fixed properly for every
     provider: both call sites now route through `_forge_provider_for`
     instead of hardcoding GitHub. A follow-up review round correctly noted
     the existing GitHub-configured fixtures (in
     `test_repository_issue_loop_cli.py`, not `test_loop_commands.py` — that
     file is only an import guard) don't prove the hardcoding is actually
     gone; added two dedicated regression tests there
     (`test_discover_routes_through_the_configured_azure_devops_provider`,
     `test_status_routes_forge_reservations_through_the_configured_azure_devops_provider`)
     that monkeypatch only `AzureDevOpsProvider` and would fail loud (a real
     failed `gh` call) if either call site regressed back to hardcoding
     GitHub.
- Both fixes are tightly coupled to this change (the first is this PR's own
  config-acceptance choice; the second was only reachable via this PR's new
  Gitea path, even though it's a real latent bug for ADO too) and are landed
  in the same PR rather than filed separately.
- Phase 2's backlog-adapter scope is settled: ADO backlog done
  (pre-existing), Gitea backlog deferred with a stub. The reviewer-recipe
  ADO/Gitea adapter item (and its Tests item) remain genuinely open and
  unstarted — **not** transferred or resolved, still unchecked above.
  Moving to Phase 3 (the `extends:` registrar unification) next, since the
  reviewer-recipe delta (Phase 4) depends on it and the named recipes
  (Phases 5-8) build on both; Phase 2's remaining item stays tracked here
  and gets picked up alongside Phase 4.

### 2026-10-01 — Phase 3 kickoff: concurrent-work check, sub-plan extracted
- Before starting Phase 3, checked for concurrent work in this space per
  operator direction (another harness agent is active here): no open PR or
  active effort targets the `extends:`/single-emitter-primitive unification
  itself. Found and ruled out two adjacent-but-distinct items: the
  `agent-dispatch-emitter-receipts` effort (same account) is a different,
  already-merged-and-archived feature (durable receipts for `kind: emitter`
  command-authored tasks, #4774/#4775/#4790) with no scope overlap; open PR
  #4791 ("opt-in enforcement for registered agent-backed repo lanes")
  touches adjacent registrar files (`registrar_discovery.py`,
  `registrar_lane_aliases.py`, ...) but a different concern (lane
  enforcement, not `extends:`/kind unification) — noted as a rebase-watch
  item, not a blocker.
- Phase 3 is substantially larger than a typical phase (a core-pipeline
  architecture change touching `registrar_discovery.py`'s one dispatch
  chokepoint, plus new recipe-template/merge machinery). Per the `efforts`
  skill's *decompose large phases into linked sub-docs* guidance, extracted
  [`phase-3-extends-registrar.md`](phase-3-extends-registrar.md): identifies
  `read_declaration_file_set`'s `kind`-dispatch as the exact integration
  point, the `extends:` ref syntax (`global:`/repo-local/cross-repo) and
  deep-merge semantics, and sequences the work into 4 independently
  reviewable sub-PRs — explicitly decoupling `extends:` itself from the
  single-`emitter`-primitive taxonomy refactor (Phase 3's own first
  bullet), since the latter is not a hard prerequisite for the former to
  deliver value.
- Next: sub-PR 1 (the `extends:` resolution mechanism + repo-local/
  cross-repo refs, no global recipes yet).

### 2026-10-01 (same day) — Phase 3 sub-PR 1: `extends:` resolution mechanism landed
- Implemented `registrar_recipes.py` per the sub-plan: `resolve_recipe_ref`
  (three ref kinds — `global:<name>` against an in-code `GLOBAL_RECIPES`
  registry, currently empty; repo-local/cross-repo plain file paths,
  resolved relative to a caller-supplied base directory), `deep_merge`
  (nested-mapping recursion, declaration overrides win, lists/scalars
  replaced wholesale — never concatenated), and `resolve_extends` (the
  public entry point: a no-op passthrough for a declaration with no
  `extends:` key, so every declaration can run through it unconditionally).
- Wired as a pre-expansion step in `registrar_discovery.py`'s
  `read_declaration_file_set`, immediately after decoding and before the
  existing `kind`-dispatch (`reviewer-loop`/`repository-issue-loop`/else) —
  confirmed zero changes to that dispatch or any of its three branches.
  Base directory for relative refs: the known `repo_root` when the caller
  supplies one, else the declaration file's own parent directory (never bare
  CWD, so resolution stays deterministic and test-friendly).
- No path-traversal hardening yet (a `../` cross-repo ref is read as freely
  as a repo-local one) — explicitly deferred per the sub-plan's own Sub-PR
  4, not an oversight.
- 18 new tests, all passing: 15 unit tests (`test_registrar_recipes.py` —
  `deep_merge` semantics, all three `resolve_recipe_ref` kinds including
  failure modes, `resolve_extends`'s passthrough/merge/non-mapping-template
  paths) + 3 integration tests (`test_registrar_discovery.py` — the
  pre-expansion wiring resolves against `repo_root` when given, falls back
  to the declaration directory otherwise, and a plain declaration with no
  `extends:` is provably unaffected).
- Validation: full `agent-dispatch` suite (3 runs) — every run showed the
  same two pre-existing, unrelated flakes already documented or newly
  confirmed as environmental: the Windows long-path/unicode `test_procutil.py`
  failures from Phase 2 (not re-triggered this run, suite composition
  varies by sub-suite grouping) and one new one-off,
  `test_coordinator.py::test_abandoned_passive_reap_status_lifecycle_reflects_the_live_loop`
  (a 5-second timing-sensitive background-loop assertion that failed once
  under heavy parallel sub-suite load, then passed cleanly re-run in
  isolation — confirmed a load-timing flake, not a regression). Every
  registrar/recipes-related test passed cleanly in every run, including the
  two full clean sub-suites (843 + 432 passed) each time.
- Module size: `registrar_discovery.py`'s 6-line addition stayed well under
  its own cap; `registrar_recipes.py` is a new file (no cap impact).
- Next: sub-PR 2 (ship the four archetype global recipes).

### 2026-10-01 (same day) — Review feedback on sub-PR 1
- Automated review caught a real, pre-existing-shaped bug: `_reviewer_loop_declarations`
  (`reviewer_loop_commands.py`) called `read_declaration_file_set(path)`
  **before** deriving `repo_root`, so a reviewer-loop declaration's
  `extends:` repo-local refs would have resolved against the registrar
  directory instead of the actual repo root — `_repository_issue_loop_declarations`
  (`loop_commands.py`) already gets this ordering right, confirming the
  correct pattern to copy. Fixed by reordering: derive `repo_root` first,
  then pass it into `read_declaration_file_set`.
- Fixed a real encoding-error gap: `Path.read_text()` raises
  `UnicodeDecodeError` (a `UnicodeError`, not an `OSError`) for an
  invalid-UTF-8 recipe file, which escaped `registrar_recipes.py`'s
  `RegistrarError` boundary entirely. Added an explicit `except UnicodeError`
  branch (matching `read_declaration_file_set`'s own existing pattern) plus
  a regression test.
- Strengthened the repo-root integration test to compare the **complete**
  resolved `ProfileDeclaration` against a hand-written direct declaration
  with the same effective fields (owner, description, concurrency — not
  just name/labels), per Phase 3's own validation contract ("behaves
  identically to today's direct `kind:` declaration").
- Corrected two inaccuracies this same Journal/Plan had introduced: the
  Tests checkbox was marked done while its own text said the per-archetype
  half is still blocked (reverted to unchecked), and the test count was
  off (20 claimed vs. 18 actual: 15 unit + 3 integration).
- Added `extends:` user documentation (reference syntax, merge semantics,
  a worked repo-local example) to `plugins/agent-dispatch/README.md` in
  this same PR rather than deferring it to sub-PR 3 — the review correctly
  pointed out the mechanism is already live/usable for repo-local and
  cross-repo refs, so deferring its documentation would leave shipped
  syntax undocumented.

### 2026-10-01 (same day) — Review feedback, round 2
- Fixed a real gap: `extends: null` (a present key with a null value) was
  conflated with an absent `extends:` key (`data.get("extends")` returning
  `None` either way), silently bypassing `resolve_recipe_ref`'s own
  validation and surfacing a confusing downstream "unknown key: extends"
  error instead. Fixed by checking key membership (`"extends" not in data`)
  before reading the value, so a present-but-malformed ref always reaches
  `resolve_recipe_ref`'s clear error. Added a regression test.
- Implemented the sub-plan's own stated design gap: `{placeholder}`-style
  substitution from the declaration's own scalar override values into a
  resolved template's string fields, before the deep-merge step — the
  review correctly caught that `resolve_extends` only deep-merged and never
  substituted, despite the sub-plan explicitly specifying "a global recipe
  template using `{placeholder}` ... substitution". Added
  `substitute_placeholders` (first version via `str.Formatter`/`.vformat`,
  replaced in round 3 below with a plain regex once that approach proved
  unsafe). Only scalar (`str`/`int`/`float`/`bool`) override values are
  usable as substitutions; an unresolved placeholder is left intact, never
  an error.
- Added a real CLI-level regression test (`test_cli.py`) that exercises the
  actual `_reviewer_loop_declarations` repo-root-derivation fix from round 1
  through the real CLI (`reviewer-loop inspect`), not just a direct call
  with `repo_root` already supplied — the review correctly noted the
  existing integration test couldn't have caught the original bug since it
  passed `repo_root` explicitly rather than letting the caller derive it.
- Corrected the PR description and this Journal's test counts again (24
  total: 20 unit + 3 integration + 1 CLI-level regression) after the new
  tests landed.

### 2026-10-01 (same day) — Review feedback, round 3
- Fixed a real crash: `str.Formatter().vformat()` (round 2's substitution
  implementation) raises `ValueError` on literal braces in template prose
  that aren't a valid format spec — e.g. a worker-guidance string
  documenting an expected JSON shape like `Return {"decision": "emit"}` —
  and that exception escaped the `RegistrarError` boundary entirely.
  Replaced the whole substitution engine with a plain regex
  (`_PLACEHOLDER_RE = r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}"`) that only ever
  matches a bare `{identifier}` token and can never raise — any other
  brace content (JSON-like prose, format specs, mismatched braces) is left
  completely untouched, a strictly safer contract than the one this sub-PR
  originally shipped.
- Fixed a real validation gap: `_load_recipe_document` decoded any file
  regardless of suffix (`_decode` treats every non-`.json` suffix as YAML),
  so a `.txt` ref would have been silently accepted even though
  `read_declaration_file_set` itself rejects an unsupported declaration
  suffix. Added the identical suffix check (`.yaml`/`.yml`/`.json`) ahead
  of decoding.
- Fixed a real design gap: a scalar override used purely to fill a
  placeholder (e.g. `producer_login`, not itself a valid top-level
  declaration field) was also being deep-merged into the resolved output,
  so `load_declaration` would reject it downstream as an unknown key.
  Introduced a reserved `params:` block: substitution-only values that are
  used to fill placeholders but are never merged into the resolved
  declaration — an ordinary override field (like `repo`) still does both,
  since it's a legitimate field in its own right. Added a
  `read_declaration_file_set`-level integration test proving a `params:`
  value survives substitution but never appears as a stray key on the
  resolved `ProfileDeclaration`.
- Documented placeholder substitution and the `params:` block in
  `plugins/agent-dispatch/README.md` (the earlier round only documented
  `extends:`'s ref syntax and deep-merge, not substitution itself).
- Final count: 29 new tests (24 unit, 4 integration, 1 CLI-level
  regression). Full affected-file suite green (266 passed).

### 2026-10-01 (same day) — Review feedback, round 4
- Fixed a real gap: `Path()`/`.resolve()`/`.read_text()` can raise a bare
  `ValueError` (e.g. an embedded NUL byte in the ref), not just
  `OSError`/`UnicodeError` — that escaped the `RegistrarError` boundary
  entirely. Wrapped path construction and the read call in `except
  ValueError` too, translating both to `RegistrarError`. Added a
  regression test (an embedded `\x00` in the ref).
- Fixed a real documentation/design-drift gap: the sub-plan doc
  (`phase-3-extends-registrar.md`) still described the original plan
  (placeholders filled only from top-level override keys, explicitly "not
  under a nested `params:` block") even though the landed design added the
  `params:` block in round 3. Reconciled the sub-plan with the actual
  contract, including the previously-undocumented precedence rule (a key
  present in both an ordinary override and `params:` uses the `params:`
  value for substitution, while the ordinary override field still ends up
  in the resolved output as always) — also corrected the sub-plan's
  `str.Formatter`/`_Safe` reuse claim, since round 3 replaced that approach
  with a plain regex after finding it unsafe.
- Added a dedicated precedence test and updated the README's substitution
  section to state the same precedence rule.
- Final count: 31 new tests (26 unit, 4 integration, 1 CLI-level
  regression). Full affected-file suite green (268 passed).

### 2026-10-01 (same day) — Review feedback, round 5
- Fixed a real correctness bug: `_load_recipe_document` wrapped every
  `OSError` as a plain `RegistrarError`, but
  `registrar_registry._classify_declaration()` only preserves a plugin
  declaration's last-known state for `RegistrarIndeterminateError`
  specifically — so a transient permission/read race on an `extends:`
  target would have been classified as a permanently invalid entry,
  withdrawing a previously active unit on what might be a one-tick
  filesystem hiccup. Split `OSError` (→ `RegistrarIndeterminateError`,
  matching `read_declaration_file_set`'s own identical branch for the
  direct declaration read) from `ValueError` (→ stays `RegistrarError`, a
  genuinely permanent problem). Added the analogous retention regression
  test in `test_registrar_registry.py`
  (`test_indeterminate_extends_recipe_read_retains_only_that_document`),
  mirroring the existing direct-declaration version of the same test.
- Addressed five low-severity documentation/prose-polish findings: removed
  review-history narration from the sub-plan doc, the plugin README, the
  `registrar_recipes.py` module comment, and a test docstring, replacing
  each with timeless current-state rationale (no "this was originally
  planned as X" / "a prior version did Y" framing in durable references).
- Final count: 32 new tests (26 unit, 4 integration, 1 CLI-level
  regression, 1 registrar-registry retention test). Full affected-file
  suite green (311 passed).

### 2026-10-01 (same day) — Review feedback, round 6
- Fixed a real crash: `substitute_placeholders`'s unconditional recursion
  never terminates on a YAML document's self-referential alias (a
  mapping/list that, directly or transitively, contains itself) —
  `RecursionError` is not a `RegistrarError`, so one malformed recipe could
  abort the whole registrar scan rather than fail that single declaration.
  Added on-stack ancestor tracking (object ids currently being recursed
  into, not every object ever seen) so a true cycle raises a clear
  `RegistrarError`, while a non-cyclic *shared* reference (the same
  sub-object reachable from two different sibling branches — an ordinary
  YAML anchor reused twice, never from itself) still resolves normally.
  Added both a cyclic-mapping and cyclic-list regression test, plus a
  shared-non-cyclic-reference test proving the fix doesn't overreach.
- The remaining four items this round's overview listed as "still open"
  were stale GitHub review-thread markers for content already fixed in
  earlier rounds (confirmed by re-reading the current file content — no
  further change needed).
- Final count: 35 new tests (29 unit, 4 integration, 1 CLI-level
  regression, 1 registrar-registry retention test). Full affected-file
  suite green (314 passed).

### 2026-10-01 (same day) — Review feedback, round 7
- Fixed a real bug: `registrar_registry._classify_declaration()` already
  receives `plugin_root` but called `read_declaration_file(path,
  allow_plugin_companion=True)` without threading it through, so a
  plugin-contributed declaration's `extends:` ref resolved against the
  registrar subdirectory instead of the plugin root — a recipe placed at
  the plugin root (the documented, intended convention for a
  plugin-shipped recipe) would never be found. Passed
  `repo_root=plugin_root` through that call site. Added a regression test
  with the recipe genuinely at the plugin root (not beside the
  declaration, which the existing retention test from round 5 happened to
  use and would have missed this exact gap).
- The remaining four items this round's overview listed as "still open"
  were, again, stale GitHub review-thread markers for content already
  fixed in earlier rounds.
- Final count: 36 new tests (29 unit, 4 integration, 1 CLI-level
  regression, 2 registrar-registry tests). Full affected-file suite green
  (315 passed).

### 2026-10-01 (same day) — Review feedback, round 8
- Fixed a real substitution-contract violation: `_PLACEHOLDER_RE` matched a
  `{identifier}` token even when doubled up in extra braces (`{{name}}`),
  silently substituting `{x}` for `{{name}}` with `params={"name": "x"}` —
  but `{{`/`}}` is a literal-brace escaping convention (mirroring
  `str.format`'s own), not a placeholder, and the documented contract is
  "bare `{identifier}` only". Added `(?<!\{)`/`(?!\})` guards so a doubled
  brace never matches at all. Added a regression test.
- Fixed a real gap in round 6's own cycle-detection fix: the tuple branch
  of `substitute_placeholders` dropped the current `_ancestors` set
  entirely (called the recursive step with none), so a cycle routed
  through a tuple (a mapping containing a tuple that contains that same
  mapping) would recurse past the tuple undetected into `RecursionError` —
  exactly the failure mode round 6 was meant to close. Fixed by threading
  `_ancestors` through the tuple branch unchanged (a tuple itself can't be
  a cycle point since it's immutable, so no new marker is added there, but
  its elements still need the accumulated ancestor chain). Added a
  regression test with a cycle specifically routed through a tuple.
- The remaining five items this round's overview listed as "still open"
  were, again, stale GitHub review-thread markers for content already
  fixed in earlier rounds.
- Final count: 38 new tests (31 unit, 4 integration, 1 CLI-level
  regression, 2 registrar-registry tests). Full affected-file suite green
  (317 passed).

### 2026-10-02 — Sub-PR 2: ship the four global recipes

With Sub-PR 1 merged (#4875), populated `GLOBAL_RECIPES` with all four named
archetypes. The two genuinely non-obvious design calls, both surfaced to the
operator before implementing (not guessed at unilaterally, given a
concurrent session was already deep in this exact file):

1. **Naming vs. engine mapping.** The sub-plan names `global:reviewer`/
   `global:conflict-resolution`/`global:goal-driven` after the *ad-hoc CLI
   recipe* archetypes (`agent_dispatch.recipes.registry`), not any existing
   registrar `kind`. Only `repository-issue-loop` has a matching standing-
   loop engine today; `reviewer-loop` is a separate, independently-
   implemented engine (never built on the CLI recipe's own
   `render_recipe("reviewer", ...)`), and `conflict-resolution`/
   `goal-driven` have no standing-loop form at all. Operator confirmed the
   goal is genuinely delivering all four as real, working archetypes (not
   stubs) — resolved by mapping `reviewer`/`conflict-resolution` onto the
   existing `reviewer-loop` engine (its own standing-loop counterpart) and
   `goal-driven` onto `repository-issue-loop` (issues-as-goals, one worker
   per discovered issue), each via a default `pool.body.charter` (reviewer/
   conflict-resolution) or `worker_identity` (goal-driven, since
   `reviewer-loop` has no identity-file mechanism — only `pool.body.charter`/
   `pool.body.agent` — while `repository-issue-loop` does).
2. **Charter content must be generalized, not ad-hoc-recipe-shaped.** The
   CLI recipes' own charter templates are written for *one specific task*
   (`{repo}`/`{pr}`/`{land}` placeholders filled per `kick` invocation). A
   standing loop's `pool.body.charter` is static across every task the pool
   ever claims — the per-occurrence specifics live in that occurrence's own
   task (set by the consumer's own discovery/emitter script), not in this
   charter. Wrote generalized versions instead of reusing the CLI templates
   verbatim, while still reusing the four shared standing-conduct clauses
   (now exported publicly from `recipes/registry.py`) rather than
   duplicating that prose a third time.

Also found and fixed two false positives during validation, both confirmed
not to be real regressions before moving on:
- `test_idle_headless_fleet_nudge_includes_remote_host` — a known,
  previously-confirmed pre-existing flake (fails in isolation on a clean
  `dev` checkout too).
- Three `test_consume_*` tests in `test_cli.py` appeared to fail
  (`bind_owner_session` appearing in the recorded transition list where the
  fixture didn't expect it) — traced to `task_query_cli.py`'s
  `COPILOT_AGENT_SESSION_ID`-conditional bind step reading that env var
  directly from the process environment, which a pytest run launched *from
  inside* a live Copilot CLI session inherits (since the shell tool itself
  runs within that session). Clearing the variable before the test run
  reproduces green consistently; an actual CI runner never sets it, so this
  was a test-environment artifact of the authoring environment, not a code
  defect.

Shipped: `registrar_recipes.py` (`GLOBAL_RECIPES`, 4 entries), a new
built-in `agent_dispatch/identities/goal-driven.identity.md`, public
`RESOLUTION_CLAUSE`/`SUSPEND_CLAUSE`/`EXTERNAL_AUTHOR_CLAUSE`/
`STAGNATION_CLAUSE` exports from `recipes/registry.py` + `recipes/__init__.py`,
a worked migration example + shipped-recipes table in
`plugins/agent-dispatch/README.md`, and 7 new tests. Full affected-file
suite green (3743 passed, 23 skipped, the one known flake above).

### 2026-10-02 — Phase 4: reviewer stale-exit + verification-gate wiring landed
- Added an optional top-level `stale_after_days` field to
  `kind: reviewer-loop` declarations (`reviewer_loops.py`). It is validated
  as a finite positive number, rejected from inline `evaluator` overrides as
  a derived field, and threaded onto the expanded evaluator registration as
  `spec.reviewer_loop.stale_after_days` — explicitly per declaration, with
  **no default** when omitted.
- Wired the reviewer-loop lifecycle extension into submitted verification in
  the way the review correctly required, not just as a point-in-time check:
  `verification.py` now wraps matching evaluator registrations with
  `ReviewerLoopEvaluator`, which preserves any existing merged/abandoned
  decisions from the underlying evaluator, abandons immediately once the
  stale deadline has actually passed, and (when it has *not* passed yet)
  schedules a future submitted-verification request at exactly that deadline
  via the existing background verification-request drain. Advancing the queue
  clock and letting the drain run is now enough to fire the stale-exit; no
  ad hoc direct `evaluate_submitted_task` call is required.
- Wired the same deadline into the suspended reviewer lifecycle: a new
  `reconcile_reviewer_deadlines()` pass (piggybacked on the existing
  always-on liveness/cooldown loop in `coordinator_loops.py`) wakes a
  suspended reviewer once its stale deadline elapses. If the task is parked
  behind a detached run-waiter, the reconciler supersedes that waiter and
  queues the normal wake path; otherwise it uses the existing `resume`
  transition. This closes the exact gap the review called out: an unchanged
  hibernated review no longer waits forever for some unrelated external
  event before it can be driven to expiry.
- Confirmed and retained the pre-existing generic suspend-requires-verdict
  wiring instead of adding redundant code: the existing monitor/run-waiter
  recovery machinery is what wakes a suspended reviewer without a verdict;
  Phase 4 only needed to cover it with the right reviewer-specific deadline
  triggers and tests.
- Production metadata source: stale evaluation now accepts either inline
  reviewer metadata (`payload_inline.reviewer_loop.last_commit_at`) **or**
  the standard GitHub-backed provider path. `github_provider_adapter.py` now
  reads the current PR head commit's `committedDate`; `PRObservation` /
  `PRObservationStore` persist that `last_commit_at`, and reviewer stale
  evaluation can derive it from a standard `payload_ref` of the form
  `github-pr:owner/repo#123` without requiring every consumer's custom
  emitter to invent its own metadata contract.
- Tests added/extended:
  - `test_verification.py`: one declaration with `stale_after_days: 7` and
    one with `30` abandon only at their own thresholds; a declaration with
    no `stale_after_days` never takes the stale-exit path; the delayed
    verification request is scheduled and later fires by clock advance alone;
    and the provider-observation-store fallback path supplies
    `last_commit_at` when only a standard `github-pr:...` payload ref is
    present.
  - `test_coordinator.py`: a suspended reviewer task with no verdict is
    re-woken by the real reviewer-deadline recovery sweep (run-waiter
    superseded + wake queued), not by a synthetic event-note injection.
  - `test_registrar_discovery.py`: reviewer-loop expansion threads
    `stale_after_days` onto the evaluator registration spec.
  - `test_github_provider_adapter.py`, `test_pr_observation_store.py`, and
    `test_pr_review_poll_loop.py`: the provider-observation path now carries
    and persists `last_commit_at`.
- README updated (`plugins/agent-dispatch/README.md`) to document the new
  reviewer-loop parameter, its no-default behavior, and the two metadata
  sources (inline reviewer metadata or the GitHub PR-observation cache).
- Validation-coupled fix found while driving the full suite: `payload.py`'s
  temporary spill file names were unnecessarily long for deep Windows
  worktree paths, which made `test_payload_endpoint_spilled_blob` fail before
  the Phase 4 assertions were even reachable in a full-suite run. Shortened
  the temp spill suffix without changing blob refs or persisted payload
  names. Also shortened one procutil test's per-case leaf while keeping it
  within pytest's own temp root, preserving its quoting/Unicode coverage
  without relying on Windows long-path policy in this deep worktree.
- Validation:
  - focused reviewer-loop/provider-path tests: pass
  - full `agent-dispatch` suite: **3747 passed, 23 skipped**
  - neither of the two effort-noted unrelated flakes
    (`test_idle_headless_fleet_nudge_includes_remote_host`,
    `test_consume_baton_*` under a live Copilot CLI session) appeared in
    this run.

### 2026-10-03 — Phase 5: backlog-triager named recipe landed
- Added a fifth built-in global recipe template,
  `global:backlog-triager`, as a `repository-issue-loop`
  parameterization (`registrar_recipes.py`): common backlog-loop defaults
  (`exclude_labels`, headless pool body) plus a new built-in
  `worker_identity: backlog-triager`, `require_verification: true`, and a
  shared opaque `evaluator_ref: backlog-triager`.
- Added the built-in worker identity
  `agent_dispatch/identities/backlog-triager.identity.md`. Its charter is
  the shared/generic half only: classify the issue, confirm whether it is
  a legitimate bug vs. a question/already-fixed/duplicate/out-of-scope
  item, assign the repository's priority/triage markers, and ensure the
  issue is attached to tracked effort work before considering triage
  complete. It explicitly points at the repository's own same-repo
  effort-linking convention (for this repo family, a direct
  `efforts/active/<slug>/README.md` reference) rather than inventing a new
  marker shape.
- **Shared-vs-consumer evaluator split (the key design call):** the shipped
  recipe fixes the global lifecycle contract (`require_verification` +
  `evaluator_ref`) but does **not** hardcode any one repository's exact
  triage label schema, comment/body markers, or effort-assignment policy.
  Those are consumer-specific and are enforced by the consuming repo's own
  trusted evaluator registration under `evaluator_ref: backlog-triager`
  (repo-scoped or `all_repos`, as that consumer chooses). This mirrors the
  reviewer recipe's own split between shared engine/lifecycle behavior and
  consumer-supplied evaluator registration, and is the note Phase 9 docs
  need to preserve when documenting migration/adoption.
- Reused the existing provider-neutral issue-list emitter exactly as Phase 5
  required: no new backlog-listing code was added. The new recipe resolves
  to ordinary `kind: repository-issue-loop`, so it automatically uses
  whatever forge provider that existing loop already routes to
  (GitHub/Azure DevOps today; Gitea remains the Phase 2 stub and is still
  validation-rejected, unchanged here).
- Tests added:
  - `test_registrar_recipes.py`: `global:backlog-triager` resolves end to
    end, stamps the expected verification fields, and the built-in identity
    resolves.
  - `test_repository_issue_loops.py`: an `extends: global:backlog-triager`
    declaration drives all the way through the generic repository-issue-loop
    tick path against a fixture issue, proving the named recipe reuses the
    existing issue-list emitter rather than bypassing it.
  - `test_verification.py`: a fixture trusted script evaluator keyed by
    `backlog-triager` confirms completion only when the selected issue's
    labels include the required triage schema and its body carries an effort
    marker (`efforts/active/.../README.md`); an otherwise-triaged issue
    lacking that effort marker stays submitted.
- Validation:
  - focused Phase 5 tests: **152 passed**
  - full `agent-dispatch` suite: **3810 passed, 23 skipped**
  - local note: the default 300s per-sub-suite wall-clock budget in
    `tools/run-plugin-tests.py` proved too tight for one deep Windows
    sub-suite here; rerunning with `--timeout 600 --plugin-timeout 2400`
    completed green with no code changes, confirming a local timing-budget
    issue rather than a Phase 5 regression.

### 2026-10-03 — Phase 6: issue-reproducer named recipe landed
- Added a sixth built-in global recipe template,
  `global:issue-reproducer`, as another `repository-issue-loop`
  parameterization (`registrar_recipes.py`): common backlog-loop defaults
  (`exclude_labels`, headless pool body) plus a new built-in
  `worker_identity: issue-reproducer`, `require_verification: true`, a
  shared opaque `evaluator_ref: issue-reproducer`, and a recipe-specific
  `task_contract` that turns the created task into a bounded reproduction
  lane rather than the default implement-through-merge contract.
- Added the built-in worker identity
  `agent_dispatch/identities/issue-reproducer.identity.md`. Its charter is
  the shared/generic half only: attempt reproduction via whatever
  strategies the repository/stack makes available, record durable evidence
  of what was actually tried, keep reproducible bugs active with the
  repository's reproducible marker(s), and apply the repository's
  not-reproducible + strike-marker convention on the bounded failure path.
- **Shared-vs-consumer evaluator split (same pattern as Phase 5):** the
  shipped recipe fixes the global lifecycle contract (`require_verification`
  + `evaluator_ref`) but does **not** hardcode any one repository's exact
  evidence/comment schema, reproducible/not-reproducible tag names, or
  strike-marker vocabulary. Those remain consumer-specific and are enforced
  by the consuming repo's own trusted evaluator registration under
  `evaluator_ref: issue-reproducer` (repo-scoped or `all_repos`, as that
  consumer chooses).
- Reused the existing provider-neutral issue-list emitter exactly as Phase 6
  required: no new listing code was added. The new recipe resolves to
  ordinary `kind: repository-issue-loop`, so it automatically uses the same
  forge adapters Phase 2 already established for that loop
  (GitHub/Azure DevOps today; Gitea remains the deferred stub and is still
  validation-rejected, unchanged here).
- Tests added:
  - `test_registrar_recipes.py`: `global:issue-reproducer` resolves end to
    end, stamps the expected verification fields, and the built-in identity
    resolves.
  - `test_repository_issue_loops.py`: an `extends: global:issue-reproducer`
    declaration drives all the way through the generic
    repository-issue-loop tick path against a fixture issue, proving the
    named recipe reuses the existing issue-list emitter and emits the
    reproduction-specific task contract.
  - `test_verification.py`: a fixture trusted script evaluator keyed by
    `issue-reproducer` confirms both a reproducible outcome and a
    not-reproducible-with-strike outcome only when the selected issue has
    durable reproduction evidence plus the expected outcome markers; a
    missing-strike not-repro outcome stays submitted.
- Documentation updated: `plugins/agent-dispatch/README.md` now lists
  `global:issue-reproducer` alongside the other shipped global recipes and
  documents the same shared-recipe / consumer-evaluator adoption split as
  `global:backlog-triager`.
- Validation:
  - focused Phase 6 tests: **157 passed**
  - full `agent-dispatch` suite: **passed** via
    `python tools/run-plugin-tests.py agent-dispatch --timeout 600 --plugin-timeout 2400`
  - local note: the default 300s per-sub-suite wall-clock budget in
    `tools/run-plugin-tests.py` again proved too tight for one deep Windows
    sub-suite here; the longer-timeout rerun completed green with no code
    changes, confirming a local timing-budget issue rather than a Phase 6
    regression.

### 2026-10-03 — Phase 7: effort-builder named recipe landed
- Added a seventh built-in global recipe template,
  `global:effort-builder`, with a new built-in
  `worker_identity: effort-builder`, `require_verification: true`, an opaque
  shared `evaluator_ref: effort-builder`, and a bounded `task_contract`
  specialized to grouping multiple already-triaged issues into one tracked
  effort.
- **Design decision — why this still resolves to `kind: repository-issue-loop`
  even though Request item (e) called it a `goal-driven` specialization:**
  the materially new part of effort-builder is the *worker's posture and stop
  condition*, not a different discovery engine. The standing loop still needs
  the Phase 2 provider-neutral issue-list emitter to take either a query or a
  named set of issues and reserve that bounded set as one occurrence. So the
  shipped declaration remains a `repository-issue-loop` recipe, but its
  contract is intentionally *goal-driven in shape*: one invocation spans
  multiple related issues and stops once they are grouped into one effort and
  that effort's plan artifact has reached the repository's review gate, rather
  than implementing/fixing the issues through merge. This avoids mechanically
  copying the per-issue triage/repro lanes from Phases 5/6 while still
  reusing the only existing standing engine that actually emits bounded issue
  sets.
- To close the "named issue set" half of that contract concretely, the generic
  `repository-issue-loop` declaration now accepts `issue_numbers: [...]` as a
  provider-neutral selector. When present, eligibility is restricted to exactly
  that explicit set (still honoring the loop's normal label/quiet-period/
  reservation guards), so effort-builder can group the caller-selected issues
  even when other eligible backlog items are present.
- Added the built-in worker identity
  `agent_dispatch/identities/effort-builder.identity.md`. Its charter is
  deliberately explicit about the boundary the Request required: create or
  join one coherent effort, assign every named issue to it, and drive only the
  effort-tracking artifact to the review gate; do **not** implement the
  constituent bug fixes, close issues merely because an effort exists, or
  archive/drive the effort itself (that remains Phase 8).
- **Shared-vs-consumer evaluator split (same deliberate pattern as Phases 5/6):**
  the shipped recipe fixes the reusable lifecycle contract
  (`require_verification` + `evaluator_ref`) but does **not** hardcode one
  repository's exact effort-assignment marker or review-gate evidence shape.
  A consumer repo supplies its own trusted evaluator registration under
  `evaluator_ref: effort-builder` to define what counts as "assigned to an
  effort" and "that effort reached the repo's own review gate" (for this repo,
  the planning-efforts convention is that the effort README itself is
  submitted through a PR before execution).
- Reused the existing provider-neutral issue-list emitter exactly as intended:
  no new issue discovery code was added. The named recipe still flows through
  the ordinary `repository-issue-loop` path and therefore keeps the same forge
  adapter surface Phase 2 already established (GitHub + Azure DevOps today;
  Gitea remains the deferred Phase 2 stub and is unchanged here).
- Tests added:
  - `test_registrar_recipes.py`: `global:effort-builder` resolves end to end,
    stamps the expected verification fields, and the built-in identity
    resolves.
  - `test_repository_issue_loops.py`: an `extends: global:effort-builder`
    declaration drives through the generic repository-issue-loop tick path
    against a fixture pair of triaged issues, proving one created task spans
    multiple related issues and carries the bounded effort-building contract
    instead of the default implement-through-merge contract.
  - `test_verification.py`: a fixture trusted script evaluator keyed by
    `effort-builder` confirms completion only when every selected issue points
    at the same effort and that effort has reached a review-gated state,
    while mismatched-effort and pre-gate fixtures stay submitted. The passing
    fixture models the effort-creation PR as already merged, satisfying the
    "merged effort-creation PR with all issues assigned" validation shape.
- Validation:
  - focused Phase 7 tests: **161 passed**
  - full `agent-dispatch` suite: **passed** via
    `python tools/run-plugin-tests.py agent-dispatch --timeout 600 --plugin-timeout 2400`
  - full-suite sub-run totals:
    - 813 passed, 5 skipped
    - 500 passed, 8 skipped
    - 435 passed, 3 skipped
    - 615 passed, 6 skipped
    - 651 passed
    - 792 passed
    - 24 passed, 1 skipped
  - none of the effort-noted unrelated flakes appeared in this run.

### 2026-10-04 — Phase 8: effort-driver named recipe landed
- Added an eighth built-in global recipe template,
  `global:effort-driver`, with a new built-in `worker_identity:
  effort-driver`, `require_verification: true`, an opaque shared
  `evaluator_ref: effort-driver`, and a bounded task contract specialized to
  driving one already-assigned tracked effort through its remaining PRs
  until the effort itself reaches archive state.
- **Design decision — why this is its own `kind: effort-driver-loop` instead
  of another `repository-issue-loop` parameterization:** Phase 7's
  effort-builder still reused `repository-issue-loop` because the discovery
  problem stayed "take a query or named set of repository issues and reserve
  them." Phase 8's discovery problem is materially different: there is no
  forge-backed issue list here at all. The source of work is the consumer's
  own state root (`efforts/active/<slug>/README.md`-shaped state), so the
  shipped standing loop is a new repo-local emitter kind that scans active
  effort READMEs and authors one goal-driven task per eligible effort. The
  generic emitter runtime was extended with a second built-in tick path
  (`effort_driver_loop`) alongside the existing `repository_issue_loop`,
  while the declaration still expands to the same ordinary emitter + one
  headless worker lane shape.
- Added the built-in worker identity
  `agent_dispatch/identities/effort-driver.identity.md`. Its charter is
  explicitly the execution-only half of the effort-builder/effort-driver
  boundary: drive the already-named effort through implementation, review,
  merge, and archive; do **not** create a replacement effort for work that
  already belongs to the named one, and do **not** collapse back into raw
  issue triage as the objective.
- **Shared-vs-consumer evaluator split (same deliberate pattern as Phases
  5-7):** the shipped recipe fixes the reusable lifecycle contract
  (`require_verification` + `evaluator_ref`) and the repo-local
  effort-discovery source, but does **not** hardcode one repository's exact
  archive convention, merged-PR proof shape, or "constituent issues
  resolved" evidence contract. A consumer repo supplies its own trusted
  evaluator registration under `evaluator_ref: effort-driver` to define what
  counts as archive state there.
- Documentation updated: `plugins/agent-dispatch/README.md` now documents
  `global:effort-driver` in the shipped global-recipes table (and corrects
  that table's stale shipped-recipe count) plus the same shared-recipe /
  repo-scoped-evaluator adoption split as `effort-builder`.
- Tests added:
  - `test_effort_driver_loops.py`: the new loop expands to one emitter + one
    headless worker lane, discovers active efforts from a repo-local state
    root, and authors the expected goal-driven task contract for the named
    effort while leaving unrelated active efforts untouched. Explicit
    `effort_slugs` behave as an all-or-nothing selector (missing named
    efforts suppress creation rather than silently creating a partial set).
  - `test_registrar_recipes.py`: `global:effort-driver` resolves end to end
    through `read_declaration_file_set`, stamps the expected verification
    fields, and the built-in identity resolves.
  - `test_producers_emitter.py`: the generic emitter runtime now dispatches
    the new built-in `effort_driver_loop` tick path and threads the stamped
    `cwd` into its validation exactly as it already does for
    `repository_issue_loop`.
  - `test_verification.py`: a fixture trusted script evaluator keyed by
    `effort-driver` confirms completion only when the named effort has left
    `efforts/active/`, appeared at its archive path, and the archived README
    carries durable PR + constituent-issue evidence; still-active and
    weak-evidence fixtures stay submitted.
- Validation:
  - focused Phase 8 tests: **123 passed**
  - install contract: **OK**
  - full `agent-dispatch` suite: re-run twice via
    `python tools/run-plugin-tests.py agent-dispatch --timeout 600 --plugin-timeout 2400`;
    both runs hit only the already-noted pre-existing aggregate flake
    `test_liveness_gc_publishes_a_bus_event_for_auto_suspend_with_zero_requeued`
    (`test_liveness_gc*` is the exact known Phase 7 flake class called out in
    this effort's operator brief). Isolated rerun of that single test passed
    immediately, confirming the failure shape stayed flaky-only-in-aggregate
    rather than a Phase 8 regression.

### 2026-10-04 (same day) — Review feedback, round 1
- Automated review caught two real duplicate-suppression bugs in the new
  effort-driver loop, both fixed:
  1. **Medium**: the first version treated `submitted` tasks as terminal for
     per-effort suppression, so a task whose evaluator returned `noop` would
     still allow the next cadence to create a duplicate effort-driver task for
     the same still-active effort. Fixed by removing `submitted` from the
     loop's terminal set and adding a regression test that a submitted task
     suppresses re-creation on a later cadence.
  2. **Medium**: the first version scanned the newest 1,000 tasks in the repo
     and suppressed effort creation from that unfiltered corpus. Once the repo
     had enough unrelated tasks, an older still-active effort task could fall
     out of that window and the loop would emit a duplicate. Fixed by
     discovering the active efforts first, then querying the queue per effort
     key (`exclusive_key` for active-task suppression, `origin_ref` for
     same-occurrence suppression) with `limit=1`, plus a regression test that
     the plan uses those per-effort lookups rather than a whole-corpus scan.
- Focused Phase 8 tests after the fixes: **123 passed**.

### 2026-10-04 (same day) — Review feedback, round 2
- Automated review caught one more real portability bug in the new
  effort-driver loop: the first version serialized `readme_relative` with the
  host platform's native path separator, then fed that platform-shaped string
  into the persistent `exclusive_key` / `dedup_key`. That would let the same
  active effort receive different suppression keys on Windows vs. Linux,
  defeating cross-machine dedup for a lane that can legally move between
  eligible producer hosts. Fixed by canonicalizing repo-relative effort paths to
  POSIX form (`relative_path.as_posix()`) before they ever reach task payloads
  or persistent keys, and updated the focused effort-driver tests to assert the
  canonical forward-slash form explicitly.
- Focused Phase 8 tests after the fix: **123 passed**.

### 2026-10-04 (same day) — Review feedback, rounds 3-4
- Automated review then found two more correctness/polish gaps, both fixed:
  1. **Medium**: the first version persisted the producer machine's absolute
     `state_root` into emitted tasks. A worker or trusted evaluator running on
     another eligible machine could not rely on that host-local path. Fixed by
     carrying only the repo-relative effort README path (`efforts/active/...`)
     plus the effort slug in task payloads/prompts; resolving the actual bound
     state root is left to the consumer's own execution context (the same place
     the repo-scoped trusted evaluator already owns).
  2. **Medium/Low**: `worker_filters` had been temporarily accepted as an input
     declaration key even though it is a derived/internal field, and this
     effort's status surfaces were out of sync (`README.md` said
     `In Progress`, `efforts/README.md` still said `Draft`). Fixed by
     rejecting `worker_filters` from authored declarations again (matching the
     repository-issue-loop pattern), changing this effort's canonical status to
     `Active (...)`, and synchronizing the active-effort index row.
- Focused Phase 8 tests after the fixes: **123 passed**.

### 2026-10-04 (same day) — Review feedback, round 5
- Automated review caught one final real integration bug: the supervisor's
  registration launcher still recognized only `command` and
  `repository_issue_loop` emitter specs as **periodic emitters**, so the new
  inline `effort_driver_loop` spec would have been mis-launched down the
  legacy webhook path and never ticked. Fixed `supervisor_registration.
  build_command()` to treat `effort_driver_loop` the same as the other
  periodic emitter shapes, and added a regression test in
  `test_supervisor_daemon.py` proving an expanded effort-driver declaration
  materializes to `agent-dispatch emitter serve ... --holder <machine>` rather
  than `webhook --config`.
- Focused validation after the fix:
  - `python tools/run-plugin-tests.py agent-dispatch -k effort_driver --timeout 600 --plugin-timeout 2400`
    → **10 passed**
  - `python tools/run-plugin-tests.py agent-dispatch -k "effort_driver_loop_expansion_builds_periodic_emitter_command or builtin_effort_driver_loop" --timeout 600 --plugin-timeout 2400`
    → **2 passed**

### 2026-10-04 (same day) — Review feedback, round 6
- Automated review's remaining genuine concern was default eligibility: the
  first effort-driver declaration shape would scan `efforts/active/` and drive
  **every** README there when `effort_slugs` was omitted, which would include
  Draft efforts in this repository's own active tree and violate the recipe's
  "already-assigned effort" boundary. Fixed by making `effort_slugs` required:
  a consumer must name one or more active effort slugs explicitly, and the
  repo-local scan now serves only to resolve those named efforts from the bound
  state root rather than to auto-adopt every active README by default.
- Documentation updated in `plugins/agent-dispatch/README.md` to call out that
  explicit selector requirement for `global:effort-driver`.
- Focused validation after the fix:
  - `python tools/run-plugin-tests.py agent-dispatch -k effort_driver --timeout 600 --plugin-timeout 2400`
    → **14 passed**

### 2026-10-04 (same day) — Review feedback, round 7
- Automated review's remaining real gaps were the *other* eager-validation path
  and the effort record's own coverage claim:
  1. `registrations.py` still only routed `command` and
     `repository_issue_loop` emitter specs through eager `validate_spec()`,
     so an inline `effort_driver_loop` registration could still bypass that
     validation even though the supervisor launcher path was fixed in round 5.
     Fixed by teaching `validate_registration(RegistrationKind.EMITTER, ...)`
     the new builtin, with a dedicated regression test.
  2. The Phase 8 checklist/journal claimed end-to-end lifecycle coverage more
     strongly than the tests actually demonstrated. Added a lifecycle test that
     starts from a real active effort README, runs `effort_driver_loop.run_tick`
     to author the task payload, then moves that same effort into the archive
     with merged-PR / closed-issue evidence and confirms the queued submitted
     task through the trusted `effort-driver` evaluator.
- Focused validation after the fix:
  - `python tools/run-plugin-tests.py agent-dispatch -k "effort_driver or effort_driver_loop_expansion_builds_periodic_emitter_command or builtin_effort_driver_loop" --timeout 600 --plugin-timeout 2400`
    → **14 passed**

### 2026-10-04 (same day) — Phase 9 docs landed
- Completed the final documentation pass in `plugins/agent-dispatch/README.md`:
  clarified the `extends:` model as the thin-declaration adoption path,
  documented the shipped global recipe set as four base archetypes plus four
  named instantiations, explicitly called out that `backlog-triager`,
  `issue-reproducer`, and `effort-builder` are `repository-issue-loop`
  parameterizations while `effort-driver` is the distinct repo-local
  `effort-driver-loop`, and added provider-state notes for backlog adapters
  (GitHub + Azure DevOps realized; Gitea still the deferred `#4825` stub) and
  reviewer adapters (GitHub only; Azure DevOps/Gitea still open).
- Published concrete migration notes in that README for both a
  hand-written `repository-issue-loop` declaration and a hand-written
  `reviewer-loop` declaration with repo-local emitter wiring, showing each
  verbose direct declaration collapsing to a thin `extends: "global:..."`
  form with only the genuinely repo-specific fields left in place.
- Revised the standing visions in place, in should-be/current-state register:
  `visions/plugins/agent-dispatch/README.md` now describes the shipped
  `extends:` bases and named recipe library as the default reuse surface;
  `visions/plugins/agent-dispatch/repository-issue-loop/README.md` now treats
  GitHub + Azure DevOps backlog providers as realized on the shared surface,
  with Gitea as a separately tracked future adopter; and
  `visions/plugins/agent-dispatch/reviewer/README.md` now treats reviewer
  adoption through shipped recipe bases as the common path while stating
  reviewer-side provider neutrality precisely as partial (GitHub realized,
  Azure DevOps/Gitea adapters still future work).
- Validation for this docs phase: `python tools/check-docs-consistency.py`
  stays green, and the full plugin test/doc gate run for the PR is the
  remaining merge-time confirmation.
- With Phase 9 checked off, every planned phase in this effort is now closed
  except the explicitly deferred, separately tracked follow-ons: Phase 2's
  remaining **Gitea** reviewer adapter work and Phase 3's single-emitter-
  primitive taxonomy refactor. The effort's own Plan is therefore complete
  modulo those named follow-ons. Whether to mark the effort archived under this
  repo's planning convention remains an explicit decision for the orchestrating
  session/operator, not something to do unilaterally here.

### 2026-10-04 (same day) — Phase 2 remainder: Azure DevOps reviewer observation adapter landed; Gitea stays deferred
- Implemented the Azure DevOps half of Phase 2's remaining reviewer-provider
  gap as a **read-only observation surface**, intentionally mirroring the
  existing GitHub adapter's shape rather than inventing a different contract:
  new `azure_devops_provider_adapter.py` adds a pure
  `observe_pr_state(...) -> PRObservation` classifier plus a thin
  `AzureDevOpsPRAdapter` that fetches the raw PR / reviewer / thread / source
  commit state through authenticated `az rest` calls, reusing the exact Azure
  DevOps identity-verification pattern already proven in the backlog adapter
  (`repository_issue_loops.AzureDevOpsProvider`: `connectionData` against
  resource `499b84ac-1321-427f-aa17-267ca6975798`, then project/repository
  verification).
- Reviewer-loop payload refs are now provider-aware rather than
  GitHub-hardcoded: added `review_target_refs.py`, widened reviewer payload
  parsing from only `github-pr:owner/repo#<n>` to also accept
  `azure-devops-pr:organization/project/repository#<n>`, and taught the
  stale-exit provider-cache path (`reviewer_loops._provider_snapshot`) to
  resolve those ADO refs through a provider-disambiguated observation-store
  key. This keeps `pr_review_poll_loop`'s two-argument observer contract
  unchanged, exactly as the Phase 10 poll-loop design intended.
- **Important scope-finding, confirmed by code search before changing
  anything:** there is still **no engine-side verdict-posting API for GitHub
  either** in `agent_dispatch` (no `gh pr review`, no `az repos pr`, no
  provider-specific vote-casting call anywhere in engine code). The standing
  reviewer loop's own charter already relies on the worker agent's direct
  tool access for posting feedback/approvals and for merge/close actions. So
  Phase 2's "verdict posting / merge-close state" wording does **not**
  translate to adding a new engine-owned vote API here; "done" for the ADO
  half is the read-side provider adapter plus provider-aware routing/stale
  lifecycle support.
- Webhook decision: **do not add an Azure DevOps service-hook receiver in
  this slice.** `provider_state_machine.PROVIDER_CAPABILITIES["azure_devops"]`
  already records the empirically observed fidelity split (push for commits /
  review submission / check status, but **poll-only** for thread resolution).
  The existing GitHub webhook module is explicitly a trigger-only receiver;
  adding a second provider-specific trigger service is materially more scope
  than the read-side adapter gap this effort still had open, while the
  declared polling fallback already covers the one ADO event class known not
  to push-notify reliably. The ADO adapter therefore lands the **read-side
  observation primitives** now (adapter, provider-aware payload refs, and
  poll-observer routing helper); a future ADO webhook or live poll-service
  producer can build on those separately.
- Gitea remains explicitly deferred. Mirroring the backlog-side precedent,
  landed a reviewer-surface `GiteaPRAdapter` **stub only** in its own module;
  every method raises `NotImplementedError` pointing back at this effort's
  Phase 2 tracker section. No real Gitea API integration was attempted here.
- Tests:
  - Added `test_azure_devops_provider_adapter.py`, deliberately mirroring
    `test_github_provider_adapter.py`'s split: pure classifier coverage for
    approval/mergeability/holds/revision plus thin-wrapper `az` runner tests,
    along with payload-ref routing / Gitea-stub assertions.
  - Added a reviewer-loop integration regression in `test_verification.py`
    proving an `azure-devops-pr:...` payload ref resolves through the persisted
    provider observation cache for stale-exit evaluation.
  - Focused validation:
    `python tools/run-plugin-tests.py agent-dispatch -k "azure_devops_provider_adapter or reviewer_loop_can_derive_last_commit_at_from_azure_devops_provider_observation_store" --timeout 600 --plugin-timeout 2400`
    → **48 passed**.
- Checklist result after this slice: Azure DevOps reviewer observation is now
  realized and checked off above; the Phase 2 item remains visibly open only
  for the **Gitea** reviewer adapter follow-on. The effort stays **Active**;
  do not archive it yet.

### 2026-10-06 (later) — Validation Plan: 2 of 4 remaining items closed, 2 clarified

Resumed driving this effort's own Validation Plan (its remaining scope
after Phase 2's Gitea close-out and Phase 3's single-emitter-primitive
slice being explicitly spun off separately):

- **Each Phase's own tests, independently:** ran each recipe-library-specific
  test module directly and in isolation (not only as part of a combined
  full-suite run) -- `test_registrar_recipes.py`,
  `test_registrar_discovery.py`, `test_repository_issue_loops.py`,
  `test_effort_driver_loops.py`, `test_cli.py`, `test_registrar_registry.py`
  together: **567 passed, 1 skipped.** Closes this criterion.
- **Full-suite-green:** ran the full `agent-dispatch` suite 3 times on a
  confirmed heavily-loaded shared machine (another concurrent heavy
  test-runner instance held the host admission lock for a long stretch).
  Every recipe-library-specific test module passed cleanly all 3 times, but
  each run had a failure elsewhere in the suite (none in a module this
  effort touches) -- this criterion stays **open** until one complete run
  passes clean. 2 distinct, unrelated timing-sensitive failures were
  observed and filed as their own issues rather than silently dismissed:
  #5582 (a hypothesized coordinator health-loop `in_progress` race --
  confirmed transient, passes cleanly in isolation) and #5584 (a
  companion-readiness test with a hardcoded 3-second real-subprocess
  startup timeout, reproduces even in isolation, traced to a tight
  real-world timeout the surrounding test file's own comments already
  acknowledge as CI-runner-speed sensitive). Filing both is not the same as
  a green run.
- **Real consuming-repo migration:** a consuming repo maintained by this
  session's operator had 4 live, hand-written `kind: repository-issue-loop`
  declarations driving a real standing pipeline -- the same shape the
  effort's own motivating operational finding describes. Before touching
  the live files, validated the migration safely in a side channel: copied
  one declaration to a scratch directory, converted it to
  `extends: "global:repository-issue-loop"` (dropping the now-redundant
  `pool.body.type: headless`), and diffed `agent-dispatch registrar
  discover-repo`'s resolved JSON output before/after -- byte-for-byte
  identical, confirming zero behavior change. Applied the same validated
  transformation to all 4 real declarations, landed and merged the change
  through that consumer's own PR flow, then went back and reconstructed
  each of the 4 real declarations' **pre-change** content from git history
  to diff the **complete, all-4** resolved `discover-repo` output against
  the post-change tree -- byte-for-byte identical for all 4, not just the
  one representative sample.
- **Recipe README cross-check:** read `plugins/agent-dispatch/README.md`'s
  global-recipe table and narrative against `registrar_recipes.py`'s actual
  `GLOBAL_RECIPES` dict entry-by-entry (kind, exclude_labels,
  worker_identity, require_verification, evaluator_ref for all 8 recipes).
  Already fully accurate -- no changes needed.
- **Still open:** the live-fixture-repo/issue/PR validation. Each Phase's
  own "Tests" item (Phases 5-8) used a local/mocked `tmp_path` pytest
  fixture, not a genuinely live GitHub repo/issue/PR set -- confirmed by
  reading the actual test bodies rather than assuming "fixture" meant
  "live." This is a materially larger undertaking (standing up a disposable
  scratch repo, creating real fixture issues, and driving each of the 4
  named recipes' loops to real completion against it) and, together with
  one genuinely clean full-suite run, is what remains of this effort's own
  scope.
- **Next:** get one complete, clean full-suite run (retry once #5582/#5584
  settle or the host machine is less loaded), then stand up the live
  fixture validation -- or make an explicit call with the operator on
  whether this effort can be considered substantially complete and
  archived with those two items tracked as named follow-ons, given Phase
  2's Gitea remainder and Phase 3's single-emitter-primitive slice are
  already being carried the same way.
