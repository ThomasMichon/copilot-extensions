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
- **Umbrella issue:** #786 (supersedes #775, the headless sub-thread — Phases 1-2 landed); current regression: #2037.

## Guiding Intent

Every agent-* background launch on Windows must be **headless** — no window ever
surfaces, Windows Terminal / the DefTerm handoff included — and it must use **one
uniform mechanism**, not per-plugin variants. `-WindowStyle Hidden` alone does
not achieve this (DefTerm ignores it); the correct mechanism differs by launch
kind:

- **pwsh/powershell launches** (session-start reconcile, scheduled tasks): wrap
  in `conhost.exe --headless <interp> …` (proven: agent-bridge/vault/dispatch).
- **Long-lived detached daemons**: `DETACHED_PROCESS` (no console at all), stdio
  redirected to files and a GUI-subsystem root such as `pythonw.exe`. Every
  console child they spawn must use the short-lived captured-tree primitive.
- **Short-lived / piped process trees**: launch a console-subsystem root with
  `CREATE_NO_WINDOW` on win32. Do not combine it with `DETACHED_PROCESS` or use
  a GUI-subsystem root (`pythonw.exe`) — Windows ignores the flag in both cases,
  leaving console descendants free to invoke Default Terminal. The shared
  `run_background_capture` primitive preserves stdout/stderr and timeout/error
  behavior while keeping descendants on the windowless tree.

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
6. **SSH ProxyCommand descendant containment** — tracked by #1742. Use an
   inherited hidden console for non-interactive SSH trees on Windows because
   `CREATE_NO_WINDOW` suppresses `ssh.exe` itself but does not reliably contain
   console-subsystem proxy helpers. Reap the process tree on managed timeout and
   teardown paths.
7. **Crash-proof SSH tree ownership** — evaluate kill-on-close Windows Job
   Objects for SSH trees whose root exits before a proxy descendant. PID-tree
   cleanup cannot recover that already-orphaned shape reliably.
8. **Durable coding and review invariants** — #1940. Run the headless-launch
   guard in required CI, cover canonical shared libraries, reject
   `CREATE_NEW_CONSOLE` in production background paths, and require live
   windowless-parent validation for launch-path reviews.
9. **agent-worktrees status-loop containment** — completed by #1980. The
   resident status monitor and per-session status updater now launch from one
   console-subsystem Python root under `CREATE_NO_WINDOW`, so repeated `psmux`
   probes inherit one hidden console instead of allocating a new console host
   per refresh.
10. **Recurring service-descendant containment** — #2037. Run agent-bridge,
    agent-dispatch supervision, and the container Docker broker from a hidden
    console root; suppress every short-lived captured child; and migrate
    installer probes away from direct console launches.

## Deferred Backlog Intake

- [ ] Accept Windows-launch candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against current spawn, console, and
  binstub behavior; return obsolete or unsafe candidates for explicit
  disposition.
- [ ] Place each accepted public tracker item in exactly one remaining slice,
  extending this plan before implementation when necessary.
- [ ] Use synthetic process trees and paths in all published evidence.

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

### 2026-08-23 - Consoleless-parent SSH residual
- On-box process monitoring after #973 captured periodic agent-dispatch
  embodiment probes launching `ssh.exe` from the new `pythonw.exe` coordinator;
  each unguarded child allocated `conhost.exe` plus `OpenConsole.exe -Embedding`
  and stole focus.
- Follow-up #982 applies shared `no_window_kwargs()` to tracking probes and the
  remaining dispatch-owned SSH transports. The children remain owned and
  pipe-captured; only new-console allocation is suppressed.

### 2026-08-24 - Descendant-console inheritance
- Issue #1015 / PR #1018 removed the Windows batch shim from agent-dispatch
  sibling discovery and launched the versioned runtime directly. Live
  verification showed that the first fix's `pythonw.exe + DETACHED_PROCESS`
  combination still left console descendants free to allocate Default Terminal
  consoles: one identity probe produced four `OpenConsole` processes and four
  foreground-title transitions as its `git.exe` children ran.
- A controlled comparison against the exact identity command established the
  durable short-lived-tree primitive: console `python.exe +
  CREATE_NO_WINDOW` preserved captured stdout/stderr while producing zero
  `OpenConsole` processes and zero foreground transitions. `conhost --headless`
  also contained descendants but consumed the captured stream; a hidden new
  console worked but was unnecessary.
- The follow-up centralizes capture semantics in `run_background_capture`, uses
  it for both identity discovery and bridge liveness, and adds a Windows
  integration regression whose helper child repeatedly launches real
  `git.exe` while observing process and foreground state.

### 2026-09-02 - SSH ProxyCommand descendant residual
- Live process-tree capture found repeated non-interactive `ssh.exe` probes and
  forwards whose direct child used `CREATE_NO_WINDOW`, while proxy helpers still
  acquired their own console hosts.
- #1742 adds an SSH-specific hidden-console spawn path for every ssh-manager and
  agent-dispatch SSH launch site. Non-SSH capture retains the narrower
  `CREATE_NO_WINDOW` primitive.
- Managed cancellation and timeout paths now reap live SSH trees, including
  forwards, relay channels, health checks, graceful disconnects, and dispatch
  probes. Relay teardown still drains captured transports.
- A Windows integration regression repeatedly launches real console descendants
  under both capture modes while observing `OpenConsole.exe` and foreground
  state. Standalone ssh-manager tests and the complete agent-dispatch suite pass.

### 2026-09-03 - Durable invariant follow-up
- A later periodic-SSH regression showed that the existing unit assertion
  incorrectly credited `CREATE_NEW_CONSOLE + SW_HIDE` as headless. Default
  Terminal still surfaced the delegated console.
- #1940 extends the guard from agent-procutil adopters to canonical shared
  libraries, makes it a required CI gate, and promotes the live Windows
  validation matrix into the patterns and contribution guidance.
- The expanded guard immediately caught two direct `CREATE_NO_WINDOW` references
  in agent-dispatch's compatibility layer; those now reuse
  `agent_procutil.no_window_flags()`, demonstrating that the guard closes real
  drift rather than documenting an already-perfect baseline.
- Review then closed two further enforcement holes: aliased/numeric flag
  constants and divergent vendored library copies are now scanned directly, and
  vendored synchronization itself is required in CI and pre-push.
- A second review extended coverage to shipped plugin scripts and annotated
  constants, and made the exception syntax fail closed: it must be an actual
  inline comment with a non-empty reason.

### 2026-09-04 - agent-worktrees status-loop residual
- A one-hour Windows process trace attributed repeated short-lived console
  allocations to the resident `agent-worktrees status-monitor`: each `psmux`
  probe launched beneath the consoleless `pythonw` daemon allocated its own
  `conhost`.
- A controlled comparison over 20 real `psmux list-sessions` cycles reproduced
  20 descendant `conhost` processes with `pythonw` plus per-child
  `CREATE_NO_WINDOW`, versus one inherited hidden `conhost` when console
  `python.exe` was the root under `CREATE_NO_WINDOW`.
- Accepted #1974 as the status-loop containment slice. The implementation will
  use the console-root primitive for both the resident monitor and its
  per-session fallback, then validate multiple real periodic cycles with no
  Default Terminal process or foreground transition.

### 2026-09-04 - agent-worktrees status-loop containment landed
- PR #1980 merged the console-root launch path for both status loops, removed
  the process-wide child-spawn monkeypatch, and shipped agent-worktrees
  `1.5.5-dev2`.
- The focused Windows lane downloads a checksum-pinned psmux release, starts a
  real session, exercises the production daemon spawn seam, and rejects visible
  terminal windows, focus transitions, repeated console hosts, incomplete
  process snapshots, and leaked probe processes.
- The reusable Windows launch pattern now distinguishes fully detached
  `pythonw` daemons from daemons with recurring console descendants that need
  one inherited hidden console.

### 2026-09-04 - recurring service descendants
- Live process inspection found an agent-bridge-owned `ssh.exe` tree whose
  stale container `ProxyCommand` launched `docker.exe` directly, plus recurring
  agent-dispatch Python children rooted beneath consoleless daemons.
- #2028 introduced the container loopback broker, but deployment and follow-up
  validation exposed the same root-launch mistake in the broker, bridge daemon,
  and dispatch supervisor: recurring console descendants were still parented by
  detached `pythonw.exe`.
- #2037 carries the coordinated correction: hidden-console daemon roots,
  explicit no-window flags on captured children, and a headless Docker installer
  probe, followed by multi-cycle Windows validation.
