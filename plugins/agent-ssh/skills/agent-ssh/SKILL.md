---
name: agent-ssh
description: >-
  Create and verify machine-name SSH profiles for the agent fabric, and use the
  transport-provider contract for direct or tunnel transports. Use when deriving
  ~/.ssh/config from a registry, validating reachability, adopting a machine into
  an SSH mesh, or authoring a transport module.yaml.
---

# agent-ssh (core + transport-provider contract)

The connectivity layer that makes machine-name SSH profiles real for the agent
fabric. The public runtime ships the transport-agnostic core and the provider
contract; concrete transports live in their own provider plugins.

## What lives here

- **SSH-profile emitter** (`agent-ssh emit-profile`) -- renders `Host <name>`
  blocks from a normalized registry. The ProxyCommand recipe comes from the
  transport's `module.yaml`, not from hardcoded transport logic.
- **Coexistence layout** -- a single managed `Include ~/.ssh/config.d/*` plus a
  per-transport drop-in `50-agent-ssh-<module>.conf`. Each transport owns only
  its own fragment.
- **Reachability verification** (`agent-ssh verify`) -- probes the active SSH
  profile by machine name.
- **Transport-provider contract** (`contract/`) -- schemas and public exemplars
  for provider plugins.

## Emit a profile

```bash
agent-ssh emit-profile registry.yaml --module transport/module.yaml
```

Use `--print` to inspect the fragment without writing it. Use `--config-d` and
`--ssh-config` for tests or non-default SSH config locations.

## Verify reachability

```bash
agent-ssh verify --timeout 8 machine-a machine-b
```

A failure is fail-safe: the host is not considered reachable until the probe
succeeds.

## Explore a machine

```bash
agent-ssh explore <ssh-target> [--json] [--timeout 10]
```

Introspects a **reachable** target over SSH and reports, by convention, what the
machine offers the fabric: its checked-out repos and **where** they live (read
live from the machine's own repo registry, `agent-worktrees repos list --json`),
which of those **back an agent**, each repo's declared **purpose** (`role` +
`summary`, read from the in-repo `.agent-worktrees/related.yaml` catalog(s)
checked out on the machine), whether the fabric runtimes (`agent-worktrees`
/ `agent-bridge` / `agent-dispatch`) are installed, and the **derived agents**
that fall out — `<repo>@<target>` for each agent-backing checkout (carrying that
purpose). `--json` emits the structured result.

`explore` is **read-only** — it runs one SSH probe and prints a report; it never
mutates local or remote state. Repo locations are read live from the machine at
query time (derive-don't-duplicate). It targets POSIX shells (Linux / WSL); a
PowerShell-host probe is a follow-on.

## Writing a transport

Ship a `module.yaml` conforming to `contract/module.schema.json`. Provide a
`proxy_command` template, or omit it for plain SSH. The core renders the SSH
profile, manages the managed Include, and verifies reachability.
