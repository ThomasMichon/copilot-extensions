---
name: setting-up-wsl
description: >
  Set up WSL2 as a development and service-hosting environment on Windows -
  install WSL + a distro, choose the networking mode (NAT + localhostForwarding
  vs mirrored + dnsTunneling), install base tooling, and make a WSL-hosted
  service (e.g. sshd) reachable from Windows and persistent across idle/reboot.
  Use when setting up WSL, hosting a service in WSL, exposing a WSL port to
  Windows, or preparing WSL to be an SSH host. Trigger phrases include:
  - 'set up WSL'
  - 'install WSL'
  - 'host a service in WSL'
  - 'expose a WSL port to Windows'
  - 'WSL localhost forwarding'
  - 'make WSL reachable'
  - 'WSL sshd'
  - 'keep WSL running'
---

# Setting up WSL2 (dev + service host)

Provision WSL2 so it can run a reachable, persistent service — not just an
interactive shell. This is the **environment** setup. To clone a *repo* into WSL
and wire Windows Terminal profiles, use `agent-worktrees`'
`agent-worktrees-wsl-provision` skill (they compose). To expose a WSL-hosted SSH
server through a Dev Tunnel, pair with the **`devtunnel-ssh`** plugin.

## 1. Install WSL + a distro

```powershell
wsl --status                 # already installed?
wsl --list --online          # available distros
wsl --install -d Ubuntu-22.04 # install (reboot may be required)
wsl -l -v                    # verify: STATE + VERSION 2
```

Prefer **WSL2** (`VERSION 2`). Confirm the default distro and the Linux user
(`wsl -d <distro> -- bash -lc 'whoami; id -u'`) — a normal Linux user (uid 1000)
is a real local identity with none of the Windows/Entra sshd-auth limitations.

## 2. Choose the networking mode — this is the pivotal decision

WSL2 networking mode is set in `%USERPROFILE%\.wslconfig` under `[wsl2]` and
applies only after `wsl --shutdown`.

| Mode | Reach a WSL service from Windows | Corp VPN DNS | When to use |
|------|----------------------------------|--------------|-------------|
| **`nat`** (default) + `localhostForwarding=true` | `Windows localhost:PORT -> WSL:PORT` via the host relay (robust) | needs `dnsTunneling` | **Hosting a service** that Windows / a tunnel must reach. |
| **`mirrored`** + `dnsTunneling=true` | Shares the host IP; **host↔WSL loopback frequently breaks** behind corp host-vNIC filters | best VPN behavior | Outbound-heavy dev on VPN where you don't need inbound to WSL. |

**If you need a reachable WSL-hosted service, use NAT + localhostForwarding.**
`mirrored` redirects WSL's `127.0.0.1` through a loopback relay that corporate
network filters silently drop — see `troubleshooting-wsl-networking`.

```ini
# %USERPROFILE%\.wslconfig  -- reachable-service config
[wsl2]
networkingMode=nat
localhostForwarding=true
dnsTunneling=true          # keep for corp VPN DNS; compatible with NAT
```

```powershell
wsl --shutdown             # required for .wslconfig to take effect
# NOTE: this stops ALL distros incl. Docker Desktop's backend (it auto-recovers).
```

Verify after reboot: `wsl -d <distro> -- bash -c "ip -4 -o addr show eth0 | grep -oP 'inet \K[0-9.]+'"`
shows a NAT IP (172.x); `Test-NetConnection localhost -Port <PORT>` from Windows
succeeds once the service is listening.

## 3. Enable systemd (for real services)

```powershell
wsl -d <distro> -u root bash -c "printf '[boot]\nsystemd=true\n' >> /etc/wsl.conf"
wsl --shutdown
```
With systemd, install a service and `systemctl enable <svc>` so it starts on
distro boot.

## 4. Install base tooling

WSL2 on a corp box often has **no internet egress** (see
`troubleshooting-wsl-networking`). Test first:

```powershell
wsl -d <distro> -u root bash -c "curl -m8 -sSI https://archive.ubuntu.com >/dev/null && echo OK || echo NO-EGRESS"
```

- **Egress OK** → `apt-get update && apt-get install -y <pkgs>` normally.
- **No egress** → **sideload** `.deb`s downloaded on Windows (which has
  connectivity) and `dpkg -i` them — see `troubleshooting-wsl-networking` §
  "Offline package install". Match the distro's exact release build (e.g. jammy
  `8.9p1-3ubuntu0.NN`), not the newest pool version.

## 5. Make a WSL-hosted service reachable

With NAT + `localhostForwarding`, a service listening on `0.0.0.0:PORT` inside
WSL is reachable at `Windows localhost:PORT`. Confirm end-to-end (example: sshd):

```powershell
Test-NetConnection localhost -Port 22        # TcpTestSucceeded = True
# then the app-level handshake, e.g. ssh -p 22 <user>@localhost 'id -un'
```

You do **not** need a Windows firewall / Hyper-V inbound rule for the
`localhostForwarding` relay path (it's a host-loopback → WSL relay). Only add
inbound rules if exposing WSL directly on an external interface (usually
unnecessary — front it with a tunnel instead).

## 6. Keep the distro alive (critical for hosted services)

An **idle WSL distro terminates**, killing your service (and any tunnel's local
hop). This plugin ships a **`wsl-keepalive` helper**
(`references/wsl-keepalive.ps1`) that pins the distro up (and optionally
`systemctl start`s a service) via a **windowless** VBS launcher on a
logon-triggered Scheduled Task — run it from an **elevated** shell:

```powershell
# Scheduled Task registration needs elevation. Path is relative to the copilot-extensions repo root.
$ka = 'plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1'
pwsh -File $ka install -Distro <distro> -Service <svc> -TaskName WSL-Keepalive-<svc>
pwsh -File $ka status  -TaskName WSL-Keepalive-<svc> -Distro <distro> -Service <svc>
```

**Why not run `wsl.exe` from the task directly?** A Scheduled Task that executes
`wsl.exe` pops a **visible console window** on every fire (the task's `-Hidden`
flag hides the task, not the child console). The installer routes through a VBS
launcher (`WScript.Shell.Run ..., 0`) so it is truly windowless. The `sleep
infinity` process holds the distro up; systemd keeps `<svc>` running; the logon
trigger re-establishes it after each reboot.

> Doing it by hand (no plugin checkout): deploy a one-line VBS —
> `CreateObject("WScript.Shell").Run "wsl.exe -d <distro> -u root --exec /bin/sh -c ""systemctl start <svc>; exec sleep infinity""", 0, False`
> — and register a logon Scheduled Task whose action is `wscript.exe "<that.vbs>"`.
> Never register a task that executes `wsl.exe` directly (visible window).

## Edge cases

- **`.wslconfig` change didn't apply** — you must `wsl --shutdown` (bounces all
  distros incl. Docker Desktop; it auto-recovers). Confirm the intended mode
  actually took: check the WSL IP (NAT = 172.x; mirrored = host IP).
- **Docker Desktop present** — it keeps the *WSL VM* up but not *your* distro;
  you still need the keepalive for your distro.
- **Multiple distros** — target one explicitly with `-d <distro>` everywhere and
  in the keepalive task.
- **Service reachable locally but not through a tunnel** — the tunnel host runs
  on Windows and forwards to `localhost:PORT`; that hop needs NAT
  `localhostForwarding` working (step 2) and the distro up (step 6).
