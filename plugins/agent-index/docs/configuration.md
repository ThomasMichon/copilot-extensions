# Index configuration reference

Canonical owners: [runtime config](../src/agent_index/config.py),
[layer adapter](../src/agent_index/configuration_layers.py),
[activation parser](../scripts/resolve_effective_config.py), and
[core/engine defaults](../src/agent_index/index_config.py).

## Human-authored files and exact scopes

| Source | Scope / selection |
|---|---|
| Built-in runtime/core defaults | No file; neutral client/host behavior remains useful without a knowledge binding. |
| `<repository>/.agent-index/config.yaml` | Shared repository declaration; historical canonical source. |
| `<repository>/.copilot-extensions/agent-index/config.yaml` | Canonical namespace spelling; when both spellings exist, this overlays the legacy base **within the repository tier**, before knowledge. |
| `.copilot-extensions/agent-index/marketplaces/<marketplace-id>/config.yaml` | Owner-selected marketplace overlay within the repository tier; installation identity remains independently validated. |
| Same repository paths in an explicitly bound knowledge checkout | Knowledge graft over the complete base repository tier. |
| `<installation-root>/config.yaml` | Machine-wide role/device/routing and ordinary preferences; `AGENT_INDEX_CONFIG` selects an exact file, `AGENT_INDEX_CONFIG_ROOT` selects its directory. |
| Same repository paths under Worktrees' selected project configuration root | Exact repository+machine overrides. Root comes from normalized registered remote identity and selected installation cell, not repository basename or host-name guessing. |
| `providers.d` attributed provider pointers | Content-domain provider registration, under its strict source/identity reader; not an ordinary preferences graft. |
| Repository `projects.yaml` / `repos.yaml` | Existing Worktrees discovery inputs for adopted corpus sources; registry ownership does not move into Index. |

Ordinary order: **defaults → repository (legacy, canonical, marketplace)
→ bound knowledge → machine-wide → selected repository+machine**.
Native `AGENT_INDEX_CONFIG_DATA_B64`, `AGENT_INDEX_EFFECTIVE_CONFIG` and
`AGENT_INDEX_REPO` selectors retain their meanings; a forwarded config is an
explicit execution input, not permission to discover another repository.

The adapter consumes the same native `AGENT_CONFIGURATION_CONTEXT` read-data
projection as other owners, or uses the existing qualified same-cell Worktrees
peer. A matching selected source root is required before using a projection.
Absent optional legacy owner/binding retains standalone behavior; explicitly
unresolved binding, foreign cell, malformed projection or invalid state policy
is an error. No new binder, registration engine or service is created.
The standalone activation script uses this same default read seam, not only
the delivered-projection path. Legacy invocation uses the existing
[`resolve_python`](../../../libs/versioned-runtime/versioned_runtime.py)
completion-gated SDK callback, never a self-provisioning front door. A valid
projection cannot bypass the independent installation-owner/cell or canonical
repository state-policy checks.
Without a delivered report, genuinely absent peers and recognized older
`--configuration` support retain native repository configuration even in
namespaced mode. No secondary legacy/cross-cell binding lookup occurs.
Delivered reports require fresh owner data; refusals and differing cells or
repository identities never become optional absence.

## Field families, types and defaults

| Family | Fields / types / default and required rules |
|---|---|
| `role` / `engine` | String; `role` preferred, legacy `engine` alias; `host` or `client`, otherwise unconfigured. Host capability/installation is separate from preference order. |
| `indexer` | Optional mapping with required nonempty `machine`, optional `ssh`, `endpoint`; singular compatibility. |
| `indexers` | Ordered list of the same mappings; identities must be unique in activation input. First is primary, later entries are failover order. Plural/singular declarations are alternatives: the higher layer removes the lower spelling. |
| `corpus.sources` | List of mappings with required nonempty `name`; supported connector fields include repo/root/ref/exclude/auth and source-native options. No sources by default. [Source connector ownership](architecture.md) retains validation and credential routing. |
| `device` / `capabilities` | Machine-owned device string/capability mapping, CPU/CUDA resolution remains native; unset device falls back to effective native capability/default, not a claim of GPU availability. |
| `endpoint` / `endpoints` | Optional URL string / ordered URL string list; plural preferred, singular fallback; unset=local host discovery. Endpoint preference does not authorize a host. |
| `host`, `port` | Runtime bind string/integer; shell defaults loopback and rendezvous-selected port (`0`); core legacy engine defaults retain their own constructor values. |
| Data/run/log/cache/config roots | Path strings selected by `AGENT_INDEX_*_DIR`, config-root and home selectors; native defaults under the chosen installation root. Durable stores/routing receipts are not grafted data. |
| Model/core options | model_name string, batch_size integer=16, device, max_seq_length integer=1024, model_profiles keyed mappings, content_table=`chunks`; exact maintained defaults in `IndexConfig`. |
| Streaming/search | stream_batch_size integer (500 GPU / 64 CPU unless explicit); search_in_process bool=false, query_device=`cpu`, fts_enabled=true, embed concurrency=0, max queue=8, search timeout=25 seconds. |
| Clustering | enabled=true, threshold default=.92, thresholds per bucket mapping, minimum size=2; cluster_slice_row_budget optional Python-only integer, no implicit CLI preference alias. |
| Host politeness/backup | indexer_nice=10, engine_nice=5; backup_dir path defaults under installation root, optional backup mount root; constructors/environment selectors remain native. |

The authoritative source parser contains the complete option/default tree;
the reference routes every family rather than inventing a second schema.
Unknown runtime YAML fields retain the native tolerant reader; malformed
explicit composed sources are errors. Activation's strict mapping/key/type,
duplicate-identity and source-name checks remain independent.

## Merge, null and paths

Mappings merge recursively; ordinary lists/scalars/null replace atomically.
The **one native list exception** is `corpus.sources`: higher-precedence
declarations come first, and lower declarations contribute only names not
already claimed (case-insensitive identity). Empty ordinary lists clear their
field; an empty sources list does not erase distinct lower source identities.
The native indexer/indexers alias normalizer runs before generic composition.
Null handling remains field-specific; it never means grant/activate/install.

Source-relative corpus paths retain the contributing source checkout, not the
launching harness cwd. Source roots and winning JSON-pointer provenance come
from the owner plan; they are read-data, not retrieval or credential authority.
Unsafe/escaping composed source paths are rejected by the shared reader.

## Activation, reload and state boundaries

The activation parser still requires an eligible repository declaration or
explicit forwarded execution input. A higher machine preference is not a
substitute for repository activation, installation readiness, source eligibility
or protected opt-in. Capability/role discovery remains independent of ordinary
configuration. Source-qualified explicit mode retains its inline-only contract
and does not discover host repositories or credentials.

Runtime config readers reacquire files at their native command/operation
boundary. Environment-backed constructor defaults retain their existing
import/instance lifetime; changing a YAML file does not restart the engine,
provision a server or mutate an existing index. Native corpus aggregation is
not a second knowledge binder.

Exclude vector/Lance stores, SQLite artifacts, state.json, source cursors,
providers' receipts, rendezvous/routing state, credentials and service registrations
from shareable preference examples. Token/account references stay with the
existing credential/provider owner; preference precedence grants no access.
