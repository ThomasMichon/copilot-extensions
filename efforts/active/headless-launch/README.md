# Headless Launch — no visible console windows on Windows

- **Slug:** `headless-launch`
- **Repo:** copilot-extensions (PR-required `main`, self-merge)
- **Created:** 2026-08-19
- **Status:** Active
- **Intent:** **closes** the cross-platform-parity expectation
  ([`docs/patterns/cross-platform-parity.md`](../../../docs/patterns/cross-platform-parity.md))
  that background launches are invisible on Windows. Today several launch sites
  surface real console windows (`python.exe`/`pwsh.exe`/`cmd.exe` with `.agent-`
  paths) because they rely on `-WindowStyle Hidden` alone.
- **Umbrella issue:** #775

## Guiding Intent

Every agent-* background launch on Windows must be **headless** — no window ever
surfaces, on Windows Terminal / the DefTerm handoff included. `-WindowStyle
Hidden` alone does **not** achieve this (DefTerm ignores it), and
`CREATE_NO_WINDOW` *creates a console DefTerm then shows*; the correct mechanism
differs by launch kind:

- **pwsh/powershell launches** (session-start reconcile, scheduled tasks): wrap
  in `conhost.exe --headless <interp> …` (proven: agent-bridge/vault/dispatch).
- **Long-lived detached daemons**: `DETACHED_PROCESS` (no console at all), stdio
  redirected to files — not `CREATE_NO_WINDOW`. See
  `agent_bridge._passive_daemon_creationflags`.
- **Short-lived / piped child subprocesses**: `CREATE_NO_WINDOW` on win32
  (`agent_bridge.agent_registry` pattern).

## Plan

### Phase 1 — session-start reconcile + agent-index daemon task *(this PR)*
- `bootstrap-check.ps1` (the 7-plugin psscriptroot family + agent-machines +
  agent-ssh) now launches its background reconcile through `conhost --headless`
  (base64-encoded reconcile command to avoid arg quoting). This kills the
  every-session-start window (the primary, continuous source), and its child
  uv/python venv builds inherit the headless console.
- agent-index's daemon scheduled task now uses `conhost --headless` instead of
  bare `powershell.exe`.

### Phase 2 — MCP + machines subprocess spawns
- agent-mcp spawns an upstream MCP subprocess **per bridge** (npx→`cmd.exe`,
  node, python); add win32 `creationflags` (CREATE_NO_WINDOW for the piped
  child) in the stdio/cli transports. Same for agent-machines.

### Phase 3 — shared helper + guard + agent-bridge bootstrap-check
- Extract a shared `Start-Headless` pwsh helper + a Python `win_creationflags`
  helper so the right mechanism is applied uniformly.
- Fix agent-bridge's own bootstrap-check (its variant redirects stdio to a
  reconcile log, which needs the reconcile pwsh to self-redirect under conhost).
- A guard flags a pwsh/python launch that isn't headless.

## Validation Plan

**Windows-behavior-specific — validated on a Windows host.** Deploy, start
sessions repeatedly (forcing version-drift reconciles), start the agent-index
daemon, run MCP bridges, and confirm **no console windows** surface on Windows
Terminal / DefTerm. The CREATE_NO_WINDOW-vs-DETACHED_PROCESS-vs-conhost choice is
subtle and DefTerm-dependent, so each site is confirmed against real behavior.

## Journal

### 2026-08-19 - Kickoff (Phase 1)
- Diagnosed the continuous windows: `bootstrap-check.ps1` (every session start,
  all plugins) used `Start-Process -WindowStyle Hidden` alone -- ignored by
  DefTerm. agent-index's daemon task used bare `powershell.exe`. agent-mcp/
  machines subprocess spawns lack win32 window-suppression flags.
- Phase 1: migrated 9 bootstrap-check variants + the agent-index task to
  `conhost --headless` (proven agent-bridge pattern). Staged for
  Windows validation.
