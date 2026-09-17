# PR Conduct Guidance Consolidation

- **Slug:** `pr-conduct-guidance-consolidation`
- **Repo:** copilot-extensions (primary; touches dotfiles and
  gim-home/odsp-web-harness as dependent phases)
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-16
- **Status:** In progress
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
- [ ] Re-check `AGENTS.md` line ~186 with the Phase 3 correction in mind --
      likely legitimate self-description (this repo's own PR-required
      `pr-self-merge` profile), not restated generic mechanics. Confirm
      before touching; do not trim on the original (miscalibrated)
      assumption.
- [ ] Land via its own PR flow if any change is actually warranted.

### Phase 5 -- Validate end-to-end
- [ ] Fresh session in each of the three repos actually surfaces the
      dynamically-injected PR-conduct guidance (not just the static
      `pr-workflow.md` mechanics) and it names the correct resolved
      `pr-profile` for that repo.
- [ ] Spot-check that no repo's `AGENTS.md`/`CONTRIBUTING.md`/`REVIEW.md`
      restates *generic* PR mechanics now owned by the dynamic guidance --
      but do **not** flag a repo's legitimate self-description of its own
      contribution process as redundant (Phase 3's correction).
- [ ] Note whether `dev.tmichon` (ADO, `bypass_policy: true` self-merge) or
      any other coordinated repo needs the same trim -- file a follow-on
      issue if out of this effort's scope rather than silently expanding it.

## Validation Plan

- [ ] `agent-worktrees` sessionStart hook emits a correct, bounded
      PR-conduct guidance blob for at least three distinct `pr-profile`
      values (`direct`, `pr-self-merge`, `pr-human-merge`) exercised by
      existing test fixtures or new ones.
- [ ] Each of the three repos' curated docs no longer restates a
      generic/derivable PR-conduct fact; only genuinely repo-unique policy
      remains.
- [ ] No existing `pr-*` consumer, test, or session-guidance projection
      regresses in any of the four touched repos.

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
