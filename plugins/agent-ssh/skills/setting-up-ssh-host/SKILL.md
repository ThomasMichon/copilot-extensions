---
name: setting-up-ssh-host
description: Set up inbound SSH on a Windows host through Microsoft Dev Tunnels as the real interactive user, using dtssh; and reach a WSL distro (or any behind-the-host loopback listener) as its own SSH target via ProxyJump through that dtssh host. Use when asked to "set up an SSH host", "set up a dtssh host", "host SSH through Dev Tunnel", "configure inbound SSH", "host interactive sessions over SSH", "reach WSL over SSH", "make WSL its own SSH target", "SSH into WSL through dtssh", or "fix ga_init unable to resolve user".
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

## Reaching WSL (or another behind-the-host listener) as its own SSH target

To reach a **WSL** distro — or any service that only listens on the host's
loopback — as its *own* first-class SSH alias, **do not** stand up a second dtssh
host, and **do not** run dtssh *inside* WSL. Instead **ProxyJump through the
machine's existing dtssh host** to the loopback listener:

```
# ~/.ssh/config (or a config.d/ fragment)
Host dt-<host>-wsl
    HostName localhost          # resolved on the jump host = the Windows box
    Port 2200                   # the WSL sshd's dedicated loopback port
    User <linux-user>
    ProxyJump dt-<host>         # the machine's existing dtssh host
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
```

`ssh dt-<host>-wsl` then lands inside WSL as the Linux user. Why this is the
**preferred** manner:

- **Zero extra tunnels.** It rides the machine's existing `dt-<host>` Dev Tunnel —
  no second tunnel to create, renew, or ACL, and the mesh stays at one tunnel per
  machine no matter how many WSL targets exist. (dtssh *inside* WSL would add a
  tunnel per machine.)
- **No WSL egress and no WSL devtunnel login required.** The tunnel is hosted on
  the Windows box (which has egress and is already signed in); WSL is reached over
  local loopback. This matters because dtssh-*in*-WSL is unworkable on two common
  walls: WSL with **no outbound egress** can't host a tunnel at all, and WSL has
  **no OS keyring** so an in-WSL `devtunnel` login can't persist its token (the
  host loops on "Login required"; the device-code fallback is separately
  Conditional-Access-blocked).
- **Works for any loopback listener** behind the host, not just WSL.

> **Note on `HostName localhost` + `ProxyJump`.** `localhost:2200` is resolved on
> the **jump host** (the Windows box), where WSL `localhostForwarding` maps it to
> the WSL sshd. The `-W` forward the jump performs requires the dtssh host's sshd
> to permit TCP forwarding (it does by default).

### When the ProxyJump→localhost path is blocked: the `wsl` transport (interop)

The ProxyJump wiring above depends on Windows→WSL **TCP** working. On a machine
behind a **corp network filter** — notably the **Global Secure Access** (Entra)
client — that path is dead in **both** WSL networking modes: under `mirrored`,
`localhostForwarding` is a documented no-op and the host→WSL loopback relay never
opens; switching to `nat` is worse (GSA leaves the WSL vNIC with no IP and no
egress). sshd is healthy *inside* WSL, but nothing reaches it from Windows.

The fix is the in-box **`wsl` transport**: bridge the SSH last hop through the
**`wsl.exe` interop** channel (a stdio pipe GSA does **not** filter) instead of a
TCP connection. A process launched via `wsl.exe` reaches WSL's own loopback fine,
so pipe SSH through it and let `nc` inside WSL make the local connection:

```
Host <host>-wsl
    User <linux-user>
    ProxyCommand wsl.exe -d <distro> -u <linux-user> exec nc 127.0.0.1 2200
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
```

Generate this with the transport rather than hand-writing it:

```
python transports/wsl/deploy/emit-registry.py --machines machines.yaml --out wsl-registry.yaml
python -m agent_ssh emit-profile wsl-registry.yaml --module transports/wsl/module.yaml
```

`emit-registry` auto-detects the local machine's `ssh.environments` `wsl` entry
and emits a one-record registry; the core renders `50-agent-ssh-wsl.conf`. This is
a **local** transport — `wsl.exe` runs on the same box, so it reaches only that
machine's WSL, and only while an **interactive Windows session** is up (the
keepalive pins WSL up). WSL needs `nc` (`netcat-openbsd`) installed. No inbound
port, no WSL egress, no ProxyJump. (See `transports/wsl/module.yaml`.)

### Provisioning WSL itself — bring your own, or use `wsl-setup`

This ProxyJump wiring only needs an sshd **listening on a known loopback port
with your client key authorized** — it is agnostic to *how* WSL got there.
Provision WSL however you prefer; the **`wsl-setup`** plugin's `setting-up-wsl`
skill is one turn-key path (distro + NAT networking + a dedicated-port sshd + a
windowless keepalive). Whatever mechanism you bring, the target must meet three
requirements:

- **sshd on a dedicated loopback port (e.g. 2200), not `:22`** — a Windows sshd
  commonly binds `:22` and shadows the WSL `localhostForwarding` relay,
  intermittently breaking the target.
- **A keepalive** so the distro doesn't idle-terminate — an idle WSL distro stops
  in ~2 minutes and silently kills the listener. Use `wsl-setup`'s keepalive or
  your own.
- **Your client public key in the WSL user's `authorized_keys`** (the tunnel owner
  is the identity gate; the SSH key is the second factor, as everywhere in dtssh).

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
