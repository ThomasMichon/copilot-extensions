---
name: building-harnesses
description: >
  Turn a repo into an effective agent control-harness with the copilot-extensions
  plugin suite -- greenfield ("make me a control repo"), brownfield ("build out my
  harness"), or audit ("make sure my repo follows best practices"). Routes to and
  drives the opinionated Control-Harness Runbook: repo structure, repo-scoped
  plugin registration, agent-worktrees adoption, AGENTS.md + connective-tissue
  skills, SSH/agent-bridge, Picker validation, efforts/visions, skill/agent
  review, and agent-mcp delegation.
  Trigger phrases include:
  - 'build my harness'
  - 'build out my harness'
  - 'make me a control repo'
  - 'set up a control harness'
  - 'turn this repo into an agent harness'
  - 'convert this repo into a harness'
  - 'harness best practices'
  - 'audit my harness'
  - 'control-harness runbook'
---

# Building Harnesses

Drive a repo to an effective **agent control-harness** using the
copilot-extensions plugins. This skill is the in-session entry point to the
canonical, opinionated **Control-Harness Runbook**; the runbook is the
procedure, this skill routes you to it and frames the run.

## First: get the runbook

The runbook is the source of truth. Read it before acting.

- **Local checkout present** (you are inside or beside a `copilot-extensions`
  checkout): read `docs/harness-runbook.md`.
- **No checkout** (fresh agent in a vanilla folder): fetch it from the repo:
  `https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/docs/harness-runbook.md`
  (and the repo `README.md` for concepts).

## Detect the mode, then run the same phases

| Mode | Operator ask | Behavior |
|------|--------------|----------|
| **Greenfield** | "make me a control repo like this" | Create everything from scratch. |
| **Brownfield** | "build out my harness like this" | Add what's missing; never touch product code. |
| **Audit** | "make sure my repo follows best practices" | Run each phase's "Done when" as a checklist; fix drift in place. |

## The opinion contract (do not blur it)

- **Opinionated — the harness itself:** repo structure, repo-scoped plugin
  registration, agent-worktrees adoption of the harness + related repos,
  `AGENTS.md` + connective-tissue skills, SSH + agent-bridge, Picker validation,
  efforts + visions, skill/agent review, agent-mcp + MCP delegation.
- **Unopinionated — the product:** target repo structure, where product code
  lives (don't force this repo's product organization onto a *related* repo, and
  don't copy a related repo's product in — but a harness may itself be a monorepo
  that also holds its own product), git provider, issue provider, PR/review
  policy, execution substrate (local / Codespaces / containers), voice/personality,
  and any extra control mechanisms.

On an unopinionated seam, **ask** the operator (structured question); on an
opinionated one, apply the opinion and move on.

## Phase map (full detail in the runbook)

0. Prereqs + experimental mode.
1. Repo structure (harness scaffold; keep helpers lightweight).
2. Register repo-scoped plugins in `.github/copilot/settings.json` (skill:
   `installing-plugins`), restart, deploy runtimes (skill:
   `copilot-extensions-setup`).
3. Adopt the harness + register/link related target repos (skills:
   `agent-worktrees-repos`, `agent-worktrees-related`, `working-cross-repo`).
4. `AGENTS.md` + connective-tissue skills (skills: `authoring-skills`,
   `defining-subagents`). **Standing/ambient rules** (persona, style, safety,
   cross-repo sequencing) are materialized into the always-on `AGENTS.md` and the
   skill load+enforces them — never embedded as a decaying one-shot copy (the
   ambient-guidance principle; the "install a persistent rule into `AGENTS.md`"
   seam lives in `installing-plugins`).
5. SSH mesh + agent-bridge topology (skills: `agent-ssh`, `agent-bridge`).
6. Validate end-to-end through the **Picker**.
7. Enable efforts + visions (skills: `efforts-setup`, `visions-setup`).
8. Review skills + agents (skill: **`reviewing-customizations`**).
9. agent-mcp + delegate MCP handling to sub-agents (skills: `agent-mcp`,
   `defining-subagents`).

## Curate the plugin set

The suite is large; enable the tier the harness needs, not all of it. Minimum:
`agent-worktrees` + `customizing-copilot`. Recommended default adds `efforts`,
`visions`, `agent-bridge`, `context-handoff`. Everything else is opt-in by
substrate/need. The runbook's "Recommended plugin set" table is the curation
seam.

## Finish

Follow each phase's **"Done when"** check. For Phase 8, hand off to the
`reviewing-customizations` skill. Land changes through the harness's own
contribution flow (unopinionated seam — its branch/PR policy, not this repo's).
