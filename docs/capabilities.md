# Capabilities and Selection

Select by the task you want to accomplish, not by how many plugins are in the
marketplace. This page groups capabilities; the
[manifest](../.github/plugin/marketplace.json) owns the roster, and the linked
component owns its command and support contract.

## Capability families

| Need | Start with | Boundary |
|------|------------|----------|
| Customize an agent's working context | [customizing-copilot](../plugins/customizing-copilot/README.md) | Skills for authoring/diagnosis; no supplied operator topology |
| Inspect the raw request your CLI assembles | [capturing-client-requests](../plugins/customizing-copilot/skills/capturing-client-requests/SKILL.md) | Current-model private capture plus local instruction/metadata/tool interpretation and byte accounting, never a shared-service logger |
| Work in isolated local git sessions | [agent-worktrees](../plugins/agent-worktrees/docs/getting-started.md) | Standalone local worktrees are supported without mesh siblings |
| Query/create/merge/wait for a PR by explicit repo address | [agent-pull-requests](../plugins/agent-pull-requests/README.md) | Implemented API-first verbs without a local checkout; current account resolution uses agent-worktrees |
| Message live agents | [agent-bridge](../plugins/agent-bridge/README.md) | Persistent session transport; not the durable task queue |
| Queue, claim, and supervise work | [agent-dispatch](../plugins/agent-dispatch/README.md) | Durable task/supervisor contract; configure before starting loops |
| Continue long-running objectives | [context-handoff](../plugins/context-handoff/README.md) | Continuity and baton storage; hosting/cutover remains a separate owner |
| Use remote or container venues | [agent-codespaces](../plugins/agent-codespaces/README.md), [agent-containers](../plugins/agent-containers/README.md) | Provisioning, access, leases, cost, and credentials are explicit choices |
| Resolve machine connectivity | [agent-ssh](../plugins/agent-ssh/README.md), [wsl-setup](../plugins/wsl-setup/README.md) | SSH profile/transport and Windows WSL setup, respectively |
| Drive an ordinary live CLI session | [agent-remote-driver](../plugins/agent-remote-driver/README.md) | Opt-in extension, not default adoption of CLI-default bridging |
| Plan and preserve intent | [efforts](../plugins/efforts/README.md), [visions](../plugins/visions/README.md) | Repo-owned planning adoption; visions are not shipped-feature claims |
| Log and retrieve evidence | [agent-logger](../plugins/agent-logger/README.md), [agent-index](../plugins/agent-index/README.md) | Select sources/sinks and corpus; index host provisioning belongs to its configured supervisor |
| Wrap authenticated tools | [agent-mcp](../plugins/agent-mcp/README.md) | Transport/catalog/auth boundary; agent authoring belongs to customizing-copilot |
| Reconcile host requirements | [agent-machines](../plugins/agent-machines/README.md) | Explicit requirement packages, not an implicit machine loadout |
| Fetch local secrets | [agent-vault](../plugins/agent-vault/README.md) | KeePassXC-backed local service; no universal keychain backend promise |
| Read current budget posture | [budget-guidance](../plugins/budget-guidance/README.md) | Offline configured readings, not automated billing access |
| Separate portable harness and private state | [harness-knowledge](../plugins/harness-knowledge/README.md) | An explicitly selected private knowledge repo, not a fixed name |
| Apply general conduct/publication guidance | [agent-conduct-guidance](../plugins/agent-conduct-guidance/README.md), [ai-attribution](../plugins/ai-attribution/README.md) | General policy and task-time procedures; host-specific policy remains host-owned |
| Contribute to this toolkit | [copilot-extensions-harness](../plugins/copilot-extensions-harness/README.md) | Toolkit contribution/diagnosis guidance, not your complete harness |

[delegation-guidance](../plugins/delegation-guidance/README.md) is a deprecated
compatibility pointer, not an additional independent capability.

## Support and dependencies

The [architecture lifecycle tables](architecture.md#the-plugins) distinguish
runtime-backed and payload-only plugins. Payload-only plugins may execute hooks
or extensions; runtime plugins may be on-demand CLIs or persistent services.
Neither classification alone establishes platform or venue support.

- [Installation requirements](installation.md#requirements-and-platform-scope)
  link actual owners; do not promote Windows/Linux conventions into a universal
  suite guarantee or infer macOS support from a `.sh` filename.
- Optional combinations need their selected integration owners. A local
  worktree does not require Docker, Codespaces, private-knowledge binding,
  indexing, a queue, or a bridge.
- Provider credentials and permissions are separate from plugin installation.
  A chosen forge or tracker is an adopter decision; it is not evidence that
  every plugin supports that provider's API.
- Optional standalone products—[Worktree Manager](../worktree-manager/README.md),
  [fleet-manager](../fleet-manager/README.md), and
  [agent-dispatch-service](../services/dispatch/README.md)—are outside the
  marketplace-plugin roster. Their own pages define current scope.

No fleet/controller bootstrap, new provider backend, or future Configurator
panel is implied by this map. Use [visions](../visions/README.md) for intent and
owner references for what exists.

## Choose a first useful slice

1. Identify a concrete task and its platform.
2. Enable the smallest relevant plugin set.
3. Follow the owner setup and prove one harmless operation.
4. Adopt only the needed repo/profile integration.
5. Add a service or venue when its benefit and dependencies are explicit.

[Hypothetical deployments](adoption.md#hypothetical-deployment-examples)
illustrate these choices. The [runbook](harness-runbook.md) is a more opinionated
assembly procedure, not a universal minimum plugin bundle.
