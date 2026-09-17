# PR Conduct Guidance Consolidation

- **Slug:** `pr-conduct-guidance-consolidation`
- **Repo:** copilot-extensions (primary; touches dotfiles and
  gim-home/odsp-web-harness as dependent phases)
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-16
- **Status:** Near-complete -- one explicitly deferred item (see Phase 5)
- **Vision:** none yet -- a consolidation/DRY fix, not new capability shape.
  Revisit if Phase 1 grows into something vision-shaped.
- **Umbrella issue:** none yet
- **Sub-issues:** none yet

## Guiding Intent

The same PR-conduct facts (self-merge authority, wait/rebase/merge sequence,
which role does what) are independently restated in 8+ places across
dotfiles, odsp-web-harness, and copilot-extensions (`AGENTS.md`,
`CONTRIBUTING.md`, `REVIEW.md`, and `.agent-worktrees/config.yaml` comments in
each), on top of `agent-worktrees`'s own canonical `pr-workflow.md`/`SKILL.md`.
This is genuine drift risk -- it already caused a real incident: harness
`AGENTS.md` claimed "0 required reviews" while the live branch ruleset had
drifted to requiring 1, and self-merge broke silently until diagnosed by hand.

Most of this content is **derivable**, not authored per-repo: `pr.enabled`,
`pr.required`, `pr.merge_actor`, `pr.roles`, and the resolved `pr-profile`
already live in each repo's own `.agent-worktrees/config.yaml`, readable by
`agent-worktrees` at session start. Move the generic, derivable facts into a
single dynamically-assembled, session-scoped guidance blob that
`agent-worktrees` computes and injects (same mechanism as
`dotfiles-harness`/`ai-attribution`'s sessionStart hooks), and trim each
repo's own docs down to only what is genuinely repo-unique policy (e.g.
odsp-web-harness's never-pre-patch-another's-PR rule, its live-validation
exception).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Designs and lands all phases | independent per-phase worktree |

## Coordination

- **Topology:** single driver, sequential phases (each phase its own
  worktree, its own PR, that repo's own merge policy).
- **Host (owns PRs):** the driving agent/machine.
- **Delegates:** none yet.
- **Handoff:** each phase closes with its own repo's test/validation suite
  green and its PR merged before the next phase's worktree opens; a fresh
  session may pick up at any phase boundary via a stored handoff.

## Context

Primary implementation surface: `plugins/agent-worktrees/` in
`ThomasMichon/copilot-extensions` -- its sessionStart hook plumbing
(`hooks.json`, `session-context.json`, `instruction-projections.json`,
mirroring `dotfiles-harness`'s `write_session_guidance.py` pattern) and its
config resolution (`src/agent_worktrees/config.py`, `pr_config.py`).
Consumers to trim: `gim-home/odsp-web-harness` (`AGENTS.md`,
`CONTRIBUTING.md`, `REVIEW.md`, `.agent-worktrees/config.yaml`), the
operator's dotfiles knowledge repo (`AGENTS.md`,
`.agent-worktrees/config.yaml`), and copilot-extensions' own `AGENTS.md`
(dogfood the same trim).

Full audit (repo/file/what's restated) recorded in this session's transcript
2026-09-16; not duplicated here.

## Request

> Do some auditing of where PR guidance exists, across dotfiles,
> odsp-web-harness, and copilot-extensions. Most guidance should live in
> agent-worktrees, and leverage dynamic assembly based on config (via
> session-state injected instructions), with support for per-repo guidance
> derived from the aggregated related-repo config.
>
> Yes, get working on it as a proper effort.

(Verbatim operator instruction.)

## Plan

### Phase 1 -- agent-worktrees: dynamic PR-conduct guidance assembly
- [x] Add a sessionStart computation (new script, e.g.
      `scripts/write-pr-conduct-guidance.{ps1,sh}` + Python body) that
      resolves, for the current repo: `pr.enabled`, `pr.required`,
      `pr.merge_actor`/resolved `pr-profile`, `pr.roles` (if role-aware), and
      any relevant `related.yaml` relationship -- and renders a compact,
      bounded (same budget discipline as `dotfiles-harness`) guidance blob
      naming the resolved facts and the default-conduct rule (already
      documented statically in `pr-workflow.md` as of today; this phase makes
      the *per-repo resolved facts* dynamic, not the policy prose itself).
      Landed as a `PR:` line in the existing `session_context.py` bounded
      context (no new hook needed -- reused the plugin's existing
      Checkout/State/Related sessionStart writer). Role-aware `pr.roles`
      overlay and `related.yaml` relationship were deferred (they need a
      live caller-identity/network resolution the rest of this module
      deliberately avoids); noted as a Phase 5 follow-on candidate.
- [x] Register the session-state destination via a static pointer
      instruction (same `instruction-projections.json` pattern as existing
      plugins), so every repo that has `agent-worktrees` active gets it
      without per-repo authoring. N/A -- reused the existing
      `worktree-context-guide` projection/pointer already wired for this
      context writer; no new projection needed.
- [x] Unit tests for the resolution + rendering (mirroring
      `dotfiles-harness`'s `test_contribution_boundary_hook.py` shape:
      manifest/hook contract, payload emission, budget enforcement, unsafe
      session-id rejection).
- [x] Bump `plugin.json`/`pyproject.toml`/`marketplace.json` versions per
      `CONTRIBUTING.md`; run `tools/check-version-bump.py` +
      `tools/run-plugin-tests.py agent-worktrees`.

### Phase 1b -- agent-worktrees: `pr.notes` free-text guidance channel
- [x] Operator correction mid-effort: agents are expected to access PR config
      via `agent-worktrees repos get`/the `pr-*` verbs, not by reading
      `.agent-worktrees/config.yaml` directly -- so a repo's own comments
      (e.g. "why `bypass_mode: pull_request` not `always`/`exempt`") never
      reach a calling agent. Added `PRConfig.notes: str` (parsed from
      `pr.notes`), threaded through `PRFlowProfile.notes` and
      `classify_pr_flow(..., notes=...)`, and surfaced as an extra `Note:`
      line by `_cautions()` (consumed by every `pr_reminder()` -- the
      "Reminder [...]" text every `pr-*` verb and `push-changes` already
      print, including the direct-profile early-return path, which
      previously hardcoded `cautions=()`).
- [x] Unit tests: `classify_pr_flow`/config-parsing pass-through, empty
      default, and reminder-text inclusion (including the direct-profile
      path).
- [x] Documented in `docs/config-reference.md` (new `pr.notes` row) and
      `pr-workflow.md` (pointer in the Default-conduct section).
- [x] Bump versions; `tools/check-version-bump.py` +
      `tools/run-plugin-tests.py agent-worktrees` (targeted).

### Phase 2 -- odsp-web-harness: trim redundant restatement
- [x] `AGENTS.md` §"Drive every PR you open through to merge": keep only
      genuinely unique policy (never-pre-patch-another's-PR,
      live-validation-exception, source-attribution-marker-reading); replace
      the generic wait/rebase/merge/finalize sequence with a pointer to the
      dynamic guidance.
- [x] `CONTRIBUTING.md` (4 spots) and `REVIEW.md` (1 spot): on closer read,
      predominantly repo-specific (ACL tiers, fork-flow specifics, review
      policy rationale) rather than restated generic mechanics -- left
      unchanged; the originally-scoped audit slightly overstated their
      redundancy.
- [x] `.agent-worktrees/config.yaml`: moved the one genuinely useful,
      non-obvious caution (don't trust a hardcoded review-count claim; check
      live branch-protection state) into `pr.notes` (Phase 1b); trimmed the
      top `pr:` block comment's restated mechanics.
- [x] Run `python tools/validate_harness.py`; land via its own PR flow.

### Phase 3 -- dotfiles: re-scoped after operator correction
- [x] Operator clarified the underlying model: `CONTRIBUTING.md`/`REVIEW.md`
      -style content describing a repo's **own** contribution process is
      legitimate and stays (odsp-web-harness's `CONTRIBUTING.md` is exactly
      that, correctly left alone in Phase 2). The dynamic PR-conduct
      guidance (session `PR:` line, `pr.notes`, `pr-workflow.md`'s
      default-conduct rule) exists for **contributing outward** to *other*
      repos, and the recurring real failure mode is agents getting confused
      about which repo's protocol applies mid-cross-repo-work and leaving a
      PR stuck open -- not repos over-documenting their own process.
- [x] Re-read dotfiles' `AGENTS.md` §571-584 with that lens: it is two
      paragraphs, not one -- "dotfiles' own flow is PR-primary" (legitimate
      self-description, keep, same as odsp-web-harness's `CONTRIBUTING.md`)
      and "Honor every other repo's PR gates" (already correctly points
      outward at the `working-cross-repo` skill and each
      `.agent-worktrees/related/<name>.md`). **No trim needed** -- the
      original audit mis-scoped this phase; there was no actual redundancy
      to remove here.
- [x] Redirected the phase's real work to where cross-repo protocol
      confusion actually gets resolved or missed: the `working-cross-repo`
      skill itself (now Phase 3b).

### Phase 3b -- agent-worktrees: harden working-cross-repo against protocol confusion
- [x] `working-cross-repo` `SKILL.md`'s PR-flow-checking step was missing
      the `pr-self-merge` profile entirely (only listed `direct`/
      `pr-human-merge`/`pr-agent-merge`) and didn't mention the new dynamic
      surfaces (session `PR:` line, `pr.notes`) at all.
- [x] Added the missing profile; pointed at both dynamic surfaces
      explicitly; added an explicit "drive the target's PR through to
      merge, using its resolved profile" instruction (the cross-repo
      analogue of the home-repo default-conduct rule); added an anti-pattern
      entry for the actual failure mode -- leaving a target-repo PR stuck
      open because the protocol was assumed rather than checked.
- [x] Bump versions; land via its own PR flow.

### Phase 4 -- copilot-extensions: dogfood the same corrected lens
- [x] Operator supplied the key distinction that resolves this phase:
      copilot-extensions is **not itself a harness** (nothing treats it as
      a home-base control-plane the way odsp-web-harness/dotfiles are) --
      its `AGENTS.md` only ever controls how a *visiting* agent should
      operate on/in it, for any caller regardless of home repo. Broader
      principle the operator stated for future work (recorded, **not**
      implemented in this effort -- see *Captured principle* below):
      root `AGENTS.md` = universal "how to operate in/on this repo"
      visitor contract; `.github/instructions/*.instructions.md` = the
      mechanism for a *harness* to configure its own session behavior when
      this repo **is** the home base.
- [x] Re-read `AGENTS.md`'s "Branch and Publication" section (~line 184)
      with that lens: it is a textbook visitor contract -- `pr-self-merge`
      profile, the non-blocking-Copilot-review caveat, and (notably) an
      explicit "a PR sitting untouched ... is a stuck PR -- merge it or
      explicitly abandon it" rule that is *exactly* this effort's own
      stuck-PR concern, already well-handled here. **No change needed.**
      Confirms the corrected lens rather than the original (miscalibrated)
      trim assumption.

### Phase 5 -- Validate end-to-end
- [x] Sanity-checked all three repos' real `pr:` config blocks (raw YAML,
      not synthetic fixtures) resolve as expected: odsp-web-harness
      (`enabled: true`, `merge_actor: submitter-direct`, `notes:` set --
      Phase 2), dotfiles (`enabled: true`, `merge_actor: submitter-direct`,
      no `notes` -- correctly none needed), copilot-extensions
      (`enabled: true`, `required: true`, `merge_actor: submitter-direct`
      -- resolves to `pr-self-merge`, matching its `AGENTS.md`'s own
      description). A full live-session re-launch per repo (to observe the
      actual injected `PR:` context line) was not performed this pass --
      the unit tests added in Phase 1/1b already cover the rendering path
      with equivalent synthetic configs.
- [x] Spot-checked: no repo's `AGENTS.md`/`CONTRIBUTING.md`/`REVIEW.md`
      restates *generic* PR mechanics now owned by the dynamic guidance,
      after the Phase 2/3/4 corrections -- each repo's remaining PR content
      is legitimate visitor-contract self-description.
- [ ] `dev.tmichon` (ADO, `bypass_policy: true` self-merge) or any other
      coordinated repo needing the same review is **not checked this
      pass** -- explicitly deferred, not silently dropped; a follow-on for
      whoever picks this effort back up.

## Captured principle (recorded, not implemented in this effort)

Operator-stated target architecture for the wider ecosystem, out of this
effort's original PR-conduct scope but directly adjacent to it:

> `AGENTS.md` at a repo's root should always be the guide for **any** agent
> to operate in/on that repo (the visitor contract) -- correct regardless of
> whether the calling agent's home base is this repo or another one.
> `.github/instructions/*.instructions.md` should be the official surface for
> a **harness** to configure its own session-scoped behavior when operating
> **from** this repo as home base (dynamic, plugin-computed guidance -- the
> pattern `dotfiles-harness`/`ai-attribution`/this effort's own `agent-worktrees`
> session-context work already uses).

This effort's phases happened to land squarely inside that model (Phase
1/1b built exactly the `.github/instructions`-adjacent dynamic-guidance
mechanism; Phases 2-4 confirmed each repo's `AGENTS.md` is already correctly
scoped as a visitor contract, not harness self-config), but a deliberate,
repo-wide audit against this principle -- checking whether *any* plugin's
`AGENTS.md`-shaped content should actually be dynamic instructions instead,
or vice versa -- is a materially larger initiative than PR-conduct alone.
Not undertaken here; worth its own effort/vision if the operator wants it
pursued as a first-class initiative.

## Validation Plan

- [x] `agent-worktrees` sessionStart hook emits a correct, bounded
      PR-conduct guidance blob for at least three distinct `pr-profile`
      values (`direct`, `pr-self-merge`, `pr-human-merge`) exercised by
      existing test fixtures or new ones. Covered by Phase 1's unit tests
      (`test_pr_summary_reports_resolved_profile_and_key_knobs` and
      siblings) via synthetic fixtures; not re-verified against a live
      session restart per repo this pass (see Phase 5 note).
- [x] Each of the three repos' curated docs no longer restates a
      generic/derivable PR-conduct fact; only genuinely repo-unique policy
      remains -- confirmed narrower in practice than originally scoped:
      odsp-web-harness needed a real trim (Phase 2), dotfiles and
      copilot-extensions did not (Phases 3/4 both confirmed legitimate
      visitor-contract content already in place).
- [x] No existing `pr-*` consumer, test, or session-guidance projection
      regresses in any of the four touched repos -- targeted
      `tools/run-plugin-tests.py agent-worktrees` runs green after every
      commit; `tools/validate_harness.py` green for odsp-web-harness;
      `tools/check-version-bump.py` green for every copilot-extensions
      commit.

## Proposal

_Pending._

## Journal

### 2026-09-16 -- Kickoff
- Effort created after auditing PR-conduct guidance duplication across
  dotfiles, odsp-web-harness, and copilot-extensions (prompted by today's
  earlier self-merge/ruleset incident and the just-landed
  `pr-workflow.md`/`SKILL.md` default-conduct update, PR #2796).
- Not started: no implementation yet. Next session should begin Phase 1
  (sessionStart PR-conduct guidance computation in agent-worktrees).

### 2026-09-16 -- Phase 1 landed
- Extended `session_context.render_registry_context` with a `PR:` line
  (profile + enabled/required/merge_actor) resolved live via the existing
  `pr_config._pr_flow_profile`/`.pr` config -- no new hook plumbing needed;
  the plugin already had a bounded sessionStart context writer
  (Checkout/State/Related), so this reuses it rather than adding a parallel
  mechanism.
- Added unit tests (`_pr_summary` resolution, graceful empty on no
  `default_repo`, full-render inclusion) plus a regression check that
  existing `SimpleNamespace()`-config tests are unaffected (empty PR line).
- Validated: `python tools/run-plugin-tests.py agent-worktrees` -- targeted
  suite green (11 passed/3 skipped); full suite hit two **pre-existing**,
  unrelated issues confirmed present on unmodified `main` (a sub-suite
  wall-clock-limit timeout and a flaky `test_handoff_trace.py` concurrency
  assertion) -- not caused by this change.
- Landed via PR #2811 (`pr-self-merge`), version bumped
  1.5.5-dev129 -> dev130 / marketplace 1.7.7-dev114 -> dev115.
- Next: Phase 2 (trim odsp-web-harness's `AGENTS.md`/`CONTRIBUTING.md`
  /`REVIEW.md`/`.agent-worktrees/config.yaml` restatement down to genuinely
  unique policy, pointing at the now-dynamic PR-conduct line instead).

### 2026-09-16 -- Phase 1b: operator correction, `pr.notes` added
- Operator caught a real design gap before Phase 2 started: agents access PR
  config via `agent-worktrees repos get`/the `pr-*` verbs, not by reading
  `.agent-worktrees/config.yaml` directly, so a repo's own YAML comments
  (the exact kind of rationale Phase 2 was about to "trim down to only
  genuinely unique policy") would simply stop reaching agents once removed
  from the file, with nowhere else for them to live.
- Added `PRConfig.notes` (parsed from `pr.notes`), threaded through
  `PRFlowProfile.notes`/`classify_pr_flow`, surfaced as an extra `Note:`
  line by `_cautions()` -- which every `pr_reminder()` call already renders
  as part of the "Reminder [...]" text every `pr-*` verb/`push-changes`
  prints. Also fixed the direct-profile early-return path in `pr_reminder`,
  which had hardcoded `cautions=()` and would have silently dropped notes
  for `pr.enabled: false` repos.
- This changes Phase 2's shape: instead of just deleting config-comment
  prose, genuinely repo-specific rationale (e.g. why a bypass mode is
  `pull_request` and not `always`/`exempt`) moves into `pr.notes` so it
  keeps reaching agents, rather than being lost.
- Validated: targeted `tools/run-plugin-tests.py agent-worktrees` runs green
  across `pr_contract`/`pr_reminder`/`pr_config`/`test_config`/
  `session_context` selectors (278 + 143 passed across two runs, no
  failures).
- Landed via its own PR, version bumped again per `CONTRIBUTING.md`.
- Next: Phase 2, now using `pr.notes` for the carried-forward rationale.

### 2026-09-16 -- Phase 2 landed
- Trimmed odsp-web-harness `AGENTS.md`'s self-merge section: replaced the
  numbered wait/rebase/merge/finalize sequence with a pointer to
  agent-worktrees' own Default-conduct guidance, and fixed the actual stale
  claim that caused the original incident ("GitHub branch protection here
  requires zero approving reviews") with an explicit caution against
  hardcoding a review count at all.
- Added `pr.notes` to odsp-web-harness's `.agent-worktrees/config.yaml`
  carrying that same caution.
- On closer read, `CONTRIBUTING.md`/`REVIEW.md` turned out to be
  predominantly repo-specific (ACL tiers, fork-flow mechanics, review
  rationale) rather than restated generic PR mechanics -- left unchanged;
  narrower actual redundancy than the original audit estimated.
- Landed via PR gim-home/odsp-web-harness#436 (`pr-self-merge`).
- Remaining: Phase 3 (dotfiles trim), Phase 4 (copilot-extensions dogfood
  trim), Phase 5 (end-to-end validation). Paused here for operator check-in
  after landing three PRs across two repos.

### 2026-09-16 -- Operator correction: Phase 3 re-scoped, real gap found
- Operator corrected the model before Phase 3 started: `CONTRIBUTING.md`/
  `REVIEW.md`-style content describing a repo's **own** process is
  legitimate (odsp-web-harness's, correctly left alone in Phase 2); the
  dynamic PR-conduct guidance exists for **contributing outward**. The
  actual recurring failure is agents getting confused about which target
  repo's protocol applies and leaving a PR stuck open mid-cross-repo-work.
- Re-read dotfiles' `AGENTS.md` §571-584 with that lens: two paragraphs, not
  one -- a legitimate self-description (keep) and an already-correct
  outward pointer to `working-cross-repo`. **No trim needed; the original
  audit mis-scoped this phase.**
- Found the real gap in `working-cross-repo` `SKILL.md` itself: its
  PR-flow-checking step was missing the `pr-self-merge` profile entirely
  and didn't mention either new dynamic surface (session `PR:` line,
  `pr.notes`). Fixed both, added an explicit "drive the target's PR through
  to merge" instruction and an anti-pattern entry naming the actual failure
  mode (a PR left stuck because the protocol was assumed, not checked).
- Landed via its own PR (Phase 3b), version bumped again.
- Next: Phase 4 -- re-check copilot-extensions' own `AGENTS.md` with the
  same corrected lens before touching it (likely legitimate
  self-description, same as odsp-web-harness/dotfiles).

### 2026-09-17 -- Phase 4/5: confirmed no-op, principle captured, near-done
- Operator supplied the resolving distinction for Phase 4: copilot-extensions
  is not itself a harness (nothing treats it as home base the way
  odsp-web-harness/dotfiles are), so its `AGENTS.md` only ever needs to be a
  **visitor contract** -- correct for any agent working on it, home-repo or
  not. Re-read its "Branch and Publication" section with that lens: a
  textbook visitor contract already including its own stuck-PR rule
  ("merge it or explicitly abandon it"). **No change needed.**
- Operator also stated the broader target architecture for the ecosystem:
  root `AGENTS.md` = universal visitor contract; `.github/instructions
  /*.instructions.md` = the surface for a harness's own session-scoped
  self-configuration. Recorded as a *Captured principle* section above --
  explicitly **not** implemented here; a full repo-wide audit against it is
  a materially larger initiative than PR-conduct consolidation and belongs
  in its own effort/vision if pursued.
- Phase 5: sanity-checked all three repos' real `pr:` config blocks resolve
  as expected (raw YAML read, not synthetic). Did not re-verify the
  injected session `PR:` line via an actual fresh-session restart per repo
  this pass -- the Phase 1/1b unit tests already cover that rendering path
  with equivalent synthetic fixtures, and a live multi-repo session-restart
  validation was judged lower-value than the phases already completed.
- **Deliberately left open**: whether `dev.tmichon` (ADO) or any other
  coordinated repo needs the same review. Not silently dropped -- recorded
  as the effort's one remaining open item for whoever picks it back up.
- Six PRs landed total across two repos this effort: copilot-extensions
  #2811, #2812, #2814, #2817, #2821 (+this journal update); odsp-web-harness
  #436.
