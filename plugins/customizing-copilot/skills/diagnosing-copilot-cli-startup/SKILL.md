---
name: diagnosing-copilot-cli-startup
description: >
  Diagnose a GitHub Copilot CLI session stuck on Loading or Resuming without
  destroying the evidence. Correlates the terminal or mux pane, exact process
  tree, session-state events, process and extension logs, plugin reconciliation,
  MCP reloads, and Agent Bridge live-session registration. Use for "Copilot
  won't load", "stuck on Loading", "stuck on Resuming", "resume hang",
  "extension reload hang", "session startup wedged", or "diagnose Copilot CLI
  startup".
---

# Diagnosing Copilot CLI startup

Diagnose a headed or interactive Copilot CLI startup that remains on
`Loading...` or `Resuming...`. Preserve the terminal, session state, and logs
until the evidence identifies which startup boundary stopped moving.

For a bridge-owned ACP session, use
`agent-bridge:agent-bridge-troubleshooting`. This skill owns the interactive CLI
and mux side; the bridge skill owns ACP retries, session-host traces, and remote
transport.

## Authority boundary

Start read-only. Unless the operator explicitly authorizes remediation:

- do not stop the Copilot process or mux session;
- do not restart or update Copilot, a plugin runtime, or Agent Bridge;
- do not delete locks, session state, logs, plugin caches, or MCP caches;
- do not replace the session with a fresh one.

A slow startup is evidence too. Capture timestamps before deciding it is
wedged. If it eventually becomes interactive, report the delay as a transient
recurrence rather than rewriting it as success.

## 1. Capture the visible symptom

Record:

- the exact terminal text (`Loading`, `Resuming`, inventory counts, warnings);
- Copilot CLI version, model, and displayed context percentage;
- mux session, window, and pane identity;
- how long the display has remained unchanged.

Use the multiplexer already owning the pane. Resolve its exact executable from
the pane or server process; do not substitute a same-named command from another
installation.

For psmux:

```powershell
& "<exact psmux.exe>" list-windows -t <session>
& "<exact psmux.exe>" list-panes -t <session>
& "<exact psmux.exe>" capture-pane -p -t <session>
```

Capturing the pane is read-only. Attaching another client or sending keys is
not.

## 2. Map pane -> process -> persisted session

List the exact process tree. On Windows:

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -like "*<worktree-or-session-id>*" -or
    $_.ParentProcessId -eq <copilot-pid>
  } |
  Select-Object ProcessId,ParentProcessId,Name,CreationDate,ExecutablePath,CommandLine
```

On POSIX, use `ps` with PID and parent-PID filters. Record:

- the interactive Copilot PID and launch arguments;
- the persisted `--resume=<session-id>`, if present;
- extension-bootstrap children and their PIDs;
- the mux server and pane-wrapper ancestry;
- whether more than one interactive Copilot process claims the same session.

Do not infer the session solely from recency. Prefer the resume argument,
session lock, launch metadata, or live-session registration.

## 3. Read the persisted evidence

The principal paths are:

| Evidence | Path |
|----------|------|
| Session events | `~/.copilot/session-state/<session-id>/events.jsonl` |
| Session metadata | `origin.json`, `workspace.yaml`, `agent-worktrees.json` |
| In-use owner | `inuse.<pid>.lock` |
| Interactive process log | `~/.copilot/logs/process-<start>-<pid>.log` |
| Extension child log | `~/.copilot/logs/extensions/*-<child-pid>.log` |

Read bounded tails first. Preserve timestamps and look for:

- `session.start`, `session.resume`, and `session.shutdown`;
- model warmup completion and `session.info`;
- extension bootstrap `ready`;
- MCP reload requests, cancellation, and completion;
- skill or agent inventory completion;
- `Plugin reconciliation queued` and `Plugin reconciliation queue drained`;
- the final timestamp before logging stops.

Do not treat an extension's visible `ready` notification as proof that the
whole startup barrier completed. Conversely, if every extension is ready and
the reconciliation queue drained, do not misclassify the failure as the older
incomplete-extension-participant race.

## 4. Classify the boundary

| Signature | Classification |
|-----------|----------------|
| One or more extension children never reach `ready`; reconciliation never drains | Extension initialization or generation race |
| Extensions are ready and reconciliation drains, but the UI remains on `Loading` or `Resuming` | Post-reconciliation readiness or foreground-session handoff stall |
| A later retry reaches the prompt with the same state and configuration | Transient startup recurrence; preserve both traces |
| Last lifecycle is `session.resume` with no clean shutdown and state parsing fails | Persisted-session recovery problem |
| The process is busy extracting or replacing an update package | Update work is extending startup; distinguish delay from deadlock |
| Pane is gone but the mux server or wrapper remains | Pane/process lifecycle problem, not a loading barrier |

Warnings about stale MCP cache schemas, failed optional skills, or repeated
extension-ready notices may be adjacent noise. Promote one to root cause only
when its timestamp and lifecycle effect explain the missing transition.

## 5. Use Agent Bridge as a differential

Before stopping anything, inspect the live-session registry with the exact
payload-local Agent Bridge command:

```powershell
& "<agent-bridge catalog argv[0]>" --json live-sessions list
& "<agent-bridge catalog argv[0]>" --json live-sessions resolve --handle <session-or-worktree>
```

Record `status`, `turn_state`, `liveness`, `pid`, `cwd`, and `worktree_id`.

- A live registration can receive a bounded `--status-check`; do not send new
  work to a session whose startup state is unclear.
- An expired registration should reject delivery. That is transport safety,
  not proof the persisted Copilot session is unusable.
- A null `worktree_id` is significant: bridge takeover may be unable to resolve
  the dormant worktree even when `cwd` and `branch` are present.
- `resume <session-id>` applies only to bridge-owned ACP sessions.

Do not use `--force` unless the operator authorized stopping the interactive
CLI and accepting take-over semantics. Follow
`agent-bridge:agent-bridge-troubleshooting` for bridge-owned recovery.

## 6. Operator-authorized reproduction

When the operator authorizes a controlled retry:

1. Save the pane capture, process tree, event tail, and process/extension log
   paths.
2. Stop only the exact interactive Copilot PID. Do not kill by process name or
   restart the shared bridge daemon.
3. Confirm whether the mux pane survived. If the pane command was the Copilot
   process, the pane may exit with it.
4. Relaunch the same persisted session with the same cwd, arguments,
   environment, plugin settings, and launcher path.
5. Keep a successful low-loaded retry alive for operator use.
6. Capture the retry's startup duration and logs even if it succeeds.

Prefer the original launcher or pane wrapper. A manual launch with a different
`PATH`, custom-instructions directory, or plugin environment is a differential
experiment, not an exact reproduction; state that limitation explicitly.

## Report

Report these separately:

1. **Visible symptom and duration**
2. **Last completed lifecycle boundary**
3. **Extension/plugin/MCP state**
4. **Bridge registration and recovery result**
5. **Retry result and whether the failure was transient**
6. **Exact log paths and issue destination**

Sanitize public bug reports: remove usernames, machine names, private
repositories, worktree handles, session IDs, URLs, and internal topology.
Retain the Copilot version, relative event ordering, generic command shape, and
the distinction between persistent and transient behavior.
