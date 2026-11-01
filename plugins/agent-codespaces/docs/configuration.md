# Configuration reference

[Native dataclasses, readers and validators](../src/agent_codespaces/config.py)
are the executable schema; [migrations](../src/agent_codespaces/config_migrations.py)
own schema versions. Existing README/skill examples remain the authoring examples.

## Documents and boundaries

Selected repository: `.copilot-extensions/agent-codespaces/config.yaml`, otherwise
`.agent-codespaces/config.yaml`, otherwise root `codespaces.yaml`. Only one
canonical/legacy file is selected. The installation-qualified
`marketplaces/<marketplace-id>/config.yaml` overlay remains explicit.
Machine-wide ordinary preferences use `<runtime>/config.yaml`. The
owner-resolved `repository_machine_root` uses the native canonical/legacy
filenames; if no separate root is supplied, the base repository's
`.copilot-extensions/agent-codespaces/config.local.yaml` is its local companion.
Active plugins' `codespaceConfig` manifests and admitted `config.d` contributions
are lower-priority providers, not authority to activate plugins. Operator dropins
live under `<runtime>/config.d`; managed pointer schema v1 fields are
`schema_version`, `plugin`, `plugin_root`, `target`, validated by the native
classifier. Foreign, linked or malformed provider entries retain scan findings,
last-known/indeterminate semantics and native ownership checks.

Ordinary selected-context preference order is builtins → base repository →
bound knowledge graft → machine → exact repository+machine. An unrelated
adopted repository must not shadow the selected current repository. Service-wide
adoption aggregation remains a different operation (`include_cwd=False`).
Worktrees owns binding and qualified repository/logical-machine/cell identity.
Explicit unresolved binding refuses; unbound operation retains defaults.
CLI selectors remain final native overrides, not a fifth stored tier.
Native launchers deliver a validated version-1 owner projection through
`AGENT_CONFIGURATION_CONTEXT`; namespaced fallback reads use the existing
ready-only same-cell peer. Without an optional qualified owner/projection,
standalone reads retain native repository files/defaults without selecting an
incomplete legacy runtime or bootstrapping it.

Deep mappings merge; lists/null/scalars replace. Credential source allowlists
and identity/feed variable-name collections retain their native union contract
and independent admission. Plugin activation/settings source paths remain
harness-owned, even when provision configuration is knowledge-owned.
Source `provision.files[].src` remains relative to its declaring repository root,
not incidental cwd; `dest` is remote and may use `~`. List replacement retains
one source for the whole list. Configuration reads do not provision, start the
Connection Owner, borrow a venue, authenticate or issue credentials.

## YAML fields

Fields are optional unless stated. Unknown keys retain native ignored-key
behavior; provider structural validators still apply. Wrong native structure
does not become valid merely because composition accepts JSON mappings.

| Fields | Type / default |
|---|---|
| `schema_version` | Migration-owned integer |
| `defaults.machine_type`, `defaults.location` | String; `largePremiumLinux`, `EastUs` |
| `defaults.devcontainer_path` | String; `.devcontainer/devcontainer.json`; used only by native multiple-config selection |
| `defaults.dotfiles_repo`, `defaults.harness_repo` | String/null; null; distinct purposes |
| `defaults.ssh_user` | String; `vscode` |
| `defaults.workspace_folder`, `defaults.acp_command` | String/null; null; command generated from effective workspace |
| `credentials.relay_port` | Integer; 0 means dynamic OS-assigned port |
| `credentials.ado_host` | String/null; null refuses hostless ADO request |
| `credentials.enforce_ado_rest_login` | Native boolean; true; connect-time admission only |
| `credentials.feed_token_env`, `credentials.identity_env` | String lists; empty; names only, never token contents; native union |
| `credentials.sources.<name>.enabled` | Boolean; false |
| `credentials.sources.<name>.allowed_hosts`, `.allowed_resources` | String lists; empty; native union across contributions; enabled source requires appropriate allowlist |
| `connection_owner.enabled` | Native boolean; true |
| `connection_owner.reconcile_interval` | Number; 15 seconds |
| `connection_owner.idle_shutdown_after` | Number/null; 300 seconds; null disables idle shutdown |
| `repos.<target>.workspace_repo`, `.workspace_folder` | String/null; null; workspace derived by native relationship convention |
| `repos.<target>.machine_type`, `.location`, `.devcontainer_path` | String/null; global fallback |
| `repos.<target>.bootstrap.post_create` | String/null; null |
| `provision`, `repos.<target>.provision` | Mapping; absent disables extras |
| `provision.files` | Atomic list; empty; entries require `src` and `dest` strings; `mode` defaults to string `0644`; invalid entries warn/skip natively |
| `provision.on_connect`, `.on_create` | String lists; empty; native invocation owns execution |
| `codespace_plugins` | List of mappings; empty; `{source, enable?, forWorkspaceRepo?}` as [native resolver](../src/agent_codespaces/codespace_plugins.py) |

Null can clear optional scalars; native block readers interpret absent/null hook
lists as empty. `{}` does not clear an existing mapping. No new aliases are
introduced. `AGENT_HOME`, `AGENT_CODESPACES_HOME`, installation context and
adoption receipts select runtime/ownership, not ordinary preference tiers.
Use the existing `config migrate` command for canonical relocation; ordinary
reads never rewrite config. Reads reload live, while existing connections and
created venues change only through their admitted owner operations.

Exclude secrets, SSH masters/sockets, lease/adoption/activation receipts,
credential cache, logs and installed generations from portable YAML. Credential
source *configuration* is not a secret and is documented here; it does not grant
access independently of relay, repository and transport custody.
