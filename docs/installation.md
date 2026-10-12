# Installation and First Use

Choose one useful capability before assembling a deployment. A plugin can be
used independently where its owner documents that path. The toolkit does not
install an operator's complete harness, identities, or repository policy.

## Load the marketplace payload

With an installed and authorized Copilot CLI, this is the **user-global**
installation path (it affects sessions beyond one repository):

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install customizing-copilot@copilot-extensions
```

This example loads customization skills, not a runtime service. For a
repository-scoped choice, use the settings path below instead of a global
install command. To select a
different capability, replace the plugin name with one from the
[marketplace](../.github/plugin/marketplace.json). Restart the session and
verify the expected skills or extension are present.

For repeatable repository-scoped selection, use
[installing-plugins](../plugins/customizing-copilot/skills/installing-plugins/SKILL.md).
The following is a **minimal example**, not a default full harness:

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
    "customizing-copilot@copilot-extensions": true
  }
}
```

The owner guide places this in `.github/copilot/settings.json`. Preserve
existing settings rather than overwriting the file. Plugin loading does not
require experimental mode. Repository settings participate only when the
repository is trusted; ACP sessions require persisted trust because they
cannot present the interactive trust prompt.

**Session extensions have a separate activation boundary.** The current
extension loader reads user-level `enabledPlugins` and persisted install
state without merging repository settings. For a selected extension-backed
capability, use deliberate user-level enablement/global installation and
verify that the extension loaded; a repo-only payload setting is not enough.
For this documented session-extension path, also set `"experimental": true`
in `~/.copilot/settings.json`, as required by the
[session-extension install contract](install-contract.md).
This additional extension gate is distinct from ordinary plugin loading.
Installed inventory alone is not activation. See the
[installation owner's scoping caveats](../plugins/customizing-copilot/skills/installing-plugins/SKILL.md)
and the [configuration ownership map](configuration.md).

## Payload, runtime, and adoption are different steps

1. **Payload:** Copilot loads the selected plugin's skills, hooks, and
   extensions. Payload-only means no separately installed Python package; it
   does not mean every hook/extension needs no Python, Node, or CLI tools.
2. **Runtime:** a runtime-backed plugin deploys its own versioned Python
   package and optional service. Use its setup guide and attributable command
   glossary. First-use provisioning/reconciliation is plugin-specific; do not
   assume payload installation proves runtime readiness.
3. **Adoption:** explicitly integrate the plugin with a repository or machine
   profile using its documented `register`, `adopt`, or setup operation.
   [Install/update do not edit committed repo policy](patterns/install-vs-adopt-boundary.md).

For standalone local worktrees, follow
[Agent Worktrees Getting Started](../plugins/agent-worktrees/docs/getting-started.md).
For selected mesh runtimes and MCP, the
[setup skill](../plugins/agent-worktrees/skills/copilot-extensions-setup/SKILL.md)
is the task-time entry point; use the current
[architecture](architecture.md) and [install contract](install-contract.md)
for composition and command ownership. Other runtime plugins have their own
setup owners in the [reference index](reference-index.md).

The mesh setup skill retains legacy endpoint/install-order and
experimental-setting examples; its payload refresh is tracked under
[adopter-material-refresh](../efforts/active/adopter-material-refresh/README.md).
For those boundaries, the current installation owner and architecture describe
ordinary plugin activation, user-level extension enablement, discovered
endpoints, and filesystem providers. Do not hardcode an endpoint or infer a
mandatory sibling install order from those older examples.

## Requirements and platform scope

| Selected workflow | Requirement/evidence owner |
|-------------------|----------------------------|
| Copilot payloads | Copilot CLI access and plugin loading; [installation skill](../plugins/customizing-copilot/skills/installing-plugins/SKILL.md) |
| Local worktrees | Python 3.10+, Git 2.15+, Copilot CLI; [standalone guide](../plugins/agent-worktrees/docs/getting-started.md#prerequisites) |
| Python runtime plugins | Current plugin `pyproject.toml` files declare Python >=3.10; `uv` and versioned deployment are governed by the [install contract](install-contract.md) |
| Codespaces | GitHub CLI/access, SSH and selected relay policy; [Codespaces setup](../plugins/agent-codespaces/skills/codespaces-setup/SKILL.md) |
| Container fleet operations | Reachable Docker provider and its host/guest requirements; [container owner](../plugins/agent-containers/README.md) |
| Vault | A user-selected KeePassXC database and `keepassxc-cli`; [vault setup](../plugins/agent-vault/skills/agent-vault-setup/SKILL.md) |
| WSL setup | Windows with the WSL2 workflow being configured; [WSL owner](../plugins/wsl-setup/README.md) |

Windows and Linux/WSL are common runtime deployment targets, not a universal
assurance for every workflow. macOS support is component-specific:
the worktrees standalone guide includes a Linux/macOS init path, while
[Agent Bridge](../plugins/agent-bridge/README.md) explicitly lists macOS as
planned. A POSIX script or portable skill alone is not proof that a daemon,
credential source, container host, or launch mechanism is supported there.
Follow each selected component's documented platform path and prerequisites.

## Verify the capability you actually selected

- Verify the plugin loaded in a fresh session.
- For a runtime, run the owner's version/status or readiness check using the
  published command path; verify the service only if that capability has one.
- For adoption, check the registered repo/profile and a harmless local
  operation before introducing remote resources or credentials.
- Record the actual platform, version, venue, and result. A status command
  proves that command's contract, not all integrations in the suite.

The complete Installer & Configurator bootstrap is
[standing intent](../visions/installer/README.md), not a current one-line
installation promise. Optional standalone products such as
[Worktree Manager](../worktree-manager/README.md) own their own installers and
prerequisites; they are not required to load a payload-only skill plugin.

## Maintenance and recovery

For an agent-worktrees-adopted deployment, `agent-worktrees update` is the
management path for refreshing registered payloads and reconciling runtimes.
Follow [the unified update contract](../CONTRIBUTING.md#deploying-one-command--repo-update)
and the selected plugin's owner. For a standalone deployment, use that owner's
documented maintenance path. Do not edit installed payloads or deployed runtimes.
Source changes land through [contribution](../CONTRIBUTING.md), and a merge to
`dev` is not a release or local rollout.

Before removal, inspect the owner's uninstall and configuration-purge options:
[worktrees](../plugins/agent-worktrees/docs/cli-reference.md),
[bridge](../plugins/agent-bridge/docs/getting-started.md),
[Codespaces](../plugins/agent-codespaces/README.md), and each additional selected
plugin. Repository-level `tools/reset.ps1` and `tools/reset.sh` are source-checkout
recovery tools, not universal plugin cleanup promises. Inspect their documented
scope before using them; never delete managed worktrees manually.

For clone recovery after the `main` history rewrite, use
[pipeline recovery](pipelines.md#if-mains-history-is-force-rewritten).

## Next

[Capabilities](capabilities.md) → [adoption](adoption.md) →
[configuration](configuration.md). For exact procedures, use the
[reference index](reference-index.md).
