---
name: setting-up-ssh-host
description: Set up inbound SSH on a Windows host through Microsoft Dev Tunnels as the real interactive user, using dtssh. Use when asked to "set up an SSH host", "set up a dtssh host", "host SSH through Dev Tunnel", "configure inbound SSH", "host interactive sessions over SSH", or "fix ga_init unable to resolve user".
---

# Setting Up an SSH Host (dtssh)

Expose a Windows machine's **interactive user session** over SSH-through-Dev-Tunnel
so it can be reached and driven from other machines — landing remote clients as
the **real Entra user** (e.g. `CORP\you`), not a stand-in service account. This
is the host-side companion to `setting-up-ssh-client`.

The tool is **[dtssh](https://github.com/bmiddha/devtunnel-ssh)** (a turn-key
dev-tunnel-with-SSH). It provisions a dedicated per-user loopback `sshd`,
auto-manages the SSH key, pins the host key, and hosts an **owner-only** Dev
Tunnel — so there is **no OpenSSH Server, no `sshd_config`, no `authorized_keys`
ACL, and no local service account** to manage by hand.

## Why dtssh (the Entra-`sshd` wall)

Windows `sshd` cannot reliably resolve Entra-joined-only cloud accounts (SIDs like
`S-1-12-1-*`); a manual OpenSSH host fails with `ga_init unable to resolve user`.
dtssh sidesteps this entirely by running **its own** loopback listener as *you*,
the interactive user — so you land as the real Entra identity and can attach your
user-side psmux / editor / agent sessions, which a service account never could.

> **Interactive, user-side — not headless.** Those sessions live only inside your
> interactive logon and carry *your* credentials, so the host runs **as you, at
> logon**. Intended flow: **log in once → confirm the host is up → disconnect RDP
> (the session and listener persist) → drive over `ssh dt-<host>` from anywhere.**
> True headless (cold boot, nobody logged in) is out of scope by design.

## 1. Authenticate

Launch WAM in its own window — inline login often falls back to device-code flow,
which corporate Conditional Access blocks.

```powershell
Start-Process devtunnel -ArgumentList "user","login","--entra"
# confirm before continuing:
devtunnel user show
```

`dtssh login` works too. dtssh auto-downloads the `devtunnel` CLI on first use.

## 2. Install dtssh

```powershell
irm https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.ps1 | iex
```

Single self-contained binary to `%LOCALAPPDATA%/dtssh/bin`.

### AV / Defender caveat

Scripts that download and install networking binaries are sometimes flagged as
trojan-like. Prefer the upstream signed `install-release.ps1` above over ad-hoc
`Invoke-WebRequest`-of-an-exe patterns.

## 3. Host your session

```powershell
dtssh host --persist
```

This provisions the key, pins the host key, and hosts a dedicated loopback `sshd`
(`:2222`) reachable **only** through an **owner-only** Dev Tunnel — private to your
Entra identity by construction (no `--allow-anonymous`, no `--tenant`/`--repo`
grant). The SSH key is a second factor; the identity gate is the tunnel owner.

## 4. Persist across logon

The session is user-side, so host it from a **logon Startup launcher** (not a
SYSTEM/scheduled task — the sessions need your interactive token, and non-elevated
task creation is often blocked on managed Dev Boxes). Point a hidden Startup-folder
shortcut at a small launcher that runs `dtssh host --persist` at logon; it then
fires whenever you RDP/console-log-on and persists after you disconnect.

A reference implementation (launcher + installer) ships **in-box with the
`agent-ssh` plugin** as its `dtssh` transport — `transports/dtssh/scripts/install-host.ps1`
(idempotent `install` / `update` / `status` / `stop` / `uninstall`) drives a
self-healing `transports/dtssh/scripts/dtssh-host-launcher.ps1` from a hidden
Startup-folder shortcut.

## 5. Validate

```powershell
# host is up and hosting:
Get-Process dtssh
# a client on the SAME Entra account then runs `dtssh discover` + `ssh dt-<host>`
```

From a client, configure `setting-up-ssh-client` and run `ssh dt-<host>` — you
should land as the real Entra user.

## Gotchas

- **WinGet `Links\*.exe` shims don't run over SSH.** App-Execution-Alias reparse
  points (e.g. `…\WinGet\Links\psmux.exe`) fail over a non-interactive SSH logon
  (*"cannot execute the specified program"*). Invoke the real exe under
  `…\WinGet\Packages\…` or wrap in `pwsh -Command`.
- **Same account both sides.** Client and host must be signed in to dtssh with the
  **same** Entra identity, or the owner-only tunnel won't admit the client.
- **Domain-joined hosts.** A genuinely domain/hybrid-joined Windows host (on-prem
  AD SID `S-1-5-21-*`) *can* run real-identity OpenSSH directly, but dtssh is the
  portable path that works on Entra-joined-only Dev Boxes too.
