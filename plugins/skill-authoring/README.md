# skill-authoring

A **payload-only** Copilot CLI plugin that teaches an agent how to extend and
customize the Copilot CLI: writing **skills**, defining **sub-agents**,
registering **MCP servers**, wiring **hooks**, and adding **custom
instructions** — plus the **per-skill folder conventions** that keep a skill
discoverable and token-efficient.

It ships one skill, [`authoring-extensions`](skills/authoring-extensions/SKILL.md),
which supplements knowledge the CLI does not ship natively and points at the
authoritative GitHub Copilot CLI and Anthropic Agent Skills documentation.

## What it covers

| Topic | Where |
|-------|-------|
| Skill format, discovery, validation checklist, and the **folder convention** (`SKILL.md` + `references/` + `scripts/` + `assets/`) | [SKILL.md](skills/authoring-extensions/SKILL.md) |
| Custom agents (sub-agents): file format, frontmatter, tool aliases, MCP ownership, anti-recursion | [reference.md](skills/authoring-extensions/references/reference.md) |
| MCP servers: registration hierarchy, config formats, env-var syntax, writing a server | [reference.md](skills/authoring-extensions/references/reference.md) |
| Custom instructions: scopes, auto-load avoidance | [reference.md](skills/authoring-extensions/references/reference.md) |
| Hooks: events, config format, script I/O | [reference.md](skills/authoring-extensions/references/reference.md) |

## Install

No runtime — the skill loads from the marketplace payload when enabled.

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install skill-authoring@copilot-extensions
```

Or enable it per-repo in that repo's `.github/copilot/settings.json`:

```json
{ "enabledPlugins": { "skill-authoring@copilot-extensions": true } }
```

## License

[MIT](../../LICENSE)
