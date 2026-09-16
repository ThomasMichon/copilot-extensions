---
name: hoisting-plugin-agents
description: >
  Hoist enabled directory-marketplace plugin agents into a repo-local
  `.github/agents/` fallback so delegated/background sub-agents and freshly
  spawned nested `copilot` processes can still reach them, working around a
  Copilot CLI limitation where those surfaces cannot resolve
  marketplace/plugin-defined custom agents. Generate, verify, and retire the
  hoisted copies.
  Trigger phrases include:
  - 'hoist plugin agents'
  - 'delegated agent cannot reach my plugin agent'
  - 'task tool cannot find my custom agent'
  - 'background subagent cannot invoke my plugin agent'
  - 'nested copilot process missing my plugin'
  - 'workaround for custom agent delegation'
---

# Hoisting Plugin Agents

A **temporary, mechanical workaround** for a Copilot CLI limitation: some
runtime versions do not let a delegated/background sub-agent (for example, the
`task` tool's `agent_type`) invoke a marketplace/plugin-defined custom agent,
and a freshly spawned nested `copilot` process does not inherit the parent
session's resolved `enabledPlugins` either -- it has to be told about every
plugin explicitly via `--plugin-dir`, and even then only a fully-qualified
`plugin:agent` name resolves. Repo-local `.github/agents/*.agent.md` files
remain natively discoverable from every one of those surfaces, independent of
plugin/marketplace resolution.

Use this skill when a repo depends on marketplace-defined sub-agents (an
in-repo directory marketplace shipping `agents/*.agent.md`, e.g. a harness's
own `.ai/<plugin>/agents/`) and that dependency needs to survive
delegated/background invocation on an affected runtime version.

## What it does

[`scripts/hoist_plugin_agents.py`](scripts/hoist_plugin_agents.py) copies
(never moves, never disables) every *enabled* plugin's `agents/*.agent.md`
from an in-repo **directory** marketplace into a repo-local output directory
(default `.github/agents/`), rewriting only the relative markdown links each
file carries so they still resolve back to the plugin's real `agents/`
directory. Plugin-owned skills, MCP bridge configs, and marketplace
registration are untouched -- this produces a regenerable parallel entry
point, not a fork.

It intentionally covers only **directory-source** marketplaces (an in-repo
plugin tree declared as `extraKnownMarketplaces.<name>.source == {"source":
"directory", "path": ...}`). An installed (github/npm/etc.) marketplace
payload lives outside the repository, so a relative link back to it cannot be
committed portably; there is no proven need for that case yet, so it is out of
scope rather than guessed at.

## Generate / verify

```bash
python3 <skill-dir>/scripts/hoist_plugin_agents.py sync <repo-root>
python3 <skill-dir>/scripts/hoist_plugin_agents.py scan <repo-root>
```

- `sync` (re)writes every hoisted copy under the output directory and removes
  stale copies for plugins that are no longer enabled or no longer ship the
  agent. Idempotent: a second `sync` with nothing changed writes nothing.
- `scan` reports drift (out-of-date or stale copies) without writing, and
  exits non-zero on any finding -- wire it into your own repo's build/test
  validation so a hand-edited or stale hoisted copy is a hard failure, the
  same way `manage-instruction-projections.py scan` gates instruction
  projections.
- `--output-dir <path>` overrides the default `.github/agents` (repository-
  relative, POSIX-style).
- `--json` emits a stable `{"operation", "changed", "stale"}` object for
  automation.

Each hoisted file carries a `GENERATED` provenance comment naming its source
plugin, version, and script -- never hand-edit a hoisted copy; edit the source
`agents/<name>.agent.md` and re-run `sync`.

## Conflicts and safety

- Two enabled plugins shipping an agent with the same filename is a hard
  **error** (`HoistError`), not a silent last-write-wins overwrite.
- A relative link that would resolve outside the repository root is a hard
  error -- it cannot be expressed as a committed, portable path.
- A directory-marketplace `path` that escapes the repository root is a hard
  error for the same reason.
- Malformed `enabledPlugins` / `extraKnownMarketplaces` in the settings
  files is a hard error rather than a best-effort skip.

## Retiring the workaround

This is scaffolding around a runtime limitation, not a permanent capability.
Once your Copilot CLI runtime version resolves marketplace-defined agents from
every delegated/background surface, remove the adopting repo's `sync`
invocation from its build/validation tooling, delete its hoisted
`.github/agents/` output, and drop any repo-level documentation pointing at
this workaround.

## Reference

Pairs with **`reviewing-customizations`** (the mechanical scan / instruction-
projection manager for the same class of Copilot CLI limitations) and
**`defining-subagents`** (how to author the agents this script hoists).
