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
| 2 | usage error (recorded only when an owning run already exists, e.g. a Manager whose engine rejects the forwarded arguments) |
| 3 | `configuration_limitation` |
| 4 | `auth_required` |
| 5 | `blocked_by_lock` |
| 6 | `timeout` |
| 130 | interrupted |

Runtime installer exits map as: `75` (cutover not completed, previous
generation still serving) to `blocked_by_lock`, `76` (degraded cutover) to
`command_failed`, `124` (watchdog) to `timeout`. A timeout is always
`timeout`: a completed version slot does not prove the timed-out attempt
activated it. Exceptions map as: `TimeoutExpired` to `timeout`;
`KeyboardInterrupt` to `interrupted`; `blocked_by_lock` only on contention
evidence -- `LockTimeout` (`lock_timeout`) or a Windows sharing/lock violation
(winerror 32/33); plain access denial (`PermissionError`, EACCES, EPERM,
winerror 5) to `command_failed` with `error.code=permission_denied`, POSIX EIO
to `command_failed` with `io_error`; anything else to `command_failed`. OS
errors carry `error.path`/`error.op` when known. Each `plugins` entry carries the
`detail` and `error_code` of the one event that set its outcome, and
`last_failure` takes its `summary` and `fix` from one source -- the worst
required target when it set the run's outcome, otherwise the run's own error --
so the remedy always matches the failure it describes.

## Events and redaction

One JSON object per line (at most 8 KiB: an oversize event drops `argv` and
`details`, then bounds its remaining strings until it fits), written with a
single `write()` in
append mode; readers skip and count unparseable lines. The schemas live in
[`schemas/`](schemas/). Run-level phases include `manager_self_update` (the
Manager's own update), `payload_refresh`, `engine_runtime` (agent-worktrees'
own runtime installer), `binstubs`, `module_update` (`modules.json` sibling
modules), `runtime_reconcile` (other registered runtimes), `anchor_sync` and
`prune`, so each has its own timing. Values after `http.extraheader=` and `Authorization:`,
authorization-scheme credentials, GitHub token shapes, and URL user-info are
redacted from `argv`, `action`, `error` and `details` before anything is
written.

## Human operation output

`update_telemetry.human.HumanOperation` is the opt-in presentation extension
used by the Manager update, engine update phases and launch preparation. It
does not own another update run, change structured outcomes, persist human
lines, or acquire a telemetry lock per line.

```text
[12:00:01.120Z] update/engine/runtime_reconcile/agent-example starting [elapsed 0.0s]
[12:00:04.220Z] update/engine/runtime_reconcile/agent-example resolving dependencies [elapsed 3.1s]
[12:00:05.420Z] update/engine/runtime_reconcile/agent-example done exit 0 [duration 4.3s]
```

UTC wall time is correlation only. Elapsed and duration use the containing
operation's `time.monotonic()` clock, independently of wall-clock adjustments.
Nested scopes inherit clocks and sink; completion is emitted once. Multiline,
empty and unterminated child lines are framed individually. Known credentials
are redacted before the human sink; already canonical child records are not
framed twice. Human output goes to stderr; JSON output helpers and unscoped
legacy output retain their existing behavior.
Redaction covers complete logical messages before line splitting or state
prefixes. Native relays retain only incomplete credential prefixes recognized
by the existing redactor until their following text can be masked safely.

`native_output()` adapts only explicitly selected human installer streams.
The existing Python operation reads a pipe live; no reporting interpreter,
raw-output spool file, daemon or per-line process is introduced. The caller
still owns the child command, environment, timeout and exact exit status.
After that command returns, the relay drains bytes already written without
waiting for EOF from detached service descendants. Parsed JSON commands,
binary/private protocols and interactive passthroughs must not use this adapter.

### Native producer admission

Captured PowerShell installers opt in through generated
`scripts/install.human-output.json`, bound to the original entrypoint and its
packaged `installer-human-output.ps1` initializer. The digests normalize only
CRLF to LF for portable checkouts. These are fixed build declarations, not user
configuration or an alternative signature/trust authority.
`tools/sync-installer-engine.py` owns the helper copies and declarations.
Re-run it when a declaring installer changes. Materialization refreshes the
declaration after rewriting an engine reference and refuses linked, missing
or conflicting helper assets.

The updater requests UTF-8 only in the admitted child's process environment.
That original producer dot-sources its own helper before installer output;
without a request, it does not load the helper at all. A requested missing
helper fails closed before command discovery. The initializer installs UTF-8,
auto-flushing stdout/stderr text writers only in that redirected, consoleless
child. It preserves native-command decoding, pipeline stdin encoding, culture
and console codepages. The host executable, `-File` argv, target file,
parameter binding, execution policy and native process exit remain unchanged.
The helper is a native payload asset beside the installer, not a runtime
Python-wheel dependency. Native signature and execution-policy checks still
apply to both files; no wrapper, evaluation or policy bypass is introduced.
Pre-script host parse/policy diagnostics are not evidence that initialization
ran.

Unknown/legacy PowerShell producers, stale declarations and indirect/private
invocation prefixes retain their original inherited streams without decoding.
They receive one scoped presentation-gap notice, not a claim of successful
UTF-8 capture. Direct POSIX `.sh` installer calls retain the existing Python
stream owner's UTF-8 human adapter. This is not a claim that an arbitrary
private protocol or every standalone installer has the same encoding.

This does **not** yet frame every standalone native bootstrap or shell launch
maintenance line. In particular, the native launchers' bootstrap/self-update
and two-pass reconcile blocks still own their existing setup-log/status
adapters; Bash's `SECONDS` timing there is not a monotonic-clock guarantee.
Before adding a streaming interpreter at that boundary, its admission and
lifetime must be independent of any runtime the same operation updates or
collects. A first bootstrap before an interpreter is admitted retains native
diagnostics rather than claiming fabricated monotonic timing. The Python-owned
native-child adapter needs no such extra interpreter and does not alter
runtime-slot retention or activation.
