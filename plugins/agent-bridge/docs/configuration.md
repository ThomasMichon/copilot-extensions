# Bridge configuration reference

[`src/agent_bridge/config.py`](../src/agent_bridge/config.py) owns human-authored
configuration loading. [`models.py`](../src/agent_bridge/models.py) defines
`ServiceConfig`, `TopologyProfile`, `RepoBridgeConfig` and nested field schemas.
[machine-config.md](machine-config.md) is the canonical complete topology,
adoption, machine identity, legacy roster and source-path reference.

## Files and selectors

The installation's `config.yaml` (legacy standalone `~/.agent-bridge/config.yaml`)
contains service settings and named topology profiles. `install_paths.py` owns
installation-root selection; reading portable preferences cannot replace a
cell-owned root, database, auth file or routing record.

Repository ordinary spawn defaults use
`.copilot-extensions/agent-bridge/config.yaml`, then legacy
`.agent-bridge/config.yaml` only when canonical is absent. The explicit
installation-marketplace overlay is
`.copilot-extensions/agent-bridge/marketplaces/<marketplace-id>/config.yaml`.
`load_repo_bridge_config` deep-merges mappings, atomically replaces lists and
scalar/null values, and applies the native Pydantic schema. Missing repository
source is optional; malformed-present or unavailable source refuses preference
authority fallback. Repo `preference_source` is forbidden at the root; the
native `default_env` policy selector has its own validated contract.

Machine-profile spawn defaults and explicit agent entries retain their native
roster precedence; command-line `--project`, qualified `<repo>@<venue>`,
`--machines-yaml`, `--agents-config` and explicit spawn arguments remain
invocation selections rather than another portable tier. Existing stateless
topology path inheritance may point to admitted knowledge state; it does **not**
itself graft repository preferences. `config adopt` writes only the user
profile and does not modify repositories or create a knowledge binding.

### Ordinary spawn-default layers

For an admitted installed Worktrees owner, Bridge now reads the canonical
`state-root --configuration --json` report through its existing qualified
peer-launch prefix (never a new PATH lookup). `configuration_context` validates
the returned exact identities and bound/unbound context. No Worktrees runtime
is imported. Ordinary `default_copilot_args`/`default_env` compose built-ins →
base then bound knowledge repository files → explicitly declared machine profile
fields → canonical plugin config beneath the owner's exact
`repository_machine_root`. Marketplace overlays retain native selection.
The repository+machine tier is cell-scoped; repository role sources retain
physical source ownership. Lists replace and environment mappings deep-merge.

Knowledge/repository+machine files may contain only these ordinary fields;
native session modes, remote bindings, endpoint and installation settings cannot
be supplied by those portable overrides. Pydantic validation and preference
source validation remain independent. An unavailable optional installed owner
preserves legacy native defaults; invalid present reports or explicitly
unresolved binding must not become absence. The derived roster's native
`_effective_spawn_defaults` consumer applies the machine tier before the exact
repository+machine tier and retains in-memory JSON-pointer provenance.

A minimal optional ordinary repository document is:

```yaml
default_copilot_args: []
default_env: {}
```

It supplies no endpoint, credential, model override, service activation or
installation authority. Put service/topology settings in their native owner
file rather than copying them into a portable default override.

## Human-authored service fields

All defaults/types below come from `ServiceConfig` and its nested models:

| Family | Type/default |
|---|---|
| `schema_version` | Integer `2`; `config_migrations.py` owns migration. Read-time migration is in-memory; explicit migration/writers own persistence. |
| `port`, `bind`, `db_path`, `log_level` | Integer `0` (ephemeral), string `127.0.0.1`, cell-owned `sessions.db`, string `info`. Endpoint/database ownership is not portable policy. |
| `topologies.<profile>` | Object of `TopologyProfile` fields below, default empty map. |
| `context_thresholds.warning`, `.critical` | Integers `75`, `90`. |
| `auto_handoff.enabled`, `.unwatched_only` | Booleans false/true; no automatic transfer merely by reading. |
| `timeouts.codespace_boot`, `.ssh_connect`, `.session_start`, `.session_new`, `.command`, `.session_host_ready` | Numeric seconds `300`, `120`, `240`, `1200`, `1800`, `90`. |
| `retention.enabled`, `.max_age_hours`, `.statuses`, `.vacuum`, `.vacuum_min_free_mb`, `.sweep_interval_hours` | true, numeric `168`, list `ended/failed/stopped`, true, numeric `128`, numeric `12`. Live sessions remain protected by native GC admission. |
| `worktree_discovery_interval` | Numeric seconds `0` (on-demand). |
| `agent_roster_cache_interval` | Finite numeric seconds `12`, minimum `1`. |
| `idle_shutdown_seconds` | Integer `0` (disabled). |
| `enable_credential_relay` | Boolean true; service startup, not configuration reading, owns relay activation. |
| `session_host_stale_reap_seconds` | Integer `0` (age bound disabled). |
| `graceful_cancel_settle_seconds`, `cancel_turns_on_redeploy` | Integer `45`, boolean false; restart alone remains a transport event by default. |
| `idle_reap_ttl_seconds`, `idle_reap_sweep_seconds` | Integers `600`, `120`; sweep has native 30-second floor. |
| `session_host_unexpected_reap_seconds`, `session_host_active_reap_seconds` | Integers `60`, `1800`; existing native child custody remains required. |
| `live_stall_interrupt_after_s` | Integer `900`; nonpositive disables threshold-based interruption. |
| `session_modes` | Native `SessionModes` object; see `preference_models.py` and existing machine-config session-mode section. |
| `remote_acp`, `remote_acp_bindings`, `remote_acp_auth_sources` | Maps of target/binding/approval objects from `remote_acp_config.py`; protected remote relationship and credential-custody inputs. |
| `local_execution_space` | Native execution identity selector from `execution_identity_models.py`, not hostname or portable preference. |
| `preference_source` | `caller-settings` default or `target-settings`; protected preference-source contract inherited from `PreferencePolicyFields`. |

Topology profiles contain optional string `machines_yaml`, `agents_config`,
`source_repo`, `default_copilot_args` list (empty) and `default_env` string map
(empty). `source_repo` is a stable locator captured by adoption, not execution
identity. Repository schema contains `default_copilot_args`, `default_env`,
`remote_acp_bindings`, `session_modes`; only eligible ordinary defaults should
participate in portable composition. Native remote bindings, session preference
source authority and execution identity retain their independent validators.

Unknown service/repository model keys are ignored for forward compatibility;
known invalid session-mode, remote ACP, explicit preference-policy and
execution/topology values fail closed rather than silently resetting authority.
Empty mappings merge; lists replace, not append. Null replacement must still
satisfy the destination native field type (null is not a magic delete).

### Session-mode and remote-field ledger

`session_modes.default` is optional `acp`/`cli` (null = no preference).
`session_modes.venues` maps exactly `codespace`/`container`/`ssh`/`local` to
those modes. `session_modes.repos` maps exact case-sensitive repository selectors
to objects with their own optional `default` and `venues`. All maps default
empty; unknown keys fail. Resolution is most-specific repo+venue, repo default,
venue, default within each native owner; the native operator/repo/service
precedence remains separate from ordinary spawn-default composition.

`remote_acp_config.py` defines every remote-family object (all reject extras):

| Object | Fields/defaults |
|---|---|
| `RemoteAuth` | `source` = `git-credential` default / `injected` / `none`; required nonempty `identity`; optional `credential_source`; `resource`/`scope` empty, `scheme` = `Bearer`. Resource/scope/reference require `injected`. |
| `RemoteAcpTarget` | Required `endpoint`, `transport` (`streamable-http`/`websocket`), `auth`; pinned `profile`/`draft_commit`; `application_approvals` = `required`, `permissions` = `manual`; `timeout_seconds` = `30` (1..120), `idle_seconds` = `120` (1..3600), `rpc_timeout_seconds` = `900` (1..86400), `max_frame_bytes` = 1 MiB (1 KiB..8 MiB), `max_queue_messages` = `64` (1..256), `allow_loopback_test` = false. |
| `RemoteAcpBinding` | Required qualified `plugin`, `endpoint`, nonempty `identity`; optional `credential_source`/`project`; `resource`/`scope` empty. |
| `RemoteAuthApproval` | Required `origin`, nonempty `identity`; `resource`/`scope` empty, `scheme` = `Bearer`. |
| `RemoteAcpRequirements` (plugin declaration, not local binding) | Required transport; pinned profile/draft commit, required application approvals/manual permissions; auth requirements source `git-credential` default / `injected`, scheme `Bearer`; description string empty. No endpoint/credential/identity/command. |

Endpoint strings are bounded to 2048 characters and require a real path,
no userinfo/query/fragment; transport/auth/TLS validators remain active.
Ordinary reads cannot approve a target, change pinned experimental protocol,
grant loopback-test authority, or manufacture credential-source approval.

## Reload and protected state

`load_config` reloads per call; a running daemon and derived roster retain their
existing snapshot/cache interval contract. Native `config show`, `config
validate`, roster reads and spawn paths share the loader; changing files does
not imply a running service has reloaded. Concurrent writers use
`config_write.py` locks and compare-before-write behavior; session mode changes
are preserved or refused on conflict.

`auth.yaml`, `active.json`, generation records, PID/process handles, session
host descriptors, session databases and provider/credential receipts are
generated or protected runtime state. They never become portable preferences.
`load_or_create_auth_token`, migrations, service lifecycle, profile adoption,
remote authentication and claim-producing operations remain **separate explicit
owners**, not effects granted by ordinary configuration. Never print secrets
or private local topology in provenance examples.
