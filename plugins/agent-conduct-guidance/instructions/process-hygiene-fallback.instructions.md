---
applyTo: "**"
---

# Headless process-spawn fallback

**Fallback policy `[owner: agent-conduct-guidance@0.1.3-dev1]`:** Every ad hoc
child process an agent starts to serve one turn -- a shell command, a helper
script, a build/watch/dev-server launch, a self-authored scheduled task or
service -- must not surface a visible console window or steal focus. On
Windows, a naive spawn of a console-subsystem program (`cmd.exe`,
`powershell.exe`, `pwsh.exe`, console `python.exe`, `node.exe`, `git.exe`,
`ssh.exe`) allocates a fresh terminal window per invocation even when the
parent itself is headless; `-WindowStyle Hidden` alone does not suppress it.
Route every spawn through the language- and OS-correct headless mechanism
(`CREATE_NO_WINDOW` / `windowsHide: true` / a GUI-subsystem interpreter /
`nohup`+redirected stdio, per the exact table in the paired skill) instead of
a bare `Start-Process`, `os.system`, or unflagged `child_process.spawn`. Prefer
routing to an existing local API/runtime over spawning a process at all.
Before starting a long-lived, repeating, or backgrounded process, invoke the
`spawning-headless-processes` skill to select the correct primitive for the
target language and OS.
