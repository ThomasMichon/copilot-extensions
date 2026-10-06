---
applyTo: "**"
---

# Headless process-spawn fallback

**Fallback policy `[owner: agent-conduct-guidance@0.1.5-dev1]`:** Every ad hoc
child process an agent starts to serve one turn -- a shell command, a helper
script, a build/watch/dev-server launch, a self-authored scheduled task or
service -- must not surface a visible console window or steal focus. On
Windows, a naive spawn of a console-subsystem program (`cmd.exe`,
`powershell.exe`, `pwsh.exe`, console `python.exe`, `node.exe`, `git.exe`,
`ssh.exe`) allocates a fresh terminal window per invocation even when the
parent itself is headless; `-WindowStyle Hidden` alone does not suppress it.
`wsl.exe` is the same class even though it reads as a "bridge" rather than a
console program -- a bare `wsl.exe -d <distro> -- <command>` still allocates
its own window per call. Route every spawn through the language- and
OS-correct headless mechanism (`CREATE_NO_WINDOW` / `windowsHide: true` / a
GUI-subsystem interpreter / `nohup`+redirected stdio, per the exact table in
the paired skill) instead of a bare `Start-Process`, `os.system`, or unflagged
`child_process.spawn`. When the caller needs captured output (not just
suppression), `conhost.exe --headless` does not pipe stdout/stderr back --
use `CREATE_NO_WINDOW` (Python) or `[System.Diagnostics.ProcessStartInfo]`
with `CreateNoWindow = $true` plus **`ReadToEndAsync()` on both
StandardOutput and StandardError, started before `WaitForExit()`**
(PowerShell) instead -- sequential `StandardOutput.ReadToEnd()` then
`StandardError.ReadToEnd()` can deadlock on a full pipe, and
`Register-ObjectEvent` + `BeginOutputReadLine()` silently captures almost
nothing in a single blocking script (those events need PowerShell's own idle
loop, which a blocking `WaitForExit()` never yields to); see the paired skill
for the full, verified pattern. Prefer
routing to an existing local API/runtime over spawning a process at all.
Before starting a long-lived, repeating, or backgrounded process, invoke the
`spawning-headless-processes` skill to select the correct primitive for the
target language and OS.
