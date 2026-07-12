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

> **Declarative first.** Skills, custom instructions, hooks, sub-agents, and MCP
> servers are *declarative* surfaces -- prefer them. The CLI also has an
> *imperative* **Extensions API** (a JS `extension.mjs` calling `joinSession`),
> but it is heavier and **may be on its way out**: the native runtime (1.0.66+)
> already **removed extension SDK callback hooks**, and the declarative hook
> system below now covers what they did -- including injecting `additionalContext`
> into the model. Reach for an extension only when no declarative surface fits.

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
  **Decompose liberally:** SKILL.md is loaded whole when the skill triggers, but a
  `references/` doc is fetched **only when the agent follows the link**. So bias
  toward a lean SKILL.md that links out to focused reference docs — pull each large,
  self-contained topic (a deep procedure, a full schema, a worked example set) into
  `references/<topic>.md` and leave a one-line pointer. That keeps per-trigger
  context small; the trade is an extra read on demand. Link out *and* back; no
  orphan references. The same bias applies to any long doc a skill owns.
- **`scripts/`** holds code the agent **executes by path** ("run `scripts/x.py`")
  rather than pasting inline -- more reliable, fewer tokens.
- **`assets/`** holds templates/fixtures (e.g. a `TEMPLATE.md` the skill copies).
- **Use forward slashes** in skill-internal references so they resolve on every
  platform (documenting an OS-specific *command* path is fine).
- **Keep `SKILL.md` under 500 lines — and split *before* that when a topic can
  stand alone.** 500 is the ceiling, not a target; move detail into `references/`
  proactively at a natural seam and leave a pointer.

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

### Action-sequence skills vs ambient-guidance skills

A skill's guidance applies **most strongly during the turn it is invoked**, and
fades on later turns. Author with that grain, not against it:

- **Action-sequence skills** — a procedure the agent runs *now* (setup steps, a
  deploy flow, a review pass). These fit the model perfectly: the sequence is
  consumed in-turn. Write them as concrete, ordered steps.
- **Ambient-guidance skills** — standing rules meant to hold for the *rest of
  the session* (a voice/persona, a style bar, a safety discipline). A one-shot
  skill body decays after its turn, so the guidance quietly stops applying.
  Instead, keep the durable guidance in an **always-on home** (root `AGENTS.md` /
  custom instructions, or a linked doc) and have the skill **point at it and
  activate it** — e.g. *"Load and enforce the guidance at `<link>` for the
  remainder of the session; if it is already in context, keep applying it."* The
  skill's job becomes *loading + activating* ambient guidance, not *being* a
  transient copy of it.

This mirrors "Skills vs custom instructions" above: always-on guidance belongs in
custom instructions; a skill that must reach ambient guidance should **install or
enforce** it (point at the durable home) rather than restate it one-shot.

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

Configure events in **camelCase** (native, fields camelCase) or **PascalCase**
(VS Code / Claude-compatible, fields snake_case). Command hooks are the default;
**`http`** hooks POST the payload to a URL, and **`prompt`** hooks (sessionStart
only) auto-submit text or a slash command.

| Event | Fires when | Output |
|-------|-----------|--------|
| `sessionStart` | New or resumed session begins | Can inject **`additionalContext`** |
| `sessionEnd` | Session completes or terminates | Ignored |
| `userPromptSubmitted` | User submits a prompt | Ignored |
| `preToolUse` | Before any tool invocation | **Allow/deny/modify** -- `{"permissionDecision":"deny","permissionDecisionReason":"..."}` or `modifiedArgs` |
| `postToolUse` | After a tool completes successfully | **Inject `additionalContext`** (appended to the result, same turn) or `modifiedResult` |
| `postToolUseFailure` | After a tool fails | Recovery guidance via **`additionalContext`** |
| `notification` | Async CLI notification (`shell_completed`, `agent_completed`, `agent_idle`, `permission_prompt`, ...) | Can inject **`additionalContext`**; fire-and-forget, never blocks |
| `permissionRequest` | Before the permission service runs | `{"behavior":"allow"|"deny"}` (CLI only; great for `-p`/CI) |
| `preCompact` | Before context compaction (manual/auto) | Ignored |
| `agentStop` | Main agent finishes a turn | **Block** -- `{"decision":"block","reason":"..."}` forces another turn |
| `subagentStart` | A sub-agent is spawned | `additionalContext` prepended to its prompt |
| `subagentStop` | Sub-agent completes | **Block** (force another turn) |
| `errorOccurred` | Error during agent execution | Ignored |

> **`additionalContext` is the declarative way to talk to the model.** Several
> events (`postToolUse`, `notification`, `sessionStart`, `postToolUseFailure`)
> let a hook write `{"additionalContext": "..."}` to stdout and the string is
> surfaced to the model. This is the supported replacement for the **removed**
> extension SDK `onPostToolUse` callback: a command hook can read a small
> **state file** (e.g. a sidecar maintained by a background process) and inject
> a nudge when some condition holds -- no `extension.mjs` required. Multiple
> hooks' `additionalContext` are joined (double newline) and capped at 10 KB.

> **Hooks are reactive -- they can't originate or schedule a turn.** Every hook
> fires in response to activity the session is already producing. The only hook
> that injects a *follow-up prompt* is `agentStop` with
> `{"decision":"block","reason":"..."}` (the `reason` becomes a new user turn,
> verified) -- but it fires only at a turn boundary, so it's a continuation
> loop, **not a scheduler**, and never fires once the agent is idle. No hook
> fires on a clock or from an external/async event; `notification` is
> fire-and-forget, has no turn-forcing output, and does not fire in
> non-interactive mode. Waking an idle session asynchronously (callbacks, peer
> messaging, scheduled prompts) still needs `session.send()` (an extension) or
> the runtime's own scheduled prompts -- not a hook.

### Script I/O

- **Input:** read all of stdin as JSON (`jq` in bash, `ConvertFrom-Json` in
  PowerShell). Tool hooks also receive `toolName` / `toolArgs`; post-tool hooks
  include `toolResult`.
- **Output:** a single JSON object on stdout. Decision/injection events read it
  -- `preToolUse` (`permissionDecision`/`modifiedArgs`), `postToolUse` /
  `postToolUseFailure` / `notification` / `sessionStart` (`additionalContext`),
  `permissionRequest` (`behavior`), `agentStop` (`decision`). Emit **exactly
  one** final JSON object (progress lines `{"type":"progress",...}` are stripped
  first; two decision objects concatenate into invalid JSON and are ignored).
  Other events ignore stdout.
- **Stderr:** debug logging, ignored. **Exit code:** 0 = success.
- **Performance:** hooks run synchronously and block the agent -- keep them under
  5 seconds; background expensive work.
