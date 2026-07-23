# Transport-provider contract

How a transport plugs into agent-ssh. A transport is its own plugin (in its
audience's marketplace) that ships a `module.yaml` conforming to
`contract/module.schema.json`. The agent-ssh core consumes it; the transport
never re-implements profile rendering, coexistence, or verification.

## Division of labor

- **Core owns the mechanism:** `Host <name>` block rendering, deterministic
  option ordering, the `~/.ssh/config.d` managed-`Include` coexistence layout,
  atomic per-transport fragment writes, and the reachability probe.
- **Transport owns the recipe:** a single `proxy_command` template describing
  how to dial a host (or nothing, for plain SSH), a `proxy_binary_default`, an
  optional `install-client` script, and its own config schema extensions.

## The `proxy_command` template

Placeholders filled by the core per host:

| Placeholder | Value |
|---|---|
| `{hostname}` | the machine's transport-resolved hostname (`%h` for a jumpbox gate) |
| `{proxy_binary}` | registry `proxy_command_binary` override, else `proxy_binary_default` |
| `{name}` `{user}` `{port}` | the machine's registry fields |

Examples:
- Cloudflare: `"{proxy_binary} access ssh --hostname {hostname}"`
- (a dev-tunnel transport supplies its own equivalent)
- `direct`: omit `proxy_command` entirely -> plain SSH.

## Topology

- `per-machine` (default) -- each host dials its own hostname via the recipe.
- `jumpbox` -- one `gate` host carries the recipe; other hosts `ProxyJump` it.

## Coexistence rules (binding)

1. Write **only** `~/.ssh/config.d/50-agent-ssh-<module>.conf`.
2. Add **only** the single managed `Include ~/.ssh/config.d/*` line to
   `~/.ssh/config`; never rewrite existing content.
3. A machine belongs to exactly one transport's fragment (its `transport:` key).
4. Never read, write, or assume the layout of a peer transport's fragment.

## Verbs the core satisfies vs. the transport owns

| Verb | Owner |
|---|---|
| `emit-profile` | core (`agent-ssh emit-profile`, `core/ssh_profile.py` compatibility wrapper) |
| `verify` | core (`agent-ssh verify`, `scripts/verify.*`) |
| `install-client` | transport (if it needs a client binary) |
| `provision-server` | transport (optional; may be operator-manual) |

## `install-client` on Windows — no App Execution Alias shims (binding)

A transport whose `install-client` installs a helper CLI on **Windows** MUST put a
**real standalone executable on PATH** — it must NOT rely on a WinGet *App
Execution Alias* shim (`%LOCALAPPDATA%\Microsoft\WinGet\Links\<tool>.exe`). Those
shims are reparse points that Windows refuses to execute over a **non-interactive
SSH logon** (`the path cannot be traversed because it contains an untrusted mount
point`). Because agent-ssh exists to drive machines *over SSH* — including a
control plane re-running `install-client` / `emit-profile` / discovery on a remote
box (e.g. after a tunnel rotation) — a shim-only helper breaks exactly the
over-SSH path the transport is meant to enable.

Requirement: `install-client` (windows entrypoint) installs the helper as a plain
binary into a transport-owned dir that it prepends to the **User PATH ahead of**
`WinGet\Links` (direct-download the vendor exe rather than `winget install …`, or
copy the real exe out of `WinGet\Packages\…`). Prefer this even when a winget
package exists, so the tool resolves shim-free both interactively and over SSH.

> **Worked example — the `dtssh` transport.** dtssh shells out to `devtunnel`.
> `winget install Microsoft.devtunnel` lands only the Links shim, so `dtssh
> discover` fails when run over SSH. The dtssh `install-client` must instead drop
> the standalone `devtunnel.exe` (`aka.ms/TunnelsCliDownload/win-x64`) into its
> bin dir on PATH ahead of the shim. Reference implementation: the dotfiles
> `services/dtssh-host/install.ps1` `Install-Devtunnel` step (the pre-graduation
> home of this transport). The same caution applies to any Windows helper a
> transport installs (e.g. `cloudflared` for the Cloudflare transport).
