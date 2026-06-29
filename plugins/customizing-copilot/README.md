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
