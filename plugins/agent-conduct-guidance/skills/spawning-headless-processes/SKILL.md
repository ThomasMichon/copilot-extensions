---
name: spawning-headless-processes
description: >
  Selects the correct headless/windowless mechanism before spawning a child
  process from CMD, PowerShell, Python, Node.js, or another language, so a
  short-lived helper, background job, or self-authored service never surfaces
  a visible console window or steals focus. Use when writing or running a
  script that starts another process (a build, a dev/watch server, a
  scheduled task, a detached daemon, a probe) especially on Windows, or when
  diagnosing a process that keeps flashing/stealing focus on spawn. Not for
  interactive terminals the user explicitly wants to see, and not a
  replacement for a plugin's own vendored process-spawn library when one is
  already in scope (e.g. copilot-extensions' `agent-procutil` /
  `docs/patterns/windows-background-process-launch.md`) -- prefer that when
  present, and use this skill's routes otherwise.
---

# Spawning Headless Processes

A process spawned to serve one turn, one probe, or one background job should
never surface a window or steal focus -- on any OS, but especially on Windows,
where the default behavior of a console-subsystem child is to allocate its own
terminal window even when its parent has none. `-WindowStyle Hidden` alone does
**not** fix this: it asks Windows to allocate a console first and hide one
implementation of it second, and the user's configured Default Terminal is
free to still surface the delegated window. Suppress window creation at launch
time instead.

## Decide the launch shape first

| Shape | Default |
|-------|---------|
| One-shot command whose output you need (`git status`, a build, a lint run) | Short-lived captured child -- see the table below |
| Long-lived background process (dev server, watcher, daemon) the current turn does not wait on | Detached/background launch -- still headless, plus explicit stdio redirection so nothing pipes to a vanished console |
| A process that must be genuinely interactive (the user is meant to see and type into it) | Do not headless it; that is the reviewable exception -- state why |
| Same-machine work reachable via an existing local API, module import, or running service | Skip the process spawn entirely; call the API/module/service instead |

Avoiding the window is the last line of defense -- avoid the unnecessary
process first when a local API or already-running service can do the same
work in-process.

## Per-language, per-OS routes

### PowerShell / `pwsh`

- Calling **out** to a console program from a PowerShell script that must stay
  windowless: wrap it so the child inherits headlessness rather than trusting
  `-WindowStyle Hidden` on `Start-Process` -- e.g. run the target under
  `conhost.exe --headless <interpreter> ...` (proven pattern for pwsh/cmd
  launchers whose output is not captured -- the headless console buffer this
  creates is not piped back to the caller, so this route is wrong the moment
  you need stdout/stderr), or shell out through a console-subsystem root using
  `CREATE_NO_WINDOW` when the caller needs to capture output:

  ```powershell
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = "wsl.exe"              # or git.exe, ssh.exe, any console-subsystem target
  $psi.Arguments = "-d Ubuntu -- bash -lc 'echo hi'"
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $p = [System.Diagnostics.Process]::Start($psi)
  # Sequential ReadToEnd() on both streams can deadlock: the child blocks on a
  # full stderr pipe while stdout's ReadToEnd() is still waiting for EOF, and
  # neither side ever reaches the other read. ReadToEndAsync() on BOTH streams
  # before WaitForExit() keeps both pipes draining concurrently via the .NET
  # thread pool regardless of how much either stream writes -- this does not
  # depend on PowerShell's own event/idle loop the way Register-ObjectEvent +
  # BeginOutputReadLine does (that combination silently captures almost
  # nothing here, because the queued events never get a turn to run during a
  # single blocking script with no idle periods).
  $stdoutTask = $p.StandardOutput.ReadToEndAsync()
  $stderrTask = $p.StandardError.ReadToEndAsync()
  $p.WaitForExit()
  [System.Threading.Tasks.Task]::WaitAll($stdoutTask, $stderrTask)
  $stdout = $stdoutTask.Result
  $stderr = $stderrTask.Result
  ```

  This is the PowerShell/.NET equivalent of the Python `CREATE_NO_WINDOW` route
  below -- `[System.Diagnostics.ProcessStartInfo].CreateNoWindow` plus
  `UseShellExecute = $false` suppresses the window while the two
  `ReadToEndAsync()` calls still capture output, which `conhost.exe
  --headless` cannot do.
- `Start-Process -WindowStyle Hidden` is acceptable **only** for a true
  GUI-subsystem executable that never allocates a console of its own; it is
  not sufficient for `cmd.exe`, `powershell.exe`, `pwsh.exe`, `python.exe`,
  `node.exe`, `wsl.exe`, or any other console-subsystem target. `wsl.exe` in
  particular is easy to miss -- it reads as a "bridge" rather than a console
  program, but Windows launches it exactly like `cmd.exe`: a bare
  `wsl.exe -d <distro> -- <command>` from a windowless parent still allocates
  its own terminal window per invocation.
- A background job whose output must still be captured: prefer `Start-Job`
  (runs in a separate hidden PowerShell process by design) over
  `Start-Process` + polling a redirected file.

### Python

- Short-lived child, output captured or piped -- use `CREATE_NO_WINDOW` on
  Windows:

  ```python
  import subprocess, sys

  kwargs = {}
  if sys.platform == "win32":
      kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
  subprocess.run(["git", "status"], capture_output=True, **kwargs)
  ```

- Long-lived, fully detached daemon with no recurring console descendants --
  launch under `pythonw.exe` (the GUI-subsystem interpreter that ships beside
  `python.exe`) plus `DETACHED_PROCESS`, with stdio redirected to files (a
  vanished console cannot be waited on for output). Resolve the real
  `pythonw.exe`, not a relocatable venv trampoline that might re-exec a
  console base interpreter -- check `pyvenv.cfg`'s base install when running
  inside a virtualenv.
- Long-lived daemon that itself repeatedly spawns console children (e.g. it
  shells out to `git`/`ssh` on every cycle) -- give it a console-subsystem
  root (`python.exe`, not `pythonw.exe`) under `CREATE_NO_WINDOW` so every
  cycle inherits one hidden console, instead of `pythonw.exe` + per-child
  flags (each child would still allocate its own console; a GUI-subsystem
  parent does not suppress a console-subsystem child's own allocation).
- Non-Windows: redirect stdio and use `start_new_session=True` (or
  `os.setsid`) for a detached background process; there is no console-window
  concern to suppress, only the controlling-terminal/SIGHUP concern.

### Node.js

- `child_process.spawn`/`execFile` accept **`windowsHide: true`** directly --
  the built-in, no-extra-flags route on Windows:

  ```js
  const { spawn } = require("node:child_process");
  spawn("git", ["status"], { windowsHide: true });
  ```

- Detached background process: combine `detached: true`, `stdio: "ignore"` (or
  redirected file descriptors), and `windowsHide: true`, then `.unref()` the
  child so the parent can exit without waiting on it.
- `exec`/`execSync` (which spawn through a shell) also honor `windowsHide` in
  their options object -- pass it explicitly rather than relying on the
  default.

### CMD / batch

- A `.cmd`/`.bat` launcher invoked from a windowless parent inherits that
  parent's console state; the risk is a *later* stage of the same script
  calling `start` without `/B` (which explicitly requests a **new** window).
  Use `start /B` for a background step that must not open its own window, and
  avoid bare `start` in any non-interactive automation path.

### Cross-platform / general

- Prefer an existing local API, module, or already-running service over
  shelling out at all.
- Redirect stdout/stderr explicitly rather than leaving them attached to a
  console that may not exist by the time the child produces output.
- A genuinely interactive terminal the user is meant to see and type into is
  the one reviewable exception -- state the reason inline (e.g. a
  `# headless-guard: allow <reason>` marker, matching the convention used by
  copilot-extensions' own enforcement guard) rather than silently omitting the
  headless flag.

## Verifying the fix

After changing a launch site, confirm no window actually appears rather than
trusting the flag alone:

1. Launch from the real parent shape you built for (a windowless script, a
   scheduled task, a detached daemon) -- not an interactive terminal, which
   can mask a launch site that would otherwise surface a window.
2. Run at least two cycles if the process is periodic/recurring; a single
   clean run can hide a per-cycle leak (e.g. a daemon that reuses one hidden
   console versus one that allocates a fresh console per child).
3. Force the timeout/cancellation path and confirm the whole process tree
   exits -- a headless child is easy to leak silently since there is no
   visible window to notice still running.

## Boundaries

- This skill is about the agent's own ad hoc spawns (commands it runs,
  scripts/services it authors) in any repository. It does not replace a
  repository's own vendored process-spawn library or enforcement guard when
  one already exists in scope -- use that repository's canonical primitive
  first, and fall back to the routes above only when none is available.
- It does not cover process supervision, restart policy, or reaping orphaned
  trees -- only the window-suppression concern at launch time.
