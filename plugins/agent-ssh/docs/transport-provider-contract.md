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
  atomic per-transport fragment writes, managed-fragment hygiene, and the
  reachability probe.
- **Transport owns the recipe:** a single `proxy_command` template describing
  how to dial a host (or nothing, for plain SSH), a `proxy_binary_default`, an
  optional `install-client` script, and its own config schema extensions.

The JSON schemas in `contract/` are the published contract for authors and
callers. `agent-ssh emit-profile` validates the complete shape it consumes
(`module`, `proxy_command`, registry transport/topology, exact unique aliases,
gate, machines, and option maps) and rejects source/runtime contract drift
before publishing. Transport-specific schemas may extend the normalized record,
but must preserve these base constraints.

`emit-profile` stamps the canonical absolute registry and module paths into the
fragment. Those files are the current transport/topology authority and must
remain durable after emission. Operational commands compare the fragment with a
fresh render from those sources; a confirmed missing, changed, malformed, or
identity-mismatched source withdraws that managed alias from agent-ssh
diagnostics. Source or registry I/O uncertainty retains only an unchanged
last-known fragment in a stateful process and never activates a fresh one.

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
- provider exec: `'"{proxy_binary}" ssh-stdio "{hostname}"'` (the external provider
  hosts SSH protocol over child-process stdio and translates accepted requests
  into its own execution boundary)
- `direct`: omit `proxy_command` entirely -> plain SSH.

A `ProxyCommand` transports SSH bytes; it does not replace the SSH protocol.
Provider-exec transports must therefore present an SSH protocol endpoint on
stdio even when the target itself has no sshd. A no-listener adapter is valid:
the process may terminate with the one client connection and open no TCP or Unix
socket outside that process. The provider remains responsible for live target
lookup, readiness, posture validation, lifecycle admission, target-user
selection, command execution, and exit-status/stderr fidelity. It must not let
the SSH username select a more privileged execution identity.

If an adapter accepts only one channel, its normalized machine options must
disable OpenSSH multiplexing (`ControlMaster no`, `ControlPath none`, and
`ControlPersist no`). Authentication and host-key options must match the actual
adapter. For a local, ephemeral, stdio-only endpoint, a provider may accept
OpenSSH's initial `none` authentication probe and use an ephemeral host key, but
the emitted profile must prevent key, password, keyboard-interactive, and GSSAPI
credential projection. Long-lived execution must hold the provider's lifecycle
admission for the entire SSH connection, not merely perform a point-in-time
readiness check.

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
4. A transport provider never reads, writes, or assumes the layout of a peer
   transport's fragment. The core audits only the shared
   `50-agent-ssh-*.conf` namespace and never parses or changes unrelated
   OpenSSH drop-ins.

Managed fragments may contain only exact `Host <name>` blocks and ordinary
indented options. Wildcard/multi-host aliases, nested `Host`, `Match`, and
`Include` directives, control characters, and module names that could escape
the managed filename namespace are rejected before publication.

## Verbs the core satisfies vs. the transport owns

| Verb | Owner |
|---|---|
| `emit-profile` | core (`agent-ssh emit-profile`, `core/ssh_profile.py` compatibility wrapper) |
| `verify` | core (`agent-ssh verify`, `scripts/verify.*`) |
| `doctor` | core (`agent-ssh doctor`, exhaustive human/JSON, report-only) |
| `install-client` | transport (if it needs a client binary) |
| `provision-server` | transport (optional; may be operator-manual) |
| `setup` / `status` | transport driver, run by `agent-ssh setup` / `agent-ssh status` (see below) |

`entrypoints` in `module.yaml` are metadata for installers/orchestrators. The
core `emit-profile` and `verify` commands do not call transport `install-client`
or `provision-server` scripts.

## Driver setup and status

Some transports need provider-owned per-machine setup (for example creating or
enrolling a key, or a one-time interactive provider sign-in). A machine selects
the transport module that owns this in `machines.yaml`:

```yaml
machines:
  workstation:
    hostname: workstation
    ssh:
      driver: { plugin: example-provider, module: example }
```

Both values must be names matching `^[a-z0-9][a-z0-9-]*$`; the mapping must
contain exactly `plugin` and `module`. A machine without `ssh.driver` has no
driver setup (`state: ready`).

**Resolution.** `agent-ssh setup|status` resolve `machines.yaml` like
`session-snapshot` (`--machines`, else the calling repo, else the bound external
state root), pick the local machine by matching `socket.gethostname()` against
each entry's `hostname` (falling back to its key; exactly one match, or pass
`--machine <key>`), then locate `<plugin-root>/transports/<module>/module.yaml`.
Plugin roots are searched in tiers; the first tier with a hit wins and must have
exactly one distinct match (otherwise the result is `failed`, listing the
candidates):

1. agent-ssh's own payload, when `plugin` is `agent-ssh`;
2. directory-source marketplaces in the calling repo's
   `.github/copilot/settings.json` (`extraKnownMarketplaces.<name>.source` with
   `source: directory` and a repo-relative `path`; plugin directories come from
   `<path>/.claude-plugin/marketplace.json` or
   `<path>/.github/plugin/marketplace.json`, else `<path>/<plugin>`). This tier
   is consulted only when the repo root is covered by Copilot's own folder
   trust (`trustedFolders` in `$COPILOT_HOME/config.json`, default
   `~/.copilot/config.json`), so an untrusted checkout can never supply or
   override an executable provider, and only when `<plugin>@<marketplace>` is
   explicitly enabled (`enabledPlugins` in the repo's untracked
   `settings.local.json`, then `settings.json`, then the user's
   `<copilot home>/settings.json`). `settings.local.json` may also redeclare a
   marketplace by name (and point outside the repo); committed declarations
   must resolve inside the repository, and a manifest plugin `source` must stay
   inside its marketplace directory;
3. installed plugins, `<copilot home>/installed-plugins/<marketplace>/<plugin>`,
   only when the copy is effectively active: an explicit `enabledPlugins`
   value (repo local, repo, then user) wins, otherwise the `enabled` flag of its
   `installedPlugins` record in `config.json`. A copy with no inventory record
   is skipped.

A disabled candidate is never executed; when nothing else matches, the
not-found error names the skipped disabled plugins.

`--module <path to module.yaml>` overrides discovery and is the explicit
authorization path (the `module` name must still match the declaration).

**Capabilities and entrypoints.** `capabilities.setup` / `capabilities.status`
(booleans) enable `entrypoints.setup` / `entrypoints.status` (`posix` / `windows`
script paths relative to the module directory). Without `setup`, `agent-ssh
setup` reports `ready` ("no driver setup"). Without `status`, `agent-ssh status`
runs the setup entrypoint in plan mode (no `--apply`, never `--interactive`).
On Windows `.ps1` runs via `powershell -NoProfile -ExecutionPolicy Bypass -File`
and `.py` via the current Python; other files (and every POSIX script except
`.py`) execute directly. Batch (`.cmd`/`.bat`) entrypoints are refused because
`cmd.exe` would re-parse the machine key and topology path. Entrypoints run
headless (no console window), with stdin closed and output captured under byte
budgets enforced while reading (64 KiB each for stdout and stderr; overflow is
`failed`), from the module directory (the directory holding `module.yaml`).

**Invocation.**

```text
<entry> --machine <local-key> --machines <absolute machines.yaml> [--apply] [--interactive] --json
```

Without `--apply` the entrypoint plans only. Without `--interactive` it must
never prompt; anything that needs the operator is reported as an auth request.
Provider discovery ignores inherited `GIT_*` environment selectors so the
calling repository is the one containing the working directory.
stdout must be exactly one JSON object (at most 64 KiB):

```json
{"schema": "agent-ssh.driver-result", "version": 1,
 "state": "ready|planned|applied|operator-action-required|failed",
 "detail": "human summary",
 "auth_requests": [{"key": "<stable collapse key>", "driver": "<plugin>:<module>",
                    "machine": "<key>", "reason": "...", "command": ["argv0", "arg"]}]}
```

| state | exit code | auth_requests |
|---|---|---|
| `ready`, `planned`, `applied` | `0` | must be empty |
| `operator-action-required` | `2` | at least one |
| `failed` | `1` | must be empty |

Validation is strict: unknown fields, a wrong schema/version, non-string argv
entries, `driver`/`machine` values that differ from the invoked driver and
machine, oversized output, or an exit code that disagrees with `state` all turn
the result into `failed`. `auth_requests` may be omitted when empty. At most 32
requests and 64 argv entries are accepted; strings are bounded to 4096 chars.

**Interactive mode.** `agent-ssh setup --interactive` requires an attached
console. When the result is `operator-action-required`, agent-ssh runs each
request's `command` once per distinct `key`, attached to the console (the only
step that inherits it; its stdout is redirected to stderr so `--json` output
stays parseable). Auth commands run from the module directory with `argv0`
resolved to a trusted absolute path: absolute paths are used as-is, a path
containing a separator is resolved inside the module directory (escaping it is
refused), and a bare name is searched on absolute `PATH` entries only (never
the caller's working directory). A resolved batch file is refused if any
argument contains `cmd.exe` metacharacters. agent-ssh then re-runs the
setup entrypoint once with the same flags and reports that second result (exit
`2` if still `operator-action-required`).

`agent-ssh` prints the validated result (`--json`) or a short summary. The
human summary escapes terminal control characters from provider-supplied text,
and the suggested `run:` line is quoted with `shlex.join` on POSIX and
`subprocess.list2cmdline` on Windows. The
`auth_requests` list is the input contract for an operator auth queue: unattended
runs (for example renewal) report requests instead of prompting, and the `key`
lets a queue collapse duplicates.

## Failure behavior

- `emit-profile` exits `2` when the module file lacks a string `module` name.
  File/permission errors and invalid jumpbox records fail loud instead of
  producing a partial profile.
- Managed-fragment sweeps isolate malformed/stale peers, cap and deduplicate
  operational warnings, and direct the operator to `agent-ssh doctor`.
- `verify` exits `2` when no host names are supplied, exits `1` if any probed
  alias is unreachable or has a confirmed-inactive managed profile, and prints
  one `[OK]` / `[FAIL]` line per alias. Network unreachability is operational
  status only; it never makes a fragment stale.

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
