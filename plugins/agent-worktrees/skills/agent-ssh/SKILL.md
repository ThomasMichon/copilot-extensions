---
name: agent-ssh
description: >
  SSH connectivity between project machines -- aliases, remote commands,
  and launching the project binstub on other machines. Use this skill
  when SSHing to another machine, running remote commands, or launching
  agents on remote nodes.
  Trigger phrases include:
  - 'ssh'
  - 'remote command'
  - 'run on'
  - 'connect to'
  - 'other machine'
  - 'remote machine'
  - 'launch on'
---

# Agent SSH -- Remote Machine Access

This project may be deployed across multiple machines. The machine
registry at `machines.yaml` in the repo root defines which machines
exist, their SSH aliases, shell types, and roles.

## Finding Available Machines

Read `machines.yaml` in the repo root. Each machine entry under
`machines:` includes an `ssh:` block with:

- **`environments`** -- list of SSH targets (alias, shell, port)
- **`ready`** -- whether the machine is SSH-reachable (`true`/`false`)
- **`ip`** -- the machine's IP (for reference only; use aliases)

Only machines with `ssh.ready: true` are reachable.

## Running Commands Remotely

Use the SSH alias from `machines.yaml`, not raw IPs:

```
ssh <alias> "<command>"
```

Match command syntax to the remote shell (`shell` field in the
environment entry). For example, `pwsh` aliases expect PowerShell
syntax; `bash` aliases expect shell syntax.

## Launching the Project on Another Machine

The project binstub (same name as the project, shown in
`machine.instructions.md` under `Binstub:`) is available on each
machine where the project is installed. To start an agent-worktrees
session on a remote machine:

```
ssh <alias> "<binstub-name>"
```

This launches the full agent-worktrees flow on the remote machine
(worktree picker, setup, Copilot CLI session).

## Guidelines

- **Prefer SSH aliases over raw IPs.** Aliases encode the correct
  user, port, and key selection.
- **Check the shell type** before composing remote commands. The
  `shell` field in each environment entry tells you what runs on the
  other end.
- **Transport details are repo-specific.** How SSH connectivity is
  established (direct LAN, tunnels, VPN, jump hosts) varies by
  repository. Consult this repository's own SSH docs or skills for
  transport configuration, key management, and tunneling.
