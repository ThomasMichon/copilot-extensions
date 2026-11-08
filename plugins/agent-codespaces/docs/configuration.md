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
The caller's native installation is qualified before any delivered report is
read. A genuinely absent same-cell Worktrees peer, or its recognized older
`--configuration` option rejection, preserves native standalone defaults/files.
Wrong-cell data, explicit binding/refusal signals and ambiguous readiness or
read failures remain vetoes; optional compatibility never provisions an owner.

Deep mappings merge; lists/null/scalars replace. Credential source allowlists
and identity/feed variable-name collections retain their native union contract
and independent admission. Plugin activation/settings source paths remain
harness-owned, even when provision configuration is knowledge-owned.
Source `provision.files[].src` remains relative to its declaring repository root,
not incidental cwd; `dest` is remote and may use `~`. List replacement retains
one source for the whole list. Configuration reads do not provision, start the
Connection Owner, borrow a venue, authenticate or issue credentials.

## YAML fields

### Windows scheduling

The installation's own `<runtime>/config.yaml` has an installer-only
`windows_scheduled_task` field, outside layered repository preferences:

| Value | Windows install/update behavior |
|---|---|
| `preserve` (unset default) | Missing tasks stay absent; retain existing/custom registration and enablement. |
| `enabled` | Explicit opt-in to the supported login task; preserve disabled-task holds. |
| `absent` | Validate and retire only this installation's supported task registration, then continue independent capability/selected-Owner reconciliation. |

`connection_owner.enabled: true` remains capability activation, not scheduling
consent. On-demand Owner/relay operation is retained. The default does not
require ScheduledTasks, local-account setup or administrator rights. Without the
module, installer startup defers to on-demand use; with it, an observed disabled
task holds direct installer startup. Scheduling policy does not clear other
maintenance or automatic-operation holds.
On normal install/update, `absent` does not disable the Owner: enabled capability
still ensures the selected Owner generation through its existing direct launcher,
never through Task Scheduler. Disabled capability starts nothing. Unknown
capability status fails explicitly rather than reporting complete readiness.

For registration-only retirement without selecting an incoming source runtime:

```powershell
pwsh -File plugins\agent-codespaces\scripts\install.ps1 task-retire -InstallDir <runtime>
```

This `task-retire` command never queries capability activation or starts/stops an
Owner; it is deliberately narrower than normal install/update with `absent`.
`-DryRun` previews retirement. The exact root-folder task name, installation
working directory and supported owner launcher must match. Foreign, ambiguous,
multi-action or unsupported tasks are refused. Disabled tasks are retained with
an explicit hold because removing the registration would lose disabled intent.
Failed queries/removals are errors; a genuinely missing task is idempotent.
Retirement never stops the daemon, closes channels, revokes credentials or removes
leases, runtime generations, configuration, logs or other state. Full `uninstall`
is separate. Custom external scheduling remains operator-owned.

Fields are optional unless stated. Unknown keys retain native ignored-key
behavior; provider structural validators still apply. Wrong native structure
does not become valid merely because composition accepts JSON mappings.

| Fields | Type / default |
|---|---|
| `schema_version` | Migration-owned integer |
| `defaults.machine_type`, `defaults.location` | String; `largePremiumLinux`, `EastUs` |
| `defaults.max_owned_codespaces` | Positive integer; 30; conservative per-account allocation policy, not an immutable provider quota |
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

Owned-venue admission counts stopped as well as running venues under the
target repository's selected account. `allocate` and ordinary `create` require
credential-pinned complete inventory; a failed/malformed read or the 50-row
listing bound is unknown capacity, not an empty account. The policy can be
raised or lowered through the normal preference layers without changing the
provider's actual quota. Running-core headroom is independent; unknown machine
costs refuse additional compute, including a stopped venue's wake.

The same native preferences govern sequential reuse: configured machine cores
are a minimum for a newly borrowed venue, while its actual wake cost must fit
known headroom. There is no new reuse YAML switch. Compatible explicitly
returned stopped venues are considered before creating another owned slot;
existing workstream reuse retains its prior behavior. The
[creation/reuse contract](../README.md#create-options) describes
selected-runtime capability discovery, ownership and retained-state limits.
Explicit repository or non-default global devcontainer/location preferences
are not qualified by the current pool metadata, so they do not silently select
a cross-effort venue.

Exclude secrets, SSH masters/sockets, lease/adoption/activation receipts,
credential cache, logs and installed generations from portable YAML. Credential
source *configuration* is not a secret and is documented here; it does not grant
access independently of relay, repository and transport custody.
