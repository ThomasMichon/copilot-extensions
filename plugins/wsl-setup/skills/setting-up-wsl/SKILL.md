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
interactive shell. This is the **environment** setup and works standalone: enable
the payload-only plugin and run this skill; no repo needs to be registered as an
agent-worktrees harness. To clone a *repo* into WSL and wire Windows Terminal
profiles, use `agent-worktrees:agent-worktrees-wsl-provision` skill (they
compose). To reach a WSL-hosted sshd as its **own SSH target** from other
machines, keep the boundary-crossing transport on the Windows host: either
forward the Windows `localhost:<port>` hop through your tunnel, or use the
**`agent-ssh`** plugin's `agent-ssh:setting-up-ssh-host` skill (§ "Reaching WSL … as its own
SSH target") for a ProxyJump through the host's existing dtssh host. WSL itself
does not need to run devtunnel/dtssh.

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
| **`nat`** (default) + `localhostForwarding=true` | `Windows localhost:PORT -> WSL:PORT` via the host relay (robust) | use `dnsTunneling=true` for VPN DNS | **Hosting a service** that Windows / a tunnel must reach. |
| **`mirrored`** + `dnsTunneling=true` | Officially supports localhost, but the relay can time out or hit the host side on locked-down corp host-vNIC/filter stacks; `localhostForwarding` is ignored in mirrored mode | best VPN behavior | Outbound-heavy dev on VPN where you do not need a Windows/tunnel hop into WSL. |

**If you need a reachable WSL-hosted service, use NAT + localhostForwarding.**
Mirrored is excellent for some VPN/DNS scenarios, but NAT is the predictable
service-hosting mode when Windows or a host-side tunnel must reach a WSL listener
by `localhost:<port>` — see `troubleshooting-wsl-networking`.

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

Verify after reboot:

```powershell
wsl -d <distro> hostname -I             # NAT normally shows a 172.x address
Test-NetConnection localhost -Port <PORT> # succeeds once the service is listening
```

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

### Copilot CLI in WSL — it auto-installs; don't install it

If the machine has the **Windows** Copilot CLI, running `copilot` inside WSL
invokes its **WSL stub**, which **auto-installs** the Linux binary (to
`~/.local/share/gh/copilot/copilot`) on first run. So **do not** hand-install
Copilot in the distro, and **do not** symlink it onto `PATH`.

Two consequences to know about:

- **The auto-installed binary is often not on `PATH` immediately**, and a bare
  `copilot` in WSL otherwise resolves via **interop** to the non-executable
  Windows `.exe` stub. Anything that shells out to `copilot` right after the
  first run can therefore fail to find a runnable CLI.
- **`agent-worktrees` handles this for you** (≥ 1.5.3): it *resolves* Copilot
  from known locations — `PATH` → `~/.local/bin/copilot` →
  `~/.local/share/gh/copilot/copilot` — in both its `update` flow and the
  session-start provision loop, so `<repo> update` / payload provisioning work
  **without** any PATH tinkering. It only *finds* Copilot; the Windows-side stub
  owns the install.

`~/.local/bin` is on `PATH` via the stock `~/.profile` snippet (+ `~/.local/bin/env`
from uv), so binstubs the plugins deploy there are picked up automatically.

## 5. Make a WSL-hosted service reachable

With NAT + `localhostForwarding`, a service listening on `0.0.0.0:PORT` inside
WSL is reachable at `Windows localhost:PORT`. Confirm end-to-end (example: sshd):

```powershell
Test-NetConnection localhost -Port 2200        # TcpTestSucceeded = True
# then the app-level handshake, e.g. ssh -p 2200 <user>@localhost 'id -un'
```

You do **not** need a Windows firewall / Hyper-V inbound rule for the
`localhostForwarding` relay path (it's a host-loopback → WSL relay). Only add
inbound rules if exposing WSL directly on an external interface (usually
unnecessary — front it with a tunnel instead).

> **For sshd, pick a dedicated port (e.g. 2200), not `:22`.** A Windows OpenSSH
> `sshd` commonly binds `:22`; when it does, `localhostForwarding` does **not**
> forward `Windows localhost:22` to WSL (the Windows binding wins), so the WSL
> sshd is silently shadowed. Run the WSL sshd on an unused port
> (`/etc/ssh/sshd_config.d/*.conf` → `Port 2200`) and use that everywhere.

## 6. Keep the distro alive (critical for hosted services)

An **idle WSL distro terminates**, killing your service (and any tunnel's local
hop). This plugin ships a **WSL keepalive helper** (`references/wsl-keepalive.ps1`)
with `install`, `status`, and `uninstall` actions. It pins the distro up via a
**windowless** VBS launcher on a logon-triggered Scheduled Task. With `-Service`,
the launcher runs `systemctl start <svc>` once before `exec sleep infinity`; it
does not monitor or restart the service after that. Use `systemctl enable <svc>`
and the unit's own restart policy for ongoing service supervision.

Run `install` and `uninstall` from an **elevated** shell (Scheduled Task
registration/removal needs elevation); `status` can run unelevated:

```powershell
# Scheduled Task registration needs elevation. Path is relative to the copilot-extensions repo root.
$ka = 'plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1'
pwsh -File $ka install -Distro <distro> -Service <svc> -TaskName WSL-Keepalive-<svc>
pwsh -File $ka status  -TaskName WSL-Keepalive-<svc> -Distro <distro> -Service <svc>
pwsh -File $ka uninstall -TaskName WSL-Keepalive-<svc>
```

**Why not run `wsl.exe` from the task directly?** A Scheduled Task that executes
`wsl.exe` pops a **visible console window** on every fire (the task's `-Hidden`
flag hides the task, not the child console). The installer routes through a VBS
launcher (`WScript.Shell.Run ..., 0`) so it is truly windowless. The `sleep
infinity` process holds the distro up; the logon trigger re-establishes it after
each reboot.

> Doing it by hand (no plugin checkout): deploy a one-line VBS —
> `CreateObject("WScript.Shell").Run "wsl.exe -d <distro> -u root --exec /bin/sh -c ""systemctl start <svc>; exec sleep infinity""", 0, False`
> — and register a logon Scheduled Task whose action is `wscript.exe "<that.vbs>"`.
> Never register a task that executes `wsl.exe` directly (visible window).

## 7. Expose WSL as its own SSH target (preferred: ProxyJump via agent-ssh)

Once WSL runs sshd on a dedicated port (step 5) and stays up (step 6), reach it
as its **own** SSH alias (`ssh <host>-wsl`, landing as the Linux user) by
forwarding the Windows `localhost:<port>` hop through the host's authenticated
transport. The preferred managed wiring lives in the **`agent-ssh`** plugin's
`agent-ssh:setting-up-ssh-host` skill, § "Reaching WSL … as its own SSH target": ProxyJump
through the host's existing dtssh host to `localhost:<port>`. A host-side Dev
Tunnel/SSH forward can use the same hop. Do not run dtssh/devtunnel inside WSL:
WSL does not need egress for this, and the tunnel credential/keyring belongs on
Windows.

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
