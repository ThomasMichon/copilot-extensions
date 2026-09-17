# AGENTS.md vs .github/instructions Split

- **Slug:** `agents-md-vs-instructions-split`
- **Repo:** copilot-extensions (primary; touches `gim-home/odsp-web-harness`
  and the operator's dotfiles knowledge repo as audit targets)
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-17
- **Status:** Done; pending archive -- all acceptance criteria satisfied;
  `ThomasMichon/copilot-extensions#2825` closed.
- **Vision:** none yet -- a formalization + audit, not new capability shape.
- **Umbrella issue:** `ThomasMichon/copilot-extensions#2825`
- **Sub-issues:** none

## Guiding Intent

Surfaced during `efforts/active/pr-conduct-guidance-consolidation` (see its
"Captured principle" section): a repo's root `AGENTS.md` should always be the
**universal visitor contract** -- correct guidance for *any* agent operating
in/on that repo, regardless of whether the calling agent's home base is this
repo or another one. `.github/instructions/*.instructions.md` should be the
official surface for a **harness** to configure its own session-scoped
behavior when operating **from** that repo as its home base (the dynamic,
plugin-computed pattern `dotfiles-harness`/`ai-attribution`/`agent-worktrees`'s
own session-context work already use via `instruction-projections.json` + a
`sessionStart` hook).

That effort confirmed the principle in miniature for PR-conduct content only
(odsp-web-harness needed a real trim; dotfiles and copilot-extensions' own
`AGENTS.md` were already correctly scoped) but did not perform a deliberate,
repo-wide audit against it. This effort tracks that larger initiative.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Designs and lands all phases | independent per-phase worktree |

## Coordination

- **Topology:** single driver, sequential phases (each phase its own
  worktree, its own PR, that repo's own merge policy).
- **Host (owns PRs):** the driving agent/machine.
- **Delegates:** none.
- **Handoff:** each phase closes with its own repo's validation green and its
  PR merged (or explicitly deferred) before the next phase starts; a fresh
  session may pick up at any phase boundary via a stored handoff.

## Context

Primary implementation surface: this repo's own docs/skills (the durable
principle write-up) plus an audit pass over:

- This repo's own plugins (`plugins/*/AGENTS.md` where present, and each
  plugin's `instruction-projections.json`/`sessionStart` hooks).
- `gim-home/odsp-web-harness`'s root `AGENTS.md` (already partially reviewed
  for PR-conduct only in the prior effort -- extend to its full content).
- The operator's dotfiles knowledge repo's root `AGENTS.md` (same extension).

## Request

> (Following the just-completed `pr-conduct-guidance-consolidation` effort)
> "Yes, do that" (file the captured architecture principle as its own
> issue/effort) -- filed as `ThomasMichon/copilot-extensions#2825`. Then:
> "In a handoff, drive this overhaul upstream."

(Paraphrased operator instruction, relayed via handoff.)

## Plan

### Phase 1 -- Document the principle durably
- [ ] Add a new pattern doc, `docs/patterns/agents-md-vs-instructions-split.md`,
      stating the split, its rationale, and a short audit checklist/heuristic.
- [ ] Reference it from `plugins/customizing-copilot/skills/authoring-harness-plugins/SKILL.md`
      (item 5, "Own generic ambient policy") and from
      `docs/patterns/session-scoped-dynamic-guidance.md`'s "See Also".
- [ ] Bump `plugin.json`/`pyproject.toml`/`marketplace.json` versions for
      `customizing-copilot` (doc lives under `docs/`, but the SKILL.md
      cross-reference is a plugin payload change); run
      `tools/check-version-bump.py`.

### Phase 2 -- Audit this repo's own plugins
- [x] Enumerate `plugins/*/AGENTS.md` -- confirmed **none exist**; no
      plugin ships its own root-level `AGENTS.md`. Each plugin's
      `instruction-projections.json`/`instructions/*.instructions.md`
      content is the harness-self-config dynamic half for *consumers* that
      enable the plugin (correctly scoped -- these are not this repo's own
      visitor contract).
- [x] Audited this repo's own root `AGENTS.md` in full (414 lines): map/
      orientation, contribution rules, branch/publication policy,
      version-bump requirements, test/deploy instructions, code standards,
      "what not to do", key files. All genuinely durable, universal
      visitor-contract content -- **no misplacement found**; confirms and
      extends the prior effort's Phase 4 finding beyond PR-conduct alone.
- [x] No corrections needed for this repo.

### Phase 3 -- Audit gim-home/odsp-web-harness
- [x] Re-read its root `AGENTS.md` in full (beyond the PR-conduct section
      already reviewed in the prior effort) against the principle --
      confirmed the current worktree state already reflects PR #436's fix
      (trimmed PR-conduct restatement); no further misplacement found in
      the remaining sections (two-repo model, harness plugin enablement,
      worktree/knowledge resolution, statelessness invariant, persisting
      knowledge). All durable, universal visitor-contract content.
- [x] No corrections needed.

### Phase 4 -- Audit the operator's dotfiles knowledge repo
- [x] Re-read its root `AGENTS.md` in full (839 lines) against the
      principle. Found **one confirmed misplacement**: the "Remote Dispatch
      (Agent Bridge)" section hardcoded "Agent-bridge is a persistent
      service (port 9280)" -- a live, OS-assigned/discoverable value (per
      agent-bridge's own README: "runs as a local HTTP service on an
      OS-assigned loopback port by default... discovers it there, so
      callers should use `agent-bridge status` rather than hardcoding a
      port") restated as static prose -- the exact failure mode this audit
      targets.
- [x] Corrected via a dedicated paired knowledge worktree, landed
      direct-to-`main` (dotfiles' own direct-commit flow): replaced the
      hardcoded port with a pointer to `agent-bridge status` /
      `~/.agent-bridge/active.json`.
- [x] Rest of the file (identity, `.ai` marketplace, plugin naming
      convention, sub-agent delegation, remote dispatch mechanics minus the
      port claim, working-across-repos, git/commit policy, repo structure,
      key conventions, ICM management, persisting-knowledge, visions/
      efforts, issue tracking) is durable, universal visitor-contract
      content -- no further misplacement found.

### Phase 5 -- Close out
- [x] Updated `ThomasMichon/copilot-extensions#2825`'s acceptance criteria
      checklist and closed it -- all items satisfied.

## Validation Plan

- [x] The new pattern doc exists, is linked from at least one skill, and
      states a testable heuristic (not just prose).
- [x] Each of the three audited repos has an explicit finding recorded
      (either "already correctly scoped" or "corrected via PR #N") for its
      full `AGENTS.md`, not just PR-conduct content. (this repo:
      already-correct; odsp-web-harness: already-correct, post-#436;
      dotfiles: corrected direct-to-`main`.)
- [x] `ThomasMichon/copilot-extensions#2825`'s acceptance criteria are all
      checked and the issue is closed.

## Proposal

_Pending._

## Journal

### 2026-09-17 -- Kickoff
- Effort created from a handoff continuing
  `pr-conduct-guidance-consolidation`'s captured-but-unimplemented principle.
- Not started: no implementation yet. Next: Phase 1 (durable principle
  write-up).

### 2026-09-17 -- Phase 1 landed
- Added `docs/patterns/agents-md-vs-instructions-split.md`: the principle,
  a per-paragraph audit heuristic (audience + resolvability), and the
  worked PR-conduct precedent. Cross-referenced from
  `authoring-harness-plugins` SKILL.md and
  `session-scoped-dynamic-guidance.md`.
- Bumped `customizing-copilot` `plugin.json` + marketplace catalog version
  (`0.1.0-dev73` -> `dev74`); `tools/check-version-bump.py` clean.
- Landed on `worktree/tmichon-cloud1-win-20260917-114502-b7ce` (this
  worktree); PR to be opened via `create-pr` in the same worktree once
  remaining phases land, or split -- see next entries.

### 2026-09-17 -- Phase 2 audit: this repo's own plugins + root AGENTS.md
- Confirmed no `plugins/*/AGENTS.md` files exist in this repo.
- Full re-read of this repo's own 414-line root `AGENTS.md` against the
  heuristic: no misplacement found. Every section is durable,
  universal-visitor-contract content (repo map, contribution rules,
  publication/version-bump policy, test/deploy mechanics, code standards).
  No change needed here.

### 2026-09-17 -- Phase 3 audit: gim-home/odsp-web-harness
- Re-read the full current `AGENTS.md` in the active odsp-web-harness
  worktree (confirmed up to date with merged PR #436, which already fixed
  the PR-conduct restatement). No further misplacement found across the
  rest of the file (two-repo model, harness plugin enablement, worktree/
  knowledge resolution, statelessness invariant, persisting-knowledge
  routing). No change needed.
- Note: the registered *anchor* checkout (`C:\Data\Src\odsp-web-harness`)
  is >100 commits stale (never fetched/updated) and briefly produced a
  false-positive reading of the old, unfixed PR-conduct section -- always
  audit from an up-to-date worktree, not the anchor.

### 2026-09-17 -- Phase 4 audit + fix: dotfiles
- Full re-read of the 839-line `AGENTS.md`. Found one confirmed
  misplacement: "Agent-bridge is a persistent service (port 9280)" hardcodes
  a live, OS-assigned/discoverable value as static prose -- agent-bridge's
  own README states the default port is dynamic and discovered via
  `agent-bridge status` / `~/.agent-bridge/active.json`. This is exactly the
  failure mode the principle targets (cf. the odsp-web-harness review-count
  incident).
- Corrected via a freshly carved paired knowledge worktree (the prior
  harness/knowledge pairing had already been finalized) and landed
  direct-to-`main` via dotfiles' own direct-commit flow
  (`agent-worktrees push-changes`), then finalized the worktree.
- Rest of the file audited and confirmed as legitimate, durable
  visitor-contract content (identity/placeholders, `.ai` marketplace, plugin
  naming grammar, sub-agent delegation, cross-repo guard rules, git/commit
  policy, repo structure, key conventions, ICM management,
  persisting-knowledge routing, visions/efforts, issue tracking). No further
  correction made.
- Next: Phase 5 -- update and close `ThomasMichon/copilot-extensions#2825`'s
  acceptance criteria, and land this worktree's Phase 1/2/3 changes via
  copilot-extensions' own PR flow.

### 2026-09-17 -- Phase 5: closed out
- Landed the Phase 1 (principle doc) + Phase 2/3/4 journal updates via
  `ThomasMichon/copilot-extensions#2831` (squash-merged, `pr-self-merge`).
- Closed `ThomasMichon/copilot-extensions#2825` with a summary comment
  covering all four acceptance criteria. Effort complete.
