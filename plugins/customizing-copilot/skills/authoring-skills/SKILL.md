---
name: authoring-skills
description: >
  Author Copilot CLI skills -- the SKILL.md format, the per-skill folder
  convention (SKILL.md + references/, scripts/, assets/), the validation
  checklist, plus the related hook and custom-instruction surfaces. Use when
  creating or editing a SKILL.md, organizing a skill's companion files, writing a
  lifecycle hook, or adding custom instructions.
  Trigger phrases include:
  - 'create a skill'
  - 'new skill'
  - 'SKILL.md'
  - 'skill folder structure'
  - 'skill best practices'
  - 'skill audit'
  - 'write a hook'
  - 'lifecycle hook'
  - 'custom instructions'
  - 'AGENTS.md'
---

# Authoring Skills

How to write Copilot CLI **skills** -- task-specific instruction bundles loaded
on demand -- plus the two always-/lifecycle-adjacent surfaces that pair with
them: **hooks** and **custom instructions**. This supplements knowledge the
Copilot CLI does not ship natively.

Reference documentation:

| Feature | URL |
|---------|-----|
| Skills | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-skills |
| **Skill Best Practices** | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices |
| Custom instructions | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions |
| Hooks | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks |
| Hooks config reference | https://docs.github.com/en/copilot/reference/hooks-configuration |

When in doubt, fetch the relevant URL for the latest details.

---

## Skills

A skill is a SKILL.md file (and optional companion resources) in a named
subdirectory. Copilot auto-discovers skills from known locations and loads them
when relevant.

### Locations

| Scope | Path |
|-------|------|
| Project | `.github/skills/<skill-name>/SKILL.md` or `.copilot/skills/<skill-name>/SKILL.md` |
| Personal | `~/.copilot/skills/<skill-name>/SKILL.md` |
| Plugin | `plugins/<plugin>/skills/<skill-name>/SKILL.md` (shipped by an enabled plugin) |

Add extra search paths with `/skills add`.

### SKILL.md format

YAML frontmatter (`name` required, `description` required, `license` optional)
followed by markdown instructions. The description drives auto-matching -- be
specific about trigger conditions.

- **`name`** -- lowercase letters, numbers, and hyphens only; max 64 chars; no
  reserved words (`anthropic`, `claude`). Prefer gerund form (`authoring-skills`,
  `processing-pdfs`).
- **`description`** -- non-empty, max **1024 characters**, third person, no XML
  tags. State both **what** the skill does and **when** to use it, with concrete
  trigger terms.

### Per-skill folder convention

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
  Keep links **one level deep** from SKILL.md -- no nested reference chains.
- **`scripts/`** holds code the agent **executes by path** ("run `scripts/x.py`")
  rather than pasting inline -- more reliable, fewer tokens.
- **`assets/`** holds templates/fixtures (e.g. a `TEMPLATE.md` the skill copies).
- **Use forward slashes** in skill-internal references so they resolve on every
  platform (documenting an OS-specific *command* path is fine).
- **Keep `SKILL.md` under 500 lines.** When it grows, move detail into
  `references/` and leave a pointer.

### Validation checklist

When creating or modifying a skill, validate against Anthropic's best practices:

- **Description:** specific, third-person, includes key trigger terms, under
  1024 chars. Avoid "I can" / "You can use this".
- **Body:** under 500 lines. Split into companion files if larger.
- **Conciseness:** only add context the agent doesn't already have. Challenge
  each paragraph: does it justify its token cost?
- **Degrees of freedom:** match specificity to fragility -- exact commands for
  fragile ops, high-level guidance for flexible tasks.
- **No time-sensitive data.** Use "old patterns" sections if needed.
- **Progressive disclosure + folder structure:** per the convention above.
- **Consistent terminology:** one term per concept throughout.

### Invocation & CLI

- **Explicit:** `/skill-name` in a prompt. **Auto-match:** Copilot matches the
  prompt against skill descriptions and loads relevant skills automatically.
- Commands: `/skills list`, `/skills info`, `/skills` (toggle), `/skills add`,
  `/skills reload`, `/skills remove DIR`.

### Skills vs custom instructions

Use **custom instructions** for simple, always-on guidance (coding standards,
repo conventions). Use **skills** for detailed, task-specific instructions
Copilot should load only when relevant.

## Custom Instructions

Always-on context injected into every prompt.

| Scope | File |
|-------|------|
| Repo (always loaded) | `AGENTS.md` in repo root or cwd |
| Repo (always loaded) | `.github/copilot-instructions.md` |
| Personal (all repos) | `~/.copilot/copilot-instructions.md` |
| Host/machine-scoped (deployed) | a generated instructions directory loaded via `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` |

Suppress with `--no-custom-instructions`.

**Avoid auto-load in AGENTS.md:** Copilot follows valid Markdown links in
custom-instruction files and auto-loads them. Reference docs with backtick code
spans (`` `docs/tools.md` ``), not `[text](path)` links, so Copilot reads files
on demand instead of loading them into every session.

## Hooks

Shell commands that run at agent lifecycle points. The `preToolUse` hook can
**block** tool execution -- the primary mechanism for guardrails and policy
enforcement. Config lives in `.github/hooks/*.json` (discovered from the cwd;
for cloud agent, on the default branch).

### Config format

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      {
        "type": "command",
        "bash": "./scripts/check.sh",
        "powershell": "./scripts/check.ps1",
        "cwd": ".",
        "env": { "LOG_LEVEL": "INFO" },
        "timeoutSec": 15
      }
    ]
  }
}
```

### Events

| Event | Fires when | Can block? |
|-------|-----------|------------|
| `sessionStart` | Session begins or resumes | No |
| `sessionEnd` | Session completes or terminates | No |
| `userPromptSubmitted` | User submits a prompt | No |
| `preToolUse` | Before any tool invocation | **Yes** -- return `{"permissionDecision":"deny","permissionDecisionReason":"..."}` |
| `postToolUse` | After a tool completes | No |
| `agentStop` | Main agent finishes responding | No |
| `subagentStop` | Sub-agent completes | No |
| `errorOccurred` | Error during agent execution | No |

### Script I/O

- **Input:** read all of stdin as JSON (`jq` in bash, `ConvertFrom-Json` in
  PowerShell). Tool hooks also receive `toolName` / `toolArgs`; post-tool hooks
  include `toolResult`.
- **Output (preToolUse only):** single-line JSON on stdout (`jq -c` /
  `ConvertTo-Json -Compress`).
- **Stderr:** debug logging, ignored. **Exit code:** 0 = success.
- **Performance:** hooks run synchronously and block the agent -- keep them under
  5 seconds; background expensive work.
