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
| `ssh <alias>` closes pre-banner (`Connection closed by UNKNOWN port 65535`) from **every** client *and* from the host itself, while `dtssh host` is running, `:2222` is listening, and the tunnel shows host connections > 0 | **sshd pre-auth WEDGE** — half-open/idle pre-auth connections piled up past OpenSSH's `MaxStartups` (default 10:30:100), so sshd accepts TCP but drops new handshakes before the banner. dtssh's generated `sshd_config` sets no `MaxStartups`/`LoginGraceTime`. Confirm with the banner test below and a high Established count on :2222. | Restart the host to reap the pileup: `install-host.ps1 stop; install-host.ps1 start` (or stop the `dtssh host` PID + the launcher pwsh, then relaunch). The self-healing launcher (agent-ssh ≥ 0.1.0-dev22) now detects this via a banner probe and auto-restarts within ~`HealthCheckSec × ConsecutiveFailures`. |
| `Permission denied` / not admitted | Client and host are on **different** Entra identities (owner-only tunnel) | Sign both into dtssh with the **same** account (`dtssh login`); verify with `devtunnel user show`. |
| `ga_init unable to resolve user` in OpenSSH logs | You're hitting a **manual** Windows `sshd` that can't resolve an Entra-only cloud account (`S-1-12-1-*`) | This is exactly what dtssh avoids — use `dtssh host` (its own loopback listener runs as the real user), not a hand-rolled OpenSSH host. |
| Lands but `psmux.exe` "cannot execute" over SSH | WinGet `Links\*.exe` App-Execution-Alias shims don't run over non-interactive SSH | Invoke the real exe under `…\WinGet\Packages\…` or wrap in `pwsh -Command`. |
| Nested-mux confusion driving `psmux` remotely | `PSMUX_SESSION` / `TMUX` inherited from your own session | Clear `PSMUX_SESSION` / `TMUX` before driving `psmux` on the remote host. |

---

## Is the sshd actually *serving*? (banner, not just a listener)

A bare `Test-NetConnection -Port 2222` (or `Get-Process dtssh`) is **not enough**:
both a dead sshd child (#576) and a pre-auth **wedge** (MaxStartups saturated)
can leave the port apparently up while remote reach is broken. The listener
either isn't there, or accepts TCP but never sends its SSH banner. Read the
banner to tell "serving" from "wedged":

```powershell
# Serving  -> prints "SSH-2.0-OpenSSH_for_Windows_..."; wedged -> times out / empty.
try {
  $c = [Net.Sockets.TcpClient]::new(); $c.Connect('127.0.0.1', 2222)
  $s = $c.GetStream(); $s.ReadTimeout = 4000
  $b = [byte[]]::new(64); Start-Sleep -Milliseconds 300
  "BANNER: " + [Text.Encoding]::ASCII.GetString($b, 0, $s.Read($b, 0, 64)); $c.Close()
} catch { "NOT SERVING: $_" }

# Wedge tell-tale: a large pile of Established pre-auth connections on :2222
(Get-NetTCPConnection -LocalPort 2222 -State Established -ErrorAction SilentlyContinue).Count
```

If it reads NOT SERVING (or a high Established count), restart the host —
`install-host.ps1 stop; install-host.ps1 start` — which reaps the pileup.
`install-host.ps1 status` now performs this banner check for you. The launcher
also **preemptively reaps** the pileup (restarts) once the Established count on
:2222 crosses a pathological threshold, and the true fix is upstream — dtssh now
emits `MaxStartups`/`LoginGraceTime`/`ClientAlive*` in its generated
`sshd_config` ([bmiddha/devtunnel-ssh#13](https://github.com/bmiddha/devtunnel-ssh/pull/13)),
so an updated dtssh binary can't accumulate the pileup in the first place.

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
Test-NetConnection -ComputerName localhost -Port 2222  # loopback sshd listening (TCP only — see banner check above)
# confirm the Startup-folder launcher shortcut exists and points at the dtssh host launcher
```

> `Test-NetConnection` only proves the port *accepts TCP* — it stays green on a
> wedged sshd. Use the **banner check** above to prove sshd is actually serving.

If the host process isn't running, log on interactively (its launcher is
logon-triggered), or start it manually with `dtssh host --persist`. There is no
`sshd` service or `authorized_keys` file to check — dtssh owns the listener, the
key, and the tunnel lifecycle. Note dtssh **regenerates** its
`%LOCALAPPDATA%\dtssh\host\sshd_config` on every host start with no
`MaxStartups`/`LoginGraceTime` override, so hand-edits to that file do not
persist — the durable mitigation for the pre-auth wedge is the launcher's
banner-probe auto-restart (agent-ssh ≥ 0.1.0-dev22).
