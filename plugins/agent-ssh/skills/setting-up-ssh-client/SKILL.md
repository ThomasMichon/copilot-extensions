---
name: setting-up-ssh-client
description: Configure outbound SSH over Microsoft Dev Tunnels as the real interactive user, using dtssh. Use when asked to "set up an SSH client", "set up a dtssh client", "connect through Dev Tunnel", "dtssh discover", "install dtssh", "login to devtunnel", or "reach dt-<host>".
---

# Setting Up an SSH Client (dtssh)

Configure a client machine to reach a host whose interactive session is published
through **[dtssh](https://github.com/bmiddha/devtunnel-ssh)** (see
`setting-up-ssh-host`). dtssh provisions and pins keys for you, so there is **no
per-machine keypair to generate or share, no ProxyCommand, and no host-key
prompt** — three commands and you're in, landing as the **real Entra user**.

## 1. Install dtssh

```powershell
irm https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.ps1 | iex
```

dtssh auto-downloads the `devtunnel` CLI on first use.

## 2. Sign in with Entra — the SAME account as the host

Launch WAM in its own window. Do not run login inline in a headless/embedded
terminal; it often falls back to device-code flow, which corporate Conditional
Access blocks.

```powershell
dtssh login
# verify:
devtunnel user show
```

The client and host must be signed in to the **same** Entra identity — the host's
Dev Tunnel is owner-only, so it only admits its owner.

## 3. Discover hosts and connect

```powershell
dtssh discover        # wires up `ssh dt-<host>` for every host you can reach
ssh dt-<host>         # lands as the real Entra user
```

`dtssh discover` writes the SSH config entries (HostName, key, pinned host key,
ProxyCommand) automatically. Re-run it whenever a new host comes online or a host
is re-provisioned.

## 4. Verify

```powershell
ssh dt-<host> "whoami && hostname"
```

Expected: you land as the real Entra user (e.g. `CORP\you`) on the target host.

## Edge cases

- **`ssh dt-<host>` unknown / not in config:** run `dtssh discover` (and confirm
  the host is actually hosting — `dtssh host --persist` on the host).
- **Connects but no host reachable:** the host's interactive session isn't up. Log
  on to the host once so its Startup launcher starts `dtssh host`, then disconnect;
  the listener persists.
- **Device-code prompt appears:** cancel it and re-run `dtssh login` from an
  interactive desktop session (WAM in its own window).
- **`psmux.exe` "cannot execute" over SSH:** WinGet `Links\*.exe` App-Execution-Alias
  shims don't run over a non-interactive SSH logon. Invoke the real package exe
  under `%LOCALAPPDATA%/Microsoft/WinGet/Packages/…` or wrap in `pwsh -Command`.
- Deeper diagnosis: `troubleshooting-devtunnel-ssh`.
