# delegation-guidance

> Coordinator-first task routing for Copilot CLI.

This payload-only plugin keeps the main agent focused on decomposition,
synthesis, integration, and completion while moving broad separable work into
bounded sub-agent contexts before it consumes the coordinator's context window.
It is model-neutral and independently enableable.

The ambient kernel is deliberately concise. Detailed routing guidance loads
only when the `delegating-work` skill is invoked or matched.

## What it does (and how to use it)

| Entry point | When it applies | What it does |
|-------------|-----------------|--------------|
| `sessionStart` hook | Every new or resumed session where the plugin is enabled | Injects a bounded owner-marked coordinator/delegate policy |
| [`delegating-work`](skills/delegating-work/SKILL.md) skill | "delegate this research", "use agents to compare", "parallelize the investigation", "split these bulk edits" | Chooses direct versus delegated work, defines bounded contracts, and preserves coordinator ownership |

Enable it in Copilot settings:

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": {
      "source": {
        "source": "github",
        "repo": "ThomasMichon/copilot-extensions"
      }
    }
  },
  "enabledPlugins": {
    "delegation-guidance@copilot-extensions": true
  }
}
```

Then ask, for example:

> Use agents to compare the three independent storage implementations, then
> synthesize a recommendation and implement the chosen integration.

The coordinator should assign bounded evidence tracks, continue any independent
coordinator-owned work, synthesize the reports, and retain the implementation
and completion decision.

## What this plugin provides - and what it doesn't

**Provides**

- concise ambient coordinator-first routing policy;
- early delegation guidance for broad research, comparisons, evaluations,
  domain-tool calls, and disjoint bulk edits;
- bounded, non-overlapping delegate contract guidance;
- limits on recursive delegation, duplicate investigation, and repeated review.

**Does NOT provide**

- a sub-agent runtime, task queue, cross-machine transport, or MCP server;
- named domain agents or environment-specific routing;
- automatic delegation enforcement or a replacement for coordinator judgment;
- custom-agent authoring or validation guidance.

**Assumes**

- the active Copilot CLI exposes one or more sub-agent types when delegation is
  requested;
- separately enabled domain plugins provide any named agents or MCP tools;
- the coordinating agent remains responsible for integrating delegated work.

## Dependencies & assumptions

The plugin has no runtime, service, network, authentication, or configuration
dependency. Its hook uses the platform's Bash or PowerShell environment and
fails open with `{}` when the plugin payload is incomplete.

Plugin manifests do not install companion plugins transitively. Enable domain
agent, MCP, bridge, or dispatch plugins separately when the task needs them.

## What's in this plugin

| Path | Purpose |
|------|---------|
| [`skills/delegating-work/SKILL.md`](skills/delegating-work/SKILL.md) | Detailed direct-versus-delegated routing procedure |
| [`hooks.json`](hooks.json) | Cross-platform `sessionStart` registration |
| [`scripts/emit-guidance.ps1`](scripts/emit-guidance.ps1) | PowerShell policy producer |
| [`scripts/emit-guidance.sh`](scripts/emit-guidance.sh) | Bash policy producer |
| [`tests/test_emit_guidance.py`](tests/test_emit_guidance.py) | Hook, parity, budget, and payload tests |

The skill file is the source of truth for task-time routing behavior.

## Troubleshooting, contributing & issues

- **No ambient guidance:** confirm the plugin is enabled and start or resume a
  session that loads plugin hooks.
- **The hook emits `{}`:** confirm the host supplies a plugin-root environment
  variable, then reinstall or update the plugin if the payload is incomplete.
- **No suitable sub-agent is available:** use the skill's unavailable-agent
  path; the plugin does not install agent types.

Contributions follow the repository's PR-required workflow in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md). File issues in the
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues)
repository.
