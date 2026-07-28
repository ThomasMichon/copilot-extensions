---
name: sharing-ssh-keys
description: How SSH keys are handled for dtssh Dev Tunnel SSH, and the private-key hygiene rules. Use when asked to "share my SSH key", "add my key to a host", "where should SSH public keys live", or "distribute SSH keys".
---

# SSH Keys for dtssh

**With [dtssh](https://github.com/bmiddha/devtunnel-ssh) you do not share keys by
hand.** On first `dtssh host` / `dtssh discover`, dtssh **provisions the SSH key
and pins the host key** for you, scoped to your own Entra identity. There is no
per-machine keypair to generate, no public key to copy to a host, and no
`authorized_keys` file or ACL to manage — the owner-only Dev Tunnel is the
identity gate, and dtssh owns the key material.

So for the interactive `ssh dt-<host>` path, the "sharing" step is simply:

```powershell
dtssh login       # same Entra account on host and client
dtssh discover    # client picks up the pinned host key automatically
```

## Private-key hygiene (always)

These rules hold regardless of tooling:

- **Never copy a private key** anywhere — not to cloud storage, chat, email, repos, or
  a host. Only ever distribute a `.pub`.
- Treat any authorized-key list as **access-control data**: remove stale machines
  and unknown entries.
- If a private key may have been exposed, **rotate it** — remove the old public
  key everywhere it was trusted and generate a fresh key. With dtssh, re-run
  `dtssh login` / `dtssh host` to re-provision.

> A machine that hosts its own non-dtssh listener (e.g. a genuinely domain-joined
> Windows host, or a Linux/WSL `sshd` owned by a real user) manages its own
> `authorized_keys` per that platform's convention — outside dtssh's scope and
> outside this skill.
