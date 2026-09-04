# agent-ssh

The **SSH connectivity layer** for the agent fabric. It provides a standalone
`agent-ssh` CLI that renders machine-name SSH profiles, verifies reachability,
and defines the transport-provider contract used by direct and tunnel
transports.

## What you can do with only this plugin

`agent-ssh` does not require a harness, daemon, or sibling plugin. From a
registry plus a transport `module.yaml` it writes one managed SSH fragment:

```powershell
agent-ssh emit-profile registry.yaml --module transports\direct\module.yaml
agent-ssh doctor
agent-ssh verify --timeout 8 my-machine
agent-ssh explore my-machine --json
agent-ssh mesh-status
```

The CLI manages only SSH aliases. Once `ssh <name>` works, sibling plugins such
as agent-bridge or agent-codespaces can use that OpenSSH surface, but agent-ssh
does not import their runtimes or require them to be installed.

`restore-host` exposes transport-owned host setup to declarative orchestrators
without requiring them to know installed payload paths:

```powershell
agent-ssh restore-host --transport dtssh --alias example-host --port 2222 --dry-run
agent-ssh restore-host --transport dtssh --alias example-host --port 2222 --apply
```

The default/dry-run path executes the dtssh host status contract without
mutation. `--apply` invokes the transport's idempotent installer with
interactive login disabled; authentication remains an external prerequisite
and is never captured. When `--apply` is invoked from an SSH session on
Windows, it brokers the updater through WMI so stopping the serving sshd cannot
reap the updater with the SSH session. That mode returns
`verification_required: true` and does not claim `applied` until the caller
reconnects and runs the dry-run/status contract. Use `--json` for structured
command, output, and result data.

On Windows, the installer preserves the dtssh server host identity across
updates before restarting the host. It automatically uses an available
`OneDriveCommercial` folder for an alias-scoped durable backup, otherwise it
uses a separate local backup outside dtssh's state directory. The
`AGENT_SSH_DTSSH_HOST_KEY_BACKUP_ROOT` environment variable or the install
script's `-HostKeyBackupRoot` parameter selects an explicit location. Partial,
corrupt, or conflicting identities fail closed instead of silently rotating a
key that clients have pinned.

`mesh-status [--json]` is a fail-open view of a calling repository's
`machines.yaml`. In addition to SSH readiness and environments, it shows the
optional static machine metadata shared with agent-worktrees and agent-bridge:
`role` is a stable terse classification, `description` explains the machine's
purpose, and `capabilities` is an ordered list of broad discovery hints. These
fields describe topology, not live machine state.

The repository-gated mesh pointer also names the maintenance fallback for a
machine that remains unreachable after bounded diagnosis. Repeatable state
belongs in agent-machines requirement packages or another declared auto-update
owner; residual local execution becomes a machine-scoped maintenance issue in
an explicitly identified user repository. The target drains that queue with
the optional `agent-machines:performing-machine-maintenance` skill when that
plugin is active. The emitted rule remains self-contained when it is absent:
maintenance becomes inspection-only and mutation stops until an equivalent
trusted workflow is available. agent-ssh reports the routing boundary but does
not own the queue or execute issue instructions.

## Minimal setup

When the plugin is enabled, its skills and hook are available immediately. The
runtime can be stamped cheaply, then self-provisions on first `agent-ssh` use:

```powershell
# Windows, from this plugin directory (checkout or installed payload)
pwsh -File .\scripts\install.ps1 stamp

# POSIX / WSL
bash ./scripts/install.sh stamp
```

Use `install` instead of `stamp` to build the runtime eagerly. The installer
deploys a binstub under the user's local bin directory and a version-gated
session-start reconcile hook under `~/.agent-ssh/bin`; the first self-provisioning
call prints a `::agent-provisioning::` line and fails loud if the runtime cannot
be built.

## The split (core + in-box vs. external transports)

| Piece | Home | Owns |
|---|---|---|
| **agent-ssh core + contract** (this plugin) | copilot-extensions (public) | SSH-profile creation/validation, `~/.ssh/config.d` coexistence, `verify`, `explore`, and the `module.yaml`/registry-record contract schemas. |
| **In-box transports** (`transports/<module>/`) | this plugin (public) | **Self-contained** transports with **no non-public provider/multi-machine system config** — today `direct` (plain SSH), `dtssh` (real-user reach over public Microsoft Dev Tunnels; operator identity injected at deploy), and `wsl` (local-to-WSL reach via the `wsl.exe` interop stdio pipe — needs no ProxyJump/localhostForwarding). |
| **External provider transports** | provider-owned marketplaces | Transports needing **multi-machine system/provider config or credentials** — e.g. the Cloudflare transport (Access org / SSO / multi-machine system hostnames), which ships as its own plugin and registers against this contract. |

**Where a transport lives is decided by one axis: does it carry non-public
provider/multi-machine system config?** If not, it ships **in-box** under `transports/`
(direct, dtssh, wsl). If it does, it stays an **external plugin** in its audience's
marketplace and keeps its hostnames, identifiers, and secrets out of this public
core (cloudflare). Either way it plugs in through the same `module.yaml` contract.

## The deliverable: name-keyed SSH profiles

Consumers reach a machine by `ssh <name>`. Each transport contributes only its
own `Host <name>` blocks to a managed `~/.ssh/config.d/50-agent-ssh-<module>.conf`
fragment, so multiple transports coexist on one client, dispatched per machine
by the registry `transport:` key. No transport owns the whole config.

New fragments carry schema-v1 source identity for the absolute registry and
transport-module files that produced them. Keep those source files durable:
operational commands audit each managed fragment against the current sources,
withdraw confirmed-stale aliases from `verify`/`explore`, and emit bounded
warnings without probing the host. Legacy fragments remain usable with a
`legacy-unattributed` advisory until `emit-profile` rewrites them.

`agent-ssh doctor [--json]` reports every managed-fragment finding and the exact
file to re-emit or remove. It scans only `50-agent-ssh-*.conf`; unrelated
OpenSSH drop-ins are never parsed or changed. Cleanup is intentionally
report-only because agent-ssh does not have a receipt ledger that could prove
safe deletion.

## Layout

```
plugins/agent-ssh/
  plugin.json
  pyproject.toml
  src/agent_ssh/
  skills/
    agent-ssh/SKILL.md              # profile awareness/consumption
    setting-up-ssh-host/            # dtssh transport runbook (host)
    setting-up-ssh-client/          #                         (client)
    sharing-ssh-keys/               #                         (key hygiene)
    troubleshooting-devtunnel-ssh/  #                         (diagnosis)
  core/ssh_profile.py               # compatibility wrapper for the packaged core
  contract/
    module.schema.json              # transport-provider contract (the recipe shape)
    registry-record.schema.json     # normalized machine record
    examples/{direct,cloudflare}.module.yaml   # contract exemplars (rendering tests)
  transports/                       # first-party in-box transports (real, not exemplars)
    direct/module.yaml              # plain SSH (no recipe, no installer)
    dtssh/                          # module.yaml + scripts/ (install-host/client + launcher)
                                    #   + deploy/emit-registry + schema/ + examples/
    wsl/                            # local WSL interop transport + deploy/emit-registry
  scripts/{install,init,emit-profile,verify}.{sh,ps1}
  docs/transport-provider-contract.md
```

## Usage path

- **Core CLI / contract:** `skills/agent-ssh/SKILL.md` and
  `docs/transport-provider-contract.md`.
- **dtssh host/client setup:** `skills/setting-up-ssh-host/SKILL.md` and
  `skills/setting-up-ssh-client/SKILL.md`.
- **Key handling:** `skills/sharing-ssh-keys/SKILL.md`.
- **Failures:** start with `skills/troubleshooting-devtunnel-ssh/SKILL.md`.

## Writing a transport

Ship a `module.yaml` conforming to `contract/module.schema.json`. Provide a
`proxy_command` template (or omit it for plain SSH). The core does the rest:
renders `Host` blocks, manages the `Include`, writes your namespaced fragment,
and verifies reachability. If your transport is self-contained (no non-public
config), add it in-box under `transports/<module>/`; otherwise ship it as its own
provider plugin. See `docs/transport-provider-contract.md`.
