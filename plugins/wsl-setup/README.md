# wsl-setup

A payload-only Copilot CLI plugin that teaches an agent to set up and
troubleshoot **WSL2** as a first-class development and **service-hosting**
environment — not just a shell, but a place that can reliably host a listener
(e.g. an SSH server) reachable from Windows and, through a tunnel, from the mesh.

## Why this exists

Standing up a service inside WSL2 on a locked-down corporate Windows box surfaces
three failure classes that aren't obvious and waste hours each:

1. **Egress is silently blocked.** DNS resolves but TCP times out (`apt` fails
   with "No route to host") because a corporate host-vNIC network filter passes
   only recognized host-process traffic, not the WSL virtual adapter — while the
   Windows host itself has full connectivity.
2. **Host↔WSL loopback is broken in mirrored networking.** With
   `networkingMode=mirrored`, `127.0.0.1` inside WSL is redirected through a
   special loopback device to the host relay, so a connection to a WSL-local
   listener never reaches it. Switching to `networkingMode=nat` +
   `localhostForwarding=true` restores `Windows localhost:PORT -> WSL:PORT`.
3. **The distro doesn't stay up.** An idle distro terminates, taking your
   service (and any tunnel's local hop) with it — you need a keepalive.

This plugin encodes the diagnosis-and-fix for all three, plus the base
install/networking choices, so the next agent doesn't re-derive them.

## Skills

| Skill | Use when |
|-------|----------|
| **setting-up-wsl** | Install WSL + a distro, pick the networking mode, install base tooling, and make a WSL-hosted service reachable + persistent. |
| **troubleshooting-wsl-networking** | Egress blocked, `apt` "No route to host", host→WSL `localhost:PORT` times out/refused, or a WSL service disappears when idle. |

## Composition

This plugin covers **environment setup**. For cloning a **repo** into WSL and
wiring Windows Terminal profiles, use `agent-worktrees`'
`agent-worktrees-wsl-provision` skill — the two compose: provision the repo with
agent-worktrees, ready the environment with wsl-setup.

To expose a WSL-hosted SSH server through a Dev Tunnel in an Entra-compliant way,
pair this with the **`devtunnel-ssh`** plugin (example-marketplace marketplace): wsl-setup
readies the WSL environment and the `localhost:PORT` hop; devtunnel-ssh sets up
sshd + the tunnel and targets WSL as the host.

## Shipped keepalive helper

Beyond skills, this plugin ships a **`wsl-keepalive` helper**
(`skills/setting-up-wsl/references/wsl-keepalive.ps1` + `.service.yaml`): a
logon-triggered, **windowless** Scheduled Task that pins a distro up (and
optionally starts a systemd service) so a WSL-hosted listener survives
idle-termination and reboot. It's a per-machine, out-of-band helper (not a
reconciler-managed runtime), so it lives as a skill reference. Install it from an
elevated shell:

```powershell
pwsh -File plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1 install -Distro Ubuntu-22.04 -Service ssh -TaskName WSL-SSH-Keepalive
```

The skills teach the concepts; the helper makes the keepalive reproducible.

