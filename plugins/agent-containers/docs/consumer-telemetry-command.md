# Optional consumer telemetry command

This is opt-in **source support**, not proof of release or installation.
Finite `agent-containers` CLI runs use the shared
[consumer telemetry command v1](../../../docs/patterns/consumer-telemetry-command.md)
finite-owner admission and the closed `containers.lifecycle.v1` projection.
It does not install a receiver, observability SDK or service.

## Admission

The machine owner may place `consumer-telemetry.json` in the runtime
directory (normally `~/.agent-containers`, or `AGENT_CONTAINERS_HOME`):

```json
{"schema_version": 1, "root": "/absolute/approved/harness"}
```

Use a native absolute path on Windows. The approved root supplies the shared
`.agent-telemetry/config.json` command declaration and optional gated
`config.local.json`; their parser, precedence and local disable are unchanged.
The shared contract owns the policy grammar and the refusal semantics.

Without the file the run is unchanged: only that one path is read and no
child starts. A run that noted no stage (fleet listing, leases, `--help`,
`exec`) never looks it up. A present invalid file refuses export for the run;
it never borrows another route. No CLI argument, environment variable,
repository or current directory selects a root or a command.

## What is observed

The remote-worker paths of the `copilot` verb note one closed record per
stage they pass through: the detached launch (`copilot --detach`) and the
attached terminal session (`copilot` without `--detach`). After the console
command returns, the run offers those records (at most 32) once, then drains
within the contract's bounded shutdown budget. A detached launch also offers
one overall `launch` record whose failure reason is the last failing stage's
reason. Receiver absence, failure or refusal never changes the command's
output or exit status, and the original error is always reported unchanged.

| Stage | Where | Outcomes and reasons |
|-------|-------|----------------------|
| `container_lookup` | both paths | `engine_unavailable` (container CLI, daemon or runtime driver unavailable), `listing_failed` (other lookup failures; not reported as missing), `not_found` (not a fleet member), `not_running` (container not ready) |
| `seed_validation` | detached | `seed_too_long`, `seed_invalid` (also unreadable reference files) |
| `claim` | both paths | `claim_busy`; success with `force_claimed` when `--force` was used |
| `account_resolution` | both paths, only when the fleet forwards a GitHub token | `account_unresolved` |
| `bridge_payload` | detached | `payload_install_failed` (the agent-worktrees launch payload could not be installed in the container) |
| `credential_relay` | both paths, only when the relay is enabled | `relay_not_listening` (no live published relay port, or its ping probe failed) |
| `plugin_preparation` | both paths | `marketplace_add_failed`, `plugin_install_failed`, `clone_auth_failed`, `relay_not_listening` |
| `exec` | detached | `timeout`/`transport_failed` with `limit_ms`, `transport_failed` (OpenSSH exit 255) |
| `remote_embody` | detached | `installer_unavailable`, `bootstrap_python_unavailable` |
| `bridge_registration` | detached | `session_unregistered`, `session_unrepresented` |
| `container_connect` | attached | `connect_failed` (OpenSSH exit 255; any other exit is the interactive session's own status and records success) |
| `launch` | detached | the overall detached launch |

Every stage may also report `interrupted` or `other`. A successful record
always has reason `none` except a forced claim. Records carry `attempt`
(1..65536) and, where measured, `duration_ms` from a monotonic clock.

Reasons are classified in-process from fixed markers; only the code leaves
the process. Container, fleet, image, repository and account names, container
ids, paths, hosts, command lines, seed text and error text are never offered.

## Coverage inventory

| Registered value | Status |
|------------------|--------|
| Every stage and reason in the table above | Emitted |
| `container_start` stage; `image_unavailable`, `start_failed` reasons | Not emitted: the `copilot` paths refuse a container that is not running rather than starting it |
| `retry` outcome; `transport_retry` reason | Not emitted: the launch and attach paths make a single exec/SSH attempt |
| `lease_conflict`, `credential_fill_failed`, `snapshot_timeout` reasons | Not emitted: the container paths have no corresponding phase |

## Support discovery

The selected executing package carries
`agent_containers/metadata/consumer-telemetry-contract.json`, advertising
`consumer-telemetry-command` version 1 over `command-jsonl-v1` with **only**
`containers.lifecycle.v1`. Missing or older metadata means unknown support.
Admit that exact schema in the receiver before configuring the route. Older
runtimes do not read the admission file; withdraw it before selecting one.

`tests/test_consumer_telemetry_command.py` drives the instrumented entry
through the real finite owner and a real receiver subprocess, and checks
legacy inertness, stage sequencing, reason classification and receiver
failure.
