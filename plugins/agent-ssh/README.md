# agent-ssh

The **connectivity layer** of the agent fabric: it provisions and keeps the SSH
mesh real, and owns the **transport-provider contract** every transport plugs
into. It realizes the `visions/plugins/agent-ssh/` connectivity-layer intent.

## The split (multi-part provider architecture)

| Piece | Home | Owns |
|---|---|---|
| **agent-ssh core + contract** (this plugin) | copilot-extensions (public) | SSH-profile creation/validation, `~/.ssh/config.d` coexistence, `verify`, and the `module.yaml`/registry-record contract schemas. |
| **Provider transport plugins** | provider-owned marketplaces | Client installers, concrete `module.yaml` instances, and provider-specific config. |

A transport is **its own plugin in its audience's marketplace**, registering
against this contract -- not contributed here. Keep provider-specific hostnames,
identifiers, and secrets out of this public core.

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
  skills/agent-ssh/SKILL.md
  core/ssh_profile.py               # compatibility wrapper for the packaged core
  contract/
    module.schema.json              # transport-provider contract (the recipe shape)
    registry-record.schema.json     # normalized machine record
    examples/{direct,cloudflare}.module.yaml
  scripts/{install,init,emit-profile,verify}.{sh,ps1}
  docs/transport-provider-contract.md
```

## Writing a transport

Ship a `module.yaml` conforming to `contract/module.schema.json`. Provide a
`proxy_command` template (or omit it for plain SSH). The core does the rest:
renders `Host` blocks, manages the `Include`, writes your namespaced fragment,
and verifies reachability. See `docs/transport-provider-contract.md`.
