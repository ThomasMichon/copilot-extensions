---
name: authoring-extensions
description: >
  Author Copilot CLI extensions -- skills, sub-agents, MCP servers, hooks, and
  custom instructions -- and lay out a skill's folder (SKILL.md + references/,
  scripts/, assets/) following Agent Skills best practices. Use when creating or
  editing a SKILL.md, defining a custom agent, registering an MCP server, writing
  a hook, adding custom instructions, or organizing a skill's companion files.
  Trigger phrases include:
  - 'create a skill'
  - 'new skill'
  - 'SKILL.md'
  - 'skill folder structure'
  - 'skill best practices'
  - 'skill audit'
  - 'custom agent'
  - 'sub-agent'
  - 'MCP server'
  - 'register an MCP'
  - 'write a hook'
  - 'custom instructions'
  - 'agent definition'
---

# Authoring Copilot CLI Extensions

How to extend and customize the Copilot CLI -- skills, sub-agents, MCP servers,
custom instructions, and hooks -- plus the folder conventions that keep a skill
discoverable and token-efficient. This skill supplements knowledge the Copilot
CLI does not ship with natively.

Reference documentation:

| Feature | URL |
|---------|-----|
| Overview | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/overview |
| Skills | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-skills |
| **Skill Best Practices** | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices |
| Custom agents | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli |
| Agent config reference | https://docs.github.com/en/copilot/reference/custom-agents-configuration |
| MCP servers (CLI) | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers |
| Custom instructions | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions |
| Hooks | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks |
| Plugins | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing |

When in doubt, fetch the relevant URL for the latest details.

---

## Skills

Task-specific instruction bundles loaded on demand. A skill is a SKILL.md
file (and optional companion resources) in a named subdirectory. Copilot
auto-discovers skills from known locations and loads them when relevant.

### Locations

| Scope | Path |
|-------|------|
| Project | `.github/skills/<skill-name>/SKILL.md` or `.copilot/skills/<skill-name>/SKILL.md` |
| Personal | `~/.copilot/skills/<skill-name>/SKILL.md` |
| Plugin | `plugins/<plugin>/skills/<skill-name>/SKILL.md` (shipped by an enabled plugin) |

Additional search paths can be added with `/skills add`.

### SKILL.md Format

YAML frontmatter (`name` required, `description` required, `license`
optional) followed by markdown instructions. The description drives
auto-matching -- be specific about trigger conditions. The skill directory
may also contain scripts and resources referenced by the instructions.

- **`name`** -- lowercase letters, numbers, and hyphens only; max 64 chars;
  no reserved words (`anthropic`, `claude`). Prefer gerund form
  (`authoring-extensions`, `processing-pdfs`).
- **`description`** -- non-empty, max **1024 characters**, third person, no
  XML tags. State both **what** the skill does and **when** to use it, and
  include concrete trigger terms.

## Per-Skill Folder Convention

Lay every skill out the same way so companion files are discoverable and the
SKILL.md stays a lean table of contents:

```
<skill-name>/
  SKILL.md            # required: frontmatter (name + description) + body
  references/         # companion docs the SKILL.md points to, loaded on demand
    <topic>.md
  scripts/            # executable utilities the agent RUNS (not loaded as text)
  assets/             # templates / fixtures the skill copies or fills in
```

Rules:

- **Only `SKILL.md` lives at the top level.** Everything else goes in
  `references/`, `scripts/`, or `assets/` -- don't scatter loose `.md` siblings.
- **`references/`** holds prose the SKILL.md links to (progressive disclosure).
  Keep links **one level deep** from SKILL.md -- no nested reference chains, or
  the agent may only partially read them.
- **`scripts/`** holds code the agent **executes by path** ("run
  `scripts/x.py`") rather than pasting inline -- more reliable, fewer tokens.
- **`assets/`** holds templates/fixtures (e.g. a `TEMPLATE.md` the skill copies).
- **Use forward slashes** in all skill-internal references so they resolve on
  every platform (documenting an OS-specific *command* path is fine).
- **Keep `SKILL.md` under 500 lines.** When it grows past that, move detail
  into `references/` and leave a pointer.

## Skill Validation Checklist

When creating or modifying a skill, validate against Anthropic's best
practices (see the reference table above). Key checks:

- **Description:** specific, third-person, includes key trigger terms,
  under 1024 chars. Avoid "I can" or "You can use this".
- **Body:** under 500 lines. Split into companion files if larger.
- **Conciseness:** only add context the agent doesn't already have.
  Challenge each paragraph: does it justify its token cost?
- **Degrees of freedom:** match specificity to fragility -- exact commands
  for fragile ops, high-level guidance for flexible tasks.
- **No time-sensitive data.** Use "old patterns" sections if needed.
- **Progressive disclosure:** SKILL.md is a table of contents. Reference
  files are one level deep (no nested references).
- **Folder structure:** follow the convention above.
- **Consistent terminology:** pick one term per concept throughout.

### Invocation

- **Explicit:** `/skill-name` in a prompt (e.g., `/authoring-extensions create a skill`)
- **Auto-match:** Copilot matches the prompt against skill descriptions and
  loads relevant skills automatically

### CLI Commands

`/skills list`, `/skills info`, `/skills` (toggle on/off),
`/skills add`, `/skills reload`, `/skills remove DIR`.

### Skills vs Custom Instructions

Use **custom instructions** for simple, always-on guidance (coding standards,
repo conventions). Use **skills** for detailed, task-specific instructions
that Copilot should only load when relevant.

## Custom Agents

See [reference.md](references/reference.md) for agent file format, frontmatter
properties, tool aliases, invocation, MCP server ownership, and the
anti-recursion pattern.

## MCP Servers

See [reference.md](references/reference.md) for the registration hierarchy,
config formats (per-agent, project, global, VS Code), env-var syntax, CLI
commands, and writing an MCP server in Python.

## Custom Instructions

See [reference.md](references/reference.md) for the scope/file table and how to
avoid auto-loading linked files.

## Hooks

Shell commands that run at agent lifecycle points (session start/end, before
and after tool calls, on errors). The `preToolUse` hook can block tool
execution -- making it the primary mechanism for guardrails and policy
enforcement. Configuration lives in `.github/hooks/*.json`.

See [reference.md](references/reference.md) for the event table, config format,
and script I/O; the [hooks docs](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks)
carry the full input/output schemas.
