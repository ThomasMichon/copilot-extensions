# Transport-provider contract

How a transport plugs into agent-ssh. A transport ships a `module.yaml`
conforming to `contract/module.schema.json`; the agent-ssh core consumes it and
the transport never re-implements profile rendering, coexistence, or
verification.

**Two homes, one contract.** A transport lives in-box or external on a single
axis — **does it carry non-public provider/multi-machine system config?**

- **In-box** (`transports/<module>/`, this plugin): self-contained transports with
  no non-public config — `direct` (plain SSH), `dtssh` (real-user reach over
  the public Microsoft Dev Tunnels service; the operator's identity and live
  tunnel ids are injected at deploy time, not baked in), and `wsl` (local
  Windows-to-WSL reach through the `wsl.exe` interop stdio pipe).
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

The JSON schemas in `contract/` are the published contract for authors and
callers. The current CLI is intentionally thin: `agent-ssh emit-profile` requires
only that the module file contain a string `module` name, then consumes the
fields it knows (`proxy_command`, `proxy_binary_default`, registry `machines`,
`gate`, `options`, and so on). Schema validation is a caller/test responsibility
today, not an extra runtime pass inside the emitter.

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
- `jumpbox` -- when the registry sets `topology: jumpbox` and a top-level `gate`,
  the core emits the gate host with the transport recipe; machines that set
  `via: jumpbox` get `ProxyJump <gate.name>`.

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

`entrypoints` in `module.yaml` are metadata for installers/orchestrators. The
core `emit-profile` and `verify` commands do not call transport `install-client`
or `provision-server` scripts.

## Failure behavior

- `emit-profile` exits `2` when the module file lacks a string `module` name.
  File/permission errors and invalid jumpbox records fail loud instead of
  producing a partial profile.
- `verify` exits `2` when no host names are supplied, exits `1` if any probed
  alias is unreachable, and prints one `[OK]` / `[FAIL]` line per alias.

## `install-client` on Windows — App Execution Alias shims break over SSH

A transport whose `install-client` installs a helper CLI on **Windows** should put
a **real standalone executable on PATH** rather than relying on a WinGet *App
Execution Alias* shim (`%LOCALAPPDATA%\Microsoft\WinGet\Links\<tool>.exe`). Those
shims are reparse points that Windows refuses to execute over a **non-interactive
SSH logon** (`the path cannot be traversed because it contains an untrusted mount
point`). Because agent-ssh exists to drive machines *over SSH* — including a
control plane re-running `install-client` / `emit-profile` / discovery on a remote
box (e.g. after a tunnel rotation) — a shim-only helper breaks exactly the
over-SSH path the transport is meant to enable. The core does not enforce this;
it is a provider/installer responsibility and a troubleshooting checkpoint.

Preferred shape: `install-client` (windows entrypoint) installs the helper as a plain
binary into a transport-owned dir that it prepends to the **User PATH ahead of**
`WinGet\Links` (direct-download the vendor exe rather than `winget install …`, or
copy the real exe out of `WinGet\Packages\…`). Prefer this even when a winget
package exists, so the tool resolves shim-free both interactively and over SSH.

> **Current in-box `dtssh` shape.** The dtssh transport installs dtssh itself
> under `%LOCALAPPDATA%\dtssh\bin` via the upstream dtssh installer and puts that
> real binary on the User PATH. Its Windows scripts resolve `devtunnel.exe` from
> a sibling copy when present, otherwise from PATH; if no `devtunnel` is found,
> `install-client.ps1` falls back to `winget install Microsoft.devtunnel`. Because
> that fallback can land a WinGet Links shim, the troubleshooting guidance still
> treats shim-only helper binaries as a known over-SSH failure mode. Reference:
> `transports/dtssh/scripts/install-client.ps1`,
> `transports/dtssh/scripts/install-host.ps1`.
