# customizing-copilot

A **payload-only** Copilot CLI plugin that teaches an agent how to customize and
extend the GitHub Copilot CLI. There is no runtime, service, venv, or binstub:
**enable the plugin and restart the session** so the eight skills are available
on demand.

The skills are standalone authoring guidance. They work in any repo that enables
the plugin; they do **not** require that repo to be registered as an agent
harness. Some skills teach harness/plugin patterns, but the install itself is
just plugin enablement.

## What it does (and how to use it)

Ask for the customization task in natural language (or explicitly mention a
skill name) and Copilot loads the matching skill:

| Skill | Use it when... | Covers |
|-------|----------------|--------|
| [authoring-skills](skills/authoring-skills/SKILL.md) | creating or auditing `SKILL.md`, hooks, or custom instructions | Skill locations and frontmatter, folder conventions, validation checklist, hooks, custom-instruction surfaces, and portable plugin runtime context |
| [defining-subagents](skills/defining-subagents/SKILL.md) | creating, defining, or reviewing a custom agent / `.agent.md` | Agent frontmatter, bounded direct-execution contracts, Task-capable anti-self-delegation, per-agent MCP ownership, shared-tool exceptions, equivalent materialized CLI fallback, and MCP-readiness guards |
| [registering-mcp-servers](skills/registering-mcp-servers/SKILL.md) | adding or debugging MCP servers | Per-agent / project / global registration, config formats, env-var substitution, relocatable plugin servers, CLI commands, and server authoring |
| [installing-plugins](skills/installing-plugins/SKILL.md) | enabling plugins or adding a marketplace | Installed inventory vs. user/repository activation, dry-run-first activation inspection/removal, repo-scoped `.github/copilot/settings.json`, global installs, `.ai` directory marketplaces, payload-vs-runtime, and launch-time reconciliation |
| [building-harnesses](skills/building-harnesses/SKILL.md) | building or auditing an agent control harness | Entry point to the [Control-Harness Runbook](../../docs/harness-runbook.md): plugin set, repo adoption, `AGENTS.md`, delegation, validation, efforts, and visions |
| [reviewing-customizations](skills/reviewing-customizations/SKILL.md) | reviewing a repo's customization surfaces | Mechanical scan + design critique over skills, project/`.ai` agents, origin/version-aware advisory checks for enabled external plugin agents, instructions, hooks, and MCP configs |
| [authoring-harness-plugins](skills/authoring-harness-plugins/SKILL.md) | packaging a repo's operator guidance for other control repos | The payload-only `<repo>-harness` pattern: contribute/diagnose skills, README bar, marketplace wiring, and adoption |
| [diagnosing-copilot-cli-startup](skills/diagnosing-copilot-cli-startup/SKILL.md) | an interactive CLI is stuck on `Loading` or `Resuming` | Mux capture, process/session correlation, persisted events and logs, startup-boundary classification, bridge differential diagnosis, and operator-authorized reproduction |

Each skill supplements the base CLI documentation with this repo's authoring
patterns, and points at authoritative GitHub Copilot CLI and Anthropic Agent
Skills documentation where relevant.

## Choosing a surface: declarative first

Copilot CLI exposes two kinds of customization:

- **Declarative surfaces** (what these skills cover) -- skills, custom
  instructions, **hooks** (`.github/hooks/*.json`), sub-agents, MCP servers, and
  plugins. All are config/Markdown loaded by the runtime; nothing to compile, no
  process to babysit.
- **The imperative Extensions API** -- a JavaScript `extension.mjs` that calls
  `joinSession(...)` to register tools/commands, subscribe to `session.on(...)`
  events, and drive the session via `session.send(...)`.

**Prefer the declarative surfaces.** They are simpler, safer, and first-class in
the runtime. The Extensions API is heavier and **may be on its way out**: the
native runtime (1.0.66+) already **removed extension SDK callback hooks**
(`joinSession({ hooks: {...} })` now fails the extension at load), and the
**declarative hook system has grown to cover what those callbacks did** --
including injecting `additionalContext` into the model from `postToolUse` /
`notification` / `sessionStart` (see the `authoring-skills` hooks section). A
hook can read a small **state file** (maintained by a lightweight background
process if needed) and emit `{"additionalContext": "..."}`, which is the
declarative replacement for the old extension `onPostToolUse` injection. Reach
for an extension only when no declarative surface can express the goal (e.g. a
genuinely interactive slash command with live UI), and keep the imperative part
minimal.

**The one gap declarative surfaces can't close: originating a turn.** Hooks are
**reactive** -- they ride activity the session is already producing, and can
*decorate*, *gate*, or *continue* it, but they cannot *start* a turn. The
closest is `agentStop` with `decision: "block"`, which forces a follow-up turn
using `reason` as the prompt (verified) -- but only at the moment the agent
finishes a turn, so it's a continuation loop, **not a scheduler**: once the
agent goes idle it never fires again. There is **no hook that fires on a clock
or from an external/async event to wake an idle session** (the `notification`
hook is fire-and-forget, carries no turn-forcing output, and does not even fire
in non-interactive mode). Asynchronous "push a new turn into a live/idle
session" -- callbacks, peer-to-peer messaging, scheduled wake-ups -- still
requires **`session.send()`** (an extension) or the runtime's own
agent-initiated scheduled prompts. That gap is the strongest reason the
Extensions API is not yet fully replaceable.

## What this plugin provides — and what it doesn't

Provides:

- Eight skills, the bundled `reviewing-customizations` scanner and instruction
  projection manager, and the cross-platform
  `installing-plugins/scripts/plugin-activation.py` state helper.
- Source-aware agent validation: editable project, `.ai`, and suite agents are
  enforced locally; enabled external plugin agents produce actionable advisory
  findings without editing installed payloads, while editable suite source
  supersedes duplicate installed copies.
- MCP plugin recovery validation: plugin-packaged MCP agents must ship a
  discoverable troubleshooting skill and document dependencies/prerequisites in
  the plugin README; external plugin gaps remain advisory.
- A shared runtime-context reference for relocatable hook, MCP, extension, and
  LSP entry points.
- Guidance for both loose repo customizations and plugin-packaged
  customizations, including the in-repo `.ai` local marketplace pattern.
- References to authoritative GitHub Copilot CLI docs and this repo's
  prescriptive pattern docs where the skill depends on them.

Does **not** provide:

- A runtime installer, daemon, binstub, MCP server, or session extension.
- Automatic edits to your repo. The skills tell the agent what to change; the
  agent still edits the target files in the current task.
- Harness registration by itself. `building-harnesses` teaches that workflow,
  but enabling this plugin does not adopt a repo or install runtime plugins.

## Install / enable

No runtime — the skills load from the marketplace payload when enabled.

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install customizing-copilot@copilot-extensions
```

Or enable it per-repo in that repo's `.github/copilot/settings.json`:

```json
{ "enabledPlugins": { "customizing-copilot@copilot-extensions": true } }
```

For a repo-scoped enablement that has not already registered the marketplace,
also declare it in `extraKnownMarketplaces`:

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": {
      "source": { "source": "github", "repo": "ThomasMichon/copilot-extensions" }
    }
  },
  "enabledPlugins": {
    "customizing-copilot@copilot-extensions": true
  }
}
```

Restart the active Copilot session after changing plugin enablement; plugin
payloads are scanned at session start.

## License

[MIT](../../LICENSE)
