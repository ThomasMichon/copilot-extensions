# wsl-setup

A payload-only Copilot CLI plugin that teaches an agent to set up and
troubleshoot **WSL2** as a first-class development and **service-hosting**
environment — not just a shell, but a place that can reliably host a listener
(for example sshd) reachable from Windows and, through a host-side SSH/Dev
Tunnel hop, from elsewhere.

There is no runtime install, venv, or binstub. Enable the plugin, then use the
skills below. The bundled keepalive is a standalone reference script, not a
managed plugin service.

## Why this exists

Standing up a service inside WSL2 on a locked-down corporate Windows box surfaces
three failure classes that aren't obvious and waste hours each:

1. **Egress is silently blocked.** DNS resolves but TCP times out (`apt` fails
   with "No route to host") because a corporate host-vNIC network filter passes
   only recognized host-process traffic, not the WSL virtual adapter — while the
   Windows host itself has full connectivity.
2. **Host↔WSL loopback can fail in mirrored networking on locked-down hosts.**
   Mirrored mode is designed to support localhost between Windows and WSL, but
   on some corporate host-vNIC/filter stacks the relay path times out or reaches
   the host side instead of the WSL listener. Switching to `networkingMode=nat`
   + `localhostForwarding=true` is the known-good service-hosting path:
   `Windows localhost:PORT -> WSL:PORT`.
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

This plugin covers **environment setup** and is usable standalone: no repo needs
to be registered as an agent-worktrees harness. If you also want to clone a
**repo** into WSL and wire Windows Terminal profiles, compose it with
`agent-worktrees:agent-worktrees-wsl-provision` skill: provision the repo with
agent-worktrees, ready the environment with wsl-setup.

To reach a WSL-hosted sshd as its **own SSH target**, first make sshd listen on a
dedicated WSL port and keep the distro alive. Then either forward the Windows
`localhost:<port>` hop through your tunnel, or use the **`agent-ssh`** plugin's
`agent-ssh:setting-up-ssh-host` skill (§ "Reaching WSL … as its own SSH target") to wire
`ssh <host>-wsl` as a ProxyJump through the host's existing dtssh host. WSL does
not need to run devtunnel/dtssh itself; the boundary-crossing transport lives on
the Windows host, consistent with the repo's `service-transport` pattern.

## Shipped keepalive helper

Beyond skills, this plugin ships a **WSL keepalive helper**
(`skills/setting-up-wsl/references/wsl-keepalive.ps1` + `.service.yaml`): a
script with `install`, `status`, and `uninstall` actions. `install` writes a
windowless VBS launcher to `%LOCALAPPDATA%\wsl-keepalive\<TaskName>.vbs`,
registers an at-logon Scheduled Task that runs `wscript.exe`, starts the task
once, and records `%LOCALAPPDATA%\wsl-keepalive\deploy-manifest.json`.

With `-Service`, the launcher runs this inside the distro:

```sh
systemctl start <service>; exec sleep infinity
```

Without `-Service`, it runs only `exec sleep infinity`. That means it starts the
service once before pinning the distro; it is not a service monitor. Use systemd
unit settings for service restart policy. `install`/`uninstall` require an
elevated shell because they register/remove a Scheduled Task; `status` does not.

Install it from an elevated shell:

```powershell
pwsh -File plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1 install -Distro Ubuntu-22.04 -Service ssh -TaskName WSL-SSH-Keepalive
pwsh -File plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1 status  -TaskName WSL-SSH-Keepalive -Distro Ubuntu-22.04 -Service ssh
pwsh -File plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1 uninstall -TaskName WSL-SSH-Keepalive
```

The skills teach the concepts; the helper makes the keepalive reproducible.
