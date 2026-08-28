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
- **Sub-agents** — every `.agent.md` under `.github/agents/` and
  `.claude/agents/`, plus agents shipped by the plugins enabled for this repo.
- **Instructions** — root `AGENTS.md` and any nested `AGENTS.md` / custom
  instruction files.
- **Hooks** — `.github/hooks/*.json` (or `hooks.json`).
- **MCP configs** — per-agent `mcp-servers`, project `.mcp.json` /
  `.github/mcp.json`, user `~/.copilot/mcp-config.json` if it is relevant to
  the loaded session, plugin `mcpServers`, and any `agent-mcp` bridge configs.

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
**anti-recursion** (a Task-capable agent without an agent-specific
anti-self-delegation line), **MCP readiness** (an MCP-owning agent without a
`## MCP Readiness` section), **agent-mcp fallback** (an agent-mcp-backed agent
without an equivalent materialized CLI fallback), **inline secrets** in config
files, and **raw IPs** in ssh/scp/rsync commands.
`--strict` exits non-zero on any BLOCKING finding, so it drops into a hook or CI
gate. It is a **heuristic aid, not a proof** — it deliberately under-flags rather
than cry wolf; feed its findings into the design critique, don't treat a clean
scan as a full review.

Add `--context-budget` for a reproducible, counts-only inventory:

```bash
python3 <skill-dir>/scripts/scan-customizations.py <repo-root> \
  --from-settings --context-budget
```

It counts Unicode characters, UTF-8 bytes, words, and estimated tokens using the
fixed heuristic `ceil(Unicode characters / 4)`. It separates always-loaded repo
instructions, nested/conditional `AGENTS.md`, standard personal Copilot
instructions, `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` payloads, enabled skill/agent
frontmatter metadata upper bounds, `additionalContext`-capable command-hook
registrations, prompt-hook registrations, and other hook registrations. Dynamic
payload size remains unknown; prompt-hook payloads are reported separately and
are not counted as `additionalContext`.
JSON output includes a stable `context_budget` object.

The report prints paths and counts only. It never dumps instruction contents or
hook commands, and it **never executes hooks merely to measure them**. The token
estimate is a comparison heuristic, not a tokenizer result; metadata is an upper
bound, and dynamic context remains unknown until runtime. The budget excludes
runtime MCP tool schemas unless an authoritative runtime measurement is
available. MCP configuration bytes are not rendered tool-schema cost and must
not be reported as though they were.

**Scan the plugin set actually LOADED for the repo — `--from-settings`.** Trigger
collisions are computed from both the structured `Trigger phrases include:` list
**and** inline prose (`Use when asked to "…"`) — a skill hides no triggers by
choosing prose. But the bigger blind spot is *which skills are even in scope*: a
harness that *consumes* plugins can mis-route when a **local** skill collides
with a **plugin** skill, and (for a repo that packages its own skills as in-repo
`.ai` plugins) the repo's *own* owned skills live outside `.github/skills`.
`--from-settings` resolves the repo's `.github/copilot/settings.json` (+ user
settings) `enabledPlugins` / `extraKnownMarketplaces` into the concrete loaded
set and brings each into scope:

```bash
# review against exactly what this repo loads (in-repo .ai plugins fully
# checked; external marketplace plugin agents advisory + source-classified):
python3 <skill-dir>/scripts/scan-customizations.py <repo-root> --from-settings
```

- An **in-repo `directory` marketplace** plugin (e.g. `./.ai`) is *owned* — it
  gets the full frontmatter / name-folder / trigger checks, closing the gap
  where a repo's own `.ai` skills were invisible to the scan.
- An **external marketplace** plugin is *advisory*: its skills join the
  collision map (so a `LOCAL ↔ PLUGIN` clash is visible), and its Task-capable
  agents receive anti-self-delegation / MCP-readiness / agent-mcp-fallback
  checks. Findings are warnings tagged with plugin origin, installed version,
  source, and the upstream contribution path because the consumer cannot edit
  the installed payload.
- When an editable `plugins/*` suite skill or agent matches an installed copy
  by plugin and item name, the editable source wins. The scanner does not
  report stale installed copies as external collisions or agent advisories.
- A Task-disabled agent whose explicit `tools` list omits `agent` / Task is
  exempt from the anti-self-delegation check. A coordinator agent is not exempt:
  it may delegate other types when authorized, but it must still forbid another
  copy of itself.

Collision owners are tagged with their origin (`skill [marketplace/plugin]`).
(The older `--include-installed` / `--include-plugins DIR` still work — they add
a raw installed-plugin tree the same advisory way — but `--from-settings`
is preferred because it scopes to the *enabled* set, not every installed
plugin.)

**A finding that touches an external plugin is outside your repo's control.**
The scan says so, names the upstream `source` and version, and points at the fix
path. You can't edit the plugin in-repo, so choose:

1. **In-repo workaround** — reclaim the phrase with a local authority-override
   skill, disable the offending plugin for this repo, or narrow *your* trigger.
2. **Upstream fix** — file an issue / open a PR on the plugin's source repo. If a
   **`<repo>-harness`** plugin is enabled for that source, use its
   **`contributing-to-<repo>`** skill as the concrete fix path (for the
   copilot-extensions suite that's **`copilot-extensions-harness` →
   `contributing-to-copilot-extensions`**). This `<repo>-harness → contributing`
   hop is the **skill bridge**: it turns "this is broken in an external plugin"
   into "here is exactly where and how to fix it."

Some collisions are intentional (an authority override that deliberately
reclaims a phrase); judge each in the design critique rather than "fixing" it
blindly. And **never edit an external plugin's installed payload in place** — it
is overwritten on update; fix it in-repo or upstream.

### 1. Design critique (rubber-duck)

Hand the gathered files to a **reviewer** — the Copilot CLI **`/rubber-duck`**
critique command where available, a harness-provided review sub-agent, or an
equivalent independent reviewer. Ask it for **bugs and design flaws, not style**:

- ambiguous, overlapping, or colliding **trigger phrases** across skills;
- **duplicate or redundant** skills that should merge (context-budget waste);
- **ambient-guidance skills that restate standing rules one-shot** instead of
  respecting the authoritative owner — a skill whose body *is* a
  persona/style/safety rule meant to hold for the rest of the session decays
  after its turn. Repository-owned invariants stay in `AGENTS.md`; plugin-owned
  policy should be injected by the plugin as a concise context kernel; detailed
  procedures stay in the skill (see `customizing-copilot:authoring-skills`
  § *sessionStart context injection*);
- **contradictory rules** between `AGENTS.md`, skills, and hooks;
- Task-capable sub-agents missing the agent-specific **anti-recursion** guard,
  and MCP-owning agents missing readiness / equivalent fallback behavior;
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
| Sub-agents | **`defining-subagents`** | `.agent.md` frontmatter, bounded direct-execution contract, Task-capability, per-agent MCP ownership, anti-recursion pattern |
| MCP servers | **`registering-mcp-servers`** | registration scope (per-agent vs project vs global), config shape, env substitution, no inline secrets |
| Plugin registration | **`installing-plugins`** | repo `settings.json` (`extraKnownMarketplaces` + `enabledPlugins`), payload-vs-runtime, no "just in case" plugins |
| Instructions | this skill + `authoring-skills` | `AGENTS.md` is a lean map with repository-owned invariants/fail-safes; plugin ambient policy uses config-backed injection; skills hold detailed procedures; headless/cloud fallback coverage remains |

## Output and follow-through

Produce a **prioritized findings list** (blocking vs non-blocking), each with the
file and the concrete fix. Then:

- **Fix the minor issues in place** — trigger tweaks, missing frontmatter,
  format nits, obvious contradictions — with atomic commits.
- **Surface the structural ones** to the operator — skills that should merge,
  a missing anti-recursion guard, an instruction that needs a new surface —
  before acting, since they change design.
- **Treat external-plugin findings as upstream work.** Do not edit the installed
  payload. Configure/disable it locally or use the reported source and
  contribution path to fix the owning repository.

Re-run after fixes until the design critique is clean and every artifact
conforms.
