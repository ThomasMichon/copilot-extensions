---
name: requesting-windows-elevation
description: "Run one explicitly approved same-account Windows administrator operation through a temporary interactive scheduled task, a genuine RunAs UAC request, and a specified private output log with a matching completion marker. Use for Windows elevation, UAC prompts on a development machine, run as administrator, administrator PowerShell, or a headless agent whose approved elevation never reaches the user's desktop. Never grants general administrative authority or bypasses UAC."
---

# Requesting Windows elevation

Elevate **one reviewed operation**, not the agent, shell session or arbitrary
future commands. Existing operator approval may cover that exact operation;
otherwise obtain explicit consent naming its target and effects first.
Administrator access never authorizes a reboot, unrelated setup, policy change,
credential repair or service reset. Keep those separate decisions.

Prefer the operation's existing owner: use its attributable command, declared
machine-state resource or installer rather than replacing its managed flow with
raw OS changes. This helper is a transport for an approved script, not another
configuration or service manager.

## Why the scheduled task matters

A headless caller can lack the interactive desktop needed to display consent.
The helper registers a uniquely named, **limited-privilege**, same-user
interactive task and runs it in one verified active desktop session. That task
requests `RunAs` for the operation. The operator approves the real Windows UAC
dialog; execution writes both output streams to the chosen log.

`RunLevel=Highest` does **not** itself create a UAC prompt: a low-privilege
process cannot register such a task. Do not work around that with SYSTEM,
stored passwords, S4U, a persistent privileged task, disabled UAC or a reusable
administrator relay. See Microsoft's
[task security contexts](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks)
and [RunEx session selection](https://learn.microsoft.com/en-us/windows/win32/taskschd/registeredtask-runex).

An elevated token proves execution, not that a dialog was visible. Already
elevated callers and OS policy can change prompt behavior. To qualify the UAC
desktop path, obtain the operator's confirmation that they saw and approved
the dialog; never weaken policy merely to force one to appear.

## Procedure

1. **Prepare.** Windows and PowerShell 7.2+ are required; fail explicitly if
   unavailable, never install tools as an incidental elevation step.
   This version supports same-account elevation only: an already-administrator
   token or a linked limited token. Preflight rejects standard-user,
   alternate-administrator credentials before requesting UAC. Do not select
   another account in a credential dialog; the worker also checks the original
   owner SID. Owner-only scratch permissions remain unchanged.
   Use `using-scratch-space` to prepare a private timestamped task directory.
   Write a reviewable `.ps1` for the exact operation, using absolute attributable
   command paths and a deliberate working directory. Never put credentials in
   script arguments or logs. Preserve the operation's own consent and error
   checks. The helper binds the script hash before execution.
   It holds a read handle that denies writes and replacement through execution;
   leave that approved script unchanged until the operation finishes.
2. **Prove the desktop route without elevation when needed.** Invoke the bundled
   harmless probe with `-InteractiveProbe`. This proves scheduler, session and
   logging only; its `probe` result is **not administrator success**.
3. **Request the approved operation.** Invoke the helper below. Explain the UAC
   dialog to the operator before starting. `-ConsentConfirmed` records that the
   conversational approval exists; it neither supplies credentials nor approves
   the Windows prompt. The only intended visible UI is that consent prompt.
4. **Wait for the actual terminal result.** Keep the bounded command attached.
   The helper prints its exact `request.json` and log paths. Read progress from
   that log if necessary; do not launch a second request while the first is
   pending. After an interruption, use `-WaitFor` with that same request.
   If the operation already completed and its task was removed, resumed waiting
   reads the matching completed receipt without creating another task or prompt.
5. **Interpret and verify.** Require the matching `AGENT_ELEVATION_END` run ID,
   an actual payload exit code of zero and `elevated: true` for administrator
   success. Task registration, scheduler exit zero, a created log or a `probe`
   result is insufficient. Verify the operation's intended effect separately.
   A canceled UAC request (`1223`), nonzero payload result or missing marker is
   not success. Do not silently retry a canceled consent request.
6. **Clean up owned state.** A confirmed terminal operation removes only its
   exact temporary limited task. Keep the request/log as evidence until routed
   to the normal durable home, then remove the exact owned scratch directory.
   On a timeout or missing marker, retain the task and diagnostics, inspect
   its exact identity and running instance, and diagnose before cleanup or any
   new request. The elevated worker owns a kill-on-close Job before starting
   the payload; timeout or leftover descendants terminate that Job instead of
   producing a successful completion marker. Do not stop foreign tasks or
   processes to clear a blocker.

Resolve `<skill-base>` from this loaded skill, not an installed-directory guess.
Use a fresh log filename in the prepared private task directory:

```powershell
& '<skill-base>\scripts\invoke-windows-elevation.ps1' `
    -ScriptPath '<reviewed-operation.ps1>' -LogPath '<private-task-directory>\operation.log' `
    -WorkingDirectory '<operation-directory>' -ConsentConfirmed
```

For the harmless scheduler probe, use the same invocation with
`-ScriptPath '<skill-base>\assets\probe.ps1' -InteractiveProbe` instead of
`-ConsentConfirmed`. A separately approved administrator probe uses that same
harmless script with `-ConsentConfirmed`.

If there are multiple active same-user desktops, select the intended
`-SessionId` explicitly. No active desktop is an operator/session blocker,
not permission to run as another user.

Resume **waiting**, without repeating the operation:

```powershell
& '<skill-base>\scripts\invoke-windows-elevation.ps1' -WaitFor '<printed-request.json>'
```

`-ConsentTimeoutSeconds` defaults to 300 and `-TimeoutSeconds` to 600.
The separate admission deadline bounds queued startup and consent: execution
cannot start after it merely because the payload still has time allocated.
Both original absolute deadlines survive resumed waiting. A restart-required
exit result remains visible and requires the operation owner's interpretation;
this helper never restarts the machine.
