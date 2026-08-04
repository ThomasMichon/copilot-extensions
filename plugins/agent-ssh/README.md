# agent-ssh

The **connectivity layer** of the agent fabric: it provisions and keeps the SSH
mesh real, and owns the **transport-provider contract** every transport plugs
into. It realizes the `visions/plugins/agent-ssh/` connectivity-layer intent.

## The split (core + in-box vs. external transports)

| Piece | Home | Owns |
|---|---|---|
| **agent-ssh core + contract** (this plugin) | copilot-extensions (public) | SSH-profile creation/validation, `~/.ssh/config.d` coexistence, `verify`, and the `module.yaml`/registry-record contract schemas. |
| **In-box transports** (`transports/<module>/`) | this plugin (public) | **Self-contained** transports with **no non-public provider/facility config** — today `direct` (plain SSH), `dtssh` (real-user reach over public Microsoft Dev Tunnels; operator identity injected at deploy), and `wsl` (local-to-WSL reach via the `wsl.exe` interop stdio pipe — GSA-safe, needs no ProxyJump/localhostForwarding). |
| **External provider transports** | provider-owned marketplaces | Transports needing **facility/provider config or credentials** — e.g. the Cloudflare transport (Access org / SSO / facility hostnames), which ships as its own plugin and registers against this contract. |

**Where a transport lives is decided by one axis: does it carry non-public
provider/facility config?** If not, it ships **in-box** under `transports/`
(direct, dtssh). If it does, it stays an **external plugin** in its audience's
marketplace and keeps its hostnames, identifiers, and secrets out of this public
core (cloudflare). Either way it plugs in through the same `module.yaml` contract.

## The deliverable: name-keyed SSH profiles

Consumers reach a machine by `ssh <name>`. Each transport contributes only its
own `Host <name>` blocks to a managed `~/.ssh/config.d/50-agent-ssh-<module>.conf`
fragment, so multiple transports coexist on one client, dispatched per machine
by the registry `transport:` key. No transport owns the whole config.

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
  scripts/{install,init,emit-profile,verify}.{sh,ps1}
  docs/transport-provider-contract.md
```

## Writing a transport

Ship a `module.yaml` conforming to `contract/module.schema.json`. Provide a
`proxy_command` template (or omit it for plain SSH). The core does the rest:
renders `Host` blocks, manages the `Include`, writes your namespaced fragment,
and verifies reachability. If your transport is self-contained (no non-public
config), add it in-box under `transports/<module>/`; otherwise ship it as its own
provider plugin. See `docs/transport-provider-contract.md`.
