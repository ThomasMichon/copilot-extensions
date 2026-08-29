---
name: agent-ssh
description: >-
  Create, verify, and use machine-name SSH profiles for the agent fabric, and
  use the transport-provider contract for direct or tunnel transports. Use when
  deriving ~/.ssh/config from a registry, validating reachability, adopting a
  machine into an SSH mesh, exploring or connecting to a reachable SSH target,
  running remote commands, launching agents on remote nodes, or authoring a
  transport module.yaml. Trigger phrases include:
  - "ssh"
  - "remote command"
  - "run on"
  - "connect to"
  - "other machine"
  - "remote machine"
  - "launch on"
  - "derive SSH config"
  - "audit agent-ssh fragments"
  - "verify SSH reachability"
  - "author an SSH transport module"
---

# agent-ssh (core + transport-provider contract)

> **Before you start — readiness (standalone runtime).** The runtime does not
> require agent-worktrees, agent-bridge, or a harness. In an agent session,
> invoke the exact `argv` from the agent-ssh session command catalog; the
> payload-local command self-provisions on first use. Do not search `PATH` or
> substitute a same-named command from another payload. Outside an agent
> session, stamp a management binstub from an explicitly chosen payload or
> checkout:
>
> ```powershell
> pwsh -File .\scripts\install.ps1 stamp   # Windows, from plugins\agent-ssh
> bash ./scripts/install.sh stamp          # POSIX / WSL, from plugins/agent-ssh
> ```
>
> The first CLI call may take ~30–120s to build the runtime (watch for
> `::agent-provisioning::`). Let it finish. If provisioning fails, surface the
> exact message; do not improvise a toolchain install.

The connectivity layer that makes machine-name SSH profiles real for the agent
fabric. The public runtime ships the transport-agnostic core and the provider
contract, plus the self-contained in-box transports (`direct`, `dtssh`, `wsl`).
Transports with provider/system-specific config can live in their own provider
plugins and register against the same contract.

## What lives here

- **SSH-profile emitter** (`<catalog argv[0]> emit-profile`) -- renders `Host <name>`
  blocks from a normalized registry. The ProxyCommand recipe comes from the
  transport's `module.yaml`, not from hardcoded transport logic.
- **Coexistence layout** -- a single managed `Include ~/.ssh/config.d/*` plus a
  per-transport drop-in `50-agent-ssh-<module>.conf`. Each transport owns only
  its own fragment.
- **Managed-fragment hygiene** (`<catalog argv[0]> doctor`) -- audits only the
  managed fragment namespace against the current registry/module sources, isolates
  malformed or stale peers, and gives exhaustive human/JSON report-only cleanup
  guidance without touching unrelated OpenSSH files.
- **Reachability verification** (`<catalog argv[0]> verify`) -- probes the active SSH
  profile by machine name and exits non-zero on missing names or unreachable
  aliases.
- **Transport-provider contract** (`contract/`) -- schemas and public exemplars
  for provider plugins.
- **Live introspection** (`<catalog argv[0]> explore`) -- read-only SSH probe of a
  reachable target's fabric runtimes, repos, and derived agents.
- **Mesh status** (`<catalog argv[0]> mesh-status`) -- render the calling repo's SSH
  machine mesh from its `machines.yaml` (per-host role, reachability, aliases).
  Config-driven and read-only; no probe.

## Emit a profile

```bash
<catalog argv[0]> emit-profile registry.yaml --module transport/module.yaml
```

Use `--print` to inspect the fragment without writing it. Use `--config-d` and
`--ssh-config` for tests or non-default SSH config locations. Keep the registry
and module files at durable absolute paths after emission: new fragments stamp
those sources and operational commands use them as current authority.

## Audit managed fragments

```bash
<catalog argv[0]> doctor [--json]
```

Doctor is exhaustive and report-only. It identifies the exact managed entry,
source, reason, and re-emission/removal remedy. Routine commands emit only a
bounded, deduplicated warning set. Legacy fragments remain active with a
`legacy-unattributed` advisory until `emit-profile` rewrites them.

## Verify reachability

```bash
<catalog argv[0]> verify --timeout 8 machine-a machine-b
```

A failure is fail-safe: the host is not considered reachable until the probe
succeeds. A confirmed-stale managed alias is rejected before the network probe;
ordinary connection failure remains reachability status and does not classify
the fragment as stale.

## Use a configured target

Once a machine-name profile is present and verified, use that configured name
rather than an IP address:

```bash
ssh <machine> "<command>"
```

Match the command syntax to the target shell reported by the machine registry
or `<catalog argv[0]> explore <machine>`. To launch a repository's agent entry
point remotely, invoke that repository's published binstub through the same
profile. Transport setup and repair remain in the dedicated client, host, key,
and troubleshooting skills shipped by this plugin.

## Explore a machine

```bash
<catalog argv[0]> explore <ssh-target> [--json] [--timeout 10]
```

Introspects a **reachable** target over SSH and reports, by convention, what the
machine offers the fabric: its checked-out repos and **where** they live (read
live from the machine's own project registry),
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

## Show the machine mesh

```bash
<catalog argv[0]> mesh-status [--path machines.yaml] [--json] [--summary]
```

Renders the **calling repo's** SSH machine mesh from its `machines.yaml` — for
each machine: `display_name`, `role`, `environment`, declared reachability
(`ssh.ready`), the per-environment SSH aliases (windows/wsl/…), and dtssh notes
(alias + best-effort). It is **config-driven and repo-specific**: it resolves
`machines.yaml` from the current git repo (or `--path`) and says nothing when the
repo ships none, so one repo's mesh never leaks into another. **Read-only** — it
parses config, it does not probe; `ssh.ready` is the operator's declared state,
so use `<catalog argv[0]> verify <alias>` to probe a host live.

A cwd-gated `sessionStart` hook (`scripts/emit-mesh-pointer.*`) emits only a
**succinct pointer** to this command when the repo has a `machines.yaml`, rather
than injecting the whole table every session — run `mesh-status` on demand for
the detail.

## Writing a transport

Ship a `module.yaml` conforming to `contract/module.schema.json`. Provide a
`proxy_command` template, or omit it for plain SSH. The core renders the SSH
profile, manages the managed Include, and verifies reachability.

`entrypoints` in a transport module are metadata for installers/orchestrators;
`<catalog argv[0]> emit-profile` and `<catalog argv[0]> verify` do not run transport setup
scripts. Use the relevant transport skill (`setting-up-ssh-host`,
`setting-up-ssh-client`, or provider-owned docs) for install/provision steps.
