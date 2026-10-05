# Pull-Request Capability

- **Slug:** `pull-request-capability`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-15
- **Status:** Active (Phase 2 + Phase 2d landed; Phase 3 next)
- **Vision:** [`plugins/agent-worktrees/pull-requests`](../../../visions/plugins/agent-worktrees/pull-requests/README.md)
  (all three Features: `conformance-verified-mock-provider`,
  `foreign-repo-pr-operations`, `reviewer-capable-provider`)
- **Umbrella issue:** none (three sibling issues below, no umbrella needed at
  this size)
- **Sub-issues:** [#2691](https://github.com/ThomasMichon/copilot-extensions/issues/2691)
  (conformance-verified mock provider),
  [#2700](https://github.com/ThomasMichon/copilot-extensions/issues/2700)
  (foreign-repo addressing),
  [#2699](https://github.com/ThomasMichon/copilot-extensions/issues/2699)
  (reviewer-capable provider)

## Guiding Intent

Close the delta between the `pull-requests` vision and today's `PRProvider`
reality: a PR capability that is author-side-only, CWD-bound to a local
checkout, and has no fabricated provider to test against. Land the three
Features in an order where each phase's foundation makes the next phase
safer and cheaper to build and test, rather than three independent patches
landed in any order.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Designs and lands all three phases | independent per-phase worktree |

## Coordination

- **Topology:** single driver, sequential phases (each phase's own worktree,
  its own PR, `pr-self-merge`).
- **Host (owns PRs):** the driving agent/machine.
- **Delegates:** none yet.
- **Handoff:** each phase closes with `python -m pytest` (or this repo's
  equivalent) green and its PR merged before the next phase's worktree opens;
  a fresh session may pick up at any phase boundary via a stored handoff.

## Context

Repo: `ThomasMichon/copilot-extensions`. Relevant source:
`plugins/agent-worktrees/src/agent_worktrees/providers/base.py` (the
`PRProvider` protocol and `_PROVIDERS` registry: `github`, `gitea`,
`azure-devops` today), its concrete provider modules, and the existing
`plugins/agent-worktrees/tests/test_pr_*.py` / `test_providers.py` suite.

The vision and all three issues were mined from a live private-downstream
clean-room finding: a `code-review` scenario-eval run correctly reported
BLOCKED ("no PR available") rather than fabricate a review under literal
mode -- honest behavior that also surfaced these three gaps in the
underlying PR capability. See the vision's own Provenance for the full
trace; not repeated here (public-artifact rule: keep this effort generic).

## Request

> Carve the effort, then tackle in a handoff.

(Verbatim operator instruction following the vision's landing — see
`visions/plugins/agent-worktrees/pull-requests/README.md` PR #2692.)

## Plan

### Phase 1 — Provider conformance contract + mock provider
- [x] Formalize the existing `test_pr_*.py` / `test_providers.py` suite (or
      a curated subset of it) into an explicit, named conformance contract
      that any `PRProvider` implementation — real or fabricated — is run
      against, per Vision §Features/`conformance-verified-mock-provider`.
      Landed as `tests/test_pr_provider_conformance.py`: a structural layer
      (`TestProviderRegistryConformance`) parametrized across every
      registered provider name (protocol conformance, `name` matches
      registry key, `authority_endpoint`/`head_contained_in_base` contracts)
      needing no transport, plus a full behavioral lifecycle layer
      (`TestMockProviderLifecycle`) run against `mock`.
- [x] Decide the mock provider's fabrication strategy: an in-process fake
      store (simplest, fastest, no external process) vs. a purpose-built MCP
      sub-agent that fabricates PR details/diffs (richer, closer to what a
      driven agent would actually interact with, per the operator's original
      framing). Start with the in-process fake unless the conformance
      contract proves it insufficient — the simpler shape first, escalate
      only if needed. Chose the in-process fake; it satisfied the full
      conformance contract without needing to escalate.
- [x] Implement `MockPRProvider` satisfying the `PRProvider` protocol;
      register it in `_PROVIDERS` as `mock`.
- [x] Run the conformance contract against `mock` and confirm it passes the
      same assertions real providers do (adjusting only what is genuinely
      forge-specific, e.g. exact URL formats).

### Phase 2 — Foreign-repo addressing
- [x] Design the addressing mechanism per Vision
      §Features/`foreign-repo-pr-operations`. Added
      `pr_config.resolve_repo_config_for_slug()`: resolves the repo that OWNS
      a given `owner/name`/ADO `project/repo` slug, independent of the
      caller's active project. Fast path for the caller's own active repo
      (no extra config load); for a *different* registered repo, loads
      **that repo's own** layered config via `config.load_project_config()`
      (never inheriting the caller's); unregistered resolves honestly
      (`ForeignRepoResolution(repo_config=None)`).
- [x] Implement honest failure per Vision
      §Behaviors/`foreign-target-resolves-honestly`: pr-watch/pr-merge now
      refuse and report plainly when an explicit slug names a repo this
      machine can't resolve a PR binding for, instead of silently using the
      active project's own binding (the pre-existing bug this phase fixes).
- [x] Wired foreign-repo addressing through `pr-watch` (`wait`/`cursor`) and
      `pr-merge` (single PR + `--all` sweep, which share one resolution
      point). `create`/`complete`/`ready` and `pr-status` were **not**
      touched this phase — `pr-status` is inherently worktree-scoped (reads
      that worktree's own tracking record, not addressable by a foreign
      slug) and the others don't yet accept an explicit foreign-repo
      positional the way pr-watch/pr-merge already did; left as a follow-on
      if foreign addressing is wanted there too.
- [ ] Extend the conformance contract (Phase 1) to run once against a local
      target and once against a foreign-addressed target, proving the two
      paths converge on the same provider dispatch. **Not done this
      phase** — the conformance contract (Phase 1) exercises `PRProvider`
      implementations directly, not the `pr_config`/CLI-level repo-binding
      resolution this phase added; extending it would mean teaching the
      contract to drive a *second*, foreign-addressed config through the
      same assertions, which didn't fit this slice's scope. Left as a
      concrete next step before Phase 3 reuses this resolver.

### Phase 2d — create-pr: real foreign creation from an already-pushed branch (Done 2026-10-05)
- [x] Design + implement an additive code path in `pr_ops.create_pr()` (or a
      new sibling function) for: `--repo <foreign, registered>` +
      `--from-branch <branch>` (a branch some other process already pushed
      to that repo's remote). Must skip the entire local squash/push/
      title-derivation machinery (none of it applies -- no local checkout),
      resolve the foreign repo's own binding via
      `pr_config.resolve_repo_config_for_slug()`, and call
      `provider.create_pull()` directly against it. **Done**: PR #5289
      (merged, squash, 5 review rounds). New `pr_foreign_create.py` module
      (`pr_ops.py` is already at its grandfathered line-count ceiling)
      implements `create_foreign_pr_from_branch()`, wired into
      `create-pr`'s existing `--repo` foreign-refusal branch via a new
      `--from-branch` flag. Honors the target repo's own `pr.enabled`,
      `required_body_sections` (checked only when actually about to open a
      PR, not on an idempotent reuse), `source_attribution` (full
      tri-state + the `may_publish_codename` provenance gate), and label
      config; normalizes Azure DevOps' own `"active"` status to the
      cross-provider `"open"` literal the claim gate requires; is
      idempotent via `provider.find_pull_by_head()` (reuses a still-open
      PR on retry rather than duplicating, `--new` forces fresh); reloads
      the tracking record under `_RecordLock` immediately before the claim
      write (not a pre-network-call snapshot) to avoid clobbering a
      concurrent update.
- [x] **Decided (2026-10-04, operator-directed):** the CALLING worktree
      always auto-journals a `pr`-kind claim via the existing
      `_ensure_pr_claim`/`add_resource_claim` primitive -- never the manual
      `claims add pr` workaround `venue-and-claims.md` documents today. The
      motivating case is a host agent (e.g. a container/other source
      pushed the remote branch) that wants to open and durably own a PR on
      a repo it has no local checkout of; the whole point is a turn-key
      "create + claim" in one call. Attribution/codename marker uses the
      CALLING worktree's own codename (consistent with every other
      create-pr invocation -- there is no other sensible identity to
      stamp). Label application: apply whatever labels the TARGET repo's
      own config declares for a normal create-pr, same as the local path.
- [x] Give `agent-pull-requests create` the same two behaviors (it already
      supports `--repo`/`--head` with no local checkout, per the Journal
      entry below, but today does neither): require a resolvable claimant
      worktree (shell `agent-worktrees get worktree-id`; refuse with a
      `pr_cli.require_claimant_worktree`-style actionable message if empty,
      since this plugin can't import agent-worktrees internals directly),
      then auto-journal the same `pr`-kind claim via `agent-worktrees claims
      add pr <url> --worktree <id> --json` (best-effort, non-fatal --
      mirrors `context-handoff`'s own `addHandoffClaim` pattern for calling
      across the same plugin boundary). **Done**: same PR #5289.
- [x] Still gated by `require_claimant_worktree()` (already in place from
      the slice above).
- [x] Motivating case: `create-pr --repo <product-repo> --from-branch
      <user>/<topic>-<worktree-suffix> --title "..." --body-file ...` run
      from a harness worktree with no local checkout of `<product-repo>`.
      Covered by `test_pr_foreign_create.py`'s own fixture-driven tests
      (21 tests) and the CLI-dispatch tests in
      `test_pr_create_claimant_guard.py` (9 tests).

### Phase 3 — Reviewer-capable provider
- [ ] Extend the `PRProvider` protocol with reviewer-side operations: read
      current diff/surrounding context, read/post comments, read/resolve
      review threads, publish a verdict — per Vision
      §Features/`reviewer-capable-provider`.
- [ ] Implement across `github`, `gitea`, `azure-devops`, and `mock`.
- [ ] Extend the conformance contract to cover the new operations for every
      provider, including `mock`.
- [ ] Expose the new operations through the CLI alongside the existing
      `pr-*` family.
- [ ] **Carry forward the `@copilot`-mention guard.** `GitHubProvider`
      already hard-blocks a bare `@copilot` mention in `create_pull()`
      (title/body) and `publish_source_marker()` via the shared
      `reject_copilot_mention()` helper in `providers/base.py` (landed in
      #3700, complementing the `copilot-extensions-harness` preToolUse hook
      from #3663 — that hook can't see text posted through a provider's own
      internal transport call). Every new comment/review/verdict-posting
      operation this phase adds to `github.py` must call
      `reject_copilot_mention()` on its caller-supplied text before
      publishing, the same way the two existing operations do. Decide
      per-provider whether `gitea`/`azure-devops` need an equivalent guard
      (only do so if either forge grows a comparable
      mention-invokes-an-autonomous-agent hazard — today `@copilot` has no
      special meaning there, so this is GitHub-only unless that changes).

### Phase 4 — Validate and land
- [ ] Full `plugins/agent-worktrees` test suite passes with all three
      Features implemented.
- [ ] Each phase already landed via its own PR (per-phase, not batched) —
      this phase is a final confirmation pass, not a fourth PR of its own
      unless cleanup is needed.
- [ ] Note whether `agent-dispatch/reviewer` should be updated to compose
      the new reviewer-side operations instead of any forge calls it makes
      today — file as a follow-on issue if it's out of this effort's scope
      rather than silently expanding Phase 3.

## Validation Plan

- [ ] `mock` is a registered `PRProvider` passing the same conformance
      contract as `github`/`gitea`/`azure-devops`.
- [ ] A `pr-*` operation against a named foreign repo (no local checkout)
      resolves that repo's own provider and succeeds or fails honestly —
      demonstrated against `mock` at minimum, ideally against one real
      provider too.
- [ ] Reviewer-side operations (diff, comments, threads, verdict) work
      end-to-end against `mock`, and against at least one real provider.
- [ ] No existing `pr-*` consumer or test regresses.

## Proposal

_Pending._

## Journal

### 2026-10-05 — Phase 2d: foreign PR creation + auto-claim (done)
Operator-directed: implement the `--from-branch` already-pushed-branch
creation mode item 2 of Phase 2d's own Journal entry flagged as the next
real slice, plus the matching behavior for `agent-pull-requests create`.

Landed **PR #5289** (merged, squash) after **5 review rounds**, each
catching a real issue:
- **Round 1** (11 findings): `--from-branch` silently ignored outside the
  resolved-foreign-repo branch (fixed with an upfront validation gate);
  `--dry-run`/`--no-open` ignored (now rejected as incompatible); a blank
  `--title` silently sent to the provider (now required, non-blank);
  `pr_label_error` dropped in the non-JSON success path; the new path
  bypassed `required_body_sections`; the attribution default resolved
  `None` to an unconditional `True` instead of the target repo's own
  `pr.source_attribution`; claim-persistence exceptions could crash after
  the PR already existed; the docs (venue-and-claims.md, pr-workflow.md,
  agent-pull-requests' cli-reference.md) went stale; and the
  module-size-baseline.json edit widened an UNTOUCHED file's ceiling
  through a feature PR (reverted; landed separately as its own small,
  focused **PR #5291**, since the local pre-push hook runs the guard
  unscoped unlike CI's own `--changed-since` check).
- **Round 2**: Azure DevOps' own `create_pull()` returns its native
  `"active"` status, never the cross-provider `"open"` literal the claim
  gate requires -- every successful ADO foreign PR was opening unclaimed;
  the tracking record was being saved from a pre-network-call snapshot
  (a real TOCTOU race against a concurrent claim/settle); the target
  repo's own `pr.enabled` policy wasn't honored; the codename provenance
  gate (`may_publish_codename`) had been dropped entirely while fixing the
  attribution-default bug, re-opening a custom-wordlist-codename leak risk
  CI's own marketplace-isolation guard separately caught a bare
  `agent-pull-requests` command reference in the docs (needed the
  `<agent-pull-requests catalog argv[0]>` indirection every other
  cross-plugin skill reference uses).
- **Round 3**: the claimant-resolution helper caught only `RuntimeError`,
  not `OSError`, from a failed `agent-worktrees` spawn; the raw
  (non-codename) attribution marker used `config.machine`/
  `parent_session` instead of the record's own machine + latest LIVE
  session (the same selection the local path uses) -- a genuine
  caller-identity bug, not just a style nit.
- **Round 4**: `build_marker`'s `head=` parameter documents a commit SHA,
  but the new path was passing the branch NAME; the whole path was
  non-idempotent (a retry against the same `--from-branch` would ask the
  forge to open a duplicate) -- added `provider.find_pull_by_head()` reuse
  (with a `--new`-equivalent escape hatch) matching `create_pr`'s own
  "safe to re-run" contract; `claim_history.record_pr_event()` was called
  INSIDE the record lock, risking starving a concurrent updater since it
  takes its own separate lock and does real I/O.
- **Round 5**: the `required_body_sections` check ran before the new
  reuse-lookup, so an idempotent retry could fail it even when no new PR
  was being opened at all (deferred until genuinely about to create one);
  a reused PR reported the caller's own `--draft` request instead of
  `False` (no draft was actually created that call); `_ensure_pr_claim`
  returns `None` for both a genuine failure AND an already-active
  idempotent no-op, which had been conflated into a bogus "not claimed"
  warning on an otherwise-correct retry.

39 new/updated tests across `pr_foreign_create.py` (21),
`test_pr_create_claimant_guard.py` (9), and `agent-pull-requests`' own
`test_cli.py` (18, one updated). Full `agent-worktrees` suite: 6798
passed, 28 skipped, same 2 pre-existing environment-specific failures
throughout (`test_doctor.py`, `test_registration_home.py`).

**Phase 2d is done.** Phase 3 (reviewer-capable provider) remains.

### 2026-10-03 — create-pr: claimant guard + a real --repo bug found
- Operator caught a gap in the claimant-CWD rollout: `create-pr` didn't get
  the guard, and their worked example
  (`create-pr --repo <product-repo> --from-branch ... --title ...`, run from the
  harness worktree) surfaced something worse than a missing refusal --
  `--repo` on `create-pr` was **cosmetic only**. It relabels the tracked PR
  record's `repo` field, but `pr_ops.create_pr()` still pushes/opens against
  `config.default_repo` (the caller's own repo) unconditionally. So
  `--repo <foreign> --title ...` would have silently pushed local commits
  and opened a PR against the CALLER's own repo while recording it as if it
  targeted the foreign one.
- Added the same `require_claimant_worktree()` check (only when no explicit
  `worktree_id` positional is given -- that stays its own sanctioned
  bypass). Added a new, separate refusal: `--repo` naming a different,
  also-registered repo is rejected outright (no already-pushed-branch mode
  exists yet to push into it from here), pointing at two real
  alternatives: create a worktree of that repo, or use
  `agent-pull-requests create --repo <repo> --head <branch>` (already
  built for exactly this).
- **Did not build** the `--from-branch` already-pushed-branch creation mode
  the operator's example implied wanting (open a PR against a foreign
  repo's existing pushed branch, using create-pr's fuller machinery --
  attribution, labels, tracking). `create_pr()` is ~400 lines of
  local-git-coupled logic (squash, title derivation from commit history,
  branch-reuse semantics) that a from-branch/no-local-checkout path would
  need to skip entirely, not retrofit -- a genuine new code path, not a
  guard addition. Flagged as the next real slice of this effort rather than
  guessed at blind.
- Added `tests/test_pr_create_claimant_guard.py` (6 tests). Landed via PR
  [#5120](https://github.com/ThomasMichon/copilot-extensions/pull/5120).

### 2026-10-03 — Phase 2 refinement: claimant-CWD contract + guidance-rich refusals
- Operator feedback after Phase 2 landed: docs/skills needed to state the
  intended usage explicitly (owning project = CWD, target repo = argument),
  and it should be **invalid** to call these tools from a CWD that can't
  trace back to a valid claimant worktree. Also: every failure path must
  guide the agent back to the correct pattern, never leave it to conclude
  "this tool doesn't work, fall back to gh/az/git directly."
- Added `pr_cli.require_claimant_worktree()`: pr-watch/pr-merge now refuse
  up front when CWD isn't a tracked worktree (untracked dir, bare anchor,
  or the *target*'s own checkout instead of the caller's). Rewrote both
  this and the existing foreign-repo-resolution refusal (from the first
  Phase 2 landing) to spell out the correct invocation and explicitly warn
  against the gh/az/git fallback.
- Documented the contract: `pr-workflow.md` (new *Addressing a foreign
  repo* section), `working-cross-repo/SKILL.md` (distinguishes "create a
  worktree of the target" from "just check/merge via slug, stay in your
  own worktree"), `venue-and-claims.md` (no claim-journal needed for an
  existing foreign PR, just a valid claimant CWD).
- Added a conftest.py autouse fixture: the new CWD resolution was
  previously unmocked across the whole `test_pr_*` suite and read the
  REAL enclosing git worktree of wherever tests happened to run (not
  isolated by the existing HOME-isolation fixture) -- now defaults to a
  fixed test worktree id. New `test_pr_claimant_guard.py` for the guard
  itself.
- 598-test `test_pr_*`/`test_providers.py` run passes (minus the
  independently slow, pre-existing `test_pr_ops.py`). Landed via PR
  [#5096](https://github.com/ThomasMichon/copilot-extensions/pull/5096).

### 2026-10-03 — Phase 2 landed
- Added `pr_config.resolve_repo_config_for_slug()` +
  `ForeignRepoResolution`: resolves the `RepoConfig` that owns an explicit
  `owner/name`/ADO `project/repo` slug, independent of the caller's active
  project. Fast path for the caller's own repo; foreign-but-registered loads
  via `config.load_project_config()` (confirmed this already existed and
  does exactly what the vision describes -- no new config-loading mechanism
  needed, just a slug->name lookup via the `repos` registry); unregistered
  resolves honestly instead of guessing.
- Wired into `pr-watch` (`wait`/`cursor`) and `pr-merge` (single PR +
  `--all`, sharing one resolution point). Confirmed the pre-existing bug
  this fixes: both commands already accepted an explicit foreign repo slug
  but silently built the PR binding from `config.default_repo` regardless --
  so addressing a different registered repo silently used the *wrong*
  repo's provider/token/policy. Now it either resolves that repo's own
  binding or refuses plainly.
- 4 pre-existing tests (`test_pr_merge_now.py` x3, `test_pr_repo_inference.py`
  x1) used fake single-repo configs with no real registry/remote, so they
  relied on the old unconditional fallback; patched them to stub the new
  resolver directly (they test merge/flow mechanics given an
  already-resolved config, not resolution itself) rather than fake a
  registry. Added 5 new tests for the resolver itself
  (`test_pr_config_foreign_repo.py`).
- Full `test_pr_*`/`test_providers.py` suite (487 tests) passes.
  `check-module-size.py`/`check-feed-neutrality.py` pass. Landed via PR
  [#5086](https://github.com/ThomasMichon/copilot-extensions/pull/5086)
  (self-merged, Maintainer bypass).
- **Scoped out of this phase, left as follow-ons:** `create`/`complete`/
  `ready` and `pr-status` weren't touched (see Plan above for why); the
  conformance contract wasn't extended to run against a foreign-addressed
  target (Phase 1's contract tests `PRProvider` implementations directly,
  not this phase's config-level resolver -- doing so cleanly is worth a
  dedicated pass before Phase 3 builds on this resolver).
- Not started: Phase 3 (reviewer-capable provider).

### 2026-09-15 — Kickoff
- Effort created immediately after the `pull-requests` vision landed
  (PR #2692). Filed and linked the three sibling issues (#2691, #2699,
  #2700) that carve its delta. Ordered phases so the conformance
  contract/mock (Phase 1) and foreign-repo addressing (Phase 2) land before
  reviewer-side operations (Phase 3), so the newest surface is built with
  both already in place rather than retrofitted.
- Not started: no implementation yet. Next session should begin Phase 1
  (formalize the conformance contract, decide the mock's fabrication
  strategy, implement `MockPRProvider`).

### 2026-09-15 — Phase 1 landed
- Added `plugins/agent-worktrees/src/agent_worktrees/providers/mock.py`:
  `MockPRProvider`, an in-process fake (`_FakePR` store keyed by repo →
  number) implementing every `PRProvider` protocol method plus test-only
  `add_review`/`add_thread` fabrication helpers. Registered as `"mock"` in
  `providers/base.py`'s `_PROVIDERS`.
- Added `tests/test_pr_provider_conformance.py`: the named conformance
  contract — a structural layer run against all four registered providers
  (no transport needed) and a full behavioral lifecycle layer run against
  `mock` only. Deliberately did not build a unified transport-faking harness
  for the three real providers (`github`/`gitea`/`azure-devops`); their
  existing bespoke `test_providers.py` coverage stands as-is — out of
  Phase 1 scope.
- Validated: `python tools/run-plugin-tests.py agent-worktrees` — new
  conformance suite passes 39/39; full plugin suite passes except two
  pre-existing, unrelated `test_ahp_command.py` failures confirmed present
  on `main` before this change (verified via `git stash`).
- Landed via PR (see this worktree's PR) using `pr-self-merge` per the
  effort's working pattern. Next: open Phase 2's worktree (foreign-repo
  addressing).

### 2026-09-25 — Forward note: @copilot-mention guard now load-bearing on GitHubProvider
- Unrelated work (the `copilot-extensions-harness` preToolUse hook, #3663)
  surfaced that a shell-level guard can't see text an `agent_worktrees`
  provider posts through its own internal `gh` subprocess call. Landed
  #3700 as a direct fix ahead of this effort: `GitHubProvider.create_pull()`
  and `.publish_source_marker()` now hard-block a bare `@copilot` mention
  via a shared `reject_copilot_mention()` helper in `providers/base.py`.
- Added to Phase 3's plan above: every new comment/review/verdict-posting
  operation this phase adds must call the same helper before publishing.
  Not yet implemented (Phase 3 hasn't started) — this is a forward note so
  the guard isn't dropped when that surface lands.
