# customizing-copilot

A **payload-only** Copilot CLI plugin that teaches an agent how to customize and
extend the GitHub Copilot CLI. It bundles four focused skills covering the main
extensibility surfaces, with the per-skill folder conventions and Agent Skills
best practices baked in.

| Skill | Covers |
|-------|--------|
| [authoring-skills](skills/authoring-skills/SKILL.md) | The SKILL.md format, the per-skill folder convention (`SKILL.md` + `references/` + `scripts/` + `assets/`), the validation checklist, and the related **hook** and **custom-instruction** surfaces |
| [defining-subagents](skills/defining-subagents/SKILL.md) | Custom agents (sub-agents): `.agent.md` format, frontmatter, tool aliases, invocation, per-agent MCP ownership, and the anti-recursion / MCP-readiness pattern |
| [registering-mcp-servers](skills/registering-mcp-servers/SKILL.md) | The MCP registration hierarchy (per-agent / project / global), config formats, env-var substitution, the MCP CLI, and writing a server |
| [installing-plugins](skills/installing-plugins/SKILL.md) | Repo-scoped plugin registration via `.github/copilot/settings.json` (+ experimental mode) vs global installs, the payload-vs-runtime model, and launch-time reconciliation |

Each skill supplements knowledge the CLI does not ship natively and points at the
authoritative GitHub Copilot CLI and Anthropic Agent Skills documentation.

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

## Install

No runtime — the skills load from the marketplace payload when enabled.

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install customizing-copilot@copilot-extensions
```

Or enable it per-repo in that repo's `.github/copilot/settings.json`:

```json
{ "enabledPlugins": { "customizing-copilot@copilot-extensions": true } }
```

## License

[MIT](../../LICENSE)
