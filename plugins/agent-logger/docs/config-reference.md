# Logger configuration reference

Canonical readers:
[`config.py`](../src/agent_logger/config.py),
[`configuration_layers.py`](../src/agent_logger/configuration_layers.py),
[`config migrations`](../src/agent_logger/config_migrations.py),
[organization/manifest contract](manifest-contract.md), and
[deployment topologies](deployment-topologies.md).

## All human-authored surfaces

| File / family | Scope, type and defaults | Validation / ownership |
|---|---|---|
| `$AGENT_LOGGER_HOME/config.yaml` (default `~/.agent-logger/config.yaml`) | Machine-wide mapping; built-in defaults below | In-memory schema migration; explicit home selects this machine source, not a knowledge repo. |
| `.agent-logger.yaml`, `.agent-logger.yml`, `.config/agent-logger.yaml`, `.config/agent-logger.yml` | First eligible alias; repository organization mapping, `schema_version` integer ≥1 (omitted=current, 3) | Registered project **and** registered default branch, or exact operator-approved trust path; links/linked ancestry rejected. |
| Same organization aliases in the bound knowledge checkout | Repository-tier graft over reusable base | Same organization schema and independent trust gate. Binding alone does not make the checkout trusted. |
| Same organization aliases in the owner-selected project configuration root | Exact repository+machine organization overrides | Root comes from Worktrees read-data report, preserving registered remote/cell identity; not discovered by basename. |
| `.copilot-extensions/agent-logger/config.yaml` | Repository aggregate/opt-in declaration, distinct from organization aliases | [Aggregate schema/reader](../src/agent_logger/aggregate.py) and [origin opt-in reader](../src/agent_logger/sync/origin.py). Never load this as organization YAML or generic machine policy. |
| Machine `aggregate` admission and `tenant` organization declarations | Explicit admitted repositories, machine selectors, claims, resources and tenant sync overrides | [Aggregate](../src/agent_logger/aggregate.py), [tenancy](../src/agent_logger/tenancy.py), [manifest contract](manifest-contract.md); admission is not an ordinary preferences merge. |

Ordinary precedence is **defaults → trusted base organization → trusted bound
knowledge organization → machine-wide config → selected repository+machine
organization → explicit environment override**. Repository organization is
deliberately restricted; it cannot set machine identity, active sync target,
notify credentials, retention authority or process-log reclamation. Aggregate
and origin aggregation are separate, source-attributed admission operations.

`AGENT_LOGGER_REPO_CONFIG` explicitly selects one organization file through the
same trust/link gate. `0`, `false`, `off`, or `none` disables repository discovery
and grafting. Scheduled `config_repo` discovery retains this exact-file contract.
The native launcher may deliver `AGENT_CONFIGURATION_CONTEXT`; outside that
launch, the existing qualified Worktrees peer supplies the read-only report.
An explicit file selector remains one selected organization source, without
additional inferred graft discovery. Ordinary discovery can receive trusted
knowledge organization even when the base has no organization file.
Absence of an optional legacy owner preserves standalone behavior. Invalid
explicit binding/cell/projection data is an error, not an empty knowledge layer.
Namespaced invocation and a delivered projection independently retain the
installation-owner/cell gate. Legacy reads use the existing completion-gated
[`resolve_python`](../../../libs/versioned-runtime/versioned_runtime.py)
callback through `ready_owner_prefix`, never a provisioning front door.

## Organization fields

| Field | Type; default / meaning |
|---|---|
| `schema_version` | Integer ≥1; omitted=3. Versions ≤3 reject unknown fields; future versions ignore fields unknown to this reader. |
| `log.root` | Safe relative string; omitted leaves the next layer/default applicable, explicit organization null is invalid. A provided value resolves against its **supplying** organization checkout, not the harness cwd. Escape/absolute paths are rejected. |
| `log.path_template` | Safe relative template string; default `{year}/{month}/{day} {hhmmss} {title}.md`; only year/month/day/hhmmss/machine/title tokens. |
| `log.timezone` | IANA timezone string or null; null=system timezone. |
| `log.note_marker` | Nonempty string; default `SESSION NOTE:`. |
| `log.template` | Nonempty Markdown template or null; null=built-in structure. Allowed tokens: title/date/branches/prs/summary/key_changes/commits/open_items. |
| `log.narration_style` | Nonempty string or null; `objective` expands to neutral factual narration. |
| `log.exemplars` | Nonempty-string list or null; atomic replacement, null=no examples. |
| `log.closing_remark` | Nonempty string or null; null=no closing seam. |
| `sync.local_path` | Schema-v3 safe absolute native path; portable organization value for a common local destination only. It normalizes to `sync.targets.local.path`, never changes `sync.target`. Foreign-platform paths are rejected when effective for the local target. |
| `tenant` | Dedicated tenant declaration mapping; not merged into ambient machine sync policy. See tenancy reader for its required selector/target fields. |

Unknown organization fields at supported versions are errors. `log`/`sync`
must be mappings when supplied; native field validators determine permitted
nulls. Mapping merge is recursive; ordinary lists, scalars and permitted nulls
replace atomically. Relative roots retain their own source root. No new generic
list/set semantics are introduced.

## Machine fields and defaults

The exact maintained default tree is [`DEFAULTS`](../src/agent_logger/config.py);
these are all field families, not an independent schema copy:

| Family | Fields / types / neutral defaults |
|---|---|
| `store_dir` | Path string/null; null=`<home>/session-digests`. |
| `machine` | `name`, `role` optional strings; name null=detected native identity, role null=unspecified. These are machine-owned, not repository preferences. |
| `log` | Organization fields above plus `voice_pack` string (`none`); environment may select voice pack. |
| `sync` selection | `target` string (`local`), `source` path/null (`~/.copilot`), `targets` keyed mapping; local.path=null (`<home>/sessions`), onedrive.subfolder=`Apps/agent-logger/sessions`; ssh/ssh-tunnel/ingest options retain their transport owners. |
| `sync` filters/origin | `repo_allowlist`, `repo_denylist`, `harness_repos` string lists (`[]`), `repo_allowlist_fail_closed`, `require_repo_opt_in` booleans (`false`). Lists replace; native origin logic is not a knowledge binder. |
| `sync` lifetime | `retention_days` number/null (null/nonpositive=retain), `lock_timeout_sec` number (10), `lock_name` string (`session-sync.lock`), `usage_capture` boolean (true). |
| `sync.notify` | `url`, `bearer_token_file` path/string/null (unset), `timeout` number (5); token **file reference**, never inline credentials in shared examples. |
| `sync` protection / process logs | Complete nested defaults and strict validation in [sync protection](../src/agent_logger/config_sync_protection.py) and [process-log config](../src/agent_logger/config_process_logs.py). Reclamation requires pinned **machine-only** provenance; ordinary composed Config receives no retirement authority. |
| `chronicle` | `enabled` bool=false, `settle_seconds` number=600; holder/corpus_root/db_path/manifests_dir/default_sink strings/null; routes/skip_repositories lists=`[]`, sinks mapping=`{}`. Null route sink means skip, not fall-through. Sink fields: repo_path/output_root/log_path_template/narration_style/exemplars/closing_remark/landing/push, validated by [chronicle reader](../src/agent_logger/chronicle/__init__.py). |
| `catalog` | `db_path` path/null; null=`<home>/review-catalog.db`. |
| `aggregate` | Machine admission mapping, default disabled/no admitted plan; strict typed repository/resource selectors are in [aggregate reader](../src/agent_logger/aggregate.py). Higher ordinary preferences cannot authorize a plan. |

Environment selectors `AGENT_LOGGER_HOME`, `AGENT_LOGGER_SYNC_TARGET`,
`AGENT_LOGGER_VOICE_PACK`, exact `AGENT_LOGGER_REPO_CONFIG` and exact-path
`AGENT_LOGGER_TRUST_REPO_CONFIG` retain native semantics. Invocation corpus/root
selectors choose query data, not the configuration repository.

## Reload, provenance and exclusions

Each `load_config()` reacquires ordinary files; environment overrides are last.
`Config.configuration_provenance` contains winning JSON-pointer source/tier
records for the shared plan. Long-running sync/chronicle operations retain
their native acquisition/locking boundary, not automatic hot reload.
`include_repo=False` remains a strictly machine-only load and retains pinned
process-log policy provenance. File changes do not install or start anything.

Session archives, origin sidecars, digest stores, catalog/chronicle databases,
reservations, lock files, provider receipts and credentials are state, not
configuration grafts. Do not move aggregate authority, sync targets or secrets
into the organization schema to make an example look uniform.
