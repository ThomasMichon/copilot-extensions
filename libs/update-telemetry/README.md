# update-telemetry

Shared, stdlib-only telemetry for update runs (`agent-worktrees update`,
`worktree-manager update`): one append-only `events.jsonl` per run, a folded
`update-status.json`, and a stable exit-code table. Distribution
`agent-update-telemetry`; import module `update_telemetry`.

## Locations and propagation

| What | Where |
|---|---|
| Home | `$COPILOT_EXTENSIONS_UPDATE_HOME`, default `~/.copilot-extensions/update/` |
| Status | `<home>/update-status.json` (temp file + `os.replace`, under `<home>/update-status.lock`) |
| Events | `<home>/runs/<run_id>/events.jsonl` (the newest 20 run dirs are kept) |

The first process of a run is the **owner**: it creates the run and exports
`COPILOT_EXTENSIONS_UPDATE_RUN_ID`, `COPILOT_EXTENSIONS_UPDATE_EVENTS`
(absolute path) and `COPILOT_EXTENSIONS_UPDATE_OWNER` (`<proc>:<pid>`) to its
children. A process that finds them set is a **participant** and only appends
events. The owner writes `in_progress` status at start (refreshed at most every
30 s) and the final status in `finish()`, which also restores the environment.

```python
import update_telemetry as ut

run = ut.Run.from_env_or_new("engine")           # owner or participant
run.enter("payload_refresh")                      # sequential run-level phases
run.target("payload_refresh", "agent-x@copilot-extensions", ut.VERIFIED_CURRENT)
with run.phase("manager_self_update") as ph:     # exceptions map to outcomes
    ph.changed = True
exit_code = run.finish(ut.VERIFIED_CURRENT)       # returns the exit code below
```

Every method fails open: an I/O error prints one warning to stderr and never
raises into, or changes the result of, the update.

## Outcomes and exit codes

Outcomes in increasing severity: `verified_current`, `requires_restart`,
`in_progress` (status only), `configuration_limitation`, `blocked_by_lock`,
`auth_required`, `timeout`, `command_failed`; `skipped` never counts. The run
outcome is the most severe outcome over **required** targets; failures of
non-required targets are reported as `advisories`.

| Exit | Outcome |
|---|---|
| 0 | `verified_current` or `requires_restart` |
| 1 | `command_failed` |
| 2 | usage error |
| 3 | `configuration_limitation` |
| 4 | `auth_required` |
| 5 | `blocked_by_lock` |
| 6 | `timeout` |
| 130 | interrupted |

Runtime installer exits map as: `75` (cutover not completed, previous
generation still serving) to `blocked_by_lock`, `76` (degraded cutover) to
`command_failed`, `124` (watchdog) to `timeout`. Exceptions map as:
`TimeoutExpired` to `timeout`; `PermissionError` / errno 5 or 13 to
`blocked_by_lock` (with `error.path`/`error.op`); `KeyboardInterrupt` to
`interrupted`; anything else to `command_failed`.

## Events and redaction

One JSON object per line (at most 8 KiB), written with a single `write()` in
append mode; readers skip and count unparseable lines. The schemas live in
[`schemas/`](schemas/). Values after `http.extraheader=` and `Authorization:`,
authorization-scheme credentials, GitHub token shapes, and URL user-info are
redacted from `argv`, `action`, `error` and `details` before anything is
written.
