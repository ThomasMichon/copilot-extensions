---
name: reviewing-customizations
description: >
  Run a structured review pass over a harness's Copilot CLI customization
  surfaces -- skills, sub-agents, AGENTS.md / custom instructions, hooks, and MCP
  configs. Combines a design critique (a rubber-duck-style review sub-agent) with
  a conformance check against the authoring-skills, defining-subagents,
  registering-mcp-servers, and installing-plugins skills. Use before trusting new
  or changed customizations, or to audit an existing harness.
  Trigger phrases include:
  - 'review my skills'
  - 'review my customizations'
  - 'rubber-duck my agents'
  - 'rubber-duck my skills'
  - 'critique my skills'
  - 'validate my harness'
  - 'audit my customizations'
  - 'check my AGENTS.md'
  - 'review my hooks'
  - 'review my sub-agents'
---

# Reviewing Customizations

A repeatable review pass over the things that make a harness *behave* — its
skills, sub-agents, instruction files, hooks, and MCP configs. Run it whenever
you author or change these, and as the review step (Phase 8) of the
**`building-harnesses`** runbook. Unlike a one-off code review, this is scoped to
Copilot CLI customization surfaces and checks them against the authoring skills
this plugin ships.

## What to review

Gather the harness's customization surfaces:

- **Skills** — every `SKILL.md` under `.github/skills/` (and any plugin skills
  the harness authors).
- **Sub-agents** — every `.agent.md` under `.github/agents/`.
- **Instructions** — root `AGENTS.md` and any nested `AGENTS.md` / custom
  instruction files.
- **Hooks** — `.github/hooks/*.json` (or `hooks.json`).
- **MCP configs** — per-agent `mcp-servers`, project `.copilot/mcp.json` /
  `mcp-config.json`, and any `agent-mcp` bridge configs.

## Method: mechanical scan, then design critique

Run the fast **mechanical scan** first to clear the machine-checkable violations,
then the **design critique** for the judgment calls the scan can't make, and a
**conformance cross-check** against the authoring skills.

### 0. Mechanical scan (repeatable)

Before any hand review, run the bundled scanner over the repo root — it catches
the checkable violations consistently so the human/sub-agent pass can focus on
design:

```bash
python3 <skill-dir>/scripts/scan-customizations.py <repo-root> [--json] [--strict]
```

It reports (BLOCKING vs WARNING) on: **skill frontmatter** (`name` +
`description`), **name/folder match**, **trigger collisions** across skills,
**anti-recursion** (an agent that declares `mcp-servers` but lacks an MCP-readiness
section or an anti-self-delegation line — the hard rule from `defining-subagents`),
**inline secrets** in config files, and **raw IPs** in ssh/scp/rsync commands.
`--strict` exits non-zero on any BLOCKING finding, so it drops into a hook or CI
gate. It is a **heuristic aid, not a proof** — it deliberately under-flags rather
than cry wolf; feed its findings into the design critique, don't treat a clean
scan as a full review.

### 1. Design critique (rubber-duck)

Hand the gathered files to a **review sub-agent** — the Copilot CLI built-in
**`rubber-duck`** task sub-agent where available, or any equivalent reviewer the
harness provides. Ask it for **bugs and design flaws, not style**:

- ambiguous, overlapping, or colliding **trigger phrases** across skills;
- **duplicate or redundant** skills that should merge (context-budget waste);
- **ambient-guidance skills that restate standing rules one-shot** instead of
  pointing at an always-on home — a skill whose body *is* a persona/style/safety
  rule meant to hold for the rest of the session decays after its turn; it should
  **load and enforce** the durable guidance (`AGENTS.md` / a linked doc) rather
  than embed a transient copy (see `authoring-skills` § Action-sequence vs
  ambient-guidance skills);
- **contradictory rules** between `AGENTS.md`, skills, and hooks;
- sub-agents missing the **anti-recursion / MCP-readiness** guard;
- **footguns** — destructive commands without confirmation, hardcoded paths,
  raw IPs in SSH, secrets in config;
- instructions that tell the agent to *do* something no surface can express
  (e.g. expecting a hook to originate a turn).

Feed it the actual file contents (not summaries) and act on high-signal
findings.

### 2. Conformance check (authoring skills)

Cross-check each artifact against the skill that governs its format:

| Artifact | Check against | Look for |
|----------|---------------|----------|
| Skills | **`authoring-skills`** | frontmatter (`name`, `description` with triggers), folder convention, description length, discoverable triggers |
| Sub-agents | **`defining-subagents`** | `.agent.md` frontmatter, valid `tools` aliases, per-agent MCP ownership, anti-recursion pattern |
| MCP servers | **`registering-mcp-servers`** | registration scope (per-agent vs project vs global), config shape, env substitution, no inline secrets |
| Plugin registration | **`installing-plugins`** | repo `settings.json` (`extraKnownMarketplaces` + `enabledPlugins`), experimental mode, payload-vs-runtime, no "just in case" plugins |
| Instructions | this skill + `authoring-skills` | `AGENTS.md` points at skills instead of restating them; rules are consistent and non-redundant |

## Output and follow-through

Produce a **prioritized findings list** (blocking vs non-blocking), each with the
file and the concrete fix. Then:

- **Fix the minor issues in place** — trigger tweaks, missing frontmatter,
  format nits, obvious contradictions — with atomic commits.
- **Surface the structural ones** to the operator — skills that should merge,
  a missing anti-recursion guard, an instruction that needs a new surface —
  before acting, since they change design.

Re-run after fixes until the design critique is clean and every artifact
conforms.
