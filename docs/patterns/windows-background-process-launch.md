# Pattern: windows-background-process-launch

**Serves:** *Vision plugin-services* through cross-platform parity: background
work has no user-visible terminal side effect on Windows.
**Exemplars:** `agent-procutil`, `ssh-manager`, and the PowerShell
`conhost.exe --headless` service launchers.

## Problem

A service commonly runs beneath `pythonw.exe`, a hidden scheduled task, or
another consoleless parent. Starting a console-subsystem child from that parent
can ask Windows to allocate a terminal. The allocation is delegated to the
user's configured Default Terminal, so a supposedly hidden health probe may
create a visible window and steal focus.

`STARTUPINFO` plus `SW_HIDE` does not make `CREATE_NEW_CONSOLE` safe. It requests
a new console first and asks one implementation to hide it second; Default
Terminal is free to surface the delegated window. Machine configuration may
change the symptom, but it is never part of the launch contract.

## Launch-kind matrix

| Launch kind | Windows mechanism | Required properties |
|---|---|---|
| Short-lived child with captured or redirected stdio | A console-subsystem root plus `agent_procutil.no_window_kwargs()` / `no_window_flags()` | `CREATE_NO_WINDOW`; pipes and exit status preserved; timeout owns the complete tree |
| Non-interactive OpenSSH transport with redirected stdio | `ssh_manager.ssh_subprocess_kwargs()` | `DETACHED_PROCESS`; no console exists for Default Terminal to surface; pipes, exit status, and root PID remain owned |
| Long-lived Python daemon | `windowless_python()` plus `windowless_daemon_kwargs()` | No visible root console; survivability is explicit; every console child still uses the short-lived primitive |
| PowerShell startup or scheduled launcher whose output is not captured | `conhost.exe --headless <interpreter> ...` | Headless console inherited by descendants; stable installed target; explicit stop/cutover ownership |
| Intentional interactive terminal | An explicit interactive launcher | Reviewable exception with `# headless-guard: allow <reason>` when low-level flags are necessary |

Do not use `conhost.exe --headless` for a process whose stdout, stderr, or exit
status must be captured: the host owns that stream boundary. Do not use
`DETACHED_PROCESS` for an arbitrary captured child. The narrow OpenSSH exception
is safe only because every call is non-interactive, owns all stdio through pipes
or null handles, and tears down the tree by root PID. Do not launch a console
descendant from `pythonw.exe` without an explicit no-window primitive.

## Routing before launching

Avoiding the window is the last line of defense; avoid unnecessary processes
first. A same-machine target uses the local API or runtime directly. Machine
identity comparisons are normalized and case-insensitive before choosing an SSH
or other remote transport. Periodic supervision must not turn a local liveness
check into recurring self-SSH process churn.

## Automated enforcement

`tools/check-headless-launch.py` is required in CI and pre-push:

- an `agent-procutil` adopter may not hand-roll Windows creation flags;
- production plugin and canonical shared-library code may not reference
  `CREATE_NEW_CONSOLE`;
- a genuine interactive or low-level exception requires an inline
  `# headless-guard: allow <reason>` marker.

Canonical shared libraries and every shipped vendored copy are scanned directly.
The vendored-library synchronization guard is also required in CI and pre-push,
so one plugin cannot silently ship a divergent process primitive.

## Review and validation

Mocked flag assertions prove wiring, not behavior. A Windows launch-path change
also needs a focused live regression:

1. Launch from the real windowless parent (`pythonw.exe`, scheduled launcher, or
   service daemon).
2. Exercise a real console child and any configured proxy/transport descendant.
3. Observe at least two periodic cycles with Win32 window enumeration and
   foreground tracking; record zero visible windows, Default Terminal hosts, and
   focus transitions.
4. Force timeout/cancellation and prove the full process tree exits.
5. Verify local routing does not cross a remote process boundary.

Keep the live test out of the fast required lane when it needs a Windows host;
the static guard and unit contract remain the portable CI gate.

## See Also

- [cross-platform-parity](cross-platform-parity.md)
- [service-lifecycle-supervision](service-lifecycle-supervision.md)
- [`windows-launch-hardening` effort](../../efforts/active/windows-launch-hardening/README.md)
