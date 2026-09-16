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
- [ ] Add a sessionStart computation (new script, e.g.
      `scripts/write-pr-conduct-guidance.{ps1,sh}` + Python body) that
      resolves, for the current repo: `pr.enabled`, `pr.required`,
      `pr.merge_actor`/resolved `pr-profile`, `pr.roles` (if role-aware), and
      any relevant `related.yaml` relationship -- and renders a compact,
      bounded (same budget discipline as `dotfiles-harness`) guidance blob
      naming the resolved facts and the default-conduct rule (already
      documented statically in `pr-workflow.md` as of today; this phase makes
      the *per-repo resolved facts* dynamic, not the policy prose itself).
- [ ] Register the session-state destination via a static pointer
      instruction (same `instruction-projections.json` pattern as existing
      plugins), so every repo that has `agent-worktrees` active gets it
      without per-repo authoring.
- [ ] Unit tests for the resolution + rendering (mirroring
      `dotfiles-harness`'s `test_contribution_boundary_hook.py` shape:
      manifest/hook contract, payload emission, budget enforcement, unsafe
      session-id rejection).
- [ ] Bump `plugin.json`/`pyproject.toml`/`marketplace.json` versions per
      `CONTRIBUTING.md`; run `tools/check-version-bump.py` +
      `tools/run-plugin-tests.py agent-worktrees`.

### Phase 2 -- odsp-web-harness: trim redundant restatement
- [ ] `AGENTS.md` §"Drive every PR you open through to merge": keep only
      genuinely unique policy (never-pre-patch-another's-PR,
      live-validation-exception, source-attribution-marker-reading); replace
      the generic wait/rebase/merge/finalize sequence with a pointer to the
      dynamic guidance.
- [ ] `CONTRIBUTING.md` (4 spots) and `REVIEW.md` (1 spot): remove restated
      Maintainer/Contributor self-merge facts; point at the dynamic guidance
      instead.
- [ ] `.agent-worktrees/config.yaml`: trim comment prose that only restates
      what's now derivable/dynamic; keep comments that explain a
      non-obvious *choice* (e.g. why `bypass_mode: pull_request` not
      `always`/`exempt`).
- [ ] Run `python tools/validate_harness.py`; land via its own PR flow.

### Phase 3 -- dotfiles: trim redundant restatement
- [ ] `AGENTS.md` §571-584 and `.agent-worktrees/config.yaml` comments: same
      trim as Phase 2.
- [ ] Run `python tools/validate-session-context.py` + the plugin test
      suite; land via its own PR flow.

### Phase 4 -- copilot-extensions: dogfood the same trim
- [ ] `AGENTS.md` line ~186 and `.agent-worktrees/config.yaml` comments: same
      trim, in the repo that now owns the canonical + dynamic guidance.
- [ ] Land via its own PR flow (same worktree as Phase 1, or a fresh one --
      decide at execution time based on how large Phase 1 already is).

### Phase 5 -- Validate end-to-end
- [ ] Fresh session in each of the three repos actually surfaces the
      dynamically-injected PR-conduct guidance (not just the static
      `pr-workflow.md` mechanics) and it names the correct resolved
      `pr-profile` for that repo.
- [ ] No repo's `AGENTS.md`/`CONTRIBUTING.md`/`REVIEW.md` still independently
      restates a fact now owned by the dynamic guidance (spot-check via the
      same grep patterns used in the original audit).
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
