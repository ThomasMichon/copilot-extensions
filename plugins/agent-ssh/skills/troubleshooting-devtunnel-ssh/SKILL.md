---
name: troubleshooting-devtunnel-ssh
description: Diagnose dtssh SSH over Microsoft Dev Tunnels (real-user interactive reach). Use when asked to "troubleshoot Dev Tunnel SSH", "dtssh discover fails", "ssh dt-<host> fails", "SSH tunnel expired", "device-code login blocked", "WAM login failed", "host not listening", or "ga_init unable to resolve user".
---

# Troubleshooting dtssh (Dev Tunnel SSH)

Reach is via **[dtssh](https://github.com/bmiddha/devtunnel-ssh)**
(`ssh dt-<host>`, real Entra user). Diagnose from outside in: **client auth →
same-account → host listener → session state**.

## Quick checks

```powershell
dtssh login          # client + host must be the SAME Entra account
devtunnel user show  # confirm the authenticated identity
dtssh discover       # (re)wire `ssh dt-<host>`
ssh -v dt-<host>     # verbose connect
```

On the host:

```powershell
Get-Process dtssh                                   # is `dtssh host` running?
Test-NetConnection -ComputerName localhost -Port 2222  # dedicated loopback sshd up?
```

---

## Symptom → cause → action

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `devtunnel user show` / `dtssh login` says not logged in | Dev Tunnel/dtssh auth expired | Run `dtssh login` (or `Start-Process devtunnel -ArgumentList "user","login","--entra"`); finish WAM in the new window. |
| Login shows device-code flow or fails under Conditional Access | Login ran inline/headless and WAM could not open | Cancel the device-code flow; re-run `dtssh login` from an interactive desktop session. |
| `ssh dt-<host>` unknown / not in SSH config | Client never ran `dtssh discover`, or the host isn't hosting | Run `dtssh discover`; on the host confirm `dtssh host --persist` (Startup launcher) is running. |
| Connects to the tunnel but no host answers | Host has 0 connections — the interactive session isn't up (sessions are user-side) | Log on to the host once (RDP/console) so its Startup launcher starts `dtssh host`, then disconnect — the listener persists. |
| `Permission denied` / not admitted | Client and host are on **different** Entra identities (owner-only tunnel) | Sign both into dtssh with the **same** account (`dtssh login`); verify with `devtunnel user show`. |
| `ga_init unable to resolve user` in OpenSSH logs | You're hitting a **manual** Windows `sshd` that can't resolve an Entra-only cloud account (`S-1-12-1-*`) | This is exactly what dtssh avoids — use `dtssh host` (its own loopback listener runs as the real user), not a hand-rolled OpenSSH host. |
| Lands but `psmux.exe` "cannot execute" over SSH | WinGet `Links\*.exe` App-Execution-Alias shims don't run over non-interactive SSH | Invoke the real exe under `…\WinGet\Packages\…` or wrap in `pwsh -Command`. |
| Nested-mux confusion driving `psmux` remotely | `PSMUX_SESSION` / `TMUX` inherited from your own session | Clear `PSMUX_SESSION` / `TMUX` before driving `psmux` on the remote host. |

---

## Recycle a stale local `devtunnel connect` safely

dtssh manages its own connect process, but if a stale one wedges the local
forward, identify only the tunnel's `devtunnel connect` process and stop it by
**PID** (never by name):

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'devtunnel.exe'" |
    Where-Object { $_.CommandLine -match 'connect' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Then retry `ssh dt-<host>` (dtssh restarts the connect).

---

## Host health checklist

```powershell
Get-Process dtssh                                      # host process present
Test-NetConnection -ComputerName localhost -Port 2222  # loopback sshd listening
# confirm the Startup-folder launcher shortcut exists and points at the dtssh host launcher
```

If the host process isn't running, log on interactively (its launcher is
logon-triggered), or start it manually with `dtssh host --persist`. There is no
`sshd` service, `sshd_config`, `authorized_keys` file, or renewal scheduled task
to check — dtssh owns the listener, the key, and the tunnel lifecycle.
