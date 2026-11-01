# SSH configuration reference

The qualified Worktrees configuration report is optional: a recognized older
peer without `--configuration` contributes no extra layer, preserving native
files/defaults. Binding/refusal, malformed or unsafe data and wrong-cell reports
still fail. Optional absence never qualifies an unvalidated delivered projection.

SSH has three distinct human-authored contracts: repository mesh metadata,
transport modules, and normalized profile inputs. None grants transport
installation, credential acquisition, profile mutation, or remote access on read.

## Repository mesh

[`src/agent_ssh/mesh.py`](../src/agent_ssh/mesh.py) selects the first existing
`machines.yaml`, `.agent-worktrees/machines.yaml`, `config/machines.yaml`, or
`.github/machines.yaml` in the calling Git repository. Without Git it walks
parents without crossing a `.git` boundary. Explicit CLI `--path` preserves its
native source selection. Missing optional mesh means no declared machines,
not permission to borrow another repository's mesh.

The YAML (JSON fallback when YAML is unavailable) root contains a `machines`
mapping keyed by logical machine ID. The read-only renderer projects:

| Family | Type/default |
|---|---|
| `control_plane.project` | Optional string, default unnamed; launch-role parsing belongs to the topology owner. |
| `machines.<id>.display_name`, `role`, `environment`, `hostname` | Optional strings, default empty. Hostname is display/transport data, not the logical execution ID. |
| `description` | Optional string, trimmed, default empty. |
| `capabilities` | Optional list of strings, trimmed and deduplicated in display order, default empty. |
| `ssh.ready` | Optional boolean, default false; declaration, not a live probe or credential. |
| `ssh.environments` | Optional list of objects (null is empty); entries have string `name`, `alias`, `shell`, `user`, default empty. |
| `dtssh.alias`, `dtssh.best_effort` | Optional string and boolean, defaults empty/false; descriptive tunnel metadata. |

Strict consumers require a root `machines` mapping, mapping-valued machine,
SSH and dtssh blocks, boolean readiness, and string aliases. Tolerant legacy
rendering ignores malformed entries. Unknown metadata is not projected.
Lists are native declared lists, not accumulated across unrelated registries.
The mesh renderer reloads on invocation, never probes on load.

### Ordinary presentation layers

An installed same-cell Worktrees owner supplies its read-only
`state-root --configuration --json` report. The canonical shared
`configuration_context` adapter validates exact repository, logical execution
machine, installation cell and explicit bound/unbound context. No Worktrees
runtime is imported and no PATH-only owner lookup is introduced.

Automatic `mesh-status` and session snapshots compose presentation only:
native declared defaults → base/knowledge
`.copilot-extensions/agent-ssh/config.yaml` → installation-owned `config.yaml`
→ the same plugin-relative path beneath the report's exact
`repository_machine_root`. These preference documents contain only
`machines.<existing-logical-id>.display_name`/`description` strings and
`capabilities` string lists. Knowledge wins at the repository tier; machine
and exact repository+machine win later. Lists replace atomically. Unknown,
null, or protected fields fail native preference validation; new machine
records are never created from presentation settings.

No admitted optional owner preserves the native standalone mesh. Invalid
present owner reports and explicit unresolved binding fail, not optional absence.
Explicit `--path` skips this ordinary graft and reads that chosen topology.
Aliases, readiness, role, hostname, transport, identity and profile custody remain
outside composition. Source-owned paths are read through shared physical-root
containment; relative path values are not part of this label-only schema.
The in-memory mesh retains JSON-pointer provenance; no generated state is written.
Conventional nested topology paths retain the repository root returned by native
discovery, not the topology file's parent directory. The Git-root reader and
existing non-Git selection walk supply that root; snapshots retain their original
selection start. Delivered configuration is matched against the selected root.
Explicit topology paths continue to bypass ordinary preference grafting.
Direct preference-enabled loaders retain a non-current selector's start through
`selection_start`; an unrelated current selection refuses grafting rather than
guessing the repository from the topology's parent folder.

Minimal native mesh (`machines.yaml`):

```yaml
machines:
  example-worker:
    display_name: Example worker
    description: General development worker
    capabilities: [development]
    ssh:
      ready: false
```

Portable presentation (`.copilot-extensions/agent-ssh/config.yaml`):

```yaml
machines:
  example-worker:
    description: Repository-specific display description
    capabilities: [development, documentation]
```

The second document cannot add a transport, alias, host, login or readiness
grant. An absent/unready legacy Worktrees owner leaves the native mesh intact;
an existing owner is read only through the runtime's completion-gated
`versioned_runtime.py` plus shared `ready_owner_prefix`, not an ambient binstub.

OpenSSH profiles retain actual connection authority (`HostName`, `User`,
`Port`, `IdentityFile`, proxy and host-key settings). Topology labels and
rendered readiness cannot override that admission. WSL, native Windows and
physical host aliases retain the existing logical execution identity contract.

## Transport module schema

[`contract/module.schema.json`](../contract/module.schema.json) maps the entire
`module.yaml` contract:

| Fields | Type/requirement |
|---|---|
| `module`, `kind` | Required transport slug and literal `transport`. |
| `produces.ssh_profile`, `produces.keyed_by` | Required object with literals `true`, `machine-name`. |
| `inbound_ports` | Required `none` or `some`; declaration, not firewall permission. |
| `summary`, `proxy_command`, `proxy_binary_default`, `config_schema` | Optional strings; template, binary default and transport-owned schema path. |
| `topology.default`, `topology.supported` | Optional `per-machine`/`jumpbox` selector and list of supported values. |
| `capabilities.install-client`, `emit-profile`, `verify`, `provision-server` | Optional booleans declaring supplied verbs. |
| `entrypoints.<verb>.posix`, `.windows` | Optional transport-owned script paths. |

Proxy templates accept `{hostname}`, `{name}`, `{user}`, `{port}`, `{distro}`,
`{proxy_binary}` under the native renderer. Module and schema paths belong to
their transport payload, not the calling CWD. See
[transport-provider-contract.md](transport-provider-contract.md) for admission,
dual-platform entrypoints, installation and provider boundaries.

## Normalized registry/profile schema

[`contract/registry-record.schema.json`](../contract/registry-record.schema.json)
is not the repository mesh format:

| Fields | Type/default |
|---|---|
| `transport`, `machines` | Required transport slug and list of machine records. |
| `topology` | `per-machine` (default) or `jumpbox`. |
| `proxy_command_binary` | Optional transport binary override string. |
| `gate` | Optional jumpbox object; required exact `name`, optional `hostname`, `user`, `identity_file`, `strict_host_key_checking`, `options`. |
| `machines[]` | Required exact `name`; optional `hostname`, `user`, integer/string `port`, `identity_file`, `via` (`direct` default or `jumpbox`), `options`. |
| `options.<OpenSSH-key>` | String/number/boolean/null; keys cannot be `Host`, `Include`, `Match` or non-option identifiers. |

Names are exact non-pattern OpenSSH aliases; machine and gate aliases must be
unique case-insensitively. Transport-specific configuration under
`transports/dtssh` and `transports/wsl` extends this contract rather than replacing
the registry parser. Native renderer validation is still required even when a
document satisfies JSON schema. Unknown schema keys do not automatically gain
rendering behavior; list/null behavior follows the transport parser, not a
generic list union.

`gateway_profile.py` and [gateway-transport.md](gateway-transport.md) own the
separate Gateway settings and credential-provider boundary. Copilot detached-home
JSON is separately scoped to one workspace per SSH alias; it is generated
execution state, not mesh preference configuration. Keys, generated fragments,
known-host receipts and connection tokens must not be grafted as ordinary
preferences or printed in examples. Change/reload profiles only through their
native explicit owner operation.

The transport extension schemas are
[`transports/dtssh/schema/config.schema.json`](../transports/dtssh/schema/config.schema.json)
and [`transports/wsl/schema/config.schema.json`](../transports/wsl/schema/config.schema.json).
Both fix `topology: per-machine`. dtssh requires each machine's `name`,
live discovered `hostname`, `port`, `identity_file`; optional `user`,
`proxy_command_binary`, and options `IdentitiesOnly` (`yes`/`no`),
`UserKnownHostsFile`, `StrictHostKeyChecking`, `ServerAliveInterval`.
WSL requires `name`, `user`, `port`, `distro`; optional `identity_file` and
host-key/keepalive options. WSL reaches the local distribution via native
interop, not a TCP host inferred from a physical alias. Option extensions
remain string/number/boolean data and pass native option validation.

`gateway_profile.py` accepts bounded JSON (1 MiB) with **exactly** `schema`
(`agent-ssh.gateway-profile`), `version` (integer `1`), `gateway` exact SSH
alias, `identity_file` absolute local path, and `machines` list (at most 256).
Each machine has exactly `name` (exact alias), `user` (bounded login identifier),
`reverse_port` (unique integer 1..65535), `host_key` (approved single-line
ed25519 public key). Gateway and machine aliases are case-insensitively unique.
Unknown fields, duplicate JSON keys and partial rows fail; there are no merge
or null defaults for required fields. This is a protected native route binding,
not presentation preferences or permission to install/configure a Gateway.
