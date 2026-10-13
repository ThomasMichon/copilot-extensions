# Reference Index

Start with the [adopter reading path](README.md) for orientation. This index
preserves deeper component and planning references without duplicating their
mechanics. Plugin-local guides and skills own their current commands.

## Suite references

- [Architecture](architecture.md), [configuration](configuration.md), and
  [install contract](install-contract.md).
- [Harness runbook](harness-runbook.md) and [architecture patterns](patterns/README.md).
- [Contributing](../CONTRIBUTING.md), [development guide](../AGENTS.md),
  [testing](../TESTING.md), and [pipelines](pipelines.md).
- [Documentation architecture](documentation-architecture.md) and
  [documentation vision](../visions/documentation/README.md).

## Workspace and communication

| Component | References |
|-----------|------------|
| Agent Worktrees | [Overview](../plugins/agent-worktrees/README.md), [getting started](../plugins/agent-worktrees/docs/getting-started.md), [architecture](../plugins/agent-worktrees/docs/architecture.md), [CLI](../plugins/agent-worktrees/docs/cli-reference.md), [configuration](../plugins/agent-worktrees/docs/config-reference.md) |
| Pull requests | [Current extracted CLI](../plugins/agent-pull-requests/README.md) |
| Agent Bridge | [Overview](../plugins/agent-bridge/README.md), [getting started](../plugins/agent-bridge/docs/getting-started.md), [architecture](../plugins/agent-bridge/docs/architecture.md), [machine configuration](../plugins/agent-bridge/docs/machine-config.md) |
| Codespaces | [Overview/CLI/configuration](../plugins/agent-codespaces/README.md), [setup](../plugins/agent-codespaces/skills/codespaces-setup/SKILL.md), [lifecycle](../plugins/agent-codespaces/skills/codespaces-lifecycle/SKILL.md) |
| Containers | [Overview/CLI/configuration](../plugins/agent-containers/README.md), [fleet operation](../plugins/agent-containers/skills/containers-fleet/SKILL.md) |
| SSH | [Overview](../plugins/agent-ssh/README.md) |
| Remote driver | [Opt-in extension](../plugins/agent-remote-driver/README.md) |
| Context handoff | [Overview](../plugins/context-handoff/README.md), [continuation workflow](../plugins/context-handoff/skills/context-handoff/SKILL.md), [setup](../plugins/context-handoff/skills/context-handoff-setup/SKILL.md) |
| Task dispatch | [Queue/coordinator/CLI/MCP](../plugins/agent-dispatch/README.md) |

## Planning, evidence, and host integration

| Component | References |
|-----------|------------|
| Efforts | [Overview](../plugins/efforts/README.md), [planning](../plugins/efforts/skills/planning-efforts/SKILL.md), [schema/lifecycle reference](../plugins/efforts/skills/planning-efforts/references/efforts.md), [repo setup](../plugins/efforts/skills/efforts-setup/SKILL.md) |
| Visions | [Overview](../plugins/visions/README.md), [envisioning](../plugins/visions/skills/envisioning/SKILL.md), [repo setup](../plugins/visions/skills/visions-setup/SKILL.md) |
| Logger | [Overview](../plugins/agent-logger/README.md), [one-session logging](../plugins/agent-logger/skills/log-session/SKILL.md), [backlog processing](../plugins/agent-logger/skills/process-backlog/SKILL.md), [session-sync setup](../plugins/agent-logger/skills/session-sync-setup/SKILL.md) |
| Index | [Indexing/retrieval owner](../plugins/agent-index/README.md) |
| MCP | [Transport/catalog/auth/materialization](../plugins/agent-mcp/README.md), [bridge workflow](../plugins/agent-mcp/skills/agent-mcp/SKILL.md) |
| Machines | [Requirements reconciliation](../plugins/agent-machines/README.md) |
| Vault | [Overview/CLI](../plugins/agent-vault/README.md), [day-to-day use](../plugins/agent-vault/skills/agent-vault/SKILL.md), [setup](../plugins/agent-vault/skills/agent-vault-setup/SKILL.md) |
| Budget | [Offline posture](../plugins/budget-guidance/README.md) |
| WSL | [Windows/WSL setup guidance](../plugins/wsl-setup/README.md) |
| Knowledge binding | [Optional private-state binding](../plugins/harness-knowledge/README.md) |
| Attribution | [Publication guidance](../plugins/ai-attribution/README.md) |
| Conduct | [Modular guidance](../plugins/agent-conduct-guidance/README.md), [deprecated delegation alias](../plugins/delegation-guidance/README.md) |

## Customization and toolkit contribution

- [Customizing Copilot overview](../plugins/customizing-copilot/README.md).
- [Authoring skills](../plugins/customizing-copilot/skills/authoring-skills/SKILL.md).
- [Defining sub-agents](../plugins/customizing-copilot/skills/defining-subagents/SKILL.md).
- [Registering MCP servers](../plugins/customizing-copilot/skills/registering-mcp-servers/SKILL.md).
- [Installing plugins](../plugins/customizing-copilot/skills/installing-plugins/SKILL.md).
- [Building harnesses](../plugins/customizing-copilot/skills/building-harnesses/SKILL.md).
- [Reviewing customizations](../plugins/customizing-copilot/skills/reviewing-customizations/SKILL.md).
- [Authoring harness plugins](../plugins/customizing-copilot/skills/authoring-harness-plugins/SKILL.md).
- [Diagnosing CLI startup](../plugins/customizing-copilot/skills/diagnosing-copilot-cli-startup/SKILL.md).
- [Hoisting plugin agents](../plugins/customizing-copilot/skills/hoisting-plugin-agents/SKILL.md).
- [Componentizing modules](../plugins/customizing-copilot/skills/componentizing-modules/SKILL.md).
- [Setting up instruction sync](../plugins/customizing-copilot/skills/setting-up-instruction-sync-worker/SKILL.md).
- [Toolkit harness overview](../plugins/copilot-extensions-harness/README.md),
  [contribution workflow](../plugins/copilot-extensions-harness/skills/contributing-to-copilot-extensions/SKILL.md),
  and [toolkit diagnosis](../plugins/copilot-extensions-harness/skills/diagnosing-copilot-extensions/SKILL.md).

## Planning material, not installation promises

The references below are retained for design/history, not presented as evidence
of current support:

- [Plugin consolidation discussion](plans/plugin-consolidation.md).
- [Rollout-readiness plan](plans/rollout-readiness.md).
- [Fresh-box validation plan](plans/fresh-devbox-validation.md).
- [Standing visions](../visions/README.md) and [campaign index](../efforts/README.md).

The [marketplace manifest](../.github/plugin/marketplace.json) is the complete
plugin roster. Standalone products such as
[Worktree Manager](../worktree-manager/README.md),
[fleet-manager](../fleet-manager/README.md), and
[agent-dispatch-service](../services/dispatch/README.md) have separate
entry points and are not counted as marketplace plugins.
