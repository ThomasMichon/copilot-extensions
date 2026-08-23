# Windows Launch Hardening — headless, uniform process launches on Windows

- **Slug:** `windows-launch-hardening`
- **Repo:** copilot-extensions (PR-required `main`, self-merge)
- **Created:** 2026-08-19
- **Status:** Active
- **Intent:** **closes** the cross-platform-parity expectation
  ([`docs/patterns/cross-platform-parity.md`](../../../docs/patterns/cross-platform-parity.md))
  that background launches are **invisible** on Windows. Several launch sites
  surfaced real console windows (`python.exe`/`pwsh.exe`/`cmd.exe` with `.agent-`
  paths) because they relied on `-WindowStyle Hidden` alone or spawned without
  window-suppression flags.
- **Umbrella issue:** #786 (supersedes #775, the headless sub-thread — Phases 1-2 landed).

## Guiding Intent

Every agent-* background launch on Windows must be **headless** — no window ever
surfaces, Windows Terminal / the DefTerm handoff included — and it must use **one
uniform mechanism**, not per-plugin variants. `-WindowStyle Hidden` alone does
not achieve this (DefTerm ignores it); `CREATE_NO_WINDOW` *creates a console
DefTerm then shows*; so the correct mechanism differs by launch kind:

- **pwsh/powershell launches** (session-start reconcile, scheduled tasks): wrap
  in `conhost.exe --headless <interp> …` (proven: agent-bridge/vault/dispatch).
- **Long-lived detached daemons**: `DETACHED_PROCESS` (no console at all), stdio
  redirected to files — not `CREATE_NO_WINDOW`. See
  `agent_bridge._passive_daemon_creationflags`.
- **Short-lived / piped child subprocesses**: `CREATE_NO_WINDOW` on win32
  (`agent_bridge.agent_registry` pattern; the shared `_exec.no_window_creationflags`).

## Progress

### Landed
- **#779** — `bootstrap-check.ps1` (the 7-plugin `psscriptroot` family +
  agent-machines + agent-ssh) launches its session-start reconcile through
  `conhost --headless` (base64-encoded reconcile to avoid arg quoting); child
  uv/python builds inherit the headless console. agent-index's daemon scheduled
  task → `conhost --headless`. Kills the *every-session-start* window (the
  continuous source).
- **#781** — agent-mcp upstream MCP subprocesses → `CREATE_NO_WINDOW` via the
  shared `_exec.no_window_creationflags()` helper (both transports + the resident
  `serve` session-host). Kills the *per-bridge* MCP-upstream windows.

### Remaining (tracked on #786)
1. **Windows validation** of #779/#781 on a Windows host — confirm no windows
   surface on DefTerm. If the long-lived MCP upstream still surfaces, switch that
   spawn to `DETACHED_PROCESS`.
2. **agent-machines subprocess** (`src/agent_machines/modules.py`) — apply the
   shared no-window helper.
3. **`.cmd`/`.ps1` binstub window audit** — confirm the `~/.local/bin` binstubs
   (the `.cmd` fallback especially) never flash a `cmd.exe` window; headless any
   that do.
4. **agent-bridge's own `bootstrap-check.ps1`** — its stdio-redirecting variant
   needs the reconcile pwsh to self-redirect under `conhost --headless` so it is
   both headless and observable.
5. **Shared helper + guard** — extract a `Start-Headless` pwsh helper (the
   `conhost --headless` wrapper) for uniform pwsh launches, and a guard that
   flags a pwsh/python launch site that isn't headless.

## Not in scope
- **SAC signed-python** handling is already uniform across all 11 plugins.
- **Uniform interpreter resolution** (its Windows `resolve-runtime.ps1` facet) is
  a separate effort (`uniform-runtime-resolution`, #765) — validated together on
  Windows.

## Validation Plan

**Windows-behavior-specific — validated on a Windows host.** Deploy, start
sessions repeatedly (forcing version-drift reconciles), start the agent-index
daemon, run MCP bridges, and confirm **no console windows** surface on Windows
Terminal / DefTerm. The `CREATE_NO_WINDOW`-vs-`DETACHED_PROCESS`-vs-`conhost
--headless` choice is subtle and DefTerm-dependent, so each site is confirmed
against real behavior.

## Journal

### 2026-08-19 - Kickoff + Phases 1-2
- Diagnosed the continuous windows: `bootstrap-check.ps1` (every session start,
  all plugins) used `Start-Process -WindowStyle Hidden` alone -- ignored by
  DefTerm. agent-index's daemon task used bare `powershell.exe`. agent-mcp/
  machines subprocess spawns lacked win32 window-suppression flags.
- Landed #779 (reconcile + daemon task -> `conhost --headless`) and #781
  (agent-mcp subprocesses -> `CREATE_NO_WINDOW`).
- **Consolidated** the remaining Windows-side launch work here (renamed from
  `headless-launch`); confirmed SAC signed-python is already uniform and is not
  a gap. Umbrella issue #786.

### 2026-08-23 - Detached venv trampoline residual
- On-box validation found persistent DefTerm tabs for current agent-bridge and
  agent-dispatch daemons even though their outer spawns used the intended
  detached/headless flags.
- Root-caused the residual to the Windows venv `python.exe` trampoline: the
  detached launcher re-execed a base console interpreter that allocated a new
  console, which DefTerm captured as `OpenConsole.exe`.
- Follow-up #973 moves the proven agent-worktrees `pythonw.exe` selection into
  shared `agent-procutil` and applies it to fully detached Python daemons across
  the runtime plugins. Interactive and pipe-captured children remain on
  `python.exe`.
