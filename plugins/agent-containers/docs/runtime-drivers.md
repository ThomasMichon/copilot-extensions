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

`agent-containers drivers [--json]` lists registered drivers and invalid
manifests. `installer-readiness` reports a fleet whose driver is not
registered. Docker is not required when every configured fleet uses a driver.

## Protocol (version 1)

### Invocation

Every non-interactive verb is:

```
<command...> <verb> --json
```

- The request is one JSON object on **stdin**; the response is one JSON object
  on **stdout**. Diagnostics go to stderr.
- The call is non-interactive: no TTY, stdin carries only the request, and the
  driver must never prompt. `AGENT_CONTAINERS_DRIVER_PROTOCOL=1` is set in its
  environment.
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

- `name`, `id`: required, `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`. `id` identifies
  this incarnation and must change when the member is recreated.
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
address members by their unique fleet name in that case. `provision` must
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
| `ssh_transport` | Members run `sshd` and `exec-stream` may run `/usr/sbin/sshd -i` as `root`. The trusted OpenSSH transport (and with it the agent-containers relay reverse forward, the `copilot` verb and agent-bridge Session Host) rides on `exec-stream` as its `ProxyCommand`. POSIX hosts only. |

Unknown capability keys are ignored. Without `ssh_transport`, ACP dispatch uses
direct streaming exec (`venue.transport: driver-exec`) and Session Host /
interactive `copilot` sessions are refused.

Fail-closed rule: if a fleet's credential relay is enabled
(`relay_enabled`, default true) and the driver declares neither `owns_relay`
nor `ssh_transport`, resolution and dispatch fail with an explicit error. Set
`relay_enabled: false` for that fleet or use a driver with one of those
capabilities.

## Not part of the protocol

Restricted posture, rescue/session-evidence capture and `unpause` remain
Docker-only. Driver members are always trusted venues.
