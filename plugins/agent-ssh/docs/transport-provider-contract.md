# Transport-provider contract

How a transport plugs into agent-ssh. A transport ships a `module.yaml`
conforming to `contract/module.schema.json`; the agent-ssh core consumes it and
the transport never re-implements profile rendering, coexistence, or
verification.

**Two homes, one contract.** A transport lives in-box or external on a single
axis — **does it carry non-public provider/multi-machine system config?**

- **In-box** (`transports/<module>/`, this plugin): self-contained transports with
  no non-public config — `direct` (plain SSH) and `dtssh` (real-user reach over
  the public Microsoft Dev Tunnels service; the operator's identity and live
  tunnel ids are injected at deploy time, not baked in).
- **External** (its own plugin in its audience's marketplace): transports needing
  multi-machine system/provider config or credentials — e.g. Cloudflare (Access org / SSO /
  multi-machine system hostnames). These keep their concrete values out of this public core
  and register against this same contract.

Either way, the recipe shape and the core's obligations are identical.

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
| `{distro}` | the machine's registry `distro` field (used by local-machine transports such as `wsl`; other transports ignore it) |

Examples:
- Cloudflare: `"{proxy_binary} access ssh --hostname {hostname}"`
- (a dev-tunnel transport supplies its own equivalent)
- `wsl`: `"wsl.exe -d {distro} -u {user} exec nc 127.0.0.1 {port}"` (bridges the last hop through WSL interop instead of TCP)
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

> **Worked example — the in-box `dtssh` transport.** dtssh shells out to
> `devtunnel`. `winget install Microsoft.devtunnel` lands only the Links shim, so
> `dtssh discover` fails when run over SSH. The dtssh `install-client` instead
> drops the standalone `devtunnel.exe` (`aka.ms/TunnelsCliDownload/win-x64`) into
> its bin dir on PATH ahead of the shim. Reference implementation:
> `transports/dtssh/scripts/install-client.ps1` (and `install-host.ps1` for the
> host side). The same caution applies to any Windows helper a transport installs
> (e.g. `cloudflared` for the external Cloudflare transport).
