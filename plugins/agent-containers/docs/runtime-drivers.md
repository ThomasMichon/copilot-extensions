# External runtime drivers

A fleet normally runs on the built-in **Docker** runtime (`devcontainer up` or
`docker run`). A fleet that sets `driver:` instead delegates every runtime
operation to an **external driver command**: a separate program that owns image
selection, workspace setup and container lifecycle for its runtime (for example
a Podman-based tool). agent-containers keeps everything that is not a runtime
operation:

| Stays in agent-containers | Delegated to the driver |
|---|---|
| fleet sizing and `<prefix>-<n>` naming | provision a named member |
| leases, `borrow`/`release`, admission | enumerate members (incl. stopped) |
| readiness (`namespace-ensure-ready`) | inspect one member |
| trust posture and launch policy | start / stop / remove |
| the agent-bridge `container:<name>` resolver | non-interactive exec (captured) |
| Copilot auth staging and launch hardening | streaming exec (ACP stdio, SSH `ProxyCommand`) |
| credential relay (unless the driver owns it) | copy host files into a member |

The agent-bridge resolver's spawn command stays
`agent-containers exec --stdio <name>`; that wrapper routes through the driver.

## Fleet configuration

```yaml
fleets:
  example:
    driver: example-driver          # registered driver name (see Discovery)
    driver_options:                 # passed verbatim on every call; never secret
      profile: default
    size: 2
    workspace_folder: /workspaces/example
    exec_user: vscode
    relay_enabled: false            # or a driver with owns_relay/ssh_transport
```

Absent `driver` keeps today's Docker behavior unchanged. `driver: docker` is
rejected (omit the key instead).

Allowed with `driver`: `size`, `name_prefix`, `workspace_folder`, `exec_user`,
`acp_command`, `environment` (forwarded to `provision`), `forward_gh_token`,
`relay_enabled`, `copilot_cli_account`, `copilot_auth_mode`.

Rejected with `driver` (config load fails with a clear error -- these are the
Docker/devcontainer provisioning keys the driver owns; pass equivalents through
`driver_options`): `repo`, `devcontainer_path`, `devcontainer_config`, `image`,
`network`, `memory`, `cpus`, `pids_limit`, `workspace_size`, `home_size`,
`host_workspace_path`, `host_home_path`, `home_folder`, `systemd_capable`,
`native_inference`, `dispatch_only`. Driver fleets must also be `trusted`
(the restricted profile is enforced by inspecting Docker's hardening flags) and
use the default `lifecycle_owner`.

A driver fleet may also come from a plugin-shipped `containers.yaml` fragment
(`containersConfig`). `driver` and `driver_options` compose like any other
fleet keys: operator layers override per key (`driver_options` merges
recursively), an empty operator entry (`example:`, `example: {}` or
`example: null`) removes the shipped fleet and with it all driver routing, and
driver validation runs on the composed fleet, so an operator `image` added to a
shipped driver fleet is refused.

## Discovery

A driver name resolves to a command, first match wins:

1. **Manifest** `<drivers dir>/<name>.json`, where the drivers dir is
   `$AGENT_CONTAINERS_DRIVERS_DIR` or `~/.agent-containers/drivers.d`:

   ```json
   {"schema_version": 1, "name": "example-driver",
    "command": ["/opt/example/bin/example-driver", "--quiet"]}
   ```

   `name` must equal the file stem; `command[0]` must be an existing absolute
   executable or resolve on `PATH`. Each file is its own fault boundary: an
   invalid manifest is reported and never hides valid ones. A tool may drop its
   own manifest at install time.
2. **PATH**: an executable named `agent-containers-driver-<name>`.
3. **Bundled Podman**: `driver: podman` uses the selected agent-containers
   interpreter's `agent_containers.podman_driver` when no explicit manifest or
   PATH driver is present. An invalid explicit Podman manifest refuses instead
   of silently selecting this fallback or a PATH implementation.

`agent-containers drivers [--json]` lists registered drivers and invalid
manifests, and names the bundled fallback separately (`bundled_drivers` in JSON).
`installer-readiness` reports a fleet whose driver is not
registered. Docker is not required when every configured fleet uses a driver.

### Bundled Podman configuration

```yaml
fleets:
  example:
    driver: podman
    driver_options:
      image: registry.example.com/dev-environment:stable
      # executable: /opt/podman/bin/podman
      # connection: existing-connection
      pull_policy: missing
    size: 1
    workspace_folder: /workspaces/example
    exec_user: vscode
```

The initial authenticated transport is **POSIX-host SSH**, including a
preconfigured rootless Podman engine. Windows driver-backed SSH and streaming
exec remain unsupported; readiness refuses that lane. No native platform or
installed/live qualification is implied by the portable driver controls.
The image must already supply `/bin/sleep`, the configured execution user,
the existing configured workspace directory with usable permissions, `sshd`,
`ssh-keygen` and the normal guest tools.
The driver replaces the image entrypoint with `/bin/sleep infinity`; provider
readiness and SSH preparation validate the real consumer prerequisites before
dispatch. A running member alone is not readiness proof.

`image` is required. `executable` selects the existing Podman client (or an
operator-owned equivalent wrapper), defaulting to `podman` on PATH. An optional
`connection` names an already-configured Podman connection. The driver never
falls back to Docker, installs an engine, initializes or starts a Podman machine,
or runs login. Unknown `driver_options` are rejected.

Image aliases, registries, mirrors, transport support, TLS and authentication
remain Podman's configured responsibilities. The driver does not read or copy
credential files, inject inline registry credentials, weaken TLS, or guess a
registry. Control/image calls have closed stdin, no TTY, bounded captured output and deadlines;
an alias requiring a prompt fails rather than becoming an interactive login.
Remote Podman clients support only registry transport; local clients may use
their other configured native image transports.

`pull_policy` accepts `missing` (default), `always`, or `never`. `newer` is
deliberately unsupported because Podman can suppress its pull failure in favor
of a local image. A successful native pull/lookup must return one immutable
image ID; creation uses that ID with `--pull=never`, not the mutable reference.
Pull/resolve failure creates no member. Creation never uses `--replace`; an
existing name, unknown outcome or later readiness failure preserves the member
and its writable-layer workspace for inspection, not automatic deletion/retry.
Stopped members remain visible. Explicit removal still follows the provider's
existing lease and retained-work gates.

All per-member effects inspect fleet/driver ownership and select the full native
container ID before exec, copy or lifecycle commands. `start` unpauses a paused
member and starts a stopped member; a failed operation is not replayed.
A pinned request never reaches a same-name successor. Native diagnostics from
failed control operations are not copied into protocol errors; those identify the operation and exit code
and direct the operator to the selected connection/source/authentication owner.
The driver accepts request packets up to 1 MiB. Streaming environment inheritance
uses variable names, not secret values in host arguments.

#### Native qualification (explicit opt-in)

`tests/test_podman_e2e.py` is off by default. Run each fixture only with separate authority
for one temporary member on the selected existing engine/connection and
the selected image. It installs no engine and performs no login. Set
`AGENT_CONTAINERS_PODMAN_E2E=1` and
`AGENT_CONTAINERS_PODMAN_E2E_IMAGE` to that image. Optional `_EXECUTABLE`,
`_CONNECTION`, and `_USER` variables share the same prefix. The default
`_PULL_POLICY=never` requires a local image; another supported policy needs
explicit pull authority.

Select it with the repository's contained runner:
`python tools/run-plugin-tests.py agent-containers --test-file tests/test_podman_e2e.py --allow-explicit-tiers`.
Allow a bounded 120-second item budget (`--test-timeout 120`) for the native
multi-process lifecycle and graceful stop. The runner keeps an isolated home;
when qualifying an existing Windows connection, Podman's supported
`PODMAN_CONNECTIONS_CONF` may point to its existing connection file without
copying its contents or changing general test-home isolation.

Each native fixture explicitly registers the bundled command in its private
driver directory and traps an inherited Podman PATH driver. A private directory
alone is not selection: ordinary driver resolution still prefers PATH over the
bundled fallback.

The lifecycle fixture covers provider provisioning, real native inventory,
literal exec, copy-in, paused/resumed and stopped/restarted state on Windows or
POSIX. Its selected image must provide `/workspaces/qualification` and no baked-in SSH
host keys. The separate authenticated fixture is POSIX-only: it creates a fresh
private SSH key/profile and runs a command through the driver's ProxyCommand.
Cleanup targets only the successfully observed fixture incarnation. Unknown
creation outcomes are preserved for inspection, never name-based cleanup.
Lifecycle success is not authenticated dispatch; real credential-relay delivery,
Bridge dispatch and Windows SSH still need their own acceptance.

## Protocol (version 1)

### Invocation

Every non-interactive verb is:

```
<command...> <verb> --json
```

- The request is one JSON object on **stdin**; the response is one JSON object
  on **stdout**. Diagnostics go to stderr.
- The call is non-interactive: no TTY, stdin carries only the request, and the
  driver must never prompt. The `--json` flag selects this protocol mode for
  every verb, including `exec-stream`; the request's `protocolVersion` selects
  the version. There is no environment marker, because streaming invocations
  (an SSH `ProxyCommand`) may be spawned by processes agent-containers does not
  control.
- Exit `0` with `"ok": true` is success. Any failure exits non-zero with
  `"ok": false` and a typed error.

Every request carries:

```json
{"protocolVersion": 1, "driver": "example-driver", "fleet": "example",
 "driverOptions": {"profile": "default"}}
```

Every response carries `"protocolVersion": 1`; any other value is refused as
`unsupported_protocol`. Success: `{"protocolVersion": 1, "ok": true, ...}`.
Failure:

```json
{"protocolVersion": 1, "ok": false,
 "error": {"code": "not_found", "message": "no member example-3"}}
```

| Error code | Meaning |
|---|---|
| `not_found` | The named member does not exist (`inspect` maps this to "absent"). |
| `conflict` | Name already taken, identity (`id`) mismatch, or state forbids the operation. |
| `invalid_request` | Malformed request or `driverOptions`. |
| `unsupported` | Verb or option not implemented. |
| `unsupported_protocol` | The driver does not speak this `protocolVersion`. |
| `runtime_unavailable` | The underlying runtime is not reachable. |
| `timeout` | The operation did not finish in time. |
| `internal` | Any other driver failure. |

agent-containers itself reports `protocol_error` for an unparseable or invalid
response.

### Member object

```json
{"name": "example-1", "id": "3f2a9c...", "fleet": "example",
 "instance": "example-1", "state": "running", "status": "Up 3 minutes",
 "image": "example/image:1", "repo": "example-repo"}
```

- `name`: required, `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`.
- `id`: required, lowercase hex `[0-9a-f]{12,64}` (the SSH profile alias,
  Session Host recreation and agent-bridge key on a hex instance id). It
  identifies this incarnation and must change when the member is recreated. A
  runtime with non-hex ids should derive one, for example a SHA-256 of its
  native id. If the same container is also visible to the Docker CLI, report
  its Docker id so the two inventories are recognized as one member.
- `fleet`: required; must equal the request's `fleet`. The driver persists the
  fleet/instance identity given at `provision` and reports it back.
- `state`: required. Docker vocabulary: `running` is ready; `exited`,
  `created` and `paused` are startable.
- `instance` (defaults to `name`), `status`, `image`, `repo`: optional.

### Verbs

| Verb | Request fields (beyond the common ones) | Response fields |
|---|---|---|
| `capabilities` | -- | `capabilities` (object), optional `driver: {name, version}` |
| `provision` | `member: {name, fleet, instance}`, `workspaceFolder`, `execUser`, `environment` | `member` (must match `member.name`) |
| `list` | -- | `members`: every member of `fleet`, including stopped ones |
| `inspect` | `name`, optional `id` | `member`, or error `not_found` |
| `start` | `name`, optional `id` | -- |
| `stop` | `name`, optional `id` | -- |
| `remove` | `name`, optional `id`, `force` | -- |
| `exec` | `name`, optional `id`, `argv`, `user`, `env`, `workdir`, `stdin` (string or null), `timeoutSeconds` | `exitCode`, `stdout`, `stderr` |
| `copy-in` | `name`, optional `id`, `source` (absolute host path, file or directory), `target` (member path) | -- |

**Incarnation pinning.** When a caller addresses a member by its instance id
(for example a Session Host bound to the exact container it prepared), every
request for that member -- including `exec-stream` -- carries that `id`. The
driver must apply the operation to that incarnation only, atomically, and
otherwise fail with `conflict` (`exec-stream`: a non-zero exit before running
anything). A request with only `name` targets the current incarnation, like
`docker exec <name>`. Member names and ids are routing handles; a handle that
two drivers or two members report is treated as ambiguous and refused, so
address members by their unique fleet name in that case. Internally,
agent-containers addresses a selected incarnation by the fleet-qualified handle
`<name>.<id>`, so a named launch stays routed (and pinned) even when another
runtime reports the same id. `provision` must
leave the member running.
`exec` runs `argv` directly (no shell) as `user` (null: the runtime default)
with `env` added to the member environment and `workdir` as the working
directory; a non-zero `exitCode` is a successful call.

### Streaming exec

Interactive streaming exec carries the ACP stdio channel and, for
`ssh_transport` drivers, the SSH `ProxyCommand`:

```
<command...> exec-stream --json --request '<request JSON>'
```

The request (same common fields plus `name`, optional `id`, `argv`, `user`,
`env`, `inheritEnv`, `workdir`) is an **argument** because stdin belongs to the
stream. The driver connects its own stdin/stdout/stderr to the process in the
member, without a TTY and without adding output of its own, and exits with
that process's exit code. Because the request is visible in host argv, `env`
and `driverOptions` must never hold secrets; secret values are named in
`inheritEnv` and copied by the driver from its own environment into the member
process.

### Capabilities

```json
{"protocolVersion": 1, "ok": true,
 "capabilities": {"owns_relay": true, "ssh_transport": false}}
```

| Capability | Effect |
|---|---|
| `owns_relay` | The driver guarantees the in-container credential-relay env contract (`LC_GIT_CREDENTIAL_RELAY`, `LC_GIT_CREDENTIAL_RELAY_HOST`, `LC_GIT_CREDENTIAL_RELAY_TOKEN`) itself. agent-containers deploys no relay shims and sets none of those variables, and the resolver publishes `relay_remote_port: null` so agent-bridge adds no reverse forward. |
| `ssh_transport` | Members run `sshd` and `exec-stream` may run `/usr/sbin/sshd -i` as `root`. The trusted OpenSSH transport (and with it the agent-containers relay reverse forward, the `copilot` verb and agent-bridge Session Host) rides on `exec-stream` as its `ProxyCommand`. POSIX hosts only: installer readiness refuses an `ssh_transport` driver fleet on a Windows host, while direct-exec drivers are accepted there. |

Unknown capability keys are ignored. Without `ssh_transport`, ACP dispatch uses
direct streaming exec (`venue.transport: driver-exec`) and Session Host /
interactive `copilot` sessions are refused.

Fail-closed rule: if a fleet's credential relay is enabled
(`relay_enabled`, default true) and the driver declares neither `owns_relay`
nor `ssh_transport`, resolution and dispatch fail with an explicit error. Set
`relay_enabled: false` for that fleet or use a driver with one of those
capabilities.

## agent-bridge integration

The resolver publishes `venue.driver` for driver-backed members. agent-bridge's
pre-launch repository-hosting check normally runs `docker inspect`/`docker
exec` itself; for a venue with `driver` it runs the same Docker-shaped commands
through `agent-containers runtime-proxy --fleet <venue.fleet> inspect|exec ...`,
which reaches the member through its driver with the same instance pinning and
refuses anything that is not a driver member. `--fleet` limits discovery to
that fleet's driver, so another runtime can neither delay the check nor answer
for the same id. An agent-bridge without this support fails closed
(its Docker inspection cannot find the member) rather than skipping the check.

Standalone entry points that receive a member name or instance id in a fresh
process (`session-host-state`, `session-host-cleanup`, `runtime-proxy`)
enumerate driver fleets first so the handle routes to its driver; a
recreated member's previous instance id is inspected through the member name
and reported as missing.

Driver fleets are listed concurrently under one overall deadline (60s for
inventory, 20s for targeted discovery, which fits agent-bridge's 30s provider
inspect). Every protocol call runs as an owned process tree: on timeout the
driver and all of its descendants are retired and output draining is bounded,
so a hung driver delays nothing beyond the deadline and leaves nothing
running. A fleet whose driver fails or misses the deadline is skipped and
logged; operations addressed to that fleet itself fail.

## Lifecycle ownership

agent-containers acts on a driver-fleet member only through the exact
incarnation (member name **and** instance id) its driver reported. The Docker
lifecycle fallback (`start`, `stop`, `rm`) is refused for any container that
claims a driver fleet by label or deterministic name, or whose name a driver
member holds, unless that exact name and incarnation are driver-routed. A
same-name driver member is never sufficient. A Docker container whose name
collides with a driver member is therefore fail-closed: agent-containers
neither manages it through Docker nor through the driver. Rename or remove it
outside agent-containers to resolve the collision. Exec and copy route by
exact handle in the same way: a driver instance id or `<name>.<id>` goes to
its driver, a Docker container id goes to Docker, and a name shared by more
than one runtime is refused as ambiguous.

Addressing a member by name (exec dispatch, namespace resolution, Session Host
preparation) first asks only the driver fleet whose deterministic namespace
(`<prefix>-<n>`) owns that name, within the 20s discovery budget. That answer
is final: an unavailable or slow driver fails the request instead of falling
back to the full inventory, and one bounded `docker inspect` of the name
refuses it as ambiguous when a distinct Docker container shares it (a
container Docker reports with the member's own id is the same member).
An absent member is reported as not found from that fleet's answer alone;
no other fleet is listed to describe it.
An owning driver that cannot list is an explicit error for targeted lookups
(including Session Host state), never evidence that the member is missing;
full inventory stays best-effort and skips the failing fleet.

Routing is reconciled against the configuration and each successful listing,
not accumulated for the life of a process. Every inventory and targeted lookup reconciles
first, including when a reload leaves no driver fleet at all. A fleet's driver
registration is resolved on every inventory: when its manifest is replaced or
removed, routes through the previous executable are retired and it is never
invoked again. A listing still in flight from a replaced or removed registration,
or from a fleet the configuration no longer assigns to that driver, binds nothing
when it completes. A Docker name collision found by a targeted lookup
clears as soon as a later probe no longer sees it. When a configuration reload removes
a driver fleet or assigns it another driver, the retired driver's routes for
that fleet are dropped on the next enumeration, so its names neither reach the
old driver nor become ambiguous with the replacement. A member name the driver
stops reporting stops routing; its instance id and `<name>.<id>` handles stay
pinned to that driver, which refuses the stale incarnation instead of letting
it reach a replacement member or Docker. Docker name collisions are recomputed
from every inventory snapshot.

An attached or detached `copilot` launch validates one member incarnation and
then pins its name to that incarnation for the rest of the launch: SSH
preparation and its `ProxyCommand`, credential staging, shared plugin
preparation and cleanup all carry the validated instance id, so a member
recreated after validation is refused by its driver rather than receiving the
launch or its credentials.
Session Host preparation records the member's runtime identity
(`execution_runtime`: `docker`, or `driver:<driver>/<fleet>@<registration>`
where the registration digest covers the driver's resolved command, its
source and the fleet's effective `driver_options`), which agent-bridge hands back to state and cleanup as
`--expected-runtime`. If that exact runtime no longer owns the member -- its
fleet was removed or re-driven, or its driver registration was replaced, even
when the new runtime reports the same name and id -- cleanup is refused and
state reports the prepared member missing. A driver member's prepared instance
is never acted on without that recorded identity. A driver
member is addressed by its fleet-qualified handle; a bare instance id is used
only when no driver claims it, so cleanup never reaches a different runtime's
member that shares the id.

## Not part of the protocol

Restricted posture, rescue/session-evidence capture and `unpause` remain
Docker-only. Driver members are always trusted venues.
