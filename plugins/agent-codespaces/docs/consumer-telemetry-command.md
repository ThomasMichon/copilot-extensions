# Optional consumer telemetry command

This is opt-in **source support**, not proof of release or installation.
Finite `agent-codespaces` CLI runs use the shared
[consumer telemetry command v1](../../../docs/patterns/consumer-telemetry-command.md)
finite-owner admission and the closed `codespaces.lifecycle.v1` projection.
It does not install a receiver, observability SDK or service.

## Admission

The machine owner may place `consumer-telemetry.json` in the runtime
directory (normally `~/.agent-codespaces`):

```json
{"schema_version": 1, "root": "/absolute/approved/harness"}
```

Use a native absolute path on Windows. The approved root supplies the shared
`.agent-telemetry/config.json` command declaration and optional gated
`config.local.json`; their parser, precedence and local disable are unchanged.
The shared contract owns the policy grammar and the refusal semantics.

Without the file the run is unchanged: only that one path is read and no
child starts. A run that noted no stage (`--help`, or a command that never
resolves an account or launches a worker) never looks it up. Any command that
runs account discovery, including `list` and CodeSpace lookup, notes an
`account_resolution` record and so offers it to a configured receiver. A present invalid file refuses export for the run; it never
borrows another route. No CLI argument, environment variable, repository or
current directory selects a root or a command.

## What is observed

Remote-worker launch and connect paths (`copilot --detach`, `ssh` and the
`copilot` venue preparation they share) note one closed record per stage
they pass through. After the console command returns, the run offers those
records (at most 32) once, then drains within the contract's bounded
shutdown budget. A detached launch also offers one overall `launch` record
whose failure reason is the last failing stage's reason. Receiver absence,
failure or refusal never changes the command's output or exit status.

| Stage | Outcomes and reasons |
|-------|----------------------|
| `codespace_lookup` | `listing_failed` (the listing itself failed; not reported as missing), `not_found` |
| `seed_validation` | `seed_too_long`, `seed_invalid` |
| `credential_relay` | `relay_not_listening`, `credential_fill_failed` |
| `claim` | `claim_busy`, `lease_conflict`, `other`; success with `force_claimed` when `--force-claim` was used |
| `account_resolution` | `timeout`/`snapshot_timeout` with `limit_ms` (including a timeout the same-cell adapter wraps), `account_unresolved` |
| `plugin_preparation` | `marketplace_add_failed`, `plugin_install_failed`, `clone_auth_failed`, `relay_not_listening` |
| `bridge_payload` | `payload_install_failed` |
| `codespace_connect` | `retry`/`transport_retry` per transient attempt, `timeout`/`transport_failed` with `limit_ms`, `connect_failed` |
| `ssh` | `retry`/`transport_retry` with the attempt number, `timeout`/`transport_failed` with `limit_ms` (the operation's overall deadline, including after a successful connect), `transport_failed`, `connect_failed`. A standalone remote command (`ssh --remote-cmd`, or a launch path that does not own an `ssh` stage) records its own execution: `transport_failed` for SSH exit 255 or a raised transport error, `timeout` with `limit_ms` for the command timeout, and success for any other exit code |
| `remote_embody` | `installer_unavailable`, `bootstrap_python_unavailable` |
| `bridge_registration` | `session_unregistered`, `session_unrepresented` |
| `launch` | the overall detached launch |

Every stage may also report `interrupted` or `other`. A successful record
always has reason `none` except a forced claim. Records carry `attempt`
(1..65536) and, where measured, `duration_ms` from a monotonic clock.

Reasons are classified in-process from fixed markers; only the code leaves
the process. CodeSpace, machine, repository and account names, paths,
command lines, seed text and error text are never offered.

## Coverage inventory

| Registered value | Status |
|------------------|--------|
| Every stage and reason in the table above | Emitted |
| `credential_relay` success records from the `ssh` relay check | Emitted |

## Support discovery

The selected executing package carries
`agent_codespaces/metadata/consumer-telemetry-contract.json`, advertising
`consumer-telemetry-command` version 1 over `command-jsonl-v1` with **only**
`codespaces.lifecycle.v1`. Missing or older metadata means unknown support.
Admit that exact schema in the receiver before configuring the route. Older
runtimes do not read the admission file; withdraw it before selecting one.

`tests/test_consumer_telemetry_command.py` drives the instrumented entry
through the real finite owner and a real receiver subprocess, and checks
legacy inertness, stage sequencing, reason classification and receiver
failure.
