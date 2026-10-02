# agent-dispatch recipe library (registrar `extends:` unification + named recipes)

- **Slug:** `agent-dispatch-recipe-library`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-30
- **Status:** In Progress (Phase 1 done)
- **Vision:** `visions/plugins/agent-dispatch/README.md` (§*The recipe*)
  advances *loop-recipes* from "four fixed archetypes, hand-declared per
  consumer" to "named, extendable templates a consumer instantiates with a
  handful of params"; `visions/plugins/agent-dispatch/repository-issue-loop/README.md`
  and `visions/plugins/agent-dispatch/reviewer/README.md` advance their own
  already-declared `provider-neutral-backlog-capability` /
  `provider-neutral-review-capability` features from should-be to realized
  (Azure DevOps + Gitea adapters).
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
- [x] Add a Gitea backlog-provider adapter, same surface. **Deferred to
      `ThomasMichon/copilot-extensions#4825`**: per operator direction, a
      structural `GiteaProvider` stub landed (implements `ForgeProvider`;
      `_forge_provider_for` can route to it), but every method raises
      `NotImplementedError` pointing at the tracking issue, and
      `validate_config` deliberately still **rejects** `forge.provider:
      gitea` (accepting it would validate cleanly then fail forever on the
      first tick) until a real adapter lands — real implementation is left
      to a future agent/session with Gitea access and expertise, since no
      integration approach was chosen and no live instance is available
      here to validate against.
- [ ] Add the equivalent forge adapters for the **reviewer** recipe's
      provider-neutral review capability (author/reviewer relationships,
      verdict posting, merge/close state) for Azure DevOps and Gitea.
      **Genuinely new, not yet started** — confirmed the reviewer-side PR
      feed/verdict posting is GitHub-only today
      (`producers/github_pr_review_webhook.py`, no ADO/Gitea equivalent);
      the ADO backlog adapter above does not cover this surface.
- [ ] Tests: adapter contract tests mirroring the existing GitHub adapter's
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
- [ ] Introduce `extends:` in a registrar declaration: a reference to a
      global (plugin-shipped), repo-local, or cross-repo recipe, plus a
      `emitter:`/`evaluator:` block of template-injected or direct param
      values. A global recipe provides the core scripts/prompts; a
      declaration fills in only its own variables (which may themselves be
      script references for the remaining gaps).
- [ ] Ship the four already-existing archetypes (reviewer,
      conflict-resolution, goal-driven, repository-issue-loop) as
      `extends:`-able global recipes under this model, with no behavior
      change to their existing direct-declaration path (backward
      compatible).
- [ ] Tests: an `extends:`-based declaration referencing each existing
      archetype behaves identically to today's direct `kind:` declaration
      with the same effective params; a repo-local and a cross-repo recipe
      reference both resolve correctly.

### Phase 4 — Reviewer-recipe delta (Request item b's remainder)
- [ ] Add a **configurable stale-exit parameter** to the reviewer recipe
      (e.g. `stale_after_days`, measured since the target change's last
      commit) alongside merged/abandoned in its resolution logic. This is a
      **per-declaration param, not an engine constant** — different
      consuming repos/venues need materially different values (e.g. 30 days
      for a slower-moving Azure DevOps backlog vs. 7 days for a fast-moving
      GitHub repo like this one or a harness repo). No default bakes in a
      single "one true" cadence; a declaration that omits the param leaves
      staleness un-checked (never silently applies a guessed default).
- [ ] Confirm (and extend if needed) the reviewer recipe's evaluator uses
      `task-verification-gate`'s `require_verification` +
      suspend-requires-verdict pattern: when a reviewer task suspends
      (`run`-hibernates) without having posted a verdict, the evaluator (or
      the `run`-outage recovery sweep, whichever owns this case) wakes it
      rather than leaving it silently parked.
- [ ] Tests: a reviewer task that suspends without a verdict is woken, not
      left parked; two fixture declarations with different
      `stale_after_days` values (e.g. 7 and 30) each resolve via the
      stale-exit path only once *their own* configured threshold is crossed,
      confirming the parameter is genuinely per-declaration, not a shared
      constant; a declaration with no `stale_after_days` set never triggers
      a stale-exit.

### Phase 5 — Named recipe: backlog-triager (Request item c)
- [ ] Ship a global `extends:`-able recipe parameterizing
      `repository-issue-loop`: prompt requests classification, legitimacy
      check, and priority assignment; evaluator (via the verification-gate
      mechanism) confirms the issue carries the required triage label/marker
      schema and effort assignment.
- [ ] Use Phase 2's GitHub/ADO/Gitea backlog-provider adapters for its
      filterable issue-list emitter; no recipe-specific listing code.
- [ ] Tests: end-to-end against a fixture issue, GitHub adapter first;
      confirm the evaluator's schema/marker check.

### Phase 6 — Named recipe: issue-reproducer (Request item d)
- [ ] Ship a global recipe: attempts reproduction via relevant strategies,
      attaches evidence + a comment, tags reproducible/not-reproducible
      (a "strike" marker on the not-reproducible path).
- [ ] Reuse Phase 2's backlog-provider adapters (same filterable-issue-list
      shape as Phase 5).
- [ ] Tests: a reproducible and a not-reproducible fixture outcome, each
      confirmed via the evaluator's evidence/tag check.

### Phase 7 — Named recipe: effort-builder (Request item e)
- [ ] Ship a global recipe (a `goal-driven` specialization): takes a query or
      a named set of triaged issues, groups related ones, carves/joins a
      tracked effort, and assigns the constituent issues to it — does not
      drive the effort itself.
- [ ] Evaluator requires every named issue assigned to an effort, and that
      effort in PR (this repo's own effort review-gate, or the consumer's
      equivalent).
- [ ] Tests: a fixture set of related issues drives the recipe to a merged
      effort-creation PR with all issues assigned.

### Phase 8 — Named recipe: effort-driver (Request item f)
- [ ] Ship a global recipe (a `goal-driven` specialization): takes an
      assigned effort and drives it — PRs as needed, resolving bugs/hurdles
      — until it reaches archive state via its last PR.
- [ ] Emitter sources from the active efforts in the consumer's own bound
      state root (repo-local; no forge adapter needed here).
- [ ] Evaluator requires the named effort in archive state, with evidence
      the PRs were made and the constituent issues resolved.
- [ ] Tests: a fixture effort with open constituent issues drives to
      archive state with issues closed and PRs merged.

### Phase 9 — Docs
- [ ] `plugins/agent-dispatch/README.md`: document the `extends:` model, the
      six recipes (existing four plus the two truly new engines this effort
      adds none of — clarify that c/d/e/f are named instantiations, not new
      archetypes), and the ADO/Gitea adapters.
- [ ] Update `visions/plugins/agent-dispatch/README.md`,
      `.../repository-issue-loop/README.md`, and `.../reviewer/README.md` to
      mark `provider-neutral-backlog-capability` /
      `provider-neutral-review-capability` realized, and add the `extends:`
      model + four named recipes as realized features.
- [ ] Publish a migration note for a consumer moving a hand-written
      `kind: repository-issue-loop`/`reviewer-loop` declaration (with inline
      custom scripts) onto the new `extends:`-based thin form.

## Validation Plan

- [ ] Full `agent-dispatch` plugin suite green
      (`test-supervisor -- python3 tools/run-plugin-tests.py agent-dispatch`).
- [ ] Each Phase's own tests above pass independently.
- [ ] A real consuming repo's hand-written declaration (the motivating
      operational finding's own lanes, or an equivalent fixture) is migrated
      to an `extends:`-based thin declaration with zero custom script, and
      confirmed behavior-equivalent to the original.
- [ ] `agent-dispatch recipes list` shows all six recipes (four existing +
      backlog-triager + issue-reproducer, with effort-builder/effort-driver
      also present) with their params documented.
- [ ] A live fixture repo/issue/PR set exercises each of the four newly
      named recipes end-to-end per their own Phase's test item.

## Proposal

_Pending review._

## Journal

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
